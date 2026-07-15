#!/usr/bin/env python3
"""Trail-sensitivity sweep — shrink the rode->lock gap.

Objective (user): too many trades RIDE (neither target nor trail fired) to the
actual exit, giving back profit that built up. Tighten the trail (arm sooner /
hug peak) to convert those `rode` exits into profit-`lock` exits near the peak,
WITHOUT over-whipsawing winners (loss% blowup).

Sweeps init_sl / step / cushion / aggressive_pct on the aggr trail engine
(risk_gate.target_sl_level, Rule 6B), RIDE mode (no target cap -> trail is the
sole exit, so the ONLY thing that reduces rode is a tighter trail). Reports
train/OOS/all net + lock% / loss% / rode% + maxDD + win%. Ranked by
min(train,OOS) net (robust, TRAP #103).

Run on VPS: venv/bin/python scripts/trail_sweep.py [--no-fetch]
"""
import sys, os, argparse, itertools
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import path_aware_sl_sim as P

# trail knobs (per-lot Rs). target kept wide so it never caps (ride mode anyway).
INIT_SL  = [500, 750, 1000, 1500, 2500]
STEP     = [50, 100, 200]      # favour_step == sl_move
CUSHION  = [0, 250, 500]
AGG_PCT  = [20, 30]
TARGET   = 3000                # agg-phase reference only (ride = no cap)


def _cfg(isl, step, cush, pct):
    return dict(target_per_lot=TARGET, initial_sl_per_lot=isl, favour_step=step,
                sl_move=step, aggressive_pct=pct, aggressive_mult=2.0, min_cushion=cush)


def _maxdd(pnls):
    """peak-to-trough of the cumulative equity (trade-ordered)."""
    eq = 0.0; peak = 0.0; dd = 0.0
    for p in pnls:
        eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
    return dd


def _inr(x): return f"{round(x):,}"


def evalset(subset, cfg):
    net = 0.0; hLock = hLoss = hRode = 0; wins = 0; pnls = []
    for c in subset:
        st, rs = P.replay_aggr(c["win"], c["side"], c["ep"], c["qty"], cfg,
                               c["lots"], c["fallback"], ride=True)
        net += rs; pnls.append(rs)
        if rs > 0: wins += 1
        if st == "TRAIL_SL": hLock += 1
        elif st == "SL": hLoss += 1
        else: hRode += 1
    n = max(1, len(subset))
    return dict(net=net, lock=100*hLock/n, loss=100*hLoss/n, rode=100*hRode/n,
                win=100*wins/n, dd=_maxdd(pnls))


def run(dfrom, dto, allow_fetch):
    trades = order_store.trades_for_range(dfrom, dto)["details"]
    trades = [t for t in trades if t.get("pnl") is not None]
    cov = P._prep_covered(trades, allow_fetch, hold_eod=False)
    cov.sort(key=lambda c: c["date"])
    n = len(cov); cut = int(n*0.65)
    train, oos = cov[:cut], cov[cut:]
    print(f"\nCovered {n}/{len(trades)}  |  Train {len(train)} (..{train[-1]['date']})  |  "
          f"OOS {len(oos)} ({oos[0]['date']}..)\n")
    a_all = sum(c["actual"] for c in cov)
    a_tr = sum(c["actual"] for c in train); a_oo = sum(c["actual"] for c in oos)
    print(f"ACTUAL (live exits): train {_inr(a_tr)} | OOS {_inr(a_oo)} | all {_inr(a_all)} "
          f"| maxDD {_inr(_maxdd([c['actual'] for c in cov]))}\n")

    rows = []
    for isl, step, cush, pct in itertools.product(INIT_SL, STEP, CUSHION, AGG_PCT):
        cfg = _cfg(isl, step, cush, pct)
        tr = evalset(train, cfg); oo = evalset(oos, cfg); al = evalset(cov, cfg)
        rows.append(dict(p=f"SL{isl}/st{step}/cu{cush}/pct{pct}",
                         tr=tr["net"], oo=oo["net"], al=al,
                         mn=min(tr["net"], oo["net"])))
    rows.sort(key=lambda r: r["mn"], reverse=True)

    print("Ranked by MIN(train,OOS) net — top 14 (a=all-covered stats):")
    print(f"  {'params':26s} {'train':>8} {'oos':>8} {'all':>8} | "
          f"{'lock%':>5} {'loss%':>5} {'rode%':>5} {'win%':>5} {'maxDD':>8}")
    for r in rows[:14]:
        a = r["al"]
        print(f"  {r['p']:26s} {_inr(r['tr']):>8} {_inr(r['oo']):>8} {_inr(a['net']):>8} | "
              f"{a['lock']:>4.0f}% {a['loss']:>4.0f}% {a['rode']:>4.0f}% {a['win']:>4.0f}% "
              f"{_inr(a['dd']):>8}")

    # reference: current deployed aggr (6000/2500/step100/pct30), ride + cap
    dep = dict(target_per_lot=6000, initial_sl_per_lot=2500, favour_step=100,
               sl_move=100, aggressive_pct=30, aggressive_mult=2.0, min_cushion=0)
    dr = evalset(cov, dep)
    print(f"\nREF deployed-aggr (6000/2500/st100) RIDE: all {_inr(dr['net'])} "
          f"| lock {dr['lock']:.0f}% loss {dr['loss']:.0f}% rode {dr['rode']:.0f}% "
          f"win {dr['win']:.0f}% maxDD {_inr(dr['dd'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-21")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
