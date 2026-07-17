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

**As of 2026-07-17, ZONE/state logs carry the full OHLC — `hi=` `lo=` `o=` `c=`.**
Not cosmetic. TV's and Dhan's bars agree on high/low/close and DISAGREE on the open
(~21% of NIFTY bars), and every candle pattern rides on the open. That one difference
IS the fidelity ceiling — but with only `hi=`/`lo=` in the log you cannot see it, and
you will spend a day hunting logic bugs that aren't there (we did). With `o=`/`c=` it
is a ten-minute check, done FIRST, and it tells you when to stop.
```pinescript
log.info("ZONE RED " + str.format_time(time,"yyyy-MM-dd HH:mm","Asia/Kolkata")
     + " hi=" + str.tostring(high,"#.##") + " lo=" + str.tostring(low,"#.##")
     + " o="  + str.tostring(open,"#.##") + " c="  + str.tostring(close,"#.##")
     + " line=" + lineType)
```

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

---
---

# 2026-07-17 — THE CEILING, AND WHY WE SPENT A DAY UNDER IT

**Read this before touching Pine↔Python matching again. It will save you a day.**

Started from *"TV took 2 trades today, the webhook took 1"*. Ended with the live engine
at **49.5% → 78.3%** of TV's entries, **−439.7 → +1,646 points**, 124 trades → **106
(TV: 106)**, max DD **−414 vs TV's −484 (better than TV)**. And, worth more than any of
that:

## 🧱 78% IS THE CEILING. Stop optimising below it.

**TradingView's NIFTY bars and Dhan's NIFTY bars have different OPENs.**
High, low and close match to the paisa. Open does not — on **91 of 436 bars (21%)**.
No rule to it: not the high, not the low, not the previous close, no time-of-day
pattern. It is a vendor difference — an index is recomputed tick by tick, and a bar's
OPEN is whichever tick a vendor snapshots first. Extremes and last-price converge; the
first tick doesn't.

**Why that is fatal for this strategy specifically:**

```
body  = |close − open|      green = close > open      red = close < open
```

Every candle pattern rides on the open. Two points of difference flips a candle's
colour, makes or breaks a hammer, and decides harami's `open < prevClose`. Live-seen:
a bar where harami failed by **0.05 points**.

**21% of bars have a different open → ~21% of zones differ → ~21% of trades differ.**
Our entry match: **78.3%**. That is not a coincidence — that is the wall.

> **If a future Pine↔Python match sits near 78-80% on NIFTY, you are done.**
> Chasing the rest means chasing a data feed, not a bug. To go higher you would have to
> buy TV's data, not fix code.

**Verify the ceiling FIRST, before any logic hunt.** Make the Pine log `o=`/`c=`
alongside `hi=`/`lo=` (the DEFAULT RULE above now says so), export, compare all four
against your bars. Ten minutes. We did it last, after a full day of logic hunting, and
it explained everything the logic hunt could not.

## 🔴 THE BIG ONE: two engines, and every fix was in the wrong one

`run_signal_engine` (places orders) vs `validate_strategy.backtest_day` (tests only —
docstring: *"Mirror of run_signal_engine but COLLECTS every trade"*). Copied for a fair
reason: the engine returns only the LAST signal, a backtest needs all of them. Never
re-synced. **Every Pine-fidelity fix from the 90.2% work landed in the mirror** —
harami patterns, selectedLine RESISTANCE-priority, the TV fill convention, dropping the
tracked_high filter. Measured against the same TV export:

| | vs TV |
|---|---|
| `backtest_day` (mirror, all the fixes) | **75.3%** |
| `run_signal_engine` (traded, none of them) | **49.5%** |

The traded one was net-negative on bars where TV made money. Six drifts, all in the
mirror's favour — full list in `range_trader.run_signal_engine`'s docstring and
LESSONS TRAP #131.

**Fixed structurally, not patched:** the mirror's logic moved INTO the engine;
`backtest_day` is now a wrapper adding only TV's next-bar fill (a genuine backtest
concern). Proof the move was behaviour-preserving: validate_strategy re-scored **75.3%
/ 80% entry-exact / 70 matched — every number identical to before the merge**.

