"""#09 — MEAN-REVERSION POSITIONAL (NIFTY daily, Bollinger band fade, monthly ATM options).

The first positional edge to survive the honest gauntlet (2026-07-14):
  - signal significant (rotation p=0.005 — NOT beta; buy-hold was only +34%)
  - out-of-sample holds (evo≈val)
  - survives REAL MONTH-lake option pricing WITH the TRAP #114 contract-roll guard
    (unguarded Sharpe 9-12 was a roll-seam artifact; guarded ≈ 2.6-3.9, honest)

SIGNAL (daily bars): Bollinger(w) ± k·σ. Close below lower band -> LONG (buy dip);
  close above upper band -> SHORT (fade rip). ATR stop (sl·ATR, trailing), RR target,
  max-hold cap. 1x, risk 1.5%/trade.
EXECUTION: BUY held-strike MONTHLY ATM option (CE long / PE short), REAL lake premium,
  date-aware Zerodha charges + DOM slippage. PROPER ROLL: a hold crossing the entry
  contract's monthly expiry closes that contract at expiry and re-opens the next-month
  ATM (charges on every roll) — never fake-prices exit on a fresh contract (TRAP #114).

3-pass (mission standard): instrument = spot signal P&L · rms = + monthly caps ·
  bs = + real-lake monthly options with roll = the deployable truth.

STATUS: research candidate. NOT deployed — needs forward paper before any live money.
Run:  python -X utf8 build_meanrev.py
"""
import os, json, time
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import real_struct2 as r2
import ml_gate
from montecarlo import montecarlo
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SLUG = "meanrev_nifty"
N_DS = 400
EVO_END = dt.date(2025, 7, 1)
RISK, CAP, LEV = 0.015, engine.START_CAP, 1.0
RMS_CAPS = dict(loss_cap=0.02 * CAP, profit_target=0.03 * CAP)
# honest DSR trial count: the mean-reversion optimize explored ~250 configs this session
N_TRIALS = 250
CFG = dict(w=15, k=1.5, sl=3.0, rr=1.5, mh=10)   # picked by EVOLUTION robustness (guarded real-lake)


# ---------------- daily bars + signal ----------------
def daily():
    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"])
    df["d"] = df.Datetime.dt.date
    g = df.groupby("d").agg(O=("Open", "first"), H=("High", "max"),
                            L=("Low", "min"), C=("Close", "last")).reset_index()
    g = g[pd.to_datetime(g.d) >= "2021-07-01"].reset_index(drop=True)   # real-lake window
    return g


def atr14(H, L, C):
    n = len(C)
    tr = np.maximum(H[1:] - L[1:], np.maximum(np.abs(H[1:] - C[:-1]), np.abs(L[1:] - C[:-1])))
    a = np.full(n, np.nan)
    a[1:] = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
    return a


def bb_signal(C, w, k, shift=0):
    mid = pd.Series(C).rolling(w).mean().values
    sd = pd.Series(C).rolling(w).std().values
    lo, sh = C < mid - k * sd, C > mid + k * sd
    if shift:
        lo, sh = np.roll(lo, shift), np.roll(sh, shift)
    return lo, sh


