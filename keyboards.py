from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ChatGPT модели
CHATGPT_MODELS = {
    "freemodel/gpt-5.4-nano": "GPT 5.4 Nano",
    "freemodel/gpt-5.5": "GPT 5.5",
}

# Gemini модели
GEMINI_MODELS = {
    "gemini/gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
    "gemini/gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini/gemini-3.1-pro": "Gemini 3.1 Pro",
}

# Остальные модели (Other)
OTHER_MODELS = {
    "meta/llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick 17B",
    "z-ai/glm-5.1": "GLM-5.1",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": "Nemotron Ultra 253B",
    "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super 120B",
}

MODELS = {**CHATGPT_MODELS, **GEMINI_MODELS, **OTHER_MODELS}

# -------------------- Клавиатуры --------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Чат с ИИ", callback_data="mode_chat"),
            InlineKeyboardButton(text="Генерация фото", callback_data="mode_image_gen"),
        ],
        [
            InlineKeyboardButton(text="Выбрать модель", callback_data="select_model"),
            InlineKeyboardButton(text="Очистить историю", callback_data="clear_history"),
        ],
        [
            InlineKeyboardButton(text="Помощь", callback_data="help"),
        ]
    ])


def model_group_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ChatGPT", callback_data="model_group_chatgpt")],
        [InlineKeyboardButton(text="Gemini", callback_data="model_group_gemini")],
        [InlineKeyboardButton(text="Other", callback_data="model_group_other")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")]
    ])


def models_keyboard(group: str, current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    if group == "chatgpt":
        for model_id, model_name in CHATGPT_MODELS.items():
            label = f"[x] {model_name}" if model_id == current else model_name
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    elif group == "gemini":
        for model_id, model_name in GEMINI_MODELS.items():
            label = f"[x] {model_name}" if model_id == current else model_name
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    elif group == "other":
        for model_id, model_name in OTHER_MODELS.items():
            label = f"[x] {model_name}" if model_id == current else model_name
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="select_model")])
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
        [InlineKeyboardButton(text="Flux 2 Klein", callback_data="editmodel_flux.2-klein-4b")],
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
