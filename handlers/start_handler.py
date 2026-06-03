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

# Считываем ID администратора из переменных окружения
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

WELCOME_TEXT = """
👋 <b>Привет! Я твой ИИ-ассистент</b>

Я умею:
• 💬 Отвечать на вопросы (с памятью диалога)
• 🖼 Анализировать фото по твоему запросу
• 🎨 Генерировать изображения
• ✏️ Редактировать фото по описанию

Выбери нужный режим работы ниже 👇
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
        logger.error(f"Не удалось уведомить админа: {e}")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    # Принудительно сбрасываем любые зависшие состояния (важно для iOS-клиентов)
    await state.clear()
    await state.set_state(BotStates.main_menu)
    
    user_id = message.from_user.id

    # Если это создатель/админ — пускаем без каких-либо проверок базы данных
    if user_id == ADMIN_ID:
        await message.answer(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard(is_admin=True),
            parse_mode="HTML"
        )
        return

    # Логика для обычных пользователей
    if db.is_approved(user_id):
        await message.answer(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard(is_admin=False),
            parse_mode="HTML"
        )
    elif db.is_pending(user_id):
        await message.answer("⏳ <b>Твой запрос находится на рассмотрении у администратора.</b>", parse_mode="HTML")
    elif db.is_rejected(user_id):
        await message.answer("🚫 <b>Доступ к этому боту для тебя ограничен.</b>", parse_mode="HTML")
    else:
        # Если пользователя вообще нет в базе — отправляем запрос админу
        db.add_pending(user_id)
        await message.answer(
            "👋 <b>Привет! Доступ к боту ограничен.</b>\n\n"
            "Запрос на доступ автоматически отправлен администратору. Ожидайте уведомления.",
            parse_mode="HTML"
        )
        await notify_admin(message.bot, message.from_user)


@router.callback_query(F.data == "to_main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    """Хэндлер для кнопки 'Назад в главное меню'"""
    await state.clear()
    await state.set_state(BotStates.main_menu)
    user_id = callback.from_user.id
    is_admin = (user_id == ADMIN_ID)
    
    try:
        await callback.message.edit_text(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard(is_admin=is_admin),
            parse_mode="HTML"
        )
    except Exception:
        # Если сообщение нельзя отредактировать (например, там было фото), просто шлем новое
        await callback.message.answer(
            WELCOME_TEXT,
            reply_markup=main_menu_keyboard(is_admin=is_admin),
            parse_mode="HTML"
        )
    await callback.answer()


# ── АДМИН ПАНЕЛЬ ──────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    await message.answer("🛠 <b>Панель администратора:</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет прав!", show_alert=True)
        return
    await state.clear()
    await callback.message.edit_text("🛠 <b>Панель администратора:</b>", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_approve_"))
async def cb_approve_notification(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    uid = int(callback.data.replace("admin_approve_", ""))
    db.approve_user(uid)
    await callback.message.edit_text(f"✅ Пользователь <code>{uid}</code> успешно одобрен!", parse_mode="HTML")
    await callback.answer("Пользователь одобрен")
    try:
        await callback.bot.send_message(uid, "🎉 <b>Администратор одобрил твой доступ к боту!</b>\nНапиши /start для начала работы.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление пользователю: {e}")


@router.callback_query(F.data.startswith("admin_reject_"))
async def cb_reject_notification(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    uid = int(callback.data.replace("admin_reject_", ""))
    db.reject_user(uid)
    await callback.message.edit_text(f"❌ Пользователь <code>{uid}</code> отклонён.", parse_mode="HTML")
    await callback.answer("Пользователь отклонён")


@router.callback_query(F.data == "admin_pending")
async def cb_pending_list(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    pending = db.get_all_pending() if hasattr(db, "get_all_pending") else []
    if not pending:
        await callback.answer("Заявок на рассмотрении нет", show_alert=True)
        return
    buttons = []
    for uid in pending:
        buttons.append([
            InlineKeyboardButton(text=f"🆔 {uid}", callback_data="noop"),
            InlineKeyboardButton(text="✅", callback_data=f"admin_approve_{uid}"),
            InlineKeyboardButton(text="❌", callback_data=f"admin_reject_{uid}"),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    await callback.message.edit_text(
        f"⏳ <b>Ожидают одобрения ({len(pending)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    approved = db.get_all_approved() if hasattr(db, "get_all_approved") else []
    pending = db.get_all_pending() if hasattr(db, "get_all_pending") else []
    await callback.message.edit_text(
        f"📊 <b>Статистика бота</b>\n\n✅ Одобрено пользователей: <b>{len(approved)}</b>\n⏳ Ожидают проверки: <b>{len(pending)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()
