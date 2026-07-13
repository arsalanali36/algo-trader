"""Black-Scholes ATM-option pass — the 3rd, SEPARATE layer of the pipeline.

Pipeline (each a distinct, inspectable pass):
    (1) Instrument  — raw signal P&L on NIFTY spot (no RMS, no options)
    (2) + RMS       — same trades, daily loss/profit caps applied
    (3) + BS (here) — pass-2 trades repriced into the ATM CE/PE premium actually
                      traded (delta + theta + real Zerodha F&O charges)

Signals are found on spot but the strategy BUYS ATM options, and real expired-
weekly 1-min option data does not exist (Dhan drops it — LESSONS TRAP #100).
So we PRICE the ATM option at entry & exit with Black-Scholes to get a realistic
option-premium P&L instead of a misleading spot-notional number.

Spec: scratch/nifty_trend/BS_OPTION_SIM.md.  Pure — no external option data.
"""
import os
import math
import datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R_FREE = 0.065          # risk-free (small effect)
STRIKE_STEP = 50        # NIFTY strikes every 50 pts
VIX_FLOOR, VIX_CAP = 0.08, 0.60   # clamp the realised-vol proxy to a sane IV band


# ---------------- Black-Scholes ----------------
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S, K, T, sigma, r=R_FREE, opt="CE"):
    """European option price. T in years, sigma annualised. At/after expiry -> intrinsic."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_delta(S, K, T, sigma, r=R_FREE, opt="CE"):
    """Option delta (dPrice/dSpot). CE in (0,1), PE in (-1,0). At/after expiry -> {0,±1}.
    Needed for delta-neutral structures: net portfolio delta = Σ position_sign * bs_delta(leg)."""
    if T <= 0 or sigma <= 0:
        if opt == "CE":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _norm_cdf(d1) if opt == "CE" else _norm_cdf(d1) - 1.0


# ---------------- real Zerodha F&O charges (mirror index.html calcCharges) ----------------
def calc_charges(entry_prem, exit_prem, qty, entry_side="BUY"):
    buy_px = entry_prem if entry_side == "BUY" else exit_prem
    sell_px = exit_prem if entry_side == "BUY" else entry_prem
    buy_turn, sell_turn = buy_px * qty, sell_px * qty
    total = buy_turn + sell_turn
    brokerage = 40.0                    # Rs 20 x 2 legs
    stt = 0.000625 * sell_turn          # 0.0625% on SELL premium
    exch = 0.00053 * total              # 0.053% both legs
    sebi = 0.0000001 * total            # Rs 10 / crore
    stamp = 0.00003 * buy_turn          # 0.003% on BUY
    gst = 0.18 * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst


# ---------------- DOM-measured spread slippage (brother's real order-book) ----------------
# The BS price above is the option MID (fair value). A real fill crosses the bid/ask spread:
# BUY at ask = mid*(1+h), SELL at bid = mid*(1-h). Extra round-trip cost per LEG = h*(|ep|+|xp|)*qty
# where h = the DOM-measured per-leg one-way half-spread FRACTION for that premium band
# (dom_cost.py / dom_spread_calib.json — NIFTY ATM ≈ 0.11-0.16%, OTM wings ≈ 0.24%). Prior to
# 2026-07-12 the reprice* pass modeled ZERO spread; this makes every future hunt honest by default.
# Measured on 21 recent days (Jun-Jul'26) → RECENT-regime; set SLIP_MULT>1 to stress older years.
# See ADR-005. Set SLIP_ENABLED=False to reproduce the pre-2026-07-12 zero-slip numbers.
SLIP_ENABLED = True     # global toggle
SLIP_MULT    = 1.0      # >1 = regime/robustness stress
_DOMC = None            # lazy-loaded dom_cost.DomCost (or False if calib missing)


def _domc():
    global _DOMC
    if _DOMC is None:
        try:
            import dom_cost
            _DOMC = dom_cost.load() or False
        except Exception as e:
            print(f"[bs] DOM slip calib not loaded ({e}) -> 0.15% conservative fallback", flush=True)
            _DOMC = False
    return _DOMC


def slip_frac_for(premium):
    """per-leg one-way half-spread FRACTION for an option at this premium (DOM-calibrated)."""
    if not SLIP_ENABLED:
        return 0.0
    dc = _domc()
    base = dc.slip_for(abs(premium)) if dc else 0.0015   # 0.15% fallback if calib missing
    return base * SLIP_MULT


def slip_cost_leg(entry_prem, exit_prem, qty):
    """rupee spread cost for ONE option leg's round trip: h*(|ep|+|xp|)*qty (0 if disabled)."""
    return slip_frac_for(entry_prem) * (abs(entry_prem) + abs(exit_prem)) * qty


