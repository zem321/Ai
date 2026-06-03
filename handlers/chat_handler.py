import os
import base64
import logging
import aiohttp
import json
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command

import database as db
from keyboards import cancel_keyboard, VISION_MODELS, MODELS
from states import BotStates
from handlers.image_handler import compress_image

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY")
CHAT_URL = "https://ai-proxy.izisoft.xyz/v1/chat/completions"


@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    current_model = db.get_user_model(callback.from_user.id)
    model_name = MODELS.get(current_model, current_model)
    await callback.message.edit_text(
        f"💬 <b>Режим чата с ИИ активирован!</b>\n\n"
        f"🤖 Текущая нейросеть: <b>{model_name}</b>\n"
        f"✉️ Отправь текстовое сообщение или пришли фотографию для её анализа.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("clear"))
async def cmd_clear(message: Message):
    db.clear_history(message.from_user.id)
    await message.answer("🗑 <b>История вашего диалога очищена!</b> Контекст сброшен.", parse_mode="HTML")


@router.message(BotStates.chat_mode, F.text)
async def handle_chat_text(message: Message):
    user_id = message.from_user.id
    text = message.text

    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("🤔 <i>Думаю...</i>", parse_mode="HTML")

    current_model = db.get_user_model(user_id)
    db.add_history_message(user_id, "user", text)
    history = db.get_history(user_id)

    try:
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": current_model,
            "messages": history
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(CHAT_URL, json=payload, headers=headers) as resp:
                res_text = await resp.text()
                if resp.status != 200:
                    raise Exception(f"Ошибка сервера (код {resp.status}): {res_text[:200]}")

                data = json.loads(res_text)
                reply = data["choices"][0]["message"]["content"]

                db.add_history_message(user_id, "assistant", reply)
                await status_msg.delete()
                await message.answer(reply)
    except Exception as e:
        logger.error(f"Chat completion error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка при обработке запроса ИИ:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )


@router.message(BotStates.chat_mode, F.photo)
async def handle_chat_photo(message: Message):
    user_id = message.from_user.id
    current_model = db.get_user_model(user_id)

    if current_model not in VISION_MODELS:
        await message.answer(
            f"⚠️ Выбранная модель <b>{MODELS.get(current_model, current_model)}</b> не поддерживает анализ изображений!\n\n"
            f"Переключитесь на мультимодальную модель (например, GPT-5.5 или Claude Sonnet).",
            reply_markup=cancel_keyboard(),
            parse_mode="HTML"
        )
        return

    caption = message.caption or "Проанализируй это изображение."
    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("📸 <i>Считываю и анализирую изображение...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)

        compressed_bytes = compress_image(file_bytes.read())
        base64_image = base64.b64encode(compressed_bytes).decode("utf-8")

        # Формируем структуру OpenAI Vision payload
        vision_content = [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}}
        ]

        # Для отправки в API используем полную историю + картинку в текущем запросе
        history = db.get_history(user_id)
        api_messages = history.copy()
        api_messages.append({"role": "user", "content": vision_content})

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": current_model,
            "messages": api_messages
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(CHAT_URL, json=payload, headers=headers) as resp:
                res_text = await resp.text()
                if resp.status != 200:
                    raise Exception(f"Ошибка API Vision: {res_text[:200]}")

                data = json.loads(res_text)
                reply = data["choices"][0]["message"]["content"]

                # Сохраняем в оптимизированную локальную историю текстовый аналог
                db.add_history_message(user_id, "user", vision_content)
                db.add_history_message(user_id, "assistant", reply)

                await status_msg.delete()
                await message.answer(reply)
    except Exception as e:
        logger.error(f"Vision error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка анализа фотографии:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
