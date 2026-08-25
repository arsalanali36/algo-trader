"""
pnl_journal.py — Monthly P&L journal (grid) + per-trade comments + media store.

Display-only, ZERO order/Dhan path. Reuses (Rule 6B):
  - order_store.trades_for(date)   → per-day netting (matches Orders day-view)
  - charges.option_charges         → date-aware tax
  - strategy_registry.label        → canonical strategy name
  - idea_vault.video_mime          → clip streaming mime

Data model per month: day-rows x strategy-columns; each cell aggregates that
(day, strategy, mode) into gross/tax/net + basket count, and drills into the
individual BASKETS (a hedged 2/4-leg position = 1 trade) with per-leg detail.

Notes  : data/journal_notes.json   { key: text }  (cell key OR trade key)
Media  : data/journal_media.json   { trade_key: [ {id,kind,filename,note,ctime,size} ] }
         files in data/journal_media/
"""
import os
import sys
import json
import time
import uuid
import shutil
import subprocess
import threading
import calendar
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa: F401
sys.path.insert(0, os.path.join(ROOT, "scratch", "nifty_trend"))  # charges

import order_store
try:
    import charges as _charges
except Exception:
    _charges = None
try:
    import strategy_registry as _reg
except Exception:
    _reg = None
try:
    import idea_vault as _iv          # video_mime reuse
except Exception:
    _iv = None

DATA_DIR = os.path.join(ROOT, "data")
NOTES_PATH = os.path.join(DATA_DIR, "journal_notes.json")
MEDIA_STORE = os.path.join(DATA_DIR, "journal_media.json")
MEDIA_DIR = os.path.join(DATA_DIR, "journal_media")
_lock = threading.Lock()

_VIDEO_EXT = (".webm", ".mp4", ".mkv", ".mov", ".m4v")
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")


# ── helpers ──────────────────────────────────────────────────
def _tax(t):
    if not _charges:
        return 0.0
    try:
        return round(_charges.option_charges(t.get("entry_price") or 0.0, t.get("exit_price") or 0.0,
                                             t.get("qty") or 0, entry_side=(t.get("entry") or "BUY"),
                                             when=t.get("exit_date")), 2)
    except Exception:
        return 0.0


def _fix_mojibake(s):
    """Registry labels store double-encoded bytes (UTF-8 read as cp1252) so em-dash
    shows as 'â€"'. Reverse it for DISPLAY only (journal-local; shared registry left
    untouched). ASCII round-trips unchanged; on any failure return original."""
    if not s or "Ã" not in s and "Â" not in s and "â" not in s:
        return s
    try:
        return s.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def _label(s):
    if _reg:
        try:
            return _fix_mojibake(_reg.label(s))
        except Exception:
            pass
    return s


def _mnorm(m):
    return "live" if (m or "") == "live" else "paper"


def _short(s):
    lab = _label(s)
    return tuple(lab.split(" - ", 1)) if " - " in lab else ("", lab)


