"""Emit runs/straddle_alert/ (02.07 naked) + runs/straddle_alert_hedged/ (02.07.01
hedged) in RESULTS_SCHEMA so the Strategy Registry Sharpe/Net/MaxDD/Win/Signif columns
populate (currently '-'). Trades from bt_alert_straddle.run() (REAL OptChainLake premium
+ date-aware Zerodha charges). Helpers mirror build_bnf_positional_run.py (Rule 6B).

Two INTRADAY variants (the live/paper reality):
  straddle_alert         wing=0  (naked ATM straddle)          -> 02.07
  straddle_alert_hedged  wing=8  (+/-400pt far-OTM wings=fly)  -> 02.07.01
NIFTY WEEK, 4 lots, Rs4,000 basket SL/target (matches live config).
"""
import os, io, json, math, sys, datetime as dt
import numpy as np

NT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, NT)
import bt_alert_straddle as bt          # noqa: E402
import expiry_calendar as xcal          # noqa: E402

RUNS = os.path.join(NT, "runs")
U, EXP, STEP, LOT, LOTS = "NIFTY", "WEEK", 50, 75, 4
QTY = LOT * LOTS                         # 300 (bt __main__ convention)
START_CAP = 400_000                      # 02.07.01 tier (capital_rs=4L)
BASKET = 4000.0
CREATED = dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def _expfn(d):
    wd = xcal.weekly_expiry_weekday(d); e = d
    while True:
        if e.weekday() == wd and e >= d:
            return e
        e = e + dt.timedelta(days=1)


_DAYS = None  # shared lake, loaded once
_SPOTMAP = {}  # date -> (spot, atm) representative real values (for capital notional)


def _build_spotmap():
    """Per-date real (spot, atm) from the lake — registry_economics uses entry_spot x qty
    as the SPAN notional, so it MUST be the real ~24000 spot, not a placeholder."""
    global _SPOTMAP
    _SPOTMAP = {}
    for date, rows in (_DAYS or {}).items():
        for r in rows:
            sp = r.get("spot"); at = r.get("atm")
            try:
                sp = float(sp); at = float(at)
            except (TypeError, ValueError):
                continue
            if sp and sp > 0:
                _SPOTMAP[date] = (sp, at or round(sp / STEP) * STEP)
                break


def _rows_for(wing, hold="intraday"):
    """Run bt and shape trades into the RESULTS_SCHEMA row dicts the helpers expect."""
    tr = bt.run(U, EXP, STEP, QTY, _expfn, 15, 15, BASKET, hold=hold, wing=wing, _days=_DAYS)
    tr = sorted(tr, key=lambda t: t["date"])
    rows = []
    for t in tr:
        gross = round(t["pts"] * QTY)
        hd = int(t.get("held_days", 1) or 1)
        exd = str(t["date"] + dt.timedelta(days=hd - 1))
        spot, atm = _SPOTMAP.get(t["date"], (0.0, 0.0))
        rows.append(dict(date=str(t["date"]), exit_date=exd,
                         net=float(t["net"]), gross=float(gross),
                         charges=float(gross - t["net"]), entry_credit=float(t["entry"]),
                         exit_prem=float(t["exit"]), spot0=float(spot), atm=float(atm),
                         qty=QTY, reason=str(t["reason"])))
    return rows


