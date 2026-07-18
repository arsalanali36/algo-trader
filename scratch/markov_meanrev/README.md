# Markov Mean-Reversion (Jim Simons video reproduction)

Reproduces the "Markov chain / mean-reversion" idea from the Medallion-fund video:
Markov = model where tomorrow's probability depends only on today's state.
Trading rule derived from it: **buy after N consecutive DOWN closes, exit on the
first UP close** (classic buy-the-dip).

## Files
| File | What |
|---|---|
| `_common.py` | Resamples `nifty_1min.csv` → `nifty_daily.csv`; flexible daily-CSV loader (any Date+Close CSV works — point it at SPY too). |
| `markov_analyzer.py` | 2-state transition matrix + streak conditioning P(reversal \| N consecutive closes), with sample size, edge-vs-base, avg next-day return, and a binomial significance test. |
| `markov_backtest.py` | Backtests the buy-the-dip rule (positional, overnight, long-only), sweeps N, compares to Buy & Hold, real-ish costs. |
| `../../_PINE/markov_meanrev_v1.pine` | TradingView daily-chart strategy twin. Same rule; date-range inputs + log.info SIGNAL/EXIT kept; intraday 3:15 rule N/A (daily/positional). |

## Usage
```bash
python _common.py                       # build nifty_daily.csv from the 1-min lake
python markov_analyzer.py               # transition matrix + streak significance
python markov_backtest.py --sweep       # N=1..7 buy-the-dip backtest vs B&H
python markov_backtest.py --n 5 --cost-bps 3 --max-hold 10   # single run + trade CSV
# Any daily CSV (e.g. SPY): python markov_analyzer.py path/to/spy_daily.csv
```

## Finding (NIFTY 2018-2026, 2103 daily bars)
**The video's edge does NOT hold on NIFTY — it inverts.**

- Base up-rate 53.5% (upward drift). Transition: up→up **0.562** (sticky), down→down 0.496.
- P(next-day UP \| N consecutive DOWN closes) *falls below base and keeps falling*:
  N=1 50.4%, N=3 47.4%, N=5 45.1%. The significant cells (N=2,3) point the **wrong
  way** for buy-the-dip — down-streaks predict more down (momentum, not reversion).
- Backtest: every N loses net of a 3bps cost while Buy & Hold made **+129.8%**.
  Win rates look fine (65%) but expectancy is negative — the up-close exit clips
  wins tiny while non-bouncers bleed. Nowhere near the deploy gate (Sharpe≥1, p<0.05).

**Why:** the video uses SPY (choppy, mean-reverting US index). NIFTY is a trending
index — daily closes show weak momentum + drift.

## Full multi-instrument verdict (does the edge live ANYWHERE tradeable?)
| Test | Tool | Result |
|---|---|---|
| NIFTY daily | `markov_analyzer` / `markov_backtest` | momentum — edge inverts |
| NIFTY/BNF **5-min** | `markov_scan`, `markov_intraday_bt` | edge REAL, p=0.000, gross Sharpe~1.7 — but per-trade 0.3-0.9bps << ~2bps cost → NET collapses (N=2 net -649%). HFT-class edge below retail cost floor. 60m → momentum. |
| **20-stock daily basket** | `stock_meanrev` | P(bounce) ~0.50, p 0.4-1.0 = no edge; loses to B&H even gross. Video's 66% absent (2025-26 liquid large-caps). |
| **High-VIX regime** | `markov_regime` | HIGH-VIX = only ever-positive regime (PF~1.1) → video's "reversion shines in high vol" is directionally right but marginal, thin, < B&H, below deploy gate. |

| **215-stock full universe** | `stock_universe_bt --grid` | buy-the-dip = 36/36 configs negative, ALL worse than B&H. "Buy unusually-low" = catastrophic (−57%). |

**Buy-the-dip is dead on Indian equities. The data's real signal is MOMENTUM**
(Markov predicted it: up→up 0.562). `stock_universe_bt.py --momentum` →

| Config | Result | vs B&H |
|---|---|---|
| **breakout(50d) hold 10d** | +16.1%, CAGR 9.4%, DD −14.5%, **Sharpe 0.79** | beats B&H (+11.9%, 7.1%, 0.48) on every metric |

Clean monotonic gradient (longer lookback + hold = better) = real, not a spike.
`--focus` stress: cost-robust (+8.3% even at 20bps/side — NOT edge≈cost). BUT
train Sharpe 1.43 vs **OOS Sharpe 0.31** (~=B&H) → edge decays out-of-sample;
1.7yr only, no significance/MC. **A promising lead / paper candidate, NOT a
deploy-grade live strategy.** Pine twin: `_PINE/momentum_breakout_v1.pine`.

**Overall conclusion:** the video's mean-reversion is US/decades/high-vol
specific — dead on Indian data. The Markov *framework* correctly pointed to
momentum instead; a 50d-breakout basket beats buy&hold full-sample but fades
OOS. Deploy neither without a longer sample + significance test. Feed the
analyzer an SPY daily CSV to reproduce the video's original ~66% bounce.
Research only — NOT registered as a live strategy.

Stock data extracted from the archived equity lake
`D:\KHAZANA\_trading_lakes_2026-07-16.tar.gz` into `_equity/` (gitignored).
