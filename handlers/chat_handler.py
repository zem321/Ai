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
logger.setLevel(logging.INFO)

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

# Базовый адрес для большинства моделей freemodel/* (например freemodel/gpt-*).
FREEMODEL_API_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")

# Отдельный адрес для моделей freemodel/claude-* (Anthropic-совместимый эндпоинт).
FREEMODEL_CLAUDE_BASE = os.getenv("FREEMODEL_CLAUDE_BASE", "https://cc.freemodel.dev")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"

# Если включено (по умолчанию да), бот после каждого ответа отправляет
# отдельное сообщение с диагностикой: какая модель запрошена, какой URL
# использован, и что вернул провайдер в поле "model".
# Отключить можно переменной окружения DEBUG_MODE=0.
DEBUG_MODE = os.getenv("DEBUG_MODE", "1") not in ("0", "false", "False", "")


# ------------------ Диагностика конфигурации при импорте ------------------

logger.info("NVIDIA_CHAT_URL = %s", NVIDIA_CHAT_URL)
logger.info("FREEMODEL_API_BASE (для freemodel/gpt-* и др.) = %s", FREEMODEL_API_BASE)
logger.info("FREEMODEL_CLAUDE_BASE (для freemodel/claude-*) = %s", FREEMODEL_CLAUDE_BASE)
logger.info("GEMINI_API_BASE = %s", GEMINI_API_BASE)
logger.info("DEBUG_MODE = %s", DEBUG_MODE)

if not FREEMODEL_API_KEY:
    logger.warning(
        "FREEMODEL_API_KEY не задан. Все запросы к моделям freemodel/* "
        "(включая Claude и ChatGPT из этого бота) будут падать с ошибкой."
    )


# ------------------ Вспомогательные функции ------------------

def get_history(data):
    return data.get("chat_history", [])


def get_model(data):
    return data.get("selected_model", list(CHATGPT_MODELS.keys())[0])


def strip_provider_prefix(model_id: str) -> str:
    return model_id.replace("freemodel/", "", 1)


def freemodel_base_for(raw_model: str) -> str:
    """
    Выбирает базовый URL для freemodel/* в зависимости от имени модели
    (без префикса "freemodel/").

    Claude-модели (claude-*) идут на FREEMODEL_CLAUDE_BASE (cc.freemodel.dev),
    все остальные (gpt-*, и т.п.) — на FREEMODEL_API_BASE (api.freemodel.dev).
    """
    if raw_model.startswith("claude-"):
        return FREEMODEL_CLAUDE_BASE
    return FREEMODEL_API_BASE


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


async def send_debug_info(status_msg: Message, debug: dict):
    """
    Отправляет отдельным сообщением диагностику: какая модель была
    запрошена, на какой URL ушёл запрос, и что провайдер вернул
    в поле "model" своего ответа.
    """
    if not DEBUG_MODE or not debug:
        return

    lines = ["🔧 <b>Debug info</b>"]
    lines.append(f"Выбрана в боте: <code>{escape(str(debug.get('requested_model', '?')))}</code>")
    lines.append(f"Endpoint: <code>{escape(str(debug.get('url', '?')))}</code>")
    lines.append(f"Отправлено в payload.model: <code>{escape(str(debug.get('sent_model', '?')))}</code>")

    provider_model = debug.get("provider_model")
    if provider_model:
        lines.append(f"Ответ provider.model: <code>{escape(str(provider_model))}</code>")
    else:
        lines.append("Ответ provider.model: <i>провайдер не вернул это поле</i>")

    try:
        await status_msg.answer("\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.exception("Не удалось отправить debug info")


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
#
# Каждая call_* функция возвращает (content, debug), где debug — словарь
# с ключами "url", "sent_model", "provider_model" для диагностики.

async def call_nvidia(model_id: str, messages: list) -> tuple[str, dict]:
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

    logger.info("call_nvidia -> url=%s model=%s", NVIDIA_CHAT_URL, model_id)

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

            logger.info("call_nvidia <- responded model=%s", data.get("model"))

            debug = {
                "url": NVIDIA_CHAT_URL,
                "sent_model": model_id,
                "provider_model": data.get("model"),
            }

            try:
                return data["choices"][0]["message"]["content"], debug
            except Exception:
                raise Exception(f"Nеверный формат ответа NVIDIA: {json.dumps(data, ensure_ascii=False)[:500]}")


async def call_freemodel_openai(raw_model: str, messages: list, base_url: str) -> tuple[str, dict]:
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

    url = f"{base_url.rstrip('/')}/v1/chat/completions"

    logger.info("call_freemodel_openai -> url=%s model=%s", url, raw_model)

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

            logger.info("call_freemodel_openai <- responded model=%s", data.get("model"))

            debug = {
                "url": url,
                "sent_model": raw_model,
                "provider_model": data.get("model"),
            }

            try:
                return data["choices"][0]["message"]["content"], debug
            except Exception:
                raise Exception(f"Неверный формат ответа FreeModel: {json.dumps(data, ensure_ascii=False)[:500]}")


async def call_gemini(model_id: str, messages: list) -> tuple[str, dict]:
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

    logger.info("call_gemini -> url=%s model=%s", url, raw_model)

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

            logger.info("call_gemini <- responded model=%s", data.get("model"))

            debug = {
                "url": url,
                "sent_model": raw_model,
                "provider_model": data.get("model"),
            }

            try:
                return data["choices"][0]["message"]["content"], debug
            except Exception:
                raise Exception(f"Неверный формат ответа Gemini: {json.dumps(data, ensure_ascii=False)[:500]}")


async def call_ai(model_id: str, messages: list) -> tuple[str, dict]:
    logger.info("call_ai: selected_model=%s", model_id)

    if model_id.startswith("freemodel/"):
        raw_model = strip_provider_prefix(model_id)
        base_url = freemodel_base_for(raw_model)
        content, debug = await call_freemodel_openai(raw_model, messages, base_url)
    elif model_id.startswith("gemini/"):
        content, debug = await call_gemini(model_id, messages)
    else:
        content, debug = await call_nvidia(model_id, messages)

    debug["requested_model"] = model_id
    return content, debug


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

    logger.info("set_model: пользователь выбрал model_id=%s", model_id)

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

        reply, debug = await call_ai(model_id, messages)

        history.append({
            "role": "assistant",
            "content": reply,
        })

        await state.update_data(chat_history=trim_history(history))
        await send_ai_reply(status_msg, reply)
        await send_debug_info(status_msg, debug)

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

        reply, debug = await call_ai(model_id, messages)

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
        await send_debug_info(status_msg, debug)

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

        reply, debug = await call_ai(model_id, messages)

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
        await send_debug_info(status_msg, debug)

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
