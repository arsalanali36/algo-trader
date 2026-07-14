"""#07 Gamma Scalping — REAL-data delta-hedged long-straddle backtester.

The bet OPPOSITE to #06 short-vol: buy ATM straddle (long gamma), delta-hedge the
underlying through the day, harvest realized-vol via re-hedging. Net edge = realized
variance − implied variance − hedging costs − charges. Wins only when REALIZED vol
exceeds the IMPLIED vol paid (and beats the churn cost) — the losing side of the VRP
on average, so it needs a CONDITIONAL entry (cheap gamma / low-IV / expiry 0DTE blast).

Honest model (uses REAL premium + REAL IV from optlake, NO fabricated greeks):
  per bar i (holding a long straddle):
    dV       = real (ce+pe) premium change           [from the lake]
    δ_prev   = BS delta of the straddle (from REAL iv) at bar i-1
    hedged   = we hold  h = -δ_hedged  underlying units (band-triggered re-hedge)
    scalp_dPnL = dV + h·dS         (h offsets the option delta → leaves gamma-θ)
  charges: straddle 2 legs in+out (real Zerodha) + ₹20/leg + slippage per re-hedge.

Passes (dashboard toggle): instrument = gross (no charges/caps) · rms = +caps ·
bs = +real charges (option + hedge) = deployable.
"""
import math
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import optlake_load as lake
import expiry_calendar as xcal

