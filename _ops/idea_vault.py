"""
idea_vault.py — Quick idea/strategy/bug video capture store (display-only).

Ek chhota gallery store: koi bhi screen-recording / clip drag-drop se upload
karo, tag (idea/strategy/bug) + note ke saath turant card ban jaata hai.
JSON store `data/idea_vault.json`, files `data/idea_clips/`. Live trading /
order / Dhan path se ZERO connection — pure utility.

Streaming ke liye HTTP Range logic CODE7 (`stream_video`) se port hui hai.
"""
import os
import re
import json
import time
import uuid
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CLIPS_DIR = os.path.join(DATA_DIR, "idea_clips")
STORE_PATH = os.path.join(DATA_DIR, "idea_vault.json")

VALID_TAGS = ("idea", "strategy", "bug")
_VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".m4v")
_lock = threading.Lock()


# ── store ────────────────────────────────────────────────────
def _ensure_dirs():
    os.makedirs(CLIPS_DIR, exist_ok=True)


def load():
    if not os.path.exists(STORE_PATH):
        return {"ideas": []}
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
            if "ideas" not in d:
                d["ideas"] = []
            return d
    except (json.JSONDecodeError, IOError):
        return {"ideas": []}


def save(data):
    _ensure_dirs()
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STORE_PATH)


def _new_id():
    return uuid.uuid4().hex[:10]


# ── mime ─────────────────────────────────────────────────────
def video_mime(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".mov": "video/quicktime",
    }.get(ext, "application/octet-stream")


# ── list / get ───────────────────────────────────────────────
def list_ideas(tag=None, q=None):
    ideas = load()["ideas"]
    if tag and tag in VALID_TAGS:
        ideas = [i for i in ideas if i.get("tag") == tag]
    if q:
        ql = q.strip().lower()
        ideas = [i for i in ideas
                 if ql in (i.get("title", "") + " " + i.get("note", "")).lower()]
    # newest first
    return sorted(ideas, key=lambda i: i.get("ctime", 0), reverse=True)


def get(vid):
    for i in load()["ideas"]:
        if i.get("id") == vid:
            return i
    return None


def clip_path(vid):
    it = get(vid)
    if not it:
        return None
    p = os.path.join(CLIPS_DIR, it.get("filename", ""))
    return p if os.path.isfile(p) else None


# ── mutate ───────────────────────────────────────────────────
def add(file_storage, title="", note="", tag="idea"):
    """Save an uploaded werkzeug FileStorage into the clips dir + a store entry."""
    _ensure_dirs()
    orig = file_storage.filename or "clip.mp4"
    ext = os.path.splitext(orig)[1].lower()
    if ext not in _VIDEO_EXT:
        raise ValueError(f"unsupported file type: {ext or '(none)'}")
    if tag not in VALID_TAGS:
        tag = "idea"

    vid = _new_id()
    fname = f"{vid}{ext}"
    dest = os.path.join(CLIPS_DIR, fname)
    file_storage.save(dest)

    entry = {
        "id": vid,
        "title": (title or "").strip() or os.path.splitext(orig)[0],
        "note": (note or "").strip(),
        "tag": tag,
        "filename": fname,
        "orig_name": orig,
        "size": os.path.getsize(dest),
        "ctime": time.time(),
    }
    with _lock:
        data = load()
        data["ideas"].append(entry)
        save(data)
    return entry


def update(vid, fields):
    allowed = ("title", "note", "tag")
    with _lock:
        data = load()
        for i in data["ideas"]:
            if i.get("id") == vid:
                for k in allowed:
                    if k in fields:
                        v = fields[k]
                        if k == "tag" and v not in VALID_TAGS:
                            continue
                        i[k] = (v or "").strip() if isinstance(v, str) else v
                save(data)
                return i
    return None


def delete(vid):
    with _lock:
        data = load()
        keep, removed = [], None
        for i in data["ideas"]:
            if i.get("id") == vid:
                removed = i
            else:
                keep.append(i)
        if removed is None:
            return False
        data["ideas"] = keep
        save(data)
    if removed:
        p = os.path.join(CLIPS_DIR, removed.get("filename", ""))
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    return True
