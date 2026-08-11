"""build_run.py — emit a schema-compliant runs/strangle_920/ from the REAL-lake strangle
backtest (ivgate_results.json), so the Strategy Lab hub + registry show Sharpe/MaxDD/Win/
Trades/Signif/Created + a Lab detail page. Numbers are REAL premium (not BS) — the deployable
variant: threatened-roll trig100 + hedge spot±500 + IV-rank≥40 gate.

RESULTS_SCHEMA.md compliant. Multi-leg trades are collapsed to one row/trade (net is real)."""
import os, json, io, shutil
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NT = os.path.join(HERE, "..", "nifty_trend")
RUNS = os.path.join(NT, "runs")
SLUG = "strangle_920"
VARIANT = "thr t100 hedge | iv>=40"        # the deployed spec
START_CAP = 1_000_000
LOT = 65

data = json.load(io.open(os.path.join(HERE, "ivgate_results.json"), encoding="utf-8"))
rows = sorted(data["rows"][VARIANT], key=lambda r: r["date"])
iv = {}
for ln in io.open(os.path.join(HERE, "entry_atm_iv.csv"), encoding="utf-8"):
    p = ln.strip().split(",")
    if p[0] != "date" and len(p) >= 2:
        try: iv[p[0]] = float(p[1])
        except: pass


def _streaks(net):
    win = mx = wl = ml = cw = cl = 0
    for x in net:
        if x > 0: cw += 1; cl = 0
        else: cl += 1; cw = 0
        mx = max(mx, cw); ml = max(ml, cl)
    return mx, ml


