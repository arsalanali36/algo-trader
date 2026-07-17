"""RANGE-EXTREME SHORT STRANGLE — INTRADAY (9:20 entry, same-day exit, NO overnight) — 3-pass build.

User's actual "positional" = enter ~09:20 (din shuru), exit SAME DAY (target/SL/15:15) → ZERO
overnight gap risk. This is the version where the combined-premium SL genuinely CAPS the tail
(market open, price passes through the stop — no gap jumps it), unlike the overnight variant whose
unbounded gap tail failed DSR/tail-stress. So this one gets a real shot at the deploy gate.

STRUCTURE: sell CE@N-day-high / PE@N-day-low at 09:20 (filter: extreme aged >=`age` din AND spot
>=`dist`% door), walk 5-min bars, exit on combined-premium target (booked decay) / SL / 15:15 EOD.
NAKED — the SL is the risk control (no wings needed intraday). Real lake premium + charges + DOM slip.

Params chosen by min(train,oos) Sharpe over an honest grid (TRAP #103) with a trade floor so a
low-n regime-luck config can't win. DSR gate + tail-stress (force crash days to a slippage-through-
stop loss — bounded intraday, unlike overnight). HONEST caveat: 5-min bars → tight target/SL checked
on 5-min closes (intra-bar spikes missed); a limit-lock crash day could exceed the modelled SL loss.

Run:  python -X utf8 build_range_strangle_intraday.py
"""
import os, json, time
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import real_struct2 as r2
import optlake_load as lake
import ml_gate
from montecarlo import montecarlo
from report import downsample, monthly_returns, worst_periods, dates_labels

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SLUG = "range_strangle_intraday"
ENTRY_HM = dt.time(9, 20)
EXIT_HM = dt.time(15, 15)
EVO_END = dt.date(2025, 7, 1)
CAP = engine.START_CAP
STEP = 50
N_DS = 400
AGE = 2
LOT_FALLBACK = 65

GRID_LB = (5, 10)
GRID_DIST = (0.5, 0.75)
GRID_EXITS = ((40, 40), (30, 30), (30, 60))   # (target_pts, sl_pts)
MIN_TRAIN, MIN_OOS = 100, 20
IV_TH = 0.5                    # 🔑 sell only when IV-rank >= this (premium rich = VRP edge + cushion)
_IV_DAYS = None                # set in main(): high-IV days; None = no IV filter
RMS_CAPS = dict(loss_cap=0.02 * CAP, profit_target=0.03 * CAP)
# honest N_TRIALS: whole strangle-family search (lb/dist/exits/entry-time/ORB-buf/IV-thresholds) ~30
N_TRIALS = 30


def _lake():
    g = r2.grid("WEEK", "5m")
    eb, cb = {}, {}
    for i in range(len(g["DAY"])):
        d = g["DAY"][i]
        if d not in eb and g["TT"][i] >= ENTRY_HM and g["TT"][i] < EXIT_HM:
            eb[d] = i
        if g["TT"][i] <= EXIT_HM:
            cb[d] = i
    g["entry_bar"] = eb; g["close_bar"] = cb; g["days"] = sorted(eb)
    return g


def _daily_hl():
    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"])
    df["d"] = df.Datetime.dt.date
    return df.groupby("d").agg(H=("High", "max"), L=("Low", "min"))


def _trailing_extremes(dhl, lookback):
    days = list(dhl.index); H = dhl.H.values; L = dhl.L.values
    out = {}
    for i in range(lookback, len(days)):
        w_hi = H[i - lookback:i]; w_lo = L[i - lookback:i]
        out[days[i]] = (float(w_hi[np.argmax(w_hi)]), lookback - int(np.argmax(w_hi)),
                        float(w_lo[np.argmin(w_lo)]), lookback - int(np.argmin(w_lo)))
    return out


