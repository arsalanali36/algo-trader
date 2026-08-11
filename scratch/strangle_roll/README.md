# Strangle Roll+Hedge — research + Lab/registry producer

**Strategy:** 9:20 NIFTY short-strangle (sell CE spot+250 / PE spot−250) + cheap hedge
(buy spot±500) + **roll-away** (spot within `trig` of a sold leg → close it, resell 250 from
current spot) + **IV-gate** (enter only if trailing-60d ATM-IV rank ≥ 40%). Exit at 50% of
entry credit, else weekly expiry. Deployed variant = **threatened-roll · trig100 · hedge · IV≥40**.

Live (paper): registry **02.15** `strangle_920` (9:20 auto) / **02.16** `strangle_manual`.
Live code = `_ops/strangle_live.py` + `_ops/auto_strangle_roll.py`. This folder = RESEARCH only.

## Files
| File | What |
|---|---|
| `engine.py` | v1 backtest — naked 9-variant sweep on real OptChainLake_1m premium (2021-07→2026-07) |
| `engine2.py` | v2 — adds cheap hedge + SEQUENTIAL positional (no re-entry till exit) + significance |
| `engine3.py` | v3 — IV-GATED re-run (trailing-60d IV-rank gate). Produces `ivgate_results.json` |
| `iv_build.py` | real ATM IV per day via `bs_option.implied_vol` (lake `iv` col is empty) → `entry_atm_iv.csv` |
| `report.py` / `report2.py` | standalone HTML reports (naked sweep / hedge+significance) |
| `build_run.py` | **PRODUCER** — emits schema-compliant `runs/strangle_920/` (see below) |
| `strangle_iv_history_seed.json` | 1244-day BS-inverted ATM-IV → seeds VPS `data/strangle_iv_history.json` |

## How the registry columns + Lab detail page UPDATE

Two separate surfaces, both fed from `scratch/nifty_trend/runs/`:

1. **Registry table columns** (Sharpe/MaxDD/Win/Trades/Signif/Created for 02.15) ←
   `runs/index.json` entry's `bs_full{sharpe,net_pct,maxdd,win_rate,trades,profit_factor}` +
   `p_value` + `significant` + `deployed` (join key = registry `slug` = `strangle_920`).

2. **Lab ↗ detail page** (`/lab/runs/strangle_920/index.html`) ← `runs/strangle_920/results.js`
   (`window.RESULTS = {meta, combos{"bs|full","bs|train","bs|oos"}}`). `index.html` is the
   shared `dashboard_intraday.html` with its `<script src>` pointed at `results.js`.

**To regenerate after a backtest change:**
```bash
python scratch/strangle_roll/engine3.py        # re-run backtest → ivgate_results.json
python scratch/strangle_roll/build_run.py      # → runs/strangle_920/{results.js,meta.json,index.html} + index.json
# then deploy to VPS (git push blocked by creds → scp):
scp -i <key> scratch/nifty_trend/runs/index.json               root@VPS:.../runs/
scp -i <key> scratch/nifty_trend/runs/strangle_920/*           root@VPS:.../runs/strangle_920/
```

**Schema contract:** `scratch/nifty_trend/RESULTS_SCHEMA.md` (every field the dashboard reads).
`build_run.py` emits ALL of them — the panels crash silently on a MISSING field (`renderDD`
does `for...of worst_periods`, `renderUW` does `Math.min(...underwater)`, `renderMC` reads
`mc.paths`, `renderSig` reads `significance.null_p95`), and one crash halts every panel after it.
The canonical producer for standard (spot-signal → BS) strategies is `run_hunt.py --name <slug>`;
this strategy needs a custom producer because its P&L is REAL multi-leg lake premium, not BS.

**Numbers are REAL lake premium (not BS)** — per RESULTS_SCHEMA TRAP #136, a SELLER on real
fills is trustworthy (unlike BS-buyer runs). Deployed variant (bs|full): Sharpe **1.19**,
net 13.07%, MaxDD −2.16%, win 57.3%, PF 1.66, 370 trades, p≈0.003, train/OOS Sharpe 0.85/1.73.
Multi-leg trades collapse to 1 row in the Lab trades table (net ₹ exact; per-leg strike/premium
approximate — schema is single-leg-directional).

Memory: `project_code3b_strangle_roll_hedge`.
