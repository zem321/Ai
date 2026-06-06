import os
import base64
import logging
import asyncio
import aiohttp
import json
import io
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from PIL import Image, ImageFilter

from keyboards import cancel_keyboard, image_size_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

HF_TOKEN = os.getenv("HF_TOKEN")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_IMG_URL = "https://ai.api.nvidia.com/v1/genai/stabilityai/sdxl-turbo"

SIZE_MAP = {
    "1024x1024": (1024, 1024),
    "1792x1024": (1792, 1024),
    "1024x1792": (1024, 1792),
}


# ── Утилиты изображений ───────────────────────────────────────────────────────

def to_png(image_bytes: bytes, max_size: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def composite_fg_on_bg(fg_bytes: bytes, bg_bytes: bytes, output_size: tuple) -> bytes:
    """
    Накладывает foreground (PNG с прозрачным фоном) на background.
    Одежда и человек сохраняются pixel-perfect.
    Фон обрезается по центру (не растягивается) → нет сплюснутости.
    """
    from PIL import ImageOps

    fg = Image.open(io.BytesIO(fg_bytes)).convert("RGBA")
    bg = Image.open(io.BytesIO(bg_bytes)).convert("RGB")

    out_w, out_h = output_size

    # Фон: обрезаем по центру под нужный размер (crop to fill, без искажений)
    bg_fit = ImageOps.fit(bg, (out_w, out_h), method=Image.LANCZOS, centering=(0.5, 0.5))

    # Человек: вписываем в output_size сохраняя пропорции, прижимаем к низу
    fg_w, fg_h = fg.size
    scale = min(out_w / fg_w, out_h / fg_h)
    new_w = int(fg_w * scale)
    new_h = int(fg_h * scale)
    fg_scaled = fg.resize((new_w, new_h), Image.LANCZOS)

    # Центрируем по горизонтали, прижимаем к низу
    x = (out_w - new_w) // 2
    y = out_h - new_h  # ноги внизу, голова вверху

    # Создаём финальную картинку
    canvas = bg_fit.convert("RGBA")
    canvas.paste(fg_scaled, (x, y), mask=fg_scaled)

    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, format="JPEG", quality=93)
    return buf.getvalue()


# ── Удаление фона через rembg (локально, без лимитов) ────────────────────────

# Сессия кэшируется: загружается один раз, потом мгновенно
_rembg_session_cache = None


def _rembg_sync(image_bytes: bytes) -> bytes:
    """
    Запускается в отдельном потоке.
    rembg импортируется здесь — бот не упадёт если библиотека не установлена.
    birefnet-portrait — самая точная модель для людей, убирает тени и даёт чёткий контур.
    Сессия кэшируется глобально — модель загружается один раз, все следующие фото быстро.
    """
    global _rembg_session_cache
    try:
        import numpy as np
        from rembg import remove, new_session

        # Загружаем модель только при первом вызове, потом переиспользуем
        if _rembg_session_cache is None:
            _rembg_session_cache = new_session("birefnet-portrait")

        session = _rembg_session_cache
        result_bytes = remove(image_bytes, session=session)

        img = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
        r, g, b, a = img.split()
        a_arr = np.array(a)

        # Тени имеют alpha 20-100 — порог 127 их срезает
        # Сам человек имеет alpha 200-255 — сохраняется
        a_hard = np.where(a_arr > 127, 255, 0).astype(np.uint8)

        # Лёгкое сглаживание краёв (убирает пиксельные зазубрины)
        a_smooth = Image.fromarray(a_hard).filter(ImageFilter.GaussianBlur(radius=0.8))
        a_arr2 = np.array(a_smooth)
        a_final_arr = np.where(a_arr2 > 100, 255, 0).astype(np.uint8)
        a_final = Image.fromarray(a_final_arr)

        img.putalpha(a_final)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    except ImportError:
        raise Exception(
            "rembg не установлен.\n"
            "Добавь в requirements.txt:\n"
            "  rembg\n"
            "  onnxruntime\n"
            "И задеплой заново."
        )


