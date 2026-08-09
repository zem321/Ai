import asyncio
import json
import os
import secrets
import hashlib
import hmac
import logging
import re
import ssl
from contextlib import asynccontextmanager, suppress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import asyncpg


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} должен быть целым числом") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} должен быть от {minimum} до {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} должен быть числом") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} должен быть от {minimum} до {maximum}")
    return value


DATABASE_URL = os.getenv("DATABASE_URL")
DATABASE_SSLMODE = os.getenv("DATABASE_SSLMODE", "verify-full").strip().lower()
DATABASE_SSL_ROOT_CERT = os.getenv("DATABASE_SSL_ROOT_CERT", "").strip()
DATABASE_COMMAND_TIMEOUT = _env_float("DATABASE_COMMAND_TIMEOUT", 30, 1, 120)
DATABASE_POOL_MIN_SIZE = _env_int("DATABASE_POOL_MIN_SIZE", 1, 1, 10)
DATABASE_POOL_MAX_SIZE = max(
    DATABASE_POOL_MIN_SIZE,
    _env_int("DATABASE_POOL_MAX_SIZE", 10, 1, 50),
)
REQUEST_STATS_RETENTION_DAYS = _env_int(
    "REQUEST_STATS_RETENTION_DAYS", 90, 7, 365
)
VK_CHAT_HISTORY_RETENTION_HOURS = _env_int(
    "VK_CHAT_HISTORY_RETENTION_HOURS", 24, 1, 168
)
GLOBAL_DAILY_AI_LIMIT = _env_int(
    "GLOBAL_DAILY_AI_LIMIT", 5000, 1, 1_000_000
)
GLOBAL_DAILY_IMAGE_LIMIT = _env_int(
    "GLOBAL_DAILY_IMAGE_LIMIT", 500, 1, 100_000
)
LOGIN_CODE_MIN_INTERVAL_SECONDS = _env_int(
    "LOGIN_CODE_MIN_INTERVAL_SECONDS", 30, 1, 3600
)
MAX_WEB_SESSIONS_PER_USER = _env_int(
    "MAX_WEB_SESSIONS_PER_USER", 5, 1, 20
)
WEB_SESSION_TOUCH_INTERVAL_SECONDS = _env_int(
    "WEB_SESSION_TOUCH_INTERVAL_SECONDS", 300, 60, 3600
)
USER_REQUEST_LEASE_SECONDS = _env_int(
    "USER_REQUEST_LEASE_SECONDS", 600, 60, 1800
)
LOGIN_CODE_PEPPER = os.getenv("LOGIN_CODE_PEPPER", "")

# Telegram ID сохраняются в базе без изменений, чтобы обновление не требовало
# миграции существующих пользователей. VK ID получают отдельное пространство
# внутри BIGINT и поэтому никогда не пересекаются с Telegram ID.
VK_USER_KEY_BASE = 4_000_000_000_000_000_000
VK_MAX_EXTERNAL_USER_ID = 999_999_999_999


def vk_user_key(vk_user_id: int) -> int:
    if (
        isinstance(vk_user_id, bool)
        or not isinstance(vk_user_id, int)
        or vk_user_id <= 0
        or vk_user_id > VK_MAX_EXTERNAL_USER_ID
    ):
        raise ValueError("Некорректный VK user_id")
    return VK_USER_KEY_BASE + vk_user_id


def vk_external_user_id(user_id: int) -> int | None:
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= VK_USER_KEY_BASE
        or user_id > VK_USER_KEY_BASE + VK_MAX_EXTERNAL_USER_ID
    ):
        return None
    return user_id - VK_USER_KEY_BASE

_ALLOWED_SSL_MODES = {"verify-full"}
logger = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


