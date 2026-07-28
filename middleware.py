import os
import time
from collections import OrderedDict, deque
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
import database as db

try:
    ADMIN_ID = int(os.environ["ADMIN_ID"])
except (KeyError, TypeError, ValueError) as exc:
    raise RuntimeError("ADMIN_ID должен быть задан положительным целым числом") from exc
if ADMIN_ID <= 0 or ADMIN_ID > 2**63 - 1:
    raise RuntimeError("ADMIN_ID должен быть положительным Telegram ID")
PUBLIC_COMMANDS = {"start", "help"}
PUBLIC_HANDLER_MODULES = {"handlers.start_handler", "start_handler"}


class _UpdateRateLimiter:
    def __init__(self, max_buckets: int = 20_000):
        self.max_buckets = max_buckets
        self.buckets: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        bucket = self.buckets.get(key)
        if bucket is None:
            if len(self.buckets) >= self.max_buckets:
                self.buckets.popitem(last=False)
            bucket = deque()
            self.buckets[key] = bucket
        else:
            self.buckets.move_to_end(key)
        while bucket and now - bucket[0] >= window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


_update_rate_limiter = _UpdateRateLimiter()


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

        user_ok = _update_rate_limiter.allow(f"user:{user_id}", 120, 60)
        global_ok = _update_rate_limiter.allow("global", 3000, 60)
        if not user_ok or not global_ok:
            if isinstance(event, Message):
                await event.answer("Слишком много запросов. Подожди минуту.")
            else:
                await event.answer("Слишком много запросов.", show_alert=True)
            return

        if isinstance(event, Message) and _is_public_handler(data, _command_name(text)):
            return await handler(event, data)

        status = await db.get_user_status(user_id)
        if status == "approved":
            return await handler(event, data)

        if isinstance(event, Message):
            if status == "pending":
                await event.answer("⏳ Твой запрос на рассмотрении. Ожидай одобрения.")
            elif status == "rejected":
                await event.answer("🚫 Доступ отклонён.")
            else:
                await event.answer("👋 Напиши /start чтобы запросить доступ.")
        elif isinstance(event, CallbackQuery):
            await event.answer("🚫 Нет доступа. Напиши /start.", show_alert=True)
