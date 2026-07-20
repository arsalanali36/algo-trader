"""Task 6 — VRP UNGATED weekly condor/fly backtest (REAL WEEK option lake).

The untested MIDDLE CASE between two known results:
  - ADR-006 GATED weekly straddle-to-expiry (IV-rank>0.80): n=15, PF 4.4, p=0.0002  (SHELVED — ~3/yr)
  - intraday short-vol (TRAP #109): FAILED — round-trip cost > one day's theta

This tests: POSITIONAL weekly (one entry per weekly expiry cycle, held to expiry,
defined-risk wings MANDATORY — same as ADR-006) but WITHOUT the IV-rank gate — a fresh
weekly ATM iron fly / condor EVERY cycle, regardless of IV level.

Do NOT assume the outcome. Headline = the FULL ungated number (6a). 6b (IV-rank buckets)
and 6c (wing sweep) are DIAGNOSTIC only — never a retro-fitted new gate to ship.

Reuses (no data/pricing rebuild — apples-to-apples with ADR-006's real-lake measurement):
  - real_struct2.grid / _px      -> REAL held-strike premium (NOT Black-Scholes)
  - positional_vol._cycle_info   -> weekly cycle / trading-days-to-expiry
  - bs_option.calc_charges/slip_cost_leg  -> real Zerodha charges + ADR-005 DOM slip
  - optlake_load.iv_rank_daily   -> entry IV-rank (logged, used as gate only for the GATED comparison)
  - montecarlo / ml_gate.deflated_sharpe  -> repo's own MC + significance methodology

Two "week-start" interpretations, BOTH tested (user request):
  - cycle_start : enter the FIRST trading day of the new cycle (day after prev expiry; max DTE)
  - dte4        : enter at T-4 DTE (~Monday) — ADR-006 / live vrp_straddle_trader shape

Entry time fixed 09:20 IST (positional engine default, aligned to the day-open IV the rank uses).
NOT wired to any live trader. Backtest-only research.  Run: python -X utf8 vrp_ungated_backtest.py
"""
import os
import json
import argparse
import datetime as dt
from collections import OrderedDict

import numpy as np
import pandas as pd

import real_struct2 as r2
import bs_option as bs
import engine
import expiry_calendar as xcal
import optlake_load as ol
import ml_gate
from montecarlo import montecarlo
from positional_vol import _cycle_info

HERE = os.path.dirname(os.path.abspath(__file__))
FLAG, TF = "WEEK", "5m"
STEP = 50
CAP = engine.START_CAP
EVO_END = dt.date(2025, 7, 1)          # same OOS split as build_vrp / positional_vol
IV_LOOKBACK = 60                        # optlake_load.iv_rank_daily default (ADR-006 gate basis)
H_ENTRY, M_ENTRY = 9, 20               # fixed entry bar
EXPIRY_EOD = (14, 55)                  # ADR-006 expiry-day forced close (2:55)


# ─────────────────────────────── cycle / entry-bar resolution ───────────────────────────────
def _cycles(g):
    """OrderedDict: expiry_date -> [trading days in that weekly cycle], + tdte, is_exp."""
    is_exp, tdte, uniq = _cycle_info(g)
    exp_days = [d for d in uniq if is_exp.get(d)]
    cyc = OrderedDict()
    for d in uniq:
        nx = [e for e in exp_days if e >= d]
        if not nx:
            continue
        cyc.setdefault(nx[0], []).append(d)
    return cyc, tdte, is_exp


def _first_bar_on(g, day, h, m):
    """first grid index on `day` at/after h:m, else None."""
    for i in range(len(g["DAY"])):
        if g["DAY"][i] == day and (g["TT"][i].hour, g["TT"][i].minute) >= (h, m):
            return i
    return None


