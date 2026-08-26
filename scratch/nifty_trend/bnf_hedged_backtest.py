"""
02.10.01 — BANKNIFTY 9:20 HEDGED short strangle backtest (real premium + BS wings).

Live config (nifty_config bnf_strangle_hedged, Rule 10):
  entry 09:20 (window 10m), SELL CE+PE @ ATM+-6 strikes, BUY wings @ ATM+-11
  (6+5 further OTM), exit = combined-BASKET +-Rs4,000 (ORDERED close) OR 14:55,
  1 trade/day, 5 lots. Per-leg RMS SL off (basket is the only stop).

Data:  shorts (ATM+-6) = REAL OptChainLake_1m/BANKNIFTY/MONTH premium (as the naked
       02.10 engine). Wings (ATM+-11) are 1 strike BEYOND the lake's +-10 window, so
       priced by Black-Scholes (bs_option) at each minute — sigma inverted from the
       real ATM straddle. A far-OTM wing is a small, low-delta leg; its BS estimate
       perturbs the (real, short-driven) basket P&L only slightly. Documented caveat.
Costs: real date-aware Zerodha charges + DOM slippage (shorts SELL, wings BUY).

Reuses bnf_920_strangle_intraday (load_grid/_px/lot_for/_bnf_monthly_expiry) — Rule 6B.
"""
import os, sys, math, datetime as dt
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bnf_920_strangle_intraday as base
import bs_option as bs

STEP = base.STEP
ENTRY_T, EXIT_T = base.ENTRY_T, base.EXIT_T


def _bs_wing(S, K, T, sigma, ot):
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(0.0, (S - K) if ot == "CE" else (K - S))
    return bs.bs_price(S, K, T, sigma, opt=ot)


def run_hedged(g, off=6, wing=5, basket_sl=4000.0, basket_tgt=4000.0, lots=5,
               skip_expiry=True, slip_mult=1.0, exit_mode="basket", pt=50.0):
    """Real-premium shorts + BS wings.
    exit_mode='basket': exit on Rs-basket SL/target (date-aware qty) — the LIVE 02.10.01 rule.
    exit_mode='pts'   : exit on COMBINED-premium points +-pt (naked-style, lot-independent)."""
    bs.SLIP_MULT = slip_mult
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    n = len(DT)
    entry_i, exit_i, last_bar = {}, {}, {}
    for i in range(n):
        d = DAY[i]
        if d not in entry_i and TT[i] >= ENTRY_T:
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
        exp = base._bnf_monthly_expiry(d)
        if skip_expiry and (exp - d).days <= 0:
            continue
        T = max((exp - d).days, 1) / 365.0
        atmk = round(SPOT[e] / STEP) * STEP
        # short strikes (ATM+-off) — REAL lake
        kc_s, kp_s = atmk + off * STEP, atmk - off * STEP
        # wing strikes (ATM+-(off+wing)) — BS
        kc_w, kp_w = atmk + (off + wing) * STEP, atmk - (off + wing) * STEP
        sce, spe = base._px(g, e, "CE", kc_s), base._px(g, e, "PE", kp_s)
        if sce <= 0 or spe <= 0:
            continue
        # sigma (annualised) from the real ATM straddle: straddle ~= 0.8*S*sigma*sqrt(T)
        strad = base._px(g, e, "CE", atmk) + base._px(g, e, "PE", atmk)
        S0 = SPOT[e]
        sigma = strad / (0.8 * S0 * math.sqrt(T)) if (strad > 0 and S0 > 0) else 0.0
        wce = _bs_wing(S0, kc_w, T, sigma, "CE")
        wpe = _bs_wing(S0, kp_w, T, sigma, "PE")
        entry_credit = (sce + spe) - (wce + wpe)      # net credit (per lot, points)
        lot = base.lot_for(d)
        qty = lots * lot

        x, reason = xend, "eod"
        for i in range(e + 1, xend + 1):
            Si = SPOT[i]
            sc = base._px(g, i, "CE", kc_s); sp = base._px(g, i, "PE", kp_s)
            wc = _bs_wing(Si, kc_w, T, sigma, "CE"); wp = _bs_wing(Si, kp_w, T, sigma, "PE")
            net_val = (sc + sp) - (wc + wp)
            if exit_mode == "pts":
                mtm = entry_credit - net_val            # combined-premium points
                if mtm <= -abs(pt):
                    x, reason = i, "SL"; break
                if mtm >= abs(pt):
                    x, reason = i, "target"; break
            else:
                basket = (entry_credit - net_val) * qty     # +ve = profit (Rs, whole structure)
                if basket <= -abs(basket_sl):
                    x, reason = i, "SL"; break
                if basket >= abs(basket_tgt):
                    x, reason = i, "target"; break

        Sx = SPOT[x]
        xsce, xspe = base._px(g, x, "CE", kc_s), base._px(g, x, "PE", kp_s)
        xwce = _bs_wing(Sx, kc_w, T, sigma, "CE"); xwpe = _bs_wing(Sx, kp_w, T, sigma, "PE")
        when = pd.Timestamp(DT[e])
        # gross = short credit captured − wing debit paid (all × lot)
        gross = ((sce - xsce) + (spe - xspe) + (xwce - wce) + (xwpe - wpe)) * lot
        fee = (bs.calc_charges(sce, xsce, lot, entry_side="SELL", when=when) +
               bs.calc_charges(spe, xspe, lot, entry_side="SELL", when=when) +
               bs.calc_charges(wce, xwce, lot, entry_side="BUY", when=when) +
               bs.calc_charges(wpe, xwpe, lot, entry_side="BUY", when=when))
        slip = (bs.slip_cost_leg(sce, xsce, lot) + bs.slip_cost_leg(spe, xspe, lot) +
                bs.slip_cost_leg(wce, xwce, lot) + bs.slip_cost_leg(wpe, xwpe, lot))
        net = gross - fee - slip
        rows.append(dict(day=d, net=net, gross=gross, fee=fee, slip=slip,
                         reason=reason, off=off, dte=(exp - d).days))
    return pd.DataFrame(rows)


