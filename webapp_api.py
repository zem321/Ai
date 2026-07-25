import sys
import os

# Решение проблемы путей импорта в Python (sys.path)
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import json
import time
import hmac
import hashlib
import secrets
import logging
import inspect
import re
import asyncio
import base64
import binascii
import ipaddress
from collections import OrderedDict, deque
from urllib.parse import parse_qsl, urlsplit
from aiohttp import web

import database as db

logger = logging.getLogger(__name__)
logger.info("webapp_api module loaded: build=auth-enforced-v6 + file-support + web-code-login")

# Динамический импорт с поддержкой различных версий и структур проекта
try:
    from handlers.chat_handler import call_ai, SYSTEM_PROMPT, trim_history, make_vision_content, MAX_HISTORY
    logger.info("Imported chat_handler functions from handlers.chat_handler")
except ImportError:
    try:
        from handlers.chat_handler_4 import call_ai, SYSTEM_PROMPT, trim_history, make_vision_content, MAX_HISTORY
        logger.info("Imported chat_handler functions from handlers.chat_handler_4")
    except ImportError:
        try:
            from chat_handler import call_ai, SYSTEM_PROMPT, trim_history, make_vision_content, MAX_HISTORY
            logger.info("Imported chat_handler functions from chat_handler")
        except ImportError:
            try:
                from chat_handler_4 import call_ai, SYSTEM_PROMPT, trim_history, make_vision_content, MAX_HISTORY
                logger.info("Imported chat_handler functions from chat_handler_4")
            except ImportError as e:
                logger.error("Could not import chat_handler functions from any known chat_handler module!")
                raise e

# Динамический импорт для генератора картинок
try:
    from handlers.image_handler import generate_image
    logger.info("Imported generate_image from handlers.image_handler")
except ImportError:
    try:
        from image_handler import generate_image
        logger.info("Imported generate_image from image_handler")
    except ImportError as e:
        logger.error("Could not import generate_image from any known image_handler module!")
        raise e

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
try:
    ADMIN_ID = int(os.environ["ADMIN_ID"])
except (KeyError, TypeError, ValueError) as exc:
    raise RuntimeError("ADMIN_ID должен быть задан положительным целым числом") from exc
if ADMIN_ID <= 0:
    raise RuntimeError("ADMIN_ID должен быть положительным Telegram ID")

# Сколько секунд считаем initData валидной (защита от replay).
INIT_DATA_MAX_AGE = max(300, min(int(os.getenv("INIT_DATA_MAX_AGE", "3600")), 3600))
INIT_DATA_FUTURE_SKEW = 60

# ------------------ Вход по коду (для сайта вне Telegram) ------------------
# Код одноразовый и короткоживущий. После входа сервер выдаёт HttpOnly-cookie:
# JavaScript не видит токен сессии.
LOGIN_CODE_TTL = 10 * 60              # код действителен 10 минут
WEB_SESSION_TTL = max(
    60 * 60,
    min(int(os.getenv("WEB_SESSION_TTL", str(7 * 24 * 60 * 60))), 30 * 24 * 60 * 60),
)
WEB_SESSION_IDLE_TTL = max(
    15 * 60,
    min(int(os.getenv("WEB_SESSION_IDLE_TTL", str(24 * 60 * 60))), WEB_SESSION_TTL),
)
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1").strip().lower() not in {"0", "false", "no"}
SESSION_COOKIE_NAME = (
    "__Host-assistant_session" if COOKIE_SECURE else "assistant_session"
)
PUBLIC_ORIGIN = os.getenv("PUBLIC_ORIGIN", "").strip().rstrip("/")
if not PUBLIC_ORIGIN:
    raise RuntimeError("PUBLIC_ORIGIN должен быть задан, например https://assistant.example")
_public_origin_parts = urlsplit(PUBLIC_ORIGIN)
if (
    _public_origin_parts.scheme != "https"
    or not _public_origin_parts.netloc
    or _public_origin_parts.username is not None
    or _public_origin_parts.password is not None
    or _public_origin_parts.path not in {"", "/"}
    or _public_origin_parts.query
    or _public_origin_parts.fragment
):
    raise RuntimeError("PUBLIC_ORIGIN должен быть точным HTTPS-origin без пути")
if not COOKIE_SECURE:
    raise RuntimeError("COOKIE_SECURE должен быть включён в production")

# Ограничения запросов. Этот процесс запускается в одном экземпляре вместе с
# ботом; для горизонтального масштабирования эти счётчики следует вынести в Redis.
_CODE_RATE_LIMIT = 8
_CODE_RATE_WINDOW = 10 * 60
_MAX_RATE_BUCKETS = 10_000
try:
    _TRUSTED_PROXY_NETWORKS = tuple(
        ipaddress.ip_network(value.strip(), strict=False)
        for value in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
        if value.strip()
    )
except ValueError as exc:
    raise RuntimeError("TRUSTED_PROXY_IPS содержит некорректный IP или CIDR") from exc

ALLOWED_MODELS = {
    "gemini/gemini-3.1-flash-lite",
    "gemini/gemini-3.5-flash-lite",
    "gemini/gemini-3.6-flash",
    "meta/llama-4-maverick-17b-128e-instruct",
    "z-ai/glm-5.2",
    "nvidia/nemotron-3-super-120b-a12b",
}
DEFAULT_MODEL = "gemini/gemini-3.1-flash-lite"

