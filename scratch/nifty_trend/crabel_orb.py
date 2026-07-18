"""crabel_orb.py — Toby Crabel's Opening-Range-Breakout (ORB) on NIFTY.

Faithful port of the strategy from Crabel's "Day Trading With Short Term Price
Patterns and Opening Range Breakout" (1990), the one the video walks through.
Ported to the Indian index (NIFTY) using our 1-min data-lake (2018 -> 2026).

CRABEL'S RULES (exactly as stated in the video), all no-look-ahead:
  1. ARM tomorrow if TODAY is a "quiet" day (any one):
       NR4        : today's range is narrowest of last 4 days
       NR7        : today's range is narrowest of last 7 days
       inside-day : today's High<=yday High AND today's Low>=yday Low
  2. STRETCH = 10-day SMA of  min(High-Open, Open-Low)   (the smaller poke past open)
  3. On an armed day: buy-stop  = Open + stretch
                      sell-stop = Open - stretch
     First stop touched wins (OCO — the other cancels). Direction chosen by market.
  4. STOP-LOSS = the opposite band. So stop distance = 2*stretch = "risk per trade".
  5. 10:30 CUTOFF: if neither stop is touched by 10:30, walk away (no trade).
  6. Exit at the CLOSE if still in (no overnight, ever).
  7. FILL logic: a stop fills at the WORSE of {band, bar's open} — if price gaps
     straight through the level you fill at the gap, not the level. (real slippage)

  Signals (arm + stretch) are computed from days that ALREADY CLOSED -> shifted 1d.

Two modes (both in the book / video):
  bidirectional : place both stops every armed day
  trend50       : 50-day SMA filter — above it only the buy-stop, below only sell-stop

Cost model (video's own): commission 1bp + slippage 2bp PER FILL, round-trip = 2 fills.
A configurable fraction of slippage fills adverse vs favourable ("to be fair").

R-multiple (sizing-INDEPENDENT, the metric that decides edge):
  R = stop distance = 2*stretch.  net_R per trade = (exit-entry)*dir / (2*stretch) - cost_R
  Sum(net_R) > 0 after cost  == the pattern has a real edge on NIFTY. Everything
  else (equity curve, CAGR) is a sizing choice layered on top.

Run:  python crabel_orb.py
"""
import os
import datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- config ----
CUTOFF   = dt.time(10, 30)   # Crabel: edge strongest right after open
SESS_LO  = dt.time(9, 15)
SESS_HI  = dt.time(15, 29)   # NIFTY close (analog of US 16:00)
STRETCH_LB = 10
TREND_LB   = 50
RISK_PCT   = 0.01            # fractional-risk sizing for the equity curve
START_CAP  = 1_000_000.0
COMM_BP    = 1.0            # commission per fill (video)
SLIP_BP    = 2.0            # slippage per fill  (video)
ADVERSE_FRAC = 0.5          # fraction of the slip that is genuinely adverse


# ---------- data ----------
def load_1m(csv="nifty_1min.csv"):
    df = pd.read_csv(os.path.join(HERE, csv), parse_dates=["Datetime"])
    t = df.Datetime.dt.time
    df = df[(t >= SESS_LO) & (t <= SESS_HI)].reset_index(drop=True)
    df["day"] = df.Datetime.dt.date
    return df


def daily_from_1m(df1m):
    g = df1m.groupby("day")
    d = pd.DataFrame({
        "Open":  g.Open.first(),
        "High":  g.High.max(),
        "Low":   g.Low.min(),
        "Close": g.Close.last(),
    }).reset_index()
    return d


# ---------- signals (all shifted 1 day -> apply to NEXT session) ----------
def build_signals(daily):
    d = daily.copy()
    rng = d.High - d.Low
    # NR4 / NR7 : today's range is the min of the trailing window ending today
    d["nr4"] = rng == rng.rolling(4).min()
    d["nr7"] = rng == rng.rolling(7).min()
    d["inside"] = (d.High <= d.High.shift(1)) & (d.Low >= d.Low.shift(1))
    d["armed_today"] = d.nr4 | d.nr7 | d.inside
    d["stretch_today"] = np.minimum(d.High - d.Open, d.Open - d.Low).rolling(STRETCH_LB).mean()
    d["sma50"] = d.Close.rolling(TREND_LB).mean()
    # shift so a session only uses info known BEFORE it opens
    d["armed"]   = d.armed_today.shift(1).fillna(False).astype(bool)
    d["stretch"] = d.stretch_today.shift(1)
    d["trend_up"] = (d.Close.shift(1) > d.sma50.shift(1))
    return d


