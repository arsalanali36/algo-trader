"""
02.10.01 short-leg SELECTION comparison (positional hedged, max_hold=1, 5yr real lake).
User Q: same-strike +-6 gives unequal premiums (CE 549 vs PE 407) = built-in delta
bias -> position shows -ve from entry. Does balancing (same-premium / same-delta)
change the 5yr result + reduce the entry bias?

3 rules for the SHORT legs (wings always +5 strikes beyond each short, BS):
  A same-strike  : CE +6, PE -6              (current live)
  B same-premium : offsets 4..9 each, min |CE_prem - PE_prem|  (real lake premium)
  C same-delta   : CE offset ~+0.16 delta, PE offset ~-0.16 delta (BS)
Real premium shorts + BS wings + real date-aware Zerodha charges + DOM slip.
"""
import os, sys, math, numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import bnf_920_strangle_intraday as base
import bs_option as bs

STEP = base.STEP
ENTRY_T, EXIT_T = base.ENTRY_T, base.EXIT_T
WING = 5
RANGE = range(4, 10)          # candidate short offsets with real lake premium

def _wing(S, K, T, sig, ot):
    if T <= 0 or sig <= 0 or S <= 0:
        return max(0.0, (S - K) if ot == "CE" else (K - S))
    return bs.bs_price(S, K, T, sig, opt=ot)

def _select(rule, g, e, atmk, S0, T, sig):
    """Return (ce_off, pe_off) short offsets for this entry."""
    if rule == "A":
        return 6, 6
    ce = [(o, base._px(g, e, "CE", atmk + o * STEP)) for o in RANGE]
    pe = [(o, base._px(g, e, "PE", atmk - o * STEP)) for o in RANGE]
    ce = [(o, p) for o, p in ce if p > 0]; pe = [(o, p) for o, p in pe if p > 0]
    if not ce or not pe:
        return 6, 6
    if rule == "B":                       # premium-matched (real)
        best, bd = (6, 6), 1e9
        for co, cp in ce:
            for po, pp in pe:
                d = abs(cp - pp) + 0.5 * abs((co + po) - 12)   # tie-break toward symmetric
                if d < bd:
                    bd, best = d, (co, po)
        return best
    # rule C: delta-matched ~0.16 (BS delta from ATM-straddle sigma)
    co = min(ce, key=lambda x: abs(abs(bs.bs_delta(S0, atmk + x[0] * STEP, T, sig, opt="CE")) - 0.16))[0]
    po = min(pe, key=lambda x: abs(abs(bs.bs_delta(S0, atmk - x[0] * STEP, T, sig, opt="PE")) - 0.16))[0]
    return co, po

