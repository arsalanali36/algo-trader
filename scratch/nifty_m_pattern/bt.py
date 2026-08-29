"""
NIFTY "IV-pop -> M-pattern rollover" short iron-fly (user idea 2026-08-28).
RESEARCH ONLY. No live/order path.

THESIS
  ATM combined premium (CE_atm + PE_atm) is ~delta-neutral, so a sharp SPIKE in it
  is mostly a vega/IV pop (not a directional move). When IV pops and the ATM combined
  premium spikes hard, then rolls over forming a small "M" (double top), SELL the ATM
  straddle + BUY wings (iron fly, defined risk) to harvest the IV crush.

SIGNAL (mechanical M / double-top on the ATM combined-premium minute series)
  1. SPIKE / IV-pop gate : a local peak P1 >= (1+SPIKE_PCT) x the rolling min of the
                           SPIKE_LOOK minutes before it.
  2. Pullback            : premium falls >= PB_PCT from P1 to a valley T.
  3. Second hump (M)     : a later local peak P2 with P2 <= P1 x (1+TOL), a real bounce
                           above T.
  4. Rollover (neckline) : premium breaks BELOW T -> ENTER here (iron fly).

TRADE (deployed config = "medium" M-strictness + hold ~1 trading day + wing +-250)
  Entry  : at the rollover minute -> SELL ATM CE + SELL ATM PE, BUY CE+250 / PE-250.
  Exit   : combined running P&L >= 50% of net credit -> close all. Else close at the
           +max_hold_days trading-day 15:20 (mid-week close = REAL premium; only a true
           weekly-expiry settle uses intrinsic). Sequential/positional: at most one open
           position; a new signal is only taken after the prior position's exit date.
  Charges: real date-aware Zerodha F&O (charges.py).

RESULT (2021-07 -> 2026-07, 5 lots): medium-M + hold+1day + wing 250 + 50% credit ->
  193 trades, net +Rs 3.6L, Sharpe ~0.64-0.70, PF 1.34, win 65%, train & OOS both green,
  maxDD -Rs 2.0L. Entry-timing permutation (random-entry null, 1000 iters): p=0.009
  (real +Rs 3.6L vs null mean -Rs 3k, z=2.34) -> the M-timing edge is REAL. But Sharpe<1
  and the config was picked from a 9-cell search -> FORWARD-PAPER, not real money (Rule 10).
  Hardening (basket SL, stricter spike) both HURT -> base config is best (see harden.py).

All premiums REAL (OptChainLake_1m/NIFTY/WEEK). Missing strike at needed minute = honest
data-gap -> trade skipped/flagged, never faked.
"""
import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(HERE, "..", "nifty_trend"))
sys.path.insert(0, os.path.join(HERE, "..", "strangle_roll"))  # win over nifty_trend/engine.py
sys.path.insert(0, os.path.join(ROOT, "strategies", "signals"))
import charges as CH
import m_pattern as mp     # SINGLE SOURCE of the M-signal (ADR-010) — live calls the SAME
from engine import (load_lake, _prem, _spot_at, _minutes, _round50,
                    _nearest_weekly, LOT, POS_EXIT_HM)

LOTS = 5
QTY  = LOTS * LOT

# constants + M-detection come from the shared module (backtest == live, by construction)
SIG_LO_HM = mp.SIG_LO
SIG_HI_HM = mp.SIG_HI
M_PRESETS = mp.M_PRESETS
_extrema = mp._extrema          # re-export (harden.py reuses it)
DEPLOYED = {"m": "medium", "wing": 250, "take": 0.50, "hold_days": 1}


def atm_combined_series(grid):
    out = []
    for m in sorted(grid):
        cell = grid[m]
        atm = _round50(cell["spot"])
        ce = cell["CE"].get(atm)
        pe = cell["PE"].get(atm)
        if ce is not None and pe is not None:
            out.append((m, ce + pe))
    return out


