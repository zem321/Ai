# image_generation.py
import os
import io
import json
import html
import base64
import random
import logging
import tempfile
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
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://ai.api.nvidia.com/v1")

# ============ КАРТИНКИ ============
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

# ============ ВИДЕО ============
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

# ============ БЕСПЛАТНЫЕ SPACE ДЛЯ РЕДАКТИРОВАНИЯ ============
IMAGE_EDIT_SPACES = [
    {
        "name": "1Paint",
        "id": "1Paint/1Paint",
        "description": "Изменение фона, удаление объектов",
        "priority": 1,
    },
    {
        "name": "SDXL Inpainting",
        "id": "diffusers/stable-diffusion-xl-inpainting",
        "description": "Качественная замена частей изображения",
        "priority": 2,
    },
    {
        "name": "Lama Cleaner",
        "id": "camenduru/Lama Cleaner",
        "description": "Быстрое удаление объектов",
        "priority": 3,
    },
]

# ============ ПРЕСЕТЫ ============
EDIT_PRESETS = {
    "preset_beach": "change background to beautiful tropical beach with ocean waves",
    "preset_city": "change background to modern city skyline at sunset",
    "preset_forest": "change background to peaceful forest with sunlight",
    "preset_room": "change background to cozy living room interior",
    "preset_studio": "apply professional studio lighting and backdrop",
    "preset_artistic": "transform into artistic painting style",
    "preset_portrait": "make professional portrait with soft studio lighting",
    "preset_vintage": "apply vintage film photography style",
}


# ============ КЛАВИАТУРЫ ============
def gen_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖼 Картинка", callback_data="gen_type_image")],
            [InlineKeyboardButton(text="🎬 Видео", callback_data="gen_type_video")],
            [InlineKeyboardButton(text="✏️ Редактировать фото", callback_data="mode_image_edit")],
            [InlineKeyboardButton(text="🎨 Заменить фон", callback_data="mode_background_replace")],
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

def edit_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎨 Заменить фон", callback_data="edit_type_background")],
            [InlineKeyboardButton(text="🔄 Изменить стиль", callback_data="edit_type_style")],
            [InlineKeyboardButton(text="➕ Добавить объект", callback_data="edit_type_add")],
            [InlineKeyboardButton(text="✂️ Удалить объект", callback_data="edit_type_remove")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mode_image_gen")],
        ]
    )

def common_edits_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏖️ Пляж", callback_data="preset_beach")],
            [InlineKeyboardButton(text="🌆 Город", callback_data="preset_city")],
            [InlineKeyboardButton(text="🌲 Лес", callback_data="preset_forest")],
            [InlineKeyboardButton(text="🏠 Комната", callback_data="preset_room")],
            [InlineKeyboardButton(text="✨ Студийный свет", callback_data="preset_studio")],
            [InlineKeyboardButton(text="🎭 Художественный стиль", callback_data="preset_artistic")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="mode_image_edit")],
        ]
    )


# ============ ХЕЛПЕРЫ ============
async def download_file_as_bytes(bot, file_info) -> bytes:
    """Скачивает файл и возвращает байты (не BytesIO)"""
    image_io = await bot.download_file(file_info.file_path)
    return image_io.getvalue()

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
    if isinstance(d, list) and d:
        for item in d:
            if isinstance(item, dict):
                b64 = item.get("b64_json") or item.get("base64")
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

    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        b64 = d[0].get("b64_json")
        if isinstance(b64, str) and b64:
            return base64.b64decode(b64)

    raise Exception(f"Изображение не найдено в ответе: {str(data)[:400]}")


# ============ ГЕНЕРАЦИЯ КАРТИНОК ============
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


# ============ ГЕНЕРАЦИЯ ВИДЕО ============
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


