import os
import base64
import logging
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from keyboards import cancel_keyboard, image_size_keyboard, main_menu_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY")
IMAGE_API_URL = "https://ai-proxy.izisoft.xyz/v1/image/generation"


async def generate_image(prompt: str, size: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(IMAGE_API_URL, json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)))
            image_url = data["data"][0]["url"]

        async with session.get(image_url) as img_resp:
            return await img_resp.read()


async def edit_image(image_b64: str, prompt: str, size: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-image-2",
        "image": image_b64,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(IMAGE_API_URL, json=payload, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)))
            image_url = data["data"][0]["url"]

        async with session.get(image_url) as img_resp:
            return await img_resp.read()


# ── Генерация изображений ──────────────────────────────────────────────────────

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
        image_bytes = await generate_image(message.text, size)
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


# ── Редактирование изображений ─────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото</b>\n\n"
        "📸 Отправь фото которое хочешь отредактировать:",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    data = await state.get_data()
    edit_step = data.get("edit_step", "waiting_photo")

    if edit_step == "waiting_photo":
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_b64 = base64.b64encode(file_bytes.read()).decode("utf-8")

        await state.update_data(edit_image_b64=image_b64, edit_step="waiting_prompt")
        await message.answer(
            "✅ <b>Фото получено!</b>\n\n"
            "📝 Теперь выбери размер результата:",
            reply_markup=image_size_keyboard("edit"),
            parse_mode="HTML"
        )


@router.callback_query(F.data.startswith("size_edit_"))
async def size_edit_selected(callback: CallbackQuery, state: FSMContext):
    size = callback.data.replace("size_edit_", "")
    await state.update_data(image_size=size)
    await callback.message.edit_text(
        f"✅ Размер: <b>{size}</b>\n\n"
        f"📝 <b>Опиши что нужно изменить:</b>\n\n"
        f"<i>Пример: Сделай фон белым, добавь снег, измени цвет на синий</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.text)
async def do_edit_image(message: Message, state: FSMContext):
    data = await state.get_data()
    image_b64 = data.get("edit_image_b64")
    edit_step = data.get("edit_step")
    size = data.get("image_size", "1024x1024")

    if not image_b64 or edit_step != "waiting_prompt":
        await message.answer(
            "📸 Сначала отправь фото для редактирования.",
            reply_markup=cancel_keyboard()
        )
        return

    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("✏️ <i>Редактирую фото... ~20 секунд</i>", parse_mode="HTML")

    try:
        image_bytes = await edit_image(image_b64, message.text, size)
        image_file = BufferedInputFile(image_bytes, filename="edited.png")
        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✏️ <b>Готово!</b>\n📝 {message.text}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        # Reset for next edit
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None)
    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка редактирования:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