def detect_signal(series, params):
    """First M-rollover of the day -> (hhmm, spike_ratio) or None. Delegates to the
    shared single-source detector (m_pattern.detect) so backtest == live (ADR-010)."""
    return mp.detect(series, params, SIG_LO_HM, SIG_HI_HM)


def _atm_at(grid, hm):
    sp = _spot_at(grid, hm)
    return _round50(sp) if sp is not None else None


def run_trade(days, entry_date, entry_hm, take_pct, wing, max_hold_days=None):
    grid = days.get(entry_date)
    if not grid:
        return None
    spot0 = _spot_at(grid, entry_hm)
    atm = _round50(spot0) if spot0 is not None else None
    if atm is None:
        return None
    ce_wk, pe_wk = atm + wing, atm - wing
    sce, _ = _prem(grid, entry_hm, "CE", atm)
    spe, _ = _prem(grid, entry_hm, "PE", atm)
    bce, _ = _prem(grid, entry_hm, "CE", ce_wk)
    bpe, _ = _prem(grid, entry_hm, "PE", pe_wk)
    if None in (sce, spe, bce, bpe):
        return {"date": entry_date, "skip": "entry_strike_missing"}
    entry_credit = (sce + spe) - (bce + bpe)
    if entry_credit <= 0:
        return {"date": entry_date, "skip": "nonpositive_credit"}
    target = take_pct * entry_credit
    book = {("CE", atm):   {"side": "SELL", "p0": sce},
            ("PE", atm):   {"side": "SELL", "p0": spe},
            ("CE", ce_wk): {"side": "BUY",  "p0": bce},
            ("PE", pe_wk): {"side": "BUY",  "p0": bpe}}
    last = {k: v["p0"] for k, v in book.items()}
    exp = str(_nearest_weekly(entry_date))
    alldays = sorted(days.keys())
    week_seq = []
    for d in alldays:
        if d < entry_date or d > exp:
            continue
        lo = entry_hm + 1 if d == entry_date else 916
        hi = POS_EXIT_HM if d == exp else 1529
        week_seq.append((d, lo, hi))
    if max_hold_days is None:
        seq = week_seq
    else:
        seq = week_seq[:max_hold_days + 1]
        if seq:
            d0, lo0, _ = seq[-1]; seq[-1] = (d0, lo0, POS_EXIT_HM)
    is_exp_dl = bool(seq) and seq[-1][0] == exp
    deadline = (seq[-1][0], POS_EXIT_HM) if seq else (entry_date, POS_EXIT_HM)

    def running(dgrid, m):
        tot = 0.0
        for (ot, k), leg in book.items():
            p, _ = _prem(dgrid, m, ot, k)
            if p is not None:
                last[(ot, k)] = p
            p = last[(ot, k)]
            tot += (p - leg["p0"]) if leg["side"] == "BUY" else (leg["p0"] - p)
        return tot

    charge_legs = []; exited = False; reason = None; exit_day = deadline[0]; peak = 0.0
    for (d, lo, hi) in seq:
        dgrid = days.get(d)
        if not dgrid:
            continue
        for m in _minutes(dgrid, lo, hi):
            rp = running(dgrid, m)
            peak = max(peak, rp)
            if rp >= target:
                for (ot, k), leg in list(book.items()):
                    p, _ = _prem(dgrid, m, ot, k)
                    charge_legs.append((leg["p0"], p if p is not None else last[(ot, k)],
                                        leg["side"], d))
                book.clear(); exited = True; reason = "target"; exit_day = d
                break
        if exited:
            break
    if not exited:
        d, hm = deadline; dgrid = days.get(d)
        sp = (_spot_at(dgrid, hm) if dgrid else None) or spot0
        for (ot, k), leg in list(book.items()):
            p = None
            if dgrid:
                p, _ = _prem(dgrid, hm, ot, k)
            if p is None and is_exp_dl:
                p = max(0.0, sp - k) if ot == "CE" else max(0.0, k - sp)
            if p is None:
                p = last[(ot, k)]
            charge_legs.append((leg["p0"], p, leg["side"], d))
        book.clear(); reason = "expiry" if is_exp_dl else "time_exit"; exit_day = d
    pts = sum((xp - p0) if side == "BUY" else (p0 - xp) for (p0, xp, side, w) in charge_legs)
    gross = pts * QTY
    charge = sum(CH.option_charges(p0, xp, QTY, entry_side=side, when=w)
                 for (p0, xp, side, w) in charge_legs)
    return {"date": entry_date, "entry_hm": entry_hm, "exit_date": exit_day, "expiry": exp,
            "entry_credit": round(entry_credit, 2), "target_pts": round(target, 2),
            "peak_pts": round(peak, 2), "atm": atm, "spot0": round(spot0, 1),
            "gross": round(gross, 1), "charges": round(charge, 1),
            "net": round(gross - charge, 1), "reason": reason,
            "hold_days": len(set(w for (_, _, _, w) in charge_legs)) or 1}


