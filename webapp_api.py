"""
HTTP API для мини-аппа (tg-ai-miniapp.html).

Никакой новой логики обращения к ИИ-провайдерам здесь нет — все запросы
идут через уже существующие функции call_ai() и generate_image() из
handlers.chat_handler / handlers.image_handler, чтобы бот и мини-апп
всегда использовали одну и ту же бизнес-логику и единый список моделей.

Авторизация: мини-апп — это Telegram WebApp, поэтому пользователь
идентифицируется через initData, которую передаёт Telegram. initData
подписана HMAC-SHA256 по секрету, производному от BOT_TOKEN, поэтому
её подлинность можно проверить без отдельной системы логина.

Проверенный user_id сверяется с той же таблицей users (approved/pending/
rejected), которой пользуется бот, — то есть доступ к мини-аппу есть
только у тех, кому admin выдал доступ в самом боте.

"""

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
from urllib.parse import parse_qsl
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
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Сколько секунд считаем initData валидной (защита от replay).
INIT_DATA_MAX_AGE = 24 * 60 * 60  # 24 часа

# ------------------ Вход по коду (для сайта вне Telegram) ------------------
# Код одноразовый, короткоживущий; после успешного ввода выдаётся долгоживущий
# токен веб-сессии (Bearer), который сайт хранит у себя (localStorage) и
# использует вместо initData для всех дальнейших запросов.
LOGIN_CODE_TTL = 10 * 60              # код действителен 10 минут
WEB_SESSION_TTL = 30 * 24 * 60 * 60   # веб-сессия живёт 30 дней

# Простая защита от подбора кода: не больше N попыток с одного IP за окно времени.
_CODE_RATE_LIMIT = 8
_CODE_RATE_WINDOW = 10 * 60
_code_attempts: dict[str, list[float]] = {}

def _client_ip(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote or "unknown"

def _rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _code_attempts.get(ip, []) if now - t < _CODE_RATE_WINDOW]
    attempts.append(now)
    _code_attempts[ip] = attempts
    # чистим старые ключи, чтобы словарь не рос бесконечно
    if len(_code_attempts) > 5000:
        for k in list(_code_attempts.keys()):
            if not _code_attempts[k]:
                _code_attempts.pop(k, None)
    return len(attempts) > _CODE_RATE_LIMIT

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

    if not init_data:
        logger.warning("webapp_api: запрос без initData")
        return None

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return None

    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    auth_date = data.get("auth_date")
    if auth_date:
        try:
            if time.time() - int(auth_date) > INIT_DATA_MAX_AGE:
                logger.warning("webapp_api: initData просрочена")
                return None
        except ValueError:
            pass

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        logger.warning("webapp_api: подпись initData не совпала")
        return None

    user_raw = data.get("user")
    if user_raw:
        try:
            data["user"] = json.loads(user_raw)
        except Exception:
            data["user"] = None

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
    - 'Authorization: Bearer <token>'  — обычный сайт, вход по коду из бота.
    Устаревший заголовок X-Telegram-Init-Data оставлен для обратной совместимости.
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
        if not user_id:
            return None, web.json_response({"error": "unauthorized"}, status=401)
        return await _check_access_status(user_id)

    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        user_id = await db.get_web_session_user(token) if token else None
        if not user_id:
            return None, web.json_response(
                {"error": "unauthorized", "message": "Сессия истекла. Войдите заново по коду из бота."},
                status=401,
            )
        return await _check_access_status(user_id)

    legacy_init_data = request.headers.get("X-Telegram-Init-Data", "")
    if legacy_init_data:
        parsed = check_init_data(legacy_init_data)
        if parsed and parsed.get("user"):
            user_id = parsed["user"].get("id")
            if user_id:
                return await _check_access_status(user_id)

    return None, web.json_response(
        {"error": "unauthorized", "message": "Не удалось подтвердить пользователя."},
        status=401,
    )

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
    return web.json_response({"ok": True, "user_id": user_id, "is_admin": user_id == ADMIN_ID})

