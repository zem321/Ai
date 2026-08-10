"""Уведомления администратора о сбоях AI-провайдеров.

Срабатывает только когда пользователь реально получил ошибку в ответ, а до
этого подряд отвалилось несколько разных API-ключей (см.
provider_keys.AllProviderKeysExhausted) или несколько моделей/провайдеров в
цепочке fallback (provider_keys.AIChainExhausted). Единичный сбой одного
ключа, который бот тихо обошёл через следующий ключ, сюда не долетает.
"""

from __future__ import annotations

import logging
import os
from html import escape

from aiogram import Bot

import database as db
from provider_keys import AIChainExhausted, AllProviderKeysExhausted

logger = logging.getLogger(__name__)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))


def _describe(error: BaseException) -> tuple[str, list[str], str]:
    """Возвращает (что запрашивали, что перепробовали, детали по каждому)."""
    if isinstance(error, AllProviderKeysExhausted):
        return (
            error.provider_label,
            [error.provider],
            f"подряд не ответили {error.attempts} разных ключа(ей)",
        )
    if isinstance(error, AIChainExhausted):
        details = "\n".join(
            f"• {model}: {type(exc).__name__}: {exc}"
            for model, exc in zip(error.attempted_models, error.errors)
        )
        return error.requested_model, list(error.attempted_models), details
    return "unknown", [], str(error)


def is_provider_outage(error: BaseException | None) -> bool:
    """True, если это именно множественный сбой ключей/провайдеров, а не
    обычная ошибка ввода, safety-фильтр или лимит."""
    return isinstance(error, (AllProviderKeysExhausted, AIChainExhausted))


async def notify_admin_provider_failure(
    bot: Bot | None,
    chat_id: int | None,
    error: BaseException,
) -> None:
    """Логирует инцидент в БД и, если возможно, шлёт алерт админу в Telegram.

    Никогда не поднимает исключение наружу — вызывается "в фоне" из except-
    блоков и не должен ронять обработку сообщения пользователя.
    """
    requested, attempted, details = _describe(error)
    admin_notified = False
    try:
        if bot is not None and ADMIN_ID:
            text = (
                "🔴 <b>Сбой AI-провайдера</b>\n\n"
                f"Запрос: <code>{escape(requested)}</code>\n"
                f"Затронуто: <code>{escape(', '.join(attempted) or '—')}</code>\n"
                + (f"Chat ID пользователя: <code>{chat_id}</code>\n" if chat_id else "")
                + f"\n{escape(details)[:3000]}"
            )
            await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
            admin_notified = True
    except Exception:
        logger.exception("Не удалось отправить admin-алерт о сбое провайдера")
    try:
        incident_id = await db.log_provider_incident(
            requested=requested,
            attempted=attempted,
            details=details,
            user_id=chat_id,
            admin_notified=admin_notified,
        )
        logger.error(
            "Provider incident #%s requested=%s attempted=%s admin_notified=%s",
            incident_id,
            requested,
            attempted,
            admin_notified,
        )
    except Exception:
        logger.exception("Не удалось записать инцидент провайдера в БД")
