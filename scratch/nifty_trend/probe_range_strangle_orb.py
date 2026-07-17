"""PHASE-0e — Range Strangle INTRADAY + later entry + ORB-FAIL filter.

User's 3 ideas:
  (1) combined-premium target/SL = 30 (tighter than 40)
  (2) push entry later (9:45 / 10:00) instead of 9:20
  (3) use ORB (opening-range breakout) as a FILTER: a short strangle's enemy is a TREND/breakout
      day. ORB makes money on breakouts. So if ORB has FAILED by entry time (opening range held /
      broke-and-reverted → price back INSIDE the opening range) → today looks range-bound → SELL.
      If price has cleanly broken out of the opening range → skip (trend day, strangle gets run over).

Opening range = 09:15–09:30 (first 15 min) high/low. ORB-fail filter = spot at entry is INSIDE
[OR_low, OR_high]. Everything else same as the intraday build (9:20-config was lb=10, dist=0.5%).
Real lake premium + charges + DOM slip, same-day exit, NO overnight.
Run: python -X utf8 probe_range_strangle_orb.py
"""
import datetime as dt
import numpy as np

import bs_option as bs
import real_struct2 as r2
import probe_range_strangle_positional as P

STEP = 50
EXIT_HM = dt.time(15, 15)
OR_START = dt.time(9, 15)
OR_END = dt.time(9, 30)          # opening range = first 15 min
EVO_END = P.EVO_END
LB, DIST, AGE = 10, 0.5, 2        # the winning intraday base config


def bars_index(g, entry_hm):
    """day -> (entry_bar >= entry_hm, close_bar <=15:15, OR_high, OR_low from 9:15-9:30)."""
    DAY, TT, SPOT = g["DAY"], g["TT"], g["SPOT"]
    eb, cb, orh, orl = {}, {}, {}, {}
    for i in range(len(DAY)):
        d = DAY[i]
        if OR_START <= TT[i] < OR_END:
            orh[d] = max(orh.get(d, -1e18), SPOT[i]); orl[d] = min(orl.get(d, 1e18), SPOT[i])
        if d not in eb and TT[i] >= entry_hm and TT[i] < EXIT_HM:
            eb[d] = i
        if TT[i] <= EXIT_HM:
            cb[d] = i
    return eb, cb, orh, orl


def backtest(g, ext, lot, entry_hm, tgt, sl, orb_filter=False, period=None, orb_buf=0.0):
    """orb_buf: widen the opening range by buf * OR-width on each side (softer filter — allow a
    small breakout, only skip a BIG trend-break). buf=0 = strict (spot must be inside raw OR)."""
    SPOT, ATMK, DT = g["SPOT"], g["ATMK"], g["DT"]
    eb, cb, orh, orl = bars_index(g, entry_hm)
    trades = []; skipped_orb = 0
    for d0 in sorted(eb):
        if period is not None and d0 not in period:
            continue
        if d0 not in ext:
            continue
        hi, age_hi, lo, age_lo = ext[d0]
        e = eb[d0]; xend = cb[d0]; spot = SPOT[e]
        if age_hi < AGE or age_lo < AGE:
            continue
        if (hi - spot) < DIST / 100 * spot or (spot - lo) < DIST / 100 * spot or spot >= hi or spot <= lo:
            continue
        if orb_filter:
            if d0 not in orh:
                continue
            orw = orh[d0] - orl[d0]
            lo_b = orl[d0] - orb_buf * orw; hi_b = orh[d0] + orb_buf * orw
            if not (lo_b <= spot <= hi_b):           # broke BIG out of opening range → trend day → skip
                skipped_orb += 1; continue
        Khi = round(hi / STEP) * STEP; Klo = round(lo / STEP) * STEP
        if not (-10 <= round((Khi - ATMK[e]) / STEP) <= 10 and -10 <= round((Klo - ATMK[e]) / STEP) <= 10):
            continue
        ce_ep = r2._px(g, e, "CE", Khi); pe_ep = r2._px(g, e, "PE", Klo)
        if not (ce_ep > 0 and pe_ep > 0):
            continue
        P0 = ce_ep + pe_ep
        exit_i = xend; reason = "EOD"
        for i in range(e + 1, xend + 1):
            comb = r2._px(g, i, "CE", Khi) + r2._px(g, i, "PE", Klo)
            if comb <= P0 - tgt:
                exit_i = i; reason = "TARGET"; break
            if comb >= P0 + sl:
                exit_i = i; reason = "SL"; break
        ce_xp = r2._px(g, exit_i, "CE", Khi); pe_xp = r2._px(g, exit_i, "PE", Klo)
        qty = lot
        gross = ((ce_ep - ce_xp) + (pe_ep - pe_xp)) * qty
        fee = (bs.calc_charges(ce_ep, ce_xp, qty, entry_side="SELL", when=DT[e])
               + bs.calc_charges(pe_ep, pe_xp, qty, entry_side="SELL", when=DT[e]))
        slip = bs.slip_cost_leg(ce_ep, ce_xp, qty) + bs.slip_cost_leg(pe_ep, pe_xp, qty)
        trades.append(dict(day=d0, pnl=gross - fee - slip, reason=reason))
    return trades, skipped_orb


