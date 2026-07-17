"""PHASE-0d — Range-Extreme Short Strangle, INTRADAY (9:20 entry, SAME-DAY exit, NO overnight).

User clarified: "positional" = enter ~09:20 (din shuru hote hi), exit same day -> ZERO overnight
gap risk. This is the version they actually want. Key difference from the overnight probe: here
the SL genuinely CAPS the tail (market is open, price passes through the stop) -- no gap can jump it.

Enter 09:20 (filter: recent N-day high/low, aged + distant), sell CE@high / PE@low, walk 5-min
bars, exit on combined-premium target/SL or 15:15 EOD. Real lake premium + charges + DOM slip.
Run: python -X utf8 probe_range_strangle_intraday.py
"""
import datetime as dt
import numpy as np

import bs_option as bs
import real_struct2 as r2
import probe_range_strangle_positional as P

STEP = 50
ENTRY_HM = dt.time(9, 20)
EXIT_HM = dt.time(15, 15)
EVO_END = P.EVO_END
AGE = 2


def intraday_bars(g):
    DAY, TT = g["DAY"], g["TT"]
    eb, cb = {}, {}
    for i in range(len(DAY)):
        d = DAY[i]
        if d not in eb and TT[i] >= ENTRY_HM and TT[i] < EXIT_HM:
            eb[d] = i
        if TT[i] <= EXIT_HM:
            cb[d] = i
    return eb, cb


def backtest(g, ext, lot, lb, dist, tgt=None, sl=None, period=None, allow_days=None):
    SPOT, ATMK, DT = g["SPOT"], g["ATMK"], g["DT"]
    eb, cb = intraday_bars(g)
    trades = []
    for d0 in sorted(eb):
        if period is not None and d0 not in period:
            continue
        if allow_days is not None and d0 not in allow_days:
            continue
        if d0 not in ext:
            continue
        hi, age_hi, lo, age_lo = ext[d0]
        e = eb[d0]; xend = cb[d0]; spot = SPOT[e]
        if age_hi < AGE or age_lo < AGE:
            continue
        if (hi - spot) < dist / 100 * spot or (spot - lo) < dist / 100 * spot or spot >= hi or spot <= lo:
            continue
        Khi = round(hi / STEP) * STEP; Klo = round(lo / STEP) * STEP
        if not (-10 <= round((Khi - ATMK[e]) / STEP) <= 10 and -10 <= round((Klo - ATMK[e]) / STEP) <= 10):
            continue
        ce_ep = r2._px(g, e, "CE", Khi); pe_ep = r2._px(g, e, "PE", Klo)
        if not (ce_ep > 0 and pe_ep > 0):
            continue
        P0 = ce_ep + pe_ep
        exit_i = xend; reason = "EOD"
        if tgt is not None:
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
        trades.append(dict(day=d0, pnl=gross - fee - slip, reason=reason, hold=exit_i - e))
    return trades


def stats(trades):
    if not trades:
        return None
    p = np.array([t["pnl"] for t in trades], dtype=float)
    gp = p[p > 0].sum(); gl = -p[p < 0].sum()
    rng = np.random.default_rng(7)
    boots = np.array([rng.choice(p, len(p), replace=True).mean() for _ in range(2000)])
    return dict(n=len(p), net=float(p.sum()), avg=float(p.mean()), win=100 * (p > 0).mean(),
                pf=(gp / gl if gl > 0 else float("inf")), pval=float((boots <= 0).mean()),
                sharpe=float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) else 0.0,
                worst=float(p.min()), hold=float(np.mean([t["hold"] for t in trades])))


def show(label, trades):
    s = stats(trades)
    if s is None:
        print(f"  {label:26s} — 0 trades"); return
    print(f"  {label:26s} n={s['n']:>4} net={s['net']:>10,.0f} avg={s['avg']:>6,.0f} win={s['win']:>4.0f}% "
          f"PF={s['pf']:>4.2f} p={s['pval']:>5.3f} Sh={s['sharpe']:>5.2f} worst={s['worst']:>8,.0f} "
          f"hold={s['hold']:>4.1f}b")


def main():
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot() or 65
    print(f"PHASE-0d — Range Strangle INTRADAY (9:20 entry, same-day exit, NO overnight; lot={lot})\n")
    g = r2.grid("WEEK", "5m")
    dhl = P.daily_hl()
    ext_by_lb = {lb: P.trailing_extremes(dhl, lb) for lb in (5, 10)}
    all_days = set(g["DAY"]); tr_days = {d for d in all_days if d < EVO_END}; oos_days = {d for d in all_days if d >= EVO_END}

    for lb in (5, 10):
        ext = ext_by_lb[lb]
        for dist in (0.5, 0.75):
            print(f"=== lb={lb} dist={dist}% ===")
            for name, tgt, sl in [("EOD-only", None, None), ("T40/SL40", 40, 40),
                                  ("T30/SL60", 30, 60), ("T30/SL30", 30, 30)]:
                trf = backtest(g, ext, lot, lb, dist, tgt, sl)
                show(f"{name} FULL", trf)
                show(f"{name} train", backtest(g, ext, lot, lb, dist, tgt, sl, tr_days))
                show(f"{name} oos", backtest(g, ext, lot, lb, dist, tgt, sl, oos_days))
            print()


if __name__ == "__main__":
    main()