AUTH_BODY_LIMIT = 1024
ADMIN_BODY_LIMIT = 16 * 1024
IMAGE_BODY_LIMIT = 64 * 1024
CHAT_BODY_LIMIT = max(
    1024 * 1024,
    min(int(os.getenv("CHAT_BODY_LIMIT", str(10 * 1024 * 1024))), 15 * 1024 * 1024),
)
MAX_MESSAGE_CHARS = 20_000
MAX_REPLY_CHARS = 500_000
MAX_GENERATED_IMAGE_BYTES = 20 * 1024 * 1024
MAX_HISTORY_ITEMS = 40
MAX_HISTORY_CHARS = 50_000
MAX_HISTORY_TOTAL_CHARS = max(
    20_000,
    min(int(os.getenv("MAX_HISTORY_TOTAL_CHARS", "120000")), 250_000),
)
MAX_ATTACHMENTS = 4
MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_TEXT_CHARS = 60_000
MAX_TOTAL_ATTACHMENT_TEXT_CHARS = 120_000
AI_TIMEOUT_SECONDS = max(15, min(int(os.getenv("AI_TIMEOUT_SECONDS", "120")), 300))
IMAGE_TIMEOUT_SECONDS = max(30, min(int(os.getenv("IMAGE_TIMEOUT_SECONDS", "180")), 300))
AI_CONCURRENCY = max(1, min(int(os.getenv("AI_CONCURRENCY", "4")), 16))
DEFAULT_DAILY_AI_LIMIT = max(
    1, min(int(os.getenv("DEFAULT_DAILY_AI_LIMIT", "200")), 10000)
)
DEFAULT_DAILY_IMAGE_LIMIT = max(
    1, min(int(os.getenv("DEFAULT_DAILY_IMAGE_LIMIT", "20")), 1000)
)
_ai_semaphore = asyncio.Semaphore(AI_CONCURRENCY)

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_ALLOWED_FILE_TYPES = {
    "text/plain", "text/csv", "text/markdown", "application/json",
    "application/xml", "text/xml", "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/octet-stream", "",
}


