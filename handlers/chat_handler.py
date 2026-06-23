import asyncio
import os
import json
import base64
import logging
import re
from io import BytesIO
from html import escape
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from keyboards import (
    cancel_keyboard,
    model_group_keyboard,
    models_keyboard,
    CHATGPT_MODELS,
    GEMINI_MODELS,
    OTHER_MODELS,
    COUNCIL_MODELS,
    GROUP_TITLES,
    MODELS,
)
from states import BotStates

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
router = Router()

SYSTEM_PROMPT = (
    "Ты полезный ИИ-ассистент. "
    "Отвечай на русском языке если вопрос на русском. "
    "Будь точным и лаконичным.\n\n"
    "ВАЖНО ПРО ФАЙЛЫ:\n"
    "- Ты работаешь в Telegram-боте, который УМЕЕТ отправлять файлы пользователю. "
    "Никогда не пиши, что ты не можешь создать/отправить файл.\n"
    "- Если пользователь просит сделать файл / скрипт / код / таблицу / документ, "
    "прислать что-то файлом / в txt / скачать — "
    "ВЫДАЙ ТОЛЬКО ЧИСТОЕ СОДЕРЖИМОЕ ФАЙЛА. "
    "Строго запрещено: вступления 'Вот ваш файл', 'Конечно', пояснения до/после кода, "
    "вопросы 'нужно ли что-то еще?', 'готово', подписи, комментарии вне кода. "
    "ТОЛЬКО сам контент файла, 1-в-1 готовый для сохранения.\n"
    "- Не оборачивай ответ в тройные backticks ```, если тебя явно об этом не просили.\n"
    "- Если это код — только код. Если текст — только текст."
)

MAX_HISTORY = 20
MAX_IMAGE_BYTES = 15 * 1024 * 1024

# --- Файлы ---
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILE_CHARS = 120_000
TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".go", ".rs", ".php", ".rb", ".swift", ".kt", ".sh", ".bat", ".ps1",
    ".sql", ".ini", ".cfg", ".conf", ".toml", ".env", ".css", ".scss", ".less",
    ".vue", ".svelte", ".r", ".pl", ".lua"
}
FILE_SEND_KEYWORDS = [
    "файл", "txt", ".txt", "скачать", "сохрани", "скинь",
    "отправь файлом", "пришли файлом", "в файл", "сохрани в файл",
    "сделай файл", "дай файл", "скачать файл", "дай txt", "в txt",
    "send as file", "as a file", "в виде файла", "файлом"
]
# Короткая команда "перешли последний ответ файлом"
FILE_RESEND_COMMANDS = [
    "файлом", "в файл", "txt", "в txt", "файл", 
    "отправь файлом", "пришли файлом", "дай файл", "скачать", "сохрани"
]

# Режим ответа при запросе файлом: "file_only" или "both"
FILE_RESPONSE_MODE = os.getenv("FILE_RESPONSE_MODE", "file_only")

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY") or os.getenv("API_KEY", "")
# Базовый адрес для большинства моделей freemodel/* (OpenAI-совместимый формат,
# например freemodel/gpt-*). Эндпоинт: {base}/v1/chat/completions
FREEMODEL_API_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")
# Базовый адрес для моделей freemodel/claude-* — OpenRouter.
# Эндпоинт: {base}/v1/chat/completions
FREEMODEL_CLAUDE_BASE = os.getenv("FREEMODEL_CLAUDE_BASE", "https://openrouter.ai/api")
# Отдельный ключ для OpenRouter. Если не задан — используется FREEMODEL_API_KEY.
FREEMODEL_CLAUDE_API_KEY = os.getenv("FREEMODEL_CLAUDE_API_KEY", "")

# Ashibalt API: OpenAI-совместимый эндпоинт.
# Базовый URL уже включает /v1. Эндпоинт: {base}/chat/completions
ASHIBALT_API_KEY = os.getenv("ASHIBALT_API_KEY") or FREEMODEL_API_KEY
ASHIBALT_API_BASE = os.getenv("ASHIBALT_API_BASE", "https://api.ashibalt.ru/v1")