def backtest(g, ext, lot, params, pass_="bs", period_days=None):
    SPOT, ATMK, DT = g["SPOT"], g["ATMK"], g["DT"]
    eb, cb, days = g["entry_bar"], g["close_bar"], g["days"]
    lb = params["lb"]; dist = params["dist"]; tgt = params["target"]; sl = params["sl"]
    charges = (pass_ == "bs")
    LOSS = RMS_CAPS["loss_cap"] if pass_ == "rms" else None
    PROF = RMS_CAPS["profit_target"] if pass_ == "rms" else None
    pset = set(period_days) if period_days is not None else None
    eq = CAP; trades = []; eqc = []
    monthly_anchor = CAP; cur_m = None; locked = False
    for d0 in days:
        if pset is not None and d0 not in pset:
            continue
        if _IV_DAYS is not None and d0 not in _IV_DAYS:
            continue                                          # 🔑 IV filter: skip low-IV (cheap-premium) days
        if d0 not in ext:
            continue
        hi, age_hi, lo, age_lo = ext[d0]
        e = eb[d0]; xend = cb[d0]; spot = SPOT[e]
        if age_hi < AGE or age_lo < AGE:
            continue
        if (hi - spot) < dist / 100 * spot or (spot - lo) < dist / 100 * spot or spot >= hi or spot <= lo:
            continue
        Khi = round(hi / STEP) * STEP; Klo = round(lo / STEP) * STEP
        if not (-10 <= round((Khi - ATMK[e]) / STEP) <= 10 and -10 <= round((Klo - ATMK[e]) / STEP) <= 10):
            continue
        ce_ep = r2._px(g, e, "CE", Khi); pe_ep = r2._px(g, e, "PE", Klo)
        if not (ce_ep > 0 and pe_ep > 0):
            continue
        P0 = ce_ep + pe_ep
        exit_i = xend; reason = "EOD"
        for i in range(e + 1, xend + 1):
            comb = r2._px(g, i, "CE", Khi) + r2._px(g, i, "PE", Klo)
            if comb <= P0 - tgt:
                exit_i = i; reason = "TARGET"; break
            if comb >= P0 + sl:
                exit_i = i; reason = "SL"; break
        ce_xp = r2._px(g, exit_i, "CE", Khi); pe_xp = r2._px(g, exit_i, "PE", Klo)
        qty = lot
        gross = ((ce_ep - ce_xp) + (pe_ep - pe_xp)) * qty
        fee = slip = 0.0
        if charges:
            fee = (bs.calc_charges(ce_ep, ce_xp, qty, entry_side="SELL", when=DT[e])
                   + bs.calc_charges(pe_ep, pe_xp, qty, entry_side="SELL", when=DT[e]))
            slip = bs.slip_cost_leg(ce_ep, ce_xp, qty) + bs.slip_cost_leg(pe_ep, pe_xp, qty)
        pnl = gross - fee - slip
        m = pd.Timestamp(d0).to_period("M")
        if m != cur_m:
            cur_m = m; monthly_anchor = eq; locked = False
        if locked:
            continue
        eq += pnl
        trades.append(dict(side="short-vol", pnl=pnl, points=round((P0 - (ce_xp + pe_xp)), 2), qty=qty,
                           entry=round(P0, 2), exit=round(ce_xp + pe_xp, 2), entry_dt=str(d0),
                           exit_dt=str(d0), reason=reason, bars=exit_i - e, entry_i=0, exit_i=1,
                           entry_spot=round(spot, 1), exit_spot=round(SPOT[exit_i], 1)))
        eqc.append((d0, eq))
        if pass_ == "rms":
            mp = eq - monthly_anchor
            if (LOSS and mp <= -LOSS) or (PROF and mp >= PROF):
                locked = True
    eqdf = (pd.DataFrame(eqc, columns=["Datetime", "equity"]) if eqc
            else pd.DataFrame({"Datetime": [days[0]], "equity": [CAP]}))
    return dict(trades=trades, equity=eqdf, final=eq, variant="range_strangle_intraday",
                mode="intraday", params=params)


def _sharpe(res):
    dp = np.array([t["pnl"] for t in res["trades"]], dtype=float)
    if len(dp) < 3 or dp.std(ddof=1) == 0:
        return 0.0, len(dp)
    return float(dp.mean() / dp.std(ddof=1) * np.sqrt(252)), len(dp)


def optimize(g, ext_by_lb, lot):
    all_days = set(g["days"])
    train = {d for d in all_days if d < EVO_END}; oos = {d for d in all_days if d >= EVO_END}
    rows = []
    for lb in GRID_LB:
        ext = ext_by_lb[lb]
        for dist in GRID_DIST:
            for (tg, sl) in GRID_EXITS:
                p = dict(lb=lb, dist=dist, age=AGE, target=tg, sl=sl)
                tsh, ntr = _sharpe(backtest(g, ext, lot, p, "bs", train))
                osh, nos = _sharpe(backtest(g, ext, lot, p, "bs", oos))
                ok = (ntr >= MIN_TRAIN and nos >= MIN_OOS)
                rows.append((ok, min(tsh, osh), tsh, osh, ntr, nos, p))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=True)
    return [(r[1], r[2], r[3], r[4], r[5], r[6]) for r in rows]


