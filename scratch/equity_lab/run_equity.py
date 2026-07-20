"""Equity lab run producer. Runs a strategy through the engine and emits a REGISTRY-COMPATIBLE
run so its Sharpe/MaxDD/Win%/Significance fill in the Strategy Registry (same as the options
lab): writes runs/<slug>/{meta.json, results.js, index.html} + appends runs/index.json.

Usage:
  python run_equity.py --strategy momentum --regime 200dma --name regime_momentum \
         --title "10 - Regime-Momentum Basket (F&O stocks)" --tf monthly
  python run_equity.py --strategy sector_rotation --regime 200dma --name sector_rot_eq
  python run_equity.py --strategy selloff_dip --name selloff_dip_eq

--regime 200dma gates the basket to NIFTY>200-DMA (cash otherwise). --idle-rf sets cash yield.
Paths default to the same runs/ dir the registry reads (/lab/runs). Panel from --panel-dir.
"""
import os, sys, json, argparse
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import engine as E
import strategies as S
import regime as R

# runs/ that the registry + hub read (served at /lab/runs). Overridable.
DEFAULT_RUNS = os.environ.get("EQ_RUNS_DIR") or os.path.normpath(
    os.path.join(HERE, "..", "nifty_trend", "runs"))
DEFAULT_PANEL = os.environ.get("EQ_PANEL_DIR") or HERE


def _svg(points, w=680, h=170):
    if len(points) < 2:
        return ""
    lo, hi = min(points), max(points); rng = (hi - lo) or 1
    step = w / (len(points) - 1)
    pts = " ".join(f"{i*step:.1f},{h-(v-lo)/rng*(h-24)-12:.1f}" for i, v in enumerate(points))
    return (f'<svg width="{w}" height="{h}" style="max-width:100%">'
            f'<polyline points="{pts}" fill="none" stroke="#1f6feb" stroke-width="2"/></svg>')


