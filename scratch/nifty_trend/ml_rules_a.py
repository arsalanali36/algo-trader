"""Task 2c-e (ml-strategy-mining) — turn the GBT/SHAP findings into EXPLICIT,
READABLE rules, backtest THE RULES (never the model's raw output), and push them
through the stricter gate. T1 (15-min direction) is dropped — AUC 0.518 = no edge.

Per target (T2 decay1h, T3 orblong):
  1. per purged fold: fit a depth-2 decision tree on the fold's TRAIN days using
     only the top-4 SHAP features -> take the best leaf (lift>=1.25, support>=5%)
     as that fold's candidate rule; evaluate it on the fold's TEST days.
  2. consensus rule = feature/threshold medians across folds IF the same structure
     won >=3/5 folds; otherwise the target is declared unstable (reported, dead).
  3. consensus rule backtested over the FULL mining window with a real trade sim:
       T3: long spot @ signal close, SL 1.5xATR14, target 2.5R, EOD square-off,
           1 trade/day (first signal) -> the house exit style.
       T2: SHORT the ATM straddle @ signal close, HELD strike to EOD (chain
           re-mapping, TRAP #109-safe), 1 trade/day, DOM slip 0.13%/leg/side.
  4. gate: rotation permutation p + deflated Sharpe (N = every trial logged in
     this run, cross-trial variance from fold rules) + correlation vs the 7
     shipped strategies (runs/*/results.js bs|full daily P&L).
Every fold rule, consensus rule and reject is appended to ml_mining_log.csv.

Run: python -X utf8 ml_rules_a.py            (labels+features must exist)
Output: ml_rules_a_report.json + console table (tasklist 2e format).
"""
import os
import re
import json
import numpy as np
import pandas as pd

import ml_gate
import optlake_load as ol

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_ID = "mine_a_v1"
LOT = 65
SLIP_LEG = 0.0013          # DOM-calibrated ~0.13% per leg per side (ADR-005)
REPORT = os.path.join(HERE, "ml_rules_a_report.json")

TOP_FEATS = {
    "t2_decay1h": ["dte_days", "iv_rank_60d", "iv_chg_today", "bars_into_day"],
    "t3_orblong": ["min_since_open", "gap_open_pct", "iv_chg_today", "iv_rank_60d"],
}
MIN_LIFT, MIN_SUPPORT = 1.25, 0.05


# ---------- rule extraction ----------
def best_leaf_rule(X, y, feats):
    """depth-2 tree -> (conds, lift, support); conds = [(feat, op, thr), ...]"""
    from sklearn.tree import DecisionTreeClassifier
    t = DecisionTreeClassifier(max_depth=2, min_samples_leaf=max(int(len(X) * MIN_SUPPORT), 50),
                               random_state=7)
    t.fit(X[feats], y)
    tr = t.tree_
    base = y.mean()
    best = None
    def walk(node, conds):
        nonlocal best
        if tr.children_left[node] == -1:                       # leaf
            n = tr.n_node_samples[node]
            pos = tr.value[node][0][1] / max(tr.value[node][0].sum(), 1)
            lift = pos / max(base, 1e-9)
            if lift >= MIN_LIFT and n >= len(X) * MIN_SUPPORT:
                if best is None or pos > best[1]:
                    best = (list(conds), pos, n / len(X), lift)
            return
        f, thr = feats[tr.feature[node]], tr.threshold[node]
        walk(tr.children_left[node], conds + [(f, "<=", round(float(thr), 4))])
        walk(tr.children_right[node], conds + [(f, ">", round(float(thr), 4))])
    walk(0, [])
    return best


def rule_mask(df, conds):
    m = np.ones(len(df), dtype=bool)
    for f, op, thr in conds:
        m &= (df[f] <= thr).values if op == "<=" else (df[f] > thr).values
    return m


def rule_str(conds):
    return " AND ".join("%s %s %s" % (f, op, thr) for f, op, thr in conds)


