import os
import secrets
import hashlib
import hmac
import logging
import ssl
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "verify-full").strip().lower()
DATABASE_SSL_ROOT_CERT = os.getenv("DATABASE_SSL_ROOT_CERT", "").strip()
DATABASE_COMMAND_TIMEOUT = float(os.getenv("DATABASE_COMMAND_TIMEOUT", "30"))
DATABASE_POOL_MIN_SIZE = max(1, int(os.getenv("DATABASE_POOL_MIN_SIZE", "1")))
DATABASE_POOL_MAX_SIZE = max(
    DATABASE_POOL_MIN_SIZE,
    int(os.getenv("DATABASE_POOL_MAX_SIZE", "10")),
)
REQUEST_STATS_RETENTION_DAYS = max(
    7, min(int(os.getenv("REQUEST_STATS_RETENTION_DAYS", "90")), 365)
)
LOGIN_CODE_MIN_INTERVAL_SECONDS = max(
    1, int(os.getenv("LOGIN_CODE_MIN_INTERVAL_SECONDS", "30"))
)
MAX_WEB_SESSIONS_PER_USER = max(
    1, int(os.getenv("MAX_WEB_SESSIONS_PER_USER", "5"))
)
WEB_SESSION_TOUCH_INTERVAL_SECONDS = max(
    60, min(int(os.getenv("WEB_SESSION_TOUCH_INTERVAL_SECONDS", "300")), 3600)
)
LOGIN_CODE_PEPPER = os.getenv("LOGIN_CODE_PEPPER", "")

_ALLOWED_SSL_MODES = {"verify-full"}
logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _build_ssl_context() -> ssl.SSLContext:
    """Создаёт TLS-контекст на системных CA Render/ОС.

    Строковый режим asyncpg ``verify-full`` ищет PostgreSQL-файл
    ~/.postgresql/root.crt. На Render его обычно нет, тогда как системное
    хранилище CA доступно через ssl.create_default_context().
    """
    context = ssl.create_default_context(
        purpose=ssl.Purpose.SERVER_AUTH,
        cafile=DATABASE_SSL_ROOT_CERT or None,
    )
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


def _database_dsn_without_ssl_options(dsn: str) -> str:
    """Убирает SSL-параметры DSN: TLS полностью задаёт ssl_context.

    Это также нейтрализует сохранённый в DATABASE_URL параметр
    sslrootcert=/opt/render/.postgresql/root.crt.
    """
    parts = urlsplit(dsn)
    blocked = {
        "ssl", "sslmode", "sslrootcert", "sslcert", "sslkey",
        "sslcrl", "sslpassword",
    }
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in blocked
    ]
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))


