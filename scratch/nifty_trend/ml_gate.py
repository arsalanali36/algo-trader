"""Task 0 (ml-strategy-mining) — statistical guardrails for ML-scale candidate search.

The existing run_hunt.py gate (rotation-permutation p<0.05 + Monte Carlo) was built
for a handful of hand-designed candidates. At ML scale (hundreds/thousands of trials)
plain p<0.05 passes false positives by chance alone. This module adds:

  (0b) deflated_sharpe()      — Bailey / López de Prado Deflated Sharpe Ratio: the
                                observed Sharpe must beat the EXPECTED MAX Sharpe of
                                N no-skill trials, not just zero. Pure math.erf /
                                Acklam inverse-normal — no scipy (VPS has none).
  (0c) purged_walk_forward()  — walk-forward day folds with purge+embargo so
                                overlapping option-chain windows can't leak the
                                same underlying move into both train and test.
  (0d) assert_no_lockbox()    — hard cutoff: mining code may not touch data on/after
                                LOCKBOX_START. Final one-shot eval goes through
                                lockbox_frame() which AUDIT-LOGS every access.
  (0e) log_trials()/count_trials() — persistent per-candidate mining log so the
                                deflation N is the TRUE number of trials, not the
                                cherry-picked winner count.

Sharpe units convention: everywhere below sr is PER-DAY Sharpe; annualized values
(house style: daily mean/std ×√252, see significance._sharpe_from_bars) are
converted with sr_daily = sr_annual / sqrt(252).
"""
import os
import csv
import json
import math
import time
import datetime as dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
MINING_LOG = os.path.join(HERE, "ml_mining_log.csv")
ANN = math.sqrt(252.0)
EULER = 0.5772156649015329

# ---- 0d: hard OOS lockbox ---------------------------------------------------
# Lake spans 2021-07-01 .. 2026-07-10 (1246 days, ml_data_inventory.json).
# Last ~3 months (≈60 trading days) are locked: NOTHING in feature building,
# model training, threshold picking or rule extraction may read rows >= this date.
LOCKBOX_START = dt.date(2026, 4, 15)
LOCKBOX_LOG = os.path.join(HERE, "ml_lockbox_access.log")


