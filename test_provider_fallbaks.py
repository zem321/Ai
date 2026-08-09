import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from keyboards import (
    DEFAULT_MODEL,
    MODELS,
    MODEL_FALLBACK_CHAINS,
    REASONING_LEVELS,
)
from provider_keys import (
    NVIDIA_REQUESTS_PER_MINUTE_PER_KEY,
    ProviderKeyLease,
    acquire_provider_key,
    configured_provider_keys,
    key_fingerprint,
)
from handlers import chat_handler


class ModelFallbackConfigurationTests(unittest.TestCase):
    def test_level_chains_have_requested_order(self):
        self.assertEqual(
            MODEL_FALLBACK_CHAINS[REASONING_LEVELS["fast"]["model_id"]],
            (
                "nvidia/nemotron-3-nano-30b-a3b",
                "gemini/gemini-3.5-flash-lite",
                "groq/llama-3.1-8b-instant",
            ),
        )
        self.assertEqual(
            MODEL_FALLBACK_CHAINS[
                REASONING_LEVELS["balanced"]["model_id"]
            ],
            (
                "qwen/qwen3.6-27b",
                "gemini/gemini-3.6-flash",
                "nvidia/nemotron-3-super-120b-a12b",
            ),
        )
        self.assertEqual(
            MODEL_FALLBACK_CHAINS[REASONING_LEVELS["expert"]["model_id"]],
            (
                "z-ai/glm-5.2",
                "gemini/gemini-3.6-flash",
                "groq/openai/gpt-oss-120b",
            ),
        )

    def test_all_chain_models_are_allowlisted(self):
        for chain in MODEL_FALLBACK_CHAINS.values():
            self.assertEqual(len(chain), 3)
            for model_id in chain:
                self.assertIn(model_id, MODELS)

    def test_balanced_is_default(self):
        self.assertEqual(DEFAULT_MODEL, "qwen/qwen3.6-27b")


class ProviderKeySelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_nvidia_reservation_uses_39_rpm_and_maps_fingerprint(self):
        keys = ("nvidia-key-one", "nvidia-key-two", "nvidia-key-three")
        environment = {
            "NVIDIA_API_KEY": keys[0],
            "NVIDIA_API_KEY_2": keys[1],
            "NVIDIA_API_KEY_3": keys[2],
        }
        selected = key_fingerprint(keys[1])
        with (
            patch.dict(os.environ, environment, clear=False),
            patch(
                "provider_keys.db.reserve_provider_api_key",
                AsyncMock(return_value=selected),
            ) as reserve,
        ):
            lease = await acquire_provider_key("nvidia")

        self.assertEqual(lease.secret, keys[1])
        self.assertEqual(lease.fingerprint, selected)
        self.assertEqual(
            reserve.await_args.kwargs["per_minute_limit"],
            NVIDIA_REQUESTS_PER_MINUTE_PER_KEY,
        )
        self.assertEqual(
            set(reserve.await_args.args[1]),
            {key_fingerprint(key) for key in keys},
        )

    def test_duplicate_provider_keys_are_rejected(self):
        environment = {
            "GROQ_API_KEY": "same-groq-key",
            "GROQ_API_KEY_2": "same-groq-key",
            "GROQ_API_KEY_3": "third-groq-key",
        }
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaises(RuntimeError):
                configured_provider_keys("groq")


class ModelFallbackExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_level_reaches_groq_after_two_provider_failures(self):
        with (
            patch.object(
                chat_handler,
                "call_nvidia",
                AsyncMock(side_effect=RuntimeError("nvidia unavailable")),
            ) as nvidia,
            patch.object(
                chat_handler,
                "call_gemini",
                AsyncMock(side_effect=RuntimeError("gemini unavailable")),
            ) as gemini,
            patch.object(
                chat_handler,
                "call_groq",
                AsyncMock(return_value=("готово", {"provider": "groq"})),
            ) as groq,
        ):
            content, debug = await chat_handler.call_ai(
                REASONING_LEVELS["fast"]["model_id"],
                [{"role": "user", "content": "Привет"}],
            )

        self.assertEqual(content, "готово")
        nvidia.assert_awaited_once()
        gemini.assert_awaited_once()
        groq.assert_awaited_once()
        self.assertTrue(debug["fallback_used"])
        self.assertEqual(
            debug["fallback_model"],
            "groq/llama-3.1-8b-instant",
        )

    async def test_expert_level_uses_gpt_oss_as_last_fallback(self):
        with (
            patch.object(
                chat_handler,
                "call_nvidia",
                AsyncMock(side_effect=RuntimeError("nvidia unavailable")),
            ),
            patch.object(
                chat_handler,
                "call_gemini",
                AsyncMock(side_effect=RuntimeError("gemini unavailable")),
            ),
            patch.object(
                chat_handler,
                "call_groq",
                AsyncMock(return_value=("ответ", {"provider": "groq"})),
            ) as groq,
        ):
            _, debug = await chat_handler.call_ai(
                REASONING_LEVELS["expert"]["model_id"],
                [{"role": "user", "content": "Реши задачу"}],
            )

        self.assertEqual(
            groq.await_args.args[0],
            "groq/openai/gpt-oss-120b",
        )
        self.assertEqual(debug["fallback_model"], groq.await_args.args[0])


class _FakeContent:
    def __init__(self, payload):
        self.payload = payload

    async def iter_chunked(self, _size):
        yield self.payload


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self.content_length = None
        self.content = _FakeContent(json.dumps(payload).encode("utf-8"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeSession:
    def __init__(self, responses):
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def post(self, *_args, **_kwargs):
        return _FakeResponse(*self.responses.pop(0))


class ProviderKeyRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_failed_keys_are_quarantined_before_model_fallback(self):
        leases = [
            ProviderKeyLease("nvidia", "first-secret", "a" * 64),
            ProviderKeyLease("nvidia", "second-secret", "b" * 64),
        ]
        responses = [
            (429, {"error": "rate limit"}),
            (429, {"error": "rate limit"}),
        ]
        with (
            patch.object(
                chat_handler,
                "acquire_provider_key",
                AsyncMock(side_effect=leases),
            ) as acquire,
            patch.object(
                chat_handler,
                "mark_provider_key_failure",
                AsyncMock(return_value=60),
            ) as mark_failure,
            patch.object(
                chat_handler.aiohttp,
                "ClientSession",
                return_value=_FakeSession(responses),
            ),
        ):
            with self.assertRaises(RuntimeError):
                await chat_handler.call_nvidia(
                    "nvidia/nemotron-3-nano-30b-a3b",
                    [{"role": "user", "content": "Привет"}],
                )

        self.assertEqual(acquire.await_count, 2)
        self.assertEqual(mark_failure.await_count, 2)
        self.assertEqual(
            acquire.await_args_list[1].kwargs["excluded_fingerprints"],
            {"a" * 64},
        )
