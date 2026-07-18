# Distance-from-MA extreme-oversold BUY — findings

Source: Dhan YouTube video. Idea: large-cap stock rarely trades >10% below its
20 EMA; when it does = extreme oversold → mean-reversion snapback. Buy-side only.

Data: `._TRADING DATA/EquityDaily/<SYM>.csv` — 210 F&O stocks, daily, 2013–2026.
Zero downloads / Dhan calls / cost.

## Phase 1 — raw signal edge (cost-free, decisive)
Forward returns after every −10% extreme + bullish-reversal-candle bar vs each
stock's own unconditional baseline, pooled:

| horizon | signal mean (win%) | baseline mean | EDGE | p |
|--|--|--|--|--|
| 20d | +4.61% (58.8%) | +1.88% | +2.73% | 0.0000 |
| 40d | +8.85% (61.6%) | +3.83% | +5.02% | 0.0000 |

**Edge survives OOS (2024–26)** with NO tuning: 20d edge +1.69% p=0.0016,
40d edge +2.14% p=0.0018. Smaller than 2013–23 bull years, but real.

## Phase 2 — tradeable backtest
Entry = buy-stop at reversal-candle HIGH (within 3d). The EXIT choice decides
everything:
- Tight candle-low SL + quick EMA-touch exit (~3d hold): works in-sample,
  **FAILS OOS** (PF 0.92) — whipsawed, and misses the positional move.
- **Positional hold (40d) + wider ATR×1.5 stop** = matches what the video
  actually describes, and recovers the edge OOS:

| period | trades | win% | net/trade | PF | avg win / loss | p |
|--|--|--|--|--|--|--|
| TRAIN <2024 | 1458 | 54.0% | +4.35% | 1.78 | +18.5% / −12.2% | 0.000 |
| OOS ≥2024   | 317  | 45.7% | +1.81% | 1.35 | +15.3% / −9.6%  | 0.041 |

Per-year positive 13/14 (only 2022 −0.36%); 2024/25/26 all positive.
Costs: 0.30% round-trip delivery + 0.10% slip/side.

## Honest caveats (not yet done)
1. **Per-trade %, not portfolio return.** Trades overlap across 210 stocks; a
   real book has capital limits → the "+574% OOS sum" is NOT a return. Needs a
   capital-pool / sizing sim (see `honest_sizing.py` pattern) before any ₹ claim.
2. Winner picked from an ~8-config exit sweep → OOS p=0.041 is multiple-testing
   inflated. The robust anchor is the untuned Phase-1 OOS signal edge (p≈0.002).
3. Today's F&O list = mild survivorship / look-ahead on constituents.
4. Swing/hourly variant (−4.5% threshold, intraday lake) not tested yet.

## Verdict
Signal edge is real and OOS-robust. Correct structure = **positional, wide stop**
(the tight-SL swing version does NOT survive OOS). Deployable-shaped; next gate =
portfolio capital-pool sim, then forward-paper.

Run: `python dist_ma.py --phase both` · winner: `--phase 2 --exit hold --maxhold 40 --slatr 1.5`
