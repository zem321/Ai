import os
import secrets
import hashlib
import hmac
import logging
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "verify-full").strip().lower()
DATABASE_COMMAND_TIMEOUT = float(os.getenv("DATABASE_COMMAND_TIMEOUT", "30"))
DATABASE_POOL_MIN_SIZE = max(1, int(os.getenv("DATABASE_POOL_MIN_SIZE", "1")))
DATABASE_POOL_MAX_SIZE = max(
    DATABASE_POOL_MIN_SIZE,
    int(os.getenv("DATABASE_POOL_MAX_SIZE", "10")),
)
LOGIN_CODE_MIN_INTERVAL_SECONDS = max(
    1, int(os.getenv("LOGIN_CODE_MIN_INTERVAL_SECONDS", "30"))
)
MAX_WEB_SESSIONS_PER_USER = max(
    1, int(os.getenv("MAX_WEB_SESSIONS_PER_USER", "5"))
)
LOGIN_CODE_PEPPER = os.getenv("LOGIN_CODE_PEPPER") or os.getenv("BOT_TOKEN", "")

_ALLOWED_SSL_MODES = {"verify-full", "verify-ca"}
logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан")
    if not LOGIN_CODE_PEPPER:
        raise RuntimeError("LOGIN_CODE_PEPPER или BOT_TOKEN не задан")
    if DATABASE_SSLMODE not in _ALLOWED_SSL_MODES:
        raise RuntimeError(
            "DATABASE_SSLMODE должен быть verify-full или verify-ca"
        )

    # verify-full проверяет и цепочку сертификата, и имя сервера.
    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        ssl=DATABASE_SSLMODE,
        command_timeout=DATABASE_COMMAND_TIMEOUT,
        min_size=DATABASE_POOL_MIN_SIZE,
        max_size=DATABASE_POOL_MAX_SIZE,
    )
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                status TEXT NOT NULL CHECK (status IN ('approved', 'pending', 'rejected'))
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_stats (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'bot' CHECK (source IN ('bot', 'webapp')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rs_user ON request_stats(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rs_created ON request_stats(created_at)"
        )

        # ─── Вход на сайт по коду (вне Telegram Mini App) ──────────────────
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_codes (
                code TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                used BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_codes_user ON login_codes(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_codes_expires ON login_codes(expires_at)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_sessions_user ON web_sessions(user_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_web_sessions_expires ON web_sessions(expires_at)"
        )

        # В старой версии в login_codes.code хранился код открытым текстом.
        # Теперь в этом legacy-столбце хранится только HMAC-SHA-256. Старые короткие
        # коды намеренно инвалидируются при первом запуске обновлённой версии.
        await conn.execute("DELETE FROM login_codes WHERE LENGTH(code) <> 64")
        # Сессии отклонённых пользователей не должны оживать после одобрения.
        await conn.execute(
            """
            DELETE FROM web_sessions ws
            USING users u
            WHERE ws.user_id = u.user_id AND u.status <> 'approved'
            """
        )
        await cleanup_expired_auth(conn=conn)


async def _get_status(user_id: int) -> str | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM users WHERE user_id = $1", user_id)
        return row["status"] if row else None


async def is_approved(user_id: int) -> bool:
    return await _get_status(user_id) == "approved"


async def is_pending(user_id: int) -> bool:
    return await _get_status(user_id) == "pending"


async def is_rejected(user_id: int) -> bool:
    return await _get_status(user_id) == "rejected"


async def add_pending(user_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, status) VALUES ($1, 'pending')
            ON CONFLICT (user_id) DO NOTHING
            """,
            user_id,
        )


async def approve_user(user_id: int):
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (user_id, status) VALUES ($1, 'approved')
            ON CONFLICT (user_id) DO UPDATE SET status = 'approved'
            """,
            user_id,
        )


async def reject_user(user_id: int):
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO users (user_id, status) VALUES ($1, 'rejected')
                ON CONFLICT (user_id) DO UPDATE SET status = 'rejected'
                """,
                user_id,
            )
            await conn.execute("DELETE FROM login_codes WHERE user_id = $1", user_id)
            await conn.execute("DELETE FROM web_sessions WHERE user_id = $1", user_id)


async def revoke_user(user_id: int):
    await reject_user(user_id)


async def get_all_approved() -> list[int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE status = 'approved'")
        return [r["user_id"] for r in rows]


async def get_all_pending() -> list[int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE status = 'pending'")
        return [r["user_id"] for r in rows]


async def get_all_rejected() -> list[int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users WHERE status = 'rejected'")
        return [r["user_id"] for r in rows]


# ─── Статистика запросов ───────────────────────────────────────────────────────

async def log_request(user_id: int, model: str, source: str = "bot"):
    """Записывает один запрос в статистику. source = 'bot' | 'webapp'."""
    if source not in {"bot", "webapp"}:
        source = "bot"
    safe_model = str(model or "")[:128]
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO request_stats (user_id, model, source) VALUES ($1, $2, $3)",
                user_id, safe_model, source,
            )
    except Exception:
        # Статистика не роняет основной поток, но сбой больше не скрывается.
        logger.exception("Не удалось записать статистику запроса")


async def get_user_stats(user_id: int) -> dict:
    """Возвращает статистику запросов пользователя за день и неделю."""
    async with _pool.acquire() as conn:
        # Всего
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM request_stats WHERE user_id = $1", user_id
        )
        # За сегодня (UTC)
        day = await conn.fetchval(
            "SELECT COUNT(*) FROM request_stats WHERE user_id=$1 AND created_at >= NOW() - INTERVAL '1 day'",
            user_id,
        )
        # За неделю
        week = await conn.fetchval(
            "SELECT COUNT(*) FROM request_stats WHERE user_id=$1 AND created_at >= NOW() - INTERVAL '7 days'",
            user_id,
        )
        # По моделям (топ-10)
        model_rows = await conn.fetch(
            """
            SELECT model, COUNT(*) AS cnt
            FROM request_stats WHERE user_id = $1
            GROUP BY model ORDER BY cnt DESC LIMIT 10
            """,
            user_id,
        )
        # По источнику
        source_rows = await conn.fetch(
            "SELECT source, COUNT(*) AS cnt FROM request_stats WHERE user_id=$1 GROUP BY source",
            user_id,
        )
    return {
        "total": total,
        "day": day,
        "week": week,
        "models": {r["model"]: r["cnt"] for r in model_rows},
        "sources": {r["source"]: r["cnt"] for r in source_rows},
    }


async def get_all_users_with_stats(limit: int = 200, offset: int = 0) -> list[dict]:
    """Список всех пользователей со статусом и краткой статистикой."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                u.user_id,
                u.status,
                COUNT(r.id)                                              AS total,
                COUNT(r.id) FILTER (WHERE r.created_at >= NOW() - INTERVAL '1 day')  AS day,
                COUNT(r.id) FILTER (WHERE r.created_at >= NOW() - INTERVAL '7 days') AS week
            FROM users u
            LEFT JOIN request_stats r ON r.user_id = u.user_id
            GROUP BY u.user_id, u.status
            ORDER BY total DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return [dict(r) for r in rows]


# ─── Вход на сайт по коду (вне Telegram Mini App) ──────────────────────────────
# Код одноразовый и короткоживущий, выдаётся ботом по команде /code.
# После успешного ввода кода сайт получает HttpOnly-cookie веб-сессии.
# Статус approved/pending/rejected проверяется при каждом запросе.

_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # без 0/O/1/I/L


async def create_login_code(user_id: int, length: int = 8, ttl_seconds: int = 600) -> str:
    """Создаёт один активный код. В БД сохраняется только HMAC-SHA-256 кода."""
    if length < 8:
        raise ValueError("Код входа должен содержать минимум 8 символов")
    ttl_seconds = max(60, min(int(ttl_seconds), 1800))

    async with _pool.acquire() as conn:
        too_soon = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM login_codes
                WHERE user_id = $1
                  AND created_at > NOW() - ($2::integer * INTERVAL '1 second')
            )
            """,
            user_id,
            LOGIN_CODE_MIN_INTERVAL_SECONDS,
        )
        if too_soon:
            raise RuntimeError(
                f"Новый код можно запросить через {LOGIN_CODE_MIN_INTERVAL_SECONDS} секунд."
            )

        await conn.execute("DELETE FROM login_codes WHERE user_id = $1", user_id)
        for _ in range(5):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
            code_hash = _hash_login_code(code)
            inserted = await conn.fetchval(
                """
                INSERT INTO login_codes(code, user_id, expires_at)
                VALUES ($1, $2, NOW() + ($3::integer * INTERVAL '1 second'))
                ON CONFLICT (code) DO NOTHING
                RETURNING code
                """,
                code_hash,
                user_id,
                ttl_seconds,
            )
            if inserted:
                return code
    raise RuntimeError("Не удалось создать код входа. Попробуйте ещё раз.")


async def consume_login_code(code: str) -> int | None:
    """Если код существует, не использован и не истёк — помечает его
    использованным и возвращает user_id. Иначе — None. Код одноразовый:
    повторный ввод того же кода всегда вернёт None."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE login_codes
            SET used = TRUE
            WHERE code = $1 AND used = FALSE AND expires_at > NOW()
            RETURNING user_id
            """,
            _hash_login_code(code),
        )
    return row["user_id"] if row else None


def _hash_token(token: str) -> str:
    # В БД храним только хэш токена — сам токен нигде на сервере не хранится,
    # это защищает активные сессии даже при утечке базы.
    return hashlib.sha256(token.encode()).hexdigest()


def _hash_login_code(code: str) -> str:
    if not LOGIN_CODE_PEPPER:
        raise RuntimeError("LOGIN_CODE_PEPPER или BOT_TOKEN не задан")
    return hmac.new(
        LOGIN_CODE_PEPPER.encode(),
        code.encode(),
        hashlib.sha256,
    ).hexdigest()


async def create_web_session(token: str, user_id: int, ttl_seconds: int) -> None:
    ttl_seconds = max(300, min(int(ttl_seconds), 30 * 24 * 60 * 60))
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM web_sessions WHERE expires_at <= NOW()"
            )
            await conn.execute(
                """
                DELETE FROM web_sessions
                WHERE user_id = $1
                  AND token_hash NOT IN (
                      SELECT token_hash
                      FROM web_sessions
                      WHERE user_id = $1
                      ORDER BY created_at DESC
                      LIMIT $2
                  )
                """,
                user_id,
                max(0, MAX_WEB_SESSIONS_PER_USER - 1),
            )
            await conn.execute(
                """
                INSERT INTO web_sessions(token_hash, user_id, expires_at)
                VALUES ($1, $2, NOW() + ($3::integer * INTERVAL '1 second'))
                """,
                _hash_token(token),
                user_id,
                ttl_seconds,
            )


async def get_web_session_user(token: str, idle_seconds: int = 24 * 60 * 60) -> int | None:
    """Возвращает user_id по токену сессии, если он ещё не истёк.
    Заодно обновляет last_seen_at."""
    idle_seconds = max(300, min(int(idle_seconds), 30 * 24 * 60 * 60))
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE web_sessions
            SET last_seen_at = NOW()
            WHERE token_hash = $1
              AND expires_at > NOW()
              AND last_seen_at > NOW() - ($2::integer * INTERVAL '1 second')
            RETURNING user_id
            """,
            _hash_token(token),
            idle_seconds,
        )
    return row["user_id"] if row else None


async def delete_web_session(token: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE token_hash = $1", _hash_token(token))


async def delete_all_web_sessions(user_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE user_id = $1", user_id)


async def cleanup_expired_auth(conn=None) -> None:
    """Удаляет истёкшие коды и сессии."""
    if conn is not None:
        await conn.execute("DELETE FROM login_codes WHERE expires_at < NOW()")
        await conn.execute("DELETE FROM web_sessions WHERE expires_at < NOW()")
        return
    async with _pool.acquire() as acquired:
        await cleanup_expired_auth(conn=acquired)


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
