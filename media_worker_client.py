"""Клиент основного бота для похода во второй Render-сервис (media_worker),
который скачивает фото/видео и прогоняет их через vision-модели NVIDIA.

Специально не передаёт токен бота второму сервису напрямую — вместо этого
основной бот сам вызывает Telegram getFile (это лёгкий вызов, без скачивания
байтов) и передаёт media_worker готовую одноразовую ссылку на файл. Так
токен бота не оседает в конфиге второго сервиса.
"""

from __future__ import annotations

import os

import aiohttp
from aiogram import Bot

MEDIA_WORKER_URL = os.getenv("MEDIA_WORKER_URL", "").rstrip("/")
MEDIA_WORKER_SECRET = os.getenv("MEDIA_WORKER_SECRET", "")
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=100)


class MediaWorkerUnavailable(RuntimeError):
    """media_worker не настроен, недоступен или вернул ошибку."""


def media_worker_configured() -> bool:
    return bool(MEDIA_WORKER_URL and MEDIA_WORKER_SECRET)


async def analyze_media(
    bot: Bot,
    *,
    kind: str,  # "photo" | "video"
    file_id: str,
    prompt: str,
    model: str,
    declared_mime: str | None = None,
) -> str:
    if not media_worker_configured():
        raise MediaWorkerUnavailable("MEDIA_WORKER_URL/MEDIA_WORKER_SECRET не заданы")

    # Лёгкий вызов Telegram API — только метаданные файла, без байтов.
    tg_file = await bot.get_file(file_id)
    file_url = (
        f"https://api.telegram.org/file/bot{bot.token}/{tg_file.file_path}"
    )

    payload = {
        "kind": kind,
        "file_url": file_url,
        "prompt": prompt,
        "model": model,
        "declared_mime": declared_mime,
    }
    headers = {"Authorization": f"Bearer {MEDIA_WORKER_SECRET}"}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                f"{MEDIA_WORKER_URL}/v1/analyze",
                json=payload,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                data = await resp.json(content_type=None)
        except Exception as exc:
            raise MediaWorkerUnavailable(f"media_worker недоступен: {exc}") from exc

    if not data.get("ok"):
        raise MediaWorkerUnavailable(data.get("error", "неизвестная ошибка media_worker"))
    return data["text"]