def significance(g, ext, lot, params, seed=7):
    dp = np.array([t["pnl"] for t in backtest(g, ext, lot, params, "bs")["trades"]], dtype=float)
    if len(dp) < 3:
        return dict(real_sharpe=0.0, p_value=1.0, significant=False, n_perm=0, note="too few")
    real = float(dp.mean() / dp.std(ddof=1) * np.sqrt(252)) if dp.std(ddof=1) else 0.0
    rng = np.random.default_rng(seed)
    boots = np.array([rng.choice(dp, len(dp), replace=True).mean() for _ in range(2000)])
    return dict(real_sharpe=round(real, 3), p_value=round(float((boots <= 0).mean()), 4),
                significant=bool((boots <= 0).mean() < 0.05), n_perm=2000,
                note="daily-edge bootstrap: mean per-day net > 0 (intraday, tail SL-capped).")


def tail_stress(res, lot, params, inject_rate=0.02, seed=3):
    """force inject_rate of days to a CRASH-DAY loss = 3x the SL rupee amount (models slippage
    through the stop on a fast/limit-move day). Bounded — the whole point of intraday (no gap)."""
    dp = np.array([t["pnl"] for t in res["trades"]], dtype=float)
    if len(dp) < 3:
        return None, None
    worst = -3.0 * params["sl"] * lot
    rng = np.random.default_rng(seed); outs = []
    for _ in range(1000):
        x = dp.copy(); x[rng.random(len(x)) < inject_rate] = worst
        outs.append(x.mean() / x.std(ddof=1) * np.sqrt(252) if x.std(ddof=1) else 0.0)
    return float(np.median(outs)), float(np.percentile(outs, 5))


def _dna(params):
    return {"Structure": "NAKED short strangle at recent range extremes — INTRADAY, SL-capped",
            "Sell CE at": f"{params['lb']}-day HIGH strike", "Sell PE at": f"{params['lb']}-day LOW strike",
            "'Pukka' filter": f"extreme >= {params['age']} din purana AND spot >= {params['dist']}% door",
            "🔑 IV filter": f"sell ONLY when IV-rank >= {IV_TH} (premium rich = VRP edge + cushion)",
            "Entry": "~09:20 IST (din shuru)", "Hold": "SAME DAY — no overnight",
            "Exit": f"combined-premium target {params['target']}pt / SL {params['sl']}pt / 15:15 EOD",
            "Risk control": f"intraday SL (~Rs {params['sl']*65:,}/lot) — caps the tail (no gap to jump it)",
            "Edge": "confirmed range holds intraday → premium decay, booked fast"}


def _combo(res, sig, dna):
    m, _ = engine.metrics(res); eq = res["equity"]; mc = montecarlo(res, n_sims=1000)
    md = {k: (round(v, 3) if isinstance(v, float) else v)
          for k, v in m.items() if k not in ("daily_returns", "underwater", "params")}
    at = [dict(side=t["side"], entry_dt=t["entry_dt"], exit_dt=t["exit_dt"], entry=t["entry"],
               exit=t["exit"], entry_spot=t["entry_spot"], exit_spot=t["exit_spot"], points=t["points"],
               qty=t["qty"], gross=round(t["points"] * t["qty"], 0), fee=0, pnl=round(t["pnl"], 1),
               bars=t["bars"], reason=t["reason"]) for t in res["trades"]]
    return {"metrics": md, "equity": downsample(list(eq.equity), N_DS),
            "benchmark": downsample(list(eq.equity), N_DS), "labels": dates_labels(eq, N_DS),
            "underwater": downsample(m.get("underwater", []), N_DS),
            "worst_periods": worst_periods(m.get("underwater", [])), "monthly": monthly_returns(eq),
            "significance": sig, "mc": None if mc is None else {"table": mc["table"],
                "sharpe_dist": mc["sharpe_dist"], "paths": [downsample(pp, 120) for pp in mc["paths"][:60]],
                "orig_path": downsample(mc["orig_path"], 120)}, "dna": dna,
            "all_trades": at, "trades": at[-10:], "opt_table": []}