# ---------------- weekly expiry (time-to-expiry) ----------------
import expiry_calendar as _exp   # same folder: historical NIFTY expiry-weekday schedule (verified circulars)


def _next_weekly_expiry(ts, weekday=None, hour=15, minute=30):
    """That week's expiry datetime. The expiry WEEKDAY is looked up from the official
    NSE/SEBI schedule in force on `ts` (Thursday pre-2025-09-01, Tuesday after) — NOT a
    single hardcoded day, which mis-priced T across the 8.5yr window (KNOWN_ISSUES #1).
    Holidays ignored — sub-day T effect is negligible for same-day intraday trades.
    Rolls to next week if already past this week's. Pass `weekday` to override (tests)."""
    ts = pd.Timestamp(ts)
    if weekday is None:
        weekday = _exp.weekly_expiry_weekday(ts.date())
    days_ahead = (weekday - ts.weekday()) % 7
    exp = ts.normalize() + pd.Timedelta(days=days_ahead) + pd.Timedelta(hours=hour, minutes=minute)
    if ts > exp:
        exp = exp + pd.Timedelta(days=7)
    return exp


def tte_years(ts):
    ts = pd.Timestamp(ts)
    secs = (_next_weekly_expiry(ts) - ts).total_seconds()
    return max(secs, 0.0) / (365.0 * 86400.0)


def _next_monthly_expiry(ts, hour=15, minute=30):
    """Last <expiry-weekday> of the month (rolls to next month if already past).
    Positional trades hold multiple days → weekly options bleed theta fast, so the
    positional option pass prices a MONTHLY ATM (realistic multi-day instrument)."""
    ts = pd.Timestamp(ts)
    wd = _exp.monthly_expiry_weekday(ts.date())

    def _last_wd(year, month):
        # last day of month
        nxt = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(1)
        d = nxt.normalize()
        while d.weekday() != wd:
            d -= pd.Timedelta(days=1)
        return d + pd.Timedelta(hours=hour, minutes=minute)

    exp = _last_wd(ts.year, ts.month)
    if ts > exp:
        nm = (ts + pd.offsets.MonthBegin(1))
        exp = _last_wd(nm.year, nm.month)
    return exp


def tte_years_monthly(ts):
    ts = pd.Timestamp(ts)
    secs = (_next_monthly_expiry(ts) - ts).total_seconds()
    return max(secs, 0.0) / (365.0 * 86400.0)


# ---------------- implied vol (realised-vol proxy; plug real India VIX later) ----------------
def realised_vol_map(daily_close):
    """date -> annualised sigma from 20-day rolling std of daily log-returns.
    A defensible ATM-IV first approximation with zero extra data. Clamped to a
    sane band. (Swap in real India VIX here for a refinement — BS_OPTION_SIM.md.)"""
    s = pd.Series(daily_close.values, index=pd.to_datetime(daily_close.index))
    lr = np.log(s / s.shift(1))
    vol = lr.rolling(20).std() * math.sqrt(252)
    vol = vol.bfill().clip(VIX_FLOOR, VIX_CAP)
    return {d.date(): float(v) for d, v in vol.items()}


# ---------------- REAL implied vol: India VIX (the market's actual 30-day IV) ----------------
# The realised-vol proxy above carries NO volatility-risk-premium (implied == realised by
# construction), which is exactly why every BS-modeled SHORT-vol structure showed -95..-100%
# historically (TRAP #106 family) — it could not "see" the VRP that short-vol harvests.
# India VIX is NSE's published 30-day annualised implied-vol index (in %); pricing legs at
# VIX/100 finally lets a BS backtest capture the VRP (implied usually > realised) honestly,
# AND injects real vega: a day VIX spikes marks a short position to a loss, a day it crushes
# marks it to a gain — the single most important driver for delta-neutral / short-vol.
VIX_REAL_FLOOR, VIX_REAL_CAP = 0.05, 1.20   # 5%..120% — wide enough to keep the COVID spike (VIX 83)
_VIX_CSV = os.path.join(HERE, "india_vix_daily.csv")


