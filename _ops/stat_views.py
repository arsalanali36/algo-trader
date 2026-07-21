"""stat_views.py — saved strategy-group "Views" for the Stats tab.

A View = a named set of strategy config-keys. User builds one from the Total
Summary "Compare" selection, saves it with a name, and can rename/delete it.
Applying a view filters the whole Stats tab to that strategy set's COMBINED
result (client-side).

Pure user-config CRUD on data/stat_views.json — no order/risk/Dhan path.
"""
import json
import os
import threading

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_ROOT, "data", "stat_views.json")
_lock = threading.Lock()


_BAK = _PATH + ".bak"


def _read():
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        pass
    # Main file missing/corrupt → self-heal from the sibling backup if it has good
    # data. stat_views.json is gitignored runtime data and was lost once to a VPS
    # cleanup (2026-07-21, no backup existed then) — this makes a future loss recover.
    try:
        with open(_BAK, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            try:
                os.makedirs(os.path.dirname(_PATH), exist_ok=True)
                with open(_PATH, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
            return data
    except (FileNotFoundError, ValueError):
        pass
    return []


def _write(views):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(views, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)
    # Resilient backup — only when non-empty, so an empty write never clobbers a
    # good backup. _read() restores from this if the main file ever vanishes.
    if views:
        try:
            with open(_BAK, "w", encoding="utf-8") as f:
                json.dump(views, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _clean_strats(strategies):
    """De-dup + drop blanks/None, preserve order, cast to str."""
    seen, out = set(), []
    for s in (strategies or []):
        s = str(s).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def list_views():
    views = _read()
    for v in views:
        v.setdefault("kind", "live")   # legacy views = live
    return views


def create_view(name, strategies, kind="live"):
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    kind = "bt" if str(kind) == "bt" else "live"
    strategies = _clean_strats(strategies)
    with _lock:
        views = _read()
        new_id = (max((v.get("id", 0) for v in views), default=0) + 1)
        # kind='live' → strategies are strategy config-keys; 'bt' → run slugs
        view = {"id": new_id, "name": name, "kind": kind, "strategies": strategies}
        views.append(view)
        _write(views)
        return view


def update_view(view_id, name=None, strategies=None):
    with _lock:
        views = _read()
        for v in views:
            if v.get("id") == view_id:
                if name is not None:
                    nm = str(name).strip()
                    if not nm:
                        raise ValueError("name cannot be blank")
                    v["name"] = nm
                if strategies is not None:
                    v["strategies"] = _clean_strats(strategies)
                _write(views)
                return v
        raise KeyError(f"view {view_id} not found")


def delete_view(view_id):
    with _lock:
        views = _read()
        kept = [v for v in views if v.get("id") != view_id]
        if len(kept) == len(views):
            raise KeyError(f"view {view_id} not found")
        _write(kept)
        return True
