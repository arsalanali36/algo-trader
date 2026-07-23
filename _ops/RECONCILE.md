# Broker Reconciliation — how the app stays in sync with Zerodha

**TL;DR:** The app's LIVE position ledger is kept equal to Zerodha's own trade book,
automatically, by **mirroring** the broker (matching by unique order id) — not by
guessing. If you close/open a position directly on Zerodha, the app auto-detects it
within ~2.5 minutes and rings the bell. An independent watchdog double-checks every
2 minutes.

> Decision record: `_ADR/ADR-011-authoritative-broker-mirror-reconcile.md`
> Root-cause history: `LESSONS.md` TRAP #154 (umbrella over #44/#58/#60/#61/#92/#145)

---

## 1. The problem this solves

The webhook exit sometimes doesn't fire (e.g. the TradingView alert is pinned to an
old script version), so you close the trade **manually on Zerodha**. Historically the
app **didn't notice** — the position hung as a "phantom" (open in the app, flat at the
broker). 1-2 trades/day got stuck like this; each needed manual clean-up.

**Why it kept happening:** the old reconciliation *inferred* the truth — it tried to
GUESS which of the app's order rows a broker fill belonged to (by price/qty signature,
by trade-id, by order-id — three different heuristics running at once). Guessing on
ambiguous data is fragile, and multiple guessers with different keys **fight** each
other (one records half a fill, the next can't finish it → residual phantom). Every
misfire got a new patch; the class never closed.

---

## 2. The fix — mirror, don't guess

There is now **one** authoritative reconciler: `_ops/reconcile_broker.py`.

**Anchor fact:** every order the app places stores Zerodha's `order_id`
(`order_store.broker_order_id`), and Zerodha gives every fill a unique
`(order_id, trade_id)`. So matching is exact, never a guess:

- A broker order the app **has a row for** → KNOWN (already recorded).
- A broker order the app has **no row for** → EXTERNAL (a manual entry/close) →
  record it **once**, as **one aggregated row** of the order's total qty
  (netting-safe), attributed to the contract's single open live strategy (else
  `manual`).

Properties:
- **Idempotent** — keyed by `broker_order_id`; running it again does nothing.
- **Netting-safe** — one order = one matched-qty row. (Splitting a close into two
  65-lots against a single SELL 130 leg is exactly what *breaks* `order_store` netting
  and creates a phantom — so we never do that.)
- **LIVE only** — PAPER positions are simulated; they never exist at the broker and are
  never compared to it.
- **Conservative** — it only auto-writes confident external orders. Anything ambiguous
  (a symbol it can't resolve, or a net mismatch it can't explain by an external order =
  an app-side phantom the broker has no record of) is **flagged in the bell, never
  silently written.**

---

## 3. How it runs (operational)

| Piece | What it is | When |
|-------|-----------|------|
| `mirror_if_due()` | The routine. Called every `pos_monitor` tick, own ~2.5 min cooldown, only in the market window (09:15–15:50 IST). Auto-records external orders, rings the bell. | Automatic |
| `invariant_guard` | Independent read-only watchdog: app-net vs broker-net per contract, every 120 s. Alerts on ANY mismatch, auto-resolves when clean. | Automatic |
| 🔄 **Sync from Broker** button | On-demand `force_sync` (the old heuristic path, kept for emergencies). | Manual |
| 🧾 **Reconcile vs Broker** button | On-demand `reconcile_manual_trades`. | Manual |

**What you see:** when the app auto-detects a manual action, a bell notification fires —
e.g. *"🔁 Auto-reconcile: arschain_MAIN me BUY 130 NIFTY-24000-PE @ 162.30 record kiya —
ye Zerodha pe hua tha, ab app broker se match karta hai."* Ambiguous cases fire a
⚠️ notification asking for a manual look.

**The three OLD heuristic auto-scans are disabled** (`sync_if_due`,
`untracked_scan_if_due`, `reconcile_if_due`) — they were the guessers. Don't re-enable
them as auto-scans; the manual buttons still call the same code on demand.

---

## 4. Checking / debugging by hand

```bash
# Is the app currently in sync with the broker? (read-only, writes nothing)
python -X utf8 _ops/reconcile_broker.py --date 2026-07-23
#   → "✅ app LIVE ledger exactly mirrors the broker" OR lists external orders + gaps

# The independent watchdog's view
python -X utf8 _ops/invariant_guard.py
#   → "✅ all invariants hold — app matches reality" OR the exact contract + gap

# Verify on a COPY of a DB backup (never the live DB) that apply() reconstructs the
# correct state — used to prove the reconciler before the live cutover:
cp data/trades.db.bak.<...> data/trades.db.reconcile_test
python -c "import _paths, order_store, pathlib, reconcile_broker as rb; \
  order_store.DB_PATH=pathlib.Path('data/trades.db.reconcile_test'); \
  print(rb.apply('YYYY-MM-DD', dry_run=True))"   # dry-run: shows the plan, writes nothing
rm data/trades.db.reconcile_test
```

`reconcile_broker.plan(date)` = read-only diff (external orders + per-contract
broker-net vs app-net). `reconcile_broker.apply(date, dry_run=True/False)` = the write
path (dry-run by default).

---

## 5. Rules for future changes (read before touching reconciliation)

1. **Never re-introduce fill-signature / net-guess reconciliation.** If a gap appears,
   extend the order_id mirror — do NOT add a second guesser (two writers with different
   keys is the exact bug this retired).
2. **Record an external/manual close as ONE aggregated row matching the open leg's qty.**
   Split fills break `order_store` netting → phantom (raw sum can read 0 while the
   netted view shows a residual).
3. **PAPER is never reconciled against the broker.** It doesn't exist there; it will eat
   a live fill.
4. **Keep `invariant_guard` running.** It is the independent double-check and caught
   every mistake during this build.
5. Multi-strategy-same-contract attribution is intentionally NOT auto-written
   (`_open_live_strategy` returns `None` → `manual`; net still matches). A genuinely
   ambiguous split is flagged, not guessed.

---

## 6. Separate issue — not reconciliation

If the **webhook exit didn't fire**, that's the TradingView side: the alert is pinned
to an OLD saved version of the Pine script. TradingView alerts run a snapshot of the
code from when the alert was created — editing the script does NOT update a running
alert. **Delete and recreate the alert** on the current version. This reconciler makes
the app always *match* reality; it does not make the webhook exit.
