"""00 — CONTINUOUS BACKTEST: deployed runs ko roz aaj tak extend karo (frozen params).

User request 2026-07-21: "Backtest continuous chahiye — har roz chalta rahe, BS model
pe, taaki live se din-wise overlap kar sakun. 00.04 long straddle me sirf 9 tarik tak
ka backtest hai — wo saare din ka rahe."

Kya karta hai (koi order/risk/live path NAHI — pure research/reporting):
  1. NIFTY 1-min store ko aaj tak update (data_fetch.py subprocess — Dhan token VPS pe).
  2. Har target run ke meta.json ke FROZEN params se (koi re-optimize/re-screen NAHI —
     wahi validated config, sirf data window aaj tak) usi family ka EXISTING emitter
     dobara chalao (Rule 6B — koi naya backtest path nahi):
       - spot-design family  → run_hunt.write_run       (mid_orb, orbst, chainzone, ...)
       - structure family    → run_structure.write_run  (long_straddle, dvert, backspread, ...)
         (meta.design me "/" = structure: "long_straddle/orb_break")
  3. Original hunt ki identity PRESERVE karo — created / significant / p_value /
     deflated_sharpe / deployed / real_cost etc. emitter ke naye meta se overwrite nahi
     hote (post-patch), + "extended" block (last_extended, orig_window, bs_full_at_hunt).

⚠️ HONESTY NOTES:
  - p_value/significance ORIGINAL hunt ke hain (frozen window pe teste) — extension unhe
    re-test nahi karti. bs|full metrics naye data ke saath badalte hain = yahi feature hai
    (live-overlap), par report card me "at_hunt" snapshot extended block me preserved hai.
  - train/oos split fractional (68% bars) hai — window badhne se boundary ~1 din/3 din
    sarakti hai. Params frozen hain isliye overfit-leak nahi; train/oos display-only drift.
  - Sirf NIFTY intraday runs (v1). BankNifty/VRP/overnight/meanrev apne producers se —
    skip hone pe LOG hota hai, chupchaap nahi (no silent caps).

Usage (VPS pe — token wahan; systemd algo-btextend.timer Mon..Fri 16:25 IST):
    venv/bin/python scratch/nifty_trend/daily_extend.py               # deployed runs
    venv/bin/python scratch/nifty_trend/daily_extend.py --slugs long_straddle_orb
    venv/bin/python scratch/nifty_trend/daily_extend.py --all         # sab supported
    venv/bin/python scratch/nifty_trend/daily_extend.py --no-fetch    # data update skip
"""
import os
import sys
import json
import time
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RUNS = os.path.join(HERE, "runs")

import pandas as pd  # noqa: E402

import engine  # noqa: E402,F401  (emitters need it importable)
import intraday_engine as ie  # noqa: E402
import bs_option as bs_mod  # noqa: E402
import run_hunt  # noqa: E402
import run_structure  # noqa: E402
import hunt_guard  # noqa: E402

# Ye runs is extender ke bas ke NAHI hain (alag engine/data) — naam + wajah, taaki
# "kyun nahi badha" kabhi mystery na bane. Inke apne producers hain.
UNSUPPORTED = {
    "banknifty_hunt": "BANKNIFTY data (alag store) — v2",
    "overnight_orb_nifty": "apna producer build_overnight_orb.py (monthly-ATM positional)",
    "vrp_overnight_condor": "vrp_ungated_backtest family",
    "vrp_condor_weekly": "vrp_ungated_backtest family",
    "range_strangle": "apna builder (range-extreme)",
    "range_strangle_condor": "apna builder",
    "range_strangle_intraday": "apna builder",
    "meanrev_nifty": "build_meanrev (1D monthly-roll)",
    "regime_momentum": "equity_lab family (monthly)",
    "gamma_scalp": "rejected run (real_struct2)",
    "shortvol_ironfly": "rejected/corrected run (real_struct2 real-lake)",
    "chain_zone_naked": "research-reject snapshot — extend mat karo",
    "chain_zone_credit": "research-reject snapshot",
    "chain_zone_positional": "positional_engine family",
    "pivot_continuation": "borderline research snapshot (not deployed)",
    "long_strangle_orb": "research snapshot (not deployed)",
    "arschain_live_engine": "live-engine replay snapshot",
}

