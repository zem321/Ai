from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape

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

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25)
_MAX_CONCURRENCY = 5


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


def _build_request(provider: str, secret: str, raw_model: str) -> tuple[str, dict, dict]:
    headers = {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    payload = {
        "model": raw_model,
        "messages": [{"role": "user", "content": "ping"}],
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
        return [r for r in self.results if not r.ok]


async def _check_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    provider: str,
    key_index: int,
    secret: str,
    model_id: str,
) -> KeyModelResult:
    raw_model = _raw_model_name(provider, model_id)
    url, headers, payload = _build_request(provider, secret, raw_model)
    started = time.monotonic()
    async with sem:
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                latency_ms = int((time.monotonic() - started) * 1000)
                # 429 (rate limit) не значит "ключ мёртв" - помечаем отдельно,
                # но не как жёсткий failure дашборда.
                ok = resp.status < 300 or resp.status == 429
                text_snippet = ""
                if not ok:
                    text_snippet = (await resp.text())[:200]
                return KeyModelResult(
                    provider=provider,
                    key_index=key_index,
                    model_id=model_id,
                    ok=ok,
                    status=resp.status,
                    latency_ms=latency_ms,
                    error=None if ok else text_snippet,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            latency_ms = int((time.monotonic() - started) * 1000)
            return KeyModelResult(
                provider=provider,
                key_index=key_index,
                model_id=model_id,
                ok=False,
                status=None,
                latency_ms=latency_ms,
                error=f"{type(exc).__name__}: {exc}",
            )


async def run_full_healthcheck() -> HealthReport:
    """Прогоняет ping-запрос по каждой связке (ключ, модель) каждого
    провайдера напрямую, в обход ротации/карантина в БД — цель именно
    проверить сырую валидность ключей и доступность моделей."""
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    tasks = []
    async with aiohttp.ClientSession() as session:
        for provider in PROVIDER_KEY_ENV_NAMES:
            secrets = configured_provider_keys(provider)
            provider_models = [m for m in MODELS if _provider_for_model(m) == provider]
            for key_index, secret in enumerate(secrets, start=1):
                for model_id in provider_models:
                    tasks.append(
                        _check_one(session, sem, provider, key_index, secret, model_id)
                    )
        results = await asyncio.gather(*tasks) if tasks else []

    report = HealthReport(results=list(results))
    await _attach_uptimerobot(report)
    return report


async def _attach_uptimerobot(report: HealthReport) -> None:
    if not UPTIMEROBOT_API_KEY:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                UPTIMEROBOT_API_URL,
                data={
                    "api_key": UPTIMEROBOT_API_KEY,
                    "format": "json",
                    # Проценты доступны за сутки, неделю, месяц и за всё время;
                    # журнал нужен, чтобы в ежедневном отчёте показать инциденты.
                    "custom_uptime_ratios": "1-7-30",
                    "all_time_uptime_ratio": "1",
                    "logs": "1",
                    "logs_limit": "50",
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Cache-Control": "no-cache",
                },
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                data = await resp.json(content_type=None)
        if data.get("stat") != "ok":
            report.uptimerobot_error = str(data.get("error", "unknown error"))
            return
        report.uptimerobot = [
            {
                "name": m.get("friendly_name"),
                "status": m.get("status"),
                "url": m.get("url"),
                "uptime_1d": _uptime_ratio(m, 0),
                "uptime_7d": _uptime_ratio(m, 1),
                "uptime_30d": _uptime_ratio(m, 2),
                "uptime_all_time": m.get("all_time_uptime_ratio"),
                "incidents": _uptimerobot_incidents(m),
            }
            for m in data.get("monitors", [])
        ]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        report.uptimerobot_error = f"{type(exc).__name__}: {exc}"
        logger.exception("Не удалось получить статусы UptimeRobot")


def _uptime_ratio(monitor: dict, position: int) -> str | None:
    """Берёт заданный API процент из строки значений, разделённых дефисом."""
    ratios = str(monitor.get("custom_uptime_ratios") or "").split("-")
    if position >= len(ratios):
        return None
    value = ratios[position].strip()
    return value or None


def _uptimerobot_incidents(monitor: dict) -> list[dict]:
    """Инцидент — запись журнала UptimeRobot с типом 1 (down)."""
    incidents = []
    for log in monitor.get("logs") or []:
        if not isinstance(log, dict) or log.get("type") != 1:
            continue
        incidents.append(
            {
                "datetime": log.get("datetime"),
                "duration": log.get("duration"),
                "reason": log.get("reason"),
            }
        )
    return incidents


# Коды статусов мониторов UptimeRobot.
_UPTIMEROBOT_STATUS_LABELS = {
    0: "paused",
    1: "not checked yet",
    2: "up",
    8: "seems down",
    9: "down",
}


def _format_uptime(monitor: dict) -> str:
    periods = (
        ("1 дн.", monitor.get("uptime_1d")),
        ("7 дн.", monitor.get("uptime_7d")),
        ("30 дн.", monitor.get("uptime_30d")),
        ("за всё время", monitor.get("uptime_all_time")),
    )
    values = [f"{label}: {value}%" for label, value in periods if value is not None]
    return "; ".join(values) if values else "нет данных об uptime"


def _format_incident(incident: dict) -> str:
    timestamp = incident.get("datetime")
    if isinstance(timestamp, (int, float)):
        happened_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(
            "%d.%m %H:%M UTC"
        )
    else:
        happened_at = "время неизвестно"
    duration = incident.get("duration")
    duration_text = f", {duration} сек." if isinstance(duration, (int, float)) else ""
    reason = str(incident.get("reason") or "").strip()
    reason_text = f", {escape(reason)}" if reason else ""
    return f"{happened_at}{duration_text}{reason_text}"


def format_report(report: HealthReport) -> str:
    lines: list[str] = []
    total = len(report.results)
    failed = report.failures
    lines.append(
        f"<b>Health-check провайдеров</b>\n"
        f"Проверено связок ключ×модель: {total}\n"
        f"Проблем: {len(failed)}"
    )

    if not failed:
        lines.append("\n✅ Все ключи и модели отвечают.")
    else:
        by_provider: dict[str, list[KeyModelResult]] = {}
        for r in failed:
            by_provider.setdefault(r.provider, []).append(r)
        for provider, items in by_provider.items():
            lines.append(f"\n<b>{provider}</b>:")
            for r in items:
                status = r.status if r.status is not None else "нет ответа"
                lines.append(
                    f"  ключ #{r.key_index} × <code>{r.model_id}</code> "
                    f"→ {status} ({r.latency_ms} мс)"
                    + (f"\n    {r.error}" if r.error else "")
                )

    if report.uptimerobot_error:
        lines.append(f"\n⚠️ UptimeRobot: {report.uptimerobot_error}")
    elif report.uptimerobot is not None:
        down = [m for m in report.uptimerobot if m["status"] not in (2, 1)]
        lines.append(f"\n<b>UptimeRobot</b>: {len(report.uptimerobot)} монитор(ов)")
        for monitor in report.uptimerobot:
            name = escape(str(monitor.get("name") or "Без названия"))
            lines.append(f"  • {name} — {_format_uptime(monitor)}")
        if down:
            for m in down:
                label = _UPTIMEROBOT_STATUS_LABELS.get(m["status"], m["status"])
                lines.append(f"  ⚠️ {escape(str(m['name']))}: {label}")
        else:
            lines.append("  ✅ все мониторы up")

        incidents = [
            (monitor, incident)
            for monitor in report.uptimerobot
            for incident in monitor.get("incidents", [])
        ]
        if incidents:
            lines.append(f"  ⚠️ Инцидентов в доступном журнале: {len(incidents)}")
            for monitor, incident in incidents:
                name = escape(str(monitor.get("name") or "Без названия"))
                lines.append(f"    • {name}: {_format_incident(incident)}")
        else:
            lines.append("  ✅ Инцидентов в доступном журнале нет")

    return "\n".join(lines)