async def api_chat(request: web.Request) -> web.Response:
    user_id, err = await _authorize(request)
    if err is not None:
        logger.info("webapp_api: /api/chat — отказ в доступе")
        return err

    if not isinstance(user_id, int):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)

    user_text = (payload.get("message") or "").strip()
    model_id = payload.get("model") or "freemodel/gpt-5.5"
    history = payload.get("history") or []
    attachments = payload.get("attachments") or []

    if not user_text and not attachments:
        return web.json_response({"error": "empty_message"}, status=400)

    if not user_text and attachments:
        user_text = "Вложения"

    intent = detect_intent(user_text)

    # === ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЯ ===
    if intent == "image":
        try:
            image_bytes = await generate_image(user_text)
        except Exception as e:
            logger.exception("webapp_api: ошибка генерации изображения")
            return web.json_response({"error": "generation_failed", "message": str(e)}, status=502)

        import base64 as b64
        return web.json_response({
            "intent": "image",
            "image_base64": b64.b64encode(image_bytes).decode("ascii"),
            "prompt": user_text,
        })

    # === ОБЫЧНЫЙ ЧАТ ===
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += trim_history(list(history))

    image_attachments = [
        a for a in attachments
        if a.get("dataUrl") and a.get("type", "").startswith("image/")
    ]
    file_attachments = [
        a for a in attachments
        if a.get("dataUrl") and not a.get("type", "").startswith("image/")
    ]

    if image_attachments or file_attachments:
        # Build multi-part content
        content_parts = []

        # Add user text first — only if no file attachments (for files, text is embedded in prompt)
        has_files = bool([a for a in attachments if a.get("dataUrl") and not a.get("type", "").startswith("image/")])
        if user_text and user_text != "Вложения" and not has_files:
            content_parts.append({"type": "text", "text": user_text})

        # Add images via vision
        for img in image_attachments:
            data_url = img["dataUrl"]
            # Extract base64 data from data URL (data:image/jpeg;base64,...)
            if "," in data_url:
                header, b64data = data_url.split(",", 1)
                media_type = header.split(";")[0].replace("data:", "") or "image/jpeg"
            else:
                b64data = data_url
                media_type = img.get("type", "image/jpeg")
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{b64data}"}
            })

        # Add file contents as text blocks (same logic as bot's extract_text_from_bytes)
        for fa in file_attachments:
            import base64 as _b64
            fname = fa.get("name", "file")
            ftype = fa.get("type", "")
            data_url = fa.get("dataUrl", "")
            try:
                if "," in data_url:
                    _, b64data = data_url.split(",", 1)
                else:
                    b64data = data_url
                raw_bytes = _b64.b64decode(b64data)
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
                # Trim if too large (same as bot: MAX_FILE_CHARS = 120_000)
                MAX_FILE_CHARS = 120_000
                if len(file_text) > MAX_FILE_CHARS:
                    file_text = file_text[:MAX_FILE_CHARS] + f"\n\n[...обрезано, файл больше {MAX_FILE_CHARS} символов]"
                # Format same as bot
                if user_text and user_text != "Вложения":
                    prompt_text = f"{user_text}\n\n--- Содержимое файла {fname} ---\n{file_text}"
                else:
                    prompt_text = f"Проанализируй содержимое файла {fname}:\n\n{file_text}"
                content_parts.append({"type": "text", "text": prompt_text})
            except Exception as e:
                logger.warning("Failed to decode file attachment %s: %r", fname, e)
                content_parts.append({"type": "text", "text": f"[Файл: {fname}] — не удалось прочитать"})

        if not content_parts:
            content_parts.append({"type": "text", "text": user_text})

        user_content = content_parts
    else:
        user_content = user_text

    messages.append({"role": "user", "content": user_content})

    try:
        reply, debug = await call_ai(model_id, messages)
    except Exception as e:
        logger.exception("webapp_api: ошибка call_ai")
        return web.json_response({"error": "ai_failed", "message": str(e)}, status=502)

    # Логируем запрос в статистику
    await db.log_request(user_id, model_id, source="webapp")

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
    Ответ: {"ok": true, "token": "...", "user_id": ..., "is_admin": bool}.
    Токен дальше передаётся как 'Authorization: Bearer <token>'.
    """
    ip = _client_ip(request)
    if _rate_limited(ip):
        logger.warning("webapp_api: /api/auth/code — превышен лимит попыток, ip=%s", ip)
        return web.json_response(
            {"error": "rate_limited", "message": "Слишком много попыток. Подождите несколько минут."},
            status=429,
        )

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)

    code = (payload.get("code") or "").strip().upper()
    if not code:
        return web.json_response({"error": "empty_code", "message": "Введите код."}, status=400)

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

    return web.json_response({
        "ok": True,
        "token": token,
        "user_id": checked_id,
        "is_admin": checked_id == ADMIN_ID,
        "expires_in": WEB_SESSION_TTL,
    })


async def api_auth_logout(request: web.Request) -> web.Response:
    """POST /api/auth/logout — завершить веб-сессию (актуально только для входа по токену)."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            await db.delete_web_session(token)
    return web.json_response({"ok": True})


