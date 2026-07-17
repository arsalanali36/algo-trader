"""PHASE-0b PROBE — Range-Extreme Short Strangle, POSITIONAL (NIFTY, REAL WEEK lake).

Intraday probe showed the freshness filter is the edge, but intraday theta is small and
OOS was weak. Repo's own proof (VRP overnight condor) = premium-selling's money is in the
OVERNIGHT / multi-day decay. So test the same range-extreme short strangle held:
  (A) 1 night   — enter ~15:10 day d, exit ~15:10 day d+1
  (B) to expiry — enter ~15:10, hold until that weekly contract's expiry-day close
  (C) to expiry + breach stop — exit early if spot crosses either sold strike
and naked vs hedged (condor wings) since the multi-day tail matters more.

Honest (same rules as the intraday probe): REAL lake premium, real charges + DOM slip,
strikes must sit within the +/-10 (+/-500pt) real-data window or the entry is SKIPPED,
NO hold across the weekly-expiry contract roll (TRAP #114), intraday breach uses real spot.

Run:  python -X utf8 probe_range_strangle_positional.py
"""
import os
import datetime as dt
import numpy as np
import pandas as pd

import bs_option as bs
import real_struct2 as r2
import expiry_calendar as xcal

HERE = os.path.dirname(os.path.abspath(__file__))
STEP = 50
CLOSE_HM = dt.time(15, 15)
EVO_END = dt.date(2025, 7, 1)


def lake_days_closebars(g):
    """day -> index of its last bar at/before 15:15 (the 'close' bar we enter/exit on)."""
    cb = {}
    for i in range(len(g["DAY"])):
        if g["TT"][i] <= CLOSE_HM:
            cb[g["DAY"][i]] = i
    return cb, sorted(cb)


def daily_hl():
    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"])
    df["d"] = df.Datetime.dt.date
    return df.groupby("d").agg(H=("High", "max"), L=("Low", "min"))


def trailing_extremes(dhl, lookback):
    days = list(dhl.index); H = dhl.H.values; L = dhl.L.values
    out = {}
    for i in range(lookback, len(days)):
        w_hi = H[i - lookback:i]; w_lo = L[i - lookback:i]
        j_hi = int(np.argmax(w_hi)); j_lo = int(np.argmin(w_lo))
        out[days[i]] = (float(w_hi[j_hi]), lookback - j_hi, float(w_lo[j_lo]), lookback - j_lo)
    return out


def _next_wexp_date(d, days):
    wd = xcal.weekly_expiry_weekday(d)
    ahead = (wd - d.weekday()) % 7
    return d + dt.timedelta(days=ahead)


def backtest(g, cb, days, ext, lot, lookback, min_dist_pct, min_age, mode,
             hedged=False, wing=5, breach_stop=False):
    """mode: 'night' (d->d+1) or 'expiry' (d->weekly-expiry close). hedged: +/-wing condor wings."""
    SPOT, ATMK, DT, DAY, TT = g["SPOT"], g["ATMK"], g["DT"], g["DAY"], g["TT"]
    dayset = set(days)
    trades = []
    skips = {"no_ext": 0, "filter_age": 0, "filter_dist": 0, "unpriceable": 0, "roll": 0, "expday": 0}
    for k in range(len(days) - 1):
        d0 = days[k]
        if d0.weekday() == xcal.weekly_expiry_weekday(d0):
            skips["expday"] += 1; continue                    # don't open 0DTE
        if d0 not in ext:
            skips["no_ext"] += 1; continue
        hi, age_hi, lo, age_lo = ext[d0]
        e = cb[d0]; spot = SPOT[e]
        if age_hi < min_age or age_lo < min_age:
            skips["filter_age"] += 1; continue
        if (hi - spot) < min_dist_pct / 100 * spot or (spot - lo) < min_dist_pct / 100 * spot \
           or spot >= hi or spot <= lo:
            skips["filter_dist"] += 1; continue
        Khi = round(hi / STEP) * STEP; Klo = round(lo / STEP) * STEP
        if not (-10 <= round((Khi - ATMK[e]) / STEP) <= 10 and -10 <= round((Klo - ATMK[e]) / STEP) <= 10):
            skips["unpriceable"] += 1; continue
        exp = _next_wexp_date(d0, days)
        if mode == "night":
            d1 = days[k + 1]
            if d1 > exp:
                skips["roll"] += 1; continue                  # crossing the contract roll
        else:  # expiry: last day <= exp (and <= last available)
            hold_days = [d for d in days[k + 1:] if d <= exp]
            if not hold_days:
                skips["roll"] += 1; continue
            d1 = hold_days[-1]
        xi = cb[d1]
        # legs: sell CE@Khi, PE@Klo (+ buy wings if hedged)
        legs = [("CE", Khi, -1), ("PE", Klo, -1)]
        if hedged:
            legs += [("CE", Khi + wing * STEP, +1), ("PE", Klo - wing * STEP, +1)]
        eps = {(s, K): r2._px(g, e, s, K) for (s, K, sg) in legs}
        if not all(v > 0 for v in eps.values()):
            skips["unpriceable"] += 1; continue
        # breach stop: walk bars e..xi, exit first bar spot crosses a SOLD strike
        exit_i = xi; reason = ("EXP" if mode == "expiry" else "NIGHT")
        if breach_stop:
            for i in range(e + 1, xi + 1):
                if SPOT[i] >= Khi or SPOT[i] <= Klo:
                    exit_i = i; reason = "BREACH"; break
        xps = {(s, K): r2._px(g, exit_i, s, K) for (s, K, sg) in legs}
        ev = sum(sg * eps[(s, K)] for (s, K, sg) in legs)     # net credit (negative)
        cv = sum(sg * xps[(s, K)] for (s, K, sg) in legs)
        qty = lot
        gross = (cv - ev) * qty
        fee = sum(bs.calc_charges(eps[(s, K)], xps[(s, K)], qty,
                                  entry_side=("BUY" if sg > 0 else "SELL"), when=DT[e])
                  for (s, K, sg) in legs)
        slip = sum(bs.slip_cost_leg(eps[(s, K)], xps[(s, K)], qty) for (s, K, sg) in legs)
        pnl = gross - fee - slip
        if hedged:                                            # defined-risk clamp (build_vrp pattern)
            credit = abs(ev) * qty
            max_loss = wing * STEP * qty - credit
            pnl = float(np.clip(pnl, -max_loss - fee - slip, credit))
        trades.append(dict(day=d0, exit_day=d1, pnl=pnl, credit=abs(ev) * qty,
                           reason=reason, hold=(xi - e)))
    return trades, skips


