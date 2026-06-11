from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ChatGPT модели
CHATGPT_MODELS = {
    "freemodel/gpt-5.4-nano": "GPT 5.4 Nano",
    "freemodel/gpt-5.5": "GPT 5.5",
}

# Claude модели
CLAUDE_MODELS = {
    "freemodel/claude-sonnet-4-6": "Claude Sonnet 4.6",
    "freemodel/claude-opus-4-7": "Claude Opus 4.7",
    "freemodel/claude-opus-4-8": "Claude Opus 4.8",
    "freemodel/claude-fable-5": "Claude Fable 5",
}

# Gemini модели
GEMINI_MODELS = {
    "gemini/gemini-3.1-flash-lite": "Gemini 3.1 Flash Lite",
    "gemini/gemini-3.5-flash": "Gemini 3.5 Flash",
    "gemini/gemini-3.1-pro": "Gemini 3.1 Pro",
}

# Остальные модели (Other): текстовые + Vision
OTHER_MODELS = {
    "qwen/qwen3.5-397b-a17b": "Qwen 3.5 397B",
    "deepseek-ai/deepseek-v4-pro": "DeepSeek V4 Pro",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "Nemotron Super 49B",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
    "meta/llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick Vision",
    "meta/llama-3.2-11b-vision-instruct": "Llama 3.2 11B Vision",
}

MODELS = {**CHATGPT_MODELS, **CLAUDE_MODELS, **GEMINI_MODELS, **OTHER_MODELS}

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
        [InlineKeyboardButton(text="Claude", callback_data="model_group_claude")],
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
    elif group == "claude":
        for model_id, model_name in CLAUDE_MODELS.items():
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
