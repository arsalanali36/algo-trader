"""build_bnf_positional_run.py — emit runs/bnf_strangle_hedged/ (RESULTS_SCHEMA) from
the POSITIONAL max_hold=1 hedged-strangle backtest, so the Strategy Registry (02.10.01)
+ Lab hub + Stats backtest calendar show Sharpe/MaxDD/Win/Trades/positional-TF for the
deployed positional spec. REAL premium shorts + BS wings + date-aware Zerodha charges +
DOM slip, 5 lots. Mirrors weekly_ironfly/build_run.py metric helpers (Rule 6B)."""
import os, io, json, math, warnings
warnings.filterwarnings("ignore")
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); import sys; sys.path.insert(0, HERE)
import bnf_hedged_backtest as bt, bnf_920_strangle_intraday as base

RUNS = os.path.join(HERE, "runs")
SLUG = "bnf_strangle_hedged"
LOT_HINT, LOTS = 35, 5
START_CAP = 500_000                     # 02.10.01 tier (~Rs4-5L)

g = base.load_grid()
df = bt.run_positional(g, 6, 5, 4000.0, 8000.0, LOTS, max_hold_days=1, exp_squareoff_days=2)
df = df.sort_values("day").reset_index(drop=True)
rows = [dict(date=str(r["day"]), exit_date=str(r["exit_day"]), net=float(r["net"]), gross=float(r["gross"]),
             charges=float(r["fee"] + r["slip"]), entry_credit=float(r["entry_credit"]),
             spot0=float(r["spot0"]), atm=float(r["atm"]), qty=int(r["qty"]), reason=str(r["reason"]))
        for _, r in df.iterrows()]


def _streaks(net):
    mx = ml = cw = cl = 0
    for x in net:
        if x > 0: cw += 1; cl = 0
        else: cl += 1; cw = 0
        mx = max(mx, cw); ml = max(ml, cl)
    return mx, ml


def _metrics(rr):
    net = np.array([r["net"] for r in rr], float); n = len(net)
    eq = np.cumsum(net); peak = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    dd_abs = (eq - peak).min()
    gp = net[net > 0].sum(); gl = -net[net < 0].sum(); wins = net > 0
    yrs = max(1.0, (int(rr[-1]["date"][:4]) - int(rr[0]["date"][:4])) + 1)
    sharpe = round((net.mean() / net.std()) * math.sqrt(n / yrs), 3) if net.std() else 0.0
    downside = net[net < 0]
    sortino = round((net.mean() / downside.std()) * math.sqrt(n / yrs), 3) if len(downside) and downside.std() else sharpe
    ann = round(net.sum() / START_CAP * 100 / yrs, 3); maxdd = round(dd_abs / START_CAP * 100, 3)
    ws, ls = _streaks(net)
    return dict(trades=n, net_pct=round(net.sum() / START_CAP * 100, 3), net_abs=round(net.sum()),
                final_cap=round(START_CAP + net.sum()), start_cap=START_CAP, sharpe=sharpe, sortino=sortino,
                calmar=round(ann / abs(maxdd), 2) if maxdd else 0.0, maxdd=maxdd, underwater_days=0,
                years=round(yrs, 1), win_rate=round(100 * wins.mean(), 3),
                wl_ratio=round((wins.sum() / max(1, (~wins).sum())), 2), profit_factor=round(gp / gl, 3) if gl else 99.0,
                expectancy=round(net.mean(), 1), avg_win=round(net[net > 0].mean() if wins.any() else 0, 1),
                avg_loss=round(net[net < 0].mean() if (~wins).any() else 0, 1),
                largest_win=round(net.max()), largest_loss=round(net.min()),
                total_wins=int(wins.sum()), total_losses=int((~wins).sum()), win_streak=ws, loss_streak=ls, avg_bars=0,
                win_long=0.0, win_short=round(100 * wins.mean(), 1), pct_long=0.0, pct_short=100.0,
                fees=round(sum(r["charges"] for r in rr)), annual_return=ann,
                trades_per_day=round(n / max(1, len({r["date"] for r in rr})), 2),
                trades_per_month=round(n / (yrs * 12), 2))


def _all_trades(rr):
    out = []
    for r in rr:
        credit = r["entry_credit"]; net_u = r["net"] / max(1, r["qty"])
        out.append(dict(side="short", opt_type="condor CE+PE", strike=round(r["atm"]),
                        entry_dt=r["date"] + " 09:20", exit_dt=r["exit_date"] + " 14:55",
                        entry_spot=r["spot0"], exit_spot=r["spot0"], points=0,
                        entry_prem=round(credit, 2), exit_prem=round(credit - net_u, 2), qty=r["qty"],
                        gross=round(r["gross"]), fee=round(r["charges"]), pnl=round(r["net"]),
                        bars=0, reason=r["reason"]))
    return out


