"""Ars chain — strike ladder (ATM..ITM4) + per-trade distribution + SL/target sweep.

Answers three questions the BUY-vs-SELL result opened up, all on the SAME 9-year trade
list the live engine produces (range_trader.run_signal_engine via arschain_backtest —
never a copy, LESSONS TRAP #131):

  A) STRIKE LADDER   ATM vs ITM 1/2/3/4. Deeper ITM = more delta (premium tracks spot)
                     but more capital per lot. Prints capital/lot so "sell ₹1.5L vs buy
                     ₹30k" can be compared with real numbers, not a feel.
  B) DISTRIBUTION    per-trade ₹ P&L percentiles + MFE/MAE (how far each trade ran in
                     our favour / against us BEFORE the engine exited it). This is what
                     a max-loss and a target should be set from.
  C) SL/TARGET SWEEP every (SL, target) pair replayed bar-by-bar on the real premium
                     path, vs the strategy's own exits as the baseline. Answers "is my
                     trailing SL the enemy?" by measurement, not by argument.

CAVEATS, stated up front because they bound every number below:
  - BS-modelled premium, not the real expired-option lake (_data/opt_hist.py). The
    ladder's SHAPE is delta arithmetic and is robust; the absolute rupees are modelled.
  - Sigma = realised vol. A BUYER is HURT by that assumption if real IV > realised
    (he overpays at entry) — so the BUY numbers here are, if anything, conservative.
    That is the opposite direction to TRAP #106's iron-fly, and worth stating.
  - Same-bar (SL and target both touched) resolves SL-first — conservative.
  - Pass (2) (+RMS caps) is not here. Sizing/capital gating is run_hunt's job.

Usage:  python -X utf8 scratch/nifty_trend/arschain_exits.py [--from 2018-01-01]
"""

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import arschain_backtest as ab   # noqa: E402  the engine runner (Rule 6B — not a copy)
import bs_option as bs           # noqa: E402
import validate_strategy as vs   # noqa: E402

LADDER = [0, 1, 2, 3, 4]        # 0 = ATM, n = n strikes IN the money
SIM_ITM = 2                     # level the distribution + sweep run at (see ladder output)


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[i]


def prem_paths(trades, sig, lot, itm, lots=1):
    """Per trade: entry premium, exit premium, and the premium high/low of EVERY bar the
    trade was alive for. Priced once here so the (SL,target) grid below is arithmetic."""
    qty = int(lots) * int(lot)
    out = []
    for t in trades:
        opt = "CE" if t["side"] == "long" else "PE"
        S_in, S_out = float(t["entry"]), float(t["exit"])
        K = round(S_in / bs.STRIKE_STEP) * bs.STRIKE_STEP
        K -= itm * bs.STRIKE_STEP * (1 if opt == "CE" else -1)
        e_ts, x_ts = pd.Timestamp(t["entry_dt"]), pd.Timestamp(t["exit_dt"])
        s_in = sig.get(e_ts.date(), 0.15)
        s_out = sig.get(x_ts.date(), s_in)
        ep = bs.bs_price(S_in, K, bs.tte_years(e_ts), s_in, bs.R_FREE, opt)
        xp = bs.bs_price(S_out, K, bs.tte_years(x_ts), s_out, bs.R_FREE, opt)
        path = []
        for (bt, hi, lo) in t.get("path", []):
            tte = bs.tte_years(pd.Timestamp(bt))
            # favourable spot extreme for THIS option, and the adverse one
            good, bad = (hi, lo) if opt == "CE" else (lo, hi)
            p_hi = bs.bs_price(good, K, tte, s_in, bs.R_FREE, opt)
            p_lo = bs.bs_price(bad, K, tte, s_in, bs.R_FREE, opt)
            path.append((p_hi, p_lo))
        out.append(dict(side=t["side"], opt=opt, K=K, ep=ep, xp=xp, qty=qty, path=path,
                        e_ts=e_ts, reason=t["reason"], pts=t["points"]))
    return out


def net_of(ep, xp, qty, e_ts):
    gross = (xp - ep) * qty
    return gross - bs.calc_charges(ep, xp, qty, when=e_ts) - bs.slip_cost_leg(ep, xp, qty)


