import os
import json
import base64
import logging
from io import BytesIO
from html import escape

import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from keyboards import (
    cancel_keyboard,
    model_group_keyboard,
    models_keyboard,
    CHATGPT_MODELS,
    GEMINI_MODELS,
    OTHER_MODELS,
)
from states import BotStates


logger = logging.getLogger(__name__)
router = Router()

SYSTEM_PROMPT = (
    "Ты полезный ИИ-ассистент. "
    "Отвечай на русском языке если вопрос на русском. "
    "Будь точным и лаконичным."
)

MAX_HISTORY = 20
MAX_IMAGE_BYTES = 15 * 1024 * 1024

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY", "")
FREEMODEL_API_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"


# ------------------ Вспомогательные функции ------------------

def get_history(data):
    return data.get("chat_history", [])


def get_model(data):
    return data.get("selected_model", list(CHATGPT_MODELS.keys())[0])


def strip_provider_prefix(model_id: str) -> str:
    return model_id.replace("freemodel/", "", 1)


def trim_history(history: list) -> list:
    return history[-MAX_HISTORY:]


def guess_mime_type(file_path: str | None) -> str:
    if not file_path:
        return "image/jpeg"

    path = file_path.lower()

    if path.endswith(".png"):
        return "image/png"
    if path.endswith(".webp"):
        return "image/webp"
    if path.endswith(".gif"):
        return "image/gif"

    return "image/jpeg"


def extract_api_error(data) -> str:
    if not isinstance(data, dict):
        return str(data)

    error = data.get("error")

    if isinstance(error, dict):
        return error.get("message") or json.dumps(error, ensure_ascii=False)[:500]

    if isinstance(error, str):
        return error

    if "detail" in data:
        return str(data["detail"])

    return json.dumps(data, ensure_ascii=False)[:500]


async def edit_error(status_msg: Message, title: str, error: Exception):
    logger.exception(title)

    await status_msg.edit_text(
        f"<b>{escape(title)}:</b> {escape(str(error))}",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )


async def send_ai_reply(status_msg: Message, reply: str):
    """
    Отправляет ответ модели.
    parse_mode специально не используем, потому что модель может вернуть Markdown/HTML,
    из-за чего Telegram иногда падает с ошибкой парсинга.
    """
    if not reply:
        reply = "Пустой ответ от модели."

    chunks = [reply[i:i + 3900] for i in range(0, len(reply), 3900)]

    if len(chunks) == 1:
        await status_msg.edit_text(chunks[0], reply_markup=cancel_keyboard())
        return

    await status_msg.edit_text(chunks[0])

    for index, chunk in enumerate(chunks[1:], start=1):
        is_last = index == len(chunks) - 1
        await status_msg.answer(
            chunk,
            reply_markup=cancel_keyboard() if is_last else None,
        )


async def telegram_file_to_data_url(message: Message, file_id: str, mime_type: str | None = None) -> str:
    """
    Скачивает файл из Telegram и превращает его в data:image/...;base64,...
    Это лучше, чем передавать внешнюю ссылку Telegram в модель.
    """
    tg_file = await message.bot.get_file(file_id)

    if not tg_file.file_path:
        raise Exception("Telegram не вернул путь к файлу.")

    buffer = BytesIO()
    await message.bot.download_file(tg_file.file_path, destination=buffer)

    image_bytes = buffer.getvalue()

    if not image_bytes:
        raise Exception("Не удалось скачать изображение из Telegram.")

    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise Exception("Изображение слишком большое. Отправьте фото меньшего размера.")

    final_mime_type = mime_type or guess_mime_type(tg_file.file_path)

    if not final_mime_type.startswith("image/"):
        final_mime_type = guess_mime_type(tg_file.file_path)

    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{final_mime_type};base64,{encoded}"


def make_vision_content(prompt: str, image_data_url: str) -> list:
    prompt = (prompt or "").strip()

    if not prompt:
        prompt = "Проанализируй изображение и ответь, что на нём изображено."

    return [
        {
            "type": "text",
            "text": prompt,
        },
        {
            "type": "image_url",
            "image_url": {
                "url": image_data_url,
            },
        },
    ]


# ------------------ Вызов моделей ------------------

async def call_nvidia(model_id: str, messages: list) -> str:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан.")

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model_id,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            NVIDIA_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()

            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"NVIDIA вернул неожиданный ответ: {text[:500]}")

            if resp.status != 200:
                raise Exception(extract_api_error(data))

            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                raise Exception(f"Nеверный формат ответа NVIDIA: {json.dumps(data, ensure_ascii=False)[:500]}")


async def call_freemodel_openai(raw_model: str, messages: list) -> str:
    if not FREEMODEL_API_KEY:
        raise Exception("FREEMODEL_API_KEY не задан.")

    headers = {
        "Authorization": f"Bearer {FREEMODEL_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": raw_model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7,
    }

    url = f"{FREEMODEL_API_BASE.rstrip('/')}/v1/chat/completions"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()

            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"FreeModel вернул неожиданный ответ: {text[:500]}")

            if resp.status != 200:
                raise Exception(extract_api_error(data))

            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                raise Exception(f"Неверный формат ответа FreeModel: {json.dumps(data, ensure_ascii=False)[:500]}")


