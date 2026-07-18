"""Full F&O universe buy-the-dip — proper signals, exits, PORTFOLIO equity curve.

Delivers an actual result: an equal-weight "dip basket" portfolio (hold every
stock currently in a dip-entry, equal weight, cash when none) vs an equal-weight
Buy & Hold of the same universe. CAGR / maxDD / Sharpe, net of cost.

Signals (video-inspired):
  streak N : N consecutive down closes                       (Markov streak)
  drop  X  : 1-day return <= -X%                             ("opens/closes unusually low")
  zdrop k  : 1-day return <= -k * 20d stdev of returns       (abnormal drop)
Exits:
  hold H   : exit after H trading days   (captures multi-day reversion)
  upclose  : exit on first up close       (the video's crude exit)
Optional VIX filter: only enter when India VIX >= --vix-min.

Run: python stock_universe_bt.py --grid
     python stock_universe_bt.py --signal zdrop --k 1.5 --exit hold --H 3 --cost-bps 10
"""
import os
import sys
import glob
import math
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
EQ = os.path.join(HERE, "_equity", "Equity")
CACHE = os.path.join(HERE, "_equity", "_daily_cache")
VIX = r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\india_vix_daily.csv"


def stock_daily(sym):
    os.makedirs(CACHE, exist_ok=True)
    cpath = os.path.join(CACHE, f"{sym}.csv")
    if os.path.exists(cpath):
        return pd.read_csv(cpath, parse_dates=["Date"])
    rows = []
    for f in sorted(glob.glob(os.path.join(EQ, sym, f"{sym}_*.csv"))):
        try:
            # date from filename SYM_YYYY-MM-DD.csv; only read Close col (fast)
            dstr = os.path.basename(f)[len(sym) + 1:-4]
            d = pd.read_csv(f, usecols=["Close"])
            if d.empty:
                continue
            rows.append(dict(Date=pd.to_datetime(dstr), Close=d["Close"].iloc[-1]))
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["Date", "Close"])
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    df.to_csv(cpath, index=False)
    return df


def symbols():
    return sorted(d for d in os.listdir(EQ) if os.path.isdir(os.path.join(EQ, d)))


def entry_flags(c, signal, p):
    ret = np.r_[np.nan, c[1:] / c[:-1] - 1]
    if signal == "streak":
        down = np.r_[False, c[1:] < c[:-1]]
        run = np.zeros(len(c), int)
        for i in range(1, len(c)):
            run[i] = run[i - 1] + 1 if down[i] else 0
        return run >= p["N"]
    if signal == "drop":
        return ret <= -p["X"] / 100
    if signal == "zdrop":
        s = pd.Series(ret).rolling(20).std().values
        return ret <= -p["k"] * s
    # --- momentum (data says Indian daily = continuation) ---
    if signal == "mstreak":                        # N consecutive UP closes
        up = np.r_[False, c[1:] > c[:-1]]
        run = np.zeros(len(c), int)
        for i in range(1, len(c)):
            run[i] = run[i - 1] + 1 if up[i] else 0
        return run >= p["N"]
    if signal == "breakout":                       # new L-day high
        roll = pd.Series(c).rolling(p["L"]).max().values
        return c >= roll
    if signal == "pop":                            # +X% up-day, ride it
        return ret >= p["X"] / 100
    raise ValueError(signal)


