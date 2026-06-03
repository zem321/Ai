import os
import logging
import aiohttp
import json
import base64
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from keyboards import cancel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY")
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Локальная память диалогов
USER_HISTORY = {}


def get_user_history(user_id: int) -> list:
    if user_id not in USER_HISTORY:
        USER_HISTORY[user_id] = []
    return USER_HISTORY[user_id]


def clear_user_history(user_id: int):
    USER_HISTORY[user_id] = []


async def call_openrouter(model: str, messages: list) -> str:
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 1500,  # Защита от ошибки 402
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(CHAT_URL, headers=headers, json=payload) as resp:
            if resp.status == 402:
                return "❌ Ошибка баланса (код 402): на балансе ключа OpenRouter недостаточно средств для этого запроса. Пожалуйста, пополните аккаунт."
            if resp.status != 200:
                err_data = await resp.text()
                logger.error(f"OpenRouter error {resp.status}: {err_data}")
                return f"❌ Ошибка при обработке запроса ИИ:\nОшибка сервера (код {resp.status})"

            result = await resp.json()
            try:
                return result["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                return "❌ Не удалось прочитать ответ от ИИ."


# Обрабатываем нажатие кнопки активации чата
@router.callback_query(F.data == "chat_mode")
async def start_chat_mode(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model = data.get("current_model", "gpt-5.4-mini")

    from keyboards import MODELS
    model_name = MODELS.get(model, model)

    await callback.message.edit_text(
        f"💬 <b>Режим чата активирован!</b>\n"
        f"🤖 Текущая модель: <code>{model_name}</code>\n\n"
        f"Отправь мне любое текстовое сообщение или фото для анализа. Я помню контекст беседы.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard()
    )
    await callback.answer()


@router.message(BotStates.chat_mode, F.text)
async def handle_chat_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model = data.get("current_model", "gpt-5.4-mini")

    # Включаем анимацию "печатает"
    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("⚡️ <i>ИИ думает...</i>", parse_mode="HTML")

    history = get_user_history(message.from_user.id)
    history.append({"role": "user", "content": message.text})

    if len(history) > 15:
        history = history[-15:]
        USER_HISTORY[message.from_user.id] = history

    response_text = await call_openrouter(model, history)

    await status_msg.delete()
    await message.answer(response_text, reply_markup=cancel_keyboard())

    if not response_text.startswith("❌"):
        history.append({"role": "assistant", "content": response_text})


@router.message(BotStates.chat_mode, F.photo)
async def handle_chat_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    model = data.get("current_model", "gpt-5.4-mini")

    from keyboards import VISION_MODELS
    if model not in VISION_MODELS:
        await message.answer(
            "❌ Выбранная модель не поддерживает анализ изображений. Смените модель в меню.",
            reply_markup=cancel_keyboard()
        )
        return

    await bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("📸 <i>Скачиваю и анализирую фото...</i>", parse_mode="HTML")

    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file_info.file_path)
    b64_image = base64.b64encode(file_bytes.read()).decode("utf-8")

    prompt = message.caption or "Что изображено на этой фотографии? Опиши подробно."

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_image}"},
                },
            ],
        }
    ]

    response_text = await call_openrouter(model, messages)
    await status_msg.delete()
    await message.answer(response_text, reply_markup=cancel_keyboard())
