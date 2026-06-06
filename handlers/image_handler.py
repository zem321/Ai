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

# Официальный ключ NVIDIA (формата nvapi-...)
API_KEY = os.getenv("API_KEY")

# 🎯 Точные и актуальные производственные эндпоинты NVIDIA API Catalog
NVIDIA_GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux-1-dev"
NVIDIA_ANIMATE_URL = "https://ai.api.nvidia.com/v1/genai/nvidia/cosmos-1-0-i2v-7b"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_generate(prompt: str, size: str) -> bytes:
    """Прямой запрос к NVIDIA для генерации изображений через Flux 1 Dev"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    payload = {
        "prompt": prompt,
        "image_size": size,  # "1024x1024", "1792x1024" и т.д.
        "seed": 0
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_GEN_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise Exception(f"NVIDIA Gen Error {resp.status}: {text[:300]}")
            try:
                data = json.loads(text)
                base64_str = data["artifacts"][0]["base64"]
                return base64.b64decode(base64_str)
            except Exception as e:
                raise Exception(f"Ошибка парсинга ответа Flux: {str(e)}")


async def call_edit(image_bytes: bytes, prompt: str) -> bytes:
    """Прямой запрос к NVIDIA Cosmos 1.0 Image-to-Video для анимации фото"""
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    encoded_image = base64.b64encode(image_bytes).decode("utf-8")
    
    payload = {
        "image": f"data:image/png;base64,{encoded_image}",
        "prompt": prompt or "Animate this image smoothly, high quality, cinematic motion",
        "negative_prompt": "low quality, distorted, static, ugly",
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
                raise Exception(f"Ошибка парсинга ответа Cosmos: {str(e)}")


# ── Раздел: Генерация (Flux 1 Dev) ──────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения через NVIDIA (Flux 1 Dev)</b>\n\nВыбери размер:",
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
        f"<i>Пример: Фиолетовый суперкар Lamborghini на фоне неонового города</i>",
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


# ── Раздел: Оживление фото (NVIDIA Cosmos 1.0 i2v) ──────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await callback.message.edit_text(
        "🎬 <b>Оживление фото через NVIDIA Cosmos (Image-to-Video)</b>\n\n"
        "📸 Отправь мне фото <b>с подписью</b> — напиши задачу прямо под файлом!\n\n"
        "<i>Пример подписи: Оживи этот кадр, добавь реалистичное движение дыма и фар автомобиля</i>",
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

    # Отправляем экшн загрузки видео, так как Cosmos генерирует MP4
    await message.bot.send_chat_action(message.chat.id, "upload_video")
    status_msg = await message.answer(
        "🚀 <i>Нейросеть NVIDIA Cosmos оживляет ваше фото... Это займет около 30 секунд</i>",
        parse_mode="HTML"
    )

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        
        # Сжимаем фото и отправляем напрямую в Cosmos
        image_bytes = compress_image(file_bytes.read())
        result_bytes = await call_edit(image_bytes, caption)
        
        await status_msg.delete()
        
        # Проверяем заголовки файла: видеофайлы MP4 содержат байты 'ftyp' или 'moov'
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
            image_file = BufferedInputFile(result_bytes, filename="edited_image.png")
            await message.answer_photo(
                photo=image_file,
                caption=f"✏️ <b>Изображение обновлено!</b>\n📝 {caption}",
                parse_mode="HTML",
                reply_markup=cancel_keyboard()
            )
            
    except Exception as e:
        logger.error(f"NVIDIA processing error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка обработки NVIDIA API:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
