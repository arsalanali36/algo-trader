"""Inject 5-min intraday candles (per trade-day) into a run's results.js so the
trade-chart modal can show WHERE entry/exit happened intraday, not a useless daily bar.

meta.intraday = { "YYYY-MM-DD": [["HH:MM", o,h,l,c], ...09:15–15:30 5-min bars ] }
only for days that actually have a trade (keeps size down).

Usage:  python add_intraday.py [run_slug]   (default: mid_orb_nifty)
Reusable: run_hunt.py calls build_intraday() directly for fresh hunts.
"""
import os
import sys
import json
import datetime as dt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def build_intraday(csv, dates, tf="5min"):
    df = pd.read_csv(os.path.join(HERE, csv), parse_dates=["Datetime"])
    s = df.set_index("Datetime")
    d = (s.resample(tf, label="left", closed="left")
           .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"})
           .dropna().reset_index())
    tt = d.Datetime.dt.time
    d = d[(tt >= dt.time(9, 15)) & (tt <= dt.time(15, 30))]
    d["date"] = d.Datetime.dt.strftime("%Y-%m-%d")
    d["hm"] = d.Datetime.dt.strftime("%H:%M")
    want = set(dates)
    out = {}
    for date, grp in d.groupby("date"):
        if date not in want:
            continue
        out[date] = [[r.hm, round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
                     for r in grp.itertuples()]
    return out


def patch(run_slug, csv="nifty_1min.csv"):
    p = os.path.join(HERE, "runs", run_slug, "results.js")
    txt = open(p, encoding="utf-8").read()
    obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    dates = set()
    for c in obj["combos"].values():
        for t in c.get("all_trades", []):
            dates.add(t["entry_dt"][:10])
    obj["meta"]["intraday"] = build_intraday(csv, dates)
    open(p, "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(obj, default=float) + ";")
    print(f"patched runs/{run_slug}/results.js — {len(obj['meta']['intraday'])} trade-days, "
          f"{os.path.getsize(p)//1024} KB")


if __name__ == "__main__":
    patch(sys.argv[1] if len(sys.argv) > 1 else "mid_orb_nifty")
