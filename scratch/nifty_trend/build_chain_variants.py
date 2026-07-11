"""Chain-Zone execution-structure variants — SAME winner signal/params, different option leg.

The 2026-07-11 hunt (build_chain.py) validated the chain_zone entry (5m, p=0.000) but the
CREDIT-SPREAD pass-3 went negative: the edge lives in BIG winners (stop_only ride-to-EOD,
avg win 1.6x avg loss) and a SOLD structure CAPS winners at the credit (short gamma) —
avg win crushed 5398->1677 while losses barely shrank. So compare, on the IDENTICAL
trade set (no re-optimize, no re-hunt):

  chain_zone_naked   SELL naked ATM (PE on long / CE on short) — user-requested comparison.
                     ⚠️ engine-only: undefined tail risk, wings MANDATORY for live.
  chain_zone_longatm BUY ATM (CE on long / PE on short) — long gamma keeps winners uncapped;
                     the structure the mission's other validated strategies use.

Reuses run_hunt.build_views + the same dashboard/write pattern. Usage:
    python build_chain_variants.py naked
    python build_chain_variants.py longatm
"""
import os
import sys
import json
import time
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import intraday_engine as ie
import run_hunt as rh
from intraday_optimize import split
from build_chain import _bs_spread  # credit-spread pass builder (unused here, keeps import parity)

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
BASE = os.path.join(RUNS, "chain_zone_credit")   # winner comes from the validated hunt

VRP_MULT = 0.95   # same honest premium stress as the credit run


def _bs_naked(rms_res, d, sigma_map, lot, lots):
    opt = bs.reprice_naked(rms_res["trades"], sigma_map, lot, lots, vrp_mult=VRP_MULT)
    return _pack(opt, rms_res, d)


def _bs_longatm(rms_res, d, sigma_map, lot, lots):
    opt = bs.reprice(rms_res["trades"], sigma_map, lot, lots)   # buy ATM CE/PE
    return _pack(opt, rms_res, d)


def _pack(opt, rms_res, d):
    """option trade list -> (engine-shaped res, dashboard all_trades, fees) — mirrors run_hunt."""
    etr = [dict(side=o["side"], entry=o["entry_prem"], exit=o["exit_prem"], qty=o["qty"],
                pnl=o["pnl"], points=o["points"], bars=o["bars"], reason=o["reason"],
                entry_i=o["entry_i"], exit_i=o["exit_i"],
                entry_dt=o["entry_dt"], exit_dt=o["exit_dt"]) for o in opt]
    add = np.zeros(len(d))
    for o in opt:
        if 0 <= o["exit_i"] < len(d):
            add[o["exit_i"]] += o["pnl"]
    eqv = engine.START_CAP + np.cumsum(add)
    bs_eq = pd.DataFrame({"Datetime": d.Datetime.values, "equity": eqv})
    bs_res = dict(trades=etr, equity=bs_eq, final=float(eqv[-1]),
                  variant=rms_res["variant"], mode="intraday", params=rms_res["params"])
    at = []
    for o in opt:
        o = dict(o); o["entry"] = o["entry_spot"]; o["exit"] = o["exit_spot"]
        o.pop("entry_i", None); o.pop("exit_i", None)
        at.append(o)
    fees = round(sum(o["fee"] for o in opt), 0)
    return bs_res, at, fees


VARIANTS = {
    "naked": dict(fn=_bs_naked, slug="chain_zone_naked",
                  title="Auto Rev-Chain — Zone Breakout (NAKED ATM sell ⚠️ wings reqd for live)"),
    "longatm": dict(fn=_bs_longatm, slug="chain_zone_longatm",
                    title="Auto Rev-Chain — Zone Breakout (ATM long option)"),
}


def main(variant):
    v = VARIANTS[variant]
    t0 = time.time()
    meta0 = json.load(open(os.path.join(BASE, "meta.json")))
    tf, params, exit_style = meta0["tf"], meta0["params"], meta0["exit"]
    winner = dict(design="chain_zone", tf=tf, exit=exit_style, params=params,
                  sig=dict(real_sharpe=1.36, p_value=meta0["p_value"], null_p95=0.0,
                           null_mean=0.0, n_perm=1000, significant=True))

    rh._bs_res_and_trades = v["fn"]                      # swap ONLY the option-leg reprice
    rh.DESIGN_TITLE["chain_zone"] = v["title"]

    d = ie.resample(ie.load_1m(), tf)
    dtr, doos = split(d)
    lot = bs.get_nifty_lot()
    dd = (d.set_index("Datetime").resample("1D")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sigma_map = bs.realised_vol_map(dd.Close)
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]

    print(f"[{variant}] building 3 passes x 3 periods (winner @ {tf}, {params}) ...", flush=True)
    combos = {}
    for period, dp in (("full", d), ("train", dtr), ("oos", doos)):
        views = rh.build_views(dp, winner, sigma_map, lot, 1, winner["sig"])
        for pas, cobj in views.items():
            combos[f"{pas}|{period}"] = cobj

    dna = {**params, "exit": exit_style}
    for k in combos:
        combos[k]["dna"] = dna
    out = {"meta": {
        "window": [str(d.Datetime.iloc[0])[:10], str(d.Datetime.iloc[-1])[:10]],
        "days": int(d.day.nunique()), "start_cap": engine.START_CAP,
        "design": f"{v['title']} — NIFTY {tf}",
        "design_key": "chain_zone", "slug": v["slug"], "tf": tf,
        "instrument": "NIFTY 50", "lot_size": lot, "lots": 1,
        "rms_caps": rh.RMS_CAPS, "dna": dna,
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
        "candles": candles},
        "dna_by_combo": {k: dna for k in combos},
        "combos": combos}

    trade_dates = {t["entry_dt"][:10] for c in combos.values() for t in c.get("all_trades", [])}
    try:
        from add_intraday import build_intraday
        out["meta"]["intraday"] = build_intraday("nifty_1min.csv", trade_dates)
        print(f"  intraday: {len(out['meta']['intraday'])} trade-days", flush=True)
    except Exception as e:
        print(f"  [intraday] skipped ({e})", flush=True)

    folder = os.path.join(RUNS, v["slug"])
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash)
    meta = {"slug": v["slug"], "design": "chain_zone", "title": v["title"],
            "tf": tf, "params": params, "exit": exit_style,
            "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": out["meta"]["days"],
            "significant": True,
            "bs_full": {k: combos["bs|full"]["metrics"].get(k) for k in
                        ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "p_value": meta0["p_value"], "created": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != v["slug"]]
    idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)

    print(f"\nwrote runs/{v['slug']}/", flush=True)
    for pas in ("instrument", "rms", "bs"):
        m = combos[f"{pas}|full"]["metrics"]
        print(f"  {pas:11s} sharpe={m['sharpe']:.2f} net={m['net_pct']:.1f}% "
              f"maxDD={m['maxdd']:.1f}% win={m['win_rate']:.0f}% fees={m.get('fees',0):.0f}", flush=True)
    print(f"done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "naked")
