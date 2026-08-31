"""build_chain_real_run.py - 04.03.02 ka REAL-PREMIUM run (RESULTS_SCHEMA).

Purana `runs/chain_zone_longatm` Black-Scholes premium pe bana tha (TRAP #199) - uska
Sharpe 1.95 asli nahi tha. Yeh builder wahi trades (wahi signal, wahi ORIGINAL params,
wahi entry/exit timing - sab spot-based hai) REAL OptChainLake premium pe reprice karke
ek alag run likhta hai, taaki Lab aur Registry me SACH dikhe.

Params NAHI badle - sweep ne saabit kiya ki badalna nuksaandeh hai (TRAP #200).
"""
import os
import io
import json
import math
import sys
import warnings

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bs_vs_reallake as BV
import bs_option as bs
import real_struct2 as rs2

RUNS = os.path.join(HERE, "runs")
SRC, SLUG = "chain_zone_longatm", "chain_zone_longatm_real"
LOT, LOTS, STEP = 65, 1, 50
START_CAP = 500_000
SPLIT = "2025-01-01"

R = json.loads(io.open(os.path.join(RUNS, SRC, "results.js"), encoding="utf-8")
               .read().strip()[len("window.RESULTS = "):].rstrip(";"))
SRCMETA = json.load(io.open(os.path.join(RUNS, SRC, "meta.json"), encoding="utf-8"))

rows = []
for t in R["combos"]["bs|full"]["all_trades"]:
    if t.get("pnl") is None:
        continue
    ie, xe = BV._bar_at(t["entry_dt"]), BV._bar_at(t["exit_dt"])
    if ie is None or xe is None:
        continue
    K = float(t["strike"])
    dirn = str(t.get("side"))
    opt = t.get("opt_type") if t.get("opt_type") in ("CE", "PE") else ("CE" if dirn == "long" else "PE")
    ep, xp = rs2._px(BV._G, ie, opt, K), rs2._px(BV._G, xe, opt, K)
    if ep <= 0:
        continue
    when = pd.Timestamp(t["entry_dt"])
    qty = LOT * LOTS
    gross = (xp - ep) * qty
    fee = bs.calc_charges(ep, max(xp, 0.0), qty, entry_side="BUY", when=when)
    slip = bs.slip_cost_leg(ep, xp, qty)
    rows.append(dict(date=str(t["entry_dt"])[:10], entry_dt=t["entry_dt"], exit_dt=t["exit_dt"],
                     side=dirn, opt_type=opt, strike=K, entry_prem=round(ep, 2),
                     exit_prem=round(xp, 2), qty=qty, gross=gross, charges=fee + slip,
                     net=gross - fee - slip, spot0=float(t.get("entry_spot") or K),
                     spot1=float(t.get("exit_spot") or K), points=float(t.get("points") or 0),
                     reason=str(t.get("reason") or "")))
rows.sort(key=lambda r: r["entry_dt"])
print("repriced %d / %d trades on the REAL lake"
      % (len(rows), len(R["combos"]["bs|full"]["all_trades"])))


def _streaks(net):
    w = l = cw = cl = 0
    for x in net:
        if x > 0:
            cw += 1
            cl = 0
        elif x < 0:
            cl += 1
            cw = 0
        w = max(w, cw)
        l = max(l, cl)
    return w, l


def _years(rr):
    return max((pd.Timestamp(rr[-1]["date"]) - pd.Timestamp(rr[0]["date"])).days / 365.25, 0.25)


