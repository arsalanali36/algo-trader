#!/usr/bin/env python3
"""bs_vs_reallake.py — reprice a run's BS-modeled trades on the REAL option lake.

    python bs_vs_reallake.py                 # all NIFTY winners
    python bs_vs_reallake.py <slug> ...      # specific runs

WHY THIS EXISTS (TRAP #136). Every `runs/<slug>/results.js` Sharpe/net is
BLACK-SCHOLES-modeled premium (+ DOM slip) — NOT real premium. BS understates the
theta an option BUYER actually bleeds, so ATM-buy / long-vol strategies look far
better on BS than they trade on real data. This tool takes a run's OWN trades
(same entry/exit times) and reprices every leg on the REAL held-strike lake
(real_struct2._px) + real Zerodha charges + DOM slip, so you can see the honest
number before trusting any 'winner'. Leg specs are reused from option_structures
(STRUCTURES / DIRECTIONAL) with each run's own wing_off/bs_off.

CAVEAT: this keeps the BS run's EXIT TIMING (premium-based tp/sl fired on BS
levels, not real). For SPOT-exit single-leg buys (ORB/chain) the number is solid;
for premium-exit multi-leg structures the exact figure can shift under a full
real re-backtest — but the sign/direction is robust. NIFTY lake only.
"""
import os
import sys
import json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import _paths  # noqa: F401
import real_struct2 as rs2
import bs_option as bs
import option_structures as ostr

STEP = rs2.STEP
_G = rs2.grid("WEEK", "5m")
_DT = np.asarray(_G["DT"], dtype="datetime64[ns]")
_DAYS = (pd.Timestamp(_DT[-1]).date() - pd.Timestamp(_DT[0]).date()).days


def _bar_at(ts):
    ts = pd.Timestamp(ts)
    j = int(np.searchsorted(_DT, np.datetime64(ts), side="right")) - 1
    if j < 0 or pd.Timestamp(_DT[j]).date() != ts.date():
        return None
    return j


def _legs_for(struct, side, params):
    """(opt_type, offset_steps, side_signed) — mirrors option_structures leg build."""
    if struct in ostr.STRUCTURES:
        return list(ostr.STRUCTURES[struct])
    if struct in ostr.DIRECTIONAL:
        spec = list(ostr.DIRECTIONAL[struct][side])
        w = int(params.get("wing_off", 0))
        if w:
            spec = [(ot, (w if off > 0 else -w) if s < 0 else off, s) for (ot, off, s) in spec]
        bo = int(params.get("bs_off", 0))
        if bo:
            spec = [(ot, (bo if off > 0 else -bo) if (s > 0 and off != 0) else off, s) for (ot, off, s) in spec]
        return spec
    return None                                    # single ATM leg (ORB / chain buy)


def reprice(slug):
    R = json.loads(open(os.path.join(HERE, "runs", slug, "results.js"), encoding="utf-8")
                   .read().strip()[len("window.RESULTS = "):].rstrip(";"))
    meta = json.load(open(os.path.join(HERE, "runs", slug, "meta.json")))
    struct = str(meta.get("design", "")).split("/")[0]
    params = meta.get("params", {})
    c = R["combos"]["bs|full"]
    bs_sh = c["metrics"].get("sharpe")
    bs_net = real_net = 0.0
    rr = []
    dd = []
    for t in c["all_trades"]:
        if t.get("pnl") is None:
            continue
        K = float(t["strike"]); qty = int(t["qty"]); dirn = str(t.get("side"))
        ie, xe = _bar_at(t["entry_dt"]), _bar_at(t["exit_dt"])
        if ie is None or xe is None:
            continue
        specs = _legs_for(struct, dirn, params)
        if specs is None:
            opt = t.get("opt_type") if t.get("opt_type") in ("CE", "PE") else ("CE" if dirn == "long" else "PE")
            specs = [(opt, 0, +1)]
        when = pd.Timestamp(t["entry_dt"])
        gross = fee = slip = 0.0
        ok = True
        for (opt, off, s) in specs:
            Kl = K + off * STEP
            ep, xp = rs2._px(_G, ie, opt, Kl), rs2._px(_G, xe, opt, Kl)
            if ep <= 0:
                ok = False; break
            lq = qty * abs(s)
            gross += s * (xp - ep) * qty
            fee += bs.calc_charges(ep, max(xp, 0.0), lq, entry_side="BUY" if s > 0 else "SELL", when=when)
            slip += bs.slip_cost_leg(ep, xp, lq)
        if not ok:
            continue
        real_net += gross - fee - slip
        bs_net += float(t["pnl"])
        rr.append(gross - fee - slip)
        dd.append(str(t["entry_dt"])[:10])     # train/OOS split ke liye
    r = np.array(rr)
    sh = r.mean() / r.std() * np.sqrt(252 * len(r) / max(1, _DAYS)) if len(r) and r.std() else 0.0
    return dict(slug=slug, struct=struct, cov=len(rr), tot=len(c["all_trades"]),
                bs_sh=bs_sh, bs_net=bs_net, real_net=real_net, real_sh=sh,
                wr=(r > 0).mean() * 100 if len(r) else 0.0,
                trades=r.tolist(), dates=dd)   # per-trade REAL net + entry date


DEFAULT = ["mid_orb_nifty", "orb_supertrend", "long_straddle_orb", "debit_vertical_orb",
           "ratio_backspread", "long_strangle_orb", "chain_zone_longatm"]

if __name__ == "__main__":
    slugs = sys.argv[1:] or DEFAULT
    print("lake:", pd.Timestamp(_DT[0]).date(), "->", pd.Timestamp(_DT[-1]).date(), "(NIFTY only)")
    print("{:<20} {:<16} {:>8} {:>8} {:>12} {:>12} {:>8} {:>5}".format(
        "run", "struct", "cov/tot", "BS Sh", "BS net", "REAL net", "REAL Sh", "win"))
    for s in slugs:
        try:
            d = reprice(s)
            print("{:<20} {:<16} {:>3}/{:<4} {:>8.2f} {:>12,.0f} {:>12,.0f} {:>8.2f} {:>4.0f}%".format(
                d["slug"], d["struct"], d["cov"], d["tot"], d["bs_sh"], d["bs_net"],
                d["real_net"], d["real_sh"], d["wr"]))
        except Exception as e:
            print("{:<20} ERR {}".format(s, e))