INTRADAY_TFS = {"1m", "3m", "5m", "15m", "30m"}


def _load_results_meta(slug):
    """Original results.js ka meta + bs|full significance/opt_table (identity reuse)."""
    p = os.path.join(RUNS, slug, "results.js")
    txt = open(p, encoding="utf-8").read()
    obj = json.loads(txt[txt.index("=") + 1:].strip().rstrip(";"))
    combo = obj.get("combos", {}).get("bs|full", {})
    return obj.get("meta", {}), combo.get("significance") or {}, combo.get("opt_table") or []


def _fetch_data():
    print("[data] updating NIFTY 1-min store via data_fetch.py ...", flush=True)
    r = subprocess.run([sys.executable, os.path.join(HERE, "data_fetch.py")],
                       cwd=HERE, capture_output=True, text=True, timeout=1800)
    tail = "\n".join((r.stdout or "").strip().splitlines()[-4:])
    print(tail, flush=True)
    if r.returncode != 0:
        print("🔴 [data] data_fetch FAILED (token expire? network?) — extending on the "
              "data already on disk. stderr tail:", flush=True)
        print("\n".join((r.stderr or "").strip().splitlines()[-4:]), flush=True)
    return r.returncode == 0


def _opt_rows_back(opt_table):
    """results.js opt_table rows → emitter ke expected winner['opt'] keys (reverse map)."""
    out = []
    for c in opt_table:
        out.append(dict(params=c.get("params", {}), exit=c.get("exit", ""),
                        train_sharpe=c.get("train_sharpe", 0), oos_sharpe=c.get("oos_sharpe", 0),
                        oos_net=c.get("net", 0), oos_dd=c.get("dd", 0),
                        oos_trades=c.get("trades", 0)))
    return out


def _post_patch_meta(slug, orig_meta, rmeta_window_before):
    """Emitter ne meta.json/index.json fresh likhe — original identity wapas merge karo.
    Preserve: created/significant/p_value/deflated_sharpe/deployed/real_cost/shippable/
    data_integrity + koi bhi extra key jo original me thi. Update: window/days/bs_full/
    *_sharpe (naye data ke). Add: extended block."""
    mpath = os.path.join(RUNS, slug, "meta.json")
    new_meta = json.load(open(mpath, encoding="utf-8"))
    merged = dict(orig_meta)          # original identity base
    for k in ("window", "days", "bs_full", "instrument_full_sharpe", "rms_full_sharpe",
              "tf", "params", "exit", "lot_size", "instrument", "title", "design"):
        if k in new_meta:
            merged[k] = new_meta[k]
    ext = dict(orig_meta.get("extended") or {})
    if "orig_window" not in ext:
        ext["orig_window"] = rmeta_window_before
        ext["bs_full_at_hunt"] = orig_meta.get("bs_full")
        ext["first_extended"] = time.strftime("%Y-%m-%d")
    ext["last_extended"] = time.strftime("%Y-%m-%d %H:%M")
    merged["extended"] = ext
    json.dump(merged, open(mpath, "w", encoding="utf-8"), indent=2)

    idx_path = os.path.join(RUNS, "index.json")
    with hunt_guard.flock("runs_index"):
        idx = json.load(open(idx_path, encoding="utf-8"))
        idx = [x for x in idx if x.get("slug") != slug]
        idx.append(merged)
        json.dump(idx, open(idx_path, "w", encoding="utf-8"), indent=2)
    return merged


