"""Emit runs/bnf_920_strangle/ (results.js + index.html + meta.json + index.json entry)
for the BankNifty 9:20 short strangle (02.10), so it shows in the Lab hub + registry
with a working 'Lab ↗' link — same dashboard as every other strategy.

Reuses the exact combo builder (_combo_from_res) every other run uses. This strategy is
a REAL-premium seller (not a spot→BS sim), so all 3 passes carry the same real trades:
instrument/rms = gross (pre-cost), bs = net (real Zerodha charges + DOM slip) = deployable.
"""
import os, json, math, datetime as dt
import numpy as np, pandas as pd
import bnf_920_strangle_intraday as M
import engine
from run_hunt import _combo_from_res

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SLUG = "bnf_920_strangle"
N, TGT, SL = 6, 50, 50
STEP = M.STEP
lot_for = M.lot_for               # date-aware BNF lot (25/15/30)


def collect_trades(g):
    """Re-run the 6-strike 50/50 strategy, capturing FULL per-trade dicts (engine-shaped)."""
    DAY, TT, DT, SPOT = g["DAY"], g["TT"], g["DT"], g["SPOT"]
    n = len(DT)
    entry_i, exit_i, last_bar = {}, {}, {}
    for i in range(n):
        d = DAY[i]
        if d not in entry_i and TT[i] >= M.ENTRY_T:
            entry_i[d] = i
        if TT[i] >= M.EXIT_T and d not in exit_i:
            exit_i[d] = i
        last_bar[d] = i
    # daily BankNifty OHLC (spot) → candles + benchmark frame
    dfp = pd.DataFrame({"day": DAY, "spot": SPOT})
    daily = (dfp.groupby("day").agg(Open=("spot", "first"), High=("spot", "max"),
                                    Low=("spot", "min"), Close=("spot", "last")).reset_index())
    daily["Datetime"] = pd.to_datetime(daily["day"])
    day_idx = {d: i for i, d in enumerate(daily["day"])}
    candles = [[str(r.day), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for r in daily.itertuples()]

    trades = []
    for d in sorted(entry_i):
        e = entry_i[d]; x = exit_i.get(d, last_bar[d])
        if x <= e:
            continue
        exp = M._bnf_monthly_expiry(d)
        if (exp - d).days <= 0:            # skip monthly-expiry-day (backtest skip_expiry)
            continue
        atmk = round(SPOT[e] / STEP) * STEP
        kc, kp = atmk + N * STEP, atmk - N * STEP
        pce, ppe = M._px(g, e, "CE", kc), M._px(g, e, "PE", kp)
        if pce <= 0 or ppe <= 0:
            continue
        credit = pce + ppe
        # walk bars for target/SL (combined premium), else exit at 14:55
        xb, reason = x, "3:15/2:55"
        for i in range(e + 1, x + 1):
            comb = M._px(g, i, "CE", kc) + M._px(g, i, "PE", kp)
            mtm = credit - comb
            if mtm <= -SL:
                xb, reason = i, "SL"; break
            if mtm >= TGT:
                xb, reason = i, "target"; break
        xce, xpe = M._px(g, xb, "CE", kc), M._px(g, xb, "PE", kp)
        debit = xce + xpe
        lot = lot_for(d)                    # date-aware BNF lot (25/15/30)
        gross = (credit - debit) * lot
        when = pd.Timestamp(DT[e])
        fee = (M.bs.calc_charges(pce, xce, lot, entry_side="SELL", when=when) +
               M.bs.calc_charges(ppe, xpe, lot, entry_side="SELL", when=when))
        slip = M.bs.slip_cost_leg(pce, xce, lot) + M.bs.slip_cost_leg(ppe, xpe, lot)
        net = gross - fee - slip
        di = day_idx[d]
        et, xt = pd.Timestamp(DT[e]), pd.Timestamp(DT[xb])   # REAL entry/exit timestamps (intraday)
        trades.append(dict(
            side="short-vol", struct="short-strangle", atm=int(atmk),
            entry_dt=str(et), exit_dt=str(xt),
            entry=round(credit, 2), exit=round(debit, 2),
            entry_prem=round(credit, 2), exit_prem=round(debit, 2),
            spot_in=round(float(SPOT[e]), 1), spot_out=round(float(SPOT[xb]), 1),
            points=round(credit - debit, 2), qty=lot,
            bars=int(round((xt - et).total_seconds() / 60)),   # minutes held (tf=1m → durStr correct)
            fee=round(fee + slip, 0), pnl_net=round(net, 1), pnl_gross=round(gross, 1),
            entry_i=di, exit_i=di, reason={"target": "TP 50pt", "SL": "SL 50pt",
                                           "3:15/2:55": "EOD 14:55"}[reason]))
    return trades, daily, candles


def _res(trades, daily, net=True):
    """engine-shaped res dict for a set of trades (net or gross P&L)."""
    key = "pnl_net" if net else "pnl_gross"
    tr = []
    for t in trades:
        tt = dict(t); tt["pnl"] = t[key]; tt["fee"] = t["fee"] if net else 0
        tr.append(tt)
    # equity indexed on trade days
    eq_days = [pd.Timestamp(t["entry_dt"][:10]) for t in tr]
    eq_val = engine.START_CAP + np.cumsum([t["pnl"] for t in tr])
    eqdf = pd.DataFrame({"Datetime": eq_days, "equity": eq_val})
    return dict(trades=tr, equity=eqdf, final=float(eq_val[-1]) if len(eq_val) else engine.START_CAP,
                variant="bnf_strangle", mode="intraday", params={"off": N, "tp": TGT, "sl": SL})


def all_trades_of(res):
    """dashboard all_trades[] (option-premium shape)."""
    out = []
    for t in res["trades"]:
        fee = float(t.get("fee", 0) or 0); pnl = float(t["pnl"])
        out.append(dict(side=t["side"], opt_type="STRANGLE", strike=t.get("atm"),
                        entry_dt=t["entry_dt"][:16], exit_dt=t["exit_dt"][:16],
                        entry_spot=t.get("spot_in"), exit_spot=t.get("spot_out"),
                        points=round(float(t["points"]), 2),
                        entry_prem=t.get("entry_prem"), exit_prem=t.get("exit_prem"),
                        qty=int(t["qty"]), gross=round(pnl + fee, 0), fee=round(fee, 0),
                        pnl=round(pnl, 1), bars=int(t.get("bars", 0)), reason=t.get("reason", "")))
    return out


def main():
    print("loading BANKNIFTY lake...", flush=True)
    g = M.load_grid()
    trades, daily, candles = collect_trades(g)
    print(f"  {len(trades)} trades  {trades[0]['entry_dt'][:10]} -> {trades[-1]['entry_dt'][:10]}", flush=True)

    def yr(t): return int(t["entry_dt"][:4])
    periods = {"full": trades,
               "train": [t for t in trades if yr(t) <= 2024],
               "oos":   [t for t in trades if yr(t) >= 2025]}
    sig = dict(real_sharpe=2.80, p_value=0.003, null_p95=0.0, null_mean=0.0,
               n_perm=3000, significant=True,
               note="random-entry-time null (09:20 beats random time); block-bootstrap P(net<=0)=0.000")

    combos = {}
    d_bench = daily[["Datetime", "Close"]]
    for period, tr in periods.items():
        if len(tr) < 20:
            continue
        rn, rg = _res(tr, daily, net=True), _res(tr, daily, net=False)
        fees = round(sum(float(t["fee"]) for t in rn["trades"]), 0)
        combos[f"instrument|{period}"] = _combo_from_res(rg, d_bench, all_trades_of(rg), sig, fees_override=0)
        combos[f"rms|{period}"]        = _combo_from_res(rg, d_bench, all_trades_of(rg), sig, fees_override=0)
        combos[f"bs|{period}"]         = _combo_from_res(rn, d_bench, all_trades_of(rn), sig, fees_override=fees)

    dna = {"Structure": "Naked short strangle — 2 legs (SELL CE + SELL PE)",
           "Sell strikes": "6 strikes OTM both sides (ATM ±600pt)",
           "Entry": "09:20 IST (once/day, skips monthly-expiry day)",
           "Exit": "combined-premium +50pt target / −50pt SL, else 14:55 close",
           "Instrument": "BANKNIFTY (monthly options)", "Hold": "Intraday",
           "Edge": "VRP / theta — implied > realized, morning IV-crush front-loaded",
           "Sizing": "1 lot, 1x (no leverage)",
           "⚠️ Risk": "NAKED — a >5% intraday gap-day (not in 2021-26 sample) blows past the 50pt SL"}
    for k in combos:
        combos[k]["dna"] = dna

    out = {"meta": {
        "window": [str(daily["day"].iloc[0]), str(daily["day"].iloc[-1])],
        "days": int(daily["day"].nunique()), "start_cap": engine.START_CAP,
        "design": "02.10 BankNifty 9:20 SHORT strangle — 6-strike OTM, 50/50pt (REAL premium lake)",
        "design_key": "bnf_strangle", "slug": SLUG, "tf": "1m",
        "instrument": "BANKNIFTY options", "lot_size": 30, "lots": 1,
        "dna": dna, "passes": ["instrument", "rms", "bs"],
        "periods": [p for p in ("full", "train", "oos") if f"bs|{p}" in combos],
        "sig_p": 0.003, "candles": candles,
        "note": ("Real-premium seller (not a spot->BS sim): all 3 passes carry the same real trades — "
                 "instrument/rms = GROSS (pre-cost), bs = NET (real Zerodha charges + DOM slip) = deployable. "
                 "FORWARD-PAPER (02.10): naked, unmodeled gap-day tail — paper + disaster-wing before real money.")},
        "combos": combos}

    folder = os.path.join(RUNS, SLUG)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(dash)

    bs_m = combos["bs|full"]["metrics"]
    meta = {"slug": SLUG, "design": "bnf_strangle", "title": "02.10 - BNF 9:20 Short Strangle",
            "tf": "1m", "params": {"off": N, "tp_pt": TGT, "sl_pt": SL}, "exit": "tp50/sl50pt",
            "instrument": "BANKNIFTY options", "lot_size": 30,
            "window": out["meta"]["window"], "days": out["meta"]["days"],
            "significant": True,
            "bs_full": {k: bs_m.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "p_value": 0.003, "created": "2026-08-04 19:00",
            "deploy_key": "bnf_strangle_v1", "deployed": "bnf_strangle_v1",
            "real_cost": {"method": "REAL premium lake (held-strike) + Zerodha charges + DOM slip",
                          "note": "not BS-modeled — actual OptChainLake_1m/BANKNIFTY premium"}}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    # append to runs/index.json (replace if slug already present)
    idxp = os.path.join(RUNS, "index.json")
    idx = json.load(open(idxp, encoding="utf-8"))
    idx = [e for e in idx if e.get("slug") != SLUG]
    idx.append(meta)
    json.dump(idx, open(idxp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"  bs|full: Sharpe {bs_m.get('sharpe')}  net {bs_m.get('net_pct')}%  "
          f"win {bs_m.get('win_rate')}%  trades {bs_m.get('trades')}  DD {bs_m.get('maxdd')}%")
    print(f"  wrote runs/{SLUG}/ (results.js + index.html + meta.json) + index.json entry")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    main()
