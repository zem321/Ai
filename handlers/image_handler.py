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

API_KEY = os.getenv("API_KEY")
IMAGE_URL = "https://ai-proxy.izisoft.xyz/v1/image/generation"


def compress_image(image_bytes: bytes) -> bytes:
    """Сжимаем фото для редактирования"""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_image_api(payload: dict) -> bytes:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(IMAGE_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)))

            item = data["data"][0]
            if "url" in item:
                async with session.get(item["url"]) as img_resp:
                    return await img_resp.read()
            elif "b64_json" in item:
                return base64.b64decode(item["b64_json"])
            else:
                raise Exception("Изображение не получено")


# ── Генерация ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения</b>\n\nВыбери размер:",
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
        f"📝 <b>Опиши что хочешь создать:</b>\n\n"
        f"<i>Пример: Закат над морем в стиле аниме</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    data = await state.get_data()
    size = data.get("image_size", "1024x1024")
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("🎨 <i>Генерирую изображение... ~20 секунд</i>", parse_mode="HTML")
    try:
        payload = {
            "model": "gpt-image-2",
            "prompt": message.text,
            "size": size,
            "n": 1,
        }
        image_bytes = await call_image_api(payload)
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
            f"❌ <b>Ошибка генерации:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )


# ── Редактирование — фото + текст в одном сообщении ───────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото</b>\n\n"
        "📸 Отправь фото <b>с подписью</b> — напиши задание прямо под фото!\n\n"
        "<i>Пример подписи: Сделай фон белым и добавь снег</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    # Получаем фото и подпись сразу
    caption = message.caption

    if not caption:
        await message.answer(
            "⚠️ Напиши задание прямо под фото как подпись!\n\n"
            "<i>Зажми фото → добавь подпись → отправь</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    # Сохраняем фото и задание
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
        f"✅ <b>Фото и задание получены!</b>\n\n"
        f"📝 Задание: <i>{caption}</i>\n\n"
        f"Выбери размер результата:",
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

    await callback.message.edit_text(
        "✏️ <i>Редактирую фото... ~20 секунд</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    # Сразу запускаем редактирование
    image_b64 = data.get("edit_image_b64")
    prompt = data.get("edit_prompt")

    try:
        payload = {
            "model": "gpt-image-2",
            "prompt": prompt,
            "image": image_b64,
            "size": size,
            "n": 1,
        }
        image_bytes = await call_image_api(payload)
        image_file = BufferedInputFile(image_bytes, filename="edited.png")

        await callback.message.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=f"✏️ <b>Готово!</b>\n📝 {prompt}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None, edit_prompt=None)

    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await callback.message.edit_text(
            f"❌ <b>Ошибка редактирования:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
