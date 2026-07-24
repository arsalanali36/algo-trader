# ADR-013 — Market-open price-feed reliability (WebSocket-first) + REST-burst reduction

Status: PROPOSED (plan for review — nothing built yet)
Date: 2026-07-24

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
