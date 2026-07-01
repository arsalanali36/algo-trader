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

Sabse aasan tareeka RMS-safe hone ka: **saare orders `smart_order.execute()` ke through bhejo** —
wo order_store recording khud kar deta hai (+ bahut saare aur guards, neeche).

---

## ✅ NON-NEGOTIABLE CHECKLIST (har naye trader mein)

| # | Rule | Kyun (TRAP) |
|---|------|-------------|
| 1 | **Order `smart_order.execute()` se bhejo** (raw `requests.post` Dhan/Kite ko mat maaro) | Isse milta hai: order_store recording, rate-limit, async fill-confirm, ₹0-guard, correct Kite symbol. TRAP #14/#15/#26 |
| 2 | **Entry se pehle `strategy_safety.gate_entry(...)`** — `ok=False` aaye to skip, `gated_qty` use karo | capital/drawdown/concentration/broker-funds — ek call. TRAP #15 |
| 3 | **Exit se PEHLE fresh broker `positions()` flat-check** (live only) | Manual/broker-side close ke baad apna exit = phantom OPPOSITE position (1 trade → 3 + tax). TRAP #62/#73 |
| 4 | **Premium na mile to entry SKIP** — ₹0 kabhi record mat karo | ₹0 fill P&L corrupt karta, RMS breaker trip. TRAP #1 |
| 5 | **Naked option SELL hai to** `strategy_safety.compute_hedge_target(...)` se hedge | Hedge config RMS Risk tab se aata, per-strategy dobara mat likho. TRAP #15 |
| 6 | **3:15 PM force-exit + 3:15 ke baad no-entry** har strategy mein | Intraday-only house rule, overnight gap risk zero. |
| 7 | **Max 2 trades/day** (ya config se), din-reset ke saath | House rule. |
| 8 | **`dhan_feed.start(creds, [...])` startup pe** (agar liquidity filter / live LTP chahiye) | Sirf `add()` no-op hai jab tak feed thread na chale. TRAP #65 |
| 9 | **Config `symbols` ko parse karo** — string bhi ho sakta hai (comma), list bhi | Raw string pe `for sym in ...` = character-by-character iterate, silently 0 symbols. TRAP #16 |
| 10 | **Dashboard `STRATEGIES` dict mein sahi `script` + `grep` map karo** | Galat map = tumhari nayi file launch hi nahi hogi (rsi_trader.py vs 01_rsi_v1.py wala trap). |

---

## 📋 COPY-PASTE — ENTRY (RMS-gated, order_store-recorded)

```python
import smart_order, risk_gate, strategy_safety
from brokers import get_broker

mode   = "paper" if paper_mode else "live"     # ya cfg.get("mode")
bname  = (cfg.get("broker") or risk_gate.default_broker() or "dhan")
broker = get_broker(bname)

# 1) premium/marketable price — na mile to SKIP (₹0 mat record karo — TRAP #1)
est_price, _src = smart_order.marketable_price(order_side, sec_id, seg, broker)
if not est_price or est_price <= 0:
    log(f"{sym} — no premium (rate-limit?) — entry skipped this cycle")
    continue

# 2) RMS gate — capital/drawdown/concentration/broker-funds (TRAP #15)
gate_ok, gated_qty, reason = strategy_safety.gate_entry(
    strategy_id, sym, lots, lot_size, est_price, side=order_side,
    sec_id=sec_id, seg=seg, mode=mode, broker=broker, log=log)
if not gate_ok:
    log(f"[SKIP] {sym} — {reason}")
    continue
if gated_qty and gated_qty != qty:
    qty = gated_qty          # sized-down — use returned qty

# 3) default SL/TP tags so pos_monitor protects it
try:    sl_tags = risk_gate.default_instrument_sl_tags(strategy_id, sym)
except Exception: sl_tags = []

# 4) place via smart_order → order_store recording automatic (RMS sees it)
res = smart_order.execute(order_side, sym, sec_id, seg, qty, trad_sym, mode, broker,
                          log=log, tag="MYSTRAT", source="strategy", strategy=strategy_id,
                          instrument="options", broker_name=bname, extra_tags=sl_tags)
if res.get("ok"):
    # update your in-memory state ONLY on success
    ...
```

## 📋 COPY-PASTE — EXIT (manual-close flat-guard, order_store-recorded)

```python
import smart_order, broker_sync
from brokers import get_broker

mode   = "paper" if paper_mode else "live"
bname  = (cfg.get("broker") or "dhan")
broker = get_broker(bname)

# Manual-close phantom guard (LIVE only) — fresh broker check, NOT the 30s cache
# (broker_sync's cache is per-process; empty inside a strategy's own process).
if not paper_mode:
    try:
        bpos = broker.positions()
        if bpos is not None and broker_sync._check_flat(bname, bpos, trad_sym, str(sec_id)):
            log(f"[FLAT-CHECK] {trad_sym} already flat at broker (manual close?) "
                f"— skipping exit to avoid a phantom opposite position; clearing state")
            # clear in-memory state, do NOT place an order
            ...
            continue
    except Exception as e:
        log(f"[FLAT-CHECK] failed ({e}) — proceeding with exit (fail-open)")

smart_order.execute(exit_side, sym, sec_id, seg, qty, trad_sym, mode, broker,
                    log=log, tag="MYSTRAT", source="strategy", strategy=strategy_id,
                    instrument="options", broker_name=bname, is_exit=True)
# is_exit=True → 4 order-chase rounds with escalating LIMIT (TRAP #64). Entry = 2.
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