def vix_sigma_map(csv_path=None):
    """date -> annualised sigma from REAL India VIX daily close (Dhan sec_id 21).
    Falls back to raising if the file is missing — a short-vol backtest MUST NOT
    silently run on the VRP-free realised proxy (would invalidate the whole result)."""
    path = csv_path or _VIX_CSV
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"India VIX history not found: {path}. Run `python fetch_vix.py` first "
            "(short-vol/delta-neutral backtests require REAL implied vol, not the proxy).")
    df = pd.read_csv(path)
    out = {}
    for _, r in df.iterrows():
        d = pd.Timestamp(r["Date"]).date()
        sig = float(r["Close"]) / 100.0
        out[d] = min(max(sig, VIX_REAL_FLOOR), VIX_REAL_CAP)
    return out


def sigma_map_forward_filled(base_map, all_dates):
    """VIX is a DAILY series; a backtest bar's date may be a half-day/holiday-adjacent
    session VIX didn't print. Return a map covering every date in `all_dates`, carrying
    the last known VIX forward (never a guess — just the most recent real print)."""
    filled, last = {}, None
    for d in sorted(all_dates):
        if d in base_map:
            last = base_map[d]
        if last is not None:
            filled[d] = last
    return filled


# ---------------- lot size (never hardcode — scrip master) ----------------
def get_nifty_lot(default=75):
    try:
        import sys
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from _data import dhan_master  # offline CSV read, no API, free
        res = dhan_master.get_option_contract("NIFTY", 22000, "CE")
        if res and res[2]:
            return int(res[2])
    except Exception as e:  # scratch/offline -> labelled fallback (scales Rs only, not the edge)
        print(f"[bs] NIFTY lot fallback = {default} ({e})", flush=True)
    return default


