"""Ars chain (range_trader) — full-history backtest, spot + both option sides.

Runs the LIVE engine (range_trader.run_signal_engine via its trades_out collector —
never a copy, see LESSONS TRAP #131) over every year of NIFTY 1-min data we hold, then
prices the SAME trade list four ways so "CE buy ya PE sell?" is answered with numbers
rather than opinion:

    ① Instrument   raw spot points (no options, no charges) — is there an edge at all
    ③ BS BUY       reprice()        long -> buy ATM CE, short -> buy ATM PE
    ③ BS SELL      reprice_naked()  long -> sell ATM PE, short -> sell ATM CE
    ③ BS SPREAD    reprice_spread() the deployable SELL form (defined risk, wings)

All three option passes carry real date-aware Zerodha charges + DOM-measured slippage
(bs_option owns both — Rule 6B). Pass ② (+RMS caps) is deliberately not here: it needs
risk_gate's live config and belongs in run_hunt's pipeline, not this one-off.

Data: the daily bars only start 2025-01 in nifty_daily.csv, but key levels need ~21
days of history and sigma needs a close series. Both are DERIVED from the 1-min files
we already hold back to 2018 — built here, in memory, never written over the real file
(TRAP #126: nifty_1min.csv was once destroyed by a script that treated a derived file
as a source).

Warm-up matches LIVE exactly: the engine is handed the last WARM_DAYS days, not the
whole history, because that is what fetch_1m gives it (days_back=5). Feeding it more
would measure a strategy we don't run.

Usage:  python -X utf8 scratch/nifty_trend/arschain_backtest.py [--from 2018-01-01]
"""

import argparse
import glob
import os
import sys
from collections import defaultdict

import pandas as pd

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "_TOOLS"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _paths  # noqa: F401,E402

import range_trader as rt          # noqa: E402  THE engine — not a copy
import validate_strategy as vs     # noqa: E402  data loading (Rule 6B)
import bs_option as bs             # noqa: E402  pricing/charges/slip (Rule 6B)

WARM_DAYS = 6          # live hands the engine days_back=5 + today
LOOKBACK_DAILY = 30    # chain walks 20 days back; keep a margin


def engine_cfg(**over):
    """validate_strategy's CFG in the shape run_signal_engine actually reads.

    The zone-exit knob has TWO names: this repo's backtest config calls it `exit_main`,
    the engine + the live config + the RMS UI call it `exit_zone`. validate_strategy
    translates (line ~171); anything else driving the engine MUST too, or the engine's
    `cfg.get("exit_zone", False)` silently defaults it OFF and you measure a strategy
    nobody runs. That bug cost this file one full 9-year run.
    """
    c = dict(vs.CFG)
    if "exit_main" in c and "exit_zone" not in c:
        c["exit_zone"] = c["exit_main"]
    c.update(over)
    return c


def load_5m(date_from):
    frames = []
    for p in sorted(glob.glob(os.path.join(vs.DATA_DIR, "NIFTY_*.csv"))):
        b = os.path.basename(p).lower()
        if "daily" in b or b == "nifty.csv":
            continue
        d = b.replace("nifty_", "").replace(".csv", "")
        if len(d) != 10 or (date_from and d < date_from):
            continue
        try:
            df1 = vs.load_1m(p)
        except Exception:
            continue
        if df1.empty:
            continue          # NSE holiday — the file exists and is empty
        d5 = vs.resample_5m(df1)
        d5["date"] = d5["time"].dt.date
        frames.append(d5)
    if not frames:
        raise SystemExit("koi 5m data nahi mila")
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def daily_from_5m(cont5):
    """Daily OHLC derived from the intraday bars themselves — the same aggregation
    validate_strategy.daily_bars() falls back to. Never touches nifty_daily.csv."""
    g = cont5.groupby("date")
    return pd.DataFrame({
        "date": list(g.groups.keys()),
        "open": g["open"].first().values,
        "high": g["high"].max().values,
        "low": g["low"].min().values,
        "close": g["close"].last().values,
    }).sort_values("date").reset_index(drop=True)