class SlidingWindowLimiter:
    def __init__(self, max_buckets: int = _MAX_RATE_BUCKETS):
        self.max_buckets = max_buckets
        self.buckets: OrderedDict[str, deque[float]] = OrderedDict()

    def allow(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        bucket = self.buckets.get(key)
        if bucket is None:
            self._remove_stale(now, window)
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

    def _remove_stale(self, now: float, window: int) -> None:
        for key in list(self.buckets.keys()):
            bucket = self.buckets[key]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            if not bucket:
                self.buckets.pop(key, None)


_rate_limiter = SlidingWindowLimiter()

def _client_ip(request: web.Request) -> str:
    remote = request.remote or "unknown"
    try:
        remote_ip = ipaddress.ip_address(remote)
    except ValueError:
        return remote
    if not any(remote_ip in network for network in _TRUSTED_PROXY_NETWORKS):
        return str(remote_ip)
    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded or len(forwarded) > 512:
        return str(remote_ip)
    try:
        chain = [
            ipaddress.ip_address(value.strip())
            for value in forwarded.split(",")
            if value.strip()
        ]
    except ValueError:
        return str(remote_ip)
    if len(chain) > 10:
        return str(remote_ip)
    for candidate in reversed(chain):
        if any(candidate in network for network in _TRUSTED_PROXY_NETWORKS):
            continue
        return str(candidate)
    return str(remote_ip)

def _rate_limited(ip: str) -> bool:
    ip_ok = _rate_limiter.allow(f"auth:{ip}", _CODE_RATE_LIMIT, _CODE_RATE_WINDOW)
    if not ip_ok:
        return True
    global_ok = _rate_limiter.allow("auth:global", 300, 60)
    return not global_ok


def _limited(key: str, limit: int, window: int) -> bool:
    return not _rate_limiter.allow(key, limit, window)


async def _read_json_object(request: web.Request, max_bytes: int) -> dict:
    content_length = request.content_length
    if content_length is not None and content_length > max_bytes:
        raise web.HTTPRequestEntityTooLarge(
            max_size=max_bytes, actual_size=content_length
        )
    raw = await request.read()
    if len(raw) > max_bytes:
        raise web.HTTPRequestEntityTooLarge(
            max_size=max_bytes, actual_size=len(raw)
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(text="Некорректный JSON") from exc
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(text="Ожидается JSON-объект")
    return payload


def _request_origin(request: web.Request) -> str:
    origin = request.headers.get("Origin", "").rstrip("/")
    if origin:
        return origin
    return ""


def _same_origin(request: web.Request) -> bool:
    origin = _request_origin(request)
    if not origin:
        return False
    if PUBLIC_ORIGIN:
        return hmac.compare_digest(origin, PUBLIC_ORIGIN)
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and parts.netloc == request.host


def _safe_filename(value: object, fallback: str = "file") -> str:
    name = os.path.basename(str(value or fallback)).replace("\x00", "")
    name = re.sub(r"[\r\n\t]+", " ", name).strip()
    return (name[:100] or fallback)


def _validate_history(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_HISTORY_ITEMS:
        raise ValueError("Некорректная или слишком длинная история")
    result: list[dict] = []
    total_chars = 0
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Некорректный элемент истории")
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ValueError("Недопустимая роль или содержимое истории")
        if len(content) > MAX_HISTORY_CHARS:
            raise ValueError("Слишком длинное сообщение в истории")
        total_chars += len(content)
        if total_chars > MAX_HISTORY_TOTAL_CHARS:
            raise ValueError("Суммарная история слишком длинная")
        result.append({"role": role, "content": content})
    return result


def _decode_data_url(value: object, declared_type: str) -> tuple[bytes, str]:
    if not isinstance(value, str) or not value:
        raise ValueError("Пустое вложение")
    if len(value) > (MAX_ATTACHMENT_BYTES * 4 // 3) + 4096:
        raise ValueError("Вложение слишком большое")
    media_type = declared_type.strip().lower()
    encoded = value
    if value.startswith("data:"):
        try:
            header, encoded = value.split(",", 1)
        except ValueError as exc:
            raise ValueError("Некорректный data URL") from exc
        if ";base64" not in header.lower():
            raise ValueError("Вложение должно быть в base64")
        header_type = header[5:].split(";", 1)[0].strip().lower()
        if header_type:
            if media_type and header_type != media_type:
                raise ValueError("MIME-тип вложения не совпадает")
            media_type = header_type
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Некорректное base64-вложение") from exc
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Вложение слишком большое")
    return raw, media_type


def _validate_attachments(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > MAX_ATTACHMENTS:
        raise ValueError("Слишком много вложений")
    validated: list[dict] = []
    total = 0
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Некорректное вложение")
        declared_type = str(item.get("type") or "").strip().lower()
        raw, media_type = _decode_data_url(item.get("dataUrl"), declared_type)
        is_image = media_type in _ALLOWED_IMAGE_TYPES
        if media_type.startswith("image/") and not is_image:
            raise ValueError("Этот формат изображения не поддерживается")
        if not is_image and media_type not in _ALLOWED_FILE_TYPES:
            raise ValueError("Этот тип файла не поддерживается")
        total += len(raw)
        if total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise ValueError("Суммарный размер вложений слишком большой")
        validated.append({
            "name": _safe_filename(item.get("name"), "file"),
            "type": media_type,
            "raw": raw,
            "is_image": is_image,
        })
    return validated

# ------------------ Автоопределение режима (чат / картинка / файл) ------------------

IMAGE_KEYWORDS = (
    "нарису", "нарисуй", "нарисовать", "сгенерируй", "сгенерировать",
    "генерац", "сделай картинк", "сделай фото", "сделай изображен",
    "создай картинк", "создай изображен", "создай фото",
    "хочу картинк", "хочу фото", "хочу изображен", "покажи картинк",
    "draw ", "generate image", "generate a picture", "generate picture",
    "create an image", "create image", "create a picture",
    "make an image", "make a picture", "image of", "picture of",
    "картинку с", "картинка с", "изображение с", "фото с",
)

# Только явные команды отправки файлом
FILE_SEND_COMMANDS = [
    "отправь файлом", "ответ файлом", "скинь файлом", "дай файлом",
    "пришли файлом", "прислать файлом", "отправить файлом", "скинуть файлом",
    "отправьте файлом", "пришлите файлом", "скиньте файлом",
    "send as file", "as a file", "в виде файла",
    "файлом",
]

def detect_intent(text: str) -> str:
    """Возвращает 'image', 'file' или 'chat'."""
    t = (text or "").strip().lower()
    if not t:
        return "chat"

    for kw in IMAGE_KEYWORDS:
        if kw in t:
            return "image"

    if is_file_request(t):
        return "file"

    return "chat"

# Явные названия расширений/форматов на русском и английском
EXT_ALIASES = {
    # русские
    "докс": "docx", "ворд": "docx",
    "эксель": "xlsx", "таблиц": "xlsx",
    "питон": "py", "пайтон": "py",
    "джаваскрипт": "js", "хтмл": "html",
    "пдф": "pdf", "пдп": "pdf",
    "маркдаун": "md", "текст": "txt",
    # английские слова/аббревиатуры → расширение
    "word": "docx", "excel": "xlsx",
    "python": "py", "javascript": "js",
    "typescript": "ts", "markdown": "md",
    "html": "html", "css": "css",
    "json": "json", "yaml": "yml",
    "sql": "sql", "xml": "xml",
    "bash": "sh", "shell": "sh",
    "rust": "rs", "golang": "go",
    "java": "java", "php": "php",
    "ruby": "rb", "swift": "swift",
    "kotlin": "kt", "cpp": "cpp",
    "csharp": "cs", "csv": "csv",
    "pdf": "pdf", "zip": "zip",
    "toml": "toml", "ini": "ini",
}

# Прямые расширения (когда пишут само расширение без точки)
KNOWN_EXTS = {
    "docx","doc","xlsx","xls","py","js","ts","json","html","htm",
    "css","md","txt","csv","sql","sh","yaml","yml","xml","pdf",
    "zip","go","rs","rb","java","php","cpp","cs","toml","ini",
    "swift","kt","r","c","h","pl","lua",
}

def extract_file_extension(text: str) -> str:
    """Извлекает расширение из запроса: 'в docx', 'в py', '.html', 'word', 'python' и т.д."""
    low = (text or "").lower()

    # 1. Точечное расширение: .docx, .py и т.д.
    m = re.search(r'\.([a-z0-9]{1,5})(?:\s|$|,)', low)
    if m:
        ext = m.group(1)
        if ext in KNOWN_EXTS:
            return ext

    # 2. «в расширение» / «в формате расширение» / «формате расширение»
    m = re.search(r'(?:в\s+формате|формате|в)\s+\.?([a-z0-9а-яё]{2,12})(?:\s|$|,)', low)
    if m:
        word = m.group(1)
        if word in EXT_ALIASES:
            return EXT_ALIASES[word]
        if word in KNOWN_EXTS:
            return word

    # 3. Любое слово из EXT_ALIASES встречается в тексте
    words = re.findall(r'[a-z0-9а-яё]+', low)
    for w in words:
        if w in EXT_ALIASES:
            return EXT_ALIASES[w]
        if w in KNOWN_EXTS:
            return w

    return ""

def is_file_request(text: str) -> bool:
    """Срабатывает ТОЛЬКО на явные команды типа 'отправь файлом', 'скинь файлом'."""
    low = (text or "").lower().strip()
    return any(cmd in low for cmd in FILE_SEND_COMMANDS)

def guess_filename_from_prompt(user_prompt: str, ai_reply: str) -> str:
    """Определяет имя и расширение файла из запроса пользователя."""
    low = (user_prompt or "").lower()

    ext_map = {
        "python": "py", "py": "py",
        "javascript": "js", "js": "js",
        "typescript": "ts", "ts": "ts",
        "json": "json", "html": "html",
        "css": "css", "java": "java",
        "c": "c", "cpp": "cpp", "c++": "cpp",
        "go": "go", "rust": "rs", "rs": "rs",
        "php": "php", "ruby": "rb",
        "bash": "sh", "sh": "sh", "shell": "sh",
        "sql": "sql", "yaml": "yml", "yml": "yml",
        "xml": "xml", "markdown": "md", "md": "md",
        "txt": "txt", "csv": "csv", "toml": "toml",
        "docx": "docx", "doc": "docx",
        "xlsx": "xlsx", "xls": "xlsx",
        "pdf": "pdf",
        "zip": "zip",
    }

    # Русские слова → расширения
    ru_map = {
        "докс": "docx", "ворд": "docx", "word": "docx",
        "эксель": "xlsx", "excel": "xlsx", "таблиц": "xlsx",
        "пдф": "pdf", "питон": "py", "пайтон": "py",
        "джавастрипт": "js", "хтмл": "html",
    }
    for ru, ext in ru_map.items():
        if ru in low:
            return f"ответ.{ext}"

    # 1. Явное имя файла в промпте: script.py, report.docx и т.п.
    m = re.search(
        r'([a-zA-Z0-9_а-яё.-]+\.(?:py|js|ts|json|csv|md|txt|html|css|java|c|cpp|go|rs|php|rb|sh|yaml|yml|sql|xml|toml|ini|env|docx|doc|xlsx|xls|pdf|zip))\b',
        user_prompt, re.I
    )
    if m:
        return m.group(1)

    # 2. Расширение упомянуто в промпте: "в .py", "в docx", "скинь xlsx"
    m = re.search(
        r'(?:^|\s|в\s+|\.)(py|js|ts|json|csv|md|txt|html|css|java|cpp|go|rs|php|rb|sh|yaml|yml|sql|xml|toml|ini|env|docx|doc|xlsx|xls|pdf|zip|python|javascript|typescript|markdown|bash|shell|rust|ruby)(?:\s|$|,|\.|файл)',
        low, re.I
    )
    if m:
        lang = m.group(1).lower()
        ext = ext_map.get(lang, lang)
        return f"ответ.{ext}"

    # 3. Язык в markdown-блоке ответа модели
    m = re.search(r'^```([a-zA-Z0-9_+-]+)', ai_reply.strip(), re.MULTILINE)
    if m:
        lang = m.group(1).lower()
        ext = ext_map.get(lang, "txt")
        return f"ответ.{ext}"

    return "ответ.txt"

def get_file_type(filename: str) -> str:
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    types = {
        "html": "Code · HTML",
        "py": "Code · Python",
        "js": "Code · JavaScript",
        "ts": "Code · TypeScript",
        "json": "JSON",
        "css": "CSS",
        "md": "Markdown",
        "txt": "Text",
        "csv": "CSV",
        "sql": "SQL",
    }
    return types.get(ext, "Code")

# ------------------ Проверка Telegram WebApp initData ------------------

def check_init_data(init_data: str) -> dict | None:
    if not BOT_TOKEN:
        logger.error("webapp_api: BOT_TOKEN не задан — все запросы мини-аппа будут отклонены")
        return None

    if not init_data or len(init_data) > 8192:
        logger.debug("webapp_api: запрос без initData")
        return None

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return None

    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        logger.debug("webapp_api: initData содержит повторяющиеся поля")
        return None
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash or not re.fullmatch(r"[0-9a-fA-F]{64}", received_hash):
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        logger.debug("webapp_api: подпись initData не совпала")
        return None

    auth_date = data.get("auth_date")
    try:
        auth_timestamp = int(auth_date)
    except (TypeError, ValueError):
        logger.debug("webapp_api: initData без корректной auth_date")
        return None
    age = time.time() - auth_timestamp
    if age < -INIT_DATA_FUTURE_SKEW or age > INIT_DATA_MAX_AGE:
        logger.debug("webapp_api: initData вне допустимого временного окна")
        return None

    user_raw = data.get("user")
    if user_raw:
        try:
            data["user"] = json.loads(user_raw)
        except Exception:
            data["user"] = None
    if not isinstance(data.get("user"), dict):
        return None

    return data

async def _check_access_status(user_id: int) -> tuple[int | None, web.Response | None]:
    """Общая проверка approved/pending/rejected для уже аутентифицированного user_id.

    Используется и для Telegram-входа (initData), и для входа по коду на сайте —
    оба способа лишь подтверждают, ЧЕЙ это user_id, а доступ к боту/сайту
    по-прежнему решает одна и та же таблица users.
    """
    if user_id == ADMIN_ID:
        return user_id, None

    if await db.is_approved(user_id):
        return user_id, None

    if await db.is_pending(user_id):
        logger.info("webapp_api: пользователь %s — доступ ожидает одобрения", user_id)
        return None, web.json_response(
            {"error": "pending", "message": "Запрос на доступ ещё не одобрен."},
            status=403,
        )

    if await db.is_rejected(user_id):
        logger.info("webapp_api: пользователь %s — доступ отклонён", user_id)
        return None, web.json_response(
            {"error": "rejected", "message": "Доступ отклонён."},
            status=403,
        )

    logger.info("webapp_api: пользователь %s — не найден в базе", user_id)
    return None, web.json_response(
        {"error": "no_access", "message": "Нет доступа. Откройте бота и отправьте /start."},
        status=403,
    )


async def _authorize(request: web.Request) -> tuple[int | None, web.Response | None]:
    """Поддерживает два способа входа:
    - 'Authorization: tma <initData>'  — Telegram Mini App (проверка HMAC-подписи);
    - HttpOnly cookie — обычный сайт, вход по коду из бота.
    """
    auth_header = request.headers.get("Authorization", "")

    if auth_header.startswith("tma "):
        init_data = auth_header[4:]
        parsed = check_init_data(init_data)
        if not parsed or not parsed.get("user"):
            return None, web.json_response(
                {"error": "unauthorized", "message": "Не удалось подтвердить пользователя Telegram."},
                status=401,
            )
        user_id = parsed["user"].get("id")
        if not isinstance(user_id, int) or user_id <= 0:
            return None, web.json_response({"error": "unauthorized"}, status=401)
        return await _check_access_status(user_id)

    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token:
        if len(token) > 128:
            return None, web.json_response({"error": "unauthorized"}, status=401)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _same_origin(request):
            return None, web.json_response({"error": "bad_origin"}, status=403)
        user_id = await db.get_web_session_user(
            token, idle_seconds=WEB_SESSION_IDLE_TTL
        )
        if not user_id:
            return None, web.json_response(
                {"error": "unauthorized", "message": "Сессия истекла. Войдите заново по коду из бота."},
                status=401,
            )
        return await _check_access_status(user_id)

    return None, web.json_response(
        {"error": "unauthorized", "message": "Не удалось подтвердить пользователя."},
        status=401,
    )


async def _reserve_ai_quota(
    user_id: int,
    model: str,
    default_daily_limit: int,
) -> web.Response | None:
    try:
        reserved = await db.reserve_request(
            user_id,
            model,
            source="webapp",
            default_daily_limit=default_daily_limit,
        )
    except Exception:
        logger.exception("webapp_api: ошибка проверки серверной квоты")
        return web.json_response(
            {"error": "quota_unavailable", "message": "Проверка лимита временно недоступна."},
            status=503,
        )
    if not reserved:
        return web.json_response(
            {"error": "daily_limit", "message": "Дневной лимит для выбранной модели исчерпан."},
            status=429,
        )
    return None

def build_vision_content(text: str, image_url: str):
    try:
        sig = inspect.signature(make_vision_content)
        params = list(sig.parameters.keys())

        kwargs = {}
        for p in params:
            if p in ('image_url', 'url', 'img_url', 'img', 'image_data_url', 'data_url', 'dataUrl'):
                kwargs[p] = image_url
            elif p in ('text', 'caption', 'prompt', 'message', 'msg'):
                kwargs[p] = text

        if kwargs and len(kwargs) == len(params):
            return make_vision_content(**kwargs)

        if len(params) == 1:
            res = make_vision_content(image_url)
            if isinstance(res, list):
                has_text = any(item.get("type") == "text" for item in res if isinstance(item, dict))
                if not has_text:
                    res.append({"type": "text", "text": text})
                return res
            elif isinstance(res, dict):
                return [{"type": "text", "text": text}, res]
            else:
                return [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image_url}}]

        elif len(params) >= 2:
            first_param = params[0]
            if first_param in ('image_url', 'url', 'img_url', 'img', 'image_data_url', 'data_url', 'dataUrl'):
                return make_vision_content(image_url, text)
            else:
                return make_vision_content(text, image_url)
    except Exception as e:
        logger.warning("build_vision_content error: %r", e)

    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_url}}
    ]

