import os
import io
import json
import html
import base64
import random
import logging
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

# Несколько моделей для генерации картинок
IMAGE_MODELS = {
    "img_flux2": {
        "title": "Flux 2 Klein (быстро)",
        "path": "black-forest-labs/flux.2-klein-4b",
    },
    "img_flux1": {
        "title": "Flux 1 Schnell",
        "path": "black-forest-labs/flux.1-schnell",
    },
    "img_sdxl": {
        "title": "Stable Diffusion XL",
        "path": "stabilityai/stable-diffusion-xl",
    },
}

# Несколько моделей для видео
VIDEO_MODELS = {
    "vid_ltx": {
        "title": "LTX Video",
        "path": "lightricks/ltx-video",
    },
    "vid_cog": {
        "title": "CogVideoX",
        "path": "THUDM/cogvideox-5b",
    },
    "vid_svd": {
        "title": "Stable Video Diffusion",
        "path": "stabilityai/stable-video-diffusion",
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
    for key, data in IMAGE_MODELS.items():
        rows.append([InlineKeyboardButton(text=data["title"], callback_data=f"gen_model_{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mode_image_gen")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def video_model_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for key, data in VIDEO_MODELS.items():
        rows.append([InlineKeyboardButton(text=data["title"], callback_data=f"gen_model_{key}")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="mode_image_gen")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _post_nvidia(model_path: str, payload: dict) -> dict:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

    url = f"{NVIDIA_BASE_URL}/{model_path}"
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
                data = json.loads(raw)
            except Exception:
                data = {"raw": raw}

            if resp.status != 200:
                detail = ""
                if isinstance(data, dict):
                    if isinstance(data.get("detail"), str):
                        detail = data["detail"]
                    elif isinstance(data.get("error"), dict):
                        detail = data["error"].get("message", "")
                raise Exception(f"HTTP {resp.status}: {detail or raw[:400]}")

            if not isinstance(data, dict):
                raise Exception(f"Невалидный ответ NVIDIA: {str(data)[:300]}")

            return data


async def _download_url_bytes(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                txt = await resp.text()
                raise Exception(f"Ошибка скачивания файла: HTTP {resp.status}: {txt[:200]}")
            return await resp.read()


def _extract_image_bytes(data: dict) -> bytes:
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
        b64 = artifacts[0].get("base64")
        if b64:
            return base64.b64decode(b64)

    arr = data.get("data")
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        b64 = arr[0].get("b64_json")
        if b64:
            return base64.b64decode(b64)

    raise Exception(f"Изображение не найдено в ответе: {str(data)[:400]}")


async def _extract_video_bytes(data: dict) -> bytes:
    # Вариант 1: base64
    artifacts = data.get("artifacts")
    if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
        b64 = artifacts[0].get("base64")
        if b64:
            return base64.b64decode(b64)

    arr = data.get("data")
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        b64 = arr[0].get("b64_json")
        if b64:
            return base64.b64decode(b64)

    # Вариант 2: ссылка на файл
    for key in ("video_url", "url", "output_url", "download_url"):
        val = data.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return await _download_url_bytes(val)

    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        for key in ("video_url", "url", "output_url", "download_url"):
            val = arr[0].get(key)
            if isinstance(val, str) and val.startswith("http"):
                return await _download_url_bytes(val)

    raise Exception(f"Видео не найдено в ответе: {str(data)[:400]}")


async def generate_image(prompt: str, selected_key: str) -> tuple[bytes, str]:
    model_keys = [selected_key] + [k for k in IMAGE_MODELS.keys() if k != selected_key]
    last_error = None

    for key in model_keys:
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
            data = await _post_nvidia(model["path"], payload)
            return _extract_image_bytes(data), model["title"]
        except Exception as e:
            last_error = e
            logger.warning("Image model failed model=%s error=%s", model["path"], str(e))
            continue

    raise Exception(f"Генерация картинки не удалась. Последняя ошибка: {last_error}")


async def generate_video(prompt: str, selected_key: str) -> tuple[bytes, str]:
    model_keys = [selected_key] + [k for k in VIDEO_MODELS.keys() if k != selected_key]
    last_error = None

    for key in model_keys:
        model = VIDEO_MODELS[key]
        payload = {
            "prompt": prompt,
            "seed": random.randint(1, 2_147_483_647),
            "duration": 4,
            "fps": 16,
        }
        try:
            logger.info("Video attempt model=%s", model["path"])
            data = await _post_nvidia(model["path"], payload)
            video_bytes = await _extract_video_bytes(data)
            if len(video_bytes) < 2000:
                raise Exception(f"Слишком маленький видео-файл: {len(video_bytes)} байт")
            return video_bytes, model["title"]
        except Exception as e:
            last_error = e
            logger.warning("Video model failed model=%s error=%s", model["path"], str(e))
            continue

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


@router.callback_query(F.data == "mode_image_edit")
async def edit_mode_disabled(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Редактирование фото отключено</b>\n\nИспользуй генерацию картинки или видео.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "gen_type_image")
async def select_image_model(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="image", gen_model="img_flux2")
    await callback.message.edit_text(
        "<b>Генерация картинки</b>\n\nВыбери модель:",
        parse_mode="HTML",
        reply_markup=image_model_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "gen_type_video")
async def select_video_model(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await state.update_data(gen_type="video", gen_model="vid_ltx")
    await callback.message.edit_text(
        "<b>Генерация видео</b>\n\nВыбери модель:",
        parse_mode="HTML",
        reply_markup=video_model_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("gen_model_"))
async def set_generation_model(callback: CallbackQuery, state: FSMContext):
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

    if not gen_type:
        await message.answer(
            "Сначала выбери режим генерации в меню.",
            reply_markup=cancel_keyboard(),
        )
        return

    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return

    action = "upload_photo" if gen_type == "image" else "upload_video"
    await message.bot.send_chat_action(message.chat.id, action)

    status_msg = await message.answer("Генерирую, подожди...", parse_mode="HTML")

    try:
        if gen_type == "image":
            selected = gen_model if gen_model in IMAGE_MODELS else "img_flux2"
            image_bytes, used_model = await generate_image(prompt, selected)

            await status_msg.delete()
            await message.answer_photo(
                photo=BufferedInputFile(image_bytes, filename="generated.png"),
                caption=f"<b>Готово</b>\nМодель: {html.escape(used_model)}\n\n{html.escape(prompt)}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
        else:
            selected = gen_model if gen_model in VIDEO_MODELS else "vid_ltx"
            video_bytes, used_model = await generate_video(prompt, selected)

            await status_msg.delete()
            await message.answer_video(
                video=BufferedInputFile(video_bytes, filename="generated.mp4"),
                caption=f"<b>Готово</b>\nМодель: {html.escape(used_model)}\n\n{html.escape(prompt)}",
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
