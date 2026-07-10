"""Intraday multi-design engine — NIFTY spot, 1x (no leverage), long+short.
House rules: force-exit 15:15, no entry >=15:15, max 2 trades/day, no overnight.

Resamples the 1-min data-lake to any TF (1m/3m/5m/15m). Ships several ENTRY
DESIGNS (not just param variants) so the edge hunt can compare structurally
different ideas, each gated by the same significance test downstream.

Designs:
  rsi_rev    mean-reversion : RSI turns up from oversold (long) / down from ob
  bb_fade    mean-reversion : close pokes outside Bollinger band -> fade back
  sess_rev   mean-reversion : z-score vs session-anchored average price (VWAP proxy, volume-free)
  orb        breakout       : opening-range (first N min) high/low break
  donchian   breakout       : N-bar high/low break
  supertrend trend          : Supertrend flip
Exit styles: 'atr_rr' (ATR stop + RR target), 'trail' (ATR trailing), plus EOD/opp-signal.
"""
import numpy as np
import pandas as pd
import datetime as dt
import engine  # reuse ema/atr/bollinger/metrics + load helpers
import expiry_calendar as _xcal  # official NIFTY expiry-weekday schedule (policy-A skip-expiry)

RISK_PCT = 0.015
START_CAP = 1_000_000.0
LEV_CAP = 1.0
EXIT_HM = dt.time(15, 15)
TF_RULE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min"}


# ---------- data ----------
def load_1m(csv="nifty_1min.csv"):
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    return pd.read_csv(os.path.join(here, csv), parse_dates=["Datetime"])


def resample(df1m, tf):
    if tf == "1m":
        d = df1m.copy()
    else:
        s = df1m.set_index("Datetime")
        d = (s.resample(TF_RULE[tf], label="left", closed="left")
               .agg({"Open": "first", "High": "max", "Low": "min",
                     "Close": "last", "Volume": "sum"}).dropna().reset_index())
    t = d.Datetime.dt.time
    d = d[(t >= dt.time(9, 15)) & (t <= dt.time(15, 29))].reset_index(drop=True)
    d["day"] = d.Datetime.dt.date
    d["is_eod"] = d.Datetime.dt.time >= EXIT_HM
    d["late"] = d.Datetime.dt.time >= EXIT_HM
    return d


# ---------- indicators ----------
def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).fillna(50)


def supertrend(df, period, mult):
    a = engine.atr(df, period)
    hl2 = (df.High + df.Low) / 2
    up = hl2 - mult*a; dn = hl2 + mult*a
    n = len(df); st = np.zeros(n); dir_ = np.ones(n)
    up_v, dn_v = up.values, dn.values; close = df.Close.values
    fup = up_v.copy(); fdn = dn_v.copy()
    for i in range(1, n):
        fup[i] = max(up_v[i], fup[i-1]) if close[i-1] > fup[i-1] else up_v[i]
        fdn[i] = min(dn_v[i], fdn[i-1]) if close[i-1] < fdn[i-1] else dn_v[i]
        if close[i] > fdn[i-1]:
            dir_[i] = 1
        elif close[i] < fup[i-1]:
            dir_[i] = -1
        else:
            dir_[i] = dir_[i-1]
        st[i] = fup[i] if dir_[i] == 1 else fdn[i]
    return pd.Series(dir_, index=df.index)


