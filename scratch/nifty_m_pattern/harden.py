"""
Harden the medium-M + hold+1day iron-fly: add (a) a basket SL and (b) a stronger
spike (IV-pop) entry filter. Grid on TRAIN, confirm on OOS. No tuning-to-OOS.
Reuses bt (engine/charges) — only run_trade is reimplemented to add SL,
and detect returns the realized spike ratio so we can filter on IV-pop strength.
"""
import os, sys, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt as M
from bt import (_prem, _spot_at, _minutes, _round50, _nearest_weekly,
                          POS_EXIT_HM, QTY, _atm_at, atm_combined_series, _extrema)
import charges as CH

MPARAMS = M.M_PRESETS["medium"]     # (EXTREMA_W, SPIKE_LOOK, SPIKE_PCT, PB_PCT, TOL, BOUNCE_PCT)
WING, TAKE, MHD = 250, 0.50, 1


def detect_meta(series, params):
    """Return (hm, spike_ratio) of first M-rollover, else None. spike_ratio = P1/base."""
    EW, SL, SP, PB, TOL, BO = params
    if len(series) < SL + 10:
        return None
    mins = [s[0] for s in series]; vals = [s[1] for s in series]
    peaks, troughs = _extrema(vals, EW); n = len(vals)
    for i in range(n):
        if i not in peaks:
            continue
        p1 = vals[i]; base = min(vals[max(0, i - SL):i + 1])
        if base <= 0 or p1 < base * (1 + SP):
            continue
        j = None
        for k in range(i + 1, n):
            if k in troughs and vals[k] <= p1 * (1 - PB):
                j = k; break
            if vals[k] > p1:
                break
        if j is None:
            continue
        t = vals[j]; q = None
        for k in range(j + 1, n):
            if k in peaks and vals[k] >= t * (1 + BO) and vals[k] <= p1 * (1 + TOL):
                q = k; break
            if vals[k] > p1 * (1 + TOL):
                break
        if q is None:
            continue
        for k in range(q + 1, n):
            if vals[k] < t:
                hm = mins[k]
                if M.SIG_LO_HM <= hm <= M.SIG_HI_HM:
                    return (hm, p1 / base)
                return None
    return None


def all_sigs(days, params):
    out = []
    for d in sorted(days.keys()):
        r = detect_meta(atm_combined_series(days[d]), params)
        if r is not None:
            out.append((d, r[0], r[1]))     # date, hm, spike_ratio
    return out


def run_trade_sl(days, entry_date, entry_hm, wing, take_pct, mhd, sl_frac):
    grid = days.get(entry_date)
    if not grid:
        return None
    atm = _atm_at(grid, entry_hm)
    if atm is None:
        return None
    ce_wk, pe_wk = atm + wing, atm - wing
    sce, _ = _prem(grid, entry_hm, "CE", atm); spe, _ = _prem(grid, entry_hm, "PE", atm)
    bce, _ = _prem(grid, entry_hm, "CE", ce_wk); bpe, _ = _prem(grid, entry_hm, "PE", pe_wk)
    if None in (sce, spe, bce, bpe):
        return None
    entry_credit = (sce + spe) - (bce + bpe)
    if entry_credit <= 0:
        return None
    target = take_pct * entry_credit
    sl_level = -sl_frac * entry_credit if sl_frac else None
    book = {("CE", atm): {"side": "SELL", "p0": sce}, ("PE", atm): {"side": "SELL", "p0": spe},
            ("CE", ce_wk): {"side": "BUY", "p0": bce}, ("PE", pe_wk): {"side": "BUY", "p0": bpe}}
    last = {k: v["p0"] for k, v in book.items()}
    exp = str(_nearest_weekly(entry_date)); alldays = sorted(days.keys())
    week_seq = []
    for d in alldays:
        if d < entry_date or d > exp:
            continue
        lo = entry_hm + 1 if d == entry_date else 916
        hi = POS_EXIT_HM if d == exp else 1529
        week_seq.append((d, lo, hi))
    seq = week_seq[:mhd + 1]
    if seq:
        d0, lo0, _ = seq[-1]; seq[-1] = (d0, lo0, POS_EXIT_HM)
    is_exp_dl = bool(seq) and seq[-1][0] == exp
    deadline = (seq[-1][0], POS_EXIT_HM) if seq else (entry_date, POS_EXIT_HM)

    def running(dgrid, m):
        tot = 0.0
        for (ot, k), leg in book.items():
            p, _ = _prem(dgrid, m, ot, k)
            if p is not None:
                last[(ot, k)] = p
            p = last[(ot, k)]
            tot += (p - leg["p0"]) if leg["side"] == "BUY" else (leg["p0"] - p)
        return tot

    def close_all(dgrid, m, d, reason):
        for (ot, k), leg in list(book.items()):
            p, _ = _prem(dgrid, m, ot, k)
            charge_legs.append((leg["p0"], p if p is not None else last[(ot, k)], leg["side"], d))
        book.clear()
        return reason

    charge_legs = []; exited = None; exit_day = deadline[0]
    for (d, lo, hi) in seq:
        dgrid = days.get(d)
        if not dgrid:
            continue
        for m in _minutes(dgrid, lo, hi):
            rp = running(dgrid, m)
            if rp >= target:
                exited = close_all(dgrid, m, d, "target"); exit_day = d; break
            if sl_level is not None and rp <= sl_level:
                exited = close_all(dgrid, m, d, "sl"); exit_day = d; break
        if exited:
            break
    if not exited:
        d, hm = deadline; dgrid = days.get(d)
        sp = (_spot_at(dgrid, hm) if dgrid else None) or _spot_at(grid, entry_hm)
        for (ot, k), leg in list(book.items()):
            p = None
            if dgrid:
                p, _ = _prem(dgrid, hm, ot, k)
            if p is None and is_exp_dl:
                p = max(0.0, sp - k) if ot == "CE" else max(0.0, k - sp)
            if p is None:
                p = last[(ot, k)]
            charge_legs.append((leg["p0"], p, leg["side"], d))
        book.clear(); exited = "expiry" if is_exp_dl else "time_exit"; exit_day = d
    pts = sum((xp - p0) if side == "BUY" else (p0 - xp) for (p0, xp, side, w) in charge_legs)
    gross = pts * QTY
    charge = sum(CH.option_charges(p0, xp, QTY, entry_side=side, when=w)
                 for (p0, xp, side, w) in charge_legs)
    return {"date": entry_date, "exit_date": exit_day, "net": round(gross - charge, 1),
            "reason": exited}