class _QuotaExhausted(RuntimeError):
    """Внутренний сигнал для атомарного отката резервирования квоты."""


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
    if (
        len(set(LOGIN_CODE_PEPPER)) < 8
        or LOGIN_CODE_PEPPER.strip().lower()
        in {"changeme", "change-me", "replace-me", "your-secret-here"}
    ):
        raise RuntimeError("LOGIN_CODE_PEPPER выглядит предсказуемым; сгенерируйте случайный секрет")
    forbidden_peppers = {
        value
        for value in (
            os.getenv("BOT_TOKEN"),
            os.getenv("GEMINI_API_KEY"),
            os.getenv("GEMINI_API_KEY_2"),
            os.getenv("GEMINI_API_KEY_3"),
            os.getenv("NVIDIA_API_KEY"),
            os.getenv("NVIDIA_API_KEY_2"),
            os.getenv("NVIDIA_API_KEY_3"),
            os.getenv("GROQ_API_KEY"),
            os.getenv("GROQ_API_KEY_2"),
            os.getenv("GROQ_API_KEY_3"),
            DATABASE_URL,
        )
        if value
    }
    if LOGIN_CODE_PEPPER in forbidden_peppers:
        raise RuntimeError(
            "LOGIN_CODE_PEPPER должен отличаться от токенов, API-ключей и DATABASE_URL"
        )
    if DATABASE_SSLMODE not in _ALLOWED_SSL_MODES:
        raise RuntimeError("DATABASE_SSLMODE должен быть verify-full")

    try:
        database_parts = urlsplit(DATABASE_URL)
        database_hostname = database_parts.hostname
    except ValueError as exc:
        raise RuntimeError("DATABASE_URL имеет некорректный формат PostgreSQL") from exc
    if (
        database_parts.scheme not in {"postgres", "postgresql"}
        or not database_hostname
        or not database_parts.username
        or not database_parts.path
        or database_parts.path == "/"
        or database_parts.fragment
    ):
        raise RuntimeError("DATABASE_URL имеет некорректный формат PostgreSQL")

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
            CREATE TABLE IF NOT EXISTS vk_user_state (
                user_id BIGINT PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'main_menu'
                    CHECK (mode IN ('main_menu', 'chat_mode', 'image_generate', 'image_edit')),
                selected_model TEXT NOT NULL,
                chat_history JSONB NOT NULL DEFAULT '[]'::jsonb,
                history_updated_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE vk_user_state DROP CONSTRAINT IF EXISTS vk_user_state_mode_check
            """
        )
        await conn.execute(
            """
            ALTER TABLE vk_user_state
                ADD CONSTRAINT vk_user_state_mode_check
                CHECK (mode IN ('main_menu', 'chat_mode', 'image_generate', 'image_edit'))
            """
        )
        await conn.execute(
            """
            ALTER TABLE vk_user_state
                ADD COLUMN IF NOT EXISTS history_updated_at TIMESTAMPTZ
            """
        )
        await conn.execute(
            """
            UPDATE vk_user_state
            SET history_updated_at = updated_at
            WHERE history_updated_at IS NULL
              AND chat_history <> '[]'::jsonb
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_vk_user_state_history_updated
            ON vk_user_state(history_updated_at)
            WHERE history_updated_at IS NOT NULL
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vk_callback_events (
                event_id TEXT PRIMARY KEY
                    CHECK (
                        LENGTH(event_id) BETWEEN 1 AND 128
                        AND event_id ~ '^[A-Za-z0-9_-]+$'
                    ),
                group_id BIGINT NOT NULL CHECK (group_id > 0),
                peer_id BIGINT NOT NULL CHECK (peer_id > 0),
                conversation_message_id BIGINT NOT NULL
                    CHECK (conversation_message_id > 0),
                message_id BIGINT NOT NULL DEFAULT 0 CHECK (message_id >= 0),
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        await conn.execute(
            """
            ALTER TABLE vk_callback_events
                ADD COLUMN IF NOT EXISTS group_id BIGINT,
                ADD COLUMN IF NOT EXISTS peer_id BIGINT,
                ADD COLUMN IF NOT EXISTS conversation_message_id BIGINT,
                ADD COLUMN IF NOT EXISTS message_id BIGINT
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_vk_callback_events_expires
            ON vk_callback_events(expires_at)
            """
        )
        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                uq_vk_callback_events_official_message
            ON vk_callback_events(
                group_id,
                peer_id,
                conversation_message_id
            )
            WHERE
                group_id IS NOT NULL
                AND peer_id IS NOT NULL
                AND conversation_message_id IS NOT NULL
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
            """
            CREATE TABLE IF NOT EXISTS active_user_requests (
                user_id BIGINT PRIMARY KEY,
                lease_token TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_active_user_requests_expires
            ON active_user_requests(expires_at)
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
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS global_request_usage_daily (
                quota_class TEXT NOT NULL
                    CHECK (quota_class IN ('ai', 'image')),
                usage_date DATE NOT NULL,
                request_count INTEGER NOT NULL CHECK (request_count >= 0),
                PRIMARY KEY (quota_class, usage_date)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_global_usage_daily_date
            ON global_request_usage_daily(usage_date)
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_api_key_state (
                provider TEXT NOT NULL
                    CHECK (provider IN ('nvidia', 'gemini', 'groq')),
                key_fingerprint TEXT NOT NULL
                    CHECK (key_fingerprint ~ '^[0-9a-f]{64}$'),
                failure_count INTEGER NOT NULL DEFAULT 0
                    CHECK (failure_count >= 0),
                unavailable_until TIMESTAMPTZ NOT NULL
                    DEFAULT '-infinity'::timestamptz,
                rate_window_started_at TIMESTAMPTZ,
                rate_request_count INTEGER NOT NULL DEFAULT 0
                    CHECK (rate_request_count >= 0),
                last_reserved_at TIMESTAMPTZ,
                last_failure_at TIMESTAMPTZ,
                last_success_at TIMESTAMPTZ,
                PRIMARY KEY (provider, key_fingerprint)
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_provider_key_available
            ON provider_api_key_state(provider, unavailable_until)
            """
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


_PROVIDER_NAMES = frozenset({"nvidia", "gemini", "groq"})
_KEY_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


def _validate_provider_key_identity(
    provider: str,
    key_fingerprint: str,
) -> None:
    if provider not in _PROVIDER_NAMES:
        raise ValueError("Некорректный провайдер API-ключа")
    if not _KEY_FINGERPRINT_RE.fullmatch(key_fingerprint):
        raise ValueError("Некорректный отпечаток API-ключа")


async def reserve_provider_api_key(
    provider: str,
    key_fingerprints: tuple[str, ...],
    *,
    per_minute_limit: int | None,
) -> str | None:
    """Атомарно выбирает случайный доступный ключ и резервирует RPM-слот."""
    if provider not in _PROVIDER_NAMES:
        raise ValueError("Некорректный провайдер API-ключа")
    checked_fingerprints = tuple(dict.fromkeys(key_fingerprints))
    if not checked_fingerprints or any(
        not _KEY_FINGERPRINT_RE.fullmatch(value)
        for value in checked_fingerprints
    ):
        raise ValueError("Некорректный список отпечатков API-ключей")
    if per_minute_limit is not None and not 1 <= per_minute_limit <= 10_000:
        raise ValueError("Некорректный минутный лимит API-ключа")

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO provider_api_key_state (
                    provider,
                    key_fingerprint
                )
                SELECT $1, fingerprint
                FROM UNNEST($2::text[]) AS configured(fingerprint)
                ON CONFLICT (provider, key_fingerprint) DO NOTHING
                """,
                provider,
                list(checked_fingerprints),
            )
            row = await conn.fetchrow(
                """
                WITH candidate AS (
                    SELECT provider, key_fingerprint
                    FROM provider_api_key_state
                    WHERE provider = $1
                      AND key_fingerprint = ANY($2::text[])
                      AND unavailable_until <= NOW()
                      AND (
                            $3::integer IS NULL
                            OR rate_window_started_at IS NULL
                            OR rate_window_started_at <=
                                NOW() - INTERVAL '1 minute'
                            OR rate_request_count < $3
                          )
                    ORDER BY RANDOM()
                    LIMIT 1
                    FOR UPDATE
                )
                UPDATE provider_api_key_state AS state
                SET
                    rate_window_started_at = CASE
                        WHEN $3::integer IS NULL
                            THEN state.rate_window_started_at
                        WHEN state.rate_window_started_at IS NULL
                          OR state.rate_window_started_at <=
                                NOW() - INTERVAL '1 minute'
                            THEN NOW()
                        ELSE state.rate_window_started_at
                    END,
                    rate_request_count = CASE
                        WHEN $3::integer IS NULL
                            THEN state.rate_request_count
                        WHEN state.rate_window_started_at IS NULL
                          OR state.rate_window_started_at <=
                                NOW() - INTERVAL '1 minute'
                            THEN 1
                        ELSE state.rate_request_count + 1
                    END,
                    last_reserved_at = NOW()
                FROM candidate
                WHERE state.provider = candidate.provider
                  AND state.key_fingerprint = candidate.key_fingerprint
                RETURNING state.key_fingerprint
                """,
                provider,
                list(checked_fingerprints),
                per_minute_limit,
            )
    return str(row["key_fingerprint"]) if row else None


async def mark_provider_api_key_failure(
    provider: str,
    key_fingerprint: str,
) -> int:
    """Ставит ключ в карантин и возвращает длительность карантина в секундах."""
    _validate_provider_key_identity(provider, key_fingerprint)
    async with _pool.acquire() as conn:
        cooldown_seconds = await conn.fetchval(
            """
            UPDATE provider_api_key_state
            SET
                failure_count = failure_count + 1,
                unavailable_until = NOW() + MAKE_INTERVAL(
                    secs => CASE
                        WHEN provider = 'groq' THEN 86400
                        WHEN provider = 'gemini' AND failure_count >= 1
                            THEN 86400
                        ELSE 60
                    END
                ),
                last_failure_at = NOW()
            WHERE provider = $1
              AND key_fingerprint = $2
            RETURNING CASE
                WHEN provider = 'groq' THEN 86400
                WHEN provider = 'gemini' AND failure_count >= 2 THEN 86400
                ELSE 60
            END
            """,
            provider,
            key_fingerprint,
        )
    if cooldown_seconds is None:
        raise RuntimeError("Состояние API-ключа не найдено")
    return int(cooldown_seconds)


async def mark_provider_api_key_success(
    provider: str,
    key_fingerprint: str,
) -> None:
    _validate_provider_key_identity(provider, key_fingerprint)
    async with _pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE provider_api_key_state
            SET
                failure_count = 0,
                unavailable_until = '-infinity'::timestamptz,
                last_success_at = NOW()
            WHERE provider = $1
              AND key_fingerprint = $2
            """,
            provider,
            key_fingerprint,
        )
    if result == "UPDATE 0":
        raise RuntimeError("Состояние API-ключа не найдено")


async def get_user_status(user_id: int) -> str | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM users WHERE user_id = $1", user_id)
        return row["status"] if row else None


async def is_approved(user_id: int) -> bool:
    return await get_user_status(user_id) == "approved"


async def is_pending(user_id: int) -> bool:
    return await get_user_status(user_id) == "pending"


async def is_rejected(user_id: int) -> bool:
    return await get_user_status(user_id) == "rejected"


async def add_pending(user_id: int) -> bool:
    async with _pool.acquire() as conn:
        inserted = await conn.fetchval(
            """
            INSERT INTO users (user_id, status) VALUES ($1, 'pending')
            ON CONFLICT (user_id) DO NOTHING
            RETURNING user_id
            """,
            user_id,
        )
    return inserted is not None


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
            # Отзыв доступа должен останавливать и уже выполняющийся AI-запрос.
            # Heartbeat активного обработчика увидит потерю аренды и отменит
            # операцию до выдачи результата.
            await conn.execute(
                "DELETE FROM active_user_requests WHERE user_id = $1",
                user_id,
            )


async def revoke_user(user_id: int):
    await reject_user(user_id)


async def get_all_approved() -> list[int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id FROM users
            WHERE status = 'approved' AND user_id < $1
            """,
            VK_USER_KEY_BASE,
        )
        return [r["user_id"] for r in rows]


async def get_all_pending() -> list[int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id FROM users
            WHERE status = 'pending' AND user_id < $1
            """,
            VK_USER_KEY_BASE,
        )
        return [r["user_id"] for r in rows]


async def get_all_rejected() -> list[int]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id FROM users
            WHERE status = 'rejected' AND user_id < $1
            """,
            VK_USER_KEY_BASE,
        )
        return [r["user_id"] for r in rows]


async def get_vk_user_status(vk_user_id: int) -> str | None:
    return await get_user_status(vk_user_key(vk_user_id))


async def add_vk_pending(vk_user_id: int) -> bool:
    return await add_pending(vk_user_key(vk_user_id))


async def approve_vk_user(vk_user_id: int) -> None:
    await approve_user(vk_user_key(vk_user_id))


async def reject_vk_user(vk_user_id: int) -> None:
    await reject_user(vk_user_key(vk_user_id))


async def get_all_vk_users(status: str) -> list[int]:
    if status not in {"approved", "pending", "rejected"}:
        raise ValueError("Некорректный статус пользователя")
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id
            FROM users
            WHERE status = $1
              AND user_id > $2
              AND user_id <= $3
            ORDER BY user_id
            """,
            status,
            VK_USER_KEY_BASE,
            VK_USER_KEY_BASE + VK_MAX_EXTERNAL_USER_ID,
        )
    return [int(row["user_id"]) - VK_USER_KEY_BASE for row in rows]


