import os
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

import database as db
from keyboards import main_menu_keyboard, admin_notify_keyboard, admin_panel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WELCOME_TEXT = """
👋 <b>Привет! Я твой продвинутый ИИ-ассистент</b>

Я умею:
• 💬 Общаться (ChatGPT, Claude, Llama, Qwen)
• 🖼 Анализировать фото по запросу
• 🎨 Генерировать изображения (Flux, SD)
• ✏️ Редактировать фото по описанию
• 🎥 Создавать видео (NVIDIA Cosmos)
• 🎬 Оживлять фотографии

Выбери режим 👇
"""

async def notify_admin(bot: Bot, user):
    if not ADMIN_ID:
        return
    username = f"@{user.username}" if user.username else "нет username"
    text = (
        f"🔔 <b>Новый запрос на доступ!</b>\n\n"
        f"👤 <b>Имя:</b> {user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
        f"📱 <b>Username:</b> {username}\n\n"
        f"Выдать доступ?"
    )
    try:
        await bot.send_message(ADMIN_ID, text, reply_markup=admin_notify_keyboard(user.id), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Admin notify error: {e}")

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        db.approve_user(user_id)
        await state.set_state(BotStates.main_menu)
        await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        return
    if db.is_approved(user_id):
        await state.set_state(BotStates.main_menu)
        await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        return
    if db.is_pending(user_id):
        await message.answer("⏳ <b>Запрос уже отправлен!</b> Ожидай одобрения.", parse_mode="HTML")
        return
    if db.is_rejected(user_id):
        await message.answer("🚫 <b>Доступ отклонён.</b>", parse_mode="HTML")
        return
    db.add_pending(user_id)
    await notify_admin(message.bot, message.from_user)
    await message.answer("📨 <b>Запрос отправлен!</b>\n\nОжидай одобрения от администратора.", parse_mode="HTML")

@router.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.main_menu)
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    text = (
        "<b>❓ Помощь</b>\n\n"
        "💬 <b>Чат</b> — вопросы + фото с подписью\n"
        "🎨 <b>Фото / Видео</b> — генерация по тексту\n"
        "✏️ <b>Изменить / Оживить</b> — отправь фото с заданием в подписи\n"
        "🤖 <b>Модели</b> — настройка нейросетей\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/admin — админ панель"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer(
        f"🛠 <b>Панель администратора</b>\n\n✅ Одобрено: <b>{len(db.get_all_approved())}</b>\n⏳ Ожидают: <b>{len(db.get_all_pending())}</b>",
        reply_markup=admin_panel_keyboard(), parse_mode="HTML"
    )

# Ниже остаются старые обработчики кнопок админа (cb_approve, cb_reject, и т.д.)
# Вы можете просто скопировать их из своего старого файла start_handler.py
# (они работают без изменений)
