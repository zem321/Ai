from aiogram.fsm.state import State, StatesGroup

class BotStates(StatesGroup):
    main_menu = State()
    chat_mode = State()
    image_generate = State()
    image_edit = State()
    video_generate = State()
    video_edit = State()
# states.py
from aiogram.fsm.state import StatesGroup, State

class BotStates(StatesGroup):
    # ... существующие состояния ...
    
    image_edit = State()  # <-- ДОБАВЬ ЭТУ СТРОККУ