def _validated_vk_history(history: object) -> list[dict[str, str]]:
    if not isinstance(history, list):
        return []
    result: list[dict[str, str]] = []
    total_chars = 0
    for item in history[-20:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        remaining = 10_000 - total_chars
        if remaining <= 0:
            break
        clipped = content[: min(10_000, remaining)]
        result.append({"role": role, "content": clipped})
        total_chars += len(clipped)
    return result


async def get_vk_state(vk_user_id: int, default_model: str) -> dict:
    user_id = vk_user_key(vk_user_id)
    checked_default_model = str(default_model or "")[:128]
    if not checked_default_model:
        raise ValueError("Модель по умолчанию не задана")
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                mode,
                selected_model,
                CASE
                    WHEN history_updated_at IS NOT NULL
                     AND history_updated_at >
                         NOW() - ($2::integer * INTERVAL '1 hour')
                    THEN chat_history
                    ELSE '[]'::jsonb
                END AS chat_history
            FROM vk_user_state
            WHERE user_id = $1
            """,
            user_id,
            VK_CHAT_HISTORY_RETENTION_HOURS,
        )
    if row is None:
        return {
            "mode": "main_menu",
            "selected_model": checked_default_model,
            "chat_history": [],
        }
    raw_history = row["chat_history"]
    if isinstance(raw_history, str):
        try:
            raw_history = json.loads(raw_history)
        except json.JSONDecodeError:
            raw_history = []
    return {
        "mode": str(row["mode"]),
        "selected_model": str(row["selected_model"]),
        "chat_history": _validated_vk_history(raw_history),
    }


async def save_vk_state(
    vk_user_id: int,
    *,
    mode: str,
    selected_model: str,
    chat_history: object,
) -> None:
    user_id = vk_user_key(vk_user_id)
    if mode not in {"main_menu", "chat_mode", "image_generate", "image_edit"}:
        raise ValueError("Некорректный режим VK")
    checked_model = str(selected_model or "")[:128]
    if not checked_model:
        raise ValueError("Модель не задана")
    checked_history = _validated_vk_history(chat_history)
    serialized_history = json.dumps(
        checked_history,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO vk_user_state (
                user_id,
                mode,
                selected_model,
                chat_history,
                history_updated_at,
                updated_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4::jsonb,
                CASE
                    WHEN $4::jsonb = '[]'::jsonb THEN NULL
                    ELSE NOW()
                END,
                NOW()
            )
            ON CONFLICT (user_id)
            DO UPDATE SET
                mode = EXCLUDED.mode,
                selected_model = EXCLUDED.selected_model,
                chat_history = EXCLUDED.chat_history,
                history_updated_at = CASE
                    WHEN EXCLUDED.chat_history = '[]'::jsonb THEN NULL
                    WHEN vk_user_state.chat_history
                         IS DISTINCT FROM EXCLUDED.chat_history
                    THEN NOW()
                    ELSE vk_user_state.history_updated_at
                END,
                updated_at = NOW()
            """,
            user_id,
            mode,
            checked_model,
            serialized_history,
        )


