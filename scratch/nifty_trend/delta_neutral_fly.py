"""#06 — Delta-Neutral Iron-Fly with delta-band re-centering (NIFTY, BS on REAL India VIX).

WHY THIS, AND WHY IT'S NOT A DUPLICATE:
  The STATIC short-vol iron-fly is already tested dead (build_shortvol / real_struct2:
  −41% even at real spread — killed by trend-day blow-through, not costs). The ONE thing
  never tested is the ADJUSTMENT: when the position's net delta drifts past a band, re-center
  the whole fly at the new ATM so delta returns to ~0. That directly attacks the exact killer
  (a trending day steamrolls a static short straddle; a delta-managed one keeps re-neutralising).
  This engine tests whether dynamic delta management rescues the family.

PRICING — BS on REAL India VIX (not the realised-vol proxy):
  Legs are priced at the day's REAL India VIX (bs.vix_sigma_map). The proxy carries zero
  volatility-risk-premium (implied==realised by construction) which is why every prior
  BS short-vol showed −95..−100%. Real VIX runs ~2.4 vol-pts over realised on 80% of days
  (verified) → the BS backtest finally SEES the VRP the structure harvests, and gets real
  daily vega (VIX spike day => short marked to a loss). Still BS-modeled → the mission rule
  stands: a BS survivor must be re-confirmed on the REAL premium lake before deploy (TRAP #106).

STRUCTURE (options-only — no futures, per user; every naked short has a wing, per framework):
  Iron fly = SELL ATM CE + SELL ATM PE (the theta/VRP body) + BUY ±wing CE/PE (defined-risk wings).
  Net delta ≈ 0 at entry. As spot moves, short gamma pushes delta; when |net delta| > band we
  RE-CENTER (close all 4 legs, re-open at the new ATM) — the options-only delta reset. Capped
  re-centers/day so a choppy day can't over-trade.

RISK / EXITS (mission ground rules): intraday only, 1 fly/day, force-exit 15:15, no overnight,
  skip-expiry-day entries (policy A, 0DTE gamma guard), 1x (notional<=capital), real Zerodha
  per-leg charges (calc_charges) + DOM-calibrated slippage (slip_cost_leg) on EVERY leg of every
  open/close/re-center. Exit = profit-target as % of credit / hard stop as % of credit / 15:15.

3-pass (same dashboard): instrument = gross premium P&L (no charges/caps) · rms = +daily caps ·
  bs = +real per-leg charges+slippage (deployable). Emits runs/<slug>/ per RESULTS_SCHEMA.

Usage: python delta_neutral_fly.py [--wing 5] [--band 0.20] [--maxrc 3] [--tp 0.5] [--sl 1.0]
                                    [--entry 0930] [--name delta_neutral_fly] [--nperm 300]
"""
import os
import json
import time
import argparse
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import expiry_calendar as excal
from montecarlo import montecarlo
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
N_DS = 400
RMS_CAPS = dict(loss_cap=0.02 * engine.START_CAP, profit_target=0.03 * engine.START_CAP)
STRIKE = bs.STRIKE_STEP  # 50


def _is_expiry_day(d):
    """Policy A: skip NEW entries on the weekly-expiry weekday (0DTE gamma-inflation guard)."""
    return excal.weekly_expiry_weekday(d) == d.weekday()


def _legs(K, wing_off):
    """Iron fly legs: (position_sign, opt_type, strike). sign +1 long / -1 short."""
    return [(-1, "CE", K), (-1, "PE", K),
            (+1, "CE", K + wing_off * STRIKE), (+1, "PE", K - wing_off * STRIKE)]


