"""Task 2 (ml-strategy-mining) — Approach A: gradient-boosted trees + SHAP →
explicit rule extraction. Runs on the MINING machine (local), lockbox-free data only.

THREE targets, defined explicitly and modeled SEPARATELY (tasklist 2a — no blending):

  T1 dir15    : sign of forward 15-min (3-bar) spot return, deadzone ±0.03%
                (rows inside the deadzone are dropped from training, not labeled 0).
  T2 decay1h  : does the CURRENT ATM straddle, HELD at its entry strike, lose MORE
                premium over the next 1h (12 bars) than the flat-theta baseline
                (trailing 5-day mean full-day decay, pro-rata)? Held-strike premium is
                reconstructed from the chain by re-mapping the entry strike to its
                current offset each bar — NEVER the rolling-ATM close (TRAP #109).
  T3 orblong  : from this bar, does a LONG spot trade with the house exit style
                (SL = 1.5×ATR14, target = 2.5R, EOD square-off 15:25) hit target
                before stop? (Our existing SL/target definitions, tasklist 2a-iii.)

Usage:
  python -X utf8 ml_mine_a.py labels          # build + save ml_labels_v1.csv.gz
  python -X utf8 ml_mine_a.py train           # purged-CV LightGBM + SHAP report
                                              #   -> ml_mine_a_report.json
Both steps assert lockbox-free frames and log to ml_mining_log.csv where trials occur.
"""
import os
import sys
import json
import numpy as np
import pandas as pd

import ml_gate
import optlake_load as ol

HERE = os.path.dirname(os.path.abspath(__file__))
FEATS = os.path.join(HERE, "ml_features_v1.csv.gz")
LABELS = os.path.join(HERE, "ml_labels_v1.csv.gz")
REPORT = os.path.join(HERE, "ml_mine_a_report.json")

STRIKE_STEP = 50
DEADZONE = 0.0003            # T1: ±0.03% forward return ignored
H_T1 = 3                     # 15 min
H_T2 = 12                    # 1 hour
SL_ATR, RR = 1.5, 2.5        # T3: house exit style (mid_orb family defaults)

FEATURES = ["atm_iv", "iv_rank_60d", "iv_chg_today", "term_ratio", "atm_skew",
            "straddle_norm", "straddle_decay_today", "bars_into_day", "decay_vs_linear",
            "pcr_oi", "max_oi_ce_off", "max_oi_pe_off", "oi_imbalance", "d_atm_oi_30m",
            "atr14_pct", "ret_5m", "ret_30m", "ret_2h", "day_range_pos", "gap_open_pct",
            "rv_20b_ann", "rv_iv_ratio", "dow", "min_since_open", "dte_days",
            "is_expiry_day", "opt_volume_proxy"]


# ---------- held-strike straddle reconstruction (TRAP #109-safe) ----------
def _held_matrices():
    """chain-wide close matrices indexed by Datetime: for each bar, CE/PE close at
    every offset, plus that bar's ATM strike. Lets us price strike K at any later
    bar u via offset = (K - atm_strike(u)) / 50."""
    ch = ol.chain_frame("WEEK", "5m", offs=range(-10, 11))
    ml_gate.assert_no_lockbox(ch, what="chain(T2)")
    ch = ch.set_index("Datetime")
    offs = list(range(-10, 11))
    ce = np.full((len(ch), len(offs)), np.nan)
    pe = np.full((len(ch), len(offs)), np.nan)
    for j, off in enumerate(offs):
        tag = ol._off_tag(off)
        if f"CE{tag}_c" in ch.columns:
            ce[:, j] = ch[f"CE{tag}_c"].values
        if f"PE{tag}_c" in ch.columns:
            pe[:, j] = ch[f"PE{tag}_c"].values
    katm = ch["CEATM_k"].values if "CEATM_k" in ch.columns else np.full(len(ch), np.nan)
    return ch.index, ce, pe, katm, offs


