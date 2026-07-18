"""VWAP + Standard-Pivot intraday STOCK strategy — faithful backtest of the
YouTube "3-month reset challenge" strategy.

RULES (from the video, verbatim intent):
  * Instrument: NIFTY-50 stocks, INTRADAY only (equity MIS / futures, 5x lev).
    NO options, NO swing/BTST/STBT.
  * Indicators: intraday VWAP (volume-weighted, resets daily) + STANDARD floor
    pivots from the PREVIOUS day (P, R1..R5, S1..S5).  No Camarilla/Fib.
  * Regime: price ABOVE vwap => LONG only ; BELOW vwap => SHORT only.  "Bas."
  * Entry trigger: a 5-min candle CLOSES across a pivot level in the regime
    direction (fresh cross: prev bar was on the other side of that level).
    Skip the very first candle of the day (must be above vwap FIRST, then close).
  * Stop loss: the breakout candle's own low (long) / high (short) — i.e. the
    "below vwap" invalidation candle.  Also exit if a bar CLOSES back on the
    wrong side of vwap (regime flip).
  * Target: the NEXT pivot level in the trade direction.  (partial 40/40/20 is a
    smoothing refinement — base test books full at next pivot for a clean edge read)
  * Filters (house + video):  max 2 trades/day, no NEW entry after `no_entry_hm`
    (video: ~12:00-13:00), force square-off 15:15.

This trades the UNDERLYING (not options) so P&L is real spot points; costs use a
proper Zerodha EQUITY-INTRADAY (MIS) charge model (charges.py has only F&O).

Reuses:  intraday_engine.resample / _daily_levels (prev-day floor pivots, no lookahead).
Honest metric: per-trade return is in % of entry price (price-agnostic) so 5
different-priced stocks pool fairly — never sum per-share rupees across a universe.
"""
import os
import sys
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import intraday_engine as ie  # noqa: E402

EQ_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Equity"
EXIT_HM = dt.time(15, 15)          # house rule: square-off / no entry after


# ---------------------------------------------------------------- data
def load_stock_1m(sym):
    """Concat the per-day <SYM>_*.csv files into one 1-min OHLCV frame."""
    import glob
    folder = os.path.join(EQ_DIR, sym)
    files = sorted(glob.glob(os.path.join(folder, f"{sym}_*.csv")))
    if not files:
        return None
    frames = []
    for f in files:
        try:
            g = pd.read_csv(f)
            if len(g) and "Datetime" in g.columns:
                frames.append(g)
        except Exception:
            pass
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df.sort_values("Datetime").drop_duplicates("Datetime").reset_index(drop=True)
    return df


def add_vwap(d):
    """Daily-reset session VWAP on typical price.  d must have day/Volume."""
    tp = (d.High + d.Low + d.Close) / 3.0
    vol = d.Volume.replace(0, np.nan)
    pv = (tp * vol)
    cum_pv = pv.groupby(d.day).cumsum()
    cum_v = vol.groupby(d.day).cumsum()
    d = d.copy()
    d["vwap"] = (cum_pv / cum_v).ffill()
    return d


# ---------------------------------------------------------------- charges
def equity_intraday_charges(entry_px, exit_px, qty):
    """Zerodha equity-INTRADAY (MIS) round-trip cost in rupees, for one leg pair.
       brokerage 0.03% or Rs20/order (lower), per side
       STT 0.025% on SELL turnover
       exch txn NSE 0.00297%  each side
       SEBI  Rs10/cr = 0.0001% each side
       stamp 0.003% on BUY turnover
       GST 18% on (brokerage + exch txn + sebi)
    """
    buy_tv = entry_px * qty
    sell_tv = exit_px * qty
    brok = min(0.0003 * buy_tv, 20.0) + min(0.0003 * sell_tv, 20.0)
    stt = 0.00025 * sell_tv
    exch = 0.0000297 * (buy_tv + sell_tv)
    sebi = 0.000001 * (buy_tv + sell_tv)
    stamp = 0.00003 * buy_tv
    gst = 0.18 * (brok + exch + sebi)
    return brok + stt + exch + sebi + stamp + gst


