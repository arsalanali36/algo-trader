# ARCHITECTURE LOG — CODE3B (Algo Trader)

> Rule: Claude har kaam se PEHLE yahan entry likhega.
> Status values: DONE | IN-PROGRESS | PENDING | CANCELLED
>
> Architecture layers:
> - **broker** — Dhan/Kite API, orders, feed, candles
> - **strategy** — signal logic, Pine→Python conversion
> - **execution** — smart_order, marketable-limit, paper/live
> - **universe** — Nifty-50 scanner, sec_id routing
> - **validation** — TV vs engine match score
> - **ui** — Flask dashboard, tabs, widgets
> - **config** — nifty_config.json, variation management
> - **infra** — VPS deploy, systemd, git/GitHub

---

## 2026-07-14 — Task 5 ML hunt (Fib + premium-divergence + gap, user-seeded)
**Status:** DONE (research-only, nothing deployed — no candidate cleared the DSR gate, by design). Commit + push after.
**Kya:** 3-part user-seeded hypothesis family. **5a** — `ml_features.py` v3: first-candle fib ladder + confirm-candle continuation/reversal + premium-divergence 6-class + OI-buildup 4-quadrant + expiry_regime flag. Data-reality adaptation documented: option lake is 5-min (no 1-min option data), so premium-div measured on the 09:15 option bar's own open→close (=candles 1-5, same contract) + OI on 09:15→09:20 snapshots; spot-side fib IS true 1-min; pre-open auction gap absent from all sources (documented). **5b** — `ml_grid5.py` exhaustive grid (25,200 evals): 58/60 top = short_straddle, best evo 1.75/val 2.45, ALL fail DSR. **5c** — `ml_gp_seed5.py` bounded GP seed: given the full IV/ATR vocab, GP dropped the fib structure and re-converged on the EXACT existing VRP overnight zone (ce_iv>21+ATR-high+OI-flat, val 2.24, DSR 0.017).
**Layer:** validation (backtest research pipeline — no live-path touch)
**Files:** scratch/nifty_trend/ml_features.py (v3), ml_grid5.py (NEW), ml_gp_seed5.py (NEW), ML_MINING_TASK5.md (NEW spec+DONE LOG), ML_MINING_TASKLIST.md, ml_features_v3.csv.gz + reports (gitignored/scratch)
**Kyun:** user wanted a NARROW, seeded hunt (fib/premium/gap) distinct from the generic 39-signal mining that's on the DO-NOT-REDO list. Ran it with the SAME evolution/validation/lockbox/DSR discipline. Verdict: same family as Task 3's VRP short-vol zone, not a new edge — a weaker intraday proxy for the same high-IV signal; unprovable at N=4.36M. Confirms NIFTY short-vol saturation (machine keeps rediscovering the one VRP basin from every seed). Cost model (charges.py) + expiry_regime were already live so this hunt was on the corrected numbers from bar one.
**Depends on:** ml_features v3, charges.py (both this session)