# Ashibalt принимает точные id моделей из своего каталога.
# Дополнительно оставляем старые id как алиасы, чтобы выбранные ранее модели
# в FSM-состоянии пользователя не сломались после обновления клавиатуры.
ASHIBALT_MODEL_ALIASES = {
    "kimi-k2.6": ["kimi-k2.6"],
    "kimi-k2.7-code": ["kimi-k2.7-code", "kimi-2.7-code"],
    "kimi-2.7-code": ["kimi-k2.7-code", "kimi-2.7-code"],
    "kimi-k2.7": ["kimi-k2.7-code", "kimi-k2.7", "kimi-2.7"],
    "kimi-2.7": ["kimi-k2.7-code", "kimi-k2.7", "kimi-2.7"],
    "claude-haiku-4.5": ["claude-haiku-4.5", "claude-haiku-4-5"],
    "claude-haiku-4-5": ["claude-haiku-4.5", "claude-haiku-4-5"],
    "claude-opus-4.6": ["claude-opus-4.6", "opus-4.6", "claude-opus-4-6"],
    "opus-4.6": ["claude-opus-4.6", "opus-4.6", "claude-opus-4-6"],
    "claude-opus-4-6": ["claude-opus-4.6", "opus-4.6", "claude-opus-4-6"],
    "claude-opus-4.7": ["claude-opus-4.7", "opus-4.7", "claude-opus-4-7"],
    "opus-4.7": ["claude-opus-4.7", "opus-4.7", "claude-opus-4-7"],
    "claude-opus-4-7": ["claude-opus-4.7", "opus-4.7", "claude-opus-4-7"],
    "claude-opus-4.8": ["claude-opus-4.8", "opus-4.8", "claude-opus-4-8"],
    "opus-4.8": ["claude-opus-4.8", "opus-4.8", "claude-opus-4-8"],
    "claude-opus-4-8": ["claude-opus-4.8", "opus-4.8", "claude-opus-4-8"],
    "claude-sonnet-4.5": ["claude-sonnet-4.5", "claude-sonnet-4-5"],
    "claude-sonnet-4-5": ["claude-sonnet-4.5", "claude-sonnet-4-5"],
    "claude-sonnet-4.6": ["claude-sonnet-4.6", "sonnet-4.6", "claude-sonnet-4-6"],
    "sonnet-4.6": ["claude-sonnet-4.6", "sonnet-4.6", "claude-sonnet-4-6"],
    "claude-sonnet-4-6": ["claude-sonnet-4.6", "sonnet-4.6", "claude-sonnet-4-6"],
}

# Маппинг из внутренних ID бота -> точные имена моделей на OpenRouter
FREEMODEL_CLAUDE_MODEL_MAP = {
    "claude-sonnet-4-6":          "anthropic/claude-sonnet-4-6",
    "claude-opus-4-6":            "anthropic/claude-opus-4-6",
    "claude-opus-4-7":            "anthropic/claude-opus-4-7",
    "claude-opus-4-8":            "anthropic/claude-opus-4-8",
    "claude-haiku-4-5":           "anthropic/claude-haiku-4-5",
    "claude-haiku-4-5-20251001":  "anthropic/claude-haiku-4-5-20251001",
    "claude-sonnet-4-5":          "anthropic/claude-sonnet-4-5",
    "claude-sonnet-4-5-20250929": "anthropic/claude-sonnet-4-5-20250929",
}

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
logger.info("FREEMODEL_CLAUDE_BASE (для freemodel/claude-*, OpenRouter) = %s", FREEMODEL_CLAUDE_BASE)
logger.info("FREEMODEL_CLAUDE_API_KEY задан = %s", bool(FREEMODEL_CLAUDE_API_KEY))
logger.info("GEMINI_API_BASE = %s", GEMINI_API_BASE)
logger.info("ASHIBALT_API_BASE = %s", ASHIBALT_API_BASE)
logger.info("ASHIBALT_API_KEY задан = %s", bool(ASHIBALT_API_KEY))
logger.info("DEBUG_MODE = %s", DEBUG_MODE)
logger.info("FILE_RESPONSE_MODE = %s", FILE_RESPONSE_MODE)

if not FREEMODEL_API_KEY:
    logger.warning(
        "FREEMODEL_API_KEY не задан. Запросы к freemodel/gpt-* будут падать с ошибкой."
    )
if not FREEMODEL_CLAUDE_API_KEY and not FREEMODEL_API_KEY:
    logger.warning(
        "Ни FREEMODEL_CLAUDE_API_KEY, ни FREEMODEL_API_KEY не заданы. "
        "Запросы к Claude через OpenRouter будут падать с ошибкой."
    )
