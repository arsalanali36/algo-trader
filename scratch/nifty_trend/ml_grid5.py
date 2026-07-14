"""Task 5b (ml-strategy-mining) — EXHAUSTIVE GRID over the user-seeded fib/premium/OI
family, evaluated with the SAME machinery as the GP run (ml_gp.Data + day_pnl +
sharpe_of + MIN_TRADES + ml_mining_log + deflated-Sharpe). Grid first, GP second —
the space is bounded/discrete, so enumeration beats random-seed GP here.

Dimensions (all from ml_features_v3's Task-5a columns; day-level, known 09:20:00):
    confirm candle k       {2, 3, 5}
    fib condition          fibpos_k <= r | fibpos_k > r,  r in {.236,.382,.5,.618,.786}
    direction              continuation (conf_k>0) | reversal (conf_k<0)
    premium behavior       none | pm_class_k == c, c in 1..6
    OI buildup filter      none | oi_bld_k == q,  q in 1..4
    combo                  all 12 (long_ce/long_pe/short_straddle/long_straddle x
                           eod/overnight/expiry)
= 3*5*2*2*7*5 = 2,100 rules x 12 combos = 25,200 evaluations.

Discipline (same as ml_gp.py):
  - fitness/selection sees the EVOLUTION window ONLY (< 2025-07-01)
  - every evaluation logged to ml_mining_log.csv (DSR N grows accordingly)
  - validation (2025-07..2026-04-13) + DSR computed for the full TOP-N afterwards,
    salvage-report format — "honest, show the collapse"
  - lockbox (>= 2026-04-15) not on this machine's mining lake at all

Run:  python -X utf8 ml_grid5.py          # full grid -> ml_grid5_report.json
"""
import os
import json
import time
import itertools

import numpy as np

import ml_gate
import ml_gp

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_ID = "grid_task5"
REPORT = os.path.join(HERE, "ml_grid5_report.json")

FIB_R = (0.236, 0.382, 0.5, 0.618, 0.786)
KS = (2, 3, 5)
PM_CLASSES = (None, 1, 2, 3, 4, 5, 6)
OI_CLASSES = (None, 1, 2, 3, 4)
PM_NAME = {1: "idx_up_ce_up", 2: "idx_up_ce_down", 3: "idx_dn_pe_up",
           4: "idx_dn_pe_down", 5: "co_expanding", 6: "co_contracting"}
OI_NAME = {1: "long_buildup", 2: "short_buildup", 3: "short_covering", 4: "long_unwinding"}
TOP_N = 60          # validation + DSR on all of these, not just top-1

# absolute fib PRICE levels are stored-per-day data, not mining features
EXTRA_EXCLUDE = {c for r in FIB_R for c in ("fibp_h_%d" % int(r * 1000),
                                            "fibp_l_%d" % int(r * 1000))}


def build_rules(D):
    """enumerate the grid as ml_gp-genome dicts (conds use feature-column indices)."""
    fi = {n: i for i, n in enumerate(D.feat_names)}
    rules = []
    for k, r, side, conf, pm, oi in itertools.product(
            KS, FIB_R, (0, 1), (1, -1), PM_CLASSES, OI_CLASSES):
        conds = [[fi["fibpos_%d" % k], side, float(r)]]
        conds.append([fi["conf_%d" % k], 1, 0.5] if conf == 1
                     else [fi["conf_%d" % k], 0, -0.5])
        if pm is not None:   # pm_class = ONE per day (lake is 5m — see ml_features v3 note)
            conds += [[fi["pm_class"], 1, pm - 0.5], [fi["pm_class"], 0, pm + 0.5]]
        if oi is not None:
            conds += [[fi["oi_bld_%d" % k], 1, oi - 0.5], [fi["oi_bld_%d" % k], 0, oi + 0.5]]
        desc = ("k%d fibpos%s%.3f %s%s%s"
                % (k, "<=" if side == 0 else ">", r,
                   "CONT" if conf == 1 else "REV",
                   (" pm=" + PM_NAME[pm]) if pm else "",
                   (" oi=" + OI_NAME[oi]) if oi else ""))
        for ci in range(len(D.combos)):
            rules.append({"conds": conds, "combo": ci, "_desc": desc})
    return rules


