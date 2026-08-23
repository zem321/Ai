import os
import asyncio
import contextlib
import logging
import re
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, LinkPreviewOptions
from aiogram.fsm.storage.memory import MemoryStorage

import database as db
from build_info import BUILD_BRANCH, BUILD_SHA
from handlers.start_handler import router as start_router
from handlers.chat_handler import router as chat_router
from handlers.image_handler import router as image_router
from handlers.vk_handler import setup_vk_callback_routes
from middleware import AccessMiddleware
from provider_keys import PROVIDER_KEY_ENV_NAMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
try:
    ADMIN_ID = int(os.environ["ADMIN_ID"])
except (KeyError, TypeError, ValueError) as exc:
    raise RuntimeError("ADMIN_ID должен быть задан положительным целым числом") from exc
if ADMIN_ID <= 0 or ADMIN_ID > 2**63 - 1:
    raise RuntimeError("ADMIN_ID должен быть положительным Telegram ID")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")
if not re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", BOT_TOKEN):
    raise RuntimeError("BOT_TOKEN имеет некорректный формат")
if os.getenv("DEBUG_MODE", "0").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}:
    raise RuntimeError("DEBUG_MODE должен быть отключён на production-сервере")


def _validate_required_provider_secret(name: str, value: str) -> None:
    checked = value.strip()
    placeholder_markers = (
        "replace-with",
        "changeme",
        "change-me",
        "your-key",
        "your-secret",
    )
    if len(checked.encode("utf-8")) < 20:
        raise RuntimeError(f"{name} не задан или слишком короткий")
    if any(marker in checked.lower() for marker in placeholder_markers):
        raise RuntimeError(f"{name} содержит демонстрационное значение")
    if checked != value or any(char.isspace() for char in checked):
        raise RuntimeError(f"{name} не должен содержать пробелы")


provider_secrets: list[str] = []
for provider, env_names in PROVIDER_KEY_ENV_NAMES.items():
    for env_name in env_names:
        provider_secret = os.getenv(env_name, "")
        _validate_required_provider_secret(env_name, provider_secret)
        provider_secrets.append(provider_secret.strip())
_validate_required_provider_secret("CLOUDFLARE_API_TOKEN", CLOUDFLARE_API_TOKEN)
if not re.fullmatch(r"[0-9a-fA-F]{32}", CLOUDFLARE_ACCOUNT_ID):
    raise RuntimeError("CLOUDFLARE_ACCOUNT_ID имеет некорректный формат")
configured_secrets = {
    BOT_TOKEN,
    *provider_secrets,
    CLOUDFLARE_API_TOKEN.strip(),
}
if len(configured_secrets) != 11:
    raise RuntimeError(
        "BOT_TOKEN, три ключа NVIDIA, три ключа Gemini, три ключа Groq, "
        "CLOUDFLARE_API_TOKEN должен быть отдельным секретом"
    )


@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Необработанная ошибка HTTP-маршрута")
        response = web.Response(text="Внутренняя ошибка сервера.", status=500)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("Pragma", "no-cache")
    response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    response.headers["Server"] = "bot"
    return response


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok", content_type="text/plain")


async def ready(request: web.Request) -> web.Response:
    try:
        database_ready = await asyncio.wait_for(
            db.healthcheck(),
            timeout=2,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        database_ready = False
    return web.json_response(
        {
            "status": "ready" if database_ready else "not_ready",
            "build_sha": BUILD_SHA,
            "build_branch": BUILD_BRANCH,
        },
        status=200 if database_ready else 503,
    )


async def start_http() -> web.AppRunner:
    app = web.Application(middlewares=[security_headers])
    app.router.add_get("/health", health)
    app.router.add_get("/ready", ready)
    setup_vk_callback_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    if port < 1 or port > 65535:
        raise RuntimeError("PORT должен быть от 1 до 65535")
    site = web.TCPSite(runner, "0.0.0.0", port)  # nosec B104
    await site.start()

    logger.info(
        "HTTP server started on port %s build_sha=%s branch=%s",
        port,
        BUILD_SHA,
        BUILD_BRANCH,
    )
    return runner


async def cleanup_expired_data_loop():
    while True:
        try:
            await db.cleanup_expired_data()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка фоновой очистки данных")
        await asyncio.sleep(60 * 60)


def _seconds_until_next_daily_run(hour_utc: int, minute_utc: int = 0) -> float:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=minute_utc, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def daily_healthcheck_loop(bot: Bot):
    """Полный health-check всех ключей всех провайдеров + UptimeRobot,
    раз в сутки в 07:00 UTC (10:00 МСК). Отчёт уходит админу в Telegram."""
    from healthcheck import run_full_healthcheck, format_report

    while True:
        try:
            await asyncio.sleep(_seconds_until_next_daily_run(hour_utc=7))
            report = await run_full_healthcheck()
            text = format_report(report)
            chunk = ""
            for line in text.split("\n"):
                if len(chunk) + len(line) + 1 > 3800:
                    await bot.send_message(
                        ADMIN_ID,
                        chunk,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                    chunk = ""
                chunk += line + "\n"
            if chunk.strip():
                await bot.send_message(
                    ADMIN_ID,
                    chunk,
                    parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка планового healthcheck")


async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="chat", description="Режим чата"),
        BotCommand(command="clear", description="Очистить историю"),
        BotCommand(command="admin", description="Админ панель"),
        BotCommand(command="healthcheck", description="Проверка всех AI-ключей"),
    ])


async def main():
    await db.init_db()

    await db.approve_user(ADMIN_ID)
    logger.info("Admin %s approved on startup", ADMIN_ID)

    http_runner = await start_http()
    cleanup_task = asyncio.create_task(cleanup_expired_data_loop())

    bot = Bot(token=BOT_TOKEN)
    healthcheck_task = asyncio.create_task(daily_healthcheck_loop(bot))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())

    dp.include_router(start_router)
    dp.include_router(chat_router)
    dp.include_router(image_router)

    try:
        await set_commands(bot)
        await bot.delete_webhook(drop_pending_updates=True)

        logger.info(
            "Бот запущен: Telegram; VK Callback API работает независимо"
        )
        await dp.start_polling(bot, skip_updates=True)
    finally:
        cleanup_task.cancel()
        healthcheck_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        with contextlib.suppress(asyncio.CancelledError):
            await healthcheck_task
        await bot.session.close()
        await http_runner.cleanup()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())


