"""nifty_pct_exit_test.py - user ka %-of-credit exit idea, NIFTY pe (real premium).

USER KA IDEA (2026-08-31): "02.10.01 me combined premium ke 50% pe target aur 25% pe SL"

BNF pe ye test TRAP #198 se contaminated tha (lake ATM-relative, +-10 window = +-1000
pts, par BNF ka 2-din drift median 595 pts -> 75% holds me short leg window ke BAHAR).

NIFTY behtar hai par muft nahi:
  lake window (contiguous) = ATM+-10 = +-500 pts
  2-din max drift : median 214 / p75 320 / p90 438  -> 54% holds > 200 pts
  intraday drift  : median 130 / p75 187 / p90 264  -> 22% din  > 200 pts

## Is script ka fark (kyun ispe bharosa kiya ja sakta hai)

`_px_strict` out-of-window / khaali cell pe **None** deta hai, intrinsic NAHI.
Jo trade apni poori zindagi me ek baar bhi None chhu le, wo trade **SKIP** hoti hai
aur `skipped` me ginti hai. Yaani "short muft me buy-back" wala fake profit ban hi
nahi sakta.

## Jo ab bhi imaandaari se bolna zaroori hai (SELECTION BIAS)

Skip random NAHI hai. Leg window ke bahar tab jaati hai jab spot us leg ke KHILAAF
bhaga ho - yaani jo trade skip hote hain wo aksar HAARNE waale hote hain. Isliye
clean-subset ka result asli se BEHTAR dikhega. Coverage % har row me isliye chhapta
hai: coverage jitni kam, number utne kam bharose ke.

Lot: NIFTY 65 (scrip master). Purane saalon me NSE ka lot alag tha, isliye per-unit
POINTS primary metric hai; Rs ek fix lot pe scale hai.
"""
import argparse
import datetime as dt
import math
import os

import numpy as np
import pandas as pd

import bnf_920_strangle_intraday as base   # loader reuse (Rule 6B)
import bs_option as bs
import expiry_calendar as xcal

LOT = 65


def use_nifty():
    """Point the shared loader at the NIFTY lake (STEP 50, not BNF 100)."""
    base.LAKE = os.path.abspath(os.path.join(
        base.HERE, "..", "..", "_TRADING_DATA", "OptChainLake_1m", "NIFTY", "MONTH"))
    base.STEP = 50


def _nifty_monthly_expiry(d):
    wd = xcal.monthly_expiry_weekday(d)
    exp = xcal._last_weekday_of_month(d.year, d.month, wd)
    if exp < d:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        exp = xcal._last_weekday_of_month(y, m, wd)
    return exp


def _px_strict(g, i, side, K):
    """Real premium, ya None. Intrinsic KABHI nahi (TRAP #198)."""
    off = int(round((K - g["ATMK"][i]) / base.STEP))
    win = g.get("WIN") or 10
    if -win <= off <= win:
        v = (g["CE"] if side == "CE" else g["PE"])[i, off + win]
        if v and not np.isnan(v) and v > 0:
            return float(v)
    return None


def run(g, off=6, target_pct=0.50, sl_pct=0.25, lots=1, intraday=False,
        max_hold_days=1, exp_squareoff_days=2, wing_cost_pts=0.0):
    STEP = base.STEP
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    ENTRY = base.ENTRY_T
    n = len(DT)
    day_first, last_bar, order_days = {}, {}, []
    for i in range(n):
        d = DAY[i]
        if d not in day_first and TT[i] >= ENTRY:
            day_first[d] = i
            order_days.append(d)
        last_bar[d] = i

    rows, skipped, hold_until = [], 0, None
    for d0 in order_days:
        if hold_until is not None and d0 <= hold_until:
            continue
        e = day_first[d0]
        exp = _nifty_monthly_expiry(d0)
        if (exp - d0).days <= exp_squareoff_days:
            continue
        atmk = round(SPOT[e] / STEP) * STEP
        kc, kp = atmk + off * STEP, atmk - off * STEP
        sce, spe = _px_strict(g, e, "CE", kc), _px_strict(g, e, "PE", kp)
        if sce is None or spe is None:
            skipped += 1
            continue
        credit = sce + spe
        if credit <= 0:
            skipped += 1
            continue

        if intraday:
            dl = d0
        else:
            fut = [dd for dd in order_days if dd >= d0]
            dl = fut[min(max_hold_days, len(fut) - 1)]
            for dd in fut:
                if (exp - dd).days <= exp_squareoff_days:
                    dl = min(dl, dd)
                    break

        tgt_level = credit * (1.0 - target_pct)
        sl_level = credit * (1.0 + sl_pct)
        end = last_bar[dl]
        if intraday:
            end = last_bar[d0]
            for j in range(e + 1, last_bar[d0] + 1):
                if TT[j] >= dt.time(15, 10):     # CAS squareoff, exit_time_config
                    end = j
                    break

        x, reason, dirty = None, "deadline", False
        for i in range(e + 1, end + 1):
            sc = _px_strict(g, i, "CE", kc)
            sp = _px_strict(g, i, "PE", kp)
            if sc is None or sp is None:
                dirty = True                  # leg lake se bahar -> trade bharose ka nahi
                break
            nv = sc + sp
            if nv <= tgt_level:
                x, reason = i, "target"
                break
            if nv >= sl_level:
                x, reason = i, "SL"
                break
        if dirty:
            skipped += 1
            continue
        if x is None:
            x = end
        xsce, xspe = _px_strict(g, x, "CE", kc), _px_strict(g, x, "PE", kp)
        if xsce is None or xspe is None:
            skipped += 1
            continue

        qty = lots * LOT
        when = pd.Timestamp(DT[e])
        gross = ((sce - xsce) + (spe - xspe)) * qty
        fee = (bs.calc_charges(sce, xsce, qty, entry_side="SELL", when=when) +
               bs.calc_charges(spe, xspe, qty, entry_side="SELL", when=when))
        slip = bs.slip_cost_leg(sce, xsce, qty) + bs.slip_cost_leg(spe, xspe, qty)
        wing = wing_cost_pts * 2 * qty
        rows.append(dict(day=d0, exit_day=DAY[x], credit=credit, qty=qty,
                         gross=gross, fee=fee, slip=slip, wing=wing,
                         net=gross - fee - slip - wing, reason=reason))
        hold_until = DAY[x]
    df = pd.DataFrame(rows)
    df.attrs["skipped"] = skipped
    return df


