from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

CHATGPT_MODELS = {
    "freemodel/gpt-5.4-nano": "GPT 5.4 Nano",
    "freemodel/gpt-5.5": "GPT 5.5",
}

OTHER_MODELS = {
    "qwen/qwen3.5-397b-a17b": "Qwen 3.5 397B",
    "deepseek-ai/deepseek-v4-pro": "DeepSeek V4 Pro",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "Nemotron Super 49B",
    "moonshotai/kimi-k2.6": "Kimi K2.6",
}

VISION_MODELS_DICT = {
    "meta/llama-4-maverick-17b-128e-instruct": "Llama 4 Maverick Vision",
    "meta/llama-3.2-11b-vision-instruct": "Llama 3.2 11B Vision",
}

MODELS = {**CHATGPT_MODELS, **OTHER_MODELS, **VISION_MODELS_DICT}
VISION_MODELS = set(CHATGPT_MODELS.keys()) | set(VISION_MODELS_DICT.keys())

def model_select_keyboard(current: str = "") -> InlineKeyboardMarkup:
    buttons = []
    buttons.append([InlineKeyboardButton(text="--- ChatGPT ---", callback_data="noop")])
    for model_id, model_name in CHATGPT_MODELS.items():
        label = f"[x] {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])

    buttons.append([InlineKeyboardButton(text="--- Other ---", callback_data="noop")])
    for model_id, model_name in OTHER_MODELS.items():
        label = f"[x] {model_name}" if model_id == current else model_name
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"model_{model_id}")])

    buttons.append([InlineKeyboardButton(text="Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