def _curves(rr):
    net = np.array([r["net"] for r in rr], float); spot = np.array([r["spot0"] for r in rr], float)
    eq = START_CAP + np.cumsum(net); bench = START_CAP * (spot / spot[0])
    peak = np.maximum.accumulate(eq); uw = (eq - peak) / peak * 100.0
    idx = np.linspace(0, len(eq) - 1, min(400, len(eq))).astype(int)
    labels = [rr[i]["date"][2:7] for i in idx]
    order = np.argsort(uw); wp, seen = [], []
    for i in order:
        if any(abs(i - j) < len(eq) * 0.05 for j in seen): continue
        seen.append(i); wp.append(dict(rank=len(wp) + 1, x=int(i), dd=round(float(uw[i]), 2), frac=round(i / max(1, len(eq) - 1), 3)))
        if len(wp) >= 5: break
    return ([round(float(eq[i])) for i in idx], [round(float(bench[i])) for i in idx],
            [round(float(uw[i]), 2) for i in idx], labels, wp)


def _monthly(rr):
    m = {}
    for r in rr:
        y, mo = r["date"][:4], int(r["date"][5:7]); m.setdefault(y, {}).setdefault(mo, 0.0); m[y][mo] += r["net"] / START_CAP * 100
    return {y: {k: round(v, 2) for k, v in mm.items()} for y, mm in m.items()}


def _downpts(arr, k=120):
    arr = np.asarray(arr, float)
    if len(arr) <= k: return [round(float(x)) for x in arr]
    idx = np.linspace(0, len(arr) - 1, k).astype(int); return [round(float(arr[i])) for i in idx]


