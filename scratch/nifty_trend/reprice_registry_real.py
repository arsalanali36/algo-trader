#!/usr/bin/env python3
"""reprice_registry_real.py — put HONEST real-lake numbers on the ORB family (00.x).

Every runs/<slug>/results.js Sharpe/net is BLACK-SCHOLES-modeled premium (+DOM slip),
NOT real premium (TRAP #136, bs_vs_reallake.py). BS understates the theta an option
BUYER bleeds, so these ATM-buy / long-vol ORB structures look far better on BS than they
trade. This tool reprices each run's OWN trades (same entry/exit time + strike) on the
REAL held-strike option lake (real_struct2._px) + real Zerodha charges + DOM slip, and
writes the result back so the registry shows the TRUE number:

  * results.js  -> a new  combos["real|full" | "real|train" | "real|oos" | ...]  (real
    per-trade gross/fee, same schema as bs|full) so /api/registry-economics (Net/Tax/
    Capital columns) + any combo-reader picks it up.
  * runs/index.json -> per-run `real_full` summary (headline Sharpe/Net%/MaxDD/Win/PF) +
    `real_cost` {method,date,train,oos} so /registry2 shows real, not BS.

Generalised over underlying/flag/step so the two ORB rows the NIFTY-WEEK tool can't do
honestly are handled with their OWN lake:
  * BankNifty ORB (00.03)  -> BANKNIFTY lake, strike step 100
  * Overnight ORB  (00.08) -> NIFTY MONTH lake (it holds a MONTHLY option overnight)

CAVEAT (same as bs_vs_reallake): keeps the BS run's EXIT TIMING (tp/sl fired on BS
levels). Solid for spot-exit single-leg buys (ORB); for premium-exit multi-leg the exact
figure can shift under a full real re-backtest, but the sign/direction is robust. Real
coverage is limited to the lake window (~2021-07 on) — coverage_pct is reported and the
sharpe is annualised over the covered span only.

  python reprice_registry_real.py            # dry-run: print BS vs REAL table
  python reprice_registry_real.py --write     # also write results.js + index.json
"""
import os
import sys
import json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)
import _paths  # noqa: F401
import real_struct2 as rs2
import optlake_load as lake
import bs_option as bs
import option_structures as ostr

START_CAP = 1_000_000.0
RUNS = os.path.join(HERE, "runs")

# slug -> (underlying, expiry-flag, strike-step)
SPEC = {
    "mid_orb_nifty":       ("NIFTY", "WEEK", 50),
    "orb_supertrend":      ("NIFTY", "WEEK", 50),
    "long_straddle_orb":   ("NIFTY", "WEEK", 50),
    "debit_vertical_orb":  ("NIFTY", "WEEK", 50),
    "ratio_backspread":    ("NIFTY", "WEEK", 50),
    "long_strangle_orb":   ("NIFTY", "WEEK", 50),
    "banknifty_hunt":      ("BANKNIFTY", "WEEK", 100),
    "overnight_orb_nifty": ("NIFTY", "MONTH", 50),
}


def _legs_for(struct, side, params):
    """(opt_type, offset_steps, side_signed) — mirrors option_structures leg build."""
    if struct in ostr.STRUCTURES:
        return list(ostr.STRUCTURES[struct])
    if struct in ostr.DIRECTIONAL:
        spec = list(ostr.DIRECTIONAL[struct][side])
        w = int(params.get("wing_off", 0))
        if w:
            spec = [(ot, (w if off > 0 else -w) if s < 0 else off, s) for (ot, off, s) in spec]
        bo = int(params.get("bs_off", 0))
        if bo:
            spec = [(ot, (bo if off > 0 else -bo) if (s > 0 and off != 0) else off, s) for (ot, off, s) in spec]
        return spec
    return None                                    # single ATM leg (ORB / chain buy)


def _read_results(slug):
    txt = open(os.path.join(RUNS, slug, "results.js"), encoding="utf-8").read().strip()
    return json.loads(txt[len("window.RESULTS = "):].rstrip(";"))


def _setup_lake(underlying, flag, step):
    """Point the lake reader at <underlying> + set the strike step, and clear caches so a
    prior underlying's grid (same flag key) can't leak in (rs2._G_CACHE keys on flag,tf)."""
    lake.LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake", underlying)
    rs2.STEP = step
    rs2._G_CACHE.clear()
    G = rs2.grid(flag, "5m")
    DT = np.asarray(G["DT"], dtype="datetime64[ns]")
    return G, DT, step


