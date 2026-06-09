import os
import json
import html
import base64
import random
import logging
from typing import Any

import aiohttp

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.context import FSMContext

from keyboards import cancel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://ai.api.nvidia.com/v1/genai")
NVIDIA_OPENAI_BASE = os.getenv("NVIDIA_OPENAI_BASE", "https://integrate.api.nvidia.com/v1")

IMAGE_MODELS = {
    "img_flux2": {
        "title": "Flux 2 Klein",
        "path": "black-forest-labs/flux.2-klein-4b",
    },
}


def gen_type_keyboard() -> InlineKeyboardMarkup:
    # В этой версии только генерация фото
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖼 Генерация фото", callback_data="gen_type_image")],
            [InlineKeyboardButton(text="◀️ Меню", callback_data="main_menu")],
        ]
    )


def _build_url(base: str, path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    return f"{base.rstrip('/')}/{path_or_url.lstrip('/')}"


async def _nvidia_post_full_url(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            raw = await resp.text()
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {"raw": raw}
            if resp.status != 200:
                detail = ""
                if isinstance(data, dict):
                    if isinstance(data.get("detail"), str):
                        detail = data["detail"]
                    elif isinstance(data.get("error"), dict):
                        detail = data["error"].get("message", "")
                    elif isinstance(data.get("message"), str):
                        detail = data["message"]
                logger.warning("NVIDIA HTTP %s url=%s body=%s", resp.status, str(resp.url), raw[:500])
                raise Exception(f"HTTP {resp.status}: {detail or raw[:400]}")
            if not isinstance(data, dict):
                raise Exception(f"Некорректный ответ NVIDIA: {str(data)[:300]}")
            return data


def _extract_image_bytes(data: dict[str, Any]) -> bytes:
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
        b64 = artifacts[0].get("base64")
        if isinstance(b64, str) and b64:
            return base64.b64decode(b64)
    arr = data.get("data")
    if isinstance(arr, list) and arr:
        for item in arr:
            if isinstance(item, dict):
                b64 = item.get("b64_json") or item.get("base64")
                if isinstance(b64, str) and b64:
                    return base64.b64decode(b64)
    raise Exception(f"Изображение не найдено в ответе: {str(data)[:400]}")


async def generate_image(prompt: str, selected_key: str) -> bytes:
    model = IMAGE_MODELS.get(selected_key) or IMAGE_MODELS["img_flux2"]
    legacy_url = _build_url(NVIDIA_BASE_URL, model["path"])
    legacy_payload = {
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
        "steps": 4,
        "seed": random.randint(1, 2_147_483_647),
    }
    openai_url = _build_url(NVIDIA_OPENAI_BASE, "/images/generations")
    openai_payload = {
        "model": model["path"],
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json",
        "seed": random.randint(1, 2_147_483_647),
    }
    try:
        data = await _nvidia_post_full_url(legacy_url, legacy_payload)
        return _extract_image_bytes(data)
    except Exception as e1:
        logger.warning("Legacy image endpoint failed: %s", str(e1))
    try:
        data = await _nvidia_post_full_url(openai_url, openai_payload)
        return _extract_image_bytes(data)
    except Exception as e2:
        raise Exception(f"Генерация фото не удалась. Последняя ошибка: {e2}")


@router.callback_query(F.data == "mode_image_gen")
async def enter_generation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="image", gen_model="img_flux2")
    await callback.message.edit_text(
        "<b>Генерация фото</b>\n\nОтправь текстовый запрос для генерации фото.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    gen_type = data.get("gen_type")
    prompt = (message.text or "").strip()
    if gen_type != "image":
        await message.answer("Сначала выбери режим генерации фото в меню.", reply_markup=cancel_keyboard())
        return
    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("⏳ Генерирую фото...", parse_mode="HTML")
    try:
        image_bytes = await generate_image(prompt, "img_flux2")
        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="generated.png"),
            caption=f"<b>Готово</b>\n\n{html.escape(prompt)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Generation error")
        await status_msg.edit_text(f"❌ Ошибка генерации:\n<code>{html.escape(str(e))}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())
