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
        pnl = gross - fee
        out.append({
            "side": t["side"], "opt_type": opt, "strike": K,
            "entry_dt": str(e_ts)[:16], "exit_dt": str(x_ts)[:16],
            "entry_spot": round(S_in, 1), "exit_spot": round(S_out, 1),
            "points": round(float(t["points"]), 1),
            "entry_prem": round(ep, 2), "exit_prem": round(xp, 2), "qty": qty,
            "gross": round(gross, 0), "fee": round(fee, 0), "pnl": round(pnl, 1),
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