# ---------- designs: return (long_entry, short_entry) boolean arrays ----------
def design_signals(d, name, p):
    C, H, L, O = d.Close, d.High, d.Low, d.Open
    if name == "rsi_rev":
        r = rsi(C, int(p.get("rsi_len", 14)))
        os_, ob = p.get("os", 30), p.get("ob", 70)
        long = (r.shift(1) < os_) & (r > r.shift(1))
        short = (r.shift(1) > ob) & (r < r.shift(1))
    elif name == "bb_fade":
        up, mid, lo = engine.bollinger(C, int(p.get("bb_period", 20)), p.get("bb_dev", 2.0))
        long = (C < lo) & (C.shift(1) >= lo.shift(1))
        short = (C > up) & (C.shift(1) <= up.shift(1))
    elif name == "sess_rev":
        typ = (H + L + C) / 3
        cummean = typ.groupby(d.day).transform(lambda x: x.expanding().mean())
        cumstd = typ.groupby(d.day).transform(lambda x: x.expanding().std().bfill())
        z = (C - cummean) / cumstd.replace(0, np.nan)
        zt = p.get("zt", 2.0)
        long = z < -zt
        short = z > zt
    elif name == "orb":
        orm = int(p.get("or_min", 15))
        k = p.get("orb_k", 0.0)          # breakout must exceed OR by k x ATR (strength)
        a = engine.atr(d, 14)
        cutoff = (pd.to_datetime(d.Datetime).dt.time <=
                  (dt.datetime.combine(dt.date.today(), dt.time(9, 15)) +
                   dt.timedelta(minutes=orm)).time())
        orh = H.where(cutoff).groupby(d.day).transform("max") + k * a
        orl = L.where(cutoff).groupby(d.day).transform("min") - k * a
        after = ~cutoff
        long = after & (C > orh) & (C.shift(1) <= orh)
        short = after & (C < orl) & (C.shift(1) >= orl)
        if p.get("trend_filter"):        # only breakouts in the day's direction
            dopen = d.Open.groupby(d.day).transform("first")
            long = long & (C > dopen)
            short = short & (C < dopen)
    elif name == "donchian":
        n = int(p.get("dc", 20))
        hh = H.rolling(n).max().shift(1); ll = L.rolling(n).min().shift(1)
        long = C > hh
        short = C < ll
    elif name == "supertrend":
        dir_ = supertrend(d, int(p.get("st_period", 10)), p.get("st_mult", 3.0))
        long = (dir_ == 1) & (dir_.shift(1) == -1)
        short = (dir_ == -1) & (dir_.shift(1) == 1)
    elif name == "orb_st":                      # ORB breakout confirmed by Supertrend direction
        orm = int(p.get("or_min", 30)); k = p.get("orb_k", 1.0); a = engine.atr(d, 14)
        cutoff = (pd.to_datetime(d.Datetime).dt.time <=
                  (dt.datetime.combine(dt.date.today(), dt.time(9, 15)) +
                   dt.timedelta(minutes=orm)).time())
        orh = H.where(cutoff).groupby(d.day).transform("max") + k * a
        orl = L.where(cutoff).groupby(d.day).transform("min") - k * a
        after = ~cutoff
        dir_ = supertrend(d, int(p.get("st_period", 10)), p.get("st_mult", 3.0))
        long = after & (C > orh) & (C.shift(1) <= orh) & (dir_ == 1)
        short = after & (C < orl) & (C.shift(1) >= orl) & (dir_ == -1)
    elif name == "gap_fade":                    # fade a large opening gap back toward prev close
        a = engine.atr(d, 14)
        is_first = d.day != d.day.shift(1)
        day_open = O.groupby(d.day).transform("first")
        daily_last = d.groupby("day").Close.last()
        prev_close = d.day.map(daily_last.shift(1))
        thr = p.get("gap_k", 0.5)
        gap = day_open - prev_close
        long = is_first & (gap < -thr * a)      # gap-down -> fade up (long)
        short = is_first & (gap > thr * a)       # gap-up   -> fade down (short)
    elif name == "tod_orb":                     # ORB but entries only inside a time window
        orm = int(p.get("or_min", 30)); k = p.get("orb_k", 1.0); a = engine.atr(d, 14)
        cutoff = (pd.to_datetime(d.Datetime).dt.time <=
                  (dt.datetime.combine(dt.date.today(), dt.time(9, 15)) +
                   dt.timedelta(minutes=orm)).time())
        orh = H.where(cutoff).groupby(d.day).transform("max") + k * a
        orl = L.where(cutoff).groupby(d.day).transform("min") - k * a
        tt = pd.to_datetime(d.Datetime).dt.time
        win = (tt >= dt.time(int(p.get("h0", 10)), 0)) & (tt <= dt.time(int(p.get("h1", 13)), 0))
        after = ~cutoff & win
        long = after & (C > orh) & (C.shift(1) <= orh)
        short = after & (C < orl) & (C.shift(1) >= orl)
    else:
        raise ValueError(name)
    return long.fillna(False).values, short.fillna(False).values


