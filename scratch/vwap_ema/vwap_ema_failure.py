"""vwap_ema_failure.py — backtest of the "VWAP + 10 EMA failure/rejection" strategy.

Source: user's YouTube-transcript strategy (5-min equity intraday, NSE large-caps).

MECHANICAL RULES (what this test enforces — the video is partly discretionary,
so this is the honest mechanical core; discretionary A+ filters like "avoid dojis"
/ "previous-day-high rejection" are NOT modelled here on purpose):

  timeframe        : 5-min (resampled from 1-min lake)
  indicators       : session-reset VWAP (typical price) + EMA(10) on 5-min close
  SHORT entry      : a 5-min bar CLOSES below VWAP and below EMA10, with EMA10 < VWAP
                     and |EMA10-VWAP|/px <= dist_thr (EMA & VWAP close together).
                     Enter next bar OPEN (no lookahead).
  LONG entry       : mirror (close above both, EMA10 > VWAP, distance filter).
  stop-loss        : SHORT = signal-bar HIGH ; LONG = signal-bar LOW.
  exit             : (a) fixed R:R target  OR  (b) EMA10 trail — both tested.
  session          : entries only inside [entry_start, entry_cutoff] (Golden period
                     default 09:15-11:30). Force-exit all @ 15:15.
  discipline       : max_trades_per_day per symbol; 1 open position per symbol.

COST: real Zerodha equity-intraday round trip (equity_charges.roundtrip_cost).

Sizing: fixed notional per trade (default Rs 1,00,000) -> qty = notional/entry_px.
Reported P&L is per-trade Rs at that notional; comparisons are notional-normalised.
"""
import os
import sys
import glob
import argparse
import datetime as dt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from equity_charges import roundtrip_cost

LAKE = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Equity"

NIFTY50 = [
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO",
    "BAJAJFINSV","BAJFINANCE","BHARTIARTL","BPCL","BRITANNIA","CIPLA","COALINDIA",
    "DIVISLAB","DRREDDY","EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK","INFY","ITC",
    "JSWSTEEL","KOTAKBANK","LT","M&M","MARUTI","NESTLEIND","NTPC","ONGC","POLYCAB",
    "POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN","SUNPHARMA","TATACONSUM",
    "TATAMOTORS","TATASTEEL","TCS","TECHM","TITAN","ULTRACEMCO","UPL","WIPRO",
]


def load_day_files(sym):
    """Yield (date_str, df_1min) for each per-day CSV of a symbol, sorted."""
    files = sorted(glob.glob(os.path.join(LAKE, sym, f"{sym}_20*.csv")))
    for f in files:
        base = os.path.basename(f)
        date_str = base[len(sym) + 1:-4]  # SYM_YYYY-MM-DD.csv
        yield date_str, f


def resample_5m(df1):
    df1 = df1.set_index("Datetime")
    agg = df1.resample("5min", label="left", closed="left").agg(
        Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
        Close=("Close", "last"), Volume=("Volume", "sum"))
    return agg.dropna(subset=["Open"])


