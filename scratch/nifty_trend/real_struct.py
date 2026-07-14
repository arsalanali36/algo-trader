"""REAL-premium option-structure backtester — reads the rollingoption data-lake
(optlake_load) instead of Black-Scholes. This is the Track-B engine: short-vol /
theta / IV strategies whose edge is the VARIANCE RISK PREMIUM (real IV > realized),
which BS-from-realized CANNOT see (that's why short straddle died at -95% on BS).

Same house rules + res shape as option_structures.py so engine.metrics / montecarlo /
the 3-pass dashboard all work. 3 passes here:
    instrument = gross real-premium P&L (NO charges, NO caps) — is the VRP edge there?
    rms        = + account daily loss/profit caps
    bs (deploy)= + real Zerodha per-leg charges  (name kept for the dashboard toggle)

Structures (real premium legs, sign +buy / -sell):
    short_straddle : sell ATM CE + ATM PE                 (naked — wings for live)
    short_strangle : sell ATM+k CE + ATM-k PE            (needs offset data)
    iron_fly       : sell ATM CE+PE, buy ATM±w wings     (defined risk)
"""
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import optlake_load as lake
import expiry_calendar as xcal

START_CAP = engine.START_CAP
EXIT_HM = dt.time(15, 15)
STEP = 50


def _daily_iv_rank(m, lookback=60):
    di = m.groupby("day").atm_iv.first().dropna()
    r = di.rolling(lookback, min_periods=20).apply(lambda w: (w.iloc[-1] >= w).mean())
    return {d: float(v) for d, v in r.items() if pd.notna(v)}


def entry_array(m, sig, p):
    """boolean per-bar entry (max one True/day, at/just-after the trigger time)."""
    if sig == "_preset" and p.get("_entry") is not None:   # pre-supplied array (rotation test)
        return np.asarray(p["_entry"], dtype=bool)
    tt = m.Datetime.dt.time
    h0 = int(p.get("h0", 10))
    at = (tt >= dt.time(h0, 0))
    first = at & (~at.groupby(m.day).transform(lambda s: s.shift(1, fill_value=False)))
    if sig == "time":
        e = first
    elif sig == "iv_high":
        ivr = _daily_iv_rank(m, int(p.get("iv_lb", 60)))
        thr = float(p.get("iv_thr", 0.5))
        e = first & m.day.map(lambda d: ivr.get(d, 0.0) >= thr)
    elif sig == "iv_low":
        ivr = _daily_iv_rank(m, int(p.get("iv_lb", 60)))
        thr = float(p.get("iv_thr", 0.5))
        e = first & m.day.map(lambda d: ivr.get(d, 1.0) <= thr)
    else:
        raise ValueError(sig)
    return e.fillna(False).values


def _legs(struct, row, p):
    """resolve real-premium legs at a bar: list of (label, side, entry_prem). side -1 sell/+1 buy.
    Returns None if a required leg's premium is missing."""
    w = int(p.get("wing_off", 5))
    if struct == "short_straddle":
        legs = [("ce_close", -1), ("pe_close", -1)]
    elif struct == "iron_fly":
        # short ATM + long wings (wings from offset columns if present in `row`)
        legs = [("ce_close", -1), ("pe_close", -1),
                (f"CEATMp{w}_c", +1), (f"PEATMm{w}_c", +1)]
    else:
        raise ValueError(struct)
    out = []
    for col, side in legs:
        v = row.get(col)
        if v is None or (isinstance(v, float) and np.isnan(v)) or v <= 0:
            return None
        out.append((col, side, float(v)))
    return out


