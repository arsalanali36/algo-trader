# ADR-009 — Manual-exit robustness (net-aware reconcile + manual-close veto)

**Date:** 2026-07-20
**Status:** Accepted (deployed)

---

## Context — WHY these problems kept coming back

Over a single day (2026-07-20) the user hit three "tamashe" after they manually
closed positions (webhook exit had failed, so they closed by hand — a Kite limit
order for one, an app squareoff for another):

1. **Phantom double-count (LIVE).** They bought 2×65 to close an arschain SHORT
   of 130. Kite went flat. But the app showed **two phantom open longs** that
   didn't exist.
2. **Re-entry after manual close (PAPER).** They manually squared off the VRP
   overnight condor. The strategy **re-created the position**.
3. **A separate but same-family recovery bug earlier the same day** (TRAP #138):
   a restarted webhook rebuilt its position with a **blank option symbol** and
   sent the broker a nameless close order → the short never exited.

These are not three unrelated bugs. They share **one root cause**: the system
was built **intraday-first, single-broker-first, single-path-first**, and those
assumptions break in the *seams* the moment three real-world facts are added —
**(a) a human closes a position by hand, (b) orders actually go to Kite while
Dhan is data-only, (c) a position is held overnight (positional).**

Concretely, four structural weaknesses produced every symptom above:

### 1. Reconciliation matched fills ONE-BY-ONE, not to the broker's net truth
`reconcile_manual_trades` decided "is this Kite fill already recorded?" by an
exact `(symbol, side, qty, price)` **signature**. But a single 130-lot close
fills as **2×65**, and `broker_sync`'s ghost-exit had recorded the close as **one
130-qty row**. 65 ≠ 130 → signature miss → both real fills re-inserted as
"manual" → the **same 130 counted twice** → phantom open longs. The reconciler
was comparing shapes of individual fills instead of asking the only authority
that matters: *"broker, what is your actual net position?"*

### 2. The system had NO memory of human INTENT
Every automated path — a strategy's next entry signal, `pos_monitor`'s
profit-target squareoff — treated a closed position as simply *"position gone,
re-evaluate fresh."* A manual close by the user ("I want OUT of this") looked
**identical** to an SL hit. So the strategy re-entered, and `pos_monitor` even
squared off a paper position the user had **already** closed (creating 4 phantom
opposite legs — the VRP mess). Nothing anywhere said *"the human decided this;
respect it."*

### 3. State is reconstructed in MANY independent places, each able to drift
Four separate recovery functions (`webhook_executor`, `range_trader`,
`01_rsi_v1`, `universe_trader`), the netting engine, the reconciler, and
`pos_monitor` each independently reconstruct/interpret "what is open." When they
disagree, phantoms appear. TRAP #138 was exactly this: three of the four recovery
functions read the option symbol as `sym` (correct); the webhook one read
`trad_sym` (always empty) — a lone drift, invisible until a restart triggered it.

### 4. Intraday / single-day assumptions
`pos_monitor` and day-scoped netting assume a position opens and closes on the
same day. A positional condor's close (today) has no same-day open to net
against (the open was days ago) → it shows as a phantom; and `pos_monitor`,
seeing a still-"open" positional leg, can fire a redundant squareoff.

**Blast-radius note:** every symptom was contained to *display / bookkeeping* —
the user's real Kite position was correctly flat throughout. But a phantom OPEN
position is dangerous: if the user or the system acts on it (sells to "close" a
long that isn't there), a **real** opposite position opens. So these are treated
as correctness bugs, not cosmetic.

---

## Decision

Two principles, both making the **broker + the human** the sources of truth
instead of fill-shapes and automated assumptions:

### D1 — Reconcile to the broker's NET, not fill-by-fill
`broker_sync.reconcile_manual_trades` now records a manual fill **only if it
moves order_store's net for that contract TOWARD the broker's real net**
(`broker.positions()`, signed). Broker flat + book flat ⇒ record nothing. Plus a
**trade-id cross-path dedup**: a fill whose trade-id is already referenced by any
row's `correlation_id` (a strategy ghost-exit records the broker trade-id;
a manual row records `MANUAL_TID_<id>`) is never recorded again.

*Trade-off accepted:* a purely manual round-trip that nets flat within a cycle is
no longer auto-recorded (it changes no net). This is deliberate — a phantom OPEN
position is dangerous; a missed flat round-trip is display-only and still visible
on the broker. The `reconcile_if_due` auto-loop's original goal (capture manual
round-trips for P&L completeness) is downgraded in favor of never creating a
phantom.

