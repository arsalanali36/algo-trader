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

**Fix (2026-06-28):** `webhook_executor._do_entry()` now reads `risk_gate.hedge_config(strat)` —
same per-strategy-overrides-global table as `range_trader`, so the ONE Risk-tab row per strategy
(including `webhook_v1`) is the single source of truth. The old per-webhook `hedge_offset_strikes`
field, if still set in a webhook's own `nifty_config.json` block, is honored as a legacy override
of the Risk-tab's min-strikes floor (backward compat), but Max Premium always comes from the Risk
tab since the legacy field never had that knob. `smart_order.place_hedge_if_configured()` gained
`max_premium_rs` support too — walks further OTM using `broker.quote()` (broker-agnostic: works
whether the hedge's BUY leg routes through Dhan or Kite) until premium ≤ the cap, same
floor-vs-cheaper-insurance logic as `range_trader.resolve_hedge_contract()`.

**Permanent guard:** when a config knob is meant to be shared across multiple strategies, put it
in ONE place (`risk_gate.py` + the Risk tab) from the start — a strategy-local field for something
that "should probably apply everywhere" is how this split happened.

**Fast detect:** if a strategy's hedge "isn't working" but `hedge_offset_strikes`/`hedge_max_premium_rs`
IS set in the Risk tab for that strategy id, check whether that code path actually calls
`risk_gate.hedge_config()` or still reads its own local config key.

---

## How to extend this file

- Naya recurring-trap milte hi (ya purana lautte hi) ek `TRAP #N` add karo — **problem se index,
  date se nahi.** Date-detail `ARCHITECTURE_LOG.md` me rehne do; yahan sirf **pattern + permanent
  guard + fast-detect.**
- Agar ek guard code me bhi daal sakte ho (central chokepoint), to woh memory/doc se behtar hai —
  doc bhula ja sakta hai, code-guard nahi. (Jaise TRAP #1 ka `order_store.record` tripwire.)
