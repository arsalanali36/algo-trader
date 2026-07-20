"""Equity backtest lab — REUSABLE core engine (delivery-equity, cross-sectional).

Different vehicle from the options lab (scratch/nifty_trend): daily panel of stocks,
monthly/weekly rebalance, long baskets, delivery cost + STCG/LTCG tax. Any equity
strategy (momentum, sector rotation, selloff-buy, factor screen) plugs in as a function
that returns {rebalance_date: {sym: weight}} — the engine handles portfolio simulation,
costs, tax, metrics, train/OOS, permutation significance, and Monte-Carlo.

This mirrors the options lab's discipline (RESULTS_SCHEMA / significance-gate) so equity
runs land in the SAME registry + hub via a registry-compatible run entry (see run_equity.py).

Data: panel_close.csv (wide: Date x SYM daily close) + panel_turnover.csv (₹ turnover proxy),
built from the F&O equity lake by refresh_panel.py / build_daily_panel.py.
"""
import os
import numpy as np
import pandas as pd

# ---- shared cost / tax constants (delivery equity) ----
COST_BPS = 10.0          # per side, on traded notional (brokerage+STT+slippage proxy)
STCG = 0.20              # short-term cap-gains (equity, <1yr) — Budget-2024 rate
LTCG = 0.125             # long-term cap-gains (>1yr)
LTCG_EXEMPT = 125000.0   # LTCG annual exemption
TRADING_DAYS = 252
START_CAP = 100000.0


# ============================ data ============================
def load_panel(path):
    return pd.read_csv(path, index_col="Date", parse_dates=True).sort_index().ffill(limit=3)

def rebalance_dates(index, freq="M"):
    """Last trading day of each period. freq: 'M' monthly, 'W' weekly."""
    s = pd.Series(index=index, data=1)
    if freq == "M":
        grp = s.groupby([index.year, index.month])
    elif freq == "W":
        iso = index.isocalendar()
        grp = s.groupby([iso.year.values, iso.week.values])
    else:
        raise ValueError(freq)
    return [g.index.max() for _, g in grp]


# ============================ portfolio simulation ============================
def run_portfolio(close, weights, rebal, cost_bps=COST_BPS, rf_annual=0.0):
    """Daily gross equity of a long basket. weights = {rebal_date: {sym: weight}} (weights
    sum to 1 = fully invested; empty dict = go to CASH). Costs on turnover. Cash grows at
    rf_annual (for regime-off periods). Returns (equity Series, in_market_ratio, avg_turnover)."""
    rebal = [d for d in rebal if d in weights]
    if not rebal:
        return None
    dates = close.index[close.index >= rebal[0]]
    rf_d = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    equity = pd.Series(index=dates, dtype=float)
    holdings, cash = {}, 1.0
    reb_set = set(rebal); prev = None; tin = 0; nreb = 0; turns = []
    for d in dates:
        row = close.loc[d]
        cash *= (1 + rf_d)
        if prev is not None and holdings:
            for s in list(holdings):
                p0, p1 = prev.get(s), row.get(s)
                if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    holdings[s] *= (p1 / p0)
        port = cash + sum(holdings.values())
        if d in reb_set:
            tgt = {s: port * w for s, w in weights[d].items()}
            names = set(tgt) | set(holdings)
            turn = sum(abs(tgt.get(s, 0.0) - holdings.get(s, 0.0)) for s in names)
            turns.append(turn / port if port > 0 else 0)
            port -= turn * (cost_bps / 1e4)
            if tgt:
                scale = port / max(1e-9, sum(tgt.values()))
                holdings = {s: v * scale for s, v in tgt.items()}; cash = 0.0; tin += 1
            else:
                holdings = {}; cash = port
            nreb += 1
        equity.loc[d] = cash + sum(holdings.values())
        prev = row
    return equity.dropna(), (tin / nreb if nreb else 0.0), (float(np.mean(turns)) if turns else 0.0)


def apply_stcg(equity, boundaries, stcg=STCG):
    """Net equity after taxing each rebalance-period's realised gain at STCG (short-term,
    the churny-basket case). Compounds net. Conservative (assumes full realisation each period)."""
    idx = list(equity.index); bset = set(boundaries)
    net = pd.Series(index=equity.index, dtype=float)
    seg_g = equity.iloc[0]; seg_n = 1.0
    for i, d in enumerate(idx):
        g = equity.iloc[i]
        net.iloc[i] = seg_n * (g / seg_g)
        if d in bset:
            gain = g / seg_g - 1.0
            rn = net.iloc[i]
            if gain > 0:
                rn -= stcg * gain * seg_n
            net.iloc[i] = rn
            seg_g, seg_n = g, rn
    return net


# ============================ metrics ============================
def metrics(equity, ppy=TRADING_DAYS):
    equity = equity.dropna()
    if len(equity) < 5:
        return dict(cagr=np.nan, sharpe=np.nan, maxdd=np.nan, pf=np.nan,
                    total=np.nan, win_rate=np.nan)
    rets = equity.pct_change().fillna(0.0)
    yrs = len(equity) / ppy
    total = equity.iloc[-1] / equity.iloc[0] - 1.0
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / yrs) - 1.0 if yrs > 0 else np.nan
    sd = rets.std()
    sharpe = rets.mean() / sd * np.sqrt(ppy) if sd > 0 else np.nan
    dd = (equity / equity.cummax() - 1.0).min()
    up = rets[rets > 0].sum(); dn = -rets[rets < 0].sum()
    pf = up / dn if dn > 0 else np.inf
    # win_rate on MONTHLY returns (basket cadence), not daily — more meaningful for equity
    mret = equity.resample("ME").last().pct_change().dropna() if len(equity) > 25 else rets
    win = float((mret > 0).mean() * 100) if len(mret) else np.nan
    return dict(cagr=float(cagr), sharpe=float(sharpe), maxdd=float(dd), pf=float(pf),
                total=float(total), win_rate=win)


