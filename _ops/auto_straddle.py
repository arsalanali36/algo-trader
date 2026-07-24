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
    return {"day": _today_ist(), "straddles": [], "fired_920": []}


def _write_raw(day, straddles, fired_920=None):
    """Persist straddles + the day's 9:20 attempt-markers. fired_920=None means
    'preserve whatever's already stored for TODAY' (so the frequent add/set_status
    writes don't drop the marker set); on a day rollover the old markers are stale
    so they clear to []."""
    if fired_920 is None:
        raw = _read_raw()
        fired_920 = raw.get("fired_920") if raw.get("day") == day else []
        if not isinstance(fired_920, list):
            fired_920 = []
    try:
        _FILE.write_text(json.dumps({"day": day, "straddles": straddles,
                                     "fired_920": fired_920}))
    except Exception:
        pass


def _load_today():
    """Today's straddle list. If stored day != today, the whole set is stale
    (EOD auto-cancel) → return [] and rewrite (also clears 9:20 markers). Caller
    holds _LOCK where mutation follows."""
    raw = _read_raw()
    today = _today_ist()
    if raw.get("day") != today:
        _write_raw(today, [], fired_920=[])
        return []
    st = raw.get("straddles")
    return st if isinstance(st, list) else []


def _load_fired_920():
    """Today's 9:20 attempt-marker symbol list (day-reset applied)."""
    raw = _read_raw()
    if raw.get("day") != _today_ist():
        return []
    fl = raw.get("fired_920")
    return fl if isinstance(fl, list) else []


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


def reconcile_open(symbol, leg_open_fn, log=None):
    """has_open(symbol), but SELF-HEALS a stale-open record before answering.

    has_open() alone trusts the stored status=="open". But an SL/target/EOD exit
    (or an external/manual close) squares the legs at the broker AND records the
    exit in order_store while THIS record can still lag "open" (the flip hasn't
    run yet, or the process meant to flip it died) — that stale record then
    FALSELY blocks a fresh straddle ("ek waqt ek per index") even though nothing
    is actually open. Same class as TRAP #62 (stale in-memory/record state).

    leg_open_fn(strategy_id, sec_id, trad_sym) -> int|None mirrors
    broker_sync._my_open_qty: 0 = CONFIDENT flat (order_store has a closed
    round-trip for this leg), >0 = still open, None = uncertain (no record /
    query fail). A record is healed to "closed" ONLY when EVERY one of its SELL
    legs is confidently flat (all == 0) — any >0 or any None keeps it open
    (conservative: a false-block is far safer than firing a duplicate straddle
    on top of one that might still be live). This module stays pure — the
    order_store lookup is injected, not imported here.

    Returns True if a genuinely-open straddle for `symbol` remains."""
    symbol = str(symbol).upper()
    with _LOCK:
        rows = _load_today()
        changed = False
        still_open = False
        for s in rows:
            if s.get("status") != _OPEN or s.get("symbol") != symbol:
                continue
            sell_legs = [lg for lg in (s.get("legs") or [])
                         if str(lg.get("side", "")).upper() == "SELL"]
            if not sell_legs:
                still_open = True   # can't verify legs — trust the record
                continue
            sid = s.get("strategy_id") or ""
            verdicts = []
            for lg in sell_legs:
                try:
                    verdicts.append(leg_open_fn(sid, str(lg.get("sec_id") or ""),
                                                lg.get("trad_sym") or ""))
                except Exception:
                    verdicts.append(None)
            if all(v == 0 for v in verdicts):
                s["status"] = "closed"
                s["result"] = "reconciled-flat"
                s["closed_ts"] = int(time.time())
                changed = True
                if log:
                    log(f"[STRADDLE] {symbol} stale-open record {s.get('id')} "
                        f"reconciled → closed (order_store confirms all SELL legs flat)")
            else:
                still_open = True
        if changed:
            _write_raw(_today_ist(), rows)
        return still_open


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
    """Restart-safe one-shot guard for the 9:20 scheduled fire. TRUE if we've
    ATTEMPTED a 9:20 fire for this symbol today — success OR failure. Critical:
    an attempt is marked (mark_920) BEFORE the fire, so a failed/partial fire
    (RMS block, naked-abort, capital cap) can no longer trigger a re-fire storm
    every loop cycle within the entry window. Also honours a genuinely-recorded
    schedule_920 straddle (belt-and-suspenders / older state files)."""
    symbol = str(symbol).upper()
    with _LOCK:
        if symbol in _load_fired_920():
            return True
        return any(s.get("source") == "schedule_920" and s.get("symbol") == symbol
                   for s in _load_today())


def mark_920(symbol):
    """Record a 9:20 attempt marker for `symbol` (idempotent). Call this right
    BEFORE _fire_auto_straddle so the one-shot guard holds regardless of outcome."""
    symbol = str(symbol).upper()
    with _LOCK:
        raw = _read_raw()
        today = _today_ist()
        straddles = raw.get("straddles") if raw.get("day") == today else []
        if not isinstance(straddles, list):
            straddles = []
        fired = raw.get("fired_920") if raw.get("day") == today else []
        if not isinstance(fired, list):
            fired = []
        if symbol not in fired:
            fired.append(symbol)
        _write_raw(today, straddles, fired_920=fired)


def add(strad):
    """Append a straddle row (caller has already placed both legs). Returns the
    stored dict (with id/status/timestamps filled)."""
    row = {
        "id": "strad_" + uuid.uuid4().hex[:8],
        "symbol": str(strad.get("symbol", "NIFTY")).upper(),
        "lots": int(strad.get("lots", 1)),
        "mode": str(strad.get("mode", "paper")).lower(),
        "source": str(strad.get("source", "manual")),
        "strategy_id": str(strad.get("strategy_id", "auto_straddle")),   # straddle_920/_alert/_manual
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
        _write_raw(_today_ist(), [], fired_920=[])


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
