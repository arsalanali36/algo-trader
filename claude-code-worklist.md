# algo-trader — Claude Code Worklist

**Last updated:** 2026-07-12
**How to use this file:** Paste the whole "PROMPT FOR CLAUDE CODE" block below into
a Claude Code session. As tasks complete, move them to the "DONE LOG" section at the
bottom with the date + commit hash, so this file stays a live, accurate worklist
instead of going stale. Add new tasks at the end of the prompt block (Task 7, 8...)
rather than starting a new file.

**SELF-MAINTENANCE (Claude Code should do this automatically, not just me):**
Every Claude Code session working from this file must, at the end of its own
work, update this file itself:
- Tick the DONE LOG checkbox(es) for whatever it completed, with date + commit
  hash — don't leave that for me to do by hand.
- If it finds a NEW gap/bug/TRAP while doing any task here (the way Task 6 was
  found while doing Task 2, or Task 1 was found from TRAP #109), append it as a
  new numbered Task at the end of the PROMPT block, in the same format as the
  existing tasks — don't just mention it in chat and let it evaporate.
- If a task turns out to already be done, or blocked on something (a decision
  from me, a missing file, etc.), note that inline next to the task instead of
  silently skipping it.
This file is the standing worklist across sessions — treat updating it as part
of the task, not a separate ask.

---

## PROMPT FOR CLAUDE CODE

```
Context: scratch/nifty_trend/ pipeline (run_hunt.py) already gates every
strategy on significance (p<0.05) + Monte Carlo before it's shippable.
But TRAP #109 (rolling-ATM marking bug, #06 shortvol) proved this gate
can PASS a strategy whose underlying DATA is corrupt. The correlation
matrix (runs/compare.json) currently just sits on a dashboard — it isn't
wired into capital allocation. Real DOM-calibrated slippage (ADR-005)
was wired in on 2026-07-12 but most of the hub's saved results predate
it. And everything below is still done via one-off manual runs — this
session also sets up recurring /loop automation so this discipline runs
itself going forward.

Work through tasks in order. TASK 0 is HIGHEST PRIORITY — complete and
verify it BEFORE market open tomorrow, no exceptions. TASK 6 runs next,
before Task 5. TASK 2 is deprioritized (see note below) — skip it
unless explicitly asked.

═══════════════════════════════════════════════════════════
TASK 0 — GO-LIVE SAFETY CHECK for 7 strategies running simultaneously
tomorrow. Do this FIRST, verify each item, do not skip any.
═══════════════════════════════════════════════════════════
Context: 01-05 and 07 (6 strategies total, #06 confirmed OFF) are about
to run together for the first time. Found via audit: 5 of 6 don't
dedupe LTP calls (only range_trader does — 06 also does but is OFF), a
gitignored config means active-flags/capital-caps can't be verified
from a repo clone, and this codebase has a documented recurring
deploy-drift failure mode (TRAP #7 shape, has bitten live orders twice
before). CRITICAL: total account capital is ₹10,00,000 on Zerodha,
shared across ALL 6 strategies together — see 0c for the hard
constraint this creates.

a) LTP DEDUPE GAP: wire strategies/live/01_rsi_v1.py, 02, 03, 04, 05,
   07 (all except range_trader.py, which already does this — 06 also
   does but stays OFF) to use shared_ltp_cache.py the same way, instead
   of calling broker.quote() directly. Goal: avoid queued LTP latency
   when 6 processes want quotes around the same moment. Test in paper
   mode, confirm no signal-timing regression.

b) #06 STATUS — CONFIRMED 2026-07-12: shortvol_trader (06) stays OFF
   tomorrow. Do NOT flip it active under any circumstance in this
   session. Just double-check nifty_config.json / STRATEGIES config
   has it as active:false (or absent from the auto-start list) and
   report that confirmation — no further action needed on 06 itself.

c) CONFIG VERIFICATION (must be done ON the actual machine/VPS, not
   from a fresh clone — nifty_config.json is gitignored):
   - For each of the 6 LIVE strategy ids (01/dvert/orbst/chainzone/
     backspread/banknifty — NOT 06/shortvol, confirmed OFF), confirm
     active:true/false matches what I actually intend to run tomorrow.
   - HARD CONSTRAINT — TOTAL CAPITAL: my entire Zerodha account has
     ₹10,00,000 available for trading, and ALL strategies share this
     SAME pool — there is no additional capital beyond this ₹10L
     combined, across all 6 live strategies together, not per strategy.
     Concretely:
       * Confirm nifty_config.json["_risk"]["global"]["capital_rs"] is
         set to ≤ ₹10,00,000 (this is the account-wide ceiling every
         strategy draws from via risk_gate.check_capital()/
         capital_in_use()).
       * If nifty_config.json["_risk"]["per_strategy"][<id>]
         ["capital_rs"] overrides exist for any of the 6, SUM them and
         confirm the sum does not exceed ₹10,00,000 — if per-strategy
         caps are set independently without regard to the shared total,
         that's a bug: strategies could collectively try to deploy more
         than the account actually has. Flag this explicitly if found,
         don't silently fix the numbers — show me the current sum vs
         ₹10L and recommend a split, then wait for my go-ahead on the
         actual per-strategy numbers.
       * Confirm risk_gate.capital_in_use() correctly pools LIVE-mode
         capital across all 6 processes (cross-process, not just
         within one strategy's own process) — this was fixed for
         paper-vs-live pool separation on 2026-07-10, verify it still
         holds now that 6 live strategies (not 1-2) will be drawing
         from the same ₹10L simultaneously.
   - Report this as a table: strategy_id | active | capital_rs
     (resolved) | max_loss_rs | profit_target_rs, PLUS a final line:
     "sum of per-strategy capital_rs across all 6 active = ₹X vs ₹10L
     ceiling — [OK / OVER by ₹Y]".

   - PER-STRATEGY REAL MARGIN/PREMIUM (1 lot, TODAY's actual numbers,
     not guessed/historical): leg structure differs across the 6 —
     00/01/03/04/07 are naked-long (BUY-only legs, no SPAN margin,
     cost = today's ATM premium × lot size); 02/dvert and 05/backspread
     are true hedged spreads (a SELL leg covered by a BUY leg, real
     SPAN margin applies via broker_real_margin()). For each of the 6:
       * 00/01/03/04/07: fetch today's actual ATM CE/PE premium (via
         the same live quote path the strategy itself uses) and
         compute premium × lot_size (× 2 for 01/straddle, which has
         both a CE and PE leg).
       * 02/05: call risk_gate.broker_real_margin() with each
         strategy's actual current typical leg configuration (today's
         real strikes) to get the real SPAN+premium figure Zerodha
         would charge for 1 lot.
     Add this as a column to the capital-verification table:
     strategy_id | structure (naked-long/hedged-spread) | today's
     1-lot cost (₹) | capital_rs allocated | headroom. This makes the
     ₹10L-ceiling check concrete against real numbers, not assumptions.

d) DEPLOY-DRIFT CHECK (TRAP #7 shape — has caused live order-path
   outages twice before): before market open, md5-compare every file
   VPS is running against local git HEAD for all 6 live strategy
   scripts + their shared dependencies (execution_gateway.py,
   risk_gate.py, strategy_safety.py, smart_order.py,
   dhan_rate_limiter.py, shared_ltp_cache.py, shared_candle_cache.py,
   order_store.py). Report any mismatch before I go live — do not
   silently assume VPS is in sync.

e) DUPLICATE-PROCESS CHECK: confirm get_pid()/get_mode() cross-platform
   dedup (fixed 2026-06-23) still correctly prevents a restart from
   spawning a second copy of any of the 6 live processes. Do a live
   test: trigger a restart of one strategy, confirm exactly one
   process remains via `_proc_cmdline()`/pgrep.

f) DATA-COLLECTION SANITY: confirm auto_data_downloader.py and
   option_chain_collector.py are both configured to cover BANKNIFTY
   (new underlying for #07) at the same cadence as NIFTY, and that
   07_banknifty_trader.py's ATR/OR warm-up (TRAP #85 pattern) has
   enough historical bars available before 9:15 market open tomorrow
   — don't just check the code path exists, actually run it once
   against real recent data and confirm it produces a non-empty
   warm-up window.

g) CRASH/RESTART SAFETY: confirm every one of the 6 live strategy
   processes writes to order_store on every entry/exit (not just logs)
   so a crash mid-day can recover state on restart — this was a known
   gap for older ema_v1/rsi_v1 (non-numbered) traders; confirm 01-05/07
   specifically don't share that gap (they should already be gateway-
   wired per architecture_audit.py, but VERIFY, don't assume from the
   audit passing generically).

h) LOG ROTATION (low priority, note only, don't block on this): no
   RotatingFileHandler found anywhere — logs will grow unbounded over
   weeks of continuous running. Not urgent for tomorrow, but log as a
   Task 9 for later, don't fix it tonight and risk destabilizing
   anything close to market open.

Report back a single go/no-go summary for tomorrow: which of a-h are
CONFIRMED SAFE, which need my direct action on the VPS/machine that
Claude Code cannot do remotely (especially the ₹10L capital-sum check
in 0c and the nifty_config.json active-flag check), and — most
important — the resolved capital table from 0c so I can see with my
own eyes that nothing can collectively exceed ₹10,00,000 before I let
this run live tomorrow.

SELF-MAINTENANCE: this prompt lives in claude-code-worklist.md in the repo
root. At the end of this session, update that file yourself — tick off
whatever you completed in its DONE LOG (with date + commit hash), and if
you find any new gap/bug worth tracking while doing this work, append it
as a new numbered Task at the end of this same prompt block inside that
file, not just in your chat summary.

═══════════════════════════════════════════════════════════
TASK 6 — Re-generate ALL hub strategies with real DOM-calibrated
slippage (close the pre/post ADR-005 gap) — DO THIS FIRST
═══════════════════════════════════════════════════════════
Context: ADR-005 (real bid/ask spread from DOM data) was wired into
bs_option.reprice*/option_structures/real_struct2 as SLIP_ENABLED=True
default on 2026-07-12 15:56. But every strategy currently in
scratch/nifty_trend/runs/ (mid_orb_nifty, chain_zone_longatm,
long_straddle_orb, debit_vertical_orb, orb_supertrend,
ratio_backspread, banknifty_hunt, pivot_continuation, and the rest)
was generated BEFORE that commit — meta.json "created" timestamps are
all 07-10 through 07-12 09:57, all before 15:56. Every Sharpe/net%
number currently shown in the hub is a pre-real-spread, zero-slip
number, even though the vol-family (short_straddle/iron_fly) was
separately manually re-tested and updated (LESSONS #111).

a) Re-run `run_hunt.py --name <slug>` for every strategy currently in
   runs/index.json (not just vol-family) with SLIP_ENABLED at its now-
   default True. Do NOT touch or re-litigate the vol-family results
   (already correctly re-tested) — only regenerate the ones that
   predate the ADR-005 commit.
b) For each, produce a before/after table: slug, old bs_full.sharpe,
   new bs_full.sharpe, old net_pct, new net_pct, delta. Save as
   scratch/nifty_trend/SLIP_RECOST_2026-07-12.md.
c) Flag explicitly, in bold, any strategy whose Sharpe crosses the 1.0
   shippability gate in either direction from adding real slippage.
   Treat this the same severity as a data-integrity fail from Task 1 —
   it means the "good number" was a cost-model artifact, not a real
   edge.
d) Only overwrite a strategy's live runs/<slug>/ files with new
   numbers AFTER producing the before/after table — I want to see the
   delta before old numbers disappear. Do not silently replace them.
e) Confirm no run_hunt.py code path can override SLIP_ENABLED back to
   False without an explicit flag, so this gap can't recur on future
   strategies.

Report the before/after table directly in your summary at the end of
this session, not just as a file I have to go open.

═══════════════════════════════════════════════════════════
TASK 1 — Data-integrity pre-check (bolt onto run_hunt.py, before
significance runs)
═══════════════════════════════════════════════════════════
For any strategy using optlake_load.py or real_struct2.py (rolling/
relative-strike option data):
a) Auto-run a TRAP #109-style sanity check: pick the single worst
   spot-move day in the backtest window, hand-compute expected P&L
   DIRECTION for the position type (|move| x qty - credit), and assert
   the backtest's P&L on that day isn't the wrong sign / suspiciously
   good. Fail loud if it is.
b) Generalize the TRAP #107 coverage check (currently only in
   ironfly_frame) into a reusable `assert_coverage(frame, reference,
   min_pct=0.9)` helper. Call it in every build_*.py that does an
   inner-join on a still-filling data source.
c) Write both results into meta.json as a "data_integrity" block. If
   either fails, run_hunt.py refuses to mark the strategy shippable
   even if significance+MC pass — same treatment as a significance
   fail ("Honest result: no edge to ship" / here: "Honest result: data
   not trustworthy, re-verify").

═══════════════════════════════════════════════════════════
TASK 2 — Correlation-aware capital allocation
[DEPRIORITIZED as of 2026-07-12 — still single-lot PAPER trading, no
real capital allocation decision to make yet. Do NOT work on this task
unless explicitly asked. Keep it here so it isn't lost, and revisit
once strategies move beyond single-lot paper toward real capital
sizing across multiple strategies.]
═══════════════════════════════════════════════════════════
a) Flag near-duplicate strategies in compare.json / build_compare.py:
   any pair with correlation > 0.85 gets a "REDUNDANT_PAIR" warning in
   the hub output (found live: chain_zone_naked vs chain_zone_credit =
   0.994, long_straddle_orb vs long_strangle_orb = 0.915). Surface
   only — don't auto-delete, I decide which of each pair gets capital.
b) The ORB family (mid_orb_nifty, debit_vertical_orb, orb_supertrend,
   ratio_backspread, banknifty_hunt) is pairwise correlated 0.3-0.66 —
   mostly one factor ("NIFTY trending day"). Add a
   `portfolio_correlation_gate.py`: on any day where 3+ correlated
   (>0.3) strategies fire simultaneously, log a "CORRELATED_CLUSTER"
   event with the underlying-move context. Read-only/logging first —
   no auto-blocking trades yet.
c) Document current per-strategy capital caps vs this correlation
   matrix in a short markdown note — flag any strategy sized as if
   independent when it isn't. Don't fix silently.

═══════════════════════════════════════════════════════════
TASK 3 — Close 3 known-open architecture gaps
═══════════════════════════════════════════════════════════
a) scripts/pre-commit-architecture-audit.sh exists but isn't installed
   as .git/hooks/pre-commit. Write scripts/setup_hooks.sh that installs
   it, reference it from CLAUDE.md onboarding, and actually run it now
   + confirm it fires on a test commit.
b) strategies/live/range_trader.py calls risk_gate.check_drawdown/
   check_concentration/check_capital inline (~lines 1249-1282) parallel
   to execution_gateway. Migrate to route through execution_gateway per
   Rule 6B. Flag any behavior difference BEFORE changing it.
c) strategies/live/universe_trader.py's `_state` dict is RAM-only;
   recovery-from-order_store silently falls back to fresh-empty on
   failure. Add a startup WARN log any time that fallback fires.

═══════════════════════════════════════════════════════════
TASK 4 — webhook_executor.py gateway migration (do carefully, high-stakes)
═══════════════════════════════════════════════════════════
webhook_executor.py still uses gate_entry()+smart_order.execute()
directly instead of execution_gateway — this is my live TradingView
order path.
- First: diff exactly what execution_gateway does differently. Report
  the diff BEFORE writing migration code.
- Migrate only after I confirm the diff is safe.
- Test in PAPER mode only, verify signal parity against the old path
  on real recent webhook signals (TRAP #108 signal-replay pattern)
  before any live cutover.
- Do not touch anything live-order-related without an explicit
  paper-verified PASS reported back to me first.

═══════════════════════════════════════════════════════════
TASK 5 — Set up recurring /loop automation for all of the above
═══════════════════════════════════════════════════════════
Goal: this discipline (audit, EOD-drift check, hunt pipeline) should
run itself going forward, not depend on me remembering to trigger it.

a) Create .claude/commands/architecture-audit-loop.md — runs
   _TOOLS/architecture_audit.py, and if any NEW fail/warn appears (diff
   against last run's saved result in data/audit_history/), drafts a
   fix as a PR-style diff for my review — does NOT auto-commit.

b) Create .claude/commands/eod-report-review.md — reads the latest
   data/reports/eod_<date>.html + signal_replay output, and if any
   MISS/EXTRA/GATED anomaly appears that isn't already a known/
   documented pattern, drafts a diagnosis + fix draft for my review.
   Trigger it once manually first on today's/yesterday's report to
   confirm sane, non-noisy output before scheduling.

c) Create .claude/commands/hunt-pipeline.md that wraps
   `run_hunt.py --name <slug>` (screen→optimize→significance→MC→
   data-integrity from Task 1→3-pass→BS-reprice) so any new strategy
   idea runs through the FULL gate via one command, not ad-hoc.

d) HARD EXCLUSION — write this explicitly into CLAUDE.md as a rule:
   NONE of the above loops may ever touch, edit, or auto-fix
   execution_gateway.py, webhook_executor.py, smart_order.py,
   risk_gate.py, or any file under _core/ that's on the live order
   path. Those changes stay manual-only, one Claude Code session at a
   time, explicit review before any commit. Loops are for research/
   audit/reporting only — never for code that can place a live order.

e) If two independent tracks need to run in parallel (e.g. research
   work + architecture fixes at the same time), use separate git
   worktrees for each and route through hunt_guard.py's existing lock
   registry (built after the 2026-07-10 conflict where two sessions
   killed each other's builds) — never run two sessions against the
   same working directory unguarded.

f) BUDGET GUARDS — add to every loop defined in 5a/5b:
   - max_runtime_min: hard wall-clock cap per run (30 min for
     architecture-audit-loop, 45 min for eod-report-review) —
     independent of token budget, kills a stuck/orphaned run even if
     tokens haven't run out.
   - After each run, append a one-line entry to
     data/loop_activity.log: timestamp, loop name, tokens used,
     runtime, result (clean/fixes-drafted/failed). A log file is
     enough — don't build a dashboard for it.

g) FREQUENCY — don't run these continuously:
     /loop 2x-daily /architecture-audit-loop   (once pre-market ~9:00
       IST, once post-market ~15:50 IST — outside active trading/dev
       hours)
     /loop 1d /eod-report-review               (once, ~16:00 IST,
       after eod_report.py's own 15:45 timer has already run)
   Do not schedule anything at shorter intervals unless explicitly
   requested later. Two touchpoints a day is the target, not
   always-on polling.

h) IDLE-AWARE, NOT TIME-BLIND — before a scheduled loop run starts,
   check for evidence of an active manual session on the same repo
   (recent uncommitted changes in the last 15 min, or a lock held by
   hunt_guard.py's registry). If found, skip this run and log
   "skipped — active session detected", don't queue/retry — just wait
   for the next scheduled slot. Loops should never compete for the
   same files while I'm actively working.

max_consecutive_failures=3 for every loop — if a loop can't produce a
clean result 3 times running, it stops and flags me instead of looping
forever.

═══════════════════════════════════════════════════════════
TASK 7 — Test market-open entry for short straddle (untested variant
of user's original intent) + revisit combining with IV-rank gate
═══════════════════════════════════════════════════════════
Context: All prior short-straddle/iron-fly research (real_struct2.py)
used entry h0=10 (10:00 IST), not true market-open. User's original
mental model was "sell ATM straddle right at market open, let combined
premium decay through the day" (the Sensibull-style decay-curve view)
— that specific entry-time variant has never actually been tested.
Separately, earlier research found a real, positive-but-thin edge:
IV-rank-gated positional short-vol flipped iron-condor from -7.1% to
+1.4% (Sharpe 0.20) and iron-fly to +1.1% (Sharpe 0.28) when only
selling at IV-rank >= 0.5/0.85 — promising direction, but too few
trades/yr (10-36 vs the ~100 gate) and Sharpe below the 1.0 gate to
ship standalone (see git a1a0a64, 3d914cf).

a) Re-run the INTRADAY short_straddle test (real_struct2.py,
   real premium + real DOM slippage) with entry at true market-open
   (try h0/entry ≈ 9:20-9:25, i.e. shortly after the opening auction
   settles, not 9:15 raw open — avoid the first few minutes' auction
   noise) instead of 10:00. Keep tp_frac/sl_frac and exit (3:15) the
   same as the existing baseline so the comparison is apples-to-apples.
   Report: does the extra ~35-40 min of theta collection outweigh the
   added gap/whipsaw risk from trading through the early-session
   volatility window? Full before/after vs the existing 10:00 baseline
   (~-1.0% naked straddle, ~-41% iron-fly).

b) Combine (a)'s market-open entry with the IV-rank gate from a1a0a64
   (sell only when IV-rank >= 0.5 or 0.85) — does the earlier-entry
   premium help clear the significance-gate trade-count problem (10-36
   trades/5yr was too thin to ship), or does it stay too infrequent?
   This is the "combined-signal design" the earlier commit message
   flagged as the one live thread worth revisiting.

c) Apply the same data-integrity checks from Task 1 (worst-day sanity,
   coverage-guard) to any new result here before trusting the numbers
   — this family has already produced one fooled-by-marking-bug result
   (TRAP #109), so treat any promising-looking short-vol number with
   extra scrutiny by default.

d) Do NOT deploy anything from this task without an explicit go-ahead
   from me — this is research only. Report findings, including if the
   result is negative — an honest "still doesn't work at market-open
   either" is exactly as valuable as a positive result here.

This task is exploratory research, not a go-live blocker — do it after
Tasks 0/6/1/3/4/5 are handled, not before.

═══════════════════════════════════════════════════════════
TASK 8 — OI unwinding-at-key-levels hypothesis (STRICT staged testing
— do not skip stages or combine them, overfitting risk is high here)
═══════════════════════════════════════════════════════════
Context: hypothesis is that call/put OI unwinding (chg_oi < 0 while
price moves) means something different depending on WHERE price is —
near a major support/resistance level vs mid-range. OI data (oi,
prev_oi, chg_oi per strike) already exists in the option-chain lake
(ADR-004) and live collector — no new data collection needed.
Reusable pivot infra already exists: _CHARTING/zones.py
traditional_pivots()/build_key_levels() (extracted from range_trader).

This combination (level-type × proximity × majorness × freshness ×
direction) has enough tunable knobs to overfit easily if tested all at
once. STAGE STRICTLY — do not proceed to the next stage unless the
current stage produces a significance-gate PASS. If a stage is flat or
negative, STOP and report that honestly — do not keep adding
complexity hoping a combination works.

STAGE 1 (do this first, nothing else):
  - Levels = traditional_pivots() only (P/R1-R3/S1-S3, prev-day H/L/C).
  - Proximity = fixed 0.5×ATR, no sweep yet.
  - No majorness/freshness weighting.
  - Test TWO SEPARATE hypotheses, do not merge them:
      (A) call-unwinding near SUPPORT → reversal/bounce signal
      (B) call-unwinding near RESISTANCE → breakout-confirmation signal
  - Run each through the full existing gate (significance.py p<0.05,
    montecarlo.py, Task 1's data-integrity checks — OI data can have
    the same coverage/truncation issues as premium data, TRAP #107
    shape, check for it explicitly here since this is untested
    territory).
  - Report both results plainly, including if both are flat/negative.
    A negative result here is a valid, useful outcome — record it in
    LESSONS.md as a TRAP/finding either way, don't just discard it.

STAGE 2 (ONLY if at least one of Stage 1's two hypotheses passes
significance):
  - RV (20-day rolling realized vol) already exists in bs_option.py's
    sigma_map builder; real IV already exists in the option-chain lake
    — an IV/RV spread or ratio is a trivial derived feature, no new
    data engineering needed.
  - Test exactly ONE of these three candidate refinements at a time on
    the Stage-1 hypothesis that passed — do NOT stack more than one in
    the same run:
      (i)   majorness — touch-count weighting on the level
      (ii)  freshness — recency weighting on the level
      (iii) IV/RV filter — only trust the unwinding-at-level signal
            when IV/RV spread clears a threshold (e.g. only act on it
            when IV is rich vs RV — the same VRP condition that made
            the IV-rank gate work in Task 7/a1a0a64). Keep this as a
            simple filter (pass/no-pass) in this stage, not a
            structure-selector (buy-vs-sell decision) — that's a
            bigger design question, defer it to a later stage/task if
            this filter itself proves out.
  - The bar to proceed with any one of (i)/(ii)/(iii): OOS Sharpe must
    be genuinely better than the Stage 1 baseline, not just
    train-period better. If none of the three individually clears
    that bar, stop here and ship the simpler Stage 1 version (or
    nothing, if even that doesn't clear the gate on its own merits) —
    do not combine two of the three hoping that helps.

STAGE 3 (ONLY if Stage 2 holds on OOS):
  - Test Max-OI-strike as a REPLACEMENT for traditional_pivots() as the
    level source (not additive/combined with pivots) — does OI-wall
    location work better, worse, or about the same as price-pivots as
    the "key level" definition?

Do NOT deploy anything from this task without an explicit go-ahead
from me. This is research only, same as Task 7.

After each task, run architecture_audit.py and confirm PASS count
doesn't regress. Report before/after audit output. At the end, give me
a plain-language summary of what loops are now scheduled, on what
interval, and what they're explicitly NOT allowed to touch.
```

---

## SAFETY RAILS — quick reference (why each exists)

| Rail | What it prevents |
|---|---|
| Live-path hard exclusion (5d) | Loops can't touch `execution_gateway.py`, `webhook_executor.py`, `smart_order.py`, `risk_gate.py` — automation never gets near real order placement |
| `max_consecutive_failures=3` | Loop doesn't retry-spiral forever on a broken fix |
| No auto-commit | Every fix is a draft for review, never silently merged |
| First-run manual trigger (5b) | New loop's output gets eyeballed once before being trusted on a schedule |
| Paper-only + signal-parity for webhook migration (Task 4) | Live-money path only changes after an explicit paper-verified PASS is reported back |
| Worktree + hunt_guard for parallel sessions (5e) | Prevents repeat of the 2026-07-10 conflict where two sessions killed each other's builds |
| `max_runtime_min` (5f) | Kills a stuck/orphaned run even if it hasn't burned through its token budget |
| `data/loop_activity.log` (5f) | One-line-per-run cost/result record — no dashboard needed to sanity-check spend |
| 2x-daily frequency, not continuous (5g) | Loops don't run during active trading/dev hours by default |
| Idle-aware skip (5h) | Loop detects an active manual session and steps aside instead of fighting for files |

**Known gap, not yet added:** no automated dollar/token spend cap or alert threshold beyond the log file — currently manual review of `loop_activity.log`. Add if this becomes a problem.

---

## DONE LOG

*(Move completed tasks here with date + commit hash as they land, so this file
stays the single source of truth instead of drifting from what's actually shipped.)*

**Commit map (2026-07-13 session, local commits — NOT yet pushed / deployed; VPS deploy scheduled post-market):**
`b68cdbe` Task 6 + Task 1 · `85de33d` Task 3a + Task 3c · `e32bf3e` Task 3b · `1384058` Task 4 · `d5b6a17` Task 5.
Task 0 = verification/report only (no code). "commit: pending" inline below = one of these hashes per the map. **Post-market: push to origin + deploy git HEAD to VPS (drift sync + this session's changes) with live-position checks before any restart.**

- [~] Task 0 — GO-LIVE safety check — **VERIFIED 2026-07-13 ~08:15 IST** (pre-market). Findings: (b) #06 shortvol_v1 active:False ✅ OFF; (c) global capital_rs ₹10L ✅, per-strategy caps all null (no independent-sum-over-₹10L risk), **ALL active strategies = mode:paper → ₹0 real capital at risk** (the ₹10L live scenario does NOT apply today); (d) 🔴 **DEPLOY-DRIFT: 8 files older on VPS** than git HEAD (02-07 traders + execution_gateway + risk_gate) — flagged, NOT auto-deployed; (e) dup-process dedup ✅ present; (f) BANKNIFTY collectors ✅ cover sec_id 25, running; (g) 02-07 all order_store/gateway-wired ✅; (a) LTP dedupe: 02 & 05 call broker.quote directly at entry (01/03/04/07 don't loop-poll; range_trader ✅), monitoring centralized via ltp_poller — low severity in paper. **Remediation pending user decision:** deploy-drift sync + 02/05 LTP wiring (post-market). *(commit: pending)*
- [x] Task 6 — slip re-cost of all hub strategies — **DONE 2026-07-13.** All 14 runs predated ADR-005 (0 post). Re-cost via `dom_recost.py` (read-only, no overwrite per 6d). Full before/after → `scratch/nifty_trend/SLIP_RECOST_2026-07-13.md`. Deltas small (≤0.26 Sharpe). **6c flags:** 🔴 debit_vertical_orb(02) OOS 1.005→0.926, 🔴 chain_zone_positional(04-P) OOS 1.062→0.985 — both cross <1.0 in OOS only (full-period stays >1). Live fauj 00/01/03/04/05 keep full Sharpe >1 → edge real, not cost-artifact. 6e ✅ SLIP_ENABLED can't silently revert. **Hub overwrite pending user decision.** *(commit: pending)*
- [x] Task 1 — data-integrity pre-check — **DONE 2026-07-13.** New `scratch/nifty_trend/data_integrity.py`: (1a) `worst_day_sanity()` — TRAP #109 guard, catches a long-option posting a gain on the worst ADVERSE spot move (physically impossible = marking bug); (1b) `assert_coverage(frame, ref, min_pct=0.9)` — TRAP #107 generalized, now backs `optlake_load.ironfly_frame` + `real_struct2.grid` inner-joins. (1c) `check()` wired into `run_hunt.py` → writes `data_integrity`+`shippable` into meta.json; significant-but-corrupt → prints 🔴 and shippable=False. Unit-tested (clean pass / #109-shape fail loud / coverage 30-of-50 fail) + verified no false-fail on 3 real recorded runs. *(commit: pending)*
- [ ] Task 2 — correlation-aware capital allocation *(deprioritized — single-lot paper stage, revisit later)*
- [x] Task 3a — pre-commit hook installed — **DONE 2026-07-13.** Hook was already installed on this machine (2026-07-07) + firing (ran on commit b68cdbe); real gap was no installer for fresh clones (`.git/hooks/` untracked). New `scripts/setup_hooks.sh` (idempotent, cross-platform, uses `git rev-parse --git-path hooks`), referenced in CLAUDE.md Rule 6B point 6, run + verified. *(commit: pending)*
- [x] Task 3b — range_trader migrated to execution_gateway — **DONE 2026-07-13 (user-approved after diff).** Equity `else` branch's inline `check_drawdown/check_concentration/check_capital` (+ fail-OPEN-on-exception) replaced with `strategy_safety.gate_entry()` (same single gate the option branch + execution_gateway use — adds gating_status/broker-funds; equity `seg="NSE_EQ"` so FNO-liquidity check skips). `place_order` already delegates to execution_gateway. CAPITAL_BLOCKED row recording preserved in caller. Fixed a real scope bug caught in review: `get_broker` was a function-local import only on the option path → added `from brokers import get_broker` in the equity block (else UnboundLocalError live). Compiles + audit-clean (range_trader not in FAIL/WARN). Deploy+paper-watch post-market. *(commit: pending)*
- [x] Task 3c — universe_trader state-recovery WARN log — **DONE 2026-07-13.** `_recover_state_from_order_store` except-path upgraded from a plain log to a loud ⚠️ WARN spelling out the risk (prior-run open positions become UNMANAGED by this process on a recovery failure). Compiles. *(commit: pending)*
- [x] Task 4 — webhook_executor gateway migration — **DONE 2026-07-13 (user-approved), offline-parity-verified; live cutover still gated on VPS paper-replay.** Added `sl_type_override` param to `execution_gateway.execute_signal` (→ `default_instrument_sl_tags(mode_override=...)`). Migrated webhook `_do_entry`'s main leg from hand-rolled `gate_entry`+`smart_order.execute` to `gw.execute_signal(..., sl_type_override=cfg["sl_type"], tag="TVWH", source="webhook", group_id=...)`. Preserved: est_price single-fetch, CAPITAL_BLOCKED recording in caller (on `status=="blocked"`), size-down reflected to local qty/lots, auto-hedge BUY stays a webhook step after. New (approved) stricter: no-premium → skip (TRAP #1). Both compile. **Offline parity test PASS:** override→SL_TYPE:aggressive+TVWH+webhook, no-override→global SL, qty 1×65 & 2×65 correct, blocked→status=blocked+price. **STILL REQUIRED before LIVE webhook cutover:** VPS paper signal-replay (webhook_v1 is PAPER — deploy runs new path in paper first; verify TRAP #108-style parity on real recent signals, then user's explicit go-ahead to flip webhook live). *(commit: pending)*
- [x] Task 5 — loop automation set up — **DONE 2026-07-13 (2 prerequisites remain before scheduling).** Created 3 command defs: `.claude/commands/architecture-audit-loop.md`, `eod-report-review.md`, `hunt-pipeline.md` — each embeds ALL rails: HARD EXCLUSION (never touch `_core/` live-order-path or `nifty_config` active/mode — 5d), idle-aware skip (uncommitted <15min / hunt_guard lock — 5h), budget cap (30/45 min — 5f), `data/loop_activity.log` one-line-per-run (5f), `max_consecutive_failures=3`, no auto-commit, diff-vs-last-run (`data/audit_history/`, 5a), worktree+hunt_guard for parallel (5e), frequency 2x-daily/1d (5g). 5d also baked into CLAUDE.md as Rule 6D (permanent). Seeded `data/audit_history/` baseline (1 FAIL 03_orbst + 1 WARN universe = known-debt) + initialized `data/loop_activity.log`. **PENDING before turning schedules on:** (i) 5b first-run-MANUAL of eod-report-review needs today's EOD report (only exists after `eod_report.py`'s 15:45 IST timer) — run once by hand, eyeball for noise, THEN schedule; (ii) user starts the `/loop 2x-daily /architecture-audit-loop` + `/loop 1d /eod-report-review` (not auto-started — idle-aware would skip mid-work anyway, and market was open). **Discovery note:** commands live in `CODE3B/.claude/commands/` (travel with the repo) → discoverable when Claude runs from inside CODE3B. *(commit: pending)*
- [ ] Task 7 — market-open short-straddle test + IV-rank-gate combine *(exploratory research, after go-live tasks)*
- [ ] Task 8 — OI unwinding-at-key-levels, staged (Stage 1 → 2 → 3, do not skip) *(exploratory research)*
- [ ] Task 9 — log rotation (from 0h): no `RotatingFileHandler` anywhere → logs grow unbounded over weeks. Add rotation to the strategy Popen log files + long-lived daemons. Low priority, not a go-live blocker.
- [ ] Task 10 — `03_orbst_trader.py:170` `_supertrend_dir` is a DUP-INDICATOR (architecture_audit FAIL, pre-existing in the home-laptop code) — move it into `_CHARTING/indicators.py` (INDICATOR_REGISTRY) per Rule 6B, or import from there. Found 2026-07-13 while running the audit for Task 3b. Currently a real FAIL the staged-only pre-commit hook doesn't catch unless 03_orbst is staged.
```
