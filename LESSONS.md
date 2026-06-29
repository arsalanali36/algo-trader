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

---

## How to extend this file

- Naya recurring-trap milte hi (ya purana lautte hi) ek `TRAP #N` add karo — **problem se index,
  date se nahi.** Date-detail `ARCHITECTURE_LOG.md` me rehne do; yahan sirf **pattern + permanent
  guard + fast-detect.**
- Agar ek guard code me bhi daal sakte ho (central chokepoint), to woh memory/doc se behtar hai —
  doc bhula ja sakta hai, code-guard nahi. (Jaise TRAP #1 ka `order_store.record` tripwire.)
