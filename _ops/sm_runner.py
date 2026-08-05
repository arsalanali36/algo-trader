"""Generic StockMock-style scheduled strategy runner — PURE logic + config parse.

One config (nifty_config[<id>]["_sm"]) declares a leg-basket strategy the same way
StockMock's builder does: instrument, entry/exit time, day-filter (all / expiry / weekday),
legs (opt · side · strike-offset · lots · per-leg SL%/target%), square-off one/all. The SAME
config feeds the backtest (StockMock-parity engine) and the live-paper firer, so the two can
never silently diverge (Rule 10 / ADR-010 single-source).

This module places NO orders and reads NO market data — it only parses config and answers
"should this fire today / now, and with which legs". The dashboard's `_fire_sm_strategy`
does the actual gated firing via execution_gateway; per-leg SL + EOD exits are enforced by
the existing pos_monitor (each leg carries a `SL_TYPE:pct` tag → SELL stop at entry×(1+sl%)).
"""
import datetime as _dt

try:
    import expiry_calendar as _ec       # scratch/nifty_trend — weekly_expiry_weekday
except Exception:
    _ec = None
try:
    import market_calendar as _mc        # _core — is_trading_day (NSE holidays)
except Exception:
    _mc = None

_VALID_SYM = {"NIFTY", "BANKNIFTY"}


def is_sm(raw):
    """True if this nifty_config entry is a StockMock-style (_sm) strategy."""
    return isinstance(raw, dict) and isinstance(raw.get("_sm"), dict)


def parse_cfg(strategy_id, raw):
    """Normalise nifty_config[strategy_id] → a runner config dict, or None if not _sm.
    Legs: each {opt: CE|PE, side: SELL|BUY, off: int strikes from ATM (0=ATM), lots: int,
    sl_pct: float|None, tp_pct: float|None}. mode is HARD-LOCKED to paper here."""
    if not is_sm(raw):
        return None
    sm = raw["_sm"]
    inst = str(sm.get("instrument", "NIFTY")).upper()
    legs = []
    for lg in (sm.get("legs") or []):
        try:
            legs.append({
                "opt": str(lg.get("opt", "CE")).upper(),
                "side": str(lg.get("side", "SELL")).upper(),
                "off": int(lg.get("off", 0)),
                "lots": max(1, int(lg.get("lots") or 1)),
                "sl_pct": (float(lg["sl_pct"]) if lg.get("sl_pct") not in (None, "", 0, "0") else None),
                "tp_pct": (float(lg["tp_pct"]) if lg.get("tp_pct") not in (None, "", 0, "0") else None),
            })
        except Exception:
            continue
    return {
        "id": strategy_id,
        "instrument": inst,
        "entry_hm": str(sm.get("entry_hm", "09:22"))[:5],
        "exit_hm": str(sm.get("exit_hm", "15:15"))[:5],
        "day_filter": str(sm.get("day_filter", "all")),   # all | expiry | weekday:N (0=Mon)
        "squareoff": str(sm.get("squareoff", "one")),      # one | all (informational — per-leg SL is naturally one)
        "legs": legs,
        "mode": "paper",
        "valid": bool(legs) and inst in _VALID_SYM,
    }


def _resolve_expiry_this_week(d):
    """The resolved weekly-expiry DATE for the week containing `d` (holiday-shifted back
    to the last trading day). Needs market_calendar + expiry_calendar; None if unavailable."""
    if _ec is None:
        return None
    ewd = _ec.weekly_expiry_weekday(d)                 # target weekday (Mon=0..Sun=6)
    monday = d - _dt.timedelta(days=d.weekday())
    cand = monday + _dt.timedelta(days=ewd)
    x = cand
    while x >= monday:
        if _mc is None or _mc.is_trading_day(x):
            return x
        x -= _dt.timedelta(days=1)
    return None


def is_expiry_day(date_str, instrument="NIFTY"):
    """Is `date_str` (YYYY-MM-DD) a weekly expiry day for this index? Holiday-shift aware.
    BANKNIFTY has no weeklies after 2024-11 — falls back to its monthly via expiry_calendar."""
    try:
        d = _dt.date.fromisoformat(date_str)
    except Exception:
        return False
    inst = str(instrument).upper()
    if inst == "BANKNIFTY" and _ec is not None and hasattr(_ec, "is_banknifty_expiry_day"):
        try:
            return bool(_ec.is_banknifty_expiry_day(d))
        except Exception:
            pass
    exp = _resolve_expiry_this_week(d)
    return exp == d


def should_fire_today(date_str, cfg):
    """Day-filter gate: all / expiry / weekday:N."""
    df = cfg.get("day_filter", "all")
    if df == "all":
        return True
    if df == "expiry":
        return is_expiry_day(date_str, cfg.get("instrument", "NIFTY"))
    if df.startswith("weekday:"):
        try:
            return _dt.date.fromisoformat(date_str).weekday() == int(df.split(":", 1)[1])
        except Exception:
            return False
    return False


def hm_ge(now_hm, target_hm):
    """True if now_hm (HH:MM) >= target_hm."""
    def _m(x):
        h, mi = str(x)[:5].split(":")[:2]
        return int(h) * 60 + int(mi)
    return _m(now_hm) >= _m(target_hm)


def describe(cfg):
    """One-line human summary for logs/registry."""
    legs = " + ".join("%s %s %s%s" % (
        lg["side"][0], lg["opt"], ("ATM" if lg["off"] == 0 else "ATM%+d" % lg["off"]),
        ("×%d" % lg["lots"] if lg["lots"] > 1 else "")) for lg in cfg["legs"])
    sl = next((lg["sl_pct"] for lg in cfg["legs"] if lg.get("sl_pct")), None)
    return "%s · %s · %s→%s · %s%s" % (
        cfg["instrument"], legs, cfg["entry_hm"], cfg["exit_hm"], cfg["day_filter"],
        (" · SL %g%%" % sl if sl else ""))
