# 🧭 NEW STRATEGY CHECKLIST — `_TRADERS/` mein koi bhi live trader likhne se PEHLE padho

> **Yeh file ka maqsad:** har baar nayi strategy likhte waqt wahi purani galtiyan dobara na
> hon. Neeche har rule ke saath uska **kyun** (LESSONS.md TRAP #) diya hai — taaki pata rahe
> ki yeh niyam kis asli nuksaan se aaya. **Copy-paste templates** bhi hain — naya code inhi se
> shuru karo, scratch se RMS/exit/parsing dobara mat likho.
>
> Scope: yeh `_TRADERS/*.py` (asli live/paper trader processes) ke liye hai. Script-Library ke
> user-pasted strategies (DSL/Python `evaluate`/`backtest`) ke liye `strategies/SCRIPT_CONTRACT.md`
> dekho — wo alag cheez hai.

---

## ☠️ THE ONE RULE THAT MATTERS MOST — RMS-blind strategy = live mat chalao

**RMS (`risk_gate.py` + `pos_monitor_loop`) sirf wahi positions dekhta hai jo `order_store` mein
likhi hain.** Agar tumhari strategy seedha broker pe order daalti hai (raw `requests.post`) aur
`order_store` mein record nahi karti, to:

- ❌ Koi auto **SL / Target / 3:15 EOD-squareoff** nahi lagega (`pos_monitor` ko position dikhti hi nahi)
- ❌ **Daily-loss breaker** (RMS supreme) us position ko count nahi karega
- ❌ Dashboard **Orders & P&L** mein kuch nahi dikhega
- ❌ **Manual-close phantom guard** kaam nahi karega

Yeh koi theory nahi — `01_rsi_v1.py` (rsi_v1) exactly isi wajah se mahino tak RMS-blind chala,
aur `nifty_ema_trader.py` har loop crash karta raha, dono kisi ne notice nahi kiye kyunki paper
mein "chal rahe the". **Live jaane se pehle order_store recording ZAROORI hai.**

Sabse aasan tareeka RMS-safe hone ka (2026-07-07 se, Task 3/ADR-001): **saare orders
`execution_gateway.execute_signal()` / `execute_exit()` ke through bhejo** — ye EK call
mein deta hai: no-premium skip → RMS gate → default SL tags → smart_order.execute
(order_store recording + saare guards). Neeche copy-paste template hai.

---

## ✅ NON-NEGOTIABLE CHECKLIST (har naye trader mein)

| # | Rule | Kyun (TRAP) |
|---|------|-------------|
| 1 | **Entry `execution_gateway.execute_signal()` se, exit `execute_exit()` se** (raw `requests.post`/`broker.place_order()` kabhi nahi — pre-commit hook block karega) | Gateway ke andar: no-premium skip (TRAP #1), gate_entry RMS (TRAP #15), default SL tags, smart_order (order_store recording, rate-limit, fill-confirm, correct Kite symbol — TRAP #14/#26), pre-exit flat-check (TRAP #62/#73), Task-5 per-strategy isolation. ADR-001 |
| 2 | **RMS gate/flat-check apne haath se dobara MAT likho** — gateway mein already hai | Duplicate = drift = TRAP #15/#75 wapas. Sirf hedge (protective) leg pe `gate=False` pass karo |
| 3 | **Gateway ka RETURNED `qty` state mein rakho** (size-down ho sakta hai), aur exit pe `status=="skipped_flat"` aaye to state saaf karo — order nahi gaya hota | Sized qty ignore = capital breach; skipped_flat ignore = phantom position state |
| 4 | **Premium na mile to entry SKIP** — ₹0 kabhi record mat karo | ₹0 fill P&L corrupt karta, RMS breaker trip. TRAP #1 |
| 5 | **Naked option SELL hai to** `strategy_safety.compute_hedge_target(...)` se hedge | Hedge config RMS Risk tab se aata, per-strategy dobara mat likho. TRAP #15 |
| 6 | **3:15 PM force-exit + 3:15 ke baad no-entry** har strategy mein | Intraday-only house rule, overnight gap risk zero. |
| 7 | **Max 2 trades/day** (ya config se), din-reset ke saath | House rule. |
| 8 | **`dhan_feed.start(creds, [...])` startup pe** (agar liquidity filter / live LTP chahiye) | Sirf `add()` no-op hai jab tak feed thread na chale. TRAP #65 |
| 9 | **Config `symbols` ko parse karo** — string bhi ho sakta hai (comma), list bhi | Raw string pe `for sym in ...` = character-by-character iterate, silently 0 symbols. TRAP #16 |
| 10 | **Dashboard `STRATEGIES` dict mein sahi `script` + `grep` map karo** | Galat map = tumhari nayi file launch hi nahi hogi (rsi_trader.py vs 01_rsi_v1.py wala trap). |

---

## 📋 COPY-PASTE — ENTRY (Task 3/ADR-001: EK gateway call, sab andar)

```python
import execution_gateway as gw

mode = "paper" if paper_mode else "live"     # ya cfg.get("mode")

# Andar automatic: marketable_price (no-premium → skipped, TRAP #1) →
# strategy_safety.gate_entry (RMS: capital/drawdown/concentration/liquidity/
# max-premium/broker-funds, fail-closed, TRAP #15) → RMS default SL tags →
# smart_order.execute (order_store recording, marketable-limit, fill-confirm).
res = gw.execute_signal(strategy_id, sym, order_side, lots, lot_size,
                        sec_id, trad_sym, seg=seg, mode=mode,
                        broker_name=cfg.get("broker"), tag="MYSTRAT",
                        instrument="options", log=log)
if res["ok"]:
    qty = res["qty"]          # ⚠️ RETURNED qty — size-down ho sakta hai
    # update your in-memory state ONLY on success
    ...
else:
    log(f"[SKIP] {sym} — {res['status']}: {res['reason']}")
    # status: "blocked" (RMS) | "skipped" (no premium) | "rejected"/"failed"

# Naked SELL + hedge? Hedge BUY (protective leg) pe gate=False:
# gw.execute_signal(..., side="BUY", gate=False, ...)  — RMS-block se
# hedge rukna = naked SELL akela reh jaana. compute_hedge_target() se contract lo.
```

## 📋 COPY-PASTE — EXIT (flat-guard + exit-reason, EK call)

```python
import execution_gateway as gw

res = gw.execute_exit(strategy_id, sym, sec_id, trad_sym, qty,
                      entry_side="BUY",            # ya exit_side= seedha
                      seg=seg, mode=mode, broker_name=cfg.get("broker"),
                      tag="MYSTRAT", reason="MYSTRAT_SIGNAL_EXIT",  # Exit Reason column
                      log=log)
# status "skipped_flat" = broker pe already flat tha (manual close/SL) — order
# NAHI gaya, bas apna in-memory state saaf karo (TRAP #62/#73 phantom guard).
# Andar: fresh is_flat_fresh (strategy-aware — doosri strategy ka same-contract
# lot tumhe confuse nahi karta, Task 5) + smart_order is_exit=True
# (4 chase rounds escalating LIMIT, TRAP #64; entry = 2 rounds).
```

---

## 🐍 PYTHON GOTCHAS jo yahan bite kar chuke hain

- **Default argument EAGERLY evaluate hota hai.** `tc.get("symbols", list(SYMBOLS.keys()))` — agar
  `SYMBOLS` list hai to `.keys()` **har baar** crash karega, chahe "symbols" key ho ya na ho.
  (EMA trader har loop `'list' object has no attribute 'keys'` — isi wajah se.)
  ✅ `sym_list = tc.get("symbols") or list(SYMBOLS)` phir string-parse.
- **`symbols` list ya comma-string dono ho sakta** hai `nifty_config.json` mein:
  ```python
  sym_list = tc.get("symbols") or DEFAULT_SYMBOLS
  if isinstance(sym_list, str):
      import re
      sym_list = [s.strip().upper() for s in re.split(r"[,\s]+", sym_list) if s.strip()]
  ```
- **`get_option_contract()` 3 values return karta** hai `(sec_id, trad_sym, lot_size)` — 2 mein
  unpack karoge to `ValueError` (aksar `except: pass` mein chhup jaata, feature silently dead).
  Lot size hamesha yahin se lo, kabhi hardcode nahi. (TRAP: lot-size assume karna.)
- **Restart open positions ko orphan kar deta** hai — startup pe `_state` `position:None` ho jaata,
  aur startup-EXIT guard use "kuch open nahi" samajhta. `main()` startup pe
  `_recover_state_from_order_store()` chalao (range_trader ka pattern). TRAP #28.

---

## ⏰ INTRADAY HOUSE RULES (har strategy, koi exception nahi)

- **3:15 PM (15:15 IST)** pe saari positions force-exit; 15:15 ke baad koi nayi entry nahi.
- **Max 2 trades/day** default (config se badhaya ja sakta), har din reset.
- Overnight hold **kabhi nahi** — gap-up/down risk zero rakhna hai.
- Expiry day: earlier EOD (2:55), ITM immediate squareoff, 2:00 ke baad no entry — `risk_gate`
  ke helpers use karo (`is_expiry_day`, `option_is_itm`). TRAP #36.

---

## 🔌 DASHBOARD REGISTRATION (warna file launch hi nahi hogi)

`trader_dashboard.py` ke `STRATEGIES` dict mein entry add karo:
```python
"mystrat": {"script": str(TRADERS_DIR / "my_trader.py"), "log": ..., "cfg": TC_FILE,
            "grep": "my_trader"},   # grep = process ko pehchaanne ka token — file naam se match kare
```
⚠️ **Trap:** `grep` value asli chalne wali file se match honi chahiye. `rsi`/`rsi_v1` dono
`01_rsi_v1.py` ko launch karte the par `rsi` ka grep `"rsi_trader"` tha — is mismatch se
`rsi_trader.py` ke saare fixes dead code ban gaye (kabhi chale hi nahi). Map do baar check karo.

---

## 🧪 LIVE JAANE SE PEHLE — verification (paper-first, hamesha)

1. `python -c "import ast; ast.parse(open('_TRADERS/my_trader.py').read())"` — syntax.
2. Module import test (top-level imports + typos pakadta hai).
3. **Paper mode mein chalao** — dashboard **Orders & P&L** mein entries/exits dikhni CHAHIYE
   (agar nahi dikh rahi → order_store recording nahi ho rahi → RMS-blind, live MAT chalao).
4. Confirm: SL/Target/3:15-EOD paper positions pe lag raha (pos_monitor).
5. Ek din paper theek chale → **tabhi** live, chhoti qty se.
6. Live jaane ke baad pehle order pe Zerodha/Dhan app mein khud verify karo.

---

## 🔗 RELATED TRAP INDEX (detail LESSONS.md mein)

| TRAP | Baat |
|------|------|
| #1  | ₹0-price fill = P&L corruption; premium na mile to skip |
| #15 | `strategy_safety.gate_entry` + `compute_hedge_target` — ek jagah, dohrana nahi |
| #16 | `symbols` comma-string char-by-char iterate = 0 symbols |
| #26/#30 | Dhan order body mein `disclosedQuantity`/`afterMarketOrder` chahiye |
| #28 | Restart → open positions orphan; `_recover_state_from_order_store()` |
| #34/#35 | Hedge BUY = NRML; live P&L sirf confirmed fill ke baad record |
| #36 | Expiry-day guards (early EOD, ITM, no-entry-after-2PM) |
| #62/#73 | Manual-close phantom BUY — exit se pehle fresh broker flat-check |
| #63/#64 | Provisional order_store row on accept; order-chasing unfilled limits |
| #65 | `dhan_feed.start()` na call karne se liquidity filter andha |
| #72 | Trailing-lock indentation → SL/TP/EOD silently band (block-wrap gotcha) |

---

*Har naya trap jo bite kare → yahan ek row add karo + LESSONS.md mein detail. Goal: ek galti do
baar na ho.*
