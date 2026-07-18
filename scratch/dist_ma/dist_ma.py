#!/usr/bin/env python3
r"""
dist_ma.py — "Distance from Moving Average" extreme-oversold BUY strategy.

Source: Dhan YouTube video ("distance from moving average" quant idea).
Idea (buy-side only, large-cap equities, positional daily):
  * Track 20 EMA. Distance% = (Close - EMA20) / EMA20 * 100.
  * A big/large-cap stock rarely trades > ~10% BELOW its 20 EMA. When it does,
    that is an extreme oversold "stretched gulel" zone -> mean-reversion snap-back.
  * Entry: after the extreme, wait for a bullish reversal candle
    (hammer / bullish-engulfing / piercing), BUY on break of that candle's HIGH.
  * Stop: below the reversal candle's LOW (small).
  * Target: snap back toward the 20 EMA (video: swing 4-7%, positional 14-18%).
  * Swing/hourly variant uses a smaller threshold (~ -4 to -4.5%).

This file:
  PHASE 1 (decisive, cost-free): does the SIGNAL have forward-return edge at all?
          Forward returns after every extreme-oversold reversal vs the stock's own
          unconditional baseline, pooled across the whole F&O-stock daily lake.
  PHASE 2 (only if edge is real): full event-driven backtest — reversal-high entry,
          candle-low stop, EMA-touch / R-multiple exits, real delivery costs,
          train/OOS split, expectancy / PF / win-rate.

Data lake (read-only, shared, no downloads/API): daily OHLCV, 210 F&O stocks,
2013-2026:  ._TRADING DATA/EquityDaily/<SYM>.csv  (Date,Open,High,Low,Close,Volume)
"""
import os, sys, glob, argparse
import numpy as np
import pandas as pd

# ---- shared read-only daily equity lake (absolute; outside repo/worktree) ----
_CANDIDATES = [
    r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\EquityDaily",
    os.path.join(os.path.dirname(__file__), "..", "..", "_TRADING_DATA", "EquityDaily"),
]
# fallback = repo-relative path (_CANDIDATES[-1]), NOT the Windows path — on a
# fresh Linux/VPS box the Windows string isn't a dir, and using it as the default
# would makedirs() a literal 'D:\...'-named junk folder. Repo path is correct there.
EQ_DIR = next((p for p in _CANDIDATES if os.path.isdir(p)), _CANDIDATES[-1])


def symbols():
    return sorted(os.path.splitext(os.path.basename(f))[0]
                  for f in glob.glob(os.path.join(EQ_DIR, "*.csv")))


def load(sym):
    df = pd.read_csv(os.path.join(EQ_DIR, sym + ".csv"), parse_dates=["Date"])
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).sort_values("Date")
    return df.reset_index(drop=True)


# ---------------- indicators + candle patterns ----------------
def prep(df, ema_n=20):
    d = df.copy()
    c, o, h, l = d.Close, d.Open, d.High, d.Low
    d["ema"] = c.ewm(span=ema_n, adjust=False).mean()
    d["dist"] = (c - d["ema"]) / d["ema"] * 100.0
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1.0 / 14, adjust=False).mean()  # Wilder ATR14

    pc, po, pl, ph = c.shift(1), o.shift(1), l.shift(1), h.shift(1)
    body = (c - o).abs()
    lower_wick = np.minimum(o, c) - l
    upper_wick = h - np.maximum(o, c)
    prior_red = pc < po

    # bullish hammer: long lower wick, small upper wick, close >= open
    d["hammer"] = (lower_wick >= 2.0 * body) & (upper_wick <= body) & (c >= o)
    # bullish engulfing: today green engulfs prior red body
    d["engulf"] = (c > o) & prior_red & (o <= pc) & (c >= po)
    # piercing: prior red, today opens below prior close, closes into upper half of prior body (but < prior open)
    d["pierce"] = (c > o) & prior_red & (o < pc) & (c > (po + pc) / 2.0) & (c < po)
    # generic reversal-up fallback: green candle that closes above prior high (strength)
    d["revup"] = (c > o) & (c > ph)

    d["reversal"] = d["hammer"] | d["engulf"] | d["pierce"] | d["revup"]
    return d


def signal_bars(d, thresh=-10.0, look=3):
    """A trigger bar = a bullish reversal candle formed while the extreme
    oversold zone was hit within the last `look` bars (incl. today), and price
    is still below the EMA (dist < 0)."""
    ext = (d["dist"] <= thresh)
    ext_recent = ext.rolling(look, min_periods=1).max().astype(bool)
    return d["reversal"] & ext_recent & (d["dist"] < 0)


