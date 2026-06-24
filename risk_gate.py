#!/usr/bin/env python3
"""
risk_gate.py — capital allocation gate (RMS Stage 1).

Idea: define how much capital (₹ notional, qty*price) each strategy is allowed
to have deployed at once, plus a global ceiling across all strategies. Before
any entry, `check_capital()` tells the caller whether it fits. If not, the
caller should NOT place a real order — instead record the would-be entry as a
"blocked" leg (status='blocked', tags=['CAPITAL_BLOCKED', ...]) so it's visible
in Orders & P&L instead of silently vanishing.

Config lives in nifty_config.json["_risk"]["global"/"per_strategy"]["capital_rs"]
— same dict shape trader_dashboard.py already uses for max_loss_pct/max_loss_rs
(see _risk_config() there). Strategy-specific overrides global; absent on both
= no cap (unlimited, current default behavior unchanged).

Capital-in-use is notional (qty*entry_price) summed over today's open positions
for that strategy — NOT real margin. Margin-aware sizing is Stage 2.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TC_FILE = BASE_DIR / "nifty_config.json"


def _risk_cfg():
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        cfg = {}
    rc = cfg.get("_risk") or {}
    return {"global": rc.get("global") or {}, "per_strategy": rc.get("per_strategy") or {}}


def _today_open(strategy=None):
    import datetime
    from datetime import timedelta
    ist_now = datetime.datetime.utcnow() + timedelta(hours=5, minutes=30)
    date_str = ist_now.strftime("%Y-%m-%d")
    try:
        import order_store
        data = order_store.trades_for(date_str)
    except Exception:
        return []
    open_pos = data.get("open", [])
    if strategy is not None:
        open_pos = [p for p in open_pos if p.get("strategy") == strategy]
    return open_pos


def _margin_multiplier(strategy, rc=None):
    """Selling an option blocks margin, not just the premium received — qty*price
    massively understates the real capital block for SELL legs. Until a real
    broker margin-API check exists (Stage 3), use a configurable multiplier on
    the notional for SELL legs: capital_used = qty*price*multiplier.
    Strategy-specific margin_multiplier overrides global; default 1.0 (off,
    same behavior as Stage 1) so existing setups aren't surprised by this."""
    rc = rc or _risk_cfg()
    strat_mult = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("margin_multiplier")
    glob_mult = (rc.get("global", {}) or {}).get("margin_multiplier")
    mult = strat_mult if strat_mult is not None else glob_mult
    try:
        return float(mult) if mult is not None else 1.0
    except Exception:
        return 1.0


def _leg_capital(p, rc=None):
    """Margin-adjusted ₹ capital for one open-position dict from order_store."""
    try:
        qty, price = float(p.get("qty") or 0), float(p.get("entry_price") or 0)
    except Exception:
        return 0.0
    notional = qty * price
    if str(p.get("entry") or "").upper() == "SELL":
        notional *= _margin_multiplier(p.get("strategy"), rc)
    return notional


def capital_in_use(strategy=None):
    """₹ capital currently deployed (margin-adjusted for SELL legs — see
    _margin_multiplier) over open positions. strategy=None → ALL strategies
    (for the global cap check)."""
    rc = _risk_cfg()
    return sum(_leg_capital(p, rc) for p in _today_open(strategy))


def check_capital(strategy, qty, price, side="SELL"):
    """Would adding qty@price (side BUY/SELL) to `strategy` breach its allocation
    or the global ceiling? Returns (ok: bool, reason: str). reason='' when ok.

    SELL legs (option-selling, the common case for these strategies) get the
    margin_multiplier applied — see _margin_multiplier(). BUY legs use the
    premium paid as-is (that IS the capital committed).

    Strategy-specific capital_rs overrides the global one for that strategy's
    own cap; the global cap (sum across ALL strategies) always applies too —
    whichever is hit first blocks the entry."""
    rc = _risk_cfg()
    needed = float(qty or 0) * float(price or 0)
    if str(side or "SELL").upper() == "SELL":
        needed *= _margin_multiplier(strategy, rc)
    if needed <= 0:
        return True, ""

    strat_cap = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("capital_rs")
    glob_cap = (rc.get("global", {}) or {}).get("capital_rs")

    if strat_cap is not None:
        try:
            strat_cap = float(strat_cap)
            in_use = capital_in_use(strategy)
            if in_use + needed > strat_cap:
                return False, (f"strategy capital cap ₹{strat_cap:.0f} hit "
                                f"(in-use ₹{in_use:.0f} + needed ₹{needed:.0f})")
        except Exception:
            pass

    if glob_cap is not None:
        try:
            glob_cap = float(glob_cap)
            in_use_all = capital_in_use(None)
            if in_use_all + needed > glob_cap:
                return False, (f"global capital cap ₹{glob_cap:.0f} hit "
                                f"(in-use ₹{in_use_all:.0f} + needed ₹{needed:.0f})")
        except Exception:
            pass

    return True, ""


