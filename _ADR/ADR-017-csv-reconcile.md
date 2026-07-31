# ADR-017 — Reconcile the ledger from an uploaded Zerodha tradebook CSV

Date: 2026-07-31
Status: Accepted

## Context

The app's LIVE ledger (`order_store`) can drift from the broker's reality when the user
trades manually on Zerodha, or when the live-API reconcile (`reconcile_broker.py`, ADR-011)
hasn't run / can't reach Kite. Until now the only fixes were the automatic Kite-API mirror
(`mirror_if_due`) and the manual "🧾 Reconcile vs Broker" button — both depend on the Kite API
being reachable and correct at that moment. When they didn't work, the user had to escalate
(ask a maintainer to reconcile by hand). The user asked for a self-service path: export
Zerodha's tradebook CSV and upload it → the app reconciles itself, instantly.

## Decision

Add `_ops/reconcile_csv.py` + route `/api/reconcile-csv` + an Orders-page `📤 CSV Reconcile`
button. It treats the uploaded Zerodha tradebook as the authoritative broker truth for the day
and makes each contract's app net match it.

Two design choices that matter:

1. **Symbol source = the CSV's Kite tradingsymbol, parsed locally** (`kite_to_trad_sym`), not
   the Kite instruments API — so it works with zero broker connectivity (the whole point).
   Handles monthly (`NIFTY26AUG24100CE`) and weekly (`NIFTY2680424350CE`) → the app's
   month+year trad_sym (no expiry day, consistent with the rest of the codebase).

2. **Net-based per-contract mirror, NOT fill-id matching.** The CSV's "Trade ID" is the
   Zerodha *trade* id; the app stores the Kite *order* id (`broker_order_id`). They are
   different numbers, so matching CSV rows against app rows by id would mark every CSV fill
   "external" and RE-RECORD it → double-count (the exact failure class that corrupted P&L
   before — TRAP #60/#67/#93/#145). Instead we compare per-contract NET (Σ BUY − Σ SELL) and
   record only the missing DELTA. This is idempotent by construction: once app-net == CSV-net,
   re-uploading the same file is a no-op. Delta legs are attributed to the single open live
   strategy on that contract (`reconcile_broker._open_live_strategy`) else `manual`,
   `source=csv_reconcile`. Preview-first (read-only `plan`) → user confirms → `apply` writes.
   LIVE only; PAPER never touched.

## Consequence

- Self-service reconcile with no broker connectivity — removes the "ask a maintainer" step.
- Idempotent + preview-gated → safe to re-run; can't double-count.
- **Trade-off (accepted):** net-based mirror fixes per-contract NET/positions, but does NOT
  reconstruct the individual entry/exit prices of a round-trip the app missed *entirely*
  (both legs) — for such a case the net is corrected but the completed-trade prices are the
  delta's, not the two real fills. Fill-level reconstruction (matching by signature/time) is a
  possible v2 if per-trade history fidelity for fully-missed round-trips is ever needed.
- Complements ADR-011 (API mirror) rather than replacing it; both write `broker`-mirror rows.
