import asyncio
import os
import json
import base64
import logging
import re
import time
import zipfile
from collections import OrderedDict, deque
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
    GEMINI_MODELS,
    OTHER_MODELS,
    GROUP_TITLES,
    MODELS,
)

from states import BotStates
import database as db

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
    "- Если пользователь просит сделать файл, сгенерировать код, таблицу, документ и т.п. - "
    "просто выдай ПОЛНОЕ содержимое файла, без лишних комментариев до и после. "
    "Если нужно пояснение — кратко после содержимого.\n"
    "- Не оборачивай весь ответ в тройные кавычки, если тебя об этом явно не просили. "
    "Выдавай чистый контент, готовый для сохранения."
)

MAX_HISTORY = 20
MAX_HISTORY_ITEM_CHARS = 40_000
MAX_HISTORY_TOTAL_CHARS = 120_000
MAX_IMAGE_BYTES = 15 * 1024 * 1024

# --- Файлы ---
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILE_CHARS = 120_000
MAX_DOCX_EXPANDED_BYTES = 25 * 1024 * 1024
MAX_DOCX_ENTRIES = 2_000
MAX_PDF_PAGES = 100
FILE_PARSE_TIMEOUT_SECONDS = 15
PROVIDER_RESPONSE_LIMIT = 2 * 1024 * 1024
BOT_AI_TIMEOUT_SECONDS = max(15, min(int(os.getenv("BOT_AI_TIMEOUT_SECONDS", "120")), 300))
BOT_AI_CONCURRENCY = max(1, min(int(os.getenv("BOT_AI_CONCURRENCY", "4")), 16))
DEFAULT_DAILY_AI_LIMIT = max(
    1, min(int(os.getenv("DEFAULT_DAILY_AI_LIMIT", "200")), 10000)
)
_bot_ai_semaphore = asyncio.Semaphore(BOT_AI_CONCURRENCY)

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

FILE_RESEND_COMMANDS = [
    "файлом", "в файл", "txt", "в txt", "файл",
    "отправь файлом", "пришли файлом", "дай файл", "скачать", "сохрани"
]

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

DEBUG_MODE = os.getenv("DEBUG_MODE", "0") not in ("0", "false", "False", "")

logger.info("NVIDIA_CHAT_URL = %s", NVIDIA_CHAT_URL)
logger.info("DEBUG_MODE = %s", DEBUG_MODE)

if not GEMINI_API_KEY:
    logger.warning("GEMINI_API_KEY не задан. Запросы к Gemini будут падать с ошибкой.")
if not NVIDIA_API_KEY:
    logger.warning("NVIDIA_API_KEY не задан. Запросы к Nvidia будут падать с ошибкой.")


def get_history(data):
    return data.get("chat_history", [])

def get_model(data):
    selected = data.get("selected_model")
    return selected if selected in MODELS else list(GEMINI_MODELS.keys())[0]

def trim_history(history: list) -> list:
    result = []
    total = 0
    for item in reversed(history[-MAX_HISTORY:]):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        remaining = MAX_HISTORY_TOTAL_CHARS - total
        if remaining <= 0:
            break
        clipped = content[: min(MAX_HISTORY_ITEM_CHARS, remaining)]
        result.append({"role": role, "content": clipped})
        total += len(clipped)
    result.reverse()
    return result


class _BotRateLimiter:
    def __init__(self, max_buckets: int = 10_000):
        self.max_buckets = max_buckets
        self.buckets: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str, limit: int = 20, window: int = 60) -> bool:
        now = time.monotonic()
        bucket = self.buckets.get(key)
        if bucket is None:
            for stale_key in list(self.buckets.keys()):
                stale = self.buckets[stale_key]
                while stale and now - stale[0] >= window:
                    stale.popleft()
                if not stale:
                    self.buckets.pop(stale_key, None)
            if len(self.buckets) >= self.max_buckets:
                return False
            bucket = deque()
            self.buckets[key] = bucket
        else:
            self.buckets.move_to_end(key)
        while bucket and now - bucket[0] >= window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


_bot_rate_limiter = _BotRateLimiter()


