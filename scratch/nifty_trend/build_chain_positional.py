"""Chain-Zone POSITIONAL — 3-pass dashboard build (runs/chain_zone_positional/).

Winner from opt_positional.py (2026-07-11): 15m, exit=stop_only,
touch_tol=0, zone_age=2, max_cs=60, hawa=False, chain_lookback=10,
atr_sl=1.5, max_hold_days=3 — robust 1.14 (train 1.14 / OOS 1.44), p=0.008.

Passes:
  instrument = positional spot P&L (gap-aware stops, multi-day holds, 1x)
  rms        = + MONTHLY loss-cap/profit-target overlay (positional cadence)
  bs         = pass-2 trades repriced into MONTHLY ATM CE/PE BUY premium
               (bs_option.reprice_positional — weekly options bleed theta multi-day)
"""
import os
import json
import time
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import intraday_engine as ie
import positional_engine as pe
import run_hunt as rh
from intraday_optimize import split

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SLUG = "chain_zone_positional"
TITLE = "Auto Rev-Chain — Zone Breakout POSITIONAL (monthly ATM long option)"

TF = "15m"
PARAMS = dict(touch_tol=0.0, zone_age=2, max_cs=60.0, hawa=False,
              chain_lookback=10, atr_sl=1.5, rr=2.0, max_hold_days=3)
EXIT = "stop_only"
SIG = dict(real_sharpe=0.90, p_value=0.008, null_p95=0.49, null_mean=0.0,
           n_perm=500, significant=True)
# monthly RMS overlay (illustrative, positional cadence): 4% loss cap / 6% target
RMS_CAPS = dict(loss_cap=0.04 * engine.START_CAP, profit_target=0.06 * engine.START_CAP)


def _bs_pack(rms_res, d, sigma_map, lot, lots=1):
    opt = bs.reprice_positional(rms_res["trades"], sigma_map, lot, lots)
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
                  variant=rms_res["variant"], mode="positional", params=rms_res["params"])
    at = []
    for o in opt:
        o = dict(o); o["entry"] = o["entry_spot"]; o["exit"] = o["exit_spot"]
        o.pop("entry_i", None); o.pop("exit_i", None)
        at.append(o)
    fees = round(sum(o["fee"] for o in opt), 0)
    return bs_res, at, fees


def views(dp, sigma_map, lot, sig):
    base = pe.backtest_positional(dp, "chain_zone", PARAMS, EXIT)
    rms = pe.backtest_positional(dp, "chain_zone", PARAMS, EXIT, rms_caps=RMS_CAPS)
    bs_res, bs_at, bs_fees = _bs_pack(rms, dp, sigma_map, lot)
    return {
        "instrument": rh._combo_from_res(base, dp, rh._spot_trades(base), sig),
        "rms":        rh._combo_from_res(rms, dp, rh._spot_trades(rms), sig),
        "bs":         rh._combo_from_res(bs_res, dp, bs_at, sig, fees_override=bs_fees),
    }


