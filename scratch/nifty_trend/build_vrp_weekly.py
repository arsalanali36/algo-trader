"""Build the Lab dashboard for 02.05 — VRP Weekly Condor (mild-IV, forward-paper).

Produces runs/vrp_condor_weekly/{results.js,index.html,meta.json} + appends to runs/index.json
so the Strategy Registry shows its stats + a Lab ↗ link. Uses the EXACT deployed combo
(vrp_ungated_backtest: iron_condor body±3/wing±5, dte4 entry, iv-rank≥0.5, pre-expiry exit,
tp 0.5) on the real WEEK lake — the same numbers vrp_condor_weekly_trader.py forward-papers.

HONEST: significant=False on purpose. Raw p=0.032 clears p<0.05, BUT deflated-Sharpe FAILS
(multi-combo search + n=38, OOS n=10). So the registry shows ⚠️ weak / ⚠ FAIL, not WINNER —
this is a forward-paper candidate, not a proven edge. Run: python -X utf8 build_vrp_weekly.py
"""
import os, json, time
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import real_struct2 as r2
import optlake_load as ol
import ml_gate
from build_vrp import _combo
import vrp_ungated_backtest as vu

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
SLUG = "vrp_condor_weekly"
CAP = engine.START_CAP
EVO_END = vu.EVO_END
N_TRIALS = 24        # honest: modes×structs×wings×gates explored across this session


def _res_from_trades(trades, gross=False):
    """wrap vrp_ungated trade dicts into an engine.metrics-compatible res dict."""
    rows = []
    eq = CAP; eqc = []
    for t in sorted(trades, key=lambda x: x["exit_dt"]):
        pnl = (t["pnl"] + t["fee"] + t["slip"]) if gross else t["pnl"]
        eq += pnl
        eqc.append((pd.Timestamp(t["exit_dt"]), eq))
        rows.append(dict(side="short", entry_dt=t["entry_dt"], exit_dt=t["exit_dt"],
                         entry=round(t["credit"] / 65.0, 2), exit=0.0,
                         entry_spot=t["spot_in"], exit_spot=t["spot_out"],
                         points=t["points"], qty=65, gross=round(t["points"] * 65, 0),
                         fee=round(t["fee"], 0), pnl=round(pnl, 1),
                         bars=int(t["held_days"]), reason=t["reason"]))
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"]) if eqc else \
        pd.DataFrame({"Datetime": [pd.Timestamp(EVO_END)], "equity": [CAP]})
    return dict(trades=rows, equity=eqdf, final=eq, variant="vrp_weekly", mode="positional", params={})


DNA = {
    "Structure": "Weekly ATM iron condor — sell ATM±3, buy ATM±8 (defined-risk, 4 legs)",
    "Gate": "IV-rank ≥ 0.50 (60-day) — a MILD fear-premium filter",
    "Entry": "T-4 DTE (~Monday of the weekly cycle), 09:20, once per expiry",
    "Exit": "day BEFORE expiry (dodge 0DTE gamma), or 50%-of-credit target",
    "Hold": "positional — overnight to the day before expiry",
    "Edge": "VRP (implied>realized vol) harvested only on mildly-rich weeks",
    "Status": "FORWARD-PAPER — fails deflated-Sharpe, collecting real OOS",
    "Sizing": "1 lot, 1x (no leverage)",
}


