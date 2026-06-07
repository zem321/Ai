import os
import io
import json
import html
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

# У разных аккаунтов/роутинга assets может жить на разных хостах
ASSET_URLS = [
    ("https://ai.api.nvidia.com/v1/assets", "snake"),            # content_type
    ("https://api.nvcf.nvidia.com/v2/nvcf/assets", "camel"),     # contentType
]


def compress_image(image_bytes: bytes, max_side: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


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


def _extract_error_text(status: int, raw_text: str, parsed: dict = None) -> str:
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


def build_safe_edit_prompt(user_prompt: str) -> str:
    """
    Усиленный промпт, чтобы модель меняла только нужное и не трогала человека.
    """
    p = (user_prompt or "").strip()
    lower = p.lower()

    base_rules = (
        "Сделай строго только то, что просит пользователь.\n"
        "Главный объект (человек на фото) должен остаться тем же самым.\n"
        "Нельзя менять: лицо, черты лица, пол, возраст, волосы, тело, позу, одежду, руки, кожу.\n"
        "Нельзя добавлять или заменять человека, животных, посторонних персонажей.\n"
        "Нельзя превращать человека в другого персонажа.\n"
        "Сохрани реалистичность, ракурс и композицию.\n"
        "Изменяй только те области, которые напрямую относятся к запросу.\n"
    )

    if any(k in lower for k in ["фон", "background", "задний план"]):
        base_rules += (
            "Это запрос на смену фона: меняй только задний план.\n"
            "Передний план с человеком оставь без изменений.\n"
        )

    return f"{base_rules}\nЗапрос пользователя: {p}"


async def _post_json(url: str, payload: dict, headers: dict, timeout_sec: int = 240) -> dict:
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            raw_text = await resp.text()
            parsed = None
            try:
                parsed = json.loads(raw_text)
            except Exception:
                parsed = None

            if resp.status != 200:
                raise Exception(
                    f"{url} -> {_extract_error_text(resp.status, raw_text, parsed if isinstance(parsed, dict) else None)}"
                )

            if not isinstance(parsed, dict):
                raise Exception(f"{url} -> Некорректный JSON: {raw_text[:700]}")

            return parsed


async def upload_asset(image_bytes: bytes) -> str:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

    timeout = aiohttp.ClientTimeout(total=240)

    for asset_url, payload_style in ASSET_URLS:
        headers = {
            "Authorization": f"Bearer {NVIDIA_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if payload_style == "snake":
            body = {"content_type": "image/png", "description": "edit_image"}
        else:
            body = {"contentType": "image/png", "description": "edit_image"}

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

            logger.info("Asset uploaded via %s", asset_url)
            return asset_id
        except Exception as e:
            logger.warning("Asset endpoint failed: %s -> %s", asset_url, e)

    raise Exception("Не удалось загрузить asset ни через один endpoint (ai.api и api.nvcf)")


async def call_generate(prompt: str) -> bytes:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")

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

    data = await _post_json(GEN_URL, payload, headers)
    return parse_image_response(data)


async def _call_edit_with_asset(url: str, user_prompt: str, image_bytes: bytes, klein: bool) -> bytes:
    """
    Edit flow через assets + example_id (это то, что у тебя требует API).
    """
    asset_id = await upload_asset(image_bytes)
    safe_prompt = build_safe_edit_prompt(user_prompt)

    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "NVCF-INPUT-ASSET-REFERENCES": asset_id,
    }

    # Для твоего API нужен именно example_id
    example_ref = "data:image/png;example_id,0"

    if klein:
        # Для klein уменьшаем "творчество", чтобы меньше портил объект
        payload = {
            "prompt": safe_prompt,
            "image": [example_ref],
            "width": 1024,
            "height": 1024,
            "seed": 0,
            "steps": 3,
        }
    else:
        # Kontext лучше держит объект, повышаем точность следования ограничениями
        payload = {
            "prompt": safe_prompt,
            "image": example_ref,
            "aspect_ratio": "match_input_image",
            "seed": 0,
            "steps": 40,
            "cfg_scale": 2.5,
        }

    data = await _post_json(url, payload, headers, timeout_sec=300)
    return parse_image_response(data)


async def call_edit_kontext(prompt: str, image_bytes: bytes) -> bytes:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")
    return await _call_edit_with_asset(EDIT_URLS["flux.1-kontext-dev"], prompt, image_bytes, klein=False)


async def call_edit_klein(prompt: str, image_bytes: bytes) -> bytes:
    if not NVIDIA_API_KEY:
        raise Exception("NVIDIA_API_KEY не задан")
    return await _call_with_asset(EDIT_URLS["flux.2-klein-4b"], prompt, image_bytes, klein=True)


# ── Генерация ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "🎨 <b>Генерация изображения</b>\n\n"
        "📝 Опиши что хочешь создать:\n\n"
        "<i>Пример: Закат над морем в стиле аниме</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.image_generate, F.text)
async def do_generate_image(message: Message, state: FSMContext):
    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    status_msg = await message.answer("🎨 <i>Генерирую изображение...</i>", parse_mode="HTML")
    try:
        image_bytes = await call_generate(message.text)
        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(image_bytes, filename="generated.png"),
            caption=f"🎨 <b>Готово!</b>\n📝 {html.escape(message.text)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Image gen error")
        await status_msg.edit_text(
            f"❌ <b>Ошибка генерации:</b>\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )


# ── Редактирование ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="choose_model")
    await callback.message.edit_text(
        "✏️ <b>Редактирование фото</b>\n\nВыбери модель:",
        reply_markup=edit_model_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editmodel_"))
async def edit_model_selected(callback: CallbackQuery, state: FSMContext):
    model_key = callback.data.replace("editmodel_", "", 1)
    await state.update_data(edit_model=model_key, edit_step="waiting_photo")
    await callback.message.edit_text(
        "📸 Отправь фото <b>с подписью</b> — задание прямо под фото!\n\n"
        "<i>Пример: Замени фон на сад (человека не менять)</i>",
        reply_markup=cancel_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(BotStates.image_edit, F.photo)
async def edit_photo_received(message: Message, state: FSMContext):
    caption = (message.caption or "").strip()
    if not caption:
        await message.answer(
            "⚠️ Добавь задание как подпись к фото.",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
        return

    data = await state.get_data()
    edit_model = data.get("edit_model", "flux.1-kontext-dev")
    status_msg = await message.answer("📤 <i>Обрабатываю фото...</i>", parse_mode="HTML")

    try:
        photo = message.photo[-1]
        file = await message.bot.get_file(photo.file_id)
        file_obj = await message.bot.download_file(file.file_path)
        image_bytes = compress_image(file_obj.read())

        await status_msg.edit_text("✏️ <i>Редактирую фото...</i>", parse_mode="HTML")

        if edit_model == "flux.2-klein-4b":
            result_bytes = await call_edit_klein(caption, image_bytes)
        else:
            result_bytes = await call_edit_kontext(caption, image_bytes)

        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(result_bytes, filename="edited.png"),
            caption=f"✏️ <b>Готово!</b>\n📝 {html.escape(caption)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    except Exception as e:
        logger.exception("Image edit error")
        await status_msg.edit_text(
            f"❌ <b>Ошибка редактирования:</b>\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    finally:
        await state.update_data(edit_step="waiting_photo")