# ---------------------------------------------------------------- core sim
def run_stock(df1m, tf="5m", no_entry_hm=dt.time(12, 0), max_trades=2,
              slip_bps=1.0, ride=False):
    """Return list of trade dicts for one stock.  slip_bps = per-side slippage
       in basis points of price.  ride=True lets a hit-target roll to the next
       pivot instead of exiting (rough 'let it run')."""
    d = ie.resample(df1m, tf)
    if len(d) < 200:
        return []
    d = add_vwap(d)
    lvls = ie._daily_levels(d, lookback=20, max_jump=10.0)

    trades = []
    for day, g in d.groupby("day"):
        lv = lvls.get(day)
        if lv is None:
            continue
        # full sorted level ladder for this day (prev-day floor pivots + chain)
        P = lv["neutral"][0]
        levels = sorted(set([round(x, 4) for x in lv["res"] + lv["sup"] + [P]]))
        g = g.reset_index(drop=True)
        n = len(g)
        C = g.Close.values; H = g.High.values; L = g.Low.values
        VW = g.vwap.values; T = g.Datetime.dt.time.values
        pos = None          # dict(side, entry, sl, tgt, entry_dt, entry_i)
        n_trades = 0
        for i in range(1, n):
            t = T[i]
            # ---- manage open position (exit checks) ----
            if pos is not None:
                px_exit = None; reason = None
                if pos["side"] == 1:                       # long
                    if L[i] <= pos["sl"]:
                        px_exit, reason = pos["sl"], "SL"
                    elif pos["tgt"] is not None and H[i] >= pos["tgt"]:
                        if ride:
                            nxt = next((x for x in levels if x > pos["tgt"] + 1e-6), None)
                            if nxt is not None:
                                pos["sl"] = max(pos["sl"], pos["tgt"])  # lock at booked level
                                pos["tgt"] = nxt
                            else:
                                px_exit, reason = pos["tgt"], "Target"
                        else:
                            px_exit, reason = pos["tgt"], "Target"
                    elif C[i] < VW[i]:                     # regime flip
                        px_exit, reason = C[i], "VWAP flip"
                else:                                      # short
                    if H[i] >= pos["sl"]:
                        px_exit, reason = pos["sl"], "SL"
                    elif pos["tgt"] is not None and L[i] <= pos["tgt"]:
                        if ride:
                            nxt = next((x for x in reversed(levels) if x < pos["tgt"] - 1e-6), None)
                            if nxt is not None:
                                pos["sl"] = min(pos["sl"], pos["tgt"])
                                pos["tgt"] = nxt
                            else:
                                px_exit, reason = pos["tgt"], "Target"
                        else:
                            px_exit, reason = pos["tgt"], "Target"
                    elif C[i] > VW[i]:
                        px_exit, reason = C[i], "VWAP flip"
                if px_exit is None and t >= EXIT_HM:
                    px_exit, reason = C[i], "EOD 3:15"
                if px_exit is not None:
                    trades.append(_close(pos, px_exit, g.Datetime.iloc[i], reason, slip_bps))
                    pos = None
                continue
            # ---- look for entry ----
            if n_trades >= max_trades or t >= EXIT_HM or t >= no_entry_hm:
                continue
            if np.isnan(VW[i]):
                continue
            above = C[i] > VW[i]; below = C[i] < VW[i]
            # fresh cross of any level in regime direction
            if above:
                crossed = [x for x in levels if C[i - 1] <= x < C[i]]
                if crossed:
                    tgt = next((x for x in levels if x > C[i] + 1e-6), None)
                    pos = dict(side=1, entry=C[i], sl=L[i], tgt=tgt,
                               entry_dt=g.Datetime.iloc[i], entry_i=i)
                    n_trades += 1
            elif below:
                crossed = [x for x in levels if C[i] < x <= C[i - 1]]
                if crossed:
                    tgt = next((x for x in reversed(levels) if x < C[i] - 1e-6), None)
                    pos = dict(side=-1, entry=C[i], sl=H[i], tgt=tgt,
                               entry_dt=g.Datetime.iloc[i], entry_i=i)
                    n_trades += 1
        # force close if still open at end of day frame
        if pos is not None:
            trades.append(_close(pos, C[-1], g.Datetime.iloc[-1], "EOD", slip_bps))
    return trades


def _close(pos, px_exit, exit_dt, reason, slip_bps):
    s = pos["side"]
    entry = pos["entry"]; qty = 1
    # slippage: pay on entry (worse) and exit (worse)
    slip = slip_bps / 10000.0
    e_fill = entry * (1 + slip * s)          # long buys higher, short sells lower
    x_fill = px_exit * (1 - slip * s)
    gross_pts = (x_fill - e_fill) * s
    ret_pct = gross_pts / entry * 100.0
    # per-share cost as % — assume 5x leverage MIS, size to ~Rs1L notional/trade
    notional = 100000.0
    q = max(1, round(notional / entry))
    fee = equity_intraday_charges(e_fill, x_fill, q)
    gross_rs = gross_pts * q
    net_rs = gross_rs - fee
    fee_pct = fee / notional * 100.0
    return dict(side="long" if s == 1 else "short", entry=round(entry, 2),
                exit=round(px_exit, 2), entry_dt=str(pos["entry_dt"]),
                exit_dt=str(exit_dt), reason=reason, points=round(gross_pts, 2),
                ret_pct=round(ret_pct, 4), fee_pct=round(fee_pct, 4),
                net_pct=round(ret_pct - fee_pct, 4), gross_rs=round(gross_rs, 1),
                net_rs=round(net_rs, 1), day=str(pos["entry_dt"])[:10])