START_CAP = engine.START_CAP
EXIT_HM = dt.time(15, 15)
R_FREE = 0.065


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_delta(S, K, T, sigma, opt):
    """BS delta from REAL iv (sigma decimal). Intrinsic at/after expiry."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        if opt == "CE":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (R_FREE + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _ncdf(d1) if opt == "CE" else _ncdf(d1) - 1.0


def _daily_iv_rank(m, lookback=60):
    di = m.groupby("day").atm_iv.first().dropna()
    r = di.rolling(lookback, min_periods=20).apply(lambda w: (w.iloc[-1] >= w).mean())
    return {d: float(v) for d, v in r.items() if pd.notna(v)}


def entry_days(m, sig, p):
    """which days to run the scalp (entry at h0), as a set of dates."""
    if sig == "_preset_days":
        return set(p["_days"])
    days = sorted(m.day.unique())
    if sig == "all":
        return set(days)
    if sig in ("iv_low", "iv_high"):
        ivr = _daily_iv_rank(m, int(p.get("iv_lb", 60)))
        thr = float(p.get("iv_thr", 0.4))
        if sig == "iv_low":
            return {d for d in days if ivr.get(d, 1.0) <= thr}
        return {d for d in days if ivr.get(d, 0.0) >= thr}
    if sig == "expiry":     # 0DTE gamma-blast: only weekly-expiry days
        return {d for d in days if d.weekday() == xcal.weekly_expiry_weekday(d)}
    raise ValueError(sig)


def backtest(m, sig="all", params=None, lot=75, lots=1, charges=True, rms_caps=None):
    p = dict(params or {})
    h0 = int(p.get("h0", 10))
    band = float(p.get("hedge_band", 0.10))       # re-hedge when net delta drifts > band
    hedge_slip = float(p.get("hedge_slip", 0.5))  # index pts slippage per re-hedge (per unit)
    skip_exp = bool(p.get("skip_expiry", True)) and sig != "expiry"
    qty = int(lots) * int(lot)
    edays = entry_days(m, sig, p)

    m = m.reset_index(drop=True)
    DT = m.Datetime.values; DAY = m.day.values
    tt = m.Datetime.dt.time.values
    S = m.spot.values.astype(float); K = m.strike.values.astype(float)
    CE = m.ce_close.values.astype(float); PE = m.pe_close.values.astype(float)
    IV = (m.atm_iv.values.astype(float)) / 100.0
    Tyr = np.array([bs.tte_years(t) for t in m.Datetime])
    n = len(m)

    equity = START_CAP; trades = []; eqc = []
    cur_day = None; day_real = 0.0; locked = False
    LOSS = (rms_caps or {}).get("loss_cap"); PROF = (rms_caps or {}).get("profit_target")

    i = 0
    while i < n:
        d = DAY[i]
        if d != cur_day:
            cur_day = d; day_real = 0.0; locked = False
        # find entry bar of the day (first bar at/after h0) if this is an entry day
        if (d in edays and not locked and not (skip_exp and
                d.weekday() == xcal.weekly_expiry_weekday(d))
                and tt[i] >= dt.time(h0, 0) and tt[i] < EXIT_HM):
            e = i
            if not (CE[e] > 0 and PE[e] > 0):
                i += 1; continue
            entry_straddle = CE[e] + PE[e]
            # initial hedge to neutral
            sig_e = IV[e] if IV[e] > 0 else 0.15
            delta0 = bs_delta(S[e], K[e], Tyr[e], sig_e, "CE") + bs_delta(S[e], K[e], Tyr[e], sig_e, "PE")
            h = -delta0                       # underlying units held (per 1 straddle)
            hedged_delta = delta0
            opt_pnl_u = 0.0; hedge_pnl_u = 0.0; nhedge = 0
            hedge_cost = 0.0
            j = e + 1
            # walk to EOD (or day change)
            while j < n and DAY[j] == d and tt[j] < EXIT_HM:
                dS = S[j] - S[j - 1]
                hedge_pnl_u += h * dS
                opt_pnl_u = (CE[j] + PE[j]) - entry_straddle
                # re-hedge if net delta drifted past band
                sig_j = IV[j] if IV[j] > 0 else sig_e
                dnow = bs_delta(S[j], K[j], Tyr[j], sig_j, "CE") + bs_delta(S[j], K[j], Tyr[j], sig_j, "PE")
                if abs(dnow - hedged_delta) > band:
                    if charges:
                        hedge_cost += (20.0 * 1.18) + hedge_slip * abs(dnow + h) * qty * 0  # order fee
                        hedge_cost += hedge_slip * qty  # slippage per re-hedge (pts×qty)
                    h = -dnow; hedged_delta = dnow; nhedge += 1
                j += 1
            xbar = min(j, n - 1)
            # settle at last in-day bar
            xi = j - 1 if (j <= n and j - 1 >= e) else e
            exit_straddle = CE[xi] + PE[xi]
            opt_pnl = (exit_straddle - entry_straddle) * qty
            hedge_pnl = hedge_pnl_u * qty
            fee = 0.0
            if charges:
                fee += bs.calc_charges(CE[e], CE[xi], qty, entry_side="BUY", when=DT[e])
                fee += bs.calc_charges(PE[e], PE[xi], qty, entry_side="BUY", when=DT[e])
                fee += hedge_cost
            pnl = opt_pnl + hedge_pnl - fee
            equity += pnl; day_real += pnl
            trades.append(dict(side="long-gamma", entry=round(entry_straddle, 2),
                               exit=round(exit_straddle, 2), qty=qty, pnl=pnl,
                               points=round(opt_pnl_u + hedge_pnl_u, 2),
                               entry_i=e, exit_i=xi, entry_dt=DT[e], exit_dt=DT[xi],
                               reason="EOD 3:15", bars=xi - e, fee=round(fee, 0),
                               entry_prem=round(entry_straddle, 2), exit_prem=round(exit_straddle, 2),
                               atm=round(K[e]), struct="gamma_straddle",
                               spot_in=round(S[e], 1), spot_out=round(S[xi], 1),
                               nhedge=nhedge, opt_pnl=round(opt_pnl, 0), hedge_pnl=round(hedge_pnl, 0)))
            if rms_caps and ((LOSS and day_real <= -LOSS) or (PROF and day_real >= PROF)):
                locked = True
            eqc.append((DT[xi], equity))
            i = j                     # jump past this day's holding
            continue
        i += 1

    if not trades:
        eqc = [(DT[0], START_CAP), (DT[-1], START_CAP)]
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"gamma/{sig}", mode="intraday")


if __name__ == "__main__":
    m = lake.atm_frame("WEEK", "5m")
    lot = bs.get_nifty_lot()
    print(f"real ATM: {len(m)} bars, {m.day.nunique()} days\n")
    print(f"{'sig':10s}{'band':>6s}{'trades':>8s}{'net%':>9s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>7s}{'avgHedge':>9s}")
    for sig in ("all", "iv_low", "iv_high", "expiry"):
        for band in (0.10, 0.25):
            p = dict(h0=10, hedge_band=band, hedge_slip=0.5, iv_thr=0.4)
            res = backtest(m, sig, p, lot=lot, charges=True)
            mt, _ = engine.metrics(res)
            if mt["trades"] == 0:
                print(f"{sig:10s}{band:>6.2f}{'0':>8s}"); continue
            avgh = np.mean([t["nhedge"] for t in res["trades"]])
            print(f"{sig:10s}{band:>6.2f}{mt['trades']:>8d}{mt.get('net_pct',0):>9.1f}"
                  f"{mt.get('sharpe',0):>8.2f}{mt.get('maxdd',0):>8.1f}{mt.get('win_rate',0):>7.1f}{avgh:>9.1f}")
