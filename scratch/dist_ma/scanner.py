#!/usr/bin/env python3
r"""scanner.py — which stocks are AT the extreme-oversold zone right now.

Scans the whole daily lake as of its latest bar and lists:
  ZONE     : dist <= threshold today (in the stretched zone, waiting for reversal)
  TRIGGER  : a bullish reversal candle fired in the last N days while in-zone
             -> actionable buy-stop above that candle's high (SL below its low)

Data is only as fresh as the lake's last date (printed at top). Not live ticks.
"""
import argparse
import numpy as np
import pandas as pd
import dist_ma as m


def scan(thresh=-10.0, look=3, trig_days=5):
    zone, trig = [], []
    last_date = None
    for s in m.symbols():
        try:
            d = m.prep(m.load(s))
        except Exception:
            continue
        if len(d) < 60:
            continue
        last_date = max(last_date, d.Date.iloc[-1]) if last_date is not None else d.Date.iloc[-1]
        row = d.iloc[-1]
        sig = m.signal_bars(d, thresh, look).values
        # currently in zone?
        if row["dist"] <= thresh:
            zone.append((s, row.Date, row.Close, row.ema, row["dist"]))
        # fresh trigger in last trig_days?
        recent = np.where(sig[-trig_days:])[0]
        if len(recent):
            ti = len(d) - trig_days + recent[-1]     # last trigger bar index
            tb = d.iloc[ti]
            days_ago = len(d) - 1 - ti
            trig.append((s, tb.Date, tb["dist"], tb.High, tb.Low, row.Close, days_ago))
    return zone, trig, last_date


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--thresh", type=float, default=-10.0)
    ap.add_argument("--look", type=int, default=3)
    ap.add_argument("--trigdays", type=int, default=5)
    a = ap.parse_args()

    zone, trig, last_date = scan(a.thresh, a.look, a.trigdays)
    print(f"\nData as of: {pd.Timestamp(last_date).date()}   "
          f"(threshold {a.thresh}%, trigger window {a.trigdays}d)\n")

    print(f"### ACTIONABLE TRIGGERS (reversal candle fired, last {a.trigdays}d) ###")
    if trig:
        print(f"{'symbol':<14}{'trig date':<12}{'dist%':>7}{'buy>':>10}{'SL<':>10}"
              f"{'last':>10}{'days ago':>9}")
        for s, dt_, dist, hi, lo, last, ago in sorted(trig, key=lambda x: x[2]):
            print(f"{s:<14}{str(pd.Timestamp(dt_).date()):<12}{dist:>6.1f}%"
                  f"{hi:>10.1f}{lo:>10.1f}{last:>10.1f}{ago:>9}")
    else:
        print("  (none right now)")

    print(f"\n### IN THE ZONE (dist <= {a.thresh}%, waiting for reversal) ###")
    if zone:
        print(f"{'symbol':<14}{'date':<12}{'close':>10}{'ema20':>10}{'dist%':>8}")
        for s, dt_, c, e, dist in sorted(zone, key=lambda x: x[4]):
            print(f"{s:<14}{str(pd.Timestamp(dt_).date()):<12}{c:>10.1f}{e:>10.1f}{dist:>7.1f}%")
    else:
        print("  (none right now)")
