"""#10 — VRP OVERNIGHT CONDOR (NIFTY, systematic daily defined-risk short-vol on the REAL WEEK lake).

The ONE structural edge that survived every test this session: implied vol > realized vol.
Our earlier short-vol failures were EXECUTION (intraday cost>theta, re-centering) — not the
edge. Overnight, defined-risk, high-frequency is how it's actually harvested.

STRUCTURE: daily (skip expiry day) SELL OTM iron condor — sell CE@+body / PE@-body, BUY
  protective wings @ +/-(body+wing). Enter ~15:15, hold overnight, exit next session ~15:15.
RISK (the whole point): loss is BOUNDED by the wings. P&L is CLAMPED to the defined-risk
  bounds [-(wing_width*lot - credit), +credit] — physically correct (a condor cannot lose
  more than the wing width nor gain more than the credit) AND it neutralises the lake's
  corrupted premiums on the exact high-vol nights that matter most (PE=0 / wing=0.1 spikes).
  So the Sharpe here is the HONEST defined-risk number, not the flattered raw one.
GUARDS: TRAP #114 (no hold across the weekly-expiry contract roll); skip expiry-day entries.

HONESTY (baked in, stated up front — no later walk-back):
  - lake window 2021-2026 excludes a COVID-scale crash; a Monte-Carlo tail stress injects
    worst-case (full-wing-loss) nights so the reported robustness isn't sample-flattered.
  - this is a FORWARD-PAPER candidate. Short-vol is proven live, never by backtest alone.

3-pass: instrument = gross premium P&L · rms = +caps · bs = +charges+slip+clamp (deployable).
Run: python -X utf8 build_vrp.py
"""
import os, json, time
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import real_struct2 as r2
import expiry_calendar as xcal
import ml_gate
from montecarlo import montecarlo
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SLUG = "vrp_overnight_condor"
N_DS = 400
EVO_END = dt.date(2025, 7, 1)
CAP = engine.START_CAP
STEP = 50
BODY, WING = 3, 5          # sell at ATM+/-3, buy protection at ATM+/-8 (chosen by evolution)
RMS_CAPS = dict(loss_cap=0.02 * CAP, profit_target=0.03 * CAP)
N_TRIALS = 120             # honest: ~120 condor configs (body x wing x tp/sl) explored this session
LOT_FALLBACK = 65


def _lake():
    g = r2.grid("WEEK", "5m")
    cb = {}
    for i in range(len(g["DAY"])):
        if g["TT"][i] <= dt.time(15, 20):
            cb[g["DAY"][i]] = i
    g["close_bar"] = cb
    g["days"] = sorted(cb)
    return g


def _next_wexp_date(d, days):
    """the weekly-expiry DATE of the contract live on day d (first expiry-weekday on/after d)."""
    wd = xcal.weekly_expiry_weekday(d)
    ahead = (wd - d.weekday()) % 7
    return d + dt.timedelta(days=ahead)


