import os
import base64
import logging
import aiohttp
import json
import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard, model_select_keyboard, MODELS, VISION_MODELS
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY", "")
CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

SYSTEM_PROMPT = "Ты полезный ИИ-ассистент от NVIDIA. Отвечай строго на русском языке. Будь точным и лаконичным."
MAX_HISTORY = 20

def get_history(data): 
    return data.get("chat_history", [])

def get_model(data): 
    model = data.get("selected_model", "meta/llama-3.3-70b-instruct")
    if model not in MODELS:
        return "meta/llama-3.3-70b-instruct"
    return model

def compress_image(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((512, 512), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=60)
    return base64.b64encode(output.getvalue()).decode("utf-8")


async def call_ai(model_id: str, messages: list) -> str:
    if not API_KEY:
        raise Exception("Переменная API_KEY пустая! Добавьте её в Railway.")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 1500
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(CHAT_URL, json=payload, headers=headers) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise Exception(f"NVIDIA Chat Error {resp.status}: {text[:200]}")
            
            data = json.loads(text)
            return data["choices"][0]["message"]["content"]


@router.callback_query(F.data == "mode_chat")
async def enter_chat(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)
    
    await callback.message.edit_text(
        f"💬 <b>Режим текстового чата (NVIDIA API)</b>\n\n"
        f"🤖 Активная модель: <code>{model_name}</code>\n"
        f"✉️ Историческая память: <b>{len(get_history(data))} / {MAX_HISTORY}</b>\n\n"
        f" Напиши мне что-нибудь или отправь фото для анализа!",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data == "select_model")
async def show_models(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🤖 <b>Выбери нейросеть от NVIDIA:</b>",
        reply_markup=model_select_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("model_"))
async def model_selected(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "")
    await state.update_data(selected_model=model_id)
    model_name = MODELS.get(model_id, model_id)
    
    await callback.message.edit_text(
        f"✅ Модель успешно изменена на:\n<b>{model_name}</b>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.chat_mode, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    
    if model_id not in VISION_MODELS:
        model_id = "nvidia/llama-3.2-11b-vision-instruct"
        await state.update_data(selected_model=model_id)

    model_name = MODELS.get(model_id, model_id)
    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("👁️ <i>Анализирую изображение...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_io = await message.bot.download_file(file.file_path)
        
        # 🔥 ФИКС: Используем .getvalue() вместо .read()
        raw_bytes = file_io.getvalue()
        base64_str = compress_image(raw_bytes)

        prompt = message.caption or "Что изображено на этой фотографии? Опиши подробно на русском."
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_str}"}}
                ]
            }
        ]

        reply = await call_ai(model_id, messages)
        await status_msg.delete()
        await message.answer(
            f"{reply}\n\n<i>🤖 {model_name}</i>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await status_msg.edit_text(f"❌ <b>Ошибка анализа фото:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())


@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)

    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("⏳ <i>Думаю (NVIDIA API)...</i>", parse_mode="HTML")

    try:
        history = get_history(data)
        history.append({"role": "user", "content": message.text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]
        
        reply = await call_ai(model_id, messages)

        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)

        await status_msg.delete()
        await message.answer(
            f"{reply}\n\n<i>🤖 {model_name}</i>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Text error: {e}")
        await status_msg.edit_text(f"❌ <b>Ошибка NVIDIA API:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())
