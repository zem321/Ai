"""Отдельный сервис (второй Render Web Service) для тяжёлых по памяти
задач — скачивание фото/видео из Telegram и прогон через vision-модели
NVIDIA. Живёт отдельно от основного бота, чтобы не делить с ним 512MB RAM.

Доступ закрыт общим секретом (Bearer-токен), который знают только два
сервиса. Никаких данных о пользователях бота или токене бота этот сервис
не хранит — токен приходит только как часть одноразовой ссылки на файл
Telegram, уже встроенной в присланный URL.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import logging
import os
import random
import socket
import time
from urllib.parse import urlsplit

from aiohttp import web
import aiohttp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("media_worker")

# ─── Конфигурация ───────────────────────────────────────────────────────
SHARED_SECRET = os.environ["MEDIA_WORKER_SECRET"]  # обязателен, без дефолта
NVIDIA_API_KEYS = [
    key.strip()
    for key in os.getenv("NVIDIA_API_KEYS", "").split(",")
    if key.strip()
]
if not NVIDIA_API_KEYS:
    raise RuntimeError("NVIDIA_API_KEYS не задан для media_worker")

NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# Whitelist моделей — даже если MEDIA_WORKER_SECRET утечёт, атакующий не
# сможет прогонять через наши ключи произвольные модели. Значения должны
# совпадать с VISION_BRIDGE_MODEL/VIDEO_MODEL в keyboards.py основного бота.
ALLOWED_MODELS = frozenset(
    m.strip()
    for m in os.getenv(
        "ALLOWED_MODELS", "google/gemma-3-12b-it,google/gemma-4-31b-it"
    ).split(",")
    if m.strip()
)

# Whitelist хостов, с которых worker имеет право что-либо скачивать.
# Ничего, кроме официального домена Telegram для файлов.
ALLOWED_FILE_HOSTS = frozenset({"api.telegram.org"})

MAX_PHOTO_BYTES = int(os.getenv("MAX_PHOTO_BYTES", str(15 * 1024 * 1024)))
MAX_VIDEO_BYTES = int(os.getenv("MAX_VIDEO_BYTES", str(10 * 1024 * 1024)))
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=40)
NVIDIA_TIMEOUT_PHOTO = aiohttp.ClientTimeout(total=45)
NVIDIA_TIMEOUT_VIDEO = aiohttp.ClientTimeout(total=90)

# Не даём телу запроса от основного бота быть больше пары КБ — сюда прилетают
# только ссылка+текст, а не сами байты медиа.
MAX_REQUEST_BODY_BYTES = 32 * 1024


# ─── Авторизация ────────────────────────────────────────────────────────
def _check_auth(request: web.Request) -> bool:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    token = header[len(prefix):]
    # Сравнение постоянного времени, чтобы не утекало через тайминг.
    return hmac.compare_digest(token, SHARED_SECRET)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if request.path == "/health":
        return await handler(request)
    if not _check_auth(request):
        return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return await handler(request)


# ─── Защита от SSRF: разрешаем скачивать только с api.telegram.org ──────
def _validate_telegram_file_url(url: str) -> None:
    """Бросает ValueError, если URL — не легитимная ссылка на файл Telegram.

    Даже если MEDIA_WORKER_SECRET утечёт, это не даёт превратить worker
    в открытый SSRF-прокси: разрешён только https, только конкретный хост,
    только путь вида /file/bot<token>/..., без user-info в URL.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("недопустимая схема URL")
    if parts.username or parts.password:
        raise ValueError("недопустимый URL (userinfo)")
    if parts.hostname not in ALLOWED_FILE_HOSTS:
        raise ValueError("недопустимый хост")
    if parts.port not in (None, 443):
        raise ValueError("недопустимый порт")
    if not parts.path.startswith("/file/bot"):
        raise ValueError("недопустимый путь")

    # DNS-резолвим и убеждаемся, что это не приватный/loopback/link-local
    # адрес (страховка от DNS rebinding на разрешённый хостнейм).
    try:
        infos = socket.getaddrinfo(parts.hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"не удалось разрешить хост: {exc}") from exc
    for family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise ValueError("хост резолвится в приватный/служебный адрес")


