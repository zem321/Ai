from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Ведущие текстовые и графические модели OpenRouter (Актуальность: 2026 год)
MODELS = {
    # --- ТЕКСТОВЫЕ МОДЕЛИ ---
    "anthropic/claude-opus-4-8": "🔮 Claude Opus 4.8",
    "anthropic/claude-sonnet-4-6": "✨ Claude Sonnet 4.6",
    "openai/gpt-5.5": "🧠 GPT-5.5",
    "openai/gpt-5.4": "🧠 GPT-5.4",
    "google/gemini-3.5-flash": "⚡️ Gemini 3.5 Flash",
    "google/gemini-2.5-pro": "🔮 Gemini 2.5 Pro",
    "deepseek/deepseek-r1": "🐳 DeepSeek R1",
    "meta/llama-4": "🦬 Llama 4",
    
    # --- МЕДИА / ГРАФИЧЕСКИЕ МОДЕЛИ ---
    "openai/gpt-5.4-image-2": "🎨 GPT-5.4 Image 2",
    "google/gemini-2.5-flash-image": "🍌 Gemini 2.5 Flash Image",
    "google/gemini-3.1-flash-image-preview": "📸 Gemini 3.1 Flash Image Preview",
    "bytedance/seedream-4.5": "🌊 Seedream 4.5"
}

# Модели с поддержкой Vision (анализ изображений)
VISION_MODELS = {
    "anthropic/claude-opus-4-8",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-5.5",
    "openai/gpt-5.4",
    "google/gemini-3.5-flash",
    "google/gemini-2.5-pro",
    "google/gemini-3.1-flash-image-preview"
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Чат с ИИ", callback_data="mode_chat"),
            InlineKeyboardButton(text="🎨 Создать картинку", callback_data="mode_image_gen"),
        ],
        [
            InlineKeyboardButton(text="✏️ Редактировать фото", callback_data="mode_image_edit"),
            InlineKeyboardButton(text="🤖 Выбрать модель", callback_data="select_model"),
        ],
        [
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="clear_history"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ]
    ])


def model_select_keyboard(current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    for model_id, model_name in MODELS.items():
        label = f"✅ {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🤖 Сменить модель", callback_data="select_model"),
            InlineKeyboardButton(text="◀️ Меню", callback_data="main_menu"),
        ]
    ])


def image_size_keyboard(mode: str = "gen") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1024×1024", callback_data=f"size_{mode}_1024x1024"),
            InlineKeyboardButton(text="1792×1024", callback_data=f"size_{mode}_1792x1024"),
        ],
        [
            InlineKeyboardButton(text="1024×1792", callback_data=f"size_{mode}_1024x1792"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
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