async def api_image(request: web.Request) -> web.Response:
    user_id, err = await _authorize(request)
    if err is not None:
        return err

    if not isinstance(user_id, int):
        return web.json_response({"error": "unauthorized"}, status=401)

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        return web.json_response({"error": "empty_prompt"}, status=400)

    try:
        image_bytes = await generate_image(prompt)
    except Exception as e:
        logger.exception("webapp_api: ошибка генерации изображения")
        return web.json_response({"error": "generation_failed", "message": str(e)}, status=502)

    import base64 as b64
    return web.json_response({
        "image_base64": b64.b64encode(image_bytes).decode("ascii"),
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
    users = await db.get_all_users_with_stats()
    return web.json_response({"ok": True, "users": users})


async def api_admin_user_stats(request: web.Request) -> web.Response:
    """GET /api/admin/users/{user_id}/stats — детальная статистика пользователя."""
    ok, err = await _authorize_admin(request)
    if not ok:
        return err
    uid = int(request.match_info["user_id"])
    stats = await db.get_user_stats(uid)
    return web.json_response({"ok": True, "user_id": uid, "stats": stats})


async def api_admin_action(request: web.Request) -> web.Response:
    """POST /api/admin/action — approve/reject/revoke пользователя."""
    ok, err = await _authorize_admin(request)
    if not ok:
        return err
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)

    action = payload.get("action")  # "approve" | "reject" | "revoke"
    uid = payload.get("user_id")
    if not action or not uid:
        return web.json_response({"error": "missing fields"}, status=400)

    uid = int(uid)
    if action == "approve":
        await db.approve_user(uid)
    elif action in ("reject", "revoke"):
        await db.reject_user(uid)
    else:
        return web.json_response({"error": "unknown action"}, status=400)

    logger.info("admin action=%s user_id=%s", action, uid)
    return web.json_response({"ok": True, "action": action, "user_id": uid})


def setup_webapp_routes(app: web.Application) -> None:
    try:
        app._client_max_size = 1024 * 1024 * 30
        logger.info("webapp_api: client_max_size increased to 30MB")
    except Exception as e:
        logger.warning("webapp_api: failed to increase client_max_size: %r", e)

    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/auth/code", api_auth_code)
    app.router.add_post("/api/auth/logout", api_auth_logout)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/image", api_image)
    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_get("/api/admin/users/{user_id}/stats", api_admin_user_stats)
    app.router.add_post("/api/admin/action", api_admin_action)