async def claim_vk_callback_message(
    *,
    event_id: str,
    group_id: int,
    peer_id: int,
    conversation_message_id: int,
    message_id: int,
    retention_seconds: int = 24 * 60 * 60,
) -> bool:
    """Claims both VK event_id and the immutable official message identity."""
    if (
        not isinstance(event_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id)
    ):
        raise ValueError("Некорректный VK event_id")
    identifiers = (
        group_id,
        peer_id,
        conversation_message_id,
    )
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        for value in identifiers
    ):
        raise ValueError("Некорректный идентификатор сообщения VK")
    if (
        not isinstance(message_id, int)
        or isinstance(message_id, bool)
        or message_id < 0
    ):
        raise ValueError("Некорректный message_id VK")
    checked_retention = max(
        60 * 60,
        min(int(retention_seconds), 7 * 24 * 60 * 60),
    )
    async with _pool.acquire() as conn:
        inserted = await conn.fetchval(
            """
            INSERT INTO vk_callback_events(
                event_id,
                group_id,
                peer_id,
                conversation_message_id,
                message_id,
                expires_at
            )
            VALUES (
                $1,
                $2,
                $3,
                $4,
                $5,
                NOW() + ($6::integer * INTERVAL '1 second')
            )
            ON CONFLICT DO NOTHING
            RETURNING event_id
            """,
            event_id,
            group_id,
            peer_id,
            conversation_message_id,
            message_id,
            checked_retention,
        )
    return inserted is not None


