"""Ek ATM option leg ka asli round-trip spread — ORB/chain buys ka expectancy isse bachega?

Expectancy (real-lake): orb_v1 +Rs183 · chain_zone +Rs228 · orbst_v1 +Rs247 per trade @1 lot.
Sawaal: 1 lot ka round-trip bid/ask kharcha kitna hai? Wahi edge ka asli dushman hai.
Collector ke apne recorded top-of-book se (koi model nahi).
"""
import os, glob, datetime as dt
import pandas as pd

BASE = "_TRADING_DATA/OptionChain"
COLS = ["datetime", "spot", "expiry", "strike", "opt_type", "ltp", "bid", "ask"]
LOT = {"NIFTY": 65, "BANKNIFTY": 35}
STEP = {"NIFTY": 50, "BANKNIFTY": 100}


def rows_for(sym, hhmm_lo, hhmm_hi):
    out = []
    for p in sorted(glob.glob(f"{BASE}/{sym}/{sym}_*.csv")):
        day = os.path.basename(p).split("_")[1][:10]
        try:
            d = pd.read_csv(p, usecols=COLS)
        except Exception:
            continue
        t = d["datetime"].astype(str).str[-8:-3]
        d = d[(t >= hhmm_lo) & (t <= hhmm_hi)]
        d = d[(d.bid > 0) & (d.ask > 0) & (d.ask >= d.bid)]
        if d.empty:
            continue
        d0 = dt.date.fromisoformat(day)
        exps = sorted(d.expiry.astype(str).unique())
        cand = [e for e in exps if (dt.date.fromisoformat(e) - d0).days >= 0]
        if not cand:
            continue
        e = cand[0]
        d = d[d.expiry == e]
        spot = float(d.spot.iloc[0]); atm = round(spot / STEP[sym]) * STEP[sym]
        for ot in ("CE", "PE"):
            r = d[(d.strike == atm) & (d.opt_type == ot)]
            if r.empty:
                continue
            r = r.iloc[0]
            out.append(dict(day=day, ot=ot, dte=(dt.date.fromisoformat(e) - d0).days,
                            mid=(float(r.bid) + float(r.ask)) / 2,
                            spread=float(r.ask) - float(r.bid)))
    return pd.DataFrame(out)


for sym, exp_rs in (("NIFTY", 228), ("BANKNIFTY", None)):
    df = rows_for(sym, "09:20", "11:30")
    if df.empty:
        print(f"\n{sym}: data nahi"); continue
    lot = LOT[sym]
    df["rt_rs"] = df.spread * lot          # entry crosses + exit crosses = full spread once
    print(f"\n{'='*92}\n{sym} ATM option — 1 leg, 1 lot ({lot}) ka ROUND-TRIP spread kharcha\n{'='*92}")
    print(f"  samples {len(df)}  |  median premium {df.mid.median():.1f} pts")
    print(f"  spread  median {df.spread.median():.2f} pts  ->  Rs{df.rt_rs.median():,.0f} per round trip")
    print(f"          p75    {df.spread.quantile(.75):.2f} pts  ->  Rs{df.rt_rs.quantile(.75):,.0f}")
    print(f"          p90    {df.spread.quantile(.90):.2f} pts  ->  Rs{df.rt_rs.quantile(.90):,.0f}")
    if exp_rs:
        m = df.rt_rs.median()
        print(f"\n  expectancy Rs{exp_rs}/trade  vs  spread Rs{m:,.0f}"
              f"   ->  edge ka {100*m/exp_rs:.0f}% spread me jaata")
        print(f"  bacha hua expectancy: Rs{exp_rs - m:,.0f}/trade"
              + ("   <- ZINDA" if exp_rs - m > 0 else "   <- KHATAM"))
    by = df.groupby(pd.cut(df.dte, [-1, 0, 1, 3, 7, 40],
                           labels=["0 (expiry)", "1", "2-3", "4-7", "8+"]), observed=True)
    print(f"\n  DTE-wise median spread (pts -> Rs/lot):")
    for k, g in by:
        print(f"    {str(k):<12} {g.spread.median():>6.2f} pts  ->  Rs{g.rt_rs.median():>7,.0f}   (n={len(g)})")