def report(df, label):
    sk = df.attrs.get("skipped", 0)
    tot = len(df) + sk
    cov = 100.0 * len(df) / tot if tot else 0.0
    if len(df) < 20:
        print("  %-28s n=%-4d (bahut kam)  coverage %.0f%%" % (label, len(df), cov))
        return
    net = df.net
    unit = net / df.qty
    yrs = max((pd.Timestamp(df.exit_day.max()) - pd.Timestamp(df.day.min())).days / 365.25, .25)
    sh = (net.mean() / net.std() * math.sqrt(len(net) / yrs)) if net.std() else 0
    pf = net[net > 0].sum() / -net[net < 0].sum() if (net < 0).any() else float("inf")
    eq = net.cumsum()
    dd = (eq - eq.cummax()).min()
    mix = df.reason.value_counts(normalize=True) * 100
    print("  %-28s n=%-4d cov=%3.0f%% |%+7.2f pt | Rs%-10s Sh=%5.2f PF=%5.2f win=%4.1f%% "
          "worst=Rs%-9s DD=Rs%-9s [t%.0f s%.0f d%.0f]"
          % (label, len(net), cov, unit.mean(), format(int(net.sum()), ","), sh, pf,
             100 * (net > 0).mean(), format(int(net.min()), ","), format(int(dd), ","),
             mix.get("target", 0), mix.get("SL", 0), mix.get("deadline", 0)))


def perm_p(df, iters=5000, seed=7):
    if len(df) < 20:
        return float("nan")
    rng = np.random.default_rng(seed)
    v = df.net.values
    obs = v.mean()
    sim = (v * rng.choice([-1, 1], size=(iters, len(v)))).mean(axis=1)
    return float((sim >= obs).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lots", type=int, default=5)
    ap.add_argument("--wing-cost", type=float, default=3.0)
    a = ap.parse_args()

    use_nifty()
    g = base.load_grid()
    print("=" * 128)
    print("NIFTY %%-of-credit exit (REAL premium, out-of-window trades SKIPPED, "
          "%d lot x %d, wing %.1f pt x2)" % (a.lots, LOT, a.wing_cost))
    print("=" * 128)

    variants = [(0.50, 0.25, "USER: tgt 50% / SL 25%"),
                (0.50, 1.00, "tgt 50% / SL 100%"),
                (0.25, 1.00, "tgt 25% / SL 100%"),
                (9.99, 9.99, "BASELINE (no %-exit)")]

    print("\n>> POSITIONAL (next-day hold, 02.10.01 jaisa)")
    for tp, sl, lbl in variants:
        report(run(g, target_pct=tp, sl_pct=sl, lots=a.lots,
                   wing_cost_pts=a.wing_cost), lbl)

    print("\n>> INTRADAY (9:20 -> 15:10 same din) - lake coverage yahan behtar")
    for tp, sl, lbl in variants:
        report(run(g, target_pct=tp, sl_pct=sl, lots=a.lots, intraday=True,
                   wing_cost_pts=a.wing_cost), lbl)

    print("\n>> Strike offset sweep (intraday, tgt 50 / SL 25) - andar ka strike = behtar coverage")
    for off in (2, 3, 4, 6, 8):
        report(run(g, off=off, target_pct=0.50, sl_pct=0.25, lots=a.lots,
                   intraday=True, wing_cost_pts=a.wing_cost),
               "ATM+-%d (%d pts)" % (off, off * base.STEP))

    print("\n>> Significance (permutation, sign-flip null)")
    for intr, lbl in ((False, "positional"), (True, "intraday")):
        d = run(g, target_pct=0.50, sl_pct=0.25, lots=a.lots, intraday=intr,
                wing_cost_pts=a.wing_cost)
        print("  %-12s tgt50/SL25  perm-p = %.4f  (n=%d, cov=%.0f%%)"
              % (lbl, perm_p(d), len(d),
                 100.0 * len(d) / max(1, len(d) + d.attrs.get("skipped", 0))))

    print("\n  ! SELECTION BIAS: skip random nahi hai - leg window ke bahar tab jaati hai")
    print("    jab spot uske KHILAAF bhaga ho, yaani skipped trades aksar HAARNE waale")
    print("    the. Coverage jitni kam, ye number utne hi optimistic hain.")


if __name__ == "__main__":
    main()
