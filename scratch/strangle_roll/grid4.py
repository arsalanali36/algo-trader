"""grid4.py — 02.15 strangle roll+hedge PARAMETER GRID on the REAL lake (research only).

Why (2026-09-02): Lab distribution of the deployed variant (thr t100 · hedge 250 · IV>=40 ·
take 50%) has 280/370 trades inside -3.6k..+1.7k (1 lot) — the 50%-credit target caps wins
small while "deadline" exits carry the losses. User asked whether a grid can find a better
shaped variant. Sweeps the knobs engine2 already has:

    dist  (sell distance from spot)    200 / 250 / 300 / 350
    wing  (hedge distance beyond sold) 150 / 200 / 250   (dist+wing <= 500 = lake ATM+-10 cap)
    trig  (roll trigger)               100 / 150
    take  (target = take*credit)       0.5 / 0.7 / 1.0 (=hold to deadline)
    gate  (trailing IV-rank)           none / >=40 / >=60

Engine = engine2.run_trade (real 1-min premium, real date-aware charges) + engine3 IV rank.
Sequential positional book. Rank = min(train avg, OOS avg) per trade (TRAP #103), bootstrap p.
Also reports DISTRIBUTION shape: p10/p50/p90, share inside +-2k, avg win / avg loss.
RESEARCH ONLY. Lives in scratchpad while the repo write-lock is held; imports engine from repo.
"""
import os, sys, json, time, statistics as st
HERE_REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
REPO = HERE_REPO
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO, "scratch", "nifty_trend"))
sys.path.insert(0, os.path.join(REPO, "scratch", "strangle_roll"))
import engine as E
import engine2 as E2
import engine3 as E3

QUICK = "--quick" in sys.argv
DISTS = [200, 250, 300, 350]
WINGS = [150, 200, 250]
TRIGS = [100, 150]
TAKES = [0.5, 0.7, 1.0]
GATES = [("none", None), ("iv>=40", 0.40), ("iv>=60", 0.60)]
if QUICK:
    DISTS, WINGS, TRIGS, TAKES, GATES = [250], [250], [100], [0.5], [("iv>=40", 0.40)]


def run_config(days, alld, rankf, dist, wing, trig, take, gate_thr):
    E2.DIST = dist                      # engine2 reads the module-level name → patchable
    elig = None if gate_thr is None else (lambda d, r=rankf, t=gate_thr: (r(d) or 0) >= t)
    dates = sorted(alld); n = len(dates); i = 0; out = []
    while i < n:
        d = dates[i]
        if elig is not None and not elig(d):
            i += 1; continue
        r = E2.run_trade(days, d, "positional", "threatened", trig, wing, take_pct=take)
        if r and "skip" not in r:
            out.append(r)
            exd = r.get("exit_date", d)
            while i < n and dates[i] <= exd:
                i += 1
        else:
            i += 1
    return out


def _net(r):
    for k in ("net", "pnl", "net_pnl"):
        if k in r: return float(r[k])
    return 0.0


