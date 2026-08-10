from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field

import database as db


PROVIDER_KEY_ENV_NAMES = {
    "nvidia": (
        "NVIDIA_API_KEY",
        "NVIDIA_API_KEY_2",
        "NVIDIA_API_KEY_3",
    ),
    "gemini": (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
    ),
    "groq": (
        "GROQ_API_KEY",
        "GROQ_API_KEY_2",
        "GROQ_API_KEY_3",
    ),
}

MAX_KEY_ATTEMPTS_PER_MODEL = 2
NVIDIA_REQUESTS_PER_MINUTE_PER_KEY = 39


class ProviderKeysUnavailable(RuntimeError):
    """У провайдера нет настроенного или доступного ключа."""


class AllProviderKeysExhausted(RuntimeError):
    """В рамках одного запроса подряд не ответили несколько разных ключей
    одного провайдера (см. MAX_KEY_ATTEMPTS_PER_MODEL). Пользователь уже
    увидел ошибку — это повод уведомить администратора."""

    def __init__(self, provider: str, provider_label: str, attempts: int):
        super().__init__(
            f"Все доступные API-ключи провайдера {provider_label} исчерпаны "
            f"(попыток: {attempts})"
        )
        self.provider = provider
        self.provider_label = provider_label
        self.attempts = attempts


class AIChainExhausted(RuntimeError):
    """Все модели цепочки fallback для выбранного уровня недоступны —
    то есть отвалилось несколько провайдеров подряд в рамках одного запроса."""

    def __init__(
        self,
        requested_model: str,
        attempted_models: list[str],
        errors: list[BaseException],
    ):
        summary = "; ".join(
            f"{m}: {type(e).__name__}" for m, e in zip(attempted_models, errors)
        )
        super().__init__(
            f"Все модели выбранного уровня временно недоступны ({summary})"
        )
        self.requested_model = requested_model
        self.attempted_models = list(attempted_models)
        self.errors = list(errors)


@dataclass(frozen=True)
class ProviderKeyLease:
    provider: str
    secret: str = field(repr=False)
    fingerprint: str


def configured_provider_keys(provider: str) -> tuple[str, ...]:
    try:
        env_names = PROVIDER_KEY_ENV_NAMES[provider]
    except KeyError as exc:
        raise ValueError("Неизвестный провайдер") from exc
    keys = tuple(
        value
        for name in env_names
        if (value := os.getenv(name, "").strip())
    )
    if len(set(keys)) != len(keys):
        raise RuntimeError(
            f"Для провайдера {provider} настроены повторяющиеся API-ключи"
        )
    return keys


def configured_provider_secrets() -> tuple[str, ...]:
    return tuple(
        key
        for provider in PROVIDER_KEY_ENV_NAMES
        for key in configured_provider_keys(provider)
    )


def key_fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


async def acquire_provider_key(
    provider: str,
    *,
    excluded_fingerprints: set[str] | frozenset[str] | None = None,
) -> ProviderKeyLease:
    excluded = excluded_fingerprints or frozenset()
    keys_by_fingerprint = {
        key_fingerprint(secret): secret
        for secret in configured_provider_keys(provider)
    }
    candidates = tuple(
        fingerprint
        for fingerprint in keys_by_fingerprint
        if fingerprint not in excluded
    )
    if not candidates:
        raise ProviderKeysUnavailable(
            f"Для провайдера {provider} нет доступных API-ключей"
        )
    per_minute_limit = (
        NVIDIA_REQUESTS_PER_MINUTE_PER_KEY
        if provider == "nvidia"
        else None
    )
    fingerprint = await db.reserve_provider_api_key(
        provider,
        candidates,
        per_minute_limit=per_minute_limit,
    )
    if fingerprint is None:
        raise ProviderKeysUnavailable(
            f"Все API-ключи провайдера {provider} временно недоступны"
        )
    return ProviderKeyLease(
        provider=provider,
        secret=keys_by_fingerprint[fingerprint],
        fingerprint=fingerprint,
    )


async def mark_provider_key_failure(lease: ProviderKeyLease) -> int:
    return await db.mark_provider_api_key_failure(
        lease.provider,
        lease.fingerprint,
    )


async def mark_provider_key_success(lease: ProviderKeyLease) -> None:
    await db.mark_provider_api_key_success(
        lease.provider,
        lease.fingerprint,
    )
