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

# Claude модели (через api.ashibalt.ru — OpenAI-совместимый сервис)
CLAUDE_MODELS = {
    "ashibalt/claude-sonnet-4-6": "Claude Sonnet 4.6",
    "ashibalt/claude-opus-4-7": "Claude Opus 4.7",
    "ashibalt/claude-opus-4-8": "Claude Opus 4.8",
}

# Остальные модели (Other)
OTHER_MODELS = {
    "meta/llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick 17B",
    "z-ai/glm-5.1": "GLM-5.1",
    "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super 120B",
    "ashibalt/kimi-2.7": "Kimi 2.7",
    "ashibalt/kimi-2.7-code": "Kimi 2.7 Code",
}

# Совет ИИ-моделей: запрос параллельно уходит трём моделям-участникам
# (см. handlers.COUNCIL_MEMBER_MODELS), их ответы анонимизируются как
# A/B/C, и модель-судья (handlers.COUNCIL_JUDGE_MODEL) выбирает лучший
# ответ/делает синтез и кратко объясняет выбор.
#
# Это "виртуальный" model_id — никакого отдельного провайдера для него
# нет, вся логика обрабатывается функцией call_ai_council() в handlers.py,
# которая просто несколько раз использует уже существующие call_ai().
COUNCIL_MODELS = {
    "council/ai-council": "Совет ИИ-моделей",
}

MODELS = {**CHATGPT_MODELS, **GEMINI_MODELS, **CLAUDE_MODELS, **OTHER_MODELS, **COUNCIL_MODELS}

# Человекочитаемые названия групп моделей (для заголовков экрана выбора модели)
GROUP_TITLES = {
    "chatgpt": "ChatGPT",
    "gemini": "Gemini",
    "claude": "Claude",
    "other": "Other",
    "council": "Совет ИИ-моделей",
}

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
        [InlineKeyboardButton(text="Claude", callback_data="model_group_claude")],
        [InlineKeyboardButton(text="Other", callback_data="model_group_other")],
        [InlineKeyboardButton(text="Совет ИИ-моделей", callback_data="model_group_council")],
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
    elif group == "claude":
        for model_id, model_name in CLAUDE_MODELS.items():
            label = f"[x] {model_name}" if model_id == current else model_name
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    elif group == "other":
        for model_id, model_name in OTHER_MODELS.items():
            label = f"[x] {model_name}" if model_id == current else model_name
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])
    elif group == "council":
        for model_id, model_name in COUNCIL_MODELS.items():
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
        [InlineKeyboardButton(text="Отклонённые", callback_data="admin_list_rejected")],
        [InlineKeyboardButton(text="Статистика", callback_data="admin_stats")],
    ])