async def release_vk_callback_event(event_id: str) -> None:
    """Releases an event only when it could not be queued for processing."""
    if (
        not isinstance(event_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event_id)
    ):
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM vk_callback_events WHERE event_id = $1",
            event_id,
        )


# ─── Статистика запросов ───────────────────────────────────────────────────────

async def acquire_user_request(
    user_id: int,
    ttl_seconds: int = USER_REQUEST_LEASE_SECONDS,
) -> str | None:
    """Атомарно занимает единственный AI-слот пользователя.

    Запись хранится в PostgreSQL, поэтому запрет работает между Telegram,
    сайтом, процессами и несколькими экземплярами Render. Истёкшую аренду
    можно безопасно заменить после аварийного завершения процесса.
    """
    if (
        isinstance(user_id, bool)
        or not isinstance(user_id, int)
        or user_id <= 0
        or user_id > 2**63 - 1
    ):
        raise ValueError("Некорректный user_id")
    checked_ttl = max(60, min(int(ttl_seconds), 1800))
    lease_token = secrets.token_urlsafe(32)
    async with _pool.acquire() as conn:
        acquired = await conn.fetchval(
            """
            INSERT INTO active_user_requests(user_id, lease_token, expires_at)
            VALUES (
                $1,
                $2,
                NOW() + ($3::integer * INTERVAL '1 second')
            )
            ON CONFLICT (user_id)
            DO UPDATE SET
                lease_token = EXCLUDED.lease_token,
                expires_at = EXCLUDED.expires_at
            WHERE active_user_requests.expires_at <= NOW()
            RETURNING lease_token
            """,
            user_id,
            lease_token,
            checked_ttl,
        )
    return lease_token if acquired == lease_token else None