def _entry_bars(g, mode):
    """bar_index -> (expiry_date, entry_day) for the chosen week-start interpretation."""
    cyc, tdte, is_exp = _cycles(g)
    out = {}
    for exp, cdays in cyc.items():
        cdays = sorted(cdays)
        # candidate entry day within the cycle
        if mode == "cycle_start":
            cand = [d for d in cdays if d != exp]          # first day of cycle (day after prev expiry)
            entry_day = cand[0] if cand else None
        elif mode.startswith("dte"):
            want = int(mode[3:])
            cand = [d for d in cdays if tdte.get(d, 999) == want]
            entry_day = cand[0] if cand else None          # skip cycle if that DTE doesn't exist (holiday)
        else:
            raise ValueError(mode)
        if entry_day is None or entry_day == exp:
            continue                                       # no 0DTE entry on expiry day
        bi = _first_bar_on(g, entry_day, H_ENTRY, M_ENTRY)
        if bi is not None:
            out[bi] = (exp, entry_day)
    return out


# ─────────────────────────────────────── backtest ───────────────────────────────────────────
def backtest(g, ivr, lot, mode="cycle_start", struct="iron_fly",
             wing=5, short_off=3, tp_frac=0.5, sl_frac=None, iv_min=0.0,
             allow_days=None, exit_before_dte=None):
    """One defined-risk weekly structure per cycle, held to expiry. Real held-strike premium.

    struct: 'iron_fly'  = sell ATM CE+PE, buy ATM±wing        (ADR-006 straddle shape, defined-risk)
            'iron_condor'= sell ATM±short, buy ATM±(short+wing)
    iv_min > 0 applies the IV-rank gate (only for the GATED apples-to-apples comparison).
    sl_frac None = no % stop (wing IS the defined-risk floor; hold to expiry).
    allow_days: optional set of dates — enter only if entry_day is in it (custom VRP-spread gate).
    exit_before_dte: optional int — force-close at EOD of the day this-many trading-days before
        expiry (e.g. 1 = exit day-before-expiry, avoiding expiry-day 0DTE gamma). None = hold to expiry.
    """
    qty = int(lot)
    n = len(g["DT"]); DT, DAY, TT = g["DT"], g["DAY"], g["TT"]
    is_exp, tdte, _ = _cycle_info(g)
    ebars = _entry_bars(g, mode)

    def legs_at(i):
        K = round(g["ATMK"][i] / STEP) * STEP
        if struct == "iron_fly":
            return K, [("CE", K, -1), ("PE", K, -1),
                       ("CE", K + wing * STEP, +1), ("PE", K - wing * STEP, +1)]
        if struct == "iron_condor":
            kc, kp = K + short_off * STEP, K - short_off * STEP
            return K, [("CE", kc, -1), ("PE", kp, -1),
                       ("CE", kc + wing * STEP, +1), ("PE", kp - wing * STEP, +1)]
        raise ValueError(struct)

    pos = None
    trades = []

    def close(i, reason):
        cv = sum(s * r2._px(g, i, side, K) for (side, K, s) in pos["legs"])
        gross_u = cv - pos["entry_val"]                    # per-unit points (credit is negative entry_val)
        fee = slip = 0.0
        for (side, K, s) in pos["legs"]:
            ep = pos["eps"][(side, K)]; xp = r2._px(g, i, side, K)
            fee += bs.calc_charges(ep, xp, qty, entry_side=("BUY" if s > 0 else "SELL"), when=pos["dt"])
            slip += bs.slip_cost_leg(ep, xp, qty)
        pnl = gross_u * qty - fee - slip
        credit = abs(pos["entry_val"]) * qty
        max_loss = wing * STEP * qty - credit              # defined-risk floor (both fly & condor)
        spot_out = float(g["SPOT"][i])
        # geometric breach: underlying outside the short strikes at exit
        breached = bool(spot_out > pos["k_hi"] or spot_out < pos["k_lo"])
        trades.append(dict(
            mode=mode, struct=struct, wing=wing,
            entry_dt=str(pd.Timestamp(pos["dt"]).date()), exit_dt=str(pd.Timestamp(DT[i]).date()),
            iv_rank=pos["ivr"], credit=round(credit, 1), max_loss=round(max_loss, 1),
            pnl=round(pnl, 1), points=round(gross_u, 2), fee=round(fee, 1), slip=round(slip, 1),
            breached=breached, loss=bool(pnl < 0), reason=reason,
            spot_in=round(pos["spot_in"], 1), spot_out=round(spot_out, 1),
            held_days=(pd.Timestamp(DT[i]).date() - pd.Timestamp(pos["dt"]).date()).days))

    for i in range(1, n):
        d = DAY[i]
        if pos is not None:
            cv = sum(s * r2._px(g, i, side, K) for (side, K, s) in pos["legs"])
            mtm = (cv - pos["entry_val"]) * qty
            ref = pos["ref"]
            spot = float(g["SPOT"][i])
            hit = None
            if tp_frac and mtm >= tp_frac * ref * qty:
                hit = "Target %credit"
            elif sl_frac and mtm <= -sl_frac * ref * qty:
                hit = "Stop %credit"
            elif (exit_before_dte is not None and tdte.get(d, 999) <= exit_before_dte
                  and not is_exp.get(d) and (TT[i].hour, TT[i].minute) >= EXPIRY_EOD):
                hit = "Pre-expiry exit"                 # avoid expiry-day 0DTE gamma
            elif is_exp.get(d) and (spot > pos["k_hi"] or spot < pos["k_lo"]):
                hit = "Expiry ITM guard"                   # ADR-006 expiry-day ITM guard
            elif is_exp.get(d) and (TT[i].hour, TT[i].minute) >= EXPIRY_EOD:
                hit = "Expiry EOD"                         # ADR-006 forced close 2:55
            if hit:
                close(i, hit); pos = None

        if pos is None and i in ebars:
            exp, entry_day = ebars[i]
            rank = ivr.get(entry_day)
            if iv_min > 0 and (rank is None or rank < iv_min):
                continue                                   # GATED comparison only
            if allow_days is not None and entry_day not in allow_days:
                continue                                   # custom VRP-spread gate
            K0, legs = legs_at(i)
            eps = {(side, K): r2._px(g, i, side, K) for (side, K, s) in legs}
            if not all(v > 0 for v in eps.values()):
                continue                                   # incomplete premium data → skip cycle
            entry_val = sum(s * eps[(side, K)] for (side, K, s) in legs)
            ref = abs(entry_val) if abs(entry_val) > 1e-6 else 1.0
            shorts = [K for (side, K, s) in legs if s < 0]
            pos = dict(legs=legs, eps=eps, entry_val=entry_val, ref=ref, dt=DT[i],
                       k_hi=max(shorts), k_lo=min(shorts), spot_in=float(g["SPOT"][i]),
                       ivr=(round(rank, 3) if rank is not None else None))
    if pos is not None:
        close(n - 1, "End")
    return trades