# ------------------ HTTP-хендлеры ------------------

async def api_me(request: web.Request) -> web.Response:
    user_id, err = await _authorize(request)
    if err is not None:
        return err
    if not isinstance(user_id, int):
        return web.json_response({"error": "unauthorized"}, status=401)
    response = web.json_response(
        {"ok": True, "user_id": user_id, "is_admin": user_id == ADMIN_ID}
    )
    response.headers["Cache-Control"] = "no-store"
    return response

async def api_chat(request: web.Request) -> web.Response:
    user_id, err = await _authorize(request)
    if err is not None:
        logger.debug("webapp_api: /api/chat — отказ в доступе")
        return err

    if not isinstance(user_id, int):
        return web.json_response({"error": "unauthorized"}, status=401)

    if _limited(f"chat:{user_id}", 20, 60):
        return web.json_response(
            {"error": "rate_limited", "message": "Слишком много запросов. Подождите минуту."},
            status=429,
        )

    try:
        payload = await _read_json_object(request, CHAT_BODY_LIMIT)
        message_value = payload.get("message", "")
        if not isinstance(message_value, str):
            raise ValueError("Сообщение должно быть строкой")
        user_text = message_value.strip()
        if len(user_text) > MAX_MESSAGE_CHARS:
            raise ValueError("Сообщение слишком длинное")
        model_id = payload.get("model") or DEFAULT_MODEL
        if not isinstance(model_id, str) or model_id not in ALLOWED_MODELS:
            return web.json_response(
                {"error": "invalid_model", "message": "Недоступная модель."},
                status=400,
            )
        history = _validate_history(payload.get("history") or [])
        attachments = _validate_attachments(payload.get("attachments") or [])
    except web.HTTPRequestEntityTooLarge:
        return web.json_response(
            {"error": "too_large", "message": "Запрос или вложения слишком большие."},
            status=413,
        )
    except (web.HTTPBadRequest, ValueError) as exc:
        return web.json_response(
            {"error": "bad_request", "message": str(exc)},
            status=400,
        )

    if not user_text and not attachments:
        return web.json_response({"error": "empty_message"}, status=400)

    if not user_text and attachments:
        user_text = "Вложения"

    intent = detect_intent(user_text)

    # === ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ ===
    if intent == "image":
        if _limited(f"image:{user_id}", 5, 10 * 60):
            return web.json_response(
                {"error": "rate_limited", "message": "Лимит генерации изображений исчерпан. Попробуйте позже."},
                status=429,
            )
        quota_error = await _reserve_ai_quota(
            user_id,
            "image-generation",
            DEFAULT_DAILY_IMAGE_LIMIT,
        )
        if quota_error is not None:
            return quota_error
        try:
            async with _ai_semaphore:
                image_bytes = await asyncio.wait_for(
                    generate_image(user_text),
                    timeout=IMAGE_TIMEOUT_SECONDS,
                )
        except asyncio.TimeoutError:
            return web.json_response(
                {"error": "generation_timeout", "message": "Генерация заняла слишком много времени."},
                status=504,
            )
        except Exception:
            logger.exception("webapp_api: ошибка генерации изображения")
            return web.json_response(
                {"error": "generation_failed", "message": "Не удалось создать изображение."},
                status=502,
            )

        if not isinstance(image_bytes, (bytes, bytearray)) or len(image_bytes) > MAX_GENERATED_IMAGE_BYTES:
            logger.error("webapp_api: генератор вернул недопустимый размер изображения")
            return web.json_response(
                {"error": "generation_failed", "message": "Получено слишком большое изображение."},
                status=502,
            )
        return web.json_response({
            "intent": "image",
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "prompt": user_text,
        })

    # === ОБЫЧНЫЙ ЧАТ ===
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += trim_history(history)

    image_attachments = [
        a for a in attachments
        if a["is_image"]
    ]
    file_attachments = [
        a for a in attachments
        if not a["is_image"]
    ]

    if image_attachments or file_attachments:
        # Build multi-part content
        content_parts = []

        # Add user text first — only if no file attachments (for files, text is embedded in prompt)
        has_files = bool(file_attachments)
        if user_text and user_text != "Вложения" and not has_files:
            content_parts.append({"type": "text", "text": user_text})

        # Add images via vision
        for img in image_attachments:
            media_type = img["type"]
            b64data = base64.b64encode(img["raw"]).decode("ascii")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64data}"}
            })

        # Add file contents as text blocks (same logic as bot's extract_text_from_bytes)
        remaining_file_chars = MAX_TOTAL_ATTACHMENT_TEXT_CHARS
        for fa in file_attachments:
            fname = fa["name"]
            raw_bytes = fa["raw"]
            try:
                # Try encodings: utf-8 → cp1251 → latin1 (same as bot)
                file_text = None
                for enc in ("utf-8", "cp1251", "latin1"):
                    try:
                        file_text = raw_bytes.decode(enc)
                        break
                    except Exception:
                        continue
                if file_text is None:
                    file_text = raw_bytes.decode("utf-8", errors="replace")
                file_limit = min(MAX_ATTACHMENT_TEXT_CHARS, remaining_file_chars)
                if file_limit <= 0:
                    content_parts.append({
                        "type": "text",
                        "text": f"[Файл: {fname}] — пропущен из-за общего лимита вложений",
                    })
                    continue
                if len(file_text) > file_limit:
                    file_text = file_text[:file_limit] + (
                        f"\n\n[...обрезано до {file_limit} символов]"
                    )
                remaining_file_chars -= min(len(file_text), file_limit)
                # Format same as bot
                if user_text and user_text != "Вложения":
                    prompt_text = f"{user_text}\n\n--- Содержимое файла {fname} ---\n{file_text}"
                else:
                    prompt_text = f"Проанализируй содержимое файла {fname}:\n\n{file_text}"
                content_parts.append({"type": "text", "text": prompt_text})
            except Exception:
                logger.warning("Failed to decode file attachment %s", fname)
                content_parts.append({"type": "text", "text": f"[Файл: {fname}] — не удалось прочитать"})

        if not content_parts:
            content_parts.append({"type": "text", "text": user_text})

        user_content = content_parts
    else:
        user_content = user_text

    messages.append({"role": "user", "content": user_content})

    quota_error = await _reserve_ai_quota(
        user_id,
        model_id,
        DEFAULT_DAILY_AI_LIMIT,
    )
    if quota_error is not None:
        return quota_error

    try:
        async with _ai_semaphore:
            reply, debug = await asyncio.wait_for(
                call_ai(model_id, messages),
                timeout=AI_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        return web.json_response(
            {"error": "ai_timeout", "message": "Ответ модели занял слишком много времени."},
            status=504,
        )
    except Exception:
        logger.exception("webapp_api: ошибка call_ai")
        return web.json_response(
            {"error": "ai_failed", "message": "Модель временно недоступна."},
            status=502,
        )
    reply = str(reply or "")
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS] + "\n\n[Ответ обрезан сервером]"
    if not isinstance(debug, dict):
        debug = {}

    # === ОТПРАВКА ФАЙЛА ===
    want_file = is_file_request(user_text)

    if want_file:
        # Сначала пытаемся извлечь расширение из запроса (в docx, в py, и т.д.)
        requested_ext = extract_file_extension(user_text)
        
        if requested_ext:
            # Используем явно указанное расширение
            filename = f"ответ.{requested_ext}"
        else:
            # Иначе угадываем по содержимому
            filename = guess_filename_from_prompt(user_text, reply)
        
        file_type = get_file_type(filename)

        return web.json_response({
            "file": {
                "name": filename,
                "type": file_type,
                "content": reply,
                "mime": "text/html" if filename.endswith(".html") else "text/plain"
            }
        })

    # Обычный текстовый ответ
    return web.json_response({
        "intent": "chat",
        "reply": reply,
        "model": debug.get("provider_model") or model_id,
    })