def stock_series(df, signal, p, exit_mode, H, cost_bps, vix_ok=None, rev_dir="up"):
    """Return daily net-return Series (index=Date), value=ret while holding
    (cost-adjusted on transition days), NaN when flat. Enter next day (no look-ahead)."""
    c = df["Close"].values
    dates = df["Date"].values
    if len(c) < 25:
        return None
    ret = np.r_[np.nan, c[1:] / c[:-1] - 1]
    ent = entry_flags(c, signal, p)
    cside = cost_bps * 1e-4
    out = np.full(len(c), np.nan)
    i = 1
    while i < len(c):
        # signal at close i-1 -> hold starts day i
        if ent[i - 1] and (vix_ok is None or vix_ok.get(pd.Timestamp(dates[i - 1]), False)):
            start = i
            j = start
            while j < len(c):
                r = ret[j]
                out[j] = 0.0 if np.isnan(out[j]) else out[j]
                out[j] = r
                held = j - start + 1
                rev = (c[j] > c[j - 1]) if rev_dir == "up" else (c[j] < c[j - 1])
                exit_now = (held >= H) if exit_mode == "hold" else rev
                if j == start:
                    out[j] -= cside          # entry cost
                if exit_now or j == len(c) - 1:
                    out[j] -= cside          # exit cost
                    i = j + 1
                    break
                j += 1
            continue
        i += 1
    return pd.Series(out, index=pd.to_datetime(dates))


def run(syms, signal, p, exit_mode, H, cost_bps, vix_ok=None, rev_dir="up", window=None):
    cols = {}
    ntr = 0
    for s in syms:
        df = stock_daily(s)
        if window:
            df = df[(df["Date"] >= window[0]) & (df["Date"] <= window[1])].reset_index(drop=True)
        ser = stock_series(df, signal, p, exit_mode, H, cost_bps, vix_ok, rev_dir)
        if ser is None:
            continue
        cols[s] = ser
        # count trades = entry transitions
        held = ~ser.isna()
        ntr += int((held & ~held.shift(1, fill_value=False)).sum())
    if not cols:
        return None
    M = pd.DataFrame(cols).sort_index()
    port = M.mean(axis=1, skipna=True).fillna(0.0)          # active-only equal weight
    bh = M.notna()  # not used; benchmark below
    # benchmark: equal-weight buy&hold of all stocks (always invested)
    rets = {}
    for s in syms:
        df = stock_daily(s)
        if window:
            df = df[(df["Date"] >= window[0]) & (df["Date"] <= window[1])].reset_index(drop=True)
        if len(df) < 25:
            continue
        r = df.set_index("Date")["Close"].pct_change()
        rets[s] = r
    BH = pd.DataFrame(rets).sort_index().mean(axis=1, skipna=True).fillna(0.0)
    BH = BH.reindex(port.index).fillna(0.0)

    def curve(x):
        eq = (1 + x).cumprod()
        yrs = (x.index[-1] - x.index[0]).days / 365.25
        cagr = (eq.iloc[-1] ** (1 / yrs) - 1) * 100 if yrs > 0 else float("nan")
        dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        sh = (x[x != 0].mean() / (x[x != 0].std() or 1e-9)) * math.sqrt(252)
        expo = (x != 0).mean() * 100
        return eq.iloc[-1], cagr, dd, sh, expo

    tot, cagr, dd, sh, expo = curve(port)
    btot, bcagr, bdd, bsh, _ = curve(BH)
    return dict(signal=signal, p=p, exit=exit_mode, H=H, trades=ntr,
                tot=(tot - 1) * 100, cagr=cagr, dd=dd, sharpe=sh, expo=expo,
                bh_tot=(btot - 1) * 100, bh_cagr=bcagr, bh_dd=bdd, bh_sharpe=bsh,
                nstocks=len(cols))


def load_vix_ok(vmin):
    if vmin is None:
        return None
    v = pd.read_csv(VIX)
    v["Date"] = pd.to_datetime(v["Date"])
    return {d: (c >= vmin) for d, c in zip(v["Date"], v["Close"])}


def show(r):
    if not r:
        print("  (no result)"); return
    pd_ = ",".join(f"{k}={v}" for k, v in r["p"].items())
    print(f"  {r['signal']:>6}({pd_}) {r['exit']}/H{r['H']}: {r['trades']:>5} tr | "
          f"tot {r['tot']:>+7.1f}% | CAGR {r['cagr']:>+6.1f}% | DD {r['dd']:>6.1f}% | "
          f"Sharpe {r['sharpe']:>+5.2f} | expo {r['expo']:>4.1f}% | "
          f"[B&H tot {r['bh_tot']:+.1f}% CAGR {r['bh_cagr']:+.1f}% Sh {r['bh_sharpe']:+.2f}]")