async def reserve_bot_ai_request(message: Message, model_id: str) -> bool:
    user_id = message.from_user.id
    if not _bot_rate_limiter.allow(f"chat:{user_id}", 20, 60):
        await message.answer("Слишком много запросов. Подожди минуту.")
        return False
    try:
        reserved = await db.reserve_request(
            user_id,
            model_id,
            source="bot",
            default_daily_limit=DEFAULT_DAILY_AI_LIMIT,
        )
    except Exception:
        logger.exception("Не удалось проверить серверную квоту")
        await message.answer("Проверка лимита временно недоступна. Попробуй позже.")
        return False
    if not reserved:
        await message.answer("Дневной лимит для выбранной модели исчерпан.")
        return False
    return True

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

async def edit_error(status_msg: Message, title: str, error: Exception):
    logger.exception(title)
    await status_msg.edit_text(
        f"<b>{escape(title)}:</b> {escape(str(error))}",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )

async def send_ai_reply(status_msg: Message, reply: str):
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

# --- Работа с файлами ---
def strip_code_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```[a-zA-Z0-9_+-]*\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    if m:
        return m.group(1)
    return text

def guess_filename_from_prompt(user_prompt: str, ai_reply: str) -> str:
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
        "txt": "txt", "csv": "csv", "toml": "toml",
        "ini": "ini", "env": "env",
    }
    m = re.search(r'([a-zA-Z0-9_.-]+\.(?:py|js|ts|json|csv|md|txt|html|css|java|c|cpp|go|rs|php|rb|sh|yaml|yml|sql|xml|toml|ini|env))\b', user_prompt, re.I)
    if m: return m.group(1)
    m = re.search(r'(?:^|\s|в\s+|как?\s+|файл\s+|\.)(py|js|ts|json|csv|md|txt|html|css|java|cpp|go|rs|php|rb|sh|yaml|yml|sql|xml|toml|ini|env|python|javascript|typescript|markdown|bash|shell|rust|ruby)\b', user_prompt, re.I)
    if m:
        lang = m.group(1).lower()
        return f"ответ.{ext_map.get(lang, lang)}"
    m = re.search(r'^```([a-zA-Z0-9_+-]+)', ai_reply.strip(), re.MULTILINE)
    if m:
        lang = m.group(1).lower()
        return f"ответ.{ext_map.get(lang, 'txt')}"
    return "ответ.txt"

async def send_text_as_file(target_message: Message, text: str, filename: str = "ответ.txt"):
    if not text:
        text = "(пусто)"
    clean = strip_code_fences(text)
    file = BufferedInputFile(clean.encode("utf-8"), filename=filename)
    await target_message.answer_document(file, caption=filename)

async def send_debug_info(status_msg: Message, debug: dict):
    if not DEBUG_MODE or not debug: return
    lines = ["🔧 <b>Debug info</b>"]
    lines.append(f"Выбрана в боте: <code>{escape(str(debug.get('requested_model', '?')))}</code>")
    lines.append(f"Endpoint: <code>{escape(str(debug.get('url', '?')))}</code>")
    lines.append(f"Отправлено в payload.model: <code>{escape(str(debug.get('sent_model', '?')))}</code>")
    lines.append(f"Ответ provider.model: <code>{escape(str(debug.get('provider_model', 'не вернул')))}</code>")
    try:
        await status_msg.answer("\n".join(lines), parse_mode="HTML")
    except Exception:
        pass

async def telegram_file_to_bytes(
    message: Message,
    file_id: str,
    max_bytes: int = MAX_FILE_BYTES,
) -> bytes:
    tg_file = await message.bot.get_file(file_id)
    if not tg_file.file_path:
        raise Exception("Telegram не вернул путь к файлу.")
    buffer = BytesIO()
    await message.bot.download_file(tg_file.file_path, destination=buffer)
    raw = buffer.getvalue()
    if len(raw) > max_bytes:
        raise Exception("Файл превышает допустимый размер.")
    return raw

