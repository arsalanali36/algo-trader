"""REBUILD runs/shortvol_ironfly with the HELD-STRIKE engine (real_struct2) — the
CORRECTED, honest numbers after TRAP #109 (rolling-ATM proxy hid intrinsic losses).

Original (buggy) build showed Sharpe 8.9 / +61%; corrected: ~-3.5 / -54%. The run is
kept, re-marked ❌ CORRECTED-REJECTED so the hub/compare records the true result and
the paper deploy stays deactivated.
"""
import os, json, time
import numpy as np, pandas as pd
import engine, bs_option as bs, real_struct2 as r2
from montecarlo import montecarlo
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__)); RUNS = os.path.join(HERE, "runs"); N_DS = 400
RMS_CAPS = dict(loss_cap=0.02 * engine.START_CAP, profit_target=0.03 * engine.START_CAP)
SLUG = "shortvol_ironfly"
P = dict(h0=10, wing_off=8, tp_frac=0.5, sl_frac=1.0, slip_frac=0.005)


def _trades(res):
    out = []
    for t in res["trades"]:
        fee = float(t.get("fee", 0) or 0); pnl = float(t["pnl"])
        out.append({"side": "short-vol", "opt_type": "IRON_FLY(held)", "strike": t.get("atm"),
            "entry_dt": str(pd.to_datetime(t["entry_dt"]))[:16], "exit_dt": str(pd.to_datetime(t["exit_dt"]))[:16],
            "entry_spot": t.get("spot_in"), "exit_spot": t.get("spot_out"), "points": round(float(t["points"]), 2),
            "entry_prem": t.get("entry_prem"), "exit_prem": t.get("exit_prem"), "qty": int(t["qty"]),
            "gross": round(pnl + fee, 0), "fee": round(fee, 0), "pnl": round(pnl, 1),
            "bars": int(t.get("bars", 0)), "reason": t.get("reason", "")})
    return out


def _slice(res, d0=None, d1=None):
    tr = [t for t in res["trades"]
          if (d0 is None or pd.Timestamp(t["entry_dt"]) >= d0) and (d1 is None or pd.Timestamp(t["entry_dt"]) < d1)]
    eq = res["equity"]
    mask = pd.Series(True, index=eq.index)
    if d0 is not None:
        mask &= pd.to_datetime(eq.Datetime) >= d0
    if d1 is not None:
        mask &= pd.to_datetime(eq.Datetime) < d1
    e = eq[mask].reset_index(drop=True).copy()
    if len(e):
        e["equity"] = engine.START_CAP + (e["equity"] - e["equity"].iloc[0])
    return dict(trades=tr, equity=e, final=float(e["equity"].iloc[-1]) if len(e) else engine.START_CAP,
                params=res["params"], variant=res["variant"], mode="intraday")


def _combo(res, sig):
    mt, _ = engine.metrics(res); eq = res["equity"]
    mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in mt.items()
          if k not in ("daily_returns", "underwater", "params")}
    return {"metrics": md, "equity": downsample(list(eq.equity), N_DS),
            "benchmark": downsample(list(eq.equity * 0 + engine.START_CAP), N_DS),
            "labels": dates_labels(eq, N_DS), "underwater": downsample(mt.get("underwater", []), N_DS),
            "worst_periods": worst_periods(mt.get("underwater", [])), "monthly": monthly_returns(eq),
            "significance": sig, "mc": None if mc is None else {"table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
                "paths": [downsample(pp, 120) for pp in mc["paths"][:60]], "orig_path": downsample(mc["orig_path"], 120)},
            "all_trades": _trades(res), "trades": _trades(res)[-10:], "opt_table": []}