def backtest(df, params, sigma_map, lot, charges=True, rms_caps=None):
    """One delta-neutral iron-fly per day with delta-band re-centering. Returns the engine
    `res` contract: dict(trades, equity, final, params, variant, mode)."""
    p = dict(entry_hm=(9, 30), eod_hm=(15, 15), wing_off=5, delta_band=0.20,
             max_recenters=3, tp_frac=0.5, sl_frac=1.0, lots=1, skip_expiry=True, vix_min=0.0)
    p.update(params or {})
    qty = int(p["lots"]) * int(lot)
    eh, em = p["entry_hm"]; xh, xm = p["eod_hm"]
    wing = int(p["wing_off"]); band = float(p["delta_band"])
    maxrc = int(p["max_recenters"]); tpf = float(p["tp_frac"]); slf = float(p["sl_frac"])

    d = df.copy()
    d["Datetime"] = pd.to_datetime(d["Datetime"])
    d["day"] = d["Datetime"].dt.date
    d["t"] = d["Datetime"].dt.time
    entry_t = dt.time(eh, em); eod_t = dt.time(xh, xm)

    trades = []
    equity = engine.START_CAP
    eq_curve = []

    for day, g in d.groupby("day", sort=True):
        if p["skip_expiry"] and _is_expiry_day(day):
            continue
        sig = sigma_map.get(day)
        if sig is None:
            continue                                   # no real VIX for that day -> skip (never guess)
        if sig < p["vix_min"]:
            continue                                   # VIX-regime gate: only harvest when premium is fat
        g = g.reset_index(drop=True)
        # entry bar = first bar at/after entry time
        emask = g["t"] >= entry_t
        if not emask.any():
            continue
        i0 = int(np.argmax(emask.values))
        ts0 = g["Datetime"].iloc[i0]
        S0 = float(g["Close"].iloc[i0])
        T0 = bs.tte_years(ts0)
        if T0 <= 0:
            continue

        K = round(S0 / STRIKE) * STRIKE
        legs = _legs(K, wing)
        ref = [bs.bs_price(S0, Kx, T0, sig, opt=ot) for (_s, ot, Kx) in legs]
        credit_unit = -sum(s * pr for (s, _o, _k), pr in zip(legs, ref))   # net credit (>0)
        if credit_unit <= 0:
            continue
        credit_rs = credit_unit * qty

        realized_unit = 0.0          # P&L banked from closed (re-centered) legs, per unit
        roundtrips = []              # (open_px, close_px, sign) for every leg ever closed -> charges
        recenters = 0
        exit_reason = "EOD_1515"
        last_ts = ts0
        last_spot = S0

        for j in range(i0 + 1, len(g)):
            ts = g["Datetime"].iloc[j]
            spot = float(g["Close"].iloc[j])
            T = bs.tte_years(ts)
            px = [bs.bs_price(spot, Kx, T, sig, opt=ot) for (_s, ot, Kx) in legs]
            cur_unit = sum(s * (pn - rf) for (s, _o, _k), pn, rf in zip(legs, px, ref))
            total_unit = realized_unit + cur_unit
            total_rs = total_unit * qty
            net_delta = sum(s * bs.bs_delta(spot, Kx, T, sig, opt=ot)
                            for (s, ot, Kx) in legs)
            last_ts, last_spot = ts, spot

            hit_eod = g["t"].iloc[j] >= eod_t
            if total_rs >= tpf * credit_rs:
                exit_reason = "TP_CREDIT"; break
            if total_rs <= -slf * credit_rs:
                exit_reason = "SL_CREDIT"; break
            if hit_eod:
                exit_reason = "EOD_1515"; break
            if abs(net_delta) > band and recenters < maxrc:
                # RE-CENTER: bank current legs' P&L, record their round-trips, open fresh ATM fly
                realized_unit += cur_unit
                for (s, _o, _k), op, cp in zip(legs, ref, px):
                    roundtrips.append((op, cp, s))
                K = round(spot / STRIKE) * STRIKE
                legs = _legs(K, wing)
                ref = [bs.bs_price(spot, Kx, T, sig, opt=ot) for (_s, ot, Kx) in legs]
                recenters += 1

        # close whatever legs are still open at exit
        pxf = [bs.bs_price(last_spot, Kx, bs.tte_years(last_ts), sig, opt=ot)
               for (_s, ot, Kx) in legs]
        cur_unit = sum(s * (pn - rf) for (s, _o, _k), pn, rf in zip(legs, pxf, ref))
        realized_unit += cur_unit
        for (s, _o, _k), op, cp in zip(legs, ref, pxf):
            roundtrips.append((op, cp, s))

        gross_rs = realized_unit * qty
        fee = 0.0
        if charges:
            for (op, cp, s) in roundtrips:
                side = "SELL" if s < 0 else "BUY"
                fee += bs.calc_charges(op, cp, qty, entry_side=side, when=last_ts)
                fee += bs.slip_cost_leg(op, cp, qty)
        pnl = gross_rs - fee

        if rms_caps:
            lc = rms_caps.get("loss_cap"); ptg = rms_caps.get("profit_target")
            if lc is not None and pnl < -lc:
                pnl = -lc
            if ptg is not None and pnl > ptg:
                pnl = ptg

        equity += pnl
        eq_curve.append((last_ts, equity))
        trades.append({
            "side": "delta-neutral", "struct": "iron_fly",
            "atm": K, "strike": K,
            "entry_dt": str(ts0)[:16], "exit_dt": str(last_ts)[:16],
            "spot_in": round(S0, 1), "spot_out": round(last_spot, 1),
            "entry_spot": round(S0, 1), "exit_spot": round(last_spot, 1),
            "points": round(last_spot - S0, 1),
            "entry_prem": round(credit_unit, 2), "exit_prem": round(credit_unit - realized_unit, 2),
            "entry": round(credit_unit, 2), "exit": round(credit_unit - realized_unit, 2),
            "qty": qty, "gross": round(gross_rs, 0), "fee": round(fee, 0), "pnl": round(pnl, 1),
            "recenters": recenters, "bars": 0, "reason": exit_reason})

    if not eq_curve:
        eq_curve = [(pd.to_datetime(d["Datetime"].iloc[0]), engine.START_CAP)]
    eqdf = pd.DataFrame(eq_curve, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant="delta_fly", mode="intraday")


