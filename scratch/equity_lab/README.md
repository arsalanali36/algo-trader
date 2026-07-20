# Equity Backtest Lab

Reusable research engine for **delivery-equity, cross-sectional** strategies — the equity
counterpart of the options lab (`scratch/nifty_trend`). Different vehicle: daily stock panel,
monthly/weekly rebalance, long baskets, 10bps/side delivery cost + STCG/LTCG tax. For sector
rotation, major-selloff screens, factor baskets, etc.

## Runs land in the SAME registry
`run_equity.py` writes a **registry-compatible** run (`runs/<slug>/` + appends `runs/index.json`),
so the Strategy Registry's Sharpe / MaxDD / Win% / Significance columns fill in exactly like the
options strategies. Link a registry entry by setting its `slug` to the run's name.

## Files
| File | Role |
|------|------|
| `engine.py` | Core: panel load, portfolio sim (rebalance/cost/tax), metrics, train/OOS, permutation significance, Monte-Carlo. Strategy-agnostic. |
| `strategies.py` | Pluggable strategies. Each factory returns `(weight_fn, random_weight_fn)`. |
| `regime.py` | Regime gates (200-DMA) — hold basket vs cash. |
| `sectors.py` | NSE sector map for the F&O universe (feeds `sector_rotation`). |
| `run_equity.py` | CLI orchestrator → produces the run. |

## Data
`panel_close.csv` (wide Date × SYM daily close) + `panel_turnover.csv`, built from the F&O
equity lake by `refresh_panel.py` / `build_daily_panel.py` (see `/root/markov_analysis`).
`nifty_daily.csv` for the regime gate.

## Run
```bash
# momentum (the validated 10.01) with 200-DMA regime + survivorship-haircut context
python run_equity.py --strategy momentum --regime 200dma --name regime_momentum \
    --title "10 - Regime-Momentum Basket (F&O stocks)" --tf monthly --honest-drop 20

# sector rotation (scaffold — validate before trust)
python run_equity.py --strategy sector_rotation --regime 200dma --name sector_rot_eq --tf monthly

# buy-the-selloff (scaffold — a hypothesis to disprove; Indian daily = continuation)
python run_equity.py --strategy selloff_dip --name selloff_dip_eq --tf monthly
```

## Add a new equity strategy
1. Add a factory to `strategies.py` returning `(weight_fn, random_weight_fn)` and register it in `REGISTRY`.
   - `weight_fn(close, rebal) -> {rebal_date: {sym: weight}}` (weights sum to 1; empty = cash).
   - `random_weight_fn` = same-size RANDOM baskets (the permutation-significance null).
2. `python run_equity.py --strategy <name> --name <slug> ...`
3. Register it in `strategy_registry.json` (family 10 Factor/Equity) with `slug: "<slug>"` → columns fill.

## Honesty gates (same discipline as the options mission)
- **Significance:** permutation vs random baskets, p<0.05.
- **Train/OOS:** both positive, no OOS collapse.
- **Survivorship:** the panel = the CURRENT F&O list tested backward (delisted names gone).
  Use `--honest-drop K` to see the survivor-haircut estimate; judge the winners-vs-losers
  long-short (bias-immune) before trusting a long-only headline. See
  `memory: project_code3b_markov_meanrev_nifty` for the full survivorship analysis.
- **Regime-dependent** ≠ broken, but temper forward expectations (rich in trending years, cash in choppy).
- Real money only after months of forward-paper (`GO_LIVE_CHECKLIST.md`).