async def telegram_file_to_data_url(message: Message, file_id: str, mime_type: str | None = None) -> str:
    tg_file = await message.bot.get_file(file_id)
    if not tg_file.file_path:
        raise Exception("Telegram не вернул путь к файлу.")
    buffer = BytesIO()
    await message.bot.download_file(tg_file.file_path, destination=buffer)
    image_bytes = buffer.getvalue()
    if not image_bytes:
        raise Exception("Не удалось скачать изображение.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise Exception("Изображение слишком большое.")
    final_mime_type = mime_type or guess_mime_type(tg_file.file_path)
    if not final_mime_type.startswith("image/"):
        final_mime_type = guess_mime_type(tg_file.file_path)
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{final_mime_type};base64,{encoded}"

def make_vision_content(prompt: str, image_data_url: str) -> list:
    prompt = (prompt or "").strip()
    if not prompt: prompt = "Проанализируй изображение и ответь, что на нём изображено."
    return [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_data_url}}]

def extract_text_from_bytes(raw: bytes, filename: str, mime_type: str | None) -> str:
    name_lower = (filename or "").lower()
    mime = (mime_type or "").lower()

    if name_lower.endswith(".pdf") or mime == "application/pdf":
        try: from pypdf import PdfReader
        except ImportError: raise Exception("Для чтения PDF установите: pip install pypdf")
        reader = PdfReader(BytesIO(raw))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise Exception(f"В PDF больше {MAX_PDF_PAGES} страниц.")
        texts = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            remaining = MAX_FILE_CHARS - total
            if remaining <= 0:
                break
            texts.append(text[:remaining])
            total += min(len(text), remaining)
        return "\n".join(texts).strip()

    if name_lower.endswith(".docx") or mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try: import docx
        except ImportError: raise Exception("Для чтения DOCX установите: pip install python-docx")
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                infos = archive.infolist()
                expanded_size = sum(info.file_size for info in infos)
                if len(infos) > MAX_DOCX_ENTRIES:
                    raise Exception("В DOCX слишком много внутренних файлов.")
                if expanded_size > MAX_DOCX_EXPANDED_BYTES:
                    raise Exception("DOCX слишком велик после распаковки.")
        except zipfile.BadZipFile as exc:
            raise Exception("Некорректный DOCX-файл.") from exc
        doc = docx.Document(BytesIO(raw))
        result = []
        total = 0
        for paragraph in doc.paragraphs:
            text = paragraph.text
            remaining = MAX_FILE_CHARS - total
            if remaining <= 0:
                break
            result.append(text[:remaining])
            total += min(len(text), remaining)
        return "\n".join(result).strip()

    is_text = any(name_lower.endswith(ext) for ext in TEXT_EXTENSIONS) or mime.startswith("text/") or mime in ("application/json", "application/xml")
    if is_text or mime == "":
        for enc in ("utf-8", "cp1251", "latin1"):
            try: return raw.decode(enc)
            except Exception: continue
        return raw.decode("utf-8", errors="replace")

    raise Exception(f"Неподдерживаемый формат: {filename or mime_type}. Поддерживаются: TXT, Code, PDF, DOCX, Изображения.")


# ------------------ API Вызовы ------------------

async def _read_provider_json(resp: aiohttp.ClientResponse, provider: str) -> dict:
    if resp.content_length is not None and resp.content_length > PROVIDER_RESPONSE_LIMIT:
        raise Exception(f"{provider} вернул слишком большой ответ.")
    raw = await resp.content.read(PROVIDER_RESPONSE_LIMIT + 1)
    if len(raw) > PROVIDER_RESPONSE_LIMIT:
        raise Exception(f"{provider} вернул слишком большой ответ.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Exception(f"{provider} вернул некорректный ответ.") from exc
    if not isinstance(data, dict):
        raise Exception(f"{provider} вернул некорректный ответ.")
    return data


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
    logger.info("call_nvidia -> url=%s model=%s", NVIDIA_CHAT_URL, payload["model"])
    async with aiohttp.ClientSession() as session:
        async with session.post(NVIDIA_CHAT_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await _read_provider_json(resp, "NVIDIA")
            if resp.status != 200:
                logger.warning(
                    "NVIDIA API error status=%s",
                    resp.status,
                )
                raise Exception("NVIDIA API временно недоступен.")
            
            debug = {"url": NVIDIA_CHAT_URL, "sent_model": payload["model"], "provider_model": data.get("model")}
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise Exception("NVIDIA вернул некорректный формат ответа.") from exc
            return str(content), debug


async def call_gemini(model_id: str, messages: list) -> tuple[str, dict]:
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY не задан.")
    
    raw_model = model_id.replace("gemini/", "", 1)
    
    # Официальный OpenAI-совместимый эндпоинт от Google
    url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GEMINI_API_KEY}"
    }

    payload = {
        "model": raw_model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2048,
    }

    logger.info("call_gemini -> url=%s model=%s", url, raw_model)

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            data = await _read_provider_json(resp, "Google")
            
            if resp.status != 200:
                logger.warning(
                    "Google API error status=%s",
                    resp.status,
                )
                raise Exception("Google API временно недоступен.")
            
            debug = {
                "url": url,
                "sent_model": raw_model,
                "provider_model": data.get("model", raw_model),
            }
            
            try:
                reply = data["choices"][0]["message"]["content"]
                return str(reply), debug
            except (KeyError, IndexError, TypeError) as exc:
                raise Exception("Google API вернул некорректный формат ответа.") from exc


