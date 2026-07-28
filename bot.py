import os
import asyncio
import contextlib
import hashlib
import logging
import re
import secrets
from pathlib import Path
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

import database as db
from handlers.start_handler import router as start_router
from handlers.chat_handler import router as chat_router
from handlers.image_handler import router as image_router
from handlers.webapp_login_handler import router as webapp_login_router
from middleware import AccessMiddleware
from webapp_api import api_rate_limit_middleware, setup_webapp_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")
LOGIN_CODE_PEPPER = os.getenv("LOGIN_CODE_PEPPER", "")
try:
    ADMIN_ID = int(os.environ["ADMIN_ID"])
except (KeyError, TypeError, ValueError) as exc:
    raise RuntimeError("ADMIN_ID должен быть задан положительным целым числом") from exc
if ADMIN_ID <= 0 or ADMIN_ID > 2**63 - 1:
    raise RuntimeError("ADMIN_ID должен быть положительным Telegram ID")
MAX_REQUEST_BYTES = max(
    1024 * 1024,
    min(int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))), 20 * 1024 * 1024),
)
INDEX_PATH = Path(__file__).resolve().with_name("index.html")
TELEGRAM_SDK_PATH = (
    Path(__file__).resolve().parent / "static" / "telegram-web-app.js"
)
TELEGRAM_SDK_SHA256 = (
    "3549138a7934039fe7dfd1291a4ee739bd2b705a614308053a8b08a87d85c451"
)


def _load_verified_telegram_sdk() -> bytes:
    try:
        content = TELEGRAM_SDK_PATH.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError("Локальная копия Telegram WebApp SDK не найдена") from exc
    actual_hash = hashlib.sha256(content).hexdigest()
    if not secrets.compare_digest(actual_hash, TELEGRAM_SDK_SHA256):
        raise RuntimeError(
            "Хеш локальной копии Telegram WebApp SDK не совпадает с ожидаемым"
        )
    return content


TELEGRAM_SDK_BYTES = _load_verified_telegram_sdk()

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


_validate_required_provider_secret("GEMINI_API_KEY", GEMINI_API_KEY)
_validate_required_provider_secret("NVIDIA_API_KEY", NVIDIA_API_KEY)
configured_secrets = {
    BOT_TOKEN,
    GEMINI_API_KEY.strip(),
    NVIDIA_API_KEY.strip(),
    LOGIN_CODE_PEPPER,
}
if len(configured_secrets) != 4:
    raise RuntimeError(
        "BOT_TOKEN, GEMINI_API_KEY, NVIDIA_API_KEY и LOGIN_CODE_PEPPER "
        "должны быть разными секретами"
    )


@web.middleware
async def security_headers(request: web.Request, handler):
    csp_nonce = secrets.token_urlsafe(24)
    request["csp_nonce"] = csp_nonce
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Необработанная ошибка HTTP-маршрута")
        if request.path.startswith("/api/"):
            response = web.json_response(
                {
                    "error": "internal_error",
                    "message": "Внутренняя ошибка сервера.",
                },
                status=500,
            )
        else:
            response = web.Response(
                text="Внутренняя ошибка сервера.",
                status=500,
                content_type="text/plain",
            )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "base-uri 'none'; object-src 'none'; form-action 'self'; "
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; "
        f"script-src 'self' 'nonce-{csp_nonce}'; "
        "script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "upgrade-insecure-requests;",
    )
    # Браузер учитывает HSTS только если ответ уже получен по HTTPS.
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    if request.path == "/" or request.path == "/app" or request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
    response.headers["Server"] = "web"
    return response


async def health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def telegram_sdk(request: web.Request) -> web.Response:
    return web.Response(
        body=TELEGRAM_SDK_BYTES,
        content_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def miniapp(request: web.Request) -> web.Response:
    try:
        # Секреты провайдеров никогда не передаются браузеру.
        content = INDEX_PATH.read_text(encoding="utf-8")
        nonce_marker = "__CSP_NONCE__"
        if nonce_marker not in content:
            logger.error("В index.html отсутствует CSP nonce-маркер")
            return web.Response(text="Интерфейс временно недоступен", status=503)
        content = content.replace(nonce_marker, request["csp_nonce"])
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    except FileNotFoundError:
        logger.error("Не найден файл интерфейса: %s", INDEX_PATH)
        return web.Response(text="Интерфейс временно недоступен", status=503)


async def start_web() -> web.AppRunner:
    app = web.Application(
        client_max_size=MAX_REQUEST_BYTES,
        middlewares=[security_headers, api_rate_limit_middleware],
    )
    app.router.add_get("/", miniapp)
    app.router.add_get("/app", miniapp)
    app.router.add_get("/health", health)
    app.router.add_get(
        "/static/telegram-web-app.3549138a7934039f.js",
        telegram_sdk,
    )

    # Роуты HTTP API для мини-аппа: /api/me, /api/chat, /api/image,
    # а также /api/auth/code и /api/auth/logout — вход на сайт вне Telegram
    # по одноразовому коду, который выдаёт бот (команда /code).
    # Логика внутри переиспользует call_ai()/generate_image() из тех же
    # модулей, что использует и сам бот — никакой новой бизнес-логики.
    setup_webapp_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
    if port < 1 or port > 65535:
        raise RuntimeError("PORT должен быть от 1 до 65535")
    # Контейнерная платформа направляет HTTPS-трафик на этот внутренний порт.
    site = web.TCPSite(runner, "0.0.0.0", port)  # nosec B104
    await site.start()

    logger.info("Web server started on port %s", port)
    return runner


async def cleanup_auth_loop():
    while True:
        try:
            await db.cleanup_expired_auth()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка очистки истёкших кодов и сессий")
        await asyncio.sleep(60 * 60)


async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="chat", description="Режим чата"),
        BotCommand(command="clear", description="Очистить историю"),
        BotCommand(command="code", description="Код для входа на сайт"),
        BotCommand(command="admin", description="Админ панель"),
    ])


async def main():
    await db.init_db()

    await db.approve_user(ADMIN_ID)
    logger.info("Admin %s approved on startup", ADMIN_ID)

    web_runner = await start_web()
    cleanup_task = asyncio.create_task(cleanup_auth_loop())

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())

    dp.include_router(start_router)
    dp.include_router(chat_router)
    dp.include_router(image_router)
    dp.include_router(webapp_login_router)

    await set_commands(bot)

    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Бот запущен!")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cleanup_task
        await bot.session.close()
        await web_runner.cleanup()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
