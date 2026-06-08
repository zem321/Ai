from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Текстовые модели
TEXT_MODELS = {
    "qwen/qwen3.5-397b-a17b": "Qwen 3.5 397B",
    "deepseek-ai/deepseek-v4-pro": "DeepSeek V4 Pro",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "Nemotron Super 49B",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
    "freemodel/gpt-5.4-nano": "GPT 5.4 Nano (FreeModel)",
    "freemodel/gpt-5.5": "GPT 5.5 (FreeModel)",
}

# Vision модели
VISION_MODELS_DICT = {
    "meta/llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick Vision",
    "meta/llama-3.2-11b-vision-instruct": "Llama 3.2 11B Vision",
}

MODELS = {**TEXT_MODELS, **VISION_MODELS_DICT}
VISION_MODELS = set(VISION_MODELS_DICT.keys())

# Модель редактирования (оставили для совместимости интерфейса)
EDIT_MODELS = {
    "flux.2-klein-4b": "Flux.2 Klein",
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Чат с ИИ", callback_data="mode_chat"),
            InlineKeyboardButton(text="Создать картинку", callback_data="mode_image_gen"),
        ],
        [
            InlineKeyboardButton(text="Выбрать модель", callback_data="select_model"),
            InlineKeyboardButton(text="Очистить историю", callback_data="clear_history"),
        ],
        [
            InlineKeyboardButton(text="Помощь", callback_data="help"),
        ]
    ])


def model_select_keyboard(current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    buttons.append([InlineKeyboardButton(text="--- Текст ---", callback_data="noop")])
    for model_id, model_name in TEXT_MODELS.items():
        label = f"[x] {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])

    buttons.append([InlineKeyboardButton(text="--- Vision (фото+текст) ---", callback_data="noop")])
    for model_id, model_name in VISION_MODELS_DICT.items():
        label = f"[x] {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])

    buttons.append([InlineKeyboardButton(text="Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сменить модель", callback_data="select_model"),
            InlineKeyboardButton(text="Меню", callback_data="main_menu"),
        ]
    ])


def edit_model_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Flux.2 Klein", callback_data="editmodel_flux.2-klein-4b")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])


def admin_notify_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Одобрить", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton(text="Отклонить", callback_data=f"reject_{user_id}"),
        ]
    ])


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Одобренные", callback_data="admin_list_approved")],
        [InlineKeyboardButton(text="Ожидают", callback_data="admin_list_pending")],
        [InlineKeyboardButton(text="Статистика", callback_data="admin_stats")],
    ])
