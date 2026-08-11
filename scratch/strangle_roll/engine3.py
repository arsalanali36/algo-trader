"""engine3.py — IV-GATED re-run. Enter only when entry ATM IV is high in its TRAILING
rank (no lookahead). Uses real IV (iv_build.py output) + engine2 sim. RESEARCH ONLY."""
import os, sys, json, csv
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "nifty_trend"))
sys.path.insert(0, HERE)
from engine import load_lake
from engine2 import run_trade, stats, boot_p, significance

W_TRAIL = 60        # trailing window (entry-days) for IV percentile
WARMUP_ENTER = True # during first W_TRAIL days (thin history) -> allow entry (no bias)


def load_entry_iv():
    iv = {}
    with open(os.path.join(HERE, "entry_atm_iv.csv")) as f:
        for r in csv.DictReader(f):
            iv[r["date"]] = float(r["atm_iv"])
    return iv


def make_rank(iv):
    """iv_rank(date) using ONLY past entry-days (strict trailing, no lookahead)."""
    dates = sorted(iv)
    idx = {d: i for i, d in enumerate(dates)}

    def rank(d):
        if d not in idx:
            return None
        i = idx[d]
        past = [iv[dates[j]] for j in range(max(0, i - W_TRAIL), i)]
        if len(past) < 20:
            return 1.0 if WARMUP_ENTER else 0.0
        return sum(1 for p in past if p <= iv[d]) / len(past)   # percentile among trailing days
    return rank


def run_seq_gated(days, roll, trig, wing, holding, eligible, dates):
    dates = sorted(dates); n = len(dates); idx = 0; out = []
    while idx < n:
        d = dates[idx]
        if eligible is not None and not eligible(d):
            idx += 1
            continue
        r = run_trade(days, d, holding, roll, trig, wing)
        if r and "skip" not in r:
            out.append(r)
            exd = r.get("exit_date", d)
            while idx < n and dates[idx] <= exd:
                idx += 1
        else:
            idx += 1
    return out


if __name__ == "__main__":
    print("loading lake ...", flush=True)
    days = load_lake()
    alld = sorted(days.keys())
    iv = load_entry_iv()
    rankf = make_rank(iv)

    # gate-passing configs to re-test  (roll, trig, wing, name)
    CONFIGS = [
        ("threatened", 100, 250, "thr t100 hedge"),
        ("recenter", 100, 250, "rec t100 hedge"),
        ("threatened", 100, 0,   "thr t100 naked"),
    ]
    GATES = [("nogate", None),
             ("iv>=40", lambda d, r=rankf: (r(d) or 0) >= 0.40),
             ("iv>=50", lambda d, r=rankf: (r(d) or 0) >= 0.50),
             ("iv>=60", lambda d, r=rankf: (r(d) or 0) >= 0.60)]

    results = {}; sig = {}
    print("\n== IV-GATED POSITIONAL (sequential, no overlap) ==", flush=True)
    print(f"{'config':22s} {'gate':8s} {'n':>4} {'net':>9} {'avg':>6} {'win':>5} {'pf':>5} "
          f"{'dd':>9} {'trainAvg':>8} {'oosAvg':>8} {'p':>6} {'shrp':>5} GATE", flush=True)
    for (roll, trig, wing, name) in CONFIGS:
        for (gname, elig) in GATES:
            rows = run_seq_gated(days, roll, trig, wing, "positional", elig, alld)
            key = f"{name} | {gname}"
            results[key] = rows
            s = stats(rows)
            z = significance(rows) if s["n"] >= 20 else {}
            sig[key] = z
            ta = z.get("train", {}).get("avg", "-") if z else "-"
            oa = z.get("oos", {}).get("avg", "-") if z else "-"
            pf = z.get("p_full", "-") if z else "-"
            sh = z.get("sharpe_ann", "-") if z else "-"
            gp = ("PASS" if z.get("gate_pass") else "fail") if z else "-"
            print(f"{name:22s} {gname:8s} {s['n']:>4} {s['net']:>9} {s['avg']:>6} {s['win']:>5} "
                  f"{s['pf']:>5} {s['dd']:>9} {str(ta):>8} {str(oa):>8} {str(pf):>6} {str(sh):>5} {gp}",
                  flush=True)

    json.dump({"rows": results, "sig": sig}, open(os.path.join(HERE, "ivgate_results.json"), "w"),
              indent=1, default=str)
    print("\nwrote ivgate_results.json", flush=True)