FAQ = [
 ["Ye positional version intraday se kaise alag hai?",
  "Same Chain-Zone entry (lines→pattern-zone→breakout), lekin: <b>koi 3:15 force-exit nahi</b> — "
  "position overnight/multi-day hold hoti hai (max 3 din), koi max-2/day nahi. Overnight GAP "
  "realistically model kiya hai: gap stop ke paar khule to fill <b>next OPEN pe</b> hota hai "
  "(stop se worse) — backtest cheat nahi karta. Option bhi alag: <b>MONTHLY ATM</b> buy karte "
  "hain (weekly multi-day hold me theta se mar jaati)."],
 ["StopLoss kya hai? Overnight gap me kya hoga?",
  "Stop = entry ∓ <b>1.5×ATR(14)</b> (15m). Intraday me spot stop pe hi cut. Overnight gap stop "
  "ke paar khule to next open pe exit — is 'Gap SL' ka extra loss backtest me included hai. "
  "Upar se option BUY hai to premium se zyada kabhi nahi ja sakta, chahe gap kitna bhi bada ho — "
  "positional me yahi option-buy ka sabse bada फायदा hai (futures me gap unlimited hota)."],
 ["Winner params kya hain aur intraday se kyun alag?",
  "<b>touch_tol=0</b> (exact line touch — intraday me 5 tha), <b>max_cs=60</b>, "
  "<b>chain_lookback=10</b> din, <b>max_hold=3 din</b>, stop <b>1.5×ATR</b> (tighter), exit "
  "stop_only (no target — winners ride till stop/3-din). Multi-day edge chhote stop + strict "
  "line-touch pe hi survive karta hai; loose touch wale zones overnight noise me fail hue."],
 ["Ye edge asli hai? (significance)",
  "500-permutation rotation test: <b>p=0.008</b> (15m; 30m pe bhi p=0.002 independently pass). "
  "Train Sharpe 1.14 vs OOS <b>1.44</b> — recent regime me behtar, decay nahi. Spot pass "
  "+34% net, DD −7.7%, 251 trades."],
 ["Monthly option hi kyun, weekly kyun nahi?",
  "3-din ki hold me weekly ATM apna bada hissa theta me kho deta hai (expiry ke paas decay "
  "sabse tez). Monthly ATM ka theta/din bahut kam hai — delta ka फायदा milta hai, decay ka "
  "nuksaan chhota. Isliye pass-③ MONTHLY ATM CE/PE ke Black-Scholes premium pe hai "
  "(monthly-expiry calendar official NSE circulars se, Thu→Tue Sep-2025)."],
 ["RMS pass yahan kya karta hai?",
  "Intraday wale DAILY caps positional pe fit nahi hote — yahan <b>MONTHLY</b> overlay hai: "
  "mahine ka loss ₹40,000 (4%) cross → squareoff + us mahine entry band; profit ₹60,000 (6%) "
  "cross → lock. Agla mahina fresh."],
 ["Selling (PE/CE sell + hedge) positional me kyun nahi test kiya is run me?",
  "Ye run BUY-form validate karta hai (winners uncapped — intraday me proven pattern). "
  "Positional SELL+hedge alag structure hai (theta aapke saath, par gap-risk hedge pe depend) — "
  "wo separate backtest hoga, is run ke baad."],
]


def main():
    t0 = time.time()
    d = ie.resample(ie.load_1m(), TF)
    dtr, doos = split(d)
    lot = bs.get_nifty_lot()
    dd = (d.set_index("Datetime").resample("1D")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sigma_map = bs.realised_vol_map(dd.Close)
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]

    print("building 3 passes x 3 periods (positional) ...", flush=True)
    combos = {}
    for period, dp in (("full", d), ("train", dtr), ("oos", doos)):
        for pas, cobj in views(dp, sigma_map, lot, SIG).items():
            combos[f"{pas}|{period}"] = cobj

    dna = {**PARAMS, "exit": EXIT}
    for k in combos:
        combos[k]["dna"] = dna
    out = {"meta": {
        "window": [str(d.Datetime.iloc[0])[:10], str(d.Datetime.iloc[-1])[:10]],
        "days": int(d.day.nunique()), "start_cap": engine.START_CAP,
        "design": f"{TITLE} — NIFTY {TF}",
        "design_key": "chain_zone", "slug": SLUG, "tf": TF,
        "instrument": "NIFTY 50", "lot_size": lot, "lots": 1,
        "rms_caps": RMS_CAPS, "dna": dna, "sig_p": "0.008",
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
        "pass_labels": {"bs_desc": "MONTHLY ATM CE/PE BUY, Black-Scholes + real Zerodha tax (positional)"},
        "pass_notes": {
            "instrument": "① Raw positional signal on NIFTY spot — multi-day holds, gap-aware stops, no caps/options.",
            "rms": "② + MONTHLY RMS overlay: month-loss ₹40k → lock till next month; month-profit ₹60k → lock.",
            "bs": "③ Pass-② trades repriced into MONTHLY ATM CE/PE BUY premium (Black-Scholes, monthly expiry calendar, real Zerodha charges) — deployable truth."},
        "faq": FAQ,
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

    folder = os.path.join(RUNS, SLUG)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash)
    meta = {"slug": SLUG, "design": "chain_zone", "title": TITLE,
            "tf": TF, "params": PARAMS, "exit": EXIT,
            "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": out["meta"]["days"],
            "significant": True,
            "bs_full": {k: combos["bs|full"]["metrics"].get(k) for k in
                        ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "p_value": SIG["p_value"], "created": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != SLUG]
    idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)

    print(f"\nwrote runs/{SLUG}/", flush=True)
    for pas in ("instrument", "rms", "bs"):
        m = combos[f"{pas}|full"]["metrics"]
        print(f"  {pas:11s} sharpe={m['sharpe']:.2f} net={m['net_pct']:.1f}% "
              f"maxDD={m['maxdd']:.1f}% win={m['win_rate']:.0f}% trades={m['trades']} "
              f"fees={m.get('fees',0):.0f}", flush=True)
    print(f"done in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