def backtest(m, struct, sig, params=None, lot=75, lots=1, charges=True, rms_caps=None):
    p = dict(params or {})
    tp = float(p.get("tp_frac", 0.5)); sl = float(p.get("sl_frac", 1.0))
    skip_exp = bool(p.get("skip_expiry", True))
    qty = int(lots) * int(lot)
    ent = entry_array(m, sig, p)
    DT = m.Datetime.values
    DAY = m.day.values
    tt = m.Datetime.dt.time.values
    EOD = np.array([t >= EXIT_HM for t in tt])
    dates = m.Datetime.dt.date.values
    EXP = np.array([d.weekday() == xcal.weekly_expiry_weekday(d) for d in dates])
    rows = m.to_dict("records")
    n = len(m)

    equity = START_CAP; pos = None; trades = []; eqc = []
    cur_day = None; tcount = 0; day_real = 0.0; locked = False
    LOSS = (rms_caps or {}).get("loss_cap"); PROF = (rms_caps or {}).get("profit_target")

    def val(legs, row):
        v = 0.0
        for (col, side, ep) in legs:
            cur = row.get(col)
            if cur is None or (isinstance(cur, float) and np.isnan(cur)):
                return None
            v += side * float(cur)
        return v

    slip = float(p.get("slip_frac", 0.0))   # per-leg premium haircut, entry AND exit (bid-ask+impact)

    def close(i, reason):
        nonlocal equity, pos, day_real
        cv = val(pos["legs"], rows[i])
        if cv is None:
            cv = pos["entry_val"]
        pnl_u = cv - pos["entry_val"]
        fee = 0.0
        slip_cost = 0.0
        for (col, side, ep) in pos["legs"]:
            xp = float(rows[i].get(col) or ep)
            if charges:
                fee += bs.calc_charges(ep, xp, qty, entry_side=("BUY" if side > 0 else "SELL"),
                                       when=pos["entry_dt"])
            slip_cost += slip * (abs(ep) + abs(xp))    # you cross the spread both legs, both sides
        pnl = (pnl_u - slip_cost) * qty - fee
        equity += pnl; day_real += pnl
        trades.append(dict(side=("short" if struct != "iron_fly" else "shortvol"),
                           entry=round(pos["entry_val"], 2), exit=round(cv, 2), qty=qty,
                           pnl=pnl, points=round(pnl_u, 2), entry_i=pos["entry_i"], exit_i=i,
                           entry_dt=pos["entry_dt"], exit_dt=DT[i], reason=reason,
                           bars=i - pos["entry_i"], fee=round(fee, 0),
                           entry_prem=round(abs(pos["entry_val"]), 2), exit_prem=round(abs(cv), 2),
                           atm=pos["atm"], struct=struct, spot_in=pos["spot_in"],
                           spot_out=round(float(rows[i].get("spot") or 0), 1)))
        pos = None

    for i in range(1, n):
        if DAY[i] != cur_day:
            cur_day = DAY[i]; tcount = 0; day_real = 0.0; locked = False
        if pos is not None:
            cv = val(pos["legs"], rows[i])
            if cv is not None and not EOD[i]:
                mtm = (cv - pos["entry_val"]) * qty
                ref = pos["ref"]
                if mtm >= tp * ref * qty:
                    close(i, "Target %credit")
                elif mtm <= -sl * ref * qty:
                    close(i, "Stop %credit")
        if rms_caps and pos is not None and not EOD[i]:
            cv = val(pos["legs"], rows[i])
            if cv is not None:
                dm = day_real + (cv - pos["entry_val"]) * qty
                if LOSS and dm <= -LOSS:
                    close(i, "RMS max-loss"); locked = True
                elif PROF and dm >= PROF:
                    close(i, "RMS profit-target"); locked = True
        if pos is not None and EOD[i]:
            close(i, "EOD 3:15")
        if (pos is None and not locked and tt[i] < EXIT_HM and tcount < 2 and i + 1 < n
                and not EOD[i] and ent[i] and not (skip_exp and EXP[i])):
            legs = _legs(struct, rows[i + 1], p)
            if legs is not None:
                entry_val = sum(side * ep for (_c, side, ep) in legs)
                ref = abs(entry_val) if abs(entry_val) > 1e-6 else 1.0
                notional = sum(abs(ep) for (_c, _s, ep) in legs) * qty
                if notional <= START_CAP:
                    pos = dict(legs=legs, entry_val=entry_val, ref=ref, entry_i=i + 1,
                               entry_dt=DT[i + 1], atm=round(float(rows[i + 1].get("strike") or 0)),
                               spot_in=round(float(rows[i + 1].get("spot") or 0), 1))
                    tcount += 1
        mk = equity
        if pos is not None:
            cv = val(pos["legs"], rows[i])
            if cv is not None:
                mk += (cv - pos["entry_val"]) * qty
        eqc.append((DT[i], mk))
    if pos is not None:
        close(n - 1, "End")
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"{struct}/{sig}", mode="intraday")


if __name__ == "__main__":
    import sys
    m = lake.atm_frame("WEEK", "5m")
    print(f"real ATM frame: {len(m)} bars, {m.day.nunique()} days, "
          f"{m.Datetime.min().date()}→{m.Datetime.max().date()}\n")
    lot = bs.get_nifty_lot()
    print(f"{'struct/sig':26s}{'params':30s}{'trades':>7s}{'net%':>8s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>7s}")
    grid = [
        ("short_straddle", "time", dict(h0=10, tp_frac=0.5, sl_frac=1.0)),
        ("short_straddle", "time", dict(h0=10, tp_frac=0.3, sl_frac=0.6)),
        ("short_straddle", "time", dict(h0=11, tp_frac=0.5, sl_frac=0.5)),
        ("short_straddle", "iv_high", dict(h0=10, iv_thr=0.5, tp_frac=0.5, sl_frac=1.0)),
        ("short_straddle", "iv_high", dict(h0=10, iv_thr=0.7, tp_frac=0.4, sl_frac=0.8)),
        ("short_straddle", "iv_low", dict(h0=10, iv_thr=0.3, tp_frac=0.5, sl_frac=1.0)),
    ]
    for struct, sig, p in grid:
        res = backtest(m, struct, sig, p, lot=lot, charges=True)
        met, _ = engine.metrics(res)
        if met["trades"] == 0:
            print(f"{struct}/{sig}: 0 trades"); continue
        print(f"{struct+'/'+sig:26s}{str(p):30s}{met['trades']:>7d}{met.get('net_pct',0):>8.1f}"
              f"{met.get('sharpe',0):>8.2f}{met.get('maxdd',0):>8.1f}{met.get('win_rate',0):>7.1f}")
