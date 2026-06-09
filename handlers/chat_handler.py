import os
import json
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from keyboards import cancel_keyboard, model_group_keyboard, models_keyboard, CHATGPT_MODELS, OTHER_MODELS, MODELS
from states import BotStates

router = Router()

SYSTEM_PROMPT = "Ты полезный ИИ-ассистент. Отвечай на русском языке если вопрос на русском. Будь точным и лаконичным."
MAX_HISTORY = 20

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY", "")
FREEMODEL_OPENAI_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")


# ------------------ Вспомогательные функции ------------------

def get_history(data):
    return data.get("chat_history", [])


def get_model(data):
    return data.get("selected_model", list(CHATGPT_MODELS.keys())[0])


def strip_provider_prefix(model_id: str) -> str:
    return model_id.replace("freemodel/", "", 1)


async def call_ai(model_id: str, messages: list) -> str:
    headers = {"Authorization": f"Bearer {FREEMODEL_API_KEY if 'freemodel' in model_id else NVIDIA_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": model_id, "messages": messages, "max_tokens": 2048, "temperature": 0.7}
    url = FREEMODEL_OPENAI_BASE + "/v1/chat/completions" if "freemodel" in model_id else NVIDIA_CHAT_URL
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            data = await resp.json()
            return data["choices"][0]["message"]["content"]


# ------------------ Обработчики выбора моделей ------------------

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
async def set_model(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "")
    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text("<b>Модель выбрана</b>\n\nПиши сообщения.", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()


# ------------------ Обработчик кнопки "Чат с ИИ" ------------------

@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    model_id = data.get("selected_model") or list(CHATGPT_MODELS.keys())[0]
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text(
        "<b>Режим чата активирован</b>\n\n"
        "Пиши свои сообщения. Для анализа фото выбери модель с поддержкой Vision.\n"
        "/clear — очистить историю",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


# ------------------ Обработчики чата ------------------

@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
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
        await status_msg.edit_text(f"<b>Ошибка:</b> {str(e)}", parse_mode="HTML", reply_markup=cancel_keyboard())


# В дальнейшем можно добавить обработку фото, если нужна поддержка Vision моделей
