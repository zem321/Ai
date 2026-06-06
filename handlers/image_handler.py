import os
import base64
import logging
import aiohttp
import json
import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard, image_size_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

# Ваша переменная API_KEY должна содержать официальный ключ NVIDIA (формата nvapi-...)
API_KEY = os.getenv("API_KEY")

# 🎯 Прямые официальные эндпоинты NVIDIA API Catalog
NVIDIA_GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev"
# Для оживления фото (Image-to-Video) используем передовую модель Cosmos 1.0
NVIDIA_ANIMATE_URL = "https://ai.api.nvidia.com/v1/genai/nvidia/cosmos-1.0-image-to-video-7b"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_generate(prompt: str, size: str) -> bytes:
    """Прямой запрос к NVIDIA для генерации изображений через Flux 1.1 / Dev"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    payload = {
        "prompt": prompt,
        "image_size": size,  # Поддерживает форматы "1024x1024", "1792x1024" и т.д.
        "seed": 0
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_GEN_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise Exception(f"NVIDIA Gen Error {resp.status}: {text[:300]}")
            try:
                data = json.loads(text)
                # NVIDIA возвращает изображение в ключе base64 внутри массива artifacts
                base64_str = data["artifacts"][0]["base64"]
                return base64.b64decode(base64_str)
            except Exception as e:
                raise Exception(f"Ошибка обработки ответа генерации: {str(e)}")


async def call_edit(image_bytes: bytes, prompt: str, size: str) -> bytes:
    """Прямой запрос к NVIDIA для оживления/изменения фото (Image-to-Video)"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    # Кодируем входное изображение в base64 строку для передачи внутри JSON-тела
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    
    payload = {
        "image": f"data:image/png;base64,{encoded_image}",
        "prompt": prompt or "Animate this image smoothly, realistic movement, high quality",
        "negative_prompt": "low quality, distorted, static",
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_ANIMATE_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise Exception(f"NVIDIA Edit/Animate Error {resp.status}: {text[:300]}")
            try:
                data = json.loads(text)
                # Получаем готовый медиа-файл (картинку или MP4 видео) из base64
                base64_str = data["artifacts"][0]["base64"]
                return base64.b64decode(base64_str)
            except Exception as e:
                raise Exception(f"Ошибка обработки ответа редактирования: {str(e)}")


# ── Раздел: Генерация ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения через NVIDIA (Flux.1)</b>\n\nВыбери размер:",
        reply_markup=image_size_keyboard("gen"),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("size_gen_"))
async def size_gen_selected(callback: CallbackQuery, state: FSMContext):
    size = callback.data.replace("size_gen_", "")
    await state.update_data(image_size=size)
    await callback.message.edit_text(
        f"✅ Размер: <b>{size}</b>\n\n"
        f"📝 <b>Опиши то, что хочешь создать:</b>\n\n"
        f"<i>Пример: Фиолетовый суперкар на улицах ночного города, киберпанк</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    data = await state.get_data()
    size = data.get("image_size", "1024x1024")
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("🎨 <i>Генерирую изображение через NVIDIA... ~20 секунд</i>", parse_mode="HTML")
    try:
        image_bytes = await call_generate(message.text, size)
        image_file = BufferedInputFile(image_bytes, filename="generated.png")
        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"🎨 <b>Готово!</b>\n📝 {message.text}\n📐 {size}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Image gen error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка генерации NVIDIA:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )


# ── Раздел: Редактирование / Оживление ──────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "🎬 <b>Оживление и обработка фото через NVIDIA Cosmos</b>\n\n"
        "📸 Отправь фото <b>с подписью</b> — напиши инструкции прямо под файлом!\n\n"
        "<i>Пример подписи: Оживи этот кадр, добавь плавное движение волн</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "⚠️ <b>Напиши задание (промпт) прямо под фото как подпись!</b>\n\n"
            "<i>Зажми фото → добавить подпись → отправь</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    image_bytes = compress_image(file_bytes.read())
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    await state.update_data(
        edit_image_b64=image_b64,
        edit_prompt=caption,
        edit_step="waiting_size"
    )
    await message.answer(
        f"✅ <b>Фото и инструкции приняты!</b>\n\n"
        f"📝 Инструкция: <i>{caption}</i>\n\n"
        f"Выбери желаемое соотношение сторон:",
        reply_markup=image_size_keyboard("edit"),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("size_edit_"))
async def size_edit_selected(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("edit_step") != "waiting_size":
        await callback.answer("Сначала отправь фото с подписью!", show_alert=True)
        return

    size = callback.data.replace("size_edit_", "")
    await state.update_data(image_size=size, edit_step="processing")

    status_msg = await callback.message.edit_text(
        "🚀 <i>Нейросеть NVIDIA обрабатывает медиа... Это может занять до 30-40 секунд</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    prompt = data.get("edit_prompt")

    try:
        result_bytes = await call_edit(image_bytes, prompt, size)
        await status_msg.delete()
        
        # 💡 Умная проверка типа контента: видеофайлы MP4 от NVIDIA Cosmos содержат сигнатуру 'ftyp'
        is_video = b"ftyp" in result_bytes[:50] or b"moov" in result_bytes[:150]
        
        if is_video:
            video_file = BufferedInputFile(result_bytes, filename="animated_video.mp4")
            await callback.message.answer_video(
                video=video_file,
                caption=f"🎬 <b>Ваше видео готово!</b>\n📝 {prompt}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard()
            )
        else:
            image_file = BufferedInputFile(result_bytes, filename="edited_image.png")
            await callback.message.answer_photo(
                photo=image_file,
                caption=f"✏️ <b>Изображение обновлено!</b>\n📝 {prompt}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard()
            )
            
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None, edit_prompt=None)
    except Exception as e:
        logger.error(f"NVIDIA processing error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка обработки NVIDIA API:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
