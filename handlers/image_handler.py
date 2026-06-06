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

from keyboards import cancel_keyboard, image_size_keyboard, media_model_keyboard, MEDIA_MODELS
from states import BotStates

logger = logging.getLogger(__name__)
router = Router()

API_KEY = os.getenv("API_KEY")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

def get_media_model(data): return data.get("selected_media_model", "black-forest-labs/flux.1-schnell")
def is_video(model_id): return "cosmos" in model_id.lower() or "video" in model_id.lower()

def compress_image(image_bytes: bytes, max_size=1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()

async def call_media_generation(model: str, prompt: str, size: str = "1024x1024") -> bytes:
    if model == "gpt-image-2":
        url = "https://ai-proxy.izisoft.xyz/v1/image/generation"
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        payload = {"model": model, "prompt": prompt, "size": size, "n": 1}
    elif is_video(model):
        url = "https://integrate.api.nvidia.com/v1/video/generations"
        headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"}
        payload = {"model": model, "prompt": prompt}
    else:
        url = "https://integrate.api.nvidia.com/v1/images/generations"
        headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"}
        payload = {"model": model, "prompt": prompt, "response_format": "b64_json"}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as resp:
            text = await resp.text()
            data = json.loads(text)
            if resp.status >= 400:
                raise Exception(data.get("detail", data.get("error", str(data))))
            
            # NVIDIA / Proxy response parsing
            if "data" in data and len(data["data"]) > 0:
                item = data["data"][0]
                if "url" in item:
                    async with session.get(item["url"]) as media_resp:
                        return await media_resp.read()
                elif "b64_json" in item:
                    return base64.b64decode(item["b64_json"])
            elif "video_url" in data:
                async with session.get(data["video_url"]) as media_resp:
                    return await media_resp.read()
            raise Exception("Файл не получен от API")

async def call_media_edit(model: str, image_bytes: bytes, prompt: str, size: str = "1024x1024") -> bytes:
    if model == "gpt-image-2":
        url = "https://ai-proxy.izisoft.xyz/v1/images/edits"
        headers = {"Authorization": f"Bearer {API_KEY}"}
        form = aiohttp.FormData()
        form.add_field("model", model)
        form.add_field("prompt", prompt)
        form.add_field("size", size)
        form.add_field("image", image_bytes, filename="image.png", content_type="image/png")
        
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form, headers=headers) as resp:
                data = await resp.json()
                if resp.status != 200: raise Exception(str(data))
                item = data["data"][0]
                if "url" in item:
                    async with session.get(item["url"]) as img_resp: return await img_resp.read()
                return base64.b64decode(item["b64_json"])
    else:
        # NVIDIA Image2Video or Img2Img
        is_vid = is_video(model)
        url = "https://integrate.api.nvidia.com/v1/video/generations" if is_vid else "https://integrate.api.nvidia.com/v1/images/edits"
        headers = {"Authorization": f"Bearer {NVIDIA_API_KEY}", "Content-Type": "application/json", "Accept": "application/json"}
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        payload = {"model": model, "prompt": prompt}
        
        if is_vid:
            payload["image"] = f"data:image/jpeg;base64,{b64}"
        else:
            payload["image"] = f"data:image/png;base64,{b64}"
            payload["response_format"] = "b64_json"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status >= 400: raise Exception(str(data))
                if "data" in data and len(data["data"]) > 0:
                    item = data["data"][0]
                    if "url" in item:
                        async with session.get(item["url"]) as r: return await r.read()
                    return base64.b64decode(item["b64_json"])
                elif "video_url" in data:
                    async with session.get(data["video_url"]) as r: return await r.read()
                raise Exception("Файл не получен")

