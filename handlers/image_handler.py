import os
import base64
import logging
import aiohttp
import json
import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from PIL import Image, ImageDraw, ImageFilter

from keyboards import cancel_keyboard, image_size_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_VISION_MODEL = "meta/llama-3.2-11b-vision-instruct"

HF_TOKEN = os.getenv("HF_TOKEN")
HF_INPAINT_URL = "https://router.huggingface.co/hf-inference/models/runwayml/stable-diffusion-inpainting"
HF_FLUX_URL    = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

SIZE_MAP = {
    "1024x1024": (512, 512),
    "1792x1024": (768, 512),
    "1024x1792": (512, 768),
}
OUTPUT_SIZE_MAP = {
    "1024x1024": (1024, 1024),
    "1792x1024": (1792, 1024),
    "1024x1792": (1024, 1792),
}


def prepare_image(image_bytes: bytes, target_wh: tuple) -> bytes:
    w, h = target_wh
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def create_person_mask(width: int, height: int) -> bytes:
    """
    Маска для защиты человека целиком (голова + тело + одежда).

    Белое (255) = фон → заменить
    Чёрное (0)  = человек/одежда → сохранить

    Стратегия:
    - По высоте: защищаем 100% (от верха до низа) — голова не срезается
    - По ширине: защищаем центр ~60%, края (фон) заменяем
    - Плавный blur на краях = мягкий переход
    """
    mask = Image.new("L", (width, height), 255)  # всё белое (фон)
    draw = ImageDraw.Draw(mask)

    # Вертикальный прямоугольник на всю высоту — защищает весь рост человека
    pad_x = int(width * 0.20)   # 20% с каждой стороны = середина 60% ширины
    pad_y = int(height * 0.02)  # почти ничего сверху/снизу — не срезаем голову/ноги
    draw.rectangle(
        [pad_x, pad_y, width - pad_x, height - pad_y],
        fill=0  # чёрный = сохранить
    )

    # Мягкий переход на границе (без жёсткой линии)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(width, height) // 16))
    buf = io.BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


def upscale(result_bytes: bytes, target_wh: tuple) -> bytes:
    w, h = target_wh
    img = Image.open(io.BytesIO(result_bytes)).convert("RGB")
    img = img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=93)
    return buf.getvalue()


async def describe_clothing_detailed(image_bytes: bytes) -> str:
    """
    NVIDIA Vision: максимально детальное описание одежды для FLUX.
    Фокус на: тип, цвет (точный HEX или название), паттерн, материал,
    крой, длина, детали (пуговицы, молнии, карманы, принты, логотипы).
    """
    if not NVIDIA_API_KEY:
        return ""
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    prompt = (
        "You are a fashion expert writing prompts for AI image generation. "
        "Look at the clothing in this photo and describe it with maximum precision. "
        "Include ALL of: exact garment type, precise color names, pattern/print details, "
        "fabric texture, cut/silhouette, length, collar/neckline style, sleeve type, "
        "any logos/text/graphics, hardware (buttons/zippers/buckles), notable design features. "
        "Do NOT mention the person, model, background, or photo style. "
        "Output ONLY the clothing description in English, 3-5 sentences. "
        "Be extremely specific — as if describing it to someone who cannot see the photo."
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": prompt},
        ],
    }]
    headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": NVIDIA_VISION_MODEL,
        "messages": messages,
        "max_tokens": 350,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                NVIDIA_CHAT_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(f"NVIDIA vision failed: {e}")
        return ""


