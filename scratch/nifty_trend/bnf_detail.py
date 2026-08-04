"""Detailed robustness test of the winner: BANKNIFTY 6-strike short strangle,
SELL @09:20, intraday combined-premium target 50 / SL 50 pt, exit 14:55.

Deploy-gate treatment: year-by-year, train/OOS, parameter-neighbourhood grid,
significance (random-entry-time null), block-bootstrap CI, Monte-Carlo overfit,
cost stress, and tail honesty (SL-slip / gap days).
"""
import math, datetime as dt
import numpy as np, pandas as pd
import bnf_920_strangle_intraday as M
import bs_option as bs
import montecarlo as mc
import engine

N, TGT, SL, LOT = 6, 50, 50, 30   # display const; run_ts uses date-aware lot_for internally
rng = np.random.default_rng(7)


def metrics(net):
    net = np.asarray(net, float)
    wr = (net > 0).mean() * 100
    sh = net.mean() / net.std() * math.sqrt(252) if net.std() > 0 else 0
    tail = np.sort(net)[:max(1, len(net) // 20)].sum()
    return dict(n=len(net), win=wr, net=net.sum(), avg=net.mean(),
                sharpe=sh, worst=net.min(), worst5=tail)


def line(lbl, m):
    print(f"  {lbl:<26} n={m['n']:<4} win%={m['win']:4.0f}  net=Rs{m['net']:>11,.0f}  "
          f"avg=Rs{m['avg']:>6,.0f}  Sharpe={m['sharpe']:5.2f}  worst=Rs{m['worst']:>8,.0f}  "
          f"worst5%=Rs{m['worst5']:>10,.0f}")


print("Loading BANKNIFTY lake...", flush=True)
g = M.load_grid()
base = M.run_ts(g, N, TGT, SL)
base["y"] = pd.to_datetime(base.day).dt.year
print(f"  days={len(base)}  span {base.day.min()} -> {base.day.max()}  "
      f"(6-strike, tgt {TGT}/SL {SL}, lot {LOT})\n", flush=True)

print("== 1. HEADLINE ==")
line("6-strike 50/50", metrics(base.net))
rc = base.reason.value_counts(normalize=True) * 100
print(f"   exit mix: " + "  ".join(f"{k}={rc.get(k,0):.0f}%" for k in ("target", "SL", "3:15/2:55")))

print("\n== 2. YEAR BY YEAR ==")
for y, grp in base.groupby("y"):
    line(str(y), metrics(grp.net))

print("\n== 3. TRAIN (2021-2024) vs OOS (2025-2026) ==")
line("TRAIN 21-24", metrics(base[base.y <= 2024].net))
line("OOS   25-26", metrics(base[base.y >= 2025].net))

print("\n== 4. PARAMETER NEIGHBOURHOOD (is 6/50/50 a stable zone or a spike?) ==")
print("   strike x (target=SL) grid — Sharpe [net Rs L]:")
print(f"   {'':6}" + "".join(f"{v:>16}" for v in (40, 50, 60)))
for n_ in (5, 6, 7):
    cells = []
    for v in (40, 50, 60):
        m = metrics(M.run_ts(g, n_, v, v).net)
        cells.append(f"{m['sharpe']:5.2f} [{m['net']/1e5:4.1f}L]")
    print(f"   {n_}-strk" + "".join(f"{c:>16}" for c in cells))

print("\n== 5. SIGNIFICANCE — random-entry-time null (is 09:20 special, or any time works?) ==")
slots = [dt.time(9, 20), dt.time(9, 50), dt.time(10, 20), dt.time(10, 50),
         dt.time(11, 20), dt.time(11, 50), dt.time(12, 20), dt.time(12, 50), dt.time(13, 20)]
mats = {}
for st in slots:
    r = M.run_ts(g, N, TGT, SL, entry_t=st)
    mats[st] = dict(zip(r.day, r.net))
days = sorted(set(mats[slots[0]]).intersection(*[set(mats[s]) for s in slots[1:]]))
mat = np.array([[mats[s].get(d, np.nan) for s in slots] for d in days])  # [day, slot]
real_mean = np.nanmean(mat[:, 0])          # 09:20 column
null_means = []
for _ in range(3000):
    pick = rng.integers(0, len(slots), size=len(days))
    null_means.append(np.nanmean(mat[np.arange(len(days)), pick]))
null_means = np.array(null_means)
p = (null_means >= real_mean).mean()
print(f"   real 09:20 avg/day = Rs{real_mean:,.0f}")
print(f"   random-entry-time null: mean Rs{null_means.mean():,.0f}  "
      f"[5% Rs{np.percentile(null_means,5):,.0f} .. 95% Rs{np.percentile(null_means,95):,.0f}]")
print(f"   p(null >= real) = {p:.3f}   -> {'09:20 NOT special (edge is just theta-selling)' if p>0.05 else '09:20 beats random time'}")
print(f"   avg/day by entry slot: " + "  ".join(f"{s.strftime('%H:%M')}=Rs{np.nanmean([mats[s][d] for d in days]):,.0f}" for s in slots))

print("\n== 6. BLOCK-BOOTSTRAP CI (is total edge distinguishable from zero?) ==")
net = base.net.values
B, bl = 5000, 5
nb = int(np.ceil(len(net) / bl))
boots = []
for _ in range(B):
    starts = rng.integers(0, len(net) - bl, size=nb)
    samp = np.concatenate([net[s:s + bl] for s in starts])[:len(net)]
    boots.append(samp.sum())
boots = np.array(boots)
print(f"   observed net = Rs{net.sum():,.0f}")
print(f"   bootstrap net: median Rs{np.median(boots):,.0f}  "
      f"[5% Rs{np.percentile(boots,5):,.0f} .. 95% Rs{np.percentile(boots,95):,.0f}]")
print(f"   P(net <= 0) = {(boots <= 0).mean():.3f}   -> {'edge robustly > 0' if (boots<=0).mean()<0.05 else 'NOT robustly positive'}")

print("\n== 7. MONTE-CARLO overfit (trade-bootstrap, repo module) ==")
res = {"trades": [{"pnl": float(x)} for x in base.net]}
r = mc.montecarlo(res, n_sims=2000)
t = r["table"]["sharpe"]
print(f"   Sharpe: orig={t[0]:.2f}  [worst5={t[1]:.2f} / median={t[2]:.2f} / best5={t[3]:.2f}]")
print(f"   net%: {r['table']['net'][0]:.0f}  [w5 {r['table']['net'][1]:.0f} / med {r['table']['net'][2]:.0f} / b5 {r['table']['net'][3]:.0f}]")
print(f"   -> {'NOT overfit (orig near median)' if r['not_overfit'] else 'OVERFIT? (orig up in best tail)'}")

print("\n== 8. COST STRESS (DOM slippage x1 / x2 / x3) ==")
for mult in (1.0, 2.0, 3.0):
    line(f"slip x{mult:.0f}", metrics(M.run_ts(g, N, TGT, SL, slip_mult=mult).net))
bs.SLIP_MULT = 1.0

print("\n== 9. TAIL HONESTY (does the 50-pt SL actually hold?) ==")
sl_stop = 50 * LOT   # ideal SL loss in Rs (50 pt x lot)
sl_days = base[base.reason == "SL"]
slipped = sl_days[sl_days.net < -sl_stop]
print(f"   50-pt SL 'ideal' max loss = -Rs{sl_stop:,.0f}")
print(f"   SL-exit days: {len(sl_days)}  |  slipped worse than ideal: {len(slipped)} "
      f"({100*len(slipped)/max(len(sl_days),1):.0f}% of SL days)")
print(f"   worst 8 days (gap/fast-move tail the SL couldn't stop at -50):")
for _, row in base.nsmallest(8, "net").iterrows():
    print(f"     {row.day}  net=Rs{row.net:>9,.0f}  ({row.reason})")
