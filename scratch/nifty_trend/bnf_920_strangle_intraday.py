"""BANKNIFTY 9:20 ±1sigma short strangle, exit 14:55 SAME DAY (intraday, REAL premium).

User Q: 9:20 pe BankNifty par 1-sigma distance pe dono (CE+PE) SELL, 2:55 tak hold -> kya result?

- Data: OptChainLake_1m/BANKNIFTY/MONTH (real per-minute premium, ATM+-10 held-strike grid, 2021-08 -> 2026-07).
- Entry = first bar >= 09:20; Exit = first bar >= 14:55 (or last bar of day).
- Strike selection "1 sigma" = the market's OWN implied 1-DAY move from the ATM straddle:
      sigma_1day_pts ~= 0.8 * ATM_straddle / sqrt(DTE_calendar)   (E_to_expiry = 0.8*straddle = sigma*sqrt(DTE))
  offset_strikes = round(sigma_1day / 100), clamped 1..8 (lake window).
  Also fixed 2/3/4/5-strike strangles for sensitivity.
- Held-strike reprice (real_struct2 method): read each held strike's CURRENT offset vs rolling ATM;
  |offset|>10 => intrinsic floor (tracked + reported as fallback%).
- Costs: real date-aware Zerodha charges (bs.calc_charges) + DOM-measured slippage (bs.slip_cost_leg).
"""
import os, math, datetime as dt
import numpy as np, pandas as pd
import bs_option as bs
import expiry_calendar as xcal

HERE = os.path.dirname(os.path.abspath(__file__))
LAKE = os.path.abspath(os.path.join(HERE, "..", "..", "_TRADING_DATA",
                                    "OptChainLake_1m", "BANKNIFTY", "MONTH"))
STEP = 100
ENTRY_T = dt.time(9, 20)
EXIT_T  = dt.time(14, 55)


def lot_for(d):
    """Date-aware BANKNIFTY lot (NSE circulars, approx to the change month):
    25 (pre-2023-07) -> 15 (2023-07..2024-11) -> 30 (2024-11-20 -> today, live-verified
    scrip master). Real ₹/charges scale with this; points/%/Sharpe do NOT (per-trade lot)."""
    if d < dt.date(2023, 7, 1):
        return 25
    if d < dt.date(2024, 11, 20):
        return 15
    return 30


def _bnf_monthly_expiry(d):
    mwd = xcal.banknifty_monthly_expiry_weekday(d)
    exp = xcal._last_weekday_of_month(d.year, d.month, mwd)
    if exp < d:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        exp = xcal._last_weekday_of_month(y, m, mwd)
    return exp


def _tag(off):
    return "ATM" if off == 0 else (f"ATMp{off}" if off > 0 else f"ATMm{abs(off)}")