def _quick_option_ltp(sec_id, token, cid):
    """Best-effort option premium fetch (Dhan /v2/marketfeed/ltp), same call shape
    the legacy _TRADERS/*.py place_order() functions already make. Returns None on
    any failure — caller should fall back to a spot/estimate price."""
    try:
        import requests
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                          json={"NSE_FNO": [int(sec_id)]},
                          headers={"access-token": token, "client-id": cid, "Content-Type": "application/json"},
                          timeout=5)
        if r.status_code != 200:
            return None
        data = r.json().get("data", {}).get("NSE_FNO", {})
        for v in (data.values() if isinstance(data, dict) else []):
            ltp = float(v.get("last_price") or v.get("ltp") or 0)
            if ltp:
                return ltp
    except Exception:
        pass
    return None


def check_capital_option(strategy, qty, sec_id, token, cid, fallback_price=0.0, side="SELL"):
    """Like check_capital() but fetches the real option premium first (capital
    relevance for options is the premium, not the underlying spot price the
    legacy traders pass around internally). Falls back to fallback_price if the
    LTP fetch fails (still useful as a rough gate rather than skipping entirely)."""
    price = _quick_option_ltp(sec_id, token, cid) or float(fallback_price or 0)
    return check_capital(strategy, qty, price, side=side)


def capital_mode(strategy):
    """'reject' (default, Stage 1 behavior — block the whole entry) or
    'size_down' (fill the largest qty that fits remaining capital instead).
    Strategy-specific overrides global; absent on both = 'reject'."""
    rc = _risk_cfg()
    m = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("capital_mode")
    if m is None:
        m = (rc.get("global", {}) or {}).get("capital_mode")
    m = str(m or "reject").lower().strip()
    return m if m in ("reject", "size_down") else "reject"


def sized_lots_option(strategy, lots, lot_size, sec_id, token, cid, fallback_price=0.0, side="SELL"):
    """sized_lots() but fetches the real option premium first, like
    check_capital_option() does for the reject path."""
    price = _quick_option_ltp(sec_id, token, cid) or float(fallback_price or 0)
    return sized_lots(strategy, lots, lot_size, price, side=side)


def sized_lots(strategy, lots, lot_size, price, side="SELL"):
    """For capital_mode='size_down': how many of the requested `lots` (each
    `lot_size` qty) actually fit in the remaining capital? Returns an int
    0..lots — 0 means even one lot doesn't fit (caller should still block).
    Respects lot boundaries (can't size into a fractional lot)."""
    lots = int(lots or 0)
    if lots <= 0:
        return 0
    per_lot_qty = max(1, int(lot_size or 1))
    for try_lots in range(lots, 0, -1):
        ok, _ = check_capital(strategy, try_lots * per_lot_qty, price, side=side)
        if ok:
            return try_lots
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# RMS Stage 3 — real broker funds, cross-strategy concentration, drawdown
# circuit breaker, capital/funds reconciliation.
# ─────────────────────────────────────────────────────────────────────────────

def check_broker_funds(broker, needed_rs):
    """LIVE mode only — does the broker's actual available balance cover this
    entry? Catches drift our own bookkeeping can't see (manual trades, other
    apps drawing the same account, partial fills elsewhere). Best-effort: if
    funds() fails or returns nothing usable, DON'T block — an API hiccup
    shouldn't silently halt live trading; fall through to the capital_rs gate,
    which is more important since it's not affected by API flakiness."""
    if needed_rs <= 0 or broker is None or not hasattr(broker, "funds"):
        return True, ""
    try:
        f = broker.funds() or {}
        avail = float(f.get("available") or 0)
    except Exception:
        return True, ""
    if avail <= 0:
        return True, ""  # couldn't determine — don't block on an unknown
    if needed_rs > avail:
        return False, f"broker funds insufficient (avail ₹{avail:.0f} < needed ₹{needed_rs:.0f})"
    return True, ""


def _underlying(symbol_or_tradsym):
    """'NIFTY-Jun2026-24000-CE' -> 'NIFTY'; 'RELIANCE' -> 'RELIANCE'."""
    return str(symbol_or_tradsym or "").split("-")[0].upper()


def exposure_by_underlying(underlying=None):
    """₹ margin-adjusted capital currently deployed per underlying, across ALL
    strategies (this is the whole point — a per-strategy cap can't see another
    strategy piling into the same name). underlying=None -> dict for all."""
    rc = _risk_cfg()
    totals = {}
    for p in _today_open(None):
        u = _underlying(p.get("symbol") or p.get("sym"))
        totals[u] = totals.get(u, 0.0) + _leg_capital(p, rc)
    if underlying is not None:
        return totals.get(_underlying(underlying), 0.0)
    return totals


