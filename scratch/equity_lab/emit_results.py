"""Build a window.RESULTS in the SAME schema the options dashboard template
(scratch/nifty_trend/dashboard_intraday.html) renders — so an equity run gets ALL the
rich sections (Performance / Drawdown / Underwater / Monthly heatmap / P&L distribution /
Trades / Monte-Carlo / Significance / DNA / Info) with the SAME look & feel.

Equity mapping: each rebalance PERIOD is one "trade" (its net ₹ P&L). No option passes,
so we emit LEGACY single-axis combos ("full"/"train"/"oos") and OMIT meta.passes → the
template auto-hides the Instrument/RMS/BS toggle (see RESULTS_SCHEMA.md legacy note).
"""
import numpy as np
import pandas as pd

START_CAP = 100000.0
TD = 252
RNG = np.random.default_rng(20260720)


def _streaks(signs):
    win = loss = cw = cl = 0
    for s in signs:
        if s > 0:
            cw += 1; cl = 0; win = max(win, cw)
        elif s < 0:
            cl += 1; cw = 0; loss = max(loss, cl)
        else:
            cw = cl = 0
    return win, loss


def _metrics_from(cap, trades, years, benchmark_cap):
    """cap = ₹ equity series (daily). trades = list of dicts with 'net','bars'."""
    rets = cap.pct_change().fillna(0.0)
    net_abs = cap.iloc[-1] - cap.iloc[0]
    net_pct = cap.iloc[-1] / cap.iloc[0] - 1.0
    sd = rets.std()
    sharpe = rets.mean() / sd * np.sqrt(TD) if sd > 0 else 0.0
    dneg = rets[rets < 0].std()
    sortino = rets.mean() / dneg * np.sqrt(TD) if dneg and dneg > 0 else 0.0
    dd = (cap / cap.cummax() - 1.0)
    maxdd = dd.min() * 100
    cagr = (cap.iloc[-1] / cap.iloc[0]) ** (1 / years) - 1.0 if years > 0 else 0.0
    calmar = (cagr * 100) / abs(maxdd) if maxdd < 0 else 0.0
    # underwater days = longest run below the running peak
    uw = (dd < 0).astype(int); run = mx = 0
    for v in uw:
        run = run + 1 if v else 0; mx = max(mx, run)
    pnls = np.array([t["net"] for t in trades], float)
    wins = pnls[pnls > 0]; losses = pnls[pnls < 0]
    nT = len(pnls)
    win_rate = len(wins) / nT * 100 if nT else 0
    avg_win = float(wins.mean()) if len(wins) else 0
    avg_loss = float(losses.mean()) if len(losses) else 0
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else (np.inf if wins.sum() > 0 else 0)
    wl = abs(avg_win / avg_loss) if avg_loss else 0
    ws, ls = _streaks(np.sign(pnls))
    bars = [t["bars"] for t in trades]
    return dict(
        trades=nT, net_pct=round(net_pct * 100, 2), net_abs=round(net_abs, 0),
        final_cap=round(cap.iloc[-1], 0), start_cap=START_CAP,
        sharpe=round(sharpe, 2), sortino=round(sortino, 2), calmar=round(calmar, 2),
        annual_return=round(cagr * 100, 2), maxdd=round(maxdd, 2), underwater_days=int(mx),
        years=round(years, 2), win_rate=round(win_rate, 1),
        wl_ratio=round(wl, 2), profit_factor=round(float(pf), 2) if np.isfinite(pf) else 99.0,
        expectancy=round(float(pnls.mean()) if nT else 0, 0),
        avg_win=round(avg_win, 0), avg_loss=round(avg_loss, 0),
        largest_win=round(float(pnls.max()) if nT else 0, 0),
        largest_loss=round(float(pnls.min()) if nT else 0, 0),
        total_wins=int(len(wins)), total_losses=int(len(losses)),
        win_long=round(win_rate, 1), win_short=0.0, pct_long=100.0, pct_short=0.0,
        avg_bars=round(float(np.mean(bars)) if bars else 0, 1),
        win_streak=int(ws), loss_streak=int(ls),
        fees=round(sum(t.get("fee", 0) for t in trades), 0),
        trades_per_day=round(nT / (years * TD), 3) if years > 0 else 0,
        trades_per_week=round(nT / (years * 52), 2) if years > 0 else 0,
        trades_per_month=round(nT / (years * 12), 2) if years > 0 else 0,
    )


def _downsample(series, n=400):
    if len(series) <= n:
        return list(series)
    idx = np.linspace(0, len(series) - 1, n).astype(int)
    return [float(series[i]) for i in idx]


def _trades_between(cap, rebal, turn_cost_frac=0.0):
    """One 'trade' per rebalance period: net ₹ P&L over [rebal[i], rebal[i+1])."""
    reb = [d for d in rebal if d in cap.index]
    trades = []
    for i in range(len(reb) - 1):
        a, b = reb[i], reb[i + 1]
        va, vb = float(cap.loc[a]), float(cap.loc[b])
        net = vb - va
        pts = (vb / va - 1.0) * 100
        trades.append(dict(
            side="long", opt_type="EQ", strike=0,
            entry_dt=str(a.date()) + " 15:30", exit_dt=str(b.date()) + " 15:30",
            entry_spot=round(va, 0), exit_spot=round(vb, 0), points=round(pts, 2),
            entry_prem=round(va, 0), exit_prem=round(vb, 0),
            gross=round(net, 0), fee=0, net=round(net, 0), pnl=round(net, 0),
            qty=1, bars=int((b - a).days), reason="rebalance",
            ym=f"{a.year}-{a.month:02d}"))
    return trades