def _html(meta, res, extra):
    m = res["metrics"]; tr = res.get("train", {}); oo = res.get("oos", {})
    eq = res["eq_net"]
    monthly = list((eq.resample("ME").last() / eq.iloc[0]).dropna().values)
    def f(x, s=""):
        return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.2f}{s}"
    honest = extra.get("honest", {})
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{meta['title']}</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:system-ui,sans-serif;margin:0;padding:24px}}
.wrap{{max-width:820px;margin:0 auto}} h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#8b949e;font-size:13px;margin-bottom:18px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin-bottom:14px}}
.row{{display:flex;flex-wrap:wrap;gap:12px}} .row .card{{flex:1;min-width:120px;text-align:center}}
.big{{font-size:22px;font-weight:700}} .lbl{{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{padding:5px 8px;border-bottom:1px solid #30363d;text-align:right}} th:first-child,td:first-child{{text-align:left}}
.pos{{color:#3fb950}} .neg{{color:#f85149}} .note{{color:#8b949e;font-size:12px;line-height:1.5}} .warn{{color:#d29922}}</style></head>
<body><div class="wrap">
<h1>📊 {meta['title']}</h1>
<div class="sub">{meta['strategy']} · {meta['tf']} · {meta['instrument']} · {meta['window'][0]}..{meta['window'][1]} · equity lab</div>
<div class="row">
 <div class="card"><div class="lbl">Sharpe</div><div class="big {'pos' if (m['sharpe'] or 0)>=1 else ''}">{f(m['sharpe'])}</div></div>
 <div class="card"><div class="lbl">CAGR (net)</div><div class="big">{f(m['cagr']*100 if m['cagr'] else None,'%')}</div></div>
 <div class="card"><div class="lbl">Max DD</div><div class="big neg">{f(m['maxdd']*100 if m['maxdd'] else None,'%')}</div></div>
 <div class="card"><div class="lbl">Win% (mo)</div><div class="big">{f(m['win_rate'],'%')}</div></div>
 <div class="card"><div class="lbl">PF</div><div class="big">{f(m['pf'])}</div></div>
</div>
<div class="card"><div class="lbl">Equity (₹1L start, net of cost + STCG) → ₹{res['lakh']:,.0f}</div>{_svg(monthly)}</div>
<div class="card"><div class="lbl" style="margin-bottom:6px">Train / OOS (overfit guard)</div>
 <table><tr><th>Period</th><th>Sharpe</th><th>CAGR</th><th>Max DD</th></tr>
 <tr><td>Train</td><td>{f(tr.get('sharpe'))}</td><td>{f((tr.get('cagr') or 0)*100,'%')}</td><td>{f((tr.get('maxdd') or 0)*100,'%')}</td></tr>
 <tr><td>OOS</td><td>{f(oo.get('sharpe'))}</td><td>{f((oo.get('cagr') or 0)*100,'%')}</td><td>{f((oo.get('maxdd') or 0)*100,'%')}</td></tr></table></div>
<div class="card"><div class="lbl" style="margin-bottom:6px">Significance</div>
 <div class="note">Permutation vs random baskets: real Sharpe {f(m['sharpe'])} vs random {f(res.get('null_sharpe'))} → <b>p = {res.get('p_value','—')}</b>
 {'✅ PASS (p<0.05)' if (res.get('p_value',1)<0.05) else '⚠ weak'}. In-market {res['in_market']*100:.0f}% of rebalances · turnover {res['turnover']*100:.0f}%/mo.</div></div>
{extra.get('honest_html','')}
<div class="note" style="text-align:center;margin-top:16px">Delivery-equity backtest · 10bps/side + STCG 20% · 1x no-leverage. Research; not investment advice.</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=list(S.REGISTRY))
    ap.add_argument("--regime", default="none", choices=["none", "200dma"])
    ap.add_argument("--name", required=True, help="slug")
    ap.add_argument("--title", default=None)
    ap.add_argument("--tf", default="monthly")
    ap.add_argument("--freq", default="M", choices=["M", "W"])
    ap.add_argument("--idle-rf", type=float, default=0.0, help="cash yield when regime OFF")
    ap.add_argument("--runs-dir", default=DEFAULT_RUNS)
    ap.add_argument("--panel-dir", default=DEFAULT_PANEL)
    ap.add_argument("--template", default=None, help="path to dashboard_intraday.html")
    ap.add_argument("--sig-n", type=int, default=500)
    ap.add_argument("--honest-drop", type=int, default=0,
                    help="also compute a survivorship-haircut variant dropping top-K all-time gainers (context)")
    args = ap.parse_args()

    close = E.load_panel(os.path.join(args.panel_dir, "panel_close.csv"))
    nifty = pd.read_csv(os.path.join(args.panel_dir, "nifty_daily.csv"),
                        parse_dates=["Date"]).set_index("Date")["Close"].sort_index()
    weight_fn, rand_fn = S.REGISTRY[args.strategy]()
    regime_fn = R.dma_regime(nifty, 200) if args.regime == "200dma" else None

    print(f"Running {args.strategy} (regime={args.regime}) on {close.shape[1]} stocks...", flush=True)
    res = E.backtest(close, weight_fn, freq=args.freq, regime_fn=regime_fn,
                     rf_annual=args.idle_rf, random_weight_fn=rand_fn, sig_n=args.sig_n)
    if res is None:
        print("no data / no run", flush=True); return
    m = res["metrics"]
    print(f"  Sharpe {m['sharpe']:.2f}  CAGR {m['cagr']*100:.1f}%  maxDD {m['maxdd']*100:.1f}%  "
          f"Win {m['win_rate']:.0f}%  PF {m['pf']:.2f}  p={res.get('p_value','?')}  ₹1L→₹{res['lakh']:,.0f}", flush=True)

    # optional survivorship-haircut context variant (drop top-K all-time gainers)
    extra = {}
    if args.honest_drop:
        tot = (close.ffill().iloc[-1] / close.bfill().iloc[0]).dropna().sort_values(ascending=False)
        excl = set(tot.head(args.honest_drop).index)
        wf2, _ = S.REGISTRY[args.strategy]()
        # wrap weight_fn to exclude survivors
        def wf_excl(c, rb):
            W = wf2(c, rb)
            return {rd: {s: w for s, w in d.items() if s not in excl} for rd, d in W.items()}
        res2 = E.backtest(close, wf_excl, freq=args.freq, regime_fn=regime_fn,
                          rf_annual=args.idle_rf, random_weight_fn=None, mc=False)
        if res2:
            h = res2["metrics"]
            extra["honest_html"] = (
                f'<div class="card"><div class="lbl" style="margin-bottom:6px">⚠️ Survivorship haircut '
                f'(drop top-{args.honest_drop} all-time gainers — honest deployable estimate)</div>'
                f'<div class="note warn">Sharpe {h["sharpe"]:.2f} · CAGR {h["cagr"]*100:.1f}% · maxDD {h["maxdd"]*100:.1f}% · '
                f'₹1L→₹{res2["lakh"]:,.0f}. The headline above trades the full current F&O list (what the bot does), '
                f'but that list is survivorship-biased; this is the more honest forward expectation.</div></div>')
            print(f"  [honest drop-{args.honest_drop}] Sharpe {h['sharpe']:.2f}  CAGR {h['cagr']*100:.1f}%  ₹1L→₹{res2['lakh']:,.0f}", flush=True)

    # ---- write run ----
    slug = args.name
    rdir = os.path.join(args.runs_dir, slug)
    os.makedirs(rdir, exist_ok=True)
    title = args.title or f"{args.strategy} ({args.regime})"
    meta = dict(slug=slug, strategy=args.strategy, regime=args.regime, tf=args.tf,
                instrument=f"F&O stocks ({close.shape[1]})", window=res["window"],
                title=title, n_rebal=res["n_rebal"])
    # meta.json (equity-lab detail)
    with open(os.path.join(rdir, "meta.json"), "w") as f:
        json.dump({**meta, "metrics": m, "train": res["train"], "oos": res["oos"],
                   "p_value": res.get("p_value"), "mc": res.get("mc"),
                   "in_market": res["in_market"], "turnover": res["turnover"],
                   "lakh": res["lakh"]}, f, indent=2, default=str)
    # results.js in the SHARED options-lab schema -> the SAME rich dashboard template
    # renders it (Performance/DD/Underwater/Monthly-heatmap/Distribution/Trades/MC/
    # Significance/DNA/Info). Legacy full/train/oos combos, no meta.passes (no BS toggle).
    import emit_results as ER
    rebal = E.rebalance_dates(close.index, args.freq)
    sig = dict(real_sharpe=round(m["sharpe"], 3), p_value=round(float(res.get("p_value", 1)), 4),
               null_mean=round(float(res.get("null_sharpe", 0) or 0), 3),
               null_p95=round(float(res.get("null_p95", 0) or 0), 3),
               n_perm=args.sig_n, significant=bool(res.get("p_value", 1) < 0.05))
    dna = {"strategy": args.strategy, "regime": args.regime, "freq": args.freq,
           "lookback_m": 12, "basket": "top-decile", "skip_days": 5, "rebalance": args.tf}
    results = ER.build_results(res["eq_net"], nifty, rebal, dna, sig, title, args.tf, meta["instrument"])
    js = "window.RESULTS=" + json.dumps(results, default=str) + ";"
    # the template's <script src> is results_intraday.js — write THAT (results.js too for parity)
    for fn in ("results_intraday.js", "results.js"):
        with open(os.path.join(rdir, fn), "w", encoding="utf-8") as f:
            f.write(js)
    # index.html = the shared dashboard template (copied per run, exactly like the options lab)
    tpl = args.template or os.environ.get("EQ_TEMPLATE") or os.path.join(
        HERE, "..", "nifty_trend", "dashboard_intraday.html")
    import shutil
    shutil.copyfile(tpl, os.path.join(rdir, "index.html"))
    # BS-full metrics for the registry entry come from the full combo
    fm = results["combos"]["full"]["metrics"]

    # ---- append to runs/index.json (what the registry reads: bs_full + significant + tf) ----
    idx_path = os.path.join(args.runs_dir, "index.json")
    try:
        idx = json.load(open(idx_path)) if os.path.exists(idx_path) else []
    except Exception:
        idx = []
    entry = dict(slug=slug, kind="equity", title=title, tf=args.tf,
                 instrument=meta["instrument"], window=res["window"],
                 significant=bool(res.get("p_value", 1) < 0.05),
                 p_value=round(float(res.get("p_value", 1)), 4),
                 bs_full=dict(sharpe=fm["sharpe"], net_pct=fm["net_pct"], maxdd=fm["maxdd"],
                              win_rate=fm["win_rate"], trades=fm["trades"],
                              profit_factor=fm["profit_factor"]))
    idx = [e for e in idx if e.get("slug") != slug] + [entry]
    with open(idx_path, "w") as f:
        json.dump(idx, f, indent=1, default=str)
    print(f"\nWrote runs/{slug}/ + registry entry (index.json). Registry column-ready.", flush=True)


if __name__ == "__main__":
    main()
