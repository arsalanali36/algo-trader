"""payoff.py — multi-leg option position payoff / zone analytics.

DISPLAY-ONLY. Nothing here places an order, gates an entry/exit, or feeds any
risk decision — it only DESCRIBES a position that already exists, for the
dashboard's per-group "Payoff" panel (max profit / max loss / breakevens /
probability-of-profit / real hedged margin / combined-premium series).

Rule 6B — everything canonical is reused, nothing re-implemented:
  * pricing + IV      -> scratch/nifty_trend/bs_option.py  (the project's single
                         Black-Scholes source, see BS_OPTION_SIM.md)
  * expiry date + lot -> dhan_master.get_expiry_for_sec_id / get_lot_size_by_sec_id
                         (NEVER parsed out of the trad_sym string — index
                         trad_syms omit the expiry DAY; see that function's docstring)
  * hedged margin     -> KiteBroker.basket_margin()  (Kite basket_order_margins)

sec_id NOTE: callers must pass the sec_id recorded on the order_store row.
dhan_master.get_sec_id_for_trad_sym() resolves the NEAREST-LIVE expiry, which is
the wrong contract for any older trade (TRAP #100 shape).
"""
import math
import os
import sys
import datetime as _dt
import logging

log = logging.getLogger(__name__)

_R_FREE = 0.065
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_bs_mod = None


def _bs():
    """Lazy-import bs_option (lives under scratch/nifty_trend, not on the default
    _paths bootstrap). Same local sys.path-insert pattern trader_dashboard.py
    already uses for _TOOLS/backtest_engine. Returns None if unavailable —
    every caller degrades gracefully (payoff-at-expiry still works without it;
    only the today-curve / IV / POP need Black-Scholes)."""
    global _bs_mod
    if _bs_mod is not None:
        return _bs_mod or None
    d = os.path.join(_ROOT, "scratch", "nifty_trend")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import bs_option as _m
        _bs_mod = _m
    except Exception as e:
        log.warning("[payoff] bs_option unavailable (%s) — today-curve/POP disabled", e)
        _bs_mod = False
    return _bs_mod or None


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def parse_leg_sym(trad_sym):
    """(strike, opt_type) from a Dhan option trad_sym. Strike + CE/PE ARE
    reliably the last two dash-parts (same parse risk_gate.option_is_itm uses);
    only the expiry DAY is missing for index contracts — that comes from
    dhan_master by sec_id, never from here."""
    try:
        parts = str(trad_sym).split("-")
        strike = float(parts[-2])
        opt = parts[-1].upper()
        if opt in ("CE", "PE"):
            return strike, opt
    except Exception:
        pass
    return None, None


def build_legs(rows):
    """order_store open-position rows -> leg dicts. Skips anything that isn't a
    resolvable option leg (equity, blocked rows, unparseable)."""
    legs = []
    for r in rows or []:
        tags = r.get("tags") or []
        if "CAPITAL_BLOCKED" in tags or str(r.get("status", "")).lower() == "blocked":
            continue
        trad_sym = r.get("sym") or ""
        strike, opt = parse_leg_sym(trad_sym)
        if strike is None:
            continue
        qty = abs(float(r.get("qty") or 0))
        entry = float(r.get("entry_price") or 0)
        if qty <= 0 or entry <= 0:
            continue
        legs.append({
            "trad_sym": trad_sym,
            "sec_id": str(r.get("sec_id") or "") or None,
            "strike": strike,
            "opt": opt,
            "side": str(r.get("entry") or "").upper(),   # BUY / SELL
            "qty": qty,
            "entry": entry,
            "ltp": float(r.get("ltp") or 0) or None,
            "id": r.get("id"),
        })
    return legs


def expiry_of(legs):
    """Earliest real expiry across the legs, from the scrip master by sec_id."""
    try:
        import dhan_master
    except Exception:
        return None
    exps = []
    for L in legs:
        if not L.get("sec_id"):
            continue
        try:
            e = dhan_master.get_expiry_for_sec_id(L["sec_id"])
            if e:
                exps.append(e)
        except Exception:
            pass
    return min(exps) if exps else None