DESIGN_GRID = {
    "rsi_rev":   dict(rsi_len=[7, 10, 14, 21], os=[20, 25, 30, 35], ob=[65, 70, 75, 80], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "bb_fade":   dict(bb_period=[14, 20, 30], bb_dev=[1.8, 2.0, 2.5], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "sess_rev":  dict(zt=[1.5, 2.0, 2.5, 3.0], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "orb":       dict(or_min=[15, 30, 45, 60], orb_k=[0.5, 1.0, 1.5, 2.0],
                      atr_sl=[1.5, 2.0, 2.5, 3.0], rr=[1.5, 2.0, 2.5, 3.0]),
    "donchian":  dict(dc=[10, 20, 40, 55], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "supertrend":dict(st_period=[7, 10, 14], st_mult=[2.0, 3.0, 4.0], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "orb_st":    dict(or_min=[30, 45, 60], orb_k=[0.5, 1.0, 1.5], st_period=[7, 10, 14],
                      st_mult=[2.0, 3.0], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "gap_fade":  dict(gap_k=[0.3, 0.5, 0.8, 1.2], atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
    "tod_orb":   dict(or_min=[15, 30], orb_k=[0.5, 1.0, 1.5], h0=[10, 11], h1=[13, 14],
                      atr_sl=[1.5, 2.0, 2.5], rr=[1.5, 2.0, 2.5]),
}
DEFAULTS = {k: {kk: vv[len(vv)//2] for kk, vv in g.items()} for k, g in DESIGN_GRID.items()}


# ---------- backtest ----------
def backtest(d, name, params=None, exit_style="atr_rr", rms_caps=None):
    """rms_caps = None (clean) OR dict(loss_cap=₹, profit_target=₹) to simulate the
    account-level RMS overlay: once the day's P&L crosses -loss_cap or +profit_target,
    the open position is squared off and no new entry is taken for the rest of that day."""
    p = dict(DEFAULTS[name]); p.update(params or {})
    long_e, short_e = design_signals(d, name, p)
    a = engine.atr(d, int(p.get("atr_period", 14))).values
    O, H, L, C = d.Open.values, d.High.values, d.Low.values, d.Close.values
    DAY = d.day.values; LATE = d.late.values; EOD = d.is_eod.values; DT = d.Datetime.values
    n = len(d)
    # policy A (0DTE-inflation guard, KNOWN_ISSUES #1): skip NEW entries on the correct
    # weekly-expiry day. Default ON — same standing decision as option_structures. The BS
    # reprice already prices T off the correct weekday (expiry_calendar); skipping the entry
    # keeps the directional path consistent with #1/#2. Pass skip_expiry=False to disable.
    skip_exp = bool(p.get("skip_expiry", True))
    _dates = pd.to_datetime(d.Datetime).dt.date.values
    EXP = np.array([dd_.weekday() == _xcal.weekly_expiry_weekday(dd_) for dd_ in _dates])
    warm = 60
    equity = START_CAP; pos = None; trades = []; eq_curve = []
    cur_day = None; trades_today = 0
    day_realized = 0.0; day_locked = False
    atr_sl = p.get("atr_sl", 2.0); rr = p.get("rr", 2.0); trail_k = p.get("trail_atr", 2.0)
    LOSS_CAP = (rms_caps or {}).get("loss_cap"); PROFIT_TGT = (rms_caps or {}).get("profit_target")

    def close_pos(i, price, reason):
        nonlocal equity, pos, day_realized
        sgn = 1 if pos["side"] == "long" else -1
        pnl = (price - pos["entry"]) * pos["qty"] * sgn
        fee = 40.0 + abs(price * pos["qty"]) * 0.0002
        equity += pnl - fee
        day_realized += pnl - fee
        trades.append(dict(side=pos["side"], entry=pos["entry"], exit=price, qty=pos["qty"],
                           pnl=pnl - fee, points=(price - pos["entry"]) * sgn,
                           entry_i=pos["entry_i"], exit_i=i, entry_dt=pos["entry_dt"],
                           exit_dt=DT[i], reason=reason, bars=i - pos["entry_i"]))
        pos = None

    for i in range(warm, n):
        if DAY[i] != cur_day:
            cur_day = DAY[i]; trades_today = 0; day_realized = 0.0; day_locked = False

        if pos is not None:
            side = pos["side"]
            if exit_style == "trail":
                if side == "long":
                    pos["stop"] = max(pos["stop"], C[i] - trail_k * a[i])
                    if L[i] <= pos["stop"]:
                        close_pos(i, pos["stop"], "Trail SL")
                    elif pos.get("target") and H[i] >= pos["target"]:
                        close_pos(i, pos["target"], "Target")
                else:
                    pos["stop"] = min(pos["stop"], C[i] + trail_k * a[i])
                    if H[i] >= pos["stop"]:
                        close_pos(i, pos["stop"], "Trail SL")
                    elif pos.get("target") and L[i] <= pos["target"]:
                        close_pos(i, pos["target"], "Target")
            elif exit_style == "stop_only":   # ATR stop, else ride to EOD (capture full move)
                if side == "long":
                    if L[i] <= pos["stop"]:
                        close_pos(i, pos["stop"], "ATR SL")
                else:
                    if H[i] >= pos["stop"]:
                        close_pos(i, pos["stop"], "ATR SL")
            else:  # atr_rr bracket
                if side == "long":
                    if L[i] <= pos["stop"]:
                        close_pos(i, pos["stop"], "ATR SL")
                    elif H[i] >= pos["target"]:
                        close_pos(i, pos["target"], "Target")
                else:
                    if H[i] >= pos["stop"]:
                        close_pos(i, pos["stop"], "ATR SL")
                    elif L[i] <= pos["target"]:
                        close_pos(i, pos["target"], "Target")

        # ---- RMS account-level daily caps (loss/profit) ----
        if rms_caps and pos is not None and not EOD[i]:
            sgn = 1 if pos["side"] == "long" else -1
            day_mtm = day_realized + (C[i] - pos["entry"]) * pos["qty"] * sgn
            if (LOSS_CAP and day_mtm <= -LOSS_CAP):
                close_pos(i, C[i], "RMS max-loss"); day_locked = True
            elif (PROFIT_TGT and day_mtm >= PROFIT_TGT):
                close_pos(i, C[i], "RMS profit-target"); day_locked = True
        if rms_caps and not day_locked:
            if (LOSS_CAP and day_realized <= -LOSS_CAP) or (PROFIT_TGT and day_realized >= PROFIT_TGT):
                day_locked = True

        if pos is not None and EOD[i]:
            close_pos(i, O[i], "EOD 3:15")

        if (pos is None and not day_locked and not LATE[i] and trades_today < 2 and i + 1 < n
                and not EOD[i] and not (skip_exp and EXP[i])):
            lo, sh = long_e[i], short_e[i]
            if lo or sh:
                side = "long" if lo else "short"
                entry = O[i + 1]
                av = a[i] if a[i] > 0 else max(1.0, entry * 0.001)
                stop_dist = atr_sl * av
                qty = (RISK_PCT * equity) / max(stop_dist, 1e-6)
                qty = min(qty, LEV_CAP * equity / entry)          # 1x cap, no leverage
                stop = entry - stop_dist if side == "long" else entry + stop_dist
                tp = rr * stop_dist
                target = entry + tp if side == "long" else entry - tp
                pos = dict(side=side, entry=entry, qty=qty, stop=stop, target=target,
                           entry_i=i + 1, entry_dt=DT[i + 1])
                trades_today += 1

        m = equity
        if pos is not None:
            m += (C[i] - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
        eq_curve.append((DT[i], m))

    if pos is not None:
        close_pos(n - 1, C[n - 1], "End")
    eqdf = pd.DataFrame(eq_curve, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p, variant=name, mode="intraday")


if __name__ == "__main__":
    df1m = load_1m()
    print("SCREEN — all designs x TFs (default params, 1x, no leverage)\n")
    print(f"{'design':11s}{'tf':4s}{'trades':>7s}{'net%':>8s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>7s}")
    cache = {}
    for tf in ("15m", "5m", "3m", "1m"):
        d = resample(df1m, tf); cache[tf] = d
        for name in DESIGN_GRID:
            res = backtest(d, name)
            m, _ = engine.metrics(res)
            if m["trades"] == 0:
                print(f"{name:11s}{tf:4s}{'0':>7s}"); continue
            print(f"{name:11s}{tf:4s}{m['trades']:>7d}{m.get('net_pct',0):>8.1f}"
                  f"{m.get('sharpe',0):>8.2f}{m.get('maxdd',0):>8.1f}{m.get('win_rate',0):>7.1f}")
