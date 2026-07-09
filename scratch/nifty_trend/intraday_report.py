"""Build results_intraday.js for the ORB winner — Full / Train / OOS views so the
honest out-of-sample decay is visible, plus significance (PASS) + Monte Carlo.
Feeds dashboard_intraday.html (same render code as the main dashboard).
"""
import json, os
import numpy as np
import pandas as pd
import intraday_engine as ie
import engine
from montecarlo import montecarlo
from intraday_optimize import optimize, sig_test, split
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__))
WINNER = dict(design="tod_orb", exit="atr_rr",
              params={"or_min": 30, "orb_k": 1.0, "h0": 11, "h1": 13, "atr_sl": 2.5, "rr": 1.5})


def view(d, label):
    res = ie.backtest(d, WINNER["design"], WINNER["params"], WINNER["exit"])
    m, tr = engine.metrics(res)
    eq = res["equity"]; n = 400
    eq_ds = downsample(list(eq.equity), n)
    bh = d.set_index("Datetime").Close.reindex(pd.to_datetime(eq.Datetime)).ffill().values
    bh = engine.START_CAP * bh / bh[0]
    sig = sig_test(d, WINNER["design"], WINNER["params"], WINNER["exit"], n_perm=1000)
    mc = montecarlo(res, n_sims=1000)
    tr_s = tr.tail(10).to_dict("records") if not tr.empty else []
    for t in tr_s:
        t["entry_dt"] = str(pd.to_datetime(t["entry_dt"]))[:16]
        t["exit_dt"] = str(pd.to_datetime(t["exit_dt"]))[:16]
        for kk in ("entry", "exit", "points", "pnl", "qty"):
            t[kk] = round(float(t[kk]), 1)
    # ALL trades (lightweight) for the left-list + chart markers (Jesse-style panel)
    all_tr = []
    if not tr.empty:
        for _, t in tr.iterrows():
            all_tr.append({
                "side": t["side"], "entry_dt": str(pd.to_datetime(t["entry_dt"]))[:16],
                "exit_dt": str(pd.to_datetime(t["exit_dt"]))[:16],
                "entry": round(float(t["entry"]), 1), "exit": round(float(t["exit"]), 1),
                "points": round(float(t["points"]), 1), "pnl": round(float(t["pnl"]), 1),
                "reason": t.get("reason", "")})
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}
    return {
        "dna": {**WINNER["params"], "exit": WINNER["exit"]},
        "metrics": md,
        "equity": eq_ds, "benchmark": downsample(list(bh), n),
        "labels": dates_labels(eq, n),
        "underwater": downsample(m.get("underwater", []), n),
        "worst_periods": worst_periods(m.get("underwater", [])),
        "monthly": monthly_returns(eq),
        "significance": dict(real_sharpe=sig[0], p_value=sig[1], null_p95=sig[2],
                             null_mean=0.0, n_perm=1000, significant=bool(sig[1] < 0.05)),
        "mc": None if mc is None else {
            "table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
            "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
            "orig_path": downsample(mc["orig_path"], 120)},
        "trades": tr_s,
        "all_trades": all_tr,
        "opt_table": [],
    }


def main():
    d = ie.resample(ie.load_1m(), "15m")
    dtr, doos = split(d)
    cands = optimize(d, "tod_orb", trials=300)
    opt_table = [{"params": c["params"], "train_sharpe": round(c["train_sharpe"], 2),
                  "oos_sharpe": round(c["oos_sharpe"], 2), "net": round(c["oos_net"], 1),
                  "dd": round(c["oos_dd"], 1), "trades": c["oos_trades"]} for c in cands[:8]]

    # daily NIFTY candles (for the trade-list + candlestick chart panel)
    dd = (d.set_index("Datetime").resample("1D")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]
    out = {"meta": {"window": [str(d.Datetime.iloc[0])[:10], str(d.Datetime.iloc[-1])[:10]],
                    "days": int(d.day.nunique()), "start_cap": engine.START_CAP,
                    "design": "Mid-Day Opening-Range Breakout (11:00-13:00)", "tf": "15m",
                    "candles": candles},
           "combos": {}}
    print("building views ...", flush=True)
    out["combos"]["full"] = view(d, "full"); out["combos"]["full"]["opt_table"] = opt_table
    out["combos"]["train"] = view(dtr, "train")
    out["combos"]["oos"] = view(doos, "oos")
    for k in ("full", "train", "oos"):
        mm = out["combos"][k]["metrics"]; s = out["combos"][k]["significance"]
        print(f"  {k:5s} sharpe={mm['sharpe']} net={mm['net_pct']}% p={s['p_value']:.3f}")

    path = os.path.join(HERE, "results_intraday.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    print(f"wrote {path} ({os.path.getsize(path)//1024} KB)")


if __name__ == "__main__":
    main()
