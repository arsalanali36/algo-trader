"""
price_triggers.py — NIFTY/BANKNIFTY spot price-trigger conditional orders.

Idea: koi broker index (NIFTY spot) pe order nahi deta. Par hamara algo NIFTY
spot khud har ~1.5s poll kar raha hai (ltp_poller → shared_ltp_cache), to ham
khud watch kar sakte hain: user ek level (e.g. 24,300) + direction (upar/neeche
cross) deta hai; jab live spot us level ko cross kare, ham us waqt ki ATM ±offset
CE/PE pe chosen qty/side ka order khud fire kar dete hain — execution_gateway
(poora RMS) ke through, bilkul strategy jaisa.

Ye module SIRF state + decision hai (koi broker/order import nahi) — isliye
standalone test ho jaata hai. Firing (execute_signal) caller karta hai
(trader_dashboard.trigger_watch_loop). Design mirrors _tsl_state persist idiom.

State file: data/pending_triggers.json  →  {"day": "YYYY-MM-DD", "triggers": [...]}
Day-scoped: agle din load pe apne-aap khaali (EOD auto-cancel, user ki choice).

Trigger dict:
  {id, symbol, level, direction("above"|"below"), opt_type("CE"|"PE"),
   offset, side("BUY"|"SELL"), lots, mode, broker, arm_spot, created_ts,
   fired(bool), status("armed"|"fired"|"fire_failed"|"cancelled"|"expired"),
   result(str)}

Concurrency: dashboard process CRUD karta hai (add/remove), monitor process fire
(mark_fired). Dono same JSON pe read-modify-write karte hain ek in-process lock ke
saath — cross-process race window bahut chhota (shared_ltp_cache jaisa
last-writer-wins). Double-fire ke against asli guard monitor loop ka in-memory
"already-fired" set + fire-se-pehle mark_fired-persist hai (dono trader_dashboard me).
"""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "data" / "pending_triggers.json"
_FILE.parent.mkdir(exist_ok=True)
_LOCK = threading.Lock()

_VALID_SYMBOL = {"NIFTY", "BANKNIFTY"}
_VALID_DIR = {"above", "below"}
_VALID_OPT = {"CE", "PE"}
_VALID_SIDE = {"BUY", "SELL"}
_VALID_MODE = {"paper", "live"}
_VALID_BROKER = {"dhan", "kite"}


def _today_ist():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _read_raw():
    try:
        d = json.loads(_FILE.read_text())
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"day": _today_ist(), "triggers": []}


def _write_raw(day, triggers):
    try:
        _FILE.write_text(json.dumps({"day": day, "triggers": triggers}))
    except Exception:
        pass


def _load_today():
    """Return today's trigger list. If the stored day is not today, the whole
    set is stale (EOD auto-cancel) → return [] and rewrite so the file also
    reflects the reset. Caller already holds _LOCK where mutation follows."""
    raw = _read_raw()
    today = _today_ist()
    if raw.get("day") != today:
        _write_raw(today, [])
        return []
    trg = raw.get("triggers")
    return trg if isinstance(trg, list) else []


def would_fire(direction, level, spot):
    """Pure predicate: is `spot` on the trigger side of `level`?
    above → spot has risen to/above level; below → spot has fallen to/at level."""
    try:
        level = float(level)
        spot = float(spot)
    except (TypeError, ValueError):
        return False
    if direction == "above":
        return spot >= level
    if direction == "below":
        return spot <= level
    return False


def suggest_direction(level, spot):
    """Default direction from where spot is NOW relative to level."""
    try:
        return "above" if float(level) > float(spot) else "below"
    except (TypeError, ValueError):
        return "above"


def list_triggers():
    with _LOCK:
        return list(_load_today())