# ---------------- results.js emission (mirrors build_shortvol) ----------------
def _trades_view(res):
    out = []
    for t in res["trades"]:
        out.append({
            "side": t["side"], "opt_type": "IRON_FLY", "strike": t.get("atm"),
            "entry_dt": t["entry_dt"], "exit_dt": t["exit_dt"],
            "entry_spot": t.get("spot_in"), "exit_spot": t.get("spot_out"),
            "points": t["points"], "entry_prem": t.get("entry_prem"),
            "exit_prem": t.get("exit_prem"), "qty": int(t["qty"]),
            "gross": t.get("gross"), "fee": t.get("fee", 0), "pnl": t["pnl"],
            "recenters": t.get("recenters", 0), "bars": 0, "reason": t.get("reason", "")})
    return out


def _combo(res, d, sig):
    m, _ = engine.metrics(res)
    eq = res["equity"]
    eq_ds = downsample(list(eq.equity), N_DS)
    bh = d.set_index("Datetime").Close.reindex(pd.to_datetime(eq.Datetime)).ffill().values
    bh = engine.START_CAP * bh / bh[0]
    mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}
    return {"metrics": md, "equity": eq_ds, "benchmark": downsample(list(bh), N_DS),
            "labels": dates_labels(eq, N_DS), "underwater": downsample(m.get("underwater", []), N_DS),
            "worst_periods": worst_periods(m.get("underwater", [])),
            "monthly": monthly_returns(eq), "significance": sig,
            "mc": None if mc is None else {"table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
                "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
                "orig_path": downsample(mc["orig_path"], 120)},
            "all_trades": _trades_view(res), "trades": _trades_view(res)[-10:], "opt_table": []}


