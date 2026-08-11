"""iv_build.py — REAL implied vol from REAL premium via bs_option.implied_vol (Rule 6B).
Demo: entry (9:20) ATM IV per day -> yearly regime picture. No download, no Dhan, free/local."""
import os, sys, csv
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "nifty_trend"))
import bs_option as BS
import expiry_calendar as EC
from datetime import date, datetime, timedelta

LAKE = os.path.join(HERE, "..", "..", "_TRADING_DATA", "OptChainLake_1m", "NIFTY", "WEEK")
R = 0.065


def load_atm():
    frames = {}
    for ot in ("CE", "PE"):
        d = pd.read_csv(os.path.join(LAKE, f"{ot}_ATM.csv"),
                        usecols=["timestamp", "close", "strike", "spot"])
        ist = pd.to_datetime(d["timestamp"] + 19800, unit="s")
        d["date"] = ist.dt.strftime("%Y-%m-%d")
        d["hhmm"] = ist.dt.hour * 100 + ist.dt.minute
        frames[ot] = d
    return frames


def nearest_weekly(dstr):
    d = date.fromisoformat(dstr)
    wd = EC.weekly_expiry_weekday(d)
    cur = d
    for _ in range(10):
        if cur.weekday() == wd and cur >= d:
            return cur
        cur += timedelta(days=1)
    return d


def entry_iv():
    fr = load_atm()
    rows = []
    for ot in ("CE", "PE"):
        g = fr[ot]
        e = g[(g["hhmm"] >= 920) & (g["hhmm"] <= 925)].groupby("date").first()
        fr[ot] = e
    dates = sorted(set(fr["CE"].index) & set(fr["PE"].index))
    for dt in dates:
        ce = fr["CE"].loc[dt]; pe = fr["PE"].loc[dt]
        spot = float(ce["spot"])
        exp = nearest_weekly(dt)
        # T in years: minutes from entry(09:20) to expiry 15:30
        entry_dt = datetime.fromisoformat(dt + "T09:20:00")
        exp_dt = datetime.combine(exp, datetime.min.time()).replace(hour=15, minute=30)
        T = max((exp_dt - entry_dt).total_seconds() / (365 * 24 * 3600), 1e-5)
        try:
            iv_ce = BS.implied_vol(float(ce["close"]), spot, float(ce["strike"]), T, R, "CE")
            iv_pe = BS.implied_vol(float(pe["close"]), spot, float(pe["strike"]), T, R, "PE")
        except Exception:
            continue
        if iv_ce and iv_pe and iv_ce < 2.9 and iv_pe < 2.9:
            rows.append((dt, spot, round(100 * (iv_ce + iv_pe) / 2, 2), (exp - date.fromisoformat(dt)).days))
    return rows


if __name__ == "__main__":
    rows = entry_iv()
    print(f"computed real ATM IV for {len(rows)} entry-days\n")
    df = pd.DataFrame(rows, columns=["date", "spot", "atm_iv", "dte"])
    df["year"] = df["date"].str[:4]
    print("year   days   mean_IV  median  p10   p90")
    for y, g in df.groupby("year"):
        iv = g["atm_iv"]
        print(f"{y}   {len(g):4d}    {iv.mean():5.1f}   {iv.median():5.1f}  {iv.quantile(.1):4.1f}  {iv.quantile(.9):4.1f}")
    df.to_csv(os.path.join(HERE, "entry_atm_iv.csv"), index=False)
    print("\nwrote entry_atm_iv.csv  (date, spot, atm_iv%, dte)")