def _metrics(rr):
    net = np.array([r["net"] for r in rr], float)
    n = len(net)
    eq = np.cumsum(net)
    peak = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    dd_abs = float((eq - peak).min())
    yrs = _years(rr)
    gp = net[net > 0].sum()
    gl = -net[net < 0].sum()
    wins = net > 0
    sharpe = round((net.mean() / net.std()) * math.sqrt(n / yrs), 3) if net.std() else 0.0
    dn = net[net < 0]
    sortino = round((net.mean() / dn.std()) * math.sqrt(n / yrs), 3) if len(dn) and dn.std() else sharpe
    ws, ls = _streaks(net)
    ann = round(net.sum() / START_CAP * 100 / yrs, 3)
    maxdd = round(dd_abs / START_CAP * 100, 3)
    aw = float(net[wins].mean()) if wins.any() else 0.0
    al = float(net[~wins].mean()) if (~wins).any() else 0.0
    # longest stretch below the equity peak, in CALENDAR days (page shows "days")
    uw = eq - peak
    dts = [pd.Timestamp(r["date"]) for r in rr]
    uw_days = run = 0
    start = None
    for i, v in enumerate(uw):
        if v < 0:
            start = dts[i] if start is None else start
            run = (dts[i] - start).days
            uw_days = max(uw_days, run)
        else:
            start = None
    longs = [r for r in rr if str(r.get("side")) == "long"]
    shorts = [r for r in rr if str(r.get("side")) != "long"]
    wl = lambda s: round(100 * np.mean([r["net"] > 0 for r in s]), 1) if s else 0.0
    return dict(trades=n, net_pct=round(net.sum() / START_CAP * 100, 3), net_abs=round(net.sum()),
                final_cap=round(START_CAP + net.sum()), start_cap=START_CAP, sharpe=sharpe,
                sortino=sortino, annual_return=ann,
                maxdd=maxdd, maxdd_abs=round(dd_abs),
                calmar=round(ann / abs(maxdd), 3) if maxdd else 0.0,
                fees=round(sum(r["charges"] for r in rr)),
                underwater_days=int(uw_days),
                win_rate=round(100 * wins.mean(), 3),
                profit_factor=round(gp / gl, 3) if gl else 999,
                expectancy=round(net.mean(), 1), years=round(yrs, 2),
                avg_win=round(aw, 1), avg_loss=round(al, 1),
                wl_ratio=round(aw / abs(al), 3) if al else 0.0,
                total_wins=int(wins.sum()), total_losses=int((~wins).sum()),
                trades_per_day=round(n / max(1, len({r["date"] for r in rr})), 2),
                avg_bars=0,
                pct_long=round(100 * len(longs) / n, 1), pct_short=round(100 * len(shorts) / n, 1),
                win_long=wl(longs), win_short=wl(shorts),
                largest_win=round(net.max()), largest_loss=round(net.min()),
                win_streak=ws, loss_streak=ls)


def _all_trades(rr):
    return [dict(side=r["side"], opt_type=r["opt_type"], strike=round(r["strike"]),
                 entry_dt=r["entry_dt"], exit_dt=r["exit_dt"], entry_spot=r["spot0"],
                 exit_spot=r["spot1"], points=r["points"], entry_prem=r["entry_prem"],
                 exit_prem=r["exit_prem"], qty=r["qty"], gross=round(r["gross"]),
                 fee=round(r["charges"]), pnl=round(r["net"]), bars=0, reason=r["reason"])
            for r in rr]


def _curves(rr):
    net = np.array([r["net"] for r in rr], float)
    spot = np.array([r["spot0"] for r in rr], float)
    eq = START_CAP + np.cumsum(net)
    bench = START_CAP * (spot / spot[0])
    peak = np.maximum.accumulate(eq)
    uw = (eq - peak) / peak * 100.0
    idx = np.linspace(0, len(eq) - 1, min(400, len(eq))).astype(int)
    labels = [rr[i]["date"][2:7] for i in idx]
    order = np.argsort(uw)
    wp, seen = [], []
    for i in order:
        if any(abs(i - j) < len(eq) * 0.05 for j in seen):
            continue
        seen.append(i)
        wp.append(dict(rank=len(wp) + 1, x=int(i), dd=round(float(uw[i]), 2),
                       frac=round(i / max(1, len(eq) - 1), 3)))
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


