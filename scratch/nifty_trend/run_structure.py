"""Option-STRUCTURE hunt -> 3-pass results, saved per-strategy.

The multi-leg sibling of run_hunt.py. Screens (regime-signal x structure x TF), optimizes the
best, gates on rotation-significance (p<0.05), then builds the SAME 3-pass x 3-period results.js
the dashboard renders — so a straddle/strangle/condor strategy lands in runs/<slug>/ exactly like
a directional one and shows up in hub.html.

Passes for a structure:
    instrument = gross option-premium P&L (delta+theta, NO charges, NO caps) — "is there an edge?"
    rms        = + account daily loss/profit caps
    bs         = + real Zerodha per-leg charges = deployable truth

Usage:  python run_structure.py --name <slug> [--tf 15m,5m] [--trials 120]
        python run_structure.py --screen        # just print the screen table, write nothing
"""
import os
import json
import time
import argparse
import numpy as np
import pandas as pd

import engine
import intraday_engine as ie
import bs_option as bs
import option_structures as osx
from intraday_optimize import split
from run_hunt import _combo_from_res, N_DS, RMS_CAPS

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

STRUCT_TITLE = {
    "short_straddle": "Short Straddle", "long_straddle": "Long Straddle",
    "short_strangle": "Short Strangle", "long_strangle": "Long Strangle",
    "iron_condor": "Iron Condor", "iron_fly": "Iron Fly",
}
SIG_TITLE = {"orb_break": "ORB breakout", "tod_orb_break": "Mid-day ORB", "midday_lull": "Mid-day lull"}

DEFAULT_P = dict(tp_frac=0.5, sl_frac=1.0, or_min=15, orb_k=1.0, h0=11, h1=13)


