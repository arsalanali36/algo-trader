"""arschain_MAIN — "entry Pine se li hoti aur 3:15 tak kuch na kiya hota" — ASLI premium pe.

counterfactual.py ka revival NAHI. Wo "algo vs manual panic" naapta tha (yahan manual
sirf 1/10 hai — baaki 9 RMS ne mare) aur uski maanyata Dhan=algo/Kite=manual ab ULTI hai.

Ye ek seedha sawaal poochhta hai: har LIVE entry ko 3:15 tak hold kiya hota to kya hota?
Koi model nahi — entry = order_store ka asli fill, exit = USI contract ke asli 1-min
premium bars (data/trade_ohlc/) ka 15:15 close, charges = shared charges module.
"""
import os
import sqlite3
import sys
import datetime as dt

import _paths  # noqa: F401
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "scratch", "nifty_trend"))
import json
import charges as CH


def bars(sec, d):
    p = "data/trade_ohlc/%s_%s.json" % (sec, d)
    if not os.path.exists(p):
        return None
    raw = json.load(open(p))
    out = {}
    for k, v in (raw.get("candles") or raw).items():
        c = v.get("close") if isinstance(v, dict) else (
            v[3] if isinstance(v, (list, tuple)) and len(v) > 3 else None)
        if c is None:
            continue
        if ":" in str(k):
            hm = str(k)[:5]
        else:
            # epoch keys IST ke hain. UTC maan liya to 15:15 kabhi milta hi nahi aur
            # code chupchaap 15:29 pe gir jaata hai — pehli baar yahi hua (TRAP #100 family)
            hm = (dt.datetime.utcfromtimestamp(int(k))
                  + dt.timedelta(hours=5, minutes=30)).strftime("%H:%M")
        out[hm] = float(c)
    return out or None


def at_315(b):
    """15:15 = strategy ka apna squareoff. Aas-paas chalega, par CHUPCHAAP aakhri bar
    pe girna mana — wo 15:29 hota hai aur number jhootha ho jaata hai."""
    for hm in ("15:15", "15:14", "15:16", "15:13", "15:17", "15:12", "15:18",
               "15:11", "15:19", "15:10"):
        if hm in b:
            return b[hm], hm
    return None, None


c = sqlite3.connect("data/trades.db")
cur = c.cursor()
cur.execute("select ts, date(ts), side, trad_sym, sec_id, qty, price from orders "
            "where strategy='arschain_MAIN' and mode='live' and status='filled' order by ts")
rows = cur.fetchall()

open_leg, trades = {}, []
for ts, d, side, sym, sec, qty, px in rows:
    if side == "SELL" and sec not in open_leg:      # ye strategy BECHTI hai = entry
        open_leg[sec] = dict(ts=ts, d=d, sym=sym, sec=sec, qty=qty, entry=float(px))
    elif side == "BUY" and sec in open_leg:         # agla BUY usi contract pe = asli exit
        t = open_leg.pop(sec)
        t["exit"] = float(px)
        trades.append(t)
for sec, t in open_leg.items():
    t["exit"] = None
    trades.append(t)

print()
print("%-11s %-22s %4s %8s %8s %10s | %8s %6s %10s | %9s" % (
    "date", "contract", "qty", "entry", "exit", "ACTUAL", "3:15 px", "at",
    "WHAT-IF", "farq"))
print("-" * 118)
ta = tw = 0.0
n_act = 0
for t in sorted(trades, key=lambda x: x["ts"]):
    b = bars(t["sec"], t["d"])
    if not b:
        print("%-11s %-22s  bars nahi" % (t["d"], t["sym"][:22]))
        continue
    p315, hm = at_315(b)
    if p315 is None:
        print("%-11s %-22s  15:15 ke aas-paas bar nahi (%s..%s)" % (
            t["d"], t["sym"][:22], min(b), max(b)))
        continue
    q = int(t["qty"])
    act = None
    if t["exit"] is not None:
        act = ((t["entry"] - t["exit"]) * q
               - CH.option_charges(t["entry"], t["exit"], q, "SELL", when=t["ts"]))
        ta += act
        n_act += 1
    wi = ((t["entry"] - p315) * q
          - CH.option_charges(t["entry"], p315, q, "SELL", when=t["ts"]))
    tw += wi
    print("%-11s %-22s %4d %8.2f %8s %10s | %8.2f %6s %10s | %9s" % (
        t["d"], t["sym"][:22], q, t["entry"],
        ("%.2f" % t["exit"]) if t["exit"] is not None else "OPEN",
        format(act, ",.0f") if act is not None else "—",
        p315, hm, format(wi, ",.0f"),
        format(wi - act, ",.0f") if act is not None else "—"))
print("-" * 118)
print("  ACTUAL (%d trades): %s   |   3:15 tak hold: %s   |   farq: %s" % (
    n_act, format(ta, ",.0f"), format(tw, ",.0f"), format(tw - ta, ",.0f")))
print()
