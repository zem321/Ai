import json
import os

# Railway: добавь Volume с mount path /data для постоянного хранения
# Если /data недоступен — используем /tmp (сбросится при рестарте, но админ одобряется автоматически)
_data_dir = "/data"
if not os.path.exists(_data_dir):
    try:
        os.makedirs(_data_dir, exist_ok=True)
    except Exception:
        _data_dir = "/tmp"

DB_FILE = os.path.join(_data_dir, "users_db.json")


def _load() -> dict:
    if not os.path.exists(DB_FILE):
        return {"approved": [], "pending": [], "rejected": []}
    try:
        with open(DB_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"approved": [], "pending": [], "rejected": []}


def _save(data: dict):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_approved(user_id: int) -> bool:
    return user_id in _load()["approved"]

def is_pending(user_id: int) -> bool:
    return user_id in _load()["pending"]

def is_rejected(user_id: int) -> bool:
    return user_id in _load()["rejected"]

def add_pending(user_id: int):
    data = _load()
    if user_id not in data["pending"] and user_id not in data["approved"]:
        data["pending"].append(user_id)
        _save(data)

def approve_user(user_id: int):
    data = _load()
    for key in ["pending", "rejected"]:
        if user_id in data[key]:
            data[key].remove(user_id)
    if user_id not in data["approved"]:
        data["approved"].append(user_id)
    _save(data)

def reject_user(user_id: int):
    data = _load()
    if user_id in data["pending"]:
        data["pending"].remove(user_id)
    if user_id not in data["rejected"]:
        data["rejected"].append(user_id)
    _save(data)

def revoke_user(user_id: int):
    data = _load()
    if user_id in data["approved"]:
        data["approved"].remove(user_id)
    if user_id not in data["rejected"]:
        data["rejected"].append(user_id)
    _save(data)

def get_all_approved() -> list:
    return _load()["approved"]

def get_all_pending() -> list:
    return _load()["pending"]