def _bar_at(DT, ts):
    ts = pd.Timestamp(ts)
    j = int(np.searchsorted(DT, np.datetime64(ts), side="right")) - 1
    if j < 0 or pd.Timestamp(DT[j]).date() != ts.date():
        return None
    return j


def _reprice_trade(t, G, DT, step, struct, params):
    """One bs|full trade -> a real trade record (same schema as bs|full), or None if the
    real premium can't be resolved for a leg (outside lake window / no bar)."""
    K = float(t["strike"]); qty = int(t["qty"]); dirn = str(t.get("side"))
    ie, xe = _bar_at(DT, t["entry_dt"]), _bar_at(DT, t["exit_dt"])
    if ie is None or xe is None:
        return None
    specs = _legs_for(struct, dirn, params)
    if specs is None:
        opt = t.get("opt_type") if t.get("opt_type") in ("CE", "PE") else ("CE" if dirn == "long" else "PE")
        specs = [(opt, 0, +1)]
    when = pd.Timestamp(t["entry_dt"])
    gross = fee = slip = 0.0
    ep_net = xp_net = 0.0
    for (opt, off, s) in specs:
        Kl = K + off * step
        ep, xp = rs2._px(G, ie, opt, Kl), rs2._px(G, xe, opt, Kl)
        if ep <= 0:
            return None
        lq = qty * abs(s)
        gross += s * (xp - ep) * qty
        fee += bs.calc_charges(ep, max(xp, 0.0), lq, entry_side="BUY" if s > 0 else "SELL", when=when)
        slip += bs.slip_cost_leg(ep, xp, lq)
        ep_net += s * ep
        xp_net += s * xp
    fee_all = fee + slip
    pnl = gross - fee_all
    return {
        "side": t.get("side"), "opt_type": t.get("opt_type"), "strike": t.get("strike"),
        "entry_dt": t["entry_dt"], "exit_dt": t["exit_dt"],
        "entry_spot": t.get("entry_spot"), "exit_spot": t.get("exit_spot"),
        "points": t.get("points"),
        "entry_prem": round(ep_net, 2), "exit_prem": round(xp_net, 2),
        "qty": qty, "gross": round(gross, 2), "fee": round(fee_all, 2), "pnl": round(pnl, 2),
        "bars": t.get("bars"), "reason": t.get("reason"),
    }


def _metrics(recs):
    if not recs:
        return None
    pnl = np.array([r["pnl"] for r in recs], float)
    n = len(pnl)
    dts = sorted(pd.Timestamp(r["entry_dt"]).date() for r in recs)
    span = max(1, (dts[-1] - dts[0]).days)
    sd = pnl.std()
    sh = (pnl.mean() / sd * np.sqrt(252 * n / span)) if sd > 0 else 0.0
    eq = np.cumsum(pnl)
    dd = (eq - np.maximum.accumulate(eq))
    net = float(pnl.sum())
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    pf = (wins.sum() / abs(losses.sum())) if (losses.size and losses.sum() != 0) else (99.0 if wins.size else 0.0)
    return {
        "trades": n, "sharpe": round(float(sh), 3),
        "net_abs": round(net), "net_pct": round(net / START_CAP * 100, 3),
        "maxdd": round(float(dd.min()) / START_CAP * 100, 3),
        "win_rate": round(float((pnl > 0).mean() * 100), 3),
        "profit_factor": round(float(min(pf, 99.0)), 3),
        "start_cap": START_CAP, "cov_from": str(dts[0]), "cov_to": str(dts[-1]),
    }


def _combo_trades(R, key):
    c = (R.get("combos") or {}).get(key) or {}
    return c.get("all_trades") or []


