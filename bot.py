import os
import asyncio
import contextlib
import logging
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
from webapp_api import setup_webapp_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
try:
    ADMIN_ID = int(os.environ["ADMIN_ID"])
except (KeyError, TypeError, ValueError) as exc:
    raise RuntimeError("ADMIN_ID должен быть задан положительным целым числом") from exc
if ADMIN_ID <= 0:
    raise RuntimeError("ADMIN_ID должен быть положительным Telegram ID")
MAX_REQUEST_BYTES = max(
    1024 * 1024,
    min(int(os.getenv("MAX_REQUEST_BYTES", str(10 * 1024 * 1024))), 20 * 1024 * 1024),
)
INDEX_PATH = Path(__file__).resolve().with_name("index.html")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")


@web.middleware
async def security_headers(request: web.Request, handler):
    csp_nonce = secrets.token_urlsafe(24)
    request["csp_nonce"] = csp_nonce
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        response = exc
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "base-uri 'none'; object-src 'none'; form-action 'self'; "
        "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; "
        f"script-src 'self' 'nonce-{csp_nonce}' https://telegram.org; "
        "script-src-attr 'none'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob:; "
        "connect-src 'self';",
    )
    # Браузер учитывает HSTS только если ответ уже получен по HTTPS.
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    if request.path == "/" or request.path == "/app" or request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers.setdefault("Pragma", "no-cache")
    return response


async def health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


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
        middlewares=[security_headers],
    )
    app.router.add_get("/", miniapp)
    app.router.add_get("/app", miniapp)
    app.router.add_get("/health", health)

    # Роуты HTTP API для мини-аппа: /api/me, /api/chat, /api/image,
    # а также /api/auth/code и /api/auth/logout — вход на сайт вне Telegram
    # по одноразовому коду, который выдаёт бот (команда /code).
    # Логика внутри переиспользует call_ai()/generate_image() из тех же
    # модулей, что использует и сам бот — никакой новой бизнес-логики.
    setup_webapp_routes(app)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", "8080"))
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
