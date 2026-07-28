"""Общий декоратор единственного активного AI-запроса в Telegram."""

from __future__ import annotations

from functools import wraps
import logging

import database as db
from safety import REQUEST_IN_PROGRESS_MESSAGE


logger = logging.getLogger(__name__)


def single_user_ai_request(handler):
    """Не позволяет одному Telegram ID выполнять два AI-запроса сразу."""

    @wraps(handler)
    async def guarded(message, *args, **kwargs):
        user = getattr(message, "from_user", None)
        user_id = getattr(user, "id", None)
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
            return None
        try:
            async with db.user_request_slot(user_id) as lease:
                if lease is None:
                    await message.answer(REQUEST_IN_PROGRESS_MESSAGE)
                    return None
                return await lease.run(handler(message, *args, **kwargs))
        except db.RequestLeaseLostError:
            logger.error(
                "AI-запрос остановлен после потери аренды user_id=%s",
                user_id,
            )
            await message.answer(
                "Запрос остановлен: не удалось сохранить эксклюзивную "
                "блокировку. Попробуйте ещё раз."
            )
            return None
        except Exception:
            logger.exception(
                "Не удалось применить блокировку AI-запроса user_id=%s",
                user_id,
            )
            await message.answer(
                "Проверка активного запроса временно недоступна. "
                "Попробуйте позже."
            )
            return None

    return guarded