def consensus(fold_rules):
    """same (feature, op) structure in >=3/5 folds -> median thresholds."""
    keys = [tuple((f, op) for f, op, _ in r) for r in fold_rules if r]
    if not keys:
        return None
    from collections import Counter
    key, cnt = Counter(keys).most_common(1)[0]
    if cnt < 3:
        return None
    # same feature repeated with the same op collapses to its BINDING threshold
    # (min for <=, max for >) within each fold rule, THEN median across folds —
    # otherwise "dte<=1.07 AND dte<=0.23" would median into a meaningless 0.65
    thrs = {}
    for r in fold_rules:
        if r and tuple((f, op) for f, op, _ in r) == key:
            binding = {}
            for (f, op, thr) in r:
                cur = binding.get((f, op))
                binding[(f, op)] = thr if cur is None else (min(cur, thr) if op == "<=" else max(cur, thr))
            for ko, thr in binding.items():
                thrs.setdefault(ko, []).append(thr)
    return [(f, op, round(float(np.median(v)), 4)) for (f, op), v in thrs.items()]


# ---------- trade simulators (1 trade/day, first signal) ----------
def sim_t3(lab, spot5, mask):
    """long spot, SL 1.5xATR, target 2.5R, EOD square-off. returns day->ret_frac."""
    H, L, C = spot5.High.values, spot5.Low.values, spot5.Close.values
    day = lab.day.values
    atr_pts = lab.atr14_pct.values / 100.0 * lab.spot.values
    out, traded = {}, set()
    idxs = np.where(mask)[0]
    n = len(lab)
    for i in idxs:
        d = day[i]
        if d in traded or not np.isfinite(atr_pts[i]) or atr_pts[i] <= 0:
            continue
        traded.add(d)
        ent = lab.spot.values[i]
        sl, tg = ent - 1.5 * atr_pts[i], ent + 1.5 * 2.5 * atr_pts[i]
        j, ex = i + 1, None
        while j < n and day[j] == d:
            if np.isfinite(L[j]) and L[j] <= sl:
                ex = sl; break
            if np.isfinite(H[j]) and H[j] >= tg:
                ex = tg; break
            j += 1
        if ex is None:
            ex = C[j - 1] if j - 1 > i and np.isfinite(C[j - 1]) else ent
        out[d] = out.get(d, 0.0) + (ex - ent) / ent
    return out


def sim_t2(lab, mask, held):
    """short ATM straddle @ signal close -> EOD, held strike, slip per leg/side.
    returns day -> pnl in PREMIUM POINTS x LOT (₹)."""
    idxdt, ce, pe, katm, offs = held
    pos = pd.Series(np.arange(len(idxdt)), index=idxdt)
    bar_pos = pos.reindex(pd.to_datetime(lab.Datetime)).values
    off0 = offs.index(0)
    day = lab.day.values
    # last bar index of each day in the chain frame
    chain_day = pd.Series(idxdt.date, index=np.arange(len(idxdt)))
    last_of_day = chain_day.groupby(chain_day).apply(lambda s: s.index[-1]).to_dict()
    out, traded = {}, set()
    for i in np.where(mask)[0]:
        d = day[i]
        p = bar_pos[i]
        if d in traded or np.isnan(p):
            continue
        p = int(p)
        u = last_of_day.get(d)
        if u is None or u <= p or np.isnan(katm[p]) or np.isnan(katm[u]):
            continue
        joff = off0 + int(round((katm[p] - katm[u]) / 50))
        if not (0 <= joff < len(offs)):
            continue
        ent = ce[p, off0] + pe[p, off0]
        ex = ce[u, joff] + pe[u, joff]
        if not (np.isfinite(ent) and np.isfinite(ex)) or ent <= 0:
            continue
        traded.add(d)
        slip = (ent + ex) * SLIP_LEG * 2          # 2 legs, each side
        out[d] = out.get(d, 0.0) + (ent - ex - slip) * LOT - 4 * 25.0   # ~4 orders charges
    return out


