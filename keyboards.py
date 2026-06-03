from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Топ-8 лучших моделей для работы с текстом на OpenRouter
MODELS = {
    "anthropic/claude-3.5-sonnet": "✨ Claude 3.5 Sonnet",
    "openai/gpt-4o": "🧠 GPT-4o",
    "google/gemini-pro-1.5": "🔮 Gemini Pro 1.5",
    "meta-llama/llama-3.1-405b-instruct": "🦬 Llama 3.1 405B",
    "mistralai/mistral-large": "🇫🇷 Mistral Large",
    "anthropic/claude-3-opus": "👑 Claude 3 Opus",
    "openai/gpt-4o-mini": "⚡ GPT-4o Mini",
    "google/gemini-flash-1.5": "💨 Gemini Flash 1.5"
}

# Модели, поддерживающие анализ изображений (Vision)
VISION_MODELS = {
    "openai/gpt-4o",
    "anthropic/claude-3.5-sonnet",
    "google/gemini-pro-1.5",
    "openai/gpt-4o-mini",
    "google/gemini-flash-1.5"
}

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Режим чата", callback_data="chat_mode_status")],
        [InlineKeyboardButton(text="🎨 Генерация фото", callback_data="image_gen_status")],
        [InlineKeyboardButton(text="🤖 Выбрать модель", callback_data="select_model")]
    ])

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
        [InlineKeyboardButton(text="◀️ Назад", callback_data=\"main_menu\")],
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
        [InlineKeyboardButton(text="⏳ Ожидающие", callback_data="admin_list_pending")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])
