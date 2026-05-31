import os
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
import database as db

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PUBLIC_COMMANDS = {"/start", "/help"}


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