async def remove_background(image_bytes: bytes) -> bytes:
    """Асинхронная обёртка — не блокирует бота пока rembg работает."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _rembg_sync, image_bytes)


def _warmup_sync():
    """Загружает модель в память при старте бота (в фоне)."""
    global _rembg_session_cache
    try:
        from rembg import new_session
        if _rembg_session_cache is None:
            logger.info("Прогрев rembg: загружаю birefnet-portrait...")
            _rembg_session_cache = new_session("birefnet-portrait")
            logger.info("Прогрев rembg: готово, модель в памяти")
    except Exception as e:
        logger.warning(f"Прогрев rembg не удался (не критично): {e}")


async def warmup_rembg():
    """Вызывается при старте бота — загружает модель заранее."""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _warmup_sync)
    except Exception as e:
        logger.warning(f"Прогрев завершился с ошибкой (не критично): {e}")


# ── Генерация нового фона через NVIDIA SDXL Turbo ────────────────────────────

async def generate_background(prompt: str, size: tuple) -> bytes:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан в переменных Railway.")

    full_prompt = (
        f"{prompt}, professional product photography background, "
        f"clean, high quality, no people, no objects, no text"
    )
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "text_prompts": [{"text": full_prompt, "weight": 1}],
        "cfg_scale": 5,
        "seed": 0,
        "steps": 4,
        "sampler": "K_EULER_ANCESTRAL",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            NVIDIA_IMG_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status == 401:
                raise Exception("Неверный NVIDIA_API_KEY.")
            if resp.status == 402:
                raise Exception("Кончились кредиты NVIDIA API. Проверь баланс на build.nvidia.com.")
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"NVIDIA SDXL {resp.status}: {text[:200]}")
            data = await resp.json()
            artifacts = data.get("artifacts", [])
            if not artifacts:
                raise Exception("NVIDIA вернул пустой ответ.")
            b64 = artifacts[0].get("base64", "")
            if not b64:
                raise Exception("NVIDIA: нет base64 в ответе.")
            return base64.b64decode(b64)


# ── Хэндлеры ─────────────────────────────────────────────────────────────────

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
        "• outdoor park, natural light</i>\n\n"
        "✂️ Человек вырезается точно по контуру, фон заменяется",
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
            "<i>Зажми фото → добавь подпись → отправь</i>\n\n"
            "Например: <code>white studio background</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        return

    photo = message.photo[-1]
    file = await message.bot.get_file(photo.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    image_bytes = to_png(file_bytes.read())
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    await state.update_data(
        edit_image_b64=image_b64,
        edit_prompt=caption,
        edit_step="waiting_size"
    )
    await message.answer(
        f"✅ <b>Фото получено!</b>\n\n"
        f"🎨 Новый фон: <i>{caption}</i>\n\n"
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

    size_str    = callback.data.replace("size_edit_", "")
    output_size = SIZE_MAP.get(size_str, (1024, 1024))

    await state.update_data(image_size=size_str, edit_step="processing")
    status_msg = await callback.message.edit_text(
        "✂️ <i>Шаг 1/3: Вырезаю человека по контуру...\n"
        "⏱ Первый раз ~30 сек (загрузка модели), потом быстрее</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    bg_prompt   = data.get("edit_prompt")

    try:
        # Шаг 1: rembg вырезает человека точно по контуру
        fg_bytes = await remove_background(image_bytes)

        await status_msg.edit_text(
            "🎨 <i>Шаг 2/3: Генерирую новый фон...\n⏱ ~20-40 секунд</i>",
            parse_mode="HTML"
        )

        # Шаг 2: FLUX генерирует новый фон
        bg_bytes = await generate_background(bg_prompt, output_size)

        await status_msg.edit_text(
            "🔧 <i>Шаг 3/3: Склеиваю...</i>",
            parse_mode="HTML"
        )

        # Шаг 3: Накладываем человека на новый фон
        result = composite_fg_on_bg(fg_bytes, bg_bytes, output_size)

        image_file = BufferedInputFile(result, filename="result.jpg")
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
        await state.update_data(
            edit_step="waiting_photo",
            edit_image_b64=None,
            edit_prompt=None
        )

    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
