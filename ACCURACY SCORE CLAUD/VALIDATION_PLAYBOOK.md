# Pine → Python Matching PLAYBOOK (reusable)

**Purpose:** Next time we validate any TradingView Pine strategy against a Python
engine, follow THIS — it skips the trial-and-error that took a full day the first
time. We reached **90.2% exact / 93% entry** on Range Chain (NIFTY) with this.

Tools live in `CODE3B- TV BACKTEST ENGINE/validate_strategy.py`.

---

## DEFAULT RULE — every new Pine file gets this logging built in
**As of 2026-06-19: don't wait for a mismatch to add this.** Every Pine strategy
file written from now on (any `_PINE/*.pine`) gets `log.info()` SIGNAL/EXIT lines
at the entry/exit conditions from the start — not bolted on later. Reference:
`_PINE/rsi_v1.pine` and `_PINE/range_chain_zonelog.pine`. This means:
- `backtest_engine.py`'s `_load_tv_trades()` can always parse a Pine Logs export
  for ANY strategy (not just Range Chain), no per-strategy CSV-format guessing.
- TV "List of Trades" CSV export (`Trade number/Type/Date and time/Price` columns)
  still works too — `validate_strategy.parse_tv()` / `backtest_engine._load_tv_trades()`
  auto-detect `.csv` vs `.log` by extension.

Also default ON: **date-range inputs** (`startDate`/`endDate`/`inDateRange`) in every
new Pine file, gating entry conditions. Lets the TV Strategy Tester window match the
Python `date_from`/`date_to` exactly — no manual date trimming before comparing.
```pinescript
startDate   = input.time(timestamp("2026-01-01 09:15"), title="Backtest Start Date", group="Day Selection")
endDate     = input.time(timestamp("2026-12-31 15:15"), title="Backtest End Date",   group="Day Selection")
inDateRange = (time >= startDate and time <= endDate)
// long_entry := ... and inDateRange   (exits don't need the gate)
```

## THE FAST WORKFLOW (do this in order)

### 1. Get a CONSISTENT ground truth (the #1 time-saver)
**Trap we hit:** the "List of Trades" CSV and a separate "zone log" were exported
from DIFFERENT backtest runs (different date range → different continuous-ATR
warmup → different trades). They disagreed → fake low scores, wasted hours.

**Do instead:** add `log.info()` to the Pine and export EVERYTHING from ONE run:
```pinescript
// at zone formation (where Green_Zone := ... is set):
log.info("ZONE " + (Bullish_Candle_exitingZone ? "GREEN" : "RED") + " "
     + str.format_time(time,"yyyy-MM-dd HH:mm","Asia/Kolkata")
     + " hi=" + str.tostring(high,"#.##") + " lo=" + str.tostring(low,"#.##")
     + " line=" + lineType)
// at each strategy.entry:
log.info("SIGNAL LONG "  + str.format_time(time,"yyyy-MM-dd HH:mm","Asia/Kolkata") + " close=" + str.tostring(close,"#.##") + " line=" + lineType)
// at each strategy.close + the 3:15 close_all:
log.info("EXIT LONG " + str.format_time(time,"yyyy-MM-dd HH:mm","Asia/Kolkata") + " reason=" + exitReason + " close=" + str.tostring(close,"#.##"))
```
Ready-made file: `TEST 1/Ars_Auto_Rev_Chain_ZONELOG.pine`. Pine Editor → **Pine Logs**
tab → copy all → save as one CSV. One run = zones + entries + exits, all consistent.

### 2. Score against that single log
```
python validate_strategy.py --csv x --signals "<pine-logs>.csv"
```
Prints exact entry+exit %, entry-exact %, within-1-bar %, skipped data-gap days,
and writes a color-coded HTML report (`ACCURACY SCORE CLAUD/validation_report.html`).

### 3. Forensics on any mismatch
```
python validate_strategy.py --csv "<list-of-trades>.csv" --debug 2026-01-13
```
Prints that day's KEY LEVELS + engine zone-formation trace + engine trades + TV
trades. Then `grep "<date>" <pine-logs>.csv` to see TV's zones/signals/exits for
the SAME day. Compare line-by-line — the divergence is always one of the items
in "FIXES THAT MATTERED" below.

---

## FIXES THAT MATTERED (ranked by score impact) — check these FIRST

1. **TradingView fill convention = NEXT bar open.** A signal on bar i fills at
   bar i+1 open. Engine must record entry/exit time+price from bar i+1.  (+14%)
2. **Wilder's RMA ATR, not EMA.** Pine `ta.atr` = RMA (`alpha = 1/period`), NOT
   `ewm(span=period)`. EMA ATR is too reactive → SL too tight → early exits.  (+12%)
3. **pyramiding = 0.** A SIGNAL fired while already in that direction is IGNORED
   by `strategy.entry` (no new trade). When deriving trades from a signal log,
   skip same-direction repeats. Without this, phantom signals inflate TV count.  (huge)
4. **Exact candle patterns from the actual library.** Get the Pine library source
   (`AA_CandlePatterns`): wickRatio, body/wick caps must match to the symbol.
   redHammer needs upperWick≤body; invRedHammer needs lowerWick≤body.  (+structural)
5. **Zone formation uses the CURRENT bar's `touched`**, not a persistent
   "touch active" flag. selectedLine = RESISTANCE-priority among touched levels.
6. **Reset the zone after entry** (Pine `Green_Zone := false`) so it can't retrigger.
7. **Not_on_Red_line / Not_on_Gren_line entry filter** on the current bar's lineType
   (long can't enter on a resistance/PD_H bar; short can't on support/PD_L).
8. **Block entries that fill ≥ 15:15** — can't hold past the square-off, so pointless.
9. **Full daily history for chain/pivot lookback** (≥ ~25 prior daily bars). Fetch
   from broker daily API; truncated history → wrong early-period chains.
10. **Skip days with no intraday data** — a data gap is not an engine miss.

## GOTCHAS / TRAPS (don't lose hours here again)
- **Inconsistent exports** (see step 1) — the single biggest time sink.
- **Pivot prev-day attribution.** Pivots for day X come from day X-1. When reading
  TV pivot labels off a chart, the labels over day X are X's pivots (from X-1).
  Verify formula by matching ONE day to the decimal before suspecting the formula.
- **Daily OHLC source.** Engine's daily bars (broker) should match TradingView's
  daily within ~1pt. Verify the broker daily low/high == the intraday file's
  min/max for the same day. Big gaps = data-source mismatch (rare, gap days only).
- **NOT every Pine filter helps.** Pine's `longBelowTrackedHigh` (trackedHigh that
  never resets) over-blocked vs the real trade list — we DROPPED it for +5%. Trust
  the consistent log over a literal Pine translation when they disagree.
- **Match rule:** a trade matches only when entry AND exit (time+side) align. Always
  also print tolerance (entry-exact / within-1-bar) — it tells you "essentially
  right, micro-timing" vs "fundamentally wrong".

## WHAT'S "GOOD ENOUGH"
~90% exact / ~93% entry with zones matching bar-for-bar = engine is faithful. The
last few % are gap-day level micro-edges with diminishing returns; live trading on
the broker's own data is internally consistent regardless.

## ENGINE LOGIC LIVES IN (single source of truth)
`range_trader.py` — pivots (`traditional_pivots`), `build_key_levels`, candle
patterns, `compute_atr`, zone/entry/exit. `validate_strategy.py` mirrors its bar
loop to collect ALL trades. Fix logic in `range_trader.py` so live + validation
stay in sync.
