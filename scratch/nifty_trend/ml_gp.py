"""Task 3 (ml-strategy-mining) — genetic programming over entry rules x
(instrument x hold) combos, against the precomputed real-premium P&L table.

DESIGN FOR AN UNATTENDED OVERNIGHT RUN (user asleep, laptop on charge):
  - WAVES: each wave = one full evolution (pop POP x GENS gens, own seed + focus).
    A wave ends by appending its complete result to ml_gp_waves.json -> at ANY
    abandon point, every finished wave is a usable milestone.
  - RESUME: checkpoint per generation (ml_gp_ckpt.json). Re-running the script
    continues the interrupted wave at its last generation (deterministic per-gen
    seeds — no RNG state serialization needed).
  - STOP: runs waves until STOP_AT (07:15 local) or until a file named
    ml_gp_STOP exists in this folder.
  - HONESTY: every unique genome evaluated is appended to ml_mining_log.csv
    (tasklist 3c) — the deflated-Sharpe N at wave-end uses the TRUE total.
    Complexity cap: 1..4 AND-conditions (tasklist 3b). Fitness = Sharpe on the
    EVOLUTION window only (2021-07-29..2025-06-30); the VALIDATION window
    (2025-07-01..2026-04-13) is never seen by selection. The lockbox
    (>=2026-04-15) is not even on this machine's mining lake.
  - Overlap control: overnight/expiry holds allow ONE open trade at a time
    (an entry is skipped while the previous trade is still on).

Run:  python -X utf8 ml_gp.py            # run waves until stop
      python -X utf8 ml_gp.py report     # consolidated report of finished waves
"""
import os
import sys
import json
import time
import datetime as dt

import numpy as np
import pandas as pd

import ml_gate
import bs_option as bs

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_ID = "gp_v2"        # v2 = contract-roll-guarded P&L table (TRAP #112); the
                        # v1 overnight run (3.43M trials) converged on the roll
                        # artifact and is void — but its trials still count in N
PNL_NPZ = os.path.join(HERE, "ml_gp_pnl.npz")
FEATS = os.path.join(HERE, "ml_features_v2.csv.gz")
WAVES = os.path.join(HERE, "ml_gp_waves.json")
CKPT = os.path.join(HERE, "ml_gp_ckpt.json")
STOPF = os.path.join(HERE, "ml_gp_STOP")
ERRLOG = os.path.join(HERE, "ml_gp_errors.log")

POP, GENS = 300, 50
ELITE, TOURN = 12, 4
P_CROSS, P_MUT = 0.6, 0.9
MAX_CONDS = 4
EVO_END = dt.date(2025, 7, 1)          # evolution < this; validation >= this
STOP_AT = (dt.datetime.fromisoformat(os.environ["ML_GP_STOP"])
           if os.environ.get("ML_GP_STOP") else dt.datetime(2026, 7, 14, 7, 15))
MIN_TRADES = {"eod": 120, "overnight": 80, "expiry": 40}
FOCUS_CYCLE = [None, "short_straddle", "long_straddle", "long_ce", "long_pe"]

EXCLUDE = {"Datetime", "day", "spot", "strike", "ce_close", "pe_close", "straddle",
           "month_iv", "ce_oi", "pe_oi"}


# ---------------- data ----------------
class Data:
    def __init__(self):
        z = np.load(PNL_NPZ, allow_pickle=False)
        f = pd.read_csv(FEATS, parse_dates=["Datetime"])
        ml_gate.assert_no_lockbox(f, what="features(gp)")
        fdt = f.Datetime.values.astype("datetime64[m]").astype(np.int64)
        pos = {t: i for i, t in enumerate(z["dt"])}
        keep = np.array([t in pos for t in fdt])
        rows = np.array([pos[t] for t in fdt[keep]])
        f = f.loc[keep].reset_index(drop=True)
        self.combos = [str(c) for c in z["combos"]]
        self.pnl = z["pnl"][rows].astype(np.float32)
        self.day_id = z["day_id"][rows].astype(np.int64)
        self.days = z["days"]                      # 'YYYY-MM-DD' per day_id
        self.feat_names = [c for c in f.columns if c not in EXCLUDE]
        self.X = f[self.feat_names].to_numpy(dtype=np.float32)
        day_dates = np.array([dt.date(*map(int, d.split("-"))) for d in self.days])
        self.day_dates = day_dates
        bar_date = day_dates[self.day_id]
        self.evo_bars = bar_date < EVO_END
        self.val_bars = ~self.evo_bars
        self.evo_days = np.unique(self.day_id[self.evo_bars])
        self.val_days = np.unique(self.day_id[self.val_bars])
        # exit day per bar per hold (for the one-open-trade rule)
        uniq = np.unique(self.day_id)
        nd = uniq.max() + 1
        exp_id = np.full(nd, -1)
        date_to_id = {d: i for i, d in enumerate(self.days)}
        for d in uniq:
            e = bs._next_weekly_expiry(pd.Timestamp(str(self.days[d])) +
                                       pd.Timedelta(hours=9, minutes=15)).date()
            exp_id[d] = date_to_id.get(str(e), d)
        self.exit_day = {"eod": self.day_id,
                         "overnight": self.day_id + 1,
                         "expiry": exp_id[self.day_id]}
        # per-feature threshold grid from the EVOLUTION window only (no val leak)
        q = np.nanquantile(self.X[self.evo_bars], np.linspace(0.025, 0.975, 21), axis=0)
        self.thr_grid = q
        print("GP data: %d bars (%d evo days / %d val days), %d features, %d combos"
              % (len(self.X), len(self.evo_days), len(self.val_days),
                 len(self.feat_names), len(self.combos)))