def check_concentration(symbol, qty, price, side="SELL"):
    """Would this entry push combined exposure to `symbol`'s underlying (across
    ALL strategies) past max_underlying_exposure_rs? Global-only setting (this
    is inherently a cross-strategy check) — nifty_config.json["_risk"]["global"]
    ["max_underlying_exposure_rs"]. Absent = no cap (off by default)."""
    rc = _risk_cfg()
    cap = (rc.get("global", {}) or {}).get("max_underlying_exposure_rs")
    if cap is None:
        return True, ""
    try:
        cap = float(cap)
    except Exception:
        return True, ""
    needed = float(qty or 0) * float(price or 0)
    if str(side or "SELL").upper() == "SELL":
        needed *= _margin_multiplier(None, rc)
    if needed <= 0:
        return True, ""
    u = _underlying(symbol)
    in_use = exposure_by_underlying(u)
    if in_use + needed > cap:
        return False, (f"underlying '{u}' concentration cap ₹{cap:.0f} hit "
                        f"(in-use ₹{in_use:.0f} + needed ₹{needed:.0f})")
    return True, ""


def _today_realized_pnl():
    """Sum of realized P&L (completed trades, all strategies) for today —
    from order_store's already-netted 'details'."""
    import datetime
    from datetime import timedelta
    ist_now = datetime.datetime.utcnow() + timedelta(hours=5, minutes=30)
    date_str = ist_now.strftime("%Y-%m-%d")
    try:
        import order_store
        data = order_store.trades_for(date_str)
    except Exception:
        return 0.0
    return sum(float(d.get("pnl") or 0) for d in data.get("details", []))


def check_drawdown(unrealized_pnl=0.0):
    """Global circuit breaker — once today's cumulative P&L (realized completed
    trades + unrealized open positions, across ALL strategies) breaches
    daily_drawdown_cap_rs, block ALL new entries (existing positions' own SL/
    max-loss still manage themselves independently — this is a separate, blunter
    "stop digging" switch). unrealized_pnl is the caller's best-effort estimate
    (e.g. from /api/rms-summary) — pass 0 if unavailable, the realized-only
    check is still meaningful. Absent config = no breaker (off by default)."""
    rc = _risk_cfg()
    cap = (rc.get("global", {}) or {}).get("daily_drawdown_cap_rs")
    if cap is None:
        return True, ""
    try:
        cap = float(cap)
    except Exception:
        return True, ""
    if cap <= 0:
        return True, ""
    total = _today_realized_pnl() + float(unrealized_pnl or 0)
    if total <= -abs(cap):
        return False, f"daily drawdown cap ₹{cap:.0f} hit (today's P&L ₹{total:.0f})"
    return True, ""


def reconcile_funds(broker):
    """Read-only health_check.py-style comparison: our own capital_in_use(None)
    vs the broker's actual available funds. Doesn't block anything — just
    reports drift so it can be investigated (manual trades outside the system,
    a fill we didn't record, etc). Returns a dict, never raises."""
    out = {"ok": True, "our_capital_in_use": capital_in_use(None),
           "broker_available": None, "drift_rs": None, "note": ""}
    if broker is None or not hasattr(broker, "funds"):
        out["note"] = "no broker funds() available — skipped"
        return out
    try:
        f = broker.funds() or {}
        avail = f.get("available")
        if avail is None:
            out["note"] = "broker funds() returned nothing usable"
            return out
        out["broker_available"] = float(avail)
    except Exception as e:
        out["note"] = f"broker funds() failed: {e}"
        return out
    out["note"] = "compare manually — 'available' shrinking faster than our capital_in_use grows usually means an untracked fill"
    return out


def shadow_live_enabled(strategy):
    """Diagnostic mode — when True, a PAPER entry also fires a REAL broker order
    in parallel (same price/qty), purely to compare against Dhan's actual
    fill/reject (slippage check, price-band/freeze rejects, etc). The paper
    fill price/P&L recorded to order_store is NEVER touched by the shadow
    order's outcome — it's a side-channel diagnostic, not a fallback.

    OFF by default (False) everywhere — must be explicitly turned on per
    strategy or globally. Intended for short, deliberate testing windows with
    a near-zero account balance so the shadow orders can't actually fill.
    nifty_config.json["_risk"]["global"/"per_strategy"]["shadow_live"]."""
    rc = _risk_cfg()
    v = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("shadow_live")
    if v is None:
        v = (rc.get("global", {}) or {}).get("shadow_live")
    return bool(v)