# ================= PHASE 1: forward-return edge (cost-free) =================
def phase1(thresh=-10.0, look=3, horizons=(5, 10, 20, 40)):
    syms = symbols()
    sig_fwd = {k: [] for k in horizons}
    base_fwd = {k: [] for k in horizons}
    n_sig = 0
    for s in syms:
        try:
            d = prep(load(s))
        except Exception:
            continue
        c = d.Close.values
        n = len(c)
        if n < 60:
            continue
        sig = signal_bars(d, thresh, look).values
        for k in horizons:
            # baseline: every bar's k-day forward return
            fwd = c[k:] / c[:-k] - 1.0
            base_fwd[k].append(fwd)
            idx = np.where(sig[:-k])[0]
            if len(idx):
                sig_fwd[k].append(fwd[idx])
        n_sig += int(sig[:-max(horizons)].sum())

    print(f"\n{'='*74}\nPHASE 1 - forward-return edge   thresh={thresh}%  look={look}d")
    print(f"universe={len(syms)} stocks   signals~{n_sig}")
    print(f"{'-'*74}")
    print(f"{'horizon':>8} | {'sig_mean':>9} {'sig_win%':>8} {'sig_med':>8} | "
          f"{'base_mean':>9} {'base_win%':>9} | {'EDGE':>7} {'p':>7}")
    print(f"{'-'*74}")
    for k in horizons:
        sg = np.concatenate(sig_fwd[k]) if sig_fwd[k] else np.array([])
        bs = np.concatenate(base_fwd[k]) if base_fwd[k] else np.array([])
        if not len(sg):
            print(f"{k:>7}d | (no signals)")
            continue
        edge = sg.mean() - bs.mean()
        # t vs baseline mean (H0: signal mean == baseline mean)
        se = sg.std(ddof=1) / np.sqrt(len(sg))
        t = edge / se if se > 0 else 0.0
        from math import erf, sqrt
        p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
        print(f"{k:>7}d | {sg.mean()*100:>8.2f}% {(sg>0).mean()*100:>7.1f}% "
              f"{np.median(sg)*100:>7.2f}% | {bs.mean()*100:>8.2f}% {(bs>0).mean()*100:>8.1f}% |"
              f" {edge*100:>+6.2f}% {p:>7.4f}   n={len(sg)}")
    print(f"{'='*74}")


# ================= PHASE 2: full event-driven backtest =================
def backtest(thresh=-10.0, look=3, entry_win=3, exit_style="ema_or_rr", rr=2.0,
             max_hold=40, cost_pct=0.30, slip_pct=0.10, start=None, end=None,
             sl_atr=0.0, tp_pct=0.0):
    """Long-only. Entry = buy-stop at reversal candle HIGH within entry_win days.
    SL = reversal candle LOW (or candle_low - sl_atr*ATR14 if sl_atr>0). Exit:
      ema_touch : first bar High>=EMA20 (revert to mean) -> fill max(open, ema)
      rr        : target = entry + rr*(entry-sl)
      ema_or_rr : whichever of EMA target OR rr hits first  (+ SL + time-stop)
      hold      : no profit target -> hold to max_hold (positional), SL active
      pct       : fixed +tp_pct% target, SL active, time-stop
    Costs: round-trip cost_pct% + slip_pct% each side, on price.
    """
    syms = symbols()
    trades = []
    for s in syms:
        try:
            d = prep(load(s))
        except Exception:
            continue
        if start: d = d[d.Date >= pd.Timestamp(start)]
        if end:   d = d[d.Date <= pd.Timestamp(end)]
        d = d.reset_index(drop=True)
        O, H, L, C, E, A = (d.Open.values, d.High.values, d.Low.values,
                             d.Close.values, d.ema.values, d.atr.values)
        DATE = d.Date.values
        sig = signal_bars(d, thresh, look).values
        n = len(C)
        i = 0
        while i < n - 1:
            if not sig[i]:
                i += 1; continue
            trig_hi, trig_lo = H[i], L[i]
            entered = False
            for j in range(i + 1, min(i + 1 + entry_win, n)):
                if H[j] >= trig_hi:  # buy-stop triggered
                    entry = max(O[j], trig_hi) * (1 + slip_pct / 100)
                    sl = trig_lo - (sl_atr * A[i] if sl_atr > 0 else 0.0)
                    tgt_rr = entry + rr * (entry - sl)
                    tgt_pct = entry * (1 + tp_pct / 100) if tp_pct > 0 else None
                    exit_px = exit_reason = None
                    kk = j
                    for k in range(j, min(j + max_hold + 1, n)):
                        kk = k
                        if L[k] <= sl and k > j:
                            exit_px, exit_reason = sl, "SL"; break
                        if exit_style in ("ema_touch", "ema_or_rr") and H[k] >= E[k]:
                            exit_px, exit_reason = max(O[k], E[k]), "EMA"; break
                        if exit_style in ("rr", "ema_or_rr") and H[k] >= tgt_rr:
                            exit_px, exit_reason = tgt_rr, "RR"; break
                        if exit_style == "pct" and tgt_pct and H[k] >= tgt_pct:
                            exit_px, exit_reason = tgt_pct, "TP"; break
                        if k == min(j + max_hold, n - 1):
                            exit_px, exit_reason = C[k], "TIME"; break
                    if exit_px is None:
                        exit_px, exit_reason = C[kk], "TIME"
                    exit_px *= (1 - slip_pct / 100)
                    gross = exit_px / entry - 1.0
                    net = gross - cost_pct / 100
                    trades.append(dict(sym=s, entry_date=DATE[j], exit_date=DATE[kk],
                                       hold=kk - j, entry=entry, exit=exit_px,
                                       gross=gross, net=net, reason=exit_reason))
                    entered = True
                    i = kk  # no overlapping trade per symbol
                    break
            if not entered:
                i += 1
    return pd.DataFrame(trades)


