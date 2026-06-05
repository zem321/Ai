import os
import base64
import logging
import aiohttp
import json
import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard, image_size_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"

HF_TOKEN = os.getenv("HF_TOKEN")
# FLUX.1-schnell — быстрая качественная модель, поддерживается hf-inference бесплатно
HF_FLUX_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

SIZE_MAP = {
    "1024x1024": (1024, 1024),
    "1792x1024": (1792, 1024),
    "1024x1792": (1024, 1792),
}


def compress_image(image_bytes: bytes, max_size: int = 512) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    return output.getvalue()


def resize_result(image_bytes: bytes, size: str) -> bytes:
    w, h = SIZE_MAP.get(size, (1024, 1024))
    img = Image.open(io.BytesIO(image_bytes))
    img = img.resize((w, h), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def describe_image_nvidia(image_bytes: bytes, edit_prompt: str) -> str:
    """
    Шаг 1: NVIDIA Vision анализирует фото и составляет generation prompt
    с учётом пожелания пользователя.
    """
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан.")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    instruction = (
        f"Describe this photo in great detail for use as an image generation prompt. "
        f"Include: subjects, their appearance, clothing, poses, background, lighting, colors, style, mood, atmosphere. "
        f"Then apply this edit to the description: '{edit_prompt}'. "
        f"Output ONLY the final image generation prompt in English, 2-4 sentences. No explanations, no prefixes."
    )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": instruction},
            ],
        }
    ]

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NVIDIA_VISION_MODEL,
        "messages": messages,
        "max_tokens": 300,
        "temperature": 0.5,
        "stream": False,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            NVIDIA_CHAT_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"NVIDIA ответ: {text[:200]}")
            if resp.status != 200:
                detail = data.get("detail") or data.get("error", {}).get("message") or str(data)
                raise Exception(f"NVIDIA API {resp.status}: {detail}")
            return data["choices"][0]["message"]["content"].strip()


async def generate_image_flux(prompt: str, size: str) -> bytes:
    """
    Шаг 2: HuggingFace FLUX.1-schnell генерирует изображение.
    Использует твой HF_TOKEN, бесплатный лимит — 1000 запросов/день.
    """
    if not HF_TOKEN:
        raise Exception(
            "HF_TOKEN не задан!\n"
            "huggingface.co → Settings → Access Tokens → New token (Read)\n"
            "Добавь HF_TOKEN в переменные окружения Railway."
        )

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "x-wait-for-model": "true",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_FLUX_URL,
            json={"inputs": prompt},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")

            if resp.status == 503:
                raise Exception("Модель прогревается. Попробуй снова через 30 секунд.")

            if resp.status == 401:
                raise Exception("Неверный HF_TOKEN. Проверь токен на huggingface.co → Settings → Access Tokens.")

            if "application/json" in content_type:
                data = await resp.json()
                err = data.get("error", str(data))
                raise Exception(f"HuggingFace API: {err}")

            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HuggingFace API {resp.status}: {text[:200]}")

            result_bytes = await resp.read()
            if len(result_bytes) < 5000:
                raise Exception("Получен пустой ответ. Попробуй ещё раз.")

            return resize_result(result_bytes, size)


# ── Вход в режим редактирования ───────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото (AI)</b>\n\n"
        "📸 Отправь фото <b>с подписью</b> — напиши задание прямо под фото!\n\n"
        "<i>Примеры:\n"
        "• Change background to forest\n"
        "• Make the sky pink at sunset\n"
        "• Add snow to the scene\n"
        "• Change the shirt color to red\n"
        "• Make it look like an oil painting</i>\n\n"
        "💡 Задание лучше писать на английском",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "⚠️ <b>Напиши задание прямо под фото как подпись!</b>\n\n"
            "<i>Зажми фото → добавь подпись → отправь</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    image_bytes = compress_image(file_bytes.read())
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    await state.update_data(
        edit_image_b64=image_b64,
        edit_prompt=caption,
        edit_step="waiting_size"
    )
    await message.answer(
        f"✅ <b>Фото и задание получены!</b>\n\n"
        f"📝 Задание: <i>{caption}</i>\n\n"
        f"Выбери размер результата:",
        reply_markup=image_size_keyboard("edit"),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("size_edit_"))
async def size_edit_selected(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("edit_step") != "waiting_size":
        await callback.answer("Сначала отправь фото с подписью!", show_alert=True)
        return

    size = callback.data.replace("size_edit_", "")
    await state.update_data(image_size=size, edit_step="processing")

    status_msg = await callback.message.edit_text(
        "🔍 <i>Шаг 1/2: Анализирую фото через NVIDIA Vision...</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    prompt = data.get("edit_prompt")

    try:
        gen_prompt = await describe_image_nvidia(image_bytes, prompt)
        logger.info(f"Generated prompt: {gen_prompt}")

        await status_msg.edit_text(
            "🎨 <i>Шаг 2/2: Генерирую изображение (FLUX.1)...\n\n⏱ Обычно 15–40 секунд</i>",
            parse_mode="HTML"
        )

        result_bytes = await generate_image_flux(gen_prompt, size)

        image_file = BufferedInputFile(result_bytes, filename="edited.png")
        await status_msg.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=(
                f"✏️ <b>Готово!</b>\n"
                f"📝 {prompt}\n"
                f"📐 {size}\n\n"
                f"<i>{gen_prompt[:140]}{'...' if len(gen_prompt) > 140 else ''}</i>"
            ),
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None, edit_prompt=None)

    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка редактирования:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