# ---------------- spot backtest (engine-shaped res) ----------------
def backtest_spot(g, cfg, rms_caps=None, shift=0):
    C, H, L, O, DATE = g.C.values, g.H.values, g.L.values, g.O.values, g.d.values
    n = len(g); A = atr14(H, L, C); lo, sh = bb_signal(C, cfg["w"], cfg["k"], shift)
    sl, rr, mh = cfg["sl"], cfg["rr"], cfg["mh"]
    months = pd.to_datetime(g.d).dt.to_period("M").astype(str).values
    eq = CAP; pos = None; trades = []; eqc = []
    cur_m = None; anchor = CAP; locked = False
    LOSS = (rms_caps or {}).get("loss_cap"); PROF = (rms_caps or {}).get("profit_target")

    def close(i, px, reason):
        nonlocal eq, pos
        sgn = 1 if pos["side"] == "long" else -1
        pnl = (px - pos["entry"]) * pos["qty"] * sgn - (40.0 + abs(px * pos["qty"]) * 0.0002)
        eq += pnl
        trades.append(dict(side=pos["side"], entry=pos["entry"], exit=px, qty=pos["qty"],
                           pnl=pnl, points=(px - pos["entry"]) * sgn, entry_i=pos["ei"],
                           exit_i=i, entry_dt=pos["edt"], exit_dt=DATE[i], reason=reason,
                           bars=i - pos["ei"]))
        pos = None

    for i in range(60, n - 1):
        if pos is not None:
            s = pos["side"]; held = i - pos["ei"]
            pos["stop"] = (max(pos["stop"], C[i] - sl * A[i]) if s == "long"
                           else min(pos["stop"], C[i] + sl * A[i]))
            if s == "long":
                if O[i] <= pos["stop"]: close(i, O[i], "Gap SL")
                elif L[i] <= pos["stop"]: close(i, pos["stop"], "ATR SL")
                elif H[i] >= pos["tgt"]: close(i, pos["tgt"], "Target")
            else:
                if O[i] >= pos["stop"]: close(i, O[i], "Gap SL")
                elif H[i] >= pos["stop"]: close(i, pos["stop"], "ATR SL")
                elif L[i] <= pos["tgt"]: close(i, pos["tgt"], "Target")
            if pos is not None and held >= mh: close(i, C[i], "Max hold")
        m = eq + ((C[i] - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
                  if pos is not None else 0.0)
        if months[i] != cur_m:
            cur_m = months[i]; anchor = m; locked = False
        if rms_caps and not locked:
            mp = m - anchor
            if (LOSS and mp <= -LOSS) or (PROF and mp >= PROF):
                if pos is not None: close(i, C[i], "RMS max-loss" if mp < 0 else "RMS profit-target"); m = eq
                locked = True
        if pos is None and not locked and (lo[i] or sh[i]) and np.isfinite(A[i]) and A[i] > 0 and i + 1 < n:
            s = "long" if lo[i] else "short"; entry = O[i + 1]; sd = sl * A[i]
            qty = min(RISK * eq / max(sd, 1e-6), LEV * eq / entry)
            pos = dict(side=s, entry=entry, qty=qty,
                       stop=entry - sd if s == "long" else entry + sd,
                       tgt=entry + rr * sd if s == "long" else entry - rr * sd,
                       ei=i + 1, edt=DATE[i + 1])
        eqc.append((DATE[i], m))
    if pos is not None: close(n - 1, C[n - 1], "End")
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=eq, params=cfg, variant="meanrev", mode="positional")


# ---------------- real-lake MONTHLY option reprice WITH PROPER ROLL (TRAP #114) ----------------
_G = None
def _lake():
    global _G
    if _G is None:
        _G = r2.grid("MONTH", "5m")
        _G["last_bar"] = {}
        for i in range(len(_G["DAY"])): _G["last_bar"][_G["DAY"][i]] = i
        _G["days"] = sorted(_G["last_bar"])
    return _G


def _mexp(d):
    return bs._next_monthly_expiry(pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15)).date()


