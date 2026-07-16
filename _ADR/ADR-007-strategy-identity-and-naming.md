# ADR-007: Strategy identity — ID = idea, baaki sab property

## Status: Decided (2026-07-16)

## Context

20 strategy configs chal rahe the. User (jisne khud sab banaye) sirf **4**
pehchanta tha:

- Chain-Zone (`chainzone_v1`) — jiska proper backtest hua
- Ars Chain live (`ARS_CHAIN_V1`) — 1m, NIFTY+BNF
- Ars Chain paper (`ARS_CHAIN_V1_PAPER`) — wahi logic, stocks pe
- "webhook wala koi" — TradingView se order

Baaki ke baare mein: *"mera unse koi lena dena nahi, wo sab duplicate honge ya
pata nahi kya kar rahe"*. Aur `range_v1` pe seedha: *"ye pata nahi kya hai"*.

Ye confusion 2026-07-16 ko asli paisa kha gaya. Do cheezein saath hui:

**1. `range_v1` = "Range Breakout"?** Nahi. Config diff se: wahi
`range_trader.py`, wahi params (`atr_exit`, `fresh_zone_only`,
`max_candle_size 25`, `max_trades_per_symbol 2`, `strike_offset 0`). Farq sirf
timeframe (5m vs 1m) aur symbols — aur uski symbol list **04.01 + 04.02 ka exact
UNION thi** (25 = 2 index + 23 stocks), ek bhi extra nahi. Copy thi, naya idea
nahi. (Uske booleans JSON `true` hain jabki baaki dono me string `"true"` — yaani
UI se nahi, kisi aur code path se likhi gayi thi.)

Usne ek naam liya jo role nahi batata, aur **ek mahine chhupi rahi**.

**2. `arschain_MAIN` registry me tha hi nahi.** Ye LIVE strategy hai. Registry
use nahi jaanti thi → RMS table ke **"Other"** bucket me render hui → uska
per-strategy SL override chupchaap **global pe fall back** ho gaya → poora din
**₹1,000/lot** stop pe chali, jabki 14-July ka rollout **₹2,500/lot** deta
(wo rollout `webhook_v1` pe land hua — generic config — live strategy pe nahi).

User ne RMS me `04.01 Ars Chain (live)` pe values set ki thi. Set hui bhi thi —
**galat Ars Chain pe**. Screenshot me dono rows saath dikhti hain: `04.01` pe
"Aggressive — Per-lot…", `Arschain Main` pe "global (Aggressive)".

`label()` ka apna comment isko seal karta hai: *"Falls back to the raw alias if
unknown (so a brand-new strategy not yet registered still shows something)"* —
display-level fail-open. Isliye kisi ko dikha hi nahi.

### Naming teen orthogonal cheezein ek string me thoos rahi thi

`ARS_CHAIN_V1_PAPER` = strategy + version + **mode**. Mode already ek runtime
field hai. Do jagah sach rakhne ka nateeja:

| key | naam kya kehta | config kya kehta |
|---|---|---|
| `rsi_v1` | kuch nahi | `mode: live`, aur 43 live orders |
| `rsi_v1_PAPER` | paper | `mode: paper` |

Aur `order_store.strategy` ek free-text field hai — koi validation nahi. Pichle
mahine usme ye likha gaya: `''` (khaali — **15 live orders**), `unknown`
(**13 live**), `default` (**12 live**), `ema920` (config key hai hi nahi), aur ek
poora description string (`"52_Week_Breakout | Price closing above 52-week EMA…"`).

## Decision

### 1. ID idea ko pehchanta hai. Mode / transport / version / symbol = property.

```
ID          04.04              permanent — registry
name        "<Family> - <Role>"  role batata hai, mode nahi
config_key  arschain_MAIN      order_store + _risk.per_strategy ki key — KABHI mat badlo
aliases     [...]              purane naam — hamesha resolve honge
role        reference | mirror | canary | standalone
transport   pine | python      naam me nahi
mirrors     04.04              kaun kiska twin hai
never_live  true               intent jo naam enforce nahi kar sakta
mode        live | paper       config — NAAM ME KABHI NAHI
```

**Naam me banned:** `_PAPER` / `_LIVE` (mode runtime hai aur jhoot bolta hai),
`_V1` (version script library ka kaam hai), `MAIN` / `default` (kuch nahi batate),
mixed case.

**Instance token wahi jo farq batata ho** — `nifty`, `stocks`, `tv`, `all-5m`.
Kabhi nahi: `main`, `v1`, `paper`.

### 2. Family = idea, transport nahi.

