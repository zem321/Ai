import json
import os

DB_FILE = "users_db.json"


def _load() -> dict:
    if not os.path.exists(DB_FILE):
        return {"approved": [], "pending": [], "rejected": [], "user_models": {}, "history": {}}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "approved" not in data: data["approved"] = []
            if "pending" not in data: data["pending"] = []
            if "rejected" not in data: data["rejected"] = []
            if "user_models" not in data: data["user_models"] = {}
            if "history" not in data: data["history"] = {}
            return data
    except Exception:
        return {"approved": [], "pending": [], "rejected": [], "user_models": {}, "history": {}}


def _save(data: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_approved(user_id: int) -> bool:
    return user_id in _load()["approved"]


def is_pending(user_id: int) -> bool:
    return user_id in _load()["pending"]


def is_rejected(user_id: int) -> bool:
    return user_id in _load()["rejected"]


def add_pending(user_id: int):
    db = _load()
    if user_id not in db["pending"] and user_id not in db["approved"]:
        db["pending"].append(user_id)
        _save(db)


def approve_user(user_id: int):
    db = _load()
    for key in ["pending", "rejected"]:
        if user_id in db[key]:
            db[key].remove(user_id)
    if user_id not in db["approved"]:
        db["approved"].append(user_id)
    _save(db)


def reject_user(user_id: int):
    db = _load()
    if user_id in db["pending"]:
        db["pending"].remove(user_id)
    if user_id not in db["rejected"]:
        db["rejected"].append(user_id)
    _save(db)


def revoke_user(user_id: int):
    db = _load()
    if user_id in db["approved"]:
        db["approved"].remove(user_id)
    if user_id not in db["rejected"]:
        db["rejected"].append(user_id)
    _save(db)


def get_all_approved() -> list:
    return _load()["approved"]


def get_all_pending() -> list:
    return _load()["pending"]


# ── Работа с настройками моделей пользователей ──

def get_user_model(user_id: int) -> str:
    db = _load()
    return db["user_models"].get(str(user_id), "gpt-5.5")


def set_user_model(user_id: int, model: str):
    db = _load()
    db["user_models"][str(user_id)] = model
    _save(db)


# ── Работа с контекстом диалогов (Память) ──

def get_history(user_id: int) -> list:
    db = _load()
    return db["history"].get(str(user_id), [])


def add_history_message(user_id: int, role: str, content):
    db = _load()
    uid = str(user_id)
    if uid not in db["history"]:
        db["history"][uid] = []
    
    # Если отправлена структура Vision (с base64), сохраняем только текст, чтобы не забивать диск
    if isinstance(content, list):
        text_content = ""
        for item in content:
            if item.get("type") == "text":
                text_content = item.get("text", "")
        db["history"][uid].append({"role": role, "content": f"[Фотоанализ] {text_content}"})
    else:
        db["history"][uid].append({"role": role, "content": content})
    
    # Лимит памяти — последние 20 сообщений
    if len(db["history"][uid]) > 20:
        db["history"][uid] = db["history"][uid][-20:]
    _save(db)


def clear_history(user_id: int):
    db = _load()
    uid = str(user_id)
    if uid in db["history"]:
        db["history"][uid] = []
        _save(db)
