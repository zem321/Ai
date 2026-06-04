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

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1/chat/completions"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    return output.getvalue()


async def call_nvidia_image(model: str, messages: list) -> bytes:
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1024,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_BASE, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)[:300]))

            content = data["choices"][0]["message"]["content"]

            # Декодируем base64 из ответа
            if "data:image" in content:
                b64 = content.split(",", 1)[1]
                return base64.b64decode(b64)
            # Если чистый base64
            try:
                return base64.b64decode(content)
            except Exception:
                raise Exception(f"Модель вернула текст вместо изображения: {content[:200]}")


async def call_generate(prompt: str) -> bytes:
    messages = [{"role": "user", "content": prompt}]
    return await call_nvidia_image("qwen/qwen-image", messages)


async def call_edit(image_bytes: bytes, prompt: str) -> bytes:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        }
    ]
    return await call_nvidia_image("qwen/qwen-image-edit", messages)


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
    status_msg = await message.answer("🎨 <i>Генерирую изображение...</i>", parse_mode="HTML")
    try:
        image_bytes = await call_generate(message.text)
        image_file = BufferedInputFile(image_bytes, filename="generated.jpg")
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
        "<i>Пример: Замени фон на сад / Сделай ночным</i>",
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

    status_msg = await message.answer("✏️ <i>Редактирую фото...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_bytes = compress_image(file_bytes.read())

        result_bytes = await call_edit(image_bytes, caption)
        image_file = BufferedInputFile(result_bytes, filename="edited.jpg")
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
