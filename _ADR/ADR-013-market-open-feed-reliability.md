# ADR-013 — Market-open price-feed reliability (WebSocket-first) + REST-burst reduction

Status: PROPOSED — **original premise REFUTED by measurement 2026-07-24; see MEASUREMENT UPDATE below. Nothing built. RESUME here later.**
Date: 2026-07-24

---

## ⚠️ MEASUREMENT UPDATE (2026-07-24) — read THIS first; the Context/Decision below was written on a WRONG premise

We suspected "market-open saturates Dhan's ~1/sec REST limit". **Measured — it's false.** Then chased 2-3 more wrong theories before grounding it. Real findings:

**What the data actually shows (VPS logs + live probe):**
- **Strategies are NOT REST-rate-limited.** Total REST `DH-904` across ALL strategies TODAY ≈ **0**. (My "range_v1 hammers candles" claim was WRONG — range_v1 trades NIFTY only, 0 DH-904; its log's 1866 "429" were the dhan_feed WebSocket lines, since range_v1 is the feed leader pid.)
- **All 234 journal 429s are the WebSocket handshake being rejected** — concentrated at **startup 09:07-09:10** + a burst at 09:30. NOT strategy REST calls. So the WS 429 is NOT from REST congestion.
- **The poller updates each open leg only every ~5-15s** (live probe: 14 open legs all in the "5-15s" band, NONE "<5s"), *despite* 0 rate-limiting. So the internal rate-gate is yielding the poller's `"ltp"` slot to other traffic (candle/margin) even without 429s — the poller runs slower than its 1.5s target.

**Real root of the "BANKNIFTY-56200-PE 145s stale — SL/target PAUSED" alert:**
- `pos_monitor_loop`'s exit-price fetch (`trader_dashboard.py:~7448`) order is: **`dhan_feed.get_quote` (WebSocket) → `_rest_ltp_fallback` (which is cache-FIRST: `shared_ltp_cache` fresh ≤~5s → direct REST → `get_stale` ≤15s)**. (Correction: I twice mis-stated this — the exit DOES try WS first, and the REST fallback IS cache-first.)
- **The WebSocket is DOWN** (429) → the primary, real-time source is gone → everything leans on the poller-cache + REST.
- The poller updates legs at **5-15s = right at the 15s stale-cache fallback edge**. When WS-down + a poller slip past 15s + a momentary direct-REST fail all coincide → the leg goes **blind** → alert fires after `_STALE_ALERT_SECS=90` (`trader_dashboard.py:~7481`).
- So **Problem 1 (WS won't connect) and Problem 2 (leg stale) are ONE connected issue**, not two — WS-down removes the primary source, and the 5-15s poller cadence is too close to the 15s threshold to safely backstop it.

**Candidate fixes (with trade-offs) — NOT decided, needs Arsalan's explicit OK (money-path):**
- **C (safest, immediate):** widen `_rest_ltp_fallback`'s stale-cache last resort (`get_stale(max_age=15.0)` at `trader_dashboard.py:~7127`) to ~30s. The poller HAS the price (5-15s old); pos_monitor rejects it at 15s. Using a ≤30s price = SL fires slightly late vs today's *blind (no fire at all)* → **strictly better**; straddle isn't tick-sensitive. One-line, low blast-radius. **Needs user OK — it changes exit behaviour.**
- **B (deeper):** give the poller `"ltp"` priority over candle/margin so it updates <5s → cache always fresh. Now **data-justified** (poller measured at 5-15s). Blast radius = shared rate-limiter (affects all).
- **A (root):** make `dhan_feed` WebSocket reliably connect + stay connected. Hardest (WS fragile history TRAP #11/#87/#88/#89). The 09:07-09:10 429 storm ≠ REST congestion → likely a **startup leader-race** (many procs connecting) or a **stale/uncleanly-closed connection** Dhan still holds. Needs its own diagnosis.

**RESUME PLAN:** (1) get Arsalan's OK on **C** and ship it (immediate safety) → (2) diagnose WHY the WS won't connect (leader-race vs stale-connection) for A → (3) reconsider B only if still needed. **Everything below this line was the pre-measurement plan — treat as superseded.**

**Meta-lesson (logged):** measure-before-build saved us from building the wrong thing; I jumped to a conclusion 3× in this thread — verify every claim against data. See [[feedback_technical_peer_not_servant]].

---

## Context

Dhan enforces an account-wide REST limit of **~1 request/sec**. At market open
(~09:15–09:30) that budget is saturated by everything firing at once: 12 strategy
processes (candle scans for signals, margin calcs, LTP), the batched `ltp_poller`,
the dashboard, and the `dhan_feed` WebSocket's own (re)connect handshake.

Grounded in the 2026-07-24 incident:
- The **poller** uses `"ltp"` priority. Only `"order"` has a reserved slot; the
  poller does not. Under the open burst it kept losing the slot (`if not
  _rl.acquire("ltp"): return` → skip this cycle), falling ~145s behind → a
  straddle leg's cached price went stale → the auto-straddle basket-exit **froze**
  (safe, but SL/target enforcement paused). Alert: "BANKNIFTY-56200-PE live feed
  145s stale — SL/target enforcement PAUSED".
- The **WebSocket feed** dropped ("no close frame received") and then couldn't
  reconnect — the handshake got **HTTP 429** repeatedly (backoff 2→4→8→16→30s).
  Straddle legs had zero feed data. It was down at exactly the moment it was needed.

**Key realisation:** the poller AND the WebSocket-reconnect fail *at the same time,
for the same reason* — both compete for the same saturated ~1/sec REST budget at
open. This is a **capacity** problem at open, not merely a price-source choice.

Why the obvious single knobs are insufficient (all analysed, all rejected):
- **Reserve a poller slot** — removes the poller's "skip when busy" backpressure
  (→ more 429s for everyone) and squeezes candle-fetches (→ delayed entry signals);
  doesn't add capacity, just reshuffles the pain.
- **Loosen the staleness tolerance** — = acting on genuinely old prices during a
  fast move. Unsafe.
- **On-demand direct fetch on every stale read** — if every strategy does it, the
  same congestion is amplified (the exact anti-pattern the batched poller exists to
  prevent — TRAP #2).

## Decision

Two-pronged. Neither is a mid-day patch — build + test + **pre-market deploy**.

**A) Make `dhan_feed` (WebSocket) reliable → prices come off the REST limit.**
Once a WebSocket is *connected* it streams every subscribed instrument without
consuming the ~1/sec REST budget (persistent connection, different channel). The
gap is reliability, not concept.
1. **Connect at 09:10 pre-market startup** — before the open burst — so it's
   already streaming when congestion hits (no reconnect-during-429).
2. **Stay connected all day**; fast drop-detect + keep-alive; reconnect with a
   small reserved/jittered path so a reconnect never joins the 429 storm.
3. **Feed-first price reads**: the auto-straddle basket-exit (and `pos_monitor`
   LTP) read `dhan_feed.get_quote(sec_id, max_age=…)` FIRST → fall back to the
   poller cache → fall back to a direct REST fetch. The **freeze-on-stale backstop
   stays** as the final floor (never fire on genuinely stale data).

**B) Reduce the market-open REST burst → the root, so even reconnects/poller aren't starved.**
1. **Enforce `shared_candle_cache`** — N strategies must not each fetch the same
   NIFTY/BANKNIFTY candles; one fetch, all share.
2. **Stagger/jitter strategy loop timings** so 12 strategies don't all hit the same
   second at a bar close.
3. **Measure** the open call-rate before/after (proof, not assumption).

## Consequence (trade-offs accepted)

- Feed-first adds a dependency on `dhan_feed`, which is historically fragile
  (TRAP #11/#87/#88/#89). **Mitigated**: poller + direct fallbacks + freeze-on-stale
  all remain, so the worst case is exactly today's behaviour — never worse.
- Shared candle cache: a strategy may read a candle another strategy fetched for
  the same bar — same data, fine.
- Staggering shifts a loop by a few hundred ms — negligible for signals.
- **No order-path change.** RMS / exit semantics unchanged; freeze-on-stale (the
  safety floor) preserved.

## Pre-mortem (failure shapes — CLAUDE.md table)

- **#11/#12 built≠wired≠verified** (the exact `dhan_feed` trap): must verify the
  feed *actually connects AND streams ticks* in the live monitor — for the real
  straddle legs, during market hours — not just "lease held". Today's check showed
  lease-held-but-no-data. **Test = the real thing at the real time.**
- **#6/#2 shared resource**: verify the connected feed genuinely stays OFF the REST
  limit, and that reconnect logic can't *add* to a 429 storm.
- **#1 stale-state action**: feed-first must not serve a stale WS tick as fresh —
  `get_quote(max_age=…)`; freeze-on-stale backstop unchanged.
- **#7 deploy drift**: pre-market deploy, md5 verify, confirm 09:10 connection live.
- **Rollback**: feed-first behind a kill-switch flag → flip off = today's
  poller-only behaviour instantly.

## Rollout

- Build in a worktree; offline sim + `_DEV/tests`; **deploy before 09:10** next
  trading day so the fresh connection + open behaviour are observed live.
- Ship feed-first behind a flag (default OFF until the live 09:10 connection +
  tick-stream is verified for real legs, then ON).

## Explicitly NOT doing

- Reserve a poller slot (backpressure loss + candle squeeze — rejected).
- On-demand direct fetch on every stale read (congestion amplification — rejected).
- Loosen staleness tolerance (acting on old prices — unsafe — rejected).

Related: LESSONS #115/#158 (poller/feed staleness), #11/#87/#88/#89 (dhan_feed
history), TRAP #2 (shared rate-limit), ADR-012 (auto-straddle, the first consumer).
Memory: `project_code3b_vrp_no_spot`, `project_code3b_auto_straddle`.
