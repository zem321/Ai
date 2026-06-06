from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# 🎯 Официальные текстовые и мультимодальные модели из каталога NVIDIA
MODELS = {
    "meta/llama-3.3-70b-instruct": "🚀 Llama 3.3 70B",
    "nvidia/llama-3.1-nemotron-70b-instruct": "🦙 NVIDIA Nemotron 70B",
    "deepseek-ai/deepseek-r1": "🧠 DeepSeek R1 (NVIDIA)",
    "nvidia/llama-3.2-11b-vision-instruct": "👁️ Llama 3.2 Vision (Анализ фото)",
}

# Модели, которые умеют распознавать изображения (в чате)
VISION_MODELS = {
    "nvidia/llama-3.2-11b-vision-instruct",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct"
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💬 Чат с ИИ", callback_data="mode_chat"),
            InlineKeyboardButton(text="🤖 Выбрать ИИ", callback_data="select_model"),
        ],
        [
            InlineKeyboardButton(text="🎨 Создать фото", callback_data="mode_image_gen"),
            InlineKeyboardButton(text="🎬 Оживить фото", callback_data="mode_image_edit"),
        ],
        [
            InlineKeyboardButton(text="🗑 Очистить историю", callback_data="clear_history"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="help"),
        ]
    ])


def model_select_keyboard(current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    for model_id, model_name in MODELS.items():
        # Подставляем галочку, если модель выбрана
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
            InlineKeyboardButton(text="1024×1024 (Квадрат)", callback_data=f"size_{mode}_1024x1024"),
        ],
        [
            InlineKeyboardButton(text="1792×1024 (Горизонт)", callback_data=f"size_{mode}_1792x1024"),
            InlineKeyboardButton(text="1024×1792 (Вертикаль)", callback_data=f"size_{mode}_1024x1792"),
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