# ============ РЕДАКТИРОВАНИЕ ЧЕРЕЗ HUGGINGFACE SPACES ============
async def generate_image_edit_free(
    image_bytes: bytes,
    prompt: str,
    edit_type: str = "background"
) -> tuple[bytes, str]:
    """
    Редактирует изображение через бесплатные HuggingFace Spaces.
    """
    # Создаём временный файл
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(image_bytes)
        
        errors = []
        
        for space in sorted(IMAGE_EDIT_SPACES, key=lambda x: x["priority"]):
            space_name = space["name"]
            space_id = space["id"]
            
            try:
                logger.info(f"Attempting edit with Space: {space_name}")
                
                from gradio_client import Client
                client = Client(space_id, verbose=False)
                
                if space_name == "1Paint":
                    result = client.predict(
                        {"path": tmp_path},
                        prompt,
                        api_name="/predict"
                    )
                elif space_name == "Lama Cleaner":
                    result = client.predict(
                        tmp_path,
                        prompt,
                        api_name="/predict"
                    )
                else:
                    result = client.predict(
                        tmp_path,
                        prompt,
                        api_name="/predict"
                    )
                
                if result is None:
                    continue
                
                result_bytes = None
                
                # Обработка разных форматов результата
                if isinstance(result, str) and os.path.exists(result):
                    with open(result, "rb") as f:
                        result_bytes = f.read()
                elif hasattr(result, "save"):
                    output_path = tmp_path.replace(".jpg", "_result.png")
                    result.save(output_path)
                    with open(output_path, "rb") as f:
                        result_bytes = f.read()
                    try:
                        os.remove(output_path)
                    except:
                        pass
                elif isinstance(result, dict):
                    path = result.get("path") or result.get("image") or result.get("output")
                    if path and os.path.exists(path):
                        with open(path, "rb") as f:
                            result_bytes = f.read()
                
                if result_bytes and len(result_bytes) > 1000:
                    return result_bytes, f"HF: {space_name}"
                    
            except Exception as e:
                msg = str(e)
                errors.append(f"{space_name}: {msg}")
                logger.warning(f"Space {space_name} failed: {msg}")
                continue
        
        error_summary = errors[-1] if errors else "Все Space недоступны"
        raise Exception(f"Не удалось отредактировать. Попробуйте позже.\nОшибка: {error_summary[:200]}")
        
    finally:
        # Удаляем временный файл
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except:
                pass


# ============ ОБРАБОТЧИКИ КОЛБЭКОВ ============

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
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_type=None, edit_preset=None, edit_instruction=None)
    await callback.message.edit_text(
        "<b>✏️ Редактирование фото</b>\n\nВыбери тип редактирования:",
        parse_mode="HTML",
        reply_markup=edit_type_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "mode_background_replace")
