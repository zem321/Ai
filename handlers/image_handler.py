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
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_generate(prompt: str, size: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    # Модели генерации картинок через OpenRouter вызываются через специальный payload
    payload = {
        "model": "openai/dall-e-3",  # Либо gpt-image-2 / другой поддерживаемый провайдер
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://openrouter.ai/api/v1/images/generations", headers=headers, json=payload) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise Exception(f"OpenRouter Image Error ({resp.status}): {err_text}")
            result = await resp.json()
            b64_data = result["data"][0]["b64_json"]
            return base64.b64decode(b64_data)


async def call_edit(image_bytes: bytes, prompt: str, size: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    compressed = compress_image(image_bytes)
    b64_image = base64.b64encode(compressed).decode("utf-8")

    # Использование структуры чата с modalities для редактирования в OpenRouter
    payload = {
        "model": "google/gemini-2.5-pro",  # Модель, поддерживающая мультимодальное изменение
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Отредактируй это изображение по следующему описанию: {prompt}. Верни только измененное изображение в ответе."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                ]
            }
        ],
        "max_tokens": 2000
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                raise Exception(f"OpenRouter Edit Error ({resp.status}): {err_text}")
            result = await resp.json()
            # Провайдеры возвращают либо текст, либо ссылку/b64 в зависимости от модели
            try:
                content = result["choices"][0]["message"]["content"]
                if "base64," in content:
                    content = content.split("base64,")[1].strip()
                return base64.b64decode(content)
            except Exception:
                raise Exception("Не удалось распарсить отредактированное изображение от ИИ.")


@router.callback_query(F.data == "image_generate")
async def cb_img_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Режим генерации изображений</b>\n\n"
        "Опиши текстом то, что ты хочешь увидеть на картинке (желательно на английском для лучшего результата):",
        parse_mode="HTML",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def img_gen_text(message: Message, state: FSMContext):
    await state.update_data(gen_prompt=message.text)
    await message.answer(
        f"📝 Промпт: <i>«{message.text}»</i>\n\nВыбери желаемое разрешение картинки:",
        parse_mode="HTML",
        reply_markup=image_size_keyboard(mode="gen")
    )


@router.callback_query(F.data.startswith("size_gen_"))
async def size_gen_selected(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    prompt = data.get("gen_prompt")
    if not prompt:
        await callback.answer("Ошибка сессии. Введи промпт заново.", show_alert=True)
        return

    size = callback.data.replace("size_gen_", "")
    status_msg = await callback.message.edit_text(
        "🎨 <i>Генерирую картинку... Ориентировочно ~20 секунд.</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    try:
        img_bytes = await call_generate(prompt, size)
        image_file = BufferedInputFile(img_bytes, filename="generated.png")
        await status_msg.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=f"🎨 <b>Готово!</b>\n📝 {prompt}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Generation error: {e}")
        await status_msg.edit_text(
            f"❌ Произошла ошибка при генерации:\n<code>{str(e)[:100]}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )


@router.callback_query(F.data == "image_edit")
async def cb_img_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "✏️ <b>Режим умного редактирования</b>\n\n"
        "Отправь мне фотографию, а в <u>описании к ней (caption)</u> напиши, что именно необходимо изменить или добавить.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def img_edit_received(message: Message, state: FSMContext, bot: Bot):
    if not message.caption:
        await message.answer("❌ Пожалуйста, отправь фото повторно, но обязательно добавь текстовое описание изменений в подпись к картинке.")
        return

    status_msg = await message.answer("📥 <i>Загружаю исходное фото...</i>", parse_mode="HTML")
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file_info.file_path)

    b64_img = base64.b64encode(file_bytes.read()).decode("utf-8")
    await state.update_data(
        edit_image_b64=b64_img,
        edit_prompt=message.caption,
        edit_step="waiting_size"
    )

    await status_msg.delete()
    await message.answer(
        "Выбери итоговое разрешение:",
        reply_markup=image_size_keyboard(mode="edit")
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
        "✏️ <i>Редактирую фото... ~20 секунд</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    prompt = data.get("edit_prompt")

    try:
        result_bytes = await call_edit(image_bytes, prompt, size)
        image_file = BufferedInputFile(result_bytes, filename="edited.png")
        await status_msg.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=f"✏️ <b>Готово!</b>\n📝 {prompt}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None, edit_prompt=None)
    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка при редактировании:\n<code>{str(e)[:100]}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