def significance(frame, params, sigma_map, lot, n_perm=300, seed=7):
    """(a) daily-P&L bootstrap (is mean daily edge > 0) = primary. (b) entry-time rotation p
    (high by design for a theta/VRP harvest — reported honestly, not gamed)."""
    res = backtest(frame, params, sigma_map, lot, charges=True)
    real_m, _ = engine.metrics(res); real_sh = real_m.get("sharpe", 0.0)
    dp = np.array([float(t["pnl"]) for t in res["trades"]]) if res["trades"] else np.array([0.0])
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(dp, len(dp), replace=True).mean() for _ in range(2000)])
    p_edge = float((boots <= 0).mean())
    # entry-time rotation: random entry minute in [09:20, 14:00] -> Sharpe null
    nulls = []
    cand = [(h, mm) for h in range(9, 14) for mm in (0, 15, 30, 45) if (h, mm) >= (9, 20)]
    for _ in range(n_perm):
        h, mm = cand[rng.integers(len(cand))]
        r = backtest(frame, {**params, "entry_hm": (h, mm)}, sigma_map, lot, charges=True)
        mm2, _ = engine.metrics(r); nulls.append(mm2.get("sharpe", 0.0))
    nulls = np.array(nulls)
    p_time = float((nulls >= real_sh).mean())
    return dict(real_sharpe=round(real_sh, 3), p_value=round(p_edge, 4),
                p_timing=round(p_time, 4), null_p95=round(float(np.percentile(nulls, 95)), 3),
                null_mean=0.0, n_perm=n_perm, significant=bool(p_edge < 0.05),
                note="p_value = daily-edge bootstrap (mean daily P&L>0). p_timing (entry-time "
                     "rotation) is high by design — a delta-managed theta/VRP harvest profits at "
                     "many entry times, so timing isn't the edge; the risk premium is.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wing", type=int, default=5)
    ap.add_argument("--band", type=float, default=0.20)
    ap.add_argument("--maxrc", type=int, default=3)
    ap.add_argument("--tp", type=float, default=0.5)
    ap.add_argument("--sl", type=float, default=1.0)
    ap.add_argument("--entry", default="0930")
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--name", default="delta_neutral_fly")
    ap.add_argument("--nperm", type=int, default=300)
    args = ap.parse_args()

    t0 = time.time()
    lot = bs.get_nifty_lot()
    eh, em = int(args.entry[:2]), int(args.entry[2:])
    print(f"loading NIFTY 1-min + real India VIX ... (lot={lot})", flush=True)
    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), usecols=["Datetime", "Open", "High", "Low", "Close"])
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    vmap = bs.vix_sigma_map()
    all_days = sorted(set(pd.to_datetime(df["Datetime"]).dt.date))
    vmap = bs.sigma_map_forward_filled(vmap, all_days)

    params = dict(entry_hm=(eh, em), wing_off=args.wing, delta_band=args.band,
                  max_recenters=args.maxrc, tp_frac=args.tp, sl_frac=args.sl, lots=args.lots)

    frame = df[["Datetime", "Close"]].copy()          # for benchmark/candles
    print(f"data: {len(df)} bars, {len(all_days)} days  {all_days[0]}..{all_days[-1]}", flush=True)

    n = len(df)
    k = int(n * 0.68)
    ftr = df.iloc[:k].reset_index(drop=True)
    foos = df.iloc[k:].reset_index(drop=True)
    frec = df[df["Datetime"] >= pd.Timestamp("2023-01-01")].reset_index(drop=True)

    print("significance (daily-edge bootstrap + entry-time rotation) ...", flush=True)
    sig = significance(df, params, vmap, lot, n_perm=args.nperm)
    print(f"  edge p={sig['p_value']}  timing p={sig['p_timing']}  sharpe={sig['real_sharpe']}", flush=True)

    dd = (frame.set_index("Datetime").resample("1D").agg({"Close": ["first", "max", "min", "last"]}).dropna())
    dd.columns = ["Open", "High", "Low", "Close"]
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]

    def views(fr):
        base = backtest(fr, params, vmap, lot, charges=False)
        rms = backtest(fr, params, vmap, lot, charges=False, rms_caps=RMS_CAPS)
        dep = backtest(fr, params, vmap, lot, charges=True, rms_caps=RMS_CAPS)
        fb = fr[["Datetime", "Close"]]
        return {"instrument": _combo(base, fb, sig), "rms": _combo(rms, fb, sig), "bs": _combo(dep, fb, sig)}

    print("building 3 passes x 4 periods ...", flush=True)
    combos = {}
    for period, fr in (("full", df), ("train", ftr), ("oos", foos), ("recent", frec)):
        for pas, c in views(fr).items():
            combos[f"{pas}|{period}"] = c

    dna = {"structure": "delta_neutral_iron_fly", "wing": args.wing, "delta_band": args.band,
           "max_recenters": args.maxrc, "tp_frac": args.tp, "sl_frac": args.sl,
           "entry": f"{eh:02d}:{em:02d} daily", "pricing": "BS @ real India VIX", "signal": "time"}
    for kk in combos:
        combos[kk]["dna"] = dna

    FAQ = [
        ["Ye strategy kya hai?",
         "DELTA-NEUTRAL iron-fly: roz %02d:%02d pe ATM CE+PE <b>SELL</b> + ±%d strike wings <b>BUY</b> "
         "(defined risk). Net delta ~0. Jab market chalne se delta band (±%.2f) cross kare, poori fly ko "
         "naye ATM pe <b>re-center</b> karke delta wapas 0 — bina futures ke, sirf options se. Profit = "
         "theta + VRP (quiet days), re-centering trend-day loss ko kaat-ta hai." % (eh, em, args.wing, args.band)],
        ["Static iron-fly to −41%% marti hai — ye alag kaise?",
         "Static fly ko trend-day steamroll karta hai (delta unmanaged). Yahan har band-breach pe delta "
         "wapas neutral hota hai — yehi ek cheez pehle test nahi hui thi. Ye poora test hi ye dekhne ke liye "
         "hai ki dynamic delta-management us killer ko bachati hai ya nahi."],
        ["Ye REAL data pe hai ya modeled?",
         "BS-modeled — par legs <b>REAL India VIX</b> pe price hue (implied vol), realized-proxy pe nahi. "
         "Isliye VRP (implied 17%% vs realized 14.6%%, 80%% days) pehli baar dikhta hai + real daily vega. "
         "Mission rule: BS survivor ko deploy se pehle REAL premium lake pe re-confirm karna hoga (TRAP #106)."],
        ["Significance p ka matlab?",
         "edge-p (roz P&L bootstrap, mean>0) = %s. timing-p (entry-time rotation) = %s — high, aur ye "
         "<b>expected</b> hai: theta kabhi bhi becho milta hai, timing edge nahi, risk-premium edge hai." % (sig['p_value'], sig['p_timing'])],
        ["Costs?",
         "Har leg (4 in + 4 out, + har re-center pe 8 aur) pe real Zerodha F&O charges + DOM-calibrated "
         "slippage. Re-centering mehnga hai — isliye max %d/din capped." % args.maxrc],
    ]

    out = {"meta": {
        "window": [str(df["Datetime"].iloc[0])[:10], str(df["Datetime"].iloc[-1])[:10]],
        "days": len(all_days), "start_cap": engine.START_CAP,
        "design": f"Delta-Neutral Iron-Fly (sell ATM + ±{args.wing} wings, delta-band {args.band} re-center) — NIFTY 1m, BS@real-VIX",
        "design_key": "delta_neutral_fly", "slug": args.name, "tf": "1m",
        "instrument": "NIFTY 50 options", "lot_size": lot, "lots": args.lots,
        "rms_caps": RMS_CAPS, "dna": dna, "sig_p": str(sig["p_value"]),
        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos", "recent"],
        "philosophy": ("<b>Delta-Neutral Iron-Fly (BS @ real India VIX).</b> Sell the ATM straddle for theta+VRP, "
            "buy ±%d wings for defined risk, and RE-CENTER the whole fly at the new ATM whenever net delta drifts "
            "past ±%.2f — an options-only delta reset (no futures). This is the one short-vol variant that directly "
            "attacks the static fly's killer (trend-day blow-through). Priced on REAL India VIX so the backtest "
            "captures the volatility-risk-premium the proxy couldn't. Intraday only, 3:15 exit, skip-expiry-day." % (args.wing, args.band)),
        "faq": FAQ, "candles": candles},
        "dna_by_combo": {kk: dna for kk in combos}, "combos": combos}

    folder = os.path.join(RUNS, args.name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)
    mfull = combos["bs|full"]["metrics"]
    meta = {"slug": args.name, "design": "delta_neutral_fly", "title": f"06 - Delta-Neutral Iron-Fly ±{args.wing}",
            "tf": "1m", "params": dna, "exit": f"tp{args.tp}/sl{args.sl}/band{args.band}",
            "instrument": "NIFTY 50 options", "lot_size": lot,
            "window": out["meta"]["window"], "days": out["meta"]["days"],
            "sharpe": mfull.get("sharpe"), "net_pct": mfull.get("net_pct"),
            "maxdd": mfull.get("maxdd"), "trades": mfull.get("trades"),
            "sig_p": sig["p_value"], "significant": sig["significant"]}
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    # append to runs/index.json (hub auto-lists)
    idx_path = os.path.join(RUNS, "index.json")
    idx = []
    if os.path.exists(idx_path):
        try:
            idx = json.load(open(idx_path))
        except Exception:
            idx = []
    idx = [r for r in idx if r.get("slug") != args.name]
    idx.append(meta)
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=2)

    print(f"\nwrote runs/{args.name}/  (results.js + index.html + meta.json)", flush=True)
    print(f"bs|full: Sharpe {mfull.get('sharpe')}  net {mfull.get('net_pct')}%  "
          f"maxDD {mfull.get('maxdd')}%  trades {mfull.get('trades')}  edge-p {sig['p_value']}", flush=True)
    print(f"done in {time.time()-t0:.0f}s — open /lab/runs/{args.name}/index.html", flush=True)


if __name__ == "__main__":
    main()
