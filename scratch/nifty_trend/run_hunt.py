"""Fresh NIFTY intraday strategy hunt -> 3-PASS results, saved per-strategy.

Passes (each a separate, inspectable layer — the user's "instrument > RMS >
Black-Scholes" funnel):
    (1) instrument : raw signal on NIFTY spot, spot-notional P&L, no daily caps
    (2) rms        : same trades + account daily loss/profit caps
    (3) bs         : pass-2 trades repriced into ATM CE/PE premium (Black-Scholes,
                     delta+theta, real Zerodha F&O charges) = the deployable truth

Flow: screen all designs x TFs -> optimize the best (rank by min(train,OOS),
TRAP #103) -> significance gate (p<0.05) -> build 3 passes x 3 periods
(full/train/oos) -> Monte Carlo -> write runs/<slug>/results.js + meta.json so
every strategy lives in its OWN folder (easy to find later) and renders in the
SAME dashboard (dashboard_intraday.html) via the Pass toggle.

Usage:  python run_hunt.py [--tf 15m,5m,3m] [--trials 300] [--name my_strat]
"""
import os
import sys
import json
import time
import shutil
import argparse
import numpy as np
import pandas as pd

import engine
import intraday_engine as ie
import bs_option as bs
from montecarlo import montecarlo
from intraday_optimize import optimize, sig_test, split
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
N_DS = 400                       # equity downsample points

# spot-notional daily RMS caps for pass (2). Illustrative on the underlying;
# for LIVE the per-strategy caps are set at OPTION scale (two-stage, TRAP #104).
RMS_CAPS = dict(loss_cap=0.02 * engine.START_CAP, profit_target=0.03 * engine.START_CAP)

DESIGN_TITLE = {
    "rsi_rev": "RSI Mean-Reversion", "bb_fade": "Bollinger Fade",
    "sess_rev": "Session Z-Reversion", "orb": "Opening-Range Breakout",
    "donchian": "Donchian Breakout", "supertrend": "Supertrend Flip",
    "orb_st": "ORB + Supertrend", "gap_fade": "Gap Fade", "tod_orb": "Mid-Day ORB",
}


# ---------- screen every design x TF, rank by robustness ----------
def screen(cache, tfs):
    print("SCREEN — all designs x TFs (default params, 1x)\n", flush=True)
    rows = []
    for tf in tfs:
        d = cache[tf]
        dtr, doos = split(d)
        for name in ie.DESIGN_GRID:
            m1, _ = engine.metrics(ie.backtest(dtr, name))
            m2, _ = engine.metrics(ie.backtest(doos, name))
            if m1["trades"] < 40 or m2["trades"] < 20:
                continue
            robust = min(m1.get("sharpe", -9), m2.get("sharpe", -9))
            rows.append(dict(design=name, tf=tf, robust=robust,
                             train=m1["sharpe"], oos=m2["sharpe"], trades=m2["trades"]))
            print(f"  {name:11s}{tf:4s} robust={robust:5.2f} "
                  f"(tr {m1['sharpe']:.2f} / oos {m2['sharpe']:.2f})  tr={m2['trades']}", flush=True)
    rows.sort(key=lambda r: r["robust"], reverse=True)
    return rows


# ---------- one pass -> combo object the dashboard reads ----------
def _combo_from_res(res, d, all_trades, sig, fees_override=None):
    m, tr = engine.metrics(res)
    if fees_override is not None:
        m["fees"] = fees_override
    eq = res["equity"]
    eq_ds = downsample(list(eq.equity), N_DS)
    bh = d.set_index("Datetime").Close.reindex(pd.to_datetime(eq.Datetime)).ffill().values
    bh = engine.START_CAP * bh / bh[0]
    mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}
    return {
        "metrics": md,
        "equity": eq_ds, "benchmark": downsample(list(bh), N_DS),
        "labels": dates_labels(eq, N_DS),
        "underwater": downsample(m.get("underwater", []), N_DS),
        "worst_periods": worst_periods(m.get("underwater", [])),
        "monthly": monthly_returns(eq),
        "significance": sig,
        "mc": None if mc is None else {
            "table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
            "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
            "orig_path": downsample(mc["orig_path"], 120)},
        "all_trades": all_trades,
        "trades": all_trades[-10:],
        "opt_table": [],
    }