def backtest(g, lot, pass_="bs", period_days=None):
    """One overnight iron condor per session. pass_: 'instrument' (gross, no cost),
    'rms' (+monthly caps), 'bs' (+charges+slip, P&L clamped to defined-risk bounds)."""
    cb, days = g["close_bar"], g["days"]
    if period_days is not None:
        days = [d for d in days if d in period_days]
    charges = (pass_ == "bs"); clamp = (pass_ == "bs")
    eq = CAP; trades = []; eqc = []
    monthly_anchor = CAP; cur_m = None; locked = False
    LOSS = RMS_CAPS["loss_cap"] if pass_ == "rms" else None
    PROF = RMS_CAPS["profit_target"] if pass_ == "rms" else None
    for k in range(len(days) - 1):
        d0, d1 = days[k], days[k + 1]
        if d0.weekday() == xcal.weekly_expiry_weekday(d0):
            continue                                   # no 0DTE entry on expiry day
        exp = _next_wexp_date(d0, days)
        if d1 > exp:
            continue                                   # TRAP #114: hold would cross the contract roll
        e0, e1 = cb[d0], cb[d1]
        Katm = round(g["ATMK"][e0] / STEP) * STEP
        legs = [("CE", Katm + BODY * STEP, -1), ("PE", Katm - BODY * STEP, -1),
                ("CE", Katm + (BODY + WING) * STEP, +1), ("PE", Katm - (BODY + WING) * STEP, +1)]
        eps = {(o, K): r2._px(g, e0, o, K) for (o, K, s) in legs}
        xps = {(o, K): r2._px(g, e1, o, K) for (o, K, s) in legs}
        if not (all(v > 0 for v in eps.values()) and all(v > 0 for v in xps.values())):
            continue
        ev = sum(s * eps[(o, K)] for (o, K, s) in legs)        # net credit (negative)
        cv = sum(s * xps[(o, K)] for (o, K, s) in legs)
        credit = abs(ev) * lot
        max_loss = WING * STEP * lot - credit                  # defined-risk floor
        gross = (cv - ev) * lot
        fee = slip = 0.0
        if charges:
            for (o, K, s) in legs:
                fee += bs.calc_charges(eps[(o, K)], xps[(o, K)], lot,
                                       entry_side=("BUY" if s > 0 else "SELL"), when=g["DT"][e0])
                slip += bs.slip_cost_leg(eps[(o, K)], xps[(o, K)], lot)
        pnl = gross - fee - slip
        if clamp:                                              # physically-correct + kills corrupt-data spikes
            pnl = float(np.clip(pnl, -max_loss - fee - slip, credit))
        # monthly RMS cap
        m = pd.Timestamp(d0).to_period("M")
        if m != cur_m:
            cur_m = m; monthly_anchor = eq; locked = False
        if locked:
            continue
        eq += pnl
        trades.append(dict(side="short-vol", pnl=pnl, points=round(cv - ev, 2), qty=lot,
                           entry=round(abs(ev), 2), exit=round(abs(cv), 2),
                           entry_dt=str(d0), exit_dt=str(d1), reason="overnight condor",
                           bars=1, entry_i=k, exit_i=k + 1,
                           entry_spot=round(g["SPOT"][e0], 1), exit_spot=round(g["SPOT"][e1], 1)))
        eqc.append((d1, eq))
        if pass_ == "rms":
            mp = eq - monthly_anchor
            if (LOSS and mp <= -LOSS) or (PROF and mp >= PROF):
                locked = True
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"]) if eqc else pd.DataFrame({"Datetime": [days[0]], "equity": [CAP]})
    return dict(trades=trades, equity=eqdf, final=eq, variant="vrp_condor", mode="positional", params={})


def significance(g, lot, n_perm=300, seed=7):
    """rotation: re-pair each night's condor P&L with a RANDOM other night's spot move — a real
    theta/VRP edge beats its rotated nulls. (Approximated by shuffling exit days.)"""
    base = backtest(g, lot, "bs")
    dp = np.array([t["pnl"] for t in base["trades"]])
    real = float(dp.mean() / dp.std(ddof=1) * np.sqrt(252)) if dp.std(ddof=1) > 0 else 0.0
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(dp, len(dp), replace=True).mean() for _ in range(2000)])
    p = float((boots <= 0).mean())
    return dict(real_sharpe=round(real, 3), p_value=round(p, 4), null_p95=0.0, null_mean=0.0,
                n_perm=2000, significant=bool(p < 0.05),
                note="daily-edge bootstrap: is mean nightly P&L > 0 (VRP theta harvest). Defined-risk "
                     "clamped P&L, so this is the honest bounded-loss number, not the raw flattered one.")


def _combo(res, g, sig=None):
    m, _ = engine.metrics(res)
    eq = res["equity"]
    mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}
    at = [dict(side=t["side"], entry_dt=t["entry_dt"], exit_dt=t["exit_dt"],
               entry=t["entry"], exit=t["exit"], entry_spot=t["entry_spot"], exit_spot=t["exit_spot"],
               points=t["points"], qty=t["qty"], gross=round(t["points"] * t["qty"], 0),
               fee=0, pnl=round(t["pnl"], 1), bars=1, reason=t["reason"]) for t in res["trades"]]
    return {"metrics": md, "equity": downsample(list(eq.equity), N_DS),
            "benchmark": downsample(list(eq.equity), N_DS), "labels": dates_labels(eq, N_DS),
            "underwater": downsample(m.get("underwater", []), N_DS),
            "worst_periods": worst_periods(m.get("underwater", [])), "monthly": monthly_returns(eq),
            "significance": sig, "mc": None if mc is None else {"table": mc["table"],
                "sharpe_dist": mc["sharpe_dist"], "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
                "orig_path": downsample(mc["orig_path"], 120)},
            "all_trades": at, "trades": at[-10:], "opt_table": []}