## 2026-07-14 — Cost/Calendar refresh (Budget-2026 STT + Tue-expiry regime) before next hunt
**Follow-up (same day) — Hub real-cost sync (Task 6d go-ahead resolved):** full re-optimize re-run NAHI kiya (screen/optimize dobara chalane se design/params drift → deployed live configs se mismatch = Rule-10 problem; aur verdicts pehle se confirmed the). Instead: `stt_recost.py --apply` har run ke `runs/index.json` + `meta.json` + `results.js` meta me **labeled `real_cost` block** likhta hai (DOM slip + date-aware STT, recorded combos/trades untouched = provenance safe); `hub.html` ab real-cost Sharpe ko PRIMARY dikhata hai ("2.32 rec 2.37" style, sort bhi isi pe) aur `dashboard_intraday.html` har run-page pe 💰 REAL-COST banner (14 run-pages template se refresh kiye — wo build-time COPIES hain). Browser-verified dono. Full re-optimize-under-real-costs re-hunt = optional future job (overnight), abhi zaroori nahi.
**Status:** DONE (local commit; VPS git-pull post-market — index.html tax-display + spec-builder rates need it, backtest-side files VPS pe waise bhi hunt ke waqt hi chalte hain. **RESULT:** STT re-cost on all 14 runs = ZERO gate crossings, delta ₹62–₹455 full-period vs DOM-slip ₹6.8k–₹66.6k — Oct-24 regime option-BUYERS ke liye ~cost-neutral (txn cut ≈ STT hike offset), asli hike sirf post-Apr-26 trades pe (~+₹4-5/lot-trade BUY). Report: `scratch/nifty_trend/STT_RECOST_2026-07-14.md` + SLIP_RECOST correction (04-P OOS "0.985" was the 2× column — real = 1.024, 🔴 flag galat tha). Part B: expiry logic pehle se calendar-aware confirmed (expiry_calendar.py, KNOWN_ISSUES #1 fix) — sirf `expiry_regime` flag ml_features v3 me add hua; v3 CSV build lake-machine (VPS) pe hoga Task 5a ke saath. Part C BLOCKED: Task-5 spec file missing.)
**Kya:** (A) charges model date-aware banao — options STT 0.0625%→0.10% (2024-10-01)→0.15% (2026-04-01), NSE txn 0.053%→0.03503%→0.03553%, futures STT 0.0125%→0.02%→0.05%; single source `scratch/nifty_trend/charges.py`, `bs_option.calc_charges(when=)` delegate, saare engines entry-ts pass karein; live dashboard calcCharges current rates pe. Phir 14 runs ka STT re-cost (dom_recost pattern, DOM-delta vs STT-delta alag report). (B) expiry logic verify (already calendar-aware via expiry_calendar.py — confirmed, koi hardcoded weekday nahi) + `expiry_regime` flag ml_features v3 me. (C) Task 5 hunt — BLOCKED: spec file `ml-mining-task5-fib-premium-gap.md` kahin exist nahi karta (repo/Desktop dono checked).
**Layer:** validation / data (backtest cost model) + ui (tax display constants)
**Files:** scratch/nifty_trend/charges.py (NEW), bs_option.py, ml_gp_precompute.py, option_structures.py, real_struct.py, real_struct2.py, real_calendar.py, positional_vol.py, gamma_scalp.py, delta_neutral_fly.py, oi_signals.py, pivot_nextday_real.py, vps_pivot_real.py, ml_features.py, templates/index.html, stt_recost.py (NEW), STT_RECOST_2026-07-14.md (NEW)
**Kyun:** Budget 2026 (effective 2026-04-01) ne STT phir badhaya — verified live against zerodha.com/charges (opt STT sell 0.15%, fut 0.05%, txn 0.03553%). Code abhi bhi PRE-Oct-2024 rates (0.0625%/0.053%) pe tha — poora 4.5yr backtest ~2.4x under-priced STT ke saath. Naya hunt (Task 5) stale cost model pe optimize na kare isliye pehle ye. NOTE: ₹40/order (SEBI 50% cash-collateral rule) ka claim Zerodha charges page pe NAHI hai — flat ₹20 hi listed hai; flagged, ₹20 assume kiya.
**Depends on:** nothing

## 2026-07-14 — Tasks 80/78/76/81 (Peak summary running+tax, dhan-tag Q, YT Presentations page, per-strategy Default SL override)
**Status:** DONE (commit `724c919`, VPS git-pull + algo-dashboard restart mid-day — strategy PIDs before==after, routes 302-gated OK. algo-monitor/strategies purana code chalate rahenge aaj (Default SL globally OFF hai to no-op) — per-strategy SL override kal 9:10 auto-start se effective. VPS pe `scratch/nifty_trend/run_hunt.py` me UNCOMMITTED ml_gate work mila (ML-mining session ka) — preserve kiya, commit hona baaki.)
**Kya:** (80) Today's Peak Summary view me open positions ka LIVE running P&L column + completed-trades Tax column; (78) paper trades pe `dhan` broker-tag investigation (answer-only — 13-July pre-deploy-sync rows, aaj se kite); (76) nav Reports → dropdown (📋 EOD Reports + naya 🎬 YT Presentations date-wise page, `data/presentations/`); (81) Per-Trade Default SL & Target ka Enabled + Mode ab Per-Strategy Override table me (blank=global inherit, per-strategy ON/OFF + legacy/dropdown/aggressive)
**Layer:** ui + config (risk_gate config-resolution — firing logic untouched)
**Files:** templates/index.html, trader_dashboard.py (presentations routes), _core/risk_gate.py (`default_sl_profile(strategy=)`, `default_target_sl_config` feature_on per-strategy aware)
**Kyun:** (80) open trade ka running/tax summary me nahi dikhta tha; (78) user confusion — paper orders kite pe jane chahiye; (76) roz ka YT-presentation workflow ab app me date-wise archive; (81) global Default-SL har strategy pe lag ke backtest-fidelity todta hai (Rule 10) — per-strategy enable/mode control
**Depends on:** nothing

**Follow-up 3 (same day):** RMS Live Summary **table Per-Strategy Override me MERGE** (user request) — override table ab har strategy ka control-center: `Strategy | Run Controls | Status | Capital Used | Default SL/Target (Enabled/Mode/⚙) | ⊞Advanced(...)`. Run/Status/Capital-Used = LIVE cells (`.rms-run/.rms-gate/.rms-cap[data-rms]`) jo `_rmsPatchOverrideCells()` PATCH karta hai — table re-render nahi hota, unsaved input edits safe. X-marked columns RETIRED: Capital Cap / Open Pos / Unrealized P&L / Max-Loss Used (+ TOTAL row). Day Cap group + Per-Trade SL ₹ bhi ⊞Advanced me gaye (visible default = Run/Status/Capital/DefaultSL). Webhook strategies (cfg.webhooks) ab stratIds union me — unki Run Controls + override row bhi isi table me. RMS Live Summary section me sirf broker-balance strip + webhook trades/day + KILL-ALL/per-instrument floors bache (`_renderRmsExtras()`).

**Follow-up 2 (same day):** UI cleanup round — (1) ⚙ button clip fix (Mode cell flex + select max-width); (2) **Excel-style collapsible "Advanced" column group** (Capital ₹/Margin Mult./Mode/Shadow-Live/Auto-Hedge → `psadv` class + `#ps-ovr-table.advc` CSS + toggle button, localStorage `ps_adv_collapsed`, default COLLAPSED); (3) **global "Per-Trade Default SL & Target" card RETIRED** from Max Loss Protection (rows `display:none`, inputs DOM me preserved kyunki load/save unhe by-id padhte hain — global values inherit-fallback ke roop me zinda); **Aggressive graph block ⚙ modal me on-demand MOVE hota hai** (`tsl-preview-block` appendChild — ek hi node, id conflict impossible; `renderTslPreview(cfgOverride)` param + `_dslvGraph()` effective-values live preview, close pe block apne hidden home me wapas). Per-strategy Enabled/Mode dropdowns ke "global default" labels ab dynamic — current global state dikhate hain.

**Follow-up (same day):** user request — Mode ke saath uski **VALUES bhi per-strategy** ("do jagah ka kaam ek jagah"): Per-Strategy Override ke Mode ke bagal ⚙ button → modal (mode-specific fields: Legacy Target/SL ₹-lot; Dropdown SL/TP type+value+candle-close; Aggressive ke 7 per-lot fields), values `per_strategy[<sid>]` me (blank=global inherit), `window._dslVals` → saveRiskConfig merge. Backend: `default_instrument_sl_tags` legacy/dropdown/TP branches ab ps-values first (pair kabhi mix nahi — type+value ek hi source se), `default_target_sl_config(strategy=)` ps-overlay, monitor per-position per-strategy cfg (`_tslc_for` cycle-cache), `_reconstruct_sl_series`/`_effective_sl_now` strategy pass. 10-check offline logic test PASS.

---

## 2026-07-13 — Today's Peak: clickable per-strategy MTM + Margin Utilization view (tasks 73/74)
**Status:** DONE (commit `2e5fd16`, VPS-deployed + verified, both services restarted clean, market closed / 0 strategy PIDs).
**Layer:** ui + infra (display/observability only — NO entry/exit/risk decision changed)
**Kya:**
- **73 — clickable summary + per-strategy MTM.** "Today's Peak P&L" card renamed **Today's Peak**, ab 3-view toggle (`togglePeakView`): Graph | Summary | Margin.
  - `pos_monitor_loop` snapshot me naya **v[4] = {key:[realized,unrealized]}** + `"__all"` (whole-account). Keyed same as Summary rows (strategy key, MANUAL/WEBHOOK by source). **Display-only + PAPER-incl**; kill-floor ka live-only `_realized` untouched (Critical Rule 6). ZERO extra Dhan calls (already-fetched `data` + existing LTP loop). Inside the existing `try…except _trail_e` so blast-radius unchanged.
  - `/api/peak-pnl-history?strat=<key>` → aligned `strat_series` ({key:{r,u}}) for `__all` + requested strategy only (small payload). Pre-v4 archived dates → `{}` → frontend reconstructs a realised step-curve from that day's completed trades.
  - Graph view: **Realised / Unrealised / Current** switch (`setPeakPMode`). `__all` Current = existing MTM line (no regression); real/unreal from snapshot (today) or reconstruct (past). Floor/target/entry-markers shown only on `__all`.
  - Summary rows clickable (`peakPickStrat`) → Completed Trades **client-side** filter (`_peakTradeMatch`, Summary table stays full) + graph switches to that strategy. Faint P&L magnitude bar **removed** → **Run-Up / Run-Down** ₹ column (`_tradeRunAmts` from MAX_LTP/MIN_LTP tags). Registry **code prefix** (`regId`) before each name. Clearable filter chip in Completed Trades header (`peakClearStrat`).
- **74 — Margin Utilization view.** New **`/api/margin-history?date=&mode=all|paper|live`** — reconstructs day margin timeline from `order_store` entry/exit times (event-driven step): SELL = executing-broker real margin (`risk_gate._leg_capital`, cached), BUY = premium notional. Stacked buy+sell area (`loadMarginGraph`), **All/Paper/Real** toggle (`setMarginMode`). Money-loop untouched (read-only).
**Files:** `trader_dashboard.py` (pos_monitor v[4], peak route strat_series, new margin route), `templates/index.html` (3-view card, R/U/C + margin switches, clickable summary, run-up/down + code prefix, filter chip).
**Verify:** audit 0 FAIL; py ast + JS parse clean; local smoke (2026-07-09 data) per-strategy realized + `_leg_capital` OK; VPS snapshot writing v[4] (`__all` `[8398.65,0]` == Gross TOTAL 8,399); new routes 401 (registered/gated); algo-monitor 0 errors.
**Data note:** graph per-strategy series = GROSS realized (`pnl`), same basis as the existing account MTM line; Summary table shows Net (gross − charges) — consistent with prior behavior, different metric by design.

---

## 2026-07-13 — Position-page organise (tasks 66-72) + backspread net-structure trailing lock + Critical Rule 10 (backtest-fidelity)
**Status:** DONE (branch `feat/position-page-organise`, commit `104c46a`) — committed, NOT merged/deployed (user review-first).
**Layer:** ui + strategy + config
**Kya:**
- **66** — `#ord-summary` tiles retire → 1 consolidated table (`renderStratSummaryTable`) inside Peak P&L card + `[Graph|Summary]` header toggle (`togglePeakView`, localStorage), P&L magnitude bar per row.
- **68** — collapsed open-position group summary shows live ₹/points/% (`_patchLtpCells` querySelectorAll on `.grp-tot-*`).
- **70** — `regLabel()` drops `NN.MM ` number prefix → clean registry names.
- **71** — `STRADDLE/DVERT/BSPRD/SVOL/VRP` `_TP/_SL/_ROLLBACK/_TRAIL` exit reasons added to `order_store._EXIT_REASON_PREFIXES` + `_exitReasonBadge` (were tagged, never surfaced — Rule 9 gap).
- **72** — "Capital se Block hui Entries" → collapsible `<details>`.
- **67** — net-structure trailing profit lock in `05_backspread_trader.py` (reuses `risk_gate.advance_trailing_lock`; `trail_*` config; `pos["trail"]` persisted; ships DISABLED). Backspread had NO trailing → 8000+ rode back down.
- **69 (audit)** — live VPS per-strategy caps already set (verified read-only ssh); real paper-corruptor = aggressive Default-TSL globally ON. User chose disable (`default_sl_enabled/default_tsl_enabled=false`), PENDING apply.
- **Rule 10** — backtest-fidelity: any live tweak not in the backtest invalidates the validated number → STOP + tell user first, offer re-backtest vs leave-off, ship disabled if unsure.
**Files:** `templates/index.html`, `_core/order_store.py`, `strategies/live/05_backspread_trader.py`, `CLAUDE.md` (Rule 10 + Update Log), `ARCHITECTURE_LOG.md`.
**Kyun:** user request — position page too many tiles → consolidate; missing exit reasons; backspread gave back a big profit with no trailing protection; and a hard rule so future tweaks don't silently break backtest fidelity.
**Verify:** JS `node --check` on new regions, py `ast.parse`, architecture audit 0 FAIL, backspread trail 5-scenario state-machine sim. Not run in live dashboard.
**Depends on:** nothing (branch off master `2a62391`).

---

## 2026-07-13 — VPS ab ek git repo hai — local↔VPS sync git-based (scp retired), root-cause of the drift fixed
**Status:** DONE — VPS git repo live, bidirectional push/pull tested (local↔GitHub↔VPS all at same HEAD). Deploy-key add: user-done.
**Layer:** infra
**Kya / Kyun:** Aaj ke drift episode ki ASLI jad = do copies (local + VPS) bina kisi sync-system ke, VPS git repo tha hi nahi (scp-deployed), isliye VPS pe hua kaam (VRP) local me kabhi nahi aata tha aur silent-lose ho sakta tha. Permanent fix: VPS ko proper git repo banaya, GitHub `arsalanali36/algo-trader` = single source of truth dono side.
- **Safety-first setup:** pehle confirm kiya sab live/runtime files gitignored hain (`nifty_config.json`/`data/config.json`/`data/auth.json`/all `*.db` — bulletproof `*.db`/`*.sqlite` catch-all add kiya + empty `data/orders.db` untrack, taaki live trade-DB kabhi public origin pe na jaaye). VPS repo REAL path `/root/ARSALAN/CODE3B- TV BACKTEST ENGINE` (`/root/CODE3B...` = symlink). `git init` → `git reset --mixed origin/master` (HEAD+index origin pe, **working tree UNTOUCHED** — running code byte-identical; 8 paper PIDs + dashboard/monitor verified zinda before==after).
- **Bidirectional auth:** fetch = HTTPS anon (public repo, koi cred nahi); push = SSH via VPS deploy-key `~/.ssh/algo_deploy_ed25519` (`core.sshCommand` + `IdentitiesOnly`). User ne pubkey GitHub Deploy Keys me **write access** ke saath add ki. Test: VPS empty-commit push → GitHub → local pull → dono HEAD `4d5fb73` identical. **PASS.**
- **Naya workflow (scp retired for CODE3B):** local kaam→`git push`; VPS deploy→VPS pe `git pull`; VPS live-fix→VPS pe `git commit && git push`→local `git pull`. Memory: [[project_code3b_vps_git_sync]].
**Note:** VPS working tree abhi bhi origin se kuch files pe "behind"/dirty (`git status` me `M`=purana order-path, `D`=origin ke docs/tests jo VPS pe nahi) — worklist changes deploy karte waqt `git checkout`/`pull` (webhook Task-4 cutover pehle VPS paper signal-replay maangta).
**Files:** .gitignore (+`*.db`/`*.sqlite` catch-all, -data/orders.db), VPS-side git init/remote/deploy-key (no repo files).

## 2026-07-13 — Registry Legs column + gzip Lab pages (LIVE) + full local↔VPS drift reconcile
**Status:** DONE — features VPS-deployed; reconcile commits local-only, pushed to origin (`734be41`·`710fd67`·`fcfeb60`·`714c57f`·`784bd0c`)
**Layer:** ui / infra
**Kya / Kyun:** Do chhote user requests + unke deploy me nikla ek bada drift issue, poora reconcile kiya.
- **Registry Legs column (#56):** `strategy_registry.json` me har strategy pe explicit `legs` field (canonical source — leg-counts `option_structures.py` se: naked/single=1, straddle/strangle/vertical/backspread/credit=2, iron_fly=4). `templates/strategy_registry.html` COLS + cell me toggle-able **Legs** column (Status ke baad). Loader raw-JSON pass karta hai, koi schema-filter nahi.
- **Lab page gzip (#49):** `serve_lab_file` ab text assets (js/html/json/css…) gzip-serve karta hai — sidecar `<file>.gz` cache (mtime-tied) + ETag/304 + path-traversal guard. `results.js` 13.6MB→3.4MB (4×). Read-only path, order-flow untouched. `.gitignore`: `scratch/nifty_trend/**/*.gz`. Slowness = payload size thi (conditional-304 pehle se tha), compute nahi.
- **VPS deploy — DRIFT PAKDA:** teeno files scp karne se pehle drift-check me mila `trader_dashboard.py`/`risk_gate.py` **bidirectionally diverged** — VPS pe **VRP positional / ADR-006 overnight lane** feature tha jo local git me kabhi aaya hi nahi. Blind overwrite = wo live logic wipe (positional position 3:15 pe force-close). So registry.json/html overwrite (safe, VPS==local minus legs), par `trader_dashboard.py` **surgical-merge** (sirf gzip hunk VPS-copy pe, vrp/ADR-006 preserve). Deploy verified: md5 match, 8 paper-strategy PIDs restart survive (KillMode=process), gzip live (4.8MB→1.38MB).
- **Full drift reconcile:** poora scan (158 tracked, CRLF-noise strip karke 46 real). **Sirf genuine VPS-only kaam = VRP feature** → local me pull kiya: `risk_gate.allow_overnight()`, `trader_dashboard` vrp+overnight-skip, `health_check` vrp entry, + naye files `vrp_straddle_trader.py`, `vrp_signal.py`, `_ADR/ADR-006-*.md`. `risk_gate` ab 0/0 vs VPS. **4 order-path files (webhook_executor/range_trader/execution_gateway/universe_trader)** hunk-by-hunk padhe → **local-AHEAD** (worklist Tasks 3b/4/3c), VPS pe sirf purana superseded code, kuch bachana nahi. **Line-endings:** `.gitattributes` (force LF) — 55-file CRLF noise permanent-fix. **Dead leftover:** VPS `brokers/smart_order.py` (June-30, `_paths` `brokers/` ko path pe daalta nahi → kabhi import nahi hota) — optional cleanup.
**Files:** strategy_registry.json, templates/strategy_registry.html, trader_dashboard.py, _core/risk_gate.py, health_check.py, strategies/live/vrp_straddle_trader.py, strategies/live/vrp_signal.py, _ADR/ADR-006-*.md, .gitattributes, .gitignore

## 2026-07-13 — claude-code-worklist.md: Tasks 0/1/3/4/5/6 (go-live check + data-integrity + gateway migrations + loop automation)
**Status:** DONE (local commits `b68cdbe`·`85de33d`·`e32bf3e`·`1384058`·`d5b6a17`·`cbbe00e`; VPS deploy SCHEDULED post-market 15:35 IST via scheduled task, NOT yet pushed to origin)
**Layer:** infra / execution / validation / config
**Kya / Kyun:** User handed `claude-code-worklist.md` (standing cross-session worklist, self-maintaining). Worked it in priority order:
- **Task 0 (go/no-go, pre-market):** Verified 6-strategy go-live. Key finding — worklist assumed "6 strategies ₹10L live"; reality = **every active strategy is `mode:paper`** (₹0 real risk), #06 shortvol `active:False` ✅, global capital ₹10L, per-strategy caps null (no over-sum). **8 files deploy-drifted** (VPS older than git HEAD: 02-07 + execution_gateway + risk_gate) → user chose post-market sync. LTP-dedupe: 02/05 call broker.quote at entry (01/03/04/07 don't loop-poll; monitoring batched via ltp_poller) — low severity in paper. dup-process dedup ✅, BANKNIFTY collectors ✅, order_store wiring ✅.
- **Task 6 (slip re-cost):** `dom_recost.py` (read-only) real DOM spread on all 14 pre-ADR-005 runs → `scratch/nifty_trend/SLIP_RECOST_2026-07-13.md`. Hub NOT overwritten (user: document-only). Flags: dvert(02) OOS 1.005→0.926, chain_zone_positional OOS 1.062→0.985 cross <1.0 in OOS only; live fauj 00/01/03/04/05 keep full Sharpe >1 (edge real). 6e ✅ SLIP_ENABLED can't silently revert.
- **Task 1 (data-integrity):** new `scratch/nifty_trend/data_integrity.py` — `worst_day_sanity()` (TRAP #109: long option can't gain on an adverse move) + `assert_coverage()` (TRAP #107 generalized). Wired into `run_hunt.py` (meta.json `data_integrity` block + `shippable` gate) + `optlake_load.ironfly_frame` + `real_struct2.grid`. Unit-tested + no false-fail on real runs.
- **Task 3a/3b/3c:** `scripts/setup_hooks.sh` (pre-commit hook installer for fresh clones, referenced in CLAUDE.md Rule 6B); `range_trader.py` equity route inline `check_drawdown/concentration/capital` → shared `strategy_safety.gate_entry()` (Rule 6B; seg=NSE_EQ; fixed an `UnboundLocalError` scope bug — `get_broker` was option-branch-local); `universe_trader._recover_state_from_order_store` recovery-failure path → loud ⚠️ WARN.
- **Task 4 (webhook → gateway):** added `sl_type_override` param to `execution_gateway.execute_signal`; migrated `webhook_executor._do_entry` main leg to it (SL-type/blocked-row/hedge/est_price preserved; new no-premium skip). Offline-parity test PASS. **Live cutover still gated on VPS paper signal-replay.**
- **Task 5 (loop automation):** 3 `.claude/commands/*.md` (architecture-audit-loop, eod-report-review, hunt-pipeline) with all rails (HARD EXCLUSION of `_core/` live-path — also CLAUDE.md **Rule 6D**; idle-aware skip; budget cap; `data/loop_activity.log`; max-3-fail; no auto-commit; diff-vs-last-run; worktree+hunt_guard). Seeded audit_history baseline. First-run-manual (eod-review) + starting the `/loop`s = pending prerequisites.
**Files:** scratch/nifty_trend/{data_integrity,dom_recost-run,run_hunt,optlake_load,real_struct2}.py + SLIP_RECOST_2026-07-13.md; _core/{execution_gateway,webhook_executor}.py; strategies/live/{range_trader,universe_trader}.py; scripts/setup_hooks.sh; CLAUDE.md; .claude/commands/*.md; claude-code-worklist.md.
**Bacha:** Task 2 (deprioritized), 7 (market-open straddle), 8 (OI-unwinding), 9 (log rotation — new), 10 (`03_orbst_trader.py:170` `_supertrend_dir` DUP-INDICATOR audit FAIL — new). Post-market: VPS deploy (scheduled) + origin push (needs user OK).

## 2026-07-12 — Real bid/ask spread slippage from brother's DOM order-book (ADR-005) — measured, wired into all BS pricing engines, borderline + vol strats re-tested
**Status:** DONE (code local+git `62019ca`/`449f9b5`; vol-family re-run done on VPS)
**Kya:** User ("Direction A") — brother's 20-level order-book (DOM) data (`C:\_SABHAI DATA - Copy`,
21 days Jun-Jul'26, ATM CE/PE/FUT, ~8 snaps/sec, READ-ONLY) se pehli baar **real bid/ask spread
MEASURE** kiya, aur backtest ke guessed cost knobs ko usse replace kiya. Motivation: `bs_option.reprice*`
(sab directional ATM strats) spread ko ZERO maanti thi, `real_struct2` (vol family) flat 0.5%/leg —
dono unvalidated (LESSONS TRAP #111).
- **Measure:** `scratch/nifty_trend/dom_spread.py` — zips stream karke per-instrument/time-of-day/premium
  real one-way half-spread + walk-the-book slippage (memory-bounded Reservoir sampling) → `dom_spread_calib.json`.
  Result: NIFTY ATM ≈ **0.11-0.16%/leg** (median), OTM wings 0.24% (1.2% mean/2% p90), FUT 0.008%.
- **Load:** `dom_cost.py` — calib ko per-premium per-leg fraction ke roop me expose karta hai (flat≈0.133%).
- **Re-cost (no pipeline re-run):** `dom_recost.py` — kisi bhi recorded run ke bs trades (entry_prem/exit_prem/qty)
  se spread subtract karke Sharpe recompute (`engine._annualize_sharpe` full daily grid pe — zero-slip
  recorded Sharpe EXACTLY reproduce karta hai; verified pivot 0.967, straddle 3.549).
- **Wired (durable, "future ke liye secure"):** single shared `bs_option.slip_cost_leg()` (DOM-calibrated,
  `SLIP_ENABLED`/`SLIP_MULT` knobs, 0.15% fallback) → `bs_option.reprice*` (per leg) + `option_structures.
  backtest_structure.close_pos` (per leg, qty×|side|) + `real_struct2` (`slip_mode`: "dom" default / "flat"=legacy).
  Har future hunt ab real spread include karta hai; Sharpe≥1 gate honest numbers pe. Trade dict me `slip` field.
- **Verified E2E:** pivot bs|full = 0.967 (`SLIP_ENABLED=False`) → 0.941 (True) == dom_recost; structure path
  slip off=₹0/trade, on=₹39/trade per-leg.
- **Findings:** deployed ATM-buy fauj (01 straddle 3.55→3.29, 00 mid-ORB 2.37→2.32, 03 orb_st 2.07→2.00,
  04 chainzone 1.95→1.87) real spread pe 2× stress tak SURVIVE (haircut ~2-8%). Borderline confirmed marginal:
  #08 pivot 0.97→0.94 (gate ke neeche, cost-artifact nahi), chain-zone positional 0.97→0.93 full / OOS 1.02→0.99.
  Vol family (VPS-run, real lake): iron_fly −54%→−41% (dead), short_straddle −12%→−1.0% (~breakeven — VRP edge
  spread se nahi marti, par naked=tail / hedged=wing-cost → koi vehicle nahi). Verdict same, diagnosis refined.
- Docs: ADR-005, OPTION_STRATEGY_MISSION.md RESUME-HERE + vol-family section, LESSONS TRAP #111, CLAUDE.md.
- **NOTE:** existing `runs/` abhi bhi zero-slip numbers dikhate hain jab tak re-run na ho (dom_recost on demand
  gives honest; bulk-refresh deliberately NOT done — har documented Sharpe rewrite hota).

---

## 2026-07-10 — Backtest chart INDICATOR OVERLAYS (opening range / breakout trigger / SL / target / entry-window) + ORB logger double-print fix
**Status:** DONE (VPS-deployed + verified)
**Kya:** User chahta hai ki jab wo koi strategy ka backtest result dekhe, chart pe sirf entry/exit
arrows nahi, balki **strategy ne jo indicators use kiye** wo bhi dikhein — "tabhi samajh aayega ki
signal bana kyun". Explicitly TradingView **nahi** — apni app ka `dashboard_intraday.html` chart use
karo. Banaya ek **generic overlay mechanism**: producer per-trade levels emit karta hai, chart jo bhi
present ho draw karta hai (strategy-agnostic). ORB reference implementation done.
- **Chart** (`scratch/nifty_trend/dashboard_intraday.html` → `candleSVG()`, pure SVG): reads
  `R.meta.overlays[entry_dt]={band,lines[]}` + `R.meta.overlay_spec.window` → draws opening-range
  band, horizontal level lines (break trigger orange / SL red / target green dashed), entry-window
  vertical shade. y-range overlay levels ko include karta hai (clip nahi hote). Koi bhi strategy jo
  yeh fields populate kare, apne-aap render hoti hai.
- **Producer** (`scratch/nifty_trend/add_overlays.py`, NEW): `build_overlays(design, params, tf, trades)`
  → `meta.overlays` + `meta.overlay_spec`. `_orb_overlay()` OR-box + OR±k·ATR trigger + entry∓atr_sl·ATR
  SL/target compute karta hai — **same formulas as intraday_engine** (engine.atr + ie.resample reuse,
  Rule 6B, koi duplicate). `run_hunt.py` me `build_intraday` ke saath wire — ORB-family (orb/tod_orb/
  orb_st) fresh hunts auto-overlay. Existing run patch: `python add_overlays.py <slug>`.
- **ORB logger fix:** `strategies/live/orb_trader.py` `_make_logger()` — StreamHandler ab sirf
  interactive TTY pe (systemd/Popen me stdout pehle se log-file me redirect → har line 2 baar aa rahi
  thi). Console-cosmetic only, order/signal pe zero asar.
**Layer:** ui (backtest chart) + strategy (live logger)
**Files:** `scratch/nifty_trend/dashboard_intraday.html`, `add_overlays.py` (new), `run_hunt.py`,
`runs/mid_orb_nifty/{results.js,index.html}`, `strategies/live/orb_trader.py`
**Verified:** overlay levels backtest se EXACT match — SHORT trade (2022-02-01 13:15) ka computed SL line
17387.2 == actual ATR-SL exit 17387.2. 567 overlays generated. Rendered SVG me Opening Range/Break
trigger/SL/Target/entry-window sab present (browser DOM-verified; screenshot tool bade results.js pe
timeout hua par overlay elements confirmed). VPS scp — md5 local==remote ×3 (results.js/index.html/
template). `/lab` login-gated (2026-07-10 auth) → browser-logged-in user ko dikhta, curl 302.
orb_v1 restart karke single-line logging confirm (paper+flat, koi position nahi).
**LIMITATION:** abhi sirf ORB-family renderer. Naya non-ORB family → `add_overlays.py` me `<design>_overlays()`
add karo (RSI/EMA/VWAP = per-bar series chahiye; chart abhi band+lines+vband handle karta hai, series-type future).

---

## 2026-07-10 — Mode-wise capital pools: LIVE entry sirf LIVE in-use ke against, PAPER sirf PAPER ke against
**Status:** DONE (VPS-deployed + live-verified same day)
**Kya:** User ne poocha "₹10L cap ka kuch karna hai kya?" — breakdown nikala to asli culprit mila:
capital_in_use() me PAPER positions bhi ginti thi (LIVE ₹1.92L + PAPER ₹5.23L = ₹7.15L against ₹10L cap).
Aaj ka live webhook 2-lot isi wajah se 1 lot me size-down hua — paper data-collection strategies real
trading capital kha rahi thin. Fix: `_today_open`/`capital_in_use`/`check_capital`/`sized_lots`/
`exposure_by_underlying`/`check_concentration`/`capital_headroom` sab me optional `mode` param —
gate_entry (jo mode pehle se leta tha) ab use thread karta hai; range_trader ka equity direct-branch bhi.
mode=None = purana combined behavior (display/reconcile routes untouched). Block-reason me ab
"live-pool"/"paper-pool" tag bhi.
**Layer:** execution (RMS money-path)
**Files:** `_core/risk_gate.py`, `_core/strategy_safety.py`, `strategies/live/range_trader.py`
**Verified:** VPS pe aaj ka exact scenario re-run — 2-lot LIVE pool = PASS + sized_lots=2; old combined =
BLOCK (in-use ₹8.26L). Services restart, strategy PIDs unchanged (diff-verified), webhook position recovered.
**Note:** live strategy PROCESSES (range_v1 etc.) abhi purana code chala rahe hain — unki paper entries
tab tak combined pool (conservative) check karengi; kal 9:10 auto-start pe naya code load hoga.
**Depends on:** gate_entry ka existing mode param; order_store rows ka mode field.

## 2026-07-10 — RMS margin estimate: executing-broker-first (Kite order_margins), Dhan calculator = fallback
**Status:** DONE (VPS-deployed + live-verified: Kite ₹1,92,006 vs Dhan ₹1,92,102 on NIFTY-24250-PE 65qty SELL;
md5 ×4 local==VPS; both services restarted mid-day with user go-ahead — all 6 strategy PIDs + live webhook
position survived, [WEBHOOK][RECOVER] restored it, zero errors post-restart)
**Kya:** RMS capital checks ka "needed ₹" ab Zerodha ke apne `order_margins` API se aayega jab
default_broker=kite (jahan orders asli mein jaate hain) — Dhan margin-calculator ab fallback.
TRAP #90 ka natural follow-up: user ne phir poocha "Dhan ka margin kyun jab order Zerodha pe gaya".
**Layer:** broker / execution (RMS money-path)
**Files:** `brokers/kite_broker.py` (new `margin_for_order()`), `_data/dhan_master.py` (new
`get_trad_sym_for_sec_id()`), `_core/risk_gate.py` (new `kite_real_margin()` + `broker_real_margin()`
wrapper; `_leg_capital`/`check_capital` switch), `_core/strategy_safety.py` (`gate_entry` funds-check switch).
**Kyun:** Margin estimate executing broker ka hona chahiye (TRAP #90 lesson) — SPAN/exposure ~same
hote hain par exactness + trust. Fallback chain: Kite → Dhan calculator → multiplier (kabhi fail-open nahi).
**Depends on:** resolve_kite_symbol (TRAP #13/#59 safe resolver), kite_rate_limiter, dhan_master cache.

## 2026-07-10 — 3-PASS backtest pipeline (Instrument → RMS → Black-Scholes) + fresh NIFTY hunt + per-strategy folders
**Status:** DONE (local-verified; scratch/research only, live order-path untouched)
**Layer:** backtest / strategy / UI
**Kya:** User request #35 — RMS ki tarah Black-Scholes ko bhi ek SEPARATE pass banaya, "instrument →
RMS → Black-Scholes" funnel se har layer ka asar alag clear dikhe. Har strategy ka backtest ab apne
`runs/<slug>/` folder mein; spec-builder master-prompt isko reflect karta hai.
**Files:**
- `scratch/nifty_trend/bs_option.py` (NEW) — modular BS pass: `bs_price` (erf, no ext data),
  `calc_charges` (real Zerodha F&O), `tte_years` (weekly Thu expiry), `realised_vol_map` (σ proxy),
  `get_nifty_lot` (scrip master → 65, no hardcode), `reprice` (spot trades → ATM CE/PE premium P&L).
- `scratch/nifty_trend/run_hunt.py` (NEW) — screen×TF → optimize(min(train,oos), TRAP #103) →
  significance(p<0.05) → 3 passes×3 periods (9 combos) → BS reprice → `runs/<slug>/`{results.js,
  index.html, meta.json} + `runs/index.json` append.
- `dashboard_intraday.html` — PASS toggle (①/②/③), combo key `"<pass>|<period>"`, meta-driven info;
  BACKWARD-COMPATIBLE (old single-axis results.js still renders).
- `hub.html` — auto-loads `runs/index.json`. `strategy_spec_builder.html` — 3-pass mandate + TRAP #103
  ranking fix. `intraday_optimize.py`/`RESULTS_SCHEMA.md`/`CLAUDE.md` updated.
**Result (winner = Mid-Day ORB @ 15m, p=0.000):** ① 0.97 / ② 0.97 / ③ BS Sharpe 2.37, net +39.2%,
maxDD −1.8%, PF 1.93, real fees ₹32.8k (vs bogus spot ₹1.45L). ③ = deployable truth.
**Kyun:** spot-notional P&L misleading (fake tax, linear payoff); ab asli ATM option-premium pe.
**Depends on:** BS_OPTION_SIM.md/RESULTS_SCHEMA.md (2026-07-09).

---

## 2026-07-09 — NIFTY trend/ORB research pipeline → Mid-Day ORB strategy DEPLOYED (paper) + Strategy Lab + Spec Builder + RMS profit-target
**Status:** DONE (research + orb_v1 paper-deployed; live fire-test pending market hours)
**Layer:** strategy / backtest / RMS / UI / infra
**Files:** `scratch/nifty_trend/*` (research pipeline, git force-added), `strategies/live/orb_trader.py` (NEW live trader), `trader_dashboard.py` (routes + STRATEGIES["orb"]), `templates/index.html` (nav + RMS table), `strategy_spec_builder.html` (root), `_risk.per_strategy.orb_v1` in nifty_config.json (gitignored)

**Kya:** A YouTube video (Jesse framework + Claude) prompt-driven "find 2 trend strategies, validate with significance + Monte Carlo, don't ship overfit" — replicated for **NIFTY** inside CODE3B. Built a full standalone research pipeline, found ONE genuinely-significant edge, deployed it paper.

**Research pipeline** (`scratch/nifty_trend/`, self-contained, force-tracked; big 1-min CSVs gitignored):
- `data_fetch.py`+`datalake.py` — Dhan `/v2/charts/intraday` NIFTY (secId 13 IDX_I), **90-day max/call** (DH-905 beyond), serves 5+ yrs back. 4.5yr 1-min → per-day CSVs in canonical `._TRADING DATA/Index/NIFTY/` (reusable, zero re-download on re-run — forward-fill only, holidays skipped). `download_bnf.py` = BankNIFTY (secId 25).
- `engine.py`/`intraday_engine.py` — event-driven backtester, `wilder_atr` from `_CHARTING.indicators` (Rule 6B, matches live), **1x leverage cap (no-leverage)**, intraday rules (3:15 exit, max 2/day, no-entry-after), 6+9 designs (rsi_rev/bb_fade/sess_rev/orb/donchian/supertrend/orb_st/gap_fade/tod_orb), fee ~₹240/round-trip (realistic futures).
- `optimize.py`/`intraday_optimize.py` (train/OOS split), `significance.py` (rotation permutation ×1000, beta-controlled), `montecarlo.py` (trade bootstrap ×1000), `report.py`/`intraday_report.py` → `results*.js`.
- Dashboards: `dashboard.html` (positional), `dashboard_intraday.html` (ORB winner, Full/Train/OOS toggle, Jesse-style trades table + full-screen candlestick chart modal, search + column-toggle), `hub.html` (Strategy Lab table — sortable/filterable/star/Deploy), `strategy_spec_builder.html` (master-prompt generator), `nifty_trend_mockup.html`.

**Findings (honest):** Positional trend = Sharpe 1.1 but **significance FAIL** (mostly 5.7x leverage + beta; at 1x only +22%, below buy&hold). Intraday trend-following = weak. **WINNER = `tod_orb` Mid-Day ORB (entries only 11:00-13:00) @15m:** train Sharpe **0.95 ≈ OOS 0.96** (no decay), win 53-54%, maxDD **-4.4%**, p=0.000 significant, 20 robust-both-halves configs. Config: `{or_min:30, orb_k:1.0, h0:11, h1:13, atr_sl:2.5, rr:1.5}`.

**Live-trader `orb_trader.py` (config `orb_v1`, paper, active=false):** follows NEW_STRATEGY_CHECKLIST — entry/exit via `execution_gateway.execute_signal/execute_exit` (RMS gate + order_store + no-premium-skip + fill-confirm), fetches NIFTY 15m spot (last 5 days for continuous ATR warmup, TRAP #85), OR 09:15-09:45 → 11:00-13:00 breakout+1×ATR → BUY ATM CE(long)/PE(short), **spot-based** stop(atr_sl×ATR)+target(RR×), 3:15 force-exit, max 2/day, disk state-persist + `_recover()` vs order_store (TRAP #28). Registered STRATEGIES["orb"]; `_base("orb_v1")`→"orb". Live compute_signal VERIFIED == backtest. VPS-deployed.

**RMS two-stage validation (key concept — see LESSONS #103/#104):** search stays unconstrained (RMS NOT in master-prompt, else edge never found); before deploy, `intraday_engine.backtest(rms_caps=)` re-runs under real RMS caps. Found global ₹3000 profit-target degrades ORB 0.93→0.52. Fix: **per-strategy profit-target override** — backend `effective_daily_profit_target` already read `per_strategy[strat].profit_target_rs` (UI had no column); set `orb_v1 = {max_loss_rs:10000, profit_target_rs:15000}`; **added "Max Profit ₹" column** to Per-Strategy Override UI table (`profit_target_rs`, save/load wired). Caveat: thresholds were SPOT-P&L rupees; live 1-lot OPTION P&L scale differs — paper-measure.

**App integrations (routes in trader_dashboard.py):** `/spec-builder` (Strategy Spec Builder), `/lab/<path>` (Strategy Lab hub + dashboards from scratch/nifty_trend). Nav (index.html): removed old "Script" tab (📜 icon → Script 3), Results → More dropdown, **Strategy Lab → top-nav**, Spec Builder in More. Per-Strategy table: **freeze Strategy column** (sticky) + auto-width columns (no cramp) + **pretty display names** (`fmtStratName`: orb_v1→"ORB v1"). Data-lake NIFTY 1-min now reusable project-wide.

**Deploy:** all VPS-deployed (algo-dashboard restart, KillMode=process, market closed, live PIDs untouched); routes 200-verified. `nifty_config.json` gitignored so orb_v1 config + per_strategy override are VPS/local-only (not in git).

## 2026-07-09 — RMS single-source exit/no-entry time + 3:15 phantom-brokerage fix + local VPS-sync (#19/#20/#21)
**Status:** DONE + **VPS DEPLOYED** (commit 2e5d95e; 0 open positions/0 traders during window; import-verify before restart; both services active, HTTP 200, no errors; RMS card + webhook-removal served).
**Kya:**
- **#19 (bug) "zabardasti tax":** range/rsi/ema/universe 15:15 pe exit karti thीं par general **no-entry-after gate nahi tha** (sirf expiry-2PM) → 15:15+ signal entry leta, phir 3:15 squareoff turant band → entry+exit ka ~₹50 brokerage bina trade ke. Root confirmed (range_trader entry-block sirf `EXPIRY_NO_ENTRY_AFTER_HM` check karta tha).
- **#20 (feature) single-source:** naya `risk_gate.exit_time_config()` — `_risk.global.auto_squareoff_at` + `no_entry_after` (default 15:15). Saare 4 traders + webhook_executor + `pos_monitor_loop` yahi se time uthate hain (ab har file me hardcoded `(15,15)` nahi). Har trader me `_exit_times()`/`is_no_entry_time()` helper + entry-path me missing no-entry gate add.
- Webhook config se apne `no_entry_after`/`squareoff_at` fields HATA diye (UI + load + save) — ab RMS single-source (user: "ek jagah se sab").
- RMS tab: naya "⏰ Exit & no-entry time" card (2 time inputs, all-strategies).
- **#21 local sync:** root = koi auto VPS→local sync nahi (local dashboard stale trades.db). Naya `/api/sync-from-vps` (LOCAL-only guard: `sys.platform=='win32'`, VPS Linux pe blocked) → `_ops/sync_vps_to_local.py`; header me "🔄 Sync from VPS" button + "Auto (10m)" toggle (localStorage) + last-synced, sirf LOCAL host pe dikhta.
**Layer:** execution / config / ui / infra
**Files:** `_core/risk_gate.py` (exit_time_config + _parse_hm), `_core/webhook_executor.py`, `trader_dashboard.py` (pos_monitor + sync route), `strategies/live/{range_trader,01_rsi_v1,nifty_ema_trader,universe_trader}.py`, `templates/index.html`.
**Kyun:** User ne live pe ₹50 phantom brokerage baar-baar dekha (money-path bug) + exit-time control bikhra hua tha + local pe aaj ka data nahi aata. Verify: py_compile all, full dashboard import + route, RMS save/load round-trip (UI keys → exit_time_config → custom times MATCH), VPS import-verify before restart.

---

## 2026-07-09 — Repo folder refactor: flat root → _core/_data/_ops + strategies/{backtest,live,lab} (via _paths bootstrap)
**Status:** LOCAL DONE ✅ (5 commits: 73a213a·f308e64·9a59262·79aea41·docs) — **VPS deploy PENDING** (`VPS_MIGRATION.md`).
**Kya:**
Root pe 60+ flat `.py` + bahut scratch/junk tha. Logically reorganize kiya taaki future strategies/tools ka clear ghar bane. **Backup pehle:** git tag `backup-pre-refactor-20260709_154105` (GitHub) + local tarball (54M).
- **P1 (junk):** 6 tracked junk delete, `_test_*`→`_DEV/tests/`, mockups→`_DEV/mockups/`, 58 gitignored scratch→`scratch/`. Top-level 118→49.
- **P2+3 (packaging):** money-path modules→`_core/`, broker/data plumbing→`_data/`, standalone scripts→`_ops/`. Naya `_paths.py` har folder ko sys.path pe daalta hai → **koi import statement nahi badla**. **Critical catch:** 24 modules `Path(__file__).parent/"data"` se path banate the — 1 level neeche jaane pe ye `_core/data` point karne lage (config.json/trades.db/scrip-master galat jagah) → sabko project-root pe rebase kiya (`.parent.parent`), verify kiya sab `root/data` pe resolve.
- **P4 (strategies):** backtest strategies→`strategies/backtest/`, live traders→`strategies/live/` (was `_TRADERS/`), naya `strategies/lab/` (AI-strategy-lab scaffold). Loader **leaf-based backward-compatible** (`strategies.<id>` → `strategies.backtest.<id>`) → **live nifty_config `_module` migration ki zaroorat NAHI**. Traders 2-level deep hone se unka `__file__` root-computation ek level upar rebase (BASE_DIR→ROOT verified).
- **P5 (docs):** `strategies/lab/README.md` (per-strategy folder + Monte-Carlo/optimize/reports convention), CLAUDE.md Folder Structure section, `deploy_vps.py` fix, `VPS_MIGRATION.md`, `REFACTOR_PLAN.md`.
**Layer:** infra
**Files:** naye `_paths.py`, `_core/*` `_data/*` `_ops/*` `strategies/{backtest,live,lab}/*` (git mv), edits: `trader_dashboard.py` `health_check.py` `monitor_daemon.py`(transitive) `_TOOLS/backtest_engine.py` `_TOOLS/architecture_audit.py`(SCAN_DIRS) `strategies/__init__.py`(resolve/load) + har directly-run file me `import _paths` bootstrap.
**Kyun:** Live prj, top-level unmanageable ho gaya tha; future strategies + optimization results ka clear ghar chahiye tha (user ka Jesse-style workflow). Safety: har phase alag commit + verify (py_compile, import-graph 18/18 clean, full dashboard import clean, architecture audit 0 FAIL, pre-commit hook pass). VPS deferred — market-closed window me `VPS_MIGRATION.md` follow karna (stale files remove + `_ops` timer ExecStart paths; entrypoints root pe → systemd unchanged).

---

## 2026-07-09 — Webhook SL: fired-SL suppression root-fix + single-source consolidation (webhook SL → RMS Default-TSL, per-webhook SL-Type selector)
**Status:** DONE + **VPS DEPLOYED** (algo-monitor + algo-dashboard restart; live strategy PIDs untouched (KillMode=process); 0 real open live positions during window; py_compile + logic-verify (mode_override tags + feature_on) PASS; SL Type dropdown served).
**Kya:**
Ek live webhook position ka DEFAULT-TSL SL FIRE hua par `_pre_exit_guard` ne "webhook already claimed/closed this leg" ke naam pe squareoff SKIP kar diya → position ~1hr unprotected (LESSONS.md TRAP #102). Do compounding bugs: (1) guard `release_position()==False` (webhook track nahi kar raha) ko "already closed" maan ke skip+blacklist kar raha tha → ab False pe skip nahi, authoritative `is_flat_fresh()` broker flat-check pe fall-through; (2) `_recover_wh_state()` sirf boot pe chalta tha → algo-monitor ki `_wh_state` me post-boot webhook entries kabhi nahi aati → `webhook_monitor_loop` se ab periodic (~30s, non-clobbering, `_lock`-guarded) recover.
**+ Consolidation (user decision "sirf RMS Default-TSL"):** webhook ka apna `sl_points`/`target_points`/`trail_mode`/`trail_value` + `monitor_tick` ka premium/index trail HATA diya (dual-system overlap → chart-line ≠ real exit tha). Ab webhook config me ek **SL Type selector** (aggressive/legacy/dropdown); `_do_entry` → `default_instrument_sl_tags(..., mode_override=cfg["sl_type"])` chosen-type ke RMS tags stamp karta hai; `default_target_sl_config()` me naya `feature_on` (`=default_tsl_enabled`, global-mode-independent) + pos_monitor aggressive-firing gate `enabled`→`feature_on` (per-position AGGR_TSL — incl. per-webhook aggressive — global default se independent). Current config me `feature_on==enabled==True` → no-op, safe. `monitor_tick` sirf global-cap + 3:15 squareoff; TV-EXIT `handle_signal` se. Chart 3 lines waise hi (ab real enforced).
**Layer:** execution / config / ui
**Files:** `trader_dashboard.py` (`_pre_exit_guard` False→flat-check, `webhook_monitor_loop` periodic recover, aggressive gate feature_on), `webhook_executor.py` (`_do_entry` mode_override + own-SL removed, `monitor_tick` trail removed, `_recover_wh_state` non-clobber+lock, DEFAULTS/`_OVERRIDABLE` sl_type), `risk_gate.py` (`default_instrument_sl_tags` mode_override, `default_target_sl_config` feature_on), `templates/index.html` (webhook config SL Type dropdown).
**Kyun:** User incident — SL line dikh rahi thi par exit nahi hua; dual SL system (webhook-own vs RMS) confusion + false-claim suppression. Goal: ek source-of-truth (RMS), chart-line = real exit, per-webhook SL-type choice. Safety: enforcement fix live-money-critical; deploy 0-open-position window me + no-op-under-current-config verify.
**Depends on:** RMS Default-TSL (2026-07-04) + merged SL profile (2026-07-07); `is_flat_fresh` (TRAP #75).
**Watch:** aggressive profit-lock trail webhook pe abhi live-observe nahi hui (stock-options pe pehle verified) — agli profit-then-reverse position pe confirm karna.

---

## 2026-07-09 — Trade-chart viewer UX: Reset View + pan/zoom memory, self-hosted vendor libs, live auto-refresh, LONG/SHORT label
**Status:** DONE + **VPS DEPLOYED** (`algo-dashboard` restart only — `KillMode=process` se child strategy PIDs untouched, `algo-monitor` position-guard separate service; har restart pe LIVE PIDs before==after + md5 local==VPS verified).
**Kya:**
(1) **⟲ Reset View + pan/zoom memory** — `trade_chart.html` har refresh pe `fitContent()` kar deta tha → dobara pan/zoom karna padta. Naya `window.CVS` helper: har pane ka `getVisibleLogicalRange()` localStorage (`tcv:`+sym+date+et+xt) me save + refresh pe restore (price scale auto → same candles fit → same view); Reset = clear + fitContent (+ priceScale autoScale). `subscribeVisibleLogicalRangeChange` se user-pan save; `restoring` guard + `pauseSave(ms)` se programmatic setData/refit save-loop me na ghusein. Left-pane ke flex-height deferred-rAF re-measure ke `fitContent` calls ko `CVS.refit('left')` se replace (warna restore override hota).
(2) **CDN libs → self-hosted** — `static/vendor/` me 5 libs (lightweight-charts 4.1.3, jspdf 2.5.1, jspdf-autotable 3.8.2, chart.js 4.5.1 umd, apexcharts) curl-download; 5 templates (trade_chart/index/watch_chart/backtest_chart/mtm_charts) ke `<script src>` unpkg/jsdelivr → `/static/vendor/`. Kyun: render-blocking external round-trip = "refresh slow" (API khud 80-130ms fast thi); ab Flask se ~11ms + browser-cache + offline-proof. `Flask(__name__)` default `/static/` serve karta hai.
(3) **Live auto-refresh (in-place)** — naya IIFE: har 15s dono chart endpoints re-fetch → `window._premSeries.setData`/`_leftSeries.setData` (candle-only; markers/lines/view intact — recording-friendly, no reload flash). Header 🔄 Auto toggle (localStorage `tradeChartAutoRefresh`, default ON), `document.hidden` pe pause, `CVS.pauseSave(800)` view-drift guard.
(4) **LONG/SHORT direction label** — header pill (`#dir`, ▲ LONG green / ▼ SHORT red) + premium & underlying entry markers directional. Logic: `(side==='BUY')===(optType==='CE')` ? LONG : SHORT (SELL PE/BUY CE=LONG bullish; SELL CE/BUY PE=SHORT bearish). User ke NIFTY-PE SELL = ▲ LONG.
**Layer:** ui / infra
**Files:** `templates/trade_chart.html` (CVS helper, Reset+Auto buttons, self-host, auto-refresh IIFE, dir label, series refs), `templates/index.html`+`watch_chart.html`+`backtest_chart.html`+`mtm_charts.html` (self-host src), `static/vendor/*.js` (5 new). `index.html` me prior-session already-VPS-live "Script 2 tab removed" bhi is commit me carry (git↔VPS drift reconcile).
**Kyun:** User live position ko chart pe watch/record kar raha tha — refresh slow + har baar view reset + long/short ambiguous. Safety: `algo-dashboard` = web/API only (pos_monitor_loop `algo-monitor` me) + `KillMode=process` → restart se koi live position/strategy touch nahi.
**Depends on:** lightweight-charts v4 `setVisibleLogicalRange`/`subscribeVisibleLogicalRangeChange`; `/api/trade-chart-data` (`sl_series`, 2026-07-08).
**Known gap (offered, user-decision pending):** auto-refresh sirf candles rebuild karta hai — SL-now/Trailing-SL/Target/P&L overlays load-time value pe atke (full reload pe sahi). User ne isko "sl line sahi jagah nahi" identify kiya; fix = overlay-draw ko re-callable function bana ke tick pe rebuild (frontend-only).

---

## 2026-07-08 — 4-task worklist: 3-dot SL menu fix, blocked-record premium fix, RMS audit log, moving SL on chart+live row
**Status:** DONE + **VPS DEPLOYED** (algo-dashboard restart only — monitor/positions untouched by design; range_trader effective next ARS_CHAIN start; md5 local==remote, VPS py_compile PASS, live endpoints verified).
**Kya:**
(2) **3-dot ⋮ → SL/Target menu "nahi khulta"** — console `Cannot set properties of null (setting 'checked')` at `openSlTpModal` (index.html:11261): modal ke purane candle-close checkboxes (`edit-sltp-sl-candle`/`-tp-candle`) redesign me hata diye gaye the (Type dropdown ka "Candle Close" option unhe replace kar chuka), par JS abhi bhi `.checked` set kar raha tha → null crash → modal kabhi khulta hi nahi. Fix = `openSlTpModal`+`saveSlTp` me null-guard. (Ye TRAP #97 ka pending #2 tha — ab live repro mil gaya.)
(14) **LT-3960-PE "3974 in / 65.60"** — `range_trader.py:1169` CAPITAL_BLOCKED option leg ko underlying **spot** `price` (3974.8) pe record kar raha tha, `opt_prem` (65.6) pe nahi → blocked-entries panel me bekaar entry-price/points. Fix = `opt_prem` record karo. `webhook_executor`+`universe_trader` pehle se premium record karte the; sirf range_trader ka options-branch galat tha (equity-branch `price` sahi hai). Historical VPS row id 832 abhi bhi 3974.8 (paper/blocked/display-only — chhoda, user ko offer kiya).
(13) **RMS value-change audit log** — `api_set_risk_config` ab purane vs naye `_risk` ko diff karke sirf changed fields (IST ts + scope) `data/rms_audit_log.json` me append karta hai; naya `GET /api/rms-audit-log`; Risk tab me "📋 Change History" panel (`toggleRmsAudit`/`loadRmsAudit`/`renderRmsAudit`, field/scope filter). Pehli save pe populate.
(8) **Moving trailing/aggressive SL — chart + live row.** Per-tick SL kahin store nahi hota, isliye REPLAY: `risk_gate.target_sl_level` reuse (Rule 6B — risk math duplicate nahi). Chart: `/api/trade-chart-data` ab `sl_series` deta hai (`_reconstruct_sl_series` — AGGR_TSL aggressive Default-TSL + trailing_pt; static types flat line rakhte hain), trade_chart.html 2 WithSteps line series (blue=trailing, orange=aggressive tail) + "SL now"/Target price-lines. Live row: `/api/orders` open positions me `sl_now`/`tp_now`/`sl_aggressive` (`_live_sl_for_open` — aggressive: tsl_state.json ka tracked peak; trailing_pt: CONF_MAX/MIN_LTP tags; profile disabled ho to skip taaki misleading na ho), entry_time cell me "Aggr/Trail SL nn ▲". **Live-test me mila+fix kiya:** `_trade_entry_row` same-contract-same-day pe do strategies hone par earliest leg uthata tha → galat entry/side; ab entry-time (et) + strategy se disambiguate (chart `strategy` param pass karta hai). Verified live: rsi BUY @65.6 (SL neeche→upar trail) vs ARS_CHAIN SELL @71.25 (SL upar→neeche trail), dono sahi.
**Layer:** ui / strategy / config
**Files:** `templates/index.html` (Task 2 null-guard, Task 13 panel+JS, Task 8 live-row badge), `templates/trade_chart.html` (Task 8 stepped SL series + strategy param), `trader_dashboard.py` (Task 13 audit routes/diff, Task 8 `_reconstruct_sl_series`/`_live_sl_for_open`/`_trade_entry_row` + `/api/orders`+`/api/trade-chart-data` wiring), `_TRADERS/range_trader.py` (Task 14 opt_prem)
**Kyun:** User worklist (mockup-approved for Task 8+13). Safety: `algo-dashboard` (web/API) pos_monitor_loop nahi chalata — wo `algo-monitor` (monitor_daemon.py) me — isliye dashboard restart SL/TP/EOD enforcement ya open positions ko touch nahi karta.
**Depends on:** risk_gate.target_sl_level / default_target_sl_config (2026-07-04 aggressive profile), tsl_state.json

---

## 2026-07-07 — Backtesting flow redesign: unified Parameter Modal + 4-tab Script page
**Status:** DONE + **VPS DEPLOYED** (2026-07-08 pre-market — 4 backtest files md5-verified local==remote, VPS py_compile PASS, algo-dashboard restart clean HTTP 200, SBIN optimize live-verified on VPS). **Task-3 execution_gateway DELIBERATELY NOT deployed** — VPS pe `execution_gateway.py` still ABSENT, live order-path (traders/backtest_engine) untouched; trader_dashboard.py execution_gateway ko import nahi karta (sab lazy) isliye standalone safe tha. Task-3 apna alag paper-watch rollout deserve karta hai. LESSONS.md TRAP #98 (optimizer parallel-path drift).
**Kya:** Script 3 page ka poora Run/Optimize/Save flow user-sketch ke mutabiq redesign — (P1) EK unified Parameter Modal (Run → modal script page pe hi; version pill, searchable instrument picker reuse, date range, config paste, Code button; single value=Backtest, comma list=Optimize sweep; result NAYE browser tab me auto-run), (P2) 4 top-level tabs (Editor | History | Past Optimizations | Saved Results) — Past Opt all-strategies + sort + search + multi-select delete (per-row del hatao), Saved Results me Rerun/Deploy/Result, (P3) per-strategy presets (full modal state), config version ke saath permanently attach, version pill → code+desc+images+us-version-ke-runs, (P4) Editor me desc+images UI restore (backend routes zinda hain) + multi-symbol save fix (saare symbols ek entry, combined + per-symbol breakdown). (P5, user feedback 2026-07-07 shaam) **Deploy ab NAMED variation banata hai** — pehle Saved Result ka 🚀 Deploy sirf generic `rsi_v1` ka Run modal kholta tha (na saved cfg, na naam, Logs me kuch naya nahi aata). Naya `/api/deploy-variation`: `{base_prefix}_{slug(name)}` config-key (`"Manual Run 7 july"`+rsi_v1 → `rsi_manual_run_7_july`), base-prefix se `_base()` sahi live-script route karta hai, cfg+symbols carry over, **hamesha `active:false mode:paper`** (deploy kabhi auto-run/live nahi — house rule); backtest-only bases (bb/vwap/user_*) reject. Logs sidebar + grid me `RSI_MANUAL_RUN_7_JULY` (user ka naam) dikhta hai. Local verified end-to-end. (P6, user feedback) **Optimize "0 results" fix** — 2 compounding bugs: (a) `api_backtest_optimize` raw variant id (`ARS_CHAIN_V1`) optimizer ko bhejta tha par `run_backtest` `_RUNNERS` BASE-type se dispatch karta hai (`bb_v1`/`rsi_v1` explicit keys the isliye chalte the, `ARS_CHAIN_V1`/`ema_v1` nahi → har combo "unsupported strategy type" → 0 results) — ab `_base()` normalize + custom-script `_module`/`_lang` grid me inject; (b) `optimizer.py` har cfg value `str()` karta tha → builtin runners numeric compare pe `float vs str` crash (sirf bb-custom_rule_engine strings parse karta tha) — ab type preserve. Verified: range NIFTY 2 combos + SBIN 2 combos real trades/pnl. (P7, user feedback) **Optimize galat symbol pe chal raha tha** — user ne dekha ki SBIN optimize Sharpe 1.54/PNL 665.60 dikha raha, par Load karne pe (same params/SBIN) Sharpe −0.28/PNL −8.1. Root: `run_backtest` runners SINGLE `symbol` key padhte hain (`_run_range` → "NIFTY" default; `_cfg_symbol` `symbols` ko sirf LIST maano); optimizer sirf `symbols` (comma STRING) set karta tha → har combo chupchaap NIFTY pe. Fix `_run_single_worker`: per-run `symbol` set — single = run_backtest ka summary direct (Load se bit-identical), multi = trades combine via `_compute_stats` (Load ka `_runMultiSymbol` bhi yahi karta hai). Verified local+VPS: SBIN optimize == SBIN single backtest (MATCH True). VPS deployed (optimizer.py only, algo-dashboard restart). (P8, user feedback) **Sab optimize combos identical** — user Range Chain pe `overbought/oversold/rsi_exit/rsi_period` sweep kar raha tha (RSI params, Range padhta nahi → har combo same 665.60). Root: `ARS_CHAIN_V1` config RSI keys se polluted (Run-modal Save-Config `_RUN_PARAM_DEFAULTS` sab strategies ke daal deta) + Parameter Modal har stored key dikha raha tha. Fix `relevantCfg()` (`script3.html`): builtin ka config sirf uske REAL params (`STRAT_KEYS`, `backtest_chart` `STRAT_FIELDS` mirror) + universal set pe filter; user scripts sab dikhaate. Browser-verified: Range Chain modal me RSI junk gone, Range params intact. VPS deployed (script3.html, algo-dashboard restart, served-HTML confirmed). LESSONS.md TRAP #98 addendum #98b.
**Layer:** ui / config
**Files:** `templates/script3.html`, `templates/backtest_chart.html`, `trader_dashboard.py` (pine/save + saved-results + presets routes), `_TOOLS/backtest_engine.py` (agar multi-symbol combined result shape chahiye)
**Kyun:** User ka hand-drawn flow sketch — abhi 2 alag modals + Past Opt/Saved Results modals ke andar dabe hain; multi-symbol save 1 hi symbol ka naam save karta hai; Editor ka desc/images UI update me kho gaya tha.
**Depends on:** nothing

---

## 2026-07-07 — Tasks 3+5+4: execution_gateway + per-strategy position isolation + checklist update
**Status:** DONE (local; VPS deploy pending — paper-day rule discuss karke) — audit **0 FAIL, 0 baselined** (7→0 in one day), 15/15 isolation tests PASS.
**Kya:** (3) `execution_gateway.py` — `execute_signal()` (no-premium skip TRAP #1 → `gate_entry` fail-closed RMS → default SL tags sirf gated entries pe, hedge legs pe nahi → `smart_order.execute`) + `execute_exit()` (fresh strategy-aware flat-check → `is_exit=True`); teeno strategy files refactored: rsi entry/exit direct gateway + dead raw `place_order()` DELETED, range ka file-local `place_order()` ab thin gateway-delegate (`gate=False` — SELL+hedge pairing ka apna gate pehle chalta hai), universe ke 4 order-sites gateway pe (gating_status short-circuit + CAPITAL_BLOCKED ghost-row preserved); dashboard `/api/kite-test-order` raw kite SDK → smart_order (test order ab order_store mein = SL/EOD protected). (5) Task-5 isolation gateway mein baked: `broker_sync.is_flat/is_flat_fresh` ab `strategy_id` lete hain — `_my_open_qty()` order_store-first (confident-0 SIRF closed-round-trip evidence ke saath; record na ho to None → broker check pe fall through — TRAP #58 untracked gap se jhootha flat nahi); `_pending_group_close` key ab `strategy:sec_id` (helpers `_pgc_queue/_pgc_pop`, old bare-key fallback restart-compat); `_known_broker_keys()` ab `{key: total_qty}` dict + untracked scan qty-compare (partial-untracked detect — TRAP #58 same-contract defeat fix). (Backtest) `backtest_engine` ab `execution_gateway` import karta hai + har result mein explicit `risk_note` (kya enforce hota hai kya nahi — Rule 6B pt 4, silent shortcut nahi); gateway `mode="backtest"` = deterministic checks only (max-premium). (4) `NEW_STRATEGY_CHECKLIST.md` — entry/exit templates ab 1-1 gateway call. universe_trader ko standard sys.path bootstrap bhi mila (pehle root imports spawn-env pe depend karte the). Baseline ratchet 0 pe — ab koi bhi naya raw order-call/inline risk-check/dup indicator commit-block.
**Layer:** execution / broker / strategy / infra
**Files:** `execution_gateway.py` (new), `broker_sync.py`, `trader_dashboard.py`, `_TRADERS/01_rsi_v1.py`, `_TRADERS/range_trader.py`, `_TRADERS/universe_trader.py`, `_TOOLS/backtest_engine.py`, `_TRADERS/NEW_STRATEGY_CHECKLIST.md`, `_test_gateway_isolation.py` (new), `_ADR/ADR-001` + `ADR-003` (new), `_TOOLS/audit_baseline.json`
**Kyun:** Teen strategy files ka entry/exit sequence 3 halke-alag copies mein tha (TRAP #15/#75 drift shape) + Task-5 ka confirmed same-contract collision edge (over-sell risk, pending-close overwrite, untracked-scan defeat).
**Depends on:** Task 1 (audit), Task 2 (indicators)

---

## 2026-07-07 — Task 2: Unified Indicator Layer (duplicate RSI/ATR consolidation)
**Status:** DONE + VPS DEPLOYED (2026-07-07 13:20 IST, user-approved mid-market MCQ ke saath) — deploy_vps.py tarball, backup `/tmp/code3b_backup_1783410453.tgz`, md5 7/7 local==VPS, dashboard+monitor active, HTTP 200, 17 open positions intact (2 live BANKNIFTY untouched), VPS py_compile + teeno trader `--help` import smoke PASS (running traders old code in-memory — formulas identical, naya code kal 9:10 auto-scheduler spawn pe load hoga). Audit 7 FAIL → 3 FAIL (saare 4 DUP-INDICATOR gone; bache 2 RAW-ORDER + 1 BACKTEST-RISK = Task 3 scope). Scope expand hua: inline span-EMAs bhi consolidate kiye (`nifty_ema_trader.py`, `backtest_engine.py` ×2, `strategies/vwap_ema_failure.py`). `_CHARTING/indicators.py` ab 2-tier: canonical pure-pandas `wilder_rsi`/`wilder_atr`/`pine_ema` (tier 1 — live traders bina `ta` ke import kar sakte hain, `ta` lazy) + chart-only SMA/VWAP/BBANDS (tier 2). Registry RSI/ATR/EMA canonical pe point — signal aur chart EK calculation. Verification: synthetic equivalence test old-vs-new **max diff 0.0** (bit-identical → 90.2% validation score unchanged by construction, validate re-run unnecessary); py_compile ALL PASS; teeno trader files `--help` smoke PASS (module-level imports resolve). ADR: `_ADR/ADR-002-indicator-source-of-truth.md`.
**Kya:** 4 duplicate indicator definitions ko `_CHARTING/indicators.py` mein consolidate karna. Strategy ka Wilder-RSI (Pine-validated) = "sach"; `ta`-library wala sirf chart-cosmetic tha. Sites: `_TRADERS/01_rsi_v1.py:253` `_compute_rsi`, `_TRADERS/range_trader.py:284` `compute_atr` (Wilder alpha=1/period), `_TOOLS/backtest_engine.py:541` `_compute_rsi`, `strategies/rsi_v1.py:62` `_rsi`. Sab consumers registry se import karenge — signal aur chart ek hi calculation se.
**Layer:** strategy / validation
**Files:** `_CHARTING/indicators.py`, `_TRADERS/01_rsi_v1.py`, `_TRADERS/range_trader.py`, `_TOOLS/backtest_engine.py`, `strategies/rsi_v1.py`, `_ADR/ADR-002-indicator-source-of-truth.md` (new)
**Kyun:** Signal aur chart do alag formulas se aa rahe the — mismatch possible. Audit baseline ke 4 DUP-INDICATOR FAILs yahi hain.
**Depends on:** Task 1 (audit script — FAIL count verify karne ke liye)

---

## 2026-07-07 — Stable Architecture: Rule 6B/6C baked + architecture_audit.py + pre-commit hook (Tasks 0, 1, 1B)
**Status:** DONE — commit `4a42e1f`. Hook verified: deliberate `_rsi()` violation → commit BLOCKED; clean commit → PASS. Baseline: 7 FAIL, 1 WARN (`_TOOLS/ARCH_AUDIT_REPORT.md`) — 4 DUP-INDICATOR → Task 2; 2 RAW-ORDER + 1 BACKTEST-RISK → Task 3; 1 STATE-PERSIST WARN (universe_trader `_state`). VPS pe hook ki zaroorat nahi (VPS git-archive sync hai, commits wahan nahi hote).
**Kya:** Task-list (Desktop/claude-code-task-list.md) ke pehle 3 tasks: (0) Rule 6B "duplicate mat karo extend karo" + Rule 6C "ADR likho" CLAUDE.md Critical Rules mein permanently baked; (1) `_TOOLS/architecture_audit.py` — pure static analysis (AST/regex, no LLM): raw broker order-calls, inline risk-checks, duplicate indicators, non-persisted state, backtest risk-bypass detect kare; `--staged-only`/`--report` flags, exit 1 on FAIL; (1B) `.git/hooks/pre-commit` wire — har commit pe audit auto-chale, FAIL pe commit block.
**Layer:** infra / validation
**Files:** `CLAUDE.md`, `_TOOLS/architecture_audit.py` (new), `scripts/pre-commit-architecture-audit.sh` (new), `.git/hooks/pre-commit`
**Kyun:** Baar-baar duplicate-function bug family (indicator/risk-check/order-call ke do "sach" diverge hote hain — TRAP #77/#84 shape). Mechanical enforcement chahiye jo AI ke yaad rakhne pe depend na kare.
**Depends on:** nothing

---

## 2026-07-07 — Dhan hands-off + max-premium filter + 3-SL-systems merge + premium-chart persistence + exit-reason ₹
**Status:** DONE — deployed to VPS + verified (md5 local==remote, py_compile 3.12.3, algo-dashboard/algo-monitor/data-downloader restarted clean, zero open positions, no code errors in logs). #2 (3-dot menu) still pending a live repro.
**Kya:** 6-issue user worklist.
(1) **#1 Dhan hands-off** — `broker_sync._run_untracked_scan()` was auto-adopting the user's MANUAL Dhan trades into order_store, after which `pos_monitor` squared them off. Algo trades on Kite (`default_broker=kite`); Dhan is manual/data-only. Skip Dhan entirely in the scan + guard in `_handle_untracked()`. See LESSONS.md TRAP #97.
(2) **#4 Per-index max-premium entry filter** — `risk_gate.max_premium_config()`/`max_premium_cap_for()` (keys `max_premium_nifty/banknifty/stock`); `strategy_safety.gate_entry()` blocks an option entry whose per-unit premium exceeds that index's cap (BANKNIFTY's expensive premium hits a fixed-₹ SL in seconds). NSE_FNO only, blank/0=off. RMS "🚫 Max Premium Entry Cap" card.
(3) **#3 SL merge** — three overlapping per-trade SL systems (Legacy fixed-₹ / Dropdown type+value / Aggressive per-lot trail) merged into ONE mode selector. `risk_gate.default_sl_profile()` → (enabled, mode) drives both entry-time tags (`default_instrument_sl_tags`) and the monitor-time aggressive profile (`default_target_sl_config`). Applies to NEW trades only — aggressive scoped by an `AGGR_TSL` entry marker so a mid-day switch never grabs open positions; legacy/dropdown are entry-tagged so open positions keep their old SL. Backward-compat: a bare `default_sl_rs` infers legacy (SL only, no phantom TP) so the live SL isn't silently dropped. 3️⃣ Per-Instrument Lock + 4️⃣ KILL-ALL floors stay separate.
(4) **#5 Premium charts persist** — `/api/trade-chart-data` resolves the REAL historical sec_id from order_store (dhan_master gave nearest-LIVE-expiry = wrong for expired contracts), write-through saves each fetched series to `data/trade_ohlc/` keyed by raw Dhan epoch (TZ-unambiguous), disk fallback when Dhan won't serve an expired contract. `auto_data_downloader.py` switched to the same epoch-key format. Going-forward only.
(5) **#6 Exit Reason ₹** — `DEFAULT_TSL_SL/TARGET` tags carry the ₹ level; `_exitReasonBadge` parses SL/TP amount from SL_HIT/TP_HIT/DEFAULT_TSL tags ("🛡️ Default SL ₹2,000").
**Layer:** broker / execution / ui / config
**Files:** `broker_sync.py` (untracked-scan Dhan skip), `risk_gate.py` (`max_premium_*`, `default_sl_profile`, `default_target_sl_config` gate, `default_instrument_sl_tags` mode-driven), `strategy_safety.py` (premium filter in `gate_entry`), `trader_dashboard.py` (chart sec_id/persist/fallback, DEFAULT_TSL reason ₹, `_exitReasonAmt`/badge, aggressive `AGGR_TSL` scoping), `templates/index.html` (merged SL card + mode JS, max-prem card, exit-reason badge), `auto_data_downloader.py` (epoch-key bars), `LESSONS.md` (TRAP #97).
**Kyun:** User reported the app closing his manual Dhan trades, three confusing overlapping default-SL systems, expensive-BANKNIFTY-premium instant SL, missing premium charts for expired contracts, and exit reasons not showing the SL amount. Discussed each (MCQ decisions), mockup-approved the SL merge, built + VPS-deployed. Effective VPS mode resolved to Aggressive ON (config had `default_tsl_enabled=true`); user to verify/choose mode + set caps in RMS tab. Kite token was expired at deploy time (daily refresh, unrelated).
**Depends on:** nothing

## 2026-07-03 — Trailing SL spike-guard + RSI restart-recovery/duplicate-file cleanup
**Status:** DONE (code changes local + compile-verified; NOT yet deployed to VPS/restarted)
**Kya:** (1) Added a 2-reading confirmed peak/trough (`CONF_MAX_LTP`/`CONF_MIN_LTP`/`PREV_LTP`) feeding only the `trailing_pt` SL/TP ratchet in `_pos_monitor_check_one`/`_generic_px`, so a single spike/stale tick can no longer permanently ratchet a position's trailing SL. (2) Ported the TRAP #76 restart-recovery fix into `_TRADERS/01_rsi_v1.py` (the file actually wired to `rsi`/`rsi_v1` in `STRATEGIES` — the earlier fix had landed in `_TRADERS/rsi_trader.py`, a duplicate that was never run), added per-cycle order_store re-validation, inlined `_compute_rsi()` into `_TOOLS/backtest_engine.py` to drop its only dependency on the duplicate file, then deleted `_TRADERS/rsi_trader.py` outright. Fixed the `STRATEGIES["rsi"]["grep"]` / `health_check.py TRADER_SCRIPTS["rsi"]` mismatches that both still pointed at the (now-deleted) file.
**Layer:** strategy / execution / infra
**Files:** `trader_dashboard.py` (`_pos_monitor_check_one`, `_generic_px`, `STRATEGIES["rsi"]`, `_proc_cmdline` docstring), `_TRADERS/01_rsi_v1.py` (`_recover_rsi_state()` + per-cycle re-validation), `_TRADERS/rsi_trader.py` (deleted), `_TOOLS/backtest_engine.py` (inlined `_compute_rsi()`), `health_check.py` (`TRADER_SCRIPTS["rsi"]`), `CLAUDE.md` (Files table + Critical Rule 6 correction), `LESSONS.md` (TRAP #83-84)
**Kyun:** User asked for a pre-money-risk review of the Gemini-built Trailing Points SL, and asked why yesterday's RSI session showed a symbol re-bought right after being sold with matching ₹0 phantom P&L rows. Root-caused both to real, verifiable bugs (not display glitches) — see LESSONS.md TRAP #83-84 for full chains.
**Depends on:** nothing — but deploying either fix needs `algo-dashboard`/`algo-monitor` restarted (SL fix) and `rsi_v1` restarted (recovery fix), both requiring a zero-open-position check first per this project's standing rule.

---

## 2026-07-02 — Per-Instrument Trailing Lock redesigned to match KILL-ALL's arm+gap+confirm pattern; shared state machine extracted into risk_gate.py
**Status:** DONE (deployed, algo-dashboard + algo-monitor restarted — 0 open positions confirmed; hit + fixed a live UnboundLocalError mid-deploy, see TRAP #82)
**Kya:** User's 4-point request on Section 3 of the Risk tab, clarified via a short back-and-forth (user was confused between account-level vs per-instrument semantics):
1. Renamed "Account-Level Trailing Lock" → "Per-Instrument Trailing Lock" — removed the Aggregate/Total-Portfolio mode entirely (that concept now lives solely in the KILL-ALL Profit Floor, Section 4).
2. Added the missing "Arm ₹ (base)" concept the user was asking about — per-instrument now arms only after ITS OWN unrealized P&L first crosses an arm threshold, same convention as the account-level system.
3. Removed the "Multi trade — % of peak" field.
4. Both systems (account-level KILL-FLOOR + per-instrument) now share the exact same design: Enabled toggle + Arm ₹ + Gap ₹ + Confirm sec, confirmed via AskUserQuestion (confirm-timer=yes, entry-block scope=this-position-only per TRAP #77, Enabled=explicit dropdown).
**Layer:** strategy / execution / ui / config
**Files:**
- `risk_gate.py` — new `per_instrument_lock_config()` + shared `advance_trailing_lock(state, mtm, arm_rs, gap_rs, confirm_secs, now_ts, mtm_unreliable)` pure state-machine function (arm/peak/floor/breach-confirm/fire), used by BOTH the account-level kill-floor and the new per-instrument lock — extracted specifically so the two can't independently drift the way TRAP #77 found.
- `trader_dashboard.py` — Kill-Floor block refactored to call the shared function (was inline-duplicated); per-instrument block fully rewritten from the old flat-₹/%-of-peak design (`_pos_peaks`, `_trail_rs`, `_trail_pct`, `_lock_mode`) to per-position `advance_trailing_lock()` state stored in `_pos_lock_state` (disk-persisted to `data/pos_lock_state.json`, replacing `pos_peaks.json`); new `/api/per-instrument-lock-status` endpoint (mirrors `/api/kill-floor-status`); `/api/peak-pnl-history`'s dashed floor-line now sources from the Kill-Floor's own `gap_rs` instead of the removed aggregate-lock keys.
- `templates/index.html` — Section 3 HTML rebuilt to match Section 4's exact field layout (Enabled/Arm/Gap/Confirm); new `per-instrument-live` status box + `perInstrumentLockStatusPoll()` JS (mirrors `killfloor-live`/`killFloorStatusPoll()`); dead `updateTrailingLockTip()` + its now-orphaned HTML removed; `saveRiskConfig()`/load wiring switched to the new `per_instrument_lock_*` config keys.
**Kyun:** User's own words: "KILL-ALL Profit Floor me jaise aap ne base amt diya ki base ho, gap from peak itene amt se ho to nikal jaye waise hi do ban jayenge eak account level pe aur eak trade level pe" — wanted one proven design (the KILL-FLOOR's arm+gap+confirm+2-reading-peak anti-misfire pattern) applied at BOTH scopes, not two different designs.
**Depends on:** TRAP #77 (per-instrument must not write the account-wide entry-block flag), the KILL-ALL feature note (2026-07-02) for the underlying state-machine design this reuses.

---

## 2026-07-02 — Notes UI reorganized, note-preview modal redesigned, Exit Reason robustified + documented, Cumulative column + sort-arrow cleanup
**Status:** DONE (deployed, algo-dashboard + algo-monitor restarted — 0 open positions confirmed, both times, including a re-deploy after a silent scp under-write on the first attempt matching LESSONS.md TRAP #27/#69)
**Kya:** User's Priority-2-followup list, all items done in one pass:
(1) **Notes column removed** from COMPLETED_COLS_DEF/OPEN_COLS_DEF (was already redundant — the ⋮ actions dropdown already had "Edit Note", and the row-render code already had a graceful fallback showing the note preview under Symbol whenever the Note column is off). Stale localStorage column-prefs with an old 'note' entry are now filtered out on load so it can't linger as an empty dead column for existing users.
(2) **"Show Notes" toggle** added next to 🧾 Reconcile vs Broker on the Orders & P&L tab (new `global-notes-toggle-2`, synced in both directions with the pre-existing Calendar-tab checkbox via one shared `toggleAllNotes()`/`initGlobalNotesToggle()`).
(3) **Note preview modal redesigned** — `openNoteModal()` now takes just an id and self-looks-up the full trade object (was passing note/imgs as URL-encoded onclick params, fragile + no access to other fields). New metadata header (P&L, Points, Duration, Entry/Exit Time, Tax, Qty, Entry Px) shown above the note text. New ◀/▶ Prev/Next navigation across ALL of today's trades (completed + open), chronologically ordered by exit/entry time regardless of the table's current sort. Image display + delete already existed (`_renderNoteImgs`/`deleteNoteImg`) — confirmed working, no changes needed there.
(4) **Exit Reason made robust** — `order_store.py`'s `_EXIT_REASON_PREFIXES` whitelist was missing several real, currently-in-use reasons (a raw tag not matching ANY listed prefix silently returns "" — the column shows "—" even though the app DOES know exactly why the trade exited). Grep-audited every actual `extra_tags=[...]`/`tag=...`/`reason=...` call site across trader_dashboard.py/webhook_executor.py/broker_sync.py and added: KILL_FLOOR, TRAILING_PROFIT_LOCK, RMS_PROFIT_TARGET, EXPIRY_EOD_SQUAREOFF, EXPIRY_ITM_SQUAREOFF, NO_PRICE_EMERGENCY_EXIT, IDX_TRAIL, GLOBAL_CAP, SQUAREOFF_315, EXTERNALLY_CLOSED, MANUAL_EXIT_BROKER. Frontend `_exitReasonBadge()` expanded to match 1:1 with clear labels/colors + a "(hedge pair)" note for `_GROUP`-suffixed reasons.
(5) **"Reasons For Exit" documentation** — new RMS tab sidebar entry (📖, under a new "Reference" group) listing every exit reason in 3 groups (universal pos_monitor reasons / webhook-only reasons / manual-broker reasons) with a plain-language explanation each, kept explicitly in-sync-by-comment with the backend whitelist and frontend badge function. Small dynamic strip shows which strategies are live RIGHT NOW and whether the webhook-only group is currently applicable.
(6) **Cumulative column** added next to Run-Down in Completed Trades — only populated when sorted by Exit Time ascending AND not grouped-by-symbol (any other state = blank "—", matching the user's explicit "warna blank ho jaye" requirement — a running total only means something in a fixed chronological order).
(7) **Sort-arrow cleanup** — the static "↕" shown on every unsorted column header (both Completed and Open tables) removed; only the currently-sorted column shows a small ▲/▼ triangle now, unsorted columns show just their name.
**Layer:** ui
**Files:** templates/index.html (Notes UI, note modal, Exit Reason badge/docs, Cumulative column, sort-arrow cleanup), order_store.py (_EXIT_REASON_PREFIXES expansion)
**Kyun:** User's Priority-2-followup ask, item by item — see conversation for full original wording.
**Depends on:** nothing new. Flagged (not fixed) in the same investigation: `updatePnl()`/`renderSingleTradeRow()`/`renderGroupedRow()` confirmed 100% dead code (early-return guard checks a tab id 'pnl' that doesn't exist) — spawned as a separate cleanup task, not touched here to keep this change scoped to what was asked.

---

## 2026-07-02 — Peak P&L graph was truncated to ~last 40 minutes (500-entry cap, fixed to 6000)
**Status:** DONE (deployed, algo-monitor restarted — market already closed, 0 open positions)
**Kya:** User reported "Today's Peak P&L graph looks like only last 1hr of data" + wanted the graph's MTM to clearly match Zerodha's gross (realized+unrealized) day P&L. Root cause found: `pos_monitor_loop` (in `trader_dashboard.py`, runs inside `algo-monitor`/`monitor_daemon.py`) appends one entry to `_peak_pnl_history` every ~5s cycle, then capped the in-memory list (and therefore what gets written to `data/peak_pnl_history.json`, which the graph API reads) at 500 entries — 500×5s ≈ 42 minutes, so the graph only ever showed the most recent ~40min slice of the trading day, discarding everything from 09:15 onward. The underlying MTM MATH itself was already correct (gross realized+unrealized, no brokerage/tax subtracted — matches Zerodha's own M2M display) — the bug was purely the graph's visible WINDOW, which explains why the second complaint ("gross MTM not clear") likely stems from the same root cause: only seeing a 40-min slice makes the day's actual shape/total invisible.
**Layer:** ui / config
**Files:** trader_dashboard.py (pos_monitor_loop's `_peak_pnl_history` cap: 500 → 6000)
**Kyun:** Market day is 09:15-15:30 = 375min; even at the fastest possible 5s/cycle that's ≤4500 entries — 6000 gives headroom. Frontend (`loadPeakGraph()`) and the API endpoint (`/api/peak-pnl-history`) already handle a full-day array correctly with zero windowing of their own — confirmed by code read, no changes needed there.
**Depends on:** nothing — fix is forward-looking only; today's already-truncated history before the restart is not retroactively recoverable (was already discarded before ever being written under the old cap).

---

## 2026-07-02 — RMS/Risk tab: Live Monitoring accordions moved INTO the sidebar (9 tabs total, no more bottom accordion block)
**Status:** DONE (deployed, algo-dashboard restarted — algo-monitor untouched)
**Kya:** User feedback on the v2 RMS layout — move Broker Balances/RMS Live Summary/Rate Limit Room/Per-Strategy Override out of the bottom collapsible accordion block and into the same sidebar as the 5 config tabs. Sidebar now has 2 header groups ("Global Settings" 5 tabs, "Live Monitoring" 4 tabs), all 9 as `settings-section` panels in one `folder-content` area — single consistent navigation pattern instead of two different UI patterns (tabs + accordion) on one screen. Dead-code cleanup in the same pass: `toggleAccordion()` JS function and 6 now-unused CSS rules (`.monitor-group`, `.monitor-title`, `.accordion-card` + its color variants, `.accordion-header*`, `.accordion-body`) removed — nothing referenced them anymore after the move. All ids (`broker-balances-content`, `rms-summary-content`, `killfloor-live`/`kf-floor`/`kf-peak`/`kf-status`, `rl-top-offenders`/`rl-events-body`, `risk-strategy-table`) unchanged, zero JS data-loading function changes.
**Layer:** ui
**Files:** templates/index.html
**Kyun:** User: "Live Monitoring waale options ko bhi global setting ke andar daal dijiye" — wanted one unified sidebar, not tabs+accordion split.
**Depends on:** same restart note as the earlier RMS v2 entry — Flask debug=False caches templates, algo-dashboard restart needed for template changes; algo-monitor/trading logic never touched, 0 open positions re-verified before this restart too.

---

## 2026-07-02 — RMS/Risk tab v2 layout (sidebar settings + collapsible monitoring accordions)
**Status:** DONE (deployed, algo-dashboard restarted — algo-monitor/trading logic untouched)
**Kya:** User ne Gemini se `rms_mockup.html` banwaya tha (folder-sidebar + rms-table row layout + bottom collapsible "Live Monitoring" accordions). Real dashboard ke Risk tab (flat card stack, 9 separate cards) ko is design se replace kiya — har existing field ID (`risk-global-pct`, `risk-killfloor-*`, etc.), har JS function (`renderRiskTab`, `saveRiskConfig`, `runReconcile`, `killFloorStatusPoll`, etc.), har data endpoint bilkul same rakha — sirf visual structure badla. 5-tab sidebar (Max Loss Protection / Capital Allocation / Shadow-Live / Auto Hedge / Liquidity Filter) + bottom 4 accordions (Broker Balances, RMS Live Summary + Kill-Floor live status, Rate Limit Room, Per-Strategy Override). Naye CSS classes collision-check karke add kiye (`.btn` jaisi already-existing class ko bilkul touch nahi kiya — mockup ka apna `.btn` clash karta, isliye `.rms-btn*` naam diye). Har `<div>/<table>/<tr>/<td>/<select>/<span>` tag count balance-verified before deploy.
**Layer:** ui
**Files:** templates/index.html (new CSS block + full RISK TAB HTML replace + `switchSettingsTab()`/`toggleAccordion()` JS)
**Kyun:** User apna RMS layout better dikhna chahta tha — mockup already ban chuka tha, sirf real dashboard mein wire karna tha.
**Depends on:** Flask `debug=False` → templates cached, isliye `algo-dashboard` restart zaroori tha (algo-monitor/trading process ko bilkul touch nahi kiya, 0 open positions confirm karke restart kiya).

---

## 2026-07-02 — KILL-ALL Profit Floor (account-level trailing kill-switch) — user-designed, real-data-calibrated, simulation-verified
**Status:** DONE (deployed; enabled=OFF by default — user RMS tab se ON karega)
**Kya:** Naya account-level kill-floor jo purane "aggregate" trailing lock ko REPLACE karta hai (user decision: ek hi account-level system, parallel nahi). Semantics user ke saath lock hue + aaj ke real trades pe calibrate (peak ₹5,193 / final ₹1,937 / ₹3,256 giveback): (a) **Arm ₹500** — confirmed MTM ye cross kare tab floor arm; (b) **Gap ₹1,500** — floor = confirmed_peak − gap (aaj ke data se: ₹500 gap premature kill karta ~₹1,000-1,500 pe, ₹1,500 multi-position flutter survive karke ₹3,693 pe lock karta = +₹1,756 better); (c) **₹1-fine ratchet** — har confirmed naye high pe floor upar, KABHI neeche nahi; (d) **Confirm 60s** — MTM lagatar floor ke neeche rahe tabhi fire (purane floor ke misfire ka root cause = spike pe turant fire); (e) **2-reading confirmed peak** — min(prev, current), ek bad tick kabhi peak inflate nahi kar sakta; (f) **bad-data freeze** — koi position ka price missing ho to us cycle kill na fire hota hai na timer aage badhta. Fire → har position `_pre_exit_guard` (webhook-claim + fresh flat-check) se hokar limit-order close (chase enabled), no-price legs `_pending_group_close` me queue, day-flag likhta hai jo AB har entry path block karta hai (naya `risk_gate.kill_floor_fired_today()` check `gating_status` me — pehle sirf webhook flag check karta tha, strategies nahi: ye gap bhi is build me band hua), alert-banner me red alert. State disk-persisted (`data/kill_floor_state.json`, same-day restore, `breach_since` deliberately NOT restored — restart ke baad timer conservative fresh start). RMS tab me naya card: Enable/Arm/Gap/Confirm inputs + bada live display (FLOOR ABHI / CONFIRMED PEAK / STATUS), `/api/kill-floor-status` se 5s poll. UI save payload me naye keys zaroori the (POST `_risk` wholesale replace karta hai — bina iske koi bhi risk-save inhe ura deta).
**Verification:** 5-scenario offline simulation of the exact state machine — (1) aaj ka real shape: ₹3,200 pe exit vs ₹1,937 actual (+₹1,263); (2) single spike peak inflate nahi karta; (3) short whipsaw fire nahi karta; (4) 20 consecutive bad-data readings fire nahi karte; (5) floor kabhi neeche nahi aata. Saare PASS. Bonus fix: pos_monitor_loop ke outer catch-all print me missing flush=True (TRAP #56 ka exact silent-death mode abhi bhi maujood tha).
**Layer:** execution / ui / config
**Files:** risk_gate.py (kill_floor_config, kill_floor_fired_today, gating_status check), trader_dashboard.py (engine in pos_monitor_loop replacing aggregate branch, _kf_state persistence, /api/kill-floor-status, _mtm_unreliable tracking, flush fix), templates/index.html (RMS card + save/load JS + live status poll)
**Kyun:** User priority-1 request — "Kill All Position + trailing profit lock, proactively, purane floor ke misfire se bachte hue". Config keys: _risk.global.kill_floor_{enabled,arm_rs,gap_rs,confirm_secs}.
**Depends on:** algo-monitor restart (done) — enabled=false default, user turns on from RMS tab.

---

## 2026-07-02 — Kite (Zerodha) manual-order auto-adoption + auto-reconcile timer (user-requested, live restart deferred)
**Status:** CODE DONE + DEPLOYED to VPS. NOT active yet — algo-monitor (running process) has broker_sync.py cached in memory from its earlier import; these changes only take effect on that process's next restart. Deliberately NOT restarted (user instruction — ARS_CHAIN_V1 has a live position running right now).
**Kya:** User's real workflow: sometimes places SL/Target as a manual LIMIT order directly on Zerodha based on price action, wants the app to pick it up automatically (SL/Target protection) without a phantom/duplicate-order risk. Investigation found: (a) manual EXIT on an ALGO-opened position — already automatic (ghost-sync, ✅ working since TRAP #59-61). (b) A genuinely FRESH manual entry on Kite — was alert-ONLY before today (`_handle_untracked`'s Kite branch never auto-adopted, unlike Dhan, out of caution re: TRAP #13's string-guessing risk). Fixed properly: **new `resolve_dhan_from_kite_symbol()`** (brokers/kite_broker.py) — reverse of the existing forward resolver, uses Kite's OWN structured instrument fields (name/expiry/strike/instrument_type from `kite.instruments("NFO")`, matched by exact tradingsymbol) cross-matched against Dhan's scrip master (`dhan_master._options_cache`, same 4 structured fields) — exact match discipline in both directions, never a string-guess. `_handle_untracked()`'s Kite branch now auto-adopts (scoped to NFO/options only, matching this system's SL/TP/hedge/RMS model) when resolution is a confident exact match; falls back to alert-only otherwise (never guesses). **Also added `reconcile_if_due()`** (own 180s cooldown, in `broker_sync.py`) — auto version of the existing "🧾 Reconcile vs Broker" button, wired into `pos_monitor_loop` right next to the other 2 scans — catches a manual entry+exit round-trip that both happen inside one untracked-scan gap (button stays fully available for on-demand use too, per user's explicit "dono chahiye" ask).
**Layer:** broker / execution
**Files:** brokers/kite_broker.py (resolve_dhan_from_kite_symbol + KiteBroker.resolve_dhan), broker_sync.py (_handle_untracked Kite branch, reconcile_if_due, _RECONCILE_INTERVAL), trader_dashboard.py (pos_monitor_loop wiring)
**Kyun:** User's actual live broker (confirmed via nifty_config.json _risk.global.default_broker) is Kite, not Dhan — this gap directly affected their real workflow.
**Depends on:** requires algo-monitor restart to activate — user's call on timing, ARS_CHAIN_V1's current open position stays on the OLD (alert-only) behavior until then.

---

## 2026-07-02 — nifty_ema_trader.py candle-fetch DH-904 fix (dhan_rate_limiter wired in)
**Status:** DONE (code fixed + deployed to VPS; NOT restarted — user explicit instruction, ARS_CHAIN_V1 has live positions running, restart deferred to user's call)
**Kya:** `fetch_candles()` was calling Dhan's /v2/charts/intraday directly with zero rate-limiting — every symbol in ema_v1's watchlist hit Dhan back-to-back every scan cycle, causing a DH-904 429 storm (LT/MARUTI/HINDUNILVR/ITC/ADANIENT/SUNPHARMA/TITAN/ULTRACEMCO all failing in the same second, seen live in the dashboard log). This is a DIFFERENT Dhan endpoint than today's earlier P7 LTP-poller fix (/v2/marketfeed/ltp) — that fix never touched candle fetches. range_trader.py's equivalent candle-fetch already routes through dhan_rate_limiter (acquire("candle") + note_429() on 429); nifty_ema_trader.py never got that treatment. Same 2-line fix applied here.
**Layer:** broker
**Files:** _TRADERS/nifty_ema_trader.py
**Kyun:** User spotted the DH-904 spam live in the dashboard log, asked if it was already fixed by today's LTP work — it wasn't (different endpoint), fixed on the spot.
**Depends on:** nothing — file deployed but process (currently not even running) will only pick this up on its NEXT restart, whenever the user calls for it.

---

## 2026-07-02 — range_trader.py last_day=None bug fixed (TRAP #28's fix was silently undoing itself) — LIVE process, restarted with explicit user go-ahead
**Status:** DONE (deployed + restarted, zero open positions confirmed before AND after)
**Kya:** Same bug as rsi_trader/universe_trader (fixed earlier today) — `last_day = None` before the main loop meant the loop's OWN "new day" check fired on the very first iteration after every restart, wiping _recover_state_from_order_store()'s just-populated _state right back to flat. VPS log proof: 2026-07-01 11:24:29 "[RECOVER] re-attached 1 open position" immediately followed by "New trading day — resetting state". This is the LIVE ARS_CHAIN_V1 process — user explicitly confirmed go-ahead after I verified zero open positions in order_store first. Fix: seed `last_day = ist_now().date()` right after recovery instead of None.
**Layer:** execution
**Files:** _TRADERS/range_trader.py
**Kyun:** User asked me to verify the "data survives restart" claim end-to-end; found this while porting the same recovery pattern to rsi_trader/universe_trader.
**Depends on:** nothing (fix is 1-line, same as the other two files)

---

## 2026-07-02 — rsi_trader/universe_trader restart-recovery (TRAP #28 ported) + last_day seeding bug found+fixed
**Status:** DONE (deployed — neither process was running at fix time, so zero live impact)
**Kya:** rsi_trader.py aur universe_trader.py dono me _state (positions/active_opts/trades_today ya _state dict) restart pe order_store se rebuild nahi hoti thi — sirf range_trader.py ko TRAP #28 mila tha 2026-06-29, ye dono chhoot gaye the. Naya `_recover_rsi_state()` / `_recover_state_from_order_store()` add kiya, dono ne CE/PE-suffix se LONG/SHORT derive karna aur (universe ke liye) equity-route BUY/SELL entry-side bhi handle karna cover kiya. **Isi ke saath ek naya, zyada serious bug mila:** dono files (aur range_trader.py bhi, jo ABHI LIVE hai) me `last_day/last_date = None` set hota tha loop se pehle — jiski wajah se pehli hi loop-iteration turant "New trading day" reset trigger karti thi, jo recovery ne abhi populate kiya tha use turant wapas khali kar deti thi. VPS log se confirm kiya (ARS_CHAIN_V1.log 2026-07-01 11:24:29): "[RECOVER] re-attached 1 open position" ke agli hi line pe "New trading day — resetting state". rsi_trader/universe_trader me `last_date`/`last_day` ko turant `ist_now().date()` se seed kar diya (recovery ke turant baad) — range_trader.py ka wahi fix pending hai (live process, alag se permission maang raha hoon).
**Layer:** execution
**Files:** _TRADERS/rsi_trader.py, _TRADERS/universe_trader.py
**Kyun:** User ne kal ke gap-report ke baad turant fix karne ko kaha (dono processes is waqt band the, safe tha).
**Depends on:** range_trader.py ka same `last_day=None` bug — separate fix, user go-ahead pending (live process)

---

## 2026-07-02 — P6 ownership-desync audit findings #1-5 all fixed
**Status:** DONE (deployed)
**Kya:** Kal ke audit ne 5 jagah dhoondi thi jahan exit order fresh broker flat-check ke bina fire hota tha (TRAP #44/#73 family, manual close ke saath race). Sab 5 fix: (1) trailing-lock squareoff (per-instrument + aggregate, dono) — naya shared `_pre_exit_guard()` helper, `_do_squareoff` bhi isi se refactor kiya (duplicate logic hataya); (2) webhook_executor `_do_exit` layer-2 — is_flat() → is_flat_fresh(); (3) 3:15 exit-all — range_trader/rsi_trader/universe_trader teeno me flat-check add + range_trader ka dead duplicate elif hataya; (4) universe_trader FLIP-close — flat-check add; (5) manual UI close (/api/close-position, /api/close-position-group dono isi se) — flat-check add, already-flat pe order_store externally_closed mark karta hai, koi fabricated row nahi.
**Layer:** execution
**Files:** trader_dashboard.py, webhook_executor.py, _TRADERS/range_trader.py, _TRADERS/rsi_trader.py, _TRADERS/universe_trader.py
**Kyun:** User ne kal ke P6 audit report ke baad turant fix karne ko kaha — "report only" tha, ab sab live band ho gaye.
**Depends on:** nothing (broker_sync.is_flat_fresh already P3 me bana tha)

---

## 2026-07-02 — Batched LTP poller (ltp_poller.py) + _rest_ltp_fallback rate-limiter wiring
**Status:** DONE (deployed)
**Kya:** Naya ltp_poller.py — algo-monitor me daemon thread, har 1.5s me EK batched /v2/marketfeed/ltp call (sab open positions + NIFTY/BANKNIFTY spot, segment-grouped), results shared_ltp_cache.put_many() se sab processes ko. _rest_ltp_fallback ab shared cache first + dhan_rate_limiter.acquire/note_429 through (pehle throttle se puri tarah invisible tha, apna private cache tha). dhan_broker.quote() pehle se cache-first tha — ab poller us cache ko warm rakhta hai. Direct REST sirf cache-miss one-off (naye contract entry-time) ke liye bacha hai — deliberately, warna entries block ho jati.
**Layer:** broker / infra
**Files:** ltp_poller.py (new), shared_ltp_cache.py (put_many), trader_dashboard.py (_rest_ltp_fallback), monitor_daemon.py (start)
**Kyun:** Worklist Priority 7 — N open positions = N separate 1-req/sec calls; Dhan 1000 symbols/call allow karta hai.
**Depends on:** nothing

---

## 2026-07-02 — Hedge-sibling close: 35s-stale is_flat() → fresh is_flat_fresh() (TRAP #73 last open path)
**Status:** DONE (deployed)
**Kya:** _do_squareoff ka pre-exit flat check ab broker_sync.is_flat_fresh() use karta hai — 5s se purana positions data kabhi trust nahi, fresh broker.positions() fetch (shared cache refresh hota hai to EOD/group burst me ek hi API call). Sibling-close recursion _do_squareoff se hi jati hai to hedge leg bhi covered.
**Layer:** execution
**Files:** broker_sync.py (new is_flat_fresh), trader_dashboard.py (_do_squareoff)
**Kyun:** Worklist Priority 3 — dono hedge legs paas-paas manually close hue to 35s stale cache sibling-close ko already-flat leg pe live order fire karne deta tha (TRAP #73 shape, ye path chhut gaya tha).
**Depends on:** nothing

---

## 2026-07-02 — _pending_group_close queue disk-persist (restart-safe forced hedge-close)
**Status:** DONE (deployed)
**Kya:** _pending_group_close (hedge forced-retry queue) ab data/pending_group_close.json me persist — har add/pop pe write, startup pe same-day restore (loud recovery log), day-rollover pe clear. Keys str-normalized.
**Layer:** execution
**Files:** trader_dashboard.py
**Kyun:** Worklist Priority 4 — restart ke waqt queued leg apni scheduled protection chupchaap kho deta tha (no retry, no alert).
**Depends on:** nothing

---

## 2026-07-02 — Per-instrument trailing lock: account-wide entry-block flag removed + _pos_peaks disk persistence
**Status:** DONE (deployed)
**Kya:** (a) per_instrument mode me single position ka floor fire hone pe ab day-level trailing_lock_fired flag NAHI likha jata (wo flag webhook _do_entry se PURE account ki new entries block karta tha — per-instrument mode ka point hi khatam). User decision: option (a), koi block nahi — fired floor = closed resolved event. (b) _pos_peaks (per-position peak tracker) ab data/pos_peaks.json me persist hota hai (har cycle write, startup pe same-day restore, day-rollover pe clear) — mid-day dashboard restart pe trailing-lock memory zero hone ka gap band (TRAP #38 ka per-instrument equivalent).
**Layer:** execution / ui
**Files:** trader_dashboard.py
**Kyun:** Worklist Priority 2 — live confirmed: ek instrument ka floor fire → poore account ki entries blocked, per-instrument mode chunne ki wajah hi defeat.
**Depends on:** nothing

---

## 2026-07-02 — TRAP #74: order-chase duplicate-order guard (terminal-status + cancel_ok gating)
**Status:** DONE (user review + VPS deploy pending)
**Kya:** smart_order chase loop — manual/external cancel ke baad duplicate order re-place hone ka path band; chase ka self-abort bug (apna hi cancel "REJECTED" samajh lena) fix; Dhan EXPIRED/PART_TRADED + Kite partial-fill statuses ab distinctly handled.
**Layer:** execution / broker
**Files:** smart_order.py, brokers/dhan_broker.py, brokers/kite_broker.py, brokers/base_broker.py, LESSONS.md (TRAP #74)
**Kyun:** Worklist Priority 1 — "MARUTI duplicate" report. Code-trace ne dikhaya reported mechanism deployed code pe fire nahi ho sakta tha (get_fill CANCELLED→REJECTED collapse pehle se tha); asli gaps adjacent the — cancel_ok-unaware re-place, self-defeating chase, unmapped EXPIRED/PART_TRADED.
**Depends on:** nothing

---

## 2026-06-30 — Orders & P&L tab: 5 compounding bugs fixed (OPEN positions, trailing floor, NET panel)
**Status:** DONE
**Kya:** P&L tab me open positions nahi dikh rahi thi, trailing 30% floor kabhi fire nahi hoti thi, NET panel tiles "—" dikh rahe the, page refresh pe 10+ second freeze. Sab ek hi session me fix kiya.
**Layer:** broker, ui
**Files:** `order_store.py` (`_net_rows`), `trader_dashboard.py` (margin estimate, trailing peak restore), `templates/index.html` (JS bugs: `let _tot` scope, `</tfoot>` without opener, `_patchLtpCells` missing branch, TOTAL row as `<tfoot>`)

### BUG #1 — `_net_rows` phantom completed trades (order_store.py)
**Root cause:** OPEN-status rows (status="OPEN", live positions) ko netting algorithm mein daal rahe the. Ek SELL OPEN + hedge BUY OPEN (same trad_sym/strategy) pair ho ke phantom "completed trade" ban jaata tha — P&L=0, open positions blank.
**Fix:** `_OPEN_ST = {"open"}` set banao. `live_rows` alag karo pehle, sirf `closed_rows` par netting chalao. `live_rows` directly `opens` list mein.
**LESSONS.md TRAP #32 bana iske liye.**
**Downstream effect:** Trailing floor bhi is wajah se nahi chal raha tha — `_n_pos=0` se wrong branch execute hoti thi.

### BUG #2 — Trailing peak reset on restart (trader_dashboard.py)
**Root cause:** `_trailing_peak_pnl = 0.0` on every service restart. Agar service 09:50 pe peak ₹7246 dekha, phir 11:30 pe restart hua — peak 0 ho gayi, floor 0 → kabhi squareoff trigger nahi hua.
**Fix:** Startup pe `data/peak_pnl_history.json` padho. Agar aaj ki file hai → `max(v[1] for v in history)` se peak restore karo. Confirmed working: `[TRAILING-LOCK] Restored peak ₹7246 from 500 history entries after restart.`

### BUG #3 — Page freeze 10+ seconds on refresh
**Root cause:** `risk_gate._leg_capital()` har open position ke liye Dhan `/v2/margincalculator` API hit karta tha. 10 positions × 1 req/sec rate limit = 10+ second freeze. `/api/orders` route ka response await hota hai — is doran UI hang.
**Fix:** Local estimate: `margin = qty × price × multiplier (5x for SELL)`. Multiplier `risk_config.json` ke `margin_multiplier` key se. Zero Dhan API calls. Instant.

### BUG #4 — `let _tot` block-scope JS ReferenceError (index.html)
**Root cause:** `let _tot = {g:0,...}` declare tha `if(sortedCompleted.length){` block ke ANDAR, lekin reference tha bahar `window._realizedTot = _tot` line par. Classic JS block-scope trap — `let`/`const` sirf us block mein visible hote hain, `var` ki tarah nahi.
**Fix:** `let _tot` ko `if` block se BAHAR hoist kiya (ek line upar).
**Symptom:** Try-catch daala tha render ke around — error: `ReferenceError: _tot is not defined`.

### BUG #5 — `</tfoot>` without `<tfoot>` opener
**Root cause:** TOTAL row add karte waqt `</tbody></table>` ko `</tr></tfoot></table>` se replace kiya, but `<tfoot>` kabhi open nahi hua. Browser silently ignore karta hai malformed HTML.
**Fix:** `tfoot` open + close dono properly kiye.

### BUG #6 — `_patchLtpCells()` not called in no-positions branch
**Root cause:** Jab koi open position nahi hoti, ek branch `return` kar jaata tha bina `_patchLtpCells()` call kiye. NET panel tiles (REALIZED/UNREALIZED/NET TODAY) "—" dikha rahe the.
**Fix:** No-positions branch mein bhi `_patchLtpCells()` call karo.

### OPEN POSITIONS TOTAL ROW
**Kya bana:** Completed trades wali `<tfoot>` TOTAL row pattern open positions table mein bhi lagai. Pehle ek flex div tha jo columns se align nahi hota tha.
**How:** Per-strategy group ke end mein `<tfoot><tr>` banao. `activeOpenCols` ke har column ke liye `text-align` decide karo (right: entry_px/ltp/points/pnl/ret_pct/margin/run_up/run_down, center: entry_time/qty/chart/actions). `qty` aur `margin` sum show karo.

### LESSON: DB file name trap
**Real file:** `trades.db` (not `orders.db`). Table name: `orders`. Columns: id, ts, date, source, strategy, mode, broker, symbol, instrument, trad_sym, sec_id, segment, side, qty, price, correlation_id, broker_order_id, status, tags, product_type, group_id.
**Status values in DB:** `"COMPLETE"` / `"filled"` (closed), `"OPEN"` (live open position), `"paper"` (paper filled), `"rejected"` / `"cancelled"` / `"failed"` (dead — skip from netting).

### LESSON: Try-catch silently kills progress
**Problem:** `statsMetricsRender` ka `catch(e){ /* ignore */ }` aisi errors swallow karta tha jo pills update se pehle throw hoti. Debugging impossible.
**Fix rule:** Production code mein bhi `catch(e){ console.error('[context] error:', e); }` likho. `/* ignore */` kabhi mat karo — at least console me dikhao.

---

## 2026-06-23 — Script Library: paste-and-run custom strategies (TradingView-style)
**Status:** DONE (local build + backend verified) — VPS deploy PENDING (market open; do off-market)
**Kya:** "📌 Pine" tab → "📜 Script" library banao jisme Pine + Python + DSL-rule versions save hon. Koi bhi conforming Python/DSL script ko backtest dropdown se runnable banao (Pine reference-only). Plus ek master-prompt + contract doc jo kisi bhi AI ko de do to woh hamare syntax me code likhe.
**Layer:** strategy, ui, config
**Files:** `_TOOLS/backtest_engine.py` (new `_run_custom` generic dynamic-import runner + `_eval_loop` + dispatch in `run_backtest`), `trader_dashboard.py` (`/api/pine/save` lang+snapshot-ext+python→`strategies/<id>.py`+dsl→cfg parse, `_parse_dsl_block` helper, `api_backtest_run` dynamic dispatch), `templates/index.html` (Script rename, lang pills+auto-detect+confirm, file upload, Lang badge, Master-Prompt modal), `templates/backtest_chart.html` (skip `_`-keys in edit modal; dropdown auto-lists new config keys — free), `strategies/SCRIPT_CONTRACT.md` (NEW — DSL+Python spec + master prompt), `strategies/custom_rule_engine.py` (exists local, VPS pe deploy)
**Kyun:** User ko TradingView/QuantMan jaisa flow chahiye — ek file paste/upload → version-history library → dropdown → backtest. Abhi har strategy hardcoded (`_RUNNERS` + manual `strategies/*.py`). custom_rule_engine local pe bana tha, VPS pe missing.
**Reuse:** `custom_rule_engine._run_bb` (DSL exec), `_run_ema`/`_run_vwap_ema` patterns (eval-loop / backtest-call), data loaders (`ensure_and_load_symbol`/equity loaders/`_cfg_symbol`/`_fill`/`TF_MIN`), generic Edit modal + `collectModalFields` (already a key=value editor), `/api/config`-driven dropdown (auto-lists any nifty_config key)
**Depends on:** nothing (backtest cached data; no live Dhan)
**Build:** LOCAL first (VPS pe live trading — undisturbed), verify, phir off-market SCP deploy
**Verified (local, port 5098, NIFTY Apr-2026 cached):** python save → id `user_<slug>_v1` + `strategies/<id>.py` + nifty_config `{_module,_lang,active:false}`; dsl save → parsed `entry_long/exit_long/bb_window/sl_pct` + `_lang:dsl`; pine save → name from `strategy("...")`, NO script_id (reference-only); backtest python script = 1507 candles/36 trades/+904 pts; dsl script = 23 trades; delete cleans config+`.py`+snapshots. Frontend (Script tab pills/upload/Master-Prompt modal, lang badge, deep-link Run) = needs user's visual check after restart.
**Known caveat (follow-up):** evaluate() path recomputes indicators per-bar (O(n²)) — ~1-2 min for ~1500 bars over a buffered month; fine for 5m/short ranges, slow for 1m/multi-month. backtest(df,cfg) path (vectorized) avoids it.
**Pending:** off-market VPS deploy (incl. `strategies/custom_rule_engine.py` which is MISSING on VPS) + restart `algo-dashboard`; `sync_pine.py` extend for `.py`/`.rules` snapshots + `strategies/user_*.py`.

## 2026-06-21 — TradingView Webhook → auto order engine (Phase 1)
**Status:** DONE
**Kya:** TV Pine alert → Flask webhook → Dhan paper order. TV sirf thin signal (ENTRY/EXIT + direction) bhejta hai; strike select (ATM±offset), option type, qty, paper/live — sab Python config (`webhook_v1`) decide karta hai. Strategy ek hi jagah (Pine) → zero drift. Phase 1: receiver + executor + safety (max/day, no-entry-after).
**Layer:** broker, execution, config, infra
**Files:** `webhook_executor.py` (NEW — handle_signal ENTRY/EXIT, _wh_state, dedup, safety, status), `trader_dashboard.py` (`/api/webhook/tv` token-auth route + `/api/webhook/status` + auto_scheduler guard so non-process keys skip), `nifty_config.json` (`webhook_v1` block)
**Kyun:** Pine→Python full conversion me logic drift hoti thi (90% match ceiling, live fail). TV ko signal-generator banake execution Python me rakhne se drift khatam.
**Reuse:** `dhan_master.get_option_contract/get_equity_info`, `smart_order.execute` (paper==live parity), `brokers/dhan_broker.DhanBroker`, `dhan_feed`, log format `parse_pnl`-compatible (webhook trades P&L tab me auto dikhte hain)
**Verified:** offline (token expired today) — ENTRY→paper log→state→SL, dedup, reopen-block, EXIT netting; HTTP route 403 on bad/no token, 200 + paper order on good token (query + X-WH-Token header); `parse_pnl` → 1 closed trade ₹650. Live order test pending fresh Dhan token (rozana update).
**Depends on:** TradingView paid plan (webhook feature); fresh Dhan token for live-data path
**Next:** Phase 2 — monitor daemon thread (trailing SL premium/index, target, 3:15 force squareoff)

### Phase 2 (same day) — monitor daemon: trailing SL + target + 3:15 squareoff
**Status:** DONE
**Kya:** `webhook_executor.monitor_tick()` — har ~3s open webhook positions pe: (1) premium-mode trailing SL (option premium pe ratchet, default), (2) index-mode trailing (underlying ATR×mult, fallback 30pts), (3) fixed target/SL, (4) 3:15 force squareoff. Daemon `webhook_monitor_loop()` `trader_dashboard.py` __main__ me wired (auto_scheduler ke saath). Helpers: `_current_premium` (feed→REST), `_index_atr` (Wilder RMA, best-effort), `_do_entry` ab `entry_spot`+`idx_sl`+`idx_trail_dist` store karta hai. `_do_exit` reused for all exit reasons (TV_EXIT/TRAIL_SL/TARGET/IDX_TRAIL/SQUAREOFF_315).
**Verified:** offline stubs — premium SL ratchet 120→130→150 (no down-ratchet), TRAIL_SL exit @148; TARGET exit @195 (tgt 190); 3:15 squareoff; index-mode idx_sl trail 24470→24570 → IDX_TRAIL exit on pullback.
**Next:** Phase 3 — "🔗 Webhook" UI tab (mockup-first): config + secret token + TV alert template + live log + open positions.

### Phase 3 (same day) — Webhook UI tab + SELL default + Pine override
**Status:** DONE
**Kya:** `templates/index.html` me naya "🔗 Webhook" tab (mockup-first, approved). Sections: connection (webhook URL + secret token, copy/regenerate), execution config grid (strike/qty/trail/SL/target/squareoff), **Option mode Sell/Buy toggle** (Sell default — user selling karta; toggle opt_action + long/short type flip karta: SELL→long PE/short CE, BUY→long CE/short PE), **live strike LTP preview** (CE+PE, `/api/option-ltp` reuse, 4s poll, symbol picker — user ne maanga), TradingView alert template (`{{timenow}}`/`{{strategy.order.action}}` literal via `{% raw %}`, copy ENTRY/EXIT), open positions + live webhook log (`/api/webhook/status` poll).
**Executor changes:** `_DEFAULTS` ab SELL convention (long PE/short CE/opt_action SELL); **`_OVERRIDABLE`** — Pine alert JSON me bheja koi execution param (strike_offset/qty/sl_points/etc.) dashboard config ko override karta hai (`_merge_overrides`) → user ko Pine me set kiya value dobara dashboard me nahi daalna padta.
**Verified (browser, preview):** tab renders 0 console errors; config load/save incl. SELL↔BUY flip persisted; toggles work; LTP graceful degrade (token expired → clean note; VPS pe live); TV template literal placeholders; live POST `/api/webhook/tv` on running server → 403 bad token / executor reached on good token. Jinja `{{ }}` clash fixed via `{% raw %}`. Note: Flask debug=False → template cache; edits need server restart.
**Next:** Phase 4 — VPS deploy (deploy_vps.py + webhook_executor.py in file list; webhook_v1 block on VPS nifty_config.json since gitignored) + real TradingView alert wiring (paper) + optional UFW TV-IP whitelist.

### Phase 4 (same day) — VPS deploy + LIVE end-to-end verified (paper)
**Status:** DONE
**Kya:** webhook engine VPS pe deploy + live test. **Manual SCP** use kiya (deploy_vps.py STALE hai — REMOTE_DIR=`/root/code4` galat, asli dir `/root/CODE3B- TV BACKTEST ENGINE`; FILES me root-level trader files hain jo ab `_TRADERS/` me; SSH/SCP space-quoting bhi nahi). Pushed: `webhook_executor.py` (naya), `trader_dashboard.py`, `templates/index.html`. `webhook_v1` block VPS `nifty_config.json` me **merge** kiya (overwrite nahi — ARS_CHAIN_V1/ema_v1/rsi_v1 intact). `systemctl restart algo-dashboard`.
**Verified LIVE (VPS public IP, fresh token):** `POST /api/webhook/tv` good token → real paper order **SELL NIFTY-Jun2026-24000-PE @ 72.30** (spot 24013 → ATM 24000 PE, SL 102.30 = entry+30, qty 65); EXIT → closed @ 72.40. Bad token → 403. Auto-scheduler started ARS/rsi but **skipped webhook_v1** (process guard works). Monitor thread running (`[WEBHOOK] new trading day` in journal). `GET /` 200 (Jinja ok), webhook tab + `{{...}}` literal served.
**VPS facts (corrected):** dir `/root/CODE3B- TV BACKTEST ENGINE/`, venv `venv/bin/python`, service `algo-dashboard`, has own `data/config.json` token. CLAUDE.md `/root/code4` was stale → fixed.
**Pending (user):** TradingView alert wiring (Webhook URL + alert JSON from the tab) — user will do later. Optional: UFW TV-IP whitelist. deploy_vps.py proper fix (separate task).

### Phase 4b (same day) — HTTPS reachability via Caddy (TradingView "port 80 only" fix)
**Status:** DONE
**Problem:** TradingView HTTP sirf port 80 / HTTPS 443 allow karta hai — `http://...:5099` reject ("Only port 80 is allowed for HTTP"). User ne LAN IP (192.168.29.200) bhi diya tha (TV public chahiye).
**VPS infra (discovered):** port 80 = code2 Docker (busy); port 443 = **Caddy** already running, serving `https://72-61-173-32.nip.io` (nip.io → IP, auto Let's Encrypt valid cert) → `localhost:3737`.
**Fix (user-approved — shared infra):** `/etc/caddy/Caddyfile` me route add (backup liya: `Caddyfile.bak.<ts>`): `handle /algo/api/webhook/* { uri strip_prefix /algo; reverse_proxy localhost:5099 }` + catch-all `handle { reverse_proxy localhost:3737 }` (existing site untouched, verified root→200). `caddy validate` + `systemctl reload caddy` (graceful).
**TradingView webhook URL (LIVE, HTTPS):** `https://72-61-173-32.nip.io/algo/api/webhook/tv?token=<secret>` — tested: bad token 403, good token → executor. Only `/algo/api/webhook/*` proxied (dashboard surface minimal).
**Dashboard:** webhook tab ab `public_webhook_base` config (`https://72-61-173-32.nip.io/algo/api/webhook/tv`) se URL dikhata hai (location.origin fallback) — copy-ready, no LAN:5099 confusion. `index.html` `WH_PUBLIC_BASE` logic.

### Phase 4c (same day) — Pine TV alerts JSON + dual-dashboard sync
**Status:** DONE
**Pine alerts:** `range_chain.pine` ke `alert()` calls ab JSON bhejte hain (LONG→ENTRY/buy, SHORT→ENTRY/sell, exits→EXIT) + `whSymbol` input + 3:15 guarded EXIT. TV setup: "alert() function calls only" + Webhook URL (Message box ignore). Base = user's actual `Desktop/LATEST.txt` (not dashboard v5 — wo purana tha; `show_hlc`/`show_fc_fib` false preserved). Output: `Desktop/LATEST_webhook.txt` + repo `_PINE/range_chain.pine` + dashboard version.
**Pine store mismatch fix:** Dashboard "Pine > History" ek alag store hai (`_PINE/versions.json` + `v{N}.pine` snapshots), repo file se NAHI. User do dashboards chalata hai — **local (Windows, 192.168.29.200)** aur **VPS (72.61.173.32)** — jo diverge ho gaye the (local: VWAP+RSI v2; VPS: Ars webhook). UNION merge karke dono ko identical [1,4,5,6,7,8,9,10] kiya.
**`sync_pine.py` (NEW):** smart union-merge — VPS+local versions.json union, missing snapshots cross-pull, merged store dono pe push. Kabhi version drop nahi. "Pine ek jagah save karo (local) → `python sync_pine.py` → dono identical."
**LOCAL/VPS badge:** `index.html` header me hostname-based badge (🖥️ LOCAL `192.168.*`/`127.*` vs ☁️ VPS) + browser tab title prefix — dono dashboards same dikhte the, confusion fix.
**Encoding:** local dashboard ko `-X utf8` ke saath relaunch kiya (manual launch bina utf8 = emoji mojibake on Windows cp1252; `-X utf8` se versions.json UTF-8 read/write). launch.json me `-X utf8` already hai.

---

## 2026-06-20 — Reusable charting/pattern/zone module (_CHARTING)
**Status:** DONE
**Kya:** Candle pattern detection + zone/pivot builder + indicator calc (pandas-ta) ko `range_trader.py` se nikal ke `_CHARTING/` shared module mein daalna; `backtest_chart.html` ko generic plot-spec renderer banana (indicators/zones/pattern markers) taaki har naya strategy bina chart-code likhe visualize ho. Stretch goal: TV-parity itni achi ho ki Pine-first step skip ho sake.
**Layer:** validation, ui, strategy
**Files:** `_CHARTING/__init__.py`, `_CHARTING/patterns.py`, `_CHARTING/zones.py`, `_CHARTING/indicators.py`, `_CHARTING/plot_spec.py`, `_TRADERS/range_trader.py`, `_TOOLS/backtest_engine.py`, `templates/backtest_chart.html`
**Kyun:** Pine vs Python visual mismatch debug karne mein time barbaad hota tha — asal mein logic bug nahi, sirf Python chart mein zone/indicator draw nahi hota tha
**Depends on:** `pandas-ta` pip install; existing 90.2%/93% validate_strategy.py baseline (regression gate)

### Follow-up (same day) — 3 UX fixes after first review
1. **Picker slowness fixed** — "Add Indicator" ab client-side JS me compute hota hai (candles already page pe hain), server round-trip / data re-download nahi → instant. Server `/api/indicators/compute` route abhi bhi hai (fallback), par picker use nahi karta. VWAP ke liye `_candles_json` ab `volume` bhi bhejta hai.
2. **Strategy ke apne indicators by default** — RSI/EMA/VWAP runners already plot_spec me apne indicators emit karte hain (vwap → EMA(10)+VWAP auto).
3. **Oscillators alag panel (TV jaisa)** — registry me `overlay` flag: EMA/SMA/VWAP/BBANDS price chart pe (overlay=True), RSI/ATR apne bottom panel me (overlay=False, own priceScaleId + scaleMargins). Client RSI math server `ta` se 60 bars baad ~identical (cold-start sirf pehle ~40 bars, documented warm-up behaviour).

### Follow-up 2 (same day) — symbol picker + line styling + NIFTY download bug
4. **NIFTY redundant download fix** — `run_backtest()` ka unconditional `ensure_nifty_data()` hata diya; ab `_run_range` apni NIFTY ensure karta hai, rsi/ema/vwap apne symbol ki. Pehle TCS/POLYCAB (vwap) backtest bhi NIFTY days download karta tha ("downloading NIFTY 1/10" har run) — fixed.
5. **Symbol-aware rsi/ema** — naya `ensure_and_load_symbol(symbol, ...)` generic loader (NIFTY index store ya equity store, `cfg.symbol` se pick). rsi/ema ab kisi bhi symbol pe chalte hain (signal logic symbol-agnostic). `_buffered_from(date_from, symbol)` — equity ke liye flat 45-day warmup (NIFTY-cache extension sirf index ke liye). **Range NIFTY-only hi rehta** — pivot/zone/chain engine index-specific + 90.2% validated, equity generalization separate task.
6. **UI symbol picker har symbol-pickable strategy me** — `modal-multi-row` ab vwap/rsi/ema sab me (range nahi). `symbolPickable(type)` helper. `symbolsFor()` ab explicit `cfg.symbol` ko `symbols` array se priority deta hai.
7. **Indicator line color + thickness UI** — har drawn indicator (default + picker) ke liye 🎨 color picker + 1-4px thickness dropdown; live `applyOptions`, localStorage `bt_ind_styles` me persist (`_addIndicatorSeries` apply karta hai).

---

## 2026-06-16 — Project init + EMA/RSI strategies
**Status:** DONE
**Kya:** CODE3B banaya — EMA 9/20 + RSI(14) paper trader, Flask dashboard port 5099
**Layer:** strategy, ui, infra
**Files:** `nifty_ema_trader.py`, `rsi_trader.py`, `trader_dashboard.py`, `deploy_vps.py`
**Kyun:** CODE4 CLI-only tha, web dashboard chahiye tha
**Depends on:** Dhan JWT token, VPS running

---

## 2026-06-16 — Range Chain strategy
**Status:** DONE
**Kya:** PineScript `Ars_Auto_Rev_Chain_RANGE` ka Python conversion
**Layer:** strategy
**Files:** `range_trader.py`
**Kyun:** Main trading strategy yahi hai — live pe chalani hai
**Depends on:** `dhan_master.py` (option contracts)

---

## 2026-06-17 — Bug fixes batch (stale entry, startup exit, options price)
**Status:** DONE
**Kya:** 4 critical bugs fix — stale signal, fake startup trades, options ₹0 price, TATAMOTORS remove
**Layer:** strategy, execution
**Files:** `range_trader.py`
**Kyun:** Live pe jaane se pehle yeh bugs hote to bade loss hote
**Depends on:** nothing

---

## 2026-06-17 — P&L tab rebuild + Open Positions LTP
**Status:** DONE
**Kya:** Dashboard P&L tab full redesign — summary pills, open positions with live LTP, completed trades table
**Layer:** ui, broker
**Files:** `trader_dashboard.py`, `templates/index.html`
**Kyun:** Pehle P&L readable nahi tha, positions ka LTP nahi dikh raha tha
**Depends on:** Dhan `/v2/marketfeed/ltp`

---

## 2026-06-17 — Universe System (Phases 0–3)
**Status:** DONE
**Kya:** Best-in-class Nifty-50 scanner — broker abstraction, WebSocket feed, marketable-limit, universe engine
**Layer:** broker, execution, universe
**Files:** `brokers/base_broker.py`, `brokers/dhan_broker.py`, `dhan_feed.py`, `smart_order.py`, `universe.py`, `universe_trader.py`, `strategies/`
**Kyun:** yfinance slow + MARKET order slip — Dhan real-time feed + marketable-limit chahiye tha
**Depends on:** Dhan Data API subscription, `dhanhq` pkg

---

## 2026-06-17 — Pine→Python Validation (Phases 4–5)
**Status:** DONE
**Kya:** `validate_strategy.py` — TV "List of Trades" CSV vs engine signals % match score. 90.2% exact achieved.
**Layer:** validation
**Files:** `validate_strategy.py`, `ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md`
**Kyun:** Live pe jaane se pehle engine aur Pine 1:1 match zaroori tha
**Depends on:** `ACCURACY SCORE CLAUD/TEST 1/pine-logs UPDATE.csv`

---

## 2026-06-17 — Pine Version Control (`_PINE/` folder)
**Status:** DONE
**Kya:** `_PINE/` folder — canonical Pine files, git-tracked, ritual for paste→diff→sync→commit
**Layer:** strategy, infra
**Files:** `_PINE/range_chain.pine`, `_PINE/range_chain_zonelog.pine`, `_PINE/README.md`
**Kyun:** Pine files ad-hoc naam se padhi thi — versions track karna mushkil tha
**Depends on:** GitHub repo (`algo-trader.git`)

---

## PENDING — Phase 6 — Go Live
**Status:** PENDING
**Kya:** universe_v1 ko paper se live mode mein switch karna, ek manual order test karna pehle
**Layer:** execution, config
**Files:** `nifty_config.json`, Quick Order widget
**Kyun:** Phases 0-5 done, validation 90.2% — ab real money test
**Depends on:** Dhan account balance > ₹0, JWT token fresh (expires 24h)

---

## 2026-06-18 — Pine Version Manager (dashboard tab)
**Status:** DONE
**Kya:** Dashboard mein "📌 Pine" tab — script paste karo, strategy name auto-parse ho, version+timestamp assign ho, history dikhe
**Layer:** ui, infra
**Files:** `trader_dashboard.py` (2 routes), `templates/index.html` (tab + UI), `_PINE/versions.json` (new)
**Kyun:** Pine script baar baar badle — track karna mushkil; ek jagah paste karo aur confirm ho ki latest loaded hai
**Depends on:** `_PINE/` folder (already exists)

---

## PENDING — UI Polish (universe config tab, shadow badge, Quick Order bid/ask)
**Status:** PENDING
**Kya:** Dashboard mein universe config tab (abhi manual JSON), shadow badge on positions, live bid/ask in Quick Order
**Layer:** ui
**Files:** `trader_dashboard.py`, `templates/index.html`
**Kyun:** Non-blocking — live ke baad karna hai
**Depends on:** Phase 6 done

## 2026-06-22 — Dashboard: Orders+P&L merge, Quick Order CE/PE fix, dates+charts
**Status:** DONE
**Kya:**
- #3 Quick Order CE/PE confusion fix — ab CE/PE explicitly select hota hai (tick swatch), BUY/SELL usi selected leg pe chalta hai. Pehle hardcoded tha (BUY→PE, SELL→CE). Backend `api_manual_order` ab `opt_type` request se leta hai (legacy fallback retained).
- #4 P&L tab ko Orders tab me fold kiya (P&L tab + col-modal markup hata). 📒 Orders & P&L ab per-strategy summary pills + Gross/Tax(Zerodha charges)/Net columns dikhata hai. `calcCharges()` reuse.
- #2 Completed Trades me Date column; Open Positions me 📈 chart button (entry kahan hua). `order_store.trades_for` ab entry_date/exit_date deta hai. `openTradeChart()` optional date param leta hai.
- Orders tab 4s auto-refresh (DB-backed, no Dhan) — P&L ki jagah.
**Layer:** ui / data
**Files:** `templates/index.html`, `trader_dashboard.py`, `order_store.py`
**Kyun:** User feedback — quick order galat leg le raha tha; do tab same kaam; trade date + open-position entry visibility chahiye thi
**Pending:** #5 webhook reversal bug (TV reverse karta, Python `_do_entry` "position already open" pe block karke purani pakde rehta) — baad me. Validated: Jinja render + node --check + py_compile sab OK. VPS deploy pending.

## 2026-06-22 — Close zero-price fix, two-pass netting, phantom-position handling, RSI→order_store
**Status:** DONE
**Kya:**
- **Zero-price close fix** — `api_close_position` me `option_ltp` default 0.0; LTP fetch fail/429 pe close 0.00 record ho raha tha (SELL@71→exit@0 = jhootha profit). Ab 3x retry; na mile to record NAHI karta, error deta. Same `_dhan_live_fate()` verify manual-order + close dono me — Dhan 200=accepted (filled nahi); REJECTED ko phantom position nahi banata.
- **Two-pass netting** (`order_store.trades_for`) — Pass1: exact (source,strategy,trad_sym) round-trips; Pass2: bache opposite legs ko (mode,trad_sym) FIFO net (manual BUY se webhook/strategy SELL bhi close hoti). Rejected/cancelled/failed legs netting se excluded.
- **Phantom clear** — `/api/orders/book-close` + 🗑 button: stuck/phantom position ko ledger se hatao (offsetting leg @ entry price, pnl0, no real order).
- **RSI → order_store** — `_TRADERS/rsi_trader.py` ab entry + RSI-exit + 3:15-exit pe `order_store.record()` karta hai (source='strategy'), **actual option premium fetch karke** (`_opt_ltp`, pehle sirf underlying close logs hota tha). Isse RSI trades 'Orders & P&L' tab me dikhenge (range_trader/webhook ki tarah). ema/universe inactive — chhoda.
**Layer:** ui / data / strategy-engine
**Files:** `trader_dashboard.py`, `order_store.py`, `templates/index.html`, `_TRADERS/rsi_trader.py`
**Kyun:** User-reported: close zero price, manual close net nahi hota, live phantom positions, strategy entries P&L tab me nahi
**Verify:** RSI order_store recording next market session (9:10) pe live confirm hoga — abhi market band.

## 2026-06-22 — Webhook reversal fix (#5) + zero-leg data cleanup
**Status:** DONE
**Kya:**
- **Webhook reversal** (`webhook_executor._do_entry`) — pehle "position already open" pe naya ENTRY block hota tha → TV reverse karta, Python purani pakde rehta. Ab: opposite-direction ENTRY = REVERSAL (purani exit → nayi enter, atomic: exit fail to entry nahi), same-direction = ignore (pyramiding off). Pine unchanged (ek alert, Python reconcile). Unit-tested: buy→LONG, sell→reverse→SHORT, sell→ignored, 2 trades.
- **Zero-leg cleanup** — aaj ke 2 corrupt @0 close legs (24150-CE webhook, 24000-CE manual) ko Dhan intraday se asli exit premium (₹62.80 / ₹163.95) set kiya (one-off `_fix_zero_legs.py`, DB backup leke, script delete). Fake ~₹15,343 profit hata.
**Files:** `webhook_executor.py`
**Verify:** reversal unit-test pass + VPS deploy OK. Live reversal kal market me confirm hoga.

## 2026-07-01 — Peak-P&L day-rollover fix + critical pos_monitor_loop silent-break fix (datetime.utcnow() deprecation-commit fallout)
**Status:** DONE
**Kya:**
- **Peak-P&L stale-carryover fix** — user ne dekha ki naye din ki shuruaat me (zero trades) Peak/DD/30% floor purane din ke ₹7,916 dikha raha tha. Root cause: `pos_monitor_loop`'s day-rollover check apne hi abhi-abhi-likhe hue `peak_pnl_history.json` ka mtime check kar raha tha — hamesha "aaj" hi milta, reset kabhi fire nahi hota tha (process long-lived systemd service hai, roz restart nahi hota). Fix: naya module-level `_peak_day_str` explicit day-tracker, mtime-dependency hata di. TRAP #55.
- **CRITICAL — same restart ne ek alag, pehle se maujood bug expose kiya:** commit `3cbad3f` (isi session se ~10 min pehle, ek earlier session ne `datetime.utcnow()` deprecation fix kiya tha) ne 5 jagah imports galat kar diye the — `risk_gate.py` ke 3 functions (`_today_open`/`_today_realized_pnl`/`_strategy_day_pnl` — capital/daily-loss/concentration checks) NameError pe crash kar rahe the, aur `trader_dashboard.py` me `_trailing_lock_fired_today()` + trailing-lock flag-write ka `as _dtc` galti se `timezone` ko bind kar raha tha (`datetime` ko nahi) — ek jagah to poore `pos_monitor_loop` (SL/TP/EOD-squareoff wala loop) ko HAR CYCLE UnboundLocalError pe crash kara raha tha, bilkul silently (missing `flush=True` outer print pe). Dono bugs sirf isliye pehle nazar nahi aaye kyunki purana already-running process apna OLD in-memory code use kar raha tha — restart karte hi surface hue. Fix: sab 5 jagah sahi import wapas kiya. TRAP #56.
**Layer:** infra / broker / validation
**Files:** `trader_dashboard.py`, `risk_gate.py`
**Kyun:** User-reported cosmetic bug (peak P&L stale) → diagnosis ke dauraan ek zyada critical live-safety bug mil gaya (pos_monitor_loop poora silently down tha restart ke baad)
**Depends on:** nothing
**Verify:** VPS pe dono `algo-dashboard` + `algo-monitor` restart karke confirm kiya — `peak_pnl_history.json` ab clean ₹0 se start hota hai, koi error log nahi, `_trailing_lock_fired_today()` sahi kaam karta hai. Us waqt koi open position nahi thi (zero live-trading impact is baar).

## 2026-07-01 — Restart-risk scenario modeling → 2 preemptive fixes (mode-preservation + untracked-position scan)
**Status:** DONE
**Kya:** User ne pucha "restart bolun to kya toot sakta hai" — 4 restart-types (single strategy / algo-monitor / algo-dashboard / full VPS reboot) x 3 categories (data corruption / order flow / position mismatch) model kiya, code padh ke (guesswork nahi). 4 findings mile, user ne top-2 critical fix karne bola:
- **TRAP #57 — silent live→paper downgrade:** `auto_scheduler()` restart-recovery hardcoded `mode=paper` bhejta tha; `nifty_config.json` `mode` store hi nahi karta tha. Fix: `/api/start` ab `cfg[s]['mode']` bhi save karta hai; `auto_scheduler` usi ko read karke restore karta hai.
- **TRAP #58 — untracked live position:** `broker_sync.py` sirf ek direction check karta tha (DB-open-but-broker-flat). Ulta kabhi nahi (broker-open-but-DB-absent) — jo `smart_order.execute()`'s ~8s live-fill-poll window me SIGTERM (koi handler kahin nahi hai) se ban sakta hai. Fix: naya `broker_sync.untracked_scan_if_due()` — dono broker ke live positions seedhe poll karke order_store se diff karta hai; Dhan → auto-adopt (apna hi tradingSymbol/segment deta hai, guessing nahi), Kite → alert-only (reverse-symbol-guess nahi kiya, TRAP #13/#22 jaisa hi).
**Layer:** infra / broker / validation / risk-management
**Files:** `trader_dashboard.py`, `broker_sync.py`, `brokers/base_broker.py`, `brokers/dhan_broker.py`, `brokers/kite_broker.py`
**Kyun:** User khud VPS pe build karta hai (restart frequent) — preemptive modeling taaki live capital risk na aaye
**Depends on:** nothing
**Not done (user-scoped out for later):** #3 webhook profit-target lost on recovery (target:None), #4 nifty_config.json concurrent-write race (no atomic write)
**Verify:** VPS deploy + syntax-check + restart (no open positions) + confirmed clean logs, peak-pnl still healthy, zero errors in pos_monitor_loop for 2+ min post-restart. Untracked-scan correctly silent (nothing to find). Live "adopt a real orphan" path not yet exercised (no orphan existed to test against) — logic reviewed carefully but flagging as unverified-in-anger.

## 2026-07-01 — TRAP #58 live-confirmed same day + TRAP #59 (resolve_kite_symbol signature bug + quantity field bug) — real untracked position found and fixed while deploying
**Status:** DONE (detection + alerting) / PENDING (deeper structural fix, deferred)
**Kya:** ~1hr baad hi TRAP #58 ka mechanism REAL LIVE mila — user ne khud Zerodha screenshot bheja jisme RELIANCE-1310-CE (SELL 500) aur SUNPHARMA-1980-CE hedge (BUY 350) dashboard me nahi the lekin Zerodha me the. Root cause: restart nahi, **8-second fill-confirm timeout** — `smart_order.execute()` ne order place kiya, broker pe fill bhi ho gaya, lekin poll 8s me TRADED confirm nahi hua to `order_store.record()` kabhi chala hi nahi. Deploy karte waqt untracked-scan khud zero orphans dikha raha tha (jabki 2 the) — dusra bug mila: `resolve_kite_symbol(trad_sym)` sab jagah galat signature se call ho raha tha (real signature `(kite, trad_sym, sec_id)`, `kite` client missing) — TypeError silently swallow. Compounding: Kite field `net_quantity` nahi, `quantity` hai — dono `positions()` aur naya `positions_detailed()` galat field padh rahe the (hamesha 0). Teesra bug (verify ke dauraan mila): alert-writer `.get()` crash kar raha tha kyunki `downloader_alert.json` me mixed format (strings + dicts) hain — same bug `_write_naked_alert` (TRAP #53) me bhi tha, dono fix kiye.
**Layer:** broker / risk-management / validation
**Files:** `broker_sync.py`, `brokers/kite_broker.py`
**Kyun:** Live incident ke dauraan mila, turant fix + deploy kiya (RELIANCE position abhi bhi user ko manually handle karna pada — alert-only hai, auto-SL nahi)
**Depends on:** TRAP #58 (same-day, is session ka pehla fix)
**Not done (user ne deferred kiya, RELIANCE handle karne ke baad):** `smart_order.execute()` ka structural fix — order accept hote hi "pending" row likhna, phir confirm par update. Untracked-scan sirf after-the-fact catch karta hai; yeh fix root cause par lagta.
**Verify:** VPS pe deploy + restart, dry-run se independently detection verify kiya (restart se pehle), phir live confirm — dono orphans correctly UNTRACKED detect hue, alert successfully likha gaya (crash-fix ke baad). SUNPHARMA-1980 khud resolve ho gaya (position flat), RELIANCE abhi bhi open + untracked tha jab session yahan tak pahuncha.

## 2026-07-01 — TRAP #60: ghost-sync duplicate-exit feedback loop found live + fixed + full-day P&L reconciliation
**Status:** DONE
**Kya:** RELIANCE handle karne ke baad user ne poore din ke trades Zerodha se sync karne ko bola — reconciliation ke dauraan mila ki `broker_sync` har ~30s cycle MARUTI ke liye NAYA duplicate "exit" row bana raha tha, same stale price (₹388.00) baar-baar reuse karke — ek live, active feedback loop jo diagnosis ke dauraan bhi chalta raha (~20 phantom rows). Root cause do gaps ka combo: (1) `_net_rows()` Pass-1 pairing sirf simple side-alternation karta hai id-order me — odd-count same-key rows me last row hamesha "dangling open" reh jaata hai, chahe din genuinely flat ho chuka ho; (2) `_fetch_fills()` ek symbol ke SAARE fills ko EK dict entry me collapse kar deta tha (last-write-wins) — `_resolve_exit_price()` isliye har baar SAME purana fill price deta tha, koi tareeka nahi tha yeh janne ka ki "yeh fill pehle hi use ho chuka hai." Dono gaps mil ke loop banate the: TRAP #58/#59 ke fix ne (sahi resolve_kite_symbol + sahi quantity field) `_check_flat` ko PEHLI BAAR sahi se "haan flat hai" confirm karne diya — jo pehle silently fail hoti thi, ab har cycle trigger hone lagi, is pre-existing weakness ko expose karke.
**Fix (root):** `_fetch_fills()` ab har fill ka apna unique broker id (`trade_id`/Kite native, `exchangeTradeId`/`orderId` Dhan me naya add kiya) bhi carry karta hai. `_run_sync()` exit likhne se PEHLE `_fill_already_used(tid,...)` check karta hai (order_store ke `correlation_id` field se, jo schema me pehle se tha bas use nahi ho raha tha) — agar yeh fill pehle hi record ho chuka hai to skip (na duplicate write, na `mark_externally_closed` — dusra isliye bhi zaroori kyunki woh legitimate row ko P&L se hi hata deta, `_dead_filtered` ki wajah se).
**Manual data fix (aaj ke liye):** ~20 galat MARUTI rows delete karke, Zerodha ke real fills se 4 clean round-trips insert kiye. Poora din reconcile kiya (RELIANCE/SUNPHARMA-1880/SUNPHARMA-1980-hedge/MARUTI sab) — final total ₹1,827.50, Zerodha se exact match. `trades.db` backup liya har DELETE/INSERT se pehle.
**Layer:** broker / data-integrity / risk-management
**Files:** `broker_sync.py`, `brokers/dhan_broker.py`
**Kyun:** Live P&L corruption diagnosis ke dauraan mila — root cause fix taaki dobara na ho (kal ya kabhi bhi, 3+ round-trip wale din pe)
**Depends on:** TRAP #58/#59 (isi session, jinhone yeh pre-existing bug expose kiya)
**Verify:** Fix deploy + restart, 60s window me row-count stable (9, no new phantom) — 2 baar independently confirm kiya (before aur after root fix). Broker confirmed fully flat throughout.

## 2026-07-01 — TRAP #61 (mark_externally_closed unconditionally hid P&L entries) fixed + TRAP #62 (strategy state-desync causing real unintended orders) flagged
**Status:** DONE (#61) / PENDING (#62, user-flagged for later)
**Kya:** User ne khud pucha "yeh SUNPHARMA BUY kaise ho gaya, hamari strategy sirf SELL karti hai" — investigate karne pe do alag bugs mile. **TRAP #62 (root trigger):** account-level trailing-profit-lock (jo pura account squareoff karta hai) ne SUNPHARMA position 10:26 pe close kiya, lekin strategy process (`range_trader.py`) ki apni memory ko pata hi nahi chala — 40 min baad strategy ne "EXIT via ATR_TRAILING" samajh ke ek REAL BUY order bhej diya, jabki koi position band karne layak thi hi nahi (fresh unwanted long entry, ~₹122.50 cost). **TRAP #61 (display bug jo isse expose hua):** jab user ne is phantom BUY ko Zerodha pe manually close kiya, `broker_sync` ne sahi se exit record kiya (TRAP #60 ka fix sahi kaam kar raha tha) — lekin sath hi entry row ko `mark_externally_closed` bhi kar diya, jo P&L calculation se hi row hata deta hai (`_dead_filtered`) — isliye entry gayab ho gaya, exit akela "nayi open position" jaisa dikhne laga.
**Fix (#61 — done):** `mark_externally_closed` ab sirf tab call hota hai jab exit price hi nahi mila (genuinely kuch pair karne ko nahi). Jab exit sahi se record ho jaaye, entry ka status chhedo mat — normal netting khud pair kar leta hai.
**#62 abhi fix nahi hua** — user ne baad ke liye flag kiya. Do options note kiye: trailing-lock squareoff strategy process ko signal bheje, ya strategy apni state ko live periodically order_store se re-validate kare.
**Layer:** broker / strategy-engine / data-integrity
**Files:** `broker_sync.py`
**Kyun:** User ne khud confusion flag kiya ("strategy sirf sell karti hai, yeh buy kaise") — investigate karke 2 real bugs mile
**Depends on:** TRAP #60 (isi session — jiske baad yeh path pehli baar cleanly exercise hua)
**Verify:** Row 378 ka status fix karke turant sahi pair hua (BUY 28.60→SELL 28.25, pnl -122.50). Poore din ka total ab ₹1,705.00 — Zerodha ke apne "Total P&L" se EXACT match. Deploy + restart clean, 10 completed trades, open:[].

## 2026-07-01 — TRAP #63: TRAP #58 ka root cause fix — order_store row broker-accept pe hi likho, fill-confirm ke baad nahi
**Status:** DONE
**Kya:** Aaj hi 4 baar (RELIANCE, SUNPHARMA-hedge, HINDUNILVR, aur implicitly MARUTI ke through bhi) same gap dikha — `smart_order.execute()` sirf TRADED confirm hone ke baad hi order_store me likhta tha, aur 8 second (5×1.5s) baad haar maan leta tha. User ne pucha "yeh baar-baar kyun ho raha hai" — jawaab: saare affected symbols (RELIANCE/SUNPHARMA/MARUTI/HINDUNILVR) STOCK options hain, NIFTY jaise liquid nahi — inka fill-confirm aksar 8 sec se zyada leta hai, chahe fill genuinely ho chuka ho.
**Fix:** Order broker accept karte hi (poll shuru hone se PEHLE) ek "provisional" row likh do (best-guess price, `UNCONFIRMED_FILL` tag). Confirm TRADED → price sahi karo, tag hatao. Confirm REJECTED → status='rejected' (P&L se sahi exclude). Timeout → row waise hi chhodo — already protected hai (pos_monitor_loop SL/EOD laga dega), aur agar genuinely fill nahi hua to broker_sync khud clean kar dega (TRAP #61 ka no-price-branch).
**Layer:** broker / risk-management / order-flow
**Files:** `smart_order.py`, `order_store.py` (naya `update_fill()` function)
**Kyun:** User ne khud pucha "kyun baar-baar ho raha hai" — root fix maangi
**Depends on:** TRAP #58 (jisne yeh gap pehli baar identify kiya), TRAP #61 (jiska no-price branch iske timeout-case ko safely clean karta hai)
**Verify:** Deploy karte waqt ek REAL open MARUTI position thi (protected) — `ARS_CHAIN_V1` strategy process restart karna zaroori tha (Python `smart_order.py` hot-reload nahi karta), `_recover_state_from_order_store` (TRAP #28) se position sahi recover hui, zero protection-gap. Restart ke baad dashboard pe MARUTI abhi bhi correctly tracked confirmed.

## 2026-07-01 — TRAP #63 follow-up: delayed-fill monitoring log (user-requested — "is state ko track karo, data baad me kaam aayega")
**Status:** DONE
**Kya:** User ne 2 sawaal pucho: (1) 15-min-delay jaisa extreme case ho to SL protection turant lagti hai ya confirm ka wait karti hai — confirm kiya: turant lagti hai, kyunki provisional row apne SL tags ke saath hi likha jaata hai. (2) TRAP #63 ka fix delay resolve hone pe `UNCONFIRMED_FILL` tag hata deta hai — matlab "yeh delay hua tha" ka record kho jaata hai. User ne isko track karne ko bola.
**Fix:** Naya append-only log `data/fill_confirm_delays.json` — har live order jiska fill-confirm poll 1 se zyada attempt le (symbol, side, qty, attempted price, order_id, attempts, resolution: confirmed/rejected/timeout). Dashboard route `/api/fill-delays` (optional `?symbol=` filter) se dekho.
**Layer:** broker / monitoring
**Files:** `smart_order.py`, `trader_dashboard.py`
**Kyun:** User ne khud pucha "is data ko rakh lo, baad me kaam aayega"
**Depends on:** TRAP #63 (isi session)
**Verify:** Deploy + restart clean (`ARS_CHAIN_V1` + dashboard/monitor dono), koi open position nahi thi. Route `/api/fill-delays` test kiya — `[]` return kar raha (abhi tak koi delayed fill nahi hui, expected).

## 2026-07-01 — UI: Completed Trades "Group by Symbol" (Zerodha Day's History jaisa)
**Status:** DONE
**Kya:** User ne Zerodha ke "Day's history" table ka screenshot dikhaya — per-symbol grouped totals, expand karke individual trades. Wahi feature app ke "Completed Trades" table me bhi maanga.
**Fix:** Naya "📁 Group by Symbol" toggle button (Completed Trades header). OFF = purana flat view (byte-identical, refactor-only). ON = per-symbol summary row (points/gross/tax/net total + trade count), click karke expand/collapse — individual trades neeche sub-rows me dikhte hain. Row-rendering logic ek reusable function `_completedRowHtml()` me nikaala (pehle forEach ke andar duplicate tha) taaki flat aur grouped dono modes same code use karein — zero drift risk.
**Layer:** ui
**Files:** `templates/index.html`
**Kyun:** User-requested UI parity with Zerodha's own trade history view
**Depends on:** nothing
**Verify:** JS syntax `node --check` se verify kiya (Jinja tags strip karke). Deploy kiya (Flask templates auto-reload — koi service restart nahi lagi, `curl` se 200 OK + naya button HTML confirm kiya). Visual/interaction testing user ne khud browser me karna hai (is session me direct browser access nahi tha).

## 2026-07-01 — Full-day live incident investigation (Zerodha CSV se) → TRAP #64 order-chasing + shadow-live/paper-mode clarify
**Status:** DONE (order-chasing) / user ke apne actions clarify hue (shadow-live, paper mode, NIFTY manual trade)
**Kya:** User ne poore din ka Zerodha orders CSV diya — TITAN aur ICICIBANK ke orders 8-second wait ke baad bhi minutes tak broker pe unfilled reh rahe the (TITAN ~4.5 min baad khud fill hua; ICICIBANK kabhi fill hi nahi hua, user ne haath se cancel kiya). 30% trailing-lock din me 3 baar fire hua (12:00, 12:05, 13:41) — design ke mutabik kaam kar raha hai, lekin user ne pucha kya per-instrument lagana better hoga (design question, abhi decide nahi hua). Shadow-Live ON mila (user ne khud on kiya tha bhool se, ab OFF kar diya maine seedha config me — UI se save nahi hua tha). Paper mode (12:41 se) user ka apna intentional choice tha.
**Fix:** User ka idea implement kiya — order agar poll ke baad bhi fill nahi hua, cancel karke fresh price pe re-place karo (max 2 chase, 3 total attempts, ~24s max). Naya `BaseBroker.cancel_order()` (Dhan `DELETE /v2/orders/{id}`, Kite `cancel_order()`). Provisional row (TRAP #63) ka price + broker_order_id har chase round update hota hai.
**Layer:** broker / order-flow
**Files:** `smart_order.py`, `order_store.py`, `brokers/base_broker.py`, `brokers/dhan_broker.py`, `brokers/kite_broker.py`
**Kyun:** User ne khud suggest kiya jab dekha orders manually cancel karne pad rahe the
**Depends on:** TRAP #63 (isi session)
**Not done:** ICICIBANK phantom row (id 386) abhi bhi order_store me "open" dikha raha hai — real risk nahi hai (kabhi fill hi nahi hua), lekin cleanup pending. 30% ceiling per-instrument vs account-level — user decide karenge.
**Verify:** Syntax check + deploy + restart (paper mode me, safe waqt), clean startup confirmed, koi error nahi.

## 2026-07-01 — TRAP #65: liquidity filter poore din data hi nahi paa raha tha — `dhan_feed.start()` kabhi call hi nahi hua
**Status:** DONE (root fix) / PENDING (cold-start proactive-subscribe, REST fallback hardening)
**Kya:** User ne pucha "hamare paas to 2/3 wala liquidity filter hai, TITAN/ICICI illiquid hone ke bawajood kaise chal gaye?" — check kiya to poore din ke SAARE 13 `[LIQUIDITY]` log lines mein se HAR EK "no live market-depth data — failing OPEN" tha. Ek bhi real data check nahi hua. Root cause: `range_trader.py` sirf `dhan_feed.add()` call karta tha, `dhan_feed.start()` KABHI nahi — `add()` sirf tabhi kaam karta hai jab feed ka background thread already chal raha ho; woh thread sirf `start()` create karta hai. Isliye `dhan_feed.LIVE` is process me hamesha khaali raha (confirmed: size 0), aur har liquidity check REST fallback pe gira — jo khud test karne pe theek kaam kiya (direct call se real data mila) lekin real-time load me consistently fail ho raha tha.
**Fix:** `range_trader.py`'s `main()` ab startup pe `dhan_feed.start({client_id, jwt_token}, [])` call karta hai (same pattern jo `trader_dashboard.py`'s `_ensure_feed_started()` use karta hai) — ab feed genuinely connect hoga.
**Layer:** broker / risk-management
**Files:** `_TRADERS/range_trader.py`
**Kyun:** User ka sawaal ("filter kaam kyun nahi kiya") — investigate karke root cause mila
**Depends on:** nothing
**Not done:** din ki shuruaat me saare universe symbols ko proactively subscribe karna (abhi sirf reactive, signal aane pe) — pehla signal cold-start rahega. REST fallback ki reliability under load bhi harden karni chahiye shayad.
**Verify:** Deploy + restart clean, `[startup] dhan_feed started` log confirm hua. Real signal ka wait chal raha hai final confirmation ke liye (`[LIQUIDITY]` line real data ke saath aani chahiye ab).

## 2026-07-01 — Exit-side order-chasing (TRAP #64 follow-up) + phantom ICICI row cleanup
**Status:** DONE
**Kya:** User: entries conservative reh sakte hain (price bhaag jaye to skip), par exits ko zyada aggressive hona chahiye — loss badhta jaye aur price hi na mile aisa nahi hona chahiye. `smart_order.execute()` ab `is_exit` param leta hai — exits ko 4 chase rounds milte hain (entries 2 hi), aur har round LIMIT price ko spread ke aur andar cross karta hai (buffer double hota hai round pe, 150bps tak cap) — kabhi MARKET order nahi (Zerodha stock-options pe MARKET reject karta hai, sirf LIMIT allow hai). Saare automated exit call-sites (`range_trader` EOD+EXIT, `webhook_executor._do_exit`, `universe_trader` 3 exits, `trader_dashboard` trailing-lock+`_do_squareoff`) ko `is_exit=True` diya. Saath hi ICICIBANK ka ek phantom row (id 386, BUY 1330-PE @9.85, UNCONFIRMED_FILL) mila — Kite `order_history()` se verify kiya ki woh order CANCELLED hua tha (filled_quantity=0, kabhi fill nahi hua) — safe delete, DB backup lekar.
**Layer:** broker / risk-management
**Files:** `smart_order.py`, `_TRADERS/range_trader.py`, `_TRADERS/universe_trader.py`, `webhook_executor.py`, `trader_dashboard.py`
**Kyun:** User ne live TITAN/ICICIBANK stuck-order incident ke baad flexibility maangi
**Depends on:** TRAP #64 (order-chasing base)
**Verify:** Deploy + restart, ARS_CHAIN_V1 clean startup, koi open positions disturb nahi hui.

## 2026-07-01 — Broker Balances "Cash" label fix (TRAP #66)
**Status:** DONE
**Kya:** User: "Zerodha se mera balance app ka match nahi kar raha." 💰 Broker Balances card "Cash" line `b.available` dikha raha tha — Kite ke liye yeh total available MARGIN hai (cash + pledged collateral − used), asli cash nahi. Real cash (`b.cash`) already API se aa raha tha, bas UI mein kabhi use nahi hua. Fix: Cash ab `b.cash` dikhata hai, naya "Available Margin" line `b.available` ke liye add kiya.
**Layer:** UI / broker
**Files:** `templates/index.html`
**Kyun:** User ka mismatch report
**Depends on:** nothing
**Verify:** Deploy + dashboard restart, curl se "Available Margin" text confirm hua page pe.

## 2026-07-01 — Manual-trade broker reconciliation (🧾 Reconcile vs Broker) — built, broke, fixed (TRAP #67, #68, #69)
**Status:** DONE
**Kya:** User: Zerodha hi actual source of truth hai (app sirf order punch karti hai) — chahte hain ki Zerodha ke real fills se app automatically match ho jaye, jo trade app ne nahi kiya (manual) wo tag ke saath dikhe. Naya `broker_sync.reconcile_manual_trades()` (button-triggered, `/api/reconcile-manual-trades` route, "🧾 Reconcile vs Broker" button Completed Trades ke paas) — Kite ke `trades()` se aaj ke saare real fills leta hai, `order_store` se match karta hai, jo match nahi hota use `source=manual` tag ke saath insert kar deta hai.
**Pehla version TOOTA:** matching sirf `broker_order_id` (order id) se ki thi — kuch purani rows (isi session ki earlier manual TRAP#60/61 cleanup se) ka broker_order_id kabhi populate hi nahi hua tha, to unhe "unmatched" samajh ke 32 DUPLICATE rows insert kar diye. User ne turant screenshot se pakda ("gross balance alag hai"). Surgical DELETE se (`correlation_id LIKE 'MANUAL_TID_%'`) saaf kiya — poori DB restore nahi ki (WAL-mode backup incomplete nikla, restore karta to 19 real rows kho jaate — TRAP #68).
**Fix (v2):** matching ab SIGNATURE+COUNT se — `(root_symbol, strike, CE/PE, side, qty, price)` normalize karke, jitne broker ke fills us signature ke utne hi order_store rows hone chahiye; farak > 0 to utne hi naye insert karo. Ye kisi bhi purani row ke broker_order_id missing hone se safe hai, aur khud-idempotent hai. Dry-run script se pehle verify kiya (12 genuinely-missing fills mile, 0 false-positive dupes) tab jaake live chalaya.
**Deploy gotcha:** ek multi-file scp mein `templates/index.html` silently deploy nahi hua tha (koi error nahi dikha) — user ne button missing dekha, mtime check se pakda, alag se re-deploy kiya (TRAP #69).
**Layer:** broker / data-integrity / UI
**Files:** `broker_sync.py`, `trader_dashboard.py`, `templates/index.html`
**Kyun:** User ka reconciliation automation ka ask
**Depends on:** nothing
**Verify:** Signature-based version live-run kiya — 12 manual trades insert hue (HINDUNILVR, NIFTY x2, NESTLEIND), per-instrument Zerodha se exact match (HINDUNILVR -225, NIFTY -1215.50, NIFTY -1186.25). Live-mode total ab ₹-2,389.25 vs Zerodha ₹-2,426.75 (₹37.50 ka chhota gap bacha, NESTLEIND-1450 pe — further check pending).

## 2026-07-01 — Alternate History / Counterfactual feature — rewritten, then REMOVED entirely (user decision)
**Status:** DONE (removal)
**Kya:** Same-session follow-up: user ne "Alternate History" table (duplicate Algo+Panic rows same trade ke liye) dikhaya. Root cause counterfactual.py's dual-source design tha (order_store + separate Kite raw-fill refetch, same real trade dono jagah se aa raha tha — TRAP #67 wali exact same class of bug, ek level upar). Pehle isko theek kiya: `order_store._net_rows()`'s `_complete()` mein `exit_source`/`exit_strategy` add kiya (entry aur exit ka origin independently pata chale), `counterfactual.analyze()` ko poora order_store-only (single source) pe rewrite kiya, table mein per-leg 🤖Algo/👤Manual chip + price dikhaya, chart hover tooltips (price+instrument), bottom-label overlap bhi fix kiya. Live-verify kiya — duplicate rows gaye, numbers sahi (algo ₹1337.50, manual -₹3726.75, panic ₹0). **User phir bhi bola "ye eakdum bekar hai, pura hata do"** — feature hi hata diya: `counterfactual.py` delete, `/api/counterfactual` + `/api/kite-csv-upload` routes hate, poora "🔄 Alternate History" card + JS (`loadCounterfactual()`) UI se nikala, `order_store.py`'s `exit_source`/`exit_strategy` bhi revert kiya (sirf isi feature ke liye tha, koi aur consumer nahi).
**Layer:** UI / broker / data-integrity
**Files:** `counterfactual.py` (deleted), `trader_dashboard.py`, `templates/index.html`, `order_store.py`
**Kyun:** User ka explicit "hata do" — feature trust nahi bana paya, complexity uske value se zyada thi
**Depends on:** nothing (Completed Trades' apna manual-trade tagging aur "🧾 Reconcile vs Broker" button untouched hai — wo alag feature hai, iska hissa nahi)
**Verify:** Deploy + restart, `/api/counterfactual` 404, koi UI trace nahi bacha, baaki dashboard (Completed Trades, Open Positions) normal chal raha.

## 2026-07-04 — Default Target/SL exit profile (RMS)
**Status:** DONE (deployed DISABLED to VPS; live fire-test pending market hours)
**Kya:** Global rupee-based exit profile — fixed target + stepped trailing SL + aggressive 2x phase after X% of target + min_cushion whipsaw guard. User-designed, mockup+graph approved. Config ₹ PER-LOT, scaled by lots (=qty/lot_size from scrip master).
**Layer:** RMS / risk-gate / UI
**Files:** `risk_gate.py` (`default_target_sl_config()` + pure `target_sl_level()`/`advance_target_sl()` state machine, confirmed-peak spike-guard); `dhan_master.py` (`get_lot_size_by_sec_id()` reverse lookup, memoized); `trader_dashboard.py` (pos_monitor_loop firing block + `_tsl_state` persistence/restore/rollover + global decl); `order_store.py` (`DEFAULT_TSL_TARGET`/`_SL` exit-reason prefixes); `templates/index.html` (RMS row 5️⃣ card, 8 config fields, Graph+Table sub-tabs w/ hover, load/save wiring, exit-reason badges×2).
**Kyun:** User ko har trade pe ek consistent, scalable (per-lot rupee) disciplined trailing exit chahiye. Points/percent instrument-to-instrument drift karta; rupee-MTM consistent.
**Depends on:** `advance_trailing_lock()` confirmed-peak technique (reused). Ships DISABLED (`default_tsl_enabled=false`) like KILL-ALL floor.
**Verify:** JS `node --check` OK; JS↔Python SL-trail parity EXACT at every point (cushion 0 & 150); py_compile all 4 files (local+VPS); 2-lot sim = TARGET@₹4000 / SL trails ₹2800 / straight-loss stops ₹2000; unknown lot_size → skip (no guess). Deployed Sat 15:31 IST (market closed, safest window), md5 local==remote on all 5, both services active+clean, 8 `risk-tsl-*` fields served, zero `[DEFAULT-TSL]` errors/tracebacks. **Pre-mortem shapes 1/3/4/6/8 all guarded (see chat).**
**Pending:** (1) user enable + live fire-test during market hours; (2) target-side is immediate-fire (spike to target = exit) — user can request 2-tick confirm later.

## 2026-07-04 — Total Summary aggregation-mode switch (Σ/Avg/Min/Max)
**Status:** DONE (deployed VPS, Sat market-closed)
**Kya:** Ek `Σ Total | Avg | Min | Max` switch jo saare metric columns pe apply hota — purane per-combo columns (Avg Net, Best/Worst Net, Max Run-Up/Down, Avg Pts...) ko base-metric toggles (Points/Gross/Net/Tax/Duration/Run-Up/Run-Down) + 1 mode-switch me collapse kiya (~40 toggle → ~12 chips + switch). User-designed, interactive mockup approved.
**Layer:** ui
**Files:** `templates/index.html` only — `_sumTrades()` ab per-metric `{sum,avg,min,max}` (`s.m[base][mode]`) + real cumulative `maxDD` (net-equity peak-to-trough, time-ordered) return karta; `_sumRow`/header/`sortVal` mode-aware; new `_calSumAgg` state (localStorage `cal_sum_agg`), `calSumAggSeg()` handler, switch UI. Fixed (mode-independent): Trades/Strategies/W/L/Win%/Expectancy/Max Drawdown.
**Verify:** node --check OK; real `_sumTrades` eval-test — net Σ900/avg300/min−400/max1000, points Σ18, dur Σ75/avg25/min20/max30, run-up Σ1900, cumulative maxDD 400 — all exact. Deployed, md5 local==remote, dashboard active, switch served. NOTE: metric ids changed (`m_*`) → old `cal_sum_cols` localStorage resets to new defaults (Trades/Strategies/W/L/Win%/Gross/Tax/Net on) — expected, harmless.

## 2026-07-14 — ML-Assisted Strategy Mining infra (Tasks 0-4) + verdict: NO new strategy, VRP zone confirmed
**Status:** DONE (research infra permanent; mining rounds concluded for intraday/weekly NIFTY)
**Kya:** ML/GP se candidate-entry-condition mining ka poora pipeline banaya — statistical guardrails (deflated Sharpe, purged walk-forward CV, hard OOS lockbox, multiple-testing log) + feature layer + GBT/SHAP rule-extraction (Approach A) + wave-based genetic programming (Approach B). ~4.0M rules test hue do GP runs me — ZERO ne formal gate (DSR≥0.95 @ true trial count) clear kiya. GP ka convergent verdict = VRP short-straddle-overnight zone (IV>21 + ATR high + OI flat; val Sharpe 2.25, corr 0.15 vs shipped) — wahi zone jo haath se validated hai (vrp panic-fade, PF 4.4). TRAP #114 mila+fixa (WEEK rolling series ka contract-roll seam — v1 run ka val-Sharpe-7.1 "golden rule" 94% expiry-day fake tha). Salvage scan: v1 ke clean eod/expiry rules bhi val me ~0 (curve-fit proof). Legacy strategies naye DSR bar se neeche (0.87-0.90) — USER DECISION: 0.95 bar sirf naye ML candidates pe, legacy grandfathered.
**Layer:** research (scratch/nifty_trend), gate wiring in run_hunt.py
**Files:** `ml_gate.py` (DSR no-scipy + purged CV + lockbox + trials log + Li-Ji N_eff), `ml_data_inventory.py/.json`, `ML_MINING_DATA_INVENTORY.md`, `ml_lockbox_split.py`, `ml_gate_check.py` (calibration), `ml_features.py` (v2: 42 cols), `ml_mine_a.py`/`ml_rules_a.py` (Approach A), `ml_gp_precompute.py`/`ml_gp.py`/`ml_gp_salvage.py` (Approach B), `run_hunt.py` (DSR wired: p<0.05 AND DSR AND MC), LESSONS #114. Tasklist DONE LOG: Desktop `ml-strategy-mining-tasklist.md`.
**Data (git me NAHI — VPS se download):** lake master VPS `_TRADING_DATA/OptChainLake/` (566MB); derived tables (features v1/v2, labels, gp_pnl.npz, waves/reports) + `ml_mining_log.csv.gz` (64MB, audit log) VPS `_TRADING_DATA/ML_MINING_BACKUP/` me backed-up (2026-07-14). Nayi machine: dono scp karo + lake pe `ml_lockbox_split.py` chalao (idempotent). Regenerate-from-scratch path bhi kaam karta hai: `ml_features.py --force` → `ml_gp_precompute.py`. ML libs py3.8 pins: sklearn 1.3.2, lightgbm 4.5.0, xgboost 2.0.3, shap 0.44.1.
**Verify:** ml_gate.py self-tests (norm/DSR/purge/lockbox) local+VPS pass; noise best-of-1000 (ann Sharpe 1.71) DSR 0.34 reject; TRAP #112 star-rule fixed table pe 0 trades; lockbox access log empty (kisi ne earn nahi kiya).
**DO-NOT-REDO:** 15-min direction (AUC 0.518), intraday premium selling (cost wall), expiry-day "cheap straddle" (roll seam), long-vol re-hunt, intraday/weekly NIFTY re-mining (search saturated).
