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
import logging
import inspect
import re
from urllib.parse import parse_qsl
from aiohttp import web

import database as db

logger = logging.getLogger(__name__)
logger.info("webapp_api module loaded: build=auth-enforced-v5 + file-support")

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

    for kw in FILE_KEYWORDS:
        if kw in t:
            return "file"

    return "chat"

def extract_file_extension(text: str) -> str:
    """Извлекает расширение из запроса: 'в docx', 'py', '.html' и т.д."""
    low = (text or "").lower()
    
    # Ищем "в расширение", "в .расширение", "формате расширение"
    patterns = [
        r'в\s+\.?([a-z0-9]{2,5})(?:\s|$)',  # в docx, в .py, в html
        r'формате\s+\.?([a-z0-9]{2,5})(?:\s|$)',  # формате docx
        r'\.([a-z0-9]{2,5})(?:\s|$)',  # .docx, .html
    ]
    
    for pattern in patterns:
        match = re.search(pattern, low)
        if match:
            ext = match.group(1).lower()
            if ext.isalnum() and 2 <= len(ext) <= 5:
                return ext
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

async def _authorize(request: web.Request) -> tuple[int | None, web.Response | None]:
    auth_header = request.headers.get("Authorization", "")
    init_data = ""
    if auth_header.startswith("tma "):
        init_data = auth_header[4:]
    else:
        init_data = request.headers.get("X-Telegram-Init-Data", "")

    parsed = check_init_data(init_data)
    if not parsed or not parsed.get("user"):
        return None, web.json_response(
            {"error": "unauthorized", "message": "Не удалось подтвердить пользователя Telegram."},
            status=401,
        )

    user_id = parsed["user"].get("id")
    if not user_id:
        return None, web.json_response({"error": "unauthorized"}, status=401)

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
    return web.json_response({"ok": True, "user_id": user_id})

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

def setup_webapp_routes(app: web.Application) -> None:
    try:
        app._client_max_size = 1024 * 1024 * 30
        logger.info("webapp_api: client_max_size increased to 30MB")
    except Exception as e:
        logger.warning("webapp_api: failed to increase client_max_size: %r", e)

    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/image", api_image)
