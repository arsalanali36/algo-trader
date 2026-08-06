# 📚 Documentation Plan + Progress Tracker

**Isko sabse pehle padho** agar tum ek Claude session ho jo is repo ki documentation
likh rahi thi aur beech me ruk gayi (session-limit / naya session). Yahan likha hai:
kya banana hai, kaun se order me, aur **abhi tak kya ho chuka**. Wahin se uthao.

Owner: Arsalan. Reason: har session pieces ko baar-baar reverse-engineer karni padti
thi — ye docs isliye hain ki wo dobara na karna pade. Token-conscious: **ek-ek piece,
har ke baad git commit** (backup + doc saath-saath).

---

## System kaise kaam karta hai (2 layers — refactor-proof)

1. **`_DOCS/MODULES.md` — AUTO-GENERATED, kabhi haath se mat likho.**
   `_TOOLS/gen_module_docs.py` har module ka **module-level docstring** + public
   functions/classes (unki 1-line docstrings) nikaal ke folder-wise index banata hai.
   Source of truth = **code ke docstrings** (jo pehle se hain). Refactor/rename/move
   karo → generator dobara chalao → doc khud current. Pre-commit hook har commit pe
   refresh karta hai (staged `.py` badle to).
   - Regenerate manually: `python _TOOLS/gen_module_docs.py`
   - Naya module bana? → uska module-docstring likho (pehli line = 1-line role),
     public functions pe 1-line docstring do → generator baaki khud kar lega.

2. **`_DOCS/ARCHITECTURE.md` — HAND-WRITTEN, par STABLE.**
   Jo docstring-per-file nahi bata sakti: **pieces aapas me kaise judte hain** —
   money/order-path flow, data-flow, process model, layers. Ye sirf asli architecture
   badalne pe update hoti hai (rare). ADRs (`_ADR/`) se cross-link.

**Already maujood docs (inhe dohraana nahi):** `_ADR/ADR-0xx-*.md` (17 decisions),
`LESSONS.md` (recurring traps), `ARCHITECTURE_LOG.md` (changelog), `CLAUDE.md`
(feature index + rules), `strategies/live/NEW_STRATEGY_CHECKLIST.md`,
`scratch/nifty_trend/RESULTS_SCHEMA.md` + `BS_OPTION_SIM.md`, `STOCKMOCK_PIPELINE.md`,
`WORKFLOW.md`. Ye docs unhi ko complement karti hain — overlap mat banao, link karo.

---

## Priority order + STATUS

Status: ☐ = pending · 🚧 = in-progress · ✅ = done

| # | Area | Deliverable | Status |
|---|------|-------------|--------|
| **P0a** | Auto-doc machinery | `_TOOLS/gen_module_docs.py` + generated `_DOCS/MODULES.md` (131 modules) + pre-commit hook wired (auto-refresh) | ✅ |
| **P0b** | Money-path wiring | `_DOCS/ARCHITECTURE.md` → process-model + order/execution flow + monitoring/exit + netting + reconcile + margin gate + data-flow (short) | ✅ |
| **P1** | Data/broker plumbing | ARCHITECTURE.md §7 full — read-path 4-layer (rate_limiter→feed→poller→cache) + resolver layer + broker abstraction; `_data/*` + `brokers/*` docstrings verified (no gaps) | ✅ |
| **P2** | Process model + dashboard | ARCHITECTURE.md §8 — control plane: start/stop→supervisor desired-state (fork-after-warm) + auto_scheduler internal-token + health_check preflight + `trader_dashboard.py` route-map (group-wise, money-path flagged) | ✅ |
| **P3** | Frontend map | `_DOCS/FRONTEND.md` — load-order (script-src = code order) + shared/global (registry/notify/topnav) + app-00..15 ownership table + standalone pages + render pipeline + TRAP #125/#132 | ✅ |
| **P4** | Strategy layer | ARCHITECTURE.md §9 — signals/live folder split (ADR-010) + concrete `orb_trader` lifecycle walk (signal→gate→execute→record→monitor→exit→recovery) + 4-point Rule-8 + honest signal-share boundary | ✅ |
| **P5** | Research/backtest | `_DOCS/BACKTEST.md` — 3-pass pipeline + run_hunt reference producer + build_* family + engine files (bs_option/intraday_engine/option_structures/charges/dom_*/honest_sizing) + runs/<slug>→Lab wiring + deploy gate; RESULTS_SCHEMA/BS_OPTION_SIM linked | ✅ |
| **P6** | Ops/observability | ARCHITECTURE.md §10 — `_ops/*` (52) grouped 6-way: reconcile+integrity · reports · display-pages (Rule 10) · data-lakes/collectors · order-adjacent ops · config/sync/utility | ✅ |

---

## RESUME HERE (last session ne yahan chhoda)