def all_signals(days, params):
    out = []
    for d in sorted(days.keys()):
        r = detect_signal(atm_combined_series(days[d]), params)
        if r is not None:
            out.append((d, r[0]))
    return out


def run(days, sigs, take_pct, wing, max_hold_days=None):
    out = []; busy = ""
    for (d, hm) in sigs:
        if d <= busy:
            continue
        r = run_trade(days, d, hm, take_pct, wing, max_hold_days)
        if not r or "skip" in r:
            continue
        out.append(r); busy = r["exit_date"]
    return out


def deployed_rows(days=None):
    """The deployed config's trade rows (for build_run.py Lab artifact)."""
    days = days or load_lake()
    sigs = all_signals(days, M_PRESETS[DEPLOYED["m"]])
    return run(days, sigs, DEPLOYED["take"], DEPLOYED["wing"], DEPLOYED["hold_days"])


def stats(rows, label):
    if not rows:
        return {"label": label, "n": 0}
    net = np.array([r["net"] for r in rows])
    eq = np.cumsum(net); dd = (eq - np.maximum.accumulate(eq)).min()
    gp = net[net > 0].sum(); gl = -net[net < 0].sum()
    tgt = sum(1 for r in rows if r["reason"] == "target")
    yrs = (int(max(r["date"] for r in rows)[:4]) - int(min(r["date"] for r in rows)[:4])) + 1
    sharpe = (net.mean() / net.std()) * np.sqrt(len(net) / max(1, yrs)) if net.std() else 0
    return {"label": label, "n": len(rows), "net": round(net.sum()),
            "avg": round(net.mean(), 1), "win": round(100 * (net > 0).mean(), 1),
            "pf": round(gp / gl, 2) if gl else 99.0, "maxdd": round(dd),
            "best": round(net.max()), "worst": round(net.min()),
            "target_hit": tgt, "expiry_settle": len(rows) - tgt,
            "sharpe": round(sharpe, 2)}


if __name__ == "__main__":
    print("loading lake ...", flush=True)
    days = load_lake()
    rows = deployed_rows(days)
    s = stats(rows, "deployed (medium-M + hold+1day + wing250 + 50% credit)")
    print(f"  trades={s['n']}  net(5lot)=Rs {s['net']:,}  Sharpe={s['sharpe']}  "
          f"PF={s['pf']}  win={s['win']}%  maxDD=Rs {s['maxdd']:,}", flush=True)
    tr = stats([r for r in rows if r["date"] < "2025-01-01"], "train")
    oos = stats([r for r in rows if r["date"] >= "2025-01-01"], "oos")
    print(f"  train net=Rs {tr.get('net',0):,} (Sh {tr.get('sharpe',0)})  "
          f"OOS net=Rs {oos.get('net',0):,} (Sh {oos.get('sharpe',0)})", flush=True)
