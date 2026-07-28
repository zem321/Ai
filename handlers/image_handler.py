import asyncio
import os
import secrets
import base64
import binascii
import json
import logging
import time
from collections import OrderedDict, deque
from html import escape
from urllib.parse import urlsplit
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from keyboards import cancel_keyboard
from states import BotStates
import database as db
from request_guard import single_user_ai_request
from safety import (
    AI_DISABLED_MESSAGE,
    AI_REQUESTS_ENABLED,
    contains_probable_secret,
    prohibited_image_reason,
    sanitize_safe_image_payload,
    safety_response_for_reason,
)

router = Router()
logger = logging.getLogger(__name__)
logger.info("image_handler module loaded: build=stream-read-v3")

__all__ = ("router", "generate_image")

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://ai.api.nvidia.com/v1/genai")
NVIDIA_OPENAI_BASE = os.getenv("NVIDIA_OPENAI_BASE", "https://integrate.api.nvidia.com/v1")
_ALLOWED_NVIDIA_HOSTS = {"ai.api.nvidia.com", "integrate.api.nvidia.com"}
MAX_GENERATED_IMAGE_BYTES = 10 * 1024 * 1024
PROVIDER_RESPONSE_LIMIT = 15 * 1024 * 1024
MAX_IMAGE_PROMPT_CHARS = 4_000
IMAGE_TIMEOUT_SECONDS = max(30, min(int(os.getenv("IMAGE_TIMEOUT_SECONDS", "180")), 300))
BOT_IMAGE_CONCURRENCY = max(1, min(int(os.getenv("BOT_IMAGE_CONCURRENCY", "2")), 8))
DEFAULT_DAILY_IMAGE_LIMIT = max(
    1, min(int(os.getenv("DEFAULT_DAILY_IMAGE_LIMIT", "20")), 1000)
)
_image_semaphore = asyncio.Semaphore(BOT_IMAGE_CONCURRENCY)

IMAGE_MODELS = {"img_flux2": {"title": "Flux 2 Klein", "path": "black-forest-labs/flux.2-klein-4b"}}