def main():
    argv = sys.argv[1:]

    def opt(k, d, cast=float):
        return cast(argv[argv.index(k) + 1]) if k in argv else d

    cost = opt("--cost-bps", 10.0)
    vmin = opt("--vix-min", None, float) if "--vix-min" in argv else None
    vix_ok = load_vix_ok(vmin)
    syms = symbols()
    print(f"Universe: {len(syms)} stocks | cost {cost}bps/side"
          f"{f' | VIX>={vmin}' if vmin else ''}\n")

    if "--focus" in argv:
        # stress the winner: 50d breakout, hold 10d, momentum
        sig, p, em, H, rd = "breakout", {"L": 50}, "hold", 10, "down"
        print("WINNER stress: breakout(L=50) hold/H10 momentum\n")
        print("-- cost sensitivity --")
        for cb in [0, 5, 10, 15, 20]:
            r = run(syms, sig, p, em, H, cb, None, rd)
            print(f"  cost {cb:>2}bps:", end=" "); show(r)
        print("\n-- train / OOS split (first half vs second half of dates) --")
        # find global date span
        alld = []
        for s in syms[:30]:
            d = stock_daily(s)
            if len(d):
                alld += [d["Date"].min(), d["Date"].max()]
        d0, d1 = min(alld), max(alld)
        mid = d0 + (d1 - d0) / 2
        for tag, win in [("TRAIN", (d0, mid)), ("OOS  ", (mid, d1))]:
            r = run(syms, sig, p, em, H, 10, None, rd, window=win)
            print(f"  {tag} {win[0].date()}..{win[1].date()}:", end=" "); show(r)
    elif "--momentum" in argv:
        # data says Indian daily = continuation; RIDE strength, exit on trend break
        print("MOMENTUM (long strength). exit hold/H or revclose=first down close:")
        for sig, plist in [
            ("mstreak", [{"N": 2}, {"N": 3}, {"N": 4}]),
            ("breakout", [{"L": 10}, {"L": 20}, {"L": 50}]),
            ("pop", [{"X": 2}, {"X": 3}]),
        ]:
            for p in plist:
                for exit_mode, H in [("revclose", 1), ("hold", 3), ("hold", 5), ("hold", 10)]:
                    show(run(syms, sig, p, exit_mode, H, cost, vix_ok, rev_dir="down"))
            print()
    elif "--grid" in argv:
        print("signal(params) exit/H:  portfolio  vs  [equal-weight Buy&Hold]")
        for sig, plist in [
            ("streak", [{"N": 2}, {"N": 3}, {"N": 4}]),
            ("drop", [{"X": 2}, {"X": 3}, {"X": 4}]),
            ("zdrop", [{"k": 1.5}, {"k": 2.0}, {"k": 2.5}]),
        ]:
            for p in plist:
                for exit_mode, H in [("upclose", 1), ("hold", 2), ("hold", 3), ("hold", 5)]:
                    show(run(syms, sig, p, exit_mode, H, cost, vix_ok))
            print()
    else:
        sig = next((a for a in ["streak", "drop", "zdrop"] if a in argv), "zdrop")
        p = ({"N": opt("--N", 3, int)} if sig == "streak" else
             {"X": opt("--X", 3.0)} if sig == "drop" else {"k": opt("--k", 1.5)})
        show(run(syms, sig, p, ("hold" if "--exit" in argv and argv[argv.index("--exit")+1] == "hold" else
                                 argv[argv.index("--exit")+1] if "--exit" in argv else "hold"),
                 opt("--H", 3, int), cost, vix_ok))


if __name__ == "__main__":
    main()
