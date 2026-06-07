import os
import io
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
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://ai.api.nvidia.com/v1/genai").rstrip("/")

# Картинки: рабочие/популярные пути (оставлены с fallback)
IMAGE_MODELS = {
    "img_flux2": {
        "title": "Flux 2 Klein (быстро)",
        "path": "black-forest-labs/flux.2-klein-4b",
    },
    "img_flux1": {
        "title": "Flux 1 Schnell",
        "path": "black-forest-labs/flux.1-schnell",
    },
}

# Видео: пути могут быть недоступны для конкретного аккаунта NVIDIA.
# Бот автоматически перебирает и покажет понятную ошибку, если доступа нет.
VIDEO_MODELS = {
    "vid_svd": {
        "title": "Stable Video Diffusion",
        "path": "stabilityai/stable-video-diffusion",
    },
    "vid_ltx": {
        "title": "LTX Video",
        "path": "lightricks/ltx-video",
    },
    "vid_cog": {
        "title": "CogVideoX",
        "path": "THUDM/cogvideox-5b",
    },
}


def gen_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖼 Картинка", callback_data="gen_type_image")],
            [InlineKeyboardButton(text="🎬 Видео", callback_data="gen_type_video")],
            [InlineKeyboardButton(text="◀️ Меню", callback_data="main_menu")],
        ]
    )