async def release_user_request(user_id: int, lease_token: str) -> None:
    """Освобождает только ту аренду, которую получил текущий обработчик."""
    if not isinstance(lease_token, str) or not lease_token:
        return
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM active_user_requests
            WHERE user_id = $1 AND lease_token = $2
            """,
            user_id,
            lease_token,
        )


async def _renew_user_request(
    user_id: int,
    lease_token: str,
    ttl_seconds: int,
) -> bool:
    """Продлевает только всё ещё принадлежащую обработчику аренду."""
    async with _pool.acquire() as conn:
        renewed = await conn.fetchval(
            """
            UPDATE active_user_requests
            SET expires_at = NOW() + ($3::integer * INTERVAL '1 second')
            WHERE user_id = $1 AND lease_token = $2
            RETURNING user_id
            """,
            user_id,
            lease_token,
            ttl_seconds,
        )
    return renewed == user_id


async def _is_user_request_owned(user_id: int, lease_token: str) -> bool:
    """Проверяет владение и актуальный статус доступа перед выдачей ответа."""
    async with _pool.acquire() as conn:
        owned = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1
                FROM active_user_requests AS active
                JOIN users ON users.user_id = active.user_id
                WHERE active.user_id = $1
                  AND active.lease_token = $2
                  AND active.expires_at > NOW()
                  AND users.status = 'approved'
            )
            """,
            user_id,
            lease_token,
        )
    return bool(owned)


