from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Gemini модели
GEMINI_MODELS = {
    "gemini/gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
    "gemini/gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
    "gemini/gemini-3.6-flash": "Gemini 3.6 Flash",
}

# Остальные модели (Other)
OTHER_MODELS = {
    "z-ai/glm-5.2": "GLM-5.2",
    "deepseek-ai/deepseek-v4-pro": "DeepSeek V4 Pro",
    "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super 120B",
    "meta/llama-3.2-11b-vision-instruct": "Llama 3.2 Vision 11B",
}

MODELS = {**GEMINI_MODELS, **OTHER_MODELS}

# Llama Vision используется как визуальный адаптер для текстовых Other-моделей.
# Модели из этого набора получают изображение напрямую.
VISION_BRIDGE_MODEL = "meta/llama-3.2-11b-vision-instruct"
DIRECT_VISION_MODELS = frozenset((*GEMINI_MODELS, VISION_BRIDGE_MODEL))

# Человекочитаемые названия групп моделей
GROUP_TITLES = {
    "gemini": "Gemini",
    "other": "Other",
}

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Чат с ИИ", callback_data="mode_chat"),
            InlineKeyboardButton(text="Генерация фото", callback_data="mode_image_gen"),
        ],
        [
            InlineKeyboardButton(text="Редактировать фото", callback_data="mode_image_edit"),
        ],
        [
            InlineKeyboardButton(text="Очистить историю", callback_data="clear_history"),
        ],
        [
            InlineKeyboardButton(text="Код для сайта", callback_data="webapp_login_code"),
        ],
        [
            InlineKeyboardButton(text="Помощь", callback_data="help"),
        ]
    ])

def model_group_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Gemini", callback_data="model_group_gemini")],
        [InlineKeyboardButton(text="Other", callback_data="model_group_other")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")]
    ])

def models_keyboard(group: str, current: str = "") -> InlineKeyboardMarkup:
    buttons = []

    if group == "gemini":
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

def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")]
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
        [InlineKeyboardButton(text="Отклонённые", callback_data="admin_list_rejected")],
        [InlineKeyboardButton(text="Статистика", callback_data="admin_stats")],
    ])

def webapp_code_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура под сообщением с кодом входа на сайт."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Новый код", callback_data="webapp_login_code")],
        [InlineKeyboardButton(text="Меню", callback_data="main_menu")],
    ])