async def enter_background_replace(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_type="background", edit_preset=None, edit_instruction=None)
    await callback.message.edit_text(
        "<b>🎨 Замена фона</b>\n\nВыбери нужный фон:",
        parse_mode="HTML",
        reply_markup=common_edits_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_type_"))
async def select_edit_type(callback: CallbackQuery, state: FSMContext):
    edit_type_map = {
        "edit_type_background": "background",
        "edit_type_style": "style",
        "edit_type_add": "add",
        "edit_type_remove": "remove",
    }
    
    edit_type = edit_type_map.get(callback.data)
    if not edit_type:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    
    await state.update_data(edit_type=edit_type)
    
    type_descriptions = {
        "background": "Изменить фон изображения",
        "style": "Изменить стиль фото",
        "add": "Добавить объект на фото",
        "remove": "Удалить объект с фото",
    }
    
    await callback.message.edit_text(
        f"<b>✏️ Тип:</b> {type_descriptions.get(edit_type, edit_type)}\n\n"
        "Отправь фото и напиши инструкцию.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("preset_"))
async def select_preset(callback: CallbackQuery, state: FSMContext):
    preset_key = callback.data
    preset_instruction = EDIT_PRESETS.get(preset_key)
    
    if not preset_instruction:
        await callback.answer("Неизвестный пресет", show_alert=True)
        return
    
    await state.update_data(edit_preset=preset_key, edit_instruction=preset_instruction)
    
    preset_names = {
        "preset_beach": "🏖️ Пляж",
        "preset_city": "🌆 Город",
        "preset_forest": "🌲 Лес",
        "preset_room": "🏠 Комната",
        "preset_studio": "✨ Студийный свет",
        "preset_artistic": "🎭 Художественный стиль",
    }
    
    await callback.message.edit_text(
        f"<b>✅ Выбрано:</b> {preset_names.get(preset_key, preset_key)}\n\n"
        "Теперь отправь фото.",
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


@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено.", reply_markup=cancel_keyboard())
    await callback.answer()


# ============ ОБРАБОТЧИКИ СООБЩЕНИЙ ============

@router.message(BotStates.image_generate, F.text)
async def do_generate(message: Message, state: FSMContext):
    data = await state.get_data()
    gen_type = data.get("gen_type")
    gen_model = data.get("gen_model")
    prompt = (message.text or "").strip()

    if not gen_type:
        await message.answer("Сначала выбери режим генерации в меню.", reply_markup=cancel_keyboard())
        return

    if not prompt:
        await message.answer("Отправь текстовый запрос.")
        return

    chat_action = "upload_photo" if gen_type == "image" else "upload_video"
    await message.bot.send_chat_action(message.chat.id, chat_action)

    status_msg = await message.answer("⏳ Генерирую...", parse_mode="HTML")

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
            f"❌ Ошибка генерации:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


# ============ РЕДАКТИРОВАНИЕ ИЗОБРАЖЕНИЙ ============

@router.message(BotStates.image_edit, F.photo)
async def handle_edit_photo(message: Message, state: FSMContext):
    """Пользователь отправил фото для редактирования."""
    data = await state.get_data()
    edit_instruction = data.get("edit_instruction")
    edit_preset = data.get("edit_preset")
    edit_type = data.get("edit_type")

    status_msg = await message.answer("⏳ Скачиваю фото...", parse_mode="HTML")

    try:
        # Скачиваем фото - ИСПРАВЛЕНО!
        file_info = await message.bot.get_file(message.photo[-1].file_id)
        image_bytes = await download_file_as_bytes(message.bot, file_info)

        if len(image_bytes) < 1000:
            raise Exception("Слишком маленькое изображение")

        # Определяем инструкцию
        if edit_preset:
            instruction = EDIT_PRESETS.get(edit_preset, "edit this image")
        elif edit_instruction:
            instruction = edit_instruction
        else:
            # Сохраняем фото и ждём инструкцию
            await state.update_data(edit_image_bytes=image_bytes)
            await status_msg.edit_text(
                "📷 Фото получено!\n\n"
                "Напиши что изменить:\n"
                "• «изменить фон на пляж»\n"
                "• «сделать студийный портрет»\n"
                "• «добавить кота»",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
            return

        # Обрабатываем сразу
        await status_msg.edit_text("✏️ Обрабатываю...", parse_mode="HTML")
        await message.bot.send_chat_action(message.chat.id, "upload_photo")

        result_bytes, source = await generate_image_edit_free(image_bytes, instruction, edit_type or "background")

        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(result_bytes, filename="edited.png"),
            caption=(
                f"<b>✏️ Готово!</b>\n"
                f"Источник: {html.escape(source)}\n"
                f"Инструкция: {html.escape(instruction)}"
            ),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )

        # Сбрасываем состояние
        await state.update_data(
            edit_image_bytes=None,
            edit_instruction=None,
            edit_preset=None,
        )

    except Exception as e:
        logger.exception("Error editing photo")
        await status_msg.edit_text(
            f"❌ Ошибка обработки:\n<code>{html.escape(str(e)[:300])}</code>\n\n"
            "Попробуй ещё раз.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


@router.message(BotStates.image_edit, F.text & ~F.text.startswith("/"))
async def handle_edit_instruction(message: Message, state: FSMContext):
    """Пользователь отправил текстовую инструкцию."""
    data = await state.get_data()
    image_bytes = data.get("edit_image_bytes")

    if not image_bytes:
        instruction = (message.text or "").strip()
        if instruction:
            await state.update_data(edit_instruction=instruction)
            await message.answer(
                f"✅ Запомнил: «{instruction}»\n\nОтправь фото.",
                parse_mode="HTML",
                reply_markup=cancel_keyboard(),
            )
        else:
            await message.answer("Напиши что изменить.")
        return

    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Напиши инструкцию.")
        return

    status_msg = await message.answer("✏️ Обрабатываю...", parse_mode="HTML")

    try:
        await message.bot.send_chat_action(message.chat.id, "upload_photo")
        result_bytes, source = await generate_image_edit_free(image_bytes, prompt, data.get("edit_type", "background"))

        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(result_bytes, filename="edited.png"),
            caption=(
                f"<b>✏️ Готово!</b>\n"
                f"Источник: {html.escape(source)}\n"
                f"Инструкция: {html.escape(prompt)}"
            ),
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )

        await state.update_data(edit_image_bytes=None, edit_instruction=None)

    except Exception as e:
        logger.exception("Edit error")
        await status_msg.edit_text(
            f"❌ Ошибка:\n<code>{html.escape(str(e)[:300])}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


@router.message(BotStates.image_edit, F.document)
async def handle_edit_document(message: Message, state: FSMContext):
    await message.answer("Отправь фото (не файл).", reply_markup=cancel_keyboard())


# ============ ДОПОЛНИТЕЛЬНЫЕ КОМАНДЫ ============

@router.message(F.text == "/cancel")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=cancel_keyboard())


@router.message(BotStates.image_edit)
async def fallback_image_edit(message: Message, state: FSMContext):
    await message.answer("Отправь фото или текст.", reply_markup=cancel_keyboard())