def _monthly(cap):
    m = cap.resample("ME").last().pct_change().dropna() * 100
    out = {}
    for d, v in m.items():
        out.setdefault(str(d.year), {})[d.month] = round(float(v), 2)
    return out


def _worst_periods(cap):
    dd = (cap / cap.cummax() - 1.0)
    out = []
    for rank in range(1, 6):
        if dd.min() >= -1e-9:
            break
        i = int(dd.values.argmin())
        out.append(dict(rank=rank, x=i, dd=round(float(dd.iloc[i]) * 100, 1),
                        frac=round(i / len(dd), 3)))
        # zero out the trough's local basin so the next-worst is found
        j = i
        while j < len(dd) and dd.iloc[j] < 0:
            j += 1
        k = i
        while k >= 0 and dd.iloc[k] < 0:
            k -= 1
        dd.iloc[k + 1:j] = 0
    return out


def _mc(trades, n=1000):
    pnls = np.array([t["net"] for t in trades], float)
    if len(pnls) < 5:
        return {}
    nets, dds, shs, paths = [], [], [], []
    for k in range(n):
        seq = RNG.choice(pnls, size=len(pnls), replace=True)
        eq = START_CAP + np.cumsum(seq)
        nets.append(eq[-1] / START_CAP - 1.0)
        peak = np.maximum.accumulate(eq)
        dds.append(float(((eq - peak) / peak).min()))
        r = np.diff(eq) / eq[:-1]
        shs.append(r.mean() / r.std() * np.sqrt(12) if r.std() > 0 else 0)
        if k < 60:
            idx = np.linspace(0, len(eq) - 1, min(120, len(eq))).astype(int)
            paths.append([round(float(eq[i]), 0) for i in idx])
    orig = START_CAP + np.cumsum(pnls)
    oidx = np.linspace(0, len(orig) - 1, min(120, len(orig))).astype(int)
    def q(a, p): return round(float(np.percentile(a, p)), 3)
    return dict(
        table=dict(
            net=[round(orig[-1] / START_CAP - 1, 3), q(nets, 5), q(nets, 50), q(nets, 95)],
            maxdd=[round(float(((orig - np.maximum.accumulate(orig)) / np.maximum.accumulate(orig)).min()), 3),
                   q(dds, 5), q(dds, 50), q(dds, 95)],
            sharpe=[0, q(shs, 5), q(shs, 50), q(shs, 95)]),
        sharpe_dist=dict(original=q(shs, 50), median=q(shs, 50), best5=q(shs, 95), worst5=q(shs, 5)),
        paths=paths, orig_path=[round(float(orig[i]), 0) for i in oidx])


def build_combo(cap, nifty, rebal, dna, sig):
    cap = cap.dropna()
    years = len(cap) / TD
    trades = _trades_between(cap, rebal)
    m = _metrics_from(cap, trades, years, None)
    # benchmark: NIFTY buy&hold normalised to START_CAP over the same window
    nb = nifty[(nifty.index >= cap.index[0]) & (nifty.index <= cap.index[-1])]
    bench = (nb / nb.iloc[0] * START_CAP) if len(nb) else cap * 0 + START_CAP
    bench = bench.reindex(cap.index, method="ffill").fillna(START_CAP)
    dd = (cap / cap.cummax() - 1.0) * 100
    labels = [d.strftime("%y-%m") for d in cap.index]
    return dict(
        dna=dna, metrics=m,
        equity=[round(x, 0) for x in _downsample(cap.values)],
        benchmark=[round(x, 0) for x in _downsample(bench.values)],
        labels=_downsample_labels(labels),
        underwater=[round(x, 2) for x in _downsample(dd.values)],
        worst_periods=_worst_periods(cap),
        monthly=_monthly(cap), significance=sig, mc=_mc(trades),
        opt_table=[], all_trades=trades, trades=trades[-10:])


def _downsample_labels(labels, n=400):
    if len(labels) <= n:
        return labels
    idx = np.linspace(0, len(labels) - 1, n).astype(int)
    return [labels[i] for i in idx]


def build_results(eqn, nifty, rebal, dna, sig, design, tf, instrument,
                  train_end="2021-12-31", oos_start="2022-01-01"):
    """Full window.RESULTS with legacy full/train/oos combos (no meta.passes)."""
    cap = eqn / eqn.iloc[0] * START_CAP
    TE, OS = pd.Timestamp(train_end), pd.Timestamp(oos_start)
    combos = {"full": build_combo(cap, nifty, rebal, dna, sig)}
    tr = cap[cap.index <= TE]
    oo = cap[cap.index >= OS]
    if len(tr) > 30:
        combos["train"] = build_combo(tr / tr.iloc[0] * START_CAP, nifty, rebal, dna, sig)
    if len(oo) > 30:
        combos["oos"] = build_combo(oo / oo.iloc[0] * START_CAP, nifty, rebal, dna, sig)
    return dict(
        meta=dict(window=[str(cap.index[0].date()), str(cap.index[-1].date())],
                  days=len(cap), start_cap=START_CAP, design=design, tf=tf,
                  instrument=instrument, lot_size=1, lots=1, intraday=False,
                  periods=[k for k in ["full", "train", "oos"] if k in combos]),
        combos=combos)
