import os
import base64
import logging
import json
import io
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard, model_group_keyboard, models_keyboard, MODELS, CHATGPT_MODELS, OTHER_MODELS
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

SYSTEM_PROMPT = "Ты полезный ИИ-ассистент. Отвечай на русском языке если вопрос на русском. Будь точным и лаконичным."
MAX_HISTORY = 20

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY", "")
FREEMODEL_OPENAI_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")


def get_history(data):
    return data.get("chat_history", [])


def get_model(data):
    return data.get("selected_model", "nvidia/llama-3.3-nemotron-super-49b-v1.5")


def get_provider(model_id: str) -> str:
    return "freemodel" if model_id.startswith("freemodel/") else "nvidia"


def strip_provider_prefix(model_id: str) -> str:
    return model_id.replace("freemodel/", "", 1)


def join_api_path(base: str, path: str) -> str:
    clean_base = (base or "").strip().rstrip("/")
    if clean_base.endswith("/v1") and path.startswith("/v1/"):
        path = path[3:]
    return f"{clean_base}{path}"


def compress_image(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    return base64.b64encode(output.getvalue()).decode("utf-8")


def to_openai_messages(messages: list) -> list:
    out = []
    for msg in messages:
        out.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", "")
        })
    return out


async def call_nvidia(model_id: str, messages: list) -> str:
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model_id, "messages": to_openai_messages(messages), "max_tokens": 2048, "temperature": 0.7}
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_CHAT_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            text = await resp.text()
            data = json.loads(text)
            return data["choices"][0]["message"]["content"]


async def call_freemodel_openai(raw_model: str, messages: list) -> str:
    headers = {"Authorization": f"Bearer {FREEMODEL_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": raw_model, "messages": to_openai_messages(messages), "max_tokens": 2048, "temperature": 0.7}
    url = join_api_path(FREEMODEL_OPENAI_BASE, "/v1/chat/completions")
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            text = await resp.text()
            data = json.loads(text)
            return data["choices"][0]["message"]["content"]


async def call_ai(model_id: str, messages: list) -> str:
    return await call_nvidia(model_id, messages) if not model_id.startswith("freemodel/") else await call_freemodel_openai(strip_provider_prefix(model_id), messages)


# ---------------- Обработчики выбора моделей ----------------

@router.callback_query(F.data == "select_model")
async def select_model_group(callback: CallbackQuery):
    await callback.message.edit_text("<b>Выберите группу моделей</b>", reply_markup=model_group_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("model_group_"))
async def show_models_group(callback: CallbackQuery, state: FSMContext):
    group = callback.data.replace("model_group_", "")
    data = await state.get_data()
    current = data.get("selected_model", "")
    await callback.message.edit_text(
        f"<b>Модели группы {group.capitalize()}</b>",
        reply_markup=models_keyboard(group, current),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("model_"))
async def set_generation_model(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "", 1)
    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text("<b>Модель выбрана</b>\n\nПиши сообщения.", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


# ---------------- Чат ----------------

@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("<i>Думаю...</i>", parse_mode="HTML")
    try:
        history = get_history(data)
        history.append({"role": "user", "content": message.text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]
        reply = await call_ai(model_id, messages)
        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)
        await status_msg.edit_text(reply, parse_mode="HTML", reply_markup=cancel_keyboard())
    except Exception as e:
        await status_msg.edit_text(f"<b>Ошибка:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())
