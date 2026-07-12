"""Claim-2 research screen — user's NEXT-DAY continuation hypothesis (2026-07-12).

Story: day D pierces an extreme pivot (S3-S5 / R3-R5). Intraday it RECOVERS
(trapped traders exit, big player buys their liquidity = trap bounce). But the
NEXT day the trend CONTINUES in the fall/breach direction (momentum resumes).

Screen (no optimisation, no charges yet — does the raw effect even exist?):
  condition day D (decided from bars <= 15:00, no lookahead):
    breach  : low pierced S_n (n in band)         -> candidate SHORT
              high pierced R_n                    -> candidate LONG
    bounce  : breach AND price at 15:00 is back on the safe side of the
              breached level (the user's trap-recovery confirmation)
  entry: day-D 15:00 close (captures overnight gap)  OR  next-day open
  exit : next-day 15:00 close
Prints N / win% / avg pts / median / total for every variant x band x direction.
"""
import datetime as dt
import numpy as np
import pandas as pd
import intraday_engine as ie

CUT = dt.time(15, 0)   # decision + entry time (bar CLOSE at/before this)


def build_days(tf="5m"):
    d = ie.resample(ie.load_1m(), tf)
    lvls = ie._daily_levels(d, 20, 10.0)
    days = sorted(d["day"].unique())
    # per-day: close at 15:00 cut, full-session close, next-day open
    rows = {}
    for day, g in d.groupby("day"):
        pre = g[g.Datetime.dt.time <= CUT]
        if pre.empty:
            continue
        rows[day] = dict(
            cut_close=pre.Close.iloc[-1],
            pre_low=pre.Low.min(), pre_high=pre.High.max(),
            open_=g.Open.iloc[0], close=g.Close.iloc[-1])
    return days, rows, lvls


def screen():
    days, rows, lvls = build_days()
    print("CLAIM-2 SCREEN - extreme-pivot day -> NEXT-DAY continuation (spot pts, 1 unit)")
    print("cond: breach=pierced extreme | bounce=pierced AND recovered by 15:00 (the trap)")
    print()
    hdr = f"{'cond':7s}{'band':>10s}{'entry':>9s}{'dir':>7s}{'N':>5s}{'win%':>6s}{'avg':>8s}{'med':>8s}{'tot':>9s}{'t':>6s}"
    for band in [(3, 4, 5), (4, 5), (2, 3, 4, 5)]:
        print(f"--- band S/R{list(band)} ---")
        print(hdr)
        for cond in ("breach", "bounce"):
            for entry in ("eod", "nxtopen"):
                res = {"SHORT": [], "LONG": []}
                for i in range(len(days) - 1):
                    day, nxt = days[i], days[i + 1]
                    if day not in rows or nxt not in rows:
                        continue
                    lv = lvls.get(day)
                    if lv is None:
                        continue
                    r = rows[day]
                    # direction from breach of extreme levels
                    for side, arr, pierced, level in (
                            ("SHORT", lv["sup"], r["pre_low"], min),
                            ("LONG", lv["res"], r["pre_high"], max)):
                        hit_lv = None
                        for idx in band:
                            if idx < 6:  # only pivot S1..S5/R1..R5 slots (0=pdl/pdh)
                                L = arr[idx]
                                if (side == "SHORT" and r["pre_low"] < L) or \
                                   (side == "LONG" and r["pre_high"] > L):
                                    hit_lv = L if hit_lv is None else \
                                        (min(hit_lv, L) if side == "SHORT" else max(hit_lv, L))
                        if hit_lv is None:
                            continue
                        if cond == "bounce":
                            # trap confirmation: price recovered back past the level by 15:00
                            if side == "SHORT" and not (r["cut_close"] > hit_lv):
                                continue
                            if side == "LONG" and not (r["cut_close"] < hit_lv):
                                continue
                        ep = r["cut_close"] if entry == "eod" else rows[nxt]["open_"]
                        xp = rows[nxt]["cut_close"]
                        pts = (xp - ep) if side == "LONG" else (ep - xp)
                        res[side].append(pts)
                for side in ("SHORT", "LONG"):
                    a = np.array(res[side])
                    if len(a) == 0:
                        continue
                    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 2 and a.std() > 0 else 0
                    win = (a > 0).mean() * 100
                    print(f"{cond:7s}{str(list(band)):>10s}{entry:>9s}{side:>7s}{len(a):>5d}"
                          f"{win:>6.0f}{a.mean():>8.1f}{np.median(a):>8.1f}{a.sum():>9.0f}{t:>6.2f}")
        print()


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    screen()