def build_labels(f):
    f = f.sort_values("Datetime").reset_index(drop=True)
    day = f.day.values
    spot = f.spot.values

    # T1 — forward 3-bar spot return sign with deadzone
    fwd = np.full(len(f), np.nan)
    same = day[H_T1:] == day[:-H_T1]
    fwd[:-H_T1][same] = spot[H_T1:][same] / spot[:-H_T1][same] - 1.0
    t1 = np.where(fwd > DEADZONE, 1.0, np.where(fwd < -DEADZONE, 0.0, np.nan))

    # T2 — held-strike straddle decay vs flat-theta baseline
    idx, ce, pe, katm, offs = _held_matrices()
    pos = pd.Series(np.arange(len(idx)), index=idx)
    bar_pos = pos.reindex(pd.to_datetime(f.Datetime)).values
    t2 = np.full(len(f), np.nan)
    dec_frac = np.full(len(f), np.nan)
    off0 = offs.index(0)
    # trailing 5-day mean full-day decay (leak-free, from features)
    daily_dec = f.groupby("day").straddle_decay_today.last()
    trail = daily_dec.rolling(5, min_periods=3).mean().shift(1)
    base_slope = f.day.map(trail).values          # full-day expected decay (negative)
    for i in range(len(f)):
        p = bar_pos[i]
        if np.isnan(p) or np.isnan(base_slope[i]):
            continue
        p = int(p)
        u = p + H_T2
        if u >= len(idx) or idx[u].date() != idx[p].date():
            continue
        k_entry = katm[p]
        if np.isnan(k_entry) or np.isnan(katm[u]):
            continue
        joff = off0 + int(round((k_entry - katm[u]) / STRIKE_STEP))
        if not (0 <= joff < len(offs)):
            continue
        entry_st = ce[p, off0] + pe[p, off0]
        held_st = ce[u, joff] + pe[u, joff]
        if not np.isfinite(entry_st) or not np.isfinite(held_st) or entry_st <= 0:
            continue
        dec = held_st / entry_st - 1.0                       # negative = decayed
        dec_frac[i] = dec
        t2[i] = 1.0 if dec < base_slope[i] * (H_T2 / 75.0) else 0.0

    # T3 — long setup with house SL/target, path-checked bar by bar on 5m OHLC
    # (features dropped OHLC, so reload the spot frame for the path check)
    t3 = np.full(len(f), np.nan)
    import ml_features as mf
    s5 = mf._spot_5m()
    s5 = s5.set_index("Datetime")
    s5 = s5.reindex(pd.to_datetime(f.Datetime))
    H, L = s5.High.values, s5.Low.values
    atr_pts = f.atr14_pct.values / 100.0 * spot
    day_arr = f.day.values
    n = len(f)
    for i in range(n):
        if not np.isfinite(atr_pts[i]) or atr_pts[i] <= 0:
            continue
        ent = spot[i]
        sl = ent - SL_ATR * atr_pts[i]
        tg = ent + SL_ATR * RR * atr_pts[i]
        j = i + 1
        lab = None
        while j < n and day_arr[j] == day_arr[i]:
            if np.isfinite(L[j]) and L[j] <= sl:
                lab = 0.0; break
            if np.isfinite(H[j]) and H[j] >= tg:
                lab = 1.0; break
            j += 1
        if lab is None and j > i + 1:
            lab = 0.0                        # EOD square-off without target = not a win
        if lab is not None:
            t3[i] = lab

    out = f[["Datetime", "day"] + FEATURES].copy()
    out["t1_dir15"] = t1
    out["t2_decay1h"] = t2
    out["t2_dec_frac"] = dec_frac            # raw decay, for economics later
    out["t1_fwd_ret"] = fwd
    out["t3_orblong"] = t3
    ml_gate.assert_no_lockbox(out, what="ml_labels_v1")
    return out