def tte_years(expiry_date, now_ist=None):
    """Years to expiry (expiry 15:30 IST). <=0 once expired."""
    if not expiry_date:
        return None
    now_ist = now_ist or (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30))
    exp_dt = _dt.datetime.combine(expiry_date, _dt.time(15, 30))
    secs = (exp_dt - now_ist).total_seconds()
    return max(secs / (365.0 * 24 * 3600), 0.0)


def _signed(L, value):
    """P&L contribution of one leg, per unit, given the leg's CURRENT value."""
    return (value - L["entry"]) if L["side"] == "BUY" else (L["entry"] - value)


def payoff_expiry(legs, S):
    """Total ₹ P&L at expiry if the underlying settles at S."""
    tot = 0.0
    for L in legs:
        intr = max(S - L["strike"], 0.0) if L["opt"] == "CE" else max(L["strike"] - S, 0.0)
        tot += _signed(L, intr) * L["qty"]
    return tot


def payoff_today(legs, S, T):
    """Total ₹ P&L if the underlying were at S right now (T years left), each leg
    priced by Black-Scholes at its OWN implied vol (sticky-IV). None if bs_option
    is unavailable or any leg has no IV."""
    bs = _bs()
    if not bs or T is None or T <= 0:
        return None
    tot = 0.0
    for L in legs:
        iv = L.get("iv")
        if not iv:
            return None
        v = bs.bs_price(S, L["strike"], T, iv, opt=L["opt"])
        tot += _signed(L, v) * L["qty"]
    return tot


def attach_ivs(legs, spot, T):
    """Derive each leg's implied vol from its live LTP (falls back to entry px).
    Mutates legs (adds 'iv'). Returns the average IV, or None."""
    bs = _bs()
    if not bs or not spot or T is None or T <= 0:
        return None
    ivs = []
    for L in legs:
        px = L.get("ltp") or L.get("entry")
        try:
            iv = bs.implied_vol(px, spot, L["strike"], T, opt=L["opt"])
        except Exception:
            iv = None
        L["iv"] = iv if (iv and iv > 0) else None
        if L["iv"]:
            ivs.append(L["iv"])
    return (sum(ivs) / len(ivs)) if ivs else None


def _grid(legs, spot):
    """Spot scan range — wide enough to cover every strike plus a margin."""
    ks = [L["strike"] for L in legs] + [spot]
    lo, hi = min(ks), max(ks)
    span = max(hi - lo, spot * 0.02)
    return lo - span * 1.2, hi + span * 1.2


def profit_zones(legs, lo, hi, step):
    """Contiguous [a, b] spot intervals where expiry P&L > 0. Handles ANY
    structure (condor = 1-2 zones, straddle = 2 tails, etc.) — no assumption
    that there's exactly one breakeven."""
    zones, start, S = [], None, lo
    prev_S, prev_v = None, None
    while S <= hi:
        v = payoff_expiry(legs, S)
        if prev_v is not None:
            if prev_v <= 0 < v:      # entering profit — interpolate the crossing
                f = prev_v / (prev_v - v)
                start = prev_S + f * (S - prev_S)
            elif prev_v > 0 >= v:    # leaving profit
                f = prev_v / (prev_v - v)
                zones.append([start if start is not None else lo, prev_S + f * (S - prev_S)])
                start = None
        elif v > 0:
            start = lo
        prev_S, prev_v = S, v
        S += step
    if start is not None:
        zones.append([start, hi])
    return zones


def prob_of_profit(zones, spot, T, sigma, lo, hi):
    """P(spot at expiry lands in a profit zone), lognormal terminal distribution.
    Zones touching the scan edges are treated as open-ended tails."""
    if not zones or not sigma or T is None or T <= 0 or not spot:
        return None

    def p_below(K):
        if K <= 0:
            return 0.0
        d2 = (math.log(spot / K) + (_R_FREE - sigma ** 2 / 2) * T) / (sigma * math.sqrt(T))
        return _norm_cdf(-d2)

    tot = 0.0
    for a, b in zones:
        pa = 0.0 if a <= lo else p_below(a)
        pb = 1.0 if b >= hi else p_below(b)
        tot += max(pb - pa, 0.0)
    return min(max(tot, 0.0), 1.0)


