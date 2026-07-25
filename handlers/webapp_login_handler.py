from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

import database as db
from keyboards import webapp_code_keyboard

router = Router()


async def _build_code_message(user_id: int) -> str:
    """Общая логика выдачи кода — используется и командой, и кнопкой меню.

    AccessMiddleware уже не пустит сюда pending/rejected пользователей, но
    проверка оставлена как дополнительная подстраховка.
    """
    if not await db.is_approved(user_id):
        return "У тебя пока нет доступа. Напиши /start и дождись одобрения."

    try:
        code = await db.create_login_code(user_id)
    except RuntimeError as exc:
        return f"⏳ {exc}"
    return (
        "🔑 Код для входа на сайт (вне Telegram):\n\n"
        f"`{code}`\n\n"
        "Действует 10 минут и одноразовый. Предыдущий код уже отключён. "
        "Никому не пересылай этот код."
    )


@router.message(Command("code"))
async def cmd_web_login_code(message: Message):
    text = await _build_code_message(message.from_user.id)
    await message.answer(text, parse_mode="Markdown", reply_markup=webapp_code_keyboard())


@router.callback_query(lambda c: c.data == "webapp_login_code")
async def cb_web_login_code(callback: CallbackQuery):
    text = await _build_code_message(callback.from_user.id)
    await callback.answer()
    await callback.message.answer(text, parse_mode="Markdown", reply_markup=webapp_code_keyboard())
