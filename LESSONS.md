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

