#!/usr/bin/env python3
"""
dashboard_auth.py — single-password login gate for the trader dashboard.

Why: trader_dashboard.py (port 5099) is exposed on the public internet
(VPS + Caddy). Anyone with the URL could control LIVE trading. This adds a
session-based password gate over the whole app. The TradingView webhook
(/api/webhook/tv) stays OPEN — it has its own token auth and is machine-to-
machine, never a browser session.

Credentials live in data/auth.json (gitignored) — a password HASH, never
plaintext. Set/change via set_password.py. The Flask secret_key is generated
once and persisted in the same file so login sessions survive restarts.
"""

import json
import secrets
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

# data/ is at the project ROOT; this file lives in _core/ → parent.parent
AUTH_FILE = Path(__file__).resolve().parent.parent / "data" / "auth.json"


def _load() -> dict:
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    AUTH_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")


def is_configured() -> bool:
    """True once a username+password has been set."""
    d = _load()
    return bool(d.get("username") and d.get("password_hash"))


def get_secret_key() -> str:
    """Persistent Flask secret_key — generated once, reused across restarts
    so sessions (login cookies) don't invalidate on every deploy/restart."""
    d = _load()
    key = d.get("secret_key")
    if not key:
        key = secrets.token_hex(32)
        d["secret_key"] = key
        _save(d)
    return key


def set_credentials(username: str, password: str) -> None:
    """Create or overwrite the single login. Password is stored hashed."""
    username = (username or "").strip()
    if not username:
        raise ValueError("username required")
    if not password or len(password) < 4:
        raise ValueError("password must be at least 4 characters")
    d = _load()
    d["username"] = username
    d["password_hash"] = generate_password_hash(password)
    if not d.get("secret_key"):
        d["secret_key"] = secrets.token_hex(32)
    _save(d)


def verify(username: str, password: str) -> bool:
    """Constant-time-ish credential check."""
    d = _load()
    stored_user = d.get("username")
    stored_hash = d.get("password_hash")
    if not stored_user or not stored_hash:
        return False
    if (username or "").strip() != stored_user:
        # still run a hash check to reduce username-enumeration timing signal
        check_password_hash(stored_hash, password or "")
        return False
    return check_password_hash(stored_hash, password or "")


def username() -> str:
    return _load().get("username", "")