async def inpaint_background(image_bytes: bytes, mask_bytes: bytes, bg_prompt: str) -> bytes:
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан в переменных Railway.")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mask_b64  = base64.b64encode(mask_bytes).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "x-wait-for-model": "true",
    }
    payload = {
        "inputs": {"image": image_b64, "mask_image": mask_b64},
        "parameters": {
            "prompt": f"{bg_prompt}, professional photography, clean, high quality",
            "negative_prompt": "blurry, low quality, dirty, text, watermark",
            "num_inference_steps": 30,
            "guidance_scale": 7.5,
        },
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_INPAINT_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            ct = resp.headers.get("Content-Type", "")
            if resp.status == 503:
                raise Exception("Модель прогревается. Попробуй снова через 30 секунд.")
            if resp.status == 401:
                raise Exception("Неверный HF_TOKEN.")
            if "application/json" in ct:
                data = await resp.json()
                err = str(data.get("error", data))
                if any(x in err.lower() for x in ("not supported", "pipeline", "task")):
                    raise ValueError("not_supported")
                raise Exception(f"Inpainting: {err}")
            if resp.status != 200:
                text = await resp.text()
                if any(x in text.lower() for x in ("not supported", "pipeline")):
                    raise ValueError("not_supported")
                raise Exception(f"Inpainting {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 5000:
                raise Exception("Пустой ответ. Попробуй ещё раз.")
            return result


async def flux_with_clothing(clothing_desc: str, bg_prompt: str, size_str: str) -> bytes:
    """
    FLUX генерирует фото с максимально точным сохранением одежды.
    Промт строится из детального описания одежды + желаемый фон.
    """
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан в переменных Railway.")

    w, h = OUTPUT_SIZE_MAP.get(size_str, (1024, 1024))

    # Строим промт: одежда описана детально → FLUX воспроизводит точнее
    if clothing_desc:
        full_prompt = (
            f"Professional fashion e-commerce photo. "
            f"A person wearing: {clothing_desc}. "
            f"Background: {bg_prompt}. "
            f"Full body visible including head, commercial photography, "
            f"sharp details, high resolution, fashion catalog quality."
        )
        negative = (
            "headless, cropped head, no head, missing person, "
            "blurry, low quality, different clothes, wrong colors, "
            "distorted, deformed, ugly, watermark"
        )
    else:
        full_prompt = (
            f"Professional fashion e-commerce photo, person wearing clothing, "
            f"background: {bg_prompt}, full body with head visible, "
            f"commercial photography, high quality."
        )
        negative = "headless, cropped head, blurry, low quality, watermark"

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
        "x-wait-for-model": "true",
    }
    payload = {
        "inputs": full_prompt,
        "parameters": {"width": min(w, 1024), "height": min(h, 1024)},
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_FLUX_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "application/json" in ct:
                data = await resp.json()
                raise Exception(f"FLUX: {data.get('error', data)}")
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"FLUX {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 5000:
                raise Exception("Пустой ответ от FLUX.")
            return result


# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "🛍 <b>Смена фона для товара</b>\n\n"
        "📸 Отправь фото одежды <b>с подписью</b> — опиши желаемый фон!\n\n"
        "<i>Примеры:\n"
        "• white studio background\n"
        "• minimalist grey gradient\n"
        "• luxury dark background with soft lighting\n"
        "• flat lay on marble surface\n"
        "• beige aesthetic background\n"
        "• outdoor park, natural light</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "⚠️ <b>Напиши описание нового фона прямо под фото!</b>\n\n"
            "<i>Зажми фото → добавь подпись → отправь</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    img = Image.open(io.BytesIO(file_bytes.read())).convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    image_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    await state.update_data(edit_image_b64=image_b64, edit_prompt=caption, edit_step="waiting_size")
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

    size_str     = callback.data.replace("size_edit_", "")
    inpaint_size = SIZE_MAP.get(size_str, (512, 512))
    output_size  = OUTPUT_SIZE_MAP.get(size_str, (1024, 1024))

    await state.update_data(image_size=size_str, edit_step="processing")
    status_msg = await callback.message.edit_text(
        "🔍 <i>Шаг 1/2: Анализирую одежду...</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    bg_prompt   = data.get("edit_prompt")

    try:
        # Шаг 1: NVIDIA описывает одежду детально
        clothing_desc = await describe_clothing_detailed(image_bytes)
        if clothing_desc:
            logger.info(f"Clothing desc: {clothing_desc[:100]}...")

        await status_msg.edit_text(
            "🎨 <i>Шаг 2/2: Меняю фон...\n⏱ ~30-60 секунд</i>",
            parse_mode="HTML"
        )

        # Шаг 2: Пробуем inpainting (сохраняет оригинал максимально)
        try:
            img_resized = prepare_image(image_bytes, inpaint_size)
            mask_bytes  = create_person_mask(*inpaint_size)
            result      = await inpaint_background(img_resized, mask_bytes, bg_prompt)
            result      = upscale(result, output_size)
            method_note = ""
        except ValueError:
            # Inpainting не поддержан → FLUX с детальным описанием одежды
            logger.info("Switching to FLUX with detailed clothing description")
            await status_msg.edit_text(
                "🎨 <i>Генерирую через FLUX...\n⏱ ~20-40 секунд</i>",
                parse_mode="HTML"
            )
            result = await flux_with_clothing(clothing_desc, bg_prompt, size_str)
            method_note = "\n<i>💡 Для точного сохранения одежды лучше фото с белым фоном</i>"

        image_file = BufferedInputFile(result, filename="result.jpg")
        await status_msg.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=(
                f"✅ <b>Готово!</b>\n"
                f"🎨 Фон: <i>{bg_prompt}</i>\n"
                f"📐 {size_str}{method_note}"
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