def shape(rows):
    pn = sorted(_net(r) for r in rows)
    if not pn: return {}
    n = len(pn); wins = [p for p in pn if p > 0]; losses = [p for p in pn if p < 0]
    return {"n": n, "net": round(sum(pn)), "avg": round(sum(pn) / n),
            "p10": round(pn[n // 10]), "p50": round(pn[n // 2]), "p90": round(pn[9 * n // 10]),
            "win": round(100 * len(wins) / n, 1),
            "avg_win": round(st.mean(wins)) if wins else 0, "avg_loss": round(st.mean(losses)) if losses else 0,
            "in_pm2k": round(100 * sum(1 for p in pn if -2000 <= p <= 2000) / n, 1),
            "worst": round(pn[0]), "best": round(pn[-1])}


def main():
    t0 = time.time()
    print("loading lake ...", flush=True)
    days = E.load_lake(); alld = sorted(days.keys())
    iv = E3.load_entry_iv(); rankf = E3.make_rank(iv)
    print(f"lake {len(alld)} days, loaded in {time.time()-t0:.0f}s", flush=True)
    grid = [(d, w, tr, tk, g) for d in DISTS for w in WINGS if d + w <= 500
            for tr in TRIGS for tk in TAKES for g in GATES]
    print(f"{len(grid)} configs", flush=True)
    results = []
    for k, (dist, wing, trig, take, (gname, gthr)) in enumerate(grid, 1):
        t1 = time.time()
        rows = run_config(days, alld, rankf, dist, wing, trig, take, gthr)
        if k == 1: print("sample row keys:", list(rows[0].keys()) if rows else None, flush=True)
        s = E2.stats(rows) if rows else {}
        z = E2.significance(rows) if len(rows) >= 20 else {}
        sh = shape(rows)
        tr_avg = (z.get("train") or {}).get("avg"); oos_avg = (z.get("oos") or {}).get("avg")
        rank_key = min(tr_avg, oos_avg) if (tr_avg is not None and oos_avg is not None) else -1e9
        rec = {"dist": dist, "wing": wing, "trig": trig, "take": take, "gate": gname, "stats": s,
               "sig": {k2: v for k2, v in z.items() if k2 in ("p_full", "sharpe_ann", "gate_pass", "train", "oos")},
               "shape": sh, "rank_key": rank_key, "secs": round(time.time() - t1, 1)}
        results.append(rec)
        print(f"[{k}/{len(grid)}] d{dist} w{wing} t{trig} take{take} {gname:7s} n={sh.get('n',0):3d} "
              f"net={sh.get('net',0):>8} avg={sh.get('avg',0):>5} win={sh.get('win',0):>5} p50={sh.get('p50',0):>5} "
              f"in±2k={sh.get('in_pm2k',0):>5}% p={z.get('p_full','-')} shrp={z.get('sharpe_ann','-')} "
              f"tr/oos={tr_avg}/{oos_avg} ({rec['secs']}s)", flush=True)
        json.dump(results, open(os.path.join(OUT, "grid4_results.json"), "w"), indent=1, default=str)
    ok = sorted([r for r in results if r["rank_key"] > -1e8], key=lambda r: r["rank_key"], reverse=True)
    base = [r for r in results if (r["dist"], r["wing"], r["trig"], r["take"], r["gate"]) == (250, 250, 100, 0.5, "iv>=40")]
    L = ["# grid4 — 02.15 strangle roll+hedge parameter grid (REAL lake, 1 lot, research)", "",
         f"configs {len(results)} · lake days {len(alld)} · rank = min(train avg, OOS avg)/trade · p = bootstrap", ""]
    if base:
        b = base[0]; s = b["shape"]
        L += ["## Deployed variant (d250 w250 t100 take0.5 iv>=40)", "",
              f"n {s.get('n')} · net ₹{s.get('net')} · avg ₹{s.get('avg')} · win {s.get('win')}% · p50 ₹{s.get('p50')} · in±2k {s.get('in_pm2k')}% · p={b['sig'].get('p_full')} · Sharpe {b['sig'].get('sharpe_ann')} · min(train,oos) {b['rank_key']}", ""]
    L += ["## Top 15 by min(train, OOS) avg/trade", "",
          "| # | dist | wing | trig | take | gate | n | net | avg | win% | p50 | in ±2k | avg win | avg loss | worst | p | Sharpe | train | OOS |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ok[:15], 1):
        s = r["shape"]; z = r["sig"]
        L.append(f"| {i} | {r['dist']} | {r['wing']} | {r['trig']} | {r['take']} | {r['gate']} | {s.get('n')} | {s.get('net')} | {s.get('avg')} | {s.get('win')} | {s.get('p50')} | {s.get('in_pm2k')}% | {s.get('avg_win')} | {s.get('avg_loss')} | {s.get('worst')} | {z.get('p_full')} | {z.get('sharpe_ann')} | {(z.get('train') or {}).get('avg')} | {(z.get('oos') or {}).get('avg')} |")
    L += ["", "## Caveats", "",
          "- 1 lot, real 1-min premium, real date-aware charges + engine slip; lake ATM±10 caps dist+wing ≤ 500.",
          "- A grid this size WILL overfit if the top row is picked blindly — look for a PLATEAU (neighbours also good), require p<0.05 + train≈OOS.",
          "- take=1.0 = hold to deadline (no credit target). Distribution shape is a by-product of the exit rule, not a bug.",
          f"- total runtime {round((time.time()-t0)/60,1)} min"]
    open(os.path.join(OUT, "grid4_report.md"), "w", encoding="utf-8").write("\n".join(L))
    print("\nwrote grid4_results.json + grid4_report.md  (%.1f min)" % ((time.time() - t0) / 60), flush=True)


if __name__ == "__main__":
    main()