def _spot_trades(res):
    """all_trades[] for the spot passes (instrument / rms)."""
    out = []
    for t in res["trades"]:
        qty, pts, pnl = float(t.get("qty", 0)), float(t["points"]), float(t["pnl"])
        gross = pts * qty
        out.append({
            "side": t["side"], "entry_dt": str(pd.to_datetime(t["entry_dt"]))[:16],
            "exit_dt": str(pd.to_datetime(t["exit_dt"]))[:16],
            "entry": round(float(t["entry"]), 1), "exit": round(float(t["exit"]), 1),
            "entry_spot": round(float(t["entry"]), 1), "exit_spot": round(float(t["exit"]), 1),
            "points": round(pts, 1), "qty": round(qty, 1),
            "gross": round(gross, 0), "fee": round(gross - pnl, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", "")})
    return out


def _bs_res_and_trades(rms_res, d, sigma_map, lot, lots):
    """Pass 3: reprice the RMS trade set into ATM option premium P&L."""
    opt = bs.reprice(rms_res["trades"], sigma_map, lot, lots)
    # engine-shaped trades so engine.metrics() works on the option P&L
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
    # dashboard all_trades: spot levels for the table/chart + option fields
    at = []
    for o in opt:
        o = dict(o); o["entry"] = o["entry_spot"]; o["exit"] = o["exit_spot"]
        o.pop("entry_i", None); o.pop("exit_i", None)
        at.append(o)
    fees = round(sum(o["fee"] for o in opt), 0)
    return bs_res, at, fees


def build_views(d, winner, sigma_map, lot, lots, sig):
    base = ie.backtest(d, winner["design"], winner["params"], winner["exit"])
    rms = ie.backtest(d, winner["design"], winner["params"], winner["exit"], rms_caps=RMS_CAPS)
    bs_res, bs_at, bs_fees = _bs_res_and_trades(rms, d, sigma_map, lot, lots)
    return {
        "instrument": _combo_from_res(base, d, _spot_trades(base), sig),
        "rms":        _combo_from_res(rms, d, _spot_trades(rms), sig),
        "bs":         _combo_from_res(bs_res, d, bs_at, sig, fees_override=bs_fees),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15m,5m,3m")
    ap.add_argument("--trials", type=int, default=300)
    ap.add_argument("--name", default="")
    ap.add_argument("--lots", type=int, default=1)
    args = ap.parse_args()
    tfs = [x.strip() for x in args.tf.split(",") if x.strip()]

    t0 = time.time()
    df1m = ie.load_1m()
    cache = {tf: ie.resample(df1m, tf) for tf in tfs}

    ranked = screen(cache, tfs)
    if not ranked:
        print("no design passed the screen"); return

    print("\nOPTIMIZE + significance on top screened designs:", flush=True)
    winner = None
    for cand in ranked[:4]:
        d = cache[cand["tf"]]
        cs = optimize(d, cand["design"], trials=args.trials)
        if not cs:
            print(f"  {cand['design']}/{cand['tf']}: no optimize candidate"); continue
        c = cs[0]
        real, p, n95 = sig_test(d, cand["design"], c["params"], c["exit"], n_perm=1000)
        sig_ok = p < 0.05
        print(f"  {cand['design']:10s}/{cand['tf']}: robust={c['robust']:.2f} "
              f"(tr {c['train_sharpe']:.2f}/oos {c['oos_sharpe']:.2f}) "
              f"entry_sharpe={real:.2f} p={p:.3f} {'*** SIGNIFICANT ***' if sig_ok else 'not sig'}",
              flush=True)
        if sig_ok and winner is None:
            winner = dict(design=cand["design"], tf=cand["tf"], exit=c["exit"],
                          params=c["params"], robust=c["robust"],
                          train_sharpe=c["train_sharpe"], oos_sharpe=c["oos_sharpe"],
                          sig=dict(real_sharpe=round(real, 3), p_value=round(p, 4),
                                   null_p95=round(n95, 3), null_mean=0.0,
                                   n_perm=1000, significant=True),
                          opt=cs[:8])
    if winner is None:
        print("\nNo design passed significance (p<0.05). Honest result: no edge to ship.")
        return

    design, tf = winner["design"], winner["tf"]
    slug = args.name or f"{design}_{tf}"
    print(f"\nWINNER: {DESIGN_TITLE.get(design, design)} @ {tf}  {winner['params']}", flush=True)

    d = cache[tf]
    dtr, doos = split(d)
    lot = bs.get_nifty_lot()
    dd = (d.set_index("Datetime").resample("1D")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sigma_map = bs.realised_vol_map(dd.Close)
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]

    sig_full = winner["sig"]
    sig_tr = dict(sig_full); sig_oos = dict(sig_full)  # entry-edge test on full window; label per period
    print("building 3 passes x 3 periods ...", flush=True)
    combos = {}
    for period, dp, sg in (("full", d, sig_full), ("train", dtr, sig_tr), ("oos", doos, sig_oos)):
        views = build_views(dp, winner, sigma_map, lot, args.lots, sg)
        for pas, cobj in views.items():
            combos[f"{pas}|{period}"] = cobj

    # optimize sweep table on the BS/full view (deployable) — ranked by robust
    opt_table = [{"params": c["params"], "exit": c["exit"],
                  "train_sharpe": round(c["train_sharpe"], 2), "oos_sharpe": round(c["oos_sharpe"], 2),
                  "net": round(c["oos_net"], 1), "dd": round(c["oos_dd"], 1),
                  "trades": c["oos_trades"]} for c in winner["opt"]]
    for pas in ("instrument", "rms", "bs"):
        combos[f"{pas}|full"]["opt_table"] = opt_table

    dna = {**winner["params"], "exit": winner["exit"]}
    out = {"meta": {
        "window": [str(d.Datetime.iloc[0])[:10], str(d.Datetime.iloc[-1])[:10]],
        "days": int(d.day.nunique()), "start_cap": engine.START_CAP,
        "design": f"{DESIGN_TITLE.get(design, design)} — NIFTY {tf} (ATM options)",
        "design_key": design, "slug": slug, "tf": tf,
        "instrument": "NIFTY 50", "lot_size": lot, "lots": args.lots,
        "rms_caps": RMS_CAPS, "dna": dna,
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
        "candles": candles},
        "dna_by_combo": {k: dna for k in combos},
        "combos": combos}
    # dna is per-combo in the schema; keep a top-level for the DNA panel
    for k in combos:
        combos[k]["dna"] = dna

    # FAQ (user request 2026-07-11) — reusable Q&A in the dashboard's Philosophy panel
    try:
        from faq_lib import build_faq
        out["meta"]["sig_p"] = f'{winner["sig"]["p_value"]:.3f}'
        out["meta"]["faq"] = build_faq(out["meta"])
    except Exception as e:
        print(f"  [faq] skipped ({e})", flush=True)

    # 5-min intraday candles per trade-day → trade-chart shows real entry/exit intraday
    trade_dates = {t["entry_dt"][:10] for c in combos.values() for t in c.get("all_trades", [])}
    try:
        from add_intraday import build_intraday
        out["meta"]["intraday"] = build_intraday("nifty_1min.csv", trade_dates)
        print(f"  intraday: {len(out['meta']['intraday'])} trade-days (5-min)", flush=True)
    except Exception as e:
        print(f"  [intraday] skipped ({e})", flush=True)

    # per-trade indicator overlays → trade-chart shows WHY the signal fired (levels used)
    try:
        from add_overlays import build_overlays
        _seen, _tr = set(), []
        for c in combos.values():
            for t in c.get("all_trades", []):
                if t["entry_dt"] not in _seen:
                    _seen.add(t["entry_dt"]); _tr.append(t)
        _ov, _spec = build_overlays(design, dict(out["meta"].get("dna") or {}),
                                    tf, _tr, "nifty_1min.csv")
        out["meta"]["overlays"] = _ov
        if _spec:
            out["meta"]["overlay_spec"] = _spec
        print(f"  overlays: {len(_ov)} trade-overlays", flush=True)
    except Exception as e:
        print(f"  [overlays] skipped ({e})", flush=True)

    # ---- write to the strategy's OWN folder ----
    folder = os.path.join(RUNS, slug)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    # self-contained: each run folder gets its own copy of the dashboard, pointing at ./results.js
    dash_src = os.path.join(HERE, "dashboard_intraday.html")
    dash = open(dash_src, encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash)
    meta = {"slug": slug, "design": design, "title": DESIGN_TITLE.get(design, design),
            "tf": tf, "params": winner["params"], "exit": winner["exit"],
            "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": out["meta"]["days"],
            "significant": True,
            "bs_full": {k: combos["bs|full"]["metrics"].get(k) for k in
                        ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "p_value": winner["sig"]["p_value"], "created": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # append to a runs index the hub reads
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != slug]
    idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)

    # refresh the all-strategies compare table (compare.html reads runs/compare.json)
    try:
        import build_compare
        build_compare.main()
    except Exception as e:
        print(f"  [compare] skipped ({e})", flush=True)

    print(f"\nwrote runs/{slug}/  (results.js + index.html + meta.json)", flush=True)
    for pas in ("instrument", "rms", "bs"):
        m = combos[f"{pas}|full"]["metrics"]
        print(f"  {pas:11s} sharpe={m['sharpe']:.2f} net={m['net_pct']:.1f}% "
              f"maxDD={m['maxdd']:.1f}% win={m['win_rate']:.0f}% fees={engine.START_CAP and m.get('fees',0):.0f}",
              flush=True)
    print(f"\ndone in {time.time()-t0:.0f}s — open /lab/runs/{slug}/index.html", flush=True)


if __name__ == "__main__":
    main()