# ─────────────────────────────────────── metrics ────────────────────────────────────────────
def _equity(trades):
    eq = CAP
    curve = [CAP]
    for t in trades:
        eq += t["pnl"]; curve.append(eq)
    return np.array(curve)


def metrics(trades, ann=52):
    n = len(trades)
    if n == 0:
        return dict(n=0, win_rate=0, pf=0, sharpe=0, net_pct=0, maxdd=0, total=0,
                    avg=0, breach_rate=0, loss_rate=0)
    pnl = np.array([t["pnl"] for t in trades], dtype=float)
    wins = pnl[pnl > 0].sum(); losses = pnl[pnl < 0].sum()
    pf = float(wins / abs(losses)) if losses < 0 else float("inf")
    sharpe = float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(ann)) if n > 1 and pnl.std(ddof=1) > 0 else 0.0
    eq = _equity(trades)
    peak = np.maximum.accumulate(eq); maxdd = float(((eq - peak) / peak * 100).min())
    return dict(n=n, win_rate=round(100 * (pnl > 0).mean(), 1), pf=round(pf, 2),
                sharpe=round(sharpe, 3), net_pct=round(pnl.sum() / CAP * 100, 2),
                maxdd=round(maxdd, 2), total=round(float(pnl.sum()), 0),
                avg=round(float(pnl.mean()), 1),
                breach_rate=round(100 * np.mean([t["breached"] for t in trades]), 1),
                loss_rate=round(100 * np.mean([t["loss"] for t in trades]), 1))


