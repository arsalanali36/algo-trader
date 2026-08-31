"""04.03.02 param sweep — protocol-first, taaki jawab shor na ho.

KHATRA: 1,728 combos me se "best" nikalna = multiple testing. Pure shor pe bhi koi na koi
config accha dikhega. Isliye:

  1. Sweep sirf TRAIN pe (< 2025-01-01). OOS ko haath nahi lagta.
  2. Har config ki P&L REAL premium se (BS nahi) — hamesha.
  3. TRAIN ka winner chuno, phir OOS ek hi baar dekho.
  4. Baseline (abhi ka deployed config) usi list me rakho — winner usse OOS pe behtar
     hai ya nahi, yahi asli sawaal hai.
  5. Report karo kitne configs try kiye — warna "best" ka matlab hi nahi.

Exit params (atr_sl/rr) FIX rakhe hain: exit-tweak pe 4 pichhle research sab REJECT hue
(rr_sweep, lot_sl_hypothesis, rsi_fixed_bracket, default_target_sl). Sirf SIGNAL params.
"""
import itertools, time, sys
import numpy as np
import intraday_engine as E
import bs_vs_reallake as BV
import bs_option as bs
import real_struct2 as rs2
import pandas as pd

SPLIT = "2025-01-01"
LOT, STEP = 65, 50
BASE = dict(touch_tol=5.0, zone_age=2, max_cs=40.0, hawa=False,
            chain_lookback=20, atr_sl=2.5, rr=1.5)
GRID = dict(touch_tol=[0.0, 5.0, 10.0, 15.0], zone_age=[1, 2, 3],
            max_cs=[25.0, 40.0, 60.0, 100.0], hawa=[False, True],
            chain_lookback=[10, 20])


def reprice(trades):
    """Engine ke SPOT trades -> ATM option BUY, REAL lake premium se."""
    out_p, out_d = [], []
    for t in trades:
        ie, xe = BV._bar_at(t["entry_dt"]), BV._bar_at(t["exit_dt"])
        if ie is None or xe is None:
            continue
        K = round(float(t["entry"]) / STEP) * STEP
        opt = "CE" if str(t["side"]) == "long" else "PE"
        ep, xp = rs2._px(BV._G, ie, opt, K), rs2._px(BV._G, xe, opt, K)
        if ep <= 0:
            continue
        when = pd.Timestamp(t["entry_dt"])
        gross = (xp - ep) * LOT
        fee = bs.calc_charges(ep, max(xp, 0.0), LOT, entry_side="BUY", when=when)
        slip = bs.slip_cost_leg(ep, xp, LOT)
        out_p.append(gross - fee - slip); out_d.append(str(t["entry_dt"])[:10])
    return np.array(out_p), np.array(out_d)


def stat(p):
    if len(p) < 30:
        return None
    pf = p[p > 0].sum() / -p[p < 0].sum() if (p < 0).any() else float("inf")
    return dict(n=len(p), net=p.sum(), pf=pf, exp=p.mean(), wr=100 * (p > 0).mean())


d = E.resample(E.load_1m(), "5m")
keys = list(GRID)
combos = [dict(zip(keys, v)) for v in itertools.product(*[GRID[k] for k in keys])]
print(f"configs: {len(combos)}  (exit params fix: atr_sl={BASE['atr_sl']} rr={BASE['rr']})")
print(f"train < {SPLIT}   |   OOS >= {SPLIT}  (OOS ko sweep me NAHI dekha jaayega)\n")

rows = []
t0 = time.time()
for i, c in enumerate(combos, 1):
    p = dict(BASE); p.update(c)
    try:
        res = E.backtest(d, "chain_zone", p, exit_style="stop_only")
    except Exception:
        continue
    tr = res["trades"] if isinstance(res, dict) else res
    pnl, dates = reprice(tr)
    if len(pnl) < 50:
        continue
    m = dates < SPLIT
    s_tr, s_oo = stat(pnl[m]), stat(pnl[~m])
    if not s_tr:
        continue
    rows.append(dict(cfg=c, train=s_tr, oos=s_oo,
                     is_base=all(BASE[k] == v for k, v in c.items())))
    if i % 24 == 0:
        print(f"  {i}/{len(combos)}  ({time.time()-t0:.0f}s)", flush=True)

rows.sort(key=lambda r: -r["train"]["net"])
print(f"\n{len(rows)} configs chale, {time.time()-t0:.0f}s\n")
print("=" * 112)
print("TOP 8 by TRAIN  (aur unka OOS — jo sweep me chhua hi nahi gaya)")
print("=" * 112)
print(f"{'#':>3}  {'config':<62}{'train net':>11}{'tr PF':>7}{'OOS net':>10}{'OOS PF':>8}")
base_row = next((r for r in rows if r["is_base"]), None)
for i, r in enumerate(rows[:8], 1):
    o = r["oos"]
    tag = "  <- ABHI KA" if r["is_base"] else ""
    print(f"{i:>3}  {str(r['cfg']):<62}{r['train']['net']:>11,.0f}{r['train']['pf']:>7.2f}"
          f"{(o['net'] if o else 0):>10,.0f}{(o['pf'] if o else 0):>8.2f}{tag}")
if base_row:
    rank = rows.index(base_row) + 1
    o = base_row["oos"]
    print(f"\nABHI KA CONFIG: rank {rank}/{len(rows)} by train   "
          f"train Rs{base_row['train']['net']:,.0f} (PF {base_row['train']['pf']:.2f})   "
          f"OOS Rs{o['net']:,.0f} (PF {o['pf']:.2f})")
    w = rows[0]
    print(f"TRAIN WINNER  : train Rs{w['train']['net']:,.0f} (PF {w['train']['pf']:.2f})   "
          f"OOS Rs{w['oos']['net']:,.0f} (PF {w['oos']['pf']:.2f})")
    print(f"\n  >> winner ka OOS baseline se {'BEHTAR' if w['oos']['net'] > o['net'] else 'KHARAB'} "
          f"(fark Rs{w['oos']['net'] - o['net']:,.0f})")
oos_all = np.array([r["oos"]["net"] for r in rows if r["oos"]])
tr_all = np.array([r["train"]["net"] for r in rows])
print(f"\n  train net range : Rs{tr_all.min():,.0f} .. Rs{tr_all.max():,.0f}")
print(f"  OOS   net range : Rs{oos_all.min():,.0f} .. Rs{oos_all.max():,.0f}")
print(f"  train-vs-OOS correlation: {np.corrcoef(tr_all, oos_all)[0,1]:.3f}"
      "   <- 0 ke paas = train ki ranking OOS me bekaar")