# ---------- one trading day ----------
def simulate_day(bars, stretch, mode, trend_up):
    """bars: 1-min bars for one session (sorted). Returns trade dict or None."""
    o = bars.Open.iloc[0]
    buy_lvl  = o + stretch
    sell_lvl = o - stretch
    allow_long  = (mode == "bidir") or (mode == "trend50" and trend_up)
    allow_short = (mode == "bidir") or (mode == "trend50" and (not trend_up))

    entry = None  # (dir, fill_price, time)
    b = bars[bars.Datetime.dt.time <= CUTOFF]
    for _, r in b.iterrows():
        # buy-stop: triggers if the bar's HIGH reaches the level.
        # fill = worse of {level, bar open}  (gap-through fills at the gap)
        hit_long  = allow_long  and r.High >= buy_lvl
        hit_short = allow_short and r.Low  <= sell_lvl
        if hit_long and hit_short:
            # both in same bar — can't know order intrabar; skip (conservative, rare)
            return None
        if hit_long:
            fill = max(buy_lvl, r.Open)           # worse for a buy = higher
            entry = (1, fill, r.Datetime); break
        if hit_short:
            fill = min(sell_lvl, r.Open)          # worse for a sell = lower
            entry = (-1, fill, r.Datetime); break
    if entry is None:
        return None

    d, fill, etime = entry
    stop_lvl = sell_lvl if d == 1 else buy_lvl    # opposite band
    # walk the rest of the session for stop or close
    after = bars[bars.Datetime > etime]
    exit_px, exit_reason = bars.Close.iloc[-1], "close"
    for _, r in after.iterrows():
        if d == 1 and r.Low <= stop_lvl:
            exit_px = min(stop_lvl, r.Open); exit_reason = "stop"; break
        if d == -1 and r.High >= stop_lvl:
            exit_px = max(stop_lvl, r.Open); exit_reason = "stop"; break
    return {"dir": d, "entry": fill, "exit": exit_px, "reason": exit_reason,
            "stretch": stretch, "etime": etime}


# ---------- cost ----------
def cost_points(price, rng):
    """round-trip cost in index points. comm+slip per fill, x2 fills.
    adverse fraction of slip hurts, the rest is neutral-ish (favourable cancels)."""
    per_fill_bp = COMM_BP + SLIP_BP * (2 * ADVERSE_FRAC)  # expected adverse slip
    return price * (per_fill_bp / 1e4) * 2


# ---------- backtest ----------
def backtest(mode="bidir", with_cost=True, start=None, end=None):
    df1m = load_1m()
    daily = daily_from_1m(df1m)
    sig = build_signals(daily).set_index("day")
    by_day = {d: g for d, g in df1m.groupby("day")}

    equity = START_CAP
    rows = []
    for day, bars in by_day.items():
        s = sig.loc[day] if day in sig.index else None
        if s is None or not s.armed or pd.isna(s.stretch) or s.stretch <= 0:
            continue
        if start and day < start: continue
        if end and day > end: continue
        tr = simulate_day(bars.sort_values("Datetime"), float(s.stretch),
                          mode, bool(s.trend_up))
        if tr is None:
            continue
        gross_pts = (tr["exit"] - tr["entry"]) * tr["dir"]
        cst = cost_points(tr["entry"], None) if with_cost else 0.0
        net_pts = gross_pts - cst
        R = 2 * tr["stretch"]
        net_R = net_pts / R
        # fractional-risk sizing on the equity curve
        qty = max(1.0, (RISK_PCT * equity) / R)
        pnl_rs = net_pts * qty
        equity += pnl_rs
        rows.append({"day": day, "dir": tr["dir"], "reason": tr["reason"],
                     "entry": tr["entry"], "exit": tr["exit"], "stretch": tr["stretch"],
                     "gross_pts": gross_pts, "net_pts": net_pts, "net_R": net_R,
                     "pnl_rs": pnl_rs, "equity": equity, "etime": tr["etime"]})
    return pd.DataFrame(rows), daily


# ---------- metrics ----------
def metrics(tr, daily):
    if tr.empty:
        return {"trades": 0}
    n = len(tr)
    wins = (tr.net_R > 0).sum()
    eq = tr.equity.values
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    ret_tot = eq[-1] / START_CAP - 1
    days = (tr.day.iloc[-1] - tr.day.iloc[0]).days or 1
    yrs = days / 365.25
    cagr = (eq[-1] / START_CAP) ** (1 / yrs) - 1 if yrs > 0 else 0
    # per-trade R sharpe (sizing-independent)
    r = tr.net_R.values
    sharpe_r = (r.mean() / r.std() * np.sqrt(252 * n / max(1, (tr.day.nunique())))) if r.std() > 0 else 0
    # buy&hold over same window
    dd_idx = daily.set_index("day")
    bh = dd_idx.loc[tr.day.iloc[-1]].Close / dd_idx.loc[tr.day.iloc[0]].Close - 1
    return {
        "trades": n, "win%": round(100 * wins / n, 1),
        "sum_R": round(r.sum(), 1), "avg_R": round(r.mean(), 4),
        "net_%": round(100 * ret_tot, 1), "cagr_%": round(100 * cagr, 1),
        "maxDD_%": round(100 * dd.min(), 1), "sharpe_R": round(sharpe_r, 2),
        "buyhold_%": round(100 * bh, 1),
        "long": int((tr.dir == 1).sum()), "short": int((tr.dir == -1).sum()),
        "stop_exits": int((tr.reason == "stop").sum()),
    }


if __name__ == "__main__":
    print(f"{'='*70}\nCRABEL ORB on NIFTY  (2018-2026, 1-min lake)\n{'='*70}")
    for mode in ("bidir", "trend50"):
        for wc in (False, True):
            tr, daily = backtest(mode=mode, with_cost=wc)
            m = metrics(tr, daily)
            tag = "WITH cost (1bp comm + 2bp slip/fill)" if wc else "NO cost (gross)"
            print(f"\n--- {mode.upper():8s} | {tag} ---")
            for k, v in m.items():
                print(f"   {k:12s}: {v}")
