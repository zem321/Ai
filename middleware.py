import os
import logging
import asyncio
import aiohttp
from aiohttp import web

# Импорты aiogram
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.types import Message, CallbackQuery

# Импорт вашей базы данных (должен лежать в database.py рядом)
import database as db 

# =====================================================================
# ЕСЛИ ВАШИ ХЕНДЛЕРЫ БОТА НАХОДЯТСЯ В ДРУГОМ ФАЙЛЕ (НАПРИМЕР, handlers.py),
# РАСКОММЕНТИРУЙТЕ СТРОКУ НИЖЕ И ИЗМЕНИТЕ НАЗВАНИЕ ИМПОРТА:
# from handlers import router as bot_router
# =====================================================================

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Переменные окружения из панели Railway
API_KEY = os.getenv("API_KEY", "КЛЮЧ_НЕ_ЗАДАН")
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТОКЕН_НЕ_ЗАДАН")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

PUBLIC_COMMANDS = {"/start", "/help"}

# Эндпоинты Izisoft
IZISOFT_CHAT_URL = "https://ai-proxy.izisoft.xyz/v1/chat/completions"
IZISOFT_GEN_URL = "https://ai-proxy.izisoft.xyz/v1/images/generations"
IZISOFT_EDIT_URL = "https://ai-proxy.izisoft.xyz/v1/images/edits"


# ================= МИДЛВАРЬ КОНТВОЛЯ ДОСТУПА =================
class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            user_id = event.from_user.id
            text = event.text or ""
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
            text = ""
        else:
            return await handler(event, data)

        if user_id == ADMIN_ID:
            return await handler(event, data)

        if isinstance(event, Message) and any(text.startswith(c) for c in PUBLIC_COMMANDS):
            return await handler(event, data)

        if db.is_approved(user_id):
            return await handler(event, data)

        if isinstance(event, Message):
            if db.is_pending(user_id):
                await event.answer("⏳ Твой запрос на рассмотрении. Ожидай одобрения.")
            elif db.is_rejected(user_id):
                await event.answer("🚫 Доступ отклонён.")
            else:
                await event.answer("👋 Напиши /start чтобы запросить доступ.")
        elif isinstance(event, CallbackQuery):
            await event.answer("🚫 Нет доступа. Напиши /start.", show_alert=True)


# ================= СЕРВЕРНАЯ ЧАСТЬ И БЕЗОПАСНЫЙ ПРОКСИ =================
async def health(request):
    return web.Response(text="OK", status=200)

async def serve_webapp(request):
    if os.path.exists("index.html"):
        return web.FileResponse("index.html")
    return web.Response(text="index.html не найден", status=404)

async def proxy_chat(request):
    try:
        body = await request.json()
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(IZISOFT_CHAT_URL, json=body, headers=headers) as resp:
                return web.json_response(await resp.json(), status=resp.status)
    except Exception as e:
        return web.json_response({"error": {"message": str(e)}}, status=500)

async def proxy_image_gen(request):
    try:
        body = await request.json()
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(IZISOFT_GEN_URL, json=body, headers=headers) as resp:
                return web.json_response(await resp.json(), status=resp.status)
    except Exception as e:
        return web.json_response({"error": {"message": str(e)}}, status=500)

async def proxy_image_edit(request):
    try:
        data = await request.post()
        form = aiohttp.FormData()
        for key, value in data.items():
            if isinstance(value, web.FileField):
                form.add_field(key, value.file.read(), filename=value.filename, content_type=value.content_type)
            else:
                form.add_field(key, str(value))
        headers = {"Authorization": f"Bearer {API_KEY}"}
        async with aiohttp.ClientSession() as session:
            async with session.post(IZISOFT_EDIT_URL, data=form, headers=headers) as resp:
                return web.json_response(await resp.json(), status=resp.status)
    except Exception as e:
        return web.json_response({"error": {"message": str(e)}}, status=500)


# ================= ИНИЦИАЛИЗАЦИЯ И СВЯЗЫВАНИЕ КОМПОНЕНТОВ =================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Подключение вашей мидлвари контроля доступа
dp.message.middleware(AccessMiddleware())
dp.callback_query.middleware(AccessMiddleware())

# ЕСЛИ ВЫ ИСПОЛЬЗУЕТЕ ВНЕШНИЙ РОУТЕР ХЕНДЛЕРОВ, РАСКОММЕНТИРУЙТЕ СТРОКУ НИЖЕ:
# dp.include_router(bot_router)


# ================= ХЕНДЛЕРЫ БОТА (ЕСЛИ ОНИ БЫЛИ В MAIN.PY) =================
# Если у вас есть функции вроде @dp.message(Command("start")), можете вставить их прямо сюда.
@dp.message()
async def default_handler(message: Message):
    if message.text == "/start":
        await message.answer("👋 Привет! Используй кнопку меню, чтобы открыть ИИ-ассистента.")
    else:
        await message.answer("🤖 Все запросы к нейросети выполняются через графический интерфейс Mini App.")


# ================= УПРАВЛЕНИЕ ЖИЗНЕННЫМ ЦИКЛОМ ПРИЛОЖЕНИЯ =================
async def start_bot_background(app):
    logger.info("Запуск Telegram-бота в фоновом режиме внутри веб-сервера...")
    app['bot_task'] = asyncio.create_task(dp.start_polling(bot))

async def stop_bot_background(app):
    logger.info("Остановка Telegram-бота и очистка сессий...")
    app['bot_task'].cancel()
    try:
        await app['bot_task']
    except asyncio.CancelledError:
        pass
    await bot.session.close()

async def init_app():
    app = web.Application()
    
    # Роуты веб-сервера для Mini App фронтенда
    app.router.add_get("/", serve_webapp)
    app.router.add_get("/health", health)
    
    # Защищенные Middleware-роуты до Izisoft
    app.router.add_post("/api/chat", proxy_chat)
    app.router.add_post("/api/image/gen", proxy_image_gen)
    app.router.add_post("/api/image/edit", proxy_image_edit)
    
    # Привязка событий старта и остановки бота к веб-серверу
    app.on_startup.append(start_bot_background)
    app.on_shutdown.append(stop_bot_background)
    
    return app

if __name__ == "__main__":
    if API_KEY == "КЛЮЧ_НЕ_ЗАДАН" or BOT_TOKEN == "ТОКЕН_НЕ_ЗАДАН":
        logger.error("ВНИМАНИЕ: Проверьте переменные окружения в настройках Railway!")
        
    port = int(os.getenv("PORT", 8080))
    web.run_app(init_app(), host="0.0.0.0", port=port)