> **The rule:** if the difference between two copies is the OUTPUT SHAPE, add an
> optional output (`trades_out=[]`), never a second brain. And when a strategy has a
> "backtest version" and a "live version", **score the live one** — the other number
> describes a program you don't run.

## 🎯 EVERY ASSUMPTION I MADE THAT WAS WRONG (the expensive part)

Six hypotheses, confidently argued, **all wrong**. Each cost 20-40 minutes. The shape is
identical every time: *reasoned from code I had read, instead of measuring.*

| # | I claimed | Truth | Cost |
|---|---|---|---|
| 1 | "Pattern thresholds differ" | Pine's `minBodySize=0.5, wickRatio=2.5, prevBodyMinPts=0.5` — **identical to ours**. All 7 pattern functions line-by-line identical. | ~30 min |
| 2 | "Our 5m bar data differs" | hi/lo match **91.4% exactly**. The data was fine — the OPEN wasn't, and I only compared hi/lo because that's all the log carried. Should have asked for `o=`/`c=` on hour one. | ~20 min |
| 3 | "10 trading days missing from nifty_daily.csv" | They are **NSE holidays**. The 1-min files exist but are EMPTY. The daily file was correct. | ~25 min |
| 4 | "Our `max_jump_pct` is 50, TV uses 10 for an index" | `zones.py:48` already did `mj = 10.0 if is_index else max_jump_pct`. Already correct. | ~10 min |
| 5 | "Our pivot formula differs" | `traditional_pivots()` is TV's Traditional formula exactly — P, R1-R5, S1-S5. | ~10 min |
| 6 | "The engine under-forms zones (302 vs TV's 430) — I overshot the fix" | The engine formed **393**. `zones_history` just never recorded the ones an entry consumed. **My counter was broken, not the engine.** | ~30 min |

**Also wrong, and it mattered more:** I framed the project as *"save ₹19,200/yr vs 246
points of drift"* and told the user it was barely break-even. He corrected me — matching
TV isn't about the subscription. It unlocks **10-year backtests at any resolution,
optimization sweeps, many instruments, trailing, partial booking, RMS, sizing** — none
of which TV can do at all. The subscription is the smallest number in the room.
**Don't reduce a capability question to a cost question.**

## 🔧 MY OWN TOOLS LIED THREE TIMES

A diagnostic that lies is worse than no diagnostic.

1. **`live_vs_tv.py` dropped reversals.** `if pos is None: pos = ...` silently discarded
   any ENTRY arriving while a position was open. On 2026-04-06 the engine went SHORT
   09:50, reversed LONG 11:20 and made +356 — exactly what TV did, to the paisa. The
   tool recorded the long as never happening and left the short open to 15:15 for −398.
   The same event then appeared TWICE in the analysis: as "the single biggest missed
   trade, +355.8" and as "−354 of the exit gap". **~710 of a reported 951-point gap was
   one artifact.** I showed the user the broken number before checking it; he correctly
   questioned the whole project's premise on the strength of it.
2. **The signal replay reported one signal N times.** `run_signal_engine` returns the
   LAST signal found, however old — that is what `sig_bar`/`total_bars` are FOR, and the
   adapter read only `out[0..2]`. Result: 15 identical EXIT_SHORTs at the same spot.
3. **The zone diagnostic ran on the pre-fix zone list.** Its 34/16/33/3 breakdown was
   computed while 91 zones were unrecorded. I reasoned off it for 30 minutes.

> **The rule:** verify the measuring tool against a case you can read by hand — one
> day's pine log — BEFORE trusting a single number it prints. Every one of these was
> catchable in 30 seconds that way.

## 🚫 TV-SIDE ARTIFACTS — not your bug, don't "fix" them

- **15:30 zones (33 of 430).** `prevClose = request.security(..., "D", close[0])` with
  lookahead OFF returns yesterday's close all day — but at 15:30 the daily bar CLOSES,
  so it becomes **today's** close. TV's PD_C is self-referential on the last bar. Ours
  uses yesterday's throughout, which is correct. They can never trade anyway (no entry
  after 15:15) and our data has no 15:30 bar. **Exclude them from any zone count** —
  TV's real tradeable zone count was 390, not 430.
