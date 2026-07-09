# strategies/lab/ — AI-built Strategy Lab

Ye folder AI-designed strategies ka **development + validation** ghar hai — jahan
main (Claude) ek strategy banata hun, backtest karta hun, **Monte Carlo distribution**
+ **optimization** + **walk-forward** chalata hun, aur ek **results dashboard** deta hun.

> Inspired by the Jesse-framework workflow (per-strategy folder + reports).
> **Lab = kaccha maal / experiment.** Jab ek strategy proven ho jaye → `strategies/backtest/`
> me graduate karo (paper), phir `strategies/live/` me ek trader loop banao (live).

## Har strategy = apna self-contained folder

```
strategies/lab/<strategy_name>/
├── strategy.py          # strategy logic + params (min/max/default — Jesse hyperparameters() style)
├── spec.md              # jo condition/params mujhe diye gaye ("entry when X, exit when Y")
├── config.json          # instrument(s), timeframe, date range, param grid (optimize ke liye)
├── reports/
│   ├── backtest.md      # metrics: Sharpe / Sortino / Calmar, max DD, expectancy, win-rate
│   ├── monte_carlo.json # MC distribution (N resampled runs) + percentile bands (P5/P50/P95)
│   ├── optimization.json# hyperparameter sweep → ranked candidates (cand#1, cand#2, …)
│   └── walk_forward.md  # out-of-sample validation (clean OOS Sharpe)
└── results/
    ├── equity_curve.png / drawdown.png
    └── dashboard.html   # self-contained results dashboard (Jesse-style, offline-openable)
```

## Workflow (jab aap bolo "strategy banao + optimize karo")

1. **spec.md** — aapki condition/params likhta hun (entry/exit rules, instrument, TF).
2. **strategy.py** — logic + tunable params (each: `min/max/default`).
3. **Backtest** — reuses `_TOOLS/backtest_engine.py` → `reports/backtest.md`.
4. **Monte Carlo** — trade-order shuffle / block bootstrap → distribution → `reports/monte_carlo.json`
   (batata hai: result luck tha ya edge — P5..P95 band).
5. **Optimize** — reuses `_ops/optimize_strategy.py` param sweep → ranked candidates → `reports/optimization.json`.
6. **Walk-forward** — in-sample tune → out-of-sample test → `reports/walk_forward.md` (overfit-check).
7. **Dashboard** — `results/dashboard.html` (equity vs benchmark, drawdown periods, all metrics).

## Engine components (reuse-first — abhi build nahi hue, scope only)

| Kaam | Reuse / build |
|------|---------------|
| Backtest | `_TOOLS/backtest_engine.py` (already exists) |
| Optimize | `_ops/optimize_strategy.py` (already exists) |
| Indicators | `_CHARTING/indicators.py` (single source — Rule 6B) |
| Risk sim | `execution_gateway.execute_signal(mode="backtest")` (ADR-003 — apna risk-sim mat likho) |
| Monte Carlo | **TO BUILD** — trade-resampler → percentile bands |
| Walk-forward | **TO BUILD** — rolling in/out-of-sample splitter |
| Dashboard gen | **TO BUILD** — per-strategy HTML report writer |

## Promotion path

```
strategies/lab/<name>/   →   strategies/backtest/<name>.py   →   strategies/live/<name>_trader.py
   (experiment)                 (proven, paper dropdown)            (live trader loop, RMS-wired)
```

Live me jaane se pehle `strategies/live/NEW_STRATEGY_CHECKLIST.md` padho (RMS/order_store wiring).