# ---------------- genome ----------------
def rule_str(g, D):
    c = " AND ".join("%s %s %.4g" % (D.feat_names[f], "<=" if op == 0 else ">", t)
                     for f, op, t in g["conds"])
    return "[%s] IF %s" % (D.combos[g["combo"]], c)


def rand_cond(D, rng):
    f = int(rng.integers(0, len(D.feat_names)))
    return [f, int(rng.integers(0, 2)), float(D.thr_grid[rng.integers(0, 21), f])]


def rand_genome(D, rng, focus=None):
    n = int(rng.integers(1, MAX_CONDS + 1))
    combos = [i for i, c in enumerate(D.combos) if focus is None or c.startswith(focus)]
    return {"conds": [rand_cond(D, rng) for _ in range(n)],
            "combo": int(combos[rng.integers(0, len(combos))])}


def mutate(g, D, rng, focus=None):
    g = {"conds": [list(c) for c in g["conds"]], "combo": g["combo"]}
    r = rng.random()
    if r < 0.25 and len(g["conds"]) < MAX_CONDS:
        g["conds"].append(rand_cond(D, rng))
    elif r < 0.45 and len(g["conds"]) > 1:
        g["conds"].pop(int(rng.integers(0, len(g["conds"]))))
    elif r < 0.80:
        c = g["conds"][int(rng.integers(0, len(g["conds"])))]
        k = int(np.argmin(np.abs(D.thr_grid[:, c[0]] - c[2])))
        c[2] = float(D.thr_grid[int(np.clip(k + rng.integers(-3, 4), 0, 20)), c[0]])
    else:
        combos = [i for i, c in enumerate(D.combos) if focus is None or c.startswith(focus)]
        g["combo"] = int(combos[rng.integers(0, len(combos))])
    return g


def crossover(a, b, rng):
    conds = [list(c) for c in (a["conds"] + b["conds"])]
    rng.shuffle(conds)
    n = int(np.clip(rng.integers(1, MAX_CONDS + 1), 1, len(conds)))
    return {"conds": conds[:n], "combo": (a if rng.random() < 0.5 else b)["combo"]}


def genome_key(g):
    return json.dumps({"c": sorted(map(tuple, g["conds"])), "k": g["combo"]}, default=str)


# ---------------- evaluation ----------------
def day_pnl(g, D, bar_mask):
    """dict day_id -> ₹ for rule g inside bar_mask window (1 trade/day, and for
    multi-day holds only one open trade at a time)."""
    m = bar_mask.copy()
    for f, op, t in g["conds"]:
        col = D.X[:, f]
        m &= (col <= t) if op == 0 else (col > t)
    c = g["combo"]
    m &= np.isfinite(D.pnl[:, c])
    if not m.any():
        return {}
    idx = np.flatnonzero(m)
    dids = D.day_id[idx]
    first = np.unique(dids, return_index=True)[1]
    idx = idx[first]                             # first fire per day
    hold = D.combos[c].split("|")[1]
    exit_day = D.exit_day[hold]
    out, last_exit = {}, -1
    for i in idx:
        d = D.day_id[i]
        if d <= last_exit:
            continue                              # previous trade still open
        out[int(d)] = out.get(int(d), 0.0) + float(D.pnl[i, c])
        last_exit = int(exit_day[i])
    return out


def sharpe_of(dp, day_universe):
    if len(dp) < 10:
        return 0.0
    v = np.zeros(len(day_universe))
    pos = {d: i for i, d in enumerate(day_universe)}
    for d, x in dp.items():
        if d in pos:
            v[pos[d]] = x
    if v.std(ddof=1) == 0:
        return 0.0
    return float(v.mean() / v.std(ddof=1) * np.sqrt(252))