### D2 — Respect the human close (manual-close veto)
When the user closes a position — via the app close button **or** an
external/manual broker close that `broker_sync` detects — `risk_gate` records a
day-scoped veto on that `(strategy, symbol)`. `strategy_safety.gate_entry()` (the
single chokepoint every strategy + webhook entry passes via
`execute_signal → gate_entry`) checks it **first** and blocks re-entry.

- A strategy's **own** SL/target exit never marks the veto — only the manual/
  external paths do — so normal re-entry after an automated exit is unaffected.
- Day-scoped, auto-resets next day, and clearable (`clear_manual_veto` — for a
  future "🔓 allow re-entry again" control).

### D3 — Mechanical + test guards against the recovery-drift family (TRAP #138)
- Recovery reads the correct field (`sym`) + falls back to `sec_id` resolution;
  refuses to create a nameless position.
- `smart_order.execute()` — the single order gate — **refuses to place any order
  with a blank symbol** (universal floor, all strategies).
- `architecture_audit` **check #10 (RECOVER-FIELD)** blocks reading `trad_sym`
  off an order_store open/closed row at commit time (repo-wide).

---

## Consequence

**Prevents (going forward):**
- Same fill counted twice → **no more phantom open positions** from reconcile
  (net-aware + trade-id dedup).
- Strategy/webhook re-opening what the user manually closed → **blocked for the
  day** (veto), all strategies + webhook, via one chokepoint.
- Nameless orders reaching the broker → **refused** at the shared order gate.
- The recovery field-drift → **can't be committed** (audit check #10).

**Trade-offs accepted:**
- Manual flat round-trips within a reconcile cycle aren't auto-recorded (D1).
- Veto is coarse per `(strategy, symbol)` per day — a user who *wants* to re-enter
  the same symbol the same day must clear the veto (day-reset or the clear API).
- Veto ENFORCEMENT in a running strategy process only takes effect at its next
  restart (the check lives in `gate_entry`, loaded per process) — so it is fully
  active from the next 9:10 auto-start; the webhook path (in the dashboard
  process) gets it on a dashboard restart. Reconcile (D1) is active immediately
  in `algo-monitor`.

**Verification (replay of the exact incidents, temp DB / temp veto file — real
data never touched):**
- `TEST/test_reconcile_net_aware.py` — incident replay inserts **0 phantom**; a
  genuine untracked manual trade is still recorded + idempotent (7/7).
- `TEST/test_manual_close_veto.py` — mark/check/granularity/clear/persist + real
  `gate_entry` blocks a vetoed re-entry (12/12).
- `TEST/test_wh_recover_symbol.py` — TRAP #138 recovery + blank-symbol guard +
  dedup-retry + audit check (16/16).
- Full `architecture_audit`: 0 FAIL.

**Cleanup of the day's live artifacts (one-time, backed up first):** the two
phantom arschain manual rows and the four phantom VRP `pos_monitor_exit` legs
were deleted after a WAL-safe online backup (`data/trades.db.bak.<ts>`); both
contracts verified flat afterward, matching the real (flat) Kite account. The
VRP strategy's stale on-disk state was cleared so any restart recovers flat.

**Related:** ADR-006 (positional/overnight lane), ADR-007 (strategy identity),
LESSONS.md TRAP #138 (recovery field-drift) + TRAP #139 (this incident family).
