from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ─── ТЕКСТОВЫЕ МОДЕЛИ ───────────────────────────────────────────
TEXT_CATS = {
    "gpt": "🧠 ChatGPT",
    "claude": "🔮 Claude",
    "nvidia": "🚀 NVIDIA & Other"
}

TEXT_MODELS = {
    "gpt": {
        "gpt-5.5": "🧠 GPT-5.5",
        "gpt-5.4-mini": "💨 GPT-5.4 Mini",
    },
    "claude": {
        "anthropic/claude-opus-4-8": "🔮 Claude Opus 4.8",
        "anthropic/claude-sonnet-4-6": "✨ Claude Sonnet 4.6",
        "claude-haiku-4-5-20251001": "⚡️ Claude Haiku 4.5",
    },
    "nvidia": {
        "meta/llama-3.1-405b-instruct": "🦙 Llama 3.1 405B",
        "meta/llama-3.2-90b-vision-instruct": "👁 Llama 3.2 90B Vision",
        "nvidia/nemotron-4-340b-instruct": "🚀 Nemotron-4 340B",
        "mistralai/mixtral-8x22b-instruct-v0.1": "🌪 Mixtral 8x22B",
        "google/gemma-2-27b-it": "💎 Gemma 2 27B",
        "microsoft/phi-3-medium-4k-instruct": "🔬 Phi-3 Medium",
        "qwen/qwen2-72b-instruct": "🐉 Qwen2 72B",
        "databricks/dbrx-instruct": "🧱 DBRX Instruct",
        "snowflake/arctic": "❄️ Snowflake Arctic",
        "upstage/solar-10.7b-instruct": "☀️ Solar 10.7B",
    }
}

# Плоский словарь для быстрого поиска названий текстовых моделей
ALL_TEXT_MODELS = {}
for cat_models in TEXT_MODELS.values():
    ALL_TEXT_MODELS.update(cat_models)

VISION_MODELS = {
    "anthropic/claude-opus-4-8", "anthropic/claude-sonnet-4-6", 
    "gpt-5.5", "gpt-5.4-mini", "meta/llama-3.2-90b-vision-instruct"
}

# ─── МЕДИЯ МОДЕЛИ (ФОТО И ВИДЕО) ────────────────────────────────
MEDIA_MODELS = {
    "gpt-image-2": "🎨 GPT Image 2 (Proxy)",
    "black-forest-labs/flux.1-schnell": "⚡️ Flux.1 Schnell (Фото)",
    "black-forest-labs/flux.1-dev": "✨ Flux.1 Dev (Фото)",
    "stabilityai/stable-diffusion-xl": "🌌 SDXL (Фото)",
    "nvidia/cosmos3-nano": "🎥 Cosmos 3 Nano (Видео)",
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Чат с ИИ", callback_data="mode_chat")],
        [
            InlineKeyboardButton(text="🎨 Создать фото", callback_data="mode_image_gen"),
            InlineKeyboardButton(text="✏️ Изменить фото", callback_data="mode_image_edit"),
        ],
        [
            InlineKeyboardButton(text="🎥 Создать видео", callback_data="mode_video_gen"),
            InlineKeyboardButton(text="🎬 Оживить фото", callback_data="mode_video_anim"),
        ],
        [
            InlineKeyboardButton(text="🤖 Текст. модель", callback_data="select_text_cat"),
            InlineKeyboardButton(text="🖼 Медиа модель", callback_data="select_media_model"),
        ],
        [
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="clear_history"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ]
    ])

def text_category_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for cat_id, cat_name in TEXT_CATS.items():
        buttons.append([InlineKeyboardButton(text=cat_name, callback_data=f"cat_{cat_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def text_model_keyboard(category: str, current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    for model_id, model_name in TEXT_MODELS.get(category, {}).items():
        label = f"✅ {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Категории", callback_data="select_text_cat")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def media_model_keyboard(current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    for model_id, model_name in MEDIA_MODELS.items():
        label = f"✅ {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"media_{model_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Меню", callback_data="main_menu")]
    ])

def image_size_keyboard(mode: str = "gen") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1024×1024", callback_data=f"size_{mode}_1024x1024"),
            InlineKeyboardButton(text="1792×1024", callback_data=f"size_{mode}_1792x1024"),
        ],
        [InlineKeyboardButton(text="1024×1792", callback_data=f"size_{mode}_1024x1792")],
        [InlineKeyboardButton(text="◀️ Меню", callback_data="main_menu")],
    ])

def admin_notify_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user_id}"),
        ]
    ])

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Одобренные", callback_data="admin_list_approved")],
        [InlineKeyboardButton(text="⏳ Ожидают", callback_data="admin_list_pending")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
    ])
