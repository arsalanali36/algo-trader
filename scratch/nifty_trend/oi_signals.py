"""C-family OI/PCR signals on the REAL ±10-strike chain + directional REAL-premium backtest.

Features per 5m bar (from optlake chain_frame, REAL OI/premium/IV):
  pcr        : Σ PE OI / Σ CE OI over the ±10 window  (window-PCR, not full-chain — stated)
  ce_wall_d  : distance (pts) from spot to the MAX-CE-OI strike (the "resistance wall")
  pe_wall_d  : distance (pts) from spot to the MAX-PE-OI strike (the "support wall")
  dce, dpe   : k-bar change in aggregate CE/PE OI (window aggregate — ATM-shift smoothed)
  dspot      : k-bar spot change

Designs (long_arr, short_arr):
  pcr_extreme : PCR percentile vs trailing days — high→LONG / low→SHORT (contrarian)
  maxoi_fade  : spot near CE wall → SHORT (fade resistance); near PE wall → LONG
  maxoi_break : spot crosses a wall AND that wall's OI is unwinding → momentum with the break
  oi_buildup  : classic 4-state (price↑ + PE-OI↑ = long-buildup → LONG; price↓+CE-OI↑ → SHORT)
  oi_div      : price up but CE OI building faster than PE (writers leaning in) → SHORT; mirror LONG

Execution (REAL premium, no BS): long → BUY ATM CE, short → BUY ATM PE at the lake's
real 5m premium; exits: tp/sl as % of entry premium + 15:15 EOD; max 2/day; skip-expiry.
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
_FEAT_CACHE = {}


def features(flag="WEEK", tf="5m", k=6):
    """chain features frame (cached). k = bars for ΔOI/Δspot (6×5m = 30min)."""
    key = (flag, tf, k)
    if key in _FEAT_CACHE:
        return _FEAT_CACHE[key]
    ch = lake.chain_frame(flag, tf, offs=range(-10, 11))
    atm = lake.atm_frame(flag, tf)
    m = ch.merge(atm[["Datetime", "ce_close", "pe_close", "atm_iv", "straddle"]], on="Datetime", how="inner")
    ce_oi_cols = [c for c in m.columns if c.startswith("CE") and c.endswith("_oi")]
    pe_oi_cols = [c for c in m.columns if c.startswith("PE") and c.endswith("_oi")]
    ce_k_cols = {c: c[:-3] + "_k" for c in ce_oi_cols}
    pe_k_cols = {c: c[:-3] + "_k" for c in pe_oi_cols}
    CEO = m[ce_oi_cols].values; PEO = m[pe_oi_cols].values
    CEK = m[[ce_k_cols[c] for c in ce_oi_cols]].values
    PEK = m[[pe_k_cols[c] for c in pe_oi_cols]].values
    spot = m["spot"].values.astype(float)
    with np.errstate(invalid="ignore"):
        ce_tot = np.nansum(CEO, axis=1); pe_tot = np.nansum(PEO, axis=1)
        m["pcr"] = np.where(ce_tot > 0, pe_tot / ce_tot, np.nan)
        iw_ce = np.nanargmax(np.where(np.isnan(CEO), -1, CEO), axis=1)
        iw_pe = np.nanargmax(np.where(np.isnan(PEO), -1, PEO), axis=1)
        m["ce_wall"] = CEK[np.arange(len(m)), iw_ce]
        m["pe_wall"] = PEK[np.arange(len(m)), iw_pe]
        m["ce_wall_oi"] = CEO[np.arange(len(m)), iw_ce]
        m["pe_wall_oi"] = PEO[np.arange(len(m)), iw_pe]
    m["ce_wall_d"] = m["ce_wall"] - spot          # +ve: wall above spot
    m["pe_wall_d"] = spot - m["pe_wall"]          # +ve: wall below spot
    m["dce"] = pd.Series(ce_tot).diff(k).values
    m["dpe"] = pd.Series(pe_tot).diff(k).values
    m["dwall_ce_oi"] = m["ce_wall_oi"].diff(k)
    m["dspot"] = pd.Series(spot).diff(k).values
    m["day"] = m.Datetime.dt.date
    _FEAT_CACHE[key] = m
    return m


def _day_pctile(series, day, lookback_days=20):
    """per-bar percentile of value vs the trailing N-day distribution of the same feature."""
    df = pd.DataFrame({"v": series.values, "day": day})
    daily_med = df.groupby("day").v.median()
    roll = daily_med.rolling(lookback_days, min_periods=10)
    lo, hi = roll.quantile(0.2), roll.quantile(0.8)
    lo_map = df.day.map(lo.shift(1)); hi_map = df.day.map(hi.shift(1))   # prev-day info only
    return lo_map.values, hi_map.values


def design_signals(m, name, p):
    spot = m.spot.values
    if name == "pcr_extreme":
        lo, hi = _day_pctile(m.pcr, m.day, int(p.get("pcr_lb", 20)))
        long = (m.pcr.values >= hi) ; short = (m.pcr.values <= lo)
        if p.get("invert"):
            long, short = short, long
    elif name == "maxoi_fade":
        nd = float(p.get("near_pts", 25.0))
        long = (m.pe_wall_d.values >= -nd) & (m.pe_wall_d.values <= nd)     # at support wall → LONG
        short = (m.ce_wall_d.values >= -nd) & (m.ce_wall_d.values <= nd)    # at resistance wall → SHORT
    elif name == "maxoi_break":
        # spot crosses the CE wall upward while wall OI unwinds → LONG breakout (shorts covering)
        cross_up = (m.ce_wall_d.values < 0) & (pd.Series(m.ce_wall_d.values).shift(1).values >= 0)
        cross_dn = (m.pe_wall_d.values < 0) & (pd.Series(m.pe_wall_d.values).shift(1).values >= 0)
        unw = m.dwall_ce_oi.values < 0
        long = cross_up & unw
        short = cross_dn
    elif name == "oi_buildup":
        thr = float(p.get("oi_thr", 0.0))
        up = m.dspot.values > 0; dn = m.dspot.values < 0
        pe_up = m.dpe.values > thr; ce_up = m.dce.values > thr
        long = up & pe_up          # price↑ + put-writing↑ = long-buildup confidence
        short = dn & ce_up         # price↓ + call-writing↑ = short-buildup
    elif name == "oi_div":
        up = m.dspot.values > 0; dn = m.dspot.values < 0
        ce_faster = (m.dce.values - m.dpe.values) > 0
        long = dn & (~ce_faster)   # price down but put-writers still firmer → reversal long
        short = up & ce_faster     # price up but call-writers leaning in → reversal short
    else:
        raise ValueError(name)
    return (pd.Series(long).fillna(False).values.astype(bool),
            pd.Series(short).fillna(False).values.astype(bool))


DESIGNS = ("pcr_extreme", "maxoi_fade", "maxoi_break", "oi_buildup", "oi_div")


def backtest(m, name, params=None, lot=75, lots=1, charges=True, rms_caps=None, _entry=None):
    """directional: long → BUY real ATM CE, short → BUY real ATM PE. tp/sl % of premium."""
    p = dict(params or {})
    tp = float(p.get("tp_frac", 0.5)); sl = float(p.get("sl_frac", 0.4))
    h0 = int(p.get("h0", 10)); h1 = int(p.get("h1", 14))
    skip_exp = bool(p.get("skip_expiry", True))
    qty = int(lots) * int(lot)
    if _entry is not None:
        long_e, short_e = _entry
    else:
        long_e, short_e = design_signals(m, name, p)
    DT = m.Datetime.values; DAY = m.day.values
    tt = m.Datetime.dt.time.values
    CE = m.ce_close.values.astype(float); PE = m.pe_close.values.astype(float)
    S = m.spot.values.astype(float)
    dates = m.Datetime.dt.date.values
    EXP = np.array([d.weekday() == xcal.weekly_expiry_weekday(d) for d in dates])
    n = len(m)
    equity = START_CAP; pos = None; trades = []; eqc = []
    cur_day = None; tcount = 0; day_real = 0.0; locked = False
    LOSS = (rms_caps or {}).get("loss_cap"); PROF = (rms_caps or {}).get("profit_target")
    W0, W1 = dt.time(h0, 0), dt.time(h1, 0)

    def prem(i, side):
        return CE[i] if side == "long" else PE[i]

    def close(i, reason):
        nonlocal equity, pos, day_real
        xp = prem(i, pos["side"])
        if not xp or xp <= 0:
            xp = pos["ep"]
        gross = (xp - pos["ep"]) * qty
        fee = bs.calc_charges(pos["ep"], xp, qty, entry_side="BUY") if charges else 0.0
        pnl = gross - fee
        equity += pnl; day_real += pnl
        trades.append(dict(side=pos["side"], entry=round(pos["ep"], 2), exit=round(xp, 2), qty=qty,
                           pnl=pnl, points=round(xp - pos["ep"], 2), entry_i=pos["i"], exit_i=i,
                           entry_dt=pos["dt"], exit_dt=DT[i], reason=reason, bars=i - pos["i"],
                           fee=round(fee, 0), entry_prem=round(pos["ep"], 2), exit_prem=round(xp, 2),
                           atm=round(float(m.strike.values[pos["i"]]) if "strike" in m else 0),
                           struct="atm_buy", spot_in=round(S[pos["i"]], 1), spot_out=round(S[i], 1)))
        pos = None

    for i in range(1, n):
        if DAY[i] != cur_day:
            cur_day = DAY[i]; tcount = 0; day_real = 0.0; locked = False
            if pos is not None:      # safety: never carry overnight
                close(i, "EOD 3:15")
        if pos is not None:
            xp = prem(i, pos["side"])
            if xp and xp > 0 and tt[i] < EXIT_HM:
                r = (xp - pos["ep"]) / pos["ep"]
                if r >= tp:
                    close(i, "Target %prem")
                elif r <= -sl:
                    close(i, "Stop %prem")
            if pos is not None and tt[i] >= EXIT_HM:
                close(i, "EOD 3:15")
        if rms_caps and not locked:
            mtm = day_real
            if pos is not None:
                xp = prem(i, pos["side"])
                if xp and xp > 0:
                    mtm += (xp - pos["ep"]) * qty
            if LOSS and mtm <= -LOSS:
                if pos is not None:
                    close(i, "RMS max-loss")
                locked = True
            elif PROF and mtm >= PROF:
                if pos is not None:
                    close(i, "RMS profit-target")
                locked = True
        if (pos is None and not locked and tcount < 2 and i + 1 < n
                and W0 <= tt[i] <= W1 and tt[i] < EXIT_HM
                and not (skip_exp and EXP[i]) and (long_e[i] or short_e[i])):
            side = "long" if long_e[i] else "short"
            ep = prem(i + 1, side)
            if ep and ep > 0:
                pos = dict(side=side, ep=float(ep), i=i + 1, dt=DT[i + 1])
                tcount += 1
        mk = equity
        if pos is not None:
            xp = prem(i, pos["side"])
            if xp and xp > 0:
                mk += (xp - pos["ep"]) * qty
        eqc.append((DT[i], mk))
    if pos is not None:
        close(n - 1, "End")
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"oi/{name}", mode="intraday")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    m = features("WEEK", "5m")
    lot = bs.get_nifty_lot()
    print(f"chain features: {len(m)} bars, {m.day.nunique()} days, "
          f"pcr med={np.nanmedian(m.pcr):.2f}, wall_d med ce={np.nanmedian(m.ce_wall_d):.0f} pe={np.nanmedian(m.pe_wall_d):.0f}\n")
    print(f"{'design':14s}{'trades':>7s}{'net%':>8s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>7s}")
    for name in DESIGNS:
        res = backtest(m, name, dict(tp_frac=0.5, sl_frac=0.4), lot=lot, charges=True)
        mt, _ = engine.metrics(res)
        if mt["trades"] == 0:
            print(f"{name:14s}      0"); continue
        print(f"{name:14s}{mt['trades']:>7d}{mt.get('net_pct',0):>8.1f}{mt.get('sharpe',0):>8.2f}"
              f"{mt.get('maxdd',0):>8.1f}{mt.get('win_rate',0):>7.1f}")