def stats(trades):
    if not trades:
        return None
    p = np.array([t["pnl"] for t in trades], dtype=float)
    gp = p[p > 0].sum(); gl = -p[p < 0].sum()
    rng = np.random.default_rng(7)
    boots = np.array([rng.choice(p, len(p), replace=True).mean() for _ in range(2000)])
    return dict(n=len(p), net=float(p.sum()), win=100 * (p > 0).mean(),
                pf=(gp / gl if gl > 0 else float("inf")), pval=float((boots <= 0).mean()),
                sharpe=float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) else 0.0,
                worst=float(p.min()))


def show(label, trades):
    s = stats(trades)
    if s is None:
        print(f"  {label:30s} — 0 trades"); return
    print(f"  {label:30s} n={s['n']:>4} net={s['net']:>10,.0f} win={s['win']:>4.0f}% PF={s['pf']:>4.2f} "
          f"p={s['pval']:>5.3f} Sh={s['sharpe']:>5.2f} worst={s['worst']:>8,.0f}")


def main():
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot() or 65
    print(f"PHASE-0e — Range Strangle intraday: later entry + ORB-fail filter (lb={LB} dist={DIST}%, lot={lot})\n")
    g = r2.grid("WEEK", "5m")
    ext = P.trailing_extremes(P.daily_hl(), LB)
    all_days = set(g["DAY"]); tr = {d for d in all_days if d < EVO_END}; oos = {d for d in all_days if d >= EVO_END}

    print("=== A. ENTRY TIME (no ORB filter) — T30/SL30 vs T40/SL40 ===")
    for et in (dt.time(9, 20), dt.time(9, 45), dt.time(10, 0)):
        for (T, SL) in ((30, 30), (40, 40)):
            trf, _ = backtest(g, ext, lot, et, T, SL)
            show(f"entry {et.strftime('%H:%M')} T{T}/SL{SL}", trf)
        print()

    print("=== B. ORB-FAIL FILTER (entry 9:45, OR 9:15-9:30) — OFF vs ON, with train/OOS ===")
    for (T, SL) in ((30, 30), (40, 40)):
        for filt in (False, True):
            tag = "ORB-ON " if filt else "ORB-off"
            trf, sk = backtest(g, ext, lot, dt.time(9, 45), T, SL, orb_filter=filt)
            show(f"T{T}/SL{SL} {tag} FULL", trf)
            show(f"T{T}/SL{SL} {tag} train", backtest(g, ext, lot, dt.time(9, 45), T, SL, filt, tr)[0])
            show(f"T{T}/SL{SL} {tag} oos", backtest(g, ext, lot, dt.time(9, 45), T, SL, filt, oos)[0])
            if filt:
                print(f"     (ORB filter ne {sk} trend-day entries skip kiye)")
            print()

    print("=== C. ORB-fail + entry 10:00 (OR 9:15-9:30, more room to confirm) ===")
    for (T, SL) in ((30, 30), (40, 40)):
        trf, sk = backtest(g, ext, lot, dt.time(10, 0), T, SL, orb_filter=True)
        show(f"entry 10:00 T{T}/SL{SL} ORB-ON FULL", trf)
        show(f"entry 10:00 T{T}/SL{SL} ORB-ON train", backtest(g, ext, lot, dt.time(10, 0), T, SL, True, tr)[0])
        show(f"entry 10:00 T{T}/SL{SL} ORB-ON oos", backtest(g, ext, lot, dt.time(10, 0), T, SL, True, oos)[0])
        print()


if __name__ == "__main__":
    main()
