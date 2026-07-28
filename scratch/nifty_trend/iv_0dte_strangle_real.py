"""
REAL 0DTE short-strangle reprice — "sell the 9:20 ±1σ expected move, expiry day".
No BS: entry premium = REAL OptChainLake price at the strike; exit = intrinsic at
expiry EOD (deterministic → no held-strike problem). ±1σ expected move = the REAL
ATM straddle at 9:20 (market's own priced move), so strikes are self-consistent.
Reports full distribution + worst days + IV-rank filter + defined-risk (wing) cap.
"""
import os, json, math
import numpy as np
import pandas as pd
import real_struct2 as r2
import vrp_ungated_backtest as vu
import bs_option as bs

STEP = 50
LOT = bs.get_nifty_lot() or 65
HERE = os.path.dirname(__file__)

# IV-rank from the daily IV history (lake iv col is sparse) — 252d percentile
ivh = json.load(open(os.path.join(HERE, "..", "..", "data", "vrp_iv_history.json")))
ivs = pd.Series({pd.Timestamp(k): float(v) for k, v in ivh.items()}).sort_index()
ivs = ivs[(ivs > 3) & (ivs < 90)]
ivrank = (ivs.rolling(252, min_periods=20)
              .apply(lambda w: (w.iloc[-1] >= w).mean(), raw=False) * 100)
ivrank = {d.date(): float(r) for d, r in ivrank.items() if pd.notna(r)}

g = r2.grid("WEEK", "5m")
cyc, tdte, is_exp = vu._cycles(g)
DAY, TT, SPOT = g["DAY"], g["TT"], g["SPOT"]
n = len(DAY)

# ONE O(n) pass: per-day last bar + first bar at/after 09:20 (avoid O(n) rescans)
last_bar, entry920 = {}, {}
import datetime as _dt
_t920 = _dt.time(9, 20)
for i in range(n):
    d = DAY[i]
    last_bar[d] = i
    if d not in entry920 and TT[i] >= _t920:
        entry920[d] = i
expiry_days = [d for d in cyc if is_exp.get(d)]

def run(move_mode, wing_off, iv_min):
    """move_mode: 'straddle'(0.85×ATM straddle) or int(fixed short_off strikes).
       wing_off: None=naked, else buy wing this many strikes beyond short (defined risk)."""
    rows = []
    for day in expiry_days:
        e = entry920.get(day)
        if e is None:
            continue
        rk = ivrank.get(day)
        if iv_min is not None and (rk is None or rk < iv_min):
            continue
        atmk = round(g["ATMK"][e] / STEP) * STEP
        s0 = float(SPOT[e])
        if move_mode == "straddle":
            strad = r2._px(g, e, "CE", atmk) + r2._px(g, e, "PE", atmk)
            off = max(1, round(0.85 * strad / STEP))       # ≥1 strike so it's a strangle
        else:
            off = int(move_mode)
        off = min(off, 8)                                   # stay inside lake ±10
        kc, kp = atmk + off * STEP, atmk - off * STEP
        pce, ppe = r2._px(g, e, "CE", kc), r2._px(g, e, "PE", kp)
        if pce <= 0 or ppe <= 0:
            continue
        legs = [("CE", kc, -1), ("PE", kp, -1)]
        if wing_off is not None:
            wc, wp = kc + wing_off * STEP, kp - wing_off * STEP
            legs += [("CE", wc, +1), ("PE", wp, +1)]
        # entry value (credit = negative), real premium
        entry_val = sum(s * r2._px(g, e, side, K) for (side, K, s) in legs)
        # exit at expiry EOD = intrinsic
        j = last_bar[day]
        sT = float(SPOT[j])
        exit_val = sum(s * max(0.0, (sT - K) if side == "CE" else (K - sT)) for (side, K, s) in legs)
        gross = (exit_val - entry_val) * LOT
        fee = slip = 0.0
        for (side, K, s) in legs:
            ep = r2._px(g, e, side, K)
            xp = max(0.0, (sT - K) if side == "CE" else (K - sT))
            fee += bs.calc_charges(ep, xp, LOT, entry_side=("BUY" if s > 0 else "SELL"), when=g["DT"][e])
            slip += bs.slip_cost_leg(ep, xp, LOT)
        net = gross - fee - slip
        credit = abs(entry_val) * LOT
        breach = bool(sT > kc or sT < kp)
        rows.append(dict(day=day, rk=rk, credit=credit, off=off, net=net, breach=breach))
    return pd.DataFrame(rows)

def rep(df, label):
    if len(df) < 15:
        print(f"  {label:<34} n={len(df):<4} (too few)"); return
    net = df.net
    wr = (net > 0).mean() * 100
    sh = (net.mean() / net.std()) * math.sqrt(52) if net.std() > 0 else 0   # ~52 expiries/yr
    tail5 = net.nsmallest(max(1, len(net) // 20)).sum()
    print(f"  {label:<34} n={len(df):<4} win%={wr:4.0f}  net=₹{net.sum():>9,.0f}  avg=₹{net.mean():>6,.0f}  "
          f"Sharpe={sh:5.2f}  worst=₹{net.min():>8,.0f}  breach%={df.breach.mean()*100:3.0f}  worst5%=₹{tail5:>9,.0f}")

print(f"lot={LOT}  expiry days in WEEK lake: {len(expiry_days)}\n", flush=True)
print("══ NAKED short strangle, entry expiry-day 9:20, exit intrinsic @ expiry (REAL premium) ══")
print("  [±1σ = 0.85× real ATM straddle]")
rep(run("straddle", None, None), "±1σ  (all expiry days)")
for thr in (50, 70):
    rep(run("straddle", None, thr), f"±1σ  IV-rank ≥ {thr}")
print("  [fixed offset in strikes]")
for o in (1, 2, 3):
    rep(run(o, None, None), f"fixed {o}-strike strangle (all)")

print("\n══ DEFINED-RISK (buy wing +5 strikes beyond short) — tail capped ══")
rep(run("straddle", 5, None), "±1σ condor (all)")
rep(run("straddle", 5, 50), "±1σ condor  IV-rank ≥ 50")
for o in (2, 3):
    rep(run(o, 5, None), f"fixed {o}-strike condor (all)")