async def api_auth_code(request: web.Request) -> web.Response:
    """POST /api/auth/code — вход на сайт (вне Telegram) по одноразовому коду из бота.

    Тело: {"code": "AB12CD3"}.
    Ответ: {"ok": true, "user_id": ..., "is_admin": bool}.
    Токен возвращается только в защищённой HttpOnly-cookie.
    """
    try:
        payload = await _read_json_object(request, AUTH_BODY_LIMIT)
    except web.HTTPRequestEntityTooLarge:
        return web.json_response({"error": "too_large"}, status=413)
    except web.HTTPBadRequest:
        return web.json_response({"error": "bad_request"}, status=400)

    if not _same_origin(request):
        return web.json_response({"error": "bad_origin"}, status=403)

    raw_code = payload.get("code")
    if not isinstance(raw_code, str):
        code = ""
    else:
        code = raw_code.strip().upper()
    if not re.fullmatch(r"[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}", code):
        return web.json_response(
            {"error": "invalid_code", "message": "Введите 8-значный код из бота."},
            status=400,
        )

    ip = _client_ip(request)
    if _rate_limited(ip):
        if _rate_limiter.allow(f"auth-log:{ip}", 1, 60):
            logger.warning("webapp_api: /api/auth/code — превышен лимит попыток, ip=%s", ip)
        return web.json_response(
            {"error": "rate_limited", "message": "Слишком много попыток. Подождите несколько минут."},
            status=429,
        )

    user_id = await db.consume_login_code(code)
    if not user_id:
        return web.json_response(
            {"error": "invalid_code", "message": "Код неверный или уже истёк. Запросите новый в боте."},
            status=401,
        )

    checked_id, err = await _check_access_status(user_id)
    if err is not None:
        return err

    token = secrets.token_urlsafe(32)
    await db.create_web_session(token, checked_id, WEB_SESSION_TTL)
    logger.info("webapp_api: пользователь %s вошёл на сайт по коду", checked_id)

    response = web.json_response({
        "ok": True,
        "user_id": checked_id,
        "is_admin": checked_id == ADMIN_ID,
        "expires_in": WEB_SESSION_TTL,
    })
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=WEB_SESSION_TTL,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="Strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def api_auth_logout(request: web.Request) -> web.Response:
    """POST /api/auth/logout — завершить текущую cookie-сессию."""
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token and not _same_origin(request):
        return web.json_response({"error": "bad_origin"}, status=403)
    if token:
        await db.delete_web_session(token)
    response = web.json_response({"ok": True})
    response.del_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="Strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