# ---------- gate pieces ----------
def daily_series(day_ret, all_days):
    return np.array([day_ret.get(d, 0.0) for d in all_days])


def sharpe_ann(x):
    x = np.asarray(x, dtype=float)
    if len(x) < 20 or x.std(ddof=1) == 0:
        return 0.0
    return float(x.mean() / x.std(ddof=1) * np.sqrt(252))


def bootstrap_p(day_ret, pool_ret, n_perm=2000, seed=7):
    """p-value: probability that picking the SAME NUMBER of random days from the
    pool gives a mean day-P&L >= the rule's. Preserves fat tails (draws real days)."""
    rng = np.random.default_rng(seed)
    k = len(day_ret)
    real = float(np.mean(list(day_ret.values())))
    pool = np.asarray(pool_ret, dtype=float)
    null = np.array([pool[rng.integers(0, len(pool), k)].mean() for _ in range(n_perm)])
    return real, float((null >= real).mean())


def corr_vs_shipped(day_pnl):
    """pearson corr of daily P&L vs each shipped strategy (runs/*/results.js)."""
    runs = os.path.join(HERE, "runs")
    out = {}
    idx = json.load(open(os.path.join(runs, "index.json")))
    for meta in idx:
        slug = meta["slug"]
        rp = os.path.join(runs, slug, "results.js")
        if not os.path.exists(rp):
            continue
        txt = open(rp, encoding="utf-8").read()
        R = json.loads(txt[txt.index("{"):txt.rstrip().rstrip(";").rindex("}") + 1])
        theirs = {}
        c = R.get("combos", {}).get("bs|full") or {}
        for t in c.get("all_trades", []):
            d = str(t.get("exit_dt", ""))[:10]
            if d:
                theirs[d] = theirs.get(d, 0.0) + float(t.get("pnl", 0) or 0)
        days = sorted(set(theirs) | set(str(d) for d in day_pnl))
        if len(days) < 60:
            continue
        a = np.array([day_pnl.get(d, day_pnl.get(pd.Timestamp(d).date(), 0.0)) for d in days])
        b = np.array([theirs.get(d, 0.0) for d in days])
        if a.std() > 0 and b.std() > 0:
            out[slug] = round(float(np.corrcoef(a, b)[0, 1]), 3)
    return out