def reprice_roll(spot_trades, lot):
    """Each spot trade -> real-lake monthly-option P&L, rolling at each monthly expiry.
    Returns engine-shaped option trades (entry/exit = premium) + a per-day P&L add array key."""
    g = _lake(); lb, days = g["last_bar"], g["days"]
    out = []; rolls = 0
    for t in spot_trades:
        ed, xd, side = t["entry_dt"], t["exit_dt"], t["side"]
        if ed not in lb:
            continue
        opt = "CE" if side == "long" else "PE"
        seg_start = ed; tot = 0.0; gtot = 0.0; ftot = 0.0; ep0 = None; xpN = None; nseg = 0
        while True:
            exp = _mexp(seg_start)
            seg_exit = xd if xd <= exp else max([d for d in days if d <= exp], default=seg_start)
            si, xi = lb.get(seg_start), lb.get(seg_exit)
            if si is None or xi is None or xi < si:
                break
            K = round(g["ATMK"][si] / 50) * 50
            ep = r2._px(g, si, opt, K); xp = r2._px(g, xi, opt, K)
            if not (ep > 0 and xp > 0):
                break
            fee = bs.calc_charges(ep, xp, lot, entry_side="BUY", when=g["DT"][si])
            slip = bs.slip_cost_leg(ep, xp, lot)
            gross_seg = (xp - ep) * lot
            tot += gross_seg - fee - slip
            gtot += gross_seg; ftot += fee + slip     # surface per-trade gross + charges (net = pnl unchanged)
            if ep0 is None: ep0 = ep
            xpN = xp; nseg += 1
            if seg_exit >= xd:
                break
            nxt = [d for d in days if d > exp]                 # roll to next month's first lake day
            if not nxt:
                break
            seg_start = nxt[0]; rolls += 1
        if ep0 is None:
            continue
        out.append(dict(side=side, entry=round(ep0, 2), exit=round(xpN, 2), qty=lot,
                        pnl=round(tot, 1), gross=round(gtot, 1), fee=round(ftot, 1),
                        points=round((xpN - ep0), 1),
                        entry_dt=str(ed), exit_dt=str(xd), reason=t.get("reason", "") + (f" +{nseg-1}roll" if nseg > 1 else ""),
                        entry_i=t["entry_i"], exit_i=t["exit_i"], bars=t.get("bars", 0),
                        entry_spot=round(float(t["entry"]), 1), exit_spot=round(float(t["exit"]), 1)))
    return out, rolls


# ---------------- significance (rotation) ----------------
def significance(g, cfg, n_perm=300, seed=7):
    real = engine.metrics(backtest_spot(g, cfg))[0].get("sharpe", 0.0)
    rng = np.random.default_rng(seed); n = len(g); nulls = []
    for _ in range(n_perm):
        s = int(rng.integers(10, n - 10))
        nulls.append(engine.metrics(backtest_spot(g, cfg, shift=s))[0].get("sharpe", 0.0))
    nulls = np.array(nulls); p = float((nulls >= real).mean())
    return dict(real_sharpe=round(real, 3), p_value=round(p, 4),
                null_p95=round(float(np.percentile(nulls, 95)), 3), null_mean=0.0,
                n_perm=n_perm, significant=bool(p < 0.05),
                note="rotation significance: signal shifted by a random offset; a real timing "
                     "edge beats its own rotated nulls. Buy-hold beta was only +34% over the window.")


def _combo(res, g, all_trades, sig=None, fees=None):
    m, _ = engine.metrics(res)
    if fees is not None: m["fees"] = fees
    eq = res["equity"]
    bh = g.set_index("d").C.reindex(pd.to_datetime(eq.Datetime).dt.date).ffill().values
    bh = CAP * bh / bh[0]
    mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}
    return {"metrics": md, "equity": downsample(list(eq.equity), N_DS),
            "benchmark": downsample(list(bh), N_DS), "labels": dates_labels(eq, N_DS),
            "underwater": downsample(m.get("underwater", []), N_DS),
            "worst_periods": worst_periods(m.get("underwater", [])),
            "monthly": monthly_returns(eq), "significance": sig,
            "mc": None if mc is None else {"table": mc["table"], "sharpe_dist": mc["sharpe_dist"],
                "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
                "orig_path": downsample(mc["orig_path"], 120)},
            "all_trades": all_trades, "trades": all_trades[-10:], "opt_table": []}


def _opt_res(opt_trades, g):
    add = np.zeros(len(g))
    for o in opt_trades:
        if 0 <= o["exit_i"] < len(g): add[o["exit_i"]] += o["pnl"]
    eqv = CAP + np.cumsum(add)
    return dict(trades=opt_trades, equity=pd.DataFrame({"Datetime": g.d.values, "equity": eqv}),
                final=float(eqv[-1]), variant="meanrev", mode="positional", params=CFG)


