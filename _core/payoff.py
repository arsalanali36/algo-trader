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
_ch_mod = None


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


def _charges():
    """Lazy-import the single-source charges module (same scratch/nifty_trend dir as
    bs_option). Used for the payoff panel's total round-trip transaction cost. Returns
    None if unavailable (tax line just shows '—')."""
    global _ch_mod
    if _ch_mod is not None:
        return _ch_mod or None
    d = os.path.join(_ROOT, "scratch", "nifty_trend")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import charges as _c
        _ch_mod = _c
    except Exception as e:
        log.warning("[payoff] charges module unavailable (%s) — tax line disabled", e)
        _ch_mod = False
    return _ch_mod or None


def structure_tax(rows):
    """Total Zerodha round-trip transaction cost (brokerage+STT+txn+SEBI+stamp+GST) to
    trade this whole multi-leg structure, estimated at CURRENT premiums (exit premium is
    forward-unknown, so entry premium is used both ways). Returns float, or None if the
    charges module isn't available. Display-only — never gates anything."""
    ch = _charges()
    if not ch:
        return None
    total = 0.0
    for r in (rows or []):
        try:
            prem = float(r.get("entry_price") or r.get("price") or 0)
            qty = int(float(r.get("qty") or 0))
            side = str(r.get("entry") or r.get("side") or "BUY").upper()
            if prem > 0 and qty > 0:
                total += ch.option_charges(prem, prem, qty, entry_side=side)
        except Exception:
            continue
    return round(total, 2)


def structure_tax_breakdown(rows):
    """Same round-trip cost as structure_tax() but itemised (brokerage/STT/exch/GST/
    stamp/SEBI) + the ENTRY-only total. Display-only: lets the panel show WHY the
    number is what it is (and that entry-only == what broker builders like Sensibull
    show — ours is round-trip = entry + exit). Exit premium is forward-unknown, so the
    exit leg is estimated at the entry premium (same as structure_tax). None if the
    charges module is unavailable."""
    ch = _charges()
    if not ch:
        return None
    agg = {"brokerage": 0.0, "stt": 0.0, "exch": 0.0, "sebi": 0.0, "stamp": 0.0, "gst": 0.0}
    entry_total = 0.0
    for r in (rows or []):
        try:
            prem = float(r.get("entry_price") or r.get("price") or 0)
            qty = int(float(r.get("qty") or 0))
            side = str(r.get("entry") or r.get("side") or "BUY").upper()
            if prem <= 0 or qty <= 0:
                continue
            for k, v in ch.option_charges_parts(prem, prem, qty, entry_side=side).items():
                agg[k] += v
            entry_total += sum(ch.option_entry_parts(prem, qty, side=side).values())
        except Exception:
            continue
    parts = {k: round(v, 2) for k, v in agg.items()}
    return {"parts": parts, "total": round(sum(agg.values()), 2), "entry_total": round(entry_total, 2)}


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
    for L in legs:
        L.setdefault("iv", None)      # key must exist even if we early-return below
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