# ─── Скачивание файла с ограничением по размеру ─────────────────────────
async def _download_with_cap(session: aiohttp.ClientSession, url: str, max_bytes: int) -> bytes:
    _validate_telegram_file_url(url)
    async with session.get(
        url, timeout=DOWNLOAD_TIMEOUT, allow_redirects=False
    ) as resp:
        if resp.status in (301, 302, 303, 307, 308):
            # Официальный API Telegram для файлов редиректы не делает —
            # если вдруг пришёл редирект, не идём по нему, а считаем это
            # подозрительным и отказываем.
            raise web.HTTPBadGateway(reason="unexpected redirect from file host")
        if resp.status != 200:
            raise web.HTTPBadGateway(
                reason=f"Не удалось скачать файл (HTTP {resp.status})"
            )
        content_length = resp.headers.get("Content-Length")
        if content_length is not None and int(content_length) > max_bytes:
            raise web.HTTPRequestEntityTooLarge(
                max_size=max_bytes, actual_size=int(content_length)
            )
        chunks = []
        total = 0
        async for chunk in resp.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=max_bytes, actual_size=total
                )
            chunks.append(chunk)
        return b"".join(chunks)


# ─── Вызов NVIDIA vision API с перебором ключей ─────────────────────────
async def _call_nvidia_vision(
    session: aiohttp.ClientSession,
    *,
    model: str,
    data_url: str,
    content_type: str,  # "image_url" или "video_url"
    prompt: str,
    timeout: aiohttp.ClientTimeout,
) -> str:
    keys = NVIDIA_API_KEYS[:]
    random.shuffle(keys)
    last_error: Exception | None = None
    for key in keys[:3]:  # не больше 3 попыток на разных ключах
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {content_type: {"url": data_url}, "type": content_type},
                    ],
                }
            ],
            "max_tokens": 1024,
        }
        try:
            async with session.post(
                NVIDIA_CHAT_URL, json=payload, headers=headers, timeout=timeout
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
                if resp.status in (401, 403, 429):
                    last_error = RuntimeError(f"NVIDIA HTTP {resp.status}")
                    continue
                text = await resp.text()
                raise web.HTTPBadGateway(reason=f"NVIDIA HTTP {resp.status}: {text[:300]}")
        except asyncio.TimeoutError as exc:
            last_error = exc
            continue
    raise web.HTTPBadGateway(reason=f"Все ключи NVIDIA не сработали: {last_error}")


# ─── Основной endpoint ──────────────────────────────────────────────────
async def analyze(request: web.Request) -> web.Response:
    if request.content_length and request.content_length > MAX_REQUEST_BODY_BYTES:
        return web.json_response({"ok": False, "error": "request body too large"}, status=413)

    body = await request.json()
    kind = body.get("kind")
    file_url = body.get("file_url")
    prompt = (body.get("prompt") or "Опиши, что на этом медиафайле.").strip()
    model = body.get("model")

    if kind not in ("photo", "video") or not file_url or not model:
        return web.json_response({"ok": False, "error": "bad request"}, status=400)
    if model not in ALLOWED_MODELS:
        return web.json_response({"ok": False, "error": "model not allowed"}, status=400)

    max_bytes = MAX_PHOTO_BYTES if kind == "photo" else MAX_VIDEO_BYTES
    content_type = "image_url" if kind == "photo" else "video_url"
    nvidia_timeout = NVIDIA_TIMEOUT_PHOTO if kind == "photo" else NVIDIA_TIMEOUT_VIDEO
    mime_default = "image/jpeg" if kind == "photo" else "video/mp4"
    mime = body.get("declared_mime") or mime_default

    started = time.monotonic()
    async with aiohttp.ClientSession() as session:
        try:
            raw_bytes = await _download_with_cap(session, file_url, max_bytes)
        except ValueError as exc:
            logger.warning("Отклонён file_url: %s", exc)
            return web.json_response({"ok": False, "error": "invalid file_url"}, status=400)
        except web.HTTPRequestEntityTooLarge:
            return web.json_response(
                {"ok": False, "error": f"файл больше {max_bytes // (1024 * 1024)}MB"},
                status=413,
            )
        except web.HTTPBadGateway as exc:
            return web.json_response({"ok": False, "error": exc.reason}, status=502)

        data_url = f"data:{mime};base64,{base64.b64encode(raw_bytes).decode()}"

        try:
            text = await _call_nvidia_vision(
                session,
                model=model,
                data_url=data_url,
                content_type=content_type,
                prompt=prompt,
                timeout=nvidia_timeout,
            )
        except web.HTTPBadGateway as exc:
            return web.json_response({"ok": False, "error": exc.reason}, status=502)

    latency_ms = int((time.monotonic() - started) * 1000)
    return web.json_response({"ok": True, "text": text, "model": model, "latency_ms": latency_ms})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware], client_max_size=MAX_REQUEST_BODY_BYTES)
    app.router.add_get("/health", health)
    app.router.add_post("/v1/analyze", analyze)
    return app


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    web.run_app(create_app(), host="0.0.0.0", port=port)