# ── month builder (per-day netting, basket detail) ───────────
def build_month(year, month):
    d0 = dt.date(year, month, 1)
    last = calendar.monthrange(year, month)[1]
    D0 = d0.isoformat()
    D1 = dt.date(year, month, last).isoformat()

    cells = {}         # iso|strat|mode -> [g,t,n,baskets]
    trades = {}        # iso|strat|mode -> [ {id,g,tx,n,legs:[...]} ]
    strat_modes = {}
    basket = {}        # cellkey -> {basketkey: {gid,legs,g,tx}}

    for dd in range(1, last + 1):
        day = dt.date(year, month, dd)
        if day.weekday() >= 5:
            continue
        iso = day.isoformat()
        try:
            r = order_store.trades_for(iso)
        except Exception:
            continue
        for i, t in enumerate(r.get("details", [])):
            s = t.get("strategy") or "unknown"
            m = _mnorm(t.get("mode"))
            g = float(t.get("pnl") or 0.0); tx = _tax(t)
            ep = t.get("entry_price") or 0.0; xp = t.get("exit_price") or 0.0
            side = t.get("entry") or "BUY"
            pts = round((xp - ep) if side == "BUY" else (ep - xp), 2)
            leg = {"sy": t.get("sym"), "sd": side, "q": t.get("qty"),
                   "ep": round(ep, 2), "xp": round(xp, 2), "pt": pts,
                   "g": round(g), "tx": round(tx),
                   "et": t.get("entry_time"), "xt": t.get("exit_time"),
                   "rs": t.get("exit_reason") or ""}
            gid = t.get("group_id") or ""
            bkey = gid if gid else f"u{i}"
            bk = basket.setdefault(f"{iso}|{s}|{m}", {})
            b = bk.setdefault(bkey, {"gid": gid, "legs": [], "g": 0.0, "tx": 0.0})
            b["legs"].append(leg); b["g"] += g; b["tx"] += tx
            strat_modes.setdefault(s, set()).add(m)

    for key, bk in basket.items():
        lst = []; cg = ctx = 0.0
        for bkey, b in bk.items():
            lst.append({"id": bkey, "g": round(b["g"]), "tx": round(b["tx"]),
                        "n": round(b["g"] - b["tx"]), "legs": b["legs"]})
            cg += b["g"]; ctx += b["tx"]
        trades[key] = lst
        cells[key] = [round(cg), round(ctx), round(cg - ctx), len(lst)]

    def sortkey(s):
        idp, _ = _short(s)
        if idp and idp[0].isdigit():
            return (0, idp, s)
        if s == "manual":
            return (2, "", s)
        return (1, _label(s), s)

    strat_list = []
    for s in sorted(strat_modes.keys(), key=sortkey):
        idp, nm = _short(s)
        strat_list.append({"id": s, "idp": idp, "name": nm, "modes": sorted(strat_modes[s])})

    weeks = []; cur = None
    for dd in range(1, last + 1):
        day = dt.date(year, month, dd); dow = day.weekday()
        if dow >= 5:
            continue
        mon = day - dt.timedelta(days=dow); wk = mon.isoformat()
        if not cur or cur["wk"] != wk:
            cur = {"wk": wk, "days": []}; weeks.append(cur)
        cur["days"].append({"d": dd, "dow": ["M", "T", "W", "T", "F"][dow], "iso": iso if False else day.isoformat()})

    return {"year": year, "month": month,
            "label": d0.strftime("%B %Y"),
            "gen": dt.datetime.now().strftime("%d %b %Y %H:%M"),
            "strats": strat_list, "weeks": weeks, "cells": cells, "trades": trades}


# ── notes (cell + trade comments) ────────────────────────────
def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def get_notes():
    return _load(NOTES_PATH, {})


def set_note(key, text):
    with _lock:
        d = _load(NOTES_PATH, {})
        if text is None or text == "":
            d.pop(key, None)
        else:
            d[key] = text
        _save(NOTES_PATH, d)
    return True


# ── media (video notes + images per trade) ───────────────────
def _load_media():
    return _load(MEDIA_STORE, {})


def list_media(trade_key):
    return _load_media().get(trade_key, [])


def all_media():
    return _load_media()


def add_media(file_storage, trade_key, note=""):
    os.makedirs(MEDIA_DIR, exist_ok=True)
    orig = file_storage.filename or "clip.webm"
    ext = os.path.splitext(orig)[1].lower()
    if ext in _VIDEO_EXT:
        kind = "video"
    elif ext in _IMG_EXT:
        kind = "img"
    else:
        raise ValueError(f"unsupported file type: {ext or '(none)'}")
    mid = uuid.uuid4().hex[:12]
    fname = f"{mid}{ext}"
    file_storage.save(os.path.join(MEDIA_DIR, fname))
    entry = {"id": mid, "kind": kind, "filename": fname,
             "note": (note or "").strip(), "orig": orig,
             "size": os.path.getsize(os.path.join(MEDIA_DIR, fname)),
             "ctime": time.time()}
    if kind == "video":
        entry["compressing"] = True   # background thread will compress + replace
    with _lock:
        d = _load_media()
        d.setdefault(trade_key, []).append(entry)
        _save(MEDIA_STORE, d)
    if kind == "video":
        threading.Thread(target=_compress_worker, args=(mid,), daemon=True).start()
    return entry