# ── Выбор медиа модели ──────────────────────────────────────────────
@router.callback_query(F.data == "select_media_model")
async def cb_select_media_model(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    current = get_media_model(data)
    await callback.message.edit_text("🖼 <b>Выбери модель для фото/видео:</b>", reply_markup=media_model_keyboard(current), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("media_"))
async def cb_media_selected(callback: CallbackQuery, state: FSMContext):
    model_id = callback.data.replace("media_", "", 1)
    await state.update_data(selected_media_model=model_id)
    await callback.message.edit_text(f"✅ Установлена медиа-модель: <b>{MEDIA_MODELS.get(model_id, model_id)}</b>", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

# ── Генерация Фото / Видео ──────────────────────────────────────────
@router.callback_query(F.data.in_({"mode_image_gen", "mode_video_gen"}))
async def enter_media_gen(callback: CallbackQuery, state: FSMContext):
    is_vid = "video" in callback.data
    await state.set_state(BotStates.video_generate if is_vid else BotStates.image_generate)
    
    if is_vid:
        await callback.message.edit_text("🎥 <b>Генерация видео</b>\n\n📝 Напиши сценарий/промпт:", reply_markup=cancel_keyboard(), parse_mode="HTML")
    else:
        await callback.message.edit_text("🎨 <b>Генерация фото</b>\n\nВыбери размер:", reply_markup=image_size_keyboard("gen"), parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("size_gen_"))
async def size_gen_selected(callback: CallbackQuery, state: FSMContext):
    size = callback.data.replace("size_gen_", "")
    await state.update_data(image_size=size)
    await callback.message.edit_text(f"✅ Размер: <b>{size}</b>\n\n📝 <b>Опиши что создать:</b>", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.message(BotStates.image_generate, F.text)
@router.message(BotStates.video_generate, F.text)
async def do_generate_media(message: Message, state: FSMContext):
    data = await state.get_data()
    model = get_media_model(data)
    is_vid = await state.get_state() == BotStates.video_generate.state
    size = data.get("image_size", "1024x1024")
    
    await message.bot.send_chat_action(message.chat.id, "upload_video" if is_vid else "upload_photo")
    status_msg = await message.answer(f"⏳ <i>Генерирую {'видео (это может занять минуту...)' if is_vid else 'фото...'}</i>", parse_mode="HTML")
    
    try:
        media_bytes = await call_media_generation(model, message.text, size)
        await status_msg.delete()
        if is_vid or is_video(model):
            file = BufferedInputFile(media_bytes, filename="video.mp4")
            await message.answer_video(video=file, caption=f"🎥 <b>Готово!</b>\n📝 {message.text}", parse_mode="HTML", reply_markup=cancel_keyboard())
        else:
            file = BufferedInputFile(media_bytes, filename="image.png")
            await message.answer_photo(photo=file, caption=f"🎨 <b>Готово!</b>\n📝 {message.text}", parse_mode="HTML", reply_markup=cancel_keyboard())
    except Exception as e:
        logger.error(f"Media gen error: {e}")
        await status_msg.edit_text(f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())

# ── Оживление / Редактирование (Img2Img / Img2Vid) ────────────────
@router.callback_query(F.data.in_({"mode_image_edit", "mode_video_anim"}))
async def enter_media_edit(callback: CallbackQuery, state: FSMContext):
    is_vid = "anim" in callback.data
    await state.set_state(BotStates.video_edit if is_vid else BotStates.image_edit)
    await state.update_data(edit_step="waiting_photo")
    
    verb = "🎬 <b>Оживить фото</b>" if is_vid else "✏️ <b>Редактировать фото</b>"
    await callback.message.edit_text(f"{verb}\n\n📸 Отправь фото <b>с подписью</b>-заданием!", reply_markup=cancel_keyboard(), parse_mode="HTML")
    await callback.answer()

@router.message(BotStates.image_edit, F.photo)
@router.message(BotStates.video_edit, F.photo)
async def media_photo_received(message: Message, state: FSMContext):
    if not message.caption:
        await message.answer("⚠️ <b>Добавь подпись к фото!</b>", parse_mode="HTML", reply_markup=cancel_keyboard())
        return

    is_vid = await state.get_state() == BotStates.video_edit.state
    file = await message.bot.get_file(message.photo[-1].file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    image_b64 = base64.b64encode(compress_image(file_bytes.read())).decode("utf-8")

    await state.update_data(edit_image_b64=image_b64, edit_prompt=message.caption, edit_step="processing" if is_vid else "waiting_size")
    
    if is_vid:
        await process_media_edit(message, state) # Сразу генерируем видео (размер не нужен)
    else:
        await message.answer("Выбери размер:", reply_markup=image_size_keyboard("edit"), parse_mode="HTML")

@router.callback_query(F.data.startswith("size_edit_"))
async def size_edit_selected(callback: CallbackQuery, state: FSMContext):
    await state.update_data(image_size=callback.data.replace("size_edit_", ""), edit_step="processing")
    await callback.answer()
    await process_media_edit(callback.message, state, is_callback=True)

async def process_media_edit(message: Message, state: FSMContext, is_callback=False):
    data = await state.get_data()
    model = get_media_model(data)
    is_vid = await state.get_state() == BotStates.video_edit.state
    
    status_msg = await message.answer(f"⏳ <i>{'Оживляю фото (до 1-2 минут)...' if is_vid else 'Редактирую фото...'}</i>", parse_mode="HTML")
    
    try:
        img_bytes = base64.b64decode(data["edit_image_b64"])
        res_bytes = await call_media_edit(model, img_bytes, data["edit_prompt"], data.get("image_size", "1024x1024"))
        await status_msg.delete()
        
        if is_vid or is_video(model):
            file = BufferedInputFile(res_bytes, filename="video.mp4")
            await message.answer_video(video=file, caption=f"🎬 <b>Готово!</b>\n📝 {data['edit_prompt']}", parse_mode="HTML", reply_markup=cancel_keyboard())
        else:
            file = BufferedInputFile(res_bytes, filename="edited.png")
            await message.answer_photo(photo=file, caption=f"✏️ <b>Готово!</b>\n📝 {data['edit_prompt']}", parse_mode="HTML", reply_markup=cancel_keyboard())
        
        await state.update_data(edit_step="waiting_photo", edit_image_b64=None, edit_prompt=None)
    except Exception as e:
        logger.error(f"Media edit error: {e}")
        await status_msg.edit_text(f"❌ <b>Ошибка:</b>\n<code>{str(e)}</code>", parse_mode="HTML", reply_markup=cancel_keyboard())
