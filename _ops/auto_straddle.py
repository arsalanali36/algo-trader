"""
auto_straddle.py — Auto ATM straddle (SHORT) order state + basket-exit decision.

Sell ATM CE + ATM PE (equal lots). Exit is on the COMBINED premium (credit):
    entry_credit = ce_entry + pe_entry
    live_credit  = ce_ltp   + pe_ltp
    profit_pts   = entry_credit - live_credit        (premium FELL = seller profit)
      profit_pts >= tp_pt   -> "target"  (square off BOTH legs)
      profit_pts <= -sl_pt  -> "sl"      (credit rose sl_pt -> loss cap)

PURE state + decision (no broker / order / Dhan import) — standalone-testable,
mirrors price_triggers.py. Firing (2 legs via execution_gateway), squaring off
(both legs), and live LTP are the CALLER's job (trader_dashboard). Three entry
sources:
    schedule_920  (A) — 9:20 auto, per configured index
    manual        (B) — Quick Order "Sell ATM Straddle"
    alert:<type>  (C) — on an option-alert (straddle pop/crush, gamma spike)

State file: data/auto_straddles.json -> {"day": "YYYY-MM-DD", "straddles": [...]}
Day-scoped (agle din khaali). Restart-safe: the store IS the source of truth for
"already fired today?" and "still open?".

Straddle dict:
  {id, symbol, lots, mode, source, group_id, tp_pt, sl_pt, entry_credit,
   legs: [{opt_type, sec_id, trad_sym, entry_price, qty}, ...],
   status("open"|"target"|"sl"|"eod"|"manual"|"failed"|"closed"),
   result, created_ts, closed_ts, exit_credit}
"""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "data" / "auto_straddles.json"
_FILE.parent.mkdir(exist_ok=True)
_LOCK = threading.Lock()

_VALID_SYMBOL = {"NIFTY", "BANKNIFTY"}
_OPEN = "open"


def _today_ist():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _read_raw():
    try:
        d = json.loads(_FILE.read_text())
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"day": _today_ist(), "straddles": []}


def _write_raw(day, straddles):
    try:
        _FILE.write_text(json.dumps({"day": day, "straddles": straddles}))
    except Exception:
        pass


def _load_today():
    """Today's straddle list. If stored day != today, the whole set is stale
    (EOD auto-cancel) → return [] and rewrite. Caller holds _LOCK where mutation
    follows."""
    raw = _read_raw()
    today = _today_ist()
    if raw.get("day") != today:
        _write_raw(today, [])
        return []
    st = raw.get("straddles")
    return st if isinstance(st, list) else []


def list_today():
    with _LOCK:
        return list(_load_today())


def list_open():
    with _LOCK:
        return [s for s in _load_today() if s.get("status") == _OPEN]


def has_open(symbol):
    symbol = str(symbol).upper()
    with _LOCK:
        return any(s.get("status") == _OPEN and s.get("symbol") == symbol for s in _load_today())


def count_today(symbol, source_prefix=None):
    """How many straddles fired today for `symbol` (optionally only a given source
    prefix, e.g. 'alert' / 'schedule_920')."""
    symbol = str(symbol).upper()
    with _LOCK:
        rows = [s for s in _load_today() if s.get("symbol") == symbol]
    if source_prefix:
        rows = [s for s in rows if str(s.get("source", "")).startswith(source_prefix)]
    return len(rows)


def fired_920_today(symbol):
    """Restart-safe one-shot guard for the 9:20 scheduled fire."""
    symbol = str(symbol).upper()
    with _LOCK:
        return any(s.get("source") == "schedule_920" and s.get("symbol") == symbol
                   for s in _load_today())


def add(strad):
    """Append a straddle row (caller has already placed both legs). Returns the
    stored dict (with id/status/timestamps filled)."""
    row = {
        "id": "strad_" + uuid.uuid4().hex[:8],
        "symbol": str(strad.get("symbol", "NIFTY")).upper(),
        "lots": int(strad.get("lots", 1)),
        "mode": str(strad.get("mode", "paper")).lower(),
        "source": str(strad.get("source", "manual")),
        "group_id": strad.get("group_id", ""),
        "tp_pt": float(strad.get("tp_pt", 30)),
        "sl_pt": float(strad.get("sl_pt", 30)),
        "entry_credit": float(strad.get("entry_credit", 0)),
        "legs": strad.get("legs", []),
        "status": _OPEN,
        "result": "",
        "created_ts": int(time.time()),
        "closed_ts": None,
        "exit_credit": None,
    }
    with _LOCK:
        today = _today_ist()
        rows = _load_today()
        rows.append(row)
        _write_raw(today, rows)
    return row


def set_status(sid, status, result="", exit_credit=None):
    with _LOCK:
        today = _today_ist()
        rows = _load_today()
        for x in rows:
            if x.get("id") == sid:
                x["status"] = status
                x["result"] = result
                if exit_credit is not None:
                    x["exit_credit"] = round(float(exit_credit), 2)
                if status != _OPEN:
                    x["closed_ts"] = int(time.time())
                _write_raw(today, rows)
                return True
        return False


def get(sid):
    with _LOCK:
        for x in _load_today():
            if x.get("id") == sid:
                return dict(x)
    return None


def cancel_all():
    with _LOCK:
        _write_raw(_today_ist(), [])


def check_exit(entry_credit, ce_ltp, pe_ltp, tp_pt, sl_pt):
    """Pure basket-exit decision for a SHORT straddle.
    Returns (reason|None, live_credit|None, profit_pts|None).
    Bad/incomplete data (a leg <= 0) → (None, None, None) — NEVER fire on it."""
    try:
        ec = float(entry_credit); ce = float(ce_ltp); pe = float(pe_ltp)
        tp = float(tp_pt); sl = float(sl_pt)
    except (TypeError, ValueError):
        return None, None, None
    if ce <= 0 or pe <= 0 or ec <= 0:
        return None, None, None   # incomplete data — freeze, don't fire (TRAP #1 shape)
    live = ce + pe
    profit = ec - live            # premium fell = short straddle profit
    if tp > 0 and profit >= tp:
        return "target", live, profit
    if sl > 0 and profit <= -sl:
        return "sl", live, profit
    return None, live, profit


if __name__ == "__main__":
    # quick self-test of the pure basket-exit decision (entry credit 300)
    assert check_exit(300, 130, 140, 30, 30)[0] == "target"   # live 270, profit +30 -> target
    assert check_exit(300, 135, 140, 30, 30)[0] is None       # live 275, profit +25 -> hold
    assert check_exit(300, 170, 165, 30, 30)[0] == "sl"       # live 335, profit -35 -> sl
    assert check_exit(300, 165, 165, 30, 30)[0] == "sl"       # live 330, profit -30 -> sl (boundary)
    assert check_exit(300, 160, 160, 30, 30)[0] is None       # live 320, profit -20 -> hold
    assert check_exit(300, 0, 140, 30, 30)[0] is None         # bad leg (<=0) -> freeze, never fire
    print("check_exit ok — all 6 pass")
