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

SYSTEM_PROMPT = "Ты полезный ИИ-ассистент. Отвечай на русском языке если вопрос на русском. Будь точным и лаконичным."
MAX_HISTORY = 20

# NVIDIA (оставляем как было)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# FreeModel
FREEMODEL_API_KEY = os.getenv("FREEMODEL_API_KEY", "")
FREEMODEL_OPENAI_BASE = os.getenv("FREEMODEL_OPENAI_BASE", "https://api.freemodel.dev")
FREEMODEL_ANTHROPIC_BASE = os.getenv("FREEMODEL_ANTHROPIC_BASE", "https://cc.freemodel.dev")


def get_history(data):
    return data.get("chat_history", [])


def get_model(data):
    return data.get("selected_model", "nvidia/llama-3.3-nemotron-super-49b-v1.5")


def get_provider(model_id: str) -> str:
    return "freemodel" if model_id.startswith("freemodel/") else "nvidia"


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
    out = []
    for msg in messages:
        out.append({
            "role": msg.get("role", "user"),
            "content": msg.get("content", "")
        })
    return out


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

        # Текстовое сообщение
        if isinstance(content, str):
            out.append({
                "role": mapped_role,
                "content": [{"type": "text", "text": content}]
            })
            continue

        # Мультимодальное сообщение (text + image_url)
        converted = []
        for block in content:
            if block.get("type") == "text":
                converted.append({"type": "text", "text": block.get("text", "")})
            elif block.get("type") == "image_url":
                url = (block.get("image_url") or {}).get("url", "")
                if url.startswith("data:image/jpeg;base64,"):
                    b64_data = url.split(",", 1)[1]
                    converted.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64_data
                        }
                    })

        if not converted:
            converted = [{"type": "text", "text": ""}]

        out.append({
            "role": mapped_role,
            "content": converted
        })

    return system_text, out


async def call_nvidia(model_id: str, messages: list) -> str:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

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
            timeout=aiohttp.ClientTimeout(total=60)
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")

            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)[:300]))

            return data["choices"][0]["message"]["content"]


async def call_freemodel_openai(raw_model: str, messages: list) -> str:
    if not FREEMODEL_API_KEY:
        raise Exception("FREEMODEL_API_KEY не задан")

    headers = {
        "Authorization": f"Bearer {FREEMODEL_API_KEY}",
        "Content-Type": "application/json",
    }
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
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")

            if resp.status != 200:
                raise Exception(data.get("error", {}).get("message", str(data)[:300]))

            return data["choices"][0]["message"]["content"]


