import os
import io
import json
import html
import base64
import random
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

ASSET_URLS = [
    ("https://ai.api.nvidia.com/v1/assets", "snake"),            # {"content_type": "..."}
    ("https://api.nvcf.nvidia.com/v2/nvcf/assets", "camel"),     # {"contentType": "..."}
]


def compress_image(image_bytes: bytes, max_side: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def parse_image_response(data: dict) -> bytes:
    artifacts = data.get("artifacts")

    if isinstance(artifacts, list) and artifacts:
        first = artifacts[0]
        if isinstance(first, dict) and first.get("base64"):
            return base64.b64decode(first["base64"])

    if isinstance(artifacts, dict) and artifacts.get("base64"):
        return base64.b64decode(artifacts["base64"])

    d = data.get("data")
    if isinstance(d, list) and d and isinstance(d[0], dict):
        b64 = d[0].get("b64_json")
        if b64:
            return base64.b64decode(b64)

    raise Exception(f"Изображение не получено от сервера: {str(data)[:700]}")


def _extract_error_text(status: int, raw_text: str, parsed: dict | None = None) -> str:
    if isinstance(parsed, dict):
        if isinstance(parsed.get("detail"), str):
            return parsed["detail"]
        if isinstance(parsed.get("error"), dict):
            msg = parsed["error"].get("message")
            if msg:
                return msg
        if isinstance(parsed.get("message"), str):
            return parsed["message"]
    return f"HTTP {status}: {raw_text[:700]}"


async def _post_json(url: str, payload: dict, headers: dict, timeout_sec: int = 240) -> dict:
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            raw = await resp.text()
            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                pass

            if resp.status != 200:
                raise Exception(
                    f"{url} -> {_extract_error_text(resp.status, raw, parsed if isinstance(parsed, dict) else None)}"
                )

            if not isinstance(parsed, dict):
                raise Exception(f"{url} -> Некорректный JSON: {raw[:700]}")

            return parsed


def build_safe_edit_prompt(user_prompt: str) -> str:
    p = (user_prompt or "").strip()
    lower = p.lower()

    rules = (
        "Сделай строго только то, что просит пользователь.\n"
        "Сохрани основного человека/объект без изменений.\n"
        "Не меняй лицо, тело, позу, одежду, возраст, пол, идентичность.\n"
        "Не добавляй новых людей или животных на передний план.\n"
        "Не заменяй главного человека/объект.\n"
        "Сохрани реалистичность и ракурс.\n"
    )

    if any(x in lower for x in ["фон", "background", "задний план"]):
        rules += (
            "Это запрос на смену фона: измени только фон.\n"
            "Передний план и человека оставь без изменений.\n"
        )

    return f"{rules}\nЗапрос пользователя: {p}"


async def upload_asset(image_bytes: bytes) -> str:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

    timeout = aiohttp.ClientTimeout(total=240)
    last_error = None

    for asset_url, style in ASSET_URLS:
        headers = {
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        body = (
            {"content_type": "image/png", "description": "edit_image"}
            if style == "snake"
            else {"contentType": "image/png", "description": "edit_image"}
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(asset_url, json=body, headers=headers) as resp:
                    raw = await resp.text()
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = None

                    if resp.status != 200 or not isinstance(data, dict):
                        raise Exception(_extract_error_text(resp.status, raw, data if isinstance(data, dict) else None))

                    asset_id = data.get("assetId")
                    upload_url = data.get("uploadUrl")
                    if not asset_id or not upload_url:
                        raise Exception(f"Некорректный ответ assets: {str(data)[:700]}")

                async with session.put(
                    upload_url,
                    data=image_bytes,
                    headers={
                        "Content-Type": "image/png",
                        "x-amz-meta-nvcf-asset-description": "edit_image",
                    },
                ) as put_resp:
                    if put_resp.status not in (200, 204):
                        put_text = await put_resp.text()
                        raise Exception(f"Upload failed: HTTP {put_resp.status}: {put_text[:700]}")

            logger.info("Asset uploaded via %s | asset_id=%s", asset_url, asset_id)
            return asset_id

        except Exception as e:
            last_error = e
            logger.warning("Asset endpoint failed: %s -> %s", asset_url, e)

    raise Exception(f"Не удалось загрузить asset: {last_error}")


def _edit_headers(asset_id: str) -> dict:
    return {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "NVCF-INPUT-ASSET-REFERENCES": asset_id,
        "NVCF-FUNCTION-ASSET-IDS": asset_id,
    }


async def call_generate(prompt: str) -> bytes:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

    payload = {
        "prompt": prompt,
        "width": 1024,
        "height": 1024,
        "seed": random.randint(1, 2_147_483_647),
        "steps": 4,
    }
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    data = await _post_json(GEN_URL, payload, headers)
    return parse_image_response(data)


async def _call_edit(url: str, user_prompt: str, image_bytes: bytes, klein: bool) -> tuple[bytes, int]:
    """
    Критично:
    1) Отправляем только поле "image" (строка), без "images".
    2) Сначала asset_id.
    3) Если endpoint требует example_id -> fallback на example_id,0.
    """
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

    asset_id = await upload_asset(image_bytes)
    seed = random.randint(1, 2_147_483_647)
    safe_prompt = build_safe_edit_prompt(user_prompt)
    headers = _edit_headers(asset_id)

    if klein:
        primary_payload = {
            "prompt": safe_prompt,
            "image": f"data:image/png;asset_id,{asset_id}",
            "width": 1024,
            "height": 1024,
            "seed": seed,
            "steps": 4,
        }
    else:
        primary_payload = {
            "prompt": safe_prompt,
            "image": f"data:image/png;asset_id,{asset_id}",
            "aspect_ratio": "match_input_image",
            "seed": seed,
            "steps": 40,
            "cfg_scale": 2.5,
        }

    try:
        logger.info("Edit primary | url=%s | seed=%s | asset_id=%s", url, seed, asset_id)
        data = await _post_json(url, primary_payload, headers, timeout_sec=300)
        return parse_image_response(data), seed
    except Exception as e:
        text = str(e).lower()
        if "expected: example_id" not in text:
            raise

        logger.warning("Server expects example_id, fallback enabled: %s", e)

        if klein:
            fallback_payload = {
                "prompt": safe_prompt,
                "image": "data:image/png;example_id,0",
                "width": 1024,
                "height": 1024,
                "seed": seed,
                "steps": 4,
            }
        else:
            fallback_payload = {
                "prompt": safe_prompt,
                "image": "data:image/png;example_id,0",
                "aspect_ratio": "match_input_image",
                "seed": seed,
                "steps": 40,
                "cfg_scale": 2.5,
            }

        data = await _post_json(url, fallback_payload, headers, timeout_sec=300)
        return parse_image_response(data), seed


async def call_edit_kontext(prompt: str, image_bytes: bytes) -> tuple[bytes, int]:
    return await _call_edit(EDIT_URLS["flux.1-kontext-dev"], prompt, image_bytes, klein=False)


async def call_edit_klein(prompt: str, image_bytes: bytes) -> tuple[bytes, int]:
    return await _call_edit(EDIT_URLS["flux.2-klein-4b"], prompt, image_bytes, klein=True)


@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения</b>\n\nОпиши, что нужно создать.",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("Генерирую изображение...", parse_mode="HTML")
    try:
        image_bytes = await call_generate(message.text)
        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="generated.png"),
            caption=f"<b>Готово</b>\n{html.escape(message.text)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Image gen error")
        await status_msg.edit_text(
            f"Ошибка генерации:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="choose_model")
    await callback.message.edit_text(
        "<b>Редактирование фото</b>\n\nВыбери модель:",
        reply_markup=edit_model_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editmodel_"))
async def edit_model_selected(callback: CallbackQuery, state: FSMContext):
    model_key = callback.data.replace("editmodel_", "", 1)
    await state.update_data(edit_model=model_key, edit_step="waiting_photo")
    await callback.message.edit_text(
        "Отправь фото с подписью (что изменить).",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = (message.caption or "").strip()
    if not caption:
        await message.answer(
            "Добавь задание как подпись к фото.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    edit_model = data.get("edit_model", "flux.1-kontext-dev")
    status_msg = await message.answer("Обрабатываю фото...", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_obj = await message.bot.download_file(file.file_path)
        image_bytes = compress_image(file_obj.read())

        await status_msg.edit_text("Редактирую...", parse_mode="HTML")

        if edit_model == "flux.2-klein-4b":
            result_bytes, seed = await call_edit_klein(caption, image_bytes)
            model_name = "flux.2-klein-4b"
        else:
            result_bytes, seed = await call_edit_kontext(caption, image_bytes)
            model_name = "flux.1-kontext-dev"

        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(result_bytes, filename="edited.png"),
            caption=f"<b>Готово</b>\n{html.escape(caption)}\n\n<i>{model_name} | seed={seed}</i>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )

    except Exception as e:
        logger.exception("Image edit error")
        await status_msg.edit_text(
            f"Ошибка редактирования:\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    finally:
        await state.update_data(edit_step="waiting_photo")
