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

# img2img модель: берёт оригинальное фото + промт, меняет только то что нужно
HF_IMG2IMG_URL = "https://router.huggingface.co/hf-inference/models/stabilityai/stable-diffusion-xl-refiner-1.0"
# Fallback: text-to-image если img2img не сработает
HF_FLUX_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

SIZE_MAP = {
    "1024x1024": (1024, 1024),
    "1792x1024": (1792, 1024),
    "1024x1792": (1024, 1792),
}


def prepare_image(image_bytes: bytes, target_size: tuple = (1024, 1024)) -> bytes:
    """Подгоняем под нужный размер для img2img."""
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    # Для img2img нужны кратные 8 размеры
    w, h = target_size
    img = img.resize((w, h), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def describe_clothing_short(image_bytes: bytes) -> str:
    """Краткое описание одежды для усиления промта."""
    if not NVIDIA_API_KEY:
        return "clothing item"

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "Describe this clothing item in 1 short sentence for an image prompt. "
        "Include: garment type, main color, key details. "
        "Example: 'black leather jacket with silver zippers'. "
        "Output ONLY the description, no other text."
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ],
    }]
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NVIDIA_VISION_MODEL,
        "messages": messages,
        "max_tokens": 80,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                NVIDIA_CHAT_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"NVIDIA description failed: {e}")
        return "clothing item"


async def img2img_hf(image_bytes: bytes, prompt: str, negative_prompt: str) -> bytes:
    """
    img2img через SDXL Refiner.
    strength=0.35 — сохраняет ~65% оригинала, меняет только фон/стиль.
    """
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан в переменных Railway.")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "x-wait-for-model": "true",
    }
    payload = {
        "inputs": image_b64,
        "parameters": {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "strength": 0.38,          # низкое значение = сохраняем форму одежды
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
        },
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_IMG2IMG_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status == 503:
                raise Exception("Модель прогревается. Попробуй снова через 30 секунд.")
            if resp.status == 401:
                raise Exception("Неверный HF_TOKEN.")
            if "application/json" in content_type:
                data = await resp.json()
                err = data.get("error", str(data))
                # Если модель не поддерживает img2img — пробуем fallback
                if "not supported" in err.lower() or "pipeline" in err.lower():
                    raise ValueError(f"img2img_not_supported: {err}")
                raise Exception(f"HuggingFace SDXL: {err}")
            if resp.status != 200:
                text = await resp.text()
                if "not supported" in text.lower():
                    raise ValueError(f"img2img_not_supported")
                raise Exception(f"HuggingFace SDXL {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 5000:
                raise Exception("Пустой ответ. Попробуй ещё раз.")
            return result


async def text2img_flux_fallback(prompt: str, size: tuple) -> bytes:
    """Fallback на FLUX если SDXL img2img не поддерживается."""
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан.")
    w, h = size
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "x-wait-for-model": "true",
    }
    payload = {
        "inputs": prompt,
        "parameters": {"width": min(w, 1024), "height": min(h, 1024)},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_FLUX_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                data = await resp.json()
                raise Exception(f"FLUX: {data.get('error', str(data))}")
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"FLUX {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 5000:
                raise Exception("Пустой ответ от FLUX.")
            return result


# ── Вход в режим ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "🛍 <b>Смена фона для товара</b>\n\n"
        "📸 Отправь фото одежды <b>с подписью</b> — опиши желаемый фон!\n\n"
        "<i>Примеры:\n"
        "• white studio background\n"
        "• minimalist grey gradient background\n"
        "• luxury dark background with soft lighting\n"
        "• flat lay on marble surface\n"
        "• outdoor nature background\n"
        "• beige aesthetic background</i>\n\n"
        "✅ Форма и цвет одежды сохранятся",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "⚠️ <b>Напиши описание фона прямо под фото!</b>\n\n"
            "<i>Зажми фото → добавь подпись → отправь</i>\n\n"
            "Например: <code>white studio background</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file.file_path)

    img = Image.open(io.BytesIO(file_bytes.read()))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    image_bytes = buf.getvalue()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    await state.update_data(
        edit_image_b64=image_b64,
        edit_prompt=caption,
        edit_step="waiting_size"
    )
    await message.answer(
        f"✅ <b>Фото получено!</b>\n\n"
        f"🎨 Фон: <i>{caption}</i>\n\n"
        f"Выбери размер:",
        reply_markup=image_size_keyboard("edit"),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("size_edit_"))
async def size_edit_selected(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("edit_step") != "waiting_size":
        await callback.answer("Сначала отправь фото с подписью!", show_alert=True)
        return

    size_str = callback.data.replace("size_edit_", "")
    target_size = SIZE_MAP.get(size_str, (1024, 1024))
    await state.update_data(image_size=size_str, edit_step="processing")

    status_msg = await callback.message.edit_text(
        "🔍 <i>Анализирую одежду...</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    bg_prompt = data.get("edit_prompt")

    try:
        # Получаем краткое описание одежды
        clothing_desc = await describe_clothing_short(image_bytes)
        logger.info(f"Clothing: {clothing_desc}")

        # Промт: сохранить одежду, поменять фон
        full_prompt = (
            f"Professional product photo of {clothing_desc}, "
            f"{bg_prompt}, "
            f"high quality, sharp details, commercial photography, fashion catalog"
        )
        negative_prompt = (
            "blurry, low quality, distorted clothing, changed outfit, "
            "different clothes, deformed fabric, wrong colors"
        )

        await status_msg.edit_text(
            "🎨 <i>Меняю фон, сохраняю одежду...\n⏱ ~30-60 секунд</i>",
            parse_mode="HTML"
        )

        # Ресайз фото под нужный размер
        img_resized = prepare_image(image_bytes, target_size)

        try:
            # Пробуем img2img (сохраняет форму оригинала)
            result_bytes = await img2img_hf(img_resized, full_prompt, negative_prompt)
            method = "img2img"
        except ValueError:
            # Если img2img не поддерживается — FLUX text2img
            logger.info("img2img not supported, falling back to FLUX")
            await status_msg.edit_text(
                "🎨 <i>Генерирую через FLUX...\n⏱ ~20-40 секунд</i>",
                parse_mode="HTML"
            )
            result_bytes = await text2img_flux_fallback(full_prompt, target_size)
            method = "FLUX"

        image_file = BufferedInputFile(result_bytes, filename="product.jpg")
        await status_msg.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=(
                f"✅ <b>Готово!</b>\n"
                f"🎨 Фон: <i>{bg_prompt}</i>\n"
                f"📐 {size_str}"
            ),
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None, edit_prompt=None)

    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
