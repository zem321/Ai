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

import os
import json
import time
import hmac
import hashlib
import logging
from urllib.parse import parse_qsl

from aiohttp import web

import database as db
from handlers.chat_handler import call_ai, SYSTEM_PROMPT, trim_history, make_vision_content, MAX_HISTORY
from handlers.image_handler import generate_image

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Сколько секунд считаем initData валидной (защита от replay).
INIT_DATA_MAX_AGE = 24 * 60 * 60  # 24 часа


# ------------------ Автоопределение режима (чат / картинка) ------------------
#
# Эвристика на ключевых словах: если сообщение явно похоже на просьбу
# нарисовать/сгенерировать изображение — уходим в генерацию картинки,
# иначе — в обычный чат. Без отдельного LLM-вызова, чтобы не тратить
# лишнее время и токены на классификацию каждого сообщения.

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


def detect_intent(text: str) -> str:
    """Возвращает 'image' или 'chat' на основе текста запроса."""
    t = (text or "").strip().lower()
    if not t:
        return "chat"
    for kw in IMAGE_KEYWORDS:
        if kw in t:
            return "image"
    return "chat"


# ------------------ Проверка Telegram WebApp initData ------------------

def _check_init_data(init_data: str) -> dict | None:
    """
    Проверяет подпись initData по алгоритму Telegram.
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

    Возвращает распарсенные данные (включая user) при успехе, иначе None.
    """
    if not BOT_TOKEN:
        logger.error("webapp_api: BOT_TOKEN не задан — все запросы мини-аппа будут отклонены")
        return None

    if not init_data:
        logger.warning("webapp_api: запрос без initData (заголовок Authorization отсутствует/пуст)")
        return None

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True)
    except Exception:
        return None

    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        logger.warning("webapp_api: initData без hash — отклонено")
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
        logger.warning("webapp_api: подпись initData не совпала — отклонено")
        return None

    user_raw = data.get("user")
    if user_raw:
        try:
            data["user"] = json.loads(user_raw)
        except Exception:
            data["user"] = None

    return data


async def _authorize(request: web.Request) -> tuple[int | None, web.Response | None]:
    """
    Извлекает и проверяет initData из заголовка Authorization
    ('tma <initData>', стандарт Telegram), сверяет пользователя с базой
    разрешённых. Возвращает (user_id, None) при успехе либо
    (None, error_response) при отказе.
    """
    auth_header = request.headers.get("Authorization", "")
    init_data = ""
    if auth_header.startswith("tma "):
        init_data = auth_header[4:]
    else:
        # Фоллбек — initData может прийти отдельным заголовком
        init_data = request.headers.get("X-Telegram-Init-Data", "")

    parsed = _check_init_data(init_data)
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

    logger.info("webapp_api: пользователь %s — не найден в базе, нет доступа", user_id)
    return None, web.json_response(
        {"error": "no_access", "message": "Нет доступа. Откройте бота и отправьте /start."},
        status=403,
    )


# ------------------ HTTP-хендлеры ------------------

async def api_me(request: web.Request) -> web.Response:
    """Проверка доступа — мини-апп вызывает это при старте."""
    user_id, err = await _authorize(request)
    if err:
        return err
    return web.json_response({"ok": True, "user_id": user_id})


async def api_chat(request: web.Request) -> web.Response:
    """
    Принимает: {"model": str, "history": [...], "message": str}
    history — предыдущие сообщения в формате [{"role": "user"/"assistant", "content": str}, ...]

    Если по тексту определяется, что пользователь хочет картинку —
    режим переключается на генерацию изображения автоматически и в ответе
    возвращается base64 картинки вместо текста (intent == "image").
    """
    user_id, err = await _authorize(request)
    if err:
        return err

    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "bad_request"}, status=400)

    user_text = (payload.get("message") or "").strip()
    model_id = payload.get("model") or "freemodel/gpt-5.5"
    history = payload.get("history") or []

    if not user_text:
        return web.json_response({"error": "empty_message"}, status=400)

    intent = detect_intent(user_text)

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

    # Обычный чат
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += trim_history(list(history))
    messages.append({"role": "user", "content": user_text})

    try:
        reply, debug = await call_ai(model_id, messages)
    except Exception as e:
        logger.exception("webapp_api: ошибка call_ai")
        return web.json_response({"error": "ai_failed", "message": str(e)}, status=502)

    return web.json_response({
        "intent": "chat",
        "reply": reply,
        "model": debug.get("provider_model") or model_id,
    })


async def api_image(request: web.Request) -> web.Response:
    """Принудительная генерация изображения, минуя автоопределение."""
    user_id, err = await _authorize(request)
    if err:
        return err

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
    """Регистрирует все API-роуты мини-аппа в существующем aiohttp Application."""
    app.router.add_get("/api/me", api_me)
    app.router.add_post("/api/chat", api_chat)
    app.router.add_post("/api/image", api_image)