Family `07 External` transport se bani thi — **yahi original galti hai**.
`arschain_MAIN` ki logic Ars Chain hai (wahi `range_chain.pine`, jiska Python
conversion `range_trader.py` hai) — bas chalti TV pe hai. Isliye wo **04.04** hai,
07.02 nahi. `07` ab sirf webhook **engine** ke generic config ke liye.

Family `04` ka naam bhi `Range` → **`Ars chain`**. Family ka naam hi wajah tha ki
`range_v1` wahan fit lagti thi.

### 3. Family 04 — final

| ID | Naam | config_key | Role | Anjaam |
|---|---|---|---|---|
| `04.01` | Ars chain - Canary (NIFTY-BNF) | `ARS_CHAIN_V1` | canary | retire |
| `04.02` | Ars chain - Canary (stocks) | `ARS_CHAIN_V1_PAPER` | canary, `never_live` | retire |
| `04.03` | **Ars chain - Pine2Python** | `range_v1` | **mirror → 04.04** | **bachega** |
| `04.04` | Ars chain - DirectWebhook | `arschain_MAIN` | reference, LIVE | retire |

### 4. Rename mat karo — alias layer poora karo

`order_store` me hazaaron rows `ARS_CHAIN_V1_PAPER` ke saath hain. Rename =
history + P&L attribution + `tsl_state` keys tootengi. **Sirf display naam
badla; ek bhi `config_key` nahi.** `resolve()` ab `aliases` bhi match karta hai,
to purana har naam hamesha resolve hoga (`"Range Breakout"` → `04.03`).

### 5. Canary strategy nahi hai — usko strategy mat samjho

User ki apni definition: *"ye bahut saare order fire karti rehti hai, alag alag
edge case dikhte rehte hai, jo agar ham focus kar ke karen to kabhi samne hi
nahi aayenge."*

Ye empirically sach hai — **TRAP #76, #83, #84 sab isi family se nikle.** Aur wo
edge cases iske apne nahi the: wo `execution_gateway` / `risk_gate` /
`order_store` / `pos_monitor` me the — **jo mission strategies bhi use karti
hain.** Iska signal-logic retire ho sakta hai; jo kaam ye kar raha hai — shared
money-path ko roz thokna — wo mission ke liye aur zaroori hai.

Isliye naam `Canary` hai, aur `desc` me **kyun zinda hai** likha hai. Warna koi
(ya Claude) ise "duplicate" samajh ke band kar dega.

## Consequences

- RMS table ka **"Other" bucket ab khaali** hai. `arschain_MAIN` apni family me.
  UI apne aap update hua — `STRAT_GROUPS` `/api/strategy-registry` se overwrite
  hota hai (`app-05-sound-bulk.js:92`), koi JS change nahi.
- `mirrors: 04.04` ab machine-checkable banata hai ki `04.03` ka symbol/timeframe
  apne reference se match kare. **Aaj wo match nahi karta** — 25 symbols vs 1.
- ₹1,600/month ka sawaal ab naapne layak hai: roz ka drift report. Aur sahi sawaal
  "1:1 exact?" nahi — **"drift ka kharcha ₹19,200/saal se kam hai?"**

## Jo abhi bhi khula hai

1. **`04.03` NIFTY-only nahi hua.** Uski 25 symbols me 23 stocks capital kha ke
   NIFTY ko `CAPITAL_BLOCKED` kar dete hain → **mahine me 1 NIFTY trade**. Poora
   Pine2Python project isi wajah se band pada hai. Merge karne ko kuch nahi —
   saare 23 stocks pehle se `04.02` me hain, BANKNIFTY `04.01` me. Zero nuksaan.
2. **Niyam 4 nahi laga:** `order_store.strategy` me abhi bhi kuch bhi likha ja
   sakta hai. Jab tak sirf registered ID enforce nahi hoti, `''` / `unknown` /
   `default` live orders likhte rahenge — aur naye naam kuch nahi bachayenge.
3. **`never_live` enforce nahi hota.** `trader_dashboard.py:1214` —
   `mode = request.args.get('mode', 'paper')`. Poore codebase me koi guard nahi.
   `04.02` ke 23 stocks **ek dropdown click door** hain Zerodha se. Abhi unhe
   `_PAPER` naam bacha raha hai — aur naam kuch nahi rokta.
4. **`label()` ka silent fallback zinda hai** — unknown alias raw string bankar
   nikal jaata hai. Yahi aaj ka bug invisible rakhta tha.
5. `cfg.webhooks.global` (shared config block, koi strategy nahi) RMS table me
   fake row banata hai.

## References

- LESSONS.md TRAP #62 (cross-process `_wh_state` ghost — 2026-07-01 flagged,
  2026-07-16 closed), TRAP #13/#79 (structured-field resolution)
- `strategy_registry.json` → `_meta.field_guide`
- ADR-001 (execution_gateway), ADR-003 (backtest shares live risk rules)
