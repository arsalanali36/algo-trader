"""Sizing on the REAL premium series (honest_sizing.py ka method, BS data ke bina).

honest_sizing.py `runs/<slug>/results.js` padhta hai — wo Black-Scholes numbers hain
(TRAP #199). Yeh script wahi method REAL repriced per-trade series pe chalata hai:

  1. dd_per_lot = bootstrap MC ka WORST-5% maxDD (1 lot, ₹) — realised DD nahi, taaki
     sizing bad-luck ordering ke against ho, us lucky ordering ke nahi jo record hua.
  2. lots = floor(equity × dd_budget / dd_per_lot), har MAHINE dobara, min 1, --max-lots cap.
  3. P&L linear scale (CONSERVATIVE — flat ₹20/order brokerage N lots pe per-lot GIRTA
     hai, toh asli N-lot P&L isse thoda behtar hoga).
  4. equity trade-by-trade compound; CAGR + realised DD usi sequence se.

--max-lots HAMESHA do (LESSONS TRAP #127): bina cap ke compounding kisi bhi edge ko
astronomical bana deti hai, aur cap badalne pe CAGR ka hilna khud ek diagnostic hai.
"""
import argparse
import numpy as np
import pandas as pd
import bs_vs_reallake as B

LOT = 65


def mc_worst5(r, iters=5000, seed=7):
    """Bootstrap trade ORDER; har path ka maxDD; worst-5 percentile (₹, 1 lot)."""
    rng = np.random.default_rng(seed)
    dds = np.empty(iters)
    for i in range(iters):
        s = r[rng.permutation(len(r))]
        eq = s.cumsum()
        dds[i] = (eq - np.maximum.accumulate(eq)).min()
    return abs(np.percentile(dds, 5)), abs(np.percentile(dds, 50))


def simulate(r, dates, cap, dd_pct, dd_per_lot, max_lots):
    eq = cap; lots = max(1, int(cap * dd_pct / 100.0 / dd_per_lot))
    lots = min(lots, max_lots)
    curve = [eq]; lot_hist = [lots]; month = dates[0][:7]
    for pnl, d in zip(r, dates):
        if d[:7] != month:
            month = d[:7]
            lots = max(1, min(int(eq * dd_pct / 100.0 / dd_per_lot), max_lots))
        eq += pnl * lots
        curve.append(eq); lot_hist.append(lots)
        if eq <= 0:
            break
    c = np.array(curve)
    dd = ((c - np.maximum.accumulate(c)) / np.maximum.accumulate(c)).min() * 100
    yrs = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25
    cagr = ((c[-1] / cap) ** (1 / yrs) - 1) * 100 if yrs > 0 and c[-1] > 0 else -100
    return dict(final=c[-1], cagr=cagr, dd=dd, lots0=lot_hist[0],
                lots_end=lot_hist[-1], lots_max=max(lot_hist))


ap = argparse.ArgumentParser()
ap.add_argument("--slug", default="chain_zone_longatm")
ap.add_argument("--capital", type=float, default=500000)
ap.add_argument("--dd-budget", default="10,15,20,25")
ap.add_argument("--max-lots", type=int, default=10)
a = ap.parse_args()

d = B.reprice(a.slug)
r = np.array(d["trades"], float); dates = list(d["dates"])
order = np.argsort(dates); r, dates = r[order], [dates[i] for i in order]

w5, med = mc_worst5(r)
realised_eq = r.cumsum()
realised_dd = abs((realised_eq - np.maximum.accumulate(realised_eq)).min())

print("=" * 104)
print(f"REAL-PREMIUM SIZING — {a.slug}   ({dates[0]} -> {dates[-1]}, {len(r)} trades)")
print("=" * 104)
print(f"  1 lot pe:  net Rs{r.sum():,.0f}   expectancy Rs{r.mean():,.0f}/trade   "
      f"win {100*(r>0).mean():.1f}%")
print(f"  maxDD @1 lot:  realised Rs{realised_dd:,.0f}   MC median Rs{med:,.0f}   "
      f"MC worst-5% Rs{w5:,.0f}  <- sizing isi pe")
print(f"  capital cap Rs{a.capital:,.0f}   max-lots {a.max_lots}\n")
print(f"{'DD budget':>10}{'lots start':>12}{'lots end':>10}{'final Rs':>14}"
      f"{'CAGR':>9}{'realised DD':>13}")
for pct in [float(x) for x in a.dd_budget.split(",")]:
    s = simulate(r, dates, a.capital, pct, w5, a.max_lots)
    print(f"{pct:>9.0f}%{s['lots0']:>12}{s['lots_end']:>10}{s['final']:>14,.0f}"
          f"{s['cagr']:>8.1f}%{s['dd']:>12.1f}%")

print(f"\n  sanity @1 lot (no sizing): final Rs{a.capital + r.sum():,.0f}  "
      f"= capital + Rs{r.sum():,.0f}")
print("  NOTE: lots ka cap badal ke dobara chalao — CAGR bahut hile to backtest se zyada "
      "ummeed mat rakho (TRAP #127).")