def fitness(g, D):
    dp = day_pnl(g, D, D.evo_bars)
    hold = D.combos[g["combo"]].split("|")[1]
    if len(dp) < MIN_TRADES[hold]:
        return -9.0, len(dp), 0.0
    sr = sharpe_of(dp, D.evo_days)
    return sr - 0.02 * (len(g["conds"]) - 1), len(dp), sr


N_COUNTER = {"n": 0}                     # true cumulative GP trial count (set in main)
NULL_CACHE = os.path.join(HERE, "ml_gp_null_%s.json" % RUN_ID)


def null_sharpes(D, n=2000):
    """full-window Sharpes of n RANDOM rules (with the same min-trade screen) —
    the honest zero-selection null spread for the deflated-Sharpe variance."""
    if os.path.exists(NULL_CACHE):
        return json.load(open(NULL_CACHE))
    rng = np.random.default_rng(424242)
    all_bars = np.ones(len(D.X), dtype=bool)
    all_days = np.unique(D.day_id)
    out = []
    while len(out) < n:
        g = rand_genome(D, rng)
        dp = day_pnl(g, D, all_bars)
        hold = D.combos[g["combo"]].split("|")[1]
        if len(dp) < MIN_TRADES[hold]:
            continue
        out.append(round(sharpe_of(dp, all_days), 4))
    json.dump(out, open(NULL_CACHE, "w"))
    print("null spread built: %d random rules, std=%.3f (annualized)"
          % (len(out), float(np.std(out, ddof=1))), flush=True)
    return out


# ---------------- one wave ----------------
def run_wave(D, wave, focus):
    rng0 = np.random.default_rng(1000 + wave)
    ck = {}
    if os.path.exists(CKPT):
        ck = json.load(open(CKPT))
        if ck.get("wave") != wave:
            ck = {}
    pop = ck.get("pop") or [rand_genome(D, rng0, focus) for _ in range(POP)]
    g0 = ck.get("gen", 0)
    seen = set(ck.get("seen", []))
    t0 = time.time()
    best = None
    for gen in range(g0, GENS):
        rng = np.random.default_rng(hash((1000 + wave, gen)) % (2 ** 31))
        scored, log_rows = [], []
        for g in pop:
            fit, ntr, sr = fitness(g, D)
            scored.append((fit, g))
            k = genome_key(g)
            if k not in seen:
                seen.add(k)
                log_rows.append(dict(candidate="w%d/g%d" % (wave, gen),
                                     target=D.combos[g["combo"]],
                                     params={"rule": rule_str(g, D)},
                                     train_sharpe=round(sr, 3),
                                     oos_sharpe="",              # val never seen here
                                     verdict="evolved trades=%d" % ntr))
        if log_rows:
            ml_gate.log_trials(log_rows, run_id=RUN_ID, approach="gp")
            N_COUNTER["n"] += len(log_rows)
        scored.sort(key=lambda t: -t[0])
        best = scored[0]
        nxt = [g for _, g in scored[:ELITE]]
        while len(nxt) < POP:
            def pick():
                cand = [scored[int(rng.integers(0, len(scored)))] for _ in range(TOURN)]
                return max(cand, key=lambda t: t[0])[1]
            child = crossover(pick(), pick(), rng) if rng.random() < P_CROSS else pick()
            if rng.random() < P_MUT:
                child = mutate(child, D, rng, focus)
            if rng.random() < 0.05:
                child = rand_genome(D, rng, focus)
            nxt.append(child)
        pop = nxt
        json.dump({"wave": wave, "gen": gen + 1, "pop": pop, "seen": list(seen)[-50000:]},
                  open(CKPT, "w"))
        if gen % 10 == 0 or gen == GENS - 1:
            print("  wave %d gen %2d best_fit=%.2f  %s" % (wave, gen, best[0],
                  rule_str(best[1], D)[:110]), flush=True)

    # ---- wave-end evaluation of top unique rules ----
    # N = every GP trial ever (v1's 3.43M voided-table trials INCLUDED — conservative).
    # Deflation variance = spread of RANDOM (unselected) rules' full-window Sharpes:
    # using the evolved population's spread would double-count selection (evolution
    # concentrates at the max, inflating V) and set an unpassable sr* — measured
    # 2026-07-14 on the v1 log: post-selection spread implied sr*≈13 annualized.
    n_trials = N_COUNTER["n"]
    trial_srs = null_sharpes(D)
    uniq, out = set(), []
    for fit, g in sorted([(fitness(g, D)[0], g) for g in pop], key=lambda t: -t[0]):
        k = genome_key(g)
        if k in uniq or fit <= -9:
            continue
        uniq.add(k)
        dp_evo = day_pnl(g, D, D.evo_bars)
        dp_val = day_pnl(g, D, D.val_bars)
        dp_all = day_pnl(g, D, np.ones(len(D.X), dtype=bool))
        sr_evo = sharpe_of(dp_evo, D.evo_days)
        sr_val = sharpe_of(dp_val, D.val_days)
        all_days = np.unique(D.day_id)
        ser = np.zeros(len(all_days))
        pos = {d: i for i, d in enumerate(all_days)}
        for d, x in dp_all.items():
            ser[pos[d]] = x
        dsr = ml_gate.deflated_sharpe(ser / 250000.0, n_trials=n_trials,
                                      trial_sharpes_annual=trial_srs)
        rec = {"rule": rule_str(g, D), "combo": D.combos[g["combo"]],
               "trades_evo": len(dp_evo), "trades_val": len(dp_val),
               "sharpe_evo": round(sr_evo, 3), "sharpe_val": round(sr_val, 3),
               "sharpe_full": round(sharpe_of(dp_all, all_days), 3),
               "pnl_evo": round(sum(dp_evo.values())), "pnl_val": round(sum(dp_val.values())),
               "dsr_prob": dsr["dsr_prob"], "dsr_pass": dsr["pass"], "n_trials": n_trials}
        if sr_val > 0.5 and sr_evo > 0.5:
            try:
                from ml_rules_a import corr_vs_shipped
                dpnl = {str(D.days[d]): v for d, v in dp_all.items()}
                cs = corr_vs_shipped(dpnl)
                rec["max_abs_corr"] = max(abs(v) for v in cs.values()) if cs else None
                rec["corr_top"] = dict(sorted(cs.items(), key=lambda kv: -abs(kv[1]))[:3])
            except Exception as e:
                rec["corr_err"] = str(e)
        out.append(rec)
        if len(out) >= 10:
            break
    waves = json.load(open(WAVES)) if os.path.exists(WAVES) else []
    waves.append({"wave": wave, "focus": focus, "finished": dt.datetime.now().isoformat(timespec="seconds"),
                  "secs": round(time.time() - t0), "pop": POP, "gens": GENS,
                  "n_trials_total": ml_gate.count_trials(run_id=RUN_ID), "top": out})
    json.dump(waves, open(WAVES, "w"), indent=1)
    if os.path.exists(CKPT):
        os.remove(CKPT)
    print("wave %d done in %ds; best val_sharpe=%.2f  (waves.json updated)"
          % (wave, time.time() - t0, max((r["sharpe_val"] for r in out), default=0)), flush=True)


