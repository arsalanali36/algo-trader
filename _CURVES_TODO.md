# /curves (Option Curves) — worklist (from Arsalan, 2026-07-24)

Files: `templates/option_curves.html` (frontend), `_ops/option_curves.py` (backend). Display-only page except #6 (quick order = money-path).

| # | Item | Type | Status |
|---|------|------|--------|
| 1 | **Header title** — browser tab says "Option Curves — Algo Trader"; make it **OC-Nifty / OC-BNF** (by underlying) | trivial | ⬜ |
| 2 | **Wrong start time** — 5m data starts at 09:19, market opens 09:15 → should start 09:15 | investigate (backend/collector) | ⬜ |
| 3 | **Full view per panel** — when panels squeeze, add a per-panel expand button → click = that panel full view | moderate FE | ⬜ |
| 4 | **Line inverts on squeeze** — "Theo vs Actual decay" panel: when vertically squeezed the line renders UPSIDE-DOWN; vertical-stretch shows true. Rendering bug | investigate FE (autoscale) | ⬜ |
| 5 | **Zoom resets on refresh** — zoom in to inspect → auto-refresh resets the zoom (refresh should preserve zoom; code CLAIMS fit=false but user sees reset) | investigate FE | ⬜ |
| 6 | **Quick-order button** — add the circular quick-order button (main dashboard) to /curves so orders fire from here | MONEY-PATH — careful | ⬜ |
| 7 | **Notes/auto-notifications persist across days** — yesterday's chart notes/alert-markers not shown today; should show in Today/3d/older views. (Notes localStorage-keyed per date; alerts backend per-day) | moderate (FE+maybe BE) | ⬜ |
| 8 | **Total tax in payoff** — payoff (4-leg) panel should show total tax for the 4 legs | small (payoff.py + FE) | ⬜ |

Notes: refresh path already setData-only (no buildPanes) per html:456; investigate why zoom still resets. Notes key = `oc_notes_<U>_<date>` (html:273) → per-date, so multi-day view can't see them.