def run_engine(cont5, daily, cfg):
    """Live engine, day by day, warm-up window exactly as live gets it."""
    days = sorted(cont5["date"].unique())
    by_day = {d: g for d, g in cont5.groupby("date")}
    dmap = {r["date"]: i for i, r in daily.iterrows()}
    trades = []
    for n, d in enumerate(days):
        di = dmap.get(d)
        if di is None or di < 2:
            continue
        sub = daily.iloc[max(0, di - LOOKBACK_DAILY):di + 1].reset_index(drop=True)
        if len(sub) < 2:
            continue
        try:
            levels = rt.build_key_levels(sub, is_index=True)
        except Exception:
            continue
        if not levels:
            continue
        window = days[max(0, n - WARM_DAYS + 1):n + 1]
        upto = pd.concat([by_day[x] for x in window], ignore_index=True)
        if len(upto) < 21:
            continue
        raw = []
        try:
            rt.run_signal_engine(upto, levels, cfg, trades_out=raw)
        except Exception:
            continue
        raw = [r for r in raw if r["time"].date() == d]
        dbars = upto[upto["time"].dt.date == d]

        pos = None
        for r in raw:
            if r["kind"].startswith("ENTRY"):
                side = "long" if r["kind"] == "ENTRY_LONG" else "short"
                if pos is not None:          # reversal closes the old side (TRAP: never drop these)
                    _close(trades, pos, r["time"], r["price"], "REVERSAL", dbars)
                pos = dict(side=side, entry=float(r["price"]), entry_dt=r["time"])
            elif pos is not None:
                _close(trades, pos, r["time"], r["price"], r["reason"], dbars)
                pos = None
        if pos is not None:                  # engine squares off at 15:15 itself; safety net
            last = dbars.iloc[-1]
            _close(trades, pos, last["time"], float(last["close"]), "EOD", dbars)
        if n % 200 == 0:
            print("     ...%s  (%d trades)" % (d, len(trades)), flush=True)
    return trades


def _close(trades, pos, t, price, reason, dbars=None):
    pts = (float(price) - pos["entry"]) * (1 if pos["side"] == "long" else -1)
    tr = dict(side=pos["side"], entry=pos["entry"], exit=float(price),
              entry_dt=pos["entry_dt"], exit_dt=t, points=pts,
              bars=0, entry_i=0, exit_i=0, reason=reason)
    if dbars is not None:
        # every bar the trade was actually alive for — what an SL/target would have seen.
        # Kept as plain tuples: a 9-year run holds ~1,900 of these.
        p = dbars[(dbars["time"] >= pos["entry_dt"]) & (dbars["time"] <= t)]
        tr["path"] = [(r.time, float(r.high), float(r.low)) for r in p.itertuples()]
        tr["bars"] = len(tr["path"])
    trades.append(tr)


def stats(pnls):
    if not pnls:
        return dict(n=0, net=0, win=0, pf=0, sharpe=0, maxdd=0, avg=0, best=0, worst=0)
    wins = [p for p in pnls if p > 0]
    loss = [p for p in pnls if p <= 0]
    eq = peak = 0.0
    dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    mean = sum(pnls) / len(pnls)
    var = sum((p - mean) ** 2 for p in pnls) / len(pnls)
    sd = var ** 0.5
    return dict(n=len(pnls), net=sum(pnls), win=100.0 * len(wins) / len(pnls),
                pf=(sum(wins) / abs(sum(loss))) if loss and sum(loss) else float("inf"),
                sharpe=(mean / sd * (len(pnls) ** 0.5)) if sd else 0.0,
                maxdd=dd, avg=mean, best=max(pnls), worst=min(pnls))


