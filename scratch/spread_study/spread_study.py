"""Real bid/ask spread study from the live option-chain collector.

Question: how big is a 4-leg structure's own bid/ask cost, compared to the exit
threshold that structure is trying to detect?

  02.10.01 BNF : SELL ATM+-600, BUY ATM+-1100 (monthly)  -> exit = FIXED 26.7 pt
  02.15  NIFTY : SELL ATM+-250, BUY ATM+-500  (weekly)   -> exit = 50% of credit

Nothing is priced or modelled here - these are the collector's own recorded
top-of-book bid/ask at the strategy's own entry minute.
"""
import os, sys, glob, datetime as dt
import pandas as pd

BASE = "_TRADING_DATA/OptionChain"
COLS = ["datetime", "spot", "expiry", "strike", "opt_type", "ltp", "bid", "ask"]
ENTRY_FROM, ENTRY_TO = "09:20", "09:26"


def load_entry_snap(path):
    try:
        d = pd.read_csv(path, usecols=COLS)
    except Exception:
        return None
    t = d["datetime"].astype(str).str[-8:-3]          # HH:MM
    d = d[(t >= ENTRY_FROM) & (t <= ENTRY_TO)]
    if d.empty:
        return None
    first = d["datetime"].min()
    d = d[d["datetime"] == first]
    d = d[(d.bid > 0) & (d.ask > 0) & (d.ask >= d.bid)]
    return d if len(d) else None


def pick(d, expiry, strike, typ):
    r = d[(d.expiry == expiry) & (d.strike == strike) & (d.opt_type == typ)]
    if r.empty:
        return None
    r = r.iloc[0]
    return dict(bid=float(r.bid), ask=float(r.ask), ltp=float(r.ltp),
                mid=(float(r.bid) + float(r.ask)) / 2.0,
                spread=float(r.ask) - float(r.bid))


def study(sym, step, dist, wing, expiry_mode, label, fixed_stop_pt=None):
    rows = []
    for path in sorted(glob.glob(f"{BASE}/{sym}/{sym}_*.csv")):
        day = os.path.basename(path).split("_")[1][:10]
        d = load_entry_snap(path)
        if d is None:
            continue
        spot = float(d.spot.iloc[0])
        atm = round(spot / step) * step
        exps = sorted(d.expiry.astype(str).unique())
        d0 = dt.date.fromisoformat(day)
        cand = [(e, (dt.date.fromisoformat(e) - d0).days) for e in exps]
        cand = [(e, n) for e, n in cand if n >= (3 if expiry_mode == "monthly" else 0)]
        if not cand:
            continue
        expiry, dte = cand[0]
        legs = {
            "sCE": pick(d, expiry, atm + dist, "CE"),
            "sPE": pick(d, expiry, atm - dist, "PE"),
            "wCE": pick(d, expiry, atm + dist + wing, "CE"),
            "wPE": pick(d, expiry, atm - dist - wing, "PE"),
        }
        if any(v is None for v in legs.values()):
            continue
        credit = (legs["sCE"]["mid"] + legs["sPE"]["mid"]
                  - legs["wCE"]["mid"] - legs["wPE"]["mid"])
        spread_rt = sum(v["spread"] for v in legs.values())      # full round trip
        prem_tot = sum(v["mid"] for v in legs.values())
        stop_pt = fixed_stop_pt if fixed_stop_pt else credit * 0.50
        rows.append(dict(day=day, dte=dte, spot=round(spot), atm=atm,
                         credit=round(credit, 1), prem=round(prem_tot, 1),
                         spread_rt=round(spread_rt, 2),
                         half=round(spread_rt / 2, 2),
                         stop_pt=round(stop_pt, 1),
                         cost_pct=round(spread_rt / stop_pt * 100, 1) if stop_pt > 0 else None))
    df = pd.DataFrame(rows)
    print(f"\n{'='*104}\n{label}\n{'='*104}")
    if df.empty:
        print("  (no usable snapshots)"); return df
    print(f"{'date':<12}{'dte':>4}{'credit':>9}{'4-leg prem':>12}"
          f"{'spread(rt)':>12}{'entry half':>12}{'exit thr':>10}{'spread/thr':>12}")
    for _, r in df.iterrows():
        print(f"{r.day:<12}{r.dte:>4}{r.credit:>9.1f}{r.prem:>12.1f}"
              f"{r.spread_rt:>12.2f}{r.half:>12.2f}{r.stop_pt:>10.1f}{r.cost_pct:>11.1f}%")
    print(f"\n  median spread/threshold = {df.cost_pct.median():.1f}%"
          f"   |   worst {df.cost_pct.max():.1f}%   best {df.cost_pct.min():.1f}%"
          f"   |   n={len(df)}")
    return df


bnf = study("BANKNIFTY", 100, 600, 500, "monthly",
            "02.10.01  BANKNIFTY  SELL ATM+-600 / BUY ATM+-1100  (monthly)   "
            "exit = FIXED 26.7 pt", fixed_stop_pt=26.7)
nif = study("NIFTY", 50, 250, 250, "weekly",
            "02.15     NIFTY      SELL ATM+-250 / BUY ATM+-500   (weekly)    "
            "exit = 50% of credit")

print("\n" + "=" * 104)
print("VERDICT — spread as % of the exit threshold it has to be detected against")
print("=" * 104)
for name, df in (("02.10.01 BNF  (fixed 26.7 pt)", bnf), ("02.15    NIFTY (50% of credit)", nif)):
    if len(df):
        print(f"  {name:<34} median {df.cost_pct.median():>6.1f}%   "
              f"range {df.cost_pct.min():>5.1f}% .. {df.cost_pct.max():>6.1f}%   n={len(df)}")