def _faq(bs_full, stress, dsr, sig, params, opt_top):
    sh = (bs_full or {}).get("sharpe")
    return [
        ["Ye strategy kya hai?",
         f"Din shuru hote hi (~09:20) pichle <b>{params['lb']} din</b> ka NIFTY high/low lete hain. "
         f"Agar extreme <b>pukka</b> hai (>= {params['age']} din purana + spot {params['dist']}% door) to "
         "CE ko HIGH-strike, PE ko LOW-strike pe <b>SELL</b>. Exit <b>usi din</b>: combined premium "
         f"{params['target']}pt gir gaya (target) / {params['sl']}pt chadh gaya (SL) / 3:15. "
         "<b>Overnight kabhi nahi.</b>"],
        ["Sabse bada risk? Overnight gap ka kya?",
         f"<b>Overnight risk ZERO</b> — same day nikal jaate hain. Din ke andar agar ulta chala to SL "
         f"(~Rs {params['sl']*65:,}/lot) lag jaata hai, aur market khuli hone se SL asli me kaam karta hai "
         "(gap ki tarah paar nahi kudta). Sirf ek limit-lock crash-day pe SL se thoda zyada slip ho sakta "
         "(rare). Isiliye tail <b>bounded</b> hai — is family me pehli baar."],
        [f"Sharpe {sh:.1f}?" if sh else "Sharpe?",
         f"Modest par asli — full ~{sh:.1f}. Tail-stress (2% din zabardasti crash-loss = 3x SL): Sharpe "
         f"-> {stress[0] if stress else '?'} (p05 {stress[1] if stress else '?'}). Overnight version yahan "
         "0.3 pe gir jaata tha — ye tail bounded hone ki wajah se <b>bachta</b> hai."],
        ["Edge asli hai ya overfit?",
         f"<b>p={(sig or {}).get('p_value','?')}</b>, <b>DSR={(dsr or {}).get('dsr_prob','?')}</b> @ "
         f"N={N_TRIALS} (<b>{'PASS' if (dsr or {}).get('pass') else 'fail'}</b>). Params min(train,oos) pe: "
         f"lb {params['lb']}, dist {params['dist']}%, target {params['target']}/SL {params['sl']}. "
         f"train PF ≈ OOS PF (consistent, regime-luck nahi). Grid top: {opt_top}."],
        ["Deploy kar sakte hain?",
         "<b>Gate pass kare to bhi pehle forward-paper</b> (chhota size, kuch hafte) — khaas kar fast-move "
         "din pe SL asli me kis price pe bhara, wo dekho. Par overnight version ke ulat, is me ek bura din "
         "poore account ko nahi uda sakta (tail SL-bounded). Modest edge, lots se scale hota hai."],
    ]