def lakh_model(equity_net):
    """₹1L terminal after the net (cost+STCG) equity curve."""
    return START_CAP * equity_net.iloc[-1] / equity_net.iloc[0]

def nifty_bh_net(nifty, a, b, cap=START_CAP):
    """NIFTY buy&hold over [a,b], single LTCG at exit (tax-efficient benchmark)."""
    nb = nifty[(nifty.index >= a) & (nifty.index <= b)]
    if len(nb) < 5:
        return None
    gm = nb.iloc[-1] / nb.iloc[0]
    gain = cap * gm - cap
    tax = max(0.0, (gain - LTCG_EXEMPT) * LTCG)
    term = cap * gm - tax - 20
    yrs = (nb.index[-1] - nb.index[0]).days / 365.25
    return dict(terminal=term, cagr=(term / cap) ** (1 / yrs) - 1 if yrs > 0 else np.nan,
                maxdd=float((nb / nb.cummax() - 1).min()), gross_mult=float(gm))


# ============================ significance + MC ============================
def permutation_pvalue(close, rebal, real_sharpe, weight_fn_random, n=500, rng=None):
    """Null = random baskets of the same size each rebalance (no signal). p = P(random >= real)."""
    rng = rng or np.random.default_rng(20260720)
    ge = 0; vals = []
    for _ in range(n):
        w = weight_fn_random(close, rebal, rng)
        out = run_portfolio(close, w, rebal)
        if out is None:
            continue
        eq, _, _ = out
        eqn = apply_stcg(eq, [d for d in rebal if d in eq.index])
        m = metrics(eqn)["sharpe"]
        if not np.isnan(m):
            vals.append(m)
            if m >= real_sharpe:
                ge += 1
    p = (ge + 1) / (len(vals) + 1)
    return (p, (float(np.mean(vals)) if vals else np.nan),
            (float(np.percentile(vals, 95)) if vals else np.nan))

def bootstrap_mc(equity_net, n=1000, block=5, rng=None):
    rng = rng or np.random.default_rng(20260720)
    r = equity_net.pct_change().fillna(0.0).values
    L = len(r)
    if L < 20:
        return {}
    nb = L // block; sh = []
    for _ in range(n):
        idx = rng.integers(0, L - block, size=nb)
        samp = np.concatenate([r[i:i + block] for i in idx])
        sd = samp.std()
        if sd > 0:
            sh.append(samp.mean() / sd * np.sqrt(TRADING_DAYS))
    sh = np.array(sh)
    return dict(sharpe_mean=float(sh.mean()), sharpe_p05=float(np.percentile(sh, 5)),
                sharpe_p95=float(np.percentile(sh, 95)), p_pos=float((sh > 0).mean()))


# ============================ one-call backtest ============================
def backtest(close, weight_fn, freq="M", regime_fn=None, rf_annual=0.0,
             train_end="2021-12-31", oos_start="2022-01-01",
             random_weight_fn=None, sig_n=500, mc=True):
    """Full run: weights -> portfolio -> net(STCG) -> metrics + train/OOS + significance + MC.
    weight_fn(close, rebal) -> {date: {sym: weight}}.
    regime_fn(rebal_date) -> bool (True=hold basket, False=cash). Optional.
    Returns a dict with eq_net + all metrics."""
    rebal = rebalance_dates(close.index, freq)
    weights = weight_fn(close, rebal)
    if regime_fn is not None:
        weights = {rd: (w if regime_fn(rd) else {}) for rd, w in weights.items()}
    out = run_portfolio(close, weights, rebal, rf_annual=rf_annual)
    if out is None:
        return None
    eq, in_mkt, turn = out
    bnd = [d for d in rebal if d in eq.index]
    eq_net = apply_stcg(eq, bnd)
    m = metrics(eq_net)
    TE, OS = pd.Timestamp(train_end), pd.Timestamp(oos_start)
    def _slice(a, b):
        e = eq_net[(eq_net.index >= a) & (eq_net.index <= b)]
        return metrics(e / e.iloc[0]) if len(e) > 5 else {}
    m_tr = _slice(eq_net.index.min(), TE)
    m_oo = _slice(OS, eq_net.index.max())
    res = dict(metrics=m, train=m_tr, oos=m_oo, in_market=in_mkt, turnover=turn,
               eq_net=eq_net, window=[str(eq_net.index.min().date()), str(eq_net.index.max().date())],
               n_rebal=len(bnd), lakh=lakh_model(eq_net))
    if random_weight_fn is not None and not np.isnan(m["sharpe"]):
        p, null_mean, null_p95 = permutation_pvalue(close, rebal, m["sharpe"], random_weight_fn, n=sig_n)
        res["p_value"] = p; res["null_sharpe"] = null_mean; res["null_p95"] = null_p95
    if mc:
        res["mc"] = bootstrap_mc(eq_net)
    return res