# ---- metric helpers (verbatim from build_bnf_positional_run.py, Rule 6B) ----
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
        out.append(dict(side="short", opt_type="straddle CE+PE", strike=round(r["atm"]),
                        entry_dt=r["date"] + " (alert)", exit_dt=r["exit_date"] + " EOD",
                        entry_spot=r["spot0"], exit_spot=r["spot0"], points=round(r["entry_credit"] - r["exit_prem"], 1),
                        entry_prem=round(r["entry_credit"], 2), exit_prem=round(r["exit_prem"], 2), qty=r["qty"],
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


def build(slug, wing, title, structure, note, hold="intraday", tf="intraday"):
    rows = _rows_for(wing, hold)
    if len(rows) < 5:
        print("  ! %s: too few trades (%d) — skip" % (slug, len(rows))); return None
    tr = [r for r in rows if r["date"] < "2025-01-01"]; oos = [r for r in rows if r["date"] >= "2025-01-01"]
    mf, mt, mo = _metrics(rows), _metrics(tr) if tr else _metrics(rows), _metrics(oos) if oos else _metrics(rows)
    p_full = _boot_p([r["net"] for r in rows])
    candles = [[r["date"], r["spot0"], r["spot0"], r["spot0"], r["spot0"]] for r in rows]

    def combo(rr):
        eq, bench, uw, labels, wp = _curves(rr)
        return dict(dna=dict(wing=wing, basket=int(BASKET), structure=structure, lots=LOTS),
                    metrics=_metrics(rr), equity=eq, benchmark=bench, underwater=uw, labels=labels, worst_periods=wp,
                    significance=_significance(rr), mc=_mc(rr), monthly=_monthly(rr),
                    all_trades=_all_trades(rr), trades=_all_trades(rr[-10:]), opt_table=[])

    combos = {k: combo(v) for k, v in (("bs|full", rows), ("bs|train", tr or rows), ("bs|oos", oos or rows),
                                       ("full", rows), ("train", tr or rows), ("oos", oos or rows))}
    meta = dict(window=[rows[0]["date"], rows[-1]["date"]], days=len({r["date"] for r in rows}), start_cap=START_CAP,
                design=title, tf=tf, candles=candles, passes=["bs"], periods=["full", "train", "oos"],
                instrument="NIFTY", lot_size=LOT, lots=LOTS)
    os.makedirs(os.path.join(RUNS, slug), exist_ok=True)
    with io.open(os.path.join(RUNS, slug, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(dict(meta=meta, combos=combos), default=str) + ";")
    _dash = io.open(os.path.join(NT, "dashboard_intraday.html"), encoding="utf-8").read().replace('src="results_intraday.js"', 'src="results.js"')
    with io.open(os.path.join(RUNS, slug, "index.html"), "w", encoding="utf-8") as f:
        f.write(_dash)
    entry = dict(slug=slug, design=slug, title=title, tf=tf,
                 params=dict(wing=wing, basket=int(BASKET), structure=structure, lots=LOTS),
                 exit="Rs4,000 basket SL/target (else EOD)", instrument="NIFTY", lot_size=LOT,
                 window=meta["window"], days=meta["days"], significant=p_full < 0.05,
                 bs_full=dict(sharpe=mf["sharpe"], net_pct=mf["net_pct"], maxdd=mf["maxdd"],
                              win_rate=mf["win_rate"], trades=mf["trades"], profit_factor=mf["profit_factor"]),
                 combos=dict(train=dict(sharpe=mt["sharpe"], net_pct=mt["net_pct"]),
                             oos=dict(sharpe=mo["sharpe"], net_pct=mo["net_pct"])),
                 p_value=p_full, created=CREATED, deploy_key=slug, deployed=slug,
                 real_cost=dict(method="REAL OptChainLake_1m premium + date-aware Zerodha charges, 4 lots (lot 75)",
                                note=note))
    with io.open(os.path.join(RUNS, slug, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=1, default=str)
    print("  %s: %d trades | Sharpe %.2f | net %.1f%% | maxDD %.1f%% | win %.0f%% | p=%.3f" %
          (slug, mf["trades"], mf["sharpe"], mf["net_pct"], mf["maxdd"], mf["win_rate"], p_full))
    return entry


def main():
    global _DAYS
    print("Loading OptChainLake_1m/%s/%s once ..." % (U, EXP), flush=True)
    _DAYS = bt.load(U, EXP, STEP)
    _build_spotmap()
    print("  loaded %d trading days | spotmap %d dates (real spot for capital)" %
          (len(_DAYS), len(_SPOTMAP)), flush=True)
    print("Building all 3 alert-straddle Lab artifacts (REAL lake) ...", flush=True)
    entries = []
    e1 = build("straddle_alert", 0,
               "02.07 - Straddle @ Alert (naked ATM, intraday, REAL-lake, 4 lots)",
               "short_straddle",
               "Naked ATM straddle on first alert/day, Rs4k basket. Intraday Sharpe<1 (2 losing yrs) -> gate FAIL; naked-POSITIONAL variant (02.07.02) passes. NIFTY only (BankNifty loser). net_pct on Rs4L cap, not true CAGR.")
    if e1: entries.append(e1)
    e2 = build("straddle_alert_hedged", 8,
               "02.07.01 - Straddle @ Alert Hedged Live (iron-fly +/-400pt, intraday, REAL-lake, 4 lots)",
               "iron_fly",
               "Live hedged config: SELL ATM CE+PE + BUY +/-8 (=Rs400) wings, Rs4k basket. Wings cut P&L vs naked. Wings at Rs400 far from ~25-40pt intraday gap -> limited tail cap at Rs4k scale. Turned OFF 2026-08-29. net_pct on Rs4L cap.")
    if e2: entries.append(e2)
    e3 = build("straddle_alert_positional", 0,
               "02.07.02 - Straddle @ Alert (naked, POSITIONAL hold-to-expiry, REAL-lake, 4 lots)",
               "short_straddle",
               "Naked ATM straddle on first alert/day, HELD to weekly expiry (no 3:15 force-exit), Rs4k basket. Only ~2.4% of trades actually hold overnight; edge = not scratching un-resolved trades at 3:15 (overnight theta -> next-day Rs4k target). 2022/2023 (intraday losing yrs) flip green. Naked = unbounded overnight gap tail (research; forward-paper before real money). NIFTY only. net_pct on Rs4L cap, not true CAGR.",
               hold="positional", tf="positional")
    if e3: entries.append(e3)

    # merge into runs/index.json (dedup by slug) — same pattern as producers
    idxp = os.path.join(RUNS, "index.json")
    idx = json.load(io.open(idxp, encoding="utf-8"))
    lst = idx if isinstance(idx, list) else idx.get("runs", [])
    keep_slugs = {e["slug"] for e in entries}
    lst = [r for r in lst if r.get("slug") not in keep_slugs] + entries
    if isinstance(idx, list): idx = lst
    else: idx["runs"] = lst
    with io.open(idxp, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1, default=str)
    print("index.json updated (+%d entries). DONE." % len(entries), flush=True)


if __name__ == "__main__":
    main()