async def call_freemodel_anthropic(raw_model: str, messages: list) -> str:
    if not FREEMODEL_API_KEY:
        raise Exception("FREEMODEL_API_KEY не задан")

    system_text, anthropic_messages = to_anthropic_messages(messages)
    payload = {
        "model": raw_model,
        "max_tokens": 2048,
        "messages": anthropic_messages,
    }
    if system_text:
        payload["system"] = system_text

    url = f"{FREEMODEL_ANTHROPIC_BASE}/v1/messages"

    # Пробуем Bearer (часто работает в прокси), если нет — fallback на x-api-key + anthropic-version
    async with aiohttp.ClientSession() as session:
        bearer_headers = {
            "Authorization": f"Bearer {FREEMODEL_API_KEY}",
            "Content-Type": "application/json",
        }
        async with session.post(url, json=payload, headers=bearer_headers, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ сервера: {text[:300]}")

            if resp.status == 200:
                parts = [item.get("text", "") for item in data.get("content", []) if item.get("type") == "text"]
                answer = "\n".join([p for p in parts if p]).strip()
                return answer or "Пустой ответ от модели."

        fallback_headers = {
            "x-api-key": FREEMODEL_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async with session.post(url, json=payload, headers=fallback_headers, timeout=aiohttp.ClientTimeout(total=60)) as resp2:
            text2 = await resp2.text()
            try:
                data2 = json.loads(text2)
            except Exception:
                raise Exception(f"Ответ сервера: {text2[:300]}")

            if resp2.status != 200:
                raise Exception(data2.get("error", {}).get("message", str(data2)[:300]))

            parts = [item.get("text", "") for item in data2.get("content", []) if item.get("type") == "text"]
            answer = "\n".join([p for p in parts if p]).strip()
            return answer or "Пустой ответ от модели."


async def call_ai(model_id: str, messages: list) -> str:
    provider = get_provider(model_id)

    if provider == "nvidia":
        # Для nvidia оставляем модель без изменений
        return await call_nvidia(model_id, messages)

    raw_model = strip_provider_prefix(model_id)
    if raw_model.startswith("claude-"):
        return await call_freemodel_anthropic(raw_model, messages)
    return await call_freemodel_openai(raw_model, messages)


@router.callback_query(F.data == "select_model")
async def cb_select_model(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = get_model(data)
    await callback.message.edit_text(
        "🤖 <b>Выбери модель ИИ:</b>\n\n"
        "👁 <i>Vision-модели поддерживают анализ фото</i>",
        reply_markup=model_select_keyboard(current),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("model_"))
async def cb_model_selected(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("model_", "", 1)
    await state.update_data(selected_model=model_id)
    model_name = MODELS.get(model_id, model_id)
    is_vision = model_id in VISION_MODELS
    vision_note = "\n👁 <i>Поддерживает анализ фото</i>" if is_vision else "\n📝 <i>Только текст</i>"
    await state.set_state(BotStates.chat_mode)
    await callback.message.edit_text(
        f"✅ <b>Модель:</b> {model_name}{vision_note}\n\nПиши сообщения!",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer(f"✅ {model_name}")


@router.message(Command("chat"))
async def enter_chat_mode_cmd(message: Message, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)
    is_vision = model_id in VISION_MODELS
    vision_note = "• Отправляй фото с подписью — отвечу!\n" if is_vision else "• Для анализа фото выбери Vision-модель\n"
    await message.answer(
        f"💬 <b>Режим чата</b>\n\n"
        f"🤖 Модель: <b>{model_name}</b>\n\n"
        f"• Пиши любые вопросы\n"
        f"{vision_note}\n"
        f"/clear — очистить историю",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )


@router.callback_query(F.data == "mode_chat")
async def enter_chat_mode_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.chat_mode)
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)
    is_vision = model_id in VISION_MODELS
    vision_note = "• Отправляй фото с подписью — отвечу!\n" if is_vision else "• Для анализа фото выбери Vision-модель\n"
    await callback.message.edit_text(
        f"💬 <b>Режим чата</b>\n\n"
        f"🤖 Модель: <b>{model_name}</b>\n\n"
        f"• Пиши любые вопросы\n"
        f"{vision_note}\n"
        f"/clear — очистить историю",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(Command("clear"))
async def clear_history_cmd(message: Message, state: FSMContext):
    await state.update_data(chat_history=[])
    await message.answer("🗑 <b>История очищена!</b>", reply_markup=cancel_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "clear_history")
async def clear_history_cb(callback: CallbackQuery, state: FSMContext):
    await state.update_data(chat_history=[])
    await callback.message.edit_text("🗑 <b>История очищена!</b>", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer("История очищена!")


@router.message(BotStates.chat_mode, F.photo)
async def handle_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)

    if model_id not in VISION_MODELS:
        await message.answer(
            f"⚠️ Модель <b>{model_name}</b> не поддерживает фото.\n\n"
            f"Нажми 🤖 Сменить модель и выбери одну из <b>Vision</b>-моделей.",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    caption = message.caption or "Подробно опиши что на этом фото"
    status_msg = await message.answer("🔍 <i>Анализирую фото...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_b64 = compress_image(file_bytes.read())

        history = get_history(data)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": caption},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
            ]
        })

        reply = await call_ai(model_id, messages)

        history.append({"role": "user", "content": f"[Фото] {caption}"})
        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)

        await status_msg.edit_text(
            f"🖼 <b>Анализ фото:</b>\n\n{reply}\n\n<i>🤖 {model_name}</i>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )


@router.message(BotStates.chat_mode, F.text)
async def handle_text(message: Message, state: FSMContext):
    data = await state.get_data()
    model_id = get_model(data)
    model_name = MODELS.get(model_id, model_id)

    await message.bot.send_chat_action(message.chat.id, "typing")
    status_msg = await message.answer("⏳ <i>Думаю...</i>", parse_mode="HTML")

    try:
        history = get_history(data)
        history.append({"role": "user", "content": message.text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history[-MAX_HISTORY:]

        reply = await call_ai(model_id, messages)

        history.append({"role": "assistant", "content": reply})
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        await state.update_data(chat_history=history)

        await status_msg.edit_text(
            f"{reply}\n\n<i>🤖 {model_name}</i>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Chat error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