def significance(trades, n_boot=5000, seed=7):
    """bootstrap: is mean weekly P&L > 0? (rotation/random-week null approximated by
    resampling the trade-P&L sequence — same shape as build_vrp.significance)."""
    if len(trades) < 8:
        return dict(p_value=None, sharpe=None, note="too few trades")
    dp = np.array([t["pnl"] for t in trades], dtype=float)
    real = float(dp.mean() / dp.std(ddof=1) * np.sqrt(52)) if dp.std(ddof=1) > 0 else 0.0
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(dp, len(dp), replace=True).mean() for _ in range(n_boot)])
    return dict(p_value=round(float((boots <= 0).mean()), 4), sharpe=round(real, 3),
                significant=bool((boots <= 0).mean() < 0.05))


def mc_summary(trades):
    res = dict(trades=trades, equity=pd.DataFrame({"Datetime": [0], "equity": [CAP]}), final=CAP)
    mc = montecarlo(res, n_sims=1000)
    if mc is None:
        return None
    t = mc["table"]
    # % profitable paths: reconstruct from net percentiles is lossy → do a quick own count
    pnl = np.array([x["pnl"] for x in trades], dtype=float)
    rng = np.random.default_rng(11)
    prof = float(np.mean([rng.choice(pnl, len(pnl), replace=True).sum() > 0 for _ in range(1000)]) * 100)
    return dict(net=t["net"], maxdd=t["maxdd"], sharpe=t["sharpe"],
                pct_profitable=round(prof, 1), not_overfit=mc["not_overfit"])


def dsr_check(trades, n_trials):
    if len(trades) < 8:
        return None
    ser = np.array([t["pnl"] for t in trades], dtype=float) / 250000.0
    d = ml_gate.deflated_sharpe(ser, n_trials=n_trials)
    return {k: d[k] for k in ("dsr_prob", "pass", "n_trials", "sr_star_annual") if k in d}


def oos_split(trades):
    tr = [t for t in trades if dt.date.fromisoformat(t["entry_dt"]) < EVO_END]
    oo = [t for t in trades if dt.date.fromisoformat(t["entry_dt"]) >= EVO_END]
    return metrics(tr), metrics(oo)


