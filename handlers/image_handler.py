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

# Официальный ключ NVIDIA (начинается с nvapi-...)
API_KEY = os.getenv("API_KEY")

# 🎯 Точные эндпоинты NVIDIA API Catalog
NVIDIA_GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux-1-dev"
NVIDIA_ANIMATE_URL = "https://ai.api.nvidia.com/v1/genai/nvidia/cosmos-1-0-i2v-7b"


def compress_image(image_bytes: bytes) -> bytes:
    """Сжимаем изображение перед отправкой в нейросеть, чтобы не превысить лимиты API"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Уменьшаем до 1024px по большей стороне для стабильной работы
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_generate(prompt: str) -> bytes:
    """Генерация фото с нуля через NVIDIA Flux 1 Dev"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    payload = {
        "prompt": prompt,
        "seed": 0
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_GEN_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise Exception(f"NVIDIA API Error {resp.status}: {text[:300]}")
            try:
                data = json.loads(text)
                base64_str = data["artifacts"][0]["base64"]
                return base64.b64decode(base64_str)
            except Exception as e:
                raise Exception(f"Ошибка получения картинки от сервера: {str(e)}")


async def call_edit(image_bytes: bytes, prompt: str) -> bytes:
    """Оживление фото (создание видео) через NVIDIA Cosmos"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    
    payload = {
        "image": f"data:image/png;base64,{encoded_image}",
        "prompt": prompt or "Animate this image, smooth and realistic motion, high quality",
        "negative_prompt": "static, ugly, deformed, blurry",
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_ANIMATE_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise Exception(f"NVIDIA Cosmos Error {resp.status}: {text[:300]}")
            try:
                data = json.loads(text)
                base64_str = data["artifacts"][0]["base64"]
                return base64.b64decode(base64_str)
            except Exception as e:
                raise Exception(f"Ошибка получения видео от сервера: {str(e)}")


# ── 1. ГЕНЕРАЦИЯ ФОТО ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация фото (NVIDIA Flux 1 Dev)</b>\n\n"
        "📝 <b>Опиши то, что хочешь создать:</b>\n\n"
        "<i>Пример: Футуристичный город на Марсе с летающими машинами</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("🎨 <i>Нейросеть рисует... (~15-20 секунд)</i>", parse_mode="HTML")
    try:
        image_bytes = await call_generate(message.text)
        image_file = BufferedInputFile(image_bytes, filename="generated.png")
        
        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"🎨 <b>Готово!</b>\n📝 {message.text}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Image gen error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка генерации:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )


# ── 2. ОЖИВЛЕНИЕ ФОТО (ВИДЕО) ────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await callback.message.edit_text(
        "🎬 <b>Оживление фото (NVIDIA Cosmos)</b>\n\n"
        "📸 Отправь мне фотографию <b>с подписью</b> — напиши задачу прямо под файлом!\n\n"
        "<i>Пример подписи: Оживи кадр, добавь движение воды и облаков</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "⚠️ <b>Обязательно напиши задание прямо под фото как подпись!</b>\n\n"
            "<i>Выбери фото → добавь подпись → отправь</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    await message.bot.send_chat_action(message.chat.id, "upload_video")
    status_msg = await message.answer(
        "🚀 <i>Нейросеть Cosmos оживляет фото...\nЭто тяжелая задача, может занять 30-45 секунд.</i>",
        parse_mode="HTML"
    )

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        
        # Сжимаем и отправляем в API
        image_bytes = compress_image(file_bytes.read())
        result_bytes = await call_edit(image_bytes, caption)
        
        await status_msg.delete()
        
        # Интеллектуальное определение типа медиа: mp4 файлы имеют сигнатуру ftyp/moov
        is_video = b"ftyp" in result_bytes[:50] or b"moov" in result_bytes[:150]
        
        if is_video:
            video_file = BufferedInputFile(result_bytes, filename="animated_video.mp4")
            await message.answer_video(
                video=video_file,
                caption=f"🎬 <b>Ваше видео готово!</b>\n📝 {caption}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard()
            )
        else:
            image_file = BufferedInputFile(result_bytes, filename="animated_image.png")
            await message.answer_photo(
                photo=image_file,
                caption=f"✏️ <b>Готово!</b>\n📝 {caption}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard()
            )
            
    except Exception as e:
        logger.error(f"NVIDIA Cosmos error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка генерации видео:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
