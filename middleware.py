import os
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
import database as db

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PUBLIC_COMMANDS = {"start", "help"}
PUBLIC_HANDLER_MODULES = {"handlers.start_handler", "start_handler"}


def _command_name(text: str) -> str | None:
    """Возвращает точное имя команды без / и @botname."""
    first = (text or "").strip().split(maxsplit=1)[0]
    if not first.startswith("/") or len(first) < 2:
        return None
    return first[1:].split("@", 1)[0].lower()


def _is_public_handler(data: dict, command: str | None) -> bool:
    """Разрешает публичную команду только обработчику start_handler.

    Это не даёт общему chat-handler получить /help или похожую строку в обход
    проверки доступа.
    """
    if command not in PUBLIC_COMMANDS:
        return False
    handler_object = data.get("handler")
    callback = getattr(handler_object, "callback", None)
    module = getattr(callback, "__module__", "")
    return module in PUBLIC_HANDLER_MODULES


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if event.from_user is None:
                return
            user_id = event.from_user.id
            text = event.text or ""
        elif isinstance(event, CallbackQuery):
            if event.from_user is None:
                return
            user_id = event.from_user.id
            text = ""
        else:
            return await handler(event, data)

        if user_id == ADMIN_ID:
            return await handler(event, data)

        if isinstance(event, Message) and _is_public_handler(data, _command_name(text)):
            return await handler(event, data)

        if await db.is_approved(user_id):
            return await handler(event, data)

        if isinstance(event, Message):
            if await db.is_pending(user_id):
                await event.answer("⏳ Твой запрос на рассмотрении. Ожидай одобрения.")
            elif await db.is_rejected(user_id):
                await event.answer("🚫 Доступ отклонён.")
            else:
                await event.answer("👋 Напиши /start чтобы запросить доступ.")
        elif isinstance(event, CallbackQuery):
            await event.answer("🚫 Нет доступа. Напиши /start.", show_alert=True)
