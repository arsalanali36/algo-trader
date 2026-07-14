"""Task 5c (ml-strategy-mining) — SEED the top grid survivors into a BOUNDED GP run,
mixed with the full v3 signal vocabulary (fib/pm/oi + iv_rank_60d/atm_skew/pcr_oi/...),
so GP can (i) fine-tune the continuous thresholds the grid could only step, and
(ii) try COMBINING this short-straddle family with the volatility-regime signals that
the grid never touched (the spec's explicit 5c ask).

Reuses ml_gp.Data / day_pnl / fitness / mutate / crossover / null_sharpes / deflated_sharpe
as-is (same discipline: fitness = EVOLUTION window only; validation + DSR after; every genome
logged to ml_mining_log; lockbox off-machine). BOUNDED, not the overnight run:
POP=200, GENS=25, 2 short-straddle-focused waves seeded from the grid.

Run:  python -X utf8 ml_gp_seed5.py   -> ml_gp_seed5_report.json
"""
import os
import json
import time

import numpy as np

import ml_gate
import ml_gp

HERE = os.path.dirname(os.path.abspath(__file__))
GRID_REPORT = os.path.join(HERE, "ml_grid5_report.json")
OUT = os.path.join(HERE, "ml_gp_seed5_report.json")
RUN_ID = "gp_seed5"

POP, GENS, WAVES = 200, 25, 2
ELITE, TOURN = 10, 4
P_CROSS, P_MUT = 0.6, 0.9
N_SEED = 14          # top grid survivors injected as founder genomes

# grid produced absolute fib-price levels as data — keep them out of the mining X, same as ml_grid5
EXTRA_EXCLUDE = {c for rr in (0.236, 0.382, 0.5, 0.618, 0.786)
                 for c in ("fibp_h_%d" % int(rr * 1000), "fibp_l_%d" % int(rr * 1000))}


def load_seeds(D):
    """top grid survivors -> ml_gp genome dicts (conds already index-based, same feat order)."""
    rep = json.load(open(GRID_REPORT))
    seeds = []
    for t in rep["top"][:N_SEED]:
        seeds.append({"conds": [list(c) for c in t["conds"]], "combo": t["combo_idx"]})
    return seeds


def evaluate_final(D, pop, n_trials, trial_srs):
    all_days = np.unique(D.day_id)
    uniq, out = set(), []
    scored = sorted([(ml_gp.fitness(g, D)[0], g) for g in pop], key=lambda t: -t[0])
    for fit, g in scored:
        k = ml_gp.genome_key(g)
        if k in uniq or fit <= -9:
            continue
        uniq.add(k)
        dp_evo = ml_gp.day_pnl(g, D, D.evo_bars)
        dp_val = ml_gp.day_pnl(g, D, D.val_bars)
        dp_all = ml_gp.day_pnl(g, D, np.ones(len(D.X), dtype=bool))
        sr_evo = ml_gp.sharpe_of(dp_evo, D.evo_days)
        sr_val = ml_gp.sharpe_of(dp_val, D.val_days)
        ser = np.zeros(len(all_days)); pos = {d: i for i, d in enumerate(all_days)}
        for d, x in dp_all.items():
            ser[pos[d]] = x
        dsr = ml_gate.deflated_sharpe(ser / 250000.0, n_trials=n_trials,
                                      trial_sharpes_annual=trial_srs)
        rec = {"rule": ml_gp.rule_str(g, D), "combo": D.combos[g["combo"]],
               "sr_evo": round(sr_evo, 3), "sr_val": round(sr_val, 3),
               "sr_full": round(ml_gp.sharpe_of(dp_all, all_days), 3),
               "trades_evo": len(dp_evo), "trades_val": len(dp_val),
               "dsr_prob": dsr["dsr_prob"], "dsr_pass": dsr["pass"]}
        if sr_evo > 0.5 and sr_val > 0.5:
            try:
                from ml_rules_a import corr_vs_shipped
                cs = corr_vs_shipped({str(D.days[d]): v for d, v in dp_all.items()})
                rec["max_abs_corr"] = max(abs(v) for v in cs.values()) if cs else None
            except Exception as e:
                rec["corr_err"] = str(e)
        out.append(rec)
        if len(out) >= 15:
            break
    return out