def _mc(rr, iters=1000, seed=3):
    rng = np.random.default_rng(seed); net = np.array([r["net"] for r in rr], float)
    nets, dds, shs, paths = [], [], [], []
    for j in range(iters):
        s = net[rng.integers(0, len(net), len(net))]; nets.append(s.sum() / START_CAP * 100)
        eq = np.cumsum(s); dds.append((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min() / START_CAP * 100)
        shs.append((s.mean() / s.std()) if s.std() else 0)
        if j < 60: paths.append(_downpts(START_CAP + eq))
    q = lambda a, p: round(float(np.percentile(a, p)), 2); orig = _downpts(START_CAP + np.cumsum(net))
    return dict(table=dict(net=[round(net.sum() / START_CAP * 100, 2), q(nets, 5), q(nets, 50), q(nets, 95)],
                           maxdd=[_metrics(rr)["maxdd"], q(dds, 5), q(dds, 50), q(dds, 95)],
                           sharpe=[_metrics(rr)["sharpe"], q(shs, 5), q(shs, 50), q(shs, 95)]),
                paths=paths, orig_path=orig,
                sharpe_dist=dict(original=round(_metrics(rr)["sharpe"], 2), median=q(shs, 50), best5=q(shs, 95), worst5=q(shs, 5)))


def _significance(rr, iters=1000, seed=11):
    rng = np.random.default_rng(seed); net = np.array([r["net"] for r in rr], float)
    real_sh = _metrics(rr)["sharpe"]; dem = net - net.mean(); yrs = _metrics(rr)["years"]; nulls = []
    for _ in range(iters):
        s = dem[rng.integers(0, len(dem), len(dem))]; nulls.append((s.mean() / s.std()) * math.sqrt(len(s) / yrs) if s.std() else 0)
    nulls = np.array(nulls); p = float((nulls >= real_sh).mean())
    return dict(real_sharpe=round(real_sh, 3), p_value=round(p, 4), null_p95=round(float(np.percentile(nulls, 95)), 3),
                null_mean=round(float(nulls.mean()), 3), n_perm=iters, significant=p < 0.05)


def _boot_p(net, iters=5000, seed=7):
    rng = np.random.default_rng(seed); net = np.asarray(net, float)
    if len(net) < 5: return 1.0
    return round(float((net[rng.integers(0, len(net), size=(iters, len(net)))].mean(axis=1) <= 0).mean()), 4)


tr = [r for r in rows if r["date"] < "2025-01-01"]; oos = [r for r in rows if r["date"] >= "2025-01-01"]
mf, mt, mo = _metrics(rows), _metrics(tr), _metrics(oos)
p_full = _boot_p([r["net"] for r in rows])
candles = [[r["date"], r["spot0"], r["spot0"], r["spot0"], r["spot0"]] for r in rows]


def combo(rr):
    eq, bench, uw, labels, wp = _curves(rr)
    return dict(dna=dict(off=6, wing=5, sl=4000, tgt=8000, max_hold=1, structure="iron_condor", lots=LOTS),
                metrics=_metrics(rr), equity=eq, benchmark=bench, underwater=uw, labels=labels, worst_periods=wp,
                significance=_significance(rr), mc=_mc(rr), monthly=_monthly(rr),
                all_trades=_all_trades(rr), trades=_all_trades(rr[-10:]), opt_table=[])


combos = {k: combo(v) for k, v in (("bs|full", rows), ("bs|train", tr), ("bs|oos", oos),
                                   ("full", rows), ("train", tr), ("oos", oos))}
meta = dict(window=[rows[0]["date"], rows[-1]["date"]], days=len({r["date"] for r in rows}), start_cap=START_CAP,
            design="02.10.01 BNF Hedged Strangle POSITIONAL — SELL ATM±6 CE+PE + BUY ±11 wings, enter 09:20, hold max 1 overnight, ±Rs4k/Rs8k basket (REAL premium shorts + BS wings, 5 lots)",
            tf="positional", candles=candles, passes=["bs"], periods=["full", "train", "oos"],
            instrument="BANKNIFTY", lot_size=LOT_HINT, lots=LOTS)

os.makedirs(os.path.join(RUNS, SLUG), exist_ok=True)
with io.open(os.path.join(RUNS, SLUG, "results.js"), "w", encoding="utf-8") as f:
    f.write("window.RESULTS = " + json.dumps(dict(meta=meta, combos=combos), default=str) + ";")
_dash = io.open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
with io.open(os.path.join(RUNS, SLUG, "index.html"), "w", encoding="utf-8") as f:
    f.write(_dash)

entry = dict(slug=SLUG, design="bnf_strangle_hedged",
             title="02.10.01 - BNF Hedged Strangle (POSITIONAL, 1-night, REAL-lake, 5 lots)", tf="positional",
             params=dict(off=6, wing=5, sl=4000, tgt=8000, max_hold=1, structure="iron_condor", lots=LOTS),
             exit="±Rs4k/Rs8k basket / next-day EOD (max 1 overnight)", instrument="BANKNIFTY", lot_size=LOT_HINT,
             window=meta["window"], days=meta["days"], significant=p_full < 0.05,
             bs_full=dict(sharpe=mf["sharpe"], net_pct=mf["net_pct"], maxdd=mf["maxdd"], win_rate=mf["win_rate"],
                          trades=mf["trades"], profit_factor=mf["profit_factor"]),
             combos=dict(train=dict(sharpe=mt["sharpe"], net_pct=mt["net_pct"]), oos=dict(sharpe=mo["sharpe"], net_pct=mo["net_pct"])),
             p_value=p_full, created="2026-08-27 11:00", deploy_key=SLUG, deployed=SLUG,
             real_cost=dict(method="REAL OptChainLake premium (shorts) + BS wings + date-aware Zerodha charges + DOM slip, 5 lots",
                            note="Positional max_hold=1 vs intraday: +44% net, less tax. Rs4k SL overnight-gap breach ~3x/5yr (worst -Rs10.4k, wing-BOUNDED). Sharpe>4 = seller red-flag; forward-paper."))
with io.open(os.path.join(RUNS, SLUG, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(entry, f, indent=1, default=str)
idxp = os.path.join(RUNS, "index.json"); idx = json.load(io.open(idxp, encoding="utf-8"))
lst = idx if isinstance(idx, list) else idx.get("runs", [])
lst = [r for r in lst if r.get("slug") != SLUG] + [entry]
if isinstance(idx, list): idx = lst
else: idx["runs"] = lst
with io.open(idxp, "w", encoding="utf-8") as f:
    json.dump(idx, f, indent=1, default=str)
print(f"wrote runs/{SLUG}/ + index.json")
print(f"  FULL sharpe {mf['sharpe']} net {mf['net_pct']}% (Rs{mf['net_abs']:,}) maxdd {mf['maxdd']}% win {mf['win_rate']}% PF {mf['profit_factor']} trades {mf['trades']} p={p_full}")
print(f"  TRAIN sh {mt['sharpe']} net {mt['net_pct']}% | OOS sh {mo['sharpe']} net {mo['net_pct']}%")
