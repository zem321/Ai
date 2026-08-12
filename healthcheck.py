"""
Полная проверка всех связок ключ×модель у всех AI-провайдеров,
плюс диагностика DNS/TCP/TLS/HTTP и статусы UptimeRobot.

Не участвует в обычном пользовательском пути запроса.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiohttp

from keyboards import MODELS
from provider_keys import PROVIDER_KEY_ENV_NAMES, configured_provider_keys

logger = logging.getLogger(__name__)

NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
GEMINI_CHAT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
)
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"

UPTIMEROBOT_API_KEY = os.getenv("UPTIMEROBOT_API_KEY", "").strip()
UPTIMEROBOT_API_URL = "https://api.uptimerobot.com/v2/getMonitors"

# Отдельные лимиты диагностики.
_DNS_TIMEOUT = float(os.getenv("HEALTHCHECK_DNS_TIMEOUT", "5"))
_TCP_TIMEOUT = float(os.getenv("HEALTHCHECK_TCP_TIMEOUT", "5"))
_TLS_TIMEOUT = float(os.getenv("HEALTHCHECK_TLS_TIMEOUT", "10"))
_HTTP_RESPONSE_TIMEOUT = float(
    os.getenv("HEALTHCHECK_HTTP_RESPONSE_TIMEOUT", "20")
)

_UPTIMEROBOT_TIMEOUT = float(
    os.getenv("HEALTHCHECK_UPTIMEROBOT_TIMEOUT", "15")
)

_MAX_CONCURRENCY = int(
    os.getenv("HEALTHCHECK_MAX_CONCURRENCY", "5")
)

_MAX_ERROR_LENGTH = 500


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------

def _provider_for_model(model_id: str) -> str:
    if model_id.startswith("gemini/"):
        return "gemini"

    if model_id.startswith("groq/"):
        return "groq"

    return "nvidia"


def _raw_model_name(provider: str, model_id: str) -> str:
    if provider == "gemini":
        return model_id.replace("gemini/", "", 1)

    if provider == "groq":
        return model_id.removeprefix("groq/")

    return model_id


def _build_request(
    provider: str,
    secret: str,
    raw_model: str,
) -> tuple[str, dict[str, str], dict]:

    headers = {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "OracleAi-healthcheck/1.0",
    }

    payload = {
        "model": raw_model,
        "messages": [
            {
                "role": "user",
                "content": "ping",
            }
        ],
        "temperature": 0,
    }

    if provider == "groq":
        payload["max_completion_tokens"] = 1
        url = GROQ_CHAT_URL

    elif provider == "gemini":
        payload["max_tokens"] = 1
        url = GEMINI_CHAT_URL

    else:
        payload["max_tokens"] = 1
        url = NVIDIA_CHAT_URL

    return url, headers, payload


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass
class KeyModelResult:
    provider: str
    key_index: int
    model_id: str
    ok: bool
    status: int | None
    latency_ms: int | None
    error: str | None = None


@dataclass
class HealthReport:
    results: list[KeyModelResult] = field(default_factory=list)

    uptimerobot: list[dict] | None = None
    uptimerobot_error: str | None = None

    @property
    def failures(self) -> list[KeyModelResult]:
        return [
            result
            for result in self.results
            if not result.ok
        ]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _short_error(value: str | None) -> str:
    if not value:
        return ""

    value = str(value)
    value = value.replace("\x00", "")
    value = value.replace("\r", " ")
    value = value.replace("\n", " ")

    if len(value) > _MAX_ERROR_LENGTH:
        value = value[:_MAX_ERROR_LENGTH] + "…"

    return html.escape(value)


def _http_status_label(status: int) -> str:
    if status == 401:
        return "HTTP 401"

    if status == 403:
        return "HTTP 403"

    if status == 404:
        return "HTTP 404"

    if status == 429:
        return "HTTP 429"

    if 500 <= status <= 599:
        return "HTTP 5xx"

    return f"HTTP {status}"


def _format_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "неизвестно"

    if seconds < 0:
        seconds = 0

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days} д")

    if hours:
        parts.append(f"{hours} ч")

    if minutes:
        parts.append(f"{minutes} мин")

    if seconds or not parts:
        parts.append(f"{seconds} сек")

    return " ".join(parts)


def _format_timestamp(timestamp) -> str:
    try:
        timestamp = int(timestamp)
        dt = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )

        return dt.strftime(
            "%d.%m.%Y %H:%M:%S UTC"
        )

    except (TypeError, ValueError, OSError):
        return "неизвестно"


def _format_uptime(value) -> str:
    if value is None:
        return "неизвестно"

    try:
        return f"{float(value):.3f}%"
    except (TypeError, ValueError):
        return html.escape(str(value))


# ---------------------------------------------------------------------------
# DNS
# ---------------------------------------------------------------------------

async def _dns_check(
    host: str,
) -> tuple[list[tuple], int]:

    started = time.monotonic()

    loop = asyncio.get_running_loop()

    try:
        addresses = await asyncio.wait_for(
            loop.getaddrinfo(
                host,
                443,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
            timeout=_DNS_TIMEOUT,
        )

    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            "DNS timeout"
        ) from exc

    except socket.gaierror as exc:
        raise OSError(
            f"DNS error: {exc}"
        ) from exc

    elapsed_ms = int(
        (time.monotonic() - started) * 1000
    )

    unique = []
    seen = set()

    for item in addresses:
        sockaddr = item[4]

        if sockaddr in seen:
            continue

        seen.add(sockaddr)
        unique.append(item)

    if not unique:
        raise OSError(
            "DNS error: no addresses"
        )

    return unique, elapsed_ms


# ---------------------------------------------------------------------------
# TCP
# ---------------------------------------------------------------------------

async def _tcp_check(
    addresses: list[tuple],
) -> int:

    started = time.monotonic()

    last_error: BaseException | None = None

    for family, socktype, proto, _, sockaddr in addresses:

        writer = None

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=sockaddr[0],
                    port=sockaddr[1],
                    family=family,
                    proto=proto,
                ),
                timeout=_TCP_TIMEOUT,
            )

            elapsed_ms = int(
                (time.monotonic() - started) * 1000
            )

            return elapsed_ms

        except asyncio.TimeoutError as exc:
            last_error = exc

        except OSError as exc:
            last_error = exc

        finally:
            if writer is not None:
                writer.close()

                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    if isinstance(
        last_error,
        asyncio.TimeoutError,
    ):
        raise TimeoutError(
            "TCP connect timeout"
        )

    raise ConnectionError(
        f"TCP connect error: {last_error}"
    )


# ---------------------------------------------------------------------------
# TLS
# ---------------------------------------------------------------------------

async def _tls_check(
    addresses: list[tuple],
    host: str,
) -> int:

    started = time.monotonic()

    context = ssl.create_default_context()

    last_error: BaseException | None = None

    for family, socktype, proto, _, sockaddr in addresses:

        writer = None

        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host=sockaddr[0],
                    port=sockaddr[1],
                    family=family,
                    proto=proto,
                    ssl=context,
                    server_hostname=host,
                ),
                timeout=_TLS_TIMEOUT,
            )

            elapsed_ms = int(
                (time.monotonic() - started) * 1000
            )

            return elapsed_ms

        except asyncio.TimeoutError as exc:
            last_error = exc

        except ssl.SSLError as exc:
            raise ssl.SSLError(
                f"TLS error: {exc}"
            ) from exc

        except OSError as exc:
            last_error = exc

        finally:
            if writer is not None:
                writer.close()

                try:
                    await writer.wait_closed()
                except Exception:
                    pass

    if isinstance(
        last_error,
        asyncio.TimeoutError,
    ):
        raise TimeoutError(
            "TLS timeout"
        )

    raise ssl.SSLError(
        f"TLS error: {last_error}"
    )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

async def _http_check(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict[str, str],
    payload: dict,
) -> tuple[int, str, int]:

    started = time.monotonic()

    timeout = aiohttp.ClientTimeout(
        total=None,
        connect=_TCP_TIMEOUT + _TLS_TIMEOUT,
        sock_connect=_TCP_TIMEOUT + _TLS_TIMEOUT,
        sock_read=_HTTP_RESPONSE_TIMEOUT,
    )

    try:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        ) as response:

            # Важно: timeout ожидания HTTP-ответа контролируется
            # aiohttp sock_read.
            body = await response.text()

            elapsed_ms = int(
                (time.monotonic() - started) * 1000
            )

            return (
                response.status,
                body[:_MAX_ERROR_LENGTH],
                elapsed_ms,
            )

    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            "HTTP response timeout"
        ) from exc


# ---------------------------------------------------------------------------
# Single key × model check
# ---------------------------------------------------------------------------

async def _check_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    provider: str,
    key_index: int,
    secret: str,
    model_id: str,
) -> KeyModelResult:

    raw_model = _raw_model_name(
        provider,
        model_id,
    )

    url, headers, payload = _build_request(
        provider,
        secret,
        raw_model,
    )

    started = time.monotonic()

    async with sem:

        try:
            parsed = urlsplit(url)

            host = parsed.hostname

            if not host:
                raise OSError(
                    "DNS error: invalid host"
                )

            # ---------------------------------------------------------
            # DNS
            # ---------------------------------------------------------

            try:
                addresses, _ = await _dns_check(
                    host
                )

            except TimeoutError:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error="DNS timeout",
                )

            except Exception as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=f"DNS error: {_short_error(str(exc))}",
                )

            # ---------------------------------------------------------
            # TCP
            # ---------------------------------------------------------

            try:
                await _tcp_check(
                    addresses
                )

            except TimeoutError:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error="TCP connect timeout",
                )

            except Exception as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        "TCP connect error: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            # ---------------------------------------------------------
            # TLS
            # ---------------------------------------------------------

            try:
                await _tls_check(
                    addresses,
                    host,
                )

            except TimeoutError:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error="TLS timeout",
                )

            except ssl.SSLError as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        "TLS error: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            except Exception as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        "TLS error: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            # ---------------------------------------------------------
            # HTTP
            # ---------------------------------------------------------

            try:
                status, body, _ = await _http_check(
                    session,
                    url,
                    headers,
                    payload,
                )

            except TimeoutError:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error="HTTP response timeout",
                )

            except aiohttp.ClientConnectorCertificateError as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        "TLS certificate error: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            except aiohttp.ClientConnectionResetError as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        "TCP connection reset: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            except aiohttp.ClientConnectorError as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        "TCP connection error: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            except Exception as exc:
                elapsed_ms = int(
                    (time.monotonic() - started) * 1000
                )

                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=False,
                    status=None,
                    latency_ms=elapsed_ms,
                    error=(
                        f"{type(exc).__name__}: "
                        f"{_short_error(str(exc))}"
                    ),
                )

            elapsed_ms = int(
                (time.monotonic() - started) * 1000
            )

            # 2xx — успех.
            ok = 200 <= status < 300

            if ok:
                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=True,
                    status=status,
                    latency_ms=elapsed_ms,
                )

            # Любой HTTP-код >= 300 — проблема health-check.
            return KeyModelResult(
                provider=provider,
                key_index=key_index,
                model_id=model_id,
                ok=False,
                status=status,
                latency_ms=elapsed_ms,
                error=body,
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            elapsed_ms = int(
                (time.monotonic() - started) * 1000
            )

            return KeyModelResult(
                provider=provider,
                key_index=key_index,
                model_id=model_id,
                ok=False,
                status=None,
                latency_ms=elapsed_ms,
                error=(
                    f"{type(exc).__name__}: "
                    f"{_short_error(str(exc))}"
                ),
            )


# ---------------------------------------------------------------------------
# Main health-check
# ---------------------------------------------------------------------------

async def run_full_healthcheck() -> HealthReport:
    """
    Проверяет каждую связку ключ×модель напрямую,
    в обход ротации/карантина БД.
    """

    sem = asyncio.Semaphore(
        _MAX_CONCURRENCY
    )

    tasks = []

    timeout = aiohttp.ClientTimeout(
        total=None
    )

    connector = aiohttp.TCPConnector(
        limit=_MAX_CONCURRENCY,
        ttl_dns_cache=60,
    )

    async with aiohttp.ClientSession(
        timeout=timeout,
        connector=connector,
    ) as session:

        for provider in PROVIDER_KEY_ENV_NAMES:

            secrets = configured_provider_keys(
                provider
            )

            provider_models = [
                model
                for model in MODELS
                if _provider_for_model(model) == provider
            ]

            for key_index, secret in enumerate(
                secrets,
                start=1,
            ):

                for model_id in provider_models:

                    tasks.append(
                        _check_one(
                            session,
                            sem,
                            provider,
                            key_index,
                            secret,
                            model_id,
                        )
                    )

        results = (
            await asyncio.gather(*tasks)
            if tasks
            else []
        )

    report = HealthReport(
        results=list(results)
    )

    await _attach_uptimerobot(
        report
    )

    return report


# ---------------------------------------------------------------------------
# UptimeRobot
# ---------------------------------------------------------------------------

async def _attach_uptimerobot(
    report: HealthReport,
) -> None:

    if not UPTIMEROBOT_API_KEY:
        return

    try:
        timeout = aiohttp.ClientTimeout(
            total=_UPTIMEROBOT_TIMEOUT,
            connect=5,
            sock_connect=5,
            sock_read=10,
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                UPTIMEROBOT_API_URL,
                data={
                    "api_key": UPTIMEROBOT_API_KEY,
                    "format": "json",

                    # История событий.
                    "logs": "1",
                    "logs_limit": "50",

                    # Uptime за всё время существования
                    # монитора.
                    "all_time_uptime_ratio": "1",
                    "all_time_uptime_durations": "1",
                },
                headers={
                    "Content-Type": (
                        "application/x-www-form-urlencoded"
                    ),
                    "Cache-Control": "no-cache",
                },
            ) as resp:

                data = await resp.json(
                    content_type=None
                )

        if data.get("stat") != "ok":

            report.uptimerobot_error = _short_error(
                str(
                    data.get(
                        "error",
                        "unknown error",
                    )
                )
            )

            return

        report.uptimerobot = []

        for monitor in data.get(
            "monitors",
            [],
        ):

            report.uptimerobot.append(
                {
                    "name": monitor.get(
                        "friendly_name"
                    ),
                    "status": monitor.get(
                        "status"
                    ),
                    "url": monitor.get(
                        "url"
                    ),
                    "uptime_ratio": monitor.get(
                        "all_time_uptime_ratio"
                    ),
                    "uptime_durations": monitor.get(
                        "all_time_uptime_durations"
                    ),
                    "logs": monitor.get(
                        "logs"
                    ) or [],
                }
            )

    except asyncio.CancelledError:
        raise

    except Exception as exc:

        report.uptimerobot_error = (
            f"{type(exc).__name__}: "
            f"{_short_error(str(exc))}"
        )

        logger.exception(
            "Не удалось получить статусы UptimeRobot"
        )


# ---------------------------------------------------------------------------
# UptimeRobot formatting
# ---------------------------------------------------------------------------

_UPTIMEROBOT_STATUS_LABELS = {
    0: "paused",
    1: "not checked yet",
    2: "up",
    8: "seems down",
    9: "down",
}


def _format_uptimerobot_monitor(
    monitor: dict,
) -> list[str]:

    name = monitor.get(
        "name"
    ) or "Без названия"

    name = html.escape(
        str(name)
    )

    raw_status = monitor.get(
        "status"
    )

    try:
        status = int(
            raw_status
        )
    except (TypeError, ValueError):
        status = -1

    status_label = _UPTIMEROBOT_STATUS_LABELS.get(
        status,
        str(raw_status),
    )

    icon = (
        "✅"
        if status == 2
        else "❌"
    )

    uptime = _format_uptime(
        monitor.get(
            "uptime_ratio"
        )
    )

    logs = monitor.get(
        "logs"
    ) or []

    # type=1 — DOWN.
    incidents = []

    for log in logs:

        try:
            log_type = int(
                log.get("type")
            )
        except (TypeError, ValueError):
            continue

        if log_type == 1:
            incidents.append(
                log
            )

    lines = [
        f"  {icon} {name}",
        f"     Статус: {status_label}",
        f"     Uptime: {uptime}",
        f"     Инцидентов: {len(incidents)}",
    ]

    if incidents:

        latest = incidents[0]

        lines.append(
            "     Последний: "
            f"{_format_timestamp(latest.get('datetime'))}"
        )

        lines.append(
            "     Длительность: "
            f"{_format_duration(latest.get('duration'))}"
        )

        reason = latest.get(
            "reason"
        )

        if reason:

            lines.append(
                "     Причина: "
                f"{_short_error(str(reason))}"
            )

    return lines


# ---------------------------------------------------------------------------
# Final Telegram report
# ---------------------------------------------------------------------------

def format_report(
    report: HealthReport,
) -> str:

    lines: list[str] = []

    total = len(
        report.results
    )

    failed = report.failures

    lines.append(
        "<b>Health-check провайдеров</b>"
    )

    lines.append(
        f"Проверено связок ключ×модель: {total}"
    )

    lines.append(
        f"Проблем: {len(failed)}"
    )

    if not failed:

        lines.append(
            ""
        )

        lines.append(
            "✅ Все ключи и модели отвечают."
        )

    else:

        by_provider: dict[
            str,
            list[KeyModelResult],
        ] = {}

        for result in failed:

            by_provider.setdefault(
                result.provider,
                [],
            ).append(
                result
            )

        for provider, items in by_provider.items():

            lines.append(
                ""
            )

            lines.append(
                f"<b>{html.escape(provider)}</b>:"
            )

            for result in items:

                model = html.escape(
                    result.model_id
                )

                if result.status is not None:

                    detail = _http_status_label(
                        result.status
                    )

                else:

                    detail = (
                        result.error
                        or "нет ответа"
                    )

                line = (
                    f"  ❌ ключ #{result.key_index} "
                    f"× <code>{model}</code>\n"
                    f"     → {detail}"
                )

                if result.latency_ms is not None:

                    line += (
                        f" ({result.latency_ms} мс)"
                    )

                # Тело HTTP-ошибки.
                if (
                    result.status is not None
                    and result.error
                ):

                    body = _short_error(
                        result.error
                    )

                    if body:

                        line += (
                            f"\n       {body}"
                        )

                lines.append(
                    line
                )

    # ------------------------------------------------------------------
    # UptimeRobot
    # ------------------------------------------------------------------

    if report.uptimerobot_error:

        lines.append(
            ""
        )

        lines.append(
            "⚠️ UptimeRobot: "
            f"{report.uptimerobot_error}"
        )

    elif report.uptimerobot is not None:

        lines.append(
            ""
        )

        lines.append(
            "<b>UptimeRobot</b>: "
            f"{len(report.uptimerobot)} монитор(ов)"
        )

        for monitor in report.uptimerobot:

            lines.extend(
                _format_uptimerobot_monitor(
                    monitor
                )
            )

    return "\n".join(
        lines
    )