def _metrics(rr):
    net = np.array([r["net"] for r in rr], float)
    n = len(net); eq = np.cumsum(net)
    peak = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    dd_abs = (eq - peak).min()
    uw = eq < peak                                   # underwater mask (trade-count proxy)
    uw_run = mx = 0
    for u in uw:
        uw_run = uw_run + 1 if u else 0; mx = max(mx, uw_run)
    gp = net[net > 0].sum(); gl = -net[net < 0].sum()
    wins = net > 0
    yrs = max(1.0, (int(rr[-1]["date"][:4]) - int(rr[0]["date"][:4])) + 1)
    sharpe = round((net.mean() / net.std()) * np.sqrt(n / yrs), 3) if net.std() else 0.0
    downside = net[net < 0]
    sortino = round((net.mean() / downside.std()) * np.sqrt(n / yrs), 3) if len(downside) and downside.std() else sharpe
    ann = round(net.sum() / START_CAP * 100 / yrs, 3)
    maxdd = round(dd_abs / START_CAP * 100, 3)
    ws, ls = _streaks(net)
    return dict(
        trades=n, net_pct=round(net.sum() / START_CAP * 100, 3), net_abs=round(net.sum()),
        final_cap=round(START_CAP + net.sum()), start_cap=START_CAP,
        sharpe=sharpe, sortino=sortino, calmar=round(ann / abs(maxdd), 2) if maxdd else 0.0,
        maxdd=maxdd, underwater_days=int(mx * (252 / max(1, n / yrs)) // 1) if n else 0, years=round(yrs, 1),
        win_rate=round(100 * wins.mean(), 3), wl_ratio=round((wins.sum() / max(1, (~wins).sum())), 2),
        profit_factor=round(gp / gl, 3) if gl else 99.0,
        expectancy=round(net.mean(), 1), avg_win=round(net[net > 0].mean() if wins.any() else 0, 1),
        avg_loss=round(net[net < 0].mean() if (~wins).any() else 0, 1),
        largest_win=round(net.max()), largest_loss=round(net.min()),
        total_wins=int(wins.sum()), total_losses=int((~wins).sum()),
        win_streak=ws, loss_streak=ls, avg_bars=0,
        win_long=0.0, win_short=round(100 * wins.mean(), 1), pct_long=0.0, pct_short=100.0,
        fees=round(sum(r["charges"] for r in rr)), annual_return=ann,
        trades_per_day=round(n / max(1, len({r["date"] for r in rr})), 2),
        trades_per_month=round(n / (yrs * 12), 2),
    )


def _boot_p(net, iters=5000, seed=7):
    rng = np.random.default_rng(seed); net = np.asarray(net, float)
    if len(net) < 5: return 1.0
    m = net[rng.integers(0, len(net), size=(iters, len(net)))].mean(axis=1)
    return round(float((m <= 0).mean()), 4)


def _all_trades(rr):
    out = []
    for r in rr:
        credit = r["entry_credit"]; net_u = r["net"] / LOT
        out.append(dict(
            side="short", opt_type="CE+PE", strike=round(r["spot0"] / 50) * 50,
            entry_dt=r["date"] + " 09:20", exit_dt=r.get("exit_date", r["date"]) + " 15:20",
            entry_spot=r["spot0"], exit_spot=r["spot0"], points=0,
            entry_prem=round(credit, 2), exit_prem=round(credit - net_u, 2), qty=LOT,
            gross=round(r["gross"]), fee=round(r["charges"]), pnl=round(r["net"]),
            bars=0, reason=f"{r['reason']} · rolls {r['rolls']}"))
    return out


def _curves(rr):
    """consistent-length (≤400) equity, benchmark, underwater, labels + worst_periods."""
    net = np.array([r["net"] for r in rr], float)
    spot = np.array([r["spot0"] for r in rr], float)
    eq = START_CAP + np.cumsum(net)
    bench = START_CAP * (spot / spot[0])                      # NIFTY buy&hold
    peak = np.maximum.accumulate(eq)
    uw = (eq - peak) / peak * 100.0                           # drawdown % (<=0)
    idx = np.linspace(0, len(eq) - 1, min(400, len(eq))).astype(int)
    labels = [rr[i]["date"][2:7] for i in idx]
    # worst 5 drawdown troughs (distinct-ish by position)
    order = np.argsort(uw)
    wp, seen = [], []
    for i in order:
        if any(abs(i - j) < len(eq) * 0.05 for j in seen):
            continue
        seen.append(i)
        wp.append(dict(rank=len(wp) + 1, x=int(i), dd=round(float(uw[i]), 2), frac=round(i / max(1, len(eq) - 1), 3)))
        if len(wp) >= 5:
            break
    return ([round(float(eq[i])) for i in idx], [round(float(bench[i])) for i in idx],
            [round(float(uw[i]), 2) for i in idx], labels, wp)


def _monthly(rr):
    m = {}
    for r in rr:
        y, mo = r["date"][:4], int(r["date"][5:7])
        m.setdefault(y, {}).setdefault(mo, 0.0)
        m[y][mo] += r["net"] / START_CAP * 100
    return {y: {k: round(v, 2) for k, v in mm.items()} for y, mm in m.items()}


def _mc(rr, iters=1000, seed=3):
    rng = np.random.default_rng(seed); net = np.array([r["net"] for r in rr], float)
    nets, dds, shs = [], [], []
    for _ in range(iters):
        s = net[rng.integers(0, len(net), len(net))]
        nets.append(s.sum() / START_CAP * 100)
        eq = np.cumsum(s); dds.append((eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]).min() / START_CAP * 100)
        shs.append((s.mean() / s.std()) if s.std() else 0)
    q = lambda a, p: round(float(np.percentile(a, p)), 2)
    return dict(table=dict(net=[round(net.sum()/START_CAP*100,2), q(nets,5), q(nets,50), q(nets,95)],
                           maxdd=[_metrics(rr)["maxdd"], q(dds,5), q(dds,50), q(dds,95)],
                           sharpe=[_metrics(rr)["sharpe"], q(shs,5), q(shs,50), q(shs,95)]))


tr = [r for r in rows if r["date"] < "2025-01-01"]
oos = [r for r in rows if r["date"] >= "2025-01-01"]
mf, mt, mo = _metrics(rows), _metrics(tr), _metrics(oos)
p_full = _boot_p([r["net"] for r in rows])

candles = [[d, iv.get(d) and round(next((r["spot0"] for r in rows if r["date"] == d), 0), 1)] for d in sorted({r["date"] for r in rows})]
# daily NIFTY OHLC (flat from entry spot — chart markers only)
candles = [[r["date"], r["spot0"], r["spot0"], r["spot0"], r["spot0"]] for r in rows]

