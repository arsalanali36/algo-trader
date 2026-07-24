# /curves (Option Curves) — worklist (from Arsalan, 2026-07-24)

Files: `templates/option_curves.html` (frontend), `_ops/option_curves.py` (backend). Display-only page except #6 (quick order = money-path).

| # | Item | Type | Status |
|---|------|------|--------|
| 1 | **Header title** — OC-Nifty / OC-BNF | trivial | ✅ done (533d4f9) |
| 2 | **Wrong start time 09:19** — FIXED: resample()+ohlcFrom() now label bar at bucket-START (09:15), not last snapshot (09:19). Data was always there (collector 09:15). | done | ✅ done |
| 3 | **Full view per panel** — ⛶ button per pane → solo full-height, ⛶ exit back. `_solo`/`toggleSolo`. | done | ✅ done |
| 4 | **Line inverts on squeeze** — "Theo vs Actual decay": vertically-squeezed line renders UPSIDE-DOWN. Rendering bug. **NEEDS visual repro — can't fix blind.** | needs eyes | ⬜ |
| 5 | **Zoom resets on refresh** — #2's stable bucket-start times MAY fix it (last-bar time no longer shifts each refresh). **VERIFY after #2 deploy; if persists tell me time-zoom vs price-zoom.** | verify (maybe fixed by #2) | 🟡 verify |
| 6 | **Quick-order button on /curves** — NOT a simple "move": app-09-quick-order.js = 963 lines, depends on ~20 qo-* DOM els + watchlist wl-* + ord-open + registry/notify + order APIs. Embedding on the standalone /curves page = copy full panel HTML + wire deps + TEST real order-fire. MONEY-PATH. **Needs a dedicated fresh pass — not marathon-tail.** | MONEY-PATH, real job | ⬜ |
| 7 | **Notes/auto-notifications persist across days** — DONE: mergedNotes() unions each visible day's notes + refresh() fetches each day's /api/option-alerts in 3d/5d/10d view. | done | ✅ done (9b66369) |
| 8 | **Total tax in payoff** — DONE: payoff.structure_tax() sums charges.option_charges() over legs; 6th "Total Tax" card. Verified ₹359 on live 4-leg straddle. | done | ✅ done (e241b10) |

Notes: refresh path already setData-only (no buildPanes) per html:456; investigate why zoom still resets. Notes key = `oc_notes_<U>_<date>` (html:273) → per-date, so multi-day view can't see them.
