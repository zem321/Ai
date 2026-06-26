import os
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")

_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL)
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
        await conn.execute(
            """
            INSERT INTO users (user_id, status) VALUES ($1, 'rejected')
            ON CONFLICT (user_id) DO UPDATE SET status = 'rejected'
            """,
            user_id,
        )


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
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO request_stats (user_id, model, source) VALUES ($1, $2, $3)",
                user_id, model or "", source,
            )
    except Exception:
        pass  # статистика не должна ронять основной поток


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


async def get_all_users_with_stats() -> list[dict]:
    """Список всех пользователей со статусом и краткой статистикой."""
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
            """
        )
    return [dict(r) for r in rows]