> ✅ **DOCS EFFORT COMPLETE (P0→P6 sab done).** Poora doc-set:
> - `_DOCS/MODULES.md` (auto-gen, pre-commit hook se current) — per-module API index
> - `_DOCS/ARCHITECTURE.md` — §1 process model · §2 order money-path · §3 monitor/exit · §4 netting ·
>   §5 reconcile · §6 margin · §7 data/broker plumbing · §8 control plane · §9 strategy layer · §10 _ops
> - `_DOCS/FRONTEND.md` — static/js map + render pipeline
> - `_DOCS/BACKTEST.md` — research engine (`scratch/nifty_trend`) + 3-pass pipeline
>
> **Maintenance (aage kya):** naya module → module-docstring + public-fn 1-liner (generator baaki karega,
> hook auto-refresh). Asli ARCHITECTURE badle (naya process/layer/order-path) → relevant § update — rare.
> Docstring-per-file jo bata de wo MODULES.md me apne aap; ARCHITECTURE sirf WIRING (kaise judte hain).
> Ye plan-file ab reference; koi pending piece nahi.

### Done-log
- **P6 ✅** — `ARCHITECTURE.md` §10 (`_ops/*` ~52 files): 6 group — 10.1 reconcile+integrity
  (reconcile_broker/csv, invariant_guard) · 10.2 reports (eod_report/digest/signal_replay/daily_report/
  intervention/backtest_live_recon/param_stability/bs_shadow) · 10.3 display-pages Rule-10
  (curves/gex/backtest_lab/whatif/fii/brief/broker_ledger/backtest_calendar) · 10.4 data-lakes
  (option_chain_collector/fii_flow/chain_pcr/downloaders) · 10.5 order-adjacent (auto_straddle/roller/
  triggers/carry/exit_rules/sm_runner — gateway se) · 10.6 config/sync/utility. Commit `61e639b`.
- **P5 ✅** — naya `_DOCS/BACKTEST.md`: 3-pass pipeline (instrument→RMS→BS), run_hunt reference
  producer + build_* family, engine files (intraday_engine/option_structures/bs_option/charges/
  dom_*/ml_*/honest_sizing), runs/<slug>→Lab hub + Stats backtest wiring (backtest_calendar),
  deploy gate (Sharpe≥1 + p<0.05 + min(train,oos)) + Rule 10; RESULTS_SCHEMA/BS_OPTION_SIM linked.
  Commit `f06c9e1`.
- **P4 ✅** — `ARCHITECTURE.md` §9 (strategy layer): 9.1 signals/live folder split (ADR-010 —
  signal ki ekmatra impl `strategies/signals/*`, backtest+live dono call), 9.2 concrete
  `orb_trader` lifecycle walk (candle→`orb.orb_signal_last` shared→execute_signal gate→record→
  pos_monitor exit→`_recover_state_from_order_store`), 9.3 4-point Rule-8 (checklist link) +
  positional `_ALWAYS_OVERNIGHT` + honest signal-share boundary. Commit `ed8032d`.
- **P3 ✅** — naya `_DOCS/FRONTEND.md`: load-order (script-src = code order, TRAP #125 hoisting/
  DOMContentLoaded), shared/global (registry.js single-labeller TRAP #132, notify/topnav/env-badge/
  mobile-tools), app-00..15 ownership table (tab/concern per file), standalone pages, render
  pipeline mental-model, mobile layer (mobile.css + app-15). Commit `48a6628`.
- **P2 ✅** — `ARCHITECTURE.md` §8 (control plane): 8.1 start/stop → supervisor desired-state
  (fork-after-warm, COW, fail-safe legacy Popen) + auto_scheduler internal-token (TRAP #120) +
  get_pid setproctitle; 8.2 health_check preflight (CONFIG→SCRIPT→HEARTBEAT→TOKEN→DATA→CONTRACT);
  8.3 `trader_dashboard.py` route-map ~200 routes GROUP-wise (money-path flagged: sirf process-
  control + orders&positions + webhook chhoote hain, baaki display Rule-10). Commit `4179cb7`.
- **P1 ✅** — `ARCHITECTURE.md` §7 full expand: 7.1 do foundational baatein (account-wide
  Dhan limit + sec_id≠trad_sym identity), 7.2 read-path 4-layer fallback (dhan_rate_limiter →
  dhan_feed → ltp_poller → shared_ltp_cache; + shared_candle_cache; feed-vs-poller kab kaun),
  7.3 resolver layer (dhan_master/universe/fno_universe/opt_hist), 7.4 broker abstraction
  (get_broker factory, Kite-orders/Dhan-data split, structured-field resolve, IPv4-force).
  `_data/*` + `brokers/*` module-docstrings verify kiye — koi gap nahi (P0a ne bhar diye the).
- **P0a ✅** — `_TOOLS/gen_module_docs.py` (ast se docstrings extract, BOM-tolerant,
  `--check` mode), generated `_DOCS/MODULES.md` (131 modules, 10 folders), pre-commit
  hook (`scripts/pre-commit-architecture-audit.sh`) me auto-refresh wired (staged `.py`
  badle to MODULES.md regen + git add) + robust python-detect (Win Store-shim skip),
  `dhan_master.py` ka missing docstring bhara. Commit `284bf87`.
- **P0b ✅** — `_DOCS/ARCHITECTURE.md`: process-model (dashboard/monitor/supervisor/
  optionchain/timers) · order money-path (signal→gate_entry→execute_signal→smart_order→
  order_store) · monitoring/exit (pos_monitor_loop) · netting rules (`_net_rows`) ·
  reconcile (ADR-011) · margin gate (ADR-015) · data-flow (short, P1 me expand).