def run(days, sigs, wing, take, mhd, sl_frac, min_spike):
    out = []; busy = ""
    for (d, hm, sr) in sigs:
        if d <= busy:
            continue
        if sr < (1 + min_spike):      # extra IV-pop strength filter
            continue
        r = run_trade_sl(days, d, hm, wing, take, mhd, sl_frac)
        if not r:
            continue
        out.append(r); busy = r["exit_date"]
    return out


def stat(rows):
    if not rows:
        return {"n": 0}
    net = np.array([r["net"] for r in rows])
    eq = np.cumsum(net); dd = (eq - np.maximum.accumulate(eq)).min()
    gp = net[net > 0].sum(); gl = -net[net < 0].sum()
    yrs = 5
    sh = (net.mean() / net.std()) * np.sqrt(len(net) / yrs) if net.std() else 0
    return {"n": len(rows), "net": round(net.sum()), "sharpe": round(sh, 2),
            "pf": round(gp / gl, 2) if gl else 99, "win": round(100 * (net > 0).mean(), 1),
            "maxdd": round(dd), "worst": round(net.min())}


def boot_p(rows, iters=5000, seed=7):
    net = np.array([r["net"] for r in rows], float)
    if len(net) < 5:
        return 1.0
    rng = np.random.default_rng(seed)
    return float((net[rng.integers(0, len(net), (iters, len(net)))].mean(1) <= 0).mean())


if __name__ == "__main__":
    print("loading lake ...", flush=True)
    days = M.load_lake()
    sigs = all_sigs(days, MPARAMS)
    print(f"  {len(sigs)} raw medium-M signals\n", flush=True)
    print(f"{'sl_frac':<8}{'min_spike':<10}{'n':<5}{'net':>10}{'Sharpe':>8}{'PF':>6}"
          f"{'win%':>7}{'maxDD':>10}{'worst':>9}{'p':>8}   train->oos Sharpe/net", flush=True)
    results = {}
    for min_spike in (0.18, 0.30, 0.45):
        for sl_frac in (None, 2.0, 1.5, 1.0):
            rows = run(days, sigs, WING, TAKE, MHD, sl_frac, min_spike)
            s = stat(rows); p = boot_p(rows)
            tr = stat([r for r in rows if r["date"] < "2025-01-01"])
            oos = stat([r for r in rows if r["date"] >= "2025-01-01"])
            key = f"sl{sl_frac}|spk{min_spike}"
            results[key] = {"all": s, "p": round(p, 4), "train": tr, "oos": oos}
            print(f"{str(sl_frac):<8}{min_spike:<10}{s['n']:<5}{s['net']:>10,}"
                  f"{s['sharpe']:>8}{s['pf']:>6}{s['win']:>7}{s['maxdd']:>10,}"
                  f"{s['worst']:>9,}{p:>8.4f}   "
                  f"tr Sh{tr.get('sharpe',0)}/Rs{tr.get('net',0):,}  "
                  f"oos Sh{oos.get('sharpe',0)}/Rs{oos.get('net',0):,}", flush=True)
        print(flush=True)
    json.dump(results, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
             "harden_results.json"), "w"), indent=1)
    print("wrote harden_results.json")