def add_trigger(t, arm_spot):
    """Validate + append a new trigger. Returns (ok, trigger_or_errmsg).
    Rejects an armed trigger whose condition is ALREADY true at arm_spot
    (would fire instantly — almost always a wrong direction/level; user
    should re-check rather than get a surprise fill)."""
    symbol = str(t.get("symbol", "NIFTY")).upper()
    direction = str(t.get("direction") or "").lower()
    opt_type = str(t.get("opt_type") or "").upper()
    side = str(t.get("side") or "").upper()
    mode = str(t.get("mode", "paper")).lower()
    broker = str(t.get("broker", "dhan")).lower()
    try:
        level = float(t.get("level"))
    except (TypeError, ValueError):
        return False, "Level (NIFTY price) galat hai"
    try:
        lots = int(t.get("lots", 1))
    except (TypeError, ValueError):
        return False, "Lots galat hai"
    try:
        offset = int(t.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    # per-position SL / target in premium POINTS — default 20pt SL, 30pt target
    try:
        sl_pt = float(t.get("sl_pt", 20))
    except (TypeError, ValueError):
        sl_pt = 20.0
    try:
        tp_pt = float(t.get("tp_pt", 30))
    except (TypeError, ValueError):
        tp_pt = 30.0

    if symbol not in _VALID_SYMBOL:
        return False, f"Symbol {symbol} supported nahi"
    if direction not in _VALID_DIR:
        # infer from arm_spot if omitted
        direction = suggest_direction(level, arm_spot)
    if opt_type not in _VALID_OPT:
        return False, "CE/PE select karo"
    if side not in _VALID_SIDE:
        return False, "BUY/SELL select karo"
    if mode not in _VALID_MODE:
        mode = "paper"
    if broker not in _VALID_BROKER:
        broker = "dhan"
    if lots < 1:
        return False, "Lots >= 1 hona chahiye"
    if level <= 0:
        return False, "Level > 0 hona chahiye"

    if arm_spot is not None and would_fire(direction, level, arm_spot):
        return False, (f"Spot abhi {float(arm_spot):.0f} hai — "
                       f"'{direction}' {level:.0f} turant fire ho jaata. "
                       f"Direction/level check karo.")

    trg = {
        "id": "trg_" + uuid.uuid4().hex[:8],
        "symbol": symbol,
        "level": round(level, 2),
        "direction": direction,
        "opt_type": opt_type,
        "offset": offset,
        "side": side,
        "lots": lots,
        "sl_pt": sl_pt,
        "tp_pt": tp_pt,
        "mode": mode,
        "broker": broker,
        "arm_spot": round(float(arm_spot), 2) if arm_spot is not None else None,
        "created_ts": int(time.time()),
        "fired": False,
        "status": "armed",
        "result": "",
    }
    with _LOCK:
        today = _today_ist()
        triggers = _load_today()
        triggers.append(trg)
        _write_raw(today, triggers)
    return True, trg


def remove_trigger(tid):
    """Delete a trigger by id (armed or already-fired row). Returns True if found."""
    with _LOCK:
        today = _today_ist()
        triggers = _load_today()
        n = len(triggers)
        triggers = [x for x in triggers if x.get("id") != tid]
        _write_raw(today, triggers)
        return len(triggers) != n


def cancel_all(reason="cancelled"):
    """Clear every trigger (EOD / market-close). Returns count removed."""
    with _LOCK:
        today = _today_ist()
        triggers = _load_today()
        n = len([x for x in triggers if not x.get("fired")])
        _write_raw(today, [])
        return n


def claim(tid):
    """Atomic one-shot claim: if the row is still armed & not fired, flip it to
    fired/status="firing" and return True; else False. Read-modify-write under
    lock so a concurrent add can't clobber the fired flag. The caller places the
    order ONLY on True — this is the primary double-fire guard (persist-before-
    execute). set_result() records the outcome afterwards."""
    with _LOCK:
        today = _today_ist()
        triggers = _load_today()
        for x in triggers:
            if x.get("id") == tid:
                if x.get("fired"):
                    return False  # already claimed elsewhere
                x["fired"] = True
                x["status"] = "firing"
                x["result"] = "firing..."
                x["fired_ts"] = int(time.time())
                _write_raw(today, triggers)
                return True
        return False


def set_result(tid, status, result):
    """Record the fire outcome on an already-claimed row (no fired-guard).
    Returns True if the row was found."""
    with _LOCK:
        today = _today_ist()
        triggers = _load_today()
        for x in triggers:
            if x.get("id") == tid:
                x["status"] = status
                x["result"] = result
                _write_raw(today, triggers)
                return True
        return False


def due_triggers(spot_by_symbol):
    """Return armed, not-yet-fired triggers whose condition is met given a
    {symbol: spot} map. Pure read — caller does the firing + mark_fired."""
    out = []
    with _LOCK:
        triggers = _load_today()
    for x in triggers:
        if x.get("fired"):
            continue
        if x.get("status") != "armed":
            continue
        spot = spot_by_symbol.get(x.get("symbol"))
        if spot is None:
            continue
        if would_fire(x.get("direction"), x.get("level"), spot):
            out.append(x)
    return out
