"""bnf_pct_exit_test.py
## !!! RESULT INVALID — DO NOT USE (2026-08-31) !!!
##
## Pehla run: tgt50/SL25 -> Sharpe 4.72, tgt25/SL100 -> Sharpe 8.52, PF 10.75.
## Ye SAB fiction hai. Wajah = TRAP #198 (ATM-relative lake vs fixed strike):
##   - lake window = ATM+-10 (BNF = +-1000 pts)
##   - hold ke douran spot 400 pts se zyada khisak jaata hai 75% baar
##     (median 2-din drift 595 pts, p90 1226) -> ATM+-6 ki short leg window ke
##     BAHAR chali jaati hai -> `_px` chup-chaap INTRINSIC (OTM pe ~0) lauta deta hai
##   - combined premium 0 pe gir jaata hai -> koi bhi %-target turant "hit"
##   Saboot: 67,784 out-of-window lookups; target-exit trades ne median 421.7 pt
##   kamaye jabki credit 739.4 tha = 57% capture jab target sirf 50% tha.
##
## Ye test tab tak valid nahi jab tak +-20 lake download poora na ho.
 - user ka idea test: %-of-credit target/SL (₹4k basket ki jagah).

USER KA IDEA (2026-08-31):
   "02.10.01 me bas EXTREME ka wing khareed len sirf margin benefit ke liye, aur
    COMBINED PREMIUM ke 50% pe target aur 25% pe SL to kaisa rahega?"

## Kya test ho sakta hai aur kya NAHI

Extreme wings lake me hain hi NAHI (lake ATM+-10 tak, extreme wing usse door) - TRAP #198.
Unhe price karne ka koi imaandaar tareeka abhi nahi hai.

PAR idea ka asli hissa - **%-of-credit exit** - abhi test ho sakta hai, kyunki SHORTS
(ATM+-6) lake ke andar hain aur unka premium REAL hai. Extreme wing ka kaam hai:
  (a) margin ghatana        <- P&L pe asar ~zero
  (b) tail cap karna        <- fayda, par sirf un dino pe jo hamare sample me shayad hain hi nahi
  (c) thoda premium kharcha <- neeche `--wing-cost` se model kiya (per-leg, per-lot points)

Isliye ye test **naked shorts pe %-exit** chalata hai (real premium, real charges) aur
wing ka kharcha alag se ghata deta hai. Jo bacha - wo idea ka apna dum hai.

## Exit semantics (jaan-boojh ke saaf likha)

entry credit C (combined premium jo becha).
  TARGET : combined premium <= C x (1 - target_pct)   -> C ka target_pct% jeb me
  SL     : combined premium >= C x (1 + sl_pct)       -> C ka sl_pct% ka nuksan

User ke 50/25 ka matlab: 50% kamao, 25% ka nuksan jhelo (RR 2:1).

Usage:
    python bnf_pct_exit_test.py                    # 50/25 + sweep + baseline
    python bnf_pct_exit_test.py --wing-cost 4      # har wing 4 pt (2 wings = 8 pt/lot)
"""
import argparse
import math

import numpy as np
import pandas as pd

import bnf_920_strangle_intraday as base
import bs_option as bs

ENTRY_T = None  # set from base


def run_pct(g, off=6, target_pct=0.50, sl_pct=0.25, lots=5, max_hold_days=1,
            exp_squareoff_days=2, wing_cost_pts=0.0, slip_mult=1.0):
    """NAKED short strangle (real premium) + %-of-credit exits. Wing ka kharcha
    `wing_cost_pts` se ghataya jaata hai (per wing, per unit) - 2 wings."""
    bs.SLIP_MULT = slip_mult
    STEP = base.STEP
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    ENTRY = base.ENTRY_T if hasattr(base, "ENTRY_T") else None
    n = len(DT)
    day_first, last_bar, order_days = {}, {}, []
    for i in range(n):
        d = DAY[i]
        if d not in day_first and (ENTRY is None or TT[i] >= ENTRY):
            day_first[d] = i
            order_days.append(d)
        last_bar[d] = i

    rows, hold_until = [], None
    for d0 in order_days:
        if hold_until is not None and d0 <= hold_until:
            continue
        e = day_first[d0]
        exp = base._bnf_monthly_expiry(d0)
        if (exp - d0).days <= exp_squareoff_days:
            continue
        atmk = round(SPOT[e] / STEP) * STEP
        kc, kp = atmk + off * STEP, atmk - off * STEP
        sce, spe = base._px(g, e, "CE", kc), base._px(g, e, "PE", kp)
        if sce <= 0 or spe <= 0:
            continue
        credit = sce + spe
        if credit <= 0:
            continue
        lot = base.lot_for(d0)
        qty = lots * lot

        fut = [dd for dd in order_days if dd >= d0]
        dl = fut[min(max_hold_days, len(fut) - 1)]
        for dd in fut:
            if (exp - dd).days <= exp_squareoff_days:
                dl = min(dl, dd)
                break

        tgt_level = credit * (1.0 - target_pct)
        sl_level = credit * (1.0 + sl_pct)
        x, reason = None, "deadline"
        for i in range(e + 1, last_bar[dl] + 1):
            sc = base._px(g, i, "CE", kc)
            sp = base._px(g, i, "PE", kp)
            nv = sc + sp
            if nv <= 0:
                continue                      # bad data -> freeze, never act (TRAP #1)
            if nv <= tgt_level:
                x, reason = i, "target"; break
            if nv >= sl_level:
                x, reason = i, "SL"; break
        if x is None:
            x = last_bar[dl]

        xsce, xspe = base._px(g, x, "CE", kc), base._px(g, x, "PE", kp)
        when = pd.Timestamp(DT[e])
        gross = ((sce - xsce) + (spe - xspe)) * qty
        fee = (bs.calc_charges(sce, xsce, qty, entry_side="SELL", when=when) +
               bs.calc_charges(spe, xspe, qty, entry_side="SELL", when=when))
        slip = bs.slip_cost_leg(sce, xsce, qty) + bs.slip_cost_leg(spe, xspe, qty)
        wing = wing_cost_pts * 2 * qty        # 2 wings, ~poora premium doob jaata hai
        rows.append(dict(day=d0, exit_day=DAY[x], credit=credit, qty=qty,
                         gross=gross, fee=fee, slip=slip, wing=wing,
                         net=gross - fee - slip - wing, reason=reason))
        hold_until = DAY[x]
    return pd.DataFrame(rows)


