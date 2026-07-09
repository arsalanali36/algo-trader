"""Step 7 — run the full pipeline for all 4 combos and emit results.js
(window.RESULTS = {...}) that dashboard.html renders. Honest numbers: whatever
the backtest/significance/MC actually produce, including failing verdicts.
"""
import json, os, datetime
import numpy as np
import pandas as pd
import engine
from optimize import optimize, split
from significance import significance
from montecarlo import montecarlo

HERE = os.path.dirname(os.path.abspath(__file__))
COMBOS = [("trail", "positional"), ("atr", "positional"),
          ("trail", "intraday"), ("atr", "intraday")]


def downsample(arr, n=400):
    if len(arr) <= n:
        return list(arr)
    idx = np.linspace(0, len(arr) - 1, n).astype(int)
    return [arr[i] for i in idx]


def monthly_returns(eq):
    e = eq.copy()
    e["Datetime"] = pd.to_datetime(e.Datetime)
    daily = e.set_index("Datetime").equity.resample("D").last().dropna()
    m = daily.resample("MS").last()
    m0 = pd.concat([pd.Series([engine.START_CAP], index=[m.index[0] - pd.offsets.MonthBegin()]), m])
    ret = m0.pct_change().dropna() * 100
    out = {}
    for ts, v in ret.items():
        out.setdefault(str(ts.year), {})[ts.month] = round(float(v), 1)
    return out


def worst_periods(uw, k=5):
    a = np.array(uw)
    items = []
    for i in range(2, len(a) - 2):
        if a[i] < a[i-1] and a[i] <= a[i+1] and a[i] < -3:
            items.append((i, a[i]))
    items.sort(key=lambda x: x[1])
    chosen, used = [], []
    for i, v in items:
        if all(abs(u - i) > len(a) * 0.04 for u in used):
            chosen.append({"x": i, "dd": round(float(v), 1)}); used.append(i)
        if len(chosen) >= k:
            break
    L = len(a)
    for r, c in enumerate(chosen):
        c["rank"] = r + 1
        c["frac"] = round(c["x"] / max(1, L - 1), 4)     # position along the curve 0..1
    return chosen


def dates_labels(eq, n=400):
    dt = pd.to_datetime(downsample(list(eq.Datetime), n))
    return [d.strftime("%y-%m") for d in dt]


def run_combo(df, variant, mode):
    cands = optimize(df, variant, mode, trials=250)
    if not cands:                       # nothing cleared the gate (weak intraday)
        p = dict(engine.DEFAULTS[variant])
        opt_table = []
    else:
        p = cands[0]["params"]
        opt_table = [{
            "params": c["params"], "train_sharpe": round(c["train_sharpe"], 2),
            "oos_sharpe": round(c["oos_sharpe"], 2), "net": round(c["oos_net"], 1),
            "dd": round(c["oos_maxdd"], 1), "trades": c["oos_trades"],
        } for c in cands[:8]]

    res = engine.backtest(df, variant, mode, p)
    m, tr = engine.metrics(res)

    # equity + benchmark (buy & hold on the same window)
    eq = res["equity"]
    n = 400
    eq_ds = downsample(list(eq.equity), n)
    bh = df.set_index("Datetime").Close.reindex(pd.to_datetime(eq.Datetime)).ffill().values
    bh = engine.START_CAP * bh / bh[0]
    bh_ds = downsample(list(bh), n)
    labels = dates_labels(eq, n)
    uw_ds = downsample(m.get("underwater", []), n)

    sig = significance(df, variant, mode, p, n_perm=1000)
    mc = montecarlo(res, n_sims=1000)

    # sample trades
    tr_s = tr.tail(10).to_dict("records") if not tr.empty else []
    for t in tr_s:
        t["entry_dt"] = str(pd.to_datetime(t["entry_dt"]))[:16]
        t["exit_dt"] = str(pd.to_datetime(t["exit_dt"]))[:16]
        for kk in ("entry", "exit", "points", "pnl", "qty"):
            t[kk] = round(float(t[kk]), 1)

    # strip heavy fields from metrics
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}

    return {
        "dna": p,
        "metrics": md,
        "equity": eq_ds, "benchmark": bh_ds, "labels": labels,
        "underwater": uw_ds,
        "worst_periods": worst_periods(m.get("underwater", [])),
        "monthly": monthly_returns(eq),
        "significance": sig,
        "mc": None if mc is None else {
            "table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
            "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
            "orig_path": downsample(mc["orig_path"], 120),
        },
        "opt_table": opt_table,
        "trades": tr_s,
    }


def main():
    df = engine.load()
    out = {"meta": {
        "generated": str(datetime.date.today()),
        "window": [str(df.Datetime.iloc[0])[:10], str(df.Datetime.iloc[-1])[:10]],
        "bars": len(df), "days": int(pd.to_datetime(df.Datetime).dt.date.nunique()),
        "start_cap": engine.START_CAP,
    }, "combos": {}}
    for variant, mode in COMBOS:
        key = f"{variant}|{mode}"
        print(f"running {key} ...", flush=True)
        out["combos"][key] = run_combo(df, variant, mode)
        s = out["combos"][key]["significance"]; mm = out["combos"][key]["metrics"]
        print(f"   sharpe={mm.get('sharpe')}  net={mm.get('net_pct')}%  "
              f"sig_p={s['p_value']:.3f} ({'SIG' if s['significant'] else 'not sig'})")

    path = os.path.join(HERE, "results.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    print(f"\nwrote {path}  ({os.path.getsize(path)//1024} KB)")


if __name__ == "__main__":
    main()
