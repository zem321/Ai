import json
import os

DB_FILE = "users_db.json"


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