def report(df, label):
    if len(df) < 20:
        print("  %-34s n=%d (bahut kam)" % (label, len(df))); return None
    net = df.net
    yrs = max((pd.Timestamp(df.exit_day.max()) - pd.Timestamp(df.day.min())).days / 365.25, .25)
    sh = (net.mean() / net.std() * math.sqrt(len(net) / yrs)) if net.std() else 0
    pf = net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else float("inf")
    eq = net.cumsum()
    dd = (eq - eq.cummax()).min()
    mix = df.reason.value_counts(normalize=True) * 100
    print("  %-34s n=%-5d net=Rs%-11s Sharpe=%5.2f PF=%5.2f win=%4.1f%% "
          "worst=Rs%-9s maxDD=Rs%-10s [tgt %.0f%% sl %.0f%% dl %.0f%%]"
          % (label, len(net), format(int(net.sum()), ","), sh, pf,
             100 * (net > 0).mean(), format(int(net.min()), ","),
             format(int(dd), ","), mix.get("target", 0), mix.get("SL", 0),
             mix.get("deadline", 0)))
    return dict(n=len(net), net=net.sum(), sh=sh, pf=pf, dd=dd,
                win=100 * (net > 0).mean(), worst=net.min())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wing-cost", type=float, default=3.0,
                    help="har extreme wing ka premium (points/unit); 2 wings lagte hain")
    ap.add_argument("--lots", type=int, default=5)
    a = ap.parse_args()

    g = base.load_grid()
    print("=" * 124)
    print("02.10.01 variant - %%-of-credit exit (real BNF premium, %d lot, wing cost %.1f pt x2)"
          % (a.lots, a.wing_cost))
    print("=" * 124)

    print("\n>> USER KA IDEA: target 50%% / SL 25%%")
    base_r = report(run_pct(g, target_pct=0.50, sl_pct=0.25, lots=a.lots,
                            wing_cost_pts=a.wing_cost), "target 50% / SL 25%")

    print("\n>> SL badal ke dekho (target 50% fix)")
    for sl in (0.25, 0.50, 0.75, 1.00, 1.50):
        report(run_pct(g, target_pct=0.50, sl_pct=sl, lots=a.lots,
                       wing_cost_pts=a.wing_cost), "tgt 50%% / SL %3.0f%%" % (sl * 100))

    print("\n>> Target badal ke dekho (SL 100% fix — aam short-vol practice)")
    for tp in (0.25, 0.35, 0.50, 0.65):
        report(run_pct(g, target_pct=tp, sl_pct=1.00, lots=a.lots,
                       wing_cost_pts=a.wing_cost), "tgt %3.0f%% / SL 100%%" % (tp * 100))

    print("\n>> Wing ka kharcha kitna kha jaata hai (tgt 50 / SL 25)")
    for wc in (0.0, 3.0, 6.0, 12.0):
        report(run_pct(g, target_pct=0.50, sl_pct=0.25, lots=a.lots,
                       wing_cost_pts=wc), "wing %4.1f pt x2" % wc)

    print("\n  NOTE: ye NAKED shorts pe hai + wing ka premium ghataya hua. Extreme wing ka")
    print("  TAIL-CAP fayda isme NAHI hai (lake wahan tak jaata hi nahi) - yaani asli")
    print("  hedged version ka worst-case isse BEHTAR hoga, par kitna, wo abhi nahi keh sakte.")


if __name__ == "__main__":
    main()
