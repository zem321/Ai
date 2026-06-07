import os
import io
import json
import html
import base64
import random
import logging
import asyncio
import aiohttp

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from PIL import Image
from huggingface_hub import InferenceClient

from keyboards import cancel_keyboard, edit_model_keyboard
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")

# NVIDIA — только генерация
GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"

# Hugging Face — редактирование
HF_EDIT_MODELS = {
    "flux.1-kontext-dev": "black-forest-labs/FLUX.1-Kontext-dev",
    "flux.2-klein-4b": "black-forest-labs/FLUX.1-Kontext-dev",  # для edit используем Kontext
}

# Порядок провайдеров для HF Inference Providers.
# Можно переопределить переменной HF_EDIT_PROVIDERS="fal-ai,replicate,hf-inference,auto"
HF_EDIT_PROVIDERS = [
    p.strip()
    for p in (os.getenv("HF_EDIT_PROVIDERS") or "fal-ai,replicate,hf-inference,auto").split(",")
    if p.strip()
]


def compress_image(image_bytes: bytes, max_side: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def build_safe_edit_prompt(user_prompt: str) -> str:
    p = (user_prompt or "").strip()
    lower = p.lower()

    rules = (
        "Perform strictly only the requested change. "
        "Keep the main person/object on the photo unchanged. "
        "Do NOT change face, body, pose, clothes, age, gender, identity. "
        "Do NOT add new people or animals to the foreground. "
        "Do NOT replace the main subject. "
        "Keep realism and camera angle. "
    )

    if any(x in lower for x in ["фон", "background", "задний план"]):
        rules += "Change ONLY the background. Keep the foreground and the person untouched. "

    return f"{rules}{p}"


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

    timeout = aiohttp.ClientTimeout(total=240)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(GEN_URL, json=payload, headers=headers) as resp:
            raw = await resp.text()
            parsed = None
            try:
                parsed = json.loads(raw)
            except Exception:
                pass

            if resp.status != 200:
                err = ""
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("detail"), str):
                        err = parsed["detail"]
                    elif isinstance(parsed.get("error"), dict):
                        err = parsed["error"].get("message", "")
                raise Exception(f"NVIDIA gen error: HTTP {resp.status}: {err or raw[:500]}")

            data = parsed if isinstance(parsed, dict) else {}
            artifacts = data.get("artifacts")
            if isinstance(artifacts, list) and artifacts and isinstance(artifacts[0], dict):
                b64 = artifacts[0].get("base64")
                if b64:
                    return base64.b64decode(b64)

            d = data.get("data")
            if isinstance(d, list) and d and isinstance(d[0], dict):
                b64 = d[0].get("b64_json")
                if b64:
                    return base64.b64decode(b64)

            raise Exception(f"Изображение не получено от генерации: {str(data)[:500]}")


def _pil_to_png_bytes(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _sync_hf_image_to_image(
    provider: str,
    model_id: str,
    token: str,
    image_bytes: bytes,
    prompt: str,
    seed: int,
) -> bytes:
    """
    Синхронный вызов huggingface_hub, который запускается через asyncio.to_thread.
    """
    client = InferenceClient(provider=provider, api_key=token, timeout=300)

    # Некоторые провайдеры поддерживают расширенные параметры, некоторые — нет.
    try:
        result = client.image_to_image(
            image_bytes,
            prompt=prompt,
            model=model_id,
            guidance_scale=3.5,
            num_inference_steps=30,
            seed=seed,
        )
    except TypeError:
        result = client.image_to_image(
            image_bytes,
            prompt=prompt,
            model=model_id,
        )

    if isinstance(result, Image.Image):
        return _pil_to_png_bytes(result)

    if isinstance(result, (bytes, bytearray)):
        return bytes(result)

    raise Exception(f"Неподдерживаемый тип ответа от HF: {type(result)}")


async def call_hf_edit(prompt: str, image_bytes: bytes, model_key: str = "flux.1-kontext-dev") -> bytes:
    """
    Редактирование через Hugging Face Inference Providers.
    """
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан. Добавьте переменную HF_TOKEN в Railway.")

    model_id = HF_EDIT_MODELS.get(model_key, "black-forest-labs/FLUX.1-Kontext-dev")
    safe_prompt = build_safe_edit_prompt(prompt)

    last_error = None

    for provider in HF_EDIT_PROVIDERS:
        try:
            logger.info("HF edit attempt: provider=%s model=%s", provider, model_id)

            result_bytes = await asyncio.to_thread(
                _sync_hf_image_to_image,
                provider,
                model_id,
                HF_TOKEN,
                image_bytes,
                safe_prompt,
                random.randint(1, 2_147_483_647),
            )

            if len(result_bytes) < 1000:
                raise Exception(f"Слишком маленький ответ: {len(result_bytes)} байт")

            return result_bytes

        except Exception as e:
            msg = str(e)
            last_error = e
            logger.warning("HF edit failed with provider=%s: %s", provider, msg)

            # Критичные ошибки токена прекращают ретраи
            if "401" in msg or "403" in msg or "unauthorized" in msg.lower() or "forbidden" in msg.lower():
                raise Exception(f"HF auth error: {msg}")

            # Иначе пробуем следующий провайдер
            continue

    raise Exception(f"HF редактирование не удалось. Последняя ошибка: {last_error}")


# ── Генерация ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_gen")
async def enter_image_gen(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_generate)
    await callback.message.edit_text(
        "<b>Генерация изображения</b>\n\nОпиши, что нужно создать.",
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


# ── Редактирование ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "mode_image_edit")
async def enter_image_edit(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BotStates.image_edit)
    await state.update_data(edit_step="choose_model")
    await callback.message.edit_text(
        "<b>Редактирование фото</b>\n\n"
        "Выбери модель:\n\n"
        "<b>Flux.1 Kontext</b> — точное редактирование\n"
        "<b>Flux.2 Klein</b> — быстрое редактирование\n\n"
        "<i>Работает через Hugging Face (принимает ваши фото)</i>",
        reply_markup=edit_model_keyboard(),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("editmodel_"))
async def edit_model_selected(callback: CallbackQuery, state: FSMContext):
    model_key = callback.data.replace("editmodel_", "", 1)
    await state.update_data(edit_model=model_key, edit_step="waiting_photo")
    await callback.message.edit_text(
        "Отправь фото с подписью (что изменить).\n\n"
        "<i>Примеры:</i>\n"
        "• Смени фон на сад\n"
        "• Добавь солнечные очки\n"
        "• Измени цвет шапки на красный",
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

        await status_msg.edit_text("Редактирую через Hugging Face...", parse_mode="HTML")

        result_bytes = await call_hf_edit(caption, image_bytes, model_key=edit_model)

        await status_msg.delete()
        await message.answer_photo(
            photo=BufferedInputFile(result_bytes, filename="edited.png"),
            caption=f"<b>Готово</b>\n{html.escape(caption)}",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )

    except Exception as e:
        logger.exception("Image edit error")
        err_text = html.escape(str(e))
        await status_msg.edit_text(
            f"Ошибка редактирования:\n<code>{err_text}</code>",
            parse_mode="HTML",
            reply_markup=cancel_keyboard(),
        )
    finally:
        await state.update_data(edit_step="waiting_photo")