def reprice(slug):
    underlying, flag, step = SPEC[slug]
    R = _read_results(slug)
    meta = json.load(open(os.path.join(RUNS, slug, "meta.json")))
    struct = str(meta.get("design", "")).split("/")[0]
    params = meta.get("params", {})
    G, DT, step = _setup_lake(underlying, flag, step)

    # period membership from the bs combos (identical split guaranteed)
    periods = [p for p in ("full", "train", "oos", "recent") if f"bs|{p}" in (R.get("combos") or {})]
    memb = {p: set(t["entry_dt"] for t in _combo_trades(R, f"bs|{p}")) for p in periods if p != "full"}

    bs_full = _combo_trades(R, "bs|full")
    real_recs = []
    for t in bs_full:
        if t.get("pnl") is None:
            continue
        rr = _reprice_trade(t, G, DT, step, struct, params)
        if rr is not None:
            real_recs.append(rr)

    out = {"periods": {}, "combos": {}, "cov": len(real_recs), "tot": len(bs_full),
           "underlying": underlying, "flag": flag, "step": step}
    # full
    out["periods"]["full"] = _metrics(real_recs)
    out["combos"]["real|full"] = real_recs
    # train / oos / recent — filter real recs by that period's entry_dt set
    for p in periods:
        if p == "full":
            continue
        recs_p = [r for r in real_recs if r["entry_dt"] in memb[p]]
        out["periods"][p] = _metrics(recs_p)
        out["combos"][f"real|{p}"] = recs_p
    return out, R, meta


# ---------------------------------------------------------------- writers
def _wrap_combo(recs, metrics_extra):
    """Minimal but complete combo shell (schema-compatible with bs|full readers)."""
    m = dict(metrics_extra or {})
    return {"metrics": m, "all_trades": recs, "trades": len(recs)}


def write_run(slug, out, R):
    combos = R.setdefault("combos", {})
    for p, recs in out["combos"].items():
        period = p.split("|")[1]
        combos[p] = _wrap_combo(recs, out["periods"].get(period))
    path = os.path.join(RUNS, slug, "results.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("window.RESULTS = " + json.dumps(R, separators=(",", ":")) + ";")
    return path


def _summary(m, cov, tot):
    if not m:
        return None
    return {
        "sharpe": m["sharpe"], "net_pct": m["net_pct"], "net_abs": m["net_abs"],
        "maxdd": m["maxdd"], "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
        "trades": m["trades"], "coverage_pct": round(cov / max(1, tot) * 100, 1),
        "cov_from": m["cov_from"], "cov_to": m["cov_to"],
    }


def write_index(idx, slug, out):
    from datetime import date
    full = out["periods"].get("full")
    for e in idx:
        if e.get("slug") != slug:
            continue
        e["real_full"] = _summary(full, out["cov"], out["tot"])
        rc = e.get("real_cost") or {}
        rc.update({
            "method": f"REAL {out['underlying']} {out['flag']} held-strike premium + Zerodha charges + DOM slip",
            "date": date.today().isoformat(),
            "note": f"BS->real reprice (TRAP #136). coverage {out['cov']}/{out['tot']} of bs|full trades.",
        })
        for p in ("train", "oos"):
            mp = out["periods"].get(p)
            if mp:
                rc[p] = {"sharpe": mp["sharpe"], "net_pct": mp["net_pct"]}
        e["real_cost"] = rc
        return True
    return False


def main():
    write = "--write" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    slugs = only or list(SPEC.keys())

    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path, encoding="utf-8")) if write else None

    print("{:<20} {:<10} {:>3} {:>8} {:>10} {:>8} {:>10} {:>8} {:>6}".format(
        "run", "lake", "cov%", "BS Sh", "BS net%", "REAL Sh", "REAL net%", "win%", "PF"))
    for slug in slugs:
        try:
            out, R, meta = reprice(slug)
            bs_m = (R["combos"]["bs|full"].get("metrics") or {})
            full = out["periods"]["full"] or {}
            covpct = out["cov"] / max(1, out["tot"]) * 100
            print("{:<20} {:<10} {:>3.0f} {:>8.2f} {:>10.1f} {:>8.2f} {:>10.1f} {:>8.1f} {:>6.2f}".format(
                slug, f"{out['underlying'][:4]}/{out['flag']}", covpct,
                bs_m.get("sharpe", 0), bs_m.get("net_pct", 0),
                full.get("sharpe", 0), full.get("net_pct", 0),
                full.get("win_rate", 0), full.get("profit_factor", 0)))
            if write:
                write_run(slug, out, R)
                write_index(idx, slug, out)
        except Exception as e:
            import traceback
            print(f"{slug:<20} ERR {e}")
            traceback.print_exc()
    if write:
        with open(idx_path, "w", encoding="utf-8") as fh:
            json.dump(idx, fh, indent=1)
        print("\n[written] results.js real|* combos + index.json real_full for", len(slugs), "runs")


if __name__ == "__main__":
    main()