def report():
    if not os.path.exists(WAVES):
        print("no finished waves yet"); return
    waves = json.load(open(WAVES))
    rows = [r | {"wave": w["wave"], "focus": w["focus"]} if hasattr(dict, "__or__")
            else dict(r, wave=w["wave"], focus=w["focus"])
            for w in waves for r in w["top"]]
    rows.sort(key=lambda r: -(min(r["sharpe_evo"], r["sharpe_val"])))
    print("%d waves, %d evaluated rules total (N for deflation)\n"
          % (len(waves), waves[-1]["n_trials_total"]))
    print("top by min(evo,val) sharpe:")
    for r in rows[:15]:
        print("  w%-2d evo=%5.2f val=%5.2f full=%5.2f dsr=%.3f%s tr=%d/%d  %s"
              % (r["wave"], r["sharpe_evo"], r["sharpe_val"], r["sharpe_full"],
                 r["dsr_prob"], "*PASS*" if r.get("dsr_pass") else "",
                 r["trades_evo"], r["trades_val"], r["rule"][:95]))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report(); return
    D = Data()
    N_COUNTER["n"] = ml_gate.count_trials(approach="gp")   # v1+v2 cumulative, read once
    print("cumulative GP trials so far: %d" % N_COUNTER["n"], flush=True)
    done = {w["wave"] for w in (json.load(open(WAVES)) if os.path.exists(WAVES) else [])}
    wave = 0
    if os.path.exists(CKPT):
        wave = json.load(open(CKPT)).get("wave", 0)
    while dt.datetime.now() < STOP_AT and not os.path.exists(STOPF):
        while wave in done:
            wave += 1
        focus = FOCUS_CYCLE[wave % len(FOCUS_CYCLE)]
        print("\n=== WAVE %d (focus=%s) until %s ===" % (wave, focus, STOP_AT), flush=True)
        try:
            run_wave(D, wave, focus)
            done.add(wave)
        except Exception as e:
            import traceback
            with open(ERRLOG, "a") as f:
                f.write("%s wave %d: %s\n%s\n" % (dt.datetime.now(), wave, e,
                                                  traceback.format_exc()))
            print("wave %d ERROR (logged): %s" % (wave, e), flush=True)
            if os.path.exists(CKPT):
                os.remove(CKPT)
        wave += 1
    print("\nstopped (%s). run `python ml_gp.py report` for the consolidated view."
          % ("STOP file" if os.path.exists(STOPF) else "time"), flush=True)
    report()


if __name__ == "__main__":
    main()