# ---------- purged-CV training + SHAP ----------
def train_report(lab):
    import lightgbm as lgb
    import shap

    days = sorted(lab.day.unique())
    folds = list(ml_gate.purged_walk_forward(days, n_folds=5))
    rep = {"folds": len(folds), "targets": {}}
    for tgt in ("t1_dir15", "t2_decay1h", "t3_orblong"):
        d = lab.dropna(subset=[tgt])
        X, y = d[FEATURES], d[tgt].astype(int)
        aucs, edges, shap_sum = [], [], np.zeros(len(FEATURES))
        dirn = np.zeros(len(FEATURES))
        for tr_days, te_days in folds:
            m_tr = d.day.isin(set(tr_days)); m_te = d.day.isin(set(te_days))
            if m_tr.sum() < 5000 or m_te.sum() < 1000:
                continue
            clf = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05,
                                     num_leaves=31, min_child_samples=200,
                                     feature_fraction=0.8, verbose=-1, random_state=7)
            clf.fit(X[m_tr], y[m_tr])
            pr = clf.predict_proba(X[m_te])[:, 1]
            yy = y[m_te].values
            # AUC without sklearn dependency drama
            order = np.argsort(pr); ranks = np.empty(len(pr)); ranks[order] = np.arange(1, len(pr) + 1)
            n1, n0 = yy.sum(), (1 - yy).sum()
            auc = (ranks[yy == 1].sum() - n1 * (n1 + 1) / 2) / max(n1 * n0, 1)
            aucs.append(float(auc))
            # economic edge: top-decile predicted vs base rate
            k = max(len(pr) // 10, 50)
            top = np.argsort(pr)[-k:]
            edges.append(float(yy[top].mean() - yy.mean()))
            ex = shap.TreeExplainer(clf)
            sv = ex.shap_values(X[m_te].iloc[:4000])
            sv = sv[1] if isinstance(sv, list) else sv
            shap_sum += np.abs(sv).mean(0)
            for j, c in enumerate(FEATURES):
                v = np.corrcoef(X[m_te][c].iloc[:4000].values, sv[:, j])[0, 1]
                if np.isfinite(v):
                    dirn[j] += v
        imp = sorted(zip(FEATURES, shap_sum / max(len(aucs), 1), dirn),
                     key=lambda t: -t[1])[:8]
        rep["targets"][tgt] = {
            "n_rows": int(len(d)), "base_rate": round(float(y.mean()), 4),
            "auc_by_fold": [round(a, 4) for a in aucs],
            "auc_mean": round(float(np.mean(aucs)), 4) if aucs else None,
            "top_decile_edge_by_fold": [round(e, 4) for e in edges],
            "top_features": [{"f": f_, "shap": round(float(s), 5),
                              "dir": ("high->1" if dsum > 0 else "high->0")}
                             for f_, s, dsum in imp]}
        print(f"\n{tgt}: rows={len(d)} base={y.mean():.3f} "
              f"AUC/fold={['%.3f' % a for a in aucs]} mean={np.mean(aucs):.3f}")
        for f_, s, dsum in imp:
            print(f"   {f_:22s} shap={s:.4f}  {'high->1' if dsum > 0 else 'high->0'}")
    json.dump(rep, open(REPORT, "w"), indent=1)
    print("\nwrote", REPORT)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "train"
    if cmd == "labels":
        f = pd.read_csv(FEATS, parse_dates=["Datetime"])
        f["day"] = f.Datetime.dt.date
        lab = build_labels(f)
        lab.to_csv(LABELS, index=False, compression="gzip")
        for t in ("t1_dir15", "t2_decay1h", "t3_orblong"):
            v = lab[t].dropna()
            print(f"{t}: {len(v)} labeled rows, positive rate {v.mean():.3f}")
        print("wrote", LABELS)
    elif cmd == "train":
        lab = pd.read_csv(LABELS, parse_dates=["Datetime"])
        lab["day"] = lab.Datetime.dt.date
        train_report(lab)
