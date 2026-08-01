"""report_notes.py — server-side observation notes for the Daily Report page.

A note = free text + colour + optional images, anchored to a UI element
(a KPI tile / table cell / chart bar) by a stable text `anchor`, stored
per-DATE so it shows across devices and lands in the printed PDF.

Pure user-content CRUD on data/report_notes/<date>.json — no order/risk/Dhan
path. Same defensive read/atomic-write pattern as _ops/stat_views.py.

File shape:  {"date": "YYYY-MM-DD", "notes": [ {note}, ... ]}
Note shape:  {id, anchor, text, color, images:[fname...], ts, updated}
"""
import json
import os
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR = os.path.join(_ROOT, "data", "report_notes")
_IMG_DIR = os.path.join(_ROOT, "data", "report_note_images")
_lock = threading.Lock()

_COLORS = {"r", "g", "b", "y"}


def _date_ok(d):
    return isinstance(d, str) and len(d) == 10 and d[4] == "-" and d[7] == "-"


def _path(date):
    return os.path.join(_DIR, f"{date}.json")


def _read(date):
    try:
        with open(_path(date), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("notes"), list):
            return data
    except (FileNotFoundError, ValueError):
        pass
    return {"date": date, "notes": []}


def _write(date, data):
    os.makedirs(_DIR, exist_ok=True)
    tmp = _path(date) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _path(date))
    # sibling backup (gitignored runtime data — VPS cleanup once wiped such files)
    try:
        with open(_path(date) + ".bak", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _new_id():
    return "n" + str(int(time.time() * 1000))


# ----------------------------------------------------------------- public ----
def list_notes(date):
    if not _date_ok(date):
        return []
    with _lock:
        return _read(date).get("notes", [])


def add_note(date, anchor, text, color="b", images=None):
    if not _date_ok(date):
        raise ValueError("bad date")
    color = color if color in _COLORS else "b"
    note = {
        "id": _new_id(),
        "anchor": str(anchor or "")[:200],
        "text": str(text or "")[:4000],
        "color": color,
        "images": list(images or []),
        "ts": int(time.time()),
        "updated": int(time.time()),
    }
    with _lock:
        data = _read(date)
        data["notes"].append(note)
        _write(date, data)
    return note


def update_note(date, note_id, text=None, color=None, images=None):
    if not _date_ok(date):
        raise ValueError("bad date")
    with _lock:
        data = _read(date)
        for n in data["notes"]:
            if n.get("id") == note_id:
                if text is not None:
                    n["text"] = str(text)[:4000]
                if color is not None and color in _COLORS:
                    n["color"] = color
                if images is not None:
                    n["images"] = list(images)
                n["updated"] = int(time.time())
                _write(date, data)
                return n
    return None


def delete_note(date, note_id):
    if not _date_ok(date):
        raise ValueError("bad date")
    with _lock:
        data = _read(date)
        before = len(data["notes"])
        data["notes"] = [n for n in data["notes"] if n.get("id") != note_id]
        if len(data["notes"]) != before:
            _write(date, data)
            return True
    return False


def image_dir(date):
    d = os.path.join(_IMG_DIR, date)
    os.makedirs(d, exist_ok=True)
    return d


if __name__ == "__main__":
    import datetime
    today = datetime.date.today().isoformat()
    n = add_note(today, "KPI · Net P&L", "test note", "g")
    print("added", n["id"], "→", len(list_notes(today)), "notes")
    delete_note(today, n["id"])
    print("deleted →", len(list_notes(today)), "notes")
