import os
import base64
import logging
import aiohttp
import json
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from keyboards import cancel_keyboard, model_select_keyboard, MODELS
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY")
CHAT_URL = "https://codex.sale/v1/chat/completions"

SYSTEM_PROMPT = "Ты полезный ИИ-ассистент. Отвечай на русском языке если вопрос на русском. Будь точным и лаконичным."
MAX_HISTORY = 20

VISION_MODELS = {"gpt-5.5", "gpt-5.4", "gpt-5.4-mini"}

def get_history(data): return data.get("chat_history", [])
def get_model(data): return data.get("selected_model", "gpt-5.4-mini")


async def call_ai(model_id: str, messages: list) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.7,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(CHAT_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)))
            return data["choices"][0]["message"]["content"]


@router.callback_query(F.data == "select_model")
async def cb_select_model(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = get_model(data)
    await callback.message.edit_text(
        "🤖 <b>Выбери модель ИИ:</b>",
        reply_markup=model_select_keyboard(current),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("model_"))
async def cb_model_selected(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "", 1)
    await state.update_data(selected_model=model_id)
    model_name = MODELS.get(model_id, model_id)
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text(
        f"✅ <b>Модель:</b> {model_name}\n\nПиши сообщения или отправляй фото с подписью!",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer(f"✅ {model_name}")


@router.message(Command("chat"))
@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode(event, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model_name = MODELS.get(get_model(data), get_model(data))
    text = (
        f"💬 <b>Режим чата</b>\n\n"
        f"🤖 Модель: <b>{model_name}</b>\n\n"
        f"Пиши вопросы или отправляй фото с подписью — я отвечу сразу!\n"
        f"/clear — очистить историю"
    )
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=cancel_keyboard(), parse_mode="HTML")
        await event.answer()
    else:
        await event.answer(text, reply_markup=cancel_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "clear_history")
@router.message(Command("clear"))
async def clear_history(event, state: FSMContext):
    await state.update_data(chat_history=[])
    text = "🗑 <b>История очищена!</b>"
    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, reply_markup=cancel_keyboard(), parse_mode="HTML")
        await event.answer("История очищена!")
    else:
        await event.answer(text, reply_markup=cancel_keyboard(), parse_mode="HTML")


@router.message(BotStates.chat_mode, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)

    if model_id not in VISION_MODELS:
        await message.answer(
            f"⚠️ Модель <b>{model_name}</b> не поддерживает анализ фото.\nВыбери GPT-5.5 или GPT-5.4.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    caption = message.caption or "Опиши подробно что на этом фото"
    status_msg = await message.answer("🔍 <i>Анализирую фото...</i>", parse_mode="HTML")

    try:
        # Download photo
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_b64 = base64.b64encode(file_bytes.read()).decode("utf-8")

        # Send to AI with photo (NOT saved to history to save tokens)
        history = get_history(data)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": caption}
            ]
        })

        reply = await call_ai(model_id, messages)

        # Save only text to history (not the photo - saves tokens!)
        history.append({"role": "user", "content": f"[Фото] {caption}"})
        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)

        await status_msg.edit_text(
            f"🖼 <b>Анализ фото:</b>\n\n{reply}\n\n<i>🤖 {model_name}</i>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )


@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)

    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("⏳ <i>Думаю...</i>", parse_mode="HTML")

    try:
        history = get_history(data)
        history.append({"role": "user", "content": message.text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]
        reply = await call_ai(model_id, messages)

        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)

        await status_msg.edit_text(
            f"{reply}\n\n<i>🤖 {model_name}</i>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
