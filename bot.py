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

# ИСПРАВЛЕНО: Точные эндпоинты согласно спецификации OpenAI API
CHAT_URL = "https://ai-proxy.izisoft.xyz/v1/chat/completions"
IMAGE_GEN_URL = "https://ai-proxy.izisoft.xyz/v1/images/generations"
IMAGE_EDIT_URL = "https://ai-proxy.izisoft.xyz/v1/images/edits"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

# Стандартные заголовки для ответов Mini App (решают проблемы с блокировкой браузером)
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization"
}

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
    try:
        body = await request.json()
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(CHAT_URL, json=body, headers=headers, timeout=30) as resp:
                data = await resp.text()
                return web.Response(text=data, content_type="application/json", status=resp.status, headers=CORS_HEADERS)
    except Exception as e:
        logger.error(f"Исключение в proxy_chat: {str(e)}")
        return web.Response(
            text=json.dumps({"error": {"message": f"Ошибка бэкенда: {str(e)}"}}),
            content_type="application/json",
            status=500,
            headers=CORS_HEADERS
        )

async def proxy_image_gen(request):
    try:
        body = await request.json()
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        logger.info(f"Отправка запроса на генерацию к прокси: {IMAGE_GEN_URL}")
        async with aiohttp.ClientSession() as session:
            async with session.post(IMAGE_GEN_URL, json=body, headers=headers, timeout=60) as resp:
                data = await resp.text()
                logger.info(f"Ответ прокси генерации (Статус {resp.status}): {data}")
                return web.Response(text=data, content_type="application/json", status=resp.status, headers=CORS_HEADERS)
    except Exception as e:
        logger.error(f"Исключение в proxy_image_gen: {str(e)}")
        return web.Response(
            text=json.dumps({"error": {"message": f"Ошибка бэкенда генерации: {str(e)}"}}),
            content_type="application/json",
            status=500,
            headers=CORS_HEADERS
        )

async def proxy_image_edit(request):
    try:
        # ИСПРАВЛЕНО: Надежное чтение multipart данных из фронтенда и проброс в FormData
        reader = await request.multipart()
        data_to_forward = aiohttp.FormData()
        
        async for field in reader:
            field_data = await field.read()
            if field.filename:
                # Передаем файл как бинарник с сохранением имени и типа
                c_type = field.headers.get(aiohttp.hdrs.CONTENT_TYPE, "image/png")
                data_to_forward.add_field(
                    field.name, 
                    field_data, 
                    filename=field.filename, 
                    content_type=c_type
                )
            else:
                data_to_forward.add_field(field.name, field_data.decode('utf-8', errors='ignore'))
        
        headers = {"Authorization": f"Bearer {API_KEY}"}
        logger.info("Отправка запроса на редактирование к прокси...")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(IMAGE_EDIT_URL, data=data_to_forward, headers=headers, timeout=60) as resp:
                data = await resp.text()
                logger.info(f"Ответ прокси редактирования (Статус {resp.status}): {data}")
                return web.Response(text=data, content_type="application/json", status=resp.status, headers=CORS_HEADERS)
                
    except Exception as e:
        logger.error(f"Исключение в proxy_image_edit: {str(e)}")
        return web.Response(
            text=json.dumps({"error": {"message": f"Ошибка бэкенда редактирования: {str(e)}"}}),
            content_type="application/json",
            status=500,
            headers=CORS_HEADERS
        )

async def handle_options(request):
    return web.Response(status=200, headers=CORS_HEADERS)

async def start_web():
    app = web.Application()
    app.router.add_get("/", miniapp)
    app.router.add_get("/app", miniapp)
    app.router.add_get("/health", health)
    app.router.add_post("/api/chat", proxy_chat)
    app.router.add_post("/api/image/gen", proxy_image_gen)
    app.router.add_post("/api/image/edit", proxy_image_edit)
    
    # Регистрация OPTIONS запросов для всех эндпоинтов
    for path in ["/api/chat", "/api/image/gen", "/api/image/edit"]:
        app.router.add_route("OPTIONS", path, handle_options)
        
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