def main():
    t0 = time.time()
    # point the shared Data loader at v3 + keep absolute price levels out of X
    ml_gp.FEATS = os.path.join(HERE, "ml_features_v3.csv.gz")
    ml_gp.EXCLUDE = set(ml_gp.EXCLUDE) | EXTRA_EXCLUDE
    D = ml_gp.Data()
    n0 = ml_gate.count_trials()          # cumulative BEFORE this grid (all approaches)
    rules = build_rules(D)
    print("grid: %d rule-combo evaluations (evolution window only)" % len(rules), flush=True)

    rows, log_rows = [], []
    for i, g in enumerate(rules):
        dp = ml_gp.day_pnl(g, D, D.evo_bars)
        hold = D.combos[g["combo"]].split("|")[1]
        ntr = len(dp)
        sr = ml_gp.sharpe_of(dp, D.evo_days) if ntr >= ml_gp.MIN_TRADES[hold] else None
        rows.append({"g": g, "rule": "[%s] %s" % (D.combos[g["combo"]], g["_desc"]),
                     "trades_evo": ntr, "sr_evo": sr,
                     "pnl_evo": round(sum(dp.values())) if dp else 0})
        log_rows.append(dict(candidate="grid5/%d" % i, target=D.combos[g["combo"]],
                             params={"rule": g["_desc"]},
                             train_sharpe=round(sr, 3) if sr is not None else "",
                             oos_sharpe="",
                             verdict="grid trades=%d%s" % (ntr, "" if sr is not None else " <min")))
        if len(log_rows) >= 5000:
            ml_gate.log_trials(log_rows, run_id=RUN_ID, approach="grid")
            log_rows = []
        if i % 5000 == 0:
            print("  %d/%d (%.0fs)" % (i, len(rules), time.time() - t0), flush=True)
    if log_rows:
        ml_gate.log_trials(log_rows, run_id=RUN_ID, approach="grid")

    passed = [r for r in rows if r["sr_evo"] is not None]
    passed.sort(key=lambda r: -r["sr_evo"])
    n_trials = n0 + len(rules)
    print("\n%d/%d met MIN_TRADES; DSR N (cumulative all mining) = %d"
          % (len(passed), len(rules), n_trials), flush=True)

    # ---- validation + DSR on the full TOP-N (salvage-report discipline) ----
    trial_srs = ml_gp.null_sharpes(D)
    all_days = np.unique(D.day_id)
    out = []
    for r in passed[:TOP_N]:
        g = r["g"]
        dp_val = ml_gp.day_pnl(g, D, D.val_bars)
        dp_all = ml_gp.day_pnl(g, D, np.ones(len(D.X), dtype=bool))
        sr_val = ml_gp.sharpe_of(dp_val, D.val_days)
        ser = np.zeros(len(all_days))
        pos = {d: i for i, d in enumerate(all_days)}
        for d, x in dp_all.items():
            ser[pos[d]] = x
        dsr = ml_gate.deflated_sharpe(ser / 250000.0, n_trials=n_trials,
                                      trial_sharpes_annual=trial_srs)
        rec = {"rule": r["rule"], "combo": D.combos[g["combo"]],
               "sr_evo": round(r["sr_evo"], 3), "sr_val": round(sr_val, 3),
               "sr_full": round(ml_gp.sharpe_of(dp_all, all_days), 3),
               "trades_evo": r["trades_evo"], "trades_val": len(dp_val),
               "pnl_evo": r["pnl_evo"], "pnl_val": round(sum(dp_val.values())) if dp_val else 0,
               "dsr_prob": dsr["dsr_prob"], "dsr_pass": dsr["pass"],
               "conds": g["conds"], "combo_idx": g["combo"]}
        if r["sr_evo"] > 0.5 and sr_val > 0.5:
            try:
                from ml_rules_a import corr_vs_shipped
                dpnl = {str(D.days[d]): v for d, v in dp_all.items()}
                cs = corr_vs_shipped(dpnl)
                rec["max_abs_corr"] = max(abs(v) for v in cs.values()) if cs else None
            except Exception as e:
                rec["corr_err"] = str(e)
        out.append(rec)

    json.dump({"run_id": RUN_ID, "date": "2026-07-14", "n_grid": len(rules),
               "n_met_min_trades": len(passed), "n_trials_cumulative": n_trials,
               "note": ("fitness=evolution window only; validation+DSR on full top-%d; "
                        "lockbox untouched (not on this machine)" % TOP_N),
               "top": out}, open(REPORT, "w"), indent=1)
    print("\ntop by evo Sharpe (evo | val | full | dsr | trades e/v):")
    for r in out[:20]:
        print("  %5.2f | %5.2f | %5.2f | %.3f%s | %d/%d  %s"
              % (r["sr_evo"], r["sr_val"], r["sr_full"], r["dsr_prob"],
                 "*PASS*" if r["dsr_pass"] else "", r["trades_evo"], r["trades_val"],
                 r["rule"][:80]))
    print("\nwrote %s  (%.0fs total)" % (REPORT, time.time() - t0))


if __name__ == "__main__":
    main()
