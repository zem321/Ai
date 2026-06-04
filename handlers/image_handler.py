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

from keyboards import cancelkeyboard, editmodelkeyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

# Генерация — FLUX.2 [klein] 4B
GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"

# Редактирование
EDIT_URLS = {
    "flux.1-kontext-dev": "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev",
    "flux.2-klein-4b": "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b",
}

# NVCF Assets API
ASSET_URL = "https://api.nvcf.nvidia.com/v2/nvcf/assets"


# ── Вспомогательные функции ─────────────────────────────────────────────────────


def compress_image(image_bytes: bytes) -> bytes:
    """Сжатие изображения до PNG 1024x1024 (чтобы не вылезать за лимиты API)."""
    img = Image.open(io.BytesIO(image_bytes))

    if img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((1024, 1024), Image.LANCZOS)

    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


async def upload_asset(image_bytes: bytes) -> str:
    """
    Загрузка картинки в NVCF Assets API.
    Возвращает asset_id, который потом используем в запросе к модели.
    """
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
        # 1) Инициализация asset'а — получаем presigned URL
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

        # 2) Загрузка бинарника по presigned URL
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
    """
    Разбор ответа NVIDIA: либо artifacts[].base64, либо data[0].b64_json.
    Возвращает байты PNG.
    """
    artifacts = data.get("artifacts")
    if artifacts:
        return base64.b64decode(artifacts[0]["base64"])

    # В некоторых моделях результат лежит в data[0].b64_json
    if "data" in data:
        return base64.b64decode(data["data"][0].get("b64_json"))

    raise Exception(f"Не удалось распарсить ответ модели: {str(data)[:200]}")


async def call_generate(prompt: str) -> bytes:
    """Текст → картинка через flux.2-klein-4b."""
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
                raise Exception(f"JSON {text[:300]}")

            if resp.status != 200:
                raise Exception(data.get("detail") or str(data))

            return parse_image_response(data)


async def call_edit(asset_id: str, prompt: str, model_key: str) -> bytes:
    """
    Редактирование картинки:
    - image = data:image/png;asset_id,{asset_id}
    - + заголовки NVCF-INPUT-ASSET-REFERENCES / NVCF-FUNCTION-ASSET-IDS
    """
    if not NVIDIA_API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is not set")

    url = EDIT_URLS[model_key]

    # Ссылка на загруженный asset
    img_ref = f"data:image/png;asset_id,{asset_id}"

    if model_key == "flux.1-kontext-dev":
        # ВНИМАНИЕ: публичный Preview endpoint flux.1-kontext-dev через ai.api.nvidia.com
        # принимает только example_id 0–2. Для произвольных картинок нужен свой NIM.
        payload = {
            "prompt": prompt,
            "image": img_ref,
            "aspect_ratio": "match_input_image",
            "steps": 30,
            "cfg_scale": 3.5,
            "seed": 0,
        }
    else:  # flux.2-klein-4b
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
        # Критично: сообщаем NVCF, какой asset используется
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


# ── Генерация ───────────────────────────────────────────────────────────────────


@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🖼 Режим генерации изображений.\n\nОтправь мне *текстовый запрос*, и я сгенерирую картинку.",
        parse_mode="HTML",
        reply_markup=cancelkeyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")

    status_msg = await message.answer(
        "⌛ Генерирую картинку, подожди...",
        parse_mode="HTML",
    )

    try:
        image_bytes = await call_generate(message.text)
        image_file = BufferedInputFile(image_bytes, filename="generated.png")

        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✅ Готово!\n\n<b>Запрос:</b> {message.text}",
            parse_mode="HTML",
            reply_markup=cancelkeyboard(),
        )
    except Exception as e:
        logger.error(f"Image gen error: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка генерации:\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancelkeyboard(),
        )


# ── Редактирование ─────────────────────────────────────────────────────────────


@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="choose_model")
    await callback.message.edit_text(
        "✏️ Режим редактирования изображений.\n\nВыбери модель для редактирования:",
        parse_mode="HTML",
        reply_markup=editmodelkeyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_model"))
async def edit_model_selected(callback: CallbackQuery, state: FSMContext):
    model_key = callback.data.replace("edit_model_", "")
    await state.update_data(edit_model=model_key, edit_step="waiting_photo")

    await callback.message.edit_text(
        "📷 Пришли фото и в подписи опиши, что нужно изменить.",
        parse_mode="HTML",
        reply_markup=cancelkeyboard(),
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = message.caption
    if not caption:
        await message.answer(
            "❗ Нужно отправить фото *с подписью* (описанием правки).",
            parse_mode="HTML",
            reply_markup=cancelkeyboard(),
        )
        return

    data = await state.get_data()
    edit_model = data.get("edit_model", "flux.1-kontext-dev")

    status_msg = await message.answer(
        "⌛ Обрабатываю фото, подожди...",
        parse_mode="HTML",
    )

    try:
        # Получаем файл из Telegram
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_bytes = await message.bot.download_file(file.file_path)

        # Сжимаем и готовим PNG
        image_bytes = compress_image(file_bytes.read())

        # Загружаем в NVCF как asset
        asset_id = await upload_asset(image_bytes)

        await status_msg.edit_text(
            "🧠 Отправляю запрос модели для редактирования...",
            parse_mode="HTML",
        )

        # Редактируем
        result_bytes = await call_edit(asset_id, caption, edit_model)
        image_file = BufferedInputFile(result_bytes, filename="edited.png")

        await status_msg.delete()
        await message.answer_photo(
            photo=image_file,
            caption=f"✅ Готово!\n\n<b>Запрос:</b> {caption}",
            parse_mode="HTML",
            reply_markup=cancelkeyboard(),
        )

        # Остаёмся в режиме ожидания следующего фото
        await state.update_data(edit_step="waiting_photo")

    except Exception as e:
        logger.error(f"Image edit error: {e}")
        await status_msg.edit_text(
            f"❌ Ошибка редактирования:\n<code>{str(e)}</code>",
            parse_mode="HTML",
            reply_markup=cancelkeyboard(),
        )
        await state.update_data(edit_step="waiting_photo")