async def call_gemini(model_id: str, messages: list) -> str:
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY не задан.")

    # Убираем префикс "gemini/" для запроса
    raw_model = model_id.replace("gemini/", "", 1)

    headers = {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": raw_model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7,
    }

    url = f"{GEMINI_API_BASE.rstrip('/')}/chat/completions"

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()

            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Gemini вернул неожиданный ответ: {text[:500]}")

            if resp.status != 200:
                raise Exception(extract_api_error(data))

            try:
                return data["choices"][0]["message"]["content"]
            except Exception:
                raise Exception(f"Неверный формат ответа Gemini: {json.dumps(data, ensure_ascii=False)[:500]}")


async def call_ai(model_id: str, messages: list) -> str:
    if model_id.startswith("freemodel/"):
        return await call_freemodel_openai(strip_provider_prefix(model_id), messages)

    if model_id.startswith("gemini/"):
        return await call_gemini(model_id, messages)

    return await call_nvidia(model_id, messages)


# ------------------ Обработчики выбора моделей ------------------

@router.callback_query(F.data == "select_model")
async def select_model_group(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Выберите группу моделей</b>",
        reply_markup=model_group_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("model_group_"))
async def show_models_group(callback: CallbackQuery, state: FSMContext):
    group = callback.data.replace("model_group_", "")

    data = await state.get_data()
    current = data.get("selected_model", "")

    await callback.message.edit_text(
        f"<b>Модели группы {escape(group.capitalize())}</b>",
        reply_markup=models_keyboard(group, current),
        parse_mode="HTML",
    )

    await callback.answer()


@router.callback_query(F.data.startswith("model_"))
async def set_model(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "")

    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)

    await callback.message.edit_text(
        "<b>Модель выбрана</b>\n\nПиши сообщения.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )

    await callback.answer()


# ------------------ Обработчик кнопки "Чат с ИИ" ------------------

@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    model_id = data.get("selected_model") or list(CHATGPT_MODELS.keys())[0]

    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)

    await callback.message.edit_text(
        "<b>Режим чата активирован</b>\n\n"
        "Пиши свои сообщения.\n"
        "Для анализа фото выбери модель с поддержкой Vision.\n\n"
        "/clear — очистить историю",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )

    await callback.answer()


# ------------------ Обработчики чата ------------------

@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)

    status_msg = await message.answer("<i>Думаю...</i>", parse_mode="HTML")

    try:
        history = list(get_history(data))

        history.append({
            "role": "user",
            "content": message.text,
        })

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ] + trim_history(history)

        reply = await call_ai(model_id, messages)

        history.append({
            "role": "assistant",
            "content": reply,
        })

        await state.update_data(chat_history=trim_history(history))
        await send_ai_reply(status_msg, reply)

    except Exception as e:
        await edit_error(status_msg, "Ошибка", e)


@router.message(BotStates.chat_mode, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)

    status_msg = await message.answer("<i>Обрабатываю фото...</i>", parse_mode="HTML")

    try:
        history = list(get_history(data))

        photo = message.photo[-1]
        caption = message.caption or ""

        image_data_url = await telegram_file_to_data_url(
            message=message,
            file_id=photo.file_id,
            mime_type="image/jpeg",
        )

        user_content = make_vision_content(
            prompt=caption,
            image_data_url=image_data_url,
        )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ] + trim_history(history) + [
            {
                "role": "user",
                "content": user_content,
            }
        ]

        reply = await call_ai(model_id, messages)

        history.append({
            "role": "user",
            "content": f"[Фото] {caption}".strip(),
        })

        history.append({
            "role": "assistant",
            "content": reply,
        })

        await state.update_data(chat_history=trim_history(history))
        await send_ai_reply(status_msg, reply)

    except Exception as e:
        await edit_error(
            status_msg,
            "Ошибка при обработке фото. Проверьте, что выбрана модель с поддержкой Vision",
            e,
        )


@router.message(BotStates.chat_mode, F.document)
async def handle_image_document(message: Message, state: FSMContext):
    """
    Дополнительно: если пользователь отправил картинку не как фото,
    а как файл, бот тоже попробует её обработать.
    """
    document = message.document

    if not document:
        await message.answer(
            "Я могу обработать текст или изображение.",
            reply_markup=cancel_keyboard(),
        )
        return

    mime_type = document.mime_type or ""

    if not mime_type.startswith("image/"):
        await message.answer(
            "Этот файл не похож на изображение. Отправьте фото или картинку.",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    model_id = get_model(data)

    status_msg = await message.answer("<i>Обрабатываю изображение...</i>", parse_mode="HTML")

    try:
        history = list(get_history(data))
        caption = message.caption or ""

        image_data_url = await telegram_file_to_data_url(
            message=message,
            file_id=document.file_id,
            mime_type=mime_type,
        )

        user_content = make_vision_content(
            prompt=caption,
            image_data_url=image_data_url,
        )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            }
        ] + trim_history(history) + [
            {
                "role": "user",
                "content": user_content,
            }
        ]

        reply = await call_ai(model_id, messages)

        history.append({
            "role": "user",
            "content": f"[Изображение-файл] {caption}".strip(),
        })

        history.append({
            "role": "assistant",
            "content": reply,
        })

        await state.update_data(chat_history=trim_history(history))
        await send_ai_reply(status_msg, reply)

    except Exception as e:
        await edit_error(
            status_msg,
            "Ошибка при обработке изображения. Проверьте, что выбрана модель с поддержкой Vision",
            e,
        )


@router.message(BotStates.chat_mode)
async def handle_unsupported_message(message: Message):
    await message.answer(
        "Я могу обработать текст, фото или изображение-файл. "
        "Если отправляете фото с заданием, напишите задание в подписи к фото.",
        reply_markup=cancel_keyboard(),
    )
