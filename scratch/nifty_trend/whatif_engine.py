#!/usr/bin/env python3
"""whatif_engine.py — per-trade "what-if exit" P&L for a BACKTEST run.

For every completed backtest trade, replay it under alternate exit rules the user
wants visible in ONE sheet (so they never have to ask "ye column bhi dikhao"):

    WI Fixed T4k/SL1k   fixed target ₹4000/lot, fixed SL ₹1000/lot
    WI RR 1:1 / 1:2 / 1:3   fixed SL ₹1000/lot, target = RR x that
    WI Aggr Trail        aggressive trailing SL (₹6000 / ₹2500 / step ₹100 per lot)

HOW IT STAYS HONEST
-------------------
The exit-replay itself is the LIVE what-if engine reused verbatim
(scripts/path_aware_sl_sim.replay_legacy / replay_aggr — Rule 6B, same code that
drives the Stats "Opt Fixed/Aggr" columns). We only feed it a premium PATH.

results.js all_trades carries no intraday premium path (only entry/exit endpoints),
so we RECONSTRUCT it: for each single-leg trade we take the day's intraday NIFTY
bars over the hold, and Black-Scholes-reprice the SAME strike each bar — with sigma
implied from the trade's OWN entry_prem, drifted to the exit implied, so the
reconstructed path passes through the real entry_prem AND exit_prem and only the
intra-hold shape is modelled. Anchored to the actual trade, not a fresh guess.

MULTI-LEG STRUCTURES (ratio_backspread / condor / straddle) have no single-strike
premium — their what-if needs the structure's per-bar net MTM, which isn't in
all_trades. Those are reported covered=False (honest "—", never a fake number);
Phase-2 wires them from option_structures. Single-leg CE/PE (ORB, chain-zone-buy —
the deployed fauj) are computed exactly here.
"""
import os
import sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # CODE3B repo root
for p in (ROOT, os.path.join(ROOT, "scripts"), HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
try:
    import _paths  # noqa: F401  sys.path bootstrap for _core/_data
except Exception:
    pass

import bs_option as bs
import intraday_engine as ie
import path_aware_sl_sim as pas   # replay_legacy / replay_aggr / _agg_cfg

# ---- what-if rule set (EVOLVING — add a column here, it flows to the sheet) ----
FIX = {"target": 4000, "sl": 1000}
RR_RISK = 1000                                  # per-lot ₹ risk unit for RR columns
AGG = pas._agg_cfg(6000, 2500, 100)             # aggressive trail (deployed defaults)

# column key -> human label (order preserved). Producer reads this to build headers.
COLUMNS = [
    ("fix",  "WI Fixed T4k/SL1k"),
    ("rr1",  "WI RR 1:1"),
    ("rr2",  "WI RR 1:2"),
    ("rr3",  "WI RR 1:3"),
    ("aggr", "WI Aggr Trail"),
]

_ONE_LEG = {"CE", "PE"}


def _load_tf(tf):
    """Intraday NIFTY resampled to the run's timeframe, Datetime-indexed. Cached."""
    if not hasattr(_load_tf, "_c"):
        _load_tf._c = {}
    if tf not in _load_tf._c:
        d = ie.resample(ie.load_1m(), tf).copy()
        d["Datetime"] = pd.to_datetime(d["Datetime"])
        _load_tf._c[tf] = d.set_index("Datetime").sort_index()
    return _load_tf._c[tf]


def _premium_bars(t, tf):
    """Reconstruct [hhmm, o,h,l,c] PREMIUM bars over the hold for a single-leg trade.
    Anchored: sigma implied from entry_prem, drifted linearly to exit's implied, so
    the path hits entry_prem at entry and (approx) exit_prem at exit."""
    ot = t["opt_type"]
    if ot not in _ONE_LEG:
        return None
    K = float(t["strike"])
    ep, xp = float(t["entry_prem"]), float(t["exit_prem"])
    e_ts, x_ts = pd.Timestamp(t["entry_dt"]), pd.Timestamp(t["exit_dt"])
    df = _load_tf(tf)
    win = df.loc[e_ts:x_ts]
    if len(win) < 2 or ep <= 0:
        return None
    S_e = float(t["entry_spot"])
    T_e = bs.tte_years(e_ts)
    # implied vols at the two anchors (fall back to 0.15 if solver fails)
    try:
        sig_e = bs.implied_vol(ep, S_e, K, T_e, opt=ot) or 0.15
    except Exception:
        sig_e = 0.15
    try:
        sig_x = bs.implied_vol(xp, float(t["exit_spot"]), K, bs.tte_years(x_ts), opt=ot) or sig_e
    except Exception:
        sig_x = sig_e
    n = len(win)
    bars = []
    for i, (ts, row) in enumerate(win.iterrows()):
        frac = i / (n - 1) if n > 1 else 1.0
        sig = sig_e + (sig_x - sig_e) * frac       # drift implied across the hold
        T = max(bs.tte_years(ts), 1e-6)
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        po = bs.bs_price(o, K, T, sig, opt=ot)
        pc = bs.bs_price(c, K, T, sig, opt=ot)
        # premium high/low: CE rises with spot, PE falls with spot
        if ot == "CE":
            ph = bs.bs_price(h, K, T, sig, opt=ot); pl = bs.bs_price(l, K, T, sig, opt=ot)
        else:
            ph = bs.bs_price(l, K, T, sig, opt=ot); pl = bs.bs_price(h, K, T, sig, opt=ot)
        hhmm = ts.strftime("%H:%M")
        bars.append((hhmm, round(po, 2), round(ph, 2), round(pl, 2), round(pc, 2)))
    return bars


def _net_from_gross(entry_prem, gross, qty, e_ts):
    """Turn a what-if GROSS ₹ back into NET ₹ (comparable to the Net column) by
    deriving the implied exit premium and charging real Zerodha F&O on it."""
    if not qty:
        return round(gross, 1)
    exit_prem = entry_prem + gross / qty          # long option: gross=(xp-ep)*qty
    fee = bs.calc_charges(entry_prem, max(exit_prem, 0.0), qty, when=e_ts)
    return round(gross - fee, 1)


def compute(all_trades, meta):
    """Returns list aligned to all_trades: {fix,rr1,rr2,rr3,aggr, covered:bool}.
    Single-leg CE/PE computed exactly; anything else covered=False (all cols '—')."""
    tf = meta.get("tf", "5m")
    lot = int(meta.get("lot_size", 65) or 65)
    out = []
    for t in all_trades:
        row = {k: None for k, _ in COLUMNS}
        row["covered"] = False
        try:
            bars = _premium_bars(t, tf)
        except Exception:
            bars = None
        if not bars:
            out.append(row)
            continue
        ep = float(t["entry_prem"])
        qty = int(t["qty"])
        lots = max(1, round(qty / lot))
        gross_actual = float(t.get("gross", 0.0) or 0.0)
        # bought option -> BUY side in the replay engine
        _, g_fix = pas.replay_legacy(bars, "BUY", ep, qty, FIX["target"] * lots,
                                     FIX["sl"] * lots, gross_actual)
        rr = {}
        for key, mult in (("rr1", 1), ("rr2", 2), ("rr3", 3)):
            _, g = pas.replay_legacy(bars, "BUY", ep, qty, RR_RISK * mult * lots,
                                     RR_RISK * lots, gross_actual)
            rr[key] = g
        _, g_agg = pas.replay_aggr(bars, "BUY", ep, qty, AGG, lots, gross_actual)
        e_ts = pd.Timestamp(t["entry_dt"])
        row["fix"] = _net_from_gross(ep, g_fix, qty, e_ts)
        row["rr1"] = _net_from_gross(ep, rr["rr1"], qty, e_ts)
        row["rr2"] = _net_from_gross(ep, rr["rr2"], qty, e_ts)
        row["rr3"] = _net_from_gross(ep, rr["rr3"], qty, e_ts)
        row["aggr"] = _net_from_gross(ep, g_agg, qty, e_ts)
        row["covered"] = True
        out.append(row)
    return out
