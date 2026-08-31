"""rsi_stocks_debit_spread.py — 05.02 RSI (Stocks): naked ATM BUY vs DEBIT SPREAD.

USER KA SAWAAL (2026-08-31): "05.02 naked sell kar rahe the, credit spread ki tarah karein to?"

PREMISE CORRECTION: 05.02 option BECHTI nahi, KHARIDTI hai.
  `strategies/live/01_rsi_v1.py`  L403 "RSI always enters BUY (buys the premium, CE or PE)"
  RSI<30 cross-up -> BUY ATM CE ; RSI>70 cross-down -> BUY ATM PE
Yaani ye DEBIT strategy hai. Credit spread lagane ka matlab poori strategy ULTI karna.
Jo user actually chahte the ("naked ki jagah spread, cost/risk kam") uska sahi roop =
**DEBIT SPREAD**: BUY ATM + SELL N-strike OTM (same type) -> becha hua leg theta ka bill
bharta hai.

Ye theek wahi transform hai jisne [[project_code3b_debit_spread_rescue]] me NIFTY ke
har directional signal ko loss->profit kiya (Chain-Zone naked -68,099 -> debit +1,38,671).

## LIVE SPEC (jo yahan reproduce ho raha hai — Rule 10)
  TF        : 5m  (config "2m" hai PAR TF_MAP me "2m" key nahi -> .get("2m",5) -> 5m;
                   memory project_code3b_rsi_tf_2m_runs_5m me live-verified)
  RSI       : Wilder 14
  entry     : RSI<30 ke baad >30 cross -> BUY CE ; RSI>70 ke baad <70 cross -> BUY PE
  exit      : CE open & RSI>=50  |  PE open & RSI<=50  |  15:10 force (FORCE_EXIT)
  strike    : ATM (strike_offset 0)
  pe_only   : TCS, INFY pe CE-buy BLOCK (config pe_only_symbols)

## DATA REALITY (imaandaari se — ye test poori strategy cover nahi karta)
  RSI 22 stocks trade karti hai; lake me sirf 10 stock hain aur unme se RSI ke saath
  overlap = 7 (ADANIENT AXISBANK HDFCBANK ICICIBANK INFY LT MARUTI).
  Stock lake ka window sirf ATM+-5 -> wing max 5 strikes (NIFTY pe wing-10 winner tha).
  Isliye: ye 7 naam ka REAL-premium evidence hai, poore 22-naam portfolio ka nahi.

## TRAP #198 guard
  `_px` out-of-window/khaali cell pe None deta hai, intrinsic NAHI. Jo trade ek baar bhi
  None chhue wo SKIP + coverage% report. Stock lake ka window % me chauda hai (strike step
  ~0.5-1% of spot, +-5 = +-3-5%) isliye NIFTY/BNF jaisi contamination expected nahi — par
  naapa jaata hai, maana nahi.
"""
import argparse
import datetime as dt
import math
import os
import re

import numpy as np
import pandas as pd

import bs_option as bs

HERE = os.path.dirname(os.path.abspath(__file__))
LAKE_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "_TRADING_DATA",
                                         "OptChainLake_1m"))
# RSI ke 22 symbols me se wahi jinka lake maujood hai
SYMBOLS = ["ADANIENT", "AXISBANK", "HDFCBANK", "ICICIBANK", "INFY", "LT", "MARUTI"]
PE_ONLY = {"TCS", "INFY"}          # config pe_only_symbols -> CE-buy blocked
FORCE_EXIT = dt.time(15, 10)
TF_MIN = 5                          # live effective TF
RSI_N, OS, OB, EXIT_LVL = 14, 30.0, 70.0, 50.0


def _tag(off):
    return "ATM" if off == 0 else ("ATMp%d" % off if off > 0 else "ATMm%d" % abs(off))


