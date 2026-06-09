import os
import html
import base64
import random
import logging
from typing import Any
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from keyboards import cancel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://ai.api.nvidia.com/v1/genai")
NVIDIA_OPENAI_BASE = os.getenv("NVIDIA_OPENAI_BASE", "https://integrate.api.nvidia.com/v1")

IMAGE_MODELS = {
    "img_flux2": {"title": "Flux 2 Klein", "path": "black-forest-labs/flux.2-klein-4b"},
}

async def _nvidia_post_full_url(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Accept": "application/json", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            raw = await resp.text()
            data = {}
            try:
                data = json.loads(raw) if raw else {}
            except Exception:
                data = {"raw": raw}
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}: {raw[:400]}")
            return data


def _extract_image_bytes(data: dict[str, Any]) -> bytes:
    artifacts = data.get("artifacts")
    if artifacts and isinstance(artifacts, list):
        b64 = artifacts[0].get("base64")
        if b64:
            return base64.b64decode(b64)
    arr = data.get("data")
    if arr and isinstance(arr, list):
        for item in arr:
            b64 = item.get("b64_json") or item.get("base64")
            if b64:
                return base64.b64decode(b64)
    raise Exception("Изображение не найдено")


async def generate_image(prompt: str, selected_key: str) -> bytes:
    model = IMAGE_MODELS.get(selected_key)
    legacy_url = f"{NVIDIA_BASE_URL.rstrip('/')}/{model['path']}"
    legacy_payload = {"prompt": prompt, "width": 1024, "height": 1024, "steps": 4, "seed": random.randint(1, 2_147_483_647)}
    openai_url = f"{NVIDIA_OPENAI_BASE.rstrip('/')}/images/generations"
    openai_payload = {"model": model["path"], "prompt": prompt, "n": 1, "size": "1024x1024", "response_format": "b64_json", "seed": random.randint(1, 2_147_483_647)}
    try:
        data = await _nvidia_post_full_url(legacy_url, legacy_payload)
        return _extract_image_bytes(data)
    except Exception:
        data = await _nvidia_post_full_url(openai_url, openai_payload)
        return _extract_image_bytes(data)


@router.callback_query(F.data == "mode_image_gen")
async def enter_generation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="image", gen_model="img_flux2")
    await callback.message.edit_text("<b>Генерация фото</b>\n\nОтправь текстовый запрос для генерации фото.", parse_mode="HTML", reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return
    status_msg = await message.answer("⏳ Генерирую фото...", parse_mode="HTML")
    try:
        image_bytes = await generate_image(prompt, "img_flux2")
        await status_msg.delete()
        await message.answer_photo(photo=BufferedInputFile(image_bytes, filename="generated.png"),
                                   caption=f"<b>Готово</b>\n\n{html.escape(prompt)}",
                                   parse_mode="HTML",
                                   reply_markup=cancel_keyboard())
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка генерации:\n<code>{html.escape(str(e))}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())