async def init_db():
    global _pool
    logger.info("database module loaded: build=render-system-ca-v2")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан")
    if len(LOGIN_CODE_PEPPER.encode("utf-8")) < 32:
        raise RuntimeError(
            "LOGIN_CODE_PEPPER должен быть отдельным случайным секретом длиной не менее 32 байт"
        )
    if DATABASE_SSLMODE not in _ALLOWED_SSL_MODES:
        raise RuntimeError("DATABASE_SSLMODE должен быть verify-full")

    # Используем системные доверенные CA вместо несуществующего
    # ~/.postgresql/root.crt, который asyncpg ищет для строкового verify-full.
    ssl_context = _build_ssl_context()
    database_dsn = _database_dsn_without_ssl_options(DATABASE_URL)
    _pool = await asyncpg.create_pool(
        database_dsn,
        ssl=ssl_context,
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
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS model_limits (
                user_id BIGINT NOT NULL,
                model TEXT NOT NULL,
                daily_limit INTEGER NOT NULL CHECK (daily_limit BETWEEN 1 AND 10000),
                PRIMARY KEY (user_id, model),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS request_usage_daily (
                user_id BIGINT NOT NULL,
                model TEXT NOT NULL,
                usage_date DATE NOT NULL,
                request_count INTEGER NOT NULL CHECK (request_count >= 0),
                PRIMARY KEY (user_id, model, usage_date),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_daily_date ON request_usage_daily(usage_date)"
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
        # До установки уникальности оставляем только самый новый код пользователя.
        # Это также безопасно мигрирует базу, если старая версия успела создать
        # несколько кодов из-за параллельных запросов.
        await conn.execute(
            """
            DELETE FROM login_codes AS old_code
            USING login_codes AS newer_code
            WHERE old_code.user_id = newer_code.user_id
              AND (
                    old_code.created_at < newer_code.created_at
                    OR (
                        old_code.created_at = newer_code.created_at
                        AND old_code.code < newer_code.code
                    )
                  )
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_login_codes_user
            ON login_codes(user_id)
            """
        )
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

async def reserve_request(
    user_id: int,
    model: str,
    source: str,
    default_daily_limit: int,
) -> bool:
    """Атомарно резервирует один внешний AI-запрос.

    Квота общая для сайта и Telegram-бота и поэтому не обходится сменой
    интерфейса или запуском нескольких процессов приложения.
    """
    if source not in {"bot", "webapp"}:
        raise ValueError("Недопустимый источник запроса")
    safe_model = str(model or "")[:128]
    if not safe_model:
        raise ValueError("Модель не задана")
    limit = max(1, min(int(default_daily_limit), 10000))

    async with _pool.acquire() as conn:
        async with conn.transaction():
            configured_limit = await conn.fetchval(
                """
                SELECT daily_limit
                FROM model_limits
                WHERE user_id = $1 AND model = $2
                """,
                user_id,
                safe_model,
            )
            effective_limit = int(configured_limit or limit)
            reserved = await conn.fetchval(
                """
                INSERT INTO request_usage_daily (
                    user_id, model, usage_date, request_count
                )
                VALUES (
                    $1, $2, (NOW() AT TIME ZONE 'UTC')::date, 1
                )
                ON CONFLICT (user_id, model, usage_date)
                DO UPDATE SET request_count =
                    request_usage_daily.request_count + 1
                WHERE request_usage_daily.request_count < $3
                RETURNING request_count
                """,
                user_id,
                safe_model,
                effective_limit,
            )
            if reserved is None:
                return False
            await conn.execute(
                """
                INSERT INTO request_stats (user_id, model, source)
                VALUES ($1, $2, $3)
                """,
                user_id,
                safe_model,
                source,
            )
            return True


async def set_user_model_limit(
    user_id: int,
    model: str,
    daily_limit: int | None,
) -> None:
    safe_model = str(model or "")[:128]
    if not safe_model:
        raise ValueError("Модель не задана")
    async with _pool.acquire() as conn:
        if daily_limit is None:
            await conn.execute(
                "DELETE FROM model_limits WHERE user_id = $1 AND model = $2",
                user_id,
                safe_model,
            )
            return
        checked_limit = int(daily_limit)
        if checked_limit < 1 or checked_limit > 10000:
            raise ValueError("Лимит должен быть от 1 до 10000")
        await conn.execute(
            """
            INSERT INTO model_limits (user_id, model, daily_limit)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, model)
            DO UPDATE SET daily_limit = EXCLUDED.daily_limit
            """,
            user_id,
            safe_model,
            checked_limit,
        )


async def get_user_model_limits(user_id: int) -> dict[str, int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT model, daily_limit
            FROM model_limits
            WHERE user_id = $1
            ORDER BY model
            """,
            user_id,
        )
    return {str(row["model"]): int(row["daily_limit"]) for row in rows}


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
        user_ids = [int(row["user_id"]) for row in rows]
        limit_rows = []
        if user_ids:
            limit_rows = await conn.fetch(
                """
                SELECT user_id, model, daily_limit
                FROM model_limits
                WHERE user_id = ANY($1::bigint[])
                """,
                user_ids,
            )
    limits_by_user: dict[int, dict[str, int]] = {}
    for row in limit_rows:
        limits_by_user.setdefault(int(row["user_id"]), {})[str(row["model"])] = int(
            row["daily_limit"]
        )
    result = []
    for row in rows:
        item = dict(row)
        item["limits"] = limits_by_user.get(int(row["user_id"]), {})
        result.append(item)
    return result


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
        async with conn.transaction():
            # Блокировка действует только в этой транзакции и сериализует
            # параллельные запросы кода для одного Telegram user_id.
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", user_id)
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


def _pepper_bytes() -> bytes:
    pepper = LOGIN_CODE_PEPPER.encode("utf-8")
    if len(pepper) < 32:
        raise RuntimeError("LOGIN_CODE_PEPPER не задан или слишком короткий")
    return pepper


def _hash_login_code(code: str) -> str:
    return hmac.new(
        _pepper_bytes(),
        code.encode(),
        hashlib.sha256,
    ).hexdigest()


_SESSION_TOKEN_VERSION = 1
_SESSION_TOKEN_DOMAIN = b"web-session-v1:"
_SESSION_TOKEN_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def generate_web_session_token() -> str:
    """Создаёт непрозрачный токен с подписью для раннего отсева подделок."""
    opaque = secrets.token_urlsafe(32)
    signature = hmac.new(
        _pepper_bytes(),
        _SESSION_TOKEN_DOMAIN + opaque.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"v{_SESSION_TOKEN_VERSION}.{opaque}.{signature}"


def is_valid_web_session_token(token: str) -> bool:
    """Проверяет формат и подпись токена до обращения к PostgreSQL."""
    if not isinstance(token, str) or len(token) > 128:
        return False
    try:
        prefix, opaque, signature = token.split(".")
    except ValueError:
        return False
    if prefix != f"v{_SESSION_TOKEN_VERSION}" or not 40 <= len(opaque) <= 64:
        return False
    if any(char not in _SESSION_TOKEN_ALPHABET for char in opaque):
        return False
    if len(signature) != 64:
        return False
    try:
        bytes.fromhex(signature)
    except ValueError:
        return False
    expected = hmac.new(
        _pepper_bytes(),
        _SESSION_TOKEN_DOMAIN + opaque.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


async def create_web_session(token: str, user_id: int, ttl_seconds: int) -> None:
    if not is_valid_web_session_token(token):
        raise ValueError("Некорректный токен веб-сессии")
    ttl_seconds = max(300, min(int(ttl_seconds), 30 * 24 * 60 * 60))
    async with _pool.acquire() as conn:
        async with conn.transaction():
            # Атомарно применяем лимит сессий при параллельных входах.
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1::bigint)",
                user_id,
            )
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
    Обновляет last_seen_at с ограниченной частотой."""
    if not is_valid_web_session_token(token):
        return None
    idle_seconds = max(300, min(int(idle_seconds), 30 * 24 * 60 * 60))
    # Интервал обновления всегда меньше idle TTL, иначе активно используемая
    # сессия могла бы истечь между редкими touch-запросами.
    touch_interval = min(
        WEB_SESSION_TOUCH_INTERVAL_SECONDS,
        max(60, idle_seconds // 2),
    )
    token_hash = _hash_token(token)
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                user_id,
                last_seen_at <= NOW() - ($3::integer * INTERVAL '1 second')
                    AS should_touch
            FROM web_sessions
            WHERE token_hash = $1
              AND expires_at > NOW()
              AND last_seen_at > NOW() - ($2::integer * INTERVAL '1 second')
            """,
            token_hash,
            idle_seconds,
            touch_interval,
        )
        if row and row["should_touch"]:
            await conn.execute(
                """
                UPDATE web_sessions
                SET last_seen_at = NOW()
                WHERE token_hash = $1
                  AND last_seen_at <= NOW() - ($2::integer * INTERVAL '1 second')
                """,
                token_hash,
                touch_interval,
            )
    return row["user_id"] if row else None


async def delete_web_session(token: str) -> None:
    if not is_valid_web_session_token(token):
        return
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE token_hash = $1", _hash_token(token))


async def delete_all_web_sessions(user_id: int) -> None:
    async with _pool.acquire() as conn:
        await conn.execute("DELETE FROM web_sessions WHERE user_id = $1", user_id)


async def cleanup_expired_auth(conn=None) -> None:
    """Удаляет истёкшие данные аутентификации и старую статистику."""
    if conn is not None:
        await conn.execute("DELETE FROM login_codes WHERE expires_at < NOW()")
        await conn.execute("DELETE FROM web_sessions WHERE expires_at < NOW()")
        await conn.execute(
            """
            DELETE FROM request_usage_daily
            WHERE usage_date < (NOW() AT TIME ZONE 'UTC')::date - $1::integer
            """,
            REQUEST_STATS_RETENTION_DAYS,
        )
        await conn.execute(
            """
            DELETE FROM request_stats
            WHERE created_at < NOW() - ($1::integer * INTERVAL '1 day')
            """,
            REQUEST_STATS_RETENTION_DAYS,
        )
        return
    async with _pool.acquire() as acquired:
        await cleanup_expired_auth(conn=acquired)


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