def stats(trades):
    if not trades:
        return None
    p = np.array([t["pnl"] for t in trades])
    wins = (p > 0).sum()
    gp = p[p > 0].sum(); gl = -p[p < 0].sum()
    pf = gp / gl if gl > 0 else float("inf")
    rng = np.random.default_rng(7)
    boots = np.array([rng.choice(p, len(p), replace=True).mean() for _ in range(2000)])
    return dict(n=len(p), net=float(p.sum()), avg=float(p.mean()), win=100 * wins / len(p),
                pf=float(pf), p=float((boots <= 0).mean()),
                sharpe=float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) > 0 else 0.0,
                worst=float(p.min()))


def show(label, trades):
    s = stats(trades)
    if s is None:
        print(f"  {label:40s} — 0 trades"); return
    print(f"  {label:40s} n={s['n']:>4} net={s['net']:>11,.0f} avg={s['avg']:>7,.0f} "
          f"win={s['win']:>4.0f}% PF={s['pf']:>4.2f} p={s['p']:>5.3f} Sh={s['sharpe']:>5.2f} "
          f"worst={s['worst']:>9,.0f}")


def main():
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot() or 65
    print(f"PHASE-0b PROBE — Range-Extreme Short Strangle POSITIONAL (NIFTY WEEK, lot={lot})\n", flush=True)
    g = r2.grid("WEEK", "5m")
    cb, days = lake_days_closebars(g)
    print(f"  lake: {len(days)} days, {days[0]} -> {days[-1]}", flush=True)
    dhl = daily_hl()

    print("\n=== A. HORIZON — naked, filter ON (lb=10 dist=0.75 age>=2) ===")
    for lb in (5, 10):
        ext = trailing_extremes(dhl, lb)
        trn, skn = backtest(g, cb, days, ext, lot, lb, 0.75, 2, "night")
        tre, ske = backtest(g, cb, days, ext, lot, lb, 0.75, 2, "expiry")
        trb, skb = backtest(g, cb, days, ext, lot, lb, 0.75, 2, "expiry", breach_stop=True)
        print(f"\n  -- lookback {lb} --")
        show("1-night", trn)
        show("hold-to-expiry", tre)
        show("hold-to-expiry + breach-stop", trb)
        print(f"     coverage(expiry): {len(tre)} trades | skips {dict(ske)}")

    print("\n=== B. NAKED vs HEDGED (condor wings +/-5 strikes) — best horizon ===")
    ext = trailing_extremes(dhl, 10)
    for mode in ("night", "expiry"):
        trn, _ = backtest(g, cb, days, ext, lot, 10, 0.75, 2, mode, hedged=False)
        trh, _ = backtest(g, cb, days, ext, lot, 10, 0.75, 2, mode, hedged=True, wing=5)
        trhb, _ = backtest(g, cb, days, ext, lot, 10, 0.75, 2, mode, hedged=True, wing=5, breach_stop=True)
        print(f"\n  -- {mode} --")
        show("naked", trn)
        show("hedged condor (w5)", trh)
        show("hedged condor (w5) + breach-stop", trhb)

    print("\n=== C. TRAIN vs OOS — hold-to-expiry + breach-stop, lb=10 dist=0.75 (naked & hedged) ===")
    for hed in (False, True):
        tr, _ = backtest(g, cb, days, ext, lot, 10, 0.75, 2, "expiry", hedged=hed, wing=5, breach_stop=True)
        tag = "hedged" if hed else "naked"
        show(f"{tag} train (<2025-07)", [t for t in tr if t["day"] < EVO_END])
        show(f"{tag} oos   (>=2025-07)", [t for t in tr if t["day"] >= EVO_END])
        show(f"{tag} full", tr)
        print()

    print("  NOTE: base-edge probe only. Positive + OOS-holds + significant => full 3-pass build.", flush=True)


if __name__ == "__main__":
    main()