def _faq(bs_full, dsr, sig):
    sh = (bs_full or {}).get("sharpe")
    return [
        ["Ye strategy kya hai?",
         "Har weekly cycle ke <b>T-4 DTE</b> (~Monday) pe, AGAR NIFTY ki IV-rank ≥0.50 ho "
         "(halka fear-premium filter), ek <b>ATM iron condor</b> bechte hain (ATM±3 CE/PE sell, "
         "ATM±8 BUY = defined-risk wings). Expiry se <b>ek din pehle</b> band kar dete hain "
         "(0DTE gamma se bachne ko), ya 50% credit target pe. Edge = VRP (implied vol > realized)."],
        [f"Sharpe {sh:.1f} / PF acha hai — deploy kar sakte?" if sh else "Deploy-ready hai?",
         "<b>NAHI.</b> Ye Task-6 research ka 'best honest combo' hai — in-sample PF ~2.1, high Sharpe, "
         "p=0.032, aur raw OOS split bhi survive karta hai (Sharpe ki value annualization-method pe "
         "depend karti hai; ye flattered zone me hai). <b>PAR deflated-Sharpe FAIL "
         f"karta hai</b> ({(dsr or {}).get('dsr_prob','?')} vs 0.95 bar) jab meri multi-combo search "
         "+ chhota sample (n=38, OOS n=10) ka imaandaar haircut lagta hai. Isliye ye <b>forward-paper "
         "candidate</b> hai, proven edge NAHI. Registry me isiliye ⚠️ weak / FAIL dikhta hai."],
        ["Phir live kyun chala rahe ho (paper)?",
         "Sirf <b>asli fresh out-of-sample</b> jama karne ko. Backtest ka DSR-fail ka matlab 'shayad "
         "search-artifact hai'. Kuch mahine forward-paper karke asli T-4 trades ikatthe honge, phir "
         "<b>DSR dobara</b> chalayenge fresh data pe — tab pata chalega asli edge tha ya luck. Tab tak "
         "koi real paisa nahi."],
        ["Ungated (bina gate) kaam kyun nahi karta?",
         "Task 6 ne prove kiya: bina IV-gate ke weekly condor <b>net-negative</b> (PF 0.88-0.99). "
         "Edge sirf high-IV cycles me hai; aadhe cycles (low-IV) paisa khaate hain. Ye combo bas "
         "un low-IV cycles ko gate kar deta hai + expiry-day gamma dodge karta hai."],
    ]