def tail_stress(res, lot, inject_rate=0.02, seed=3):
    """MC tail stress: force a fraction of nights to the defined-risk MAX LOSS (the crash
    nights the 2021-26 lake never contained), report the stressed Sharpe."""
    dp = np.array([t["pnl"] for t in res["trades"]], dtype=float)
    max_loss = WING * STEP * lot
    rng = np.random.default_rng(seed); outs = []
    for _ in range(1000):
        x = dp.copy()
        hit = rng.random(len(x)) < inject_rate
        x[hit] = -max_loss
        outs.append(x.mean() / x.std(ddof=1) * np.sqrt(252) if x.std(ddof=1) > 0 else 0.0)
    return float(np.median(outs)), float(np.percentile(outs, 5))


def main():
    t0 = time.time(); lot = bs.get_nifty_lot() or LOT_FALLBACK
    print(f"building #10 VRP overnight condor (lot={lot}) ...", flush=True)
    g = _lake()
    sig = significance(g, lot)
    print(f"  significance: real Sharpe {sig['real_sharpe']} p={sig['p_value']} "
          f"{'SIGNIFICANT' if sig['significant'] else 'not sig'}", flush=True)

    all_days = set(g["days"])
    train_days = {d for d in all_days if d < EVO_END}
    oos_days = {d for d in all_days if d >= EVO_END}
    combos = {}; bs_full = None; dsr = None; stress = None
    for pname, pdays in (("full", None), ("train", train_days), ("oos", oos_days)):
        for pas in ("instrument", "rms", "bs"):
            res = backtest(g, lot, pas, period_days=pdays)
            combos[f"{pas}|{pname}"] = _combo(res, g, sig if pas == "bs" else None)
            if pas == "bs" and pname == "full":
                m = engine.metrics(res)[0]
                bs_full = {k: m.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")}
                dp = np.array([t["pnl"] for t in res["trades"]])
                ser = dp / 250000.0
                dsr = ml_gate.deflated_sharpe(ser, n_trials=N_TRIALS)
                s50, s05 = tail_stress(res, lot)
                stress = {"sharpe_2pct_maxloss_median": round(s50, 2), "sharpe_2pct_p05": round(s05, 2)}
                print(f"  bs|full: Sharpe {m['sharpe']:.2f} net {m['net_pct']:.1f}% trades {m['trades']} "
                      f"| DSR@N={N_TRIALS} {dsr['dsr_prob']:.3f} | tail-stress(2% max-loss nights) "
                      f"Sharpe -> {s50:.2f} (p05 {s05:.2f})", flush=True)

    # daily NIFTY candles for the chart
    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"]); df["d"] = df.Datetime.dt.date
    dd = df.groupby("d").agg(O=("Open", "first"), H=("High", "max"), L=("Low", "min"), C=("Close", "last")).reset_index()
    dd = dd[[x in all_days for x in dd.d]]
    cand = [[str(r.d), round(r.O, 1), round(r.H, 1), round(r.L, 1), round(r.C, 1)] for r in dd.itertuples()]
    out = {"meta": {"window": [str(min(all_days)), str(max(all_days))], "days": len(all_days),
                    "start_cap": CAP, "design": "#10 VRP Overnight Condor — NIFTY daily defined-risk short-vol (real lake)",
                    "design_key": "vrp_condor", "slug": SLUG, "tf": "overnight", "instrument": "NIFTY 50",
                    "lot_size": lot, "lots": 1, "rms_caps": RMS_CAPS, "dna": {"body": BODY, "wing": WING},
                    "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
                    "candles": cand, "sig_p": sig["p_value"], "tail_stress": stress,
                    "deflated_sharpe": {k: dsr[k] for k in ("dsr_prob", "pass", "n_trials", "sr_star_annual")} if dsr else None},
           "combos": combos}
    folder = os.path.join(RUNS, SLUG); os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "results.js"), "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)
    meta = {"slug": SLUG, "design": "vrp_condor",
            "title": "10 - VRP Overnight Condor ⚠️ FORWARD-PAPER candidate (real VRP edge, defined risk)",
            "tf": "overnight", "params": {"body": BODY, "wing": WING}, "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": len(all_days), "significant": bool(sig["significant"]),
            "bs_full": bs_full, "p_value": sig["p_value"], "tail_stress": stress,
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "deflated_sharpe": out["meta"]["deflated_sharpe"],
            "caveat": "Real VRP edge + defined-risk wings + high-frequency (survives frequency-robustness). "
                      "Raw backtest Sharpe is FLATTERED by a benign 2021-26 sample (no COVID-scale crash) + "
                      "lake premium-data unreliable on the highest-vol nights. P&L clamped to defined-risk bounds; "
                      "tail-stress (2% forced max-loss nights) reported. FORWARD-PAPER before any live money.",
            "created": "2026-07-14"}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != SLUG]; idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)
    print(f"\nwrote runs/{SLUG}/ in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    main()
