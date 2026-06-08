import os
import base64
import logging
import json
import io
import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard, model_select_keyboard, MODELS, VISION_MODELS
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

SYSTEM_PROMPT = "You are a helpful AI assistant. Reply in Russian when user writes in Russian."
MAX_HISTORY = 20

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY", "")
FREEMODEL_OPENAI_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")
FREEMODEL_ANTHROPIC_BASE = os.getenv("FREEMODEL_ANTHROPIC_BASE", "https://cc.freemodel.dev")


def get_history(data):
    return data.get("chat_history", [])


def get_model(data):
    return data.get("selected_model", "nvidia/llama-3.3-nemotron-super-49b-v1.5")


def get_provider(model_id: str) -> str:
    if model_id.startswith("freemodel/"):
        return "freemodel"
    return "nvidia"


def strip_provider_prefix(model_id: str) -> str:
    return model_id.replace("freemodel/", "", 1)


def compress_image(image_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    return base64.b64encode(output.getvalue()).decode("utf-8")


def to_openai_messages(messages: list) -> list:
    normalized = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        normalized.append({"role": role, "content": content})
    return normalized


def to_anthropic_messages(messages: list) -> tuple[str, list]:
    system_text = ""
    out = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str):
                system_text = content
            continue

        mapped_role = "assistant" if role == "assistant" else "user"

        if isinstance(content, str):
            out.append({"role": mapped_role, "content": [{"type": "text", "text": content}]})
        else:
            out.append({"role": mapped_role, "content": content})

    return system_text, out


async def call_nvidia(model_id: str, messages: list) -> str:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_id,
        "messages": to_openai_messages(messages),
        "max_tokens": 2048,
        "temperature": 0.7,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            NVIDIA_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)[:300]))
            return data["choices"][0]["message"]["content"]


async def call_freemodel(model_id: str, messages: list) -> str:
    if not FREEMODEL_API_KEY:
        raise Exception("FREEMODEL_API_KEY is not set")

    raw_model = strip_provider_prefix(model_id)

    headers = {
        "Authorization": f"Bearer {FREEMODEL_API_KEY}",
        "Content-Type": "application/json",
    }

    if raw_model.startswith("claude-"):
        system_text, anthropic_messages = to_anthropic_messages(messages)
        payload = {
            "model": raw_model,
            "max_tokens": 2048,
            "messages": anthropic_messages,
        }
        if system_text:
            payload["system"] = system_text

        url = f"{FREEMODEL_ANTHROPIC_BASE}/v1/messages"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                text = await resp.text()
                data = json.loads(text)
                if resp.status != 200:
                    raise Exception(data.get("error", {}).get("message", str(data)[:300]))

                parts = [item.get("text", "") for item in data.get("content", []) if item.get("type") == "text"]
                return "
".join([p for p in parts if p]).strip()

    payload = {
        "model": raw_model,
        "messages": to_openai_messages(messages),
        "max_tokens": 2048,
        "temperature": 0.7,
    }
    url = f"{FREEMODEL_OPENAI_BASE}/v1/chat/completions"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)[:300]))
            return data["choices"][0]["message"]["content"]


async def call_ai(model_id: str, messages: list) -> str:
    provider = get_provider(model_id)
    if provider == "freemodel":
        return await call_freemodel(model_id, messages)
    return await call_nvidia(model_id, messages)

# Keep your handlers below unchanged except call_ai(...) usage.
# Existing cb_select_model, cb_model_selected, chat mode, text and photo handlers remain.