def run_positional(g, off=6, wing=5, basket_sl=4000.0, basket_tgt=8000.0, lots=5,
                   max_hold_days=3, exp_squareoff_days=2, slip_mult=1.0):
    """HEDGED strangle held ACROSS days (positional). Enter first bar>=09:20; hold
    forward across trading days until basket target/SL, OR (expiry-exp_squareoff_days)
    squareoff, OR max_hold_days. Wings BS-priced with per-bar T (days decay). Downside
    is wing-capped every leg. No re-entry while a position is open."""
    bs.SLIP_MULT = slip_mult
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    n = len(DT)
    day_first920, last_bar = {}, {}
    order_days = []
    for i in range(n):
        d = DAY[i]
        if d not in day_first920 and TT[i] >= ENTRY_T:
            day_first920[d] = i
            order_days.append(d)
        last_bar[d] = i
    rows = []
    hold_until_day = None
    for d0 in order_days:
        if hold_until_day is not None and d0 <= hold_until_day:
            continue                                 # still holding a position
        e = day_first920[d0]
        exp = base._bnf_monthly_expiry(d0)
        if (exp - d0).days <= exp_squareoff_days:
            continue                                 # too close to expiry to open
        atmk = round(SPOT[e] / STEP) * STEP
        kc_s, kp_s = atmk + off * STEP, atmk - off * STEP
        kc_w, kp_w = atmk + (off + wing) * STEP, atmk - (off + wing) * STEP
        sce, spe = base._px(g, e, "CE", kc_s), base._px(g, e, "PE", kp_s)
        if sce <= 0 or spe <= 0:
            continue
        T0 = max((exp - d0).days, 1) / 365.0
        S0 = SPOT[e]
        strad = base._px(g, e, "CE", atmk) + base._px(g, e, "PE", atmk)
        sigma = strad / (0.8 * S0 * math.sqrt(T0)) if (strad > 0 and S0 > 0) else 0.0
        wce = _bs_wing(S0, kc_w, T0, sigma, "CE"); wpe = _bs_wing(S0, kp_w, T0, sigma, "PE")
        entry_credit = (sce + spe) - (wce + wpe)
        lot = base.lot_for(d0); qty = lots * lot
        # deadline day = min(expiry-squareoff, entry + max_hold_days trading days)
        fut = [dd for dd in order_days if dd >= d0]
        dl_idx = min(max_hold_days, len(fut) - 1)
        deadline_day = fut[dl_idx]
        # cap at expiry-squareoff
        for dd in fut:
            if (exp - dd).days <= exp_squareoff_days:
                deadline_day = min(deadline_day, dd); break
        x, reason = None, "deadline"
        for i in range(e + 1, last_bar[deadline_day] + 1):
            di = DAY[i]
            Ti = max((exp - di).days, 1) / 365.0
            Si = SPOT[i]
            sc = base._px(g, i, "CE", kc_s); sp = base._px(g, i, "PE", kp_s)
            wc = _bs_wing(Si, kc_w, Ti, sigma, "CE"); wp = _bs_wing(Si, kp_w, Ti, sigma, "PE")
            net_val = (sc + sp) - (wc + wp)
            basket = (entry_credit - net_val) * qty
            if basket <= -abs(basket_sl):
                x, reason = i, "SL"; break
            if basket >= abs(basket_tgt):
                x, reason = i, "target"; break
        if x is None:
            x = last_bar[deadline_day]
        Sx = SPOT[x]; Tx = max((exp - DAY[x]).days, 1) / 365.0
        xsce, xspe = base._px(g, x, "CE", kc_s), base._px(g, x, "PE", kp_s)
        xwce = _bs_wing(Sx, kc_w, Tx, sigma, "CE"); xwpe = _bs_wing(Sx, kp_w, Tx, sigma, "PE")
        when = pd.Timestamp(DT[e])
        gross = ((sce - xsce) + (spe - xspe) + (xwce - wce) + (xwpe - wpe)) * lot
        fee = (bs.calc_charges(sce, xsce, lot, "SELL", when) + bs.calc_charges(spe, xspe, lot, "SELL", when) +
               bs.calc_charges(wce, xwce, lot, "BUY", when) + bs.calc_charges(wpe, xwpe, lot, "BUY", when))
        slip = (bs.slip_cost_leg(sce, xsce, lot) + bs.slip_cost_leg(spe, xspe, lot) +
                bs.slip_cost_leg(wce, xwce, lot) + bs.slip_cost_leg(wpe, xwpe, lot))
        rows.append(dict(day=d0, exit_day=DAY[x], hold=(pd.Timestamp(DT[x]) - when).days,
                         net=gross - fee - slip, gross=gross, reason=reason))
        hold_until_day = DAY[x]
    return pd.DataFrame(rows)


