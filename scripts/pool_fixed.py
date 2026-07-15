#!/usr/bin/env python3
"""Capital-pool sim with FIXED 1:1 exits — does faster rotation (capital frees
sooner -> more of the CAPITAL_BLOCKED signals get taken) beat the slow big-net
ride? Reuses capital_pool_sim's pool mechanics + margins + charges + metrics
(Rule 6B) by monkeypatching a 'fix' exit rule into trade_pnl_exit.

Variants (all NAKED, real Zerodha charges + DOM slip):
  ATR      = actual live exits (baseline)
  RIDE     = deployed aggressive (6000/2500/step100), no target cap
  FIX 1:1  = fixed target==SL at 1000 / 1500 / 2000 per-lot; exits at first
             target/SL bar, else EOD 15:15.

Run on VPS: venv/bin/python scripts/pool_fixed.py [--no-fetch]
"""
import sys, os, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import capital_pool_sim as cps
import path_aware_sl_sim as pas

POOLS = [500000, 800000, 1100000]
FIX_SLS = [1000, 1500, 2000]   # 1:1 -> target == SL


def _replay_fixed_timed(bars, side, ep, qty, tgt_rs, sl_rs, fb):
    """Fixed target/SL, exit at first hit bar; else ride to EOD (fb, last bar)."""
    for (hhmm, o, h, l, c) in bars:
        if side == "SELL":
            adv = pas._mtm(side, ep, h, qty); fav = pas._mtm(side, ep, l, qty)
        else:
            adv = pas._mtm(side, ep, l, qty); fav = pas._mtm(side, ep, h, qty)
        if adv <= -sl_rs:
            return "SL", -sl_rs, hhmm
        if fav >= tgt_rs:
            return "TARGET", tgt_rs, hhmm
    xt = bars[-1][0] if bars else cps.EOD
    return "RODE", fb, xt


_orig_exit = cps.trade_pnl_exit
def _patched_exit(tr, exit_rule, cfg, hedged):
    if exit_rule == "fix":
        sl = cfg["_fix_sl"] * max(1, int(tr["lots"])); tgt = cfg["_fix_tgt"] * max(1, int(tr["lots"]))
        if tr["covered"]:
            _, rs, xt = _replay_fixed_timed(tr["win"], tr["side"], tr["ep"], tr["qty"], tgt, sl, tr["fb"])
        else:
            rs, xt = tr["actual"], tr["xt_atr"]
        return rs, xt
    return _orig_exit(tr, exit_rule, cfg, hedged)
cps.trade_pnl_exit = _patched_exit


def _split(trades):
    dates = sorted({t["date"] for t in trades})
    cut = int(len(dates) * 0.65)
    return set(dates[:cut]), set(dates[cut:]), dates[cut-1], dates[cut]


def _tnet(day_nets, dates):
    return sum(d for dt, d in day_nets if dt in dates)


def variant(trades, pool, rule, cfg, label):
    r = cps.run_variant(trades, pool, rule, cfg, hedged=False, with_charges=True)
    m = cps.metrics(r["day_nets"], pool)
    tr_d, oo_d, _, _ = _split(trades)
    return dict(label=label, net=m["net"], taken=r["taken"], blocked=r["blocked"],
                sharpe=m["sharpe"], maxdd=m["maxdd"], p=m["p_value"],
                conc=r["max_concurrent"], peak=r["peak_util"],
                train=round(_tnet(r["day_nets"], tr_d)), oos=round(_tnet(r["day_nets"], oo_d)))


def _inr(x): return f"{round(x):,}"


def run(dfrom, dto, allow_fetch):
    trades = cps.prep_trades(dfrom, dto, allow_fetch)
    tr_d, oo_d, last_tr, first_oo = _split(trades)
    print(f"\nDays: train ..{last_tr} | OOS {first_oo}.. "
          f"({len({t['date'] for t in trades})} total)\n")

    ride_cfg = dict(target_per_lot=6000, initial_sl_per_lot=2500, favour_step=100,
                    sl_move=100, aggressive_pct=30, aggressive_mult=2.0, min_cushion=0, _ride=True)

    for pool in POOLS:
        print("=" * 108)
        print(f"POOL Rs {_inr(pool)}")
        print("=" * 108)
        print(f"  {'variant':16s} {'NET':>9} {'train':>8} {'oos':>8} | "
              f"{'taken':>6} {'blockd':>6} {'conc':>5} {'peakUtil':>10} | "
              f"{'sharpe':>6} {'maxDD':>9} {'p':>5}")
        vs = [variant(trades, pool, "atr", {}, "ATR (actual)"),
              variant(trades, pool, "ride", ride_cfg, "RIDE 6k/2.5k")]
        for sl in FIX_SLS:
            cfg = {"_fix_sl": sl, "_fix_tgt": sl}
            vs.append(variant(trades, pool, "fix", cfg, f"FIX 1:1 SL{sl}"))
        for v in vs:
            print(f"  {v['label']:16s} {_inr(v['net']):>9} {_inr(v['train']):>8} {_inr(v['oos']):>8} | "
                  f"{v['taken']:>6} {v['blocked']:>6} {v['conc']:>5} {_inr(v['peak']):>10} | "
                  f"{v['sharpe']:>6} {_inr(v['maxdd']):>9} {v['p']:>5}")
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