async def api_image(request: web.Request) -> web.Response:
    user_id, err = await _authorize(request)
    if err is not None:
        return err

    if not isinstance(user_id, int):
        return web.json_response({"error": "unauthorized"}, status=401)

    if _limited(f"image:{user_id}", 5, 10 * 60):
        return web.json_response(
            {"error": "rate_limited", "message": "Лимит генерации изображений исчерпан. Попробуйте позже."},
            status=429,
        )
    try:
        payload = await _read_json_object(request, IMAGE_BODY_LIMIT)
    except web.HTTPRequestEntityTooLarge:
        return web.json_response({"error": "too_large"}, status=413)
    except web.HTTPBadRequest:
        return web.json_response({"error": "bad_request"}, status=400)

    prompt_value = payload.get("prompt")
    prompt = prompt_value.strip() if isinstance(prompt_value, str) else ""
    if not prompt:
        return web.json_response({"error": "empty_prompt"}, status=400)
    if len(prompt) > MAX_MESSAGE_CHARS:
        return web.json_response({"error": "prompt_too_long"}, status=400)

    quota_error = await _reserve_ai_quota(
        user_id,
        "image-generation",
        DEFAULT_DAILY_IMAGE_LIMIT,
    )
    if quota_error is not None:
        return quota_error

    try:
        async with _ai_semaphore:
            image_bytes = await asyncio.wait_for(
                generate_image(prompt),
                timeout=IMAGE_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        return web.json_response(
            {"error": "generation_timeout", "message": "Генерация заняла слишком много времени."},
            status=504,
        )
    except Exception:
        logger.exception("webapp_api: ошибка генерации изображения")
        return web.json_response(
            {"error": "generation_failed", "message": "Не удалось создать изображение."},
            status=502,
        )

    if not isinstance(image_bytes, (bytes, bytearray)) or len(image_bytes) > MAX_GENERATED_IMAGE_BYTES:
        logger.error("webapp_api: генератор вернул недопустимый размер изображения")
        return web.json_response(
            {"error": "generation_failed", "message": "Получено слишком большое изображение."},
            status=502,
        )
    return web.json_response({
        "image_base64": base64.b64encode(image_bytes).decode("ascii"),
        "prompt": prompt,
    })


# ─── Admin API ────────────────────────────────────────────────────────────────

async def _authorize_admin(request: web.Request):
    """Проверяет что запрос от администратора. Возвращает (True, None) или (False, Response)."""
    user_id, err = await _authorize(request)
    if err is not None:
        return False, err
    if user_id != ADMIN_ID:
        return False, web.json_response({"error": "forbidden"}, status=403)
    return True, None


async def api_admin_users(request: web.Request) -> web.Response:
    """GET /api/admin/users — список всех пользователей со статусом и статистикой."""
    ok, err = await _authorize_admin(request)
    if not ok:
        return err
    try:
        limit = int(request.query.get("limit", "200"))
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        return web.json_response({"error": "bad_pagination"}, status=400)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    users = await db.get_all_users_with_stats(limit=limit, offset=offset)
    return web.json_response({
        "ok": True,
        "users": users,
        "limit": limit,
        "offset": offset,
        "has_more": len(users) == limit,
        "default_limits": {
            "chat": DEFAULT_DAILY_AI_LIMIT,
            "image-generation": DEFAULT_DAILY_IMAGE_LIMIT,
        },
    })


async def api_admin_user_stats(request: web.Request) -> web.Response:
    """GET /api/admin/users/{user_id}/stats — детальная статистика пользователя."""
    ok, err = await _authorize_admin(request)
    if not ok:
        return err
    try:
        uid = int(request.match_info["user_id"])
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_user_id"}, status=400)
    if uid <= 0 or uid > 2**63 - 1:
        return web.json_response({"error": "bad_user_id"}, status=400)
    stats = await db.get_user_stats(uid)
    return web.json_response({"ok": True, "user_id": uid, "stats": stats})


async def api_admin_action(request: web.Request) -> web.Response:
    """POST /api/admin/action — approve/reject/revoke пользователя."""
    ok, err = await _authorize_admin(request)
    if not ok:
        return err
    try:
        payload = await _read_json_object(request, ADMIN_BODY_LIMIT)
    except (web.HTTPBadRequest, web.HTTPRequestEntityTooLarge):
        return web.json_response({"error": "bad_request"}, status=400)

    action = payload.get("action")  # "approve" | "reject" | "revoke"
    uid = payload.get("user_id")
    if action not in {"approve", "reject", "revoke"}:
        return web.json_response({"error": "unknown action"}, status=400)
    if isinstance(uid, bool):
        return web.json_response({"error": "missing fields"}, status=400)

    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_user_id"}, status=400)
    if uid <= 0 or uid > 2**63 - 1:
        return web.json_response({"error": "bad_user_id"}, status=400)
    if uid == ADMIN_ID and action in {"reject", "revoke"}:
        return web.json_response(
            {"error": "cannot_revoke_admin"},
            status=400,
        )

    if action == "approve":
        await db.approve_user(uid)
    else:
        await db.reject_user(uid)

    logger.info("admin action=%s user_id=%s", action, uid)
    return web.json_response({"ok": True, "action": action, "user_id": uid})


async def api_admin_limit(request: web.Request) -> web.Response:
    """POST /api/admin/limit — сохранить или удалить дневной лимит модели."""
    ok, err = await _authorize_admin(request)
    if not ok:
        return err
    try:
        payload = await _read_json_object(request, ADMIN_BODY_LIMIT)
    except (web.HTTPBadRequest, web.HTTPRequestEntityTooLarge):
        return web.json_response({"error": "bad_request"}, status=400)

    uid = payload.get("user_id")
    model = payload.get("model")
    raw_limit = payload.get("daily_limit")
    if isinstance(uid, bool) or not isinstance(model, str):
        return web.json_response({"error": "bad_request"}, status=400)
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_user_id"}, status=400)
    if uid <= 0 or uid > 2**63 - 1:
        return web.json_response({"error": "bad_user_id"}, status=400)
    allowed_limit_models = ALLOWED_MODELS | {"image-generation"}
    if model not in allowed_limit_models:
        return web.json_response({"error": "invalid_model"}, status=400)

    if isinstance(raw_limit, bool):
        return web.json_response({"error": "bad_limit"}, status=400)
    if raw_limit is None or raw_limit == "" or raw_limit == 0 or raw_limit == "0":
        checked_limit = None
    else:
        try:
            checked_limit = int(raw_limit)
        except (TypeError, ValueError):
            return web.json_response({"error": "bad_limit"}, status=400)
        if checked_limit < 1 or checked_limit > 10000:
            return web.json_response({"error": "bad_limit"}, status=400)

    await db.set_user_model_limit(uid, model, checked_limit)
    logger.info(
        "admin model limit updated user_id=%s model=%s configured=%s",
        uid,
        model,
        checked_limit is not None,
    )
    return web.json_response({
        "ok": True,
        "user_id": uid,
        "model": model,
        "daily_limit": checked_limit,
    })


def setup_webapp_routes(app: web.Application) -> None:
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/auth/code", api_auth_code)
    app.router.add_post("/api/auth/logout", api_auth_logout)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/image", api_image)
    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_get("/api/admin/users/{user_id}/stats", api_admin_user_stats)
    app.router.add_post("/api/admin/action", api_admin_action)
    app.router.add_post("/api/admin/limit", api_admin_limit)