async def call_ai(model_id: str, messages: list) -> tuple[str, dict]:
    logger.info("call_ai: selected_model=%s", model_id)

    if model_id.startswith("gemini/"):
        content, debug = await call_gemini(model_id, messages)
    else:
        content, debug = await call_nvidia(model_id, messages)

    debug["requested_model"] = model_id
    return content, debug


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
    title = GROUP_TITLES.get(group, group.capitalize())
    await callback.message.edit_text(f"<b>Модели группы {escape(title)}</b>", reply_markup=models_keyboard(group, current), parse_mode="HTML")
    await callback.answer()

async def activate_model(callback: CallbackQuery, state: FSMContext, model_id: str):
    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)
    model_name = MODELS.get(model_id, model_id)
    await callback.message.edit_text(f"<b>Модель выбрана:</b> {escape(model_name)}\n\nПиши сообщения.", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("model_"))
async def set_model(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "")
    if model_id not in MODELS:
        await callback.answer("Недоступная модель", show_alert=True)
        return
    await activate_model(callback, state, model_id)

@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    model_id = data.get("selected_model") or list(GEMINI_MODELS.keys())[0]
    await state.update_data(selected_model=model_id)
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text("<b>Режим чата активирован</b>\n\nПиши свои сообщения.\n/clear — очистить историю", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

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
    file_request_only = want_file and (low.strip() in FILE_RESEND_COMMANDS)

    if file_request_only:
        history = get_history(data)
        last_assistant = next((msg.get("content", "") for msg in reversed(history) if msg.get("role") == "assistant"), None)
        if last_assistant:
            await send_text_as_file(message, last_assistant, filename=guess_filename_from_prompt(text, last_assistant))
        else:
            await message.answer("В истории пока нет ответа от ИИ.", reply_markup=cancel_keyboard())
        return

    model_id = get_model(data)
    if not await reserve_bot_ai_request(message, model_id):
        return
    user_content_for_model = text
    if want_file:
        user_content_for_model += "\n\n[Системное напоминание: бот УМЕЕТ отправлять файлы. Просто выдай полное содержимое файла.]"

    status_msg = await message.answer("<i>Думаю...</i>", parse_mode="HTML")
    try:
        history = list(get_history(data))
        history.append({"role": "user", "content": user_content_for_model})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history)
        
        async with _bot_ai_semaphore:
            reply, debug = await asyncio.wait_for(
                call_ai(model_id, messages),
                timeout=BOT_AI_TIMEOUT_SECONDS,
            )
        
        history.append({"role": "assistant", "content": reply})
        await state.update_data(chat_history=trim_history(history))

        await send_ai_reply(status_msg, reply)
        if want_file:
            await send_text_as_file(status_msg, reply, filename=guess_filename_from_prompt(text, reply))
        await send_debug_info(status_msg, debug)
    except Exception as e:
        await edit_error(status_msg, "Ошибка", e)

