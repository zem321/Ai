import os
import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from handlers.start_handler import router as start_router
from handlers.chat_handler import router as chat_router
from handlers.image_handler import router as image_router
from middleware import AccessMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_KEY = os.getenv("API_KEY", "")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")


async def health(request):
    return web.Response(text="OK")


async def miniapp(request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            content = f.read()
        # Подставляем API ключ прямо в HTML
        content = content.replace(
            'localStorage.getItem("api_key") || ""',
            f'localStorage.getItem("api_key") || "{API_KEY}"'
        )
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    except FileNotFoundError:
        return web.Response(text="Mini App not found", status=404)


async def start_web():
    app = web.Application()
    app.router.add_get("/", miniapp)
    app.router.add_get("/app", miniapp)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port}")


async def set_commands(bot: Bot):
    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="chat", description="💬 Режим чата"),
        BotCommand(command="clear", description="🗑 Очистить историю"),
        BotCommand(command="admin", description="🛠 Админ панель"),
    ])


async def main():
    await start_web()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AccessMiddleware())
    dp.callback_query.middleware(AccessMiddleware())
    dp.include_router(start_router)
    dp.include_router(chat_router)
    dp.include_router(image_router)
    await set_commands(bot)
    logger.info("Бот запущен!")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
