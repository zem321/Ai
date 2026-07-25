import os
import logging
from html import escape
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext

import database as db
from keyboards import main_menu_keyboard, admin_notify_keyboard, admin_panel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()
try:
    ADMIN_ID = int(os.environ["ADMIN_ID"])
except (KeyError, TypeError, ValueError) as exc:
    raise RuntimeError("ADMIN_ID должен быть задан положительным целым числом") from exc
if ADMIN_ID <= 0:
    raise RuntimeError("ADMIN_ID должен быть положительным Telegram ID")

WELCOME_TEXT = """
👋 <b>Привет! Я твой ИИ-ассистент</b>

Я умею:
• 💬 Отвечать на вопросы (с памятью диалога)
• 🖼 Анализировать фото по твоему запросу
• 🎨 Генерировать изображения
• ✏️ Редактировать фото по описанию
• 🤖 Работать с разными моделями ИИ

Выбери режим 👇
"""


async def notify_admin(bot: Bot, user):
    if not ADMIN_ID:
        return
    full_name = escape(user.full_name or "Без имени")
    username = escape(f"@{user.username}") if user.username else "нет username"
    text = (
        f"🔔 <b>Новый запрос на доступ!</b>\n\n"
        f"👤 <b>Имя:</b> {full_name}\n"
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
        await db.approve_user(user_id)
        await state.set_state(BotStates.main_menu)
        await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        return
    if await db.is_approved(user_id):
        await state.set_state(BotStates.main_menu)
        await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
        return
    if await db.is_pending(user_id):
        await message.answer("⏳ <b>Запрос уже отправлен!</b> Ожидай одобрения.", parse_mode="HTML")
        return
    if await db.is_rejected(user_id):
        await message.answer("🚫 <b>Доступ отклонён.</b>", parse_mode="HTML")
        return
    await db.add_pending(user_id)
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
        "💬 <b>Чат</b> — вопросы + фото с подписью для анализа\n"
        "🎨 <b>Создать картинку</b> — генерация по описанию\n"
        "✏️ <b>Редактировать фото</b> — отправь фото с подписью-заданием\n"
        "🤖 <b>Модель</b> — выбери какой ИИ отвечает\n\n"
        "<b>Команды:</b>\n"
        "/start — главное меню\n"
        "/admin — панель администратора"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    approved = await db.get_all_approved()
    pending = await db.get_all_pending()
    await message.answer(
        f"🛠 <b>Панель администратора</b>\n\n✅ Одобрено: <b>{len(approved)}</b>\n⏳ Ожидают: <b>{len(pending)}</b>",
        reply_markup=admin_panel_keyboard(), parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("approve_"))
async def cb_approve(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[1])
    await db.approve_user(user_id)
    await callback.message.edit_text(callback.message.text + "\n\n✅ <b>Одобрен!</b>", parse_mode="HTML")
    await callback.answer("✅ Одобрен!")
    try:
        await bot.send_message(user_id, "🎉 <b>Доступ одобрен!</b>\n\nНапиши /start чтобы начать.", parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("reject_"))
async def cb_reject(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[1])
    await db.reject_user(user_id)
    await callback.message.edit_text(callback.message.text + "\n\n❌ <b>Отклонён.</b>", parse_mode="HTML")
    await callback.answer("❌ Отклонён")
    try:
        await bot.send_message(user_id, "😔 <b>Доступ отклонён.</b>", parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data.startswith("revoke_"))
async def cb_revoke(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа", show_alert=True)
        return
    user_id = int(callback.data.split("_")[1])
    await db.revoke_user(user_id)
    await callback.answer("🚫 Доступ отозван", show_alert=True)
    await callback.message.edit_text(
        f"🚫 Доступ пользователя <code>{user_id}</code> отозван.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ]),
        parse_mode="HTML"
    )
    try:
        await bot.send_message(user_id, "🚫 <b>Ваш доступ отозван.</b>", parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    approved = await db.get_all_approved()
    pending = await db.get_all_pending()
    await callback.message.edit_text(
        f"🛠 <b>Панель администратора</b>\n\n✅ Одобрено: <b>{len(approved)}</b>\n⏳ Ожидают: <b>{len(pending)}</b>",
        reply_markup=admin_panel_keyboard(), parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_list_approved")
async def cb_list_approved(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    approved = await db.get_all_approved()
    if not approved:
        await callback.answer("Список пуст", show_alert=True)
        return
    buttons = []
    for uid in approved:
        buttons.append([
            InlineKeyboardButton(text=f"🆔 {uid}", callback_data="noop"),
            InlineKeyboardButton(text="🚫 Отозвать", callback_data=f"revoke_{uid}"),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    await callback.message.edit_text(
        f"✅ <b>Одобренные ({len(approved)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_list_pending")
async def cb_list_pending(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    pending = await db.get_all_pending()
    if not pending:
        await callback.answer("Нет ожидающих", show_alert=True)
        return
    buttons = []
    for uid in pending:
        buttons.append([
            InlineKeyboardButton(text=f"🆔 {uid}", callback_data="noop"),
            InlineKeyboardButton(text="✅", callback_data=f"approve_{uid}"),
            InlineKeyboardButton(text="❌", callback_data=f"reject_{uid}"),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    await callback.message.edit_text(
        f"⏳ <b>Ожидают ({len(pending)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_list_rejected")
async def cb_list_rejected(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    rejected = await db.get_all_rejected()
    if not rejected:
        await callback.answer("Список пуст", show_alert=True)
        return
    buttons = []
    for uid in rejected:
        buttons.append([
            InlineKeyboardButton(text=f"🆔 {uid}", callback_data="noop"),
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{uid}"),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")])
    await callback.message.edit_text(
        f"❌ <b>Отклонённые/отозванные ({len(rejected)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_stats(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    approved = await db.get_all_approved()
    pending = await db.get_all_pending()
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n✅ Одобрено: <b>{len(approved)}</b>\n⏳ Ожидают: <b>{len(pending)}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()
