"""basket_notes.py — user's own comment/note per option BASKET (pair) in Completed Trades.

A basket = the legs of one multi-leg trade grouped in the Orders "Pair / Basket"
view (by shared group_id, else an entry-time cluster). The user can write a free
note on a basket ("kyun liya, kya galti, kya seekha"); it's keyed by that basket's
stable key so it survives refresh / re-netting.

Pure user-config CRUD on data/basket_notes.json — no order/risk/Dhan path.
Mirrors stat_views.py (atomic write + .bak self-heal).
"""
import json
import os
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_ROOT, "data", "basket_notes.json")
_BAK = _PATH + ".bak"
_lock = threading.Lock()


def _read():
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        pass
    # self-heal from sibling backup (gitignored runtime data — see stat_views.py)
    try:
        with open(_BAK, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            try:
                os.makedirs(os.path.dirname(_PATH), exist_ok=True)
                with open(_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return data
    except (FileNotFoundError, ValueError):
        pass
    return {}


def _write(notes):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)
    if notes:
        try:
            with open(_BAK, "w", encoding="utf-8") as f:
                json.dump(notes, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def all_notes():
    """{key: {text, ts}} — every saved basket note."""
    return _read()


def set_note(key, text, ts=None):
    """Save/replace/clear one basket's note. Blank text deletes it. ts is an
    IST 'YYYY-MM-DD HH:MM' string passed by the caller (module can't clock here)."""
    key = (key or "").strip()
    if not key:
        raise ValueError("key required")
    text = (text or "").strip()
    with _lock:
        notes = _read()
        if text:
            notes[key] = {"text": text, "ts": ts or ""}
        else:
            notes.pop(key, None)
        _write(notes)
        return notes.get(key)
