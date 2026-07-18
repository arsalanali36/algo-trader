#!/usr/bin/env python3
r"""portfolio.py — turn per-trade %% into a REAL rupee book (the honest gate).

The backtest produces thousands of overlapping per-stock trades. A real account
can't take them all — limited capital, limited concurrent slots. This sim:
  * starts with CAP rupees
  * holds at most MAX_SLOTS positions at once
  * sizes each new position at equity/MAX_SLOTS (so profits compound)
  * takes a signal only if a slot is free and cash is available (else SKIP)
  * reports final equity, CAGR, realized max-drawdown, taken vs skipped.

CAGR/DD here are the honest, compounding numbers (unlike the sum-of-trade-%%).
Caveat: DD is measured on realized equity at trade-exit events (open positions at
cost basis) — true intra-trade drawdown is somewhat worse.
"""
import argparse
import numpy as np
import pandas as pd
import dist_ma as m


def portfolio(tr, cap=1_000_000.0, max_slots=10):
    tr = tr.sort_values("entry_date").reset_index(drop=True)
    cash = cap
    openp = []            # dicts: exit_date, alloc, net
    curve = []            # (date, equity)
    taken = skipped = 0
    for _, t in tr.iterrows():
        ed = t.entry_date
        keep = []
        for p in openp:
            if p["exit_date"] <= ed:
                cash += p["alloc"] * (1 + p["net"])     # realize
            else:
                keep.append(p)
        openp = keep
        equity = cash + sum(p["alloc"] for p in openp)
        if len(openp) < max_slots and cash > 1:
            alloc = min(cash, equity / max_slots)
            cash -= alloc
            openp.append(dict(exit_date=t.exit_date, alloc=alloc, net=t.net))
            taken += 1
        else:
            skipped += 1
        curve.append((ed, cash + sum(p["alloc"] for p in openp)))
    for p in openp:
        cash += p["alloc"] * (1 + p["net"])
    final = cash
    cv = pd.DataFrame(curve, columns=["date", "equ"])
    cv["date"] = pd.to_datetime(cv["date"])
    peak = cv.equ.cummax()
    maxdd = ((cv.equ - peak) / peak).min()
    yrs = (cv.date.iloc[-1] - cv.date.iloc[0]).days / 365.25
    cagr = (final / cap) ** (1 / yrs) - 1 if yrs > 0 and final > 0 else float("nan")
    return dict(final=final, cagr=cagr, maxdd=maxdd, taken=taken,
                skipped=skipped, yrs=yrs, curve=cv)


def show(tr, cap, slots_list, tag=""):
    print(f"\n=== PORTFOLIO {tag}   cap=Rs{cap:,.0f} ===")
    print(f"{'slots':>5} | {'final Rs':>14} | {'CAGR':>7} | {'maxDD':>7} | "
          f"{'taken':>6} {'skip':>6} | {'x':>6}")
    print("-" * 66)
    for s in slots_list:
        r = portfolio(tr, cap, s)
        print(f"{s:>5} | {r['final']:>14,.0f} | {r['cagr']*100:>6.1f}% | "
              f"{r['maxdd']*100:>6.1f}% | {r['taken']:>6} {r['skipped']:>6} | "
              f"{r['final']/cap:>5.1f}x   ({r['yrs']:.1f}y)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=1_000_000)
    ap.add_argument("--exit", default="hold")
    ap.add_argument("--maxhold", type=int, default=40)
    ap.add_argument("--slatr", type=float, default=1.5)
    ap.add_argument("--thresh", type=float, default=-10.0)
    ap.add_argument("--cost", type=float, default=0.30)
    a = ap.parse_args()

    print(f"building trades: exit={a.exit} maxhold={a.maxhold} slatr={a.slatr} "
          f"thresh={a.thresh} cost={a.cost}%")
    tr = m.backtest(thresh=a.thresh, exit_style=a.exit, max_hold=a.maxhold,
                    sl_atr=a.slatr, cost_pct=a.cost)
    tr["y"] = pd.to_datetime(tr.entry_date).dt.year

    slots = [3, 5, 8, 10, 15, 20]
    show(tr, a.cap, slots, "FULL 2013-2026")
    show(tr[tr.y >= 2024].reset_index(drop=True), a.cap, slots, "OOS 2024-2026 only")