@router.message(BotStates.chat_mode, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    if not await reserve_bot_ai_request(message, model_id):
        return
    status_msg = await message.answer("<i>Обрабатываю фото...</i>", parse_mode="HTML")
    try:
        history = list(get_history(data))
        photo = message.photo[-1]
        caption = message.caption or ""
        image_data_url = await telegram_file_to_data_url(message=message, file_id=photo.file_id, mime_type="image/jpeg")
        user_content = make_vision_content(prompt=caption, image_data_url=image_data_url)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history) + [{"role": "user", "content": user_content}]
        
        async with _bot_ai_semaphore:
            reply, debug = await asyncio.wait_for(
                call_ai(model_id, messages),
                timeout=BOT_AI_TIMEOUT_SECONDS,
            )
        
        history.append({"role": "user", "content": f"[Фото] {caption}".strip()})
        history.append({"role": "assistant", "content": reply})
        await state.update_data(chat_history=trim_history(history))

        await send_ai_reply(status_msg, reply)
        await send_debug_info(status_msg, debug)
    except Exception as e:
        await edit_error(status_msg, "Ошибка", e)

@router.message(BotStates.chat_mode, F.document)
async def handle_document(message: Message, state: FSMContext):
    document = message.document
    if not document: return
    data = await state.get_data()
    model_id = get_model(data)
    mime_type = document.mime_type or ""
    filename = document.file_name or "file"
    caption = message.caption or ""
    max_document_bytes = MAX_IMAGE_BYTES if mime_type.startswith("image/") else MAX_FILE_BYTES
    if document.file_size and document.file_size > max_document_bytes:
        await message.answer(
            f"Файл слишком большой. Максимум {max_document_bytes // 1024 // 1024} МБ.",
            reply_markup=cancel_keyboard(),
        )
        return
    if not await reserve_bot_ai_request(message, model_id):
        return

    if mime_type.startswith("image/"):
        status_msg = await message.answer("<i>Обрабатываю изображение...</i>", parse_mode="HTML")
        try:
            history = list(get_history(data))
            image_data_url = await telegram_file_to_data_url(message=message, file_id=document.file_id, mime_type=mime_type)
            user_content = make_vision_content(prompt=caption, image_data_url=image_data_url)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history) + [{"role": "user", "content": user_content}]
            
            async with _bot_ai_semaphore:
                reply, debug = await asyncio.wait_for(
                    call_ai(model_id, messages),
                    timeout=BOT_AI_TIMEOUT_SECONDS,
                )
            
            history.append({"role": "user", "content": f"[Изображение-файл] {caption}".strip()})
            history.append({"role": "assistant", "content": reply})
            await state.update_data(chat_history=trim_history(history))
            
            await send_ai_reply(status_msg, reply)
            await send_debug_info(status_msg, debug)
        except Exception as e:
            await edit_error(status_msg, "Ошибка", e)
        return

    status_msg = await message.answer("<i>Читаю файл...</i>", parse_mode="HTML")
    try:
        raw = await telegram_file_to_bytes(
            message,
            document.file_id,
            max_bytes=MAX_FILE_BYTES,
        )
        try:
            file_text = await asyncio.wait_for(
                asyncio.to_thread(extract_text_from_bytes, raw, filename, mime_type),
                timeout=FILE_PARSE_TIMEOUT_SECONDS,
            )
        except Exception as e:
            await edit_error(status_msg, "Не удалось прочитать файл", e)
            return

        if len(file_text) > MAX_FILE_CHARS: file_text = file_text[:MAX_FILE_CHARS] + "\n[...обрезано]"
        history = list(get_history(data))
        user_prompt = caption.strip()
        full_prompt = f"{user_prompt}\n\n--- Содержимое файла {filename} ---\n{file_text}" if user_prompt else f"Проанализируй содержимое файла {filename}:\n\n{file_text}"

        history.append({"role": "user", "content": full_prompt})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + trim_history(history)

        await status_msg.edit_text("<i>Думаю...</i>", parse_mode="HTML")
        async with _bot_ai_semaphore:
            reply, debug = await asyncio.wait_for(
                call_ai(model_id, messages),
                timeout=BOT_AI_TIMEOUT_SECONDS,
            )

        history.append({"role": "assistant", "content": reply})
        await state.update_data(chat_history=trim_history(history))

        await send_ai_reply(status_msg, reply)
        await send_debug_info(status_msg, debug)
    except Exception as e:
        await edit_error(status_msg, "Ошибка при обработке файла", e)

@router.message(BotStates.chat_mode)
async def handle_unsupported_message(message: Message):
    await message.answer("Я могу обработать текст, фото, документы.", reply_markup=cancel_keyboard())