def main():
    t0 = time.time(); lot = bs.get_nifty_lot()
    full = r2.backtest("WEEK", "5m", "iron_fly", P, lot=lot, charges=True)
    base = r2.backtest("WEEK", "5m", "iron_fly", {**P, "slip_frac": 0.0}, lot=lot, charges=False)
    rms = r2.backtest("WEEK", "5m", "iron_fly", P, lot=lot, charges=True, rms_caps=RMS_CAPS)
    mt, _ = engine.metrics(full)
    sig = dict(real_sharpe=round(mt.get("sharpe", 0), 3), p_value=1.0, null_p95=0.0, null_mean=0.0,
               n_perm=0, significant=False,
               note="❌ CORRECTED-REJECTED (LESSONS TRAP #109). The original +61%/Sharpe-8.9 build marked "
                    "positions at the ROLLING-ATM premium — i.e. 'whatever the current ATM is worth', not the "
                    "CONTRACT HELD — hiding intrinsic losses whenever spot trended away from the entry strike. "
                    "This corrected build tracks the HELD strikes through the ±10 offset grid: iron-fly ≈ -54%, "
                    "short straddle ≈ -12%, long straddle ≈ -27%, gamma-scalp ≈ -50% — intraday vol trading in "
                    "BOTH directions dies to costs on honest data. Paper deploy was deactivated same day.")
    ds = pd.to_datetime(full["equity"].Datetime)
    kdt = ds.iloc[int(len(ds) * 0.68)]
    combos = {}
    for period, (d0, d1) in (("full", (None, None)), ("train", (None, kdt)), ("oos", (kdt, None))):
        combos[f"instrument|{period}"] = _combo(_slice(base, d0, d1), sig)
        combos[f"rms|{period}"] = _combo(_slice(rms, d0, d1), sig)
        combos[f"bs|{period}"] = _combo(_slice(full, d0, d1), sig)
    dna = {"structure": "iron_fly ±8 (HELD-strike)", "entry": "10:00", "tp": 0.5, "sl": 1.0, "slip": 0.005,
           "note": "corrected engine"}
    for k in combos:
        combos[k]["dna"] = dna
    m = r2.grid("WEEK", "5m")["m"]
    dd = (m.set_index("Datetime").resample("1D").agg({"spot": ["first", "max", "min", "last"]}).dropna())
    dd.columns = ["Open", "High", "Low", "Close"]
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)] for ix, r in dd.iterrows()]
    FAQ = [["Ye strategy pass hui thi na? Ab ❌ kyun?",
            "Pehle build me ek modeling bug tha (TRAP #109): position ko 'aaj ka ATM' premium pe mark kiya "
            "ja raha tha, HELD contract pe nahi — spot trend kare to asli intrinsic loss chhup jata tha. "
            "Corrected (held-strike) engine pe iron-fly <b>−54%</b> hai, +61% nahi. Isi din paper deploy "
            "deactivate kar diya. Ye run ab CORRECTED negative ke roop me documented hai."],
           ["Kya seekha?",
            "(1) Rolling-series data se structure backtest karte waqt HELD strike track karo (±10 offset grid "
            "se exact reconstruct hota hai). (2) Intraday vol-selling AUR vol-buying dono honest data pe costs "
            "se mar jaate hain — VRP gross me seller-favouring hai par charges+slippage se chhota. "
            "(3) #01-05 is bug se unaffected (BS engine held-contract hi price karta hai)."]]
    out = {"meta": {"window": [str(m.Datetime.iloc[0])[:10], str(m.Datetime.iloc[-1])[:10]],
        "days": int(m.Datetime.dt.date.nunique()), "start_cap": engine.START_CAP,
        "design": "Short-Vol Iron-Fly ±8 — ❌ CORRECTED-REJECTED (TRAP #109 held-strike fix)",
        "design_key": "iron_fly/held", "slug": SLUG, "tf": "5m", "instrument": "NIFTY 50 options",
        "lot_size": lot, "lots": 1, "rms_caps": RMS_CAPS, "dna": dna, "sig_p": "1.0",
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
        "philosophy": ("<b>❌ CORRECTED-REJECTED.</b> " + sig["note"]),
        "faq": FAQ, "candles": candles}, "dna_by_combo": {k: dna for k in combos}, "combos": combos}
    folder = os.path.join(RUNS, SLUG); os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "results.js"), "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)
    mfull = combos["bs|full"]["metrics"]
    meta = {"slug": SLUG, "design": "iron_fly/held", "title": "Short-Vol Iron-Fly ❌ CORRECTED (TRAP #109)",
        "tf": "5m", "params": dna, "exit": "tp0.5/sl1.0", "instrument": "NIFTY 50 options", "lot_size": lot,
        "window": out["meta"]["window"], "days": out["meta"]["days"], "significant": False,
        "bs_full": {k: mfull.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
        "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
        "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
        "p_value": 1.0, "created": time.strftime("%Y-%m-%d %H:%M")}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    idx_path = os.path.join(RUNS, "index.json"); idx = json.load(open(idx_path))
    idx = [x for x in idx if x.get("slug") != SLUG]; idx.append(meta); json.dump(idx, open(idx_path, "w"), indent=2)
    try:
        import build_compare; build_compare.main()
    except Exception as e:
        print(f"[compare] {e}")
    print(f"corrected {SLUG}: bs sharpe={mfull['sharpe']} net={mfull['net_pct']}% (was 8.9/+61) — done {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