def report(tr, label=""):
    if tr.empty:
        print(f"{label}: no trades"); return
    net = tr.net.values
    wins = net > 0
    exp = net.mean()
    pf_num = net[wins].sum(); pf_den = -net[~wins].sum()
    pf = pf_num / pf_den if pf_den > 0 else float("inf")
    print(f"\n--- {label} ---")
    print(f"trades       : {len(tr)}")
    print(f"win rate     : {wins.mean()*100:.1f}%")
    print(f"avg net/trade: {exp*100:+.2f}%   (gross {tr.gross.mean()*100:+.2f}%)")
    print(f"avg win      : {net[wins].mean()*100:+.2f}%   avg loss {net[~wins].mean()*100:+.2f}%")
    print(f"profit factor: {pf:.2f}")
    print(f"avg hold     : {tr.hold.mean():.1f} days   median {tr.hold.median():.0f}")
    print(f"total net (sum of trade %): {net.sum()*100:+.0f}%   over {len(tr)} trades")
    print(f"exit mix     : " + "  ".join(f"{r}={(tr.reason==r).sum()}" for r in ["EMA", "RR", "TP", "SL", "TIME"]))
    se = net.std(ddof=1) / np.sqrt(len(net)); t = exp / se if se > 0 else 0
    from math import erf, sqrt
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / sqrt(2))))
    print(f"expectancy p : {p:.4f}  (t={t:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="1", choices=["1", "2", "both"])
    ap.add_argument("--thresh", type=float, default=-10.0)
    ap.add_argument("--look", type=int, default=3)
    ap.add_argument("--rr", type=float, default=2.0)
    ap.add_argument("--exit", default="ema_or_rr")
    ap.add_argument("--maxhold", type=int, default=40)
    ap.add_argument("--cost", type=float, default=0.30)
    ap.add_argument("--slatr", type=float, default=0.0)
    ap.add_argument("--tp", type=float, default=0.0)
    a = ap.parse_args()
    print(f"lake: {EQ_DIR}   ({len(symbols())} symbols)")

    if a.phase in ("1", "both"):
        phase1(a.thresh, a.look)

    if a.phase in ("2", "both"):
        allt = backtest(a.thresh, a.look, exit_style=a.exit, rr=a.rr,
                        max_hold=a.maxhold, cost_pct=a.cost, sl_atr=a.slatr, tp_pct=a.tp)
        if not allt.empty:
            allt["y"] = pd.to_datetime(allt.entry_date).dt.year
            report(allt, f"ALL  exit={a.exit} rr={a.rr} thresh={a.thresh}")
            report(allt[allt.y < 2024], "TRAIN (<2024)")
            report(allt[allt.y >= 2024], "OOS (>=2024)")
        else:
            report(allt, "ALL")