def load_grid():
    """per-bar CE/PE premium grid across offsets -10..10 + ATMK/SPOT/DT/DAY/TT."""
    offs = list(range(-10, 11))
    base = None
    for side in ("CE", "PE"):
        for off in offs:
            p = os.path.join(LAKE, f"{side}_{_tag(off)}.csv")
            df = pd.read_csv(p, usecols=["timestamp", "close", "strike", "spot"])
            df["Datetime"] = (pd.to_datetime(df["timestamp"], unit="s", utc=True)
                              .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
            col = f"{side}{_tag(off)}"
            keep = df[["Datetime", "close"]].rename(columns={"close": col})
            if side == "CE" and off == 0:
                keep = df[["Datetime", "close", "strike", "spot"]].rename(
                    columns={"close": col, "strike": "ATMK", "spot": "SPOT"})
            base = keep if base is None else base.merge(keep, on="Datetime", how="outer")
    base = base.sort_values("Datetime").drop_duplicates("Datetime").reset_index(drop=True)
    tt = base.Datetime.dt.time
    base = base[(tt >= dt.time(9, 15)) & (tt <= dt.time(15, 29))].reset_index(drop=True)
    # SPOT/ATMK carried by CE-ATM col; ffill/bfill within-day to cover minute gaps
    base["_d"] = base.Datetime.dt.date
    base["ATMK"] = base.groupby("_d")["ATMK"].ffill().bfill()
    base["SPOT"] = base.groupby("_d")["SPOT"].ffill().bfill()
    # repair corrupt spot ticks (bad data e.g. 2024-01-10 spot=10331 vs ~47000):
    # any bar >8% off the day median -> NaN -> ffill/bfill from sane neighbours
    med = base.groupby("_d")["SPOT"].transform("median")
    base.loc[(base.SPOT - med).abs() / med > 0.08, "SPOT"] = np.nan
    base["SPOT"] = base.groupby("_d")["SPOT"].ffill().bfill()
    base = base.dropna(subset=["ATMK", "SPOT"]).reset_index(drop=True)
    CE = np.column_stack([base.get(f"CE{_tag(o)}", pd.Series(np.nan, index=base.index)).values for o in offs])
    PE = np.column_stack([base.get(f"PE{_tag(o)}", pd.Series(np.nan, index=base.index)).values for o in offs])
    return dict(CE=CE.astype(float), PE=PE.astype(float),
                ATMK=base.ATMK.values.astype(float), SPOT=base.SPOT.values.astype(float),
                DT=base.Datetime.values, DAY=base.Datetime.dt.date.values,
                TT=base.Datetime.dt.time.values, offs=offs)


_FALLBACK = [0, 0]  # [out-of-window reads, total reads]


def _px(g, i, side, K):
    off = int(round((K - g["ATMK"][i]) / STEP))
    _FALLBACK[1] += 1
    if -10 <= off <= 10:
        v = (g["CE"] if side == "CE" else g["PE"])[i, off + 10]
        if v and not np.isnan(v) and v > 0:
            return float(v)
    _FALLBACK[0] += 1
    S = g["SPOT"][i]
    return max(0.0, (S - K) if side == "CE" else (K - S))


def run(g, mode, skip_expiry=True):
    """mode: 'sigma' (daily 1-sigma auto) or int N (fixed N-strike strangle)."""
    DAY, TT, DT, SPOT, ATMK = g["DAY"], g["TT"], g["DT"], g["SPOT"], g["ATMK"]
    n = len(DT)
    # per-day entry (>=09:20) and exit (>=14:55) bar index
    entry_i, exit_i = {}, {}
    for i in range(n):
        d = DAY[i]
        if d not in entry_i and TT[i] >= ENTRY_T:
            entry_i[d] = i
        if TT[i] >= EXIT_T and d not in exit_i:
            exit_i[d] = i
    last_bar = {}
    for i in range(n):
        last_bar[DAY[i]] = i

    rows = []
    for d in sorted(entry_i):
        e = entry_i[d]
        x = exit_i.get(d, last_bar[d])
        if x <= e:
            continue
        exp = _bnf_monthly_expiry(d)
        dte = max((exp - d).days, 1)
        if skip_expiry and (exp - d).days <= 0:
            continue
        atmk = round(SPOT[e] / STEP) * STEP
        if mode == "sigma":
            strad = _px(g, e, "CE", atmk) + _px(g, e, "PE", atmk)
            if strad <= 0:
                continue
            sig = 0.8 * strad / math.sqrt(dte)
            off = int(np.clip(round(sig / STEP), 1, 8))
        else:
            off = int(mode)
        kc, kp = atmk + off * STEP, atmk - off * STEP
        pce, ppe = _px(g, e, "CE", kc), _px(g, e, "PE", kp)
        if pce <= 0 or ppe <= 0:
            continue
        xce, xpe = _px(g, x, "CE", kc), _px(g, x, "PE", kp)
        lot = lot_for(d)
        credit = (pce + ppe)
        debit  = (xce + xpe)
        gross  = (credit - debit) * lot
        when = pd.Timestamp(DT[e])
        fee = (bs.calc_charges(pce, xce, lot, entry_side="SELL", when=when) +
               bs.calc_charges(ppe, xpe, lot, entry_side="SELL", when=when))
        slip = bs.slip_cost_leg(pce, xce, lot) + bs.slip_cost_leg(ppe, xpe, lot)
        net = gross - fee - slip
        s_out = SPOT[x]
        rows.append(dict(day=d, off=off, dte=dte, credit=credit * lot,
                         gross=gross, fee=fee, slip=slip, net=net,
                         breach=bool(s_out > kc or s_out < kp),
                         pts=(credit - debit)))
    return pd.DataFrame(rows)


def run_ts(g, N, tgt_pts, sl_pts, skip_expiry=True, entry_t=ENTRY_T, slip_mult=1.0):
    """fixed N-strike strangle, intraday exit on COMBINED-premium target/SL (in points),
    else hold to 14:55. tgt/sl checked on every 1-min bar (SL priority within a bar)."""
    bs.SLIP_MULT = slip_mult
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    n = len(DT)
    entry_i, exit_i, last_bar = {}, {}, {}
    for i in range(n):
        d = DAY[i]
        if d not in entry_i and TT[i] >= entry_t:
            entry_i[d] = i
        if TT[i] >= EXIT_T and d not in exit_i:
            exit_i[d] = i
        last_bar[d] = i
    rows = []
    for d in sorted(entry_i):
        e = entry_i[d]
        xend = exit_i.get(d, last_bar[d])
        if xend <= e:
            continue
        exp = _bnf_monthly_expiry(d)
        if skip_expiry and (exp - d).days <= 0:
            continue
        atmk = round(SPOT[e] / STEP) * STEP
        kc, kp = atmk + N * STEP, atmk - N * STEP
        pce, ppe = _px(g, e, "CE", kc), _px(g, e, "PE", kp)
        if pce <= 0 or ppe <= 0:
            continue
        entry_credit = pce + ppe
        x, reason = xend, "3:15/2:55"
        for i in range(e + 1, xend + 1):
            comb = _px(g, i, "CE", kc) + _px(g, i, "PE", kp)
            mtm = entry_credit - comb            # +ve = profit
            if mtm <= -sl_pts:                   # SL checked first (conservative)
                x, reason = i, "SL"; break
            if mtm >= tgt_pts:
                x, reason = i, "target"; break
        xce, xpe = _px(g, x, "CE", kc), _px(g, x, "PE", kp)
        lot = lot_for(d)
        gross = (entry_credit - (xce + xpe)) * lot
        when = pd.Timestamp(DT[e])
        fee = (bs.calc_charges(pce, xce, lot, entry_side="SELL", when=when) +
               bs.calc_charges(ppe, xpe, lot, entry_side="SELL", when=when))
        slip = bs.slip_cost_leg(pce, xce, lot) + bs.slip_cost_leg(ppe, xpe, lot)
        rows.append(dict(day=d, net=gross - fee - slip, gross=gross, reason=reason))
    return pd.DataFrame(rows)


def report_ts(df, label):
    if len(df) < 20:
        print(f"  {label:<34} n={len(df):<4} (too few)"); return
    net = df.net
    wr = (net > 0).mean() * 100
    sh = (net.mean() / net.std() * math.sqrt(252)) if net.std() > 0 else 0
    tail = net.nsmallest(max(1, len(net) // 20)).sum()
    rc = df.reason.value_counts(normalize=True) * 100
    mix = " ".join(f"{k}:{rc.get(k,0):.0f}%" for k in ("target", "SL", "3:15/2:55"))
    print(f"  {label:<34} n={len(net):<4} win%={wr:4.0f}  net=Rs{net.sum():>10,.0f}  "
          f"avg=Rs{net.mean():>6,.0f}  Sharpe={sh:5.2f}  worst=Rs{net.min():>8,.0f}  "
          f"worst5%=Rs{tail:>9,.0f}  [{mix}]")


def report(df, label):
    if len(df) < 20:
        print(f"  {label:<30} n={len(df):<4} (too few)"); return
    net = df.net
    wr = (net > 0).mean() * 100
    sh = (net.mean() / net.std() * math.sqrt(252)) if net.std() > 0 else 0
    tail = net.nsmallest(max(1, len(net) // 20)).sum()
    print(f"  {label:<30} n={len(net):<4} avgOff={df.off.mean():4.1f}  win%={wr:4.0f}  "
          f"net=Rs{net.sum():>11,.0f}  avg=Rs{net.mean():>7,.0f}  Sharpe={sh:5.2f}  "
          f"worst=Rs{net.min():>9,.0f}  worst5%=Rs{tail:>11,.0f}  breach%={df.breach.mean()*100:3.0f}")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    print("Loading BANKNIFTY MONTH lake (real 1-min premium, +-10 held-strike grid)...", flush=True)
    g = load_grid()
    days = len(set(g["DAY"]))
    print(f"  bars={len(g['DT'])}  days={days}  span {g['DT'][0]} -> {g['DT'][-1]}  "
          f"lot=date-aware(25/15/30)\n", flush=True)

    print("== BANKNIFTY short strangle: SELL CE+PE @09:20, BUY back @14:55 (real premium, excl monthly-expiry-day) ==")
    print("   [primary] 1-sigma = straddle-implied daily move (0.8*straddle/sqrt(DTE)):")
    d_sig = run(g, "sigma", skip_expiry=True)
    report(d_sig, "1-sigma strangle")
    print("   [sensitivity] fixed N-strike (N x100 pts) OTM strangles:")
    for N in (2, 3, 4, 5, 6):
        report(run(g, N, skip_expiry=True), f"fixed {N}-strike ({N*STEP}pt)")

    print("\n   [incl. monthly-expiry-day entries]:")
    report(run(g, "sigma", skip_expiry=False), "1-sigma (incl expiry)")

    print("\n== Intraday COMBINED-premium target/SL (points), 5 & 6 strike ==")
    for N in (5, 6):
        print(f"   {N}-strike strangle:")
        report_ts(run_ts(g, N, 10**9, 10**9), f"  hold to 2:55 (no tgt/SL)")
        for v in (30, 40, 50):
            report_ts(run_ts(g, N, v, v), f"  target {v} / SL {v} pt")
        print()

    fb = _FALLBACK
    print(f"  held-strike fallback (out-of-+-10-window reads -> intrinsic floor): "
          f"{fb[0]}/{fb[1]} = {100*fb[0]/max(fb[1],1):.2f}%")
    if len(d_sig) >= 20:
        yr = d_sig.copy(); yr["y"] = pd.to_datetime(yr.day).dt.year
        print("\n  1-sigma by year:")
        for y, grp in yr.groupby("y"):
            n = len(grp); wr = (grp.net > 0).mean()*100
            print(f"    {y}  n={n:<4} win%={wr:4.0f}  net=Rs{grp.net.sum():>11,.0f}  "
                  f"avg=Rs{grp.net.mean():>7,.0f}  worst=Rs{grp.net.min():>9,.0f}")
