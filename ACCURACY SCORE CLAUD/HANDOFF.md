# Universe System + Validation — HANDOFF (resume point)

**Last updated:** 2026-06-17. All work committed to CODE3B git (master).
Plan file: `~/.claude/plans/dapper-yawning-waffle.md`. Full log: CODE3B `CLAUDE.md`.

## Built & verified (Phases 0–4 done)
- **brokers/** — Dhan/Kite switchable. `dhan_broker.py`: Dhan Data API intraday
  candles (IST +5:30 fix), REST quote/orders, funds. `get_broker("dhan")`.
- **dhan_feed.py** — WebSocket Full packet → live bid/ask `LIVE` dict.
- **smart_order.py** — marketable-limit (BUY=ask, SELL=bid), paper==live, shadow.
- **universe.py / universe_trader.py / strategies/** — Nifty-50 scanner, routes
  equity/stock_option/index_option, caps. Dashboard `STRATEGIES['universe']`,
  config `nifty_config.json` → `universe_v1`.
- **validate_strategy.py** (Phase 4) — TV List-of-Trades vs engine % match.

## Validation status (the active task)
Run: `python validate_strategy.py --csv "ACCURACY SCORE CLAUD/TEST 1/<tv>.csv" --to 2026-05-19`

Current score (Jan06–May19 NIFTY, 59 TV trades):
- **exact entry+exit: 61%**
- entry-exact (same 5-min bar + side): 63%
- entry within 1 bar: 68%
- entry+exit within 1 bar: 66%

Tools: HTML report `ACCURACY SCORE CLAUD/validation_report.html` (color-coded,
dE/dX deltas). Debug one day: `python validate_strategy.py --csv <tv> --debug YYYY-MM-DD`.

Fixes already applied (engine now faithful on these):
- TV next-bar-open fill convention; **continuous Wilder-RMA ATR(14)** (Pine ta.atr
  = RMA alpha 1/14, NOT ewm span — was causing early exits)
- candle patterns EXACT vs Pine `AA_CandlePatterns` (wickRatio 2.5; redHammer
  upperWick≤body; invRedHam lowerWick≤body) — in `range_trader.py`
- bullHarami/bearHarami added; Zone Exit (MainExit) ON; post-entry zone reset;
  max-candle-size as ENTRY filter; selectedLine RESISTANCE-priority
- **full daily history** (`nifty_daily.csv`, Dhan historical_daily_data sec_id 13,
  2025-01..2026-06) for chain lookback — in DATA_DIR

REMAINING gap (~37%) = micro-level zone-formation TIMING (which exact bar a zone
forms). No single systematic bug left — each off-day has its own micro-reason.
To close further, need TradingView BAR-LEVEL zone markers (plotshape/zone-box
export), since List-of-Trades only gives entry/exit, not zone-formation bars.
Chains + pivots + candle patterns + ATR all verified faithful to Pine.

Fixes applied this round (all committed):
- Wilder-RMA ATR (was the big exit fix)
- full Dhan daily history for chains
- tracked-high/low accumulate + current-bar zone touch (forensics on 01-13)
- DROPPED Pine's longBelowTrackedHigh/shortAboveTrackedLow (over-blocks; +5%)

## NEXT STEPS (resume here) — bottleneck = key LEVELS + tracked filters
1. **Daily key-level history (biggest lever).** Chains need ~20+ prior daily
   bars; local NIFTY data starts Jan-01 → early chains truncated vs TradingView.
   FIX: fetch NIFTY daily history from Dhan:
   `get_broker("dhan").intraday_candles` won't do daily — use SDK
   `historical_daily_data(sec_id="13", "IDX_I", "INDEX", from, to)` back ~6 months.
   Feed those daily bars into `daily_bars()` instead of (or before) the per-day
   1-min aggregates. Then re-run validation.
2. **Port tracked-high/low state machine** (Pine lines ~929-973): trackedHigh/Low
   maintained while touching selectedLine; entry filters
   `longBelowTrackedHigh = close<=trackedHigh`, `shortAboveTrackedLow = close>=trackedLow`,
   and `trackedTooClose<20` (Pine lines 1105-1114, 1160-1170).
3. **Verify pivot/chain construction** vs Pine (lines 347-580): traditional
   pivots from prev-day HLC + high/low chain with max-jump 10% (index).
4. **Missing TV data** May20–Jun17: download via pipeline for full-period score.

## Pine reference (in ACCURACY SCORE CLAUD/TEST 1/FOR CLAUD.txt)
- Entry: lines 1162-1205 (Index_Long_Signal / Index_Short_Signal + strategy.entry)
- Exit: lines 1216-1296 (ATR first, else ZONE) + 3:15 (1300-1308)
- Candle lib params: lines 819-829 (minBodySize 0.5, wickRatio 2.5, prevBodyMin 0.5)
- Touch/selectedLine: lines 905-973 ; bullish/bearish defs: 867-870

## Pending after validation hits target %
- Phase 6: go live (small qty, after % match acceptable to user)
- UI polish: universe config tab, shadow status badge, Quick Order bid/ask