# ---------- main ----------
def main():
    lab = pd.read_csv(os.path.join(HERE, "ml_labels_v1.csv.gz"), parse_dates=["Datetime"])
    lab["day"] = lab.Datetime.dt.date
    ml_gate.assert_no_lockbox(lab, what="labels")
    import ml_features as mf
    spot5 = mf._spot_5m().set_index("Datetime").reindex(pd.to_datetime(lab.Datetime))

    import ml_mine_a as mm
    held = mm._held_matrices()

    days = sorted(lab.day.unique())
    folds = list(ml_gate.purged_walk_forward(days, n_folds=5))
    report = {}
    for tgt in ("t2_decay1h", "t3_orblong"):
        feats = TOP_FEATS[tgt]
        d_all = lab.dropna(subset=[tgt])
        fold_rules, fold_oos_sr, log_rows = [], [], []
        for k, (tr_days, te_days) in enumerate(folds):
            dtr = d_all[d_all.day.isin(set(tr_days))]
            rule = best_leaf_rule(dtr, dtr[tgt].astype(int), feats)
            if rule is None:
                fold_rules.append(None)
                log_rows.append(dict(candidate="%s/fold%d" % (tgt, k), target=tgt,
                                     verdict="no_leaf_passed_lift"))
                continue
            conds, prec, supp, lift = rule
            dte_f = lab[lab.day.isin(set(te_days))]
            mask_te = rule_mask(dte_f, conds)
            if tgt == "t3_orblong":
                dr = sim_t3(dte_f.reset_index(drop=True),
                            spot5[lab.day.isin(set(te_days)).values].reset_index(),
                            mask_te)
            else:
                dr = sim_t2(dte_f.reset_index(drop=True), mask_te, held)
            sr = sharpe_ann(daily_series(dr, sorted(set(te_days)))) if dr else 0.0
            fold_rules.append(conds); fold_oos_sr.append(sr)
            log_rows.append(dict(candidate="%s/fold%d" % (tgt, k), target=tgt,
                                 params={"rule": rule_str(conds), "precision": round(prec, 3),
                                         "support": round(supp, 3)},
                                 oos_sharpe=round(sr, 3), verdict="fold_rule"))
            print("  %s fold%d: %s  prec=%.3f supp=%.2f  OOS_sharpe=%.2f"
                  % (tgt, k, rule_str(conds), prec, supp, sr))
        ml_gate.log_trials(log_rows, run_id=RUN_ID, approach="gbt_rules")

        cons = consensus(fold_rules)
        if cons is None:
            print("%s: NO STABLE CONSENSUS across folds -> dead (honest reject)" % tgt)
            ml_gate.log_trials([dict(candidate="%s/consensus" % tgt, target=tgt,
                                     verdict="unstable_reject")], run_id=RUN_ID,
                               approach="gbt_rules")
            report[tgt] = {"consensus": None, "fold_oos_sharpes": fold_oos_sr}
            continue

        mask = rule_mask(lab, cons)
        if tgt == "t3_orblong":
            dr = sim_t3(lab, spot5.reset_index(), mask)
            pool = sim_t3(lab, spot5.reset_index(), np.ones(len(lab), dtype=bool))
        else:
            dr = sim_t2(lab, mask, held)
            pool = sim_t2(lab, np.ones(len(lab), dtype=bool), held)
        real_mean, p_boot = bootstrap_p(dr, list(pool.values()))
        ser = daily_series(dr, days)
        sr_full = sharpe_ann(ser)
        n_trials = ml_gate.count_trials(run_id=RUN_ID)
        dsr = ml_gate.deflated_sharpe(ser, n_trials=n_trials,
                                      trial_sharpes_annual=[s for s in fold_oos_sr if s])
        scale = (LOT * lab.spot.mean() if tgt == "t3_orblong" else 1.0)
        day_pnl = {str(d): v * scale for d, v in dr.items()}
        corr = corr_vs_shipped(day_pnl)
        report[tgt] = {
            "consensus_rule": rule_str(cons), "features": feats,
            "trades": len(dr), "mean_day_pnl": round(real_mean, 6),
            "sharpe_full": round(sr_full, 3), "p_bootstrap": round(p_boot, 4),
            "dsr": dsr, "fold_oos_sharpes": [round(s, 3) for s in fold_oos_sr],
            "corr_vs_shipped": corr,
            "max_abs_corr": max(abs(v) for v in corr.values()) if corr else None}
        ml_gate.log_trials([dict(candidate="%s/consensus" % tgt, target=tgt,
                                 params={"rule": rule_str(cons)},
                                 oos_sharpe=round(sr_full, 3),
                                 verdict="consensus p=%.3f dsr=%.3f" % (p_boot, dsr["dsr_prob"]))],
                           run_id=RUN_ID, approach="gbt_rules")
        print("%s CONSENSUS: %s\n  trades=%d sharpe=%.2f p_boot=%.4f DSR=%.3f (N=%d) "
              "max|corr|=%s" % (tgt, rule_str(cons), len(dr), sr_full, p_boot,
                                dsr["dsr_prob"], n_trials, report[tgt]["max_abs_corr"]))
    json.dump(report, open(REPORT, "w"), indent=1, default=str)
    print("\nwrote", REPORT)


if __name__ == "__main__":
    main()