def wilder_rsi(close, n=RSI_N):
    d = np.diff(close, prepend=close[0])
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au = pd.Series(up).ewm(alpha=1.0 / n, adjust=False).mean().values
    ad = pd.Series(dn).ewm(alpha=1.0 / n, adjust=False).mean().values
    rs = np.divide(au, ad, out=np.full_like(au, np.inf), where=ad > 0)
    return 100.0 - 100.0 / (1.0 + rs)


def load_stock(sym):
    """Per-bar CE/PE premium grid + spot for one stock. Returns None if lake missing."""
    d = os.path.join(LAKE_ROOT, sym, "MONTH")
    if not os.path.isdir(d):
        return None
    have = set()
    for f in os.listdir(d):
        m = re.match(r"^CE_ATM(?:(p|m)(\d+))?\.csv$", f)
        if m:
            have.add(0 if not m.group(1)
                     else (int(m.group(2)) if m.group(1) == "p" else -int(m.group(2))))
    win = 0
    while (win + 1) in have and -(win + 1) in have:
        win += 1
    if win == 0:
        return None
    offs = list(range(-win, win + 1))
    base = None
    for side in ("CE", "PE"):
        for off in offs:
            p = os.path.join(d, "%s_%s.csv" % (side, _tag(off)))
            if not os.path.exists(p):
                return None
            df = pd.read_csv(p, usecols=["timestamp", "close", "strike", "spot"])
            col = "%s%s" % (side, _tag(off))
            keep = df[["timestamp", "close"]].rename(columns={"close": col})
            if side == "CE" and off == 0:
                keep = df[["timestamp", "close", "strike", "spot"]].rename(
                    columns={"close": col, "strike": "ATMK", "spot": "SPOT"})
            if side == "CE" and off == 1:
                keep = df[["timestamp", "close", "strike"]].rename(
                    columns={"close": col, "strike": "K1"})
            base = keep if base is None else base.merge(keep, on="timestamp", how="outer")
    base = base.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    base["Datetime"] = (pd.to_datetime(base.timestamp, unit="s", utc=True)
                        .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    tt = base.Datetime.dt.time
    base = base[(tt >= dt.time(9, 15)) & (tt <= dt.time(15, 29))].reset_index(drop=True)
    base["_d"] = base.Datetime.dt.date
    for c in ("ATMK", "SPOT", "K1"):
        base[c] = base.groupby("_d")[c].ffill().bfill()
    med = base.groupby("_d")["SPOT"].transform("median")
    base.loc[(base.SPOT - med).abs() / med > 0.15, "SPOT"] = np.nan
    base["SPOT"] = base.groupby("_d")["SPOT"].ffill().bfill()
    base = base.dropna(subset=["ATMK", "SPOT", "K1"]).reset_index(drop=True)
    step = float(pd.Series(base.K1 - base.ATMK).replace(0, np.nan).median())
    if not step or not np.isfinite(step) or step <= 0:
        return None
    CE = np.column_stack([base.get("CE%s" % _tag(o),
                                   pd.Series(np.nan, index=base.index)).values for o in offs])
    PE = np.column_stack([base.get("PE%s" % _tag(o),
                                   pd.Series(np.nan, index=base.index)).values for o in offs])
    return dict(sym=sym, CE=CE.astype(float), PE=PE.astype(float), STEP=step, WIN=win,
                ATMK=base.ATMK.values.astype(float), SPOT=base.SPOT.values.astype(float),
                DT=base.Datetime.values, DAY=base.Datetime.dt.date.values,
                TT=base.Datetime.dt.time.values)


def _px(g, i, side, K):
    """Real premium ya None — intrinsic KABHI nahi (TRAP #198)."""
    off = int(round((K - g["ATMK"][i]) / g["STEP"]))
    win = g["WIN"]
    if -win <= off <= win:
        v = (g["CE"] if side == "CE" else g["PE"])[i, off + win]
        if v and not np.isnan(v) and v > 0:
            return float(v)
    return None


def signals_5m(g):
    """5m RSI se (entry_bar_index, 'CE'|'PE') list + har 1-min bar ka running 5m RSI."""
    df = pd.DataFrame({"dt": pd.to_datetime(g["DT"]), "spot": g["SPOT"]})
    df["b"] = df.dt.dt.floor("%dmin" % TF_MIN)
    bars = df.groupby("b", sort=True).agg(close=("spot", "last")).reset_index()
    bars["rsi"] = wilder_rsi(bars.close.values)
    # bar CLOSE pe hi decision (live candle-close pe scan karta hai)
    bars["prev"] = bars.rsi.shift(1)
    bars["day"] = bars.b.dt.date
    sig = []
    for r in bars.itertuples():
        if r.prev != r.prev:
            continue
        if r.prev < OS <= r.rsi:
            sig.append((r.b, "CE"))
        elif r.prev > OB >= r.rsi:
            sig.append((r.b, "PE"))
    return bars, sig


def backtest(g, wing=0, max_per_sym=99):
    """wing=0 -> naked ATM BUY ; wing=N -> debit spread (BUY ATM + SELL N-strike OTM)."""
    sym = g["sym"]
    bars, sig = signals_5m(g)
    rsi_at = dict(zip(bars.b, bars.rsi))
    bar_of = pd.to_datetime(g["DT"]).floor("%dmin" % TF_MIN)
    DAY, TT = g["DAY"], g["TT"]
    n = len(DAY)
    # bar-close ka pehla 1-min index (entry usi ke baad hota hai)
    first_idx = {}
    for i in range(n):
        b = bar_of[i]
        if b not in first_idx:
            first_idx[b] = i
    last_bar_of_day = {}
    for i in range(n):
        last_bar_of_day[DAY[i]] = i

    rows, skipped, per_day = [], 0, {}
    open_until = -1
    for b, otype in sig:
        if otype == "CE" and sym.upper() in PE_ONLY:
            continue                                   # pe_only_symbols
        nxt = b + pd.Timedelta(minutes=TF_MIN)
        e = first_idx.get(nxt)
        if e is None or e <= open_until:
            continue
        d0 = DAY[e]
        if TT[e] >= FORCE_EXIT:
            continue
        per_day[(sym, d0)] = per_day.get((sym, d0), 0)
        if per_day[(sym, d0)] >= max_per_sym:
            continue
        atmk = round(g["SPOT"][e] / g["STEP"]) * g["STEP"]
        kw = atmk + wing * g["STEP"] if otype == "CE" else atmk - wing * g["STEP"]
        buy = _px(g, e, otype, atmk)
        sell = _px(g, e, otype, kw) if wing else 0.0
        if buy is None or sell is None:
            skipped += 1
            continue
        debit = buy - sell
        if debit <= 0:
            skipped += 1
            continue

        x, reason, dirty = None, "eod", False
        for i in range(e + 1, last_bar_of_day[d0] + 1):
            if TT[i] >= FORCE_EXIT:
                x, reason = i, "eod"
                break
            r = rsi_at.get(bar_of[i])
            if r is None or r != r:
                continue
            if otype == "CE" and r >= EXIT_LVL:
                x, reason = i, "rsi50"
                break
            if otype == "PE" and r <= EXIT_LVL:
                x, reason = i, "rsi50"
                break
        if x is None:
            x = last_bar_of_day[d0]
        xb = _px(g, x, otype, atmk)
        xs = _px(g, x, otype, kw) if wing else 0.0
        if xb is None or xs is None:
            skipped += 1
            continue
        per_day[(sym, d0)] += 1
        open_until = x
        pts = (xb - xs) - debit                        # long spread: exit - entry
        rows.append(dict(sym=sym, day=d0, edt=g["DT"][e], otype=otype, wing=wing,
                         debit=debit, pts=pts, reason=reason,
                         buy_in=buy, buy_out=xb, sell_in=sell, sell_out=xs))
    df = pd.DataFrame(rows)
    df.attrs["skipped"] = skipped
    return df


def summarise(df, label, lot, lots):
    sk = df.attrs.get("skipped", 0)
    tot = len(df) + sk
    cov = 100.0 * len(df) / tot if tot else 0.0
    if len(df) < 20:
        print("  %-26s n=%-4d (bahut kam)  cov=%.0f%%" % (label, len(df), cov))
        return None
    qty = lot * lots
    gross = df.pts * qty
    # 2 legs jab wing>0 (dono pe charges), warna 1
    fee = df.apply(lambda r: bs.calc_charges(r.buy_in, r.buy_out, qty, entry_side="BUY",
                                            when=pd.Timestamp(r.edt))
                   + (bs.calc_charges(r.sell_in, r.sell_out, qty, entry_side="SELL",
                                      when=pd.Timestamp(r.edt)) if r.wing else 0.0), axis=1)
    slip = df.apply(lambda r: bs.slip_cost_leg(r.buy_in, r.buy_out, qty)
                    + (bs.slip_cost_leg(r.sell_in, r.sell_out, qty) if r.wing else 0.0), axis=1)
    net = gross - fee - slip
    yrs = max((pd.Timestamp(df.day.max()) - pd.Timestamp(df.day.min())).days / 365.25, .25)
    sh = (net.mean() / net.std() * math.sqrt(len(net) / yrs)) if net.std() else 0
    pf = net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else float("inf")
    eq = net.cumsum()
    dd = (eq - eq.cummax()).min()
    print("  %-26s n=%-4d cov=%3.0f%% | %+7.2f pt | Rs%-11s Sh=%6.2f PF=%5.2f win=%4.1f%% "
          "DD=Rs%-10s avg debit %.1f"
          % (label, len(net), cov, net.mean() / qty, format(int(net.sum()), ","), sh, pf,
             100 * (net > 0).mean(), format(int(dd), ","), df.debit.mean()))
    return dict(net=net, df=df, sh=sh)


def perm_p(net, iters=5000, seed=7):
    if len(net) < 20:
        return float("nan")
    rng = np.random.default_rng(seed)
    v = np.asarray(net)
    return float(((v * rng.choice([-1, 1], size=(iters, len(v)))).mean(axis=1) >= v.mean()).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lots", type=int, default=3)      # config qty = 3
    ap.add_argument("--max-per-sym", type=int, default=99)
    a = ap.parse_args()

    grids = {}
    for s in SYMBOLS:
        g = load_stock(s)
        if g is None:
            print("  [skip] %s — lake incomplete" % s)
            continue
        grids[s] = g
    print("=" * 122)
    print("05.02 RSI (Stocks): NAKED ATM BUY vs DEBIT SPREAD — REAL premium, %d lot"
          % a.lots)
    print("stocks: %s" % ", ".join(sorted(grids)))
    d0 = min(g["DAY"][0] for g in grids.values())
    d1 = max(g["DAY"][-1] for g in grids.values())
    print("window: %s -> %s   (live spec: 5m RSI-14, 30/70 entry, RSI-50 exit, 15:10 EOD)"
          % (d0, d1))
    print("=" * 122)

    # LOT SIZE: scrip master se, aur na mile to LOUD ABORT — 1 pe girna hi TRAP #197
    # wali galti hai (brokerage Rs20/order FLAT hai, to galat qty poore result ko
    # charges ke neeche daba deta hai: qty=3 pe Rs40 charges vs Rs1.6 gross).
    import sys
    _root = os.path.abspath(os.path.join(HERE, "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    import _paths  # noqa: F401  (sys.path bootstrap)
    from _data import dhan_master
    lots_map = {}
    for s in grids:
        r = dhan_master.get_option_contract(s, float(grids[s]["SPOT"][-1]), "CE")
        if not r or not r[2]:
            raise SystemExit("LOT SIZE nahi mila: %s — scrip master check karo. "
                             "1 pe fallback NAHI karunga (TRAP #197)." % s)
        lots_map[s] = int(r[2])
    print("lot sizes: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(lots_map.items())))

    results = {}
    for wing in (0, 1, 2, 3, 4, 5):
        allnet, alln, allsk, frames = [], 0, 0, []
        for s, g in grids.items():
            df = backtest(g, wing=wing, max_per_sym=a.max_per_sym)
            allsk += df.attrs.get("skipped", 0)
            if len(df):
                frames.append((s, df))
        if not frames:
            continue
        # per-stock summarise then pool (lot sizes differ -> pool in Rs)
        pooled = []
        for s, df in frames:
            qty = lots_map.get(s, 1) * a.lots
            gross = df.pts * qty
            fee = df.apply(lambda r: bs.calc_charges(r.buy_in, r.buy_out, qty,
                                                     entry_side="BUY", when=pd.Timestamp(r.edt))
                           + (bs.calc_charges(r.sell_in, r.sell_out, qty, entry_side="SELL",
                                              when=pd.Timestamp(r.edt)) if r.wing else 0.0), axis=1)
            slip = df.apply(lambda r: bs.slip_cost_leg(r.buy_in, r.buy_out, qty)
                            + (bs.slip_cost_leg(r.sell_in, r.sell_out, qty) if r.wing else 0.0),
                            axis=1)
            t = df.copy()
            t["net"] = gross - fee - slip
            t["stock"] = s
            pooled.append(t)
        P = pd.concat(pooled, ignore_index=True).sort_values("edt")
        cov = 100.0 * len(P) / max(1, len(P) + allsk)
        net = P.net
        yrs = max((pd.Timestamp(P.day.max()) - pd.Timestamp(P.day.min())).days / 365.25, .25)
        sh = (net.mean() / net.std() * math.sqrt(len(net) / yrs)) if net.std() else 0
        pf = net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else float("inf")
        eq = net.cumsum()
        dd = (eq - eq.cummax()).min()
        lbl = "NAKED ATM BUY" if wing == 0 else "DEBIT SPREAD wing-%d" % wing
        print("  %-22s n=%-5d cov=%3.0f%% | Rs%-12s Sh=%6.2f PF=%5.2f win=%4.1f%% "
              "DD=Rs%-11s p=%.4f"
              % (lbl, len(net), cov, format(int(net.sum()), ","), sh, pf,
                 100 * (net > 0).mean(), format(int(dd), ","), perm_p(net.values)))
        results[wing] = P

    # best wing ka train/OOS
    if results:
        best = max(results, key=lambda w: results[w].net.sum())
        P = results[best].sort_values("day").reset_index(drop=True)
        k = int(len(P) * 0.65)
        tr, oo = P.iloc[:k], P.iloc[k:]
        print("\n>> Train/OOS (65/35 by date) — best = %s"
              % ("naked" if best == 0 else "wing-%d" % best))
        for nm, s in (("TRAIN", tr), ("OOS", oo)):
            net = s.net
            yrs = max((pd.Timestamp(s.day.max()) - pd.Timestamp(s.day.min())).days / 365.25, .25)
            sh = (net.mean() / net.std() * math.sqrt(len(net) / yrs)) if net.std() else 0
            print("   %-6s n=%-5d net=Rs%-12s Sh=%6.2f" % (nm, len(net),
                                                           format(int(net.sum()), ","), sh))
        print("\n>> Per-stock (best structure)")
        for s, grp in P.groupby("stock"):
            print("   %-11s n=%-4d net=Rs%-11s win=%4.1f%%"
                  % (s, len(grp), format(int(grp.net.sum()), ","), 100 * (grp.net > 0).mean()))

    print("\n  ! SCOPE: 7/22 symbols (lake), stock window ATM+-5 (NIFTY winner wing-10 tha).")
    print("    Ye in 7 naam ka real-premium evidence hai, poore portfolio ka nahi.")


if __name__ == "__main__":
    main()