# ─────────────────────────────────────── runner ─────────────────────────────────────────────
def run_all():
    lot = bs.get_nifty_lot() or 65
    bs.SLIP_ENABLED = True; bs.SLIP_MULT = 1.0
    g = r2.grid(FLAG, TF)
    ivr = ol.iv_rank_daily(FLAG, TF, IV_LOOKBACK)
    span = f"{min(g['DAY'])} → {max(g['DAY'])}"
    print(f"lake span {span}  bars={len(g['DT'])}  lot={lot}  IV-rank days={len(ivr)}", flush=True)

    out = dict(meta=dict(lot=lot, span=span, cap=CAP, evo_end=str(EVO_END),
                         iv_lookback=IV_LOOKBACK, entry_time=f"{H_ENTRY:02d}:{M_ENTRY:02d}"),
               a=OrderedDict(), b=OrderedDict(), c=OrderedDict(), gated=OrderedDict())

    # ── 6a: ungated headline — both week-start modes, both structures ──
    N_TRIALS = 24                      # honest trial count for DSR (modes×structs×wings explored)
    for mode in ("cycle_start", "dte4"):
        for struct, short_off in (("iron_fly", 0), ("iron_condor", 3)):
            key = f"{mode}|{struct}"
            tr = backtest(g, ivr, lot, mode=mode, struct=struct, wing=5,
                          short_off=short_off, tp_frac=0.5, sl_frac=None, iv_min=0.0)
            m = metrics(tr); sig = significance(tr); mc = mc_summary(tr)
            dsr = dsr_check(tr, N_TRIALS); trm, oom = oos_split(tr)
            out["a"][key] = dict(metrics=m, sig=sig, mc=mc, dsr=dsr,
                                 train=trm, oos=oom, trades=tr)
            print(f"[6a] {key:26s} n={m['n']:3d} PF={m['pf']:5.2f} Sh={m['sharpe']:6.2f} "
                  f"net={m['net_pct']:7.1f}% DD={m['maxdd']:6.1f}% win={m['win_rate']:4.0f}% "
                  f"p={sig['p_value']} | train net={trm['net_pct']:.0f}% oos net={oom['net_pct']:.0f}%",
                  flush=True)

    # ── GATED comparison (same engine, iv_min=0.80) — our own apples-to-apples vs ADR-006 ──
    for mode in ("cycle_start", "dte4"):
        for struct, short_off in (("iron_fly", 0), ("iron_condor", 3)):
            key = f"{mode}|{struct}"
            tr = backtest(g, ivr, lot, mode=mode, struct=struct, wing=5,
                          short_off=short_off, tp_frac=0.5, sl_frac=None, iv_min=0.80)
            out["gated"][key] = dict(metrics=metrics(tr), sig=significance(tr))
            m = out["gated"][key]["metrics"]
            print(f"[gated0.80] {key:26s} n={m['n']:3d} PF={m['pf']:5.2f} Sh={m['sharpe']:6.2f} "
                  f"net={m['net_pct']:.1f}%", flush=True)

    # ── 6b: post-hoc IV-rank sensitivity (DIAGNOSTIC) — bucket the headline ungated run ──
    head = out["a"]["cycle_start|iron_condor"]["trades"]
    buckets = OrderedDict()
    for lo, hi in ((0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        sub = [t for t in head if t["iv_rank"] is not None and lo <= t["iv_rank"] < hi]
        buckets[f"{lo:.1f}-{hi:.1f}"] = metrics(sub)
    nr = [t for t in head if t["iv_rank"] is None]
    out["b"] = dict(headline_key="cycle_start|iron_condor", buckets=buckets,
                    no_rank=metrics(nr), no_rank_n=len(nr))
    print("[6b] IV-rank buckets (cycle_start|iron_condor):", flush=True)
    for k, m in buckets.items():
        print(f"      rank {k}  n={m['n']:3d} PF={m['pf']:5.2f} win={m['win_rate']:4.0f}% net={m['net_pct']:.0f}%", flush=True)

    # ── 6c: wing-width sensitivity (DIAGNOSTIC) — sweep repo-present widths ──
    # Sweep on iron_FLY (short_off=0): wing 10 = offset ±10, exactly the lake's coverage
    # edge (matches the live vrp_straddle_trader default wing_off=10). A condor (short_off=3)
    # + wing 8/10 would need offset ±11/±13 — beyond the ±10 lake grid → no data.
    for wing in (5, 8, 10):
        tr = backtest(g, ivr, lot, mode="cycle_start", struct="iron_fly", wing=wing,
                      short_off=0, tp_frac=0.5, sl_frac=None, iv_min=0.0)
        out["c"][f"wing{wing}"] = dict(metrics=metrics(tr), sig=significance(tr))
        m = out["c"][f"wing{wing}"]["metrics"]
        print(f"[6c] iron_fly wing={wing:2d}  n={m['n']:3d} PF={m['pf']:5.2f} Sh={m['sharpe']:6.2f} "
              f"net={m['net_pct']:.1f}% maxloss/lot=₹{wing*STEP*lot:,}", flush=True)

    # persist raw
    trades_all = []
    for key, d in out["a"].items():
        trades_all += d["trades"]
    pd.DataFrame(trades_all).to_csv(os.path.join(HERE, "vrp_ungated_trades.csv"), index=False)
    slim = json.loads(json.dumps(out, default=float))
    for k in slim["a"]:
        slim["a"][k].pop("trades", None)
    json.dump(slim, open(os.path.join(HERE, "vrp_ungated_results.json"), "w"), indent=2, default=float)
    print(f"\nwrote vrp_ungated_trades.csv ({len(trades_all)} rows) + vrp_ungated_results.json", flush=True)
    return out


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true")
    ap.parse_args()
    run_all()