def main():
    t0 = time.time(); lot = bs.get_nifty_lot()
    print(f"building #09 mean-reversion positional (lot={lot}) ...", flush=True)
    g = daily()
    sig = significance(g, CFG)
    print(f"  significance: real Sharpe {sig['real_sharpe']} p={sig['p_value']} "
          f"{'SIGNIFICANT' if sig['significant'] else 'not sig'}", flush=True)

    periods = {"full": g,
               "train": g[[x < EVO_END for x in g.d]].reset_index(drop=True),
               "oos": g[[x >= EVO_END for x in g.d]].reset_index(drop=True)}
    combos = {}; bs_full_metrics = None; dsr = None
    for pname, gp in periods.items():
        inst = backtest_spot(gp, CFG)
        rms = backtest_spot(gp, CFG, rms_caps=RMS_CAPS)
        opt, rolls = reprice_roll(rms["trades"], lot)
        ores = _opt_res(opt, gp)
        combos[f"instrument|{pname}"] = _combo(inst, gp, _spot_view(inst))
        combos[f"rms|{pname}"] = _combo(rms, gp, _spot_view(rms))
        combos[f"bs|{pname}"] = _combo(ores, gp, opt, sig, fees=round(sum(
            (o["entry"] - 0) for o in opt), 0) if False else None)
        if pname == "full":
            m = engine.metrics(ores)[0]
            bs_full_metrics = {k: m.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")}
            # DSR on the deployable (bs|full) daily returns
            dd = {}
            for o in opt: dd[pd.to_datetime(o["exit_dt"]).date()] = dd.get(pd.to_datetime(o["exit_dt"]).date(), 0.0) + o["pnl"]
            allday = sorted(dd); ser = np.array([dd[d] for d in allday]) / 250000.0
            dsr = ml_gate.deflated_sharpe(ser, n_trials=N_TRIALS)
            print(f"  bs|full: Sharpe {m['sharpe']:.2f} net {m['net_pct']:.1f}% ({rolls} rolls) "
                  f"| DSR@N={N_TRIALS} {dsr['dsr_prob']:.3f} {'PASS' if dsr['pass'] else 'fail'}", flush=True)

    cand = [[str(r.d), round(r.O, 1), round(r.H, 1), round(r.L, 1), round(r.C, 1)] for r in g.itertuples()]
    out = {"meta": {"window": [str(g.d.iloc[0]), str(g.d.iloc[-1])], "days": len(g),
                    "start_cap": CAP, "design": "#09 Mean-Reversion Positional — NIFTY daily (monthly ATM options)",
                    "design_key": "meanrev", "slug": SLUG, "tf": "1D", "instrument": "NIFTY 50",
                    "lot_size": lot, "lots": 1, "rms_caps": RMS_CAPS, "dna": CFG,
                    "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
                    "candles": cand, "sig_p": sig["p_value"],
                    "deflated_sharpe": {k: dsr[k] for k in ("dsr_prob", "pass", "n_trials", "sr_star_annual")} if dsr else None},
           "combos": combos}
    folder = os.path.join(RUNS, SLUG); os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "results.js"), "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)
    meta = {"slug": SLUG, "design": "meanrev", "title": "09 - Mean-Reversion Positional (NIFTY daily, monthly options)",
            "tf": "1D", "params": CFG, "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": len(g), "significant": bool(sig["significant"]),
            "bs_full": bs_full_metrics, "p_value": sig["p_value"],
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "deflated_sharpe": out["meta"]["deflated_sharpe"], "created": "2026-07-14"}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != SLUG]; idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)
    print(f"\nwrote runs/{SLUG}/ (results.js + index.html + meta.json) in {time.time()-t0:.0f}s", flush=True)


def _spot_view(res):
    out = []
    for t in res["trades"]:
        qty, pts, pnl = float(t.get("qty", 0)), float(t["points"]), float(t["pnl"])
        out.append({"side": t["side"], "entry_dt": str(t["entry_dt"])[:16], "exit_dt": str(t["exit_dt"])[:16],
                    "entry": round(float(t["entry"]), 1), "exit": round(float(t["exit"]), 1),
                    "entry_spot": round(float(t["entry"]), 1), "exit_spot": round(float(t["exit"]), 1),
                    "points": round(pts, 1), "qty": round(qty, 2), "gross": round(pts * qty, 0),
                    "fee": round(pts * qty - pnl, 0), "pnl": round(pnl, 1),
                    "bars": int(t.get("bars", 0)), "reason": t.get("reason", "")})
    return out


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    main()