def extend_one(slug, d1m_cache):
    mpath = os.path.join(RUNS, slug, "meta.json")
    if not os.path.exists(mpath):
        print(f"  [{slug}] SKIP — meta.json nahi mila", flush=True)
        return False
    orig_meta = json.load(open(mpath, encoding="utf-8"))
    rmeta, sig, opt_table = _load_results_meta(slug)
    window_before = list(orig_meta.get("window") or rmeta.get("window") or [])

    tf = str(orig_meta.get("tf", ""))
    if tf not in INTRADAY_TFS:
        print(f"  [{slug}] SKIP — tf '{tf}' intraday extender ke scope me nahi", flush=True)
        return False
    if "BANKNIFTY" in str(rmeta.get("instrument", "")).upper():
        print(f"  [{slug}] SKIP — BANKNIFTY data v2", flush=True)
        return False
    lots = int(rmeta.get("lots") or 1)
    design = str(orig_meta.get("design", ""))
    params = orig_meta.get("params") or {}
    if not params:
        print(f"  [{slug}] SKIP — meta me params nahi (frozen config ke bina extend nahi)",
              flush=True)
        return False
    if not sig:
        # identity ke bina bhi extend ho sakta hai — significance block original se aata
        # hai; nahi mila to minimal frozen label
        sig = dict(p_value=orig_meta.get("p_value"), significant=orig_meta.get("significant"))

    if tf not in d1m_cache:
        d1m_cache[tf] = ie.resample(d1m_cache["_1m"], tf)
    d = d1m_cache[tf]
    new_end = str(d.Datetime.iloc[-1])[:10]
    if window_before and new_end <= window_before[1]:
        print(f"  [{slug}] up-to-date (data end {new_end} <= window {window_before[1]})",
              flush=True)
        return False

    print(f"\n=== [{slug}] extend {window_before[1] if window_before else '?'} → {new_end} "
          f"(frozen params, {'structure' if '/' in design else 'spot-design'}) ===", flush=True)

    if "/" in design:                       # structure family: "long_straddle/orb_break"
        struct, sig_name = design.split("/", 1)
        winner = dict(sig_name=sig_name, struct=struct, tf=tf, params=params,
                      sig=dict(sig), opt=_opt_rows_back(opt_table))
        dd = (d.set_index("Datetime").resample("1D")
              .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
        sigma_map = bs_mod.realised_vol_map(dd.Close)
        lot = bs_mod.get_nifty_lot()
        run_structure.write_run(slug, winner, d, dd, sigma_map, lot, lots=lots)
    else:                                    # spot-design family (run_hunt)
        if design not in ie.DESIGN_GRID:
            print(f"  [{slug}] SKIP — design '{design}' DESIGN_GRID me nahi", flush=True)
            return False
        winner = dict(design=design, tf=tf, exit=orig_meta.get("exit", ""),
                      params=params, sig=dict(sig), opt=_opt_rows_back(opt_table))
        run_hunt.write_run(slug, winner, d, lots=lots)

    merged = _post_patch_meta(slug, orig_meta, window_before)
    print(f"  [{slug}] ✅ extended to {merged['window'][1]} "
          f"(bs_full sharpe {merged.get('bs_full', {}).get('sharpe')})", flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slugs", default="", help="comma list; default = deployed runs")
    ap.add_argument("--all", action="store_true", help="sab supported runs (deployed na bhi ho)")
    ap.add_argument("--no-fetch", action="store_true", help="data_fetch skip (offline test)")
    args = ap.parse_args()

    t0 = time.time()
    if not args.no_fetch:
        _fetch_data()

    idx = json.load(open(os.path.join(RUNS, "index.json"), encoding="utf-8"))
    if args.slugs:
        targets = [s.strip() for s in args.slugs.split(",") if s.strip()]
    else:
        targets = [r["slug"] for r in idx
                   if (args.all or r.get("deployed")) and r.get("slug") not in UNSUPPORTED]
        skipped = [r["slug"] for r in idx if r.get("deployed") and r["slug"] in UNSUPPORTED]
        for s in skipped:
            print(f"  [{s}] SKIP (deployed but unsupported): {UNSUPPORTED[s]}", flush=True)
    if not targets:
        print("kuch extend karne ko nahi — targets empty", flush=True)
        return

    print(f"targets: {', '.join(targets)}", flush=True)
    d1m_cache = {"_1m": ie.load_1m()}
    done = 0
    for slug in targets:
        # parallel-hunt guard: agar koi aur session isi slug pe build kar raha hai to skip
        try:
            hunt_guard.register(slug, script="daily_extend.py")
        except (SystemExit, Exception) as e:      # register duplicate pe SystemExit deta hai
            print(f"  [{slug}] SKIP — slug doosri hunt ke paas ({e})", flush=True)
            continue
        try:
            if extend_one(slug, d1m_cache):
                done += 1
        except Exception as e:
            print(f"🔴 [{slug}] FAILED: {e}", flush=True)
        finally:
            try:
                hunt_guard.deregister(slug)
            except Exception:
                pass
    print(f"\n[daily_extend] {done}/{len(targets)} runs extended in {time.time()-t0:.0f}s",
          flush=True)


if __name__ == "__main__":
    main()
