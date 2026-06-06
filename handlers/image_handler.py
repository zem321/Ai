import os
import io
import json
import base64
import logging

import aiohttp
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from PIL import Image

from keyboards import cancel_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
FLUX_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))

    if img.mode != "RGB":
        img = img.convert("RGB")

    # Исправлено на Image.Resampling.LANCZOS
    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="PNG", optimize=True)
    return output.getvalue()


def parse_image_response( dict) -> bytes:
    artifacts = data.get("artifacts")
    if artifacts and len(artifacts) > 0 and artifacts[0].get("base64"):
        return base64.b64decode(artifacts[0]["base64"])

    if "data" in data and data["data"]:
        item = data["data"][0]
        if "b64_json" in item:
            return base64.b64decode(item["b64_json"])

    raise Exception(f"Не удалось распарсить ответ модели: {str(data)[:500]}")


def extract_error_message(resp_ dict) -> str:
    if not isinstance(resp_data, dict):
        return str(resp_data)

    err = resp_data.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("type") or str(err)

    return resp_data.get("detail") or resp_data.get("message") or str(resp_data)


def image_bytes_to_data_url(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"image/png;base64,{b64}"


async def call_generate(prompt: str) -> bytes:
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    payload = {
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
        "seed": 0,
        "steps": 4,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            FLUX_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            text = await resp.text()
            logger.info("Generate response %s %s", resp.status, text[:1000])

            try:
                resp_data = json.loads(text)
            except Exception:
                raise Exception(f"JSON {resp.status}: {text[:300]}")

            if resp.status != 200:
                raise Exception(extract_error_message(resp_data))

            return parse_image_response(resp_data)


async def call_edit(image_bytes: bytes, prompt: str) -> bytes:
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    image_data_url = image_bytes_to_data_url(image_bytes)

    payload = {
        "prompt": prompt,
        "image": [image_data_url],
        "width": 1024,
        "height": 1024,
        "seed": 0,
        "steps": 4,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            FLUX_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            text = await resp.text()
            logger.info("Edit response %s %s", resp.status, text[:1000])

            try:
                resp_data = json.loads(text)
            except Exception:
                raise Exception(f"JSON {resp.status}: {text[:500]}")

            if resp.status != 200:
                raise Exception(extract_error_message(resp_data))

            return parse_image_response(resp_data)


@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🖼 Режим генерации изображений.\n\nОтправь текстовый запрос.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("⌛ Генерирую картинку...", parse_mode="HTML")

    try:
        image_bytes = await call_generate(message.text)
        image_file = BufferedInputFile(image_bytes, filename="generated.png")

        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✅ Готово!\n\n<b>Запрос:</b> {message.text}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Image generation error")
        await status_msg.edit_text(
            f"❌ Ошибка генерации:\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await callback.message.edit_text(
        "✏️ Режим редактирования изображений.\n\nПришли фото с подписью, что нужно изменить.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = (message.caption or "").strip()

    if not caption:
        await message.answer(
            "❗ Нужно отправить фото с подписью.\n\nПример: <code>сделай фон ночным городом</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    status_msg = await message.answer("⌛ Обрабатываю фото...", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        tg_file = await message.bot.get_file(photo.file_id)
        downloaded = await message.bot.download_file(tg_file.file_path)
        original_bytes = downloaded.read()

        logger.info("Original image bytes: %s", len(original_bytes))

        image_bytes = compress_image(original_bytes)

        logger.info("Compressed image bytes: %s", len(image_bytes))

        await status_msg.edit_text("🧠 Отправляю запрос модели...", parse_mode="HTML")

        result_bytes = await call_edit(image_bytes, caption)
        image_file = BufferedInputFile(result_bytes, filename="edited.png")

        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✅ Готово!\n\n<b>Запрос:</b> {caption}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Image edit error")
        await status_msg.edit_text(
            f"❌ Ошибка редактирования:\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