def add_indicators(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    cum_pv = (tp * df["Volume"]).cumsum()
    cum_v = df["Volume"].cumsum().replace(0, np.nan)
    df["VWAP"] = cum_pv / cum_v
    df["EMA"] = df["Close"].ewm(span=10, adjust=False).mean()
    return df


def nifty_direction_map(date_from, date_to):
    """Per-date dict {date_str: {ts_time: +1 above vwap / -1 below}} for NIFTY.
    Used as market-direction filter: short only when mkt<vwap, long only when >."""
    out = {}
    for date_str, f in load_day_files("NIFTY"):
        if (date_from and date_str < date_from) or (date_to and date_str > date_to):
            continue
        try:
            df1 = pd.read_csv(f, parse_dates=["Datetime"])
        except Exception:
            continue
        if len(df1) < 30:
            continue
        df5 = add_indicators(resample_5m(df1))
        m = {}
        for ts, row in df5.iterrows():
            if not np.isnan(row["VWAP"]):
                m[ts.time()] = 1 if row["Close"] > row["VWAP"] else -1
        out[date_str] = m
    return out


def backtest_day(df, cfg, mkt_dir=None):
    """One symbol-day of 5-min bars w/ indicators. Returns list of trade dicts.
    mkt_dir: optional {ts_time: +1/-1} NIFTY direction for that date."""
    trades = []
    n = len(df)
    idx = df.index
    o = df["Open"].values; h = df["High"].values; l = df["Low"].values
    c = df["Close"].values; vwap = df["VWAP"].values; ema = df["EMA"].values
    times = [t.time() for t in idx]

    ecut = cfg["entry_cutoff"]; estart = cfg["entry_start"]; force = cfg["force_exit"]
    dist = cfg["dist_thr"]; rr = cfg["rr"]; use_trail = cfg["trail"]
    max_tr = cfg["max_trades"]; allow_long = cfg["long"]; allow_short = cfg["short"]

    ntr = 0
    i = 1  # need prior bar for indicators warmup
    open_pos = None
    while i < n:
        t = times[i]
        # ---- manage open position first (exit checks on bar i) ----
        if open_pos is not None:
            side = open_pos["side"]; sl = open_pos["sl"]; tgt = open_pos["tgt"]
            ex = None; expx = None; reason = None
            if side == "SHORT":
                if h[i] >= sl:
                    ex, expx, reason = True, sl, "SL"
                elif tgt is not None and l[i] <= tgt:
                    ex, expx, reason = True, tgt, "TGT"
            else:
                if l[i] <= sl:
                    ex, expx, reason = True, sl, "SL"
                elif tgt is not None and h[i] >= tgt:
                    ex, expx, reason = True, tgt, "TGT"
            # EMA trail: move SL to EMA of prior bar (i-1) in-favour only
            if not ex and use_trail:
                trail = ema[i - 1]
                if side == "SHORT" and trail < open_pos["sl"]:
                    open_pos["sl"] = trail
                if side == "LONG" and trail > open_pos["sl"]:
                    open_pos["sl"] = trail
            # force exit at/after 15:15
            if not ex and t >= force:
                ex, expx, reason = True, c[i], "EOD"
            if ex:
                open_pos.update(exit_time=idx[i], exit_px=expx, reason=reason)
                trades.append(open_pos); open_pos = None

        # ---- look for new entry signal on bar i (act next bar open) ----
        if open_pos is None and ntr < max_tr and estart <= t <= ecut and i + 1 < n:
            if np.isnan(vwap[i]) or np.isnan(ema[i]):
                i += 1; continue
            close_dist_ok = abs(ema[i] - vwap[i]) / c[i] <= dist
            short_sig = allow_short and c[i] < vwap[i] and c[i] < ema[i] and ema[i] < vwap[i]
            long_sig = allow_long and c[i] > vwap[i] and c[i] > ema[i] and ema[i] > vwap[i]
            # market-direction filter (NIFTY vs its VWAP at this bar's time)
            if mkt_dir is not None:
                md = mkt_dir.get(t)
                if md is not None:
                    if md == 1:   # market bullish -> no shorts
                        short_sig = False
                    else:         # market bearish -> no longs
                        long_sig = False
            if close_dist_ok and (short_sig or long_sig):
                side = "SHORT" if short_sig else "LONG"
                entry_px = o[i + 1]
                if side == "SHORT":
                    sl = h[i]
                    risk = sl - entry_px
                    tgt = entry_px - rr * risk if (rr and not use_trail) else None
                else:
                    sl = l[i]
                    risk = entry_px - sl
                    tgt = entry_px + rr * risk if (rr and not use_trail) else None
                if risk <= 0:  # degenerate; skip
                    i += 1; continue
                open_pos = dict(side=side, entry_time=idx[i + 1], entry_px=entry_px,
                                sl=sl, tgt=tgt, risk=risk, sig_bar=idx[i])
                ntr += 1
                i += 2  # skip the entry bar itself for exit-scan start
                continue
        i += 1

    # close any still-open at last bar
    if open_pos is not None:
        open_pos.update(exit_time=idx[-1], exit_px=c[-1], reason="EOD")
        trades.append(open_pos)
    return trades


def run(cfg, symbols, date_from=None, date_to=None, verbose=True):
    notional = cfg["notional"]
    mkt = None
    if cfg.get("mkt_filter"):
        if verbose:
            print("  building NIFTY direction map...", flush=True)
        mkt = nifty_direction_map(date_from, date_to)
    rows = []
    for si, sym in enumerate(symbols):
        for date_str, f in load_day_files(sym):
            if date_from and date_str < date_from:
                continue
            if date_to and date_str > date_to:
                continue
            try:
                df1 = pd.read_csv(f, parse_dates=["Datetime"])
            except Exception:
                continue
            if len(df1) < 30:
                continue
            df5 = resample_5m(df1)
            if len(df5) < 10:
                continue
            df5 = add_indicators(df5)
            trs = backtest_day(df5, cfg, mkt.get(date_str) if mkt else None)
            for tr in trs:
                qty = max(1, int(notional / tr["entry_px"]))
                if tr["side"] == "SHORT":
                    gross = (tr["entry_px"] - tr["exit_px"]) * qty
                else:
                    gross = (tr["exit_px"] - tr["entry_px"]) * qty
                fee = roundtrip_cost(tr["entry_px"], tr["exit_px"], qty, tr["side"])
                net = gross - fee
                rr_real = (gross / qty) / tr["risk"] if tr["risk"] else 0.0
                rows.append(dict(sym=sym, date=date_str, side=tr["side"],
                                 entry=round(tr["entry_px"], 2), exit=round(tr["exit_px"], 2),
                                 reason=tr["reason"], qty=qty, gross=gross, fee=fee,
                                 net=net, rr=rr_real))
        if verbose and (si + 1) % 10 == 0:
            print(f"  ...{si+1}/{len(symbols)} symbols", flush=True)
    return pd.DataFrame(rows)


def report(df, label=""):
    if df.empty:
        print(f"[{label}] NO TRADES"); return {}
    n = len(df)
    wins = df[df.net > 0]; losses = df[df.net <= 0]
    gross = df.gross.sum(); fee = df.fee.sum(); net = df.net.sum()
    wr = len(wins) / n
    pf = wins.net.sum() / abs(losses.net.sum()) if len(losses) and losses.net.sum() != 0 else float("inf")
    avg = df.net.mean()
    # daily P&L series for Sharpe
    dd = df.groupby("date").net.sum()
    sharpe = (dd.mean() / dd.std() * np.sqrt(252)) if dd.std() > 0 else 0.0
    print(f"\n===== {label} =====")
    print(f"  trades          : {n}   (across {df.date.nunique()} days, {df.sym.nunique()} symbols)")
    print(f"  win rate        : {wr*100:.1f}%")
    print(f"  gross Rs        : {gross:,.0f}")
    print(f"  fees  Rs        : {fee:,.0f}  ({fee/n:.0f}/trade)")
    print(f"  NET   Rs        : {net:,.0f}   ({avg:+.0f}/trade)")
    print(f"  profit factor   : {pf:.2f}  (net of cost)")
    print(f"  avg R multiple  : {df.rr.mean():+.2f}R (gross)")
    print(f"  daily Sharpe(ann): {sharpe:.2f}")
    print(f"  by side         : " + ", ".join(
        f"{s}: {len(g)} tr, net {g.net.sum():,.0f}" for s, g in df.groupby('side')))
    return dict(n=n, wr=wr, net=net, pf=pf, sharpe=sharpe, avg=avg)


DEFAULT_CFG = dict(
    entry_start=dt.time(9, 15), entry_cutoff=dt.time(11, 30), force_exit=dt.time(15, 15),
    dist_thr=0.004, rr=2.0, trail=False, max_trades=3, long=True, short=True,
    notional=100000.0, mkt_filter=False,
)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--symbols", default=None, help="comma list; default Nifty-50")
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--trail", action="store_true")
    ap.add_argument("--dist", type=float, default=0.004)
    ap.add_argument("--cutoff", default="11:30", help="entry cutoff HH:MM")
    ap.add_argument("--maxtr", type=int, default=3)
    ap.add_argument("--long-only", action="store_true")
    ap.add_argument("--short-only", action="store_true")
    ap.add_argument("--mkt", action="store_true", help="NIFTY market-direction filter")
    ap.add_argument("--out", default=None, help="save trades csv")
    a = ap.parse_args()

    cfg = dict(DEFAULT_CFG)
    cfg["rr"] = a.rr; cfg["trail"] = a.trail; cfg["dist_thr"] = a.dist
    cfg["max_trades"] = a.maxtr
    hh, mm = a.cutoff.split(":"); cfg["entry_cutoff"] = dt.time(int(hh), int(mm))
    if a.long_only: cfg["short"] = False
    if a.short_only: cfg["long"] = False
    cfg["mkt_filter"] = a.mkt

    syms = a.symbols.split(",") if a.symbols else NIFTY50
    lbl = f"RR={a.rr} trail={a.trail} dist={a.dist} cutoff={a.cutoff} maxtr={a.maxtr} " \
          f"{'LONG' if not cfg['short'] else ''}{'SHORT' if not cfg['long'] else ''}{'BOTH' if cfg['long'] and cfg['short'] else ''}"
    print(f"Running: {lbl}\n  symbols={len(syms)}  from={a.dfrom} to={a.dto}", flush=True)
    df = run(cfg, syms, a.dfrom, a.dto)
    report(df, lbl)
    if a.out:
        df.to_csv(a.out, index=False); print(f"\nsaved {a.out}")
