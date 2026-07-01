"""
daily_state.py — persists daily counters to disk so service restarts don't reset them.

File: data/daily_state.json
Format: {"date": "YYYY-MM-DD", "webhook": {"strat|sym": 2}, "strategy": {"range|NIFTY": 1}}

Usage:
    import daily_state as ds
    ds.get("webhook", key)          # → int
    ds.inc("webhook", key)          # → new count
    ds.get_all("webhook")           # → {"strat|sym": 2, ...}
    ds.reset()                      # force reset (new day)
"""

from __future__ import annotations
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FILE = Path(__file__).parent / "data" / "daily_state.json"
_lock = threading.Lock()


def _ist_date() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _load() -> dict:
    try:
        d = json.loads(_FILE.read_text())
        if d.get("date") == _ist_date():
            return d
    except Exception:
        pass
    return {"date": _ist_date(), "webhook": {}, "strategy": {}}


def _save(d: dict) -> None:
    _FILE.parent.mkdir(exist_ok=True)
    _FILE.write_text(json.dumps(d))


def get(category: str, key: str) -> int:
    with _lock:
        return _load().get(category, {}).get(key, 0)


def inc(category: str, key: str) -> int:
    with _lock:
        d = _load()
        d.setdefault(category, {})[key] = d[category].get(key, 0) + 1
        _save(d)
        return d[category][key]


def get_all(category: str) -> dict:
    with _lock:
        return dict(_load().get(category, {}))


def set_val(category: str, key: str, value: int) -> None:
    with _lock:
        d = _load()
        d.setdefault(category, {})[key] = value
        _save(d)


def reset() -> None:
    with _lock:
        _save({"date": _ist_date(), "webhook": {}, "strategy": {}})
