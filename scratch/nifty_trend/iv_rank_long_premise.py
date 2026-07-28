"""
DECISIVE PREMISE CHECK — "IV-rank <20 par NIFTY blast karta, option kharido"
(no options, no cost — just: does low IV-rank actually precede bigger moves,
and is realized-vol > implied-vol from there = the option-BUYER's real edge?)

If the <20 bucket does NOT show bigger forward moves AND realized>implied,
the thesis is survivorship and no auto-strategy is worth building.
"""
import json, os
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)

# ── daily IV series (vrp_iv_history.json = ATM/VIX-like IV, 2021-07 →) ──
_ivp = os.path.join(HERE, "vrp_iv_history.json")
if not os.path.exists(_ivp):
    _ivp = os.path.join(HERE, "..", "..", "data", "vrp_iv_history.json")
iv = json.load(open(_ivp))
iv = pd.Series({pd.Timestamp(k): float(v) for k, v in iv.items()}).sort_index()
iv = iv[(iv > 3) & (iv < 90)]                      # drop garbage

# ── NIFTY daily close from 1-min ──
df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), usecols=["Datetime", "Close"])
df["Datetime"] = pd.to_datetime(df["Datetime"])
daily = df.set_index("Datetime")["Close"].resample("1D").last().dropna()
daily.index = daily.index.normalize()

# align
idx = iv.index.intersection(daily.index)
iv = iv.reindex(idx).dropna()
px = daily.reindex(iv.index).dropna()
iv = iv.reindex(px.index)
print(f"aligned days: {len(px)}  span {px.index.min().date()} -> {px.index.max().date()}")

# ── IV-rank (percentile of today's IV vs trailing window), same formula as iv_rank_daily ──
def iv_rank(series, lookback):
    return series.rolling(lookback, min_periods=20).apply(lambda w: (w.iloc[-1] >= w).mean(), raw=False)

ret = np.log(px / px.shift(1))                     # daily log returns
ANN = np.sqrt(252)

def fwd_stats(N):
    # forward N-day: |cumulative move %|, realized annualised vol over next N days
    fwd_move = (px.shift(-N) / px - 1.0).abs() * 100
    rv = ret.shift(-1).rolling(N).std()            # std of returns days t+1..t+N
    rv = rv.reindex(px.index)
    # realised vol over the FORWARD window, annualised %
    fwd_rv = pd.Series(index=px.index, dtype=float)
    r = ret.values
    for i in range(len(px)):
        j0, j1 = i + 1, i + 1 + N
        if j1 <= len(r):
            w = r[j0:j1]
            if len(w) == N:
                fwd_rv.iloc[i] = np.std(w) * ANN * 100
    return fwd_move, fwd_rv

for LB in (252, 60):
    rank = iv_rank(iv, LB) * 100                    # 0..100
    print(f"\n================  IV-RANK (lookback {LB}d)  ================")
    buckets = [(-1, 20, "<20  (cheap vol — BUY zone)"),
               (20, 40, "20-40"), (40, 60, "40-60"),
               (60, 80, "60-80"), (80, 101, ">80  (rich vol)")]
    for N in (3, 5, 10):
        fmove, frv = fwd_stats(N)
        print(f"\n  --- forward {N} trading days ---")
        print(f"  {'IV-rank bucket':<32}{'N':>5}{'|move|%':>9}{'realVol%':>9}{'implVol%':>9}{'RV-IV':>8}")
        base_m, base_rv = fmove.mean(), frv.mean()
        for lo, hi, lbl in buckets:
            mask = (rank > lo) & (rank <= hi)
            n = int(mask.sum())
            if n < 10:
                print(f"  {lbl:<32}{n:>5}{'—':>9}")
                continue
            mm = fmove[mask].mean()
            rvm = frv[mask].mean()
            ivm = iv[mask].mean()                   # implied vol on entry day
            edge = rvm - ivm                        # realised − implied (buyer wins if >0)
            print(f"  {lbl:<32}{n:>5}{mm:>9.2f}{rvm:>9.1f}{ivm:>9.1f}{edge:>+8.1f}")
        print(f"  {'ALL days (baseline)':<32}{int(fmove.notna().sum()):>5}{base_m:>9.2f}{base_rv:>9.1f}{iv.mean():>9.1f}{base_rv-iv.mean():>+8.1f}")
