# LESSONS.md — Recurring Traps & Debugging Playbook (CODE3B)

> **Yeh kya hai:** `ARCHITECTURE_LOG.md` = "kya banaya/badla" (chronological changelog).
> **Yeh file = "kya baar baar kaatega aur use permanently kaise roka"** (problem se indexed,
> date se nahi). Jab koi bug aaye: pehle yahan dekho — shayad pehle bhi aa chuka hai.
> Jab koi bug fix karo jo dobara aa sakta hai: yahan ek entry add karo (symptom →
> root pattern → permanent guard → fast-detect). **Goal: ek bug do baar diagnose na karna.**
>
> Format har trap ka: **Symptom · Root pattern · Kahan-kahan kaata · Permanent guard · Fast detect.**

---

## TRAP #1 — ₹0-price "phantom fill" (P&L corrupt + RMS breaker trip) 🔴🔴🔴

**Sabse zyada baar lauta — ab tak 4 baar, har baar nayi jagah.**

- **Symptom:** Orders/P&L me ek entry ya exit `PX 0.00` pe dikhti hai. Ek SELL jo ₹0 pe
  "fill" hui → jab real premium pe close hoti hai to **jhootha bada P&L** banata hai
  (e.g. SELL@₹0 → BUY-close@27.45 = fake −₹4,803). Yeh fake loss **RMS daily-loss breaker
  trip** kar deta hai → asli positions force-squareoff ho jaati hain.
- **Root pattern:** Option premium fetch (`/v2/marketfeed/ltp`) **DH-904 rate-limit** se fail
  hota hai, aur code fallback me `price = 0.0` record kar deta hai. ₹0 = "unknown", par
  order_store ke liye woh ek real fill ban jaata hai. (Index/spot price log karna bhi galat —
  isiliye 0 chuna gaya tha, par 0 bhi utna hi khatarnak hai.)
- **Kahan-kahan kaata:**
  - 2026-06-17 — `range_trader.py` options branch (pehli baar)
  - 2026-06-22 — `api_close_position` (close 0.00 = jhootha profit)
  - 2026-06-22 — zero-leg cleanup (`_fix_zero_legs.py`, fake ~₹15,343 hata)
  - 2026-06-25 — `range_trader.place_order()` (MARUTI/TCS @0 → fake −₹4,803 → ARS_CHAIN_V1
    −5,350 → ₹5,000 breaker trip → asli legs squared off). TCS pair (DB id 159+160) delete kiya.
- **Permanent guard (ab lagaya):**
  1. **Caller:** koi bhi entry path jisko real price chahiye — agar premium na mile to
     **entry SKIP karo, ₹0 record MAT karo.** `range_trader.place_order` ab cache→direct→stale
     try karke, fir bhi na mile to `False` return karta hai aur caller entry skip karta hai.
  2. **Central tripwire:** `order_store.record()` me — agar `price<=0` aur status real fill hai
     (paper/filled/live, blocked/rejected nahi) to **loud ⚠️ warning** print hoti hai
     (`journalctl`/log me greppable: `SUSPICIOUS 0-price`). Yeh har naye code-path ko pakad
     leta hai, taaki 5vi baar silently na ghuse.
  3. **Price source priority (option premium):** `shared_ltp_cache.get()` → direct Dhan (backoff)
     → `shared_ltp_cache.get_stale()`. Cross-process cache se ek-ek process alag call nahi karta.
- **Fast detect:** `SELECT * FROM orders WHERE date=date('now') AND price=0 AND status NOT IN
  ('blocked','rejected','cancelled')` — agar koi row aaye to abhi corrupt data hai; entry+exit
  dono legs delete/correct karo (DB backup leke). Aur `journalctl -u algo-dashboard | grep
  '0-price'`.

---

## TRAP #2 — DH-904 rate-limit cascade (Dhan ~1 req/sec WHOLE account)

- **Symptom:** logs me `DH-904 / Rate_Limit`, `levels: 0 key levels loaded`, entries jinme spot/
  premium "no price", webhook "request timed out".
- **Root pattern:** Dhan ka limit **poore account pe ~1 req/sec** hai, per-process nahi. Jaise hi
  2+ process (dashboard webhook + range_trader + rsi + universe) saath me poll karte hain,
  sab 429 khaate hain.
- **Kahan-kahan kaata:** range_trader `fetch_1m`/`fetch_daily`; webhook entry premium; har trader
  ka apna LTP call.
- **Permanent guard (v1):** `shared_ltp_cache.py` (file-backed cross-process cache) — sab process ek
  hi cache padhte/likhte hain, "N process × M symbol" calls ≈ "1 call per symbol per TTL".
  Plus: per-symbol scan me `time.sleep` throttle; whitelist se symbol count kam (kam calls);
  blocked/maxed strategy ki LTP call hi mat karo (`gating_status` short-circuit).
- **Permanent guard (v2, 2026-06-27) — `dhan_rate_limiter.py`:** v1 ka gap yeh tha ki cache sirf
  LTP reuse karta hai — candles aur **orders** kabhi cache nahi hote, aur har process ka apna
  `time.sleep` throttle sirf "main akela polite hoon" guarantee karta hai, "sab milkar account
  limit cross na karein" nahi. `dhan_rate_limiter.py` ek sqlite-backed (stdlib, no extra dep)
  cross-process token-bucket hai jo Dhan ke EVERY call (candle/ltp/order/margin) ko ek hi global
  cap (`DHAN_RATE_LIMIT_PER_SEC`, default 3/sec) ke through route karta hai — `acquire(priority)`
  call karo, slot milne tak block karta hai (fast poll, koi busy-loop CPU waste nahi). **Sabse
  zaroori hissa: priority.** `"order"` priority ke liye 1 slot **hamesha reserved** hai — chahe
  candle-scan/LTP-poll loop kitna bhi busy ho, ek real order ka slot kabhi nahi rukta. 429 aane pe
  `note_429()` 8-second cooldown set karta hai jisme **non-order traffic poora ruk jaata hai**
  (sirf orders chalte rehte hain) — taaki account jaldi recover kare aur fresh orders bhi atke
  na rahein. Wired into: `brokers/dhan_broker.py` (quote/place_order/funds/candles — saari
  strategies+webhook+universe_trader isi se guzarti hain), `_TRADERS/range_trader.py`,
  `_TRADERS/rsi_trader.py`, `risk_gate.py` (margin-calculator + quick LTP), `trader_dashboard.py`
  (manual order, close-position, order-status poll, Quick Order LTP, debug routes). v1
  (`shared_ltp_cache`) abhi bhi chalta hai — dono saath kaam karte hain (cache=reuse, rate
  limiter=throttle+priority jab cache miss ho).
- **Fast detect:** `grep -c DH-904 logs/<strat>.log`; agar multiple traders ek saath chal rahe
  to aggregate rate dekho. Rate-limiter ka apna state `data/dhan_rate_limiter.db` (sqlite) — agar
  shaq ho ki orders queue ho rahe hain, isko delete karke restart karo (fresh state, koi data loss
  nahi, sirf rolling counters hain).
- **Permanent guard (v3, 2026-06-29) — `shared_candle_cache.py`:** v2's rate-limiter throttles
  calls but never asked *why* there were so many in the first place. Root cause found: `SBIN`
  (ARS_CHAIN_V1) hit DH-904 because **`range_trader.py` AND the rsi_v1 process both independently
  re-fetch the FULL day's 1-min candles for every overlapping symbol, every single loop (~60s)**
  — two processes asking Dhan for data that's byte-identical within the same few seconds. The
  account-wide cap was never really the bottleneck; the duplicate fetching was. **Gotcha:** the
  actually-running rsi_v1 process is the legacy `_TRADERS/01_rsi_v1.py` (Critical Rule 6 —
  RMS-blind, order_store-blind), NOT `_TRADERS/rsi_trader.py` (a newer, unused-in-prod file that
  looks like "the" RSI strategy but isn't what's launched) — `01_rsi_v1.py`'s `fetch_candles()`
  had ZERO rate-limiting or caching at all before this fix, the real source of the 429s. Fixed
  BOTH files (in case `rsi_trader.py` ever does get launched) for consistency. Fix: same
  file-backed cross-process cache pattern as `shared_ltp_cache.py`, keyed by `sec_id:interval`,
  TTL 20s (a 1-min candle genuinely can't change faster than that). `fetch_1m()`
  (`range_trader.py`) and `fetch_candles()` (`01_rsi_v1.py` + `rsi_trader.py`) all check this
  cache FIRST, and all write to it after a real fetch — so whichever strategy asks first pays the
  Dhan call, the other(s) read the cache for free. Collapses "N processes × M symbols" Dhan calls
  into roughly "1 call per symbol per 20s window," same effect `shared_ltp_cache` already proved
  for LTP, just never applied to candles. **Before trusting any fix to a "strategy file" again:
  confirm via `ps aux` which file is ACTUALLY the running process** — this repo has more than one
  file per strategy name (TRAP #3 territory).
- **Fast detect (v3):** if DH-904 keeps recurring on a symbol that's traded by 2+ active
  strategies, check `data/shared_candle_cache.json` exists and is being written (mtime updating
  every loop) — if it's stale/missing, the cache import is silently failing (wrap in try/except,
  check logs for an exception swallowed there) and both processes are fetching independently
  again.

---

## TRAP #3 — "Galat process ko blame karna" (jo chal hi nahi raha)

- **Symptom:** Bug ek strategy me dikhta hai, hum us file ko fix karte hain, par bug rehta hai.
- **Root pattern:** Hum maan lete hain ki kaun-si strategy/file trade kar rahi hai, bina verify
  kiye. (2026-06-25: maine MARUTI ₹0 ko `universe_trader` ka maana — par woh `active:false` tha,
  chal hi nahi raha; asli source `range_trader` (ARS_CHAIN_V1) tha.)
- **Permanent guard / playbook:** **Diagnose karne se PEHLE live state dekho:**
  - `ps -eo pid,etimes,args | grep -E 'trader|rsi'` — kaun actually chal raha hai.
  - `SELECT ts,source,strategy,symbol,price,status,tags FROM orders WHERE ...` — asli row ka
    `source`/`strategy` dekho (assume mat karo). `source='strategy'` + `strategy=<id>` batata hai
    kaun ne likha.
  - Local `nifty_config.json` me sirf `_risk` hota hai — **asli config VPS pe hai.** Local config
    se "kya active hai" mat maano.
- **Fast detect:** logs ke timestamp + `etimes` match karo (process kab restart hua).

---

## TRAP #4 — Python 3.8 (VPS) vs 3.10+ (local) syntax crash on import

- **Symptom:** Local sab theek, VPS pe `from brokers import get_broker` chup-chaap fail
  (`TypeError: unsupported operand type(s) for |`), webhook entries silently fail.
- **Root pattern:** `dict | None` (PEP 604) type hints default-arg me — Python 3.8 eagerly
  evaluate karke crash karta hai. VPS pe purana Python ho sakta hai.
- **Kahan kaata:** 2026-06-24 `brokers/__init__.py`, `brokers/dhan_broker.py`.
- **Permanent guard:** `typing.Optional[dict]` use karo, `X | None` nahi (jab tak VPS Python ≥3.10
  confirm na ho). Deploy se pehle **VPS pe `venv/bin/python -m py_compile <files>`** chalao —
  yeh exact mismatch pakad leta hai (humne aaj kiya).
- **Fast detect:** `ssh ... "cd <dir> && venv/bin/python -m py_compile *.py"` deploy se pehle.

---

## TRAP #5 — VPS deploy gotchas (dir-with-space + SSH key path drift)

- **Symptom:** scp/ssh fail, ya galat path pe file chali jaaye.
- **Root patterns + guards:**
  - VPS dir me space hai: `/root/CODE3B- TV BACKTEST ENGINE/` — scp/ssh me **quote karo**.
  - SSH key path **drift** kar chuka hai: ab `C:\Users\91933\.ssh\khazana_ed25519` (passwordless,
    verified). **CLAUDE.md me purana `C:\Users\arsal\...` likha hai — woh STALE hai, ignore.**
  - `deploy_vps.py` STALE hai (`/root/code4` galat) — **manual scp** use karo.
  - Deploy ke baad: VPS py_compile → `systemctl restart algo-dashboard` → verify
    `systemctl is-active` + `curl 127.0.0.1:5099/api/<route>`. Traders (range/rsi) dashboard
    restart pe **respawn** hote hain (fresh code uthate hain) — `ps ... etimes` se confirm karo.
- **Fast detect:** push ke baad ek route curl karke naye field check karo (humne `rms-summary` ke
  naye keys verify kiye).

---

## TRAP #6 — UI update "dikhta nahi, refresh karna padta hai" (fingerprint skip)

- **Symptom:** SL/Target ya koi tag-based value set karne ke baad turant nahi dikhta, page refresh
  pe aata hai.
- **Root pattern:** `ordersRender()` ek **fingerprint** (`openFp`) se decide karta hai rebuild
  karna hai ya sirf LTP-patch. Fingerprint me `tags` shaamil nahi the → tag badla par fp same →
  rebuild skip → naya SL/Target render nahi hua.
- **Kahan kaata:** 2026-06-25 SL/Target instant-display.
- **Permanent guard:** jo bhi mutation tag/SL/TP change kare, uske baad
  `document.getElementById('ord-open').dataset.fp=''` set karke `ordersRender()` — forced rebuild.
- **Fast detect:** "X set kiya, refresh pe aata hai" = hamesha fingerprint/cache skip suspect karo.

---

## TRAP #7 — Mid-day dashboard restart silently leaves strategies STOPPED

- **Symptom:** `systemctl restart algo-dashboard` ke baad traders (range/rsi) chal nahi rahe;
  `/api/status` `{}` deta hai; journal me `Auto-starting bots in PAPER mode...` to dikhta hai
  par process zinda nahi. Market open hone par bhi koi nayi entry nahi.
- **Root pattern:** Restart un traders ko (jo dashboard ke child Popen hain) **maar deta hai.**
  `auto_scheduler` boot pe turant chalta hai aur `requests.post("http://127.0.0.1:5099/api/start…")`
  karta hai — par us instant Flask abhi **bind nahi hua** hota → POST connection-refused se fail
  → `except: pass` nigal jaata hai → phir bhi `has_started_today=True` set ho jaata hai → poora
  din **retry nahi** karta. (Race jeet gaye to start ho jaata, haar gaye to chup-chaap stopped.)
- **Kahan kaata:** 2026-06-25 — guard deploy ke restart (15:07) pe ARS_CHAIN_V1 + rsi_v1 stopped
  reh gaye; manually `POST /api/start?s=<id>&mode=paper` se restore kiya.
- **Permanent guard / workaround:**
  - **Restart ke baad ALWAYS verify** (sirf "Auto-starting bots" log pe bharosa mat karo):
    `ps -eo pid,etimes,args | grep trader` + `curl -s 127.0.0.1:5099/api/status`. Agar khaali ho
    to manually start: `curl -s -X POST '127.0.0.1:5099/api/start?s=<KEY>&mode=paper'` (POST, GET
    nahi → 405). Jo pehle chal rahe the wahi (e.g. ARS_CHAIN_V1, rsi_v1).
  - **Market hours me restart se bacho** jab tak zaroori na ho (har restart yeh race + pos_monitor
    ka chhota gap deta hai). Open positions safe rehti hain (pos_monitor 15:15 EOD squareoff karta),
    par naye entries miss ho sakte hain.
  - **Code follow-up (off-market, NOT yet done):** `auto_scheduler` me `has_started_today=True`
    SIRF tab set karo jab koi start actually succeed kare (ya pehle tick se pehle chhota delay,
    ya boolean ki jagah real running-state check). Tab tak restart-verify manual.
- **Fast detect:** restart ke 10s baad `ps`+`/api/status` — khaali = manually start.

---

## TRAP #8 — Backtest auto-download poisons a day as "holiday" when the token's just expired

- **Symptom:** `_TOOLS/backtest_engine.py` reports recent days as "holiday/no data" even though
  the strategy actually traded live on those exact days (real rows in `order_store`/`trades.db`).
  Once cached this way, the day stays "holiday" **forever** — re-running the backtest after fixing
  the token doesn't help, because `os.path.exists(fpath)` skips re-fetching it.
- **Root pattern:** `_fetch_nifty_day`/`_fetch_equity_day` only special-cased the `DH-904` rate-limit
  error. Any OTHER Dhan failure — most commonly an **expired/invalid token (`DH-901`)** — fell
  through to the same "empty response = genuine holiday" branch and got written to disk as a
  permanent empty-CSV marker.
- **Kahan kaata:** 2026-06-27 — user asked for a Jun 22-24 backtest matching live paper trades;
  Jun 22-26 all came back "holiday" even though Jun 23/24 had real recorded trades. Local Dhan
  token had expired (24h JWT, Critical Rule #4) mid-download.
- **Permanent guard:** `_fetch_nifty_day`/`_fetch_equity_day` now return a distinct `"AUTH_FAIL"`
  sentinel for non-200/`DH-901`/`DH-902`/`DH-905` responses — `ensure_*_data` stops the whole
  download immediately on it and never writes the poisoning empty-file marker.
- **Fast detect:** if a recent weekday shows "holiday/no data" but you know trading happened that
  day (check `order_store.trades_for(date)`), suspect a poisoned marker — `wc -l` the CSV (1 line =
  header-only = poisoned). Delete it and re-run with a **valid** token (check via a raw
  `requests.post` to `/v2/charts/intraday` first — `DH-901` means refresh the token in Control tab).

---

## TRAP #9 — Stale duplicate file on VPS shadows the real (fixed) one via sys.path order

- **Symptom:** Deployed a fix to `_TOOLS/validate_strategy.py`, VPS still crashes with the
  pre-fix bug (`KeyError: 'date'`) even after restart + md5-verified the new file landed.
- **Root pattern:** an old `validate_strategy.py` from the pre-`_TOOLS/` reorg was still sitting
  at the **VPS project root** (`/root/CODE3B- TV BACKTEST ENGINE/validate_strategy.py`, hardcoded
  to the dead `/root/code4/nifty_days` path) — never deleted during the `_TOOLS/` migration, and
  not tracked in the local git repo at all (VPS-only cruft). `backtest_engine.py` does
  `sys.path.insert(0, BASE_DIR)` AFTER `sys.path.insert(0, TOOLS_DIR)`, so `BASE_DIR` (project
  root) ends up earlier in `sys.path` — `import validate_strategy` resolved to the stale root
  copy, not the real `_TOOLS/` one.
- **Kahan kaata:** 2026-06-27, mid Jun-22-24 backtest debugging.
- **Permanent guard:** moved the stale copy aside (`validate_strategy.py._stale_root_copy_bak_*`).
  **When deploying any file that already exists in `_TOOLS/`/`_TRADERS/`, grep the WHOLE repo root
  for a same-named duplicate first** — `find . -iname '<file>.py'` (don't assume there's only one).
- **Fast detect:** `python -c "import X; print(X.__file__)"` (with the same `sys.path` order the
  real caller uses) — if `__file__` isn't the path you just deployed to, something's shadowing it.

---

## TRAP #10 — RMS checks failing OPEN on exception (rate-limit/network) instead of blocking

- **Symptom:** no visible symptom most of the time — that's the danger. A position could exceed
  its configured loss limit specifically DURING a Dhan rate-limit/feed outage, with no error
  surfaced anywhere (logs just say "risk gate check failed (allowing entry)" or silently skip a
  position's SL check).
- **Root pattern:** every entry-gate's `try/except risk_gate.check_*` and `pos_monitor_loop`'s own
  exception handling defaulted to **fail-open** (allow the entry / leave the position unmonitored)
  on any exception — including the loop's single top-level `try/except`, which meant ONE bad
  position throwing could blind monitoring of every OTHER open position that cycle too.
- **Permanent guard (2026-06-27):**
  - All 4 entry-gate call sites (`range_trader.py`, `rsi_trader.py`, `universe_trader.py`,
    `webhook_executor.py`) now **fail closed** — an RMS exception blocks the entry instead of
    allowing it. (`check_broker_funds` stays intentionally fail-open — funds-availability check,
    not a loss-cap.)
  - `pos_monitor_loop`'s per-position logic is now `_pos_monitor_check_one()`, called inside a
    **per-position** try/except — one crash no longer blinds the rest of the cycle.
  - LTP fetch gained a 3rd fallback tier (`shared_ltp_cache.get_stale`, same pattern
    `range_trader.place_order` already used) before giving up, plus a consecutive-miss counter
    that logs `⚠️ CRITICAL` after ~30s with zero price from any source.
- **Fast detect:** `grep -rn "allowing entry\|leaving position open" *.py _TRADERS/*.py` — every
  hit should either fail-closed or have an explicit, deliberate comment explaining why fail-open
  is the safer choice there. New risk-gate call sites should default to fail-closed unless proven
  otherwise.

---

## TRAP #11 — WebSocket live feed never connects (`dhanhq` missing / wrong class imported)

- **Symptom:** logs spam `[_ensure_feed_started] fail: No module named 'dhanhq'` (or, before
  2026-06-24's fix, this failed completely silently via `except: pass`). `dhan_feed.LIVE` stays
  permanently empty → `pos_monitor_loop`'s SL/TP/EOD-squareoff always falls back to the REST
  `/v2/marketfeed/ltp` poll (works, but means 100% of LTP traffic goes through
  `dhan_rate_limiter`'s "ltp" priority instead of a free push-based feed — see TRAP #2).
- **Root pattern, TWO separate bugs stacked on top of each other:**
  1. **Package not installed at all on the VPS** — `requirements.txt` listed `dhanhq` unpinned,
     but `pip show dhanhq` on the VPS returned "Package(s) not found". Nobody had ever actually
     run `pip install -r requirements.txt` there for this package (or it silently failed at some
     point and nobody noticed because of bug #2 below hiding the real error).
  2. **Code imported symbols that don't exist in the installed version** — `dhan_feed.py` did
     `from dhanhq import DhanContext, MarketFeed`. The installed `dhanhq==2.0.2` exports
     `DhanFeed`, `OrderSocket`, `dhanhq`, `marketfeed`, `orderupdate` — **no** `DhanContext`,
     **no** `MarketFeed`. Checked PyPI history — no version of dhanhq 2.x ever exported those two
     names; the original code was likely written against a different/hypothetical API shape and
     never actually verified to import.
- **Kahan kaata:** every live position's SL/TP/EOD-squareoff since the feature was built
  (2026-06-24) — masked because the REST fallback (`_rest_ltp_fallback`, same TRAP #10 fix)
  covered for it well enough that nobody noticed positions WERE still closing correctly, just via
  REST instead of the (faster, rate-limit-free) WebSocket path.
- **Permanent guard (2026-06-27):** rewrote `dhan_feed.py` against the ACTUALLY installed
  `dhanhq.DhanFeed` class (confirmed via `inspect.getsource()` on the real installed package, not
  docs/memory) — `DhanFeed(client_id, token, instruments, version='v2')`, instrument tuples
  `(exchange_code:int, sec_id:str, 21)` where 21 = Full packet (5-level depth, same fields the old
  `MarketFeed.Full` gave). Public API (`start/add/get_quote/LIVE`) unchanged — zero changes needed
  in `smart_order.py`/`webhook_executor.py`/`trader_dashboard.py`. Pinned `dhanhq==2.0.2` in
  `requirements.txt` (was unpinned — a future `pip install -U` could silently swap the exported
  class names again). `pip install dhanhq==2.0.2` run on VPS venv. **Live-verified on VPS:**
  WebSocket handshake (HTTP 101) + subscription accepted + ping/pong keepalive all confirmed
  working; actual tick data not seen because the test ran after 15:30 IST market close — re-verify
  during market hours by checking `dhan_feed.LIVE` is non-empty for a subscribed sec_id.
- **Fast detect:** `python -c "from dhanhq import DhanFeed"` — if this raises `ImportError`, the
  installed version's API has drifted again; re-run `inspect.getsource(dhanhq.DhanFeed.__init__)`
  to see the real signature before assuming the old code is still right. `grep -c
  _ensure_feed_started logs/*.log` for the old silent-failure symptom.

---

## DEBUGGING PLAYBOOK — fast diagnosis order

1. **Live state pehle, assumption baad me** (TRAP #3): `ps` se kaun chal raha hai; `orders` table
   se asli `source/strategy/price/status`; logs ke timestamps.
2. **Local ≠ VPS:** asli config + DB + running processes **VPS pe** hain. Local `nifty_config.json`
   stripped hai. Local repo = code source-of-truth, par runtime state VPS.
3. **₹0 ya weird P&L dikhe** → TRAP #1, turant `price=0` rows query karo.
4. **"No price"/timeout/levels=0** → TRAP #2 (rate limit), `shared_ltp_cache` use ho raha hai?
5. **VPS pe crash/silent-fail par local theek** → TRAP #4 (Python version), VPS py_compile.
6. **Deploy ke baad bhi purana behaviour** → trader process restart hua? (dashboard restart →
   respawn), ya galat file/path (TRAP #5), ya ek **stale duplicate file** repo root pe shadowing
   kar raha hai (TRAP #9) — `find . -iname '<file>.py'` se confirm karo.
7. **Backtest "holiday/no data" deta hai par live trades exist karte hain** → TRAP #8, token
   expired hoga — poisoned empty-CSV marker delete karo, fresh token se re-run.
8. **Naya risk-gate check likh rahe ho** → fail-closed default rakho (TRAP #10), fail-open sirf
   deliberate, commented exception ho.
9. **"X feature already wired in" kisi purani note/changelog mein likha mile** → TRAP #12, trust
   mat karo bina live state dekhe (e.g. `dhan_feed.LIVE` non-empty hai ya khaali, `ps` se process
   chal raha hai ya nahi) — changelog "likha gaya" bata sakta hai, "kaam kar raha hai" nahi.
10. **Fix ke baad:** isi file me ek `LESSONS.md` entry add karo agar yeh dobara aa sakta hai.

---

## TRAP #12 — "Feature built" ≠ "feature verified end-to-end" (REST quietly covered for a dead WebSocket for ~10 days)

- **Symptom:** no error anywhere, no broken trade, nothing "wrong" — just every position's SL/TP
  ran on a slower, rate-limited path for ~10 days (2026-06-17 → 2026-06-27) while everyone assumed
  the WebSocket feed (built day 1, "Phase 1") was live. Found only because of an unrelated
  rate-limiter audit (TRAP #2 v2), not because anything actually broke.
- **Root pattern, two separate habits that combined to hide this:**
  1. **Feature shipped without an end-to-end live check.** `dhan_feed.py` was written, wired into
     `smart_order.py`, and never crashed loudly — `_ensure_feed_started()`'s `except: pass`
     (until the 2026-06-24 fix made it log) meant "the code exists and doesn't crash" was silently
     treated as "the feature works." Nobody ever asked `dhan_feed.LIVE` was actually non-empty.
  2. **REST calls always reached for the narrowest endpoint.** Every LTP call in this repo uses
     `/v2/marketfeed/ltp` (LTP only) — copy-pasted forward into every new file (manual order, risk
     gate, every `_TRADERS/*.py`) — even though Dhan also offers `/v2/marketfeed/ohlc` and
     `/v2/marketfeed/quote` (same call, richer response: OHLC/volume/avg-price/buy-sell-qty too).
     Once one file did it the narrow way, every later file copied the same pattern without anyone
     re-checking what Dhan actually offers.
- **Permanent guard:** TRAP #11's fix (verified WebSocket connect on the actual VPS, not just "the
  code compiles") is the direct fix for habit #1. For habit #2: when adding a new REST quote call,
  check whether `/quote` (richer, same cost) fits before defaulting to `/ltp` — there's no extra
  rate-limit cost since `dhan_rate_limiter` gates per-call not per-field.
  - **2026-06-27 follow-up:** Open Positions' LTP (`dhan_feed.get_quote()` calls already in
    `trader_dashboard.py` + the `/api/ltp-stream` SSE route) was ALREADY wired to prefer the live
    feed before REST — it just never benefited because the feed itself was dead (TRAP #11). Once
    fixed, it works automatically, no extra change needed there.
  - **Quick Order widget (`/api/option-ltp`) was the one path NOT wired to the feed at all** — it
    had its own independent REST-only cache (`_ltp_cache`, was 30s TTL specifically to dodge
    DH-904 since every call hit Dhan directly). Fixed: now tries `dhan_feed.get_quote()` for both
    the index price and CE/PE premiums first, REST only for whatever the feed doesn't have yet;
    REST calls that DO still happen now go through `dhan_rate_limiter` (were missed in the
    original rate-limiter wiring pass — found while doing this fix); cache TTL dropped 30s→2s
    since a feed-served read costs Dhan nothing, so a short TTL no longer means more Dhan calls.
- **General principle worth re-reading before any "Phase N" feature claim:** a feature is not
  "done" until someone has watched its actual runtime state (a populated dict, a non-empty log
  line, a real packet on the wire) — not just "the code imports and doesn't throw." Apply this to
  any future "X is wired in" claim in this file or `ARCHITECTURE_LOG.md` — re-verify live state,
  don't just trust the changelog entry (this is also TRAP #3's lesson, one level up: don't trust
  what *should* be running, check what *is*).
- **Fast detect:** for any "live feed"/"webhook"/"background daemon" feature, the verification
  command should always be "show me the populated runtime state right now" — e.g.
  `dhan_feed.LIVE` non-empty during market hours — not `grep -c "started successfully"` in a log.

---

## TRAP #13 — Zerodha (Kite) was a broker name, not a broker — `get_broker("kite")` always crashed 🔴🔴🔴

**Symptom:** any live position opened with `broker="kite"` could never be auto-squared-off (SL hit,
target hit, RMS max-loss breach, 3:15 EOD) — `pos_monitor_loop`'s `_do_squareoff()` would throw
before even reaching the order call, leaving the position open forever (retried every 5s cycle,
failed identically every time). Pre-entry funds/margin checks for Kite would also throw.

**Root cause:** `brokers/__init__.py`'s `get_broker("kite")` does `from .kite_broker import
KiteBroker` — but `kite_broker.py` had ONLY loose module-level functions (`place_order`,
`get_positions`, `get_ltp`), never a `KiteBroker(BaseBroker)` class. The import always raised
`ImportError`. This sat undetected because `dhan_real_margin`/Dhan-only code paths never exercised
it, and no live Kite order had been placed through the engine yet — "feature built ≠ feature
verified" (see TRAP #12) struck again, one layer up: this time the feature wasn't even built, just
named in a config comment (`"broker": "dhan" | "kite"`).

**Second bug found in the same area:** the old `dhan_sym_to_kite()` string-converter assumed Dhan's
trading-symbol format was `NAME-MonYYYY-strike-CE/PE` (e.g. `"NIFTY-Jun2026-23950-CE"`) — but Dhan's
real format includes the **day**: `"NIFTY-28Jun2026-23950-CE"`. The old code's `mon_yr[:3]` therefore
sliced `"28J"` as the "month", producing a garbage/non-existent Kite symbol on every single call —
every Kite order would have been rejected (or worse, silently routed to whatever symbol that garbage
string happened to collide with). NIFTY's weekly-expiry Kite symbol format (single-letter month +
day code) also can't be represented by *any* string-guess scheme — only an exact instrument-dump
match handles it correctly.

**Fix (2026-06-28):**
- `brokers/kite_broker.py` now has a real `KiteBroker(BaseBroker)` class — `place_order`/`quote`/
  `funds`/`intraday_candles`. Per the file's own documented design ("DATA always Dhan, ORDERS via
  Kite"), `quote()`/`intraday_candles()` delegate to `DhanBroker` rather than calling Kite's own
  (separately rate-limited) market-data endpoints.
- New `resolve_kite_symbol()` — matches Dhan's `(name, expiry date, strike, CE/PE)` against Kite's
  own `kite.instruments("NFO")` dump (cached per day) for an **exact** symbol, format-agnostic
  (works for both monthly and weekly expiries). The old `dhan_sym_to_kite()` string-guess (now with
  the day-parsing bug also fixed) is kept only as a last-resort fallback if the instrument dump is
  unreachable, and logs loudly when used so a wrong-symbol order is never silent.
- New `kite_rate_limiter.py` — Kite Connect has its own separate account-wide rate limit from Dhan's;
  reusing `dhan_rate_limiter` would have been wrong (different account, different quota). Same
  sqlite cross-process token-bucket pattern, own DB file, conservative default (3/sec, 1 reserved
  for "order" priority).
- `kiteconnect` package was **not installed anywhere** (not local, not VPS) — every Kite call would
  also have failed at `import kiteconnect` regardless of the class fix. Installed + pinned in
  `requirements.txt`.

**Permanent guard:** before trusting ANY "broker: X" config option works, actually instantiate
`get_broker(X)` and check it has every method the engine calls (`place_order`, `quote`, `funds`) —
`hasattr`/abstract-class enforcement catches a missing implementation at import time instead of at
3 AM when a live position can't be closed.

**Fast detect:** `python -c "from brokers import get_broker; get_broker('kite').name()"` — if this
raises, no Kite-routed position (entry OR exit) can ever work, full stop. Run this after any change
to `brokers/` before assuming a second broker is live-ready.

---

## TRAP #14 — A live order's `ok` flag was always True, even when the broker rejected it 🔴🔴🔴

**Symptom:** if a real (Dhan or Kite) order got rejected — bad symbol, insufficient margin, no
F&O permission, price-band/freeze — `smart_order.execute()` still returned `{"ok": True, ...}`.
Every caller (`webhook_executor`, `universe_trader`, `range_trader`) trusts `ok` to decide whether
to start tracking a position. Result: a strategy could believe it has an open position (and run
SL/TP/EOD logic against it) that **never actually existed at the broker** — or, just as bad, a
rejected EXIT could be recorded as closed while the real position stays open and unmanaged.

**Root cause:** `res = {"ok": True, ...}` was set once, early, before the live branch — the live
branch only updated `status`/`reason`/`order_id`, never re-derived `ok` from the broker's actual
response. A second, sneakier layer of the same bug: brokers' initial HTTP response (`200`/accepted)
is NOT the same as "filled" — Dhan and Kite both confirm price-band/freeze rejects **asynchronously**,
a moment after the initial accept. Nothing re-checked that later state; `_dhan_live_fate()` (in
`trader_dashboard.py`) already solved this exact problem for the MANUAL/bulk order button, but
`smart_order.execute()` (the path every strategy/webhook actually uses) never got the same fix.

**Fix (2026-06-28):**
- `BaseBroker.order_status(order_id)` (new, optional — default `None` so unimplemented brokers
  degrade gracefully) — re-query a placed order's CURRENT status. Implemented for both
  `DhanBroker` (`GET /v2/orders/{id}`) and `KiteBroker` (`kite.order_history(order_id)`).
- `smart_order.execute()`: after placing a live order, (a) an immediately-rejected response now
  flips `res["ok"] = False`; (b) for a non-terminal status (accepted/pending), sleeps ~1.2s then
  calls `broker.order_status()` once and re-derives `ok`/`status` from the CONFIRMED result before
  anything gets persisted to `order_store` or returned to the caller.
- `_TRADERS/range_trader.py`'s `place_order()` (raw Dhan REST, doesn't go through `smart_order`)
  got the same async-confirm treatment directly — HTTP 200 no longer means "filled" there either.

**Permanent guard:** any new execution path that calls a broker's `place_order` and expects the
caller to trust `res["ok"]`/a boolean return must do the same accept-vs-confirmed two-step. Don't
copy the OLD (pre-fix) `smart_order.execute()` pattern from memory/an old session transcript.

**Fast detect:** grep for `"ok": True` set unconditionally before a broker call, or a `place_order`
wrapper that returns `True` straight off an HTTP `200` with no follow-up status check.

---

## TRAP #15 — Two different hedge configs for the same feature (range_trader vs webhook_executor)

**Symptom:** the RMS Risk tab's "🛡️ Auto-Hedge" card (Min Strikes / Max Premium ₹) only ever
affected `range_trader` (`ARS_CHAIN_V1`) — setting it for `webhook_v1` in that same UI table looked
like it should work (the row exists, same inputs) but silently did nothing, because
`webhook_executor.py`'s hedge code read its OWN separate `cfg["hedge_offset_strikes"]` field
(no Max Premium support at all) instead of `risk_gate.hedge_config()`.

**Root cause:** the hedge feature was built twice, in two sessions, against two different config
sources — `webhook_executor.py` first (its own per-webhook field), then `range_trader.py` +
`risk_gate.hedge_config()` + the RMS tab UI later, without going back to unify the older path.

**Fix (2026-06-28, two passes):** first pass made `webhook_executor._do_entry()` read
`risk_gate.hedge_config(strat)` instead of its own field — fixed the symptom, but the offset-walk
math still lived in TWO places (`smart_order.place_hedge_if_configured()` and
`range_trader.resolve_hedge_contract()`), which is exactly how this trap happened the first time —
so it was re-extracted a second pass into ONE function, `strategy_safety.compute_hedge_target()`
(resolution only, no placement — see `strategy_safety.py`'s module docstring). Both
`range_trader.py` and `smart_order.place_hedge_if_configured()` (used by `webhook_executor.py`)
now call this single function; `range_trader.resolve_hedge_contract()` was deleted entirely. The
RMS pre-trade gate (drawdown/concentration/capital/broker-funds) got the same treatment —
`strategy_safety.gate_entry()` — since it was independently hand-rolled in `range_trader.py`,
`webhook_executor.py`, AND `universe_trader.py` with the same drift risk. See CLAUDE.md
"Building a new strategy" (Critical Rule 8) for the checklist a new strategy file should follow.

**Permanent guard:** when a config knob is meant to be shared across multiple strategies, put it
in ONE place (`risk_gate.py` + the Risk tab) from the start — a strategy-local field for something
that "should probably apply everywhere" is how this split happened.

**Fast detect:** if a strategy's hedge "isn't working" but `hedge_offset_strikes`/`hedge_max_premium_rs`
IS set in the Risk tab for that strategy id, check whether that code path actually calls
`risk_gate.hedge_config()` or still reads its own local config key.

---

## TRAP #16 — `cfg["symbols"]` saved as a comma-string silently traded ZERO symbols 🔴🔴🔴

**Symptom:** `ARS_CHAIN_V1`'s log showed `[WHITELIST] trading 0 liquid names; dropped 195 illiquid:
N,I,F,T,Y,,,B,A,N,K,N,I,F,T,Y,...` — every config symbol name spelled out letter by letter. The
strategy was live (`active: true`, process running, no errors, no crash) but **placing zero entries
across every symbol**, found by chance while restarting for an unrelated deploy on 2026-06-28 — could
easily have gone unnoticed through a whole live trading day otherwise (process "running" ≠ "doing
anything", same family of lesson as TRAP #12).

**Root cause:** `nifty_config.json["ARS_CHAIN_V1"]["symbols"]` was stored as a single comma-joined
string (`"NIFTY,BANKNIFTY,RELIANCE,..."`), not a JSON list. `range_trader.py` did
`symbols = cfg.get("symbols", ["NIFTY"])` and then `for symbol in symbols` — Python happily iterates
a STRING character-by-character with no error, so every "symbol" was a single letter, which then
failed the liquid-stock whitelist filter (single letters match nothing in `universe.LIQUID_PREMIUM`
or the index set) and got silently dropped to an empty list. **`health_check.py` already had a
defensive parser for exactly this** (`_symbols()`, with a comment explicitly calling out "VPS config
me kabhi-kabhi symbols ek string hota hai" from an earlier session) — but that fix was never carried
over into `range_trader.py` itself, the file that actually runs live.

**Fix (2026-06-28):** `range_trader.py` now splits `symbols` on commas/whitespace if it's a string,
same regex as `health_check.py._symbols()`, before any whitelist filtering or the main symbol loop.

**Permanent guard:** a defensive parser fixed in ONE file (here, a health-check/preflight tool) does
NOT protect the file that actually trades unless it's applied there too — preflight tools that
"work around" a data-shape bug instead of flagging it can mask the bug from the very check meant to
catch it (health_check.py's `--fire-test` never noticed because it parsed around the broken value
successfully).

**Fast detect:** any `[WHITELIST] trading 0 liquid names` (or any "trading 0 X" log line) is never
normal — grep for it after every restart. More generally: `python -c "import json; c=json.load(open('nifty_config.json'));
print(type(c['ARS_CHAIN_V1']['symbols']))"` should print `<class 'list'>`, never `<class 'str'>`.

---

## TRAP #17 — `dhan_feed.LIVE` was silently missing `volume` even though the WebSocket packet has it

**Symptom:** while building a live liquidity filter (needs day-cumulative volume per contract), found
`dhan_feed.LIVE[sec_id]` only ever had `ltp/bid/ask/bid_qty/ask_qty/oi/ts` — no `volume`, even though
the underlying Dhan Full-packet parser (`dhanhq/marketfeed.py`) returns a `"volume"` key in the same
dict `dhan_feed.py` reads `"OI"` from, two lines away.

**Root cause:** the 2026-06-27 `dhan_feed.py` rewrite (TRAP #11) copied over LTP/bid/ask/OI from the
Full packet but the original feature list never needed volume, so it was never added — not a bug in
the sense of wrong behavior, just an unused field nobody had reached for yet, until today.

**Fix (2026-06-28):** added `"volume": r.get("volume")` to the `LIVE[sid]` dict — zero new Dhan calls,
the data was already arriving in every Full packet, just not stored.

**Permanent guard:** before assuming a data field "doesn't exist" in a feed/API, check what the raw
packet/response actually contains (`inspect.getsource()` on the parser, like TRAP #11's rewrite did)
rather than what the current consumer code happens to extract — the two are not the same thing.

---

## TRAP #18 — `from .base_broker import BaseBroker` breaks when `kite_broker` is imported wrong, in 3 separate call sites 🔴🔴🔴

**Symptom:** Kite "Exchange → Save" request-token flow failed with `attempted relative import with
no known parent package`; separately, the live `rsi_v1` strategy had been crash-looping on the exact
same error since **2026-06-26** (3 days, unnoticed — its log just showed repeated tracebacks every
loop, nobody was watching that specific log file).

**Root cause:** `brokers/kite_broker.py` uses a relative import (`from .base_broker import
BaseBroker`) because it's a package module. Three call sites did
`sys.path.insert(0, BASE_DIR/"brokers"); import kite_broker` — this imports it as a **top-level**
module with no package context, so the relative import inside it has no parent package to resolve
against and crashes. The correct pattern (used everywhere else in this codebase, e.g.
`from brokers import get_broker`) is to put `BASE_DIR` (not `BASE_DIR/"brokers"`) on `sys.path` and
import `from brokers import kite_broker` — same module, package context preserved.

**Fix (2026-06-29):** `trader_dashboard.py` (`/api/kite-exchange-token`, `/api/kite-test-order`) and
`_TRADERS/01_rsi_v1.py` all changed to `from brokers import kite_broker`. Grepped the whole repo for
`import kite_broker` not preceded by `from brokers` to confirm no 4th copy was hiding somewhere.

**Permanent guard:** any module inside a package (anything with a relative `from .x import y`) must
ALWAYS be imported as `from <package> import <module>`, never as a bare top-level import with the
package's own directory shoved onto `sys.path`. If you see `sys.path.insert(0, .../"brokers")`
anywhere, that's the smell — the fix is almost always to point `sys.path` at the package's *parent*
instead and import properly qualified. **Fast-detect:** `grep -rn "import kite_broker" --include="*.py" .`
and confirm every hit is `from brokers import kite_broker`.

---

## TRAP #19 — Restarting the dashboard silently killed the live strategy traders it had spawned 🔴🔴🔴

**Symptom:** deployed an unrelated UI fix, ran `systemctl restart algo-dashboard` (routine, done many
times before) — a few minutes later noticed `ARS_CHAIN_V1`'s log had stopped advancing exactly at
the restart timestamp. `ps aux` confirmed: the `_TRADERS/range_trader.py` and `01_rsi_v1.py`
processes were **gone**, even though they're spawned with `Popen(..., start_new_session=True)`
specifically so they're supposed to survive the parent dying.

**Root cause:** `start_new_session=True` detaches the child from the parent's *process group/session*
(so it doesn't get killed by terminal signals, Ctrl+C, etc.) — but it does **not** remove the child
from the parent's systemd **cgroup**. systemd's default `KillMode=control-group` kills every process
in the unit's cgroup on stop/restart, session or no session. So every `systemctl restart
algo-dashboard` was quietly killing every strategy trader the dashboard had ever started, with zero
error or log line anywhere — they just stopped.

**Fix (2026-06-29):** added `KillMode=process` to `algo-dashboard.service` — now only the dashboard's
own main process receives the stop signal; detached children are left alone, exactly as the
`start_new_session=True` code already assumed they would be. Re-verified: restarted the dashboard,
confirmed via `ps aux` + unchanged trader PIDs that they survived.

**Permanent guard:** any systemd service whose Python process spawns long-lived detached children via
`Popen(start_new_session=True)` (or `subprocess.DETACHED_PROCESS` on Windows) needs `KillMode=process`
in its unit file — `start_new_session`/`setsid` alone is **not** enough under systemd's default
cgroup-based kill behavior. **Fast-detect after any dashboard restart:** `ps aux | grep -E
'_TRADERS|range_trader|01_rsi_v1'` — compare PIDs/start-times before and after; if they reset, the
service file is missing this.

**Related, same root issue:** `pos_monitor_loop`/`webhook_monitor_loop`/`auto_scheduler` used to run
as **in-process threads inside `trader_dashboard.py` itself** — meaning even with the cgroup fix
above, those three (SL/TP/EOD-squareoff, webhook trailing-SL, 9:10/15:30 scheduler) still paused for
the few seconds of every dashboard restart, since they live *inside* the very process being
restarted. Moved them into a separate `monitor_daemon.py` + its own `algo-monitor` systemd service —
now dashboard restarts (UI/route fixes) never pause live risk monitoring at all. **Rule going
forward: nothing safety-critical (SL/TP, squareoff, daily-loss breaker, webhook monitor) may run as a
thread inside the dashboard's own Flask process** — it has to be a separately-deployable, separately-
restartable unit, because the dashboard *will* get restarted often (every UI tweak).

---

## TRAP #20 — The "wide" live liquidity filter was silently narrowed back down by the static whitelist it was built to replace

**Symptom:** RMS Risk tab's "Live Liquidity Filter" (any-2-of-3 spread/volume/OI, ON by default, built
2026-06-28 specifically to widen the tradeable universe past the old 21-name static list) was on, but
`range_trader.py`'s log still showed `[WHITELIST] trading 7 liquid names; dropped 18 illiquid: ...`
every loop — the exact same narrow-universe behavior the new filter was supposed to have replaced.

**Root cause:** `range_trader.py` has its OWN older, separate static-whitelist filter
(`cfg["stock_whitelist"]` vs `universe.LIQUID_PREMIUM`, a fixed 21-name list) that runs at the very
top of the scan loop, **before symbols are even looked at individually** — completely independent of
`strategy_safety.check_contract_liquidity()`'s newer per-contract any-2-of-3 check, which only runs
later, per-symbol, at actual entry time. Building the new filter never removed the old one; they were
both active simultaneously, and the *earlier* one in the pipeline wins by dropping symbols before the
newer one ever sees them.

**Fix (2026-06-29):** `range_trader.py` now checks `risk_gate.liquidity_filter_enabled(strategy_id)`
before applying the static whitelist — if the live filter is ON for this strategy, the static
whitelist step is skipped entirely (full symbol list reaches the per-contract check instead).

**Permanent guard:** when a new feature is explicitly built to **replace** an existing rule (not
layer on top of it), grep for the old rule's code path and either delete it outright or gate it
behind "only if the new thing is OFF" — don't just add the new check alongside and assume the old one
is now irrelevant. Two filters with the same *intent* but different *mechanism*, both still wired in,
is the bug to watch for. **Fast-detect:** if a log line says some symbols/contracts were dropped for
liquidity/illiquidity reasons, trace which function actually logged it — `grep` the exact log message
text — before assuming it came from whichever filter you most recently touched.

---

## TRAP #21 — Option-premium fetch never checked the live WebSocket feed, despite it existing for exactly this

**Symptom:** log repeatedly showed `Option premium unavailable for NIFTY-...-PE (likely DH-904
rate-limit) — SKIPPING entry` even on a day with no apparent rate-limit pressure — confusing because
`dhan_feed.py` (TRAP #11/#12, live WebSocket tick feed) was supposedly already wired in for exactly
this kind of real-time price need.

**Root cause:** `range_trader.py`'s `place_order()` premium-fetch fallback chain was
`shared_ltp_cache → direct Dhan REST (/v2/marketfeed/ltp, with DH-904 backoff) → stale cache` —
`dhan_feed.LIVE`/`get_quote()` was never in that chain at all. Each new strategy/feature that needed
a price independently re-invented its own fallback chain (same pattern as TRAP #15's hedge-config
split) instead of reusing the one canonical "best available price" lookup, so the WebSocket feed's
benefit silently never reached this specific call site.

**Fix (2026-06-29):** `dhan_feed.get_quote()` added as the FIRST attempt (free, no Dhan REST call) in
`place_order()`'s premium chain. Also added `dhan_feed.add((seg, sec_id))` immediately after the ATM
strike is resolved (before `gate_entry`'s liquidity check, before `place_order`) — a just-resolved
contract has no tick yet (subscribing triggers a reconnect that takes a moment), so subscribing as
early as possible in the entry path gives the feed the most possible head-start before either the
liquidity check or the premium fetch needs data from it. Doesn't eliminate the REST fallback (still
needed for the first few seconds after any new contract is resolved) but should reduce how often it's
hit.

**Permanent guard:** whenever `dhan_feed.py`'s live feed exists and a new code path needs an
option/equity price, check `dhan_feed.get_quote()` first — never write a fresh REST-only fallback
chain from scratch (easy to do, since REST "just works" and is what most examples/docs show). **Also:**
any code that resolves a NEW contract (ATM strike, hedge leg, etc.) should call `dhan_feed.add()` on
it immediately, not wait until a price is actually needed — every millisecond of subscribe-to-first-
tick lag is lag some other code path (liquidity check, SL/TP monitor) will also pay.

---

## TRAP #22 — `resolve_kite_symbol()` assumed Dhan's trad_sym always has a day — wrong for INDEX options 🔴🔴🔴

**Symptom:** first-ever live Kite test order (NIFTY ATM PE, via the new Quick Order broker toggle)
was rejected by Kite with "The instrument you are placing an order for has either expired or does
not exist." `resolve_kite_symbol()` had returned `None` (silently), so the code fell through to the
`dhan_sym_to_kite()` string-guess fallback, which produced `NIFTYN2024100PE` — not a real Kite symbol
at all.

**Root cause:** `_parse_dhan_trad_sym()` assumed every Dhan trad_sym is day-inclusive
("RELIANCE-28Jun2026-2500-CE" — true for stock options) and sliced `dmy[:2]` as the day. NIFTY's
actual trad_sym from `dhan_master.get_option_contract()` is `"NIFTY-Jun2026-24100-PE"` — **no day at
all** (`dhan_master.py` itself documents this: "Same trading symbol... can map to multiple expiries
since the day is not in the symbol"). Slicing `"Ju"` as a day threw inside the try/except, expiry came
back as garbage/None, and the exact-match against Kite's instrument dump could never succeed — not a
rare edge case, this is true for **every single NIFTY/BANKNIFTY index option**, the two most-traded
instruments in the whole system. TRAP #13's original "day-inclusive" comment was correct for stock
options and silently wrong for index options — nobody had placed a live Kite index-option order
before this session to surface it.

**Fix (2026-06-29):** added `dhan_master.get_expiry_for_sec_id(sec_id)` — looks up the real expiry by
sec_id (always already known to the caller, from the same `get_option_contract()` call that produced
the trad_sym) instead of re-deriving it from a string that may or may not contain the day.
`resolve_kite_symbol(kite, trad_sym, sec_id=...)` and `KiteBroker.place_order()` now pass/use it.
Verified on VPS: same NIFTY ATM PE now resolves to `NIFTY26JUN24100PE` (real Kite contract).

**Permanent guard:** never reconstruct a date/identifier by parsing a string when the *original
structured value* (here: the sec_id → scrip-master row → real expiry) is already sitting one function
call away. String-parsing a symbol is a documented LAST RESORT in this codebase (TRAP #13's own
docstring says so) — but "last resort" still got used as the ONLY path for index options because
nobody had exercised that path live yet. **Fast-detect:** before trusting any "Dhan symbol format is
always X" assumption, check `dhan_master.py`'s own comments first (it already knew about this exact
gap) — and test the actual instrument class you're about to trade live (index vs stock options aren't
interchangeable here), not just whichever one happened to get tested first.

---

## TRAP #23 — RMS's "Global Max Loss %" (total-capital cap) got reused as a per-position option-premium SL 🔴🔴🔴

**Symptom:** the first-ever live Kite test order (NIFTY ATM PE, no explicit SL/TP set) got auto-closed
by `pos_monitor_loop` within ~20 seconds, on pure price noise, with `exit_reason` recorded blank.
User clarified: "max loss % humara total capital ka hai, option premium ka nahi" — i.e. the field was
never meant to be a per-position trigger at all.

**Root cause:** `_pos_monitor_check_one`'s legacy fallback block read `global.max_loss_pct`/
`max_loss_rs` (RMS Risk tab's "Global Max Loss %", labeled with placeholder "e.g. 25" — clearly meant
as a percentage of total capital) and applied it as `entry_px * (1 ± pct/100)` — a per-position stop
on the OPTION PREMIUM. 1% of an ~₹80 premium is ~₹0.80 — any position without an explicit SL tag
would get closed on the very next normal price tick. The SAME config fields were (correctly) already
being checked a few lines above via `risk_gate.daily_loss_breached()`, which treats them as a
cumulative ₹ cap against the strategy's realized+unrealized day P&L — the legacy block was a second,
differently-scaled consumer of the exact same numbers.

**Fix (2026-06-29):** removed the global/per-strategy fallback from the legacy per-position SL block
entirely. `max_loss_pct`/`max_loss_rs` now ONLY feed `daily_loss_breached()` (the correctly-scoped
cumulative check) — a position with no explicit `SL_TYPE`/`SL_VAL` tag and no `default_sl_rs` stamp
simply gets no automatic per-position SL from pos_monitor now, which is the actually-correct behavior.
Same commit also fixed the live-mode branch of `_do_squareoff` never tagging `exit_reason` onto the
closed order (paper-mode did; live didn't) — found while diagnosing why this trade's reason was blank.

**Permanent guard:** a config field's UI label/placeholder is a contract — "Global Max Loss %" with a
hint of "e.g. 25" describes a capital-percentage cap, not a premium-percentage one. Before reusing an
existing config value in a NEW code path, check what its *existing* consumer(s) already assume about
its units/scope (here: `risk_gate.py`'s own docstrings were explicit that these are cumulative/
total-capital fields) — don't infer meaning from the field name alone. **Fast-detect:** `grep -n
"max_loss_pct\|max_loss_rs" trader_dashboard.py risk_gate.py` and check every consumer agrees on what
the number is a percentage/amount *of*.

---

## TRAP #24 — A second `DhanContext` import crash (TRAP #11/#12's fix missed this call site)

**Symptom:** adding a Dhan-balance display (`DhanBroker.funds()`) crashed with `ImportError: cannot
import name 'DhanContext' from 'dhanhq'` — the exact same error TRAP #11 (2026-06-27) already
diagnosed and fixed, in a *different* file.

**Root cause:** TRAP #11's fix rewrote `dhan_feed.py`'s WebSocket-feed construction to match the
actually-installed `dhanhq==2.0.2` (no `DhanContext`/`MarketFeed` exported — only `DhanFeed`/
`dhanhq`/`marketfeed`/`orderupdate`). `brokers/dhan_broker.py`'s `_get_sdk()` — a *separate* call site
constructing the SDK client for `intraday_candles()`/`funds()` — had the identical broken
`dhanhq(DhanContext(cid, token))` pattern and was never touched in that pass, because nothing had
exercised `DhanBroker.funds()` or the broker-class candle path live yet (raw-REST call sites
elsewhere, e.g. `api_manual_order`'s direct `requests.post`, don't go through this class at all and
hid the gap).

**Fix (2026-06-29):** same fix as TRAP #11, second location — `from dhanhq import dhanhq as
_dhanhq_cls; self._sdk = _dhanhq_cls(self.cid, self.token)` (installed class takes
`(client_id, access_token)` directly, no context wrapper).

**Permanent guard:** when a dependency's API shape changes (or was always wrong vs. what's actually
installed), grep for EVERY call site of the old pattern across the whole repo, not just the one file
you were already working in — `grep -rn "DhanContext" --include="*.py" .` would have caught this
second site back on 2026-06-27. A fix that's "done" in one file but not grepped repo-wide is a fix
that's done by luck, not by coverage. **Fast-detect:** any code path through `brokers.DhanBroker`
that nobody has exercised live yet is a candidate for this exact bug until proven otherwise.

---

## TRAP #25 — Webhook timeouts are HTTP response timeouts, not execution failures (The Rate-Limiter Freeze)

**Symptom:** TradingView reports `Webhook delivery failed - request took too long and timed out` (at exactly 11:30:02), but the order is successfully placed in Dhan and the dashboard 40+ seconds later (e.g. 11:30:43).

**Root cause:** The webhook route `handle_signal()` executes synchronously, meaning it must finish order execution before returning the HTTP 200 `OK` to TradingView. TradingView gives up waiting after ~3 seconds. If the dashboard is spamming the Dhan REST API for missing option LTPs (due to a too-short cache TTL, e.g., 2 seconds) and triggers Dhan's 429 rate limit, the global rate limiter `dhan_rate_limiter.py` freezes all `ltp` requests for 8 seconds. When `handle_signal()` calls `smart_order.marketable_price()`, it falls back to REST `quote()` which requires an `ltp` token, so it gets stuck in line behind the dashboard's queued requests and waits out the 8+ second freeze. TradingView times out, but Python eventually gets the price and places the order via the VIP `order` rate limit bucket (which is immune to `ltp` freezes).

**Fix (2026-06-29):**
1. Added a 15-second cache (`_POS_CACHE_TTL = 15`) for missing open position LTPs in `api_positions_ltp`.
2. Restored `_LTP_CACHE_TTL = 15` in `api_option_ltp`.
3. This completely eliminates the dashboard-induced 429s, ensuring the rate limiter never freezes the `ltp` bucket, which allows `handle_signal()` to execute under 1 second and successfully respond to TradingView before the 3-second timeout.

**Permanent guard:** Never set REST cache TTLs shorter than the rate at which they are polled by the frontend if the websocket fallback is unreliable. A 15-second cache on a 4-second frontend poll ensures at most 1 request every 15 seconds, well within Dhan's limits. Also, remember that third-party webhook timeouts (TradingView) do NOT kill the Python thread processing the request.

---

## TRAP #26 — `brokers/dhan_broker.py` order body missing `disclosedQuantity`/`afterMarketOrder` — every order via `smart_order.execute()` rejected with DH-905 🔴🔴🔴

**Symptom:** 100% of `[BROKER-SHADOW]` (and would-be `[BROKER]` live) order attempts reject with
`HTTP 400 — {"errorType":"Input_Exception","errorCode":"DH-905","errorMessage":"Missing required
fields, bad values for parameters etc."}` — every symbol, every price, every qty, no exceptions
(23/23 in `ARS_CHAIN_V1.log` history). Paper fill logs look completely normal, so this hides
silently behind shadow-live testing or behind a strategy nobody's pushed live yet.

**Root cause:** `DhanBroker.place_order()` (`brokers/dhan_broker.py`) builds the Dhan `/v2/orders`
POST body with `dhanClientId`/`transactionType`/`exchangeSegment`/`productType`/`orderType`/
`validity`/`securityId`/`tradingSymbol`/`quantity`/`price`/`triggerPrice` — but is **missing
`disclosedQuantity` and `afterMarketOrder`**, which Dhan's v2 API requires. The older, independently
written "proven working live" scripts (`_TRADERS/rsi_trader.py`, `_TRADERS/nifty_ema_trader.py`,
`_TRADERS/01_rsi_v1.py`) all include both fields and work fine — only the newer shared
`brokers/dhan_broker.py` (used by `smart_order.execute()`, i.e. `range_trader.py` /
`webhook_executor.py` / `universe_trader.py` — the entire "Best-in-class Universe System" stack)
had the gap. Found 2026-06-29 while ARS_CHAIN_V1 was live — meaning real entries would have
silently failed to place at the broker (the in-memory paper-equivalent fill still logs, and
`smart_order.execute()` correctly flips `res["ok"]=False` on the reject so no phantom position
gets tracked — but the live strategy was effectively placing zero real orders).

**Fix:** Added `"disclosedQuantity": 0` and `"afterMarketOrder": False` to the body dict in
`DhanBroker.place_order()`. Matches the legacy scripts' working payload shape exactly.

**Permanent guard:** Any NEW broker-call site that builds a Dhan order body by hand (not via
`DhanBroker.place_order()`) must diff its field list against this function or a known-working
legacy script — don't assume "it returns HTTP 200 reach" means the body is complete; DH-905 is
Dhan's generic catch-all and will fire even when auth/sec_id/segment are all correct.

**Fast detect:** If `[BROKER-SHADOW]` or `[BROKER]` lines show DH-905 on literally every attempt
regardless of symbol/price/qty (100% reject rate, no pattern), suspect a missing-required-field
payload bug before suspecting margin/liquidity/symbol issues — a real margin/liquidity reject
shows a *different* Dhan error code and won't be 100% across every contract.

**Found 3 more independently-built sites with the exact same gap (2026-06-29):**
`trader_dashboard.py`'s manual-order route (`/api/manual-order`), single-leg close
(`/api/close-position`'s raw body), and the `/api/debug-test-order` route — none of them go through
`DhanBroker.place_order()` (they POST to `/v2/orders` directly), so fixing the broker class didn't
cover them. All three now have `disclosedQuantity`/`afterMarketOrder` added too. This is exactly the
kind of drift the permanent guard above warns about — there is no single chokepoint for "build a
Dhan order body" in this codebase, so this bug class will keep recurring at new call sites until
that's consolidated (a real `dhan_master.build_order_body()` helper would close this permanently).

---

## TRAP #27 — `risk_gate.py` deploy drift: `default_broker()` existed locally/in git but was never scp'd to the VPS 🔴🔴🔴

**Symptom:** A real ARS_CHAIN_V1 signal fires (`SIGNAL SELL ICICIBANK @ 1387.50`), then immediately
`ERROR ORDER ERR ICICIBANK-Jun2026-1390-CE: module 'risk_gate' has no attribute 'default_broker'`.
No order reaches Dhan; `place_order()`'s `if not place_order(...): continue` guard correctly skips
marking a phantom position, so there's no P&L corruption — but the entry signal is simply lost.

**Root cause:** `risk_gate.default_broker()` (added in an earlier session, fully committed to git)
was never actually copied to the VPS — a manual-scp deploy gap, not a code bug. The local file was
690 lines; the VPS's was 685 — a `diff` showed exactly the missing 5-line function. Because Python
caches imported modules in `sys.modules` per-process, even scp'ing the fix over doesn't take effect
until the live process (which already did `import risk_gate` once) restarts.

**Fix:** `diff` local vs. a freshly-scp'd-down copy of the VPS file to confirm exact scope before
redeploying (don't assume — confirm), scp the corrected `risk_gate.py`, then restart the live trader
process (checked for zero open positions first, per the standard live-restart safety check).

**Permanent guard:** This project's manual-scp deploy process (no CI, no `git pull` on the VPS) means
"committed to git" ≠ "live on the VPS" — they can silently diverge any time a file is edited locally
across multiple sessions and only *some* of the touched files get scp'd. After any session that
edited shared/imported modules (`risk_gate.py`, `smart_order.py`, `strategy_safety.py`,
`brokers/*.py`, etc.), diff the VPS copy against local before assuming a fix is live.

**Fast detect:** `module 'X' has no attribute 'Y'` on the VPS for a `Y` that demonstrably exists in
the local file (and git) is *always* this — a stale VPS copy, not a logic bug. `wc -l` or a real
`diff` (scp the VPS file to a temp path first) settles it in seconds.

---

## TRAP #28 — Restarting a live trader process silently orphans its own open positions' exit logic 🔴🔴🔴

**Symptom:** A strategy has genuinely open positions (visible in Orders & P&L), but its own
zone/ATR-based EXIT signal never fires for them again, even though the strategy is clearly running
and processing other symbols normally. No error logged — `EXIT_LONG`/`EXIT_SHORT` signals for
those specific symbols are just silently skipped.

**Root cause:** `_state` (the in-memory dict tracking `position`/`opt_sec_id`/`opt_qty` per symbol)
is process-local and resets to fresh defaults (`position: None`) every time the process restarts —
there was no persistence across restarts. The `elif signal in ("EXIT_LONG","EXIT_SHORT"): if
st["position"] is None: continue` guard (added 2026-06-17 specifically to stop fake exits from
stale historical data on a genuinely fresh start) can't distinguish "fresh start, nothing was ever
open" from "restart, but 4 positions are still open" — it treats both identically and skips the
exit either way. Found live 2026-06-29 after restarting ARS_CHAIN_V1 multiple times in one session
(deploying unrelated fixes) without checking the **actual** open-positions API first — checking only
the log tail for recent `SIGNAL` lines missed 4 positions opened hours earlier that never showed up
in that tail window.

**Why it wasn't worse:** These were paper-mode (zero real money), and `pos_monitor_loop` in
`trader_dashboard.py` is a *separate* process (the dashboard, not the strategy) that reads
open positions straight from the persistent `order_store` DB — independent of the strategy's
in-memory state — so SL/TP tags and the blanket 3:15 PM EOD squareoff still applied. A live-money
position hitting this same bug would have been stuck open with no risk control until that
blanket EOD squareoff, since the strategy's own exit path was the only thing actually skipped.

**Fix:** `_recover_state_from_order_store(strategy_id)` (`_TRADERS/range_trader.py`), called once
at the top of `main()` before the loop starts — reads today's open `order_store` positions for this
strategy (filtering `entry == "SELL"` to skip hedge BUY legs), derives `LONG`/`SHORT` from the
option contract's `-PE`/`-CE` suffix, and re-populates `_state` so the exit logic resumes for
positions that were already open when the process started.

**Permanent guard:** Before restarting ANY live trader process, check the **actual** open-positions
API/`order_store` (not just a log tail snippet) — a tail only shows recent lines and will miss a
position opened hours earlier with no recent activity. The state-recovery fix above also makes a
restart itself safe-by-default going forward, but verifying first is still the right habit.

**Fast detect:** A strategy clearly running + processing symbols normally, but a specific symbol's
EXIT never fires despite an obviously-stale/losing position sitting open for a long time → suspect
this exact bug. Check whether the process has been restarted since that position opened.

---

## TRAP #29 — Watch chart candles/zone boxes displayed ~5.5h ahead — IST offset applied twice

**Symptom:** New `/watch-chart` page (built 2026-06-29) showed candle timestamps and zone-box edges
roughly 5 hours 30 minutes ahead of the actual current IST time — e.g. dashboard header showing
3:13 PM but the chart's last candle labeled ~20:41.

**Root cause:** `_TRADERS/range_trader.py`'s `fetch_1m()` already converts Dhan's raw UTC epoch into
an IST wall-clock value on the way in: `pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=5,
minutes=30)` — so `df["time"]` is a naive timestamp that already *reads* as IST. Two new call
sites (`trader_dashboard.py`'s `/api/watch-chart-data`, and `range_trader.py`'s own
`zone_start_ts`/`zones_history` conversion in the watchlist snapshot) both did
`int(pd.Timestamp(row["time"]).timestamp()) + 19800` — the `+19800` (5:30 in seconds) is the right
move when the source is genuine UTC (that's the convention used elsewhere in this codebase, e.g.
`trade-chart-data`'s raw Dhan REST epoch), but here it was applied a SECOND time on data that was
already shifted, double-counting the offset.

**Fix:** Drop the redundant `+ 19800` in both new call sites — just
`int(pd.Timestamp(row["time"]).timestamp())`, since the shift already happened in `fetch_1m()`.

**Permanent guard:** Before adding a `+19800`/`-19800` IST conversion anywhere, check whether the
upstream data source (especially `range_trader.fetch_1m()`, which several call sites now consume)
already did the shift — grep the call chain for `Timedelta(hours=5` / `+ 19800` first. This
codebase has at least two different conventions in play (raw-UTC-from-Dhan vs. already-IST-shifted
`fetch_1m` output) and they look identical in code (`pd.Timestamp(...).timestamp()`) without
checking the source.

**Fast detect:** A lightweight-charts time axis off by a suspiciously round ~5.5 hours (or off by
exactly double that, ~11h, if both layers shift it) is always this class of bug, never a real data
issue — check every `+19800`/`Timedelta(hours=5, minutes=30)` in the chain from source to render.

---

## TRAP #30 — Closing only one leg of a hedge pair via the UI could orphan the other, naked, with no automatic protection 🔴🔴🔴

**The risk (raised by user 2026-06-29, not yet observed live):** A sold option + its auto-placed
far-OTM hedge BUY share a `group_id`. If only the hedge leg gets closed (SL hit, manual click,
anything) while the main SELL leg stays open, the position instantly becomes a **naked option
sell** — margin required for that is dramatically higher than for the hedged spread. If broker
funds can't cover the sudden jump, the broker can force-squareoff *other*, unrelated positions to
free margin, or simply start rejecting all new orders account-wide — a small mishap on one leg
cascading into account-wide disruption.

**What was already in place:** `pos_monitor_loop`'s `_do_squareoff()` (in `trader_dashboard.py`)
is already group-aware — when SL/TP/EOD closes ANY leg, it auto-closes every sibling sharing the
same `group_id` in the same pass. `/api/close-position-group` also already existed as a dedicated
"close both legs together" button next to the regular per-leg button on grouped positions.

**The actual gap found:** `/api/close-position` — the route wired to the regular per-leg
"BUY✕"/"SELL✕" button shown on EVERY open position (grouped or not) — had **no group_id
awareness at all**. Clicking that button on just the hedge leg (instead of the dedicated group
button sitting right next to it) would close only that leg, leaving the main SELL leg open and
unhedged with nothing flagging it.

**Fix:** `/api/close-position` now looks up the leg's `group_id` first; if found, it closes every
leg in that group together (same logic `/api/close-position-group` already used) regardless of
which button was clicked. There is no longer a single-leg-only path for a hedged pair through the
UI — the dedicated group-close button is now redundant (harmless) rather than the only safe option.

**Still open (flagged, not fixed — lower priority since the UI path above is now closed):**
(a) `strategy_safety.compute_hedge_target()`'s hedge *placement* is explicitly "best-effort" — if
the hedge contract can't be resolved or its order rejects, the main SELL leg silently stays naked
from the start with nothing tracking that it's unhedged differently from a properly-hedged one.
(b) No dashboard alert specifically calls out "this open position has no hedge" — RMS Risk tab
shows margin/capital usage in aggregate, not per-position hedge status.

**Addendum (2026-06-29, same day) — the automatic side could fail silently too.** User asked
specifically: can the hedge leg's *own* SL ever fire and close it independently of the main leg?
Confirmed by reading the actual placement call (`_TRADERS/range_trader.py`'s hedge `place_order()`)
— the hedge BUY leg is placed with **no `extra_tags`**, so it never gets an `SL_TYPE`/`SL_VAL` tag,
and `_pos_monitor_check_one()`'s SL/TP logic (both the generic and legacy paths) only reads tags
already present on *that* position — so the hedge genuinely has no independent SL/TP trigger of its
own. The mechanism that actually matches what the user was likely seeing: `_do_squareoff()`'s
group-aware sibling-close (whenever EOD/RMS/the main leg's own SL closes ONE leg, it closes the
other too) only tried 2 price sources (live feed, then REST) for the SIBLING specifically — not the
3rd "stale shared-cache" tier the PRIMARY leg's own check already had. If both failed at that exact
instant (a real possibility — Dhan rate-limits, a feed hiccup), the sibling was silently left open,
unhedged, with nothing retrying it until 3:15 PM EOD caught it hours later.

**Fix:** (1) sibling-close now tries the same 3-tier fallback (feed → REST → `shared_ltp_cache`
stale) as the primary leg. (2) If even that fails, the sibling's `sec_id` is queued in a new
`_pending_group_close` dict and forced through on the very next cycle (5s later) the moment its
own price resolves — checked first thing in `_pos_monitor_check_one()`, ahead of every other
SL/TP/EOD check, since this leg is leaving regardless of its own trigger state. This bounds the
orphan window to a few failed 5-second retries instead of "until 3:15 PM, hours away."

---

## TRAP #31 — `fetch_daily()`'s last row isn't always "today", silently shifting EVERY symbol's pivot/PD_H/PD_C/PD_L levels by a full trading day 🔴🔴🔴

**Found 2026-06-29**, user noticed (LT trade-chart vs TradingView side-by-side) that the
pivot/R1-R5/S1-S5/PDH/PDC/PDL levels our dashboard showed didn't match TV's own
`Ars_Auto_Rev_Chain` indicator on the same symbol, same day, at all — not a small rounding
difference, off by ~30-50 points on LT.

**Root cause:** `_CHARTING/zones.py`'s `build_key_levels(daily_df, ...)` hardcodes the assumption
`daily_df.iloc[-2] = yesterday, iloc[-1] = today` (a comment says so explicitly). That's true for
the backtest tools (`backtest_engine.py`, `validate_strategy.py`) because THEY deliberately slice
`daily_df` to end exactly at their simulated "today". It is **not** true for the live caller
(`_TRADERS/range_trader.py`'s `fetch_daily()` + `main()` loop) — Dhan's `/v2/charts/historical`
daily endpoint never returns a partial bar for the still-forming today, and was found to sometimes
lag by 2+ trading days (e.g. on Monday 2026-06-29, LT's last available daily row was **Thursday
2026-06-25** — Friday's row was simply missing from Dhan's response). So `iloc[-2]` silently
resolved to **Wednesday**, a full extra day stale, and every pivot/PD level computed from it was
wrong — not just cosmetically on the watch/trade charts, but for the actual live entry-signal logic
too, since `daily_levels[symbol]` (built from this same function) is what `run_signal_engine()`
checks candles against for zone-touch entries.

**Fix:** Don't touch `build_key_levels()` itself (backtest tools rely on its current contract and
work correctly). Instead, in `range_trader.py`'s `main()` loop right after `fetch_daily(symbol)`,
check if the last row's date actually equals real IST "today" (`ist_now().date()`); if not, append
a dummy all-NaN row dated today. This restores the `-2=yesterday / -1=today` contract universally
regardless of how many days Dhan's feed is lagging by, without needing to know *why* it lagged
(holiday, vendor delay, gap — doesn't matter, the date check self-corrects for all of them).

**Fast-detect for next time:** if a symbol's TradingView pivot lines and our dashboard's don't
match, the FIRST thing to check is `fetch_daily(<symbol>).tail(3)` — if the last row's date isn't
real today, this bug (or its next variant) is back.

---

## TRAP #32 — PE option strike offset goes the WRONG way: `atm_idx + offset` picks ITM, not OTM 🔴🔴🔴

**Date:** 2026-06-30 | **Symptom:** Hedge/sell PE strike landed at 15600 (deep ITM) instead of ~13000 (OTM). 5-strike offset sent price UP the chain instead of DOWN.

**Root cause:** `dhan_master.get_option_contract()` did `target_idx = atm_idx + offset` for BOTH CE and PE. For CE, higher index = higher strike = more OTM ✅. For PE it's the opposite — higher strike = more ITM, you need to go LEFT (lower index) to go OTM.

**Fix (`dhan_master.py`):**
```python
if option_type == "PE":
    target_idx = atm_idx - offset   # PE: lower strike = more OTM
else:
    target_idx = atm_idx + offset   # CE: higher strike = more OTM
```

**Permanent guard:** Whenever you add a new offset param for any option type, verify the direction by printing `(atm_strike, target_strike, option_type)` before deploying. CE offset +2 → strike goes UP. PE offset +2 → strike goes DOWN.

**Fast detect:** In logs, check `[HEDGE] contract resolved` line — if PE strike > ATM strike by more than ~50pts, direction is wrong.

---

## TRAP #33 — Hedge BUY leg routed to the WRONG broker 🔴🔴🔴

**Date:** 2026-06-30 | **Symptom:** Hedge order went to wrong broker — Kite blocked fresh MIS BUY for stock options in expiry week (physical delivery policy) — hedge silently failed.

**Root cause:** `_TRADERS/range_trader.py`'s `place_order()` called `risk_gate.default_broker()` which returned `"kite"`, but hedge was also being routed there. Main + hedge should BOTH go to the same `default_broker()` — whatever the user has selected as live broker.

**Fix:** Hedge call uses `default_broker()` (no override) — same broker as main leg:
```python
place_order(symbol, "BUY", actual_qty, ..., group_id=group_id)  # no broker_override — same as main
```
The `broker_override` param exists for cases where a specific leg MUST go to a different broker — but that is an explicit user decision, not a code assumption.

**Permanent guard:** Never hardcode a broker for any leg based on an assumption. Always follow `default_broker()` unless the user has explicitly configured a per-leg override.

**Fast detect:** After any hedge placement, check `[BROKER]` tag in log — should match the same broker as the main SELL leg.

---

## TRAP #34 — Kite MIS blocks fresh BUY on stock options in expiry week (physical delivery) 🔴

**Date:** 2026-06-30

**Symptom:** `[KITE ERR] Fresh buy orders are not allowed for stock options using MIS due to compulsory physical delivery. Try next month's expiry.` — hedge BUY fails silently on Kite when the contract is in its expiry week.

**Root cause:** Zerodha blocks fresh MIS (intraday) BUY orders on **stock options** (OPTSTK) in expiry week — physical delivery settlement risk. NIFTY/BANKNIFTY (OPTIDX) are NOT affected (cash-settled). The restriction is Kite-side, unconditional.

**Fix:** Hedge BUY orders placed via Kite use `product="NRML"` instead of `"MIS"`. Since we force-squareoff at 3:15 PM anyway via `pos_monitor_loop`, there is zero overnight risk from using NRML for the hedge.

**How it's wired:**
- `smart_order.place_hedge_if_configured()` → `execute(..., product="NRML")` (permanent, all callers)
- `range_trader.place_order()` hedge call → `product="NRML"` passed explicitly
- `KiteBroker.place_order()` now accepts `product` param: `"NRML"` → `PRODUCT_NRML`, else `MIS`
- `DhanBroker.place_order()` similarly: `"NRML"` → `"MARGIN"`, else `"INTRADAY"`

**Permanent guard:** Hedge BUY = always NRML (it's fine — 3:15 squareoff). Never use MIS for a hedge leg on Kite. Main SELL can stay MIS.

**Fast detect:** `[KITE ERR]` with "physical delivery" in logs → check product type of the BUY leg.

---

## TRAP #35 — Live order P&L recorded BEFORE broker confirms fill 🔴🔴

**Date:** 2026-06-30

**Symptom:** App shows a P&L position (profit/loss updating) even when the broker rejected or never filled the order. A limit order "zabardasti" entered the app's books the moment it was placed, not when it actually traded.

**Root cause:** `smart_order.execute()` logged the `[LIVE]` intended-fill line (which `order_store.record()` uses to build the P&L position) BEFORE firing the real broker order — and then only checked for async rejects as a best-effort afterthought. If the broker rejected (bad symbol, margin, price moved away), the P&L record already existed with no matching real fill.

**Fix:** In live mode, the flow is now strictly:
1. Place LIMIT order at bid/ask
2. Poll `broker.get_fill(order_id)` every 1.5s, up to 8s (5 attempts)
3. **`TRADED`** → log `[LIVE]` with **actual average fill price** → `order_store.record()` → return `ok=True`
4. **`REJECTED`** → log `[LIVE-SKIP]` → return `ok=False`, nothing recorded
5. **Timeout** (still PENDING after 8s) → log `[LIVE-PENDING]` → return `ok=False`, nothing recorded

Both `DhanBroker` and `KiteBroker` now implement `get_fill(order_id) → (status, fill_price)`.
Paper mode is unchanged — simulation records immediately (no real broker to wait for).

**Bonus:** When `TRADED` + actual `fill_price > 0` is returned, P&L uses the real average fill price (Kite: `average_price`, Dhan: `tradedPrice`) — not the theoretical marketable-limit estimate. Slippage is visible in log as `[FILL-ACTUAL] trad_sym 72.00 → 71.95 (−0.05)`.

**Permanent guard:** In `smart_order.execute()`, `[LIVE]` log line + `order_store.record()` are now INSIDE the `fill_st == "TRADED"` branch — physically impossible to record P&L before fill confirmation.

**Fast detect:** Check log — a real live entry should always show `[FILL-POLL] attempt N/5 -> TRADED` before `[LIVE]`. If you see `[LIVE]` without a preceding `[FILL-POLL]`, the guard was bypassed somewhere.

---

## TRAP #36 — Expiry-day positions held too long → physical delivery margin / ITM loss 🔴

**Date:** 2026-06-30

**Symptom:** On expiry day, Zerodha shows a banner: "Additional physical delivery margin applicable for ITM options." Short options that are borderline OTM at 2 PM can go ITM in the last hour — broker may auto-square-off with a penalty, OR block new orders due to margin spike.

**Root cause:** System was treating expiry day identically to any other day — waited until 3:15 PM EOD squareoff. Last hour on expiry is high-volatility, and a short option that was 15 pts OTM at 2 PM can easily flip ITM before 3:15.

**Three permanent guards added:**

1. **Earlier EOD on expiry day** (`EXPIRY_EOD_HM = (14, 55)`)
   `pos_monitor_loop` → `_pos_monitor_check_one()` — if `is_expiry_day(trad_sym, sec_id)` is True, squareoff tag `EXPIRY_EOD_SQUAREOFF` fires at 2:55 PM instead of 3:15 PM.

2. **ITM immediate squareoff on expiry day**
   Same function — if short option (`entry == "SELL"`) goes ITM on expiry day (`option_is_itm(trad_sym, spot_price)`), exits immediately with tag `EXPIRY_ITM_SQUAREOFF`. Spot fetched from `shared_ltp_cache` (index sec_id 13/25/27) or REST (stock options).

3. **No new entries after 2:00 PM on expiry day** (`EXPIRY_NO_ENTRY_AFTER_HM = (14, 0)`)
   `_TRADERS/range_trader.py` entry signal block — if time ≥ 14:00 AND `is_expiry_day(sec_id=last_known_opt_sec_id)`, entry blocked with `continue`. (A new entry at 2 PM that gets closed at 2:55 has only 55 mins of runway and disproportionate expiry risk.)

**New helpers in `risk_gate.py`:**
- `is_expiry_day(trad_sym=None, sec_id=None)` — checks today == contract expiry, tries trad_sym parse then dhan_master sec_id lookup
- `option_is_itm(trad_sym, spot_price)` — PE: spot < strike → ITM; CE: spot > strike → ITM
- Constants: `EXPIRY_EOD_HM`, `EXPIRY_NO_ENTRY_AFTER_HM` (change in risk_gate.py if needed)

**Fast detect:** On expiry day → check logs for `EXPIRY_EOD_SQUAREOFF` by 2:56 PM. If not seen for any open option position → guard didn't fire (check `is_expiry_day()` returned True for that sec_id).

---

## How to extend this file

- Naya recurring-trap milte hi (ya purana lautte hi) ek `TRAP #N` add karo — **problem se index,
  date se nahi.** Date-detail `ARCHITECTURE_LOG.md` me rehne do; yahan sirf **pattern + permanent
  guard + fast-detect.**
- Agar ek guard code me bhi daal sakte ho (central chokepoint), to woh memory/doc se behtar hai —
  doc bhula ja sakta hai, code-guard nahi. (Jaise TRAP #1 ka `order_store.record` tripwire.)

---

## TRAP #37 — `_net_rows` treats live OPEN-status orders as pairable legs → phantom completed trades + blank open positions 🔴🔴🔴

**Seen:** 2026-06-30. Dashboard: open positions blank, trailing floor never fires, NET panel "—".

**What happens:** `order_store._net_rows()` ran ALL rows through the netting algorithm. A live Zerodha/Kite short leg (`status="OPEN"`, side="SELL") + its hedge BUY leg (same trad_sym/strategy, `status="OPEN"`) got paired as a phantom "completed trade" (P&L ≈ 0). `open` list stayed empty → `_n_pos=0` → trailing-floor code took wrong branch → no squareoff ever.

**Permanent guard (in code):**
```python
_OPEN_ST = {"open"}
live_rows   = [r for r in rows if str(r.get("status") or "").lower() in _OPEN_ST]
closed_rows = [r for r in rows if str(r.get("status") or "").lower() not in _OPEN_ST]
# Only closed_rows go through Pass 1 + Pass 2 netting.
# live_rows go directly to opens list (show sell leg, skip hedge BUY).
```

**Fast detect:** `/api/orders?date=TODAY` returns `{"open": [], "details": [...]}` even though Zerodha shows live positions → check `status` column in `trades.db` (`SELECT status, COUNT(*) FROM orders GROUP BY status`). If "OPEN" rows exist but open=[] → trap active.

---

## TRAP #38 — `_trailing_peak_pnl = 0.0` on service restart wipes the daily highwater mark 🔴🔴

**Seen:** 2026-06-30. Strategy made ₹7246 profit peak; service restarted mid-session; `_trailing_peak_pnl` reset to 0 → 30% floor computed from 0 → never triggered → held positions all day.

**Permanent guard (in code):**
```python
# On startup, restore today's peak from file:
try:
    _phf = BASE_DIR / "data" / "peak_pnl_history.json"
    if _phf.exists():
        _fmtime = datetime.datetime.fromtimestamp(_phf.stat().st_mtime)
        if _fmtime.date() == datetime.datetime.now().date():
            _hist = json.loads(_phf.read_text())
            if _hist:
                _trailing_peak_pnl = max(v[1] for v in _hist)
except Exception: pass
```

**Fast detect:** After restart, check logs for `[TRAILING-LOCK] Restored peak ₹...`. If not seen → guard didn't fire → peak was reset to 0.

---

## TRAP #39 — `let` block-scope in JS: variable declared inside `if{}` invisible outside → silent ReferenceError 🔴

**Seen:** 2026-06-30. `let _tot = {g:0,tx:0,n:0,pts:0,inv:0}` was inside `if(sortedCompleted.length){ ... }` block. `window._realizedTot = _tot` was OUTSIDE. Browser threw `ReferenceError: _tot is not defined` but it was swallowed by a surrounding try-catch → open positions render silently aborted.

**Rule:** Declare loop accumulators BEFORE the `if` block that populates them. `let` and `const` are block-scoped — they don't leak out like `var`.

**Fast detect:** Wrap suspicious render functions in try-catch with `console.error` (never `/* ignore */`) → errors surface in DevTools console.

---

## TRAP #40 — Dhan `/v2/margincalculator` called per-position → 10+ second page freeze 🔴🔴

**Seen:** 2026-06-30. `risk_gate._leg_capital()` called Dhan margin API once per open position. Dhan rate-limits at ~1 req/sec. 10 positions = 10+ second wait. The `/api/orders` route awaits this → entire Orders tab freezes on every 4-second auto-refresh.

**Permanent guard:** Replace with local estimate in the `/api/orders` route:
```python
_mult = float(risk_cfg.get("global", {}).get("margin_multiplier", 5.0))
margin = qty * price * (_mult if side == "SELL" else 1.0)
```

**Rule:** Never call Dhan REST API in a per-item loop inside a Flask route that the UI polls every few seconds. Cache or estimate instead.


---

## TRAP #41 — Trailing squareoff fires → peak resets to 0 → strategy re-enters → squareoff fires AGAIN → infinite cycle 🔴🔴🔴

**Seen:** 2026-06-30. Squareoff fired 10 times in one session. Floor line visually dropped after each fire.

**What happens:**
1. `_trailing_peak_pnl` hits ₹7,246, MTM drops → squareoff fires
2. After squareoff: `_trailing_peak_pnl = 0.0` (reset so it doesn't re-fire)
3. Strategy (webhook/TV) has no idea squareoff happened → enters new positions
4. New peak builds to ₹6,116 → squareoff fires again
5. Floor drops on graph (₹4,587 → ₹2,992) because new peak is lower
6. Repeat 10+ times, burning the day's profit

**Permanent guards (in code):**

Guard 1 — Block new entries after squareoff fires:
```python
# trader_dashboard.py — on squareoff fire:
_flag = BASE_DIR / "data" / f"trailing_lock_fired_{date}.txt"
_flag.write_text(f"fired at {time}, peak was ₹{_daily_peak_ever:.0f}")

# webhook_executor.py — _do_entry():
if _trailing_lock_fired_today():
    return {"ok": False, "msg": "trailing profit lock fired today — no new entries"}
```

Guard 2 — Floor line never drops (graph):
- Track `_daily_peak_ever` separately (only goes UP, NEVER resets)
- History stores `v[3] = daily_peak_ever` (4th element)
- Graph reads `v[3]` for floor line, not `v[1]` (which resets after squareoff)
- Result: floor line is monotonically non-decreasing on the graph

**Fast detect:** Floor line dropping on graph = squareoff fired + positions re-entered. Check `data/trailing_lock_fired_*.txt` exists. Check `journalctl -u algo-monitor | grep TRAILING-LOCK` — if multiple fires same day → this trap.

---

## TRAP #42 — `_trailing_peak_pnl` and `_daily_peak_ever` are module-level globals shared between trader_dashboard.py and monitor_daemon.py via import 🟡

**Context:** `monitor_daemon.py` does `import trader_dashboard as td`. The module is imported ONCE. All globals (`td._trailing_peak_pnl`, `td._daily_peak_ever`) are shared — monitor_daemon's pos_monitor_loop modifies them, and the `/api/peak-pnl-history` route reads them. This is by design.

**But:** When `algo-dashboard` (Flask) restarts, it runs its own COPY of `trader_dashboard.py` module. The `[TRAILING-LOCK] Restored peak ₹7246` log seen at 14:12 came from the DASHBOARD process, NOT from monitor_daemon. The monitor_daemon was never restarted and kept its own `_trailing_peak_pnl` running continuously.

**Rule:** Don't confuse which process is printing `[TRAILING-LOCK]` logs. Check `journalctl -u algo-monitor` vs `journalctl -u algo-dashboard` separately. Squareoff is always logged by `algo-monitor` (monitor_daemon), never by `algo-dashboard`.

**Fast detect:** `journalctl -u algo-dashboard | grep TRAILING` → only startup restore messages. `journalctl -u algo-monitor | grep TRAILING` → actual squareoff events.

---

## TRAP #43 — No-price position held open indefinitely — SL, TP, Global Max Loss, and Trailing Squareoff all silently disabled 🔴

**Symptom:** Position in DB. SL set to ₹5000. Feed dead for that symbol. Monitor logs "CRITICAL: NO price for 6/12/18... cycles." Position stays open for hours. SL never fires. Global max loss never fires. Trailing profit lock never fires. Manual intervention required at EOD.

**Root cause:** `pos_monitor_loop`'s per-position check function returns immediately when `ltp <= 0`:
```python
if ltp <= 0:
    # ... log CRITICAL every 6 cycles ...
    return   # ← just returns. NOTHING ELSE HAPPENS.
```
No LTP = no SL check, no TP check, no RMS max-loss check, no trailing squareoff check — ALL monitoring is skipped. The position is effectively unmonitored.

**How it compounds:** If the no-price position is losing money (SL already blown), the monitor sees `unrealized = 0` (because it uses last known LTP = 0), so the Global Max Loss calculation is UNDERSTATED. The cap looks like it hasn't been hit even when the actual loss is ₹20,000+.

**Real incident (2026-06-30):** BAJFINANCE-Jun2026-990-CE. Entry 10:35. Feed dead 10:37 → 10:47+ (114+ cycles, ~570 seconds). SL ₹5000 never fired. Global Max Loss ₹10,000 never fired. At 14:28, a manual exit attempt was ALSO rejected ("Kite NFO disabled"), leaving the position permanently stuck.

**Fix (trader_dashboard.py):**
- `_NO_PRICE_EMERGENCY_EXIT_AFTER = 60` (60 cycles × ~5s = ~5 min)
- After 60 no-price cycles: LIVE position → `_do_squareoff(..., "NO_PRICE_EMERGENCY_EXIT", ...)` — `smart_order` uses its own REST fallback for pricing (better than holding forever)
- Paper position → log `🚨 MANUAL EXIT REQUIRED` (can't record ₹0, TRAP #1)
- Streak resets to 0 after attempt, so it fires again every 5 min if feed stays dead

**Additional bugs found in same incident:**
- Kite NFO segment was disabled — even manual exit attempts were rejected. **Check Zerodha console > Segment Activation if any Kite order fails with "NFO is disabled".**
- `shared_ltp_cache.get_stale()` also had no data for BAJFINANCE — suggests this symbol wasn't being polled at all, or was a wrong sec_id.

**Fast detect:** `journalctl -u algo-monitor | grep "CRITICAL.*NO price"` → see how many cycles. If streak > 60 and no "EMERGENCY EXIT" line follows → old code (before fix). After fix: `"NO-PRICE EMERGENCY EXIT"` log appears at cycle 60.

**Guard:** Every broker account must have FNO segment active. Verify once per account: Zerodha console > Segment Activation > NSE F&O = Active. Test with `/api/debug-test-order` before going live.

---

## TRAP #44 — "Feed dead" is often a ghost position — broker rejected exit, app still watching it 🔴

**Symptom:** Monitor logs "CRITICAL: NO price for X cycles" for a symbol. Feels like Dhan feed went dead. But market was open and other symbols worked fine.

**Root cause:** Exit order was placed at Kite/Dhan but got REJECTED (NFO disabled / already flat / manual close by user). App recorded the BUY exit leg as `status="OPEN"` (since broker confirmation never came). Now DB has:
- SELL entry → `status=OPEN`  
- BUY exit attempt → `status=OPEN`

Both legs show as "open positions". Monitor subscribes to the symbol's feed and polls every 5s. If Dhan feed has no data for that specific contract (expired, not subscribed, wrong sec_id) → `ltp_miss_streak` grows → "CRITICAL: NO price" every 6 cycles → LOOKS like feed is broken.

**Real incident (2026-06-30):** 5 symbols had ghost SELL+BUY "OPEN" pairs (HINDUNILVR, TCS, AXISBANK, BAJFINANCE, INFY). User had manually squared off at Zerodha in panic after app's exit orders were rejected due to NFO-disabled. App kept watching these "open" positions for hours. BAJFINANCE specifically had no Dhan feed data → CRITICAL every 30s → looked like feed failure.

**Distinguish from real feed failure:**
- Ghost position: `grep "CRITICAL.*NO price" log` — only 1-2 specific symbols affected, rest fine
- Real feed failure: ALL symbols suddenly show no price simultaneously

**Fix (manual — emergency):**
```sql
-- Run on VPS: python3 -c "..."
UPDATE orders 
SET status='externally_closed' 
WHERE trad_sym='SYMBOL-HERE' AND date='YYYY-MM-DD' AND status='OPEN';
```

**Fix needed (permanent, NOT yet built):** `/api/sync-positions` route — hits Kite `kite.positions()` + Dhan `GET /v2/positions` → compares against DB open legs → auto-marks anything flat at broker as `externally_closed`. Should be a button on the P&L tab "🔄 Sync from Broker". Call this whenever you manually close something at the broker directly.

**Fast detect (next time):** Check how many symbols are affected → if only 1-2 specific symbols → ghost position first, feed second. Check `SELECT trad_sym, side, status FROM orders WHERE status='OPEN' AND date='today'` — do SELL+BUY both show OPEN for the same symbol? → ghost confirmed.

**Prevention:** Never close positions at broker directly without telling the app. If you must (panic), immediately go to P&L tab → find the position → 🗑 book-close it so the app marks it closed. Until `/api/sync-positions` is built, this is the manual workflow.


---

## TRAP #45 — Max trades/day counter was RAM-only → reset on every service restart 🔴🔴

**Symptom:** Strategy fires way more than `max_trades_per_day` limit across a trading day. After any service restart (even for an unrelated fix), the counter resets to 0 — suddenly 10 more entries are allowed even though 8 already happened.

**Root cause:** `_trades_today` in `webhook_executor.py` was a plain Python dict — module-level, in-memory only. Any `systemctl restart algo-dashboard` or crash wipes it.

**Fix:** `daily_state.py` — thread-safe, IST-date-aware, disk-persisted daily counters. Reads from `data/daily_state.json` on startup. Auto-resets when IST date changes (not midnight UTC — market-aware).

**Usage:**
```python
import daily_state as _ds
count = _ds.inc("webhook", "ARS_CHAIN_V1|NIFTY")   # returns new count
count = _ds.get("webhook", "ARS_CHAIN_V1|NIFTY")   # read without increment
_ds.reset()                                          # called at day boundary
```

**Fast detect:** `grep "trades_today" logs/` — if you see >max_trades entries after a restart, this is the bug.

**Guard:** Every new counter in `webhook_executor.py` or any strategy that needs "per-day" semantics must go through `daily_state`, never a module-level dict.

---

## TRAP #46 — Kite token expiry not monitored — silent failure all day 🔴🔴

**Symptom:** All Kite-routed live orders fail silently from 09:15 onwards. Token expired overnight. No red banner, no alert — user only notices when checking P&L at EOD and realising zero trades went through.

**Root cause:** `health_check.py` checked Dhan token (JWT expiry) but had no Kite-specific check. Kite tokens expire after 24 hours (or manual revoke). The only existing check was Dhan's `api_auth_fail` flag — Kite errors set no such flag.

**Fix:** `health_check._check_kite_token()` — calls `kite.profile()` (lightweight read-only validity check) at 09:20 IST via systemd timer. If `TokenException` / 403 → sets `token_red=True` (cascades RED to ALL strategies in the report) + writes red banner to `data/downloader_alert.json` — visible immediately on dashboard.

**Fast detect:** `python -X utf8 health_check.py --report` → look for `kite_tok: FAIL` line.

**Guard:** Every morning after login, dashboard Control tab shows token status. If Kite shows RED → paste fresh access token. Kite token rotate = revoke+new from `kite.generate_session()` flow.

---

## TRAP #47 — Paper trades counted in daily loss limit → circuit breaker fires prematurely 🟡

**Symptom:** `risk_gate.daily_loss_breached()` returns True even though no real money was lost. All new entries blocked. User puzzled because Dhan/Kite P&L shows positive.

**Root cause:** `_today_realized_pnl()` summed ALL `order_store` details entries. Paper-mode button clicks during testing create entries with `mode="paper"`, `broker="dhan"`, `source="manual"` — these are phantom. A test sequence with heavy paper losses could trip the real-money circuit breaker.

**Fix:** `risk_gate._today_realized_pnl()` now filters `d.get("mode") != "paper"` before summing.

**Fast detect:** `grep "paper" data/order_store_YYYY-MM-DD.json | wc -l` — if >0 and circuit breaker is firing unexpectedly, this is the cause.

**Guard:** Same filter (`mode != "paper"`) applied in `counterfactual.py` — paper entries excluded from the algo P&L curve too.

---

## TRAP #48 — Trailing SL state (`_wh_state`) lost on restart → open positions become unmonitored 🔴🔴

**Symptom:** Service restarts mid-day while a webhook-placed position is open. After restart, `_wh_state` is empty → `monitor_tick()` has no record of the position → no trailing SL, no target, no 3:15 squareoff for that position. Only `pos_monitor_loop` (order_store-based) still watches it.

**Root cause:** `_wh_state` is a module-level dict in `webhook_executor.py`. Lost on any restart.

**Fix:** `_recover_wh_state()` runs once at module import. Reads `order_store.trades_for(today)["open"]`, parses `SL_VAL` from tags (stored as `"SL_VAL:72.5"` format), reconstructs `_wh_state[key]` with conservative SL (entry ± SL_VAL). Sets `_recovered: True` flag so monitor knows this is a recovery.

**Fast detect:** After a restart, `grep "RECOVER" logs/` should show a `[RECOVER]` line. If you see a webhook position in `order_store` with `status=OPEN` but no `[RECOVER]` log line → old code, state not restored.

**Guard:** `_recover_wh_state()` must be called at module-level init in `webhook_executor.py` — not inside a route handler.

---

## TRAP #49 — Corrupt peak P&L daemon entry → crash or silent data loss 🔴

**Symptom:** Dashboard Peak P&L graph crashes or shows a gap/flatline. Log shows TypeError or IndexError around the normalization code.

**Root cause:** `peak_pnl.json` is written by the daemon at every tick. A restart mid-write, a kill-9, or a daemon bug can write a partial/corrupt JSON array entry. The old `_norm()` assumed every entry was a 4-element list with valid numeric values — no guard.

**Fix:** `_safe_norm()` wrapper validates each entry:
1. Must survive `_norm()` (4-element list reorder)
2. Time must parse without exception
3. Value must be a real float (NaN check: `v == v`)
4. Time must be within market hours

Any entry failing any check is silently dropped.

**Fast detect:** `python3 -c "import json; d=json.load(open('data/peak_pnl.json')); print([e for e in d if len(e)!=4])"` — non-4-length entries = corrupt.

**Guard:** Daemon archives daily files to `data/peak_pnl_history_YYYY-MM-DD.json` at startup.

---

## TRAP #50 — Counterfactual tagging impossible when algo and manual trades share the same broker account 🔴

**Symptom:** Every trade tagged as "PANIC" (0 algo found) or every trade tagged as "ALGO" (all FIFO-matched). Symbol normalization between Dhan `trad_sym` format and Kite tradingsymbol format reliably fails. Time-based matching also fails because user closes algo positions mid-trade.

**Root cause (first attempt):** Trying to cross-reference `order_store` (algo) and Kite fills (manual) by symbol+time. This is fundamentally unsolvable when algo and user trade on the SAME Kite account — the fills are interleaved and indistinguishable.

**Root cause (real):** Architecture mismatch. The counterfactual question does not require per-trade tagging.

**Fix — two-broker architecture:**
- `order_store` = algo INTENDED trades (always the algo timeline)
- Kite FIFO = ALL actual fills (always the panic timeline)
- No cross-referencing. No symbol normalization. No time-matching.
- `intervention_cost = algo_pnl - actual_pnl` — positive = algo was better
- `counterfactual.py` builds two separate equity curves; Stats tab shows both

**June 30 live verification:**
- Algo (order_store): +₹3,263.25 (12 trades)
- Actual (Kite FIFO): -₹2,908.40 (23 matched trades)
- Intervention cost: ₹6,171.65

**Fast detect:** `counterfactual.py analyze(date)["summary"]` — check `algo_count` and `panic_count`.

**Guard:** Never try to cross-reference `order_store` and Kite fills by symbol+time. Kite = ALL actual, `order_store` = ALL algo. Two separate universes.


---

## TRAP #51 — TV EXIT webhook fires on manually-closed position → new accidental position 🔴🔴🔴

**Symptom:** TradingView fires an EXIT alert after you already closed the position manually at Zerodha. Webhook executor sees the position in `_wh_state`, sends a BUY-to-close order to Kite. Position is already flat → Kite opens a NEW naked long/short.

**Root cause:** `_do_exit()` in `webhook_executor.py` only checked `_wh_state` (in-memory) — never looked at order_store status or broker flat-check. `broker_sync` may have already marked the entry leg `externally_closed` in order_store, but webhook had no idea.

**Fix:** At the start of `_do_exit()`, two-layer flat check before placing any exit order:
1. Look up today's open legs in order_store — if the matching leg has `status=externally_closed` → skip
2. Ask `broker_sync.is_flat()` (cached, fast) — if flat → skip
In both cases: clear `_wh_state[key]["position"] = None` so the state is clean, log the skip, return without placing any order.

**Code location:** `webhook_executor._do_exit()` — guard block before `smart_order.execute()`.

**Fast detect:** After a manual exit, watch the log for the next TV alert. Should see:
`EXIT skip <key> — position already flat at broker (manually closed). Clearing _wh_state.`
If you see `[PAPER]/[LIVE] BUY ...` instead → old code still running.

**Guard:** This is a fail-open design — if the flat-check itself errors (broker API down), the exit proceeds (real open positions must be able to exit). Only definitively-flat positions are blocked.

---

## TRAP #52 — Manual exit P&L stays null in order_store → algo curve wrong in counterfactual 🔴🔴

**Symptom:** You manually close a position at Zerodha. broker_sync marks it `externally_closed`. But `pnl` field in order_store stays null forever. Dashboard shows ₹0 for that trade. Counterfactual algo curve understates algo_pnl for every manually-closed day.

**Root cause:** `broker_sync._run_sync()` previously only called `order_store.mark_externally_closed(row_id)` — just flipped a status flag. It never fetched the actual fill price from the broker, so no exit leg was recorded.

**Fix:** When `_check_flat()` returns True for a leg:
1. Call `broker.trades()` (new method on all brokers) to get today's fills
2. `_resolve_exit_price()` maps trad_sym/sec_id → fill price from the fills list
3. If found: `order_store.record()` an exit leg with `tags=["EXTERNALLY_CLOSED", "MANUAL_EXIT_BROKER"]`
4. Then (as before) `mark_externally_closed(row_id)` on the entry leg

**New broker methods:** `KiteBroker.trades()` → `kite.trades()` | `DhanBroker.trades()` → `GET /v2/trades`. Both defined in `BaseBroker` as `return []` (safe fallback if not implemented).

**Downstream fix:** This also fixes the counterfactual — once order_store has the correct exit price, `algo_trades` P&L is accurate and `intervention_cost = algo_pnl - actual_pnl` reflects reality.

**Fast detect:** After manual exit, next broker_sync cycle should log:
`[broker_sync] EXIT RECORDED — SYMBOL @ ₹XX.XX (broker fill price fetched, P&L now captured)`
If you see `fill price unavailable` instead → broker.trades() failed or symbol not in fills (check kite_rate_limiter / Dhan token).

---

## TRAP #53 — Hedge BUY closed manually → main SELL stays naked, zero alert 🔴🔴

**Symptom:** You close only the hedge BUY leg on Zerodha in a panic. App marks it externally_closed. Main SELL leg stays OPEN with no hedge. Margin required jumps sharply. No banner, no warning — you only find out when Zerodha sends a margin call SMS.

**Root cause:** broker_sync detected the hedge leg as flat and marked it closed — but had no logic to check if that leg was part of a group, or to alert when the sibling SELL leg was left exposed.

**Fix:** After marking any leg externally_closed, broker_sync now checks `group_id`:
- If the cleared leg has a `group_id`, look up siblings in the current `open_positions` list
- If any sibling has `entry == "SELL"` and is still OPEN → call `_write_naked_alert(sym, row_id)`
- `_write_naked_alert()` writes an error-level entry to `data/downloader_alert.json` — shows as a red banner on the dashboard immediately

**What the banner says:** `🚨 NAKED POSITION: SYMBOL — hedge leg was closed at broker but SELL leg is still open. Margin risk HIGH. Close the SELL leg immediately or replace the hedge.`

**What it does NOT do (intentional):** does not auto-replace the hedge (risky at unknown premium) and does not auto-close the SELL leg (user may want to keep it). It alerts and leaves the decision to the user.

**Fast detect:** Check dashboard for red banner after manually closing a hedge leg. Also:
`grep "NAKED LEG ALERT" logs/` → if line present → alert fired → check dashboard.

---

## TRAP #54 — broker_sync interval 120s → ghost position blocks entries for up to 2 min 🟡

**Symptom:** You manually close a losing position at Zerodha at 09:16 AM. A fresh signal fires at 09:17. Risk gate sees the ghost position's unrealized loss → `daily_loss_breached()` → entry blocked. Signal missed. broker_sync finally clears the ghost at 09:18. By then signal is gone.

**Root cause:** `broker_sync._INTERVAL = 120` — ghost detection ran every 2 minutes. In early morning when signals are dense, a 2-minute window is too large.

**Fix:** Reduced `_INTERVAL = 30` (30 seconds). At 30s intervals, broker_sync runs ~4x per session-minute — ghost cleared within one cycle in most cases.

**Cost:** Each cycle calls `broker.positions()` on every active broker. Rate-limited via `kite_rate_limiter`/`dhan_rate_limiter` at `"account"` priority (lowest, never starves orders). At 30s interval = 2 calls/min per broker — well within Kite's 3 req/s limit and Dhan's 1 req/s with account-priority queuing.

**Also added:** `broker.trades()` call per cycle when a ghost is detected (not every cycle — only when `_check_flat()` returns True, which is rare during normal trading).

---

## TRAP #55 — Peak-P&L day-rollover check compared a file's mtime against itself, right after rewriting it — reset could never fire 🟡

**Symptom:** Fresh trading day, zero trades, REALIZED/UNREALIZED/NET all ₹0 — but the Today's Peak P&L graph still showed yesterday's `Peak ₹7,916 | DD ₹7,916` and a stale `30% floor ₹5,541` line from the very first tick of the day.

**Root cause:** `pos_monitor_loop()` in `trader_dashboard.py` writes `data/peak_pnl_history.json` every 5s cycle, then immediately checked that same file's mtime to decide "is this a new trading day, should I archive+reset?" — but by the time the check ran, the file had already been rewritten (with "now"'s timestamp) a few lines earlier in the SAME iteration. `_mdate` (from the just-written file) was therefore always `== _today_str`, so `_mdate < _today_str` was always False and the archive/reset branch was permanently dead code. Since `trader_dashboard.py`/`monitor_daemon.py` run as long-lived systemd services (not restarted every trading day — see TRAP #19/#28 on why restarting is itself risky), `_trailing_peak_pnl`/`_daily_peak_ever` just kept carrying forward, unbounded, across every day the process stayed up.

**Fix:** Track the day explicitly in a module-level `_peak_day_str`, seeded at process-start. Each loop iteration computes `_today_str` and compares it to `_peak_day_str` **before** touching `_peak_pnl_history` or writing the file — a real day boundary now archives the old data (once) and resets `_trailing_peak_pnl`/`_daily_peak_ever`/`_peak_pnl_history` to 0/empty, independent of any file mtime.

**Also required:** `peak_pnl_history.json` already had the poisoned peak baked into every entry written that morning (before the fix landed) — restarting the service alone wasn't enough, since module-load-time restore blindly trusts "file mtime is today" without checking whether the DATA inside is stale. Had to also delete/archive the live file once, by hand, so the restored state started genuinely clean.

**Fast detect:** `python3 -c "import json; d=json.load(open('data/peak_pnl_history.json')); print(d[0], d[-1])"` at market open on a day with zero trades — if `v[1]`/`v[3]` (trail_peak/daily_peak_ever) are non-zero while `v[2]` (mtm) is 0 all the way through, the carry-forward bug is back.

---

## TRAP #56 — A "fix datetime.utcnow() deprecation" commit silently broke 5 call sites — RMS functions (NameError) + a live-safety flag (wrong exception → fails permissive) 🔴🔴🔴

**Symptom:** Restarting `algo-dashboard`/`algo-monitor` after an unrelated commit made `pos_monitor_loop` go completely silent — no `peak_pnl_history.json` writes, no `[TRAILING-LOCK]` log lines, no visible errors at all (only surfaced by a manual one-shot diagnostic script — see below). Separately, `_trailing_lock_fired_today()` started always returning `False`, meaning the "block new entries for the rest of the day" flag after a trailing-profit-lock squareoff silently stopped working.

**Root cause:** A commit (`3cbad3f`, made by an earlier session) did a mechanical find-and-replace of deprecated `datetime.utcnow()` → `datetime.now(timezone.utc)` across 6 files, but got the accompanying import fixups wrong in 2 different ways:
1. **`risk_gate.py`** (`_today_open`, `_today_realized_pnl`, `_strategy_day_pnl`) — the replace deleted the existing `import datetime` + `from datetime import timedelta` lines entirely, replacing them with only `from datetime import timezone`. Code still called `datetime.datetime.now(datetime.timezone.utc)` and bare `timedelta(...)` — both now `NameError`. These 3 functions back capital allocation, the daily-loss breaker, and concentration checks — silently broken every call.
2. **`trader_dashboard.py`** (`_trailing_lock_fired_today()` + the trailing-lock flag-write inside `pos_monitor_loop`) — the replace turned `from datetime import datetime as _dtc` into `from datetime import datetime, timedelta, timezone as _dtc`. In a **multi-name** `from X import a, b, c as d` statement, `as` only renames the LAST name — so `_dtc` ended up bound to `timezone`, not `datetime`. `_dtc.now()` → `timezone.now()` → `AttributeError` (timezone has no `.now()`). Worse: the flag-write instance of this bug sits *inside* `pos_monitor_loop`'s function body — merely having a local `from datetime import datetime, ...` statement ANYWHERE in a function body makes Python treat `datetime` as local for the WHOLE function (Python scoping, not order-of-execution), so the much-earlier `ist_now = datetime.now(timezone.utc)` at the top of the loop started raising `UnboundLocalError: cannot access local variable 'datetime'` on literally the very first line, every single cycle.
3. Both failure modes were swallowed silently: `_trailing_lock_fired_today()` has a bare `except Exception: return False` (fails PERMISSIVE, not safe — new entries were never actually blocked after a profit-lock fire, defeating the whole point of the flag), and `pos_monitor_loop`'s outermost `except Exception as e: print("Pos monitor error:", e)` has **no `flush=True`** — on a systemd service with default block-buffered stdout, the error sat unflushed for minutes, giving zero visible signal that the entire SL/TP/EOD-squareoff loop was down.

**Fix:** `risk_gate.py`'s 3 functions restored to `from datetime import datetime, timedelta, timezone` + `datetime.now(timezone.utc)`. `trader_dashboard.py`'s 2 `_dtc` spots restored to single-name `from datetime import datetime as _dtc` (matches pre-commit behavior — naive local-clock `.now()`, since the VPS system clock is already IST, confirmed via `date`). Audited the other 4 files the same commit touched (`counterfactual.py`, `daily_state.py`, `webhook_executor.py`, `_TRADERS/range_trader.py`) — all had `timezone` already available at module scope, so those specific replacements were fine.

**Fast detect:** After ANY mechanical/bulk find-and-replace touching imports, don't trust "it compiles" — `ast.parse()` only catches syntax errors, not `NameError`/`UnboundLocalError` from broken scoping. Grep the diff for `as _x` on a multi-name `from...import` line (the rename only applies to the last name) and for any `import X` line that got deleted/blanked without checking every use of `X` in that file. For a systemd-run daemon loop, always `flush=True` the outermost catch-all print — silence in a loop that's supposed to log every cycle is itself the bug signal, and you can't see it without the flush.

**Cost of not catching sooner:** ~10 minutes elapsed between the bad commit landing and the next process restart picking it up — no live positions were open in that window, so no real trading impact this time, but the class of bug (SL/TP/EOD-squareoff silently doing nothing, "new entries blocked" flag silently not blocking) is exactly the kind of thing that matters most when a position IS open.

---

## TRAP #57 — Restarting a live strategy (crash recovery / algo-monitor restart mid-day / VPS reboot) silently brings it back in PAPER mode, not LIVE 🔴🔴🔴

**Symptom:** Nothing visibly breaks — dashboard shows the strategy as "running," logs look normal. But real orders quietly stop going to the broker; everything after that point is paper-only, with zero alert.

**Root cause:** `auto_scheduler()` in `trader_dashboard.py` is the only thing that (re)starts a strategy whose process isn't currently running (checked via `get_pid()`, exact-match, no duplicate-spawn risk — that part is solid). It always called `/api/start?s=<key>&mode=paper` — hardcoded, regardless of what mode the strategy was actually last running in. `nifty_config.json` only ever stored `active: true/false`, never `mode`. Two independent triggers land on this same hardcoded path: (a) **VPS reboot** — `algo-dashboard`/`algo-monitor` are `systemctl enable`d and come back automatically, but a strategy that was LIVE comes back PAPER, silently. (b) **`algo-monitor` restarted during trading hours (9:10–15:30)** — `has_started_today`/`has_stopped_today` are function-local variables inside `auto_scheduler()`, reset to `False` on every fresh call of that function (i.e. every `algo-monitor` process restart, not just a calendar-day change) — so ANY restart in that window immediately re-runs the "start all active strategies" pass. Harmless no-op for anything already running (`/api/start` checks `get_pid()` first) — but if a strategy had crashed earlier and was LIVE, this "revives" it in PAPER. `health_check.py`'s preflight checklist doesn't check for a live/paper mismatch either — nothing flags this.

**Fix:** `/api/start` now writes `cfg[s]['mode'] = mode` alongside `active: true` every time it actually starts a process. `auto_scheduler()`'s restart pass reads `cfg[key].get("mode", "paper")` instead of hardcoding `"paper"` — a strategy comes back in whatever mode it was last explicitly started in, whether the trigger was a crash, an `algo-monitor` restart, or a full VPS reboot.

**Fast detect:** After ANY restart of `algo-monitor` or the VPS, check `get_mode(<strategy>)` (or the dashboard's live/paper badge) against what it should be — don't just check "is it running," check "is it running in the mode I expect."

---

## TRAP #58 — Ghost-position detection only ever checked one direction; the ~8s live fill-confirm window can create a completely untracked live position — NOT restart-only, confirmed live same day 🔴🔴🔴

**Symptom:** Originally found via scenario modeling (restart-risk analysis requested after TRAP #55/#56) — but **confirmed live the same day, twice, with zero restart involved**: `RELIANCE-Jul2026-1310-CE SELL 500 @ 35.90` (10:08:45) and `SUNPHARMA-Jul2026-1980-CE BUY 350 @ 6.25` (10:10:31, the hedge leg for a tracked SELL) both hit `[LIVE-PENDING] ... fill not confirmed in 8s` in `ARS_CHAIN_V1.log` — order accepted and apparently filled at Zerodha (both later confirmed present in `KiteBroker.positions_detailed()`), but `order_store.record()` never ran because `smart_order.execute()` returns early on a poll timeout, before reaching the record() call. **The real trigger is broader than a restart landing mid-poll — a slow/late fill confirmation is enough on its own, no restart required.** Far-OTM hedge legs (thin liquidity) are a likely repeat offender. The untracked-scan (this TRAP's fix, deployed same day) correctly caught both within one 30s cycle and wrote alerts — but alerts alone don't add SL/EOD protection for Kite (Dhan auto-adopts, Kite doesn't, by design — see Fix below), so the user had to manually intervene on RELIANCE while this was being fixed live.

**Root cause:** `broker_sync.py`'s ghost-position sync (TRAP #44) only ever asks one question: "order_store thinks this is OPEN — is the broker actually flat?" It iterates `open_positions` sourced FROM order_store — it never asks the mirror question: "does the broker have a position that order_store has no row for at all?" Meanwhile `smart_order.execute()`'s live path has a real ~8-second window between `broker.place_order()` succeeding and `order_store.record()` running (5×1.5s fill-confirmation poll, by design — TRAP #35, to only record after a confirmed fill). No process in this codebase installs a `SIGTERM` handler (`grep -rn "signal.signal" *.py` — only one place *sends* SIGTERM, `/api/stop`; nothing anywhere *catches* it) — so `systemctl restart` kills a process instantly, mid-poll, with zero cleanup. If that timing lines up: the broker order was placed (and may fill), but `order_store.record()` never ran. Result: a live position that is invisible to `pos_monitor_loop`'s SL/TP/EOD-squareoff (reads only from order_store), invisible to RMS capital/concentration counting (same), and invisible to the UI (same) — worse than a normal ghost, because there's no DB row to even reconcile against. The narrowest, worst-case version: this orphaned position is the *only* position that exists, so the OLD ghost-sync (`sync_if_due`, gated on `open_positions` being non-empty) would never even query the broker.

**Fix:** New `broker_sync.untracked_scan_if_due()` (same 30s cadence as ghost-sync, wired into `pos_monitor_loop` right next to it) — unconditionally polls both brokers' live positions directly (`positions_detailed()`, new optional `BaseBroker` method, default `[]` so unimplemented brokers degrade gracefully) and diffs against order_store's known-open set for that broker. Any broker position with no match:
- **Dhan** → auto-adopt into order_store (`status="open"`, tags `UNTRACKED_ADOPTED` [+ `APPROX_ENTRY_PRICE` if Dhan's own cost-price field was unavailable and `shared_ltp_cache` LTP was used instead]) — safe to do because Dhan's own position response gives us its own `tradingSymbol`/`exchangeSegment` directly, no guessing. This gets the orphan SL/TP/EOD protection immediately, one cycle after landing.
- **Kite** → alert-only, never auto-adopt. Kite's `tradingsymbol` can't be reliably reverse-mapped to a Dhan `trad_sym` (same reasoning as TRAP #13/#22 — `resolve_kite_symbol()` is forward-only, Dhan-trad_sym → Kite-symbol; going the other direction would be guessing, and guessed data in the trade DB is worse than an alert).
- Either way, writes a red-banner alert to `downloader_alert.json` (`UNTRACKED LIVE POSITION` — same alert-file convention as TRAP #53's naked-leg alert) so it's visible even for the alert-only Kite case.

**Fast detect:** `curl localhost:5099/api/downloader-alerts` after any restart that happened close to a live order, OR after any `[LIVE-PENDING] ... fill not confirmed` log line — `untracked_position` key entries mean this fired. Or just: if a position shows up in the broker app that the dashboard doesn't know about, this is why.

**PENDING (deferred, not this session) — the real structural fix:** `smart_order.execute()`'s live path should write a `status="pending"` row to order_store IMMEDIATELY after the broker accepts the order (before the 5×1.5s fill-confirm poll), then UPDATE that same row once `TRADED`/`REJECTED` is confirmed. This closes the gap at its source (a `pending` row is trivially reconcilable) instead of relying on the untracked-scan to catch it after the fact. User chose to handle the live RELIANCE incident manually first and revisit this fix later — flagged here so it isn't lost.

---

## TRAP #59 — `resolve_kite_symbol()` called with the wrong signature everywhere outside its own file — Kite ghost-detection has never actually resolved a symbol; found while deploying TRAP #58's fix 🔴🔴🔴

**Symptom:** TRAP #58's untracked-scan deployed clean (no crash) but found *zero* orphans despite 2 confirmed-live untracked Kite positions existing at the time. Separately: TRAP #44's Kite ghost-detection has, as far as can be told, never once correctly matched a Kite position to its DB row in this system's history — it just happened to fail safe (uncertain → assume open) instead of loud.

**Root cause:** `resolve_kite_symbol(kite, dhan_trad_sym, sec_id=None)`'s real signature takes the Kite SDK client as its *first* argument. Every call site outside `kite_broker.py` itself (`broker_sync.py`'s `_resolve_exit_price`, `_check_flat`, and this session's new `_known_broker_keys`) called it as `resolve_kite_symbol(trad_sym)` — one positional arg, missing `kite` entirely — a guaranteed `TypeError` on every single call, silently swallowed by a broad `except Exception: pass` at each site, falling through to "can't map → assume open" (safe-by-accident for ghost-detection, but meant it could never positively confirm a match either — including for TRAP #58's untracked-scan, where a never-matching `known` set makes literally every Kite position look untracked... except a SECOND bug (below) made `positions_detailed()` return `[]` first, so this one never even got exercised until that was fixed too).

**Compounding bug, found in the same pass:** Kite's actual position-quantity field is `quantity` — `positions()` (pre-existing) and the new `positions_detailed()` (this session) both read `net_quantity`, a field that has never existed in the real API response (confirmed via a raw `kite.positions()` dump). Always defaulted to `0`. Fixing *only* the symbol-resolution bug without this one would have been actively dangerous — Kite ghost-detection would have started successfully matching positions, then reading a permanently-wrong qty=0, concluding every real open Kite position is flat, and incorrectly `mark_externally_closed`-ing them. Both had to be fixed together.

**Fix:** New `KiteBroker.resolve_symbol(dhan_trad_sym, sec_id=None)` public wrapper (grabs `self._get_kite()` internally, calls the free function correctly) — all 3 external call sites now use `get_broker("kite").resolve_symbol(...)` instead of the free function directly. `net_quantity` → `quantity` fixed in both `positions()` and `positions_detailed()`.

**A third bug, found live during verification:** `_write_untracked_alert()` (TRAP #58's alert writer) crashed every cycle with `'str' object has no attribute 'get'` — `downloader_alert.json` is shared with `auto_data_downloader.py`, which writes plain strings, not dicts; the dedup-filter blindly called `.get()` on every existing entry. Fixed by checking `isinstance(a, dict)` before touching `.get()` — same defensive gap likely exists in TRAP #53's `_write_naked_alert()` (same pattern, same file, not yet audited).

**Fast detect:** `grep -n "resolve_kite_symbol(" *.py` — any call site outside `kite_broker.py` with fewer than 2 positional args (or not going through `KiteBroker.resolve_symbol()`) is broken the same way. For the alert file: `python3 -c "import json; [print(type(x)) for x in json.load(open('data/downloader_alert.json'))]"` — mixed types confirm the crash risk.

---

## TRAP #60 — Ghost-sync re-detected the same already-closed leg every ~30s cycle, writing a duplicate phantom exit each time — a feedback loop that corrupted a live day's P&L history through ~20 spurious rows before being caught 🔴🔴🔴

**Symptom:** Live, same day as TRAP #58/#59. User reported the app's P&L totally out of sync with Zerodha ("Sync from Broker" claimed "no ghost positions found — all match" while the app was missing/misrecording real trades). Investigating MARUTI specifically: `order_store` accumulated a cascade of rows all at the exact same stale price (₹388.00), created roughly every 30 seconds, well past the point where the real position had genuinely closed at the broker — a live feedback loop, actively running while this was being diagnosed.

**Root cause — two compounding gaps:**
1. **`_net_rows()`'s Pass-1 pairing is a simple side-alternation per (source,strategy,trad_sym) key, id-order.** For a symbol with an ODD number of same-key legs recorded on a given day, the LAST leg is always left "dangling" in the netting engine's eyes — flagged as still-open — even when the day's real trading is fully flat at the broker. This isn't a bug in isolation (it's a reasonable FIFO convention), but it means "order_store's derived open-list" is not a reliable ground-truth signal on its own for a symbol with 3+ same-day round-trips.
2. **`broker_sync._fetch_fills()` collapses ALL of a symbol's fills for the day into ONE dict entry (last-write-wins).** `_resolve_exit_price()` then hands out whatever that one remembered price is — with zero way to tell "have I already used this specific fill to close something" from "this is a brand-new fill I haven't recorded yet." Combined with (1): every ~30s cycle, `_run_sync` sees SOME row dangling (courtesy of gap 1), asks `_resolve_exit_price` for a price, gets back the SAME stale last-known fill (courtesy of gap 2), and writes ANOTHER synthetic exit record using it. That new record itself becomes a new row in the same-key sequence — which can flip which row is "dangling" on the NEXT pass, so the loop perpetuates instead of self-correcting. This is precisely what TRAP #58/#59's fixes exposed (accurate `resolve_kite_symbol` + correct `quantity` field meant `_check_flat` could, for the first time, correctly and repeatedly confirm "yes, broker is flat" — triggering this pre-existing weakness on every cycle instead of failing silently as before).

**Not a live-trading-risk bug** — MARUTI was genuinely flat at the broker throughout; this only corrupted historical P&L bookkeeping. But it actively degrades trust in every other number the dashboard shows, and the same mechanism could just as easily mis-attribute a REAL still-open position's risk if the timing lined up differently.

**Fix:** `broker_sync._fetch_fills()` now carries each fill's own unique broker id (`trade_id` for Kite — present natively in Kite's raw `trades()` passthrough; `exchangeTradeId`/`orderId` added to `DhanBroker.trades()`, which never exposed one before) alongside price. `_resolve_exit_price()` returns `(price, tid)`. Before writing ANY exit record, `_run_sync` now calls `_fill_already_used(tid, trad_sym, broker_name)` — a lookup against today's `order_store` rows' `correlation_id` field (already existed in the schema, just never populated for this purpose) — and skips silently if that exact fill was already consumed on an earlier cycle. Skip means skip entirely — no duplicate write, and critically NO `mark_externally_closed()` either, since that would have silently dropped a legitimate row's leg from all P&L (`_dead_filtered()` excludes `externally_closed` status outright) — worse than doing nothing.

**Manual data correction (this incident only):** ~20 phantom/duplicate MARUTI rows deleted and replaced with 4 clean, verified round-trip pairs derived directly from Kite's own `trades()` fills (cross-checked against RELIANCE/SUNPHARMA-1880/SUNPHARMA-1980 totals, which matched Zerodha's own P&L exactly, confirming the net-cashflow reconciliation method). Final day total: ₹1,827.50, matching Zerodha. Backed up `trades.db` before any DELETE/INSERT.

**Fast detect:** Any symbol with the SAME exit price repeating across multiple `EXTERNALLY_CLOSED`/`MANUAL_EXIT_BROKER`-tagged rows on the same day is this loop, not a coincidence. `python3 -c "import order_store; rows=order_store.query(date='YYYY-MM-DD'); from collections import Counter; print(Counter((r['trad_sym'],r['price']) for r in rows if 'EXTERNALLY_CLOSED' in (r['tags'] or '')))"` — any count > 1 for the same (symbol, price) pair means the loop fired more than once for what should be a single close event.

---

## TRAP #61 — `broker_sync`'s ghost-close unconditionally hid the ENTRY leg from all P&L, even when it successfully recorded a proper pairing exit — found live same day, right after TRAP #60 started working correctly 🔴🔴🔴

**Symptom:** User manually closed a position on Zerodha (a real trade — see TRAP #62 for why it existed at all). Within ~30s, `broker_sync` correctly detected the flat position, correctly recorded the real exit fill (price, trade_id — TRAP #60's fix working exactly as intended). But the dashboard then showed a brand-new "open position" for the SAME symbol — as if a fresh, unrelated trade had just started, with no entry price shown as ₹0/blank in places and confusing P&L.

**Root cause:** `_run_sync()`'s exit-recording block called `order_store.mark_externally_closed(row_id)` on the ORIGINAL entry row **unconditionally** — regardless of whether the `if exit_px and exit_px > 0` branch above it had just successfully recorded a proper exit leg. `mark_externally_closed()` sets `status='externally_closed'`, which is in `_dead_filtered()`'s exclusion set — meaning the entry row (side, price, qty) is stripped from BOTH the "open" and "closed" views entirely, everywhere, forever. Meanwhile the exit leg just recorded (status=`"filled"`) has no partner left to pair against (Pass-1/Pass-2 netting can't net a leg that's the only survivor of its pair) — so it displays as an unmatched, freshly-"opened" position instead of the completed round-trip it actually represents. This bug has existed since TRAP #44's original design (2026-06-29) — it just never surfaced clearly before today, because every prior ghost-close this session hit either TRAP #59's `resolve_kite_symbol` failure (never got this far) or TRAP #60's stale-price reuse (already wrong for other reasons) first.

**Fix:** Only call `mark_externally_closed()` in the genuine no-exit-price-available case (the `else` branch — nothing to pair against anyway, hiding it is the least-bad option there). When an exit leg WAS successfully recorded, leave the entry row's status untouched (`"filled"`) — normal Pass-2 FIFO netting (same `mode`+`trad_sym`, alternating sides) then pairs entry and exit correctly on its own, no special-casing needed.

**Manual data correction (this incident):** one row's status reverted from `externally_closed` back to `filled`, immediately re-paired correctly with its already-recorded exit leg — no data was missing, it just needed the status un-hidden.

**Fast detect:** Any row with `status='externally_closed'` where a corresponding "fresh open position" appeared around the same timestamp for the same symbol is this bug. `python3 -c "import order_store; [print(r['id'],r['ts'],r['side'],r['price'],r['status']) for r in order_store.query(date='YYYY-MM-DD') if r['status']=='externally_closed']"` — cross-check each against whether a same-symbol exit was recorded within seconds of it.

---

## TRAP #62 — Account-level trailing-profit-lock squareoff or manual exit closes a position at the broker but never tells the owning strategy process — the strategy can later try to "exit" a position that's already gone, placing a real, unintended order 🔴 (Fixed)

**Symptom:** ~40 minutes after `pos_monitor_loop`'s account-level trailing-profit-lock squareoff closed a SUNPHARMA SELL position (a mechanism separate from the strategy's own exit logic — see Critical Rule in `CLAUDE.md` re: trailing_profit_lock_rs/pct), or after a manual exit directly on Zerodha, the strategy process (`range_trader.py` / `rsi_trader.py` / `universe_trader.py`) logged `EXIT SUNPHARMA via ATR_TRAILING` and placed a real BUY order — treating it as "closing my short position." But there was no short position left to close (it had already been bought back by the trailing-lock/manual exit) — the strategy's own in-memory state was never told about that closure, so it kept believing a position was open until its own exit condition eventually fired, creating a real, unintended opposite position (3 trades instead of 1, plus tax/loss).

**Root cause:** Two independent systems can both close the same position — (1) the strategy's own per-symbol exit logic, and (2) the account-level trailing-profit-lock in `pos_monitor_loop` or the user manually closing it. Re-validation only ran at startup (`_recover_state_from_order_store()`), but the strategy in-memory state remained stale while the process was running.

**Fix:** Added live database-revalidation against `order_store.trades_for(today)["open"]` at the beginning of each strategy's scan iteration cycle. If the database shows the position is no longer open, the strategy automatically clears its in-memory position state (`st["position"] = None` or `positions[sym] = 0`), preventing duplicate exit orders. This is safe-guarded against SQLite connection failures (fails silent, does not clear state on temporary DB lock).

**Fast detect:** `EXIT <symbol> via <reason>` in a strategy's log with NO matching earlier `SIGNAL` line for that specific open episode (i.e., the position it's "exiting" was actually opened AND closed by something else already) — cross-check against `[TRAILING-LOCK]` lines in `algo-monitor`'s log around the same account, earlier in the day.

---

## TRAP #63 — TRAP #58's root cause fixed at the source: write the order_store row the instant the broker accepts, not after fill confirmation 🔴🔴🔴

**Symptom:** TRAP #58's untracked-position scan (detection only, deployed earlier the same day) caught the SAME root gap recur **4 separate times in one session** — RELIANCE, the SUNPHARMA hedge leg, and HINDUNILVR all hit `[LIVE-PENDING] ... fill not confirmed in 8s`, each one a real broker fill that never got an `order_store` row because `smart_order.execute()`'s live path only calls `order_store.record()` AFTER confirming `TRADED`, and gives up polling at 8s (5×1.5s). User asked directly why this kept happening: every affected symbol (RELIANCE, SUNPHARMA, MARUTI, HINDUNILVR) is a stock option — wider spreads than NIFTY/BANKNIFTY index options — and Kite's fill-confirmation routinely took longer than 8s to reflect `TRADED` even though the broker fill was genuine. Not a restart-timing coincidence (TRAP #58's original framing) — a near-certain outcome for this strategy's instrument mix, every single trading day.

**Fix:** `execute()`'s live path now writes a **provisional** `order_store` row immediately after the broker accepts the order (right after the immediate-reject check, before the fill-confirm poll even starts) — using the marketable price attempted, `status="filled"`, tagged `UNCONFIRMED_FILL`. Then, whichever way the poll resolves:
- **Confirmed TRADED** → `order_store.update_fill()` (new function) corrects the row's price to the real fill price and drops the `UNCONFIRMED_FILL` tag. No behavior change from the caller's perspective — `res["ok"]=True` as before.
- **Confirmed REJECTED** → the same row's status is updated to `"rejected"` — correctly excluded from all P&L via `_dead_filtered()`, same as if it had never been written.
- **Timeout (can't confirm either way)** → the row is left exactly as written. It's already a normal `"filled"`-status leg, so `pos_monitor_loop` starts protecting it with SL/EOD immediately, and `broker_sync`'s regular ghost-sync can reconcile it correctly later regardless of which way it actually resolved (genuinely filled → nothing more to do; genuinely never filled → `broker_sync` finds it flat with no matching fill price and cleanly excludes it via TRAP #61's no-exit-price branch — no dangling leg either way).

The bottom "persist to trade DB" block that previously ran unconditionally for every call is now skipped specifically for live-mode calls that got a provisional row (avoids a duplicate) — paper mode, and live mode if the provisional write itself failed, still fall through to it unchanged as a fallback.

**Why this wasn't done from the start:** the original design deliberately waited for confirmed `TRADED` before recording, to avoid ever logging a price that might not be real (see TRAP #35 — "Live P&L records only after confirmed fill"). This fix doesn't relax that goal — the recorded price still gets corrected to the real fill price once confirmed; it just stops treating "can't confirm within 8s" as equivalent to "doesn't exist."

**Deployed while a real position was open** (MARUTI, protected by SL tags) — required restarting the `ARS_CHAIN_V1` strategy process specifically (not just `algo-dashboard`/`algo-monitor` — Python doesn't hot-reload an already-imported `smart_order.py`), verified `_recover_state_from_order_store()` (TRAP #28) correctly re-attached the open position afterward, zero gap in protection.

**Fast detect:** `grep "LIVE-PENDING" logs/<strategy>.log` — before this fix, every such line meant a real fill was potentially untracked; after, check the corresponding `order_store` row exists with `UNCONFIRMED_FILL` still in its tags (means still genuinely unconfirmed — worth a manual broker check) vs. tag cleared (means it later confirmed fine on its own).

**Monitoring data added (same fix, user-requested):** TRAP #63's fix means a delayed confirmation no longer causes an invisible position — but it also means the `UNCONFIRMED_FILL` tag gets cleared once resolved, erasing the evidence that a delay happened at all. `data/fill_confirm_delays.json` (new, append-only) now permanently records every live order whose fill-confirm poll took more than 1 of the 5 attempts — symbol, side, qty, attempted price, order id, attempts taken, and how it resolved (`confirmed`/`rejected`/`timeout`). Read via `GET /api/fill-delays` (optionally `?symbol=RELIANCE`) or directly: `python3 -c "import json; [print(r) for r in json.load(open('data/fill_confirm_delays.json'))]"`. Intended for later analysis (which instruments run closest to the 8s cliff, how often, any time-of-day pattern) — nothing in the running system reads this back.

---

## TRAP #64 — A marketable limit that doesn't fill sat OPEN at the broker indefinitely (minutes), unmanaged — added order-chasing (cancel + re-place at the current price) instead of just giving up after one 8s poll

**Symptom:** Live, 2026-07-01. TITAN's SELL order sat unfilled at the broker (`OPEN` status) from 11:54:15 until it finally filled on its own at 11:58:42 — over 4 minutes later, and only confirmed to `execute()` because `[LIVE-PENDING]` had already returned at the 8s mark and someone happened to look. ICICIBANK's BUY order similarly sat `OPEN` and never filled at all — the user had to notice it in the Zerodha app and cancel it by hand (confirmed via `kite.order_history()`: `OPEN` for ~29s, then `CANCEL PENDING` → `CANCELLED`, user-initiated, filled_quantity 0 throughout). Both are illiquid-contract symptoms: a "marketable" limit crosses the CURRENT spread at placement time, but on a thin contract the price can walk away before anyone takes the other side, leaving the order genuinely stuck in the book — TRAP #63's provisional-row fix made this *visible/protected*, but did nothing to make the underlying order actually resolve faster, and there was no automatic cancel — a stuck order just sat there until a human noticed.

**User's proposal (implemented as-is):** if the order hasn't filled after a full poll round, don't just give up — cancel it and re-place at the current price, chasing the market, for a bounded number of attempts.

**Fix:** New `BaseBroker.cancel_order(order_id)` (implemented for `DhanBroker` — `DELETE /v2/orders/{id}` — and `KiteBroker` — `kite.cancel_order(variety="regular", order_id=...)` — default `False`/no-op for brokers that don't support it, so chasing is skipped gracefully rather than erroring). `smart_order.execute()`'s live path now loops: place → poll 5×1.5s → if still unresolved, cancel the stale order, re-check `get_fill()` once more (races a fill that landed right as the cancel went out — never treat an already-filled order as needing a re-place), fetch a fresh `marketable_price()`, place a new order at that price, and poll again. Up to `MAX_CHASE=2` re-places (3 order attempts total, ~24s worst case instead of 8s). The provisional row (TRAP #63) gets its price and `broker_order_id` updated on every chase round via a new `order_store.update_fill(broker_order_id=...)` param, so it always points at whichever order is currently actually live — a later reconciliation check queries the right one.

**Bonus side-effect:** this also shrinks (doesn't eliminate) TRAP #63's "provisional price is a guess that never gets corrected if the fill happens outside the poll window" gap — since the price gets refreshed every chase round, the LAST attempted price sitting in the provisional row is far closer to whatever the eventual real fill turns out to be, compared to a single stale 8-second-old guess.

**Resolved by Antigravity AI:**
- The Kite phantom order cleanup path is now resolved. If `_check_flat` returns `False` for a Kite position, `broker_sync.py` checks the order history of its `broker_order_id` directly using `broker.order_status(b_order_id)`. If the order was `CANCELLED` or `REJECTED` (never filled), it reconciles the position as flat and marks the row `externally_closed`.
- Same-day account-level trailing-profit-lock design question raised by the user (aggregate 30% squareoff vs. per-instrument trailing lock) — not implemented either way this session, pending the user's decision.

**Fast detect:** `grep "\[CHASE\]" logs/<strategy>.log` — chase attempts logged with round number. An order still unresolved after `chase 2/2` falls through to the existing `[LIVE-PENDING]` handling exactly as before.

---

## TRAP #65 — `range_trader.py` only ever called `dhan_feed.add()`, never `dhan_feed.start()` — the live WebSocket feed never actually connected, silently defeating the liquidity filter all day, every day

**Symptom:** User asked "we already have a 2-of-3 liquidity gate for exactly this — how did TITAN/ICICIBANK (both illiquid, both got stuck unfilled) get through it?" Checked every `[LIQUIDITY]` log line for the day (13 of them, spanning 9:37 AM to 1:40 PM, many different symbols) — every single one said `no live market-depth data ... failing OPEN (data unavailable, not confirmed illiquid)`. Not one successful real-data check all day.

**Root cause:** `dhan_feed.py`'s `add(sec_tuple)` only queues an instrument into the module's `_instruments` list, and only triggers a reconnect *if the feed's background thread is already running* (`if _running and len(_seen) != before: _pending_resub = True`). The actual WebSocket connection thread is only ever created inside `start()` (`_thread = threading.Thread(target=_run_loop, ...); _thread.start()`). `range_trader.py` calls `dhan_feed.add(("NSE_FNO", sec_id))` in one place (to opportunistically warm the cache before a premium fetch) but **never calls `dhan_feed.start()` anywhere** — so `_running` stayed `False` for the entire life of the process, `add()` just appended to a list nothing was ever consuming, and `dhan_feed.LIVE` stayed permanently empty (confirmed directly: `len(dhan_feed.LIVE) == 0`). Every `strategy_safety.check_contract_liquidity()` call's primary data source (`dhan_feed.get_quote()`) returned `{}` every time, by design falling through to the REST fallback (`_rest_quote_fallback()`) — which itself works fine in isolation (verified directly — real bid/ask/volume/OI came back instantly) but is presumably unreliable under the real concurrent load of live signal processing (shared Dhan rate limit across every running process), and/or subject to cold-start latency (a contract's first-ever check has no prior subscription to lean on). Net effect: the liquidity filter has been rubber-stamping every single trade since it was built (2026-06-28) — not a bug in the filter's logic itself (the any-2-of-3 rule and fail-open-on-no-data design are both sound), just a wiring gap that meant it never got real data to evaluate.

**Fix:** `range_trader.py`'s `main()` now calls `dhan_feed.start()` with the initial symbols watchlist proactively. Inside scanner loops of both `range_trader.py` and `universe_trader.py`, likely ATM option contracts are proactively subscribed to `dhan_feed` at the current spot price during each scanner cycle. This ensures ticks are warm before any entry or exit signal triggers.

**Resolved by Antigravity AI:**
- Proactive underlying subscriptions are resolved and subscribed on `main()` boot.
- Proactive option subscriptions warm up the likely ATM strikes (both CE and PE) based on live spot price every cycle, closing the cold-start gap completely.

**Fast detect:** `grep "no live market-depth data" logs/<strategy>.log | wc -l` vs total `[LIQUIDITY]` line count — if it's anywhere close to 100%, the feed isn't connected in that process. Confirm directly (careful — reads process memory, not a config file, so this only works if you can get code to run *inside* that same process, e.g. a debug hook, not a fresh `python -c` from outside).

---

## TRAP #66 — Broker Balances card labeled Kite's "net available margin" as "Cash" — off by ~₹10.65L 🔴🔴

**Symptom:** User: "Zerodha se mera balance app ka match nahi kar raha" — dashboard's 💰 Broker Balances card showed "Cash: ₹10,72,173", real Zerodha cash was ₹8,819.20.

**Root cause:** `KiteBroker.funds()` (`brokers/kite_broker.py`) correctly returns BOTH `available` (Kite's `eq["net"]` — cash + pledged collateral − used margin, i.e. total usable margin) AND `cash` (the real `eq["available"]["cash"]`) — the raw data was always correct. But `templates/index.html`'s "💰 Broker Balances" card (`renderBrokerBalances()`) rendered the "Cash" line from `b.available` instead of `b.cash` — `available` for Kite is NOT cash, it's total margin including pledged stock collateral (this user has ~₹10.65L in stock collateral). A DIFFERENT card on the same dashboard (the per-strategy RMS summary, driven by `/api/rms-summary`'s `broker_cash` field) already used the correct field — this was an isolated bug in one hand-rolled card that fetched straight from `/api/broker-balances` instead of reusing the already-correct rms-summary fields.

**Fix:** card now shows Cash (`b.cash`, falls back to `b.available` for Dhan where `funds()` returns no separate cash field), Collateral, a new "Available Margin" line (`b.available`, correctly relabeled), and Total Margin.

**Permanent guard:** when a broker returns MULTIPLE distinct balance concepts (cash vs total margin vs collateral), never assume a single "available"-ish field is safe to label "Cash" — check what the broker's own docs/API actually mean by each field, especially when the same field name means different things across two brokers (Dhan's `available` ≈ real cash; Kite's `available` = total net margin).

**Fast detect:** compare the dashboard's "Cash" figure against the broker's own app/site directly — a mismatch in the thousands (not paisa-level rounding) means a wrong field is being read, not a sync delay.

---

## TRAP #67 — Manual-trade reconciliation double-counted several real trades, because matching relied only on `broker_order_id` 🔴🔴🔴

**Symptom:** User pointed out the Completed Trades TOTAL still didn't match Zerodha's real day P&L, and explained Zerodha is the actual source of truth for real fills (this app just places orders and estimates prices). Built `broker_sync.reconcile_manual_trades()` — pulls today's real Kite fills via `broker.trades()`, matches each against an existing `order_store` row by `broker_order_id` (the order id we placed), and inserts anything unmatched as a `source="manual"` row. First live run inserted 32 "manual" rows and corrected 1 price — but several of the 32 (SUNPHARMA, RELIANCE, MARUTI, HINDUNILVR legs) were **duplicates of trades already correctly recorded** earlier in the same session (during a manual TRAP #60/61 cleanup) — those earlier manual inserts had never had `broker_order_id` populated (that field simply wasn't part of that ad-hoc cleanup), so the new order-id-only matching saw them as "unmatched" and inserted a second copy of the same real trade.

**Root cause:** `broker_order_id` is a good match key ONLY if every historical row that could correspond to a real fill reliably has it populated. It doesn't — several legitimate rows across this project's history (manual dashboard fixes, earlier ad-hoc reconciliations, `broker_sync`'s own ghost-close exit records) were written without it.

**Fix:** replaced order-id matching with a **signature + count** match: canonicalize every fill (both broker fills and existing `order_store` rows) into `(root_symbol, strike, CE/PE-or-EQ, side, qty, round(price,2))` — tolerant of Dhan's dashed trad_sym format and Kite's compact format alike (`_reconcile_sig()`). For each signature, insert only `max(0, broker_count - db_count)` new rows. This can't double-count regardless of whether an old row has a `broker_order_id` or not, and is naturally idempotent (re-running always converges to 0 inserts once counts match) — verified via a **read-only dry-run script** against the real DB (found the exact 12 genuinely-missing fills, zero of the 10 already-recorded symbols flagged) before ever touching the live DB with the fixed version.

**Also verified before trusting the "missing" trades were genuinely manual (not misfired shadow-live orders):** checked `risk_gate.shadow_live_enabled()` was `False` (global + per-strategy) — since some of the missing signatures (HINDUNILVR, NESTLEIND) are symbols the paper-mode strategy also watches, a shadow-live real order firing in parallel to a paper signal could look identical to a genuine manual trade. Confirming shadow-live was off first is what makes tagging them `MANUAL_TRADE` (rather than a mislabeled algo order) defensible.

**Permanent guard:** the bad 32-row run was undone via a **surgical, targeted DELETE** (`WHERE correlation_id LIKE 'MANUAL_TID_%'`, all rows this run itself had just inserted, individually identifiable) plus reverting the single price correction — NOT a full-database restore-from-backup, which (see TRAP #68) would have silently discarded 18+ rows of real trading data that arrived after the backup was taken. When undoing a bad automated write, prefer deleting exactly what you know you wrote over restoring a whole table from a snapshot, unless you've confirmed the snapshot is complete and current.

**Fast detect:** before trusting ANY broker-reconciliation match key, dry-run it read-only first and manually eyeball every signature it would insert/skip — a match key that "should" be unique (an order id) can still have silent gaps in older data it was never applied to.

---

## TRAP #68 — `cp trades.db backup.db` (plain file copy) silently missed rows sitting in SQLite's WAL file — a "backup" that was already stale the moment it was taken 🔴🔴

**Symptom:** After surgically undoing TRAP #67's bad reconcile run, comparing the live `trades.db` against a `cp`-made backup taken minutes earlier showed the live DB had 19 MORE rows than the "before" backup — even though the backup was supposed to predate all of that day's real trading activity (ICICIBANK, TITAN, BANKNIFTY, NESTLEIND rows from as early as 12:00 PM were simply absent from a backup file timestamped 15:38).

**Root cause:** `order_store.py` opens its sqlite connection in WAL mode (`_conn()`). In WAL mode, recently-committed writes can live in a separate `trades.db-wal` file and haven't necessarily been checkpointed back into the main `trades.db` file yet. A plain `cp trades.db backup.db` only copies the main file — it can silently produce a backup that's already missing recent commits, with no error or warning of any kind. This was caught only because a row-count diff looked suspiciously large — it easily could have gone unnoticed, and if the earlier (correctly-blocked) full-DB restore had been allowed to proceed, it would have silently wiped 18+ rows of real trading history.

**Permanent guard:** never `cp` a live, actively-written SQLite database file for backup purposes. Use `sqlite3`'s own online backup API instead (`src_conn.backup(dst_conn)`, or the `.backup` CLI command / `VACUUM INTO`) — these consult the WAL correctly and produce a genuinely consistent snapshot. Verified the fix: a `.backup()`-made copy's row count matched the live DB exactly, where the `cp`-made one was short by 19 rows.

**Fast detect:** after taking any backup of `trades.db` (or any WAL-mode sqlite file) while its owning process is running, immediately compare `SELECT COUNT(*) FROM orders` between the backup and the live file — they should match exactly; if the backup is short, it's WAL-incomplete, not a real point-in-time snapshot.

---

## TRAP #69 — A multi-file `scp` in one shell call silently failed for exactly one of the files, with no visible error 🔴🔴

**Symptom:** Deployed a dashboard button fix (`templates/index.html`) alongside `broker_sync.py` in one `scp file1 file2 file3 "user@host:dest/"` call. `broker_sync.py`'s change verifiably landed (`py_compile` succeeded, behavior changed) — but `templates/index.html` silently kept serving the OLD version for ~40 minutes, with no scp error, no exception, nothing. Only caught because the user reported a button that should exist wasn't visible, and a file-mtime check on the VPS showed `index.html`'s last-modified time was from an earlier, unrelated deploy.

**Root cause:** unclear exactly why that specific multi-file `scp` invocation dropped one file (no error surfaced to investigate after the fact) — but the broader lesson is that "the scp command didn't print an error" was silently trusted as "every file landed," which isn't a safe assumption for a file (an `.html` template) with no compile step to catch a stale copy the way `py_compile` does for `.py` files.

**Permanent guard:** for any file with no local syntax/compile check available (HTML/JS templates, configs, etc.), verify deployment by grepping the SERVED output (or the file's own mtime/content on disk) for a distinctive string unique to the just-made change — not just "the scp command exited 0." Doubly important when deploying several files in one command; deploy templates in their own explicit, single-file `scp` call and verify each one independently rather than trusting a bundled multi-file copy.

**Fast detect:** `ssh ... "grep -c '<distinctive-new-string>' <deployed-file-path>"` right after any deploy that includes a non-Python file — `0` means it didn't land, re-deploy that file alone.

---

## TRAP #70 — Global Option Hedging Switch (Naked vs. Hedge Mode) 🔴 (Fixed)

**Symptom:** Auto-hedging (which resolve and place further OTM buy options for naked SELL strategies) was running uncontrolled or placing too many orders when the user preferred to trade naked positions to save execution costs/margin.

**Root cause:** Hedging was always active if `min_strikes` or `max_premium` was set in the strategy config/nifty_config.json. There was no simple way to turn it off globally for all strategies on a single switch.

**Fix (by Antigravity AI):** Added a global switch (`hedge_enabled`) under the "Auto-Hedge" card on the RMS/Control tab. If turned off (Naked Mode), `risk_gate.hedge_config()` returns `0, None`, which makes `strategy_safety.compute_hedge_target()` skip hedge resolution entirely, ensuring only naked short positions are placed.

---

## TRAP #71 — Aggregate vs. Per-Instrument Trailing Profit Lock Toggle 🔴 (Fixed)

**Symptom:** The account-level trailing profit lock squared off the entire portfolio of open positions when aggregate P&L dropped from its peak. This forced closure on healthy positions due to drawdown on a single bad position.

**Root cause:** The trailing lock logic in `pos_monitor_loop()` of `trader_dashboard.py` only evaluated portfolio-level total P&L (`_total_pnl`).

**Fix (by Antigravity AI):** Added a `Trailing Mode` selector (Aggregate vs. Per-Instrument) under the Trailing Lock card on the RMS tab. In **Per-Instrument** mode, the loop tracks individual peak unrealized P&L for each open position ID in `_pos_peaks`. If a position's P&L drops from its specific peak by the ₹ lock amount (or % of its peak), only that specific position is squared off, keeping the rest of the portfolio open.

---

## TRAP #72 — TRAP #71's edit silently disabled ALL SL/TP/EOD enforcement whenever an aggregate trailing lock was configured (a 4-space indentation regression) 🔴🔴🔴 (Fixed)

**Symptom:** None visible — this is the dangerous kind. Found by code-review of TRAP #70/#71's edits, not by any log or incident. Whenever a trailing profit lock (₹ or %) was configured AND `trailing_lock_mode` was `"aggregate"` (the DEFAULT), `pos_monitor_loop()` skipped the entire `for p in open_pos: _pos_monitor_check_one(...)` pass **every single 5s cycle** — meaning per-position stop-loss, take-profit, 3:15 EOD squareoff, expiry-day guards, and the RMS force-squareoff path all stopped running. Exactly the "SL/TP loop silently doing nothing" failure family as TRAP #55/#56, but reached via a different route.

**Root cause:** When TRAP #71 wrapped the original portfolio-level trailing-lock block inside a new `if _lock_mode == "per_instrument": ... else: ...`, every line of the original block got indented +4 spaces to sit inside the new `else:`. The fire-if (`if _trailing_peak_pnl > 0 and ...`) and its body were re-indented correctly, but the trailing `time.sleep(5)` + `continue  # skip per-position checks this cycle` (which in the original lived *inside* the fire-if body — only run after an actual squareoff) were left at their old 24-space indent. That dropped them out of the fire-if and into the `else:` block body, so they executed **unconditionally** on every cycle the aggregate branch was taken, not just when the lock fired — and the `continue` skipped the per-position risk loop below it every time.

**Fix:** Re-indented `time.sleep(5)` + `continue` from 24 → 28 spaces (back inside the aggregate fire-if body), restoring the original behavior — the per-position checks are skipped for one cycle ONLY after the aggregate lock actually squares everything off. Per-Instrument mode was never affected (it has no such `continue`; it adds closed ids to `_closed_ids` and falls through to the per-position loop, which skips them). File: `trader_dashboard.py` `pos_monitor_loop()`.

**Permanent guard / fast-detect:** After ANY edit that changes indentation of a block inside `pos_monitor_loop` (or any long risk loop), grep for a stray `continue` that skips the per-position pass and confirm it's gated behind a real fire condition, not the outer `if _either_set`. A trailing lock being *configured* must never, by itself, suppress SL/TP/EOD. When wrapping an existing block in a new conditional, re-indent the WHOLE block uniformly — context/unchanged lines (that a diff shows with no +/-) are the ones most easily left behind.

---

## TRAP #73 — Manual close at the broker → strategy fires its own exit → opens a phantom OPPOSITE position (1 trade → 3 + extra tax). TRAP #62's order_store re-validation only closed HALF the gap 🔴🔴🔴 (Fixed)

**Symptom (user-reported, recurring real-money pain):** User closes an algo position manually on Zerodha. The strategy doesn't know, so when its own exit condition (ATR trailing stop / zone exit) later fires, it places a real order to "close" a position that's already flat — which instead OPENS a brand-new opposite position. User then has to close that too: one intended trade becomes three, plus the extra brokerage/STT/tax and any adverse move in between.

**Root cause — two layers, only one of which Gemini's TRAP #62 addressed:**
- TRAP #62 (Antigravity AI) added an `order_store`-based re-validation at the top of each strategy's symbol loop: if the in-memory `st["position"]` is no longer among `order_store.trades_for(today).get("open")`, clear it. This works — BUT only *after* `broker_sync._run_sync()` has detected the manual close and recorded the exit in `order_store`. `broker_sync` runs on a 30s cadence **and in a different process** (`algo-monitor`), so there's a real window where the manual close has happened but `order_store` doesn't reflect it yet.
- The strategy's own exit path (`range_trader.py` `EXIT_LONG`/`EXIT_SHORT` handler; `rsi_trader.py` live EXIT; `universe_trader.py` `sig=="EXIT"`) had **no direct broker-flat check at all** — its only guard was `if st["position"] is None`. So an exit condition firing inside that 30s window fired a live order against an already-flat position. (`broker_sync.is_flat()`'s cache is per-process and is never populated inside a strategy's own process, so it was useless here — the cache is filled only in `algo-monitor` where `pos_monitor_loop` runs.) `webhook_executor._do_exit()` already had a two-layer flat check for exactly this (TRAP #51); the strategies never got the equivalent.

**Fix:** Added a **fresh** live broker `positions()` check immediately before the exit order in all three traders (`range_trader.py`, `rsi_trader.py`, `universe_trader.py`), reusing `broker_sync._check_flat()` for the proven Dhan-sec_id / Kite-resolve_symbol matching. If the position is definitively flat at the broker, skip the exit order and just clear the stale in-memory state. Guards: **live mode only** (paper has no real broker position and no phantom-money risk, and it avoids a wasted API call per paper exit); **fail-open** on any error or uncertain result (`_check_flat` returns False when the sec_id isn't in the positions response) so a genuine exit is never wrongly blocked. This does NOT depend on `broker_sync`'s 30s cycle or on `order_store` — it asks the broker directly, closing the window entirely.

**Permanent guard:** Any code path that places an EXIT/close order for an option position must confirm the position still exists at the broker first (fresh `positions()` + `broker_sync._check_flat()`), not trust in-memory state or `order_store` alone — both can lag a manual/external close. New strategies: put this check in the exit path from day one (same as the entry path goes through `strategy_safety.gate_entry`).


---

## TRAP #74 — Order-chase could re-place a DUPLICATE order after an external/manual cancel; chase also silently aborted itself whenever its own cancel confirmed quickly 🔴🔴 (Fixed — live verification pending)

**Symptom (reported as "MARUTI duplicate today"; mechanism verified by code-trace, NOT found in the strategy log):** A human cancels a pending order at the broker while `smart_order.execute()`'s live path is still polling/chasing it — and the engine places a brand-new order on the same side/instrument/strike. One intended order becomes two.

**What the code-trace actually found (2026-07-02):** The originally-suspected mechanism — the fill-poll's hardcoded `if fill_st in ("TRADED", "REJECTED")` not recognizing `CANCELLED` — could NOT fire on the deployed code, because both brokers' `get_fill()` already collapsed `CANCELLED` → `"REJECTED"` internally (since commit `998249f`, 2026-06-30; verified identical local + VPS). No `[CHASE]` line or duplicate MARUTI order exists in `ARS_CHAIN_V1.log` for 07-01/07-02 either. But the same block had THREE real, adjacent gaps:

1. **The real duplicate path:** in the chase branch, after `broker.cancel_order(oid)` the re-check `get_fill()` can fail (429/network — swallowed by `except: pass`, leaving `fill_st` stale at `"PENDING"`) or lag (Kite `CANCEL PENDING` → `"PENDING"`). The code then re-placed a fresh order *without ever knowing whether its own cancel — or anyone's — actually happened*. A manual cancel landing in that window = duplicate order.
2. **Chase was self-defeating:** when the chase's OWN cancel confirmed quickly, `get_fill()` returned `CANCELLED`→`"REJECTED"`, the loop broke, and the whole entry/exit was written off as "fill confirmed REJECTED" (provisional row marked rejected) — the chase never re-placed at all. The feature only "worked" when the broker was slow to reflect the cancel — i.e., exactly when re-placing was least safe.
3. **Unmapped terminal/partial statuses:** Dhan `EXPIRED` fell through to `"PENDING"` (→ chase re-places an order the broker already declared dead), and `PART_TRADED` (Dhan literal status; Kite `OPEN` with `filled_quantity>0`) also read as `"PENDING"` — cancel + re-place of a partially-filled order re-places the FULL qty, duplicating the already-filled part.

**Fix (smart_order.py + both brokers + base_broker docstring):**
- Fill-poll break + post-poll check use `_is_terminal(fill_st)` (the sets `_REJECTED_STATUSES`/`_TERMINAL_STATUSES` existed all along, unused here); explicit log line `terminal non-fill status X, chase skipped` when a poll ends on a non-fill terminal.
- **Re-place is now gated on `cancel_ok`** — the chase only re-places when OUR `cancel_order()` was affirmatively accepted by the broker. `cancel_ok=False` + terminal status = someone else acted (manual cancel/reject) → chase aborted, loud log, no re-place. `cancel_ok=False` + unknown status = NOT re-placing either (duplicate-order guard), provisional row + pos_monitor protect whatever the truth turns out to be. `cancel_ok=True` + `CANCELLED` = the normal chase path — now actually re-places (fixes gap 2).
- `get_fill()` on both brokers returns terminal statuses LITERALLY (`REJECTED`/`CANCELLED`/`EXPIRED`) instead of collapsing to `REJECTED`; Kite also matches `CANCELLED AMO` via prefix. `PART_TRADED` returned distinctly by both; smart_order refuses to chase it (no cancel, no re-place — logs and leaves the provisional row + monitor protection).
- Downstream: the rejected-branch matches `_is_rejected(fill_st)` (any literal), logs the literal status; `order_store` status stays `"rejected"` (P&L-exclusion semantics unchanged).

**Permanent guard:** Never compare broker order statuses against a hardcoded 2-tuple — always `_is_terminal()`/`_is_rejected()`. Never re-place an order unless your own cancel was POSITIVELY confirmed (`cancel_ok is True`) — "status unknown" after a cancel attempt means STOP, not retry. Any partial-fill status means the chase is over. And when a bug report names a mechanism, verify it against the deployed code + logs before fixing — here the named mechanism was impossible on the deployed code, and the real gaps were adjacent to it.

**Fast-detect:** `grep -E "CHASE.*(external|NOT re-placing|PART_TRADED)|terminal non-fill" logs/*.log` — any hit means one of these guards fired in production.


---

## TRAP #75 — 5 more exit-order call sites fired without a fresh broker flat-check (trailing-lock squareoff, webhook layer-2, 3 strategies' 3:15 exit-all, FLIP-close, manual UI close) — TRAP #73's fix only covered the signal-driven exit path per file 🔴🔴 (Fixed)

**Symptom:** None live-observed this time — found by a deliberate, requested audit ("search every place an order fires as a reaction to a position's assumed state") after TRAP #73/#74, not by an incident. The exact same failure shape as TRAP #73 (manual close at broker → app's own exit order fires anyway → phantom opposite position) was still reachable through 5 code paths that never got TRAP #73's fix, because that fix was applied per-file to the *signal-driven* EXIT branch only — any OTHER place in the same file (or a different file) that also places a closing order was missed.

**Root cause:** TRAP #73 fixed `range_trader.py`/`rsi_trader.py`/`universe_trader.py`'s main signal-EXIT handlers with a fresh `broker_sync._check_flat()` call — but each file also has a SEPARATE 3:15 PM force-exit-all loop that placed orders unconditionally, with zero flat-check. `universe_trader.py` additionally has a FLIP-close (opposite-direction reversal) with the same gap. `trader_dashboard.py`'s account-level trailing-lock squareoff (both aggregate and per-instrument branches) called `smart_order.execute()` directly, bypassing `_do_squareoff()` entirely — so it never got `_do_squareoff`'s own TRAP #44/#73 guard. `webhook_executor.py`'s `_do_exit()` layer-2 check used the 35s-stale `is_flat()` cache instead of a fresh call. The manual "close position" dashboard button had no flat-check at all.

**Fix:** (1) New shared helper `trader_dashboard._pre_exit_guard(p, sec_id, exit_reason, _closed_ids, log)` — the webhook-claim + fresh-flat-check logic that used to live only inside `_do_squareoff`, now callable from anywhere. `_do_squareoff` itself refactored to call it (removes the duplication that let this class of bug happen in the first place). Both trailing-lock branches now call it before their `smart_order.execute()`. (2) `webhook_executor._do_exit()`'s layer-2 switched from `is_flat()` to `broker_sync.is_flat_fresh()` (new function, TRAP #73-era addition — never trusts cached data older than 5s, one fetch refreshes the shared cache so a burst of checks in one cycle costs one API call). (3) All 3 strategies' 3:15 exit-all loops + `universe_trader`'s FLIP-close got the same fresh-flat pattern their signal-EXIT branch already had. (4) `/api/close-position` (+ `/api/close-position-group`, which shares the same underlying `_close_position_impl`) now checks `is_flat_fresh()` before placing the live order; if already flat, marks `externally_closed` instead of firing.

**Permanent guard:** Any NEW code path that places a closing/exit order — anywhere, any file, any trigger (signal, timer, button, account-level risk event) — must go through a fresh flat-check before the order, not "the file already has this fixed somewhere else." Prefer routing through `_do_squareoff`/`_pre_exit_guard` (dashboard) or `broker_sync.is_flat_fresh()`/`_check_flat()` (strategy files) directly rather than hand-rolling a new copy. When auditing this class of bug, grep every `smart_order.execute(` and `place_order(` call site with `is_exit=True` or an exit-shaped `side` — don't assume "this file already got the TRAP #73 fix" covers every call site in it.

**Fast-detect:** `grep -n "smart_order.execute\|place_order(" <file> | grep -v "is_flat\|_check_flat"` near any exit-shaped call — a hit with no flat-check nearby is a candidate for this trap.

---

## TRAP #76 — Restart-recovery (TRAP #28) was silently undoing itself on the very next loop line, in `range_trader.py` (LIVE, since 2026-06-29), `rsi_trader.py` and `universe_trader.py` (never had recovery ported at all) 🔴🔴🔴 (Fixed)

**Symptom:** VPS log, 2026-07-01 11:24:29 — `[RECOVER] re-attached 1 open position(s) from order_store` immediately followed, same second, by `New trading day — resetting state & reloading daily levels`. No incident resulted (that particular restart happened not to matter), but the pattern proves recovery has never actually worked since it shipped.

**Root cause:** `_recover_state_from_order_store()` (range_trader.py) runs once near the top of `main()`, populating the module-level `_state` dict from today's open order_store rows. A few lines later, `last_day = None` is set, then the main `while True:` loop's very first iteration always evaluates `now.date() != last_day` as True (None never equals a date) — firing `reset_daily_state()`, which iterates `_state.keys()` (now including whatever recovery just added) and blanks every entry back to flat. Recovery → immediate self-inflicted wipe, every single restart, for 33 days. `rsi_trader.py` and `universe_trader.py` never got TRAP #28's recovery function ported to them at all — same class of gap, worse (no recovery attempt existed to even be undone).

**Fix:** All 3 files — recovery function runs, THEN `last_day`/`last_date` is seeded to `ist_now().date()` (not `None`) immediately after, so the loop's first "new day?" check correctly evaluates False and doesn't fire. `rsi_trader.py` got a new `_recover_rsi_state()` (positions/active_opts/trades_today are locals inside `run()`, not module globals — mutated in place via dict pass-by-reference) deriving LONG/SHORT from the CE/PE trad_sym suffix (RSI always enters BUY, buying the premium either way). `universe_trader.py` got `_recover_state_from_order_store(sid, log)` handling BOTH entry-side conventions in one function (equity route: BUY/SELL entry maps directly to LONG/SHORT; option routes: always-BUY entry, LONG/SHORT derived from CE/PE suffix like RSI).

**Permanent guard:** Any `last_X = None` seeded before a `while True:` loop that has its own "is this a new day" reset check is a landmine if ANYTHING populates state before that loop starts — the reset will fire on iteration 1 regardless of whether a real day actually changed. Seed the tracking variable to "now" immediately before the loop, not `None`, whenever setup work (especially restart-recovery) happens earlier in the same function. When porting a fix like TRAP #28 to a new file, verify the ENTIRE sequence end-to-end (recovery → first loop iteration → state still populated), not just that the recovery function itself runs without error — a recovery function that silently gets undone one line later "works" by every test that doesn't check the state a second time.

**Fast-detect:** `grep -n "RECOVER\|New trading day\|reset_daily" <strategy_log>` — a `[RECOVER]` line immediately followed by a reset line (same timestamp or next line) is this bug re-occurring in a strategy that doesn't yet have the `last_day` seed fix.

---

## TRAP #77 — Per-instrument trailing-lock mode wrote the SAME account-wide entry-block flag as aggregate mode, defeating per-instrument's entire purpose 🔴 (Fixed)

**Symptom (user-reported, live):** User switched to per-instrument trailing-lock mode specifically so one bad position's floor firing wouldn't stop everything else — but a single position's floor firing still blocked ALL new entries account-wide for the rest of the day, exactly like aggregate mode.

**Root cause:** When per-instrument mode was added (comment in the code literally says "matches aggregate lock design"), the day-level `trailing_lock_fired_<date>.txt` flag-write was copy-pasted from the aggregate branch without adjusting for per-instrument semantics. `webhook_executor._do_entry()` checks that flag and blocks ALL new entries regardless of symbol/strategy when it exists — correct for aggregate (account-wide risk event), wrong for per-instrument (one position's own floor firing is a closed, resolved event for THAT position only).

**Fix:** Per-instrument mode no longer writes the day-level flag at all when a single position's floor fires — that squareoff is scoped to just that position, nothing else is touched, no other symbol/strategy is blocked. (Bundled in the same pass: `_pos_peaks`, the per-position peak tracker this mode depends on, is now persisted to `data/pos_peaks.json` — was RAM-only, same TRAP #38 failure shape a mid-day restart would have hit.)

**Permanent guard:** When adding a "per-X" variant of an existing "aggregate" feature, never copy-paste the aggregate branch's side effects wholesale — re-derive each one from first principles for what per-X actually means. A flag/lock/counter that's correct at account-scope is very often wrong at instrument-scope, and the bug won't show up until someone actually needs the scoped behavior to be scoped.

---

## TRAP #78 — `nifty_ema_trader.py`'s own candle-fetch was never wired into `dhan_rate_limiter` — a second, independent occurrence of TRAP #2's exact gap, in a file TRAP #2's original sweep missed 🔴 (Fixed)

**Symptom:** Live dashboard log, DH-904 429 storm across LT/MARUTI/HINDUNILVR/ITC/ADANIENT/SUNPHARMA/TITAN/ULTRACEMCO — all failing within the same second, every scan cycle. Paper mode only, so no phantom-order risk, but every one of those symbols silently got zero signal evaluation that cycle.

**Root cause:** `fetch_candles()` called Dhan's `/v2/charts/intraday` directly via `requests.post`, in a plain `for sym in sym_list:` loop with no delay and no rate-limiting — one call per symbol, back-to-back, blowing straight through Dhan's ~1 req/sec account-wide limit. TRAP #2 (2026-06-27) wired every OTHER strategy file's candle/order/LTP calls into `dhan_rate_limiter`, but `nifty_ema_trader.py` (ema_v1) wasn't touched in that sweep — found live 2026-07-02, over a week later, while investigating something unrelated (a user question about whether an earlier LTP-batching fix also covered this).

**Fix:** Added `import dhan_rate_limiter as _rl`, `_rl.acquire("candle")` before the request, `_rl.note_429()` on a 429 response — identical pattern to `range_trader.py`'s equivalent fetch (which already had this).

**Permanent guard:** TRAP #2's rule ("every real Dhan call from every process routes through `acquire(priority)`") applies to every file that talks to Dhan directly, not just the ones audited in the original sweep. When adding ANY new file that calls a Dhan endpoint with `requests.get/post`, wire it through `dhan_rate_limiter` in the same commit — don't assume "this pattern is established elsewhere" means every file already has it. Fast-detect: `grep -rL "dhan_rate_limiter" $(grep -rl "api.dhan.co" *.py _TRADERS/*.py)` — any file that calls Dhan's API but never imports the rate limiter is a candidate.

---

## TRAP #79 — Kite (Zerodha) untracked positions were alert-only by design (TRAP #13/#22 caution) — safely upgraded to auto-adopt via a REVERSE structured-field match, not a string-guess (Feature, not a bug — documented for the technique)

**Context:** User's actual live trading broker is Kite (confirmed via `nifty_config.json`'s `_risk.global.default_broker`, not Dhan as might be assumed from how much of this codebase's data path is Dhan-only). User's real workflow includes placing SL/Target as manual LIMIT orders directly on Zerodha based on price action they see live — wanted the app to pick these up automatically for SL/EOD protection, without reopening the TRAP #13/#22 symbol-guessing risk.

**Why Kite auto-adopt was deliberately NOT done before:** `_handle_untracked()`'s Dhan branch could auto-adopt safely because Dhan's own position response hands back its OWN tradingSymbol/segment directly — zero guessing. Kite's position response only gives Kite's OWN tradingsymbol format (e.g. `NIFTY2463023900PE`), which this system needs translated to a DHAN trad_sym/sec_id (since ALL price/candle data flows through Dhan, even for Kite-placed orders, per this project's "data always Dhan, orders via Kite" design) — and a wrong translation means monitoring the WRONG contract's price for SL/TP, worse than no monitoring at all. TRAP #13 already showed that a naive string-guess (`dhan_sym_to_kite()`, the forward direction) silently produced garbage for NIFTY's weekly-expiry naming scheme.

**The safe technique (new, `resolve_dhan_from_kite_symbol()` in `brokers/kite_broker.py`):** Instead of parsing either symbol's TEXT, use each broker's own STRUCTURED instrument fields and cross-match on those. `kite.instruments("NFO")` returns `{name, expiry (date), strike (float), instrument_type}` per instrument — exact-match the position's `tradingsymbol` against this list to get those 4 structured fields, no parsing. Then look up Dhan's scrip master (`dhan_master._options_cache`, keyed by symbol → expiry-string → list of `{strike, type, sec_id, trad_sym}`) using those same 4 structured values (expiry compared by date only, since Dhan's cache key is a full datetime string) — again no parsing, direct field equality. This is the reverse of `resolve_kite_symbol()`'s existing forward direction (Dhan trad_sym → Kite symbol, also structured-match, built for TRAP #13), applying the exact same "match structured fields from BOTH sides' own data, trust neither side's string format" discipline just going the other way.

**Wired into `_handle_untracked()`:** Kite branch now attempts this resolution (scoped to `exchange=="NFO"` — options only, matching this system's SL/TP/hedge/RMS model); auto-adopts into `order_store` (tagged `UNTRACKED_ADOPTED`, `MANUAL_ENTRY_KITE`) only on a confident exact match; falls back to the original alert-only behavior whenever resolution fails for any reason (unmapped instrument, API error, no match) — never adopts a guess.

**Also added:** `broker_sync.reconcile_if_due()` — an auto-triggered version of the existing "🧾 Reconcile vs Broker" button (own 180s cooldown, wired into `pos_monitor_loop`) — catches a manual entry+exit round-trip that both complete inside one 30s untracked-scan gap (untracked-scan only diffs CURRENT positions, so a trade that opens and closes within one gap never appears as "currently open" to be caught by that scan alone). Button stays fully available for on-demand use — this doesn't replace it.

**Reusable lesson:** When translating an identifier between two systems that each have their own naming scheme, resist string-parsing/guessing in EITHER direction if either system exposes the underlying structured data (dates, numbers, enums) instead of just a formatted string — cross-match on the structured fields, which are unambiguous, rather than re-deriving a format from a string that might have edge cases (weekly vs monthly expiry codes, single-letter month encodings, etc.) neither side documents precisely. If a system's API gives you `{name, expiry_date, strike, type}` alongside a formatted `tradingsymbol`, use the structured fields for any cross-system matching — never parse the formatted string back apart.


---

## TRAP #80 — gating_status() never checked the account-level trailing-lock-fired flag for STRATEGIES — only webhook_executor honored it, found while building the kill-floor 🔴 (Fixed)

**Symptom:** None live-observed — found by inspection while wiring the new KILL-ALL profit floor (2026-07-02), not by an incident. Could have meant: account-level trailing lock fires, squares everything off, writes the day-level `trailing_lock_fired_<date>.txt` flag — and a strategy process (range_trader/rsi_trader/universe_trader) takes a brand-new entry five seconds later anyway, because nothing in its own entry path ever checked that flag.

**Root cause:** `webhook_executor._do_entry()` explicitly imports `trader_dashboard._trailing_lock_fired_today` and checks it before every entry (added when the aggregate trailing lock was first built). `risk_gate.gating_status()` — the consolidated "can this strategy enter right now?" check that every strategy's `strategy_safety.gate_entry()` call routes through — never had an equivalent check. The two entry paths (webhook vs strategy) independently reimplemented the same class of gate (this project's Critical Rule 6/8 exists specifically to prevent this kind of drift) and only one of them got this particular guard.

**Fix:** New `risk_gate.kill_floor_fired_today()` (reads the same flag file webhook already checks) wired directly into `gating_status()`, right after the daily-loss-breach check — now every strategy's entry path blocks on this flag too, automatically, with zero per-strategy code changes needed (they all already call `gate_entry()` → `gating_status()`).

**Permanent guard:** Any account-level "day is done" event (loss cap, profit target, trailing lock, kill-floor) must be checked from the ONE shared gate (`risk_gate.gating_status()`), not re-implemented per entry-path. If a new entry path is ever added that doesn't go through `gate_entry()`, it inherits this exact gap on day one — audit new entry paths against this specific flag as part of onboarding them.

---

## TRAP #81 — `pos_monitor_loop`'s outer catch-all `print()` was STILL missing `flush=True`, months after TRAP #56 diagnosed exactly this line as part of why that incident stayed silent 🔴 (Fixed)

**Symptom:** None new — TRAP #56 (2026-07-01) already named this exact line (`print("Pos monitor error:", e)`, the loop's top-level exception handler) as a factor in why an earlier `UnboundLocalError` sat invisible for minutes on a systemd service with block-buffered stdout. Re-reading the code while adding the kill-floor (2026-07-02) found the line itself had never actually been changed — TRAP #56's fix addressed the deeper `datetime` import bugs that were the crash's ROOT cause, but the visibility gap that let it stay silent (this exact print statement) was diagnosed, written up, and then never patched.

**Root cause:** A "why did we not notice this" investigation correctly named a contributing factor in its written analysis, but the fix commit only touched the bugs that were CAUSING the crash, not the logging gap that was hiding it. Nothing cross-checked the LESSONS.md writeup against the actual diff to confirm every named factor got a line changed.

**Fix:** `flush=True` added to that exact `print()` call, with a comment naming TRAP #56 directly so a future reader doesn't wonder why a one-keyword change gets a comment.

**Permanent guard:** When a bug writeup names MULTIPLE contributing factors (root cause + why-it-stayed-silent + any-other-compounding-issue), verify the fix commit actually touches EVERY factor named, not just the one that stops the crash from happening. A "silent failure" bug is only fully closed when BOTH the trigger and the visibility gap are fixed — fixing only the trigger means the exact same silent-failure shape is one new bug away from recurring, undetected, again.

**Fast-detect:** `grep -n "except Exception as e:" -A 2 trader_dashboard.py | grep "print(" | grep -v "flush=True"` — any bare `except`-then-`print` without `flush=True` in a long-lived systemd process is a candidate.

---

## Feature note — KILL-ALL account-level profit floor (2026-07-02, user-designed, simulation-verified before any live fire-test)

**Context:** User's own words after the earlier per-instrument trailing-lock misfire history: "is baar jab hum banayenge to aisi dikkat se bachne ke liye kya karenge" (what will we do differently this time to avoid the same problem) — an explicit ask for a proactive design conversation before any code, not just a bug-fix-after-the-fact.

**Design (locked via a 2-round AskUserQuestion clarification, calibrated against the user's OWN real trading day — 2026-07-02: account MTM peaked at ₹5,193, closed at ₹1,937, a ₹3,256 giveback):**
- One account-level system, not two — REPLACES the old "aggregate" trailing-lock branch outright (user's explicit call: same underlying flag/mechanism as the pre-existing daily-loss-cap `max_loss_rs`, upgraded UI, not a second parallel system racing it).
- `arm_rs` (₹500 default) — MTM must cross this once before the floor arms at all; below it, only the existing loss-cap protects.
- `gap_rs` (₹1,500 default) — floor = confirmed peak − gap. The exact gap size was picked by table-walking today's REAL trade sequence at 4 candidate gaps (₹0/500/1500/2000) and showing the user which ones would have fired prematurely (during normal multi-position flutter, before the real peak was ever reached) vs which protected more of the real ₹5,193 peak — a concrete, data-grounded choice, not a guess.
- ₹1-fine ratchet, monotonic (never decreases) — the "clean stepped counter" idea from the initial ask turned out to be purely cosmetic once the REAL misfire cause was identified (see below); the underlying lock stays continuous/fine-grained.
- **The actual misfire root cause, once traced through:** not the granularity of the ratchet (₹1 vs ₹100 makes no difference to false-fires) — it was firing on a SINGLE tick's dip with no confirmation. Fixed with two independent anti-whipsaw mechanisms: (1) a `confirm_secs` (60s default) debounce — MTM must stay below the floor for that many CONSECUTIVE seconds before firing, not one bad reading; (2) the peak itself only advances on a "confirmed" value (`min` of the current and previous reading) — a single spike-high tick can never inflate the peak that the floor is computed from either.
- Fire → every leg through the existing `_pre_exit_guard()` (TRAP #75's shared helper — webhook-claim + fresh flat-check), no-price legs queued via the existing `_pending_group_close` (TRAP #74-worklist P4), day-flag write now closes TRAP #80 (blocks every strategy, not just webhook), alert-banner entry (TRAP #79's UI).
- Bad-data handling: if ANY open leg's MTM contribution can't be priced this cycle, the WHOLE cycle is marked unreliable — the floor still ratchets from its last good state but never advances toward firing (and never fires) on data known to be incomplete. Prevents a feed hiccup on one leg from triggering a kill based on an understated total.

**Verification before ANY live exposure:** the exact state-machine transitions (not a mockup — the literal same branch structure as the deployed code) were re-implemented standalone and run against 5 scenarios: today's real rise/fall shape (fires ~₹1,263 better than the actual day did), a single-tick spike (peak unaffected), a whipsaw shorter than the confirm window (no fire), 20 consecutive bad-data cycles (no fire), and a dip-then-partial-recover (floor never drops). All 5 passed before the feature was deployed with `enabled=false` — user will do a live paper fire-test after market close before ever turning it on with real capital.

**Reusable lesson:** When a past feature "confused" users or misfired, the instinct is often to change the DISPLAY (round the numbers, add steps) — but walk the actual failure through to its root before designing the fix. Here the granularity (₹1 vs ₹100) was a red herring; the real fix was temporal (a confirm window) and structural (confirmed-peak, never a raw tick). A user's own proposed fix (finer/coarser increments) is worth taking seriously as a signal of WHERE the pain is, but verify the actual mechanism before building exactly what they described — the right fix here ended up being a different axis (time) than what was first proposed (granularity).

---

## TRAP #82 — Refactoring KILL-FLOOR to call the new shared `advance_trailing_lock()` rebinding `_kf_state` without a `global` declaration shipped a live `UnboundLocalError`, same scoping shape as TRAP #56 🔴 (Fixed)

**Symptom:** `algo-monitor` logs, ~8 cycles (~40s) right after a deploy: `[KILL-FLOOR] check error (skipped this cycle): cannot access local variable '_kf_state' where it is not associated with a value` — every single cycle, kill-floor monitoring fully dead for that window (caught by tailing logs immediately post-restart, not by an incident).

**Root cause:** The pre-refactor code only ever mutated `_kf_state` in place (`_kf_state["armed"] = True`, etc.) inside `pos_monitor_loop` — no `global _kf_state` declaration needed for that, since item-assignment on a dict doesn't rebind the name. The refactor to call the new shared `risk_gate.advance_trailing_lock()` changed the call site to `_kf_state, _kf_changed = _rg_kf.advance_trailing_lock(_kf_state, ...)` — a tuple-unpack assignment, which IS a rebind of the name `_kf_state`, even though the function mutates and returns the SAME object. Python's scoping rule doesn't know that at compile time: any assignment to a name anywhere in a function body makes that name local for the WHOLE function, so the earlier read (`not _kf_state["fired"]`, a few lines above the call) threw `UnboundLocalError` — this is the identical mechanism as TRAP #56's `_dtc`/`datetime` bug, just triggered by refactoring toward a shared function instead of an import rename.

**Fix:** Added `_kf_state` to `pos_monitor_loop`'s existing `global` statement (same line that already lists `_pos_lock_state`, `_peak_day_str`, etc.).

**Why it reached deploy:** `ast.parse()` was run before every deploy this session specifically to catch syntax breakage from the same refactor's indentation changes — and it did (a separate over-indentation bug in the same edit was caught and fixed before deploy). But `ast.parse()` only catches SYNTAX errors, not scoping bugs — an `UnboundLocalError` from a missing `global` is only detectable at runtime, when the function actually executes the code path with the offending read-before-write order. Caught within ~40 seconds because logs were tailed immediately after every restart on this live trading system, not because any static check flagged it.

**Permanent guard:** Any time a function-scoped variable's assignment pattern changes from "mutate in place" (`x[k] = v`, `x.update(...)`) to "rebind" (`x = f(x)`, `x, y = f(x)`), grep that function's `global` statement to confirm the name is listed — mutate-in-place never needs `global`, rebind always does if the name is also module-level. `ast.parse()` / a syntax check is necessary but not sufficient for this class of bug; only running the code (or a static analyzer like `pyflakes`/`pylint` that tracks scoping, which this project doesn't currently run) catches it ahead of time. Fast-detect for existing code: `pyflakes trader_dashboard.py 2>&1 | grep -i "local variable"` would have flagged this specific bug pre-deploy.

---

## TRAP #83 — Per-position Trailing Points (premium) SL ratcheted off a single raw tick, with none of the KILL-ALL floor's own spike protection 🔴 (Fixed)

**Symptom:** None live-observed yet — found by user request ("iske implementation me kahin flaws to nahi hai") before trusting a Gemini-built "Trailing Points (premium)" default SL with real capital. It's the account-wide default SL applied to every new position.

**Root cause:** `_pos_monitor_check_one`'s `MAX_LTP`/`MIN_LTP` tags — the peak/trough the `trailing_pt` SL ratchets from — updated via a plain `max(ltp, existing)` / `min(ltp, existing)` every 5s cycle, off whatever the feed/REST/stale-cache fallback chain happened to return that tick. No smoothing, no confirmation. A single spike or stale tick (thin option book flicker, a feed glitch, a quote correction) permanently ratchets the SL to a level real price never actually held — and since the ratchet never reverts, that level then gets crossed on a subsequent real candle close, causing a premature stop-out on noise. This is the exact failure shape the KILL-ALL floor was built to avoid (TRAP #80-81's 2-reading confirmed-peak + consecutive-seconds confirm window) — but that protection was never ported to this earlier, per-position SL feature.

**Fix:** Added a SEPARATE `CONF_MAX_LTP`/`CONF_MIN_LTP` track (plus a `PREV_LTP` tag to remember the previous tick), using the same `confirmed = min/max(prev_reading, cur_reading)` technique as `risk_gate.advance_trailing_lock()` — a new high/low only counts once it holds for 2 consecutive checks, so one spike tick can't move it. This feeds ONLY the `trailing_pt` SL/TP ratchet inside `_generic_px`. The existing raw `MAX_LTP`/`MIN_LTP` tags are untouched and still drive the Open Positions Run-Up/Run-Down (MFE/MAE) display, which is SUPPOSED to show the real best/worst tick seen, glitch or not — conflating the two would have quietly changed that unrelated display's meaning.

**Left alone, flagged (not fixed, per explicit user scope):** The same `trailing_pt` branch has a separate, currently-dormant bug — the Target (TP) side's `ref_ltp` is bound to the wrong extreme (a BUY's TP uses `min_ltp` instead of `max_ltp`, and vice versa for SELL), so TP would trail off ADVERSE price movement instead of favorable if `trailing_pt` is ever selected for Target Type. Not live today (Target Type is "Amount (₹)"); flagged for whoever flips that switch next.

**Permanent guard:** Any trailing-lock/ratchet/floor feature that reads a raw per-tick price/P&L feed and never reverts its internal high-water-mark needs the same 2-reading-confirm (or an equivalent debounce) BEFORE it's trusted with real capital — a single bad tick is not a hypothetical, this codebase has hit it before (that's why the KILL-ALL floor has this protection at all). When porting a proven safety pattern to a new use, port the WHOLE pattern (confirm-window included), not just the headline behavior (ratchet up, never down).

---

## TRAP #84 — 2026-07-02's restart-recovery fix (TRAP #76) landed in `_TRADERS/rsi_trader.py`, a file the dashboard never actually runs — the LIVE rsi_v1 script (`01_rsi_v1.py`) stayed unprotected, producing a real duplicate-entry + phantom-₹0-P&L incident the very next day 🔴🔴🔴 (Fixed)

**Symptom (user-reported, live, 2026-07-02):** RSI strategy showed a symbol that had already been sold appearing to get bought again, paired with same-price ₹0 P&L round-trip rows for NTPC and HDFCBANK in the Point-Per-Trade table (entry price === exit price exactly, both "SELL"-side legs).

**Root cause, chained:** (1) `trader_dashboard.py`'s `STRATEGIES` dict runs `_TRADERS/01_rsi_v1.py` for BOTH the `"rsi"` and `"rsi_v1"` strategy ids — confirmed via `RSI_SCRIPT = TRADERS_DIR / "01_rsi_v1.py"` and both dict entries' `"script"` key. (2) TRAP #76 (2026-07-01/02) fixed the exact "restart wipes in-memory position tracking" bug in `range_trader.py`, `universe_trader.py`, and a file called `_TRADERS/rsi_trader.py` — a SEPARATE, older RSI script that looked like the live one (same strategy logic, same doc comments referencing `nifty_config.json`'s `"rsi_v1"` key) but was never actually wired into `STRATEGIES` — a direct read confirmed `01_rsi_v1.py` had zero restart-recovery code at all. (3) Any mid-day restart (dashboard restart, VPS reboot, crash-relaunch, or TRAP #57's auto-scheduler re-arm pass) wiped `01_rsi_v1.py`'s own `positions`/`active_opts` dicts to flat while a position could still be genuinely open in `order_store`/at the broker — the next matching RSI signal then fired a brand-new duplicate BUY on that already-open symbol. (4) `order_store._net_rows()`'s Pass-1 netting (keyed on `(source, strategy, trad_sym)`) treats a second same-side leg arriving while one's already open as a "pyramid/dup" — it shoves the ORIGINAL (orphaned) leg into `leftover`, and Pass-2 then FIFO-pairs `leftover` legs purely by `(mode, trad_sym)`, blind to strategy or real causality — synthesizing a same-price/₹0 "round trip" out of two legs that had no real relationship to each other. This is the exact mechanism that produced the NTPC/HDFCBANK ghost rows.

**Why the confusion kept recurring, root-caused:** `_TOOLS/backtest_engine.py` had `import rsi_trader as rsit`, used only for its 6-line `compute_rsi()` Wilder-RSI helper. `01_rsi_v1.py` literally CANNOT be imported as a Python module (identifiers can't start with a digit) — so the unused duplicate file was the only importable source of that formula, which is exactly why it kept surviving past reviews looking "real" enough to matter, instead of getting deleted the first time someone noticed two RSI files existed.

**Fix:** (a) Ported `_recover_rsi_state()` into `01_rsi_v1.py` (from the unused file, with its "closed" key corrected to the actual `order_store.trades_for()` key name, `"details"` — a latent undercounting bug in the original that never actually ran live, so never got caught), called at `run()` startup with `last_date` seeded to `ist_now().date()` immediately after (not `None` — TRAP #76's own lesson, so recovery can't be undone by the loop's first "new day?" check one line later). (b) Added a NEW per-cycle re-validation against `order_store` (not just a one-time startup recovery) — every scan cycle now checks each in-memory-open symbol against `order_store`'s current open legs and clears stale state if it was closed externally (manual close, trailing-lock squareoff, pos_monitor EOD) — catching drift that happens mid-day, not just at restart. (c) Inlined the Wilder-RSI formula directly into `backtest_engine.py` (no import from either strategy file), which let `_TRADERS/rsi_trader.py` be deleted outright — verified via a repo-wide grep that every remaining reference was a comment/docstring, zero other live callers. (d) Fixed the underlying `STRATEGIES["rsi"]["grep"]` field (`"rsi_trader"` → `"01_rsi_v1"`, `trader_dashboard.py`) and the equivalent stale `TRADER_SCRIPTS["rsi"]` entry in `health_check.py` — both were only a PID-detection FALLBACK behind the `--id` exact-token match (so not the direct cause of the entry bug), but the same "two names for the same thing, one of them wrong" root pattern.

**Permanent guard:** When a proven fix (restart-recovery, safety gate, anything from this project's pre-mortem checklist) gets "ported to strategy X," verify FIRST which file X's `STRATEGIES`/`TRADER_SCRIPTS` dict entry actually launches — a strategy id and a file that LOOKS like its implementation are not the same fact-check. If a codebase ever has two files implementing the same strategy logic, one of them is dead weight that will eventually cause exactly this confusion — either delete it immediately (after confirming zero real callers) or make the difference between them impossible to miss (e.g. one clearly marked `LIBRARY-ONLY, DO NOT RUN`). A module that can't be imported due to a naming constraint (leading digit, reserved word, hyphen) is a signal to extract any genuinely-shared logic into a properly-importable helper — don't let that constraint quietly justify keeping a whole second copy of a live-trading script around just so something else can reach one function in it.

**Fast-detect:** `grep -n "positions\s*=\s*{}\|active_opts\s*=\s*{}" <strategy_file>` with no adjacent `_recover_*` call above the main loop is a restart-recovery gap. `grep -rn "STRATEGIES\[.<id>.\]\|STRATEGIES.get(.<id>.)"` vs. the actual `"script"` value in the dict — if two strategy ids resolve to the same script file, anything "fixed for id A" needs to be checked against id B's actual behavior too, they're the same running process.

---

## TRAP #85 — Every strategy discards yesterday's candles, so RSI/Chain need a fresh warm-up window every single market open before they can signal at all 🟡 (Known — diagnosed, user chose NOT to fix, 2026-07-03)

**Symptom (user-reported):** Both `rsi_v1` and `ARS_CHAIN_V1` ("Chain") consistently produce their first signal well after market open (user's own observation: "9:30 ke baad hi signal aata"), every single day.

**Root cause:** Both live-candle fetchers throw away any candle data from before today — `_TRADERS/01_rsi_v1.py`'s `fetch_candles()` requests `fromDate=today, toDate=today` directly from Dhan (never even asks for a prior day); `_TRADERS/range_trader.py`'s `fetch_1m()` actually requests 2 days back from Dhan but then explicitly filters `df[df["time"]... == today_str]` ("Sirf aaj ke bars rakho," line ~561) and drops everything else. So every trading day, candle-count restarts from zero at market open — and both strategies have a hard minimum-bar gate before they'll even evaluate a signal: `range_trader.run_signal_engine()` line 300, `if len(df_1m) < 20: return None, None, None` (20 bars × 1-min timeframe = ~20 min after 9:15 open ≈ 9:35 AM); `01_rsi_v1.get_signal()`, `if len(df) < period + 5: return None, None` (rsi_period=14 → 19 bars × configured 2-min timeframe = 38 min after open ≈ 9:53 AM). Both numbers land right around the user's "after 9:30" observation. No TRAP/ARCHITECTURE_LOG entry documents WHY "today only" was chosen — most likely just inherited from `validate_strategy.py`'s per-day backtest convention without accounting for the live warm-up cost it creates every morning.

**Possible fix (not applied — user's explicit call: "fix nahi isko note kar lijye"):** `range_trader.py` already fetches 2 prior days from Dhan before throwing them away — the tail of that data could be kept and prepended purely as an ATR/RSI warm-up buffer (indicator math only), while entry/zone-touch logic stays scoped to today's bars only (no signal would ever fire off a stale prior-day zone). Would let both strategies produce valid signals from ~9:15-9:16 instead of losing the first 20-40 minutes of every session.

**Why left alone:** Changes daily signal timing on two live strategies — a real behavior change, not a bug fix, and the user wanted to think about it / prioritize separately rather than ship it same-session.

**Fast-detect:** `grep -n "== today_str\|fromDate.*today.*toDate.*today" _TRADERS/*.py` finds the today-only filters; `grep -n "len(df.*) < \|len(df_1m) < " _TRADERS/*.py` finds the warm-up bar gates that make the filter costly every morning.

---

## TRAP #86 — A CAPITAL_BLOCKED (rejected, never-placed) entry has no terminal status in `order_store`, so it shows up as a phantom "open position" — cascading into a false capital-in-use loop that blocks every later real signal, AND getting recovered as a real position on the next restart 🔴🔴🔴 (Fixed)

**Symptom (user-reported, live, 2026-07-03):** User asked why the dashboard showed real "in-use" capital for a strategy that had placed zero real trades that day (Completed Trades empty, Open Positions empty except the same 2 "blocked" rows). Screenshot: HINDUNILVR blocked at 09:40 (`in-use ₹0 + needed ₹118196`, correct — first attempt of the day), then NIFTY blocked at 09:48 (`in-use ₹140926 + needed ₹193657`) — that ₹140,926 was entirely phantom.

**Root cause:** `order_store._dead_filtered()`'s `_DEAD` set (`rejected/cancelled/canceled/failed/expired/externally_closed`) doesn't include `"blocked"` — the status a `CAPITAL_BLOCKED` entry gets recorded with when `strategy_safety.gate_entry()`/`risk_gate.check_capital()` refuses it pre-placement. So a blocked row survives `_dead_filtered()`, gets no matching opposite leg (nothing was ever placed to close), and `_net_rows()`'s Pass-2 leftover logic leaves it as an unmatched `_as_open()` row — indistinguishable from a genuinely open position in the `"open"` list `order_store.trades_for()` returns. The dashboard UI already knows to exclude these (`templates/index.html`'s `isCapBlocked` filter, used to show them separately in the "🚫 Capital se Block hui Entries" box) — but two BACKEND consumers of the same `"open"` list never got the same filter:

1. `risk_gate._today_open()` feeds both `capital_in_use()` (global + per-strategy cap check) and `exposure_by_underlying()` (cross-strategy concentration cap) — so a blocked entry's re-estimated margin (recomputed fresh via `dhan_real_margin` each time, since it depends on current LTP) permanently inflated every LATER signal's capital check that day. Confirmed live: HINDUNILVR's block fed directly into NIFTY's in-use figure 8 minutes later, with zero real capital actually deployed anywhere.

2. `_TRADERS/range_trader.py`'s `_recover_state_from_order_store()` (TRAP #28/#76's restart-recovery) re-attached these SAME 2 phantom rows as real SHORT positions on the very next restart (entry=="SELL" matched, no CAPITAL_BLOCKED exclusion) — confirmed live via the `[RECOVER] re-attached 2 open position(s)` log line. The live exit path's fresh broker-flat-check (`broker.positions()` + `_check_flat`, added for TRAP #62/manual-close protection) would have stopped a wrong ORDER from firing, but the phantom "already in position" state still silently suppressed NEW entries on those 2 symbols until something eventually triggered that flat-check. Same latent gap existed in `universe_trader.py`'s equivalent recovery AND this session's own brand-new `01_rsi_v1.py` `_recover_rsi_state()` — none of the three recovery functions excluded `CAPITAL_BLOCKED` rows.

**Fix:** Added a `"CAPITAL_BLOCKED" not in (p.get("tags") or [])` exclusion to `risk_gate._today_open()` (fixes both `capital_in_use()` and `exposure_by_underlying()` in one place) and to all 3 strategies' restart-recovery functions (`range_trader.py`, `universe_trader.py`, `01_rsi_v1.py`) — mirroring the exclusion the dashboard frontend already had. Did NOT touch `order_store._dead_filtered()` itself — adding `"blocked"` there would also hide these rows from the "Capital se Block hui Entries" UI box, which legitimately needs them to still appear in `"open"`. The fix is scoped to each BACKEND consumer that was wrongly treating "in the open list" as "genuinely open," not the shared data source.

**Permanent guard:** Any time a NEW terminal/non-terminal order status gets introduced (here: `"blocked"`, added for the capital-cap-refusal UI feature), audit every consumer of `order_store.trades_for()`'s `"open"` list — not just the one screen it was built for. A phantom-status leak like this doesn't just look wrong in a table; if the same list feeds a capital/concentration/exposure CHECK, it directly degrades a real risk guardrail (starves legitimate entries) and if it feeds a restart-RECOVERY function, it fabricates positions that were never real. `grep -rn '\.get("open")\|trades_for(' *.py _TRADERS/*.py` and check each call site's assumption about what "open" means.

**Fast-detect:** `grep -n 'CAPITAL_BLOCKED' order_store.py` (or any file) vs. `grep -n 'isCapBlocked\|CAPITAL_BLOCKED' templates/index.html` — if the frontend filters a status/tag the backend risk/recovery logic doesn't, that's this exact bug shape. `python -c "import risk_gate; print(risk_gate.capital_in_use(None))"` on a day with zero completed trades and zero real open positions should print `0` — if it doesn't, some non-real row is leaking into the sum.

---

## TRAP #87 — `dhan_feed`'s WebSocket reconnect had no backoff, hammered Dhan every 2s forever once 3 independently-connecting processes tripped a connection rejection 🟡 (Fixed)

**Symptom (user-reported, live, 2026-07-03):** `ARS_CHAIN_V1.log` showed a continuous, unbroken stream of `[dhan_feed] loop error, reconnecting in 2s: server rejected WebSocket connection: HTTP 429` — no gaps, no successful reconnect, for several minutes straight.

**Root cause:** `dhan_feed.py` is imported independently by 3 separate long-lived processes — `trader_dashboard.py` (algo-dashboard), `monitor_daemon.py` (algo-monitor), and every live strategy that calls `dhan_feed.start()` directly (`range_trader.py`, `universe_trader.py`) — each opening its OWN WebSocket "Full packet" feed connection to the SAME Dhan account/credentials (confirmed via `grep -rn "dhan_feed.start(" *.py _TRADERS/*.py` — 3 distinct call sites). Dhan's feed gateway rejects a new connection attempt with HTTP 429 when the account already has too many open/reconnecting — restarting all 3 processes together (done this session, for 2 unrelated fixes within ~15 minutes) apparently tripped that limit. `_run_loop()`'s exception handler then made it worse: a flat, non-growing `time.sleep(2)` retry with no backoff meant every single rejected attempt was followed by ANOTHER attempt just 2 seconds later, forever — never giving Dhan's rate-limit window a chance to actually clear.

**Verified NOT an entry-blocking bug before fixing:** `strategy_safety.check_contract_liquidity()`'s own docstring (and code) confirms it fails OPEN (`ok=True`) when live depth data is genuinely unavailable from both the feed AND the REST fallback — this is a liquidity *enhancement*, not a gate that stops trading. Checked this explicitly because the user's first reaction was "warna entry hi nahi aayegi" (worried this was silently blocking real signals) — it wasn't, but the reconnect storm was still worth fixing on its own merits (feed health, and unknown longer-term interaction with Dhan's broader rate limits).

**Fix:** Exponential backoff in `_run_loop()`'s except handler — 2s → 4s → 8s → 16s → 30s (capped), reset to 2s the instant `_feed.run_forever()` returns without raising (i.e. a connection was actually accepted). Restarted all 3 processes that load this module (`algo-dashboard`, `algo-monitor` via systemd; `ARS_CHAIN_V1` via the dashboard's own stop/start API) — `algo-dashboard`'s `KillMode=process` and `algo-monitor`'s `KillMode=control-group` were both checked first (`systemctl show <svc> -p KillMode`) and each strategy subprocess's actual cgroup verified (`cat /proc/<pid>/cgroup`) to confirm neither systemd restart would cascade-kill a live strategy holding a real open position (`ARS_CHAIN_V1` had a genuine TCS SELL open at restart time — confirmed strategy subprocesses live in `algo-dashboard.service`'s cgroup, not `algo-monitor`'s, and `algo-dashboard`'s own `KillMode=process` doesn't touch children either way). Post-restart: recovery correctly re-attached exactly the 1 real position (not the phantom TRAP #86 rows), feed connected clean with zero 429s.

**Permanent guard:** Any module that opens a persistent external connection (WebSocket, long-poll, etc.) and is imported by MULTIPLE independent processes needs backoff on its own reconnect loop — a flat-interval retry that's fine for a single-process module becomes a hammering loop the moment 2+ processes' retry timers can collide against a shared external rate limit. Before restarting any systemd service that manages live-trading subprocesses, always check `KillMode` (`systemctl show <svc> -p KillMode`) and the actual cgroup of any child holding an open position (`cat /proc/<pid>/cgroup`) — `KillMode=control-group` cascades to every process in that unit's cgroup, not just the tracked main PID; `start_new_session=True` on the child's `Popen()` call does NOT protect it from a cgroup-based kill (that's about process-group/session signal delivery, not cgroup membership).

**Fast-detect:** `grep -rn "dhan_feed.start(" *.py _TRADERS/*.py` — count of independent call sites is the count of independent WebSocket connections that'll open under the same account. `journalctl`/log-grep for `reconnecting in 2s` appearing back-to-back with zero gap for more than ~30s is this bug's signature (post-fix, the interval between retries should visibly grow).

---

## TRAP #88 — TRAP #87's backoff reduced the hammering but didn't clear the underlying 429s — algo-dashboard's OWN dhan_feed connection was permanently contending with the live strategy's; disabled it, root-verified safe first 🔴 (Fixed)

**Symptom:** Even hours after TRAP #87's exponential backoff deployed, `ARS_CHAIN_V1.log` kept showing `reconnecting in 30s: HTTP 429` — at the FULL backoff cap, spaced 30s apart, still rejected every single time, for 10+ minutes straight. User (correctly) escalated this as priority: "ye meri main strategy hai... entry kaise milegi" — worried this was an ongoing entry-blocking risk, not a one-off.

**Root cause:** Backoff only slows the RATE of hammering — it doesn't fix a persistent capacity problem. `grep -rn "dhan_feed.start("` showed 2 processes on this account simultaneously holding/reconnecting a WebSocket feed connection: `trader_dashboard.py` (algo-dashboard, via `_ensure_feed_started()`) and `_TRADERS/range_trader.py` (`ARS_CHAIN_V1`, the live strategy). With both permanently trying to hold a connection slot, whichever one is mid-reconnect gets rejected by whichever already holds it — backoff just spaces out the collisions, it can't eliminate them when 2 long-lived processes are both trying forever.

**Verified before touching anything (not assumed) — later found INCOMPLETE, see TRAP #89:** Read `monitor_daemon.py` in full — the process that actually runs `pos_monitor_loop` (SL/TP/EOD/RMS, the real safety-critical loop) imports `trader_dashboard` only for its function definitions and NEVER calls `dhan_feed.start()` itself; it gets all its LTP data from `ltp_poller.py`'s separate batched-REST poller into `shared_ltp_cache`. So dhan_feed's live tick feed has ZERO role in any actual risk-management path — `algo-dashboard`'s own connection was serving UI-only consumers (Quick Order widget LTP, Open Positions live-LTP column, Watchlist/chart freshness), every one of which already has a `_rest_ltp_fallback()` path for exactly this "no feed data" case. **Correction (same day, TRAP #89):** `pos_monitor_loop` and `webhook_monitor_loop` — both of which run INSIDE `monitor_daemon.py`'s own process — DO call `_ensure_feed_started()` (line ~3390/3569 in `trader_dashboard.py`), just not `dhan_feed.start()` directly by name, which is why the `grep -rn "dhan_feed.start("` search above missed it. `algo-monitor` was a 3rd independent caller the whole time — this specific fix (disabling only `algo-dashboard`'s copy) happened to work because only 2 of the 3 were competing at that moment, not because the analysis was complete. See TRAP #89 for the durable fix.

**Fix:** Disabled `_ensure_feed_started()` in `trader_dashboard.py` — it no longer calls `dhan_feed.start()` at all, just sets the guard flag so callers behave as if already started (`add()`/`get_quote()` are safe no-ops on an unstarted feed, confirmed from `dhan_feed.py`'s own code before relying on it). `range_trader.py` is now the ONLY process on this account opening a dhan_feed WebSocket — nothing left to contend with. Restarted `algo-dashboard`+`algo-monitor` (systemd, `KillMode` re-verified) then `ARS_CHAIN_V1` (dashboard API) with zero real open positions confirmed immediately before each step; post-restart the feed connected clean on the very first attempt, zero 429s over the following 20+ seconds of observation (previously: continuous rejections every 30s for 10+ minutes).

**Permanent guard:** Backoff fixes a RATE problem (too-frequent retries against a transient limit); it does not fix a CAPACITY problem (N long-lived processes permanently wanting M<N connection slots) — if retries keep failing even at the backoff cap over an extended window, look for concurrent holders of the same resource, not just "need more backoff." Before assuming a support process (dashboard, monitor, cache warmer) needs its own copy of a live external connection, check whether the actual safety-critical consumer already has an independent, sufficient data path (here: `ltp_poller`+`shared_ltp_cache`) — a "nice to have" freshness feature isn't worth contending with the connection a live strategy actually needs.

**Fast-detect:** If `reconnecting in <backoff-cap>s` (the LOG's own backoff value) still shows a 429 on every single attempt over many consecutive retries, that's a capacity/contention problem, not a rate problem — `grep -rn "dhan_feed.start(" *.py _TRADERS/*.py` again to see how many processes are still trying, and check whether SOME of those callers' consumers of `dhan_feed.get_quote()` already degrade gracefully to REST (in which case that caller's `.start()` can just be removed).

---

## TRAP #89 — TRAP #88's "disable the extra caller" fix was scoped to today's exact process mix, not the general case — user asked "multiple strategies chalengi to phir?" and was right; built a proper cross-process connection-owner election instead (Feature/durable fix)

**Context:** Same-day follow-up to TRAP #87/88. User asked directly: "haan multiple strategies run hogi to ye prb to bilkul aa jayegi" (yes, this problem will definitely come back once multiple strategies are running) — correctly identifying that TRAP #88's fix (disable `algo-dashboard`'s copy, keep `range_trader.py`'s) only worked because exactly one live strategy happened to be running that day. Any second strategy calling `dhan_feed.start()` (e.g. `universe_trader.py`, or a future new strategy file) would immediately recreate the exact 429 collision, since nothing was actually coordinating WHO gets to hold Dhan's limited connection slot(s) — TRAP #88 removed one contender, it didn't build any arbitration.

**Also found while building this:** TRAP #88's claim that `algo-monitor` "never calls dhan_feed.start() at all" was INCOMPLETE — `grep -rn "dhan_feed.start("` only catches that exact literal call, and missed that `pos_monitor_loop`/`webhook_monitor_loop` (both run inside `monitor_daemon.py`'s own process) call `_ensure_feed_started()` — a `trader_dashboard.py` wrapper function that itself calls `dhan_feed.start()`. So there were really 3 independent callers on this account the whole time (`algo-dashboard`, `algo-monitor`, `range_trader.py`), not 2 — TRAP #88's fix happened to work anyway only because it removed one of two ACTIVELY colliding callers at that moment, not because the analysis of "who calls this" was complete. **Lesson inside a lesson:** grepping for a literal function call name misses call sites that go through an intermediate wrapper with a different name — trace the actual call graph, not just the literal string, when counting "how many things do X."

**The fix:** Cross-process leader election in `dhan_feed.py` itself, using the exact same sqlite pattern `dhan_rate_limiter.py` already established (works identically on the Windows dev box and the Linux VPS, no new dependency). A single `owner` table row (`pid`, `heartbeat`) — `_claim_or_renew_ownership()` lets a process become/stay owner if the slot is empty, already owned by itself, or the current owner's heartbeat is stale (>30s, meaning it crashed — this codebase has zero `SIGTERM` handlers anywhere, TRAP #58, so a killed owner can never cleanly release; heartbeat staleness is the only workable detection). `_run_loop()` now checks/renews this claim before EVERY connection attempt and periodically (every 10s) while connected; a process that loses the race just sleeps 5s and retries the claim later — no connection attempt, no 429, no log noise, purely quiet waiting. Every consumer already degrades to REST when `LIVE` is empty (verified in TRAP #88), so a non-owner process functions identically to before, just without the free WS tick. Re-enabled `algo-dashboard`'s own `_ensure_feed_started()` now that it's safe to call from any number of processes simultaneously.

**Verified before deploying to live money:** wrote a standalone 5-case test simulating 2 competing "processes" (via `os.getpid` monkeypatching) against a throwaway test DB — first-claim, contested-claim-while-fresh, self-renewal, stale-owner-takeover, ex-owner-rejected-after-losing — all 5 passed before touching the VPS. Post-deploy (zero real positions confirmed, `ARS_CHAIN_V1` had 1 real AXISBANK position by the time of the actual restart — recovery re-attached it correctly): `algo-monitor` won the race this time (it restarted first), `range_trader.py` correctly detected non-ownership and went quiet — zero 429s, zero errors, in 40+ seconds of observation after restart, versus continuous rejections every 30s beforehand.

**Permanent guard:** Any fix that works by "removing one of N contenders for a shared resource" is only correct for the CURRENT value of N — if N can grow (here: more strategies get added over time), that fix is temporary by construction and needs a flag/comment saying so, or (better, done here) replaced with an actual arbitration mechanism that scales to any N automatically. When counting how many processes call some behavior, grep the exact literal call AND trace through any wrapper functions with different names that might call it — a `grep` for `dhan_feed.start(` missing `_ensure_feed_started()`'s indirection is exactly how TRAP #88 undercounted the real caller list.

**Fast-detect:** `sqlite3 data/dhan_feed_owner.db "SELECT pid, heartbeat FROM owner"` — one row, one pid, heartbeat should be within the last ~10s while market is open. If `ARS_CHAIN_V1.log` (or any other process's log) ever shows dhan_feed reconnect errors again, check this table first — a genuinely-dead owner not clearing (schema/lock bug) would look like every process rejected forever again, distinguishable from a real Dhan-side outage by checking whether ANY process's heartbeat is fresh.

---

## TRAP #90 — `ARS_CHAIN_V1`'s entries were never checked against real Zerodha/broker funds, only a manually-configured ₹ cap estimated via Dhan's margin calculator; the check that WOULD have caught it (`check_broker_funds`) also compared against raw premium instead of real margin for SELL legs 🔴🔴 (Fixed)

**Context:** Same-day follow-up after explaining a "global capital cap ₹200000 hit" block to the user (ADANIENT, correctly blocked — see the capital_in_use() investigation right before this). User asked the sharper question: "Dhan ke margin se hamein kya lena dena, hum Zerodha mein trade kar rahe hain, uske capital ke hisaab se hona chahiye na" — why is a DHAN margin estimate driving a decision about a ZERODHA account's real capital?

**Root cause #1:** `range_trader.py`'s option-entry path already calls `strategy_safety.gate_entry()` (the canonical RMS gate, correctly used) — but never passed a `broker=` object. `gate_entry()`'s step 6 (`check_broker_funds` — real broker balance vs needed, LIVE mode only) is gated behind `if mode == "live" and broker is not None`, so it silently no-op'd on every single entry. The comment at that call site claimed "this legacy trader doesn't have a brokers.* broker object" — stale; `place_order()` a few lines below has resolved one via `get_broker()` for every order for a while. Fixed by resolving `_entry_broker = get_broker(risk_gate.default_broker())` right before the `gate_entry()` call and passing it through.

**Root cause #2, found while fixing #1:** Even once wired, `check_broker_funds(broker, qty * price)` compares available funds against raw PREMIUM (`qty * price`) regardless of side. Correct for a BUY (premium paid IS the real cost — this is why `rsi_v1`, which only ever buys, never exposed this gap despite already passing a broker object into every `gate_entry()` call). A massive UNDERSTATEMENT for a SELL — selling an option blocks SPAN+exposure margin, routinely several times the premium (confirmed live: AXISBANK premium ~₹23,656 vs real Dhan-calculated margin ~₹1,55,265, a ~6.5x gap) — the exact real-vs-notional distinction `capital_in_use()`/`_leg_capital()` already handle correctly elsewhere in `risk_gate.py`. This meant the broker-funds safety net could basically never fire for an option-SELLING strategy even when funds were genuinely tight, since premium is nearly always far smaller than whatever's sitting in the account. Fixed by mirroring `_leg_capital()`'s exact logic inside `gate_entry()`: for a SELL, try `dhan_real_margin()` first, fall back to `qty * price * margin_multiplier` only if that call fails.

**Verified before deploying to live money:** Directly called `dhan_real_margin()` for the actual open AXISBANK position (confirmed ₹1,55,265.47, not the ₹23,656 notional) and `get_broker('kite').funds()` (confirmed real available margin ~₹9.1L) to ground both the diagnosis and the expected post-fix behavior in real numbers before writing a single line of the fix.

**Deliberately NOT changed:** The manually-configured global capital cap (₹1,00,000/₹2,00,000 in `nifty_config.json`'s `_risk.global`) stays as-is — user's own words: that number was meant as a paper-trading ceiling, and remains the actual day-to-day constraint for now since it's currently MORE restrictive than real broker funds (~9.1L available). This fix adds the real-funds check as the underlying safety net beneath that cap, it doesn't replace or raise the cap itself — raising/removing that number, if ever wanted, is a separate, explicit decision for the user to make, not something to bundle into a bug fix.

**Permanent guard:** When a strategy trades through broker A (data/analytics) but places real orders through broker B (execution), any capital/margin/funds check MUST be validated against broker B's real numbers, not broker A's estimate — a plausible-looking number from the wrong broker is worse than an honest "unknown," because it looks like safety without providing any. When adding a broker-funds-style check to any gate, audit every caller's `side` — a formula that's correct for BUY (cost = premium) is not automatically correct for SELL (cost = margin, often multiples of premium); the existing `_leg_capital()`/`capital_in_use()` real-margin-vs-notional split in this codebase was the tell that the SAME distinction was missing here.

**Fast-detect:** `grep -n "check_broker_funds" strategy_safety.py` and read every call site's `side` — if `needed_rs`/its equivalent is computed the same way for BUY and SELL, that's this bug's shape. `grep -n "gate_entry(" _TRADERS/*.py webhook_executor.py` and check whether `broker=` is actually passed at each call site — a `gate_entry()` call with no `broker=` silently skips the entire funds-check step with no error, no log line calling out that it never ran.

---

## Feature note — Indian lakh/crore number formatting across the dashboard + RMS reason strings (2026-07-03)

**User ask:** After a support conversation about a capital-cap block where a plain `₹171878` (no separators) read ambiguously ("1.7L or 17L?"), user asked for ALL numbers in the dashboard — not just chat replies — to use Indian digit grouping (2,00,000) instead of ungrouped or international-style (200,000) numbers.

**Backend (`risk_gate.py`):** New `_inr(n)` helper — Indian grouping (last 3 digits, then groups of 2), handles negatives and decimals, falls back to `str(n)` on bad input. Wired into all 8 user-facing RMS block-reason f-strings (`check_capital`'s strategy/global cap, `check_broker_funds`, `check_concentration`, `check_drawdown`, `daily_profit_target_hit`, RMS daily-loss cap) — these strings show directly in the dashboard's "Capital se Block hui Entries" table and various Risk-tab alerts.

**Frontend (`templates/index.html`):** ~16 previously-unformatted `Math.round(...)` ₹ displays got `.toLocaleString('en-IN')` added — Open Positions' Margin/Run-Up/Run-Down columns, per-strategy TOTAL summary pills, Completed Trades' cumulative-P&L column and per-row/grand-total P&L, trade-chart tooltips, a chart Y-axis label, and the RMS reconcile-funds banner. Two spots (`.unrl-cell`'s live per-row P&L, used by 2 separate group/grand-total aggregators) needed a matching fix on the READ side — `parseFloat(cell.textContent...)` now also strips commas (`.replace(/,/g,'')`) before parsing, since formatting a value that later gets re-parsed for a running total is a real footgun: `parseFloat("1,234")` silently returns `1`, not `1234`, corrupting every downstream sum if only the write side gets fixed.

**Verified before deploying:** `_inr()` tested against 9 real values (lakhs, crores wouldn't apply here but boundary cases like 999/negative did) confirmed correct; all 4 inline `<script>` blocks in `index.html` re-parse cleanly via Node after stripping the file's pre-existing Jinja2 `{% raw %}`/`{% endraw %}` tags (a false-positive syntax "error" from a crude Node-based check that doesn't know about Flask templating — worth remembering for next time this kind of check is run); diffed the edited file against the LIVE VPS copy line-by-line before committing to confirm every changed line was exactly this fix and nothing else.

**Found in passing, not a bug:** `templates/index.html` had substantial pre-existing local changes (a "Log Tab" redesign — new sidebar/folder-card UI) that were NOT part of this session's work and were never committed to git — but a direct diff against the live VPS file confirmed they were already deployed and running there. This is the same "deploy without committing" gap this codebase has hit before (TRAP #27/#69) — worth deliberately `git add`-ing and committing pre-existing-but-undeployed local diffs like this promptly when noticed, rather than letting them silently persist as an uncommitted gap indefinitely.

**Permanent guard:** Any time a formatted (comma/currency-symbol-added) string is later re-parsed elsewhere (for a running total, a sort comparator, a search filter), fix BOTH sides together — grep for every consumer of that same DOM element/field before assuming a display-only format change is safe.

---

## TRAP #91 — `saveColPrefs()`'s Columns settings randomly reverted after refresh (read-modify-write race, 2 un-awaited backend saves) + `01_rsi_v1.py`'s exits had the exact same missing-tag gap as `range_trader.py`'s TRAP #88, same day 🔴 (Fixed)

**Symptom (user-reported, live):** (1) Toggling "Exit Reason" on in the Columns modal, saving, and refreshing sometimes reverted it back off — inconsistent, not every time. (2) RSI's completed MARUTI trade showed a blank Exit Reason, same as TCS's blank reason earlier today (TRAP #88).

**Root cause #1:** `saveColPrefs()` calls `saveUiConfigToBackend('ord_completed_cols', ...)` and `saveUiConfigToBackend('ord_open_cols', ...)` back-to-back, un-awaited. Each call independently does GET `/api/config` → merge its one key into the JSON → POST the whole config back. Two concurrent GET-modify-POST cycles on the same resource is a classic lost-update race: if call A's POST (with `ord_completed_cols` added) lands, then call B's POST lands — but B's in-memory `cfg` was built from a GET that happened BEFORE A's POST completed — B's POST overwrites the file with a snapshot that's missing A's change. Non-deterministic: whichever call's async round trip happens to finish last that particular time "wins," which key gets clobbered varies.

**Root cause #2:** `01_rsi_v1.py`'s `close_position()` (line ~412, `smart_order.execute(..., is_exit=True)`) had literally zero `extra_tags` — identical shape to TRAP #88's `range_trader.py` bug, found the same day while investigating #88's fix. Every RSI exit (signal-driven or 3:15 force-exit) recorded with empty tags, so `order_store._exit_reason()` always returned `''`.

**Fix #1:** `saveUiConfigToBackend(key, value)` now also accepts a single object argument (`{key1: val1, key2: val2}`) to update several keys in ONE atomic GET-modify-POST round trip — backward compatible for every existing single-key caller. Updated both 2-key call sites (`saveColPrefs()`'s save, and the "reset columns" function) to pass a batch object instead of firing 2 separate calls.

**Fix #2:** `close_position()` now tags `"RSI_MIDLINE_EXIT"` (signal-driven exit, RSI crossed back through 50) or reuses the already-recognized `"EOD_315_SQUAREOFF"` (the 3:15 force-exit branch, detected via `rsi_val == "3:15"`, the sentinel that call site already passes). Added `RSI_MIDLINE_EXIT` to `order_store._EXIT_REASON_PREFIXES`. Also gave both `ATR_TRAILING` and `RSI_MIDLINE_EXIT` a proper emoji+color badge in `_exitReasonBadge()` (they were falling through to plain gray text before — not blank, but not styled like every other reason either).

**Historical backfill (user explicitly asked: fix the code AND backfill existing blank rows, every time this class of gap is found):** Audited ALL completed trades all-time, not just today's — 119 of 203 had a blank `exit_reason`, broken down by strategy/source: `ARS_CHAIN_V1` 55, `manual` 34, `ema920` 21, `default`/webhook 5, `rsi_v1` 4. Backfilled only where the reason could be determined with real confidence, never guessed:
- `ARS_CHAIN_V1` → `ATR_TRAILING` — verified via `git log -p` across the file's ENTIRE history that this is the only exit-reason value this code has ever produced (pos_monitor_loop's own SL/TP/EOD exits already tag separately and were never part of this gap).
- `rsi_v1`'s other 3 (beyond the reported MARUTI one) → `RSI_MIDLINE_EXIT` — confirmed none exited near 15:15 (ruling out the EOD branch), and the code only has these 2 possible reasons.
- `source=manual` → `MANUAL_CLOSE` — not a guess, unambiguous by definition of what "manual" means.
- Row-matched via (symbol, opposite side, exact exit price, exit timestamp prefix, strategy) against the raw `orders` table — any match that wasn't EXACTLY 1 row was skipped rather than risk tagging the wrong leg. Dry-run previewed to the user before writing (71 confident vs 21 skipped-as-ambiguous), executed only after explicit go-ahead on that exact number.
- **Deliberately left blank (48 rows):** `ema920` (21, a retired strategy variant with no matching current config and no comparable reason-tracking in its code — genuinely unknowable) and `default`/webhook (5, `webhook_executor.py` has MULTIPLE possible reasons — Target/Trail-SL/Reversal/etc. — with no way to disambiguate which applied to each old trade), plus 21 more from the confident-reason categories that got skipped by the row-matching ambiguity guard (0 or 2+ candidate rows found) rather than mistagged. Result: 71/119 backfilled, 48 honestly left blank with the reason why explained to the user — fabricating a plausible-sounding but unverifiable reason into real trading history would have been worse than an honest blank.
- New standing rule from this incident: **CLAUDE.md Critical Rule 9** — every exit call must tag a reason (with the matching `_EXIT_REASON_PREFIXES` entry + badge), and any time a missing-reason gap is discovered in an EXISTING strategy, backfill history where confidently possible — but never guess where genuinely ambiguous.

**Permanent guard:** Any function that does "read the whole shared resource, modify one part, write the whole thing back" (a read-modify-write pattern against a single JSON config file, common in this codebase's `/api/config` pattern) is unsafe to call twice concurrently without awaiting between calls — always batch multiple keys into one round trip, or explicitly `await` sequentially if batching isn't practical. When a strategy file's own exit path is fixed to tag a reason (TRAP #88), immediately check every OTHER strategy file for the identical gap in its own exit call — the same category of bug (computed-a-reason-but-never-tagged-it) is very likely to exist wherever the same "log the reason, then call smart_order.execute() separately" pattern was copy-pasted.

**Fast-detect:** `grep -n "saveUiConfigToBackend(" templates/index.html` — any TWO calls with different first-arg keys appearing within a few lines of each other (not awaited/sequenced) is this race's shape. `grep -n "smart_order.execute(" _TRADERS/*.py | grep -v extra_tags` finds exit-order call sites with no reason tag at all.

---

## TRAP #92 — `broker_sync`'s ghost-position scan processed CAPITAL_BLOCKED phantom rows, producing a real ₹15.75L phantom-profit row in Completed Trades 🔴🔴🔴 (Fixed + corrupted row deleted)

**Symptom (user-reported, live):** Completed Trades showed `NIFTY-Jul2026-24350-CE SELL Entry 24,329.70 Exit 98.00 Points +24,231.70 Net ₹15,73,032` — entry price was actually the NIFTY INDEX level (not this option's premium), and the P&L was obviously impossible. This directly corrupted the day's real TOTAL P&L (would have shown ~₹15.75L too high).

**Root cause — same gap as TRAP #86, a different consumer:** `pos_monitor_loop` and the `/api/sync-positions` ("Sync from Broker" button) route both fetch `order_store.trades_for(...)["open"]` raw and hand it straight to `broker_sync.sync_if_due()`/`force_sync()`, with no `CAPITAL_BLOCKED` exclusion. A capital-blocked entry (e.g. `id=528`, NIFTY-24350-CE SELL blocked at 09:48, recorded at the INDEX level as a fallback price since no real premium was ever fetched for a rejected entry) was never actually placed at the broker — so `_check_flat()` always finds it "flat," and `broker_sync` dutifully records a "ghost close" for it. For the exit price, it just grabs whatever real fill exists for that exact contract that day — which happened to belong to a COMPLETELY UNRELATED, already-closed real trade on the same option (`id=546→551`, a normal SELL@102.20→BUY@98.00 ATR-trailing exit). The bogus close (`id=552`, BUY@98.00, `EXTERNALLY_CLOSED`/`MANUAL_EXIT_BROKER`) got written 36 seconds after the real trade's own exit — `order_store._net_rows()` then paired the blocked entry's placeholder index-level price against this unrelated real fill, producing the impossible trade.

**Fix:** Excluded `CAPITAL_BLOCKED`-tagged rows from the list passed into `broker_sync.sync_if_due()` (in `pos_monitor_loop`) and `broker_sync.force_sync()` (in `/api/sync-positions`) — same one-line filter pattern already used in `risk_gate._today_open()` (TRAP #86) and already present elsewhere in this SAME function for KILL-FLOOR's MTM calc (`_active_pos`, ~line 3642) — just never applied to these two call sites. `untracked_scan_if_due()` checked and confirmed NOT affected (it does its own broker-vs-order_store diff independent of any passed-in open-position list, for a different purpose — catching positions the broker has that order_store has no row for at all).

**Data cleanup (explicit user confirmation before any write, per this project's standing rule for direct DB edits):** Verified the exact corrupted row (`id=552`) one more time immediately before deleting it (re-read from the live DB, confirmed identical to what was diagnosed). Deleted with a precise `DELETE FROM orders WHERE id=552` — not a broader `delete_by_source()` call, which would have removed far more than intended (many legitimate rows share `source='strategy'`). Left `id=528` (the original blocked entry) untouched — once `id=552` was gone and the code fix deployed, it correctly reappeared in "Capital se Block hui Entries" as a normal, unpaired blocked attempt. Verified post-cleanup: the day's total P&L dropped from a corrupted ~₹15.75L+ to a sane ₹2,111 across 12 real completed trades.

**Permanent guard:** `CAPITAL_BLOCKED` rows are NOT real positions and must be excluded from EVERY consumer of `order_store`'s "open" list that treats it as "genuinely open positions requiring broker-side action or reconciliation" — this is now the 3rd such consumer found with this exact gap in one day (`risk_gate.capital_in_use()`/`exposure_by_underlying()` in TRAP #86, all 3 strategies' restart-recovery in TRAP #86, now `broker_sync`'s two entry points in TRAP #92). Any NEW code that reads `order_store.trades_for(...)["open"]` for a purpose beyond pure display should default to excluding `CAPITAL_BLOCKED` unless there's a specific reason not to (the dashboard's own "Capital se Block hui Entries" UI box is the ONE legitimate exception, by design).

**Fast-detect:** `grep -rn '\.get("open"' *.py _TRADERS/*.py` — for each call site, check whether it filters `CAPITAL_BLOCKED` before treating the result as real positions. A single impossible-looking P&L row (entry price wildly mismatched to the instrument's real value, or profit far exceeding any plausible position size) is this bug's signature — cross-check the entry leg's own tags for `CAPITAL_BLOCKED` before assuming it's a data-entry error rather than this exact mechanism.

---

## TRAP #93 — TRAP #92's phantom trade cascaded into a REAL account-wide KILL-FLOOR false-fire (blocked all new entries for the rest of the day) + a second, independent double-counting bug in "Reconcile vs Broker" for weekly-expiry index options 🔴🔴🔴 (Fixed)

**Symptom (user-reported, live, same-day follow-up to TRAP #92):** (1) "Today's Peak P&L" graph still showed `Peak ₹15,77,214 | DD ₹15,75,103` even after the TRAP #92 database cleanup. (2) A second, separate impossible-looking row: `NIFTY2670724350CE` tagged `manual`/`live`/`manual`, an exact duplicate (same price, qty, times) of a real `ARS_CHAIN_V1` trade sitting right above it in Completed Trades. (3) EMA logs showing DH-904 errors (turned out to be a stale log tail from the prior day, `ema_v1` not running — false alarm, no fix needed). (4) `_UI_CONFIG` appearing in the Logs tab sidebar as if it were a strategy.

**Root cause #1 — cascading failure, most serious:** `pos_monitor_loop`'s account-wide MTM/peak tracking (`_trailing_peak_pnl`, `_daily_peak_ever`) computed a real number off the phantom ₹15.75L position from TRAP #92 while that bug was live, and persisted it into `data/peak_pnl_history.json` (append-only, one row per monitor cycle) and `data/kill_floor_state.json`. Once the peak (₹15,77,172) got far enough above the real MTM (₹2,111) to breach the configured gap (₹1,000) for the confirm window (60s), **KILL-FLOOR genuinely fired** — `[KILL-FLOOR] 🔒 FIRED — MTM ₹2111 stayed below floor ₹1576172 for 60s` — writing `trailing_lock_fired_2026-07-03.txt`, which blocks ALL new entries account-wide for the rest of the day (by design, for a REAL breach — this one just wasn't real). Deleting the TRAP #92 database rows did NOT fix this: `_daily_peak_ever` is an intentional monotonic high-water-mark ("never resets") kept in the SAME process's memory and reloaded from `peak_pnl_history.json` on every restart (module-level code in `trader_dashboard.py`, runs at import time) — so even a fresh `algo-monitor` restart just re-absorbed the same corrupted peak from the file and re-armed/re-fired identically.

**First correction attempt failed — a second, subtler lesson:** Editing `peak_pnl_history.json` and `kill_floor_state.json` WHILE the still-running (not-yet-restarted) `algo-monitor` process was alive got silently undone: that process's own `pos_monitor_loop` appends a fresh entry to the history file every cycle (~5s) using ITS OWN in-memory (still-corrupted) peak value, completely independent of what's on disk — so by the time `systemctl restart` actually ran moments later, the file had already been re-corrupted by the old process's next write. **Fix: `systemctl stop` FIRST (confirmed no writer active), edit the files while nothing can write to them, THEN `systemctl start`** — not `restart`, which doesn't guarantee no write happens in the gap between the file edit and the stop taking effect if they're issued as separate commands with any delay between them.

**The correction itself:** `kill_floor_state.json` reset to `armed:false, peak:1838, floor:null, fired:false` (the real settled total was well under the ₹6,000 arm threshold, so it shouldn't have even been armed). `trailing_lock_fired_2026-07-03.txt` deleted. `peak_pnl_history.json`'s ~350 corrupted entries (mtm/peak_ever > ₹50,000 — an obvious threshold given this account's real swings are in the hundreds/low-thousands) replaced with the real settled total (₹1,838) rather than an attempt to reconstruct the exact historical curve during the ~30-minute corrupted window — that per-tick history is genuinely unrecoverable, and a flat honest correction beats a fabricated-but-plausible-looking one. Verified post-fix: `risk_gate.kill_floor_fired_today()` returns `False`, `[TRAILING-LOCK] Restored peak ₹2619` (sane) on the next restart.

**Root cause #2 — independent bug, same symptom shape as TRAP #92 but a different mechanism:** `broker_sync._reconcile_sig()` (built for the "Reconcile vs Broker" feature, TRAP #67/69) extracts `(root_symbol, strike, CE/PE, side, qty, price)` from a raw symbol string via regex, explicitly designed to handle both Dhan's dashed format and Kite's compact format. It works for MONTHLY stock options, where a 3-letter month code separates the expiry encoding from the strike (`"SUNPHARMA26JUL1880CE"` → strike correctly parsed as `"1880"`) — but WEEKLY index options (NIFTY/BANKNIFTY/FINNIFTY) encode the expiry as ALL DIGITS with no letter separator (`"NIFTY2670724350CE"`), so the regex's greedy digit-match swallows the whole run and reads strike as `"2670724350"` instead of `"24350"`. This signature never matches the identical trade already recorded in Dhan format by the strategy itself, so `reconcile_manual_trades()` inserted a duplicate "manual" trade every time it ran (auto every 180s + the manual button) — meaning **every past NIFTY/BANKNIFTY reconcile has likely double-counted**, not just this one instance.

**Fix:** Resolve Kite's raw `tradingsymbol` to Dhan's canonical `trad_sym` via `KiteBroker.resolve_dhan()` — the SAME safe, structured-field cross-broker resolver already built for TRAP #79 (Kite untracked-position auto-adopt) — BEFORE computing the reconcile signature, so both sides of the comparison are in the same canonical format regardless of index vs. stock, weekly vs. monthly. Falls back to the raw Kite string only if resolution genuinely fails (unmapped instrument, cache miss), rather than silently dropping the fill. Verified against the real scrip master: resolved signature now matches the existing DB row's signature exactly. The 2 duplicate rows from this specific instance deleted after explicit user confirmation (re-verified identical content immediately before deletion, per this project's standing rule for direct DB writes).

**`_UI_CONFIG` in Logs sidebar (minor, same-session fix):** `renderLogTab()`'s strategy-key filter excluded `_risk`/`webhooks` but was never updated when `_ui_config` (a settings-storage key, added later for the Columns/notes persistence work) was introduced — added to the exclusion list.

**Permanent guard:** Any monotonic "never resets" high-water-mark that's ALSO persisted to disk for restart-recovery (peak-ever, daily-max, etc.) can get permanently corrupted by a transient data bug — fixing the transient bug's root cause and even deleting the bad underlying records does NOT un-ratchet an already-inflated high-water-mark; the PERSISTED STATE FILE itself must also be corrected. Before editing ANY file a live process actively appends to on a short cycle, STOP that process first — editing while it's still running and then restarting moments later is not equivalent to stop-edit-start, because the still-alive process's very next write cycle can silently undo the edit before the restart takes effect. When a reconcile/matching function handles "either broker's symbol format," test it against BOTH a monthly stock option AND a weekly index option before trusting it — these two categories often have structurally different symbol encodings (letter month-code vs. all-digit), and a regex/heuristic that works for one silently breaks for the other.

**Fast-detect:** `sqlite3 data/kill_floor_state.json` (or `cat`) — a `peak`/`floor` wildly exceeding this account's realistic P&L range is this bug's signature, independent of whether `fired` is currently true. `python -c "from broker_sync import _reconcile_sig; print(_reconcile_sig('NIFTY2670724350CE','SELL',65,102.2))"` — if the returned strike field is far longer than a real strike price (5-6 digits for NIFTY, not 8-10), the weekly-index regex bug is present.

---

## TRAP #94 — TRAP #93's own correction fixed the wrong 2 of 3 numeric fields in `peak_pnl_history.json`, because I misread the raw file's field order 🟡 (Fixed)

**Symptom:** User reported the Today's Peak P&L graph was STILL showing the corrupted spike after the TRAP #93 fix, even in incognito with a hard refresh (ruling out any browser caching).

**Root cause:** `trader_dashboard.py`'s own comment on the `/api/peak-pnl-history` route documents the daemon's raw on-disk row format as `[time, trail_peak, total_mtm, peak_ever]` — i.e. raw index **2** is the actual fluctuating MTM value the chart's main line plots, and indices 1/3 are the (correctly-flat) trailing-peak/peak-ever high-water-marks. My TRAP #93 correction pass assumed the format was `[time, mtm, trail_peak, peak_ever]` and "fixed" indices 1 and 3 — which were, by coincidence, already fine (both intentionally flat at the settled total) — while leaving the real corrupted field, raw index 2, completely untouched across all 86 affected rows (`12:55`–`13:05`, values `1577213.5`/`1577171.5`).

**Fix:** Re-identified the correct field via direct inspection (`['12:55', 1838.0, 1577213.5, 1838.0]` — idx1/idx3 sane, idx2 corrupted), then corrected raw index 2 for all 86 rows. Rather than a flat replacement, **linearly interpolated** between the last known-good value before the corruption (`1763.0` at `12:53`) and the first known-good value after it (`2111.0` at `13:05`) — because unlike the high-water-mark fields, MTM is a continuously-fluctuating instantaneous value; a flat replacement would have produced an obviously-fake flat line on the chart, whereas the surrounding good data made a smooth interpolation the more honest reconstruction. Stopped `algo-monitor` first (per TRAP #93's own stop-before-edit lesson), edited, restarted, then verified via a direct `curl` of the LIVE `/api/peak-pnl-history` endpoint (not just re-reading the file) that the API's normalized output (`[time, mtm, trail_peak, peak_ever]` — the route swaps raw idx1/idx2 on the way out) showed zero rows with mtm > ₹50,000 and a smooth `1767→1839...` progression through the previously-bad window.

**Permanent guard:** When a file's field order is documented only in a one-line code comment (not a schema/dataclass), re-verify the ACTUAL field order against real sample rows before writing any correction script — don't trust a remembered/summarized description of the format from earlier in a debugging session, re-derive it from the raw data every time you're about to make a destructive edit. For monotonic high-water-mark fields (flat-correct-with-a-single-value is right), vs. continuously-fluctuating fields in the same row (interpolate against real neighboring values instead) — treat each field in a mixed-purpose row on its own merits, not with one blanket replacement value for the whole row.

**Fast-detect:** Before trusting any "already fixed" claim on a multi-field JSON array, write a one-line script that greps for the specific corrupted value/threshold across EVERY index of every row, not just the index you believe is corrupted — this alone would have caught the mis-indexing immediately at TRAP #93 fix time instead of one user report later.

---

## TRAP #95 — Constant DH-904 timeout/429 flood: five compounding rate-limit architecture gaps, found via the Rate Limit Room's own data 🔴🔴 (Fixed)

**Symptom (user-reported):** Rate Limit Room showing a continuous stream of `timeout` (8s waits) and `429` events all day — 287 events in 15 minutes (64 real 429s, 171 gate timeouts), with `unknown ×178` dominating the "who" column. User's framing: "kya abhi bhi iska architecture proper nahi hai... cache structure jo banaya tha wo kaam kar raha? Dhan se 1 baar me 1000 symbols aa jate wo use nahi kar rahe kya ham?"

**Root causes (five, compounding):**
1. **`_last_closed_candle_close()` (trader_dashboard.py) — the worst one.** A naive-vs-aware datetime subtraction (`now_ist - timedelta - datetime(1970,1,1)`, where `now_ist` is tz-aware) threw on EVERY call — but only AFTER the Dhan candle request had already been made, and BEFORE its 30s cache got written. The call was also completely UNTHROTTLED (no `acquire()`, no `note_429()`). Net effect: every pos_monitor tick (5s) with a CANDLE_CLOSE SL trigger burned one raw, invisible Dhan candle call that never populated its cache — tripping Dhan's real limits and dragging every throttled caller into the resulting cooldown. The `[_last_closed_candle_close] fail: can't subtract offset-naive and offset-aware datetimes` line had been sitting in the journal the whole time.
2. **Callers ignored `acquire()`'s return value.** The gate's contract is "False = no slot, treat like a transient failure" — but ltp_poller, both strategies' candle fetches, and `_rest_ltp_fallback` all posted to Dhan anyway after a False, which during a 429 cooldown just extends the cooldown for everyone. One 429 → 8s non-order blackout → every 8s-timeout waiter posts anyway → more 429s → repeat.
3. **No per-endpoint budgets.** Dhan enforces SEPARATE limits per endpoint (`/v2/marketfeed/ltp` ~1/sec, `/v2/charts/*` ~1/sec) — the limiter only had one global 3/sec pool, so a 25-symbol candle sweep could eat both non-order slots back-to-back (tripping charts' burst detection), and 4 independent LTP callers could collectively exceed marketfeed's own budget while staying under the global cap. Confirmed empirically: after fixing candles, ALL remaining 429s were LTP-priority.
4. **Consumers not reading the caches built for them.** `api_option_ltp`'s index-price fallback (`Dashboard:IdxLTP`, a top offender) REST-called Dhan even though ltp_poller keeps NIFTY/BANKNIFTY warm in shared_ltp_cache every 1.5s; the positions-LTP route (`Dashboard:PosLTP`) only checked its process-LOCAL cache and REST-called on miss, never the shared one the poller warms with those exact sec_ids.
5. **`unknown ×178` context.** ltp_poller and the monitor's own call paths never called `set_context()`, so the Rate Limit Room couldn't name the actual offenders — misdirecting all previous diagnosis toward the (tagged) strategies.

**Fixes:** (1) epoch math fixed (`int(time.time())` — Dhan timestamps are plain epoch), routed through the limiter with context tag; (2) all four sites now honor `acquire()==False` by skipping (poller's next cycle is 1.5s away; strategies treat it as a transient fetch-fail); (3) `PRIORITY_SUBCAP = {"candle": 1, "ltp": 1}` via a new `pwindows` sqlite table — each endpoint serialized onto its real budget, orders keep their reserved slot (unit-tested: 1 ltp + 1 candle + 1 order per window, no starvation); (4) both dashboard routes read shared_ltp_cache before any REST call, `_REST_LTP_TTL` 3→5s so one missed poller cycle doesn't stampede; (5) `set_context` tags for Poller:BatchLTP / Monitor:PosLTP / Monitor:CandleClose — "unknown" eliminated from the event log entirely. Also answered the user's 1000-symbol question: LTP already batches (ltp_poller, one call for all positions + indices); candles genuinely can't — Dhan has no multi-symbol candle endpoint — so the candle mitigation is bar-boundary-aware caching (a fetch made after the current bar opened contains every closed bar there is → lossless reuse for closed-bar signal logic) + the 1/sec smoothing.

**Deploy note:** strategy processes (range/rsi) were NOT restarted mid-day (4 real open positions) — their changes are defensive and they inherit everything at the next scheduled start; dashboard+monitor restarted immediately (the biggest offenders lived there). UI: Rate Limit Room card moved RMS tab → Logs tab per user request (it's a live log, not risk config).

**Permanent guard:** When a rate limiter exists but 429s persist, check for calls that BYPASS it before tuning its numbers — one unthrottled caller in a hot loop invalidates the whole design (grep every `requests.post(.*dhan` for a missing `acquire`). A gate whose refusals are ignored is not a gate: every `acquire()` call site must handle False as skip-this-cycle. Model third-party rate limits per-ENDPOINT, not per-account, when the vendor 429s endpoints independently. And when an event log has an "unknown" bucket dominating, fix the tagging FIRST — every diagnosis made while the biggest offender is unlabeled is suspect.

**Fast-detect:** `journalctl -u algo-monitor | grep -c 'offset-naive'` → nonzero means an unthrottled hot-loop call is burning quota invisibly. Rate Limit Room "429 by priority" all clustering on one priority = that endpoint's sub-cap is missing or too high.

**Part 2/3 (same session, post-deploy measurement):** A 5-min measurement after part-1 confirmed candle 429s went to ~zero and all remaining 429s were LTP-priority (`Dashboard:PosLTP`/`IdxLTP`) — proving Dhan rate-limits `/marketfeed/ltp` on its own budget. Three more root fixes: (a) **the sub-cap was per-epoch-SECOND counting, which is blind to Dhan's ROLLING window** — a call at N.95s + one at N+1.05s is "2 in 100ms" to Dhan but "1 per fixed-second-window" to us. Replaced the `pwindows` per-second counter with a `plast` table tracking each priority's LAST call timestamp and enforcing true MIN-SPACING (`now - last >= 1/subcap`) cross-process — immune to the boundary burst (unit-tested 3/3: two sub-second-spaced LTP calls, 2nd always refused; order unrestricted). (b) **Two more untracked Dhan callers found by process audit:** `auto_data_downloader.py` (systemd `data-downloader`, 5-min poll, its own candle bursts on new fills) bypassed the limiter entirely — wired in with `acquire("candle")` + `note_429()` on DH-904; and confirmed a *separate project* (`/root/order_book_deploy`, cron 09:12) shares the same Dhan account but already self-throttles at 1 req/sec via its own WS-first design (left as-is, noted). (c) **`ltp_poller.request_watch()` — dynamic warm-list:** the dashboard's positions-LTP and option-LTP routes now ask the ONE batched poller to keep their sec_ids warm (90s TTL, file-backed, cross-process) instead of each making its own REST fallback — so watchlist symbols and quick-order contracts ride the poller's single existing call. `PosLTP` also now returns `cache-pending` instead of forcing a REST call when the gate is busy (frontend re-polls every 3s; the poller has it warm within ~1.5s). This is the real answer to "dashboard overwhelm na kare": every repeating dashboard price need now funnels into the poller's one call/cycle rather than spawning parallel REST traffic.

**Deeper permanent guard (rolling vs. fixed windows):** a fixed-calendar-second rate counter (`int(time.time())` bucket) is NOT equivalent to what a vendor enforcing a rolling/sliding window sees — two calls straddling a second boundary satisfy "1 per fixed window" while violating "1 per any 1s span." For any sub-cap meant to mirror a vendor's real limit, track the LAST-call timestamp and enforce min-spacing, don't count per calendar-second. And a rate-limiter audit isn't done at the app boundary: `grep -rl 'api.dhan.co' /root` across EVERY project/cron/service on the shared account, because the limit is per-ACCOUNT — a sibling project or a systemd timer you forgot about spends the same quota.

**Automated verification (added so the fix proves itself, not me):** the whole fix landed off-hours (Friday evening — Dhan only trades Mon-Fri), so the real proof is the next market open under live multi-strategy load, which the session can't wait for. `rate_limit_verify.py` + systemd `algo-ratelimit-verify.timer` (Mon..Fri 09:55 IST, after the 09:15-09:35 warmup candle-storm) auto-snapshots the last 40 min of `dhan_rate_limit_events.json`, writes a dated report, and pushes a GREEN/RED verdict (with the top offending context named) to the dashboard alert banner. GREEN = ≤10 DH-904s in the window. Pattern reuse: whenever a fix's real validation is gated behind a periodic external condition you can't sit and wait for (market open, a nightly batch, a weekly cron), ship a scheduled self-verifier that surfaces its own verdict — don't leave "should be fixed" unverified until the next unrelated session. **Off-hours caveat found while testing it:** the dashboard's `OptionLTP`/`IdxLTP` polling still hits Dhan REST after market close because `ltp_poller` sleeps off-hours (so the shared cache the dashboard now reads is empty) — harmless (no trading), self-corrects at open when the poller warms the cache, but it means a verify run BEFORE market open reads pre-warm noise; the 09:55 schedule deliberately lands after the cache is warm.

---

## TRAP #96 — Webhook recovery adopted OTHER strategies' + CAPITAL_BLOCKED positions → phantom paper trades + phantom ₹-lakh P&L on every restart, "har roz" 🔴 (Fixed)

**Symptom (user-reported):** Orders & P&L tab showed a cluster of blank-symbol `webhook / paper / ARS_CHAIN_V1` entries every day — BUY at 15:16-15:18 (after the 15:15 cutoff), exit at 19:28 (hours after market close). User: "ye to bekaar entry hai, aisi entries har roz kaise aa jati hain?"

**Root cause:** `webhook_executor._recover_wh_state()` — the function that rebuilds the webhook monitor's in-memory `_wh_state` from `order_store` on every service (module) import/restart — read `order_store.trades_for(today)["open"]` and adopted **every** row with a non-empty symbol, with **no `source=="webhook"` filter and no CAPITAL_BLOCKED/blocked-status exclusion**. Two compounding failures:
1. It adopted the LIVE `ARS_CHAIN_V1` (range_trader) and `rsi_v1` strategies' own open legs — positions that already have their own monitors + configs — then double-managed them through the webhook path in PAPER via a mismatched `default` webhook cfg.
2. The live strategy hits the global capital cap most days, so `order_store` accumulates `status="blocked"` CAPITAL_BLOCKED rows whose `entry_price` is the **index/equity level** (e.g. HINDUNILVR 2225, NIFTY 24329) and whose `trad_sym` is empty. These leak into the `"open"` list (same phantom-open shape as TRAP #86/#92). Recovery adopted them treating `entry_premium = 2225` as an option premium; after 15:15, `monitor_tick` fired `_do_exit(SQUAREOFF_315)` at the real premium (~46), and `_leg_pnl = (2225 − 46) × 300` produced a phantom **₹6.5L-15.7L** — the exact spike that false-fired the KILL floor in TRAP #92-94. Every webhook-monitor restart (several per day) re-ran this, hence "har roz."

**Why it slipped past TRAP #86/#92:** those fixes added the CAPITAL_BLOCKED exclusion to `risk_gate._today_open()`, all three strategies' own recovery functions, and `broker_sync` — but `webhook_executor._recover_wh_state()` was the one recovery path that never got it (and it uniquely also lacked the source filter, since a webhook monitor should only ever own webhook positions).

**Fix:** two guards at the top of the recovery loop — `if (p.get("source") or "") != "webhook": continue` and `if p.get("status")=="blocked" or "CAPITAL_BLOCKED" in (p.get("tags") or []): continue`. Live-verified: an isolated recount showed adoptions drop 5→0; post-restart `webhook_v1.log` no longer prints `[RECOVER] N open webhook position(s)` (was 10 every restart). Deleted the 10 already-created phantom paper rows (id 569-578, all `webhook/paper/blank-trad_sym`) after a full WAL-checkpointed DB backup + a double-guarded DELETE. Left the 5 legit CAPITAL_BLOCKED rows in place — the dashboard's "🚫 Capital se Block hui Entries" panel needs them, and the new filter means they're never re-adopted.

**Permanent guard:** A recovery/rebuild function that reconstructs state from a shared store must filter by OWNERSHIP (`source`) — never adopt rows another subsystem owns just because they share a key field (symbol). And any new consumer of `order_store.trades_for()["open"]` must apply the CAPITAL_BLOCKED/blocked-status exclusion — this is now the 5th place that filter is required; treat "read the open list" as implying "exclude blocked" everywhere.

**Fast-detect:** `webhook_v1.log` showing `[RECOVER] N` where N > the count of genuine webhook positions, or any `webhook/paper` completed trade with an empty `trad_sym` and an index-level entry price = this class of phantom. `grep -c '\[RECOVER\]' logs/webhook_v1.log` spiking with restarts is the tell.

---

## TRAP #97 — Untracked-position scan auto-adopted the user's MANUAL Dhan trades (a broker the algo never trades on) → app squared off his hand-placed positions 🔴 (Fixed)

**Symptom (user-reported, real money):** "Dhan pe 2 baar haath se order, app ne square off kar diya… fir 1 baar order limit pe mila tha wo bhi square off kar diya." The user trades the algo on **Kite/Zerodha** and places MANUAL trades by hand on the **Dhan** app; the dashboard was closing those manual Dhan positions on its own.

**Root cause:** `broker_sync._run_untracked_scan()` (TRAP #58's orphan-catcher) loops `for broker_name in ("dhan","kite")`, pulls each broker's live `positions_detailed()`, and for any position with no matching `order_store` open row calls `_handle_untracked()` → which **auto-adopts** it into `order_store` (`status="open"`, tag `UNTRACKED_ADOPTED`). Once adopted, `pos_monitor_loop` treats it like any algo position and applies default SL / EOD-3:15 / floor squareoff. But `_risk.global.default_broker == "kite"` — **the algo never places orders on Dhan at all**; Dhan is purely a data + manual-trading account. So every position the scan found on Dhan was, by definition, the user's own manual trade — and adopting it handed it to the RMS squareoff engine. TRAP #58 was written when the assumption was "any broker position we don't know about is a lost algo fill"; that assumption is false for a broker the algo doesn't trade on.

**Why it wasn't caught earlier:** the untracked scan was built (2026-07-01) before the live broker settled on Kite-only; on a Dhan-order setup its Dhan branch was correct. The switch to Kite as `default_broker` silently turned the Dhan branch from "recover my own lost fills" into "hijack the user's manual trades," with no code change flagging the inversion.

**Fix:** Dhan is skipped entirely in `_run_untracked_scan()` (a `continue` before the fetch — no adoption, no untracked-alert, and no per-cycle Dhan `positions()` API call, which is rate-limit-friendly too), plus a defense-in-depth early-return for `broker_name=="dhan"` in `_handle_untracked()` for any future caller. Kite orphan adoption (the real algo broker) is unchanged. Deliberately did NOT filter `pos_monitor` by `broker=='dhan'` — paper-mode positions are stored with `broker='dhan'` even for Kite strategies, so a blanket broker filter there would silently break paper-trade SL/EOD simulation; the correct fix is narrow — stop the adoption at the source.

**Permanent guard:** A "recover positions the broker has that we don't know about" mechanism must be scoped to the broker(s) the system actually PLACES orders on. Before adopting/managing any broker position, ask "does the algo ever trade on this broker?" — a data-only / manual-only account's positions are out of scope by definition and must be left completely alone (no adopt, no alert, no squareoff). This is the same ownership-scoping lesson as TRAP #96 (filter by `source`), one level up: filter by BROKER-OWNERSHIP too, not just row-source. Whenever `default_broker` or the set of order-placing brokers changes, re-audit every "scan the broker for untracked positions" path — the scope of "ours to manage" moved with it.

**Fast-detect:** `broker_sync` log line `🔧 ADOPTED untracked <sym> … UNTRACKED_ADOPTED` for a `broker=dhan` trad_sym when `default_broker=kite`, or a completed trade tagged `UNTRACKED_ADOPTED` + `EOD_315_SQUAREOFF`/`SL_HIT` on the non-order broker = the app closed a manual trade. `grep 'ADOPTED untracked' logs/*.log` cross-referenced against `default_broker`.

---

## TRAP #98 — The Optimizer was a SECOND, silently-diverged backtest path: it ran every combo on NIFTY (not the picked symbol), returned 0 results for range/rsi/ema, and stringified configs — while single-Backtest's path was correct all along 🟡🟡 (Fixed, 2026-07-08)

**Symptom (user-reported, backtest-only — no money):** Two separate reports on the Script 3 → Optimize flow. (1) "optimise kar raha to koi result nahi dikha raha" — Range Chain optimize on SBIN showed **0 combos / No results**. (2) After that was fixed: "sharp 1.54 hai, par jaise hi load karte … sharp -0.28 dikh raha" — the optimize result table showed Sharpe **1.54 / PNL 665.60 / 33 trades**, but clicking **Load** (which runs a single backtest of that exact param set, same dates, same SBIN) showed Sharpe **-0.28 / PNL -8.1 / 30 trades**. Same params, same symbol, same dates → wildly different stats. Also a tell: **3 optimize combos were byte-identical** (a swept param that has no effect, all producing the same number).

**Root cause — the optimizer (`_TOOLS/optimizer.py` + `/api/backtest/optimize`) was a parallel implementation of "run a backtest" that had drifted from the real single-Backtest path (`/api/backtest/run` → `api_backtest_run`) in THREE independent ways, none of which the single path had:**
1. **strat_type not normalized.** `api_backtest_run` does `strat_type = _base(sid)` (`ARS_CHAIN_V1` → `range`, `ema_v1` → `ema`). `api_backtest_optimize` passed the RAW variant id straight to `run_backtest`, which dispatches off `_RUNNERS` keyed by BASE type. `bb_v1`/`rsi_v1` happen to exist as explicit `_RUNNERS` keys so they worked by luck; `ARS_CHAIN_V1`/`ema_v1` don't → `run_backtest` returned `{"error":"unsupported strategy type"}` for **every** combo → `_run_single_worker` filtered them all out → **0 results**.
2. **Configs stringified.** The optimizer did `cfg[k] = str(v).strip()` on every value. The single path passes typed JSON. The builtin runners do numeric comparisons (`(h-l) > max_candle_size`), so a stringified `"25"` threw `'>' not supported between instances of 'float' and 'str'` on every combo. Only `bb` (whose `custom_rule_engine` parses strings) ever survived this — which is exactly why bb was the only strategy that had ever produced optimize results, masking the bug for everything else.
3. **Symbol never set (the 1.54-vs-0.28 bug).** `run_backtest`'s runners read the **single `symbol`** key — `_run_range` literally `cfg.get("symbol") or "NIFTY"`, and `_cfg_symbol()` only honors `symbols` when it's a **list**. The optimizer only ever set `symbols` (plural, a comma **string** `"SBIN"`). So `symbol` was absent → every combo silently ran on **NIFTY** regardless of what the user picked. The optimize numbers were real NIFTY numbers; Load ran on the real SBIN. Two different instruments, presented as the same run.

**Why it slipped for so long:** the only strategy anyone had successfully optimized before was `bb` — and bb dodged bug #1 (explicit `_RUNNERS["bb_v1"]` key) AND bug #2 (string-parsing engine). bug #3 (NIFTY default) affected bb too, but nobody had cross-checked an optimize result against a Load of the same params, so "optimize silently runs on NIFTY" went unnoticed. The moment a user (a) picked a non-bb strategy → hit #1/#2 → 0 results; then (b) after that fix, cross-checked optimize-vs-Load on SBIN → caught #3.

**Fix:** (1) `api_backtest_optimize` now `_base()`-normalizes the id (same as the single path) and threads a custom user-script's `_module`/`_lang` into the grid so `_run_custom` still dispatches. (2) `optimizer.run_optimization_stream` preserves each value's type (`v.strip() if isinstance(v,str) else v`) instead of `str(v)`. (3) `_run_single_worker` sets `symbol` per run — **single symbol → uses `run_backtest`'s own summary (bit-identical to Load); multi symbol → runs each and combines trades via the SAME `backtest_engine._compute_stats` that Load's client-side `_runMultiSymbol` mirrors**, so optimize == Load for both. Verified local + VPS: single-SBIN optimize == single-SBIN backtest (pnl/win/pf/sharpe/trades all identical, `MATCH True`); multi SBIN+RELIANCE combines correctly.

**Permanent guard:** When there are two code paths that must produce the same answer (here: "optimize one param set" and "backtest one param set"), they must SHARE the terminal call, not re-derive its inputs. The optimizer re-built `run_backtest`'s inputs by hand (id, types, symbol) and got all three subtly wrong; the single path — which normalized via `_base()`, passed typed cfg, and set `symbol` — was correct. This is the backtest-side twin of Rule 6B ("duplicate mat karo, extend karo"): a "run one combo" worker should funnel through the exact same normalization the interactive single-run uses (ideally call a shared helper), never a parallel copy. And when two views claim to show the same thing, **cross-check one against the other with real numbers** — the 1.54-vs-0.28 mismatch was invisible until someone put them side by side.

**Fast-detect:** Optimize "0 results / 0 combos" for any non-bb strategy → suspect strat_type dispatch (#1) or a per-combo type crash (#2); add a temporary `print(res["error"])` in `_run_single_worker`. Optimize stats that don't match a Load of the same param set, or optimize numbers that look like a DIFFERENT symbol's (esp. NIFTY-shaped when you picked a stock) → suspect the `symbol` vs `symbols` split (#3). Byte-identical combos across a real sweep = the swept param is being ignored by that runner (often because the whole run is on the wrong symbol/strategy).

**Addendum #98b — the identical-combos followup (config-pollution × modal-dumps-everything):** After the symbol fix, the user optimized Range Chain again and STILL got all combos identical (665.60) — because they were sweeping `OVERBOUGHT/OVERSOLD/RSI_EXIT/RSI_PERIOD`, which **Range Chain doesn't read** (those are RSI-strategy params). Two compounding causes: (1) `nifty_config.json`'s `ARS_CHAIN_V1` entry was **polluted** with RSI keys — inert noise for a Range runner (most likely written by the Run modal's Save-Config, which renders `_RUN_PARAM_DEFAULTS` for ALL strategies and saves whatever's shown). (2) The backtest **Parameter Modal's config editor dumped every stored key verbatim** — so the junk keys appeared as sweepable params, and a runner that ignores them yields identical results for every value. Fix: `relevantCfg()` in `script3.html` filters a BUILTIN's config to only the keys that strategy actually reads (`STRAT_KEYS`, mirrors `backtest_chart.html`'s `STRAT_FIELDS`) + a universal set; user/custom scripts (base not in the map) still show ALL keys. The stored junk stays but is no longer surfaced or sweepable. **Lesson:** a "here are the params you can tune" UI must be driven by what the strategy actually CONSUMES, not by whatever happens to be sitting in its saved config — otherwise dead keys become tunable knobs that silently do nothing. Byte-identical optimize combos across a genuinely-varying param = that param isn't wired into the strategy (wrong strategy's key, or config pollution).

---

## TRAP #99 — Default SL/TP "rs" (₹ Amount) type didn't scale with lots in legacy/dropdown mode — only "aggressive" mode was lot-scaled, so the same ₹ SL moved half the price-distance on a 2-lot vs a 1-lot position 🟡 (Fixed, 2026-07-09)

**Symptom (found via the user's own manual notebook math, 2026-07-08):** For the per-position default SL/Target where Type = "₹ Amount" (`SL_TYPE:rs`/`TP_TYPE:rs`, used by BOTH the legacy Fixed-₹ card and the dropdown Type+Value card), the trigger price didn't behave consistently across lot counts. A ₹1000 SL on a 1-lot position moved the SL the "right" distance, but on a 2-lot position it moved only HALF that price-distance — because the code divided the ₹ value by total `qty` while `qty` doubled with lots.

**Root cause:** `_generic_px()` in `trader_dashboard.py`, the `typ == "rs"` branch, did `per_unit = val / p["qty"]` — treating `SL_VAL`/`TP_VAL` as a FLAT WHOLE-POSITION ₹ amount. Since `qty = lots × lot_size`, the per-unit price offset shrank as lots grew, so the price-distance was inversely tied to lot count. This was inconsistent with **aggressive mode** (`risk_gate.target_sl_level` / `advance_target_sl`), which correctly treats its ₹ as PER-LOT and scales by lot count (`lots = qty/lot_size`, `lot_size` from the scrip master, unknown = skip-never-guess). Two ₹-SL systems, two different meanings for "₹" — a Rule 6B-family divergence (same concept implemented twice, drifted).

**Fix:** the `"rs"` branch now resolves `lot_size = dhan_master.get_lot_size_by_sec_id(sec_id)` (same pattern + same fail-safe as the aggressive-mode block in `pos_monitor_loop` — unknown lot_size → `return None`, never guess) and does `per_unit = val / lot_size`, making `SL_VAL`/`TP_VAL` a PER-LOT ₹ amount whose price-distance is constant per lot regardless of how many lots. One fix covers both SL and TP (same function, `is_sl=True/False`). Docstrings updated in `risk_gate.py` (`default_sl_profile` + `default_instrument_sl_tags`), UI labels updated (legacy card → "Fixed ₹ per lot"/"Fixed Target ₹ / lot"/"Fixed SL ₹ / lot"; all four dropdown/edit-modal selectors → "Amount (₹ per lot)"). Did NOT touch `pct`/`pt`/`premium`/`index`/`trailing_pt` — none use qty/lots. **Regression-safe:** for a 1-lot position `lot_size == qty`, so behavior is byte-identical to before — only multi-lot positions change, and only toward correctness (matching aggressive mode).

**Lesson:** when two features express the same user-facing unit ("₹ SL"), they must agree on what that unit MEANS (per-lot vs whole-position). Here the divergence was silent because single-lot trades — the common case — hid it entirely (`lot_size == qty` makes both formulas identical). Any "amount" input tied to position size should pick per-lot or per-position ONCE and every code path + label must reflect that same choice.

**Fast-detect:** an ₹-based SL/TP trigger firing at the wrong price only on multi-lot positions (correct on 1 lot) → suspect a `val / qty` where it should be `val / lot_size` (or vice-versa). Compare the branch against the aggressive-mode / `_leg_capital` lot-scaling pattern, which is the canonical one.

---

## TRAP #100 — The premium-chart disk fallback silently ignored the daemon's own snapshots: it only read raw-epoch keys, but `auto_data_downloader.py` writes HH:MM keys — so expired-contract charts stayed blank even when the bars were sitting on disk 🟡 (Fixed, 2026-07-09)

**Symptom (user-reported):** "6 july ka chart missing hai, jab ki dhan to historical data hai, both for option and index." A completed trade's per-trade premium chart (📈) showed the INDEX pane fine but the OPTION pane empty for an old date (6 July, viewed 9 July).

**Root cause — two independent facts stacked:** (1) Dhan's `/v2/charts/intraday` genuinely does NOT retain intraday for an **expired index weekly** option — verified with a raw probe: sec_id 44654 (NIFTY 24400 CE, expired 07-08) → HTTP 200 but `{"open":[],...}` (0 bars), while an ACTIVE monthly stock option (TITAN, sec_id 150522) and the index (sec_id 13) both returned 375 bars for the same 6-July date. So the asymmetry the user saw (index works, option blank) is Dhan-side, not ours. (2) The on-disk fallback that's supposed to cover exactly this (`data/trade_ohlc/{sec_id}_{date}.json`) HAD the data — `auto_data_downloader.py` had captured `44645_2026-07-06.json` etc. same-day — but `_load_premium_ohlc_candles()` in `trader_dashboard.py` read **only numeric epoch keys** and explicitly skipped `HH:MM` keys as "ambiguous TZ", returning `None`. But the daemon writes `{"09:15":[o,h,l,c],...}` (HH:MM = IST market wall-clock), and the file is per-date — so the timestamp is NOT ambiguous. The 2026-07-07 chart write-through switched to raw-epoch keys but nobody reconciled the loader with the daemon's existing (and ongoing) HH:MM format → every daemon-captured expired-contract chart stayed blank despite the bars being right there on disk.

**Fix:** `_load_premium_ohlc_candles()` now parses BOTH key formats — epoch keys as before, and HH:MM keys via `calendar.timegm(strptime(date_str + " " + hhmm))` to rebuild the same IST-as-UTC epoch the chart uses (no double-shift, TRAP #29-safe, since the date comes from the filename and HH:MM is IST). Verified live: NIFTY 24300 CE / 24350 CE 6-July charts that were blank now render from `source: disk` with entry markers. (Contracts that were NEVER snapshotted pre-2026-07-07 — e.g. 44654, which the Dhan-orders-based downloader didn't catch since the algo trades on Kite — remain unrecoverable: Dhan dropped them and no disk copy exists. Going forward the 07-07 order_store-based capture + write-through covers Kite trades too.)

**Lesson:** when a reader and a writer share a file format, changing ONE side's format (epoch write-through) without teaching the reader the OTHER side's format (daemon's HH:MM) is a silent data-loss bug — the file exists, has data, and is skipped. A "skip the ambiguous case" guard is only correct if the case is genuinely ambiguous; here the date was known from the filename, so HH:MM was fully resolvable and should never have been dropped. Prefer handling both known formats over silently discarding one.

**Fast-detect:** a per-trade premium chart blank for an OLD/expired contract while the index pane renders → first probe Dhan raw for that exact historical sec_id (`_sec_id_from_order_store` gives it) for that date; 0 bars = Dhan dropped the expired contract. Then check `ls data/trade_ohlc/{sec_id}_{date}.json` — if the file EXISTS but the chart's still blank, it's a loader/format mismatch (`head -c 200` the file: HH:MM keys vs epoch keys), not missing data.

---

## TRAP #101 — CAPITAL_BLOCKED rows leaked into completed-trade netting → phantom ₹-lakh trades + false RMS profit-target (a 4th consumer of the same "blocked row must not be treated as a real position" gap) 🔴 (Fixed, 2026-07-09)

**Symptom (user screenshot, paper — no real money, but corrupts every P&L number + RMS):** Completed Trades for 2026-07-08 showed absurd rows — `LT-3960-PE SELL entry ₹3974.80 → exit ₹65.60, +₹6,83,185 net`; `NESTLEIND-1450-PE BUY entry ₹36.50 → exit ₹1448.00, +₹7,04,785 net` — each with a blank Exit Reason. One blocked row's own tag literally read *"Daily profit target ₹3,000 hit for 'range_v1' (today's P&L ₹7,07,610)"* — the phantom P&L had fired a false RMS profit-target.

**Root cause:** `order_store._net_rows()` split rows into `live_rows` (`status=="open"`) vs `closed_rows` (everything else) — and **`blocked` fell into `closed_rows`**, entering the netting pool. A CAPITAL_BLOCKED entry is not a real position (RMS rejected it pre-placement), and its recorded price was often an index-level placeholder (₹3974.8 = LT spot, not the PE premium — see the secondary bug below). Pass-2's FIFO nets opposite legs by `(mode, trad_sym)` alone, blind to strategy — so a blocked SELL@3974.8 (ARS_CHAIN_V1_PAPER) got paired against an unrelated real BUY@65.6 (rsi_v1_PAPER) of the same contract → a phantom "completed trade" with a ₹-lakh P&L. That phantom then flowed into every `_net_rows()` consumer: the dashboard total, and `risk_gate`'s realized-P&L / profit-target, which false-fired. This is the SAME "a blocked row is not a real position" gap already patched in THREE other consumers — `risk_gate._today_open()` (TRAP #86), the 3 strategy recovery fns (TRAP #86), and `broker_sync` (TRAP #92) — but the netting engine itself, the most central consumer, was never given the exclusion.

**Fix:** `_net_rows()` now pulls `status=="blocked"` rows out into their own `blocked_rows` list (alongside the existing `live_rows` split), excluded from BOTH Pass-1 and Pass-2, and surfaces them directly via `_as_open()` so the "Capital se Block hui Entries" panel still shows them — but they can NEVER be a completed-trade leg. Because the phantoms were netting artifacts (computed on read, never stored as DB rows), the fix makes them vanish with zero DB cleanup; the real blocked rows stay put. Verified live: 2026-07-08 completed rows with |P&L|>₹1L went 2→0, day total corrected to a sane −₹2,843, 22 blocked entries still shown in the panel.

**Secondary bug fixed same pass (Bug 2 — blocked price = spot):** `range_trader.py`'s block-record used `opt_prem = risk_gate._quick_option_ltp(...) or price` — when the premium LTP fetch fails (DH-904), `opt_prem` fell back to `price` (the underlying SPOT), so the blocked PE/CE recorded at the index level (₹3974.8). The Task-14 comment claimed it recorded premium, but the `or price` fallback silently defeated it whenever the fetch failed (which is often, in the block bursts that happen right when a profit-target trips and rate-limits pile up). Fixed: record `opt_prem_ltp or 0` (real premium, or 0 = "premium N/A"; order_store's ₹0 tripwire explicitly skips `blocked` status) — never spot. The gate-check keeps the conservative spot fallback (SELL capital is real-margin-based anyway). UI shows "premium N/A" for a 0-price blocked row. Historical spot-priced blocked rows left as-is (paper, now harmless since they can't net).

**Lesson:** when a row-status means "not a real position" (`blocked`, `open`-hedge, `externally_closed`…), EVERY consumer that pairs/aggregates positions must exclude it — and the netting engine is the consumer that feeds all the others, so it's the highest-value place to get it right. This gap recurred across 4 consumers because each was patched reactively where a symptom showed; the netting core was the one nobody had checked. When you find a "blocked/dead row treated as real" bug, grep for ALL callers of `_net_rows`/`trades_for`/the raw status split and fix the shared core, not just the reporting site.

**Fast-detect:** a completed trade with an entry OR exit price near an underlying's SPOT (₹3974 for a 3960 option, ₹1448 for a 1450 option) and a ₹-lakh P&L → a blocked/placeholder leg got netted. Check the raw legs (`SELECT ... FROM orders WHERE trad_sym=? AND substr(ts,1,10)=?`) for a `status=blocked` row on that contract; if one exists at a spot-level price, it leaked into netting.

---

## TRAP #102 — webhook position ka FIRED SL "webhook already claimed/closed" ke naam pe suppress ho gaya → position ~1hr unprotected; + dual SL-system consolidation 🔴🔴 (Fixed, 2026-07-09)

**Symptom:** User ne live webhook position (NIFTY-24100-PE SELL @157.85) ka premium chart dekha — "Aggressive SL 156.31" line pe premium wapas aaya par exit nahi hua; aakhir manually close karni padi @161.2. Logs: `[DEFAULT-TSL] 🎯 FIRED (SL) ... P&L ₹-2054 vs SL ₹-2000 — squaring off` **turant baad** `[DEFAULT_TSL_SL:-2000] webhook already claimed/closed this leg — skipping`. SL fire hua par squareoff SKIP → position 13:00–14:02 khuli, zero enforcement.

**Root cause (2 compounding):**
1. **Guard mis-interpretation** — `_pre_exit_guard` (trader_dashboard.py) webhook-source position pe `webhook_executor.release_position()` call karta hai. Ye **True** deta hai jab webhook apni `_wh_state` me position track kar raha ho (release karke DEFAULT-TSL ko close karne deta hai), **False** jab track NAHI kar raha. Guard False ko *"webhook ne already close kar diya → skip + `_closed_ids` me blacklist"* maan leta tha — par False ka asli matlab *"webhook ko is position ka pata hi nahi"*. DEFAULT-TSL (jo ise close karne wala akela tha) haath khade kar deta → koi close nahi karta.
2. **Cross-process recovery gap** — `_recover_wh_state()` sirf **module import (service boot)** pe chalta tha. Webhook ENTRY `algo-dashboard` process me hoti hai (Flask `/api/webhook/tv`), MONITORING `algo-monitor` (alag process, apna `_wh_state`) me. algo-monitor 12:24 boot → entry 12:55 → algo-monitor ke `_wh_state` me kabhi aayi hi nahi → `release_position` hamesha False → guard skip.

**Fix (root, dono):**
- `_pre_exit_guard`: `release_position()==False` pe **skip/blacklist mat karo** — seedha neeche ke authoritative `is_flat_fresh()` broker flat-check pe jao (flat → skip, khuli → close). release_position call rehti hai sirf apne back-off side-effect (True case) ke liye.
- `webhook_executor._recover_wh_state()`: ab `webhook_monitor_loop` se **periodic bhi** chalta hai (~30s, start pe turant), **non-clobbering** (`with _lock`, already-tracked key skip) — boot ke baad khuli webhook entries adopt ho jaati hain.

**Same session — dual SL-system consolidation (user decision):** webhook ka apna `sl_points`/`target_points`/`trail_mode`/`trail_value` (monitor_tick ka premium/index trail) AUR RMS Default-TSL (AGGR_TSL) — dono ek hi position pe lag rahe the (isi overlap se chart-line ≠ actual exit). Ab webhook config me ek **SL Type selector** (aggressive/legacy/dropdown); webhook ka apna trail HATA diya (`monitor_tick` sirf global-cap + 3:15 squareoff karta hai, TV-EXIT `handle_signal` se). Entry pe `risk_gate.default_instrument_sl_tags(strat, sym, mode_override=cfg["sl_type"])` chosen-type ke RMS tags stamp karta hai; `default_target_sl_config()` me naya `feature_on` (= `default_tsl_enabled`, global mode se independent) aur pos_monitor ka aggressive-firing gate `enabled`→`feature_on` — taaki per-position AGGR_TSL (incl. per-webhook aggressive) global default se independent chale. Current config me `feature_on==enabled==True` → deploy no-op, safe.

**Lesson:** (a) ek boolean jo do bilkul alag cheezein represent karta hai (`False` = "closed" YA "unknown") — kabhi safety-skip ka basis mat banao; hamesha authoritative fresh check (broker flat) pe defer karo. (b) Cross-process in-memory state (`_wh_state`) jo ek process me banti hai par doosre me consume hoti hai — usko boot-only recover mat karo, periodic sync karo. (c) Do systems ek hi cheez (SL) manage kar rahe hon to ek source-of-truth banao — chart pe jo dikhe wahi enforce ho.

**Fast-detect:** `journalctl -u algo-monitor | grep 'already claimed/closed this leg'` — agar ye line kisi genuinely-open position pe aa rahi hai (broker pe position abhi bhi hai) → SL suppress ho raha hai. Aur `data/tsl_state.json` empty hona + webhook position ka `source=webhook` tag + `release_position` False = cross-process gap.

**Follow-up (2026-07-09) — smart-split candle-close on the aggressive profit-lock SL:** user asked whether the trailing SL only fires on a candle CLOSE above the line (whipsaw guard). It did NOT — `advance_target_sl`'s firing (`mtm <= sl_level`) is on the live LTP tick (the 2-reading confirm only spike-guards the PEAK/level, not the firing), so a wick could stop out a still-good trade. Fix (pos_monitor aggressive block, user's "smart split" choice): in the PROFIT-lock zone (`sl_level >= 0` — SL ratcheted into locked profit) require the last CLOSED **1-min** candle (`_last_closed_candle_close`) to confirm the breach before firing; the LOSS zone (`sl_level < 0`) still fires on the tick for immediate capital protection. On suppress, reset `state["fired"]=False` so it re-evaluates next cycle (advance_target_sl sets fired=True on a would-be SL). Candle unavailable → fail-safe fire on tick. TARGET unaffected (locks profit immediately). ~1 extra 30s-cached Dhan candle call only at a profit-zone stop moment.

---

## TRAP #103 — Optimizer ranking by OOS-Sharpe IS overfitting to the OOS window (silent "fake robustness")

**Symptom:** A strategy's optimizer returned candidates with OOS Sharpe > 1.0 (1.06, 1.02) but **train Sharpe only 0.4-0.5** — OOS *higher* than in-sample. Looked like a great robust edge; wasn't.

**Root pattern:** The optimizer (`optimize()`/`intraday_optimize.optimize()`) ranked candidates by **OOS Sharpe**. Over 250-400 random-search trials, "pick the config with the best OOS number" turns the out-of-sample split into a **second training set** — you cherry-pick whatever param combo happened to fit the recent (OOS) regime. Train<<OOS is the fingerprint: the config isn't robust in-sample, it just got lucky on the holdout. Ranking by OOS is NOT out-of-sample validation, it's OOS-fitting.

**Permanent guard:** Select the winner by a **robustness metric across BOTH halves**, never by OOS alone. Concretely: filter to configs with `train_sharpe > 0.7 AND oos_sharpe > 0.7`, then rank by `min(train, oos)`. The genuine winner (tod_orb) had **train 0.95 ≈ OOS 0.96** — near-identical both halves, and 20 different configs cleared the both-halves gate (not one lucky point). That balance is what a real non-overfit edge looks like; a curve-fit collapses on one side.

**Fast-detect:** In any optimize output, if `OOS_sharpe > train_sharpe` by a wide margin, distrust it — it's OOS-overfit, not robust. Count how many configs clear a both-halves threshold; one or two = luck, many = real.

---

## TRAP #104 — Backtest runs WITHOUT the RMS overlay; live runs WITH it → results diverge. (Two-stage validation, per-strategy override.)

**Symptom:** A newly-researched strategy's clean backtest showed Sharpe 0.93 / +17.7%. But the account's global RMS caps (daily loss ₹5500, **daily profit-target ₹3000**) would square it off and block re-entry mid-day. Re-running the backtest WITH those caps applied: Sharpe crashed to **0.52**. The strategy would perform far worse live than its backtest promised — and the low profit-target was silently truncating winning days.

**Root pattern:** Research/optimization backtests are (correctly) run unconstrained — you must NOT bake RMS caps into the strategy search or the master-prompt, or you cripple the hunt and never find the edge. But then the deployed strategy runs under the live RMS overlay (`risk_gate` daily-loss/profit-target/premium-cap + `pos_monitor` squareoff), which the backtest never modeled. Gap = live ≠ backtest.

**Permanent guard — TWO-STAGE backtest:**
1. **SEARCH (unconstrained):** find the raw edge. RMS never enters the master-prompt.
2. **RMS-OVERLAY VALIDATION (before deploy):** re-run the winner under the real caps (`intraday_engine.backtest(..., rms_caps={loss_cap, profit_target})`). If the edge survives → deploy; if RMS destroys it → **per-strategy override** the conflicting rule (don't loosen the GLOBAL, which protects every other strategy); if it can't be reconciled → reject.
- The strategy's own exits (ATR stop/target) must stay **authoritative**; RMS caps are the outer backstop (Critical Rule 6 shape). Set per-strategy caps ABOVE the strategy's natural per-day range so RMS never fires first in normal operation.

**Per-strategy profit-target subtlety:** `effective_daily_profit_target(strat)` already resolves `per_strategy[strat].profit_target_rs → global → off` — but the Per-Strategy Override UI table had **no profit-target column** (only Max Loss), so it looked un-overridable. It was settable from the backend all along; also added a "Max Profit ₹" column to the UI. AND: `_strategy_day_pnl` (used by `daily_profit_target_hit`) does **NOT** filter paper trades (unlike `_today_realized_pnl`), so the global profit-target truncates even a PAPER strategy — the per-strategy override matters even before going live.

**Fast-detect:** Before deploying any researched strategy, ask "what does the live RMS do to this that the backtest didn't?" Run the rms_caps overlay. If Sharpe drops materially, a global cap is truncating the edge → per-strategy override.

---

## TRAP #105 — A great backtest Sharpe can be pure leverage + beta, not a real edge. Gate on a significance test, not the headline number.

**Symptom:** A positional NIFTY trend strategy showed Sharpe 1.1, +305% over 4.5yr, beating buy&hold ~3x. Looked shippable. It wasn't.

**Root pattern:** Two hidden inflators. (1) **Hidden leverage** — 3%-risk sizing with tight ATR stops produced ~5.7x average notional (max 11x); at a true **1x cap the same strategy made only +22%, below buy&hold** (leverage scales returns, NOT Sharpe). (2) **Beta** — a rotation permutation test (shuffle the position series against forward returns ×1000) showed the entry timing was **not** distinguishable from random given the trend (p=0.13); the money was "be long in a bull market," not a timing edge.

**Permanent guard:** For any researched strategy, gate on **statistical significance** (permutation/rotation test on the entry edge, controlling for beta), not raw Sharpe. Require p<0.05. Enforce a real **1x no-leverage** position cap (`leverage_cap=1.0`) so the number reflects the edge, not the bet size. A significant edge (like tod_orb, p=0.000) survives the rotation null; a beta-rider (positional trend) does not. **Spot-backtest → option-live gap:** signals backtested on NIFTY spot execute live via ATM options (delta/decay/premium-cap) — the rupee P&L scale and character differ, so paper-trade first and measure the execution gap before trusting live numbers.

**Fast-detect:** If a backtest looks amazing, check: (a) what's the average notional/equity (leverage)? (b) does it still beat buy&hold at 1x? (c) does a permutation test on the entries clear p<0.05? If any fails, it's not a proven edge.

---

## TRAP #106 — Black-Scholes-from-realized-vol systematically HIDES the option seller's edge (VRP). Short-vol looks dead on BS, prints on real premium.

**Symptom:** A short straddle / iron-fly priced with `bs_option.py` (BS premium from 20-day realized vol) showed **Sharpe −2.24, −95..−100%** — "no edge, Track-B." The SAME strategy on REAL Dhan expired-option premium showed **Sharpe +8.9, +61%** (iron-fly ±8, real charges + slippage).

**Root pattern:** The option SELLER's edge is the **variance risk premium** — real implied vol is structurally *higher* than realized vol, so the market pays the seller more than a realized-vol model says the option is "worth." A BS-from-realized model prices that premium AWAY by construction, so any net-short-premium structure looks like it has no edge (or negative, after charges). BS-from-realized is fine for BUYERS/directional (delta dominates) but **cannot validate a seller** — it can't see the very thing the seller is paid for.

**Permanent guard:** Never judge a **net-short-premium** structure (short straddle/strangle, iron condor/fly, credit spreads, ratio-net-credit) on BS-modeled-from-realized premium — the result is meaningless-to-pessimistic. Use REAL option premium+IV (Dhan `rollingoption`, see [[dhan_rollingoption_data]] / ADR-004) OR a BS priced off REAL implied vol (not realized). Net-LONG-premium / directional structures are still OK on BS-from-realized. Corollary: a short-vol strategy that's positive on BS-from-realized is *doubly* interesting (edge survives even the pessimistic model).

**Fast-detect:** Backtest result depends on whether you're net-selling or net-buying premium AND on the σ source. If net-short + σ=realized → distrust; re-price on real IV before believing (either direction of surprise).

---

## TRAP #107 — A still-downloading data series silently truncates an inner-join → a real-looking but BOGUS backtest.

**Symptom:** Mid-backfill, `optlake_load.ironfly_frame(wing=6)` gave iron-fly **net 0.1%, Sharpe 3.36** — internally inconsistent (near-zero net with a positive Sharpe). The complete wing=5 gave a coherent Sharpe 5 / +24%.

**Root pattern:** The ±6 wing CSV had only its most-RECENT chunks downloaded (recent-first backfill order). `atm_frame.merge(wing, how="inner")` silently collapsed the whole 5-year frame down to the ~few-months overlap where the wing existed → the backtest ran on a tiny recent slice, producing numbers that look plausible until you check day-count. No error, no warning — the join just quietly shrank the sample.

**Permanent guard:** Any join that pulls in an incrementally-populated source must **assert coverage** before use. `ironfly_frame` now checks each wing spans ≥90% of the ATM frame's day-count and returns `None` (with a loud log) otherwise, so a partial series can never masquerade as a full backtest. General rule: when reading from a data-lake that's still filling, verify span/row-count against the reference series, not just "file exists."

**Fast-detect:** A backtest's `days`/`trades` far below the expected full-window count, or metrics that are internally inconsistent (tiny net + decent Sharpe), = truncated sample. Print `frame.day.nunique()` and compare to the reference before trusting any number.

---

## TRAP #108 — Live entry must mirror the backtest's EXACT boundary condition. An off-by-one on the opening-range cutoff (`<` vs `<=`) silently drops/moves signals.

**Symptom:** `05_backspread_trader.compute_breakout` (and `02`/`03`) built the opening range with `tday["time"] < or_end` and gated entries with `bar_time < or_end`; the backtest (`intraday_engine` orb/tod_orb) uses `tt <= or_end`. Signal parity vs the backtest was **32/40** — 8 real signal bars silently missed.

**Root pattern:** The backtest's OR cutoff INCLUDES the bar labelled `or_end` in the opening range (entries strictly AFTER it); the live code EXCLUDED that bar, shifting the OR high/low and the "after-OR" boundary by one bar. A one-character boundary mismatch → different OR levels → different (missed/extra) breakouts. Live and backtest are supposed to be the same strategy; a boundary off-by-one quietly makes them different.

**Permanent guard:** When porting a backtest signal to a live trader, **parity-test it** — replay the backtest's `design_signals` vs the live `compute_*` on the same resampled bars and require ~40/40 match on both signal bars AND non-signal bars (the method: slice history so "last closed bar" == each candidate bar, call the live fn, compare). Match the exact comparison operators (`<` vs `<=`) at every boundary (OR cutoff, entry window, expiry). Fixing `<`→`<=` took 05 from 32/40 to 40/40; the same fix applied to 02/03.

**Fast-detect:** New live trader? Run the parity harness before deploy. <100% parity on signal bars = a boundary/operator mismatch somewhere; diff the two implementations' comparisons.

---

## TRAP #100 (tier-qualified addendum, 2026-07-11)

The original TRAP #100 ("Dhan drops expired NIFTY-weekly intraday → 0 bars, must BS-model / collect forward") is **FREE-tier behaviour**. With Dhan's **paid "Expired Options Data" add-on**, `POST /v2/charts/rollingoption` serves REAL 5-year expired-option premium + IV + OI for rolling ATM±N CE/PE (weekly+monthly). So the "no historical option data, must simulate" premise is account-tier-dependent. On the paid tier: download real data (see [[dhan_rollingoption_data]] / ADR-004), don't BS-model sellers (TRAP #106). The standard `/charts/intraday` STILL returns 0 for an expired contract's own sec_id even on the paid tier — you must use `rollingoption` (relative-strike, expiryCode≥1), not the expired sec_id.

---

## TRAP #109 — Rolling-ATM data series marks the position at "whatever ATM is NOW", not the CONTRACT HELD — hides intrinsic losses and fabricated a Sharpe-8.9 strategy.

**Symptom:** Short-Vol Iron-Fly (#06) backtested at Sharpe 8.9 / +61% / worst-day −0.25% on REAL rollingoption data; crash-day 2024-06-04 even showed +₹11,550 "profit". It was paper-DEPLOYED on that basis. The corrected engine shows **−54% / Sharpe −3.5** — the entire edge was a marking artifact.

**Root pattern:** Dhan `rollingoption` series are RELATIVE-strike (rolling ATM±N): each column re-references to the CURRENT ATM every bar. `real_struct.py` valued open positions by re-reading the same column at exit — i.e. "what does the CURRENT ATM option cost", not "what does MY entry-strike contract cost". When spot trends away, the held short straddle bleeds INTRINSIC value that the always-ATM column never shows (a rolling-ATM straddle stays ~pure time-value). Sellers' losses (and buyers' gains) on trend days vanished from the backtest. The bigger the move, the bigger the hidden loss — so the model looked BEST exactly on the days the real position hurt most (crash day "+profit" = IV-crush on a strike we wouldn't have held).

**Permanent guard:** With rolling/relative-strike data, ALWAYS track the HELD strike: record K at entry; each bar compute `off = (K − ATM_now)/step` and read that OFFSET column (the ±10 grid covers ±500 pts; beyond → intrinsic-floor mark). `real_struct2.py` is the reference held-strike engine — use it for ALL structure backtests on the lake; `real_struct.py` retained only as the cautionary tale. Fast sanity: re-price one known trend day (e.g. 2024-06-04) by hand — a short straddle "profiting" through a 1500-pt range day is impossible.

**Corrected picture (held-strike, real data, charges+slip):** short straddle −12%, iron-fly ±8 −54%, long straddle −27%, gamma-scalp −50% (0DTE −35%, Sh −9.7), calendar (sell-weekly/buy-monthly) +18% but Sharpe 0.12 with a −₹48.5k single-day tail → **intraday vol-trading in BOTH directions dies to costs on honest data**. #06 deactivated same session; #01–#05 UNAFFECTED (BS engine prices the held contract by construction).

**Fast-detect:** Any lake-based structure backtest whose worst-day loss looks *smaller* than a plain reading of that day's spot range implies (|move|×qty−credit), or a short-gamma strategy "profiting" on a crash day = marking bug. Cross-check one big-move day manually before trusting.


---

## TRAP #110 — Windows mkdir-lockfile: release-time `os.remove` can hit a transient PermissionError (another process reading the pid file), a silently-swallowed failure LEAKS the lock — and the owner then self-deadlocks on its own stale lock.

**Symptom:** hunt_guard's cross-process `flock()` (atomic `os.mkdir` + owner-pid file) passed single-process tests, but the 2-process hammer test (2 × 50 locked increments) produced `TimeoutError: lock held by pid X for 120s` — where pid X was the WAITING process itself. Counter stopped at 25/100.

**Root pattern:** Release did `os.remove(pidf); os.rmdir(path)` inside a bare `except OSError: pass`. On Windows, if the OTHER process is holding the pid file open (reading the owner) at that exact moment, `os.remove` raises PermissionError (sharing violation) → swallowed → lock directory left behind with the releasing process's pid still in it. That process's NEXT acquire sees "lock held by pid <me>, and I'm alive" → waits on itself forever. The classic shape: a cleanup path that "can't fail" (so its failure is silently ignored) actually CAN fail transiently on Windows file-sharing semantics, and the ignored failure converts into a deadlock one call later.

**Permanent guard:** (1) Lock release must RETRY, never fire-and-forget — `_clear_lock()` loops (40 × 50ms) until the pid file + dir are gone; FileNotFoundError = already clear = success. (2) The acquire loop treats `owner == os.getpid()` as a stale lock and breaks it (a live process can never legitimately be waiting on a lock it owns — that state only means "I leaked it"). (3) Concurrency primitives get a REAL multi-process hammer test before use (2 procs × 50 locked read-modify-writes must equal exactly 100) — single-process tests cannot surface sharing-violation races. 5/5 tests in scratchpad test_guard.py pattern.

**Fast-detect:** A `flock`-style TimeoutError whose reported owner pid equals the complainer's own pid = leaked-self lock. Any Windows file/dir cleanup inside `except OSError: pass` is a suspect — sharing violations are routine, not exceptional.

---

## TRAP #111 — Backtest cost knobs were GUESSED, never measured — a fabricated slippage number silently decides pass/fail at the Sharpe gate

**Symptom:** The BS option backtests decided "shippable" vs "dies-to-costs" using cost assumptions nobody had validated. `bs_option.reprice*` (ALL directional ATM strategies — pivot/chain-zone/ORB/straddle) applied real charges but **ZERO spread slippage**; `real_struct2` (vol family) applied a flat **0.5%/leg**. On these, the vol family read −54% (iron-fly) / −12% (short-straddle) → "dies to costs, family exhausted"; the directional strats read a slightly-too-good Sharpe (no spread at all).

**Root pattern:** A guessed/fabricated cost knob is still a HARD INPUT to the gate — it multiplies across every trade and can push Sharpe across the 1.0 line either direction. Two engines in the same repo even disagreed on it (0% vs 0.5%) and nobody flagged it, because a plausible-looking constant "feels" safe. When brother's DOM (real 20-level order-book) was finally MEASURED (`scratch/nifty_trend/dom_spread.py`, ~3.9M snapshots/leg, 21 days), the real NIFTY ATM one-way half-spread came out ≈ **0.11-0.16%/leg** — meaning the directional pass was optimistic (0 vs 0.11%) and the vol pass was ~4x pessimistic (0.5% vs 0.11%). At real spread the "dead" short-straddle moved −12% → −1.0% (breakeven): the flat 0.5% had been silently over-killing it.

**Kahan kaata:** the "vol-selling family dies to costs" conclusion was partly a 0.5%-knob artifact (spread wasn't the killer — wing-cost-vs-tail was); the directional gate numbers were mildly inflated (no spread modeled at all). Borderline verdicts (#08 pivot 0.967, chain-zone positional 0.97) were being read off a cost model that didn't include the one cost that mattered.

**Permanent guard:** cost inputs are now MEASURED, not guessed — a single shared `bs_option.slip_cost_leg()` (DOM-calibrated per-premium half-spread, `SLIP_ENABLED` default True, `SLIP_MULT` stress knob, 0.15% safe fallback) is baked into `bs_option.reprice*` AND `option_structures.backtest_structure` AND `real_struct2`, so every FUTURE hunt uses honest spread by default (ADR-005). **Rule: any fabricated/proxy input to a pass/fail gate (slippage, IV proxy, fill assumption, VRP mult) must be either measured OR explicitly flagged in the result as "GUESS — verdict is cost-sensitive", never silently trusted.** `dom_recost.py` re-costs any recorded run to expose cost-sensitivity without re-running the pipeline.

**Fast-detect:** a strategy whose verdict FLIPS when you change one hardcoded cost constant is cost-sensitive — its pass/fail is a statement about the guess, not the edge. Grep any gate-feeding backtest for hardcoded `slip`/`slippage`/`0.005`/`* 0.00` and ask "was this measured?"


## TRAP #112 — Live traded on shared_candle_cache bars that differ from Dhan's FINAL candles → a phantom ORB entry the backtest never took (a fresh cause in the TRAP #108 "live must mirror backtest" family) 🔴 (Fixed, 2026-07-13)

**Symptom:** the daily signal-replay (the TRAP #108 detector) flagged 5 ORB-family PAPER strategies (dvert/orbst/backspread/chainzone/orb) with "replay drift — 1 MISS [+ 1 EXTRA]". dvert's trace: live ENTERED 10:30 (BULL-CALL), but an EOD replay of the SAME `compute_breakout` on final Dhan candles produces NO signal at 10:30 — it fires 10:15 / 11:00 / 11:45. The 10:30 phantom consumed the 2-trade/day budget, so the real 11:00 signal came back MISS. A regenerated report still showed the drift because the phantom was already in that day's LIVE LOG (a fix can't un-happen a logged entry).

**Root cause:** live `fetch_nifty` serves `shared_candle_cache` bars FIRST (intraday-built, `max_age=20s`), which are NOT identical to Dhan's final `/charts/intraday` OHLC. Measured at EOD: cache returned **125 rows vs a fresh Dhan pull's 100** — genuinely different candle sets. `compute_breakout` is a MARGINAL cross (`close > or_high + k·ATR` and prev ≤ band); a tiny difference in the last-closed bar's close (a pre-revision cache value) flips whether the cross fires. Same signal function, different input candles → different entries. The warmup-window theory (live `days=5` vs replay `days=6`) was RULED OUT by a probe: both windows on fresh Dhan give byte-identical signals — only the cache-vs-final SOURCE differs.

**Kahan kaata:** live paper entries diverged from the validated backtest — phantom/mistimed entries the backtest never took, which ALSO inflated trade counts (the same report's separate "overtrading?" flag was partly these phantoms, not a real edge). This would have carried straight into LIVE money had the strategies been flipped without the replay catching it. Same FAMILY as TRAP #108 (live must mirror the backtest's exact entry condition) but a NEW cause — not a boundary off-by-one, the candle DATA SOURCE itself.

**Permanent guard:** every ORB-family live trader now RE-CONFIRMS its own signal on a fresh cache-bypass Dhan pull before entering. `fetch_*` gained a `use_cache` flag; on any signal the trader re-runs `compute_signal`/`compute_breakout` on `fetch(..., use_cache=False)` and enters ONLY if the direction still holds. **Fail-OPEN** if fresh data is unavailable (an infra outage must never block all trading — it skips only when fresh data actively DISAGREES). Offline replay path untouched; handles both return conventions (`direction` and `signal` keys). Commit `0fece2e`.

**Fast-detect:** if the TRAP #108 replay shows a live EXTRA with NO offline counterpart on ANY warmup window, the divergence is a candle-SOURCE mismatch, not the signal logic — check whether live reads a cache / LTP-built bar set vs the backtest's official-candle source. Any strategy that ACTS on a bar before its data source has FINALIZED it can phantom-fire on a marginal condition. Probe = fetch the strategy's own `fetch_*` with and without cache and diff the row counts/last-bar close.

---

## TRAP #113 — Reconcile's signature+count matching double-counted every MULTI-FILL order as "manual" (webhook 130-qty → broker fills 2×65 → 4 phantom manual rows + ghost-sync cascade) 🔴 (Fixed, 2026-07-14)

**Kahan kaata:** user ne dashboard pe apne webhook trade (NIFTY-Jul2026-24050-CE, 130 qty = 2 lots) ke NEECHE do "manual live" tagged 65-qty SELL rows dekhe — jabki usne Zerodha pe koi manual order daala hi nahi tha. Same din doosre webhook trade (24150-PE) pe bhi wahi hua, aur wahan ghost-sync cascade bhi shuru ho chuka tha (ek phantom manual SELL `externally_closed` mark ho gaya + uska synthetic exit row ban gaya — TRAP #60 wali feedback-loop shape). Day-total P&L me phantom −₹214 ghusa.

**Root cause:** `broker_sync.reconcile_manual_trades()` (TRAP #67 ka signature+count fix) maan ke chal raha tha ki **1 order = 1 broker fill**. Kite ne 130-qty order ko 2×65 fills me bhara (exit to alag-alag price pe bhi: 64.05 + 64.15, jinka avg 64.10 app ke row me tha) — signature `(sym, strike, CE/PE, side, qty, price)` me qty=65 vs app-row qty=130 KABHI match nahi ho sakta → har fill "app ne place nahi kiya" dikhta → `source=manual` insert. Ye TRAP #67 ke fix ka hi blind spot tha: order-id matching ISLIYE hataya gaya tha ki purani rows me id missing thi — lekin id PRESENT hone pe use na karna overcorrection tha.

**Permanent guard:** `reconcile_manual_trades()` ab signature-matching se PEHLE order-id ownership check karta hai — fill ka `order_id` kisi existing row ke `broker_order_id` se match ho to skip, unconditionally (broker order-id unique per order hota hai; app ne wo order place kiya to uske SAARE fills app ke hain, chahe qty/price kuch bhi ho). Signature+count fallback id-less legacy rows ke liye waisa hi hai. Commit `b91a49b`. Cleanup: 7 phantom rows (4× 24050-CE + 3× 24150-PE cascade) surgical delete, backup `data/backups/trades_pre_dedup_2026-07-14.db` (sqlite `.backup`, WAL-safe).

**Fast-detect:** Completed Trades me koi "manual" row jiska qty app ke kisi same-symbol same-price row ka EXACT divisor ho (65 vs 130) = multi-fill duplicate, real manual trade nahi. DB confirm: `select broker_order_id from orders where correlation_id like 'MANUAL_TID_%'` — agar wahi order_id kisi webhook/strategy row me bhi hai, duplicate pakka. Broker jitna bada order, utna zyada partial-fill chance — 2+ lot ke har trade pe ye risk tha.

---

## TRAP #114 — Rolling option series (WEEK flag) ROLLS to the next contract after expiry — any hold whose exit crosses the expiry date prices TWO DIFFERENT contracts, and a wide search engine WILL find and exploit that seam.

**Symptom:** overnight GP mining run (511 waves, 3.43M rules, 2026-07-14) converged its ENTIRE leaderboard onto one rule family: "buy ATM straddle after 14:25 when straddle_norm ≤ 0.35% of spot, hold to next day's close" — evolution Sharpe 7.5, validation Sharpe 7.1. Checked before celebrating: **94.4% of its signal bars were expiry-day bars.** The rule was buying the DYING weekly straddle for pennies on expiry afternoon; next trading day the WEEK rolling series shows the NEXT week's contract at full premium (~0.9% of spot). The "profit" was the roll gap, not a trade. On the roll-guarded table the same rule has ~0 trades / ₹0 edge.

**Root pattern:** TRAP #109's cousin. #109 = rolling-ATM re-marks the STRIKE under you; #114 = the rolling series re-marks the CONTRACT (expiry) under you. Both are fine as per-bar STATE features, both are poison across a boundary: #109 across strike moves, #114 across the expiry date. Any P&L computed from a rolling series where entry and exit straddle the expiry boundary compares two different instruments. And unlike a human designer, an optimizer/GP searches millions of rules — if a data seam exists that prints fake money, the search WILL converge on it and it will look like your best-ever strategy (too-good = first suspect the data, not the genius).

**Permanent guard:** `ml_gp_precompute.py` invalidates any hold whose exit trading day > the entry day's weekly expiry date (verified `expiry_calendar` schedule, Thu→Tue switch included). Rule for ALL future lake-based backtests: **entry and exit must be provably the SAME contract** — same strike via offset re-mapping (#109) AND exit ≤ entry's expiry date (#114); crossing the roll requires an explicit roll trade (close old contract at its own price, open new at its own price, both legs charged).

**Fast-detect:** any lake-based winner whose signals cluster on expiry day (check dte distribution of signal bars FIRST — one groupby), or whose entry premium is a tiny fraction of the exit premium's typical level. A validation-window Sharpe that ~equals the (overfit-prone) evolution Sharpe on a mined rule is also suspicious — real edges degrade OOS; data seams don't degrade because they're deterministic.

---

## TRAP #115 — A helper 3 live traders call every loop never existed; `AttributeError` swallowed by `except: pass` → strategy silently ran on its slow fallback path forever (built ≠ wired, PRE-MORTEM shape #2).

**Symptom (2026-07-15):** `vrp_condor_v1`'s log flooded with `[VRP] no spot` WARNING every ~20-30s all session while NIFTY was clearly trading. Strategy never entered (fetch_spot returned None before the entry/position-management block, so a `continue` skipped everything).

**Root pattern:** `vrp_straddle_trader.py` / `vrp_condor_trader.py` / `06_shortvol_trader.py` all start `fetch_spot()` with `v = shared_ltp_cache.get_index("NIFTY")` — but **`get_index` was never defined in `shared_ltp_cache.py`.** Every call raised `AttributeError`, caught by the function's own `except Exception: pass`, and fell through to the next tier: `dhan_rate_limiter.acquire("ltp")`, which returns None when other strategies are contending → `no spot`. So the "cache-first, zero-extra-Dhan-calls" design intent was 100% dead for these traders — they hit the rate-limiter on every single spot read, both spamming the log AND adding load that made the poller's own writes lag (25-30s stale, self-reinforcing). The poller (`ltp_poller.py`) writes NIFTY spot under sec_id key `"13"`; the traders were asking for `"NIFTY"` via a function that didn't exist. Classic build≠wire: the newer VRP/shortvol traders were written assuming a helper the cache never got.

**Kahan-kahan kaata:** all 3 VRP/short-vol live traders, since the day each was written (2026-07-11 onward). Invisible because `except: pass` hid the AttributeError and the fallback "worked" often enough (whenever the rate-limiter wasn't busy) that it looked like intermittent rate-limiting, not a permanently-dead code path.

**Permanent guard:** added `shared_ltp_cache.get_index(symbol, max_age=60.0)` — maps `NIFTY→"13"` / `BANKNIFTY→"25"` (the exact keys `ltp_poller._IDX_ALWAYS` warms every cycle) and reads the poller cache. `max_age` deliberately loose (60s) because under contention the poller's own writes lag 20-30s and these positional strategies only need a roughly-current index level, not a live tick. Returns None only if the symbol is unknown or the poller has genuinely gone silent (>60s) → caller's direct-call fallback still covers that.

**Fast-detect:** any `except: pass` / `except Exception: pass` wrapping a call to a module function is a place where a *renamed or never-created* function fails silently. When a strategy's own diagnostic log ("no spot", "no data", "no depth") repeats at a fixed interval matching a `time.sleep()` in its fallback branch, the cache-first branch above it is almost certainly never returning — grep that the function it calls actually EXISTS in the target module (`grep "def <name>"`), don't assume.

---

## TRAP #116 — `_base()` took only `split('_')[0]`, so a two-token base (`vrp_condor`) was permanently shadowed by its one-token prefix (`vrp`) → the "VRP Overnight Condor" config ran the STRADDLE trader.

**Symptom (2026-07-15):** dashboard showed `02.03 · VRP Overnight Condor (vrp_condor_v1)` but its log tagged `[VRP]` (straddle) not `[VRPC]` (condor); `ps` showed `vrp_straddle_trader.py --id vrp_condor_v1` running. The config had condor-only fields (`body_off`, `wing_off`) that the straddle ignores — so the 4-leg iron condor (the validated deployable lead) never actually traded; the paper data under that id was straddle behaviour.

**Root pattern:** `_base(strategy)` did `first = strategy.split('_')[0]` → for `vrp_condor_v1` that's `"vrp"` → `STRATEGIES["vrp"]` = `vrp_straddle_trader.py`. A config id whose base is TWO tokens (`vrp_condor`) can never resolve, because the resolver only ever looks at the first token. `_base` is load-bearing everywhere (`get_pid`, `get_mode`, `/api/start` script selection, auto-scheduler start-all) — so the wrong script was launched at every 9:10 auto-start, silently.

**Kahan-kahan kaata:** only `vrp_condor` is affected today (the only two-token base key). But the shape recurs for ANY future multi-word base (`x_y`) added alongside a shorter `x`.

**Permanent guard:** `_base` now prefers a two-token base (`parts[0]_parts[1]`) over the one-token base **only when they route to DIFFERENT scripts**:
```python
if two in STRATEGIES and STRATEGIES[two].get("script") != STRATEGIES.get(one, {}).get("script"):
    return two
```
The script-difference guard is what keeps `rsi_v1 → "rsi"` untouched (STRATEGIES has both `"rsi"` and `"rsi_v1"`, but both point to `01_rsi_v1` — no ambiguity to resolve, so it stays on the existing alias path). Verified: of 17 live config keys, exactly one (`vrp_condor_v1`) changes resolution.

**Fast-detect:** when a strategy's log TAG (`[VRPC]`) disagrees with its dashboard LABEL, or the running `ps` cmdline's script basename doesn't match what STRATEGIES maps its base to — trace `_base(config_id)` by hand. Any base key containing `_` is a candidate for prefix-shadowing by a shorter key.

---

## TRAP #117 — EOD report ke "errors" me 3 FALSE-positive + 1 real crash; observability tool khud galat gin raha tha (not the strategies).

**Symptom (2026-07-15):** user ne EOD report ke 6 negatives dekh ke poocha "koi gadbad to nahi?" — khaaskar ORB 2 din se 0-trade. Investigate karne pe: **5 me se sirf 1 asli bug tha**, baaki report/tool ke false-positives the aur strategies bilkul sahi chal rahi thi.

**Har ek ka root + fix:**

1. **`ema_v1: 1 Loop error` — ASLI bug (TRAP #86 ka teesra ghar).** `nifty_ema_trader.py` ka main loop `last_close = df["close"].iloc[-2]` karta tha sirf `df.empty` guard ke saath — market-open 09:15 pe jab ek hi candle hoti hai, `.iloc[-2]` out-of-bounds → **poora 24-symbol scan cycle crash** (per-symbol try/except nahi). Exact wahi shape jo `01_rsi_v1.py` me TRAP #86 pe fix hua tha, par ye file usme chhut gayi. Fix: `if df is None or df.empty or len(df) < 2: continue`.

2. **`vrp_condor heartbeat gap 198min` — monitoring-only (trading theek).** Naya `vrp_condor_trader.py` sirf 15:10 entry-time pe act karta hai; beech me `fetch_spot` success hone pe bhi koi log nahi karta tha → `eod_digest` ko ~3hr silence = "heartbeat gap" flag. Fix: loop me ~5-min throttled heartbeat log (`[VRPC] spot=... waiting entry 15:10`). Trading me koi change nahi.

3. **`straddle overtrading 4 vs 0.77/day` — REPORT ka miscount (leg ≠ entry).** `eod_report.py` `got = len(st["completed"])` = completed LEG round-trips ginta tha, aur usko per-ENTRY expectation (0.77) se compare karta tha. Multi-leg strategy (straddle CE+PE, condor 4-leg) me **1 entry = 2-4 fills** → hamesha jhoota "overtrading". Straddle ne aaj sirf **1 entry** liya (2 buy + 2 sell = 4 fills). Aur live config = validated backtest params bilkul same (`or_min=15/orb_k=0.5/tp_frac=0.5/sl_frac=1.0`) → koi divergence nahi. Fix: `got = len(lg["entries"]) or len(st["completed"])` (`★ ENTRY` log lines gino, legacy traders ke liye completed pe fallback).

4. **`chainzone/straddle replay drift 1 MISS` — signal_replay ka false-positive (position-hold model nahi tha).** Single-position-hold strategies (chainzone ride-to-EOD, straddle, condor, backspread) ek position din-bhar hold karti hain; live loop ka `if flat` guard re-entry rokta hai. Par `signal_replay` ka offline bar-scan har bar signal generate karta hai — position hold karte hue doosra signal → MISS. chainzone 10:35 pe SHORT enter (15:15 tak hold) → 10:50 ka signal skip → jhoota MISS; straddle 13:00 enter → 13:05 skip → jhoota MISS. **Ye har din har hold-strategy pe aayega.** Fix: naya `gate_in_position()` — `[EXIT]` lines parse karke `[entry → next-exit]` hold-intervals banao, un ke ANDAR wale offline signals ko `GATED(in-position)` karo. Opener (bar-close < uske apne `★ ENTRY` log timestamp) strict `<` se ungated rehta → `diff()` use MATCH kar leta hai.

5. **ORB 2-din 0-trade — bug HI NAHI (observability clarification).** Wo "149-point move" jo user ne chart pe dekha = **khud opening range tha** (9:15-9:45 me OR_high 24218 − OR_low 24069 = 149pt). ORB breakout thresholds usi range se bante hain (short ~24027, long ~24255); din-bhar price us range ke andar raha (sabse neeche bar-close 24054, trigger 24027 se 27pt door). Wide OR = no breakout = correct skip. Loop live, config active, zero error. **Same-day straddle ne isi drop pe trade liya** (uske sensitive params `or_min=15/orb_k=0.5` ne pakda) — proof ki dono strategies apne-apne design ke hisaab se sahi behave kar rahi thi.

**Kahan-kahan kaata:** #1 nifty_ema_trader (open pe roz possible tha); #2/#3/#4 har din har relevant strategy pe (report/tool sensitivity, strategy behaviour theek).

**Permanent guard:** teeno tool-fixes (`eod_report` entry-count, `signal_replay` in-position gate, ema `len<2`) deployed — kal 09:10 fresh process se ema clean, report tool turant clean. Commits `78ce97e` + `6e702e0`.

**Fast-detect:** EOD "overtrading?"/"replay MISS" flag dekho to **pehle strategy ki actual ENTRY count aur position-hold state check karo** (log ke `★ ENTRY` / `[EXIT]`) — leg-count aur stateless bar-scan dono multi-leg / hold-strategies pe over-report karte hain. `<TF>-min tight range me 0-trade` = ORB pe normal (breakout strategy choppy din skip karti hai); asli bug tab jab window me spot ne OR±ATR band cross kiya ho phir bhi `signal=none`. General rule: **observability tool ka flag = "dekho", "bug confirmed" nahi — pehle tool ka mechanism strategy ke design ke against verify karo** (PRE-MORTEM shape #10).

---

## TRAP #118 — EOD report ka headline P&L dashboard se alag; do independent gaps (per-strategy discovery + gross-vs-net) + downloader banner ka expired-contract wall.

**Symptom (2026-07-15):** user ne dekha ki EOD report Net P&L ₹7,747 dikha raha, par dashboard Orders&P&L TOTAL Net ₹5,753. Plus dashboard pe ~19-line ka red banner "🚨 21 Jun ka data missing — contract expire hone wala hai! Token update karo" hafton peeche tak.

**P&L root — do alag gaps (add up to the difference):**
1. **Coverage:** `eod_digest.discover_ids()` sirf config-keys (`active` present) + per-strategy log-files se strategies dhundhta hai, aur **`webhook_*` explicitly skip** karta hai ("uska apna monitor hai, per-strategy log nahi"). Par webhook ke REAL trades `order_store` me `strategy="arschain_MAIN"` (src=webhook, mode=**live**) ban ke aate hain — koi config-key/log nahi → report ke per-strategy sum me kabhi count nahi hue. Aaj ka live webhook trade (+₹1,059.5) report se poora gayab tha.
2. **Gross vs net:** report `sum(per-strategy st["pnl"])` = paper-GROSS (label "paper=gross"), jabki dashboard har trade pe `calcCharges()` laga ke NET dikhata (aaj ₹3,053 charges). Toh 7747 (gross, webhook-miss) vs 5753 (net, all-trades) — do wajah se alag.

**Permanent guard:** report headline ab `_authoritative_totals(date)` se aata — `order_store.trades_for(date)` ka SAARA completed set (webhook/manual incl) + shared `charges.option_charges()` (Rule 6B, wahi date-aware model jiska JS twin dashboard me hai) se net. Verified exact: gross ₹8,806.7 · charges ₹3,053.35 · **net ₹5,753.35 == dashboard ₹5,753**, 38 trades == 38. Per-strategy breakdown chart informational rehta hai; headline authoritative.

**Banner root:** `auto_data_downloader.gap_check()` 60-din scan karke HAR date ka missing-instrument alert karta tha. Expired option contract ka intraday Dhan **permanently drop** kar deta hai (token refresh se WAPAS nahi aata) → wo "missing" kabhi "ok" nahi hota → har cycle re-alert → weeks-long wall. Aur `days_ago >= 5 = urgent 🚨 "contract expire / token update karo"` ULTA tha (purana = expired = kam actionable, zyada nahi) + misleading (token se recover hota hi nahi). **Guard:** sirf last 2 din ke gaps alert (jahan token-fresh retry genuinely help kare); usse purana skip; honest wording ("premium-chart data nahi mila — illiquid/expired strike normal hai"), jhoota token-blame hata. 19 → 3 lines. Genuine full-token-expiry alag path (`fetch_orders() None → 🔴`) se covered hai, wo untouched.

**Fast-detect:** jab do jagah ka "same" total alag ho — **dono ka trade-SET aur charge-treatment alag-alag verify karo** (`order_store.trades_for(date)` all vs per-strategy-sum; gross vs net). Aksar ek side kisi source (webhook/manual/untagged-strategy) ko silently drop kar raha hota hai. Aur koi bhi "data missing / token update" perpetual alert dekho to check karo wo actually ACTIONABLE hai ya expired-resource ka permanent noise — unactionable alert = remove/cap, warna real alert usme dab jaata hai.

---

## TRAP #119 — Positional/overnight strategy ko INTRADAY-assumed system chupchaap 4 jagah maar deta hai

**Symptom:** VRP Overnight Condor (positional, one-night hold) ka paper trade "kal liya tha, aaj
dashboard pe dikh hi nahi raha." Investigate karne pe: trade Jul-15 15:10 pe hui, par **usi raat
22:40 pe `EOD_315_SQUAREOFF` se force-close** ho gayi.

**Root pattern — poora system "aaj ki date" assume karta hai; positional position multiple dates
span karti hai, to 4 alag jagah tootti hai:**

1. **`allow_overnight` config-fragile tha.** EOD 3:15 squareoff skip `risk_gate.allow_overnight()`
   pe depend karta hai jo `nifty_config.json._risk.per_strategy[sid].allow_overnight` padhta tha.
   Wo key config-rewrite (app khud nifty_config ko hot-write karta hai) me **do baar silently drop**
   ho gayi (top-level block se bhi). Key gayab → `allow_overnight()` fail-safe False → raat ko monitor
   restart pe overnight condor 3:15-past dekh ke squareoff. **FIX (durable):** `risk_gate.py` me
   code-level `_ALWAYS_OVERNIGHT = {"vrp_condor_v1","vrp_v1"}` set; `allow_overnight()` id is-set-me
   True, config-independent. Config override bhi rahe. **Config me positional flag daalna = fragile;
   code-level set = durable.**

2. **Dashboard/pos_monitor/recovery sab TODAY-scoped** (`order_store.trades_for(today)`):
   - **Display:** Orders & P&L `/api/orders?date=today` sirf aaj ke legs dikhata → kal ki overnight
     position agle din view se GAYAB (asli "dikh nahi raha" layer). **FIX:** `/api/orders` ab
     `allow_overnight` strategies ke prior-day open legs 7-din lookback se carry-over karta hai
     (`trades_for_range`, `carried_over` tag, display-only — pos_monitor untouched). `allow_overnight`
     pe filter zaroori (warna 7-din lookback ~18 stale intraday open-rows surface kar deta hai).
   - **Recovery:** positional trader ka `_recover()` bhi `trades_for(today)` use karta tha → exit-day
     pe restart hua to entry-date (kal) ki legs aaj ki query me nahi → recovery position CLEAR kar
     deti (kho deti). **FIX:** `vrp_condor_trader._recover()` + `vrp_straddle_trader._recover()` ab
     `trades_for_range((today-7d), today)` use karte hain.

3. **pos_monitor ka squareoff strategy ko INFORM nahi karta** (TRAP #62 shape): 22:40 wala squareoff
   pos_monitor (alag process) ne kiya, par strategy ki in-memory `pos` + state-file me condor abhi
   bhi "held" tha (strategy ko pata hi nahi) — uska apna 15:10 next-session exit pending tha.
   Isiliye trade ko "wapas laana" bas order_store ke 4 phantom `EOD_315_SQUAREOFF` exits delete
   karna tha; strategy pehle se hold kiye baithi thi.

4. **Positional ko RMS profit-lock/caps se re-date karke MAT laao** (Rule 10): position ko visible
   karne ke liye "aaj" ki date pe daalne ka test kiya to global **₹3,000 daily profit-lock turant
   `RMS_PROFIT_TARGET` fire** (condor +₹3,978 pe tha) → 3 legs band. Undo kiya. **Insight:** positional
   position ko apni ENTRY-date pe rakho — pos_monitor (today-scoped) usse manage hi nahi karta =
   natural RMS-exemption = exactly Rule 10 (backtested strategy validated DNA pe chale, RMS caps
   discretionary ke liye). Backtested/positional pe koi profit-lock/max-trades/default-SL mat lagao.

**Cosmetic (real bug nahi):** VRP traders `symbol` field me underlying "NIFTY" record karte, exits
full option-symbol — netting `trad_sym` pe key karti hai (sab legs pe sahi) to pairing/P&L theek.

**Permanent guard:** naya positional strategy = `strategies/live/NEW_STRATEGY_CHECKLIST.md` ka
"🌙 POSITIONAL / OVERNIGHT" section padho (6-point). Positional bug diagnose karne se pehle:
`grep EOD_315_SQUAREOFF` exits in trades.db + `risk_gate.allow_overnight(sid)` check + confirm
strategy ki in-memory `pos` order_store se sync hai.

**Fast-detect:** positional trade "gayab" = pehle dekho wo genuinely closed hui (`EOD_315`/`RMS_*`
exit rows) ya sirf display se (today-scoped view). Genuinely-closed = allow_overnight gap; display-only
= carry-over gap. Fix `ff8db9e`.

## INFRA — Kite/Dhan IP-whitelist model (live-verified 2026-07-16)

**Context:** Bhai ne naya VPS liya (2 static IP ke liye). Sawaal: CODE3B ko Zerodha orders ke liye
static/whitelist IP chahiye hi kya? Poora **live-test** se resolve — answer **NAHI** (single Hostinger
box hi kaafi). Beech me ek galat conclusion (neeche #2) se bhi seekha.

**Verified facts (live, market-hours):**
1. **Kite (Zerodha) ORDER placement = IP-whitelist REQUIRED** — Kite dev console → Allowed/static IP.
   Non-whitelisted IP se → `PermissionException: IP (...) is not allowed to place orders for this app`.
2. ⚠️ **Kite READ calls (profile/margins/`order_margins`) IP-gated NAHI** — aur `order_margins` ka pass
   hona `place_order` ka pass hona **PROVE NAHI karta** (ek baar main is pe galat conclude kar gaya —
   order_margins naye IP se pass hua par `place_order` reject). Order-readiness sirf asli order-path se
   confirm karo, calc/read endpoint se nahi. (Same-shape gotcha jaise Kite/Dhan ke doosre read-vs-write.)
3. **Kite order ko IPv4-force bhi chahiye** — VPS pe default IPv6 outbound se `PermissionException`
   aata hai *chahe IPv4 whitelisted ho* (IPv6 `2a02:...` != whitelisted IPv4). Live app
   `socket.getaddrinfo` AF_INET override globally karta hai — standalone script me daalna zaroori.
   (Wahi fix Dhan ke DH-905 ke liye bhi — Dhan+Kite dono.)
4. **Kite account pe market-DATA add-on NAHI** → `kite.ltp()`/`quote()` → `Insufficient permission for
   that call`. Isiliye **data Dhan se, order Kite pe**. Kite LIMIT ka price Dhan candles se lo
   (`charts/intraday`); Dhan `marketfeed/ltp` shared-account pe 429 de sakta.
5. **Dhan DATA = NO whitelist** — LTP + `charts/intraday` + WebSocket `api-feed.dhan.co`, teeno
   non-whitelisted IP se 200. Sirf **Dhan ORDER** IP-gated (DH-905). CODE3B Dhan = data-only.
6. **Dhan static-IP change = 7-DIN LOCK** (Dhan profile: "you can re-set your IP in 7 days").
   Casually reversible NAHI — hatane se pehle 100% pukka karo.
7. **Kite ↔ Dhan whitelists independent** — network trace (`socket.getaddrinfo` logger during
   `place_order`): order sirf `api.kite.trade` contact karta, Dhan ko **zero**. To Dhan whitelist
   ka Kite orders pe koi asar nahi.

**Bottom line:** CODE3B ko apna static IP ki majboori nahi (data whitelist-free, order = Kite-IP +
IPv4-force jo Hostinger pe already hai). Static IP genuinely usko chahiye jo **Dhan pe ORDER** kare
(bhai). Single Hostinger box kaafi; bhai apne Dhan me Hostinger IP whitelist kare (try-add-first to
dodge 7-day lock). Full detail: memory `project_code3b_new_server_migration`.

---

## TRAP #120 — App ne apni hi HTTP API pe auth laga di, aur apna hi background thread bahar lock ho gaya (9:10 auto-start 6 din chupchaap murda)

**Symptom:** Koi nahi. **Bilkul koi nahi.** Yehi is TRAP ka pura point hai.

Mila tabhi jab `risk_gate` ka fix uthane ke liye maine 12 strategy processes deliberately kill kiye,
`algo-monitor` restart kiya (jisme `auto_scheduler` chalta hai), aur wo **wapas uthi hi nahi**.

**Root cause:** `auto_scheduler` (algo-monitor process) bots ko HTTP se drive karta hai —
`requests.post("http://127.0.0.1:5099/api/start?...")` → algo-dashboard. 2026-07-10 ko dashboard pe
single-password **login gate** (`@app.before_request`) laga. Us din se har auto-start call ko
**HTTP 401** mila. Aur caller aisa likha tha:

```python
requests.post(f"http://127.0.0.1:5099/api/start?s={key}&mode={saved_mode}", timeout=5)
except Exception as e:
    pass                    # ← 401 exception nahi hai. status code kabhi check hi nahi hua.
```

`requests.post` 401 pe **raise nahi karta** — 200 aur 401 dono "success" jaise dikhte hain jab tak
`.status_code` dekho na. To `except: pass` bhi zaroorat nahi padi — bug ko chhupane ke liye
**status-code na dekhna hi kaafi tha**. Log me "Auto-starting bots..." roz chhapta raha (wo print
loop se PEHLE hai), aur ek bhi bot start nahi hua.

Bots chal isliye rahe the kyunki **6 din se roz haath se start ho rahe the** — aur wo manual start
itna normal lagne laga ki kisi ne poocha hi nahi ki auto-start kyun nahi chala. Ye wahi "roz ka
firefight" hai jo 2026-06-23 me fix hua declare kiya gaya tha.

**Fix:**
* `dashboard_auth.get_internal_token()` — generate-once/persist token, bilkul `get_secret_key()` wale
  pattern se (dono processes same gitignored `data/auth.json` padhte hain, restart-safe).
* `_is_internal_call()` — sirf `/api/start`+`/api/stop`, loopback + valid token. **Token hi asli gate
  hai** — loopback akela kaafi NAHI, kyunki Caddy bahar ka traffic bhi 127.0.0.1 se proxy karta hai;
  peer-check sirf defence-in-depth hai.
* `_sched_post()` — token bhejta hai **aur status code check karta hai**, kisi bhi failure pe
  `flush=True` ke saath loud 🔴 line. Jo scheduler bots start nahi kar pa raha, wo dobara kabhi
  chupchaap fail na ho.

**Permanent guard — jab bhi kisi service pe auth/gate/firewall/proxy lagao:**
> Sabse pehle likho ki **is service ko khud ke andar se kaun call karta hai**. Apne hi background
> threads, schedulers, cron, health-checks, sibling processes — sab ka enumerate karo. Gate lagana
> ek **client-visible contract change** hai; apne hi internal callers usi contract ke client hain.

**Fast-detect (roz chal sakta hai):**
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST 'http://127.0.0.1:5099/api/start?s=<id>&mode=paper'
# 401 = auto-start murda hai. 200 = zinda.
```

**Failure shapes:** PRE-MORTEM #2 (built ≠ wired ≠ verified) + #5 (silently fails). Ye #5 ka sabse
saaf example hai: koi exception nahi, koi red log nahi, sirf ek int jo kisi ne padha hi nahi.

**Aur ek sabak (isi session se):** SIGTERM turant nahi lagta. Kill ke turant baad `get_pid()` abhi
bhi PID de sakta hai (process network call me blocked ho sakta hai) — pehle test me isi race ne
"auto-restart fail" ka jhootha result diya. Kill karke restart-verify karo to **pehle maut confirm
karo**, phir scheduler ko chalao.

---

## TRAP #121 — `grep -c "import X"` se import verify mat karo: function-local imports jhoota PASS dete hain

**Symptom:** 7 live traders ek saath startup pe mar gaye —
`NameError: name 'risk_gate' is not defined`. Har log **healthy dikhta tha** apni
`[RECOVER] re-attached ...` line tak, phir traceback.

**Kya hua:** 8 traders me ek line add ki jo `risk_gate.entries_today(strategy_id)` call
karti hai (module-function scope me). Deploy se pehle "verify" aise kiya:

```bash
grep -c "^import risk_gate\|import risk_gate" strategies/live/03_orbst_trader.py   # -> 1  ✅
```

Count 1 aaya, maine PASS maan liya. **Par wo import kisi DOOSRE function ke andar tha.**
Python me function-local import sirf usi function ke scope me naam bind karta hai — mera
call site usko dekh hi nahi sakta tha. grep ne "text file me maujood hai" batayaa;
maine use "is line pe resolve hoga" samajh liya. **Do alag baatein hain.**

**Guard — import/naam ka resolve hona SIRF asli import se verify karo:**
```python
import importlib.util, sys
spec = importlib.util.spec_from_file_location("probe", path)
m = importlib.util.module_from_spec(spec); sys.modules["probe"] = m
spec.loader.exec_module(m)          # module-level code sach me chalta hai
```
`ast.parse()` bhi kaafi NAHI — ye pura file parse kar lega, kyunki `NameError`
**runtime** error hai, syntax error nahi. Same shape: audit/lint pass, process crash.

**Fix:** call site pe hi local `import risk_gate as _rg_cnt`, aur verification grep se
badal kar **8/8 modules ka asli import** (upar wala loop). Positions kabhi khatre me nahi
the — sab paper, aur `pos_monitor` order_store se SL/EOD independently protect karta hai
(strategy process se alag) — par live hota to 7 strategies chup-chaap band padi hoti.

**Failure shape:** PRE-MORTEM #2 (built ≠ wired ≠ **verified**). Yahan "verified" hi
jhoota tha — proxy check (grep) ko asli check samajh liya. Jab bhi kaho "verified", poocho:
*maine SACH me wahi cheez chalayi jo production chalayega, ya uske jaisi dikhne wali koi
cheez dekhi?*

**Bonus (usi session se):** `tail -30 logs/x.log | grep NameError` ne fix ke BAAD bhi
"STILL CRASHING" bataya — kyunki log append-mode hai aur **purani** traceback abhi bhi
last-30 me thi. Post-fix health check hamesha **timestamp** ya fresh PID + latest log line
se karo, "error string kahin hai ya nahi" se nahi.

---

## TRAP #122 — Positional ROLL ko day-scoped NETTING mis-pair kar deti hai (phantom P&L + live position gayab)

**Symptom:** User ne kaha "margin abhi bhi galat dikha raha hai". Us ek shikayat ke peeche
teen alag baatein thi — teesri sabse buri:

1. Margin column har leg ka **standalone** margin jod raha tha (hedged structure pe 75-78%
   zyada — RMS side pehle fix ho chuki thi, **display nahi**).
2. Us group ki asli hedged margin `/api/orders` bhejta hi nahi tha.
3. **Aur VRP condor us list me tha hi nahi — jabki uski 4 legs LIVE thi.**

**Root cause (#3):** Aaj 15:10 pe condor ne **roll** kiya — kal ki 4 legs band ki, 4 nayi kholi
(bilkul sahi one-night behaviour). `order_store.trades_for(today)` ki netting sirf **aaj ke**
rows dekhti hai, to usne **aaj ke CLOSING legs ko aaj ke OPENING legs se pair** kar diya:

```
DAY-SCOPED (dashboard):        RANGE-NETTED (sach):
  open      : NONE               open      : 4 legs
  completed : 4 phantom, -71.50  completed : kal ki condor +598
```

Matlab dashboard ek **live position ka farzi P&L** dikha raha tha, aur position khud chhupi hui
thi. TRAP #119 ka hi parivaar, par uska prior-day carry-over fix ise **kabhi pakad hi nahi
sakta tha** — roll ke baad live legs ka `entry_date` **aaj** hi hota hai, to "carry over" karne
ko kuch tha hi nahi. **Kharabi date-filter me nahi thi, NETTING KI WINDOW me thi.**

**Fix:** `allow_overnight` strategies ke liye `open` AUR `details` dono **range netting** se lo
(details ko us date pe jo sach me CLOSE hui usi pe filter karo), day-scoped rows drop kar do.
Intraday strategies day-scoped hi rahen — wo EOD pe flat hoti hain, unki prior-day "open" rows
**stale** hain (is DB me ~38) aur unhe range-net karna ghosts zinda kar dega.

**Guard — positional/overnight strategy ka koi bhi naya consumer likho to poocho:**
> Ye rows kis **netting window** se aa rahe hain? Ek entry aur uska exit **alag din** pad sakte
> hain. Din-bhar ka slice liya to netting galat leg jodegi — chup-chaap, aur jawab
> **plausible dikhega** (−₹71 bhi ek "normal" number lagta hai; +₹598 hona chahiye tha).

**Yeh window-blindness ab teen jagah mil chuki hai** — display (#119), RMS capital (`_today_open`),
aur netting (yahan). **Naya code positional ko touch kare to teeno check karo.**

**Bonus sabak:** "margin galat hai" jaisi ek-line shikayat ke peeche ek se zyada bug ho sakte
hain. Pehla milte hi mat ruko — maine margin display fix karke verify kiya, tabhi dikha ki
condor group **hai hi nahi**. Agar sirf apne fix ka number check karke aage badh jaata, to
farzi-P&L wala bug abhi bhi live hota.

---

## TRAP #123 — Backend badla to AUTOMATED path migrate hui, MANUAL button purane broker pe hi reh gaya

**Symptom:** Koi nahi. Yahi is trap ki khaas baat hai — 2026-07-16 ke audit tak ye chup baitha tha.

**Root cause:** 2026-07-10 (TRAP #90) pe orders Dhan se **Kite** pe chale gaye
(`_risk.global.default_broker='kite'`), aur `smart_order` har leg ki row me uska **asli broker**
likhne laga. `pos_monitor` ka apna `_do_squareoff` shuru se sahi tha:

```python
broker = get_broker(p.get("broker") or "dhan")     # leg ka apna broker
```

Par dashboard ka **manual Close ✕** (`_close_position_impl`) me har broker-touchpoint ek
hardcoded `'dhan'` literal tha — jabki route **upar hi** `this_leg` order_store se utha chuka
tha (group_id ke liye) aur uske paas asli broker maujood tha. Kite-held leg pe dono raaste galat:

| Dhan ka jawab | Kya hota |
|---|---|
| position-book milti hai, symbol nahi | `is_flat_fresh` **"FLAT"** — ye error nahi, **confident jawab** hai, isliye uska fail-open guard chalta hi nahi → `mark_externally_closed` → **koi order nahi jaata**. Zerodha pe position khuli, app usse band samajhta hai, aur `externally_closed` hone ki wajah se **3:15 squareoff bhi skip** → raat bhar unprotected. UI ka message asli flat se **bilkul same** dikhta hai. |
| Dhan error deta hai | "flat nahi" → `api.dhan.co/v2/orders` pe **asli order** → Dhan pe **nayi naked position**, Kite wali bhi khuli. Aur ye "Dhan hands-off" rule bhi todta hai (TRAP #97 ne auto-adopt band kiya tha, **manual raasta chhoot gaya**). |

**Permanent guard:** Jab bhi backend/broker/provider badlo — **automated aur manual dono raaste
ginno**. Automated path pe sabki nazar hoti hai (wahi roz chalta hai); manual button mahine me
ek baar dabta hai, isliye wahi peeche reh jaata hai. Aur agar kisi row me `broker`/`account`/
`source` jaisa field hai, to **use padho** — hardcode karna matlab us field ka matlab hi khatam.
Yahan asli fix `'dhan'` ko variable karna nahi tha (Kite ka API hi alag hai) — poora call
`smart_order.execute(is_exit=True)` pe le jaana tha, wahi jo `_do_squareoff` karta hai.

**Fast detect:** `grep -n "'dhan'" trader_dashboard.py` — jo bhi line `p.get("broker")` ke bina
hai, wo shak ke daayre me hai.

---

## TRAP #124 — Enforcer "0 FAIL" bol raha tha jabki 6 money-path violations saamne padi thi

**Symptom:** `architecture_audit.py` har commit pe pass ho raha tha. Bharosa tha ki Rule 6B
enforce ho raha hai. **Nahi ho raha tha.**

**Root cause:** Check ek **SHAPE** match karta tha, **kaam** nahi.
`check_raw_orders` sirf `.place_order()` / `.cancel_order()` wale AST attribute-calls dekhta tha.
Par order dene ka doosra roop bhi hai:

```python
r = requests.post("https://api.dhan.co/v2/orders", json=body, headers=hdrs)
```

Ye bilkul wahi kaam hai — par audit ke liye **invisible**. 6 asli sites: `/api/manual-order`,
`/api/close-position` (TRAP #123 ka ghar), `/api/debug-order`, aur **`nifty_ema_trader.py` ki do
lines** — yaani `ema_v1` raw REST se order deta hai, to RMS gating / rate-limiting / async
fill-confirm / order_store recording **sab skip**. CLAUDE.md ka Rule 6 ispe shak already jata
raha tha ("ema_v1 ka status verify na hone tak isi list me maano") — audit ise **kabhi bata hi
nahi sakta tha**.

Doosra andha kona: `_core/webhook_executor.py` apni **ekmatra** kill-floor guard
`trader_dashboard` se import kar raha tha, `try/except: pass` ke andar — yaani money-path UI pe
depend, aur **fail-OPEN**. Koi check is shape ko dekhta hi nahi tha.

**Permanent guard:** Naya check likhte waqt poochho — "ye **kaam** pakad raha hai ya sirf ek
**likhne ka tareeka**?" Har kaam ke 2-3 roop hote hain (SDK method, raw HTTP, subprocess, koi
wrapper). Ab checks 7 (`RAW-HTTP-ORDER` — URL constants + f-strings + module-level names bhi
resolve karta hai, aur sirf WRITE verbs, kyunki `GET /orders` status-poll hai) aur 8
(`CORE-IMPORTS-UI`) us gap ko band karte hain. **Aur sabse zaroori:** ek clean audit ka matlab
"sab theek hai" nahi — matlab "jo check likhe hain unme kuch nahi mila". Wo do alag baatein hain.

**Fast detect:** Naya check add karo to pehle **jaanbhoojkar violation likh ke** dekho ki wo
pakadta hai. Aur audit ka scope periodically poochho: "kya ye us file ko bhi dekh raha hai jahan
asli kaam hota hai?" (`scratch/nifty_trend` isi sawal pe 12,558 LOC ke saath bahar mila — sirf
apne NAAM ki wajah se.)

**Family:** Wahi shape teesri jagah bhi tha — `session_guard.py` (do sessions ko ek repo pe
likhne se rokta hai) sirf `CODE3B/.claude/settings.json` me register tha, to **parent se khuli
session use chalati hi nahi thi** — na lock leti, na deny hoti. Guard theek us case ke against
bekaar tha jiske liye bana tha. **Har protection se poochho: kya ye us raaste ko cover karta hai
jiske liye bana tha, ya sirf us raaste ko jahan likha gaya tha?**

---

## TRAP #125 — Bade inline `<script>` ko files me toda: `setTimeout(...,0)` "sab scripts ke baad" NAHI hai

**Symptom:** `templates/index.html` ke 9,569-line inline block ko 14 `static/js/*.js` me todne ke
baad dashboard load pe hi:

```
Uncaught ReferenceError: calendarRender is not defined @ app-00-core.js:45
```

**Root cause:** Split se pehle maine hoisting-hazard analysis chalayi thi aur wo **clean** aayi.
Do baar galat thi:

1. **v1** ne function **body** ko "deferred, isliye safe" maana. Galat — agar function
   **top-level pe CALL** ho jaye, uski body **usi waqt** chalti hai, aur uske andar ka har
   reference bhi immediate ho jaata hai. (Transitive banana pada.)
2. **v2** (transitive) ne bhi ise **pass** kar diya — kyunki call `setTimeout` ke andar tha:

```js
setTimeout(() => { startLtpStream(); loadAll(); switchTab(activeTab); }, 0);
```

`setTimeout(...,0)` ka matlab **"sab scripts ke baad"** nahi hai — uska matlab **"jaise hi stack
khaali ho"**. Ek hi inline `<script>` me wo poore 9,569-line block ke baad hi chal sakta tha
(isliye mahino chala). **Alag `<script src>` files me browser use DO scripts ke BEECH chala
deta hai** → `switchTab('calendar')` chala jab `calendarRender` wali file load hui hi nahi thi.

Do aur usi shape ke mile: `togglePeakView` restore (`try/catch` me tha → ReferenceError **poora
nigal** jaata, view chupchaap restore hi na hota, console me kuch nahi) aur
`loadStrategyRegistry` (sirf isliye bacha kyunki wo `await fetch` karta hai aur baaki scripts
race jeet leti hain — **ittefaq, guarantee nahi**).

**Permanent guard:** Ek script ko files me todte waqt asli khatra **hoisting nahi — load-time
bootstraps** hain. Jo bhi load pe chalna hai aur doosri files ke functions chhuta hai, wo
`document.addEventListener('DOMContentLoaded', ...)` me daalo — **wahi ekmatra cheez hai jo
"parser + saari classic scripts done" guarantee karti hai**. (`readyState` fallback ki zaroorat
nahi: document me likhi plain `<script src>` hamesha parse ke dauran chalti hai.) Analyzer me
`setTimeout`/`queueMicrotask`/`requestAnimationFrame` ko **immediate maano**, `setInterval` ko
nahi.

**Fast detect:** Split ke baad har file me top-level pe koi bhi call/`setTimeout` dhundo — har ek
ko `DOMContentLoaded` chahiye. Aur **browser me chala ke console dekho** — `node --check` sirf
syntax batata hai, ye class usse kabhi nahi pakdegi. (Isi session me maine `ast.parse` pe bharosa
karke dashboard ka startup `NameError` bhi introduce kiya tha — TRAP #121 ka wahi sabak, dobara:
**jo cheez chalti hai use chala ke dekho**.)

**Naya code:** naya UI JS ab `static/js/` me likho, `index.html` me inline **nahi**. Aur `{{ }}`
wala JS static file me mat daalo (wahan Jinja chalti hi nahi) — TradingView ke `{{timenow}}`
placeholders isi wajah se `{% raw %}` me the; static file me wo apne aap literal hain.

---

## TRAP #126 — DERIVED file ko source samajh liya: ek bare run ne 4 saal ka NIFTY data uda diya

**Symptom:** `git status` me `scratch/nifty_trend/nifty_1min.csv` modified — 788,410 rows se
**416,673**. Data 2018-01-01 ki jagah 2022-01-03 se shuru hone laga. Koi warning nahi, koi error
nahi.

**Root cause:** `nifty_1min.csv` **source nahi — DERIVED hai**. Asli source per-day store
(`._TRADING DATA/Index/NIFTY/NIFTY_<date>.csv`) hai; `data_fetch.rebuild_frames()` store se CSV
**overwrite** kar deta hai. Do alag tareeke se saal katte the:

1. `main(start="2022-01-01")` wahi `start` `rebuild_frames(start=start)` → `dl.load_all(start=)`
   me bhej deta tha, **jo us date se pehle ka sab filter kar deta hai**. Yaani **store poora
   hota tab bhi**, bina argument ke `python data_fetch.py` CSV ko 2022+ kar deta.
   `start` ka matlab "kitna peeche **DOWNLOAD** karna hai" tha, "kya **rakhna** hai" nahi —
   dono ek variable me mila diye gaye the.
2. Store me din na hon → CSV chhoti, chupchaap.

Dono fire hue. Commit `2c57074` ("extend NIFTY data to 8.5yr") ne CSV 2018 tak bhari thi — par
**2018-2021 ke per-day files local store me kabhi aaye hi nahi** (wo kaam VPS pe hua tha, git se
sirf **tayyar CSV** aayi). Wo 4 saal **sirf git ke committed blob me** bache the — ek `git add`
door, hamesha ke liye jaane se.

**Aur ek:** `nifty_1h.csv` (usi function se banti hai) **hamesha se 4 saal chhoti thi** — 1-min
extend hui, 1H peeche reh gayi. `engine.py` usi ko padhta hai, to **saara positional/1H research
2022+ pe chal raha tha** jabki intraday 2018+ pe. Kisi ko pata nahi chala.

**Permanent guard:** `rebuild_frames()` ab **REFUSE** karta hai agar CSV 98% se chhoti ho —
dono row counts, store ka asli range, store path, aur ilaaj print karke (`--force` se hi aage).
`rebuild` ab `start` leta hi nahi — CSV hamesha **poora store**. `seed_from_local()` sirf
**missing** din likhta hai (pehle poori CSV se overwrite karta tha, jo **taaze store days ko
purane CSV se replace** kar deta).

**Sabak (generic):** Jo file derive hoti hai, uske rebuild ko **kabhi silently chhota mat hone
do** — "output pehle se chhota hai" hamesha ek sawal hai, kabhi jawab nahi. Aur agar ek input
(`start`) do matlab rakhta ho, wo kabhi na kabhi galat wale pe lagega.

**Fast detect:** `python data_fetch.py --seed` (CSV se missing din store me wapas — token nahi
chahiye), phir dobara chalao: row count **badalna nahi chahiye**. Badle to store adhoora hai.
## TRAP #127 — 4 saal ka `annual_return` padha ja raha tha jo CAGR tha hi nahi: sizing backtest ka hissa hi nahi thi

**Kaise pakda:** user ne poocha "itni mehnat, itne backtest — par kisi bhi strategy ka CAGR
10% ke upar nahi, ye to FD se bhi kam hua na?" Sawaal poori mission ke premise pe tha, code
pe nahi. Numbers dekhe to premise nahi — **metric** toota hua nikla.

**Root cause — do baatein, dono `results.js` ke andar chupi:**

1. `engine.py:21` → `START_CAP = 1_000_000.0`. Ye number kahin se derive nahi hota, bas
   type kiya hua hai. Har run ka `net_pct` / `annual_return` isi ke against nikalta hai.
2. `bs_option.py:261` → `def reprice(trades, sigma_map, lot_size, lots=1, ...)`. **lots=1,
   hamesha.** Equity ₹10L se ₹13.9L ho jaaye, agla trade phir bhi 1 lot ka.

Matlab `metrics["annual_return"]` = *(1 lot ka rupee P&L / ek arbitrary ₹10L)* ko annualise
kiya hua. Usme **compounding hai hi nahi**, aur denominator **capital-at-risk nahi** hai.
Mid-Day ORB ka poore 4.5 saal ka sabse bura drawdown **₹17,690** tha — baaki ~₹9.8L kabhi
kaam pe laga hi nahi, par CAGR ke divide me poora gina gaya. Isi liye 7.6% dikha.

**FD se compare karna do alag sawaalon ko compare karna tha.** FD *hi* risk-free rate hai —
uska excess return, by definition, zero hai. Sahi sawaal ye nahi ki "% zyada hai ya kam", ye
hai ki **binding constraint kya hai** — aur wo capital nahi, **drawdown tolerance** hai.

**Fix:** naya `scratch/nifty_trend/honest_sizing.py` — sizing ko strategy ka hissa banata
hai. DD budget do, wo `runs/<slug>/results.js` ki asli trade sequence pe lots ko monthly
re-size karke compound karta hai, aur us curve ka asli CAGR + realised DD deta hai.
DD haircut **guess nahi** — har run ke apne Monte-Carlo ka **worst-5% trade-ordering**
(`combos[..].mc.table.maxdd[1]`, rows = `[original, worst5, median, best5]`). ORB: realised
DD ₹17,690 par MC worst-5% ₹29,431 → sizing ₹29,431 pe = bad-luck ordering ke against, us
lucky ordering ke against nahi jo record ho gayi.

**ORB (p=0.000, Sharpe 2.37, 567 trades, 4.5 yr) — wahi trades, wahi charges, wahi DOM slip,
sirf sizing add:**

| | purana | honest (10% DD budget) |
|---|---|---|
| CAGR | 7.6% | **29.1%** |
| realised maxDD | −1.77% | −4.9% |
| lots | 1 hamesha | 3 → 10 |
| ₹10L → | ₹13.9L | **₹31,63,142** |

**Sabak 1 — sizing ko backtest se bahar mat rakho.** Ek strategy = entry + exit + **sizing**.
Teeno me se ek bhi hardcode kiya to jo metric nikalta hai wo strategy ka nahi, tumhare
hardcode ka reflection hai. `lots=1` "neutral default" nahi tha, wo ek **chhupa hua sizing
decision** tha — aur poore mission ka headline number wahi decide kar raha tha.

**Sabak 2 — compounding chalu karte hi backtest ki jhoot pakdi jaati hai.** Bina capacity
ceiling ke wahi script Long Strangle ko **₹25,000 crore** tak compound kar deti hai (lots
12 → 3.15 lakh). Wo strategy ka statement nahi, **unbounded compounding ka** statement hai —
par wo diagnostic **muft me** aata hai: jo bhi edge cap se takra ke absurd ho jaaye, uska
backtest sach se zyada acha hai. Strangle ka p=0.072 (significant nahi) aur VRP Condor ka
**Sharpe 15.33** dono isi tarah saamne aa gaye — Sharpe>4 = red flag wala apna hi rule.
**Compounding sim ko truth-detector ki tarah use karo, projection ki tarah nahi.**

**Sabak 3 — jab koi metric premise-level sawaal khade kare, metric ki definition pehle
padho, jawab baad me do.** "Faayda hi kya hai" ka jawab dene se pehle `net_pct` ka
denominator dekhna tha. Do line (`engine.py:21` + `lots=1`) me poora sawaal ghul gaya.

**Bacha hua kaam (jaan-boojh kar khula):** capacity model nahi hai. `--max-lots` maine socha
hai, measure nahi kiya. ORB ko farq nahi padta (10 lots pe khud ruk jaata hai), par
straddle/strangle ke honest numbers poori tarah us arbitrary cap pe tike hain — unhe believe
karne se pehle bade size pe DOM slip (ADR-005) re-calibrate karna padega. P&L scaling linear
hai = jaan-boojh kar conservative (₹20/order flat brokerage se asli N-lot P&L thoda behtar
hoga; `scripts/lot_scale.py` live fills pe asli cost-dilution naapta hai).


---

## TRAP #128 — "SELL matlab SHORT" — ek mapping jo har baar ulti thi, aur ek ghost jo 15 din pehle flag hua tha

**Kab:** 2026-07-16 (live, arschain_MAIN, asli paisa)
**Lakshan:** TradingView ne NIFTY pe 2 trade liye. Humne 1 liya. Pehla trade bhi
wick se uda, doosra aaya hi nahi.

User ka pehla hypothesis: *"wick se trade udd gaya, hamara logic tha ki trailing
ke upar CLOSE ho tab cut ho"*. Wo lakshan sahi tha, **wajah nahi**. Teen alag
bugs the, teeno alag.

### 1. Live SL 2.5× tight chal rahi thi — ek missing config key

Exit tag: `DEFAULT_TSL_SL:-1600`. Math exact match karta hai: 2 lot, peak ₹559
(`MIN_LTP` tag se), initial SL `-2000` + 2 step × 200 → `-1600` → premium 152.31
→ wick ne le liya.

Par user ne RMS me **₹2,500/lot** set ki thi, ₹1,000 nahi. Kyun ₹1,000 chala?

```python
ps = rc.get("per_strategy", {}).get(strategy or "", {})   # miss -> {} -> global
```

RMS ka lookup **raw dict** hai. Live strategy ka id `arschain_MAIN` hai. Override
`ARS_CHAIN_V1` aur `webhook_v1` pe tha. `arschain_MAIN` pe kuch nahi → `{}` →
global (₹1,000). **Koi error nahi, koi log nahi.**

14-July ka "7 discretionary + webhook" rollout `webhook_v1` pe land hua — generic,
kabhi-na-chalne-wala config — asli live `arschain_MAIN` pe nahi. Rollout aadha
landa aur kisi ko pata nahi chala.

> `strategy_registry.resolve()` **pehle se maujood tha** aur ye bilkul isi kaam ke
> liye bana tha (`ARS_CHAIN_V1` → `04.01`, case-insensitive). Paisa-wala code usse
> poochta hi nahi tha. **Ek sahi abstraction likh dena kaafi nahi — jo code paisa
> chalata hai use uske raste me DAALNA padta hai.**

### 2. `direction` option ke order-side se nikalti thi — har sell-side config pe ulti

```python
opt_action = p.get("opt_action") or ("SELL" if p.get("entry") == "SELL" else "BUY")
direction  = "SHORT" if opt_action == "SELL" else "LONG"     # _recover_wh_state
```

`arschain_MAIN` ka config: `long_opt_type: PE`, `short_opt_type: CE`,
`opt_action: SELL`. Yaani **LONG pe bhi SELL, SHORT pe bhi SELL.**

To ye mapping kabhi-kabhi galat nahi thi — **hamesha `SHORT` deti thi**, dono
directions pe. Dashboard ~10:15 pe restart hua, 10:01 ki LONG position `SHORT`
bankar wapas aayi.

**Sabak:** `direction` (index-side signal) aur `opt_action` (option order side)
do alag cheezein hain. Ek se doosri nikalna sirf tab chalta hai jab strategy
long=BUY/short=SELL kare. Sell-side strategy pe wo ek **constant** hai — usme
information hai hi nahi. Ek field se doosra derive karne se pehle poocho: *kya
source field kabhi badalta bhi hai?*

Sahi source: contract ka apna CE/PE, scrip master ke `SEM_OPTION_TYPE` se (naya
`dhan_master.get_option_type_by_sec_id()` — structured field, `trad_sym` string
kabhi mat kaato, TRAP #13/#79), aur strategy ke apne long/short opt-type config se
match karo. Na nikle → **skip + loud log**, guess mat karo (wahi doctrine jo
`get_lot_size_by_sec_id()` → None pe hai).

### 3. Cross-process ghost — TRAP #62, 15 din pehle flag hua, kabhi band nahi hua

`_wh_state` **per-process** hai. `algo-dashboard` aur `algo-monitor` dono
`webhook_executor` import karte hain → **do alag dicts**. `release_position()`
sirf apne process ki copy badalta hai.

10:27 pe `DEFAULT_TSL_SL` `algo-monitor` me fire hua → uski copy saaf → dashboard
ki copy me ghost hamesha ke liye. 11:15 pe: `ghost.direction == "SHORT"` ==
incoming `"SHORT"` → **`ENTRY skip — already SHORT (pyramiding off)`**.

TRAP #62 (2026-07-01) me likha tha: *"Account-level trailing-profit-lock squareoff
doesn't inform the owning strategy process — flagged, not fixed."* Do direction
diye the, dono me se koi liya nahi gaya. **15 din baad usi shape ne trade khaaya.**

Ab: `handle_signal()` cached position ko `order_store` se verify karta hai
(durable, cross-process) — skip ya reversal ka faisla lene se **pehle**. Error pe
"still open" maano: **trade miss karna recoverable hai, live leg double karna
nahi.**

### 4. Aur wick? Wo bug tha hi nahi.

Candle-close confirm **pehle se hai** (`trader_dashboard.py:5989`) — par sirf
profit-lock zone me (`sl_level >= 0`). Yahan SL `-1600` thi = loss zone → live
tick pe fire, **by design** ("immediate capital protection").

Par RMS global me user ka `default_sl_candle_close: True` set hai. Wo flag sirf
dropdown/legacy SL types pe `SL_CANDLE_CLOSE:true` stamp karta hai —
**aggressive profile use karta hi nahi.** User ko laga candle-close ON hai. Tha
nahi. Aur ye trade fir bhi nahi marta agar SL sahi ₹2,500/lot pe hoti (premium
175.38, wick 155 se 20 point door).

### Teen sabak

1. **Lakshan wajah nahi hai.** "Wick se uda" sahi tha; wick ka fix kuch nahi
   bachata. Asli wajah ek missing config key thi — do layer neeche.
2. **Silent fallback = wo bug jo mahino zinda rehta hai.** `{}` → global, raw
   alias → display, dono chup the. Ek `⚠ UNREGISTERED` line pehle din pakad leti.
3. **"Flagged, not fixed" ek deadline hai, decision nahi.** TRAP #62 ka writeup
   perfect tha. Usne kuch nahi bachaya.

**Fix:** commit `cd77fd1` (dono code path + `TEST/test_wh_direction.py` — 13
checks, asli scrip master + asli config pe incident replay), `99f29d7` +
ADR-007 (identity/naming), aur `arschain_MAIN` ka RMS override (config).

**Abhi khula:** `order_store.strategy` pe koi validation nahi — pichle mahine
`''` ne **15 live orders** likhe, `unknown` ne 13, `default` ne 12. Jab tak sirf
registered ID enforce nahi hoti, ye class band nahi hui.

---

## TRAP #129 — "trades_today" counted the whole fetch window, not today (and the cache decided how big that window was)

**Symptom.** TV entered SHORT at 10:05. `range_v1` (04.03 Pine2Python — the mirror built to
replace the ₹1,600/mo TV webhook) placed nothing. User: *"ab iska masla kya hai.. kun signal
nahi aaya"*. Memory already blamed capital (`CAPITAL_BLOCKED`, "mahine me 1 NIFTY trade").

**It wasn't capital, and the engine wasn't silent.** It produced the identical `SELL` on the
identical bar and dropped it at its own cap guard. Three defects, stacked:

1. **`run_signal_engine` had no concept of a day.** `trades_today = 0` is set once per CALL,
   then the loop runs every bar in `df_1m`. `df_1m` is a 5-day window. So "max 2 trades per
   day" was really "max 2 trades per window" — spent on Jul 8-9, and every signal for the
   next 7 trading days hit `if trades_today >= max_trades: continue`. The window slides, so
   there are almost always ≥2 older entries in it: the strategy could essentially never
   trade. **The Pine had this right and the port lost it:**
   `var int tradesToday = 0 / if ta.change(time("D")) != 0 / tradesToday := 0`.
2. **`shared_candle_cache` keyed on `sec_id:interval`.** The rows behind that key are NOT
   interchangeable — each producer asks Dhan for a different window. On NIFTY 5m alone:
   chainzone_v1 stores 10 days (its ATR warm-up), straddle_v1/backspread_v1 store 5,
   range_v1 wanted today only. Last writer won, and re-decided every 20s (the TTL).
   `fetch_1m`'s own "keep only today" filter never runs on a cache hit.
3. **`fetch_1m` cut everything but today**, starving ATR(14)+zones — the engine returns
   nothing until 20 bars exist (~10:55 on 5m). TRAP #85, still open, in its worst form.

**Root pattern.** A cache key must encode everything that changes the VALUE's meaning. This
one encoded the instrument and the bar size but not the *window* — so four strategies with
four different definitions of "NIFTY 5m" shared one bucket, and which one you got depended
on who looped last. Compounding it, a variable named `trades_today` that never checks a date
reads as correct at every review; the name did the lying, not the logic.

**Fast detect.** Call the fetcher twice a few minutes apart and compare `len(df)` and
`df.time.dt.date.nunique()`. Measured live within one hour: **618 bars / 10 days**, then
**21 bars / today**. A strategy whose input window changes between loops is not a strategy.

**Permanent guard.** `days` is part of `shared_candle_cache._key()`; all 9 callers pass the
window they actually asked Dhan for (same window still shares — that's TRAP #2's whole
point). `run_signal_engine` resets day-scoped state on each date change, mirroring the Pine;
position/atr_sl reset with it (the Pine flattens daily), zone state does not (Pine's zone
vars are `var`), ATR stays continuous. `fetch_1m` keeps its window — safe only because of
the day reset, and it's what the backtest already did (continuous `atr_all` + per-day
`backtest_day`), so live and backtest now agree by construction rather than by luck.

**Found while fixing, worth its own line.** Live had `exit_zone` unset → default `False`,
while the Pine has `MainExit_Toggle=true` and `validate_strategy` mirrors it
(`"exit_main": True`). **The mirror was missing an exit TV has.** And `SL_Blw_Fib_Exit_Tog`
(Fib Exit) is `true` in the Pine and absent from BOTH python engines — likely part of the
old 9.8% validation gap nobody had itemised.

**Two tool bugs surfaced on the way** (a diagnostic that lies is worse than none):
`signal_replay`'s `LOG_LINE` regex demanded whitespace after `HH:MM:SS`, so Python's default
asctime (`10:32:15,325`) never matched → it reported *"trader ran nahi"* for a process that
was alive and 48 lines deep into today's log. It hit 6 of 18 logs — the entire Ars chain
family, the webhook, universe, ema — and `eod_report` shares `run_for()`. Separately the
adapter reported one signal N times (`run_signal_engine` returns the LAST signal found,
however old — that's what `sig_bar`/`total_bars` are for, and the adapter was reading only
`out[0..2]`).

**Blast radius.** All three configs are paper; 04.04 DirectWebhook (the live one) doesn't use
this engine. No money was lost — but every "04.03 barely trades" observation for weeks was
this, and the ₹19,200/yr decision it exists to inform was being measured on a strategy that
could not trade.

---

## TRAP #130 — Ek mahine "kaise bechen" optimise kiya; "bechna chahiye bhi ya nahi" kabhi nahi pucha 🔴🔴🔴

**Symptom.** User: *"profit ₹2,000 tak chala jaata single lot, fir fall back hota... do taraf
gussa aati hai ki profit me aa kar loss hua"* → poochha: fixed target lagayein ya "peak se
₹200-400 gir gaya to turant exit" (drop-lock)? *"iske liye hi itna aggressive trailing bana
rahe they par uska apna hi masla ho raha tha."* Ye ek **exit-rule** ka sawaal lag raha tha.

**Wo exit-rule ka sawaal tha hi nahi.** Ars chain **ATM options BECHTI hai**. 5m pe uski
**average jeet = ₹1,806**. Wo bug nahi — wo **chhat** hai: bechne wale ki maximum kamai =
jitna premium mila (`86.70 × 65 = ₹5,635`), aur realistically uska ek hissa. **Koi exit rule
chhat nahi utha sakta.** User ka "₹2,000 wall" = structure, defect nahi. Aur usne ye khud
pehle hi session me pooch liya tha (*"dono sell karen to decay hoga… is type ki strategy ko
kya bolte hain"*) — bina jaane ki uski apni live strategy wahi kar rahi hai.

**Root pattern.** Har session ne poochha **KAISE bechen**; kisi ne nahi poochha **BECHEN
KYUN**. Ek mahine ka session log (sab Ars chain pe): exit ATR vs EOD · naked vs hedged ·
SL kitna · lot kitna · capital pool · RMS override · webhook delivery · order rejection ·
lot mismatch. **Har ek "how"**. Aur jo backtest jawab deta hai wo **6 din se disk pe pada
tha** (`runs/chain_zone_naked` ⚠️ rejected, 2026-07-10) — poore session-history me **ek baar**
zikr hua, wo bhi ek correlation table ki row ke roop me. **Kisi ne use live deployment se
joda hi nahi.** "Naked vs hedged" wala backtest bhi bacha nahi saka: **dono bechne wale the.**

**Numbers.** Ek hi signal, ek hi params (`touch_tol 5.0, zone_age 2, max_cs 40.0, hawa false,
chain_lookback 20, atr_sl 2.5, rr 1.5`), ek hi exit (`stop_only`), ek hi trade set — **sirf
structure badla** (`bs.reprice` = BUY ATM option vs `bs.reprice_naked` = SELL ATM option):

| TF | trades | BUY option | Sharpe | SELL option | Sharpe |
|----|-------:|-----------:|-------:|------------:|-------:|
| 1m (04.01 Canary yahin) | 2,758 | **+₹4,43,539** | 1.17 | **−₹3,93,963** | −1.49 |
| 3m | 2,174 | +₹4,37,343 | 1.22 | −₹3,86,012 | −1.49 |
| 5m (04.03/04.04) | 1,806 | **+₹6,42,861** | 1.84 | **−₹1,53,004** | −0.58 |

**Signal khud theek hai** — 1m p=0.04, 5m p=0.00 (significance structure-independent hai).
**Ulta sirf rukh hai.** 9/9 combos: BUY jeeta, SELL haara.

**Steelman bhi fail hua.** `reprice_naked` default `vrp_mult=1.0` = premium realised-vol pe
priced = seller ko VRP **milta hi nahi** — wahi ek cheez jiske liye option bechte hain. Wo
unfair test tha, to seller ko **+30% VRP tohfa** diya (IV >> RV, bahut generous):

| seller ko diya VRP | 1m net | 5m net |
|---|---:|---:|
| 0% | −₹3,93,963 | −₹1,53,004 |
| +20% | −₹2,77,320 | −₹31,497 |
| **+30%** | **−₹2,28,057** | +₹20,108 → **train −₹26,905 = min(train,OOS) fail** |

BUY +₹4.43L / +₹6.42L **bina kisi tohfe ke** banata hai.

**Asymmetry jahan chhupi thi — tails me** (5m, per lot, real charges + DOM slip):

| | win% | avg WIN | avg LOSS | W:L | biggest WIN | biggest LOSS | PF | per trade |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BUY | 39.4% | **+₹2,898** | −₹1,295 | **2.24:1** | **+₹26,640** | −₹4,283 | 1.45 | **+₹356** |
| SELL | **43.0%** | +₹1,806 | −₹1,513 | 1.19:1 | +₹9,227 | **−₹6,000** | 0.90 | **−₹85** |

Bechne me **jeet pe chhat, nuksan pe nahi** (−₹6,000 bhi SL ne roka). Kharidne me ulta:
nuksan premium pe ruk gaya, ek trade +₹26,640 de gayi. 1m pe farak aur tez: W:L **4.52:1**.

**⚠️ ABHI PROVEN NAHI — act karne se pehle ye gate:** maine `intraday_engine.design_signals`
ka `chain_zone` chalaya. **Live `range_trader.py` ek ALAG implementation hai.** 90.2% wala
validation **Pine↔Python** tha (range_trader vs TV pine) — **`range_trader` vs `chain_zone`
kabhi match nahi hua**. Live pe flat ₹5,000/lot SL hai, backtest me ATR stop. To ye ek bahut
mazboot **ishara** hai, saboot nahi. Gate = `validate_strategy.py` wale tareeke se dono
signals match karo, phir hi structure badlo (aur paper me pehle).

**Exit-rule wala aadha hissa — ye BAND hai** (poora test ho chuka):
- ORB pe **84 overlay settings** (₹ + %): ₹-sweep me 1/44 "jeeta" — `min(train,OOS)` pe
  **₹909** se, 4.5 saal me = noise, aur total net **₹71,824 haara**. %-sweep: **0/40**, kisi
  bhi metric pe nahi. chain_zone 5m pe: **0/40**.
- **Cohort proof:** 216 ORB trades ne ₹2,000 chhua → **35 negative me khatam (−₹40,410)**,
  **54 ₹5,000+ pe khatam (+₹4,31,032)**. ₹2,000 ka cut ₹40k dard bachata hai aur ₹3.23L
  profit maarta hai = **8:1 ulta**. ₹2,000 pe wo 35 aur 54 **ek jaisi dikhti hain** — peak
  hamesha **baad me** pata chalta hai. **Give-back defect nahi, fat tail ki fees hai.**
- Drop-lock ko jitna "behtar" karo (confirm window, bada arm/gap) utna kam fire karta hai =
  utna baseline ke paas. **Ek achhe drop-lock ki limit = koi drop-lock nahi.**

**Permanent guard.**
1. Kisi bhi strategy ke exit/SL/hedge/capital knob pe kaam shuru karne se **pehle** dekho:
   `runs/` me **isi signal ka doosre structure wala variant** to nahi pada?
   `chain_zone_{longatm,naked,credit}` teeno disk pe the.
2. **Registry me structure likho, sirf signal nahi.** Chaaron 04.x rows "Auto Rev-Chain"
   kehti hain; **ek bhi nahi kehti "ATM options BECHTI hai"** — jo iski sabse badi property
   hai. ADR-007 kehta hai ID = idea; par *structure* idea ka hissa hai, mode/transport nahi.
3. **⚠️/❌ rejected run apne aap me bekaar hai** — usme likho ki wo **kaun si LIVE deployment
   ko invalid karta hai**, warna wo ek aisa sach rehta hai jispe koi amal nahi karta.

**Fast detect.**
- `order_store` me per-strategy side count: NIFTY pe **SELL 22 / BUY 12** → ye bechne wali
  hai. Family ke `runs/*` variants ka verdict uthao aur milao.
- **Symptom ki shakal:** *"profit hamesha ₹X ke aas-paas ruk jaata hai"* jahan
  X ≈ entry premium × lot → **tu short hai; X chhat hai, bug nahi.**

**Meta-sabak (sabse mehnga).** Is poore session me **win rate aur paisa ULTE the, har baar**:
71.4%-win wala drop-lock baseline ka **23%** kamata tha; SELL 43% jeetti hai vs BUY 39.4% —
aur **₹8 lakh** kam banati hai. **"Achha lagta hai" ek bharosemand signal hai ki tu haarne
wale variant ko dekh raha hai.** Aur ek mahine ki firefighting (webhook plumbing, lot
mismatch, RMS override, rejections) me **premise pe sawaal uthane ki jagah hi nahi bachti** —
aag hamesha "kaise" ki hoti hai.

---

## TRAP #131 — the fixes went in the copy; the copy didn't trade

**Symptom.** "TV ne aaj 2 trade liye, python ne 1." Chasing that one bar led to a
strategy with two engines, where the one placing orders had none of the work.

**The shape.** `range_trader.run_signal_engine` traded. `validate_strategy.backtest_day`
ran only in tests — its docstring said so outright: *"Mirror of run_signal_engine but
COLLECTS every trade"*. It was copied for a real reason: the engine returns only the
LAST signal, a backtest needs all of them. Nobody re-synced them, and the Pine-matching
work that reached 90.2% — harami patterns, selectedLine RESISTANCE priority, the TV
fill convention, dropping the tracked_high filter — all landed in the mirror. For
months the quoted number came from code that never placed an order.

Measured 2026-07-17 against the user's own TV export (Jan 6 – Jun 16):

| | vs TV |
|---|---|
| `backtest_day` (mirror) | **75.3%** |
| `run_signal_engine` (trades) | **49.5%** |

Not merely less accurate — unprofitable on the same bars: 124 trades, net −439.7 pts,
PF 0.89, Sharpe −0.46, while TV made +1,987.7.

**Six drifts, every one in the mirror's favour.** max_candle_size on the wrong bar at a
hardcoded value · zones formed off a PERSISTENT touch flag instead of the touching bar
(605 zones vs TV's 430 — the big one) · touch took the first matching level instead of
RESISTANCE-priority · tracked_high reset instead of accumulating, and its entry filter
applied where the mirror deliberately drops it · the line-type check on zone formation
instead of entry · harami missing from the pattern wrappers.

**The tell that named the root cause.** Where the zones agreed, they agreed *to the
decimal* — TV `11:30 RED[26316.8-26335.3]` == the engine's, exactly. Patterns, pivots
and zone prices were never wrong; only the gates were. **When the maths matches
perfectly and the count doesn't, it isn't a bug — it's a second version of the same
code.**

**Root pattern.** A copy made for a good reason is still a copy. The moment one gets a
fix the other is wrong — and the one that keeps getting fixes is the one people TEST,
never the one that RUNS, because testing is where you look. Rule 6B already says don't
duplicate; the exception that feels justified ("I just need it to return something
else") is exactly how this happens. **If the difference is the OUTPUT, add an optional
output (a `trades_out` list), not a second brain.**

**Permanent guard.** One engine: `run_signal_engine` holds the logic; `backtest_day` is
a wrapper adding only TradingView's next-bar fill (a genuine backtest concern).
Patterns inlined so the wrapper's omission can't return. `atr_series` param so a per-day
caller passes a warm ATR instead of forking. `exit_hm` from cfg, not hardcoded.
Verification: validate_strategy re-scored **75.3% / 80% entry-exact / 70 matched —
identical to before the merge**, proving the move was behaviour-preserving; the live
engine went 49.5% → 76.3%, −439.7 → +1,036.5 pts, PF 0.89 → 1.44, engine-only trades
78 → 17.

**Fast detect.** `_TOOLS/live_vs_tv.py` drives the LIVE engine (never a copy) against a
TV export and puts every trade on one chart, TV orange vs engine blue. This was only
found because both were finally looked at side by side. **If a strategy has a "backtest
version" and a "live version", score the live one — the other number describes a
program you don't run.**

**Blast radius.** All three Ars chain configs are paper; 04.04 DirectWebhook (the live
one) doesn't use this engine. No money lost — but the mirror project that exists to
retire a ₹19,200/yr webhook was being judged on the wrong engine's numbers.

---

## TRAP #132 — the registry was right; nothing asked it properly

**Symptom.** "Registry me jo naam likhe hain wahi dikhne chahiye — abhi bhi puri app
me ye ajeeb naam leak ho rahe hain." Three screenshots: Stats table, Payoff modal
title, notification bell. All showing `ARS_CHAIN_V1_PAPER` / `arschain_MAIN` /
`vrp_condor_v1` instead of the names the registry has held all along.

**The count.** Reported 3. An audit found **25**.

**The shape.** Not one bug — three, and every one of them made the raw key the *only
possible* output, so no amount of care at the call site would have helped:

1. **Two resolvers, different rules.** `strategy_registry.resolve()` (Python) matches
   id + config_key + slug + **aliases**. The JS copy indexed `config_key` ONLY.
   Anything arriving by alias — old `order_store` rows, `ema920`, a log filename —
   missed and fell through to raw. Two lookups = two truths (shape #4).
2. **The labeller was trapped in one file.** `regLabel` lived in `app-05`.
   `mtm_charts.html` / `backtest_chart.html` / `script3.html` never load it — on those
   pages the function **did not exist**. Raw wasn't a mistake there, it was the only
   thing that could happen (shape #2: built ≠ wired).
3. **The fallback map held retired names.** `range_v1: 'Range Breakout'`,
   `ars_chain_v1: 'Ars Chain (live)'` — names the registry had already moved into
   `aliases`. One failed fetch and the app confidently rendered a **stale** name.
   Worse than raw: raw looks broken, stale looks fine.

**Why fixing 25 sites is not a fix.** Site #26 gets written next week. The only durable
move was to make the leak *mechanically impossible to commit*.

**Fix.**
- `static/js/registry.js` — one labeller, 4-way alias index (parity with Python's
  `resolve()`), loaded on **every** page that shows a strategy. **No hardcoded seed**:
  registry down → raw key. A visible "not loaded" beats an invisible wrong name.
- `notify` — the id no longer goes *into* `msg`. A joined string can't be re-labelled
  afterwards; that's exactly why the bell read `ARS_CHAIN_V1_PAPER: ...`. It goes in
  `source=`, and `listing()` labels it at **READ** time → rename a strategy and its
  whole history relabels itself, while the raw id stays in the record for grep.
  Same idea for dashboard toasts (`strat_label()`) and `eod_report` bullets.
- Raw stays raw in plumbing: order_store rows, query params, config keys,
  `option.value`, `title=` hovers. Labelling those would be **wrong**, not just noisy.

**The permanent part — `architecture_audit` check 9 (RAW-STRAT-LABEL).**
It also scans `static/js/` + `templates/`. Checks 1-8 are `.py`-only — which is
*precisely* why all 25 leaks lived undisturbed. **An enforcer that can't see the
display layer enforces nothing about the display layer** (same lesson as TRAP #124,
where the audit reported "0 FAIL" while 6 raw-HTTP order sites were live).
Escape hatch: `raw-id-ok: <reason>` for strings that are **identity, not display** —
storage keys, change-detect fingerprints. Labelling those actively breaks things (two
strategies sharing a name → fingerprint collision; renamed key → user's saved settings
gone). The hatch requires a written reason; it is not a silent skip.

**It paid for itself immediately.** The new check found **4 leaks the manual sweep had
missed** (`backtest_chart.html` ×2, `script3.html`, `mtm_charts.html`) — i.e. the human
pass, done carefully and on purpose, was already 4 short at the moment it declared done.

**Fast-detect.** `python _TOOLS/architecture_audit.py` → any `RAW-STRAT-LABEL` line.
Historical notification rows written before this fix keep the prefix inside `msg`
(78/96 were stripped on VPS 2026-07-17, backup `data/notifications.jsonl.bak.prefix.*`;
18 left alone because their prefix didn't exactly match `source` — no guessing).

**Blast radius.** Display-only — no order path touched. But the cost was real: the same
strategy answered to 4 different names across the app, and TRAP #128 already showed
where naming confusion ends up — a live SL read from the wrong config key because the
id in the money path wasn't the id anyone recognised.

### Addendum, same day — the check couldn't see sentences, only templates

After deploying, the user's EOD report still read `banknifty_v1: heartbeat gap...`.
Two different things, worth separating:

- **The report was generated 15:45; the fix landed 16:53.** It's a static HTML file —
  it does not re-render. Regenerating produced the labels correctly. Not a bug. *Any
  time a "fix didn't work" report involves a generated artifact, check its mtime against
  the deploy before touching code.*
- **But scanning the regenerated report found a leak class check 9 could not see:** ids
  baked into user-facing **sentences**, not template literals — `risk_gate`'s three RMS
  block-reasons (`"...hit for 'rsi_v1_PAPER'"`) and three dashboard error messages.

So the check got the same treatment as the bug: **broaden the enforcer, don't patch the
sites.** Added the Python reason-string shape — and it immediately surfaced the 3
dashboard errors nobody had looked at. **Third time in one session** the same lesson
landed: the leaks live exactly where the check isn't looking (TRAP #124 → check 9's own
`.py`-only blind spot → this).

`risk_gate._sname()` mirrors `_inr()`'s precedent (both exist because these reason
strings are read by humans). **The asymmetry is deliberate and documented:** unlike
`notify` and the UI, this label goes in at **WRITE** time — the reason is a joined
sentence frozen into an `order_store` tag, with no separate field to carry the id, so a
later rename leaves old tags on the old name. Acceptable: **a tag records an event, it
isn't a live label.** Consequence to expect: tags written before this fix keep the raw
id forever (today's 12:15 `RMS_PROFIT_TARGET` rows still say `rsi_v1_PAPER`). The trade
DB was deliberately NOT backfilled — the notification log is a log, `trades.db` is the
money record; different bar for editing.

---

## TRAP #133 — "clear the alerts" is not the job; ask which ones are load-bearing

**Symptom.** 70 unread notifications. User: *"jo solve ho gaya wo clear kar dijye, aur jo
nahi hua usko bhi clear kijye… sabse zaroori baat — jo solve kar rahe wo bug fir se to
nahi aayenge na?"*

The second half is the whole ticket. `notify` is **append-only by design** — dismiss
means *read*, not *deleted*, and a re-occurrence bumps the id so it re-toasts. So
clearing a live problem doesn't remove it, it just resets the countdown. Triage first,
clear last.

**What 96 rows actually were** (grouped by `dedup` prefix, not eyeballed):

| kind | hits | verdict |
|---|---|---|
| `proc` @ 13:30 | 550 | **REAL** — another session restarted both services mid-market; strategies down ~20 min. The alert did its job. |
| `proc` @ 15:30 | 33 | **FALSE, daily** — see below. |
| `log` 401 | 71 | Dhan token expiry (Critical Rule 4). Recurs daily *by design*, not a code bug. |
| TATAMOTORS | 13 | **REAL config** — symbol doesn't exist in Dhan; would spam forever. |
| `uip` scrollIntoView | 4 | **REAL UI bug.** |
| `stale_feed` | 8 | auto-resolved. |

**The 15:30 false alarm — an off-by-one-minute that fires every trading day.**
`check_strategies` gated on `_market_hours()`, whose close is **inclusive**
(`(15,30) <= (15,30)` → True). `auto_scheduler` stops every bot at exactly `t >= (15,30)`
with `keep_active=1` (active stays true so tomorrow's 9:10 auto-start works — that's the
2026-06-23 fix). So for that one minute: market "open", bots deliberately gone,
`active:true` ⇒ 🔴 *"koi order nahi lagega"*. **The function's own docstring already said
"bahar band hona normal hai (15:30 scheduled stop)"** — the intent was documented and
correct; the boundary was one minute wide of it. Intent in a docstring is not a guard.

Fix: separate `_proc_check_window()` ending at `SCHED_STOP`. `_market_hours()` left alone
— the market genuinely IS open at 15:30; what changed is that *we* stopped the bots. Two
different facts deserve two different predicates. Boundary-tested 09:14…16:00 + Saturday.

⚠️ `SCHED_STOP` is now the **second** hardcoded copy of `(15,30)` (the other is in
`trader_dashboard.auto_scheduler`). Commented on both sides. Change one, change the other,
or this exact alarm returns.

**`if (x)` doesn't guard `x.closest(...)`.** `sltp-modal.js`: `if (tableCard)
tableCard.closest('.tv-card').scrollIntoView(...)` — the element existed, its `.tv-card`
wrapper didn't. Guarding a non-null thing says nothing about its parent.

**Lesson.** When someone asks to clear an alert queue, the useful answer is a triage
table, not an empty bell. Three of these six kinds would have been back within 24 hours —
two of them fixable in one line each. **An alert that cries wolf daily trains you to
ignore the one that isn't crying wolf** (the 550-hit burst was a genuine 20-minute
outage, sitting in the same list as a cosmetic off-by-one).

---

## TRAP #134 — a hide-list for garbage LABELS silently dropped legit unconfigured STRATEGIES

**Symptom.** A newly-registered strategy (02.04 "Dessert Range Strangle", `config_key: null` —
backtest-only, not configured yet) refused to appear on the `/registry` page. The registry file
had it (`sr.load()` returned it), the API served it, `runs/index.json` had its metrics — yet the
Family-02 count stayed 2. A second entry (02.02 Gamma Scalp, also `config_key: null`) was missing
the same way.

**Root pattern.** `strategy_registry.html`'s `allStrats()` filters `!regHidden(m.config_key)`, and
`regHidden(ck)` checked membership against `_meta.hidden.identifiers`. That set — built in TRAP #132
to suppress **raw garbage LABELS** in the notify/label layer (`'unknown'`, `'global'`, `'default'`,
`'ema920'`, `''`) — contains an **empty-string `''`** key. A `null` config_key stringifies to `''`,
so `regHidden(null)` matched `''` → **true** → every unconfigured (research/backtest) strategy got
dropped from a hub whose stated purpose is literally "deployed · research · backtest". A control
meant for one layer (labels) leaked into another (which rows exist) — same shape as TRAP #124/#132
(*a check that can't see the layer it's guarding guards nothing about it*), just inverted: here the
check saw **too much**.

**Kahan-kahan kaata.** Only the registry page's row filter. The label layer (`registry.js`'s own
`regHidden`, still `''`-hiding) is correct — an empty *label* should fall back to raw. Two different
questions ("is this a garbage label?" vs "does this strategy exist enough to list?") were answered
by one predicate.

**Permanent guard.** `regHidden()` now returns **false** for a `null`/blank config_key up front — a
missing config_key is a legit unconfigured strategy, not a garbage label. Such rows render with a
`'—'` deploy cell (no config_key → no Paper/Live buttons), so no accidental-deploy footgun. The
`''` identifier stays in the hide-set for the label layer where it belongs.

**Fast detect.** Registered strategy (correct in `strategy_registry.json`, `sr.load()` returns it)
but absent from `/registry` UI → check `config_key`. `null`/blank + `''` in
`_meta.hidden.identifiers` = this. Don't chase caching/restart first: verify the client-side filter,
not just the server payload.

---

## TRAP #135 — a domain field named like buy/sell (`side` = CE vs PE) mapped straight to buy/sell → Points sign flipped on ~39% of trades

**Symptom.** User on the Stats backtest calendar (All-runs combined): a week showed **Σ Points −267.9
but Σ Gross +29,712** — and a single trade `NIFTY 24000PE` had **points −102, gross +6634**. "Ye kaise
possible hai?" One trade, opposite signs, is not a mixed-lot aggregation artifact — it's a bug.

**Root pattern.** `results.js`'s per-trade `side` is `"long"` / `"short"`, but in this schema that means
**option DIRECTION** — `long` = *bought a CE*, `short` = *bought a PE* (both are BUYS; it's the bullish
vs bearish leg). `_ops/backtest_calendar._map_trade` read it as buy/sell: `entry = "SELL" if side ==
"short" else "BUY"`. The Stats "Points" calc is side-dependent (`pts = entry==='BUY' ? exit−entry :
entry−exit`), so every bought-PE (tagged SELL) got its premium-points **sign inverted**. Gross came
from `results.js` already-correct `gross`, so **Gross/Net were always right** — only the derived Points
display (and the BUY/SELL badge) were wrong, on **6606/16873 (39%)** of trades. A field whose *name*
looks like buy/sell ("side") but whose *domain meaning* is CE/PE was mapped 1:1 to buy/sell.

**Fix.** Don't infer buy/sell from a direction field that doesn't encode it. Derive it from a quantity
that DOES — the already-correct signed `gross` vs the price move: a **long** option's gross moves WITH
the premium (`gross × prem_move ≥ 0` → BUY); a **sold** option's gross moves AGAINST it (→ SELL). Now
Points always agrees with Gross in sign **and** the BUY/SELL badge is finally correct (bought PE shows
BUY). Verified 0/16873 sign mismatches (was 6606); the flagged week −267.9 → +554.2.

**Fast detect.** ANY single row where a "points"/"pts" column and its money column (gross/net) have
**opposite signs** = a direction-mapping bug, not an aggregation quirk (aggregates across mixed lot
sizes CAN legitimately diverge in sign — but never a single trade). When a schema field is named like
an action (`side`, `type`, `dir`) confirm its DOMAIN (CE/PE? long/short-direction? buy/sell?) before
mapping it to another axis. Money columns sourced directly (`gross`) stay correct while a *derived*
display (points, badge) silently lies — trust the sourced number, re-derive the display from it.

---

## TRAP #136 — every registry "winner" Sharpe is BS-modeled, NOT real premium; the ATM-BUY fauj collapses on the real lake (theta the model never charged)

**Symptom.** User celebrating a Strategy-Registry full of "WINNER" strategies (Mid-Day ORB
Sharpe 2.37, Long Straddle 3.55, Long Strangle 4.08, Chain-Zone 1.95, all significance PASS
p<0.05). User asked point-blank: *"ye winners real lake premium pe hain ya BS pe?"* — and, on
being shown the collapse, rightly angry that this was never flagged **before** he trusted them,
that the real lake data was there the whole time, and that his own instinct (theta favours the
SELLER — which is why he was selling) had been over-ridden by a "hero" switch to BUYing.

**Root pattern.** Every `runs/<slug>/results.js` number — the `bs|full` pass the registry/hub
display — is **Black-Scholes-modeled option premium + DOM slip**, NOT real premium. BS underprices
the theta an option **buyer** actually bleeds intraday. So any strategy that BUYS premium looks far
better on BS than it trades on real data, and the more legs it buys the worse the gap. The vol
family was caught on the real lake long ago (TRAP #106/#109 — iron-fly/straddle retracted), but the
**directional ATM-BUY fauj (ORB single-leg, chain-zone, debit vertical, ratio backspread) was only
ever DOM-slip-stressed, never repriced on real premium** — that was the hidden hole.

Repricing each winner's OWN trades (same entry/exit times) leg-by-leg on the real held-strike lake
(`real_struct2._px` + real Zerodha charges + `bs.slip_cost_leg`), 2021-26 covered:

| Winner | struct | BS Sharpe | REAL Sharpe | BS net → REAL net |
|---|---|---|---|---|
| Mid-Day ORB | buy ATM | 2.37 | **0.49** | ₹3.9L → ₹82k |
| ORB+Supertrend | buy ATM | 2.06 | **0.46** | ₹2.5L → ₹71k |
| Chain-Zone Long ATM | buy ATM | 1.95 | **0.43** | ₹4.3L → ₹1.2L |
| Long Straddle | buy 2 legs | 3.55 | **−1.47** | ₹4.8L → −₹3.0L |
| Debit Vertical | 1 buy 1 sell | 1.67 | **−0.07** | ₹1.6L → −₹12k |
| Ratio Backspread | sell1 buy2 | 1.55 | **−0.84** | ₹2.0L → −₹1.4L |
| Long Strangle | buy 2 legs | 4.08 | **−1.75** | ₹6.0L → −₹3.0L |

**0 of 7 deployable on real premium.** Single ATM-buys drop to ~0.45 Sharpe (below the ≥1.0 gate);
every multi-leg BUY flips to a LOSS — and the ones with the *highest* BS Sharpe (Straddle 3.55,
Strangle 4.08) are the *worst* on real (−1.47, −1.75), because buying two legs pays double theta,
exactly what BS hides. This VINDICATES the user's sell-side/theta instinct.

**Fix / permanent guard.**
1. **Never present a BS `results.js` Sharpe/net as a deploy signal without the real-lake reprice.**
   Run `scratch/nifty_trend/bs_vs_reallake.py <slug>` (reprices a run's trades on the NIFTY lake)
   BEFORE trusting any 'winner'. The registry/hub number is a *research* figure, not a deployable one.
2. **Flag the BS-vs-real gap up front**, at the moment a strategy is called a "winner" — not when the
   user asks. The user having to ask is the failure.
3. **Caveat honestly:** the reprice keeps the BS run's exit *timing* (premium tp/sl fired on BS
   levels). For SPOT-exit single-leg buys (ORB/chain) the figure is solid; for premium-exit
   multi-leg structures a full real re-backtest could shift the exact number, but the negative
   direction is robust (double-theta is a real, measured effect). NIFTY lake only (BankNifty needs BNF lake).
4. **Direction:** premium-BUYING intraday on NIFTY is a losing game once real theta is charged.
   The honest edge, if any, is on the theta-COLLECTING (short-vol / defined-risk-that-sells) side —
   and that side must itself be real-lake-repriced (SELL variants NOT yet tested here), not assumed.

**Fast-detect.** BS Sharpe ≥ ~1.5 on an option-BUYING strategy with no `real_lake`/`real_struct2`
number next to it = almost certainly inflated. `bs_vs_reallake.py` settles it in seconds.

---

## TRAP #137 — `el.style.display = ''` element ko uske DEFAULT display pe le jaata hai, `<label>` ka default `inline` hai (flex layout tut jaata)

**Symptom.** View-builder modal ki checklist theek khulti thi, par search box me type karte hi
rows **jumble** ho jaati thi — multiple items ek line pe, checkboxes idhar-udhar. User ne 3-4 baar
screenshot bheja "checkbox bad arranged". Isolated harness me EXACT same render-HTML **bilkul
theek** dikhta tha (checkbox left, ek row per line) — isliye bahut der laga diagnose me.

**Root.** Har `<label>` inline `style="display:flex;…"` ke saath render hoti hai (block-level flex
row). Par `statViewFilter()` — jo search pe chalti hai — matched labels ko dikhane ke liye
`l.style.display = '' ` set karta tha. **`style.display = ''` inline display property ko HATA deta
hai**, aur element apne CSS-default display pe chala jaata hai. `<label>` ka HTML default =
`display: inline`. Toh filter chalte hi har matched label `inline` ban jaata → flex khatam → rows
inline flow karke wrap + jumble. Modal PEHLI baar (bina search) theek dikhta tha (inline flex intact);
**sirf type karte hi tootta tha** — isliye screenshot me hamesha broken, harness me hamesha theek.

**Fix.** Show karte waqt intended display **explicitly** set karo, `''` nahi:
`l.style.display = match ? 'flex' : 'none';` (ek word).

**Lesson.** Kisi element ko "wapas dikhane" ke liye `style.display = ''` sirf tab safe hai jab uska
CSS-default display sahi ho. Jahan tumne inline `display:flex`/`grid`/`inline-flex` diya hai (ya
element ka default us layout se alag hai — `<label>`/`<span>`/`<td>` sab inline/table-ish default),
`''` us layout ko chupchaap gira dega. **Restore karte waqt asli display value likho.** Fast-detect:
"toggle/filter/search ke BAAD hi layout tutta hai, pehle theek" = ye pattern. Isolated repro theek
aaye to `style.display=''` wale show/hide code ko shak se dekho.

## TRAP #138 — restart-recovery ne order_store ke open-row ko GALAT key se padha (`trad_sym` jabki dict `sym` deti hai) → NAAM-rahit position jo close hi nahi ho sakti

**Symptom.** Live incident (2026-07-20): webhook (`arschain_MAIN`, NIFTY) ne SHORT li (09:35).
10:00 pe TV ne reversal bheja (short exit + reverse long). **Na short exit hui, na reverse long
bani.** User ne haath se band ki. Log: `[BROKER]  BUY 130 -> REJECTED (no trad_sym to resolve a
Kite symbol from)` — close order ka **symbol khaali** gaya.

**Root.** Do cheezon ka product: (1) dashboard 09:40 pe restart hua (user roz errors pakadne ke
liye restart karta hai). (2) Restart ke baad position `_recover_wh_state()` ne order_store se
rebuild ki, par symbol `p.get("trad_sym")` se padha — **order_store ka open-dict symbol ko `"sym"`
key me deta hai** (`order_store._as_open`: `{"sym": r["trad_sym"], …}`), `trad_sym` key **hoti hi
nahi** → `None` → recovered position ka `opt_trad_sym = ""`. Reversal ne blank-symbol close order
broker ko bheja → REJECT → reversal-exit fail → atomic rule "purani exit fail to nayi entry nahi"
ne long bhi rok di. **`range_trader` / `01_rsi_v1` / `universe_trader` teeno recovery `p.get("sym")`
sahi padhti hain — webhook akela drift kar gaya** (16-July TRAP #128 ka direction-fix isi function me
tha, tab `sym` ka pattern bagal ki file se copy nahi hua). Ye PRE-MORTEM shape #4 (duplicate logic,
ek jagah alag se fix).

**Fix.** (a) Recovery ab `p.get("sym")` padhti hai + `dhan_master.get_trad_sym_for_sec_id(sec_id)`
backstop + symbol na milne pe position recover hi **nahi** karti (naam-rahit ghost nahi banati) +
`notify.error`. (b) `_do_exit` HARD GUARD: blank symbol → pehle sec_id se resolve, warna order
place hi **mat karo** — alert + skip (pos_monitor SL/EOD phir bhi protect karta). (c) dedup ab
**fail hone pe** id ko "seen" mark nahi karta → TV ka identical retry dobara try kar sakta hai
(pehle #1 fail + #2 dedup-skip = permanent drop). (d) `_lock` → `RLock` (handle_signal lock hold
karke `_do_entry` ko bulata hai jo ghost-clear branch me `with _lock:` dobara leta — plain Lock
wahan poora webhook handler deadlock kar deta). (e) **naya `architecture_audit` check #10
`RECOVER-FIELD`**: order_store `.get("open"/"closed"/"details")` row pe `trad_sym` padhna = FAIL.

**Guard ne turant 2 aur dead-reads pakde** (`broker_sync.py:139`, `webhook_executor` flat-check) —
safe `sym`-first the par misleading dead `trad_sym` fallback; `sym`-only kar diye. Regression test:
`TEST/test_wh_recover_symbol.py` (order_store ka asli `_net_rows` + asli `_recover_wh_state`/`_do_exit`/
`handle_signal` chalata hai, koi DB write nahi; 12/12 pass).

**Lesson.** Jab bhi state ko order_store se rebuild karo, uske **emitted open-dict schema** se field
padho (`sym`, `symbol`, `sec_id`, `entry`, `qty`, `entry_price`, `tags` …) — raw DB column naam
(`trad_sym`, `side`, `price`, `status`) mat maano; wo sirf `order_store.query()` ke raw rows me hote
hain. Ek reliable alternate key (yahan `sec_id`) ho to usse **self-heal** karo, kisi ek field pe
mat atko. Aur money-path pe: blank/adhura identifier lekar broker ko order **kabhi mat bhejo** —
resolve karo ya ruk kar alert karo. Fast-detect: `no trad_sym` / blank-symbol reject log, ya
recovered position jo exit pe REJECT hoti hai.

## TRAP #139 — haath se band karne ke baad: phantom double-count + strategy ka dobara position banana

**Symptom.** User ne (webhook exit fail hone pe) arschain SHORT 130 ko haath se 2 buy (65+65) se
band kiya — Kite pe flat ho gaya, par app ne **2 jhoothi open long** dikhayi. Alag se, VRP condor
manually square off kiya to strategy ne **dobara position bana di**.

**Root (do alag jad, ek family).**
- **Phantom double-count:** `broker_sync` ne short band karne ka exit **ek 130-qty row** me record
  kiya; par asli fills **2×65** the. `reconcile_manual_trades` fill ko exact `(sym,side,qty,price)`
  **signature** se match karta tha — `65 ≠ 130` → miss → dono 65-65 ko "manual" samajh ke daal diya
  → **wahi 130 do baar gina** → 2 phantom long. Saboot: exit-row aur manual-row dono ka same Kite
  trade-id (`1822718`). Yani reconcile **fill ki shakl** milaता tha, broker ke **asli net** se nahi.
- **Re-entry:** har automated path (strategy ka agla entry-signal, `pos_monitor` ka profit-target
  squareoff) manual close ko bilkul SL-hit jaisa treat karta tha — "position gayi, fresh socho." User
  ka *intent* ("mujhe ISME nahi rehna") kahin yaad nahi tha → strategy ne dobara enter kiya, aur
  `pos_monitor` ne already-band paper position ko dobara squareoff kar ke **4 phantom opposite legs**
  bana diye (VRP mess).

**Deeper "kyun baar-baar":** system **intraday-first + single-broker-first + single-path-first**
bana tha. Manual (insaan) close + Kite-as-real-broker + positional-overnight — teen nayi haqeeqat
in assumptions ke **seams** me todti hain. Reconcile fill-by-fill tha; state kai jagah alag-alag
rebuild hoti hai (4 recovery fns + netting + reconcile + pos_monitor); insaan ke intent ki koi yaad
nahi thi; din-scoped netting positional ko phantom bana deti hai.

**Fix (jad se).**
- **Net-aware reconcile:** ab fill tabhi record hota hai jab woh us contract ka book-net **broker ke
  asli net (`positions()`) ki taraf** le jaaye. Broker flat + book flat → kuch nahi. + trade-id
  cross-path dedup (jo fill kisi row ke `correlation_id`/`MANUAL_TID_` me hai, dobara nahi).
- **Manual-close veto:** user band kare (app button ya `broker_sync` ka external-close detect) →
  `risk_gate.mark_manual_closed(strategy, symbol)` → `strategy_safety.gate_entry()` **pehla gate**
  block karta hai (har strategy+webhook wahin se guzarti). Strategy ka apna SL/target exit isse mark
  nahi karta; din-scoped + `clear_manual_veto()` se hataya ja sakta.

**Guard (taaki dobara na aaye).** 3 replay-test (`test_reconcile_net_aware` 7/7 = incident pe 0
phantom + asli manual trade phir bhi record + idempotent; `test_manual_close_veto` 12/12 = asli
`gate_entry` block; `test_wh_recover_symbol` 16/16). Poora `architecture_audit` 0 FAIL. Cleanup:
WAL-safe backup (`trades.db.bak.<ts>`) → guarded delete (id + signature match) → dono contract flat
verified → VRP disk-state clear. Poora "kyun + kya kiya": **ADR-009**.

**Lesson.** (1) Reconcile hamesha **broker ke net se milao**, fill-ki-shakl se nahi — ek sauda alag
qty/rows me aa sakta hai. (2) Insaan ke **manual close ko yaad rakho** — woh SL-hit se alag *intent*
hai; automated re-entry usse override na kare. (3) Koi bhi state-rebuild/exit path banao to poochho:
"manual close, Kite-real-broker, aur positional-overnight — teeno pe ye sahi rahega?" Fast-detect:
book me open position par broker flat (`positions()` se check); ek hi trade-id do rows me; ya
strategy `pos=held` par order_store flat.

## TRAP #140 — VRP condor PE legs went ITM (offset sign double-negated get_option_contract's built-in PE inversion)
**Symptom:** Live `vrp_condor_weekly_trader.py` entered a condor whose PUT legs were ITM (above spot),
not OTM — the recorded/captured actual strikes exposed it; the CALL side was fine, so the structure
was one-sided/wrong risk profile. The validated backtest (`vrp_ungated_backtest.backtest`, iron_condor)
builds a PROPER OTM condor (sell K−short_off, buy K−(short_off+wing) on the put side).
**Root:** `dhan_master.get_option_contract()` ALREADY inverts the offset for PE
(`target_idx = atm_idx − offset`, so a POSITIVE offset = lower strike = OTM put). The live code passed
`_resolve(sym, spot, "PE", -body)` / `-(body+wing)` — the extra minus double-negated the built-in
inversion → `atm_idx + body` → ITM put. CE was correct (`+body` → OTM call) so only the put side broke.
**Fix:** PE offsets must be POSITIVE (`+body`, `+(body+wing)`) — get_option_contract handles direction.
Numeric proof (spot 24218): sell 24050 / buy 23800 puts, sell 24350 / buy 24600 calls — matching
iron_condor's `K∓short_off·STEP` / `∓(short_off+wing)·STEP` exactly.
**Guard:** when a helper already encodes direction/sign (like PE-OTM inversion), never re-apply the sign
at the call site. For any option-leg strike selection, cross-check against the strategy's own backtest
leg math (single source of the validated structure), not intuition about "puts go below."
**Rule 10:** this makes live conform to the already-validated structure → NO re-backtest needed.

## TRAP #141 — per-day `trades_for(date)` mis-nets overnight/positional positions (phantom P&L)
**Symptom:** Stats/Real-vs-BS showed VRP Overnight Condor as a −6,168 LOSS on 2026-07-20, but it was
actually a +4,934 PROFIT (Orders "Today's Peak" correct — user caught the mismatch). Every other
strategy reconciled; only the positional one diverged.
**Root:** `order_store.trades_for(date)` filters legs by placement date (`WHERE date=d`). An overnight
position's ENTRY leg is on an earlier day and its EXIT leg on `date`, so a single day's bucket can't
pair them. On the exit day the close leg gets FIFO-netted against a SAME-DAY re-open on the same strike
→ phantom round-trip (24500-PE: close-BUY 354.4 paired with a fresh short SELL 259.7 = −6,155) + a
stranded close leg shown as a bogus "open". `calendar-summary` + `bs_shadow` both consumed per-day
`trades_for` → wrong for every positional strategy (intraday safe: entry_date==exit_date).
**Fix:** net across a lookback RANGE via the existing `order_store.trades_for_range` (pairs cross-day
legs correctly — its own docstring says so) and attribute each completed round-trip to its EXIT date
(day P&L is realized). Applied to `api_orders_calendar_summary` (400-day lookback + exit-date bucket)
and `_ops/bs_shadow.build_day` (range-net, keep exit_date==date). Data-verified: day-20 gross 8054 ==
Orders Gross TOTAL, VRP real_net +4,612.7 == Orders "VRP Net +4,613".
**Guard:** any per-day P&L consumer MUST range-net + bucket by exit date for positional/overnight
positions — never per-day `trades_for` for a multi-day hold. **BS-across-days:** repricing an overnight
leg needs the ENTRY-day intraday spot; if that day has no spot (weekend/holiday) BS is honestly "n/a".

## TRAP #142 — strategy `is_market_open()` was time-of-day only → VRP condor rolled on a SATURDAY
**Symptom:** VRP paper condor placed 8 legs on Saturday 2026-07-18 (market closed) — the ONLY strategy
to trade that weekend. Root of TRAP #141's confusion: the Saturday close+reopen at stale prices split
the real Fri→Mon hold, so netting attributed the Monday exit to a Saturday "entry" and BS broke (no
weekend spot). User: "entry Friday 17th thi, 18th nahi."
**Root:** `is_market_open()` (in vrp_condor_trader.py + weekly) checked only `hour/minute`, not the day.
On Saturday 15:10 it returned True → the daily 15:10 roll fired: EXITed Friday's held condor
(`held_new_session` = entry_date != today) and re-ENTERed a fresh one.
**Fix (shared + default):** new `_core/market_calendar.py` = single source of truth — NSE holiday list
(ported from CODE7 `MARKET_HOLIDAYS`, update annually) + `is_trading_day` (weekday AND not holiday) +
`is_market_open(now, open_hm, close_hm)`. Both VRP traders delegate to it (weekend + holiday). **Default
enforcement:** `execution_gateway.execute_signal` blocks ENTRIES on any non-trading day
(`skipped:market_closed`, mode!=backtest, fail-open) so EVERY current + future strategy gets it without
remembering — loop-gate still covers exits/rolls. `NEW_STRATEGY_CHECKLIST.md` rule #12.
**Data hygiene:** the already-placed Saturday legs (ids 1455-1462) were phantom historical records; marked
`status='cancelled'` (reversible, `_dead_filtered` excludes; DB backup taken) so 18th clears and the
Fri→Mon hold nets as one round-trip (+3,708, BS now reprices off Friday's spot). Diff proved only 18/20
changed, +₹91 phantom-roll cost removed, no ripple.
**Guard:** a market-open check that ignores the weekday/holiday is a latent "trades on a closed market"
bug for ANY daily-acting strategy — gate every trader's loop on `market_calendar`, not a bare time window.
