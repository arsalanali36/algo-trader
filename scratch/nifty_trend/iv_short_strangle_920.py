"""
"Sell the 9:20 gamma std distribution (±1σ expected move)" — decisive backtest.
Short strangle: at 9:20 sell CE @ spot+1σ, PE @ spot−1σ (σ = day's expected move
from ATM IV), hold to EOD (0DTE-style, exit = intrinsic). BS premium + real
Zerodha charges + slippage. Reports the FULL distribution + worst days (tail is
what kills naked sellers), and whether an IV-rank≥X filter helps.
"""
import json, os, math
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
LOT = 65
DAY_T = 1.0 / 252.0
Ncdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))

def bs(S, K, iv, T, call):
    if iv <= 0 or T <= 0:
        return max(0.0, (S - K) if call else (K - S))
    v = iv / 100.0
    d1 = (math.log(S / K) + 0.5 * v * v * T) / (v * math.sqrt(T))
    d2 = d1 - v * math.sqrt(T)
    if call:
        return S * Ncdf(d1) - K * Ncdf(d2)
    return K * Ncdf(-d2) - S * Ncdf(-d1)

def sell_charges(premium):
    # 2 legs SELL round-trip, conservative: STT 0.10% on sell premium notional +
    # txn+gst+sebi+stamp ~0.05% + ₹40 flat/leg + slippage ~₹1.5/leg (each side).
    notional = premium * LOT
    stt = 0.0010 * notional
    txn = 0.0006 * notional
    flat = 40.0
    slip = 1.5 * LOT
    return stt + txn + flat + slip

# ── data ──
_ivp = os.path.join(HERE, "..", "..", "data", "vrp_iv_history.json")
iv_raw = json.load(open(_ivp))
iv = pd.Series({pd.Timestamp(k): float(v) for k, v in iv_raw.items()}).sort_index()
iv = iv[(iv > 3) & (iv < 90)]

df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), usecols=["Datetime", "Close"])
df["Datetime"] = pd.to_datetime(df["Datetime"])
df["day"] = df.Datetime.dt.normalize()
df["hm"] = df.Datetime.dt.strftime("%H:%M")
# 9:20 entry price, 15:25 exit price per day
ent = df[df.hm == "09:20"].groupby("day").Close.last()
ext = df[df.hm.between("15:20", "15:29")].groupby("day").Close.last()

days = iv.index.intersection(ent.index).intersection(ext.index)
iv = iv.reindex(days); S0 = ent.reindex(days); ST = ext.reindex(days)
rank = iv.rolling(252, min_periods=20).apply(lambda w: (w.iloc[-1] >= w).mean(), raw=False) * 100

rows = []
for d in days:
    s0, st, ivd, rk = S0[d], ST[d], iv[d], rank[d]
    if not (s0 > 0 and st > 0 and ivd > 0):
        continue
    sig = s0 * (ivd / 100.0) * math.sqrt(DAY_T)     # 1-day expected move in points
    kce = round((s0 + sig) / 50) * 50
    kpe = round((s0 - sig) / 50) * 50
    prem = bs(s0, kce, ivd, DAY_T, True) + bs(s0, kpe, ivd, DAY_T, False)
    if prem <= 0:
        continue
    intrinsic = max(0.0, st - kce) + max(0.0, kpe - st)
    gross = (prem - intrinsic) * LOT
    net = gross - sell_charges(prem)
    rows.append({"day": d, "iv": ivd, "rank": rk, "sig": sig, "prem": prem,
                 "breach": intrinsic > 0, "gross": gross, "net": net})

R = pd.DataFrame(rows).set_index("day")
print(f"days traded: {len(R)}  span {R.index.min().date()} -> {R.index.max().date()}\n")

def report(sub, label):
    if len(sub) < 20:
        print(f"{label:<28} n={len(sub):<4} (too few)"); return
    net = sub.net
    wr = (net > 0).mean() * 100
    total = net.sum()
    avg = net.mean()
    worst = net.min(); best = net.max()
    p05 = net.quantile(0.05)
    sharpe = (net.mean() / net.std()) * math.sqrt(252) if net.std() > 0 else 0
    breach = sub.breach.mean() * 100
    # worst-5 sum vs total = how much the tail dominates
    tail5 = net.nsmallest(max(1, len(net)//20)).sum()
    print(f"{label:<28} n={len(sub):<4} win%={wr:4.0f}  net=₹{total:>10,.0f}  avg=₹{avg:>6,.0f}  "
          f"Sharpe={sharpe:5.2f}  worst=₹{worst:>8,.0f}  p5=₹{p05:>7,.0f}  breach%={breach:3.0f}  worst5%sum=₹{tail5:>9,.0f}")

print("── SELL ±1σ strangle @ 9:20, exit EOD (BS premium + real charges) ──")
report(R, "ALL days")
print()
for thr in (50, 60, 70, 80):
    report(R[R["rank"] >= thr], f"only IV-rank ≥ {thr}")
print()
# also: what if we cap the loss (SL) at 2x/3x credit — does capping the tail rescue it?
print("── with per-day loss cap (SL = k × credit collected) ──")
for k in (1.0, 2.0, 3.0):
    sub = R.copy()
    credit = sub.prem * LOT
    capped_gross = np.maximum(sub.gross, -k * credit)   # loss capped at k×credit
    capped_net = capped_gross - sub.prem.apply(sell_charges)
    n = len(sub); total = capped_net.sum(); avg = capped_net.mean()
    sh = (capped_net.mean()/capped_net.std())*math.sqrt(252) if capped_net.std()>0 else 0
    print(f"  SL {k:.0f}×credit        n={n:<4} net=₹{total:>10,.0f}  avg=₹{avg:>6,.0f}  Sharpe={sh:5.2f}  worst=₹{capped_net.min():>8,.0f}")
