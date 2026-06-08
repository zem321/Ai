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
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# Только одна рабочая модель, как вы попросили
IMAGE_MODELS = {
    "img_flux2": {
        "title": "Flux 2 Klein",
        "path": "black-forest-labs/flux.2-klein-4b",
    },
}


def gen_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Картинка", callback_data="gen_type_image")],
            [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
        ]
    )


def image_model_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Flux 2 Klein", callback_data="gen_model_img_flux2")],
            [InlineKeyboardButton(text="Назад", callback_data="mode_image_gen")],
        ]
    )


def _build_url(path_or_url: str) -> str:
    if path_or_url.startswith("http"):
        return path_or_url
    base = NVIDIA_BASE_URL.rstrip("/")
    path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
    return f"{base}{path}"


async def _nvidia_post(path_or_url: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

    url = _build_url(path_or_url)
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

                logger.error("NVIDIA image error status=%s url=%s body=%s", resp.status, str(resp.url), raw[:500])
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
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        b64 = arr[0].get("b64_json") or arr[0].get("base64")
        if isinstance(b64, str) and b64:
            return base64.b64decode(b64)

    raise Exception(f"Изображение не найдено в ответе: {str(data)[:400]}")


async def generate_image(prompt: str, selected_key: str) -> tuple[bytes, str]:
    model = IMAGE_MODELS.get(selected_key) or IMAGE_MODELS["img_flux2"]

    payload = {
        "model": model["path"],
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "response_format": "b64_json",
        "seed": random.randint(1, 2_147_483_647),
    }

    logger.info("Image attempt model=%s", model["path"])
    data = await _nvidia_post("/images/generations", payload)
    return _extract_image_bytes(data), model["title"]


@router.callback_query(F.data == "mode_image_gen")
async def enter_generation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type=None, gen_model=None)
    await callback.message.edit_text(
        "<b>Генерация</b>\n\nВыбери режим:",
        parse_mode="HTML",
        reply_markup=gen_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "mode_image_edit")
async def edit_mode_disabled(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Редактирование фото отключено</b>\n\nДоступна только генерация картинки через Flux 2 Klein.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "gen_type_image")
async def select_image_type(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="image", gen_model="img_flux2")
    await callback.message.edit_text(
        "<b>Генерация картинки</b>\n\nВыбери модель:",
        parse_mode="HTML",
        reply_markup=image_model_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "gen_type_video")
async def video_disabled(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Генерация видео отключена</b>\n\nОставлена только генерация фото через Flux 2 Klein.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gen_model_"))
async def choose_gen_model(callback: CallbackQuery, state: FSMContext):
    key = callback.data.replace("gen_model_", "", 1)

    if key != "img_flux2":
        await callback.answer("Доступна только Flux 2 Klein", show_alert=True)
        return

    await state.update_data(gen_type="image", gen_model="img_flux2")
    await callback.message.edit_text(
        "<b>Выбрано:</b> Flux 2 Klein\n\nТеперь отправь текстовый запрос.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    gen_type = data.get("gen_type")
    gen_model = data.get("gen_model")
    prompt = (message.text or "").strip()

    if gen_type != "image":
        await message.answer("Сначала выбери режим генерации картинки в меню.", reply_markup=cancel_keyboard())
        return

    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return

    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("Генерирую...", parse_mode="HTML")

    try:
        selected = gen_model if gen_model in IMAGE_MODELS else "img_flux2"
        image_bytes, used = await generate_image(prompt, selected)

        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="generated.png"),
            caption=f"<b>Готово</b>\nМодель: {html.escape(used)}\n\n{html.escape(prompt)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Generation error")
        await status_msg.edit_text(
            f"Ошибка генерации:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
