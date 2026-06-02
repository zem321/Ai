import os
import asyncio
import logging
import json
import aiohttp
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
CHAT_URL = "https://ai-proxy.izisoft.xyz/v1/chat/completions"
IMAGE_GEN_URL = "https://ai-proxy.izisoft.xyz/v1/image/generation"
IMAGE_EDIT_URL = "https://ai-proxy.izisoft.xyz/v1/images/edits"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")


async def health(request):
    return web.Response(text="OK")


async def miniapp(request):
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace(
            'localStorage.getItem("api_key") || ""',
            f'localStorage.getItem("api_key") || "{API_KEY}"'
        )
        return web.Response(text=content, content_type="text/html", charset="utf-8")
    except FileNotFoundError:
        return web.Response(text="Mini App not found", status=404)


async def proxy_chat(request):
    """Прокси для чат запросов"""
    try:
        body = await request.json()
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(CHAT_URL, json=body, headers=headers) as resp:
                data = await resp.text()
                return web.Response(
                    text=data,
                    content_type="application/json",
                    status=resp.status,
                    headers={"Access-Control-Allow-Origin": "*"}
                )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": {"message": str(e)}}),
            content_type="application/json",
            status=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )


async def proxy_image_gen(request):
    """Прокси для генерации изображений"""
    try:
        body = await request.json()
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(IMAGE_GEN_URL, json=body, headers=headers) as resp:
                data = await resp.text()
                return web.Response(
                    text=data,
                    content_type="application/json",
                    status=resp.status,
                    headers={"Access-Control-Allow-Origin": "*"}
                )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": {"message": str(e)}}),
            content_type="application/json",
            status=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )


async def proxy_image_edit(request):
    """Прокси для редактирования изображений"""
    try:
        reader = await request.multipart()
        form_data = aiohttp.FormData()
        async for field in reader:
            data = await field.read()
            if field.filename:
                form_data.add_field(field.name, data, filename=field.filename, content_type=field.content_type)
            else:
                form_data.add_field(field.name, data.decode())
        headers = {"Authorization": f"Bearer {API_KEY}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(IMAGE_EDIT_URL, data=form_data, headers=headers) as resp:
                data = await resp.text()
                return web.Response(
                    text=data,
                    content_type="application/json",
                    status=resp.status,
                    headers={"Access-Control-Allow-Origin": "*"}
                )
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": {"message": str(e)}}),
            content_type="application/json",
            status=500,
            headers={"Access-Control-Allow-Origin": "*"}
        )


async def handle_options(request):
    """CORS preflight"""
    return web.Response(
        status=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }
    )


async def start_web():
    app = web.Application()
    # Главная страница Mini App теперь доступна и по корню /
    app.router.add_get("/", miniapp)
    app.router.add_get("/app", miniapp)
    app.router.add_get("/health", health)
    
    # API эндпоинты для фронтенда index.html
    app.router.add_post("/api/chat", proxy_chat)
    app.router.add_post("/api/image/gen", proxy_image_gen)
    app.router.add_post("/api/image/edit", proxy_image_edit)
    
    # Защита CORS preflight запросов
    app.router.add_route("OPTIONS", "/api/chat", handle_options)
    app.router.add_route("OPTIONS", "/api/image/gen", handle_options)
    app.router.add_route("OPTIONS", "/api/image/edit", handle_options)
    
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