def main():
    t0 = time.time()
    bs.SLIP_ENABLED = True; bs.SLIP_MULT = 1.0
    g = r2.grid(vu.FLAG, vu.TF)
    ivr = ol.iv_rank_daily(vu.FLAG, vu.TF, vu.IV_LOOKBACK)
    lot = bs.get_nifty_lot() or 65
    print("building runs/vrp_condor_weekly/ (deployed combo) ...", flush=True)

    # THE DEPLOYED COMBO — must match vrp_condor_weekly_trader.py config exactly
    trades = vu.backtest(g, ivr, lot, mode="dte4", struct="iron_condor", wing=5, short_off=3,
                         tp_frac=0.5, sl_frac=None, iv_min=0.50, exit_before_dte=1)
    tr_train = [t for t in trades if dt.date.fromisoformat(t["entry_dt"]) < EVO_END]
    tr_oos   = [t for t in trades if dt.date.fromisoformat(t["entry_dt"]) >= EVO_END]

    sig = vu.significance(trades)
    m = vu.metrics(trades)
    dsr = vu.dsr_check(trades, N_TRIALS)
    # HONEST override: raw p passes p<0.05 but the FULL gate (deflated-Sharpe) FAILS →
    # the dashboard's "GENUINE EDGE" banner must NOT show green. significant=False so the
    # lab page matches the registry (⚠️ weak / FAIL), with a note explaining why.
    sig_display = {**sig, "significant": False,
                   "note": (f"p={sig['p_value']} clears p<0.05, BUT deflated-Sharpe FAILS "
                            f"({(dsr or {}).get('dsr_prob','?')} vs 0.95 bar) once the multi-combo "
                            f"search + tiny sample (n={m['n']}, OOS n=10) are accounted for. "
                            f"FORWARD-PAPER candidate, NOT a proven edge.")}
    print(f"  n={m['n']} PF={m['pf']} Sharpe={m['sharpe']} net={m['net_pct']}% p={sig['p_value']} "
          f"DSR={dsr['dsr_prob'] if dsr else '?'} (pass={dsr['pass'] if dsr else '?'})", flush=True)

    combos = {}
    for pname, subset in (("full", trades), ("train", tr_train), ("oos", tr_oos)):
        for pas, gross in (("instrument", True), ("rms", False), ("bs", False)):
            res = _res_from_trades(subset, gross=gross)
            combos[f"{pas}|{pname}"] = _combo(res, g, sig_display if (pas == "bs" and pname == "full") else None, DNA)

    bs_full_m = engine.metrics(_res_from_trades(trades, gross=False))[0]
    bs_full = {k: bs_full_m.get(k) for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")}

    out = {"meta": {"window": [min(t["entry_dt"] for t in trades), max(t["exit_dt"] for t in trades)],
                    "days": len(trades), "start_cap": CAP, "lot_size": lot, "lots": 1,
                    "design": "02.05 VRP Weekly Condor — mild-IV gate, T-4 entry, pre-expiry exit (FORWARD-PAPER)",
                    "design_key": "vrp_condor_weekly", "slug": SLUG, "tf": "weekly", "instrument": "NIFTY 50",
                    "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
                    "sig_p": sig["p_value"], "dna": DNA, "faq": _faq(bs_full, dsr, sig),
                    "deflated_sharpe": dsr},
           "combos": combos}

    folder = os.path.join(RUNS, SLUG); os.makedirs(folder, exist_ok=True)
    open(os.path.join(folder, "results.js"), "w", encoding="utf-8").write(
        "window.RESULTS = " + json.dumps(out, default=float) + ";")
    dash = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    # HONEST banner patches — this page ALWAYS fails the full DSR gate despite raw p<0.05, so the
    # template's binary significant⟺p<0.05 assumption produces misleading text. Patch ONLY this run's
    # copied HTML (shared template + other runs untouched): (1) the static "● GENUINE EDGE" pill →
    # forward-paper label; (2) the infobar's not-significant branch says "p ≥ 0.05" (false here) →
    # accurate "p<0.05 but deflated-Sharpe gate FAILS → forward-paper".
    dash = dash.replace(
        '<span class="livechip" id="livechip">● GENUINE EDGE</span>',
        '<span class="livechip" id="livechip" style="background:#3a2e0c;color:#e8c14a;border-color:#5c4a12">● FORWARD-PAPER · fails DSR</span>')
    _a = "'&lt;':'≥'} 0.05)${s&&s.significant?' — a REAL directional edge, not beta':' — treat as noise'}"
    _b = "'&lt;':'&lt;'} 0.05, but the deflated-Sharpe gate FAILS)${s&&s.significant?'':' — forward-paper only, NOT a proven edge'}"
    assert dash.count(_a) == 1, f"banner anchor not found (count={dash.count(_a)}) — template changed?"
    dash = dash.replace(_a, _b)
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(dash)

    meta = {"slug": SLUG, "design": "vrp_condor_weekly",
            "title": "02.05 - VRP Weekly Condor (mild-IV) ⚠️ FORWARD-PAPER (fails DSR)",
            "tf": "weekly", "instrument": "NIFTY 50", "lot_size": lot,
            "window": out["meta"]["window"], "days": len(trades),
            "significant": False,                 # HONEST — fails deflated-Sharpe (raw p=0.032 only)
            "bs_full": bs_full, "p_value": sig["p_value"],
            "deflated_sharpe": dsr,
            "caveat": "Best honest combo from Task-6 follow-up. In-sample PF 2.10/Sharpe 2.14/p=0.032, "
                      "survives raw OOS — but FAILS deflated-Sharpe (multi-combo search + n=38/OOS n=10). "
                      "Forward-paper only; re-run DSR on fresh forward samples before any live money.",
            "created": "2026-07-20"}
    json.dump(meta, open(os.path.join(folder, "meta.json"), "w"), indent=2)

    idx_path = os.path.join(RUNS, "index.json")
    idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    idx = [x for x in idx if x.get("slug") != SLUG]; idx.append(meta)
    json.dump(idx, open(idx_path, "w"), indent=2)
    print(f"\nwrote runs/{SLUG}/ (significant=False, honest) in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    main()
