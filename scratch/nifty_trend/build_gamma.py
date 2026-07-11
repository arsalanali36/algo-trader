"""#07 Gamma Scalping — 3-pass dashboard producer (documents an HONEST REJECTION).

Gamma scalping is the structural LOSER of the variance-risk-premium that #06 harvests:
on REAL data it loses ~-150% GROSS (before costs) — bought implied vol > realized vol
delivered. Every entry design (all / iv_low cheap-gamma / expiry 0DTE-blast) fails
decisively. Per the mission's gate ("Fail = luck/beta — DO NOT ship"), this is NOT
deployed; the dashboard + comparison table record it as a validated NEGATIVE so the
result is inspectable and the search doesn't re-tread it.

Usage: python build_gamma.py [--sig all] [--name gamma_scalp]
"""
import os, json, time, argparse
import numpy as np, pandas as pd
import engine, bs_option as bs, optlake_load as lake, gamma_scalp as gs
from montecarlo import montecarlo
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__)); RUNS = os.path.join(HERE, "runs"); N_DS = 400
RMS_CAPS = dict(loss_cap=0.02 * engine.START_CAP, profit_target=0.03 * engine.START_CAP)


def _trades(res):
    out = []
    for t in res["trades"]:
        fee = float(t.get("fee", 0) or 0); pnl = float(t["pnl"])
        out.append({"side": "long-gamma", "opt_type": "STRADDLE+HEDGE", "strike": t.get("atm"),
            "entry_dt": str(pd.to_datetime(t["entry_dt"]))[:16], "exit_dt": str(pd.to_datetime(t["exit_dt"]))[:16],
            "entry_spot": t.get("spot_in"), "exit_spot": t.get("spot_out"), "points": round(float(t["points"]), 2),
            "entry_prem": t.get("entry_prem"), "exit_prem": t.get("exit_prem"), "qty": int(t["qty"]),
            "gross": round(pnl + fee, 0), "fee": round(fee, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", "")})
    return out


def _combo(res, m, sig):
    mt, _ = engine.metrics(res); eq = res["equity"]
    eq_ds = downsample(list(eq.equity), N_DS)
    bh = m.set_index("Datetime").spot.reindex(pd.to_datetime(eq.Datetime)).ffill().values
    bh = engine.START_CAP * bh / bh[0]
    mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in mt.items()
          if k not in ("daily_returns", "underwater", "params")}
    return {"metrics": md, "equity": eq_ds, "benchmark": downsample(list(bh), N_DS),
            "labels": dates_labels(eq, N_DS), "underwater": downsample(mt.get("underwater", []), N_DS),
            "worst_periods": worst_periods(mt.get("underwater", [])), "monthly": monthly_returns(eq),
            "significance": sig, "mc": None if mc is None else {"table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
                "paths": [downsample(pp, 120) for pp in mc["paths"][:60]], "orig_path": downsample(mc["orig_path"], 120)},
            "all_trades": _trades(res), "trades": _trades(res)[-10:], "opt_table": []}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--sig", default="all"); ap.add_argument("--name", default="gamma_scalp")
    args = ap.parse_args()
    t0 = time.time(); lot = bs.get_nifty_lot()
    m = lake.atm_frame("WEEK", "5m"); m["Close"] = m["spot"]
    params = dict(h0=10, hedge_band=0.15, hedge_slip=0.5)
    k = int(len(m) * 0.68); mtr, moos = m.iloc[:k].reset_index(drop=True), m.iloc[k:].reset_index(drop=True)
    mrec = m[m.Datetime >= pd.Timestamp("2023-01-01")].reset_index(drop=True)

    # honest significance: daily-edge bootstrap (mean>0?). For a loser this is ~1.0 (fails).
    res = gs.backtest(m, args.sig, params, lot=lot, charges=True)
    by = {}
    for t in res["trades"]:
        by[str(pd.to_datetime(t["entry_dt"]))[:10]] = by.get(str(pd.to_datetime(t["entry_dt"]))[:10], 0.0) + float(t["pnl"])
    dp = np.array(list(by.values())); rng = np.random.default_rng(7)
    boots = np.array([rng.choice(dp, len(dp), replace=True).mean() for _ in range(2000)]) if len(dp) else np.array([0.0])
    p_edge = float((boots <= 0).mean())
    sig = dict(real_sharpe=round(engine.metrics(res)[0].get("sharpe", 0.0), 3), p_value=round(p_edge, 4),
               null_p95=0.0, null_mean=0.0, n_perm=2000, significant=False,
               note="REJECTED. Gamma scalping loses ~-150%% GROSS (before costs) on real data — the "
                    "structural loser of the VRP that #06 harvests (implied vol paid > realized delivered). "
                    "edge-p = P(mean daily P&L <= 0); ~1.0 = no positive edge. Every design (all/iv_low/expiry) fails.")

    dd = (m.set_index("Datetime").resample("1D").agg({"spot": ["first", "max", "min", "last"]}).dropna())
    dd.columns = ["Open", "High", "Low", "Close"]
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)] for ix, r in dd.iterrows()]

    def views(fr):
        base = gs.backtest(fr, args.sig, {**params}, lot=lot, charges=False)
        rms = gs.backtest(fr, args.sig, {**params}, lot=lot, charges=False, rms_caps=RMS_CAPS)
        dep = gs.backtest(fr, args.sig, params, lot=lot, charges=True, rms_caps=RMS_CAPS)
        return {"instrument": _combo(base, fr, sig), "rms": _combo(rms, fr, sig), "bs": _combo(dep, fr, sig)}

    combos = {}
    for period, fr in (("full", m), ("train", mtr), ("oos", moos), ("recent", mrec)):
        for pas, c in views(fr).items():
            combos[f"{pas}|{period}"] = c
    dna = {"structure": "long straddle + delta-hedge", "entry": "10:00 daily", "hedge_band": 0.15, "signal": args.sig}
    for kk in combos:
        combos[kk]["dna"] = dna

    FAQ = [
        ["Ye pass hui ya fail?",
         "<b>FAIL — deploy NAHI hui.</b> Gamma scalping real data pe <b>~-150% GROSS</b> (charges se pehle bhi) "
         "haarti hai. Ye #06 short-vol ka structural ULTA hai: aap jo implied vol khareedte ho wo realized se "
         "zyada nikalti hai (VRP). Mission rule: fail = ship mat karo. Documented as honest negative."],
        ["Gamma scalping hoti kya hai?",
         "ATM straddle <b>BUY</b> (long gamma) + din bhar underlying se <b>delta-hedge</b> — realized-vol se "
         "chhote-chhote scalp. Profit tabhi jab REALIZED vol > IMPLIED vol (jo aapne paid ki) + hedging cost. "
         "Real premium (dV) + BS-delta hedge, real charges se test kiya."],
        ["Koi design bachi jo chali?",
         "Nahi. all −223%, iv_low (cheapest gamma) −25% (Sh −13), <b>expiry 0DTE 'gamma blast' −160%</b> "
         "(train + par OOS −27 = pure lottery/noise). 7+ designs — sab decisively negative."],
        ["Fir ye result kaam ka kyun?",
         "Do wajah: (1) ye #06 (+61%) ke saath internally consistent hai — same data proves implied>realized "
         "systematically, jo confirm karta hai data + engines HONEST hain (koi fabricated edge nahi). "
         "(2) Ab pata hai NIFTY pe long-vol/gamma-scalp intraday nahi chalta — search dobara yahan time waste nahi karegi."],
    ]

    out = {"meta": {"window": [str(m.Datetime.iloc[0])[:10], str(m.Datetime.iloc[-1])[:10]],
        "days": int(m.day.nunique()), "start_cap": engine.START_CAP,
        "design": f"Gamma Scalping (long straddle + delta-hedge) — NIFTY 5m REAL — ❌ REJECTED (VRP loser)",
        "design_key": "gamma_scalp", "slug": args.name, "tf": "5m", "instrument": "NIFTY 50 options",
        "lot_size": lot, "lots": 1, "rms_caps": RMS_CAPS, "dna": dna, "sig_p": str(sig["p_value"]),
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos", "recent"],
        "philosophy": ("<b>Gamma Scalping (REAL data) — REJECTED.</b> Buy the ATM straddle (long gamma), "
            "delta-hedge intraday, harvest realized vol. The bet: realized vol > implied vol paid. On 5yr of "
            "REAL premium+IV it loses ~-150% even GROSS — the structural loser of the variance-risk-premium "
            "that #06 short-vol harvests (+61%). No entry design (cheap-IV, expiry 0DTE blast) flips it. Kept "
            "as a validated negative: internally consistent with #06 (data is honest) and a signpost not to "
            "re-hunt intraday long-vol on NIFTY."),
        "faq": FAQ, "candles": candles}, "dna_by_combo": {kk: dna for kk in combos}, "combos": combos}

    folder = os.path.join(RUNS, args.name); os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "results.js"), "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)
    mfull = combos["bs|full"]["metrics"]
    meta = {"slug": args.name, "design": "gamma_scalp", "title": "Gamma Scalping ❌ REJECTED (VRP loser)",
        "tf": "5m", "params": dna, "exit": "EOD 3:15", "instrument": "NIFTY 50 options", "lot_size": lot,
        "window": out["meta"]["window"], "days": out["meta"]["days"], "significant": False,
        "bs_full": {kk: mfull.get(kk) for kk in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
        "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
        "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
        "p_value": sig["p_value"], "created": time.strftime("%Y-%m-%d %H:%M")}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    idx_path = os.path.join(RUNS, "index.json"); idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != args.name]; idx.append(meta); json.dump(idx, open(idx_path, "w"), indent=2)
    try:
        import build_compare; build_compare.main()
    except Exception as e:
        print(f"  [compare] skipped ({e})", flush=True)
    print(f"\nwrote runs/{args.name}/  (REJECTED — significant:false)", flush=True)
    for pas in ("instrument", "rms", "bs"):
        mm = combos[f"{pas}|full"]["metrics"]
        print(f"  {pas:11s} sharpe={mm['sharpe']:.2f} net={mm['net_pct']:.1f}% trades={mm['trades']}", flush=True)
    print(f"done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