def _validated_nvidia_url(base_url: str, suffix: str) -> str:
    parts = urlsplit(base_url)
    if (
        parts.scheme != "https"
        or parts.hostname not in _ALLOWED_NVIDIA_HOSTS
        or parts.port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise RuntimeError("NVIDIA API URL должен использовать официальный HTTPS-домен NVIDIA")
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


class ProviderHTTPError(Exception):
    def __init__(self, status: int):
        super().__init__(f"NVIDIA API вернул HTTP {status}")
        self.status = status


async def _read_limited_response(
    resp: aiohttp.ClientResponse,
    limit: int,
) -> bytes:
    if resp.content_length is not None and resp.content_length > limit:
        raise RuntimeError("NVIDIA API вернул слишком большой ответ")

    raw = bytearray()
    async for chunk in resp.content.iter_chunked(64 * 1024):
        if len(raw) + len(chunk) > limit:
            raise RuntimeError("NVIDIA API вернул слишком большой ответ")
        raw.extend(chunk)
    return bytes(raw)


async def _nvidia_post_full_url(url, payload):
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY не задан")
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Accept": "application/json", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=90, connect=10, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Не следуем редиректам: исходный URL прошёл allowlist, а адрес из
        # Location уже может указывать на другой узел.
        async with session.post(
            url,
            json=payload,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            raw = await _read_limited_response(resp, PROVIDER_RESPONSE_LIMIT)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("NVIDIA API вернул некорректный JSON") from exc
            if resp.status < 200 or resp.status >= 300:
                logger.warning("NVIDIA image API error status=%s", resp.status)
                raise ProviderHTTPError(resp.status)
            if not isinstance(data, dict):
                raise RuntimeError("NVIDIA API вернул некорректный ответ")
            return data


def _extract_image_bytes(data):
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
        b64 = artifacts[0].get("base64")
        if b64:
            return _decode_image_base64(b64)
    arr = data.get("data")
    if isinstance(arr, list):
        for item in arr:
            if not isinstance(item, dict):
                continue
            b64 = item.get("b64_json") or item.get("base64")
            if b64:
                return _decode_image_base64(b64)
    raise RuntimeError("NVIDIA API не вернул изображение")


def _decode_image_base64(value: object) -> bytes:
    if not isinstance(value, str):
        raise RuntimeError("NVIDIA API вернул некорректное изображение")
    if len(value) > (MAX_GENERATED_IMAGE_BYTES * 4 // 3) + 4096:
        raise RuntimeError("NVIDIA API вернул слишком большое изображение")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("NVIDIA API вернул некорректное изображение") from exc
    if not raw or len(raw) > MAX_GENERATED_IMAGE_BYTES:
        raise RuntimeError("NVIDIA API вернул изображение недопустимого размера")
    try:
        sanitized = sanitize_safe_image_payload(
            raw,
            max_output_bytes=MAX_GENERATED_IMAGE_BYTES,
        )
    except ValueError as exc:
        raise RuntimeError(
            "NVIDIA API вернул небезопасное изображение"
        ) from exc
    if sanitized is None:
        raise RuntimeError("NVIDIA API вернул данные неизвестного формата")
    return sanitized[0]


async def generate_image(prompt: str):
    if not AI_REQUESTS_ENABLED:
        raise RuntimeError(AI_DISABLED_MESSAGE)
    if not isinstance(prompt, str):
        raise ValueError("Промпт должен быть строкой")
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("Промпт не задан")
    if len(prompt) > MAX_IMAGE_PROMPT_CHARS:
        raise ValueError("Промпт слишком длинный")
    if contains_probable_secret(prompt):
        raise ValueError("Промпт содержит данные, похожие на секрет")
    prohibited_reason = prohibited_image_reason(prompt)
    if prohibited_reason:
        raise ValueError(safety_response_for_reason(prohibited_reason))

    model = IMAGE_MODELS["img_flux2"]
    legacy_url = _validated_nvidia_url(NVIDIA_BASE_URL, model["path"])
    legacy_payload = {
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
        "steps": 4,
        "seed": secrets.randbelow(2_147_483_647) + 1,
    }
    openai_url = _validated_nvidia_url(NVIDIA_OPENAI_BASE, "images/generations")
    openai_payload = {
        "model": model["path"],
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json",
        "seed": secrets.randbelow(2_147_483_647) + 1,
    }
    try:
        data = await _nvidia_post_full_url(legacy_url, legacy_payload)
        return _extract_image_bytes(data)
    except ProviderHTTPError as exc:
        if exc.status not in {400, 404, 405, 422}:
            raise
        logger.info(
            "Основной NVIDIA image endpoint вернул HTTP %s; используется запасной endpoint",
            exc.status,
        )
    except RuntimeError as exc:
        if str(exc) not in {
            "NVIDIA API вернул некорректный JSON",
            "NVIDIA API вернул некорректный ответ",
        }:
            raise
        logger.warning(
            "Основной NVIDIA image endpoint вернул повреждённый ответ; "
            "используется запасной endpoint"
        )

    data = await _nvidia_post_full_url(openai_url, openai_payload)
    return _extract_image_bytes(data)


class _ImageRateLimiter:
    def __init__(self):
        self.buckets: OrderedDict[int, deque[float]] = OrderedDict()

    def allow(self, user_id: int) -> bool:
        now = time.monotonic()
        bucket = self.buckets.get(user_id)
        if bucket is None:
            if len(self.buckets) >= 10_000:
                stale_id, _ = self.buckets.popitem(last=False)
                logger.info("Удалена устаревшая корзина лимита user_id=%s", stale_id)
            bucket = deque()
            self.buckets[user_id] = bucket
        else:
            self.buckets.move_to_end(user_id)
        while bucket and now - bucket[0] >= 10 * 60:
            bucket.popleft()
        if len(bucket) >= 5:
            return False
        bucket.append(now)
        return True


_image_rate_limiter = _ImageRateLimiter()


@router.callback_query(F.data == "mode_image_gen")
async def enter_generation(callback: CallbackQuery, state: FSMContext):
    if not AI_REQUESTS_ENABLED:
        await callback.answer(AI_DISABLED_MESSAGE, show_alert=True)
        return
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text("<b>Генерация фото</b>\n\nОтправь текстовый запрос.", parse_mode="HTML", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
@single_user_ai_request
async def do_generate(message: Message, state: FSMContext):
    if not AI_REQUESTS_ENABLED:
        await message.answer(AI_DISABLED_MESSAGE)
        return
    prompt = message.text.strip()
    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return
    if len(prompt) > MAX_IMAGE_PROMPT_CHARS:
        await message.answer(
            f"Запрос слишком длинный. Максимум {MAX_IMAGE_PROMPT_CHARS} символов."
        )
        return
    if contains_probable_secret(prompt):
        await message.answer("Запрос похож на секрет и не был отправлен.")
        return
    prohibited_reason = prohibited_image_reason(prompt)
    if prohibited_reason:
        await message.answer(safety_response_for_reason(prohibited_reason))
        return
    user_id = message.from_user.id
    if not _image_rate_limiter.allow(user_id):
        await message.answer("Лимит генерации изображений исчерпан. Попробуй позже.")
        return
    try:
        reserved = await db.reserve_request(
            user_id,
            "image-generation",
            source="bot",
            default_daily_limit=DEFAULT_DAILY_IMAGE_LIMIT,
        )
    except Exception:
        logger.exception("Не удалось проверить лимит генерации изображений")
        await message.answer("Проверка лимита временно недоступна. Попробуй позже.")
        return
    if not reserved:
        await message.answer("Дневной лимит генерации изображений исчерпан.")
        return
    status_msg = await message.answer("⏳ Генерирую фото...", parse_mode="HTML")
    try:
        async with _image_semaphore:
            image_bytes = await asyncio.wait_for(
                generate_image(prompt),
                timeout=IMAGE_TIMEOUT_SECONDS,
            )
        await status_msg.delete()
        caption_prompt = prompt[:900] + ("…" if len(prompt) > 900 else "")
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="generated.png"),
            caption=f"<b>Готово</b>\n\n{escape(caption_prompt)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except asyncio.TimeoutError:
        await status_msg.edit_text(
            "❌ Генерация заняла слишком много времени.",
            reply_markup=cancel_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка генерации изображения")
        await status_msg.edit_text(
            "❌ Не удалось создать изображение. Попробуй позже.",
            reply_markup=cancel_keyboard(),
        )
