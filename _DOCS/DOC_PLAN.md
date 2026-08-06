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
| **P0b** | Money-path wiring | `_DOCS/ARCHITECTURE.md` → `_core` order/execution flow (order_store · execution_gateway · smart_order · risk_gate · strategy_safety · webhook_executor · broker_sync · reconcile_broker · payoff · market_calendar) | ☐ |
| **P1** | Data/broker plumbing | ARCHITECTURE.md data-flow section — `_data/*` (dhan_master · dhan_feed · ltp_poller · shared_ltp_cache · dhan_rate_limiter · kite_rate_limiter · shared_candle_cache · universe · fno_universe) + `brokers/*` | ☐ |
| **P2** | Process model + dashboard | ARCHITECTURE.md → 3-process model (dashboard / monitor_daemon / supervisor-forked strategies) + `trader_dashboard.py` route-map + health_check + strategy_supervisor | ☐ |
| **P3** | Frontend map | `_DOCS/FRONTEND.md` — `static/js/app-00..15` + registry.js: kaun si file kya owns, load-order, render pipeline, templates | ☐ |
| **P4** | Strategy layer | ARCHITECTURE.md → ek strategy order kaise fire karti (entry→gate→execute→record→monitor→exit); `strategies/live/*` + `strategies/signals/*` (checklist already hai, link) | ☐ |
| **P5** | Research/backtest | `_DOCS/BACKTEST.md` — `scratch/nifty_trend` key files (run_hunt · bs_option · intraday_engine · option_structures · charges · dom_*); RESULTS_SCHEMA/BS_OPTION_SIM link | ☐ |
| **P6** | Ops/observability | ARCHITECTURE.md → `_ops/*` (52) grouped: reconcile · reports (eod_report/digest/signal_replay/daily_report) · display-pages (option_curves/gex_profile/backtest_lab/whatif) · data-lakes | ☐ |

---

## RESUME HERE (last session ne yahan chhoda)

> **Next:** P0b — `_DOCS/ARCHITECTURE.md` likho, money-path flow section:
> signal → `strategy_safety.gate_entry` → `execution_gateway.execute_signal` →
> `smart_order.execute` → `order_store.record` → `pos_monitor_loop` (SL/EOD) →
> `broker_sync`/`reconcile_broker` (netting + broker mirror) → payoff/margin gate.
> `_core/MODULES.md` ki summaries se utha ke wiring likho (docstrings me pieces hain,
> unke BEECH ke taar ARCHITECTURE.md me).
>
> (Har piece done hone pe is section ko update karo + upar table me status badlo +
> git commit karo. Taaki agli session sirf yahan padh ke continue kar le.)

### Done-log
- **P0a ✅** — `_TOOLS/gen_module_docs.py` (ast se docstrings extract, BOM-tolerant,
  `--check` mode), generated `_DOCS/MODULES.md` (131 modules, 10 folders), pre-commit
  hook (`scripts/pre-commit-architecture-audit.sh`) me auto-refresh wired (staged `.py`
  badle to MODULES.md regen + git add), `dhan_master.py` ka missing docstring bhara.
