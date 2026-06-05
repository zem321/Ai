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
NVIDIA_EDIT_URL = "https://ai.api.nvidia.com/v1/genai/timbrooks/instruct-pix2pix"


def compress_image(image_bytes: bytes, max_size: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


def resize_image(image_bytes: bytes, size_str: str) -> bytes:
    try:
        w, h = map(int, size_str.split("x"))
    except Exception:
        return image_bytes
    img = Image.open(io.BytesIO(image_bytes))
    img = img.resize((w, h), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def call_edit_nvidia(image_bytes: bytes, prompt: str, size: str) -> bytes:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан! Добавь переменную окружения.")

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "image": image_b64,
        "prompt": prompt,
        "image_strength": 0.5,
        "cfg_scale": 9.0,
        "seed": 0,
        "steps": 50,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            NVIDIA_EDIT_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Ответ NVIDIA: {text[:300]}")

            if resp.status != 200:
                detail = data.get("detail") or data.get("error") or str(data)
                raise Exception(f"NVIDIA API ошибка {resp.status}: {detail}")

            artifacts = data.get("artifacts", [])
            if not artifacts:
                raise Exception("NVIDIA не вернула изображение")

            result_bytes = base64.b64decode(artifacts[0]["base64"])
            return resize_image(result_bytes, size)


# ── Вход в режим редактирования ───────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото (NVIDIA AI)</b>\n\n"
        "📸 Отправь фото <b>с подписью</b> — напиши задание прямо под фото!\n\n"
        "<i>Примеры заданий:\n"
        "• Change background to forest\n"
        "• Make the sky pink\n"
        "• Add snow\n"
        "• Change shirt color to red</i>\n\n"
        "💡 <b>Совет:</b> задание лучше писать на английском для точности",
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
        "✏️ <i>Редактирую фото через NVIDIA AI...\n\n⏱ Обычно 30–60 секунд</i>",
        parse_mode="HTML"
    )
    await callback.answer()

    image_bytes = base64.b64decode(data.get("edit_image_b64"))
    prompt = data.get("edit_prompt")

    try:
        result_bytes = await call_edit_nvidia(image_bytes, prompt, size)
        image_file = BufferedInputFile(result_bytes, filename="edited.png")
        await status_msg.delete()
        await callback.message.answer_photo(
            photo=image_file,
            caption=f"✏️ <b>Готово!</b>\n📝 {prompt}\n📐 {size}",
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