def main():
    t0 = time.time()
    ml_gp.FEATS = os.path.join(HERE, "ml_features_v3.csv.gz")
    ml_gp.EXCLUDE = set(ml_gp.EXCLUDE) | EXTRA_EXCLUDE
    D = ml_gp.Data()
    seeds = load_seeds(D)
    print("seeded %d grid survivors into GP; vocab = %d features (fib/pm/oi + iv_rank/skew/pcr/...)"
          % (len(seeds), len(D.feat_names)), flush=True)

    n0 = ml_gate.count_trials()
    seen = set()
    all_pop = []
    for wave in range(WAVES):
        rng = np.random.default_rng(9000 + wave)
        focus = "short_straddle"
        pop = list(seeds) + [ml_gp.rand_genome(D, rng, focus) for _ in range(POP - len(seeds))]
        for gen in range(GENS):
            rg = np.random.default_rng(hash((9000 + wave, gen)) % (2 ** 31))
            scored, log_rows = [], []
            for g in pop:
                fit, ntr, sr = ml_gp.fitness(g, D)
                scored.append((fit, g))
                kk = ml_gp.genome_key(g)
                if kk not in seen:
                    seen.add(kk)
                    log_rows.append(dict(candidate="seed5 w%d/g%d" % (wave, gen),
                                         target=D.combos[g["combo"]],
                                         params={"rule": ml_gp.rule_str(g, D)},
                                         train_sharpe=round(sr, 3), oos_sharpe="",
                                         verdict="seeded-gp trades=%d" % ntr))
            if log_rows:
                ml_gate.log_trials(log_rows, run_id=RUN_ID, approach="gp")
            scored.sort(key=lambda t: -t[0])
            nxt = [g for _, g in scored[:ELITE]]
            while len(nxt) < POP:
                def pick():
                    cand = [scored[int(rg.integers(0, len(scored)))] for _ in range(TOURN)]
                    return max(cand, key=lambda t: t[0])[1]
                child = ml_gp.crossover(pick(), pick(), rg) if rg.random() < P_CROSS else pick()
                if rg.random() < P_MUT:
                    child = ml_gp.mutate(child, D, rg, focus)
                nxt.append(child)
            pop = nxt
            if gen % 10 == 0 or gen == GENS - 1:
                print("  wave %d gen %2d best_fit=%.2f  %s"
                      % (wave, gen, scored[0][0], ml_gp.rule_str(scored[0][1], D)[:95]), flush=True)
        all_pop += pop

    n_trials = ml_gate.count_trials()
    print("\ncumulative DSR N now %d (was %d before seed run)" % (n_trials, n0), flush=True)
    trial_srs = ml_gp.null_sharpes(D)
    out = evaluate_final(D, all_pop, n_trials, trial_srs)
    json.dump({"run_id": RUN_ID, "date": "2026-07-14", "seeded": N_SEED,
               "pop": POP, "gens": GENS, "waves": WAVES, "n_trials_cumulative": n_trials,
               "note": "grid survivors seeded + 39-signal vocab; fitness evo-only; DSR after; lockbox off-machine",
               "top": out}, open(OUT, "w"), indent=1)
    print("\ntop by min(evo,val) (evo | val | full | dsr | tr e/v | corr):")
    for r in sorted(out, key=lambda r: -min(r["sr_evo"], r["sr_val"]))[:12]:
        print("  %5.2f | %5.2f | %5.2f | %.3f%s | %d/%d | %s  %s"
              % (r["sr_evo"], r["sr_val"], r["sr_full"], r["dsr_prob"],
                 "*PASS*" if r["dsr_pass"] else "", r["trades_evo"], r["trades_val"],
                 str(r.get("max_abs_corr", "?"))[:5], r["rule"][:80]))
    print("\nwrote %s (%.0fs)" % (OUT, time.time() - t0))


if __name__ == "__main__":
    main()
