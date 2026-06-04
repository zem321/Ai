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

from keyboards import cancel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

HF_TOKEN = os.getenv("HF_TOKEN")

# Генерация — Flux.1 (лучшее качество на HF бесплатно)
GEN_URL = "https://api-inference.huggingface.co/models/black-forest-labs/FLUX.1-dev"

# Редактирование — SDXL img2img
EDIT_URL = "https://api-inference.huggingface.co/models/diffusers/stable-diffusion-xl-1.0-inpainting-0.1"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_generate(prompt: str) -> bytes:
    """Генерация через Flux.1-dev"""
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": prompt,
        "parameters": {
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
        }
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(GEN_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status == 503:
                # Модель грузится — сообщаем пользователю
                raise Exception("Модель загружается (~30 сек), попробуй ещё раз через минуту")
            if resp.status != 200:
                text = await resp.text()
                try:
                    data = json.loads(text)
                    raise Exception(data.get("error", text[:200]))
                except json.JSONDecodeError:
                    raise Exception(text[:200])
            return await resp.read()


async def call_edit(image_bytes: bytes, prompt: str) -> bytes:
    """Редактирование через FLUX.1-dev img2img"""
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "inputs": prompt,
        "parameters": {
            "image": image_b64,
            "strength": 0.75,
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
        }
    }
    # Для img2img используем SDXL
    edit_url = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
    async with aiohttp.ClientSession() as session:
        async with session.post(edit_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            if resp.status == 503:
                raise Exception("Модель загружается (~30 сек), попробуй ещё раз через минуту")
            if resp.status != 200:
                text = await resp.text()
                try:
                    data = json.loads(text)
                    raise Exception(data.get("error", text[:200]))
                except json.JSONDecodeError:
                    raise Exception(text[:200])
            return await resp.read()


# ── Генерация ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения</b>\n\n"
        "📝 Опиши что хочешь создать:\n\n"
        "<i>Пример: Закат над морем в стиле аниме</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("🎨 <i>Генерирую изображение... ~30 секунд</i>", parse_mode="HTML")
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


# ── Редактирование ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото</b>\n\n"
        "📸 Отправь фото <b>с подписью</b> — напиши задание прямо под фото!\n\n"
        "<i>Пример: Сделай в стиле аниме / Измени фон на закат</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "⚠️ <b>Напиши задание прямо под фото как подпись!</b>\n\n"
            "<i>Зажми фото → добавь подпись → отправь</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    status_msg = await message.answer("✏️ <i>Редактирую фото... ~30 секунд</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_bytes = compress_image(file_bytes.read())

        result_bytes = await call_edit(image_bytes, caption)
        image_file = BufferedInputFile(result_bytes, filename="edited.png")
        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✏️ <b>Готово!</b>\n📝 {caption}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка редактирования:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