if not ASHIBALT_API_KEY:
    logger.warning(
        "ASHIBALT_API_KEY не задан. Запросы к Ashibalt будут падать с ошибкой."
    )

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

# --- Работа с файлами для отправки ---

def strip_code_fences(text: str) -> str:
    """Вытаскивает чистое содержимое из markdown-блоков."""
    text = text.strip()
    # если весь ответ - один блок ```lang ... ```
    m = re.match(r"^```[a-zA-Z0-9_+-]*\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    # иначе ищем первый большой ``` блок внутри текста
    m = re.search(r"```[a-zA-Z0-9_+-]*\s*\n(.*?)\n```", text, re.DOTALL)
    if m and len(m.group(1).strip()) > 30:
        return m.group(1).strip()
    return text

def strip_ai_fluff(text: str) -> str:
    """Убирает типичную болтовню ИИ до/после полезного контента."""
    t = text.strip()
    # убрать преамбулы
    t = re.sub(r'^(вот (ваш|готовый)?\s*(файл|код|скрипт|текст|ответ)[:\-]?\s*\n*)', '', t, flags=re.I)
    t = re.sub(r'^(конечно[,.! ]*\n*)', '', t, flags=re.I)
    t = re.sub(r'^(готово[.! ]*\n*)', '', t, flags=re.I)
    # убрать пост-болтовню в конце
    t = re.sub(r'\n+\s*(если (нужно|требуется|хотите).{0,120})$', '', t, flags=re.I | re.S)
    t = re.sub(r'\n+\s*(нужно ли (что|ещё|еще).{0,120})$', '', t, flags=re.I | re.S)
    t = re.sub(r'\n+\s*(готово[.! ].{0,80})$', '', t, flags=re.I | re.S)
    return t.strip()

def clean_file_content(text: str) -> str:
    return strip_ai_fluff(strip_code_fences(text))

def guess_filename_from_prompt(user_prompt: str, ai_reply: str) -> str:
    """Пытается вытащить имя файла из запроса пользователя, иначе угадывает по содержимому."""
    # 1. явное имя в промпте: script.py, report.md и т.п.
    m = re.search(r'([a-zA-Z0-9_.-]+\.(?:py|js|ts|json|csv|md|txt|html|css|java|c|cpp|go|rs|php|rb|sh|yaml|yml|sql|xml))\b', user_prompt, re.I)
    if m:
        return m.group(1)

    # 2. язык в markdown-блоке ответа
    m = re.search(r'```([a-zA-Z0-9_+-]+)', ai_reply.strip())
    if m:
        lang = m.group(1).lower()
        ext_map = {
            "python": "py", "py": "py",
            "javascript": "js", "js": "js",
            "typescript": "ts", "ts": "ts",
            "json": "json", "html": "html",
            "css": "css", "java": "java",
            "c": "c", "cpp": "cpp", "c++": "cpp",
            "go": "go", "rust": "rs", "rs": "rs",
            "php": "php", "ruby": "rb", "rb": "rb",
            "bash": "sh", "sh": "sh", "shell": "sh",
            "sql": "sql", "yaml": "yml", "yml": "yml",
            "xml": "xml", "markdown": "md", "md": "md",
        }
        ext = ext_map.get(lang, "txt")
        return f"ответ.{ext}"

    return "ответ.txt"

async def send_text_as_file(target_message: Message, text: str, filename: str = "ответ.txt"):
    """Отправляет текст как файл, очищая markdown-ограждения и болтовню ИИ."""
    if not text:
        text = "(пусто)"
    clean = clean_file_content(text)
    file = BufferedInputFile(clean.encode("utf-8"), filename=filename)
    await target_message.answer_document(file, caption=filename)

async def send_debug_info(status_msg: Message, debug: dict):
    """
    Отправляет отдельным сообщением диагностику: какая модель была
    запрошена, на какой URL ушёл запрос, и что провайдер вернул
    в поле "model" своего ответа.
    Для "Совета ИИ-моделей" дополнительно показывается статус каждой
    модели-участника (A/B/C) и параметры запроса к модели-судье.
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
    council_members = debug.get("council_members")
    if council_members:
        lines.append("")
        lines.append("<b>Совет — участники:</b>")
        for member in council_members:
            label = member.get("label", "?")
            model = member.get("model", "?")
            member_debug = member.get("debug") or {}
            if "error" in member_debug:
                status = f"ошибка: {member_debug['error']}"
            else:
                status = (
                    f"url={member_debug.get('url', '?')}, "
                    f"provider.model={member_debug.get('provider_model', '?')}"
                )
            lines.append(f"{label}: <code>{escape(str(model))}</code> — {escape(str(status))}")
        judge_debug = debug.get("judge_debug") or {}
        lines.append("")
        lines.append("<b>Совет — судья:</b>")
        lines.append(f"Endpoint: <code>{escape(str(judge_debug.get('url', '?')))}</code>")
        lines.append(f"Отправлено в payload.model: <code>{escape(str(judge_debug.get('sent_model', '?')))}</code>")
    try:
        await status_msg.answer("\n".join(lines), parse_mode="HTML")
    except Exception:
        logger.exception("Не удалось отправить debug info")

async def telegram_file_to_bytes(message: Message, file_id: str) -> bytes:
    """Скачивает любой файл из Telegram в память."""
    tg_file = await message.bot.get_file(file_id)
    if not tg_file.file_path:
        raise Exception("Telegram не вернул путь к файлу.")
    buffer = BytesIO()
    await message.bot.download_file(tg_file.file_path, destination=buffer)
    return buffer.getvalue()

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

# ------------------ Парсинг файлов ------------------

def extract_text_from_bytes(raw: bytes, filename: str, mime_type: str | None) -> str:
    """Извлекает текст из загруженного файла. Поддерживает txt/code, pdf, docx."""
    name_lower = (filename or "").lower()
    mime = (mime_type or "").lower()

    # PDF
    if name_lower.endswith(".pdf") or mime == "application/pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise Exception("Для чтения PDF установите: pip install pypdf")
        reader = PdfReader(BytesIO(raw))
        texts = []
        for page in reader.pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                continue
        text = "\n".join(texts).strip()
        if not text:
            raise Exception("PDF пустой или текст не удалось извлечь.")
        return text

    # DOCX
    if name_lower.endswith(".docx") or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            import docx  # python-docx
        except ImportError:
            raise Exception("Для чтения DOCX установите: pip install python-docx")
        doc = docx.Document(BytesIO(raw))
        text = "\n".join(p.text for p in doc.paragraphs)
        return text.strip()

    # Текстовые форматы
    is_text_ext = any(name_lower.endswith(ext) for ext in TEXT_EXTENSIONS)
    is_text_mime = mime.startswith("text/") or mime in (
        "application/json", "application/xml", "application/javascript",
        "application/x-yaml", "application/yaml"
    )

    if is_text_ext or is_text_mime or mime == "":
        # пробуем несколько кодировок
        for enc in ("utf-8", "cp1251", "latin1"):
            try:
                return raw.decode(enc)
            except Exception:
                continue
        # последний шанс
        return raw.decode("utf-8", errors="replace")

    raise Exception(
        f"Неподдерживаемый формат файла: {filename or mime_type or 'unknown'}. "
        f"Поддерживаются: TXT, MD, JSON, CSV, код (.py/.js/…), PDF, DOCX, изображения."
    )

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
                raise Exception(f"Неверный формат ответа NVIDIA: {json.dumps(data, ensure_ascii=False)[:500]}")

async def call_freemodel_openai(
    raw_model: str,
    messages: list,
    base_url: str,
    api_key: str | None = None,
    extra_headers: dict | None = None,
) -> tuple[str, dict]:
    """
    Вызов через OpenAI-совместимый эндпоинт /v1/chat/completions.
    Используется для всех freemodel/* моделей, включая Claude — через OpenRouter.
    Параметр api_key позволяет передать отдельный ключ для конкретного роутера.
    extra_headers — дополнительные заголовки (например, для OpenRouter).
    """
    key = api_key or FREEMODEL_API_KEY
    if not key:
        raise Exception("API ключ не задан (FREEMODEL_API_KEY или FREEMODEL_CLAUDE_API_KEY).")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; freemodel-bot/1.0)",
    }
    if extra_headers:
        headers.update(extra_headers)
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
                raise Exception(
                    f"FreeModel вернул неожиданный ответ (HTTP {resp.status}, url={url}): {text[:500]}"
                )
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

async def call_ashibalt(model_id: str, messages: list) -> tuple[str, dict]:
    if not ASHIBALT_API_KEY:
        raise Exception("ASHIBALT_API_KEY не задан.")
    requested_raw_model = model_id.replace("ashibalt/", "", 1)
    candidate_models = ASHIBALT_MODEL_ALIASES.get(requested_raw_model, [requested_raw_model])
    headers = {
        "Authorization": f"Bearer {ASHIBALT_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{ASHIBALT_API_BASE.rstrip('/')}/chat/completions"
    async def request_model(raw_model: str) -> tuple[str, dict]:
        payload = {
            "model": raw_model,
            "messages": messages,
            "max_tokens": 2048,
        }
        logger.info("call_ashibalt -> url=%s model=%s", url, raw_model)
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
                    raise Exception(
                        f"Ashibalt вернул неожиданный ответ (HTTP {resp.status}, url={url}): {text[:500]}"
                    )
                if resp.status != 200:
                    raise Exception(extract_api_error(data))
                logger.info("call_ashibalt <- responded model=%s", data.get("model"))
                debug = {
                    "url": url,
                    "sent_model": raw_model,
                    "provider_model": data.get("model"),
                }
                try:
                    return data["choices"][0]["message"]["content"], debug
                except Exception:
                    raise Exception(f"Неверный формат ответа Ashibalt: {json.dumps(data, ensure_ascii=False)[:500]}")
    last_error = None
    for index, raw_model in enumerate(candidate_models):
        try:
            return await request_model(raw_model)
        except Exception as e:
            last_error = e
            error_text = str(e)
            # Если Ashibalt не нашёл id модели, пробуем следующий алиас.
            # Остальные ошибки (ключ, баланс, формат запроса и т.п.) не маскируем.
            lower_error = error_text.lower()
            model_not_found = (
                "not available" in lower_error
                or "not found" in lower_error
                or "unknown model" in lower_error
            )
            if model_not_found and index < len(candidate_models) - 1:
                logger.warning(
                    "call_ashibalt: model=%s недоступна, пробую алиас %s",
                    raw_model,
                    candidate_models[index + 1],
                )
                continue
            raise
    raise Exception(
        f"Ashibalt не принял model id. Пробовал: {', '.join(candidate_models)}. "
        f"Последняя ошибка: {last_error}"
    )

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

# ------------------ Совет ИИ-моделей ------------------
#
# "Совет ИИ-моделей" — виртуальная модель (model_id = COUNCIL_MODEL_ID).
# Никакого отдельного провайдера для неё нет: запрос параллельно уходит
# трём моделям-участникам (COUNCIL_MEMBER_MODELS) через уже существующий
# call_ai(), их ответы анонимизируются как A/B/C и передаются
# модели-судье (COUNCIL_JUDGE_MODEL), которая выбирает лучший ответ или
# делает синтез и кратко объясняет выбор.
#
# call_nvidia / call_freemodel_openai / call_gemini при этом не меняются —
# совет лишь несколько раз переиспользует их через call_ai().

COUNCIL_MODEL_ID = list(COUNCIL_MODELS.keys())[0]

# Модели-участники совета (отвечают параллельно, анонимно как A, B, C)
COUNCIL_MEMBER_MODELS = [
    "meta/llama-4-maverick-17b-128e-instruct",   # A — Llama 4 Maverick
    "z-ai/glm-5.1",                              # B — GLM-5.1
    "nvidia/nemotron-3-super-120b-a12b",         # C — Nemotron 3 Super 120B-A12B
]

COUNCIL_LABELS = ["A", "B", "C"]

# Модель-судья
COUNCIL_JUDGE_MODEL = "freemodel/gpt-5.5"

COUNCIL_JUDGE_SYSTEM_PROMPT = (
    "Ты выступаешь в роли судьи в \"Совете ИИ-моделей\". Пользователь задал "
    "вопрос, и несколько разных ИИ-моделей дали на него ответы. Их ответы "
    "анонимно обозначены буквами (A, B, C...) — названия моделей тебе не "
    "сообщаются, не пытайся их угадывать и не упоминай в ответе.\n\n"
    "Твоя задача:\n"
    "1. Внимательно изучи исходный вопрос пользователя (и контекст переписки, "
    "если он есть) и все варианты ответов.\n"
    "2. Выбери наиболее точный, полезный и полный вариант, либо составь синтез "
    "лучших частей нескольких вариантов.\n"
    "3. Дай пользователю единый финальный ответ на исходный вопрос.\n"
    "4. После финального ответа кратко (1-3 предложения) поясни свой выбор: "
    "что было сильнее или слабее в разных вариантах (точность, полнота, "
    "структура, ошибки и т.п.), без упоминания букв-обозначений или названий "
    "моделей.\n\n"
    "Отвечай на том языке, на котором задан исходный вопрос пользователя."
    "\n\nВАЖНО ПРО ФАЙЛЫ: ты работаешь в Telegram-боте, который УМЕЕТ отправлять файлы. "
    "Никогда не пиши что не можешь создать файл. "
    "Если просят файл — выдай ТОЛЬКО содержимое файла, без вступлений и вопросов."
)

async def call_council_member(model_id: str, messages: list) -> tuple[str, dict]:
    """
    Вызывает одну модель-участника совета. Ошибки не прерывают весь
    процесс — вместо ответа подставляется текст с пояснением ошибки,
    чтобы судья мог продолжить работу с оставшимися вариантами.
    """
    try:
        return await call_ai(model_id, messages)
    except Exception as e:
        logger.warning("call_council_member: модель %s не ответила: %s", model_id, e)
        return (
            f"[Эта модель не смогла ответить: {e}]",
            {"requested_model": model_id, "error": str(e)},
        )

def build_council_judge_messages(original_messages: list, labeled_answers: list) -> list:
    """
    Собирает список сообщений для модели-судьи:
    - системный промпт судьи (вместо обычного SYSTEM_PROMPT);
    - исходная переписка пользователя (без системного сообщения бота);
    - анонимизированные варианты ответов A/B/C с просьбой выбрать лучший
      или сделать синтез.
    """
    judge_messages = [
        {
            "role": "system",
            "content": COUNCIL_JUDGE_SYSTEM_PROMPT,
        }
    ]
    for msg in original_messages:
        if msg.get("role") == "system":
            continue
        judge_messages.append(msg)
    answers_text = "\n\n".join(
        f"Вариант ответа {label}:\n{content}"
        for label, content in labeled_answers
    )
    judge_messages.append({
        "role": "user",
        "content": (
            "Вот варианты ответов разных ИИ-моделей на вопрос выше "
            "(анонимно, без названий моделей):\n\n"
            f"{answers_text}\n\n"
            "Выбери лучший вариант или составь синтез лучших частей, дай "
            "финальный ответ пользователю и кратко поясни свой выбор."
        ),
    })
    return judge_messages

async def call_ai_council(messages: list) -> tuple[str, dict]:
    """
    Реализация "Совета ИИ-моделей":
    1. Параллельно опрашивает COUNCIL_MEMBER_MODELS.
    2. Анонимизирует их ответы как A, B, C.
    3. Передаёт их вместе с исходным вопросом модели COUNCIL_JUDGE_MODEL.
    4. Возвращает финальный ответ судьи + диагностику по всем вызовам.
    """
    tasks = [call_council_member(model_id, messages) for model_id in COUNCIL_MEMBER_MODELS]
    results = await asyncio.gather(*tasks)
    labeled_answers = []
    members_debug = []
    for label, model_id, (content, member_debug) in zip(COUNCIL_LABELS, COUNCIL_MEMBER_MODELS, results):
        labeled_answers.append((label, content))
        members_debug.append({
            "label": label,
            "model": model_id,
            "debug": member_debug,
        })
    judge_messages = build_council_judge_messages(messages, labeled_answers)
    logger.info("call_ai_council: отправляю ответы A/B/C судье %s", COUNCIL_JUDGE_MODEL)
    final_content, judge_debug = await call_ai(COUNCIL_JUDGE_MODEL, judge_messages)
    debug = {
        "url": "Совет ИИ-моделей (3 модели + судья)",
        "sent_model": judge_debug.get("sent_model"),
        "provider_model": judge_debug.get("provider_model"),
        "council_members": members_debug,
        "judge_debug": judge_debug,
    }
    return final_content, debug

async def call_ai(model_id: str, messages: list) -> tuple[str, dict]:
    logger.info("call_ai: selected_model=%s", model_id)
    if model_id == COUNCIL_MODEL_ID:
        content, debug = await call_ai_council(messages)
    elif model_id.startswith("freemodel/"):
        raw_model = strip_provider_prefix(model_id)
        if raw_model.startswith("claude-"):
            # Claude идёт через OpenRouter (/api/v1/chat/completions)
            # с подменой имени модели через FREEMODEL_CLAUDE_MODEL_MAP
            # и отдельным ключом FREEMODEL_CLAUDE_API_KEY (если задан)
            mapped_model = FREEMODEL_CLAUDE_MODEL_MAP.get(raw_model, f"anthropic/{raw_model}")
            claude_key = FREEMODEL_CLAUDE_API_KEY or FREEMODEL_API_KEY
            # OpenRouter рекомендует передавать HTTP-Referer и X-Title
            openrouter_headers = {
                "HTTP-Referer": "https://t.me/",
                "X-Title": "Telegram AI Bot",
            }
            logger.info(
                "call_ai: claude маппинг %s -> %s, base=%s",
                raw_model, mapped_model, FREEMODEL_CLAUDE_BASE,
            )
            content, debug = await call_freemodel_openai(
                mapped_model,
                messages,
                FREEMODEL_CLAUDE_BASE,
                api_key=claude_key,
                extra_headers=openrouter_headers,
            )
        else:
            content, debug = await call_freemodel_openai(raw_model, messages, FREEMODEL_API_BASE)
    elif model_id.startswith("ashibalt/"):
        content, debug = await call_ashibalt(model_id, messages)
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
    # "Совет ИИ-моделей" — это единственный режим в своей группе, поэтому
    # выбираем его сразу одним кликом, без промежуточного подменю.
    if group == "council":
        model_id = list(COUNCIL_MODELS.keys())[0]
        await activate_model(callback, state, model_id)
        return
    data = await state.get_data()
    current = data.get("selected_model", "")
    title = GROUP_TITLES.get(group, group.capitalize())
    await callback.message.edit_text(
        f"<b>Модели группы {escape(title)}</b>",
        reply_markup=models_keyboard(group, current),
        parse_mode="HTML",
    )
    await callback.answer()

async def activate_model(callback: CallbackQuery, state: FSMContext, model_id: str):
    """Сохраняет выбранную модель, переключает в режим чата и показывает
    подтверждение с человекочитаемым названием модели."""
    logger.info("activate_model: пользователь выбрал model_id=%s", model_id)
    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)
    model_name = MODELS.get(model_id, model_id)
    await callback.message.edit_text(
        f"<b>Модель выбрана:</b> {escape(model_name)}\n\nПиши сообщения.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()

@router.callback_query(F.data.startswith("model_"))
async def set_model(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "")
    await activate_model(callback, state, model_id)

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

# ------------------ Очистка истории ------------------

@router.callback_query(F.data == "clear_history")
async def cb_clear_history(callback: CallbackQuery, state: FSMContext):
    await state.update_data(chat_history=[])
    await callback.answer("История очищена ✅", show_alert=True)

# ------------------ Обработчики чата ------------------

@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    text = (message.text or "").strip()
    low = text.lower()

    want_file = any(k in low for k in FILE_SEND_KEYWORDS)
    is_resend_command = low.strip() in FILE_RESEND_COMMANDS
    file_request_only = want_file and is_resend_command

    if file_request_only:
        history = get_history(data)
        last_assistant = None
        for msg in reversed(history):
            if msg.get("role") == "assistant":
                last_assistant = msg.get("content", "")
                break
        if last_assistant:
            filename = guess_filename_from_prompt(text, last_assistant)
            await send_text_as_file(message, last_assistant, filename=filename)
        else:
            await message.answer("В истории пока нет ответа от ИИ.", reply_markup=cancel_keyboard())
        return

    model_id = get_model(data)

    user_content_for_model = text
    if want_file:
        user_content_for_model = (
            text +
            "\n\n[СИСТЕМНО: В ОТВЕТ ВЫДАЙ ТОЛЬКО ЧИСТОЕ СОДЕРЖИМОЕ ФАЙЛА. "
            "Никаких вступлений, пояснений, вопросов. Только контент.]"
        )

    status_msg = await message.answer("<i>Думаю...</i>", parse_mode="HTML")
    try:
        history = list(get_history(data))
        history.append({"role": "user", "content": user_content_for_model})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history)
        reply, debug = await call_ai(model_id, messages)
        history.append({"role": "assistant", "content": reply})
        await state.update_data(chat_history=trim_history(history))

        if want_file:
            filename = guess_filename_from_prompt(text, reply)
            # удаляем "Думаю..."
            try:
                await status_msg.delete()
            except Exception:
                try:
                    await status_msg.edit_text("📄 Отправляю файл...", parse_mode="HTML")
                except Exception:
                    pass
            await send_text_as_file(message, reply, filename=filename)
            # debug отправляем отдельным сообщением, чтобы не путать с ответом ИИ
            if DEBUG_MODE:
                await send_debug_info(message, debug)
        else:
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
async def handle_document(message: Message, state: FSMContext):
    """Приём любых файлов: изображения идут в Vision, текст/PDF/DOCX - парсятся и отправляются в модель."""
    document = message.document
    if not document:
        return

    data = await state.get_data()
    model_id = get_model(data)
    mime_type = document.mime_type or ""
    filename = document.file_name or "file"
    caption = message.caption or ""

    # 1. Если это картинка, отправленная как файл - обрабатываем через Vision
    if mime_type.startswith("image/"):
        status_msg = await message.answer("<i>Обрабатываю изображение...</i>", parse_mode="HTML")
        try:
            history = list(get_history(data))
            image_data_url = await telegram_file_to_data_url(
                message=message,
                file_id=document.file_id,
                mime_type=mime_type,
            )
            user_content = make_vision_content(prompt=caption, image_data_url=image_data_url)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history) + [
                {"role": "user", "content": user_content}
            ]
            reply, debug = await call_ai(model_id, messages)
            history.append({"role": "user", "content": f"[Изображение-файл] {caption}".strip()})
            history.append({"role": "assistant", "content": reply})
            await state.update_data(chat_history=trim_history(history))
            await send_ai_reply(status_msg, reply)
            await send_debug_info(status_msg, debug)
        except Exception as e:
            await edit_error(
                status_msg,
                "Ошибка при обработке изображения. Проверьте, что выбрана модель с поддержкой Vision",
                e,
            )
        return

    # 2. Обычный файл: txt / code / pdf / docx
    if document.file_size and document.file_size > MAX_FILE_BYTES:
        await message.answer(
            f"Файл слишком большой ({document.file_size / 1024 / 1024:.1f} МБ). Максимум {MAX_FILE_BYTES // 1024 // 1024} МБ.",
            reply_markup=cancel_keyboard(),
        )
        return

    status_msg = await message.answer("<i>Читаю файл...</i>", parse_mode="HTML")
    try:
        raw = await telegram_file_to_bytes(message, document.file_id)
        try:
            file_text = extract_text_from_bytes(raw, filename, mime_type)
        except Exception as e:
            await edit_error(status_msg, "Не удалось прочитать файл", e)
            return

        if len(file_text) > MAX_FILE_CHARS:
            file_text = file_text[:MAX_FILE_CHARS] + f"\n\n[...обрезано, всего символов в файле было больше {MAX_FILE_CHARS}]"

        history = list(get_history(data))
        user_prompt = caption.strip()
        if user_prompt:
            full_prompt = f"{user_prompt}\n\n--- Содержимое файла {filename} ---\n{file_text}"
        else:
            full_prompt = f"Проанализируй содержимое файла {filename}:\n\n{file_text}"

        history.append({"role": "user", "content": full_prompt})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history)

        await status_msg.edit_text("<i>Думаю...</i>", parse_mode="HTML")
        reply, debug = await call_ai(model_id, messages)

        history.append({"role": "assistant", "content": reply})
        await state.update_data(chat_history=trim_history(history))
        await send_ai_reply(status_msg, reply)
        await send_debug_info(status_msg, debug)

    except Exception as e:
        await edit_error(status_msg, "Ошибка при обработке файла", e)

@router.message(BotStates.chat_mode)
async def handle_unsupported_message(message: Message):
    await message.answer(
        "Я могу обработать текст, фото, изображение-файл, а также документы: "
        "TXT, MD, JSON, CSV, код, PDF, DOCX.\n"
        "Просто пришлите файл с подписью-заданием, если нужно.",
        reply_markup=cancel_keyboard(),
    )
