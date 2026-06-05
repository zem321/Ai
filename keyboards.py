from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Все модели через NVIDIA API
MODELS = {
    "meta/llama-3.2-11b-vision-instruct": "⚡ Llama 3.2 11B Vision",
    "meta/llama-3.1-8b-instruct":          "⚡ Llama 3.1 8B",
    "microsoft/phi-3.5-mini-instruct":      "⚡ Phi-3.5 Mini",
    "meta/llama-3.1-70b-instruct":          "🟡 Llama 3.1 70B",
    "nvidia/llama-3.1-nemotron-70b-instruct": "🟡 Nemotron 70B",
    "meta/llama-3.2-90b-vision-instruct":   "🟡 Llama 3.2 90B Vision",
    "mistralai/mistral-large-2-instruct":   "🟡 Mistral Large 2",
    "deepseek-ai/deepseek-r1":              "🔴 DeepSeek R1",
    "meta/llama-3.1-405b-instruct":         "🔴 Llama 3.1 405B",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": "🔴 Nemotron Ultra 253B",
}

# Модели поддерживающие фото (vision)
VISION_MODELS = {
    "meta/llama-3.2-11b-vision-instruct",
    "meta/llama-3.2-90b-vision-instruct",
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Чат с ИИ", callback_data="mode_chat"),
            InlineKeyboardButton(text="✏️ Редактировать фото", callback_data="mode_image_edit"),
        ],
        [
            InlineKeyboardButton(text="🤖 Выбрать модель", callback_data="select_model"),
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="clear_history"),
        ],
        [
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ]
    ])


def model_select_keyboard(current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    for model_id, model_name in MODELS.items():
        label = f"✅ {model_name}" if model_id == current else model_name
        # Пометим vision модели
        if model_id in VISION_MODELS and model_id != current:
            label = f"📷 {model_name}"
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


def image_size_keyboard(mode: str = "edit") -> InlineKeyboardMarkup:
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
