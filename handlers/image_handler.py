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
ASSET_URL = "https://ai.api.nvidia.com/v1/assets"


def compress_image(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1024, 1024), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


def parse_image_response(data: dict) -> bytes:
    artifacts = data.get("artifacts", [])
    if artifacts:
        return base64.b64decode(artifacts[0]["base64"])
    if "data" in data:
        return base64.b64decode(data["data"][0].get("b64_json", ""))
    raise Exception("Изображение не получено от сервера")


async def upload_asset(image_bytes: bytes) -> str:
    """Загружаем фото на NVIDIA и получаем example_id"""
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    # Шаг 1 — запрашиваем upload URL
    async with aiohttp.ClientSession() as session:
        async with session.post(
            ASSET_URL,
            json={"content_type": "image/png", "description": "edit_image"},
            headers=headers
        ) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status != 200:
                raise Exception(f"Asset upload init failed: {data}")
            asset_id = data["assetId"]
            upload_url = data["uploadUrl"]

        # Шаг 2 — загружаем файл по presigned URL
        async with session.put(
            upload_url,
            data=image_bytes,
            headers={"Content-Type": "image/png", "x-amz-meta-nvcf-asset-description": "edit_image"}
        ) as resp:
            if resp.status not in (200, 204):
                raise Exception(f"Asset upload failed: status {resp.status}")

    return asset_id


async def call_generate(prompt: str) -> bytes:
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
        async with session.post(GEN_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status != 200:
                raise Exception(data.get("detail", str(data)[:300]))
            return parse_image_response(data)


async def call_edit_kontext(asset_id: str, prompt: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "NVCF-INPUT-ASSET-REFERENCES": asset_id,
    }
    payload = {
        "prompt": prompt,
        "image": f"data:image/png;asset_id,{asset_id}",
        "aspect_ratio": "match_input_image",
        "steps": 30,
        "cfg_scale": 3.5,
        "seed": 0,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(EDIT_URLS["flux.1-kontext-dev"], json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status != 200:
                raise Exception(data.get("detail", str(data)[:300]))
            return parse_image_response(data)


async def call_edit_klein(asset_id: str, prompt: str) -> bytes:
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "NVCF-INPUT-ASSET-REFERENCES": asset_id,
    }
    payload = {
        "prompt": prompt,
        "image": [f"data:image/png;asset_id,{asset_id}"],
        "width": 1024,
        "height": 1024,
        "seed": 0,
        "steps": 4,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(EDIT_URLS["flux.2-klein-4b"], json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status != 200:
                raise Exception(data.get("detail", str(data)[:300]))
            return parse_image_response(data)


# ── Генерация ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения</b>\n\n"
        "📝 Опиши что хочешь создать:\n\n"
        "<i>Пример: Закат над морем в стиле аниме</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("🎨 <i>Генерирую изображение...</i>", parse_mode="HTML")
    try:
        image_bytes = await call_generate(message.text)
        image_file = BufferedInputFile(image_bytes, filename="generated.png")
        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"🎨 <b>Готово!</b>\n📝 {message.text}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
    except Exception as e:
        logger.error(f"Image gen error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка генерации:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )


# ── Редактирование ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="choose_model")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото</b>\n\nВыбери модель:",
        reply_markup=edit_model_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editmodel_"))
async def edit_model_selected(callback: CallbackQuery, state: FSMContext):
    model_key = callback.data.replace("editmodel_", "")
    await state.update_data(edit_model=model_key, edit_step="waiting_photo")
    await callback.message.edit_text(
        "📸 Отправь фото <b>с подписью</b> — задание прямо под фото!\n\n"
        "<i>Пример: Замени фон на сад / Измени цвет машины на красный</i>",
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
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        return

    data = await state.get_data()
    edit_model = data.get("edit_model", "flux.1-kontext-dev")
    status_msg = await message.answer("📤 <i>Загружаю фото на сервер...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)
        image_bytes = compress_image(file_bytes.read())

        # Загружаем фото и получаем asset_id
        asset_id = await upload_asset(image_bytes)
        await status_msg.edit_text("✏️ <i>Редактирую фото...</i>", parse_mode="HTML")

        if edit_model == "flux.1-kontext-dev":
            result_bytes = await call_edit_kontext(asset_id, caption)
        else:
            result_bytes = await call_edit_klein(asset_id, caption)

        image_file = BufferedInputFile(result_bytes, filename="edited.png")
        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✏️ <b>Готово!</b>\n📝 {caption}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ <b>Ошибка редактирования:</b>\n<code>{str(e)}</code>",
            parse_mode="HTML", reply_markup=cancel_keyboard()
        )
        await state.update_data(edit_step="waiting_photo")