def run(g, rule, basket_sl=4000.0, basket_tgt=8000.0, lots=5, max_hold=1, exp_sq=2):
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    n = len(DT)
    first920, last_bar, order_days = {}, {}, []
    for i in range(n):
        d = DAY[i]
        if d not in first920 and TT[i] >= ENTRY_T:
            first920[d] = i; order_days.append(d)
        last_bar[d] = i
    rows = []; hold_until = None
    for d0 in order_days:
        if hold_until is not None and d0 <= hold_until:
            continue
        e = first920[d0]; exp = base._bnf_monthly_expiry(d0)
        if (exp - d0).days <= exp_sq:
            continue
        T = max((exp - d0).days, 1) / 365.0; S0 = SPOT[e]
        atmk = round(S0 / STEP) * STEP
        strad = base._px(g, e, "CE", atmk) + base._px(g, e, "PE", atmk)
        sig = strad / (0.8 * S0 * math.sqrt(T)) if (strad > 0 and S0 > 0) else 0.0
        co, po = _select(rule, g, e, atmk, S0, T, sig)
        kc_s, kp_s = atmk + co * STEP, atmk - po * STEP
        kc_w, kp_w = atmk + (co + WING) * STEP, atmk - (po + WING) * STEP
        sce, spe = base._px(g, e, "CE", kc_s), base._px(g, e, "PE", kp_s)
        if sce <= 0 or spe <= 0:
            continue
        wce, wpe = _wing(S0, kc_w, T, sig, "CE"), _wing(S0, kp_w, T, sig, "PE")
        entry_credit = (sce + spe) - (wce + wpe)
        # entry NET DELTA (position sign: short shorts, long wings)
        net_delta = (-bs.bs_delta(S0, kc_s, T, sig, opt="CE") - bs.bs_delta(S0, kp_s, T, sig, opt="PE")
                     + bs.bs_delta(S0, kc_w, T, sig, opt="CE") + bs.bs_delta(S0, kp_w, T, sig, opt="PE"))
        leg_gap = abs(sce - spe)                      # premium imbalance at entry
        lot = base.lot_for(d0); qty = lots * lot
        fut = [dd for dd in order_days if dd >= d0]
        deadline = fut[min(max_hold, len(fut) - 1)]
        for dd in fut:
            if (exp - dd).days <= exp_sq:
                deadline = min(deadline, dd); break
        x, reason = None, "deadline"
        for i in range(e + 1, last_bar[deadline] + 1):
            di = DAY[i]; Ti = max((exp - di).days, 1) / 365.0; Si = SPOT[i]
            sc, sp = base._px(g, i, "CE", kc_s), base._px(g, i, "PE", kp_s)
            wc, wp = _wing(Si, kc_w, Ti, sig, "CE"), _wing(Si, kp_w, Ti, sig, "PE")
            basket = (entry_credit - ((sc + sp) - (wc + wp))) * qty
            if basket <= -abs(basket_sl):
                x, reason = i, "SL"; break
            if basket >= abs(basket_tgt):
                x, reason = i, "target"; break
        if x is None:
            x = last_bar[deadline]
        Sx = SPOT[x]; Tx = max((exp - DAY[x]).days, 1) / 365.0
        xsce, xspe = base._px(g, x, "CE", kc_s), base._px(g, x, "PE", kp_s)
        xwce, xwpe = _wing(Sx, kc_w, Tx, sig, "CE"), _wing(Sx, kp_w, Tx, sig, "PE")
        when = pd.Timestamp(DT[e])
        gross = ((sce - xsce) + (spe - xspe) + (xwce - wce) + (xwpe - wpe)) * qty
        fee = (bs.calc_charges(sce, xsce, qty, "SELL", when) + bs.calc_charges(spe, xspe, qty, "SELL", when) +
               bs.calc_charges(wce, xwce, qty, "BUY", when) + bs.calc_charges(wpe, xwpe, qty, "BUY", when))
        slip = (bs.slip_cost_leg(sce, xsce, qty) + bs.slip_cost_leg(spe, xspe, qty) +
                bs.slip_cost_leg(wce, xwce, qty) + bs.slip_cost_leg(wpe, xwpe, qty))
        rows.append(dict(day=d0, net=gross - fee - slip, reason=reason,
                         net_delta=net_delta, leg_gap=leg_gap, co=co, po=po))
        hold_until = DAY[x]
    return pd.DataFrame(rows)

def kpi(df, yrs, label):
    net = df.net.values; n = len(net)
    sh = net.mean() / net.std() * math.sqrt(252) if net.std() > 0 else 0
    eq = np.cumsum(net); dd = (eq - np.maximum.accumulate(eq)).min()
    pf = net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else 9.99
    print(f"{label:<16} {n:>4} {net.sum()/yrs:>10,.0f} {sh:>6.2f} {pf:>5.2f} "
          f"{(net>0).mean()*100:>4.0f}% {net.min():>9,.0f} {dd:>10,.0f} "
          f"{abs(df.net_delta.mean()):>6.3f} {df.leg_gap.mean():>7.1f}  {(net<-4000).sum():>3}")

if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    g = base.load_grid()
    d0, d1 = pd.Timestamp(g["DT"][0]), pd.Timestamp(g["DT"][-1]); yrs = (d1 - d0).days / 365.25
    print(f"span {d0.date()}->{d1.date()} ({yrs:.2f}yr)  positional max_hold=1, SL4k/Tgt8k, 5 lots\n")
    print(f"{'rule':<16} {'n':>4} {'net/yr':>10} {'Shrp':>6} {'PF':>5} {'win':>5} {'worst':>9} {'maxDD':>10} {'|netD|':>6} {'legGap':>7}  >4k")
    print("-" * 96)
    kpi(run(g, "A"), yrs, "A same-strike6")
    kpi(run(g, "B"), yrs, "B same-premium")
    kpi(run(g, "C"), yrs, "C same-delta16")
