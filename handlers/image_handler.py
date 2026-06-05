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

HF_TOKEN = os.getenv("HF_TOKEN")
HF_RMBG_URL   = "https://router.huggingface.co/hf-inference/models/briaai/RMBG-1.4"
HF_FLUX_URL    = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"

SIZE_MAP = {
    "1024x1024": (1024, 1024),
    "1792x1024": (1792, 1024),
    "1024x1792": (1024, 1792),
}


def prepare_image(image_bytes: bytes, max_size: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


def _check_hf_token():
    if not HF_TOKEN:
        raise Exception(
            "HF_TOKEN не задан!\n"
            "huggingface.co → Settings → Access Tokens → New token (Read)\n"
            "Добавь HF_TOKEN в переменные Railway."
        )


async def remove_background(image_bytes: bytes) -> bytes:
    """Вырезает фон через briaai/RMBG-1.4 — возвращает PNG с прозрачным фоном."""
    _check_hf_token()
    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "image/png",
        "x-wait-for-model": "true",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            HF_RMBG_URL,
            data=image_bytes,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status == 503:
                raise Exception("Модель прогревается. Попробуй снова через 30 секунд.")
            if resp.status == 401:
                raise Exception("Неверный HF_TOKEN. Проверь токен на huggingface.co.")
            if "application/json" in content_type:
                data = await resp.json()
                raise Exception(f"HuggingFace RMBG: {data.get('error', str(data))}")
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HuggingFace RMBG {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 1000:
                raise Exception("Пустой ответ от RMBG. Попробуй ещё раз.")
            return result


async def generate_background(prompt: str, size: tuple[int, int]) -> bytes:
    """Генерирует фон через FLUX.1-schnell."""
    _check_hf_token()
    w, h = size
    full_prompt = f"Clean studio background, {prompt}, high quality, professional product photography background, no people, no objects"
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
            HF_FLUX_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status == 503:
                raise Exception("FLUX модель прогревается. Попробуй снова через 30 секунд.")
            if "application/json" in content_type:
                data = await resp.json()
                raise Exception(f"HuggingFace FLUX: {data.get('error', str(data))}")
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"HuggingFace FLUX {resp.status}: {text[:200]}")
            result = await resp.read()
            if len(result) < 5000:
                raise Exception("Пустой ответ от FLUX. Попробуй ещё раз.")
            return result


def composite(fg_bytes: bytes, bg_bytes: bytes, target_size: tuple[int, int]) -> bytes:
    """Накладывает одежду (fg с прозрачным фоном) на новый фон (bg)."""
    fg = Image.open(io.BytesIO(fg_bytes)).convert("RGBA")
    bg = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")

    # Подгоняем фон под размер foreground
    bg = bg.resize(fg.size, Image.LANCZOS)

    # Склеиваем
    result = bg.copy()
    result.paste(fg, (0, 0), fg)

    # Ресайз до целевого размера
    w, h = target_size
    result = result.resize((w, h), Image.LANCZOS)

    output = io.BytesIO()
    result.convert("RGB").save(output, format="JPEG", quality=92)
    return output.getvalue()


# ── Вход в режим редактирования ───────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "🖼 <b>Смена фона (AI)</b>\n\n"
        "📸 Отправь фото одежды <b>с подписью</b> — опиши желаемый фон!\n\n"
        "<i>Примеры:\n"
        "• white studio background\n"
        "• outdoor nature green park\n"
        "• minimalist grey gradient\n"
        "• urban street background\n"
        "• luxury marble floor\n"
        "• beach sunset background</i>\n\n"
        "✂️ AI вырежет одежду и поместит на новый фон",
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
    image_bytes = prepare_image(file_bytes.read())
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    await state.update_data(
        edit_image_b64=image_b64,
        edit_prompt=caption,
        edit_step="waiting_size"
    )
    await message.answer(
        f"✅ <b>Фото получено!</b>\n\n"
        f"🎨 Новый фон: <i>{caption}</i>\n\n"
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

    size_str = callback.data.replace("size_edit_", "")
    target_size = SIZE_MAP.get(size_str, (1024, 1024))
    await state.update_data(image_size=size_str, edit_step="processing")

    status_msg = await callback.message.edit_text(
        "✂️ <i>Шаг 1/3: Вырезаю фон...</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    bg_prompt = data.get("edit_prompt")

    try:
        # Шаг 1: Удаляем фон
        fg_bytes = await remove_background(image_bytes)

        await status_msg.edit_text(
            "🎨 <i>Шаг 2/3: Генерирую новый фон...</i>",
            parse_mode="HTML"
        )

        # Шаг 2: Генерируем новый фон
        bg_bytes = await generate_background(bg_prompt, target_size)

        await status_msg.edit_text(
            "🔧 <i>Шаг 3/3: Склеиваю...</i>",
            parse_mode="HTML"
        )

        # Шаг 3: Накладываем одежду на новый фон
        result_bytes = composite(fg_bytes, bg_bytes, target_size)

        image_file = BufferedInputFile(result_bytes, filename="result.jpg")
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
