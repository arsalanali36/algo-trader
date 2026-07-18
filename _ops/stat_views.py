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


def _read():
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _write(views):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(views, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PATH)


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
    return _read()


def create_view(name, strategies):
    name = (name or "").strip()
    if not name:
        raise ValueError("name required")
    strategies = _clean_strats(strategies)
    with _lock:
        views = _read()
        new_id = (max((v.get("id", 0) for v in views), default=0) + 1)
        view = {"id": new_id, "name": name, "strategies": strategies}
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