def _downpts(a, k=120):
    i = np.linspace(0, len(a) - 1, min(k, len(a))).astype(int)
    return [round(float(a[j])) for j in i]


def _mc(rr, iters=1000, seed=3):
    rng = np.random.default_rng(seed)
    net = np.array([r["net"] for r in rr], float)
    nets, dds, shs, paths = [], [], [], []
    yrs = _years(rr)
    for i in range(iters):
        s = net[rng.integers(0, len(net), len(net))]
        eq = np.cumsum(s)
        pk = np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
        nets.append(s.sum() / START_CAP * 100)
        dds.append((eq - pk).min() / START_CAP * 100)
        shs.append((s.mean() / s.std()) * math.sqrt(len(s) / yrs) if s.std() else 0)
        if i < 40:
            paths.append(_downpts(START_CAP + eq))
    q = lambda a, p: round(float(np.percentile(a, p)), 2)
    m = _metrics(rr)
    return dict(table=dict(net=[m["net_pct"], q(nets, 5), q(nets, 50), q(nets, 95)],
                           maxdd=[m["maxdd"], q(dds, 5), q(dds, 50), q(dds, 95)],
                           sharpe=[m["sharpe"], q(shs, 5), q(shs, 50), q(shs, 95)]),
                paths=paths, original=_downpts(START_CAP + np.cumsum(net)),
                sharpe_dist=dict(original=round(m["sharpe"], 2), median=q(shs, 50),
                                 best5=q(shs, 95), worst5=q(shs, 5)))


def _sig(rr, iters=20000, seed=11):
    net = np.array([r["net"] for r in rr], float)
    rng = np.random.default_rng(seed)
    null = (rng.choice([-1.0, 1.0], size=(iters, len(net))) * np.abs(net)).mean(axis=1)
    p = float((null >= net.mean()).mean())
    m = _metrics(rr)
    return dict(real_sharpe=m["sharpe"], p_value=round(p, 4),
                null_p95=round(float(np.percentile(null, 95)), 2),
                null_mean=round(float(null.mean()), 2), n_perm=iters, significant=p < 0.05)


def combo(rr):
    eq, bench, uw, labels, wp = _curves(rr)
    dna = dict(SRCMETA.get("params", {}))
    dna.update(structure="long_atm", lots=LOTS)
    return dict(dna=dna, metrics=_metrics(rr), equity=eq, benchmark=bench, underwater=uw,
                labels=labels, worst_periods=wp, significance=_sig(rr), mc=_mc(rr),
                monthly=_monthly(rr), all_trades=_all_trades(rr),
                trades=_all_trades(rr)[:400], opt_table=[])


tr = [r for r in rows if r["date"] < SPLIT]
oo = [r for r in rows if r["date"] >= SPLIT]
combos = {"bs|full": combo(rows), "bs|train": combo(tr), "bs|oos": combo(oo),
          "full": combo(rows), "train": combo(tr), "oos": combo(oo)}
mf, mt, mo = _metrics(rows), _metrics(tr), _metrics(oo)
p_full = _sig(rows)["p_value"]

NOTE = ("Replaces the Black-Scholes run (chain_zone_longatm, Sharpe 1.95) which overstated "
        "a BUYER's edge - BS understates the theta a buyer bleeds (TRAP #199). Same signal, "
        "same ORIGINAL params, same spot-based exits; only the option pricing is real. The "
        "lake starts 2021-07, so the 2018-2021 part of the original window is not covered. A "
        "192-config param sweep was run and REJECTED: the train winner was Rs7,778 WORSE "
        "out-of-sample and train-vs-OOS correlation was only 0.234 (TRAP #200) - params are "
        "deliberately left at their original values.")
METHOD = "REAL OptChainLake premium + date-aware Zerodha charges + DOM slip, 1 lot"