async def _request_lease_heartbeat(
    user_id: int,
    lease_token: str,
    ttl_seconds: int,
    lost_event: asyncio.Event,
) -> None:
    # Частые короткие heartbeat-запросы дают время безопасно остановить
    # обработчик до истечения аренды даже после временного сбоя БД.
    interval = max(5, min(30, ttl_seconds // 3))
    retry_delay = interval
    valid_until = asyncio.get_running_loop().time() + ttl_seconds
    while True:
        await asyncio.sleep(retry_delay)
        try:
            renewed = await _renew_user_request(
                user_id,
                lease_token,
                ttl_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Не удалось продлить аренду AI-запроса user_id=%s",
                user_id,
            )
            remaining = valid_until - asyncio.get_running_loop().time()
            if remaining <= 2:
                lost_event.set()
                return
            # После ошибки повторяем быстрее, а не ждём полный обычный интервал.
            retry_delay = max(1, min(5, int(remaining / 2)))
            continue
        if not renewed:
            logger.error(
                "Аренда AI-запроса потеряна до завершения user_id=%s",
                user_id,
            )
            lost_event.set()
            return
        valid_until = asyncio.get_running_loop().time() + ttl_seconds
        retry_delay = interval


class RequestLeaseLostError(RuntimeError):
    """Эксклюзивная аренда была потеряна до завершения AI-операции."""


class UserRequestLease:
    """Запускает операцию вместе с наблюдением за её PostgreSQL-арендой."""

    def __init__(
        self,
        user_id: int,
        lease_token: str,
        lost_event: asyncio.Event,
    ):
        self.user_id = user_id
        self.lease_token = lease_token
        self._lost_event = lost_event

    async def run(self, awaitable):
        operation_task = asyncio.create_task(awaitable)
        lost_task = asyncio.create_task(self._lost_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {operation_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if lost_task in done and self._lost_event.is_set():
                operation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await operation_task
                raise RequestLeaseLostError(
                    "Эксклюзивная аренда AI-запроса потеряна"
                )
            result = await operation_task
            # Повторная проверка закрывает окно между последним heartbeat и
            # выдачей ответа, в том числе при отзыве доступа администратором.
            if (
                self._lost_event.is_set()
                or not await _is_user_request_owned(
                    self.user_id,
                    self.lease_token,
                )
            ):
                raise RequestLeaseLostError(
                    "Эксклюзивная аренда AI-запроса потеряна"
                )
            return result
        finally:
            lost_task.cancel()
            with suppress(asyncio.CancelledError):
                await lost_task


@asynccontextmanager
async def user_request_slot(
    user_id: int,
    ttl_seconds: int = USER_REQUEST_LEASE_SECONDS,
):
    """Контекст единственного одновременно выполняемого AI-запроса."""
    checked_ttl = max(60, min(int(ttl_seconds), 1800))
    lease_token = await acquire_user_request(user_id, checked_ttl)
    heartbeat_task = None
    lease = None
    if lease_token is not None:
        lost_event = asyncio.Event()
        lease = UserRequestLease(user_id, lease_token, lost_event)
        heartbeat_task = asyncio.create_task(
            _request_lease_heartbeat(
                user_id,
                lease_token,
                checked_ttl,
                lost_event,
            )
        )
    try:
        yield lease
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        if lease_token is not None:
            try:
                await release_user_request(user_id, lease_token)
            except Exception:
                # Не маскируем успешный ответ ошибкой очистки: короткая аренда
                # всё равно истечёт и будет удалена фоновым сборщиком.
                logger.exception(
                    "Не удалось освободить аренду AI-запроса user_id=%s",
                    user_id,
                )


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
    quota_class = "image" if safe_model == "image-generation" else "ai"
    global_limit = (
        GLOBAL_DAILY_IMAGE_LIMIT
        if quota_class == "image"
        else GLOBAL_DAILY_AI_LIMIT
    )

    async with _pool.acquire() as conn:
        try:
            async with conn.transaction():
                global_reserved = await conn.fetchval(
                    """
                    INSERT INTO global_request_usage_daily (
                        quota_class, usage_date, request_count
                    )
                    VALUES (
                        $1, (NOW() AT TIME ZONE 'UTC')::date, 1
                    )
                    ON CONFLICT (quota_class, usage_date)
                    DO UPDATE SET request_count =
                        global_request_usage_daily.request_count + 1
                    WHERE global_request_usage_daily.request_count < $2
                    RETURNING request_count
                    """,
                    quota_class,
                    global_limit,
                )
                if global_reserved is None:
                    raise _QuotaExhausted

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
                    raise _QuotaExhausted
                await conn.execute(
                    """
                    INSERT INTO request_stats (user_id, model, source)
                    VALUES ($1, $2, $3)
                    """,
                    user_id,
                    safe_model,
                    source,
                )
        except _QuotaExhausted:
            # Исключение покидает transaction-блок, поэтому возможный первый
            # инкремент тоже откатывается и не расходует другую квоту.
            return False
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


async def get_global_usage_today() -> dict[str, int]:
    """Возвращает агрегированное потребление без данных запросов."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT quota_class, request_count
            FROM global_request_usage_daily
            WHERE usage_date = (NOW() AT TIME ZONE 'UTC')::date
            """
        )
    usage = {"ai": 0, "image": 0}
    for row in rows:
        quota_class = str(row["quota_class"])
        if quota_class in usage:
            usage[quota_class] = max(0, int(row["request_count"]))
    return usage


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


async def create_login_code(user_id: int, length: int = 10, ttl_seconds: int = 600) -> str:
    """Создаёт один активный код. В БД сохраняется только HMAC-SHA-256 кода."""
    if length < 10:
        raise ValueError("Код входа должен содержать минимум 10 символов")
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
    """Удаляет истёкшие данные аутентификации, историю и статистику."""
    if conn is not None:
        await conn.execute("DELETE FROM login_codes WHERE expires_at < NOW()")
        await conn.execute("DELETE FROM web_sessions WHERE expires_at < NOW()")
        await conn.execute(
            "DELETE FROM active_user_requests WHERE expires_at <= NOW()"
        )
        await conn.execute(
            "DELETE FROM vk_callback_events WHERE expires_at <= NOW()"
        )
        await conn.execute(
            """
            UPDATE vk_user_state
            SET chat_history = '[]'::jsonb,
                history_updated_at = NULL
            WHERE history_updated_at <=
                NOW() - ($1::integer * INTERVAL '1 hour')
              AND chat_history <> '[]'::jsonb
            """,
            VK_CHAT_HISTORY_RETENTION_HOURS,
        )
        await conn.execute(
            """
            DELETE FROM request_usage_daily
            WHERE usage_date < (NOW() AT TIME ZONE 'UTC')::date - $1::integer
            """,
            REQUEST_STATS_RETENTION_DAYS,
        )
        await conn.execute(
            """
            DELETE FROM global_request_usage_daily
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


async def healthcheck() -> bool:
    """Проверяет готовность пула и PostgreSQL без раскрытия деталей ошибки."""
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1
    except (asyncpg.PostgresError, OSError, RuntimeError):
        logger.warning("Проверка готовности PostgreSQL завершилась ошибкой")
        return False


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
