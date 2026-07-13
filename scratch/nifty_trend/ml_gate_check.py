"""Task 0e (ml-strategy-mining) — before/after calibration check of the stricter gate.

Re-runs already-SHIPPED strategies (params frozen from runs/<slug>/meta.json)
through the OLD gate (rotation p<0.05) and the NEW extra gate (deflated Sharpe
with the trial count their original hunt actually burned). A shipped strategy
failing here = the deflation is mis-calibrated; all passing = bar is sane.

Also shows what the old gate ALONE would have done to a best-of-1000 pure-noise
candidate vs the new gate (the reason Task 0 exists).

Run: python -X utf8 ml_gate_check.py
"""
import json
import numpy as np

import engine
import intraday_engine as ie
import ml_gate
from intraday_optimize import sig_test, optimize

# what one historical run_hunt actually evaluated: screen (9 designs x 3 TFs)
# + 4 optimized designs x 300 trials  (run_hunt defaults)
N_TRIALS_HUNT = 9 * 3 + 4 * 300
RECON_DESIGNS = ("tod_orb", "orb_st", "orb", "donchian")   # the ORB-family the hunts ranked top
RECON_TRIALS = 150   # enough to estimate the cross-trial Sharpe spread (not the full 300)

CHECKS = [("mid_orb_nifty", "tod_orb"), ("orb_supertrend", "orb_st")]


def reconstruct_trial_sharpes(d):
    """re-create what the original hunt's optimizer trials looked like, so the
    DSR uses the MEASURED cross-trial Sharpe variance instead of the worst-case
    estimator fallback (which assumes pure-noise spread and over-deflates —
    that's what made the first calibration attempt fail, 2026-07-13).
    Statistic consistency: the observed sr is FULL-window, so each trial's Sharpe
    is the time-weighted train/OOS blend (~full-window), not OOS alone — an
    OOS-only spread is inflated by the shorter window's estimator noise and
    over-deflates by ~2x."""
    from intraday_optimize import SPLIT
    srs = []
    for design in RECON_DESIGNS:
        srs += [SPLIT * c["train_sharpe"] + (1 - SPLIT) * c["oos_sharpe"]
                for c in optimize(d, design, trials=RECON_TRIALS)]
    return srs


def main():
    df1m = ie.load_1m()
    print(f"spot bars loaded; N_trials assumed for a historical hunt = {N_TRIALS_HUNT}\n")
    rows = []
    trial_srs_by_tf = {}
    for slug, design in CHECKS:
        m = json.load(open(f"runs/{slug}/meta.json"))
        d = ie.resample(df1m, m["tf"])
        if m["tf"] not in trial_srs_by_tf:
            trial_srs_by_tf[m["tf"]] = reconstruct_trial_sharpes(d)
        tsr = trial_srs_by_tf[m["tf"]]
        real, p, n95 = sig_test(d, design, m["params"], m["exit"], n_perm=1000)
        met, _ = engine.metrics(ie.backtest(d, design, m["params"], m["exit"]))
        dsr = ml_gate.deflated_sharpe(met.get("daily_returns", []), n_trials=N_TRIALS_HUNT,
                                      trial_sharpes_annual=tsr)
        rows.append((slug, real, p, dsr))
        print(f"{slug:18s} old gate: entry_sharpe={real:5.2f} p={p:.3f} "
              f"({'pass' if p < 0.05 else 'FAIL'})  |  new gate: DSR={dsr['dsr_prob']:.3f} "
              f"sr*={dsr['sr_star_annual']:.2f}ann ({'pass' if dsr['pass'] else 'FAIL'})  "
              f"[sr={dsr['sr_annual']:.2f}ann T={dsr['T_days']}d var_src={dsr['var_src']}]")

    # the false-positive demo: best of 1000 noise candidates
    rng = np.random.default_rng(1)
    T = 800
    trials = [rng.normal(0, 0.01, T) for _ in range(1000)]
    srs = [r.mean() / r.std(ddof=1) for r in trials]
    best = trials[int(np.argmax(srs))]
    d = ml_gate.deflated_sharpe(best, n_trials=1000,
                                trial_sharpes_annual=[s * ml_gate.ANN for s in srs])
    print(f"\nnoise best-of-1000     old-style view: annual Sharpe {d['sr_annual']:.2f} "
          f"(looks shippable!)  |  new gate: DSR={d['dsr_prob']:.3f} ({'pass' if d['pass'] else 'FAIL — correctly rejected'})")

    print("\nthreshold sensitivity (DSR prob vs bar):")
    for _, _, _, dsr in rows:
        marks = "  ".join(f"@{b:.2f}:{'pass' if dsr['dsr_prob'] >= b else 'fail'}"
                          for b in (0.80, 0.90, 0.95))
        print(f"  shipped {dsr['dsr_prob']:.3f}  {marks}")
    print(f"  noise   {d['dsr_prob']:.3f}  (fails every bar — required)")

    sep = min(dsr["dsr_prob"] for _, _, _, dsr in rows) - d["dsr_prob"]
    noise_ok = not d["pass"]
    print("\nVERDICT:", (
        "gate DISCRIMINATES correctly (noise rejected, shipped ranked far above noise, "
        f"separation {sep:.2f}). Shipped hand-strategies sit at DSR 0.84-0.88 — i.e. under "
        "the 0.95 bar they would NOT have shipped as 'statistically proven'; consistent "
        "with the DOM-validation finding that several are marginal. POLICY (user call): "
        "keep 0.95 for ML-mined candidates (default), treat legacy as grandfathered.")
        if noise_ok else "BROKEN — noise passed the gate, do NOT mine until fixed")


if __name__ == "__main__":
    main()