def sim(paths, sl_rs=None, tp_rs=None):
    """Replay each trade with a fixed ₹ max-loss and/or ₹ target on the premium path.
    None = don't use that leg; both None = the strategy's own exits (the baseline)."""
    pnls, hit = [], defaultdict(int)
    for p in paths:
        ep, qty = p["ep"], p["qty"]
        xp, why = p["xp"], "ENGINE"
        for (p_hi, p_lo) in p["path"]:
            if sl_rs is not None and (p_lo - ep) * qty <= -sl_rs:
                xp, why = ep - sl_rs / qty, "SL"           # same-bar tie -> SL first
                break
            if tp_rs is not None and (p_hi - ep) * qty >= tp_rs:
                xp, why = ep + tp_rs / qty, "TARGET"
                break
        pnls.append(net_of(ep, xp, qty, p["e_ts"]))
        hit[why] += 1
    return pnls, hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--itm", type=int, default=SIM_ITM)
    a = ap.parse_args()

    print("\n  5m bars + engine (live wala)...", flush=True)
    cont5 = ab.load_5m(a.dfrom)
    daily = ab.daily_from_5m(cont5)
    cfg = ab.engine_cfg()
    trades = ab.run_engine(cont5, daily, cfg)
    lot = bs.get_nifty_lot()
    sig = bs.realised_vol_map(daily.set_index("date")["close"])
    print("\n  %s -> %s | %d trades | lot=%d"
          % (daily["date"].iloc[0], daily["date"].iloc[-1], len(trades), lot))

    # ---------------- A) STRIKE LADDER ----------------
    print("\n  " + "=" * 100)
    print("  A) STRIKE LADDER — kitna andar (ITM) jaayen? (BUY, 1 lot)")
    print("  " + "-" * 100)
    print("  %-10s %12s %8s %7s %7s %12s %12s %11s" % (
        "strike", "NET Rs", "win%", "PF", "Sharpe", "maxDD Rs", "capital/lot", "avg/trade"))
    ladder = {}
    for itm in LADDER:
        rows = bs.reprice(trades, sig, lot, lots=1, itm_steps=itm)
        s = ab.stats([r["pnl"] for r in rows])
        cap = sum(r["entry_prem"] * r["qty"] for r in rows) / len(rows)
        ladder[itm] = (s, cap)
        print("  %-10s %12s %7.1f%% %7.2f %7.2f %12s %12s %11s" % (
            "ATM" if itm == 0 else "ITM %d" % itm, f"{s['net']:,.0f}", s["win"], s["pf"],
            s["sharpe"], f"{s['maxdd']:,.0f}", f"{cap:,.0f}", f"{s['avg']:,.0f}"))
    print("  " + "=" * 100)
    print("  capital/lot = average entry premium x qty — SELL ke margin (~Rs 1.5L) se yahi tulna karo")

    # 9-year average capital is useless for sizing TODAY (NIFTY was 10,500 in 2018).
    # Year-wise ATM cost is the number a "how many lots can I run?" decision needs.
    print()
    print("  ATM ka capital/lot saal-dar-saal (aaj ke sizing ke liye aakhri saal dekho):")
    rows0 = bs.reprice(trades, sig, lot, lots=1, itm_steps=0)
    yc = defaultdict(list)
    for r in rows0:
        yc[pd.Timestamp(r["entry_dt"]).year].append(r["entry_prem"] * r["qty"])
    print("     %-6s %8s %14s %14s" % ("saal", "trades", "avg cap/lot", "sabse mehnga"))
    for y in sorted(yc):
        v = yc[y]
        print("     %-6s %8d %14s %14s"
              % (y, len(v), f"{sum(v)/len(v):,.0f}", f"{max(v):,.0f}"))

    # ---------------- B) DISTRIBUTION ----------------
    itm = a.itm
    paths = prem_paths(trades, sig, lot, itm)
    base, _ = sim(paths)
    mfe = [max((h - p["ep"]) * p["qty"] for h, _l in p["path"]) if p["path"] else 0.0
           for p in paths]
    mae = [min((l - p["ep"]) * p["qty"] for _h, l in p["path"]) if p["path"] else 0.0
           for p in paths]

    print("\n  " + "=" * 100)
    print("  B) PER-TRADE DISTRIBUTION @ ITM %d  (%d trades, 1 lot)" % (itm, len(paths)))
    print("  " + "-" * 100)
    print("  %-14s %11s %11s %11s   %s" % ("percentile", "asli P&L", "MFE", "MAE", "matlab"))
    for p_, note in ((1, "sabse bura 1%"), (5, ""), (10, ""), (25, ""), (50, "beech ka trade"),
                     (75, ""), (90, ""), (95, ""), (99, "sabse achha 1%")):
        print("  %-14s %11s %11s %11s   %s" % (
            "p%d" % p_, f"{pct(base, p_):,.0f}", f"{pct(mfe, p_):,.0f}",
            f"{pct(mae, p_):,.0f}", note))
    print("  %-14s %11s %11s %11s" % ("worst", f"{min(base):,.0f}", f"{min(mfe):,.0f}", f"{min(mae):,.0f}"))
    print("  %-14s %11s %11s %11s" % ("best", f"{max(base):,.0f}", f"{max(mfe):,.0f}", f"{max(mae):,.0f}"))
    print("  " + "-" * 100)
    print("  MFE = trade apne peak pe kitna de raha tha  |  MAE = exit se pehle kitna neeche gaya")

    # ---------------- C) SL / TARGET SWEEP ----------------
    bs_ = ab.stats(base)
    print("\n  " + "=" * 100)
    print("  C) SL / TARGET SWEEP @ ITM %d — kya koi jodi strategy ke apne exits se behtar hai?" % itm)
    print("  " + "-" * 100)
    print("  BASELINE (strategy ke apne exits, koi SL/target nahi):"
          "  net %s | PF %.2f | Sharpe %.2f | maxDD %s"
          % (f"{bs_['net']:,.0f}", bs_["pf"], bs_["sharpe"], f"{bs_['maxdd']:,.0f}"))
    print()
    SLS = [None, 1500, 2000, 3000, 4000, 5000, 7500]
    TPS = [None, 2000, 3000, 5000, 7500, 10000, 15000]
    print("  net Rs (baseline se %% behtar/bura) — rows = max loss, cols = target")
    hdr = "  %-9s" % "SL \\ TP"
    for tp in TPS:
        hdr += "%13s" % ("koi nahi" if tp is None else f"{tp:,}")
    print(hdr)
    best = None
    for sl in SLS:
        line = "  %-9s" % ("koi nahi" if sl is None else f"{sl:,}")
        for tp in TPS:
            pn, _ = sim(paths, sl, tp)
            s = ab.stats(pn)
            d = 100.0 * (s["net"] - bs_["net"]) / abs(bs_["net"]) if bs_["net"] else 0
            line += "%13s" % ("%+.0f%%" % d)
            if best is None or s["net"] > best[0]["net"]:
                best = (s, sl, tp)
        print(line)
    print("  " + "-" * 100)
    s, sl, tp = best
    print("  sabse achhi jodi: SL=%s  target=%s  ->  net %s | PF %.2f | Sharpe %.2f | maxDD %s"
          % (sl, tp, f"{s['net']:,.0f}", s["pf"], s["sharpe"], f"{s['maxdd']:,.0f}"))
    print("  baseline se: %+.1f%% net" % (100.0 * (s["net"] - bs_["net"]) / abs(bs_["net"])))

    # exit-reason breakdown of the baseline — is the trailing stop the enemy?
    print()
    print("  baseline ke exits (kaunsa exit paisa de/le raha, @ ITM %d):" % itm)
    rr = defaultdict(lambda: [0, 0.0])
    for p, pnl in zip(paths, base):
        rr[p["reason"]][0] += 1
        rr[p["reason"]][1] += pnl
    for r, (n, v) in sorted(rr.items(), key=lambda x: x[1][1]):
        print("     %-18s %5d trades  net %12s  avg %9s"
              % (r, n, f"{v:,.0f}", f"{v/n:,.0f}"))

    # ---------------- D) IS THE TRAILING STOP ACTUALLY THE ENEMY? ----------------
    # The sweep above only ADDS a stop on top of the engine's own exits — it can never
    # remove the ATR trail. "ATR_TRAILING loses money" is NOT evidence the trail is bad:
    # those are the trades that went against us; without the trail they don't become
    # winners, they just exit somewhere else. The only honest test is to switch the
    # trail OFF in the engine and re-run it.
    print("\n  " + "=" * 100)
    print("  D) EXIT RULES ON/OFF — engine dobara chala kar (BUY @ ATM, 1 lot)")
    print("  " + "-" * 100)
    print("  %-34s %7s %12s %8s %7s %7s %12s" % (
        "config", "trades", "NET Rs", "win%", "PF", "Sharpe", "maxDD Rs"))
    for label, over in (("jaisa abhi hai (trail ON, zone ON)", {}),
                        ("trail OFF (zone ON)", dict(exit_atr=False)),
                        ("zone OFF (trail ON)", dict(exit_zone=False)),
                        ("dono OFF (sirf 3:15 + reversal)", dict(exit_atr=False, exit_zone=False))):
        tv = ab.run_engine(cont5, daily, ab.engine_cfg(**over))
        rows = bs.reprice(tv, sig, lot, lots=1, itm_steps=0)
        s = ab.stats([r["pnl"] for r in rows])
        print("  %-34s %7d %12s %7.1f%% %7.2f %7.2f %12s" % (
            label, s["n"], f"{s['net']:,.0f}", s["win"], s["pf"], s["sharpe"],
            f"{s['maxdd']:,.0f}"))
    print("  " + "=" * 100)
    print()


if __name__ == "__main__":
    main()