def show(title, s, extra=""):
    print("  %-22s %6d %13s %7.1f%% %7.2f %7.2f %13s %11s   %s" % (
        title, s["n"], f"{s['net']:,.0f}", s["win"], s["pf"], s["sharpe"],
        f"{s['maxdd']:,.0f}", f"{s['avg']:,.0f}", extra))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default=None)
    a = ap.parse_args()

    print("\n  5m bars bana raha hoon...", flush=True)
    cont5 = load_5m(a.dfrom)
    daily = daily_from_5m(cont5)
    print("     %s -> %s   %d trading din, %d bars"
          % (daily["date"].iloc[0], daily["date"].iloc[-1], len(daily), len(cont5)))

    cfg = engine_cfg()
    print("\n  config (user ki chalti hui TV script se):")
    print("     %s" % {k: cfg[k] for k in sorted(cfg)})

    print("\n  engine chala raha hoon (live wala, copy nahi)...", flush=True)
    trades = run_engine(cont5, daily, cfg)
    if not trades:
        raise SystemExit("koi trade nahi")

    lot = bs.get_nifty_lot()
    sig = bs.realised_vol_map(daily.set_index("date")["close"])
    print("\n  %d trades | lot=%d | sigma = realised vol (daily close se)" % (len(trades), lot))

    spot = [t["points"] * lot for t in trades]
    buy = bs.reprice(trades, sig, lot, lots=1)
    sell = bs.reprice_naked(trades, sig, lot, lots=1)
    sprd = bs.reprice_spread(trades, sig, lot, lots=1)

    print()
    print("  " + "=" * 104)
    print("  %-22s %6s %13s %8s %7s %7s %13s %11s" % (
        "PASS", "trades", "NET Rs", "win%", "PF", "Sharpe", "maxDD Rs", "avg/trade"))
    print("  " + "-" * 104)
    show("(1) Instrument/spot", stats(spot), "raw signal — no options, no charges")
    show("(3) BS  BUY  ATM", stats([t["pnl"] for t in buy]), "long->CE buy, short->PE buy")
    show("(3) BS  SELL ATM", stats([t["pnl"] for t in sell]), "long->PE sell, short->CE sell (naked)")
    show("(3) BS  SPREAD", stats([t["pnl"] for t in sprd]), "sell + wing (defined risk)")
    print("  " + "=" * 104)

    for nm, rows in (("BUY", buy), ("SELL", sell), ("SPREAD", sprd)):
        fee = sum(r["fee"] for r in rows)
        slip = sum(r.get("slip", 0) for r in rows)
        gross = sum(r["gross"] for r in rows)
        print("  %-8s gross %13s   charges %11s   slippage %11s   ->  net %13s"
              % (nm, f"{gross:,.0f}", f"{fee:,.0f}", f"{slip:,.0f}", f"{gross-fee-slip:,.0f}"))

    print()
    print("  saal-dar-saal NET (Rs, 1 lot):")
    print("     %-6s %6s %12s %12s %12s %12s" % ("saal", "trades", "spot", "BUY", "SELL", "SPREAD"))
    yr = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    for i, t in enumerate(trades):
        y = pd.Timestamp(t["entry_dt"]).year
        yr[y][0] += 1
        yr[y][1] += spot[i]
        yr[y][2] += buy[i]["pnl"]
        yr[y][3] += sell[i]["pnl"]
        yr[y][4] += sprd[i]["pnl"]
    for y in sorted(yr):
        n, sp, b, s_, w = yr[y]
        print("     %-6s %6d %12s %12s %12s %12s" % (
            y, n, f"{sp:,.0f}", f"{b:,.0f}", f"{s_:,.0f}", f"{w:,.0f}"))

    print()
    ls = [t for t in trades if t["side"] == "long"]
    sh = [t for t in trades if t["side"] == "short"]
    print("  side-wise (spot points): long %d trades, %+.0f pts | short %d trades, %+.0f pts"
          % (len(ls), sum(t["points"] for t in ls), len(sh), sum(t["points"] for t in sh)))
    rs = defaultdict(lambda: [0, 0.0])
    for t in trades:
        rs[t["reason"]][0] += 1
        rs[t["reason"]][1] += t["points"]
    print()
    print("  exit reason-wise (spot points):")
    for r, (n, p) in sorted(rs.items(), key=lambda x: -x[1][0]):
        print("     %-18s %5d trades  %+9.0f pts" % (r, n, p))
    print()


if __name__ == "__main__":
    main()


def vrp_sweep():
    """SELL priced at REALISED vol collects no volatility risk premium — and BS not
    seeing VRP is exactly TRAP #106 (iron-fly read -100% on BS, +61% on real premium).
    So before concluding "selling loses", pay the seller the VRP and see if it flips.
    vrp_mult scales entry+exit sigma: 1.0 = realised, 1.2 = IV 20% over realised.
    """
    import sys as _s
    _s.argv = [_s.argv[0]]
    cont5 = load_5m(None)
    daily = daily_from_5m(cont5)
    cfg = engine_cfg()
    trades = run_engine(cont5, daily, cfg)
    lot = bs.get_nifty_lot()
    sig = bs.realised_vol_map(daily.set_index("date")["close"])
    print()
    print("  VRP sensitivity — seller ko kitna extra premium mile to SELL jeete?")
    print()
    print("     %-10s %13s %8s %8s   %s" % ("vrp_mult", "SELL net Rs", "PF", "Sharpe", "matlab"))
    print("     " + "-" * 66)
    b = stats([t["pnl"] for t in bs.reprice(trades, sig, lot, lots=1)])
    for m, note in ((1.0, "realised vol (koi VRP nahi)"), (1.1, "IV 10% upar"),
                    (1.2, "IV 20% upar (NIFTY ka aam)"), (1.3, "IV 30% upar"),
                    (1.5, "IV 50% upar (bahut udaar)")):
        s = stats([t["pnl"] for t in bs.reprice_naked(trades, sig, lot, lots=1, vrp_mult=m)])
        print("     %-10s %13s %8.2f %8.2f   %s" % (m, f"{s['net']:,.0f}", s["pf"], s["sharpe"], note))
    print()
    print("     %-10s %13s %8.2f %8.2f   %s" % ("BUY", f"{b['net']:,.0f}", b["pf"], b["sharpe"], "tulna ke liye"))
    print()