# ---------------- reprice a spot trade list into ATM option premium P&L ----------------
def reprice(trades, sigma_map, lot_size, lots=1, r=R_FREE):
    """trades: list of dicts from intraday_engine (side, entry, exit, entry_dt, exit_dt,
    points, bars, entry_i, exit_i). Returns a NEW list with option fields per
    RESULTS_SCHEMA.all_trades. We BUY the ATM option (CE for long, PE for short)."""
    qty = int(lots) * int(lot_size)
    out = []
    for t in trades:
        opt = "CE" if t["side"] == "long" else "PE"
        S_in, S_out = float(t["entry"]), float(t["exit"])
        K = round(S_in / STRIKE_STEP) * STRIKE_STEP
        e_ts, x_ts = pd.Timestamp(t["entry_dt"]), pd.Timestamp(t["exit_dt"])
        sig_in = sigma_map.get(e_ts.date(), 0.15)
        sig_out = sigma_map.get(x_ts.date(), sig_in)
        ep = bs_price(S_in, K, tte_years(e_ts), sig_in, r, opt)
        xp = bs_price(S_out, K, tte_years(x_ts), sig_out, r, opt)
        gross = (xp - ep) * qty                       # bought option: long premium
        fee = calc_charges(ep, xp, qty)
        slip = slip_cost_leg(ep, xp, qty)             # DOM real bid/ask spread
        pnl = gross - fee - slip
        out.append({
            "side": t["side"], "opt_type": opt, "strike": K,
            "entry_dt": str(e_ts)[:16], "exit_dt": str(x_ts)[:16],
            "entry_spot": round(S_in, 1), "exit_spot": round(S_out, 1),
            "points": round(float(t["points"]), 1),
            "entry_prem": round(ep, 2), "exit_prem": round(xp, 2), "qty": qty,
            "gross": round(gross, 0), "fee": round(fee, 0), "slip": round(slip, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", ""),
            "entry_i": int(t.get("entry_i", 0)), "exit_i": int(t.get("exit_i", 0)),
        })
    return out


# ---------- reprice a spot trade list into a CREDIT-SPREAD P&L (option SELLING) ----------
def reprice_spread(trades, sigma_map, lot_size, lots=1, wing_steps=10,
                   vrp_mult=1.0, r=R_FREE):
    """Directional credit spread — the deployable form of the chain-zone SELL strategy:
        long  signal -> BULL-PUT  spread  (SELL ATM PE + BUY OTM PE `wing_steps` below)
        short signal -> BEAR-CALL spread  (SELL ATM CE + BUY OTM CE `wing_steps` above)
    Defined risk (the bought wing is the hedge). We RECEIVE net credit at entry and pay
    to close at exit -> gross = (credit_in - cost_out) * qty. Real Zerodha charges on all
    4 legs (2 in + 2 out) via calc_charges per leg. vrp_mult>1 => richer premium (helps a
    seller); use vrp_mult<1 as an HONEST stress (thinner credit) so the seller edge isn't
    over-stated by a realised-vol-priced BS world. Output shape mirrors reprice()."""
    qty = int(lots) * int(lot_size)
    w = int(wing_steps) * STRIKE_STEP
    out = []
    for t in trades:
        S_in, S_out = float(t["entry"]), float(t["exit"])
        K = round(S_in / STRIKE_STEP) * STRIKE_STEP
        if t["side"] == "long":
            ot, Kw = "PE", K - w                      # bull-put: sell ATM PE, buy lower PE
        else:
            ot, Kw = "CE", K + w                      # bear-call: sell ATM CE, buy higher CE
        e_ts, x_ts = pd.Timestamp(t["entry_dt"]), pd.Timestamp(t["exit_dt"])
        sig_in = sigma_map.get(e_ts.date(), 0.15) * vrp_mult
        sig_out = sigma_map.get(x_ts.date(), sig_in / vrp_mult if vrp_mult else 0.15) * vrp_mult
        T_in, T_out = tte_years(e_ts), tte_years(x_ts)
        atm_in = bs_price(S_in, K, T_in, sig_in, r, ot)
        atm_out = bs_price(S_out, K, T_out, sig_out, r, ot)
        wing_in = bs_price(S_in, Kw, T_in, sig_in, r, ot)
        wing_out = bs_price(S_out, Kw, T_out, sig_out, r, ot)
        credit_in = atm_in - wing_in                  # received at entry (>0)
        cost_out = atm_out - wing_out                 # paid to close
        gross = (credit_in - cost_out) * qty          # short the spread
        fee = (calc_charges(atm_in, atm_out, qty, entry_side="SELL")     # short ATM leg
               + calc_charges(wing_in, wing_out, qty, entry_side="BUY"))  # long wing leg
        slip = (slip_cost_leg(atm_in, atm_out, qty)                       # per-LEG DOM spread
                + slip_cost_leg(wing_in, wing_out, qty))                  # (wing = OTM, wider %)
        pnl = gross - fee - slip
        out.append({
            "side": t["side"], "opt_type": ot + " spread", "strike": K, "wing": Kw,
            "entry_dt": str(e_ts)[:16], "exit_dt": str(x_ts)[:16],
            "entry_spot": round(S_in, 1), "exit_spot": round(S_out, 1),
            "points": round(credit_in - cost_out, 1),
            "entry_prem": round(credit_in, 2), "exit_prem": round(cost_out, 2), "qty": qty,
            "gross": round(gross, 0), "fee": round(fee, 0), "slip": round(slip, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", ""),
            "entry_i": int(t.get("entry_i", 0)), "exit_i": int(t.get("exit_i", 0)),
        })
    return out


# ---------- reprice a POSITIONAL spot trade list into MONTHLY ATM-BUY P&L ----------
def reprice_positional(trades, sigma_map, lot_size, lots=1, r=R_FREE):
    """Positional (multi-day) → BUY MONTHLY ATM (CE for long / PE for short).
    Uses monthly time-to-expiry so a multi-day hold doesn't cross a weekly 0DTE.
    Theta over the hold is still captured (T_exit < T_entry). Same all_trades[] shape."""
    qty = int(lots) * int(lot_size)
    out = []
    for t in trades:
        opt = "CE" if t["side"] == "long" else "PE"
        S_in, S_out = float(t["entry"]), float(t["exit"])
        K = round(S_in / STRIKE_STEP) * STRIKE_STEP
        e_ts, x_ts = pd.Timestamp(t["entry_dt"]), pd.Timestamp(t["exit_dt"])
        sig_in = sigma_map.get(e_ts.date(), 0.15)
        sig_out = sigma_map.get(x_ts.date(), sig_in)
        # if the hold crosses the entry-month expiry, the option is rolled → keep the
        # SAME monthly cycle by pricing exit T against the entry's monthly expiry
        # (bounded at 0 = expiry intrinsic); positional roll modeled as re-priced intrinsic.
        exp = _next_monthly_expiry(e_ts)
        T_in = max((exp - e_ts).total_seconds(), 0.0) / (365.0 * 86400.0)
        T_out = max((exp - x_ts).total_seconds(), 0.0) / (365.0 * 86400.0)
        ep = bs_price(S_in, K, T_in, sig_in, r, opt)
        xp = bs_price(S_out, K, T_out, sig_out, r, opt)
        gross = (xp - ep) * qty                       # bought option: long premium
        fee = calc_charges(ep, xp, qty)
        slip = slip_cost_leg(ep, xp, qty)             # DOM real bid/ask spread
        pnl = gross - fee - slip
        out.append({
            "side": t["side"], "opt_type": opt, "strike": K,
            "entry_dt": str(e_ts)[:16], "exit_dt": str(x_ts)[:16],
            "entry_spot": round(S_in, 1), "exit_spot": round(S_out, 1),
            "points": round(float(t["points"]), 1),
            "entry_prem": round(ep, 2), "exit_prem": round(xp, 2), "qty": qty,
            "gross": round(gross, 0), "fee": round(fee, 0), "slip": round(slip, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", ""),
            "entry_i": int(t.get("entry_i", 0)), "exit_i": int(t.get("exit_i", 0)),
        })
    return out


# ---------- reprice a spot trade list into a NAKED ATM SELL P&L ----------
def reprice_naked(trades, sigma_map, lot_size, lots=1, vrp_mult=1.0, r=R_FREE):
    """Naked ATM option SELL (no wing): long signal -> SELL ATM PE, short -> SELL ATM CE.
    ⚠️ ENGINE-COMPARISON ONLY — undefined tail risk; live pe wings/hedge MANDATORY
    (framework rule). 2 orders round-trip (vs 4 for the spread) -> lower charges,
    higher credit. gross = (entry_prem - exit_prem) * qty (sold premium)."""
    qty = int(lots) * int(lot_size)
    out = []
    for t in trades:
        opt = "PE" if t["side"] == "long" else "CE"
        S_in, S_out = float(t["entry"]), float(t["exit"])
        K = round(S_in / STRIKE_STEP) * STRIKE_STEP
        e_ts, x_ts = pd.Timestamp(t["entry_dt"]), pd.Timestamp(t["exit_dt"])
        sig_in = sigma_map.get(e_ts.date(), 0.15) * vrp_mult
        sig_out = sigma_map.get(x_ts.date(), 0.15) * vrp_mult
        ep = bs_price(S_in, K, tte_years(e_ts), sig_in, r, opt)
        xp = bs_price(S_out, K, tte_years(x_ts), sig_out, r, opt)
        gross = (ep - xp) * qty                       # sold premium: profit when it decays
        fee = calc_charges(ep, xp, qty, entry_side="SELL")
        slip = slip_cost_leg(ep, xp, qty)             # DOM real bid/ask spread
        pnl = gross - fee - slip
        out.append({
            "side": t["side"], "opt_type": opt + " naked", "strike": K,
            "entry_dt": str(e_ts)[:16], "exit_dt": str(x_ts)[:16],
            "entry_spot": round(S_in, 1), "exit_spot": round(S_out, 1),
            "points": round(ep - xp, 1),
            "entry_prem": round(ep, 2), "exit_prem": round(xp, 2), "qty": qty,
            "gross": round(gross, 0), "fee": round(fee, 0), "slip": round(slip, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", ""),
            "entry_i": int(t.get("entry_i", 0)), "exit_i": int(t.get("exit_i", 0)),
        })
    return out


if __name__ == "__main__":
    # sanity: ATM ~= 0.4*S*sigma*sqrt(T)
    S, sig, T = 22000, 0.15, 2 / 365
    print("BS CE ATM:", round(bs_price(S, 22000, T, sig, opt="CE"), 1),
          " approx:", round(0.4 * S * sig * math.sqrt(T), 1))
    print("1-lot round-trip charge (prem~100, lot 75):",
          round(calc_charges(100, 110, 75), 1))
    print("lot:", get_nifty_lot())