# ── auto-compress uploaded videos (H.264 CRF 30 "smallest", CPU/libx264 on VPS) ─
def _update_media_entry(mid, **fields):
    with _lock:
        d = _load_media()
        for lst in d.values():
            for it in lst:
                if it.get("id") == mid:
                    it.update(fields)
                    _save(MEDIA_STORE, d)
                    return True
    return False


def _compress_worker(mid):
    """Re-encode an uploaded video in place to H.264 CRF 30 (smallest) and replace
    the original — same target as the CODE10 compressor's best preset, but libx264
    (CPU) since the VPS has no NVIDIA GPU. On any failure the original is kept."""
    try:
        ff = shutil.which("ffmpeg")
        cur = None
        for lst in _load_media().values():
            for it in lst:
                if it.get("id") == mid:
                    cur = it
        if not cur:
            return
        src = os.path.join(MEDIA_DIR, cur["filename"])
        if not ff or not os.path.isfile(src):
            _update_media_entry(mid, compressing=False)
            return
        orig_size = os.path.getsize(src)
        newfname = f"{mid}.mp4"
        tmp = os.path.join(MEDIA_DIR, f"{mid}.tmp.mp4")
        cmd = [ff, "-i", src, "-c:v", "libx264", "-crf", "30", "-preset", "medium",
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-y", tmp]
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            if os.path.exists(tmp):
                os.remove(tmp)
            _update_media_entry(mid, compressing=False)
            return
        new_size = os.path.getsize(tmp)
        # only keep the compressed version if it actually saved space
        if new_size >= orig_size * 0.97:
            os.remove(tmp)
            _update_media_entry(mid, compressing=False)  # already small enough
            return
        newpath = os.path.join(MEDIA_DIR, newfname)
        os.replace(tmp, newpath)
        if cur["filename"] != newfname and os.path.isfile(src):
            try:
                os.remove(src)
            except OSError:
                pass
        saved_pct = round((orig_size - new_size) / orig_size * 100) if orig_size else 0
        _update_media_entry(mid, filename=newfname, size=new_size, orig_size=orig_size,
                            compressed=True, compressing=False, saved_pct=saved_pct)
    except Exception as e:
        print("[journal] compress fail:", mid, e, flush=True)
        _update_media_entry(mid, compressing=False)


def update_media_note(mid, note):
    with _lock:
        d = _load_media()
        for tk, lst in d.items():
            for it in lst:
                if it.get("id") == mid:
                    it["note"] = (note or "").strip()
                    _save(MEDIA_STORE, d)
                    return True
    return False


def delete_media(mid):
    with _lock:
        d = _load_media()
        for tk, lst in list(d.items()):
            for it in list(lst):
                if it.get("id") == mid:
                    try:
                        os.remove(os.path.join(MEDIA_DIR, it["filename"]))
                    except OSError:
                        pass
                    lst.remove(it)
                    if not lst:
                        d.pop(tk, None)
                    _save(MEDIA_STORE, d)
                    return True
    return False


def media_path(mid):
    for tk, lst in _load_media().items():
        for it in lst:
            if it.get("id") == mid:
                p = os.path.join(MEDIA_DIR, it["filename"])
                return p if os.path.isfile(p) else None
    return None


def media_mime(path):
    ext = os.path.splitext(path)[1].lower()
    imap = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp"}
    if ext in imap:
        return imap[ext]
    if _iv:
        return _iv.video_mime(path)
    return "application/octet-stream"