# ---- normal distribution without scipy --------------------------------------
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p):
    """Acklam's rational approximation of the inverse normal CDF (|err|<1.15e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p in (0,1) required, got %r" % p)
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ---- 0b: probabilistic / deflated Sharpe ------------------------------------
def sharpe_moments(daily_returns):
    """per-day Sharpe + T + skew + RAW kurtosis (normal=3) of a daily-return series."""
    r = np.asarray([x for x in daily_returns if x == x], dtype=float)
    T = len(r)
    if T < 20 or r.std(ddof=1) == 0:
        return 0.0, T, 0.0, 3.0
    mu, sd = r.mean(), r.std(ddof=1)
    z = (r - mu) / sd
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean())          # raw, not excess
    return float(mu / sd), T, skew, kurt


def probabilistic_sharpe(sr, sr_ref, T, skew, kurt):
    """PSR = P[true SR > sr_ref | observed sr, T, skew, kurt]  (Bailey/LdP 2012)."""
    if T < 2:
        return 0.0
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if denom <= 0:                          # extreme higher moments — be conservative
        return 0.0
    z = (sr - sr_ref) * math.sqrt(T - 1) / math.sqrt(denom)
    return norm_cdf(z)


def expected_max_sharpe(n_trials, var_trial_sr):
    """E[max SR] of n_trials zero-skill strategies whose trial SRs vary with
    variance var_trial_sr (per-day units). n_trials<=1 -> 0 (no selection bias)."""
    n = max(int(n_trials), 1)
    if n == 1 or var_trial_sr <= 0:
        return 0.0
    return math.sqrt(var_trial_sr) * (
        (1 - EULER) * norm_ppf(1 - 1.0 / n) + EULER * norm_ppf(1 - 1.0 / (n * math.e)))


def deflated_sharpe(daily_returns, n_trials, trial_sharpes_annual=None, min_prob=0.95):
    """Full DSR gate. daily_returns: candidate's daily P&L returns (deployable pass).
    n_trials: TRUE count of candidates tried in this mining run (see log_trials).
    trial_sharpes_annual: the trials' (annualized) Sharpes, to estimate the
    cross-trial variance; falls back to the SR-estimator variance if absent.

    Returns dict — auditable, every input echoed. pass = dsr_prob >= min_prob.
    """
    sr, T, skew, kurt = sharpe_moments(daily_returns)
    if trial_sharpes_annual is not None and len(trial_sharpes_annual) >= 2:
        v = float(np.var(np.asarray(trial_sharpes_annual, dtype=float) / ANN, ddof=1))
        v_src = "cross_trial(%d)" % len(trial_sharpes_annual)
    else:
        v = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / max(T - 1, 1)
        v_src = "estimator_fallback"
    sr_star = expected_max_sharpe(n_trials, v)
    prob = probabilistic_sharpe(sr, sr_star, T, skew, kurt)
    return {
        "dsr_prob": round(prob, 4), "pass": bool(prob >= min_prob),
        "min_prob": min_prob,
        "sr_daily": round(sr, 4), "sr_annual": round(sr * ANN, 3),
        "sr_star_daily": round(sr_star, 4), "sr_star_annual": round(sr_star * ANN, 3),
        "n_trials": int(n_trials), "var_trial_sr": v, "var_src": v_src,
        "T_days": T, "skew": round(skew, 3), "kurt": round(kurt, 3),
    }


def effective_trials(returns_matrix, n_total=None):
    """Li-Ji effective number of independent trials from the correlation matrix of
    trial daily-return streams (rows = trials). Hunt trials are heavily correlated
    (measured 2026-07-13: mean pairwise rho 0.72 across ORB-family param variants,
    Li-Ji ratio 0.31 on a 36-trial sample) — raw N overstates the search breadth.
    If n_total > sampled rows, N_eff is scaled up LINEARLY (over-counts, i.e. the
    bar only gets stricter — correlation actually saturates sublinearly).
    Use as: deflated_sharpe(..., n_trials=effective_trials(X, n_total=full_count)).
    """
    X = np.asarray(returns_matrix, dtype=float)
    m = X.shape[0]
    if m < 2:
        return max(int(n_total or m), 1)
    C = np.corrcoef(X)
    lam = np.clip(np.linalg.eigvalsh(C), 0.0, None)
    neff = float(sum((1.0 if l >= 1 else 0.0) + (l - math.floor(l)) for l in lam))
    if n_total and n_total > m:
        neff *= n_total / float(m)
    return max(int(round(neff)), 1)


# ---- 0c: purged + embargoed walk-forward CV ---------------------------------
# Why purge=1 day: mining labels are intraday-forward (15min .. end-of-day), so a
# label started on day d never reaches past d+1; purging 1 day on each side of the
# test block removes any bar whose label window could straddle the boundary.
# Why embargo=5 days: NIFTY IV/OI regimes are serially correlated for ~a week
# (weekly expiry cycle) — a model peeking at day d+1..d+5 after the test block
# still "remembers" the test week's vol regime. 5 trading days ≈ one expiry cycle.
PURGE_DAYS = 1
EMBARGO_DAYS = 5


def purged_walk_forward(days, n_folds=5, purge_days=PURGE_DAYS, embargo_days=EMBARGO_DAYS):
    """days: sorted array-like of unique trading days (datetime.date).
    Yields (train_days, test_days) per fold — walk-forward: each fold's TEST block
    is a contiguous slice; TRAIN = everything else minus purge±(around test) minus
    embargo AFTER the test block. Later folds' test blocks are later in time.
    """
    days = sorted(pd.unique(pd.Series(list(days))))
    n = len(days)
    if n < n_folds * 20:
        raise ValueError("only %d days for %d folds — too thin" % (n, n_folds))
    fold_edges = np.linspace(0, n, n_folds + 1).astype(int)
    for k in range(n_folds):
        lo, hi = fold_edges[k], fold_edges[k + 1]
        test = days[lo:hi]
        drop_lo = max(0, lo - purge_days)
        drop_hi = min(n, hi + purge_days + embargo_days)
        train = days[:drop_lo] + days[drop_hi:]
        yield train, test


# ---- 0d: lockbox enforcement --------------------------------------------------
def assert_no_lockbox(df, dt_col="Datetime", what="frame"):
    """Raise if a mining-side frame contains any lockbox rows. Call this in every
    feature/training loader. Cheap (max of one column)."""
    if df is None or len(df) == 0:
        return df
    mx = pd.to_datetime(df[dt_col]).max()
    if mx.date() >= LOCKBOX_START:
        raise RuntimeError(
            "LOCKBOX VIOLATION: %s contains rows up to %s (lockbox starts %s). "
            "Mining code must filter with trim_lockbox() first." % (what, mx, LOCKBOX_START))
    return df


def trim_lockbox(df, dt_col="Datetime"):
    """The ONLY sanctioned way mining code shrinks a frame below the cutoff."""
    if df is None or len(df) == 0:
        return df
    m = pd.to_datetime(df[dt_col]).dt.date < LOCKBOX_START
    return df.loc[m].reset_index(drop=True)


def lockbox_frame(df, dt_col="Datetime", candidate="?", reason=""):
    """One-shot final-eval accessor: returns ONLY lockbox rows and appends an
    audit line (who/when/why). If a candidate shows up here more than once,
    someone is re-tuning against the lockbox — that's the failure mode 0d exists
    to prevent, and the log makes it visible."""
    with open(LOCKBOX_LOG, "a", encoding="utf-8") as f:
        f.write("%s\tcandidate=%s\treason=%s\n" % (dt.datetime.now().isoformat(), candidate, reason))
    m = pd.to_datetime(df[dt_col]).dt.date >= LOCKBOX_START
    return df.loc[m].reset_index(drop=True)


# ---- 0e: multiple-testing log -------------------------------------------------
LOG_COLS = ["ts", "run_id", "approach", "candidate", "target", "tf",
            "params", "train_sharpe", "oos_sharpe", "verdict", "note"]


def log_trials(rows, run_id, approach):
    """Append one row PER CANDIDATE TRIED (winners and losers alike).
    rows: list of dicts with any of LOG_COLS (missing -> ""). Cross-process safe
    via hunt_guard.flock (TRAP #110-hardened)."""
    import hunt_guard
    new = os.path.exists(MINING_LOG) is False
    with hunt_guard.flock("ml_mining_log"):
        with open(MINING_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=LOG_COLS, extrasaction="ignore")
            if new:
                w.writeheader()
            ts = dt.datetime.now().isoformat(timespec="seconds")
            for r in rows:
                row = {c: r.get(c, "") for c in LOG_COLS}
                row["ts"], row["run_id"], row["approach"] = ts, run_id, approach
                if isinstance(row["params"], (dict, list)):
                    row["params"] = json.dumps(row["params"], default=str)
                w.writerow(row)


def count_trials(run_id=None, approach=None):
    """TRUE trial count for the deflation calc. run_id=None -> all-time count."""
    if not os.path.exists(MINING_LOG):
        return 0
    df = pd.read_csv(MINING_LOG)
    if run_id is not None:
        df = df[df.run_id == run_id]
    if approach is not None:
        df = df[df.approach == approach]
    return int(len(df))


def trial_sharpes(run_id=None, approach=None):
    """the logged trials' OOS sharpes (annualized) — feeds cross-trial variance."""
    if not os.path.exists(MINING_LOG):
        return []
    df = pd.read_csv(MINING_LOG)
    if run_id is not None:
        df = df[df.run_id == run_id]
    if approach is not None:
        df = df[df.approach == approach]
    s = pd.to_numeric(df.oos_sharpe, errors="coerce").dropna()
    return s.tolist()


# ---- self-test ----------------------------------------------------------------
if __name__ == "__main__":
    rng = np.random.default_rng(0)

    # norm funcs round-trip
    for p in (0.001, 0.05, 0.5, 0.95, 0.999):
        assert abs(norm_cdf(norm_ppf(p)) - p) < 1e-7
    print("norm_ppf/cdf round-trip OK")

    # 1) pure-noise winner of a 1000-trial search must FAIL the DSR gate:
    #    simulate 1000 skill-less candidates, take the best one's returns.
    T = 750
    trials = [rng.normal(0.0, 0.01, T) for _ in range(1000)]
    srs = [r.mean() / r.std(ddof=1) for r in trials]
    best = trials[int(np.argmax(srs))]
    d = deflated_sharpe(best, n_trials=1000,
                        trial_sharpes_annual=[s * ANN for s in srs])
    print("noise-best of 1000:", d["dsr_prob"], "annualSR", d["sr_annual"], "->",
          "PASS" if d["pass"] else "FAIL (correct)")
    assert not d["pass"], "DSR let a pure-noise winner through!"

    # 2) genuinely strong single candidate (no search) must PASS.
    r = rng.normal(0.0012, 0.01, T)          # ~1.9 annualized Sharpe
    d2 = deflated_sharpe(r, n_trials=1)
    print("true-edge single trial:", d2["dsr_prob"], "annualSR", d2["sr_annual"], "->",
          "PASS" if d2["pass"] else "FAIL")
    assert d2["pass"], "DSR rejected a real edge with n_trials=1"

    # 3) same true edge, but honestly reported as 1-of-500 trials with wide trial
    #    spread -> the bar rises (dsr_prob must drop vs case 2).
    d3 = deflated_sharpe(r, n_trials=500,
                         trial_sharpes_annual=list(rng.normal(0, 1.0, 500)))
    print("true edge @500 trials:", d3["dsr_prob"], "(bar sr*_annual", d3["sr_star_annual"], ")")
    assert d3["dsr_prob"] < d2["dsr_prob"]

    # 4) purged walk-forward: no overlap, purge+embargo really removed.
    days = pd.bdate_range("2022-01-03", periods=500).date.tolist()
    for tr, te in purged_walk_forward(days, n_folds=5):
        ts_, te_ = set(tr), set(te)
        assert not ts_ & te_, "train/test overlap!"
        lo, hi = min(te), max(te)
        near = [x for x in tr if abs((x - lo).days) <= PURGE_DAYS or
                0 <= (x - hi).days <= PURGE_DAYS + EMBARGO_DAYS]
        # purge is in TRADING days; calendar-day check above is conservative-ish,
        # do the exact index-based check instead:
        idx = {d_: i for i, d_ in enumerate(days)}
        ilo, ihi = idx[lo], idx[hi]
        for x in tr:
            assert idx[x] < ilo - PURGE_DAYS + 1 or idx[x] > ihi + PURGE_DAYS + EMBARGO_DAYS - 1, \
                "purge/embargo breached at %s (test %s..%s)" % (x, lo, hi)
    print("purged_walk_forward folds OK (purge=%dd embargo=%dd)" % (PURGE_DAYS, EMBARGO_DAYS))

    # 5) lockbox guards
    df = pd.DataFrame({"Datetime": pd.date_range("2026-04-10", periods=10, freq="D")})
    try:
        assert_no_lockbox(df)
        raise SystemExit("lockbox assert failed to fire!")
    except RuntimeError as e:
        print("lockbox assert fires correctly:", str(e)[:60], "...")
    t = trim_lockbox(df)
    assert pd.to_datetime(t.Datetime).dt.date.max() < LOCKBOX_START
    print("trim_lockbox OK (%d -> %d rows)" % (len(df), len(t)))
    print("\nALL SELF-TESTS PASSED")