# real_cost MUST live in the results.js meta, not only in meta.json: the page's
# provenance badge reads R.meta.real_cost.method. Putting it only in meta.json is what
# made this run show "NOT PROVEN - pricing source not recorded" on its first publish.
meta = dict(window=[rows[0]["date"], rows[-1]["date"]], days=len({r["date"] for r in rows}),
            start_cap=START_CAP,
            design="04.03.02 Ars chain Directional BUY (long ATM) - REAL PREMIUM, 1 lot",
            tf="5m", candles=[], passes=["bs"], periods=["full", "train", "oos"],
            instrument="NIFTY", lot_size=LOT, lots=LOTS,
            real_cost=dict(method=METHOD, note=NOTE),
            # the template renders pass_notes[pass] in place of its canned blurb. The pass
            # KEY stays "bs" for schema compatibility (Stats-tab reads combos["bs|full"]),
            # but this P&L is not Black-Scholes at all — say so where a reader will look.
            pass_notes={"bs": ("<b>REAL premium</b> &mdash; every trade repriced on the "
                               "<b>actual traded ATM CE/PE premium</b> from OptChainLake "
                               "(date-aware Zerodha F&amp;O charges + DOM slip). "
                               "<b>No Black-Scholes anywhere.</b> The pass key is still "
                               "<code>bs</code> for schema compatibility only.")})

os.makedirs(os.path.join(RUNS, SLUG), exist_ok=True)
with io.open(os.path.join(RUNS, SLUG, "results.js"), "w", encoding="utf-8") as f:
    f.write("window.RESULTS = " + json.dumps(dict(meta=meta, combos=combos), default=str) + ";")
_dash = io.open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read() \
    .replace('src="results_intraday.js"', 'src="results.js"')
with io.open(os.path.join(RUNS, SLUG, "index.html"), "w", encoding="utf-8") as f:
    f.write(_dash)

entry = dict(slug=SLUG, design="chain_zone",
             title="04.03.02 - Ars chain Directional BUY (long ATM) - REAL PREMIUM",
             tf="5m", params=SRCMETA.get("params", {}),
             exit=SRCMETA.get("exit", "stop_only"), instrument="NIFTY", lot_size=LOT,
             window=meta["window"], days=meta["days"], significant=p_full < 0.05,
             bs_full=dict(sharpe=mf["sharpe"], net_pct=mf["net_pct"], maxdd=mf["maxdd"],
                          win_rate=mf["win_rate"], trades=mf["trades"],
                          profit_factor=mf["profit_factor"]),
             combos=dict(train=dict(sharpe=mt["sharpe"], net_pct=mt["net_pct"]),
                         oos=dict(sharpe=mo["sharpe"], net_pct=mo["net_pct"])),
             p_value=p_full, created="2026-08-31 16:00",
             deploy_key="chainzone_v1", deployed="chainzone_v1",
             real_cost=dict(
                 method=METHOD, note=NOTE))
with io.open(os.path.join(RUNS, SLUG, "meta.json"), "w", encoding="utf-8") as f:
    json.dump(entry, f, indent=1, default=str)

idxp = os.path.join(RUNS, "index.json")
idx = json.load(io.open(idxp, encoding="utf-8"))
lst = idx if isinstance(idx, list) else idx.get("runs", [])
lst = [r for r in lst if r.get("slug") != SLUG] + [entry]
if isinstance(idx, list):
    idx = lst
else:
    idx["runs"] = lst
with io.open(idxp, "w", encoding="utf-8") as f:
    json.dump(idx, f, indent=1, default=str)

print("wrote runs/%s/ + index.json" % SLUG)
print("  FULL  Sharpe %s  net Rs%s  PF %s  win %s%%  maxDD %s%%  p=%s"
      % (mf["sharpe"], format(mf["net_abs"], ","), mf["profit_factor"],
         mf["win_rate"], mf["maxdd"], p_full))
print("  train Sharpe %s net Rs%s   |   OOS Sharpe %s net Rs%s"
      % (mt["sharpe"], format(mt["net_abs"], ","), mo["sharpe"], format(mo["net_abs"], ",")))