def _bs_vega(S, K, T, sigma):
    """Vega per +1 vol-POINT (i.e. per +1% IV): S·φ(d1)·√T / 100. r≈0. Same
    convention the desk reads vega in — 'net vega N' = ₹N per 1% IV move."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
    phi = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    return S * phi * math.sqrt(T) / 100.0


def position_greeks(legs, spot):
    """Net + per-leg Delta / Vega for a position GROUP (display-only). Per-leg IV
    is back-solved from the live LTP (attach_ivs) so it reflects the ACTUAL traded
    premium (skew included), then Black-Scholes delta + vega. Position sign is
    baked in: BUY +, SELL − — so a short straddle nets ~0 delta, a short position
    nets NEGATIVE vega (loses if IV rises). Per-leg dqty/vqty (already ×qty×sign)
    let the caller sum any SUBSET (leg-checkbox selection) client-side.
    Returns {ok, spot, net_delta, net_vega, avg_iv_pct, legs:[{sec_id,dqty,vqty}]}."""
    bs = _bs()
    if not legs or not bs or not spot or spot <= 0:
        return {"ok": False}
    T = tte_years(expiry_of(legs))
    if not T or T <= 0:
        return {"ok": False}
    attach_ivs(legs, spot, T)
    out, nd, nv, ivs = [], 0.0, 0.0, []
    for L in legs:
        iv = L.get("iv")
        sign = 1 if str(L.get("side", "")).upper() == "BUY" else -1
        if not iv:
            out.append({"sec_id": L.get("sec_id"), "dqty": None, "vqty": None})
            continue
        d = bs.bs_delta(spot, L["strike"], T, iv, opt=L["opt"])
        v = _bs_vega(spot, L["strike"], T, iv)
        dq = sign * d * L["qty"]
        vq = sign * v * L["qty"]
        nd += dq
        nv += vq
        ivs.append(iv)
        out.append({"sec_id": L.get("sec_id"), "dqty": round(dq, 2), "vqty": round(vq, 2)})
    return {"ok": True, "spot": round(spot, 1), "net_delta": round(nd, 2),
            "net_vega": round(nv, 2),
            "avg_iv_pct": round(sum(ivs) / len(ivs) * 100, 1) if ivs else None,
            "legs": out}


def _grid(legs, spot):
    """Spot scan range — wide enough to cover every strike plus a margin."""
    ks = [L["strike"] for L in legs] + [spot]
    lo, hi = min(ks), max(ks)
    span = max(hi - lo, spot * 0.02)
    return lo - span * 1.2, hi + span * 1.2


def exit_day_days(now_ist=None):
    """Fractional days from now until TODAY's square-off time — i.e. when an
    intraday/one-night position actually gets closed, which for those is the
    date that matters, not expiry. Square-off time comes from
    risk_gate.exit_time_config() (the project's single source — never hardcode
    15:15). Returns None if that moment has already passed."""
    now_ist = now_ist or (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30))
    try:
        import risk_gate
        (sh, sm), _ = risk_gate.exit_time_config()
    except Exception:
        return None
    x = _dt.datetime.combine(now_ist.date(), _dt.time(sh, sm))
    secs = (x - now_ist).total_seconds()
    return (secs / 86400.0) if secs > 60 else None


def profit_zones(legs, lo, hi, step, fn=None):
    """Contiguous [a, b] spot intervals where P&L > 0. Handles ANY structure
    (condor = 1-2 zones, straddle = 2 tails, etc.) — no assumption that there's
    exactly one breakeven. `fn` defaults to the expiry payoff; pass a
    Black-Scholes today/target-date payoff to get that date's zones instead
    (same scan engine, so the two can't drift)."""
    fn = fn or (lambda S: payoff_expiry(legs, S))
    zones, start, S = [], None, lo
    prev_S, prev_v = None, None
    while S <= hi:
        v = fn(S)
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
        # The single margin gate — hedged/naked/benefit from risk_gate.margin_breakdown
        # (which uses position_margin = the SAME basket calc RMS uses). This module
        # never calls a broker margin API itself.
        bd = rg.margin_breakdown(rows)
        if not bd.get("hedged"):
            out["msg"] = "basket margin unavailable (broker call failed)"
            return out
        out.update(hedged=bd["hedged"], standalone=bd["standalone"], benefit=bd["benefit"], ok=True)
    except Exception as e:
        out["msg"] = str(e)
    return out


def analyse(rows, spot, now_ist=None, with_margin=False, target_days=None):
    """One call -> everything the Payoff panel needs. Never raises: any piece
    that can't be computed comes back None and the panel just hides it.

    target_days: fractional days ahead to ALSO evaluate (defaults to today's
    square-off — see exit_day_days). Expiry POP answers "if held to expiry",
    which is the wrong question for an intraday / one-night-hold strategy that
    closes long before then: at the target date the legs still carry time
    value, so both the profit zone AND the probability window are different.
    Returns pop_target / curve_target / breakevens_target alongside the
    expiry ones — the panel toggles between them."""
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
        # L.get (not L[k]): if spot was None, attach_ivs was skipped so 'iv' is
        # absent — L[k] would KeyError and break the whole panel (the docstring
        # promises this never raises). Any missing key → None instead.
        "legs": [{k: L.get(k) for k in ("trad_sym", "sec_id", "strike", "opt", "side", "qty", "entry", "ltp", "iv")} for L in legs],
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

    # ── target-date (exit-day) view ──────────────────────────────────────────
    # For a one-night / intraday structure the expiry numbers are theoretical —
    # it closes at today's square-off. Evaluate the SAME structure at that
    # moment: legs priced with the time value still left (T - T_target), and
    # the probability window is the short hop to the target, not to expiry.
    res.update({"pop_target": None, "curve_target": None, "breakevens_target": None,
                "target_days": None, "max_profit_target": None, "max_loss_target": None})
    td = target_days if target_days is not None else exit_day_days(now_ist)
    if td and avg_iv and T and spot:
        T_t = td / 365.0
        T_rem = T - T_t
        if T_rem > 0:
            fn = lambda S: payoff_today(legs, S, T_rem)   # noqa: E731
            try:
                ct = [[p[0], round(fn(p[0]), 2)] for p in curve]
                zt = profit_zones(legs, lo, hi, step, fn=fn)
                pt = prob_of_profit(zt, spot, T_t, avg_iv, lo, hi)
                yt = [p[1] for p in ct]
                res.update({
                    "curve_target": ct,
                    "breakevens_target": sorted({round(x, 1) for z in zt for x in z if lo < x < hi}),
                    "pop_target": round(pt, 4) if pt is not None else None,
                    "target_days": round(td, 4),
                    "max_profit_target": round(max(yt), 2),
                    "max_loss_target": round(min(yt), 2),
                })
            except Exception as e:
                log.warning("[payoff] target-date view failed: %s", e)

    if with_margin:
        res["margin"] = basket_margin(legs)
    res["total_tax"] = structure_tax(rows)   # round-trip transaction cost for the structure
    res["tax_breakdown"] = structure_tax_breakdown(rows)   # itemised + entry-only (panel tooltip)
    return res
