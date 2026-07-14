"""Salvage the CLEAN part of the void v1 GP run (TRAP #112): the eod / expiry-hold
combos were priced correctly all along — only overnight holds crossed the contract
roll. This scans the 654MB trials log for the best NON-overnight rules by their
logged evolution-window Sharpe, de-dupes, re-evaluates them on the roll-guarded
table (evo + validation + DSR), and prints an honest leaderboard.

Selection uses ONLY the logged evo Sharpe (no validation peeking); validation is
computed once per surviving rule, same discipline as a wave-end.

Run: python -X utf8 ml_gp_salvage.py [--top 60]
"""
import re
import json
import argparse

import numpy as np
import pandas as pd

import ml_gate
import ml_gp

COND_RE = re.compile(r"(\w+) (<=|>) (-?[\d.e+-]+)")
RULE_RE = re.compile(r"^\[([^\]]+)\] IF (.+)$")


def parse_rule(s, D):
    m = RULE_RE.match(s)
    if not m:
        return None
    combo, rest = m.group(1), m.group(2)
    if combo not in D.combos:
        return None
    fi = {n: i for i, n in enumerate(D.feat_names)}
    conds = []
    for f, op, thr in COND_RE.findall(rest):
        if f not in fi:
            return None
        conds.append([fi[f], 0 if op == "<=" else 1, float(thr)])
    if not 1 <= len(conds) <= ml_gp.MAX_CONDS:
        return None
    return {"conds": conds, "combo": D.combos.index(combo)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=60)
    args = ap.parse_args()
    D = ml_gp.Data()

    print("scanning trials log for best non-overnight v1 rules ...", flush=True)
    best = {}
    for chunk in pd.read_csv(ml_gate.MINING_LOG, usecols=["run_id", "target", "params", "train_sharpe"],
                             chunksize=500_000):
        c = chunk[(chunk.run_id == "gp_v1") & (~chunk.target.astype(str).str.contains("overnight"))]
        c = c.assign(sr=pd.to_numeric(c.train_sharpe, errors="coerce")).dropna(subset=["sr"])
        c = c[c.sr > 1.0]
        for p, sr in zip(c.params, c.sr):
            if p not in best or sr > best[p]:
                best[p] = sr
    print("clean rules with evo sharpe > 1.0:", len(best), flush=True)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])[:args.top]

    null = ml_gp.null_sharpes(D)
    n_trials = ml_gate.count_trials(approach="gp")
    all_days = np.unique(D.day_id)
    rows = []
    for p, sr_logged in ranked:
        try:
            rule = json.loads(p)["rule"]
        except Exception:
            continue
        g = parse_rule(rule, D)
        if g is None:
            continue
        dp_evo = ml_gp.day_pnl(g, D, D.evo_bars)
        dp_val = ml_gp.day_pnl(g, D, D.val_bars)
        dp_all = ml_gp.day_pnl(g, D, np.ones(len(D.X), dtype=bool))
        sr_evo = ml_gp.sharpe_of(dp_evo, D.evo_days)
        sr_val = ml_gp.sharpe_of(dp_val, D.val_days)
        ser = np.zeros(len(all_days)); pos = {d: i for i, d in enumerate(all_days)}
        for d, x in dp_all.items():
            ser[pos[d]] = x
        dsr = ml_gate.deflated_sharpe(ser / 250000.0, n_trials=n_trials,
                                      trial_sharpes_annual=null)
        rows.append(dict(rule=rule, sr_evo=round(sr_evo, 2), sr_val=round(sr_val, 2),
                         trades_evo=len(dp_evo), trades_val=len(dp_val),
                         pnl_val=round(sum(dp_val.values())),
                         dsr=dsr["dsr_prob"], dsr_pass=dsr["pass"]))
    rows.sort(key=lambda r: -min(r["sr_evo"], r["sr_val"]))
    json.dump(rows, open("ml_gp_salvage_report.json", "w"), indent=1)
    print("\ntop salvaged (by min(evo,val)):")
    for r in rows[:15]:
        print("  evo=%5.2f val=%5.2f dsr=%.3f%s tr=%d/%d  %s"
              % (r["sr_evo"], r["sr_val"], r["dsr"], "*PASS*" if r["dsr_pass"] else "",
                 r["trades_evo"], r["trades_val"], r["rule"][:95]))
    print("\nwrote ml_gp_salvage_report.json (%d rules re-evaluated)" % len(rows))


if __name__ == "__main__":
    main()