- **TV enters at 15:20 and exits at 15:25** (4 trades in 6 months). The Pine has NO
  no-entry-after gate. We refuse — two brokerages for nothing. These will always read as
  TV-only mismatches, and refusing is the correct trade.

## 🐞 A REAL BUG FOUND IN THE PINE (fixed 2026-07-17)

`ta.pivot_point_levels` returns `[P, R1, S1, R2, S2, R3, S3, R4, S4, R5, S5]`.
The script read indices 7..10 as `S4, R4, S5, R5` — **R4/S4 and R5/S5 swapped**. So a
touch on the real R4 was labelled "SUPPORT", which let a GREEN zone form there —
`lineType` drives `Not_on_Red_line`/`Not_on_Gren_line`, so this **gated real entries**.
Proven by measurement, not by reading: of TV's touches, R1/R2/R3 → our R1/R2/R3 and
S1/S2/S3 → our S1/S2/S3 all correct, but **"RESISTANCE" → our S4, 3 times**. Only 3
because R4/R5 sit far from price. Fixed in the Pine (7↔8, 9↔10).

## ✅ THE ORDER TO DO THIS IN NEXT TIME

1. **Get the RUNNING script, not the repo's copy.** `_PINE/range_chain.pine` was NOT
   what he runs. The live script is `Ars_Auto_Rev_Chain_common`, with settings
   **hardcoded** rather than inputs (`maxCandleSize=40` where we assumed 25;
   `SL_Blw_Fib_Exit_Tog=false`, so the "missing Fib Exit" gap didn't exist at all).
   **Never read Pine values from a file you didn't watch him load.** Ask for the text.
2. **Verify the data ceiling.** Log `hi/lo/o/c`, compare all four. If the opens differ,
   you now know your maximum and can stop when you reach it.
3. **Score the LIVE engine** (`_TOOLS/live_vs_tv.py`), never a backtest copy.
4. **Count zones, not just trades.** Zones are the raw material; a trade gap is a zone
   gap one step downstream. TV's log states every zone — use it.
5. **Split the misses by cause before fixing anything.** touch / pattern / line-type /
   no-bar. Each is different work and most of it isn't yours.
6. **Exclude the untradeable.** Anything at/after the squareoff can never become a trade.

## 📐 TOOLS (built this day, reusable)

- **`_TOOLS/live_vs_tv.py`** — drives the LIVE engine (via `run_signal_engine`'s
  `trades_out=` collector, never a copy) against a TV List-of-Trades export. Prints
  per-side net/win%/PF/Sharpe/maxDD + entry match, and renders **every trade on one
  chart, TV orange vs engine blue** (`ACCURACY SCORE CLAUD/live_vs_tv.html`). This whole
  finding exists only because both were finally put side by side and looked at.
- **`_TOOLS/validate_strategy.py`** — unbroken this day. It had been dead since the
  2026-07-09 refactor (`import range_trader` → ModuleNotFoundError; `write_html` →
  FileNotFoundError, dying AFTER doing the whole backtest). **The harness that produced
  the 90.2% everyone quotes had not been runnable for months, and nobody noticed because
  nobody ran it.** If you quote a number, re-run the thing that made it.
- **`_ops/signal_replay.py`** — now has an adapter for this engine (`ADAPTERS["range"]`).
- **`_ops/pine2python_drift.py`** — daily TV-vs-python drift, `--since <date>`.

## ⚠️ NUMBERS THAT ARE NO LONGER TRUE

**The 90.2% exact / 93% entry is dead.** It was measured at `maxTradesPerDay=4`,
`maxCandleSize=25`, against a TV export from a different script version. Current script,
current settings, his own export, Jan 6 – Jun 16: **75.3% exact entry+exit, 80% entry
exact** (backtest engine) and **78.3% entry** (live engine, Jan 6 – Jul 9).

Re-score from a fresh export before quoting fidelity again — and quote **points**, not
%. "78% match" sounds fine right up until you learn the missing 22% held all the money.
It didn't here (the real gap is 246 points over 6 months ≈ the cost of the TV
subscription), but only measurement can tell you that, and a % never will.