def image_model_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, item in IMAGE_MODELS.items():
        rows.append([InlineKeyboardButton(text=item["title"], callback_data=f"gen_model_{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mode_image_gen")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def video_model_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, item in VIDEO_MODELS.items():
        rows.append([InlineKeyboardButton(text=item["title"], callback_data=f"gen_model_{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mode_image_gen")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_url(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    return f"{NVIDIA_BASE_URL}/{path_or_url}"


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
            data = {}
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
                raise Exception(f"HTTP {resp.status}: {detail or raw[:400]}")

            if not isinstance(data, dict):
                raise Exception(f"Некорректный ответ NVIDIA: {str(data)[:300]}")
            return data


async def _download_bytes(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                t = await resp.text()
                raise Exception(f"Ошибка скачивания файла HTTP {resp.status}: {t[:300]}")
            return await resp.read()


def _extract_b64_from_response(data: dict[str, Any]) -> bytes | None:
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
        b64 = artifacts[0].get("base64")
        if isinstance(b64, str) and b64:
            return base64.b64decode(b64)

    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        b64 = d[0].get("b64_json")
        if isinstance(b64, str) and b64:
            return base64.b64decode(b64)

    return None


async def _extract_video_bytes(data: dict[str, Any]) -> bytes:
    b = _extract_b64_from_response(data)
    if b:
        return b

    for key in ("video_url", "url", "output_url", "download_url"):
        v = data.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return await _download_bytes(v)

    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        for key in ("video_url", "url", "output_url", "download_url"):
            v = d[0].get(key)
            if isinstance(v, str) and v.startswith("http"):
                return await _download_bytes(v)

    raise Exception(f"Видео не найдено в ответе: {str(data)[:400]}")


def _extract_image_bytes(data: dict[str, Any]) -> bytes:
    b = _extract_b64_from_response(data)
    if b:
        return b
    raise Exception(f"Изображение не найдено в ответе: {str(data)[:400]}")


async def generate_image(prompt: str, selected_key: str) -> tuple[bytes, str]:
    model_order = [selected_key] + [k for k in IMAGE_MODELS if k != selected_key]
    last_error = None

    for key in model_order:
        model = IMAGE_MODELS[key]
        payload = {
            "prompt": prompt,
            "width": 1024,
            "height": 1024,
            "steps": 4,
            "seed": random.randint(1, 2_147_483_647),
        }
        try:
            logger.info("Image attempt model=%s", model["path"])
            data = await _nvidia_post(model["path"], payload)
            image_bytes = _extract_image_bytes(data)
            return image_bytes, model["title"]
        except Exception as e:
            last_error = e
            logger.warning("Image model failed model=%s error=%s", model["path"], str(e))
            continue

    raise Exception(f"Генерация картинки не удалась. Последняя ошибка: {last_error}")


async def generate_video(prompt: str, selected_key: str) -> tuple[bytes, str]:
    model_order = [selected_key] + [k for k in VIDEO_MODELS if k != selected_key]
    errors = []

    for key in model_order:
        model = VIDEO_MODELS[key]
        payload = {
            "prompt": prompt,
            "seed": random.randint(1, 2_147_483_647),
            "duration": 4,
            "fps": 16,
        }
        try:
            logger.info("Video attempt model=%s", model["path"])
            data = await _nvidia_post(model["path"], payload)
            video_bytes = await _extract_video_bytes(data)
            if len(video_bytes) < 2000:
                raise Exception(f"Слишком маленький видео-файл: {len(video_bytes)} байт")
            return video_bytes, model["title"]
        except Exception as e:
            msg = str(e)
            errors.append(msg)
            logger.warning("Video model failed model=%s error=%s", model["path"], msg)
            continue

    all_404 = all(("HTTP 404" in err or "Not found for account" in err) for err in errors) if errors else False
    if all_404:
        raise Exception(
            "Видео-модели NVIDIA недоступны для вашего аккаунта (404 Not found for account). "
            "Проверьте доступные video endpoints в NVIDIA и подставьте рабочий путь модели."
        )

    last_error = errors[-1] if errors else "неизвестная ошибка"
    raise Exception(f"Генерация видео не удалась. Последняя ошибка: {last_error}")


@router.callback_query(F.data == "mode_image_gen")
async def enter_generation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type=None, gen_model=None)
    await callback.message.edit_text(
        "<b>Генерация</b>\n\nВыбери, что создать:",
        parse_mode="HTML",
        reply_markup=gen_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "mode_video_gen")
async def enter_video_generation(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="video", gen_model="vid_svd")
    await callback.message.edit_text(
        "<b>Генерация видео</b>\n\nВыбери модель:",
        parse_mode="HTML",
        reply_markup=video_model_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "mode_image_edit")
async def edit_mode_disabled(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Редактирование фото отключено</b>\n\nИспользуй генерацию картинки или видео.",
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
async def select_video_type(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="video", gen_model="vid_svd")
    await callback.message.edit_text(
        "<b>Генерация видео</b>\n\nВыбери модель:",
        parse_mode="HTML",
        reply_markup=video_model_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gen_model_"))
async def choose_gen_model(callback: CallbackQuery, state: FSMContext):
    key = callback.data.replace("gen_model_", "", 1)
    data = await state.get_data()
    gen_type = data.get("gen_type")

    if gen_type == "image" and key in IMAGE_MODELS:
        await state.update_data(gen_model=key)
        title = IMAGE_MODELS[key]["title"]
    elif gen_type == "video" and key in VIDEO_MODELS:
        await state.update_data(gen_model=key)
        title = VIDEO_MODELS[key]["title"]
    else:
        await callback.answer("Неверная модель", show_alert=True)
        return

    await callback.message.edit_text(
        f"<b>Выбрано:</b> {html.escape(title)}\n\nТеперь отправь текстовый запрос.",
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

    if not gen_type:
        await message.answer(
            "Сначала выбери режим генерации в меню.",
            reply_markup=cancel_keyboard(),
        )
        return

    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return

    chat_action = "upload_photo" if gen_type == "image" else "upload_video"
    await message.bot.send_chat_action(message.chat.id, chat_action)
    status_msg = await message.answer("Генерирую...", parse_mode="HTML")

    try:
        if gen_type == "image":
            selected = gen_model if gen_model in IMAGE_MODELS else "img_flux2"
            image_bytes, used = await generate_image(prompt, selected)
            await status_msg.delete()
            await message.answer_photo(
                photo=BufferedInputFile(image_bytes, filename="generated.png"),
                caption=f"<b>Готово</b>\nМодель: {html.escape(used)}\n\n{html.escape(prompt)}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
        else:
            selected = gen_model if gen_model in VIDEO_MODELS else "vid_svd"
            video_bytes, used = await generate_video(prompt, selected)
            await status_msg.delete()
            await message.answer_video(
                video=BufferedInputFile(video_bytes, filename="generated.mp4"),
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