def combo(rr):
    m = _metrics(rr)
    eq, bench, uw, labels, wp = _curves(rr)
    return dict(dna=dict(dist=250, wing=250, trig=100, take=0.5, iv_gate=0.40, roll="threatened"),
                metrics=m, equity=eq, benchmark=bench, underwater=uw, labels=labels, worst_periods=wp,
                significance=dict(real_sharpe=m["sharpe"], p_value=p_full, significant=p_full < 0.05),
                mc=_mc(rr), monthly=_monthly(rr), all_trades=_all_trades(rr), trades=_all_trades(rr[-10:]),
                opt_table=[])

combos = {"bs|full": combo(rows), "bs|train": combo(tr), "bs|oos": combo(oos),
          "full": combo(rows), "train": combo(tr), "oos": combo(oos)}   # legacy fallback

meta = dict(window=[rows[0]["date"], rows[-1]["date"]], days=len({r["date"] for r in rows}),
            start_cap=START_CAP, design="9:20 Strangle Roll+Hedge — REAL-lake, IV-gated (threatened t100)",
            tf="positional", candles=candles, passes=["bs"], periods=["full", "train", "oos"],
            instrument="NIFTY 50", lot_size=LOT, lots=1)

os.makedirs(os.path.join(RUNS, SLUG), exist_ok=True)
with io.open(os.path.join(RUNS, SLUG, "results.js"), "w", encoding="utf-8") as f:
    f.write("window.RESULTS = " + json.dumps(dict(meta=meta, combos=combos), default=str) + ";")

# self-contained dashboard: copy template but point its <script> at THIS run's results.js
# (run_hunt does the exact same replace — the template ships loading results_intraday.js)
_dash = io.open(os.path.join(NT, "dashboard_intraday.html"), encoding="utf-8").read().replace(
    'src="results_intraday.js"', 'src="results.js"')
with io.open(os.path.join(RUNS, SLUG, "index.html"), "w", encoding="utf-8") as f:
    f.write(_dash)

index_entry = dict(slug=SLUG, design="strangle_roll_hedge",
                   title="02.15 - 9:20 Strangle Roll+Hedge (REAL-lake, IV-gated)", tf="positional",
                   params=dict(dist=250, wing=250, trig=100, take_pct=0.5, iv_gate_rank=0.40),
                   exit="50% credit / weekly expiry", instrument="NIFTY 50", lot_size=LOT,
                   window=meta["window"], days=meta["days"], significant=p_full < 0.05,
                   bs_full=dict(sharpe=mf["sharpe"], net_pct=mf["net_pct"], maxdd=mf["maxdd"],
                                win_rate=mf["win_rate"], trades=mf["trades"], profit_factor=mf["profit_factor"]),
                   combos=dict(train=dict(sharpe=mt["sharpe"], net_pct=mt["net_pct"]),
                               oos=dict(sharpe=mo["sharpe"], net_pct=mo["net_pct"])),
                   p_value=p_full, created="2026-08-11 19:30", deploy_key=SLUG, deployed=SLUG,
                   real_cost=dict(method="REAL OptChainLake premium + date-aware Zerodha charges",
                                  note="real premium (not BS) — seller, trustworthy per TRAP #136"))

meta_json = dict(index_entry);
with io.open(os.path.join(RUNS, SLUG, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(meta_json, f, indent=1, default=str)

idxp = os.path.join(RUNS, "index.json")
idx = json.load(io.open(idxp, encoding="utf-8"))
lst = idx if isinstance(idx, list) else idx.get("runs", [])
lst = [r for r in lst if r.get("slug") != SLUG] + [index_entry]
if isinstance(idx, list): idx = lst
else: idx["runs"] = lst
with io.open(idxp, "w", encoding="utf-8") as f:
    json.dump(idx, f, indent=1, default=str)

print(f"wrote runs/{SLUG}/  (results.js + meta.json + index.html) + index.json")
print(f"  FULL  sharpe {mf['sharpe']}  net {mf['net_pct']}%  maxdd {mf['maxdd']}%  win {mf['win_rate']}%  PF {mf['profit_factor']}  trades {mf['trades']}  p={p_full}")
print(f"  TRAIN sharpe {mt['sharpe']} net {mt['net_pct']}% | OOS sharpe {mo['sharpe']} net {mo['net_pct']}%")
