from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Пользователь выбирает понятный уровень работы, а не техническое имя модели.
REASONING_LEVELS = {
    "fast": {
        "title": "⚡ Быстро",
        "model_id": "nvidia/nemotron-3-nano-30b-a3b",
    },
    "balanced": {
        "title": "⚖️ Обычно",
        "model_id": "qwen/qwen3.6-27b",
    },
    "expert": {
        "title": "🛠 Эксперт",
        "model_id": "z-ai/glm-5.2",
    },
}

DEFAULT_REASONING_LEVEL = "balanced"
DEFAULT_MODEL = REASONING_LEVELS[DEFAULT_REASONING_LEVEL]["model_id"]
LEVEL_MODELS = frozenset(
    level["model_id"] for level in REASONING_LEVELS.values()
)
MODEL_TO_LEVEL = {
    level["model_id"]: level_id
    for level_id, level in REASONING_LEVELS.items()
}

# Для каждого пользовательского уровня задан строгий порядок провайдеров.
MODEL_FALLBACK_CHAINS = {
    REASONING_LEVELS["fast"]["model_id"]: (
        REASONING_LEVELS["fast"]["model_id"],
        "gemini/gemini-3.5-flash-lite",
        "groq/llama-3.1-8b-instant",
    ),
    REASONING_LEVELS["balanced"]["model_id"]: (
        REASONING_LEVELS["balanced"]["model_id"],
        "gemini/gemini-3.6-flash",
        "nvidia/nemotron-3-super-120b-a12b",
    ),
    REASONING_LEVELS["expert"]["model_id"]: (
        REASONING_LEVELS["expert"]["model_id"],
        "gemini/gemini-3.6-flash",
        "groq/openai/gpt-oss-120b",
    ),
}

# Лёгкая vision-модель описывает фото перед передачей текстовым моделям.
VISION_BRIDGE_MODEL = "nvidia/nemotron-nano-12b-v2-vl"
DIRECT_VISION_MODELS = frozenset({
    REASONING_LEVELS["balanced"]["model_id"],
    VISION_BRIDGE_MODEL,
})

# Полный внутренний список нужен для проверки исходящих API-запросов.
MODELS = {
    "nvidia/nemotron-3-nano-30b-a3b": "Nemotron 3 Nano 30B A3B",
    "gemini/gemini-3.1-flash-lite": "Gemini 3.1 Flash-Lite",
    "gemini/gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
    "qwen/qwen3.6-27b": "Qwen 3.6 27B",
    "gemini/gemini-3.6-flash": "Gemini 3.6 Flash",
    "nvidia/nemotron-3-super-120b-a12b": "Nemotron 3 Super 120B A12B",
    "z-ai/glm-5.2": "GLM-5.2",
    "groq/llama-3.1-8b-instant": "Llama 3.1 8B Instant",
    "groq/openai/gpt-oss-120b": "GPT-OSS 120B",
    VISION_BRIDGE_MODEL: "Nemotron Nano 12B VL",
}


def reasoning_level_for_model(model_id: str) -> str:
    return MODEL_TO_LEVEL.get(model_id, DEFAULT_REASONING_LEVEL)


def reasoning_level_title(level_id: str) -> str:
    level = REASONING_LEVELS.get(
        level_id,
        REASONING_LEVELS[DEFAULT_REASONING_LEVEL],
    )
    return str(level["title"])

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

def reasoning_level_keyboard(current_model: str = "") -> InlineKeyboardMarkup:
    buttons = []
    current_level = reasoning_level_for_model(current_model)
    for level_id, level in REASONING_LEVELS.items():
        title = str(level["title"])
        label = f"✓ {title}" if level_id == current_level else title
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=f"reasoning_{level_id}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="Назад", callback_data="main_menu")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сменить уровень", callback_data="select_model"),
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
