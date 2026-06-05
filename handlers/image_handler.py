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
HF_FLUX_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

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

def _rembg_sync(image_bytes: bytes) -> bytes:
    """
    Запускается в отдельном потоке.
    rembg импортируется здесь — бот не упадёт если библиотека не установлена.
    u2net_human_seg — специальная модель для людей, лучше распознаёт ноги/руки.
    """
    try:
        import numpy as np
        from rembg import remove, new_session

        # u2net_human_seg специально обучена на людях — ноги/руки не обрезает
        session = new_session("u2net_human_seg")
        result_bytes = remove(image_bytes, session=session)

        # Чистим маску: убираем полупрозрачность
        # Пиксели с alpha > 10 → полностью непрозрачные (255)
        # Пиксели с alpha <= 10 → полностью прозрачные (0)
        img = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
        r, g, b, a = img.split()
        a_arr = np.array(a)
        a_arr = np.where(a_arr > 10, 255, 0).astype(np.uint8)

        # Небольшое сглаживание краёв чтобы не было резких пикселей
        a_clean = Image.fromarray(a_arr).filter(ImageFilter.GaussianBlur(radius=1))
        a_arr2 = np.array(a_clean)
        a_arr2 = np.where(a_arr2 > 128, 255, 0).astype(np.uint8)
        a_final = Image.fromarray(a_arr2)

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
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _rembg_sync, image_bytes)


# ── Генерация нового фона через FLUX ─────────────────────────────────────────

async def generate_background(prompt: str, size: tuple) -> bytes:
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан в переменных Railway.")

    w, h = size
    full_prompt = (
        f"{prompt}, professional product photography background, "
        f"clean, high quality, no people, no objects, no text"
    )
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
            if resp.status == 503:
                raise Exception("FLUX прогревается. Попробуй снова через 30 сек.")
            if resp.status == 401:
                raise Exception("Неверный HF_TOKEN. Проверь huggingface.co → Settings → Access Tokens.")
            if "application/json" in ct:
                data = await resp.json()
                raise Exception(f"HuggingFace FLUX: {data.get('error', data)}")
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HuggingFace FLUX {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 5000:
                raise Exception("Пустой ответ от FLUX. Попробуй ещё раз.")
            return result


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
