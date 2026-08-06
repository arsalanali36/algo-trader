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
| **P4** | Strategy layer | ARCHITECTURE.md → ek strategy order kaise fire karti (entry→gate→execute→record→monitor→exit); `strategies/live/*` + `strategies/signals/*` (checklist already hai, link) | ☐ |
| **P5** | Research/backtest | `_DOCS/BACKTEST.md` — `scratch/nifty_trend` key files (run_hunt · bs_option · intraday_engine · option_structures · charges · dom_*); RESULTS_SCHEMA/BS_OPTION_SIM link | ☐ |
| **P6** | Ops/observability | ARCHITECTURE.md → `_ops/*` (52) grouped: reconcile · reports (eod_report/digest/signal_replay/daily_report) · display-pages (option_curves/gex_profile/backtest_lab/whatif) · data-lakes | ☐ |

---

## RESUME HERE (last session ne yahan chhoda)

> **Next:** P4 — Strategy layer. `ARCHITECTURE.md` me ek section: ek strategy order kaise fire
> karti — entry→gate→execute→record→monitor→exit ka concrete walk (§2/§3 already generic hai;
> yahan `strategies/live/*` + `strategies/signals/*` ki wiring: signal-single-source ADR-010, kaun
> sa live trader kaun se signal fn ko call karta, ATR-stop/sizing sirf trader me). NEW_STRATEGY_
> CHECKLIST already hai — link karo, dohraana nahi. `strategies/live/` + `strategies/signals/`
> list karke, ek representative trader (jaise orb_trader → orb.orb_signal_last) ka flow trace karo.
>
> (Har piece done hone pe is section ko update karo + upar table me status badlo +
> git commit karo. Taaki agli session sirf yahan padh ke continue kar le.)

### Done-log
- **P3 ✅** — naya `_DOCS/FRONTEND.md`: load-order (script-src = code order, TRAP #125 hoisting/
  DOMContentLoaded), shared/global (registry.js single-labeller TRAP #132, notify/topnav/env-badge/
  mobile-tools), app-00..15 ownership table (tab/concern per file), standalone pages, render
  pipeline mental-model, mobile layer (mobile.css + app-15). Commit `<next>`.
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
