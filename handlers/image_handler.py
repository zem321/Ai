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
HF_TOKEN = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")

# NVIDIA — только генерация
GEN_URL = "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b"

# Hugging Face — редактирование
HF_EDIT_MODELS = {
    "flux.1-kontext-dev": "black-forest-labs/FLUX.1-Kontext-dev",
    "flux.2-klein-4b": "black-forest-labs/FLUX.1-Kontext-dev",  # для edit пока используем Kontext
}


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


def _decode_possible_json_image(raw: bytes) -> bytes | None:
    try:
        j = json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return None

    if isinstance(j, dict):
        for key in ["image", "output", "generated_image"]:
            val = j.get(key)
            if isinstance(val, str) and len(val) > 100:
                try:
                    return base64.b64decode(val)
                except Exception:
                    continue
    return None


async def call_hf_edit(prompt: str, image_bytes: bytes, model_key: str = "flux.1-kontext-dev") -> bytes:
    """
    Редактирование через Hugging Face Inference API.
    Важно: для image-to-image картинка передается в `inputs`, а prompt — в `parameters.prompt`.
    """
    if not HF_TOKEN:
        raise Exception("HF_TOKEN не задан. Добавьте переменную HF_TOKEN в Railway.")

    model_id = HF_EDIT_MODELS.get(model_key, "black-forest-labs/FLUX.1-Kontext-dev")
    safe_prompt = build_safe_edit_prompt(prompt)
    timeout = aiohttp.ClientTimeout(total=300)

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Accept": "image/png, image/jpeg, application/json",
    }

    endpoints = [
        f"https://router.huggingface.co/hf-inference/models/{model_id}",
        f"https://api-inference.huggingface.co/models/{model_id}",
    ]

    last_error = None

    for endpoint in endpoints:
        try:
            logger.info("HF edit request to %s", endpoint)

            # FormData нужно создавать заново на каждую попытку
            form = aiohttp.FormData()
            form.add_field("inputs", image_bytes, filename="input.png", content_type="image/png")
            form.add_field(
                "parameters",
                json.dumps(
                    {
                        "prompt": safe_prompt,
                        "guidance_scale": 3.5,
                        "num_inference_steps": 30,
                        "seed": random.randint(1, 2_147_483_647),
                    }
                ),
                content_type="application/json",
            )

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, data=form) as resp:
                    body = await resp.read()
                    content_type = (resp.headers.get("Content-Type") or "").lower()

                    if resp.status == 200:
                        # Иногда провайдер возвращает JSON вместо raw image
                        if "application/json" in content_type:
                            decoded = _decode_possible_json_image(body)
                            if decoded:
                                return decoded
                            txt = body.decode("utf-8", errors="ignore")
                            raise Exception(f"HF вернул JSON без изображения: {txt[:500]}")

                        if len(body) < 1000:
                            raise Exception(f"HF вернул слишком маленький ответ: {len(body)} байт")

                        return body

                    text = body.decode("utf-8", errors="ignore")
                    err_msg = text[:500]
                    try:
                        j = json.loads(text)
                        if isinstance(j, dict):
                            e = j.get("error") or j.get("message") or j.get("detail")
                            if isinstance(e, dict):
                                e = e.get("message", str(e))
                            if e:
                                err_msg = str(e)
                    except Exception:
                        pass

                    if resp.status == 503:
                        last_error = f"Модель загружается (503): {err_msg}"
                        logger.warning("HF 503 at %s: %s", endpoint, err_msg)
                        continue

                    raise Exception(f"HF error: HTTP {resp.status}: {err_msg}")

        except Exception as e:
            last_error = e
            logger.warning("HF edit failed at %s: %s", endpoint, e)
            # Ошибки токена/авторизации сразу возвращаем
            if "401" in str(e) or "403" in str(e) or "HF_TOKEN" in str(e):
                raise

    raise Exception(f"HF редактирование не удалось: {last_error}")


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

        # Важно: передаем выбранную модель
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
