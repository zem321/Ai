import os
import base64
import logging
import aiohttp
import json
import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard, text_category_keyboard, text_model_keyboard, ALL_TEXT_MODELS, VISION_MODELS, TEXT_MODELS
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY", "")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "")

PROXY_CHAT_URL = "https://ai-proxy.izisoft.xyz/v1/chat/completions"
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

SYSTEM_PROMPT = "Ты полезный ИИ-ассистент. Отвечай на русском языке если вопрос на русском. Будь точным и лаконичным."
MAX_HISTORY = 20

def get_history(data): return data.get("chat_history", [])
def get_model(data): return data.get("selected_model", "gpt-5.4-mini")

def is_nvidia_model(model_id: str) -> bool:
    return model_id in TEXT_MODELS.get("nvidia", {})

def compress_image(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((512, 512), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=60)
    return base64.b64encode(output.getvalue()).decode("utf-8")

async def call_ai(model_id: str, messages: list) -> str:
    use_nvidia = is_nvidia_model(model_id)
    url = NVIDIA_CHAT_URL if use_nvidia else PROXY_CHAT_URL
    token = NVIDIA_API_KEY if use_nvidia else API_KEY

    if not token:
        raise Exception(f"API Ключ для {'NVIDIA' if use_nvidia else 'Proxy'} не настроен!")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": 2000,
        "temperature": 0.7,
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)))
            return data["choices"][0]["message"]["content"]

# ── Выбор текстовой модели ─────────────────────────────────────────

@router.callback_query(F.data == "select_text_cat")
async def cb_select_text_cat(callback: CallbackQuery):
    await callback.message.edit_text("Выбери семейство моделей:", reply_markup=text_category_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("cat_"))
async def cb_select_text_model(callback: CallbackQuery, state: FSMContext):
    cat_id = callback.data.replace("cat_", "")
    data = await state.get_data()
    current = get_model(data)
    await callback.message.edit_text(
        "🤖 <b>Выбери модель:</b>",
        reply_markup=text_model_keyboard(cat_id, current),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("model_"))
async def cb_model_selected(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "", 1)
    await state.update_data(selected_model=model_id)
    model_name = ALL_TEXT_MODELS.get(model_id, model_id)
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text(
        f"✅ <b>Модель:</b> {model_name}\n\nПиши сообщения или отправляй фото с подписью!",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer(f"✅ {model_name}")

# ── Вход в чат ──────────────────────────────────────────────────────

@router.message(Command("chat"))
async def enter_chat_mode_cmd(message: Message, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model_name = ALL_TEXT_MODELS.get(get_model(data), get_model(data))
    await message.answer(f"💬 <b>Режим чата</b>\n🤖 Модель: <b>{model_name}</b>\n\n/clear — очистить историю", reply_markup=cancel_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model_name = ALL_TEXT_MODELS.get(get_model(data), get_model(data))
    await callback.message.edit_text(f"💬 <b>Режим чата</b>\n🤖 Модель: <b>{model_name}</b>\n\n/clear — очистить историю", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.message(Command("clear"))
async def clear_history_cmd(message: Message, state: FSMContext):
    await state.update_data(chat_history=[])
    await message.answer("🗑 <b>История очищена!</b>", reply_markup=cancel_keyboard(), parse_mode="HTML")

@router.callback_query(F.data == "clear_history")
async def clear_history_cb(callback: CallbackQuery, state: FSMContext):
    await state.update_data(chat_history=[])
    await callback.message.edit_text("🗑 <b>История очищена!</b>", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.message(BotStates.chat_mode, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = ALL_TEXT_MODELS.get(model_id, model_id)

    if model_id not in VISION_MODELS:
        await message.answer(f"⚠️ Модель <b>{model_name}</b> не поддерживает зрение. Выбери Claude, GPT-5.5 или Llama 3.2 Vision.", parse_mode="HTML", reply_markup=cancel_keyboard())
        return

    caption = message.caption or "Подробно опиши что на этом фото"
    status_msg = await message.answer("🔍 <i>Анализирую фото...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_b64 = compress_image(file_bytes.read())

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

        history.append({"role": "user", "content": f"[Фото] {caption}"})
        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)

        await status_msg.edit_text(f"🖼 <b>Анализ:</b>\n\n{reply}\n\n<i>🤖 {model_name}</i>", parse_mode="HTML", reply_markup=cancel_keyboard())
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await status_msg.edit_text(f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())

@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = ALL_TEXT_MODELS.get(model_id, model_id)

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

        await status_msg.edit_text(f"{reply}\n\n<i>🤖 {model_name}</i>", parse_mode="HTML", reply_markup=cancel_keyboard())
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await status_msg.edit_text(f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())
