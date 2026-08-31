"""3 survivors ka POORA report-card — real premium, train/OOS split, significance.

Project ka deploy gate: paisa positive + p < 0.05 + min(train, OOS) dekho.
Sharpe >= 1 sirf proxy hai (TRAP #199) — low win-rate + high RR wali buying strategies
ko wo galat tareeke se phenk deta hai.

NOTE: in strategies ke exits SPOT-based hain (atr_sl/rr), premium-based nahi — isliye
trade-set BS aur REAL dono me same hai; sirf pricing badalti hai. Yahi wajah hai ki
reprice hi sahi evaluation de deta hai (bs_vs_reallake docstring bhi yehi kehta hai).
"""
import numpy as np
import bs_vs_reallake as B

RUNS = ["orb_supertrend", "chain_zone_longatm", "mid_orb_nifty",
        "debit_vertical_orb", "ratio_backspread", "long_straddle_orb", "long_strangle_orb"]
SPLIT = "2025-01-01"
LAKE_START = "2021-07-01"


def perm_p(r, iters=20000, seed=7):
    if len(r) < 20:
        return 1.0
    rng = np.random.default_rng(seed)
    null = (rng.choice([-1.0, 1.0], size=(iters, len(r))) * np.abs(r)).mean(axis=1)
    return float((null >= r.mean()).mean())


def blk(r, days):
    if len(r) < 20:
        return None
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float("inf")
    yrs = max(days / 365.25, 0.25)
    sh = r.mean() / r.std() * np.sqrt(len(r) / yrs) if r.std() else 0.0
    eq = r.cumsum(); dd = (eq - np.maximum.accumulate(eq)).min()
    return dict(n=len(r), net=r.sum(), sh=sh, pf=pf, wr=100 * (r > 0).mean(),
                exp=r.mean(), dd=dd, p=perm_p(r))


def line(tag, b):
    if not b:
        print(f"    {tag:<10} (too few)"); return
    print(f"    {tag:<10}{b['n']:>6}{b['net']:>12,.0f}{b['sh']:>7.2f}{b['pf']:>7.2f}"
          f"{b['wr']:>7.1f}{b['exp']:>9,.0f}{b['dd']:>11,.0f}{b['p']:>9.4f}")


print("=" * 108)
print("REAL-PREMIUM REPORT CARD — train/OOS split @ " + SPLIT + "  (lake " + LAKE_START + " se)")
print("=" * 108)
for slug in RUNS:
    d = B.reprice(slug)
    r = np.array(d["trades"], float); dt = np.array(d["dates"])
    if len(r) < 40:
        print(f"\n{slug}: n={len(r)} too few"); continue
    tr, oo = dt < SPLIT, dt >= SPLIT
    print(f"\n{slug}   (BS Sharpe was {d['bs_sh']:.2f})")
    print(f"    {'period':<10}{'n':>6}{'net Rs':>12}{'Sh':>7}{'PF':>7}{'win%':>7}"
          f"{'exp/tr':>9}{'maxDD':>11}{'p':>9}")
    full, btr, boo = blk(r, 1885), blk(r[tr], 1280), blk(r[oo], 605)
    line("FULL", full); line("train", btr); line("OOS", boo)
    if full and btr and boo:
        ok_money = full["net"] > 0 and btr["net"] > 0 and boo["net"] > 0
        ok_p = full["p"] < 0.05
        ok_oos = boo["sh"] > 0 and boo["pf"] > 1
        v = ("PASS — paisa dono period me +, p<0.05, OOS zinda" if (ok_money and ok_p and ok_oos)
             else "REJECT — " + ", ".join(x for x, c in
                  (("paisa", not ok_money), ("p>=0.05", not ok_p), ("OOS", not ok_oos)) if c))
        print(f"    -> {v}")
print("\n  gate = paisa positive (full+train+OOS) + p<0.05 + OOS me PF>1")
print("  Sharpe report hota hai par gate NAHI hai (TRAP #199)")
