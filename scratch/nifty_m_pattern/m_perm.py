"""
Permutation significance for the FROZEN config: medium-M + hold+1day, wing 250,
50% credit. Null = random-entry-time on the SAME trade dates (holds day-selection
fixed, tests whether the M-rollover MINUTE adds value vs an arbitrary minute).
p = fraction of null totals >= real total.
"""
import sys, os, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt as M

WING, TAKE, MHD = 250, 0.50, 1
MPARAMS = M.M_PRESETS["medium"]
ITERS = 1000
SEED = 11


def total_net(rows):
    return sum(r["net"] for r in rows)


if __name__ == "__main__":
    print("loading lake ...", flush=True)
    days = M.load_lake()
    sigs = M.all_signals(days, MPARAMS)
    real_rows = M.run(days, sigs, TAKE, WING, MHD)          # sequential positional
    real_dates = [r["date"] for r in real_rows]
    real_total = total_net(real_rows)
    real_avg = real_total / max(1, len(real_rows))
    print(f"  real: {len(real_rows)} trades  total=Rs {real_total:,.0f}  "
          f"avg=Rs {real_avg:,.0f}", flush=True)

    # candidate minutes per real trade-date (within the entry window, actually present)
    cand = {}
    for d in real_dates:
        ser = M.atm_combined_series(days[d])
        cand[d] = [hm for (hm, _) in ser if M.SIG_LO_HM <= hm <= M.SIG_HI_HM]

    rng = np.random.default_rng(SEED)
    null_tot, null_avg = [], []
    for it in range(ITERS):
        tot, n = 0.0, 0
        for d in real_dates:
            ms = cand.get(d)
            if not ms:
                continue
            hm = int(rng.choice(ms))
            r = M.run_trade(days, d, hm, TAKE, WING, MHD)
            if r and "skip" not in r:
                tot += r["net"]; n += 1
        null_tot.append(tot)
        null_avg.append(tot / max(1, n))
        if (it + 1) % 100 == 0:
            arr = np.array(null_tot)
            print(f"  [{it+1}/{ITERS}] null mean total=Rs {arr.mean():,.0f}  "
                  f"p(null>=real)={float((arr >= real_total).mean()):.4f}", flush=True)

    nt = np.array(null_tot); na = np.array(null_avg)
    p_tot = float((nt >= real_total).mean())
    p_avg = float((na >= real_avg).mean())
    z = (real_total - nt.mean()) / (nt.std() or 1)
    print("\n=== RESULT (medium-M + hold+1day, wing 250, 50% credit) ===")
    print(f"  real total     : Rs {real_total:,.0f}  ({len(real_rows)} trades)")
    print(f"  null total mean: Rs {nt.mean():,.0f}  (std Rs {nt.std():,.0f})")
    print(f"  null total p95 : Rs {np.percentile(nt,95):,.0f}   p99: Rs {np.percentile(nt,99):,.0f}")
    print(f"  z-score        : {z:.2f}")
    print(f"  p (total)      : {p_tot:.4f}")
    print(f"  p (avg/trade)  : {p_avg:.4f}")
    json.dump({"real_total": real_total, "n": len(real_rows), "iters": ITERS,
               "null_mean": float(nt.mean()), "null_std": float(nt.std()),
               "p_total": p_tot, "p_avg": p_avg, "z": float(z)},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "m_perm_result.json"), "w"), indent=1)
    print("\nwrote m_perm_result.json")