# ---------------------------------------------------------------- metrics
def summarise(trades, label=""):
    if not trades:
        return dict(label=label, n=0)
    df = pd.DataFrame(trades)
    net = df.net_pct.values
    wins = net[net > 0]; losses = net[net <= 0]
    gross_win = wins.sum(); gross_loss = -losses.sum()
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    exp = net.mean()
    sd = net.std(ddof=1) if len(net) > 1 else 0.0
    # t-stat on per-trade net% mean (crude significance)
    tstat = exp / (sd / np.sqrt(len(net))) if sd > 0 else 0.0
    return dict(label=label, n=len(net),
                net_pct_sum=round(net.sum(), 2),
                gross_pct_sum=round(df.ret_pct.sum(), 2),
                fee_pct_sum=round(df.fee_pct.sum(), 2),
                win_rate=round(100 * (net > 0).mean(), 1),
                avg_net=round(exp, 4), pf=round(pf, 2),
                avg_win=round(wins.mean(), 3) if len(wins) else 0,
                avg_loss=round(losses.mean(), 3) if len(losses) else 0,
                tstat=round(tstat, 2),
                per_day=round(net.sum() / df.day.nunique(), 3) if df.day.nunique() else 0)


def main():
    import json
    stocks = sys.argv[1].split(",") if len(sys.argv) > 1 else \
        ["BAJFINANCE", "BAJAJFINSV", "TITAN", "RELIANCE", "TCS"]
    no_entry = sys.argv[2] if len(sys.argv) > 2 else "12:00"
    hh, mm = map(int, no_entry.split(":"))
    ne = dt.time(hh, mm)
    ride = "--ride" in sys.argv
    print(f"\n=== VWAP+Pivot STOCKS | tf=5m | no-entry-after={no_entry} | "
          f"ride={ride} | slip=1bp/side | eq-intraday cost ===\n")
    all_tr = []
    rows = []
    for s in stocks:
        df = load_stock_1m(s)
        if df is None or len(df) < 2000:
            print(f"  {s:12s}  (no/low data)")
            continue
        tr = run_stock(df, no_entry_hm=ne, ride=ride)
        for t in tr:
            t["sym"] = s
        all_tr += tr
        r = summarise(tr, s)
        rows.append(r)
        span = f"{df.Datetime.dt.date.min()}..{df.Datetime.dt.date.max()}"
        print(f"  {s:12s} n={r.get('n',0):4d}  net%Σ={r.get('net_pct_sum',0):8.2f}  "
              f"WR={r.get('win_rate',0):5.1f}  PF={r.get('pf',0):5.2f}  "
              f"avg={r.get('avg_net',0):+.3f}  t={r.get('tstat',0):+.2f}  [{span}]")
    print("\n  " + "-" * 78)
    agg = summarise(all_tr, "ALL")
    print(f"  {'AGGREGATE':12s} n={agg.get('n',0):4d}  net%Σ={agg.get('net_pct_sum',0):8.2f}  "
          f"WR={agg.get('win_rate',0):5.1f}  PF={agg.get('pf',0):5.2f}  "
          f"avg={agg.get('avg_net',0):+.3f}  t={agg.get('tstat',0):+.2f}")
    print(f"    gross%Σ={agg.get('gross_pct_sum')}  fees%Σ={agg.get('fee_pct_sum')}  "
          f"avg_win={agg.get('avg_win')}  avg_loss={agg.get('avg_loss')}  "
          f"per_day_net%={agg.get('per_day')}")
    # exit-reason breakdown
    if all_tr:
        rdf = pd.DataFrame(all_tr)
        print("\n  exit reasons:", dict(rdf.reason.value_counts()))
    # dump trades for later day-wise view
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vwap_pivot_trades.json")
    with open(out, "w") as f:
        json.dump(all_tr, f)
    print(f"\n  {len(all_tr)} trades -> {out}\n")


if __name__ == "__main__":
    main()