def _struct_trades(res):
    """all_trades[] in the dashboard schema for a structure pass."""
    out = []
    for t in res["trades"]:
        fee = float(t.get("fee", 0) or 0)
        pnl = float(t["pnl"])
        out.append({
            "side": t["side"], "opt_type": (t.get("struct") or "").upper(),
            "strike": t.get("atm"),
            "entry_dt": str(pd.to_datetime(t["entry_dt"]))[:16],
            "exit_dt": str(pd.to_datetime(t["exit_dt"]))[:16],
            "entry_spot": t.get("spot_in"), "exit_spot": t.get("spot_out"),
            "points": round(float(t["points"]), 2),
            "entry_prem": t.get("entry_prem"), "exit_prem": t.get("exit_prem"),
            "qty": int(t["qty"]),
            "gross": round(pnl + fee, 0), "fee": round(fee, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", "")})
    return out


# ---------- screen ----------
def screen(cache, tfs, sigma_map, lot):
    print("SCREEN — signal x structure x TF (default params, 1x, real charges)\n", flush=True)
    rows = []
    for tf in tfs:
        d = cache[tf]
        dtr, doos = split(d)
        for sig_name in osx.ENTRY_SIGNALS:
            for struct in osx.STRUCTURES:
                p = dict(DEFAULT_P)
                m1, _ = engine.metrics(osx.backtest_structure(dtr, sig_name, struct, p, sigma_map, lot, 1, charges=True))
                m2, _ = engine.metrics(osx.backtest_structure(doos, sig_name, struct, p, sigma_map, lot, 1, charges=True))
                if m1["trades"] < 40 or m2["trades"] < 20:
                    continue
                robust = min(m1.get("sharpe", -9), m2.get("sharpe", -9))
                rows.append(dict(sig_name=sig_name, struct=struct, tf=tf, robust=robust,
                                 train=m1["sharpe"], oos=m2["sharpe"], trades=m2["trades"]))
    rows.sort(key=lambda r: r["robust"], reverse=True)
    for r in rows[:12]:
        print(f"  {SIG_TITLE[r['sig_name']]:14s}{STRUCT_TITLE[r['struct']]:15s}{r['tf']:4s} "
              f"robust={r['robust']:5.2f} (tr {r['train']:.2f}/oos {r['oos']:.2f}) tr={r['trades']}", flush=True)
    return rows


# ---------- optimize (light grid) rank by robust min(train,oos) ----------
def optimize(d, sig_name, struct, sigma_map, lot, trials=120):
    dtr, doos = split(d)
    grid_tp = [0.3, 0.5, 0.75, 1.0]
    grid_sl = [0.5, 1.0, 1.5, 2.0]
    is_orb = sig_name in ("orb_break", "tod_orb_break")
    grid_orm = [15, 30, 45] if is_orb else [11]
    grid_k = [0.5, 1.0, 1.5] if is_orb else [1.0]
    grid_h0 = [10, 11] if sig_name == "tod_orb_break" else ([11, 12] if sig_name == "midday_lull" else [11])
    cands = []
    for tp in grid_tp:
        for sl in grid_sl:
            for orm in grid_orm:
                for k in grid_k:
                    for h0 in grid_h0:
                        p = dict(tp_frac=tp, sl_frac=sl, or_min=orm, orb_k=k, h0=h0, h1=13)
                        m1, _ = engine.metrics(osx.backtest_structure(dtr, sig_name, struct, p, sigma_map, lot, 1, charges=True))
                        m2, _ = engine.metrics(osx.backtest_structure(doos, sig_name, struct, p, sigma_map, lot, 1, charges=True))
                        if m1["trades"] < 30 or m2["trades"] < 15:
                            continue
                        robust = min(m1.get("sharpe", -9), m2.get("sharpe", -9))
                        cands.append(dict(params=p, robust=robust,
                                          train_sharpe=m1["sharpe"], oos_sharpe=m2["sharpe"],
                                          oos_net=m2.get("net_pct", 0), oos_dd=m2.get("maxdd", 0),
                                          oos_trades=m2["trades"]))
    cands.sort(key=lambda c: c["robust"], reverse=True)
    return cands


def build_views(d, cand, sigma_map, lot, lots, sig):
    sig_name, struct, params = cand["sig_name"], cand["struct"], cand["params"]
    base = osx.backtest_structure(d, sig_name, struct, params, sigma_map, lot, lots, charges=False)
    rms = osx.backtest_structure(d, sig_name, struct, params, sigma_map, lot, lots, charges=False, rms_caps=RMS_CAPS)
    bsr = osx.backtest_structure(d, sig_name, struct, params, sigma_map, lot, lots, charges=True, rms_caps=RMS_CAPS)
    bs_fees = round(sum(float(t.get("fee", 0) or 0) for t in bsr["trades"]), 0)
    return {
        "instrument": _combo_from_res(base, d, _struct_trades(base), sig, fees_override=0),
        "rms":        _combo_from_res(rms, d, _struct_trades(rms), sig, fees_override=0),
        "bs":         _combo_from_res(bsr, d, _struct_trades(bsr), sig, fees_override=bs_fees),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15m,5m")
    ap.add_argument("--trials", type=int, default=120)
    ap.add_argument("--name", default="")
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--nperm", type=int, default=500)
    ap.add_argument("--screen", action="store_true")
    args = ap.parse_args()
    tfs = [x.strip() for x in args.tf.split(",") if x.strip()]

    t0 = time.time()
    df1m = ie.load_1m()
    cache = {tf: ie.resample(df1m, tf) for tf in tfs}
    dd = (cache[tfs[0]].set_index("Datetime").resample("1D")
          .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sigma_map = bs.realised_vol_map(dd.Close)
    lot = bs.get_nifty_lot()

    ranked = screen(cache, tfs, sigma_map, lot)
    if not ranked:
        print("no structure passed the screen"); return
    if args.screen:
        return

    print("\nOPTIMIZE + significance on top screened structures:", flush=True)
    winner = None
    for cand in ranked[:4]:
        d = cache[cand["tf"]]
        cs = optimize(d, cand["sig_name"], cand["struct"], sigma_map, lot, trials=args.trials)
        if not cs:
            print(f"  {cand['struct']}/{cand['sig_name']}/{cand['tf']}: no opt candidate"); continue
        c = cs[0]
        real, p, n95 = osx.sig_test(d, cand["sig_name"], cand["struct"], c["params"],
                                    sigma_map, lot, args.lots, n_perm=args.nperm)
        ok = p < 0.05
        print(f"  {STRUCT_TITLE[cand['struct']]:15s}{SIG_TITLE[cand['sig_name']]:14s}/{cand['tf']}: "
              f"robust={c['robust']:.2f} (tr {c['train_sharpe']:.2f}/oos {c['oos_sharpe']:.2f}) "
              f"sharpe={real:.2f} p={p:.3f} {'*** SIGNIFICANT ***' if ok else 'not sig'}", flush=True)
        if ok and winner is None:
            winner = dict(sig_name=cand["sig_name"], struct=cand["struct"], tf=cand["tf"],
                          params=c["params"], robust=c["robust"],
                          train_sharpe=c["train_sharpe"], oos_sharpe=c["oos_sharpe"],
                          sig=dict(real_sharpe=round(real, 3), p_value=round(p, 4),
                                   null_p95=round(n95, 3), null_mean=0.0, n_perm=args.nperm,
                                   significant=True),
                          opt=cs[:8])
    if winner is None:
        print("\nNo structure passed significance (p<0.05). Honest result: no edge to ship.")
        return

    slug = args.name or f"{winner['struct']}_{winner['sig_name']}_{winner['tf']}"
    write_run(slug, winner, cache[winner["tf"]], dd, sigma_map, lot, args.lots)
    print(f"\ndone in {time.time()-t0:.0f}s — open runs/{slug}/index.html", flush=True)


def write_run(slug, winner, d, dd, sigma_map, lot, lots=1):
    """Build 3-pass x 3-period combos for a structure winner and write runs/<slug>/.
    Shared by main() and one-off builders (e.g. verdict follow-ups)."""
    struct, sig_name, tf = winner["struct"], winner["sig_name"], winner["tf"]
    title = f"{STRUCT_TITLE[struct]} @ {SIG_TITLE[sig_name]}"
    print(f"\nWINNER: {title} — NIFTY {tf}  {winner['params']}", flush=True)

    dtr, doos = split(d)
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]

    combos = {}
    for period, dp in (("full", d), ("train", dtr), ("oos", doos)):
        views = build_views(dp, winner, sigma_map, lot, lots, dict(winner["sig"]))
        for pas, cobj in views.items():
            combos[f"{pas}|{period}"] = cobj

    opt_table = [{"params": c["params"], "train_sharpe": round(c["train_sharpe"], 2),
                  "oos_sharpe": round(c["oos_sharpe"], 2), "net": round(c["oos_net"], 1),
                  "dd": round(c["oos_dd"], 1), "trades": c["oos_trades"]} for c in winner.get("opt", [])]
    for pas in ("instrument", "rms", "bs"):
        combos[f"{pas}|full"]["opt_table"] = opt_table

    dna = {**winner["params"], "structure": struct, "signal": sig_name}
    for k in combos:
        combos[k]["dna"] = dna

    out = {"meta": {
        "window": [str(d.Datetime.iloc[0])[:10], str(d.Datetime.iloc[-1])[:10]],
        "days": int(d.day.nunique()), "start_cap": engine.START_CAP,
        "design": f"{title} — NIFTY {tf} (option structure, BS premium)",
        "design_key": f"{struct}/{sig_name}", "slug": slug, "tf": tf,
        "instrument": "NIFTY 50 options", "lot_size": lot, "lots": lots,
        "rms_caps": RMS_CAPS, "dna": dna, "structure": struct, "signal": sig_name,
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
        "candles": candles},
        "dna_by_combo": {k: dna for k in combos},
        "combos": combos}

    trade_dates = {t["entry_dt"][:10] for c in combos.values() for t in c.get("all_trades", [])}
    try:
        from add_intraday import build_intraday
        out["meta"]["intraday"] = build_intraday("nifty_1min.csv", trade_dates)
        print(f"  intraday: {len(out['meta']['intraday'])} trade-days (5-min)", flush=True)
    except Exception as e:
        print(f"  [intraday] skipped ({e})", flush=True)

    folder = os.path.join(RUNS, slug)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash)
    meta = {"slug": slug, "design": f"{struct}/{sig_name}", "title": title,
            "tf": tf, "params": winner["params"], "exit": f"tp{winner['params']['tp_frac']}/sl{winner['params']['sl_frac']}",
            "instrument": "NIFTY 50 options", "lot_size": lot,
            "window": out["meta"]["window"], "days": out["meta"]["days"], "significant": True,
            "bs_full": {k: combos["bs|full"]["metrics"].get(k) for k in
                        ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "p_value": winner["sig"]["p_value"], "created": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != slug]
    idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)

    print(f"\nwrote runs/{slug}/  (results.js + index.html + meta.json)", flush=True)
    for pas in ("instrument", "rms", "bs"):
        m = combos[f"{pas}|full"]["metrics"]
        print(f"  {pas:11s} sharpe={m['sharpe']:.2f} net={m['net_pct']:.1f}% "
              f"maxDD={m['maxdd']:.1f}% win={m['win_rate']:.0f}% trades={m['trades']} fees={m.get('fees',0):.0f}", flush=True)


if __name__ == "__main__":
    main()