def report(df, label):
    if len(df) < 20:
        print(f"  {label:<30} n={len(df):<4} (too few)"); return None
    net = df.net
    wr = (net > 0).mean() * 100
    sh = (net.mean() / net.std() * math.sqrt(252)) if net.std() > 0 else 0
    tail = net.nsmallest(max(1, len(net) // 20)).sum()
    rc = df.reason.value_counts(normalize=True) * 100
    mix = " ".join(f"{k}:{rc.get(k,0):.0f}%" for k in ("target", "SL", "eod"))
    print(f"  {label:<30} n={len(net):<4} win%={wr:4.0f}  net=Rs{net.sum():>10,.0f}  "
          f"avg=Rs{net.mean():>6,.0f}  Sharpe={sh:5.2f}  worst=Rs{net.min():>8,.0f}  "
          f"w5%=Rs{tail:>9,.0f}  PF={(net[net>0].sum()/-net[net<0].sum()) if (net<0).any() else float('inf'):.2f}  [{mix}]")
    return sh


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    print("Loading BANKNIFTY MONTH lake...", flush=True)
    g = base.load_grid()
    print(f"  bars={len(g['DT'])}  days={len(set(g['DAY']))}  span {g['DT'][0]}->{g['DT'][-1]}\n", flush=True)

    print("== 02.10.01 HEDGED: SELL ATM+-6, BUY wings ATM+-11, exit +-Rs4000 basket or 14:55, 5 lots ==")
    d = run_hedged(g, off=6, wing=5, basket_sl=4000, basket_tgt=4000, lots=5)
    report(d, "LIVE config (off6/wing5/Rs4k)")

    print("\n== sensitivity ==")
    for sl, tg in ((3000, 3000), (5000, 5000), (4000, 6000)):
        report(run_hedged(g, 6, 5, sl, tg, 5), f"basket +Rs{tg}/-Rs{sl}")
    for w in (4, 6):
        report(run_hedged(g, 6, w, 4000, 4000, 5), f"wing {w} strikes (ATM+-{6+w})")
    print("\n== slippage stress (live config) ==")
    for m in (2.0, 3.0):
        report(run_hedged(g, 6, 5, 4000, 4000, 5, slip_mult=m), f"slip x{m:.0f}")

    if len(d) >= 20:
        yr = d.copy(); yr["y"] = pd.to_datetime(yr.day).dt.year
        print("\n  by year (live config):")
        for y, grp in yr.groupby("y"):
            print(f"    {y}  n={len(grp):<4} win%={(grp.net>0).mean()*100:4.0f}  "
                  f"net=Rs{grp.net.sum():>10,.0f}  avg=Rs{grp.net.mean():>6,.0f}  worst=Rs{grp.net.min():>8,.0f}")
        # train/OOS 65/35 by date
        dd = d.sort_values("day").reset_index(drop=True)
        cut = dd.day.iloc[int(len(dd) * 0.65)]
        tr, oo = dd[dd.day < cut], dd[dd.day >= cut]
        def _sh(x): return (x.net.mean()/x.net.std()*math.sqrt(252)) if len(x)>1 and x.net.std()>0 else 0
        print(f"\n  TRAIN (<{cut}) n={len(tr)} net=Rs{tr.net.sum():,.0f} Sharpe={_sh(tr):.2f}  |  "
              f"OOS (>={cut}) n={len(oo)} net=Rs{oo.net.sum():,.0f} Sharpe={_sh(oo):.2f}")
        print(f"\n  held-strike fallback (shorts, out-of-window): "
              f"{base._FALLBACK[0]}/{base._FALLBACK[1]} = {100*base._FALLBACK[0]/max(base._FALLBACK[1],1):.2f}%")
        d.to_csv(os.path.join(HERE, "bnf_hedged_trades.csv"), index=False)
        print(f"\n  wrote bnf_hedged_trades.csv ({len(d)} trades)")