def basket_margin(legs, product="NRML"):
    """Real hedged margin for the structure + the standalone per-leg sum, for
    the panel's Margin card. Both numbers come from risk_gate — THE single
    entry point for every margin estimate in this project (CLAUDE.md /
    TRAP #90); this module must never call a broker's margin API itself, or
    the panel and RMS could silently disagree about the same position.
    Returns {hedged, standalone, benefit, ok, msg}."""
    out = {"hedged": None, "standalone": None, "benefit": None, "ok": False, "msg": ""}
    try:
        import risk_gate as rg
        rows = [{"sym": L["trad_sym"], "sec_id": L.get("sec_id"), "entry": L["side"],
                 "qty": L["qty"], "entry_price": L["entry"], "ltp": L.get("ltp"),
                 "segment": "NSE_FNO"} for L in legs]
        standalone = sum(rg._leg_capital(p) for p in rows)
        out["standalone"] = round(standalone, 2)
        hedged = rg.kite_basket_margin(rows, product_type=product)
        if hedged is None:
            out["msg"] = "basket margin unavailable (broker call failed)"
            return out
        out["hedged"] = round(float(hedged), 2)
        out["benefit"] = round(standalone - float(hedged), 2)
        out["ok"] = True
    except Exception as e:
        out["msg"] = str(e)
    return out


def analyse(rows, spot, now_ist=None, with_margin=False):
    """One call -> everything the Payoff panel needs. Never raises: any piece
    that can't be computed comes back None and the panel just hides it."""
    legs = build_legs(rows)
    if not legs:
        return {"ok": False, "msg": "no option legs in this group"}

    exp = expiry_of(legs)
    T = tte_years(exp, now_ist)
    avg_iv = attach_ivs(legs, spot, T) if spot else None

    lo, hi = _grid(legs, spot or legs[0]["strike"])
    step = max((hi - lo) / 400.0, 0.5)

    curve = []
    S = lo
    while S <= hi:
        curve.append([round(S, 2), round(payoff_expiry(legs, S), 2)])
        S += step
    today = None
    if avg_iv:
        today = [[p[0], (lambda v: round(v, 2) if v is not None else None)(payoff_today(legs, p[0], T))]
                 for p in curve]
        if any(p[1] is None for p in today):
            today = None

    zones = profit_zones(legs, lo, hi, step)
    ys = [p[1] for p in curve]
    res = {
        "ok": True,
        "legs": [{k: L[k] for k in ("trad_sym", "sec_id", "strike", "opt", "side", "qty", "entry", "ltp", "iv")} for L in legs],
        "spot": spot,
        "expiry": exp.strftime("%Y-%m-%d") if exp else None,
        "tte_days": round(T * 365, 2) if T else None,
        "avg_iv": round(avg_iv, 4) if avg_iv else None,
        "curve_expiry": curve,
        "curve_today": today,
        "zones": [[round(a, 1), round(b, 1)] for a, b in zones],
        "breakevens": sorted({round(x, 1) for z in zones for x in z if lo < x < hi}),
        "max_profit": round(max(ys), 2),
        "max_loss": round(min(ys), 2),
        "pnl_now_expiry": round(payoff_expiry(legs, spot), 2) if spot else None,
        "pnl_now_today": (round(payoff_today(legs, spot, T), 2)
                          if (spot and today) else None),
        "pop": None,
        "scan": [round(lo, 1), round(hi, 1)],
    }
    pop = prob_of_profit(zones, spot, T, avg_iv, lo, hi)
    res["pop"] = round(pop, 4) if pop is not None else None
    if with_margin:
        res["margin"] = basket_margin(legs)
    return res