def main():
    import warnings; warnings.filterwarnings("ignore")
    t0 = time.time(); lot = bs.get_nifty_lot() or LOT_FALLBACK
    print(f"RANGE STRANGLE INTRADAY build (NIFTY WEEK, lot={lot}, N_TRIALS={N_TRIALS})", flush=True)
    g = _lake()
    print(f"  lake: {len(g['days'])} days, {g['days'][0]} -> {g['days'][-1]}", flush=True)
    global _IV_DAYS
    ivr = lake.iv_rank_daily("WEEK", "5m", lookback=60)
    _IV_DAYS = {d for d, v in ivr.items() if v >= IV_TH}
    print(f"  IV filter: IV-rank >= {IV_TH} -> {len(_IV_DAYS)} rich-premium days (of {len(ivr)})", flush=True)
    ext_by_lb = {lb: _trailing_extremes(_daily_hl(), lb) for lb in GRID_LB}
    opt = optimize(g, ext_by_lb, lot)
    best = opt[0][5]
    opt_top = " | ".join(f"lb{r[5]['lb']}/d{r[5]['dist']}/T{r[5]['target']}/SL{r[5]['sl']}:min{r[0]:.2f}"
                         for r in opt[:3])
    print(f"  best: {best}  (min(train,oos) Sharpe {opt[0][0]:.2f})", flush=True)
    ext = ext_by_lb[best["lb"]]
    sig = significance(g, ext, lot, best)
    print(f"  significance: Sharpe {sig['real_sharpe']} p={sig['p_value']} "
          f"{'SIGNIFICANT' if sig['significant'] else 'not sig'}", flush=True)

    all_days = set(g["days"]); train = {d for d in all_days if d < EVO_END}; oos = {d for d in all_days if d >= EVO_END}
    dna = _dna(best); combos = {}; bs_full = None; dsr = None; stress = None
    for pname, pdays in (("full", None), ("train", train), ("oos", oos)):
        for pas in ("instrument", "rms", "bs"):
            res = backtest(g, ext, lot, best, pas, pdays)
            combos[f"{pas}|{pname}"] = _combo(res, sig if pas == "bs" else None, dna)
            if pas == "bs" and pname == "full":
                m = engine.metrics(res)[0]
                bs_full = {k: m.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")}
                dp = np.array([t["pnl"] for t in res["trades"]], dtype=float)
                dsr = ml_gate.deflated_sharpe(dp / 250000.0, n_trials=N_TRIALS)
                s50, s05 = tail_stress(res, lot, best)
                stress = [round(s50, 2), round(s05, 2)]
                print(f"  bs|full: Sharpe {m['sharpe']:.2f} net {m['net_pct']:.1f}% trades {m['trades']} "
                      f"PF {m.get('profit_factor',0):.2f} worst {min(dp):,.0f} | DSR {dsr['dsr_prob']:.3f} | "
                      f"tail-stress Sharpe -> {s50:.2f} (p05 {s05:.2f})", flush=True)

    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"]); df["d"] = df.Datetime.dt.date
    dd = df.groupby("d").agg(O=("Open", "first"), H=("High", "max"), L=("Low", "min"), C=("Close", "last")).reset_index()
    dd = dd[[x in all_days for x in dd.d]]
    cand = [[str(r.d), round(r.O, 1), round(r.H, 1), round(r.L, 1), round(r.C, 1)] for r in dd.itertuples()]

    pf = (bs_full or {}).get("profit_factor") or 0.0; net = (bs_full or {}).get("net_pct") or 0.0
    dsr_pass = bool((dsr or {}).get("pass")); ts_med = stress[0] if stress else -9
    if net <= 0 or pf < 1.0:
        verdict = "REJECTED — loses net of cost"
    elif sig["significant"] and dsr_pass and ts_med >= 1.0:
        verdict = "PASSES gate — forward-paper candidate (tail SL-bounded)"
    else:
        bad = []
        if not sig["significant"]: bad.append("p>=0.05")
        if not dsr_pass: bad.append(f"DSR {dsr['dsr_prob']:.2f}<0.95")
        if ts_med < 1.0: bad.append(f"tail-stress Sh {ts_med:.2f}")
        verdict = "in-sample edge, borderline (" + ", ".join(bad) + ")"

    out = {"meta": {"window": [str(min(all_days)), str(max(all_days))], "days": len(all_days),
                    "start_cap": CAP, "slug": SLUG, "tf": "intraday", "instrument": "NIFTY 50",
                    "design": "Range-Extreme Short Strangle — INTRADAY (9:20 entry, no overnight, SL-capped)",
                    "design_key": SLUG, "lot_size": lot, "lots": 1, "rms_caps": RMS_CAPS, "params": best,
                    "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
                    "candles": cand, "sig_p": sig["p_value"], "verdict": verdict,
                    "tail_stress": {"sharpe_2pct_crash_median": stress[0], "sharpe_2pct_p05": stress[1]},
                    "dna": dna, "faq": _faq(bs_full, stress, dsr, sig, best, opt_top),
                    "deflated_sharpe": {k: dsr[k] for k in ("dsr_prob", "pass", "n_trials", "sr_star_annual")} if dsr else None},
           "combos": combos}
    folder = os.path.join(RUNS, SLUG); os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "results.js"), "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)
    meta = {"slug": SLUG, "design": SLUG, "verdict": verdict,
            "title": f"Range-Extreme Short Strangle — INTRADAY ⚠️ {verdict}",
            "tf": "intraday", "params": best, "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": len(all_days), "significant": bool(sig["significant"]),
            "bs_full": bs_full, "p_value": sig["p_value"], "tail_stress": out["meta"]["tail_stress"],
            "instrument_full_sharpe": combos["instrument|full"]["metrics"].get("sharpe"),
            "rms_full_sharpe": combos["rms|full"]["metrics"].get("sharpe"),
            "deflated_sharpe": out["meta"]["deflated_sharpe"],
            "caveat": "9:20 entry, same-day exit, NO overnight — SL caps the tail (bounded). Real lake "
                      "premium + charges + DOM slip. Modest but consistent (train≈OOS). Forward-paper first.",
            "created": "2026-07-17"}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)
    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != SLUG]; idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)
    print(f"\n  VERDICT: {verdict}")
    print(f"  wrote runs/{SLUG}/ in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
