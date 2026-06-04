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

from keyboards import cancel_keyboard, edit_model_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"
EDIT_URLS = {
    "flux.1-kontext-dev": "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev",
    "flux.2-klein-4b": "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b",
}
ASSET_URL = "https://api.nvcf.nvidia.com/v2/nvcf/assets"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def upload_asset(image_bytes: bytes) -> str:
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }

    init_body = {
        "contentType": "image/png",
        "description": "edit_image",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(ASSET_URL, json=init_body, headers=headers) as resp:
            text = await resp.text()
            logger.info(f"Asset init {resp.status} {text[:300]}")
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"Asset init JSON {text[:300]}")
            if resp.status != 200:
                raise Exception(f"Asset init failed {resp.status} {data}")

            asset_id = data["assetId"]
            upload_url = data["uploadUrl"]

        upload_headers = {
            "Content-Type": "image/png",
            "x-amz-meta-nvcf-asset-description": "edit_image",
        }

        async with aiohttp.ClientSession() as session2:
            async with session2.put(upload_url, data=image_bytes, headers=upload_headers) as resp2:
                body = await resp2.text()
                logger.info(f"Asset upload {resp2.status} {body[:100]}")
                if resp2.status not in (200, 204):
                    raise Exception(f"Asset upload failed {resp2.status} {body}")

    return asset_id


def parse_image_response(data: dict) -> bytes:
    artifacts = data.get("artifacts")
    if artifacts:
        return base64.b64decode(artifacts[0]["base64"])

    if "data" in data:
        return base64.b64decode(data["data"][0].get("b64_json"))

    raise Exception(f"Не удалось распарсить ответ модели: {str(data)[:200]}")


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
            GEN_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            text = await resp.text()
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"JSON {resp.status} {text[:300]}")

            if resp.status != 200:
                raise Exception(data.get("detail") or str(data))

            return parse_image_response(data)


async def call_edit(asset_id: str, prompt: str, model_key: str) -> bytes:
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    url = EDIT_URLS[model_key]
    img_ref = f"data:image/png;asset_id,{asset_id}"

    if model_key == "flux.1-kontext-dev":
        payload = {
            "prompt": prompt,
            "image": img_ref,
            "aspect_ratio": "match_input_image",
            "steps": 30,
            "cfg_scale": 3.5,
            "seed": 0,
        }
    else:
        payload = {
            "prompt": prompt,
            "image": img_ref,
            "width": 1024,
            "height": 1024,
            "seed": 0,
            "steps": 4,
        }

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "NVCF-INPUT-ASSET-REFERENCES": asset_id,
        "NVCF-FUNCTION-ASSET-IDS": asset_id,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            text = await resp.text()
            logger.info(f"Edit response {resp.status} {text[:500]}")
            try:
                data = json.loads(text)
            except Exception:
                raise Exception(f"JSON {resp.status} {text[:300]}")

            if resp.status != 200:
                raise Exception(data.get("detail") or data.get("message") or str(data))

            return parse_image_response(data)


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
        logger.error(f"Image gen error: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка генерации:\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="choose_model")
    await callback.message.edit_text(
        "✏️ Режим редактирования изображений.\n\nВыбери модель:",
        parse_mode="HTML",
        reply_markup=edit_model_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editmodel_"))
async def edit_model_selected(callback: CallbackQuery, state: FSMContext):
    model_key = callback.data.replace("editmodel_", "", 1)
    await state.update_data(edit_model=model_key, edit_step="waiting_photo")
    await callback.message.edit_text(
        "📷 Пришли фото с подписью, что нужно изменить.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "❗ Нужно отправить фото с подписью.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    edit_model = data.get("edit_model", "flux.1-kontext-dev")

    status_msg = await message.answer("⌛ Обрабатываю фото...", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)

        image_bytes = compress_image(file_bytes.read())
        asset_id = await upload_asset(image_bytes)

        await status_msg.edit_text("🧠 Отправляю запрос модели...", parse_mode="HTML")

        result_bytes = await call_edit(asset_id, caption, edit_model)
        image_file = BufferedInputFile(result_bytes, filename="edited.png")

        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✅ Готово!\n\n<b>Запрос:</b> {caption}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка редактирования:\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
