# Strategy ID system — proposal (choose a scheme)

**Goal:** one canonical hierarchical ID per strategy, shown EVERYWHERE (logs, positions,
RMS, reports, hub, backtest folders). Internal keys (`orb_v1`, correlationId, order_store)
stay UNCHANGED — the ID is a **display + organization layer** (registry maps ID ↔ every
alias), so live orders / history are never at risk.

Below: the SAME strategies mapped under two schemes. Pick one (or edit any row).

Legend: ✅ deployed(paper) · 🔬 research/backtest-only · ❌ rejected/failed · 💤 older/legacy live

---

## SCHEME A — keep the flat mission numbers you already know (00-08) + `.NN` only for variants

| ID | Name | family | config key | hub slug / file | status |
|----|------|--------|-----------|-----------------|--------|
| **00** | Mid-Day ORB (NIFTY) | ORB | orb_v1 | mid_orb_nifty / orb_trader.py | ✅ |
| **01** | Long Straddle @ ORB | ORB | straddle_v1 | long_straddle_orb / straddle_trader.py | ✅ |
| **02** | Debit Vertical @ ORB | ORB | dvert_v1 | debit_vertical_orb / 02_debit_vertical_trader.py | ✅ |
| **03** | ORB + Supertrend | ORB | orbst_v1 | orb_supertrend / 03_orbst_trader.py | ✅ |
| **04** | Chain-Zone (Auto Rev-Chain) | Chain-Zone | — | *(family header)* | — |
| 04.01 | Chain-Zone — Long ATM | Chain-Zone | chainzone_v1 | chain_zone_longatm / 04_chainzone_trader.py | ✅ |
| 04.02 | Chain-Zone — Credit spread | Chain-Zone | — | chain_zone_credit | 🔬 |
| 04.03 | Chain-Zone — Naked sell | Chain-Zone | — | chain_zone_naked | 🔬 |
| 04.04 | Chain-Zone — Positional | Chain-Zone | — | chain_zone_positional | 🔬 |
| **05** | Ratio Backspread @ ORB | ORB | backspread_v1 | ratio_backspread / 05_backspread_trader.py | ✅ |
| **06** | Short-Vol Iron-Fly | Vol | shortvol_v1 | shortvol_ironfly / 06_shortvol_trader.py | ❌ |
| **07** | BankNifty ORB | ORB | banknifty_v1 | banknifty_hunt / 07_banknifty_trader.py | ✅ |
| **08** | Pivot-Extreme Continuation | Pivot | — | pivot_continuation | 🔬 |
| **09** | Gamma Scalp | Vol | — | gamma_scalp | ❌ |
| **10** | Long Strangle @ ORB | ORB | — | long_strangle_orb | ❌ |
| **20** | Ars Chain / Range | Range | — | range_trader.py | 💤 |
| 20.01 | Ars Chain (live) | Range | ars_chain_v1 | range_trader.py | 💤 |
| 20.02 | Ars Chain (paper) | Range | ars_chain_v1_paper | range_trader.py | 💤 |
| 20.03 | Range Breakout | Range | range_v1 | range_trader.py | 💤 |
| **21** | RSI | Indicator | — | 01_rsi_v1.py | 💤 |
| 21.01 | RSI (live) | Indicator | rsi_v1 | 01_rsi_v1.py | 💤 |
| 21.02 | RSI (paper) | Indicator | rsi_v1_paper | 01_rsi_v1.py | 💤 |
| **22** | EMA Crossover | Indicator | ema_v1 | nifty_ema_trader.py | 💤 |
| **23** | Universe Scan | Scanner | universe_v1 | universe_trader.py | 💤 |
| **24** | TradingView Webhook | External | webhook_v1 | webhook_executor.py | 💤 |
| **30** | Bollinger Band Bounce | Bollinger | — | strategies/backtest/user_bb*.py | 🔬 |
| 30.01 | BB Bounce v1 | Bollinger | — | user_bb1_v1.py | 🔬 |
| 30.02 | BB Bounce v2 | Bollinger | — | user_bb2_v1.py | 🔬 |
| 30.03 | Bollinger Band Bounce (Gemini) | Bollinger | — | user_bollinger_band_bounce_strategy_v1.py | 🔬 |
| **31** | 52-Week (Gemini) | Breakout | — | user_temp_52_week_gemni_v{1,2}.py | 🔬 |
| **32** | VWAP-EMA Failure | Indicator | — | vwap_ema_failure.py | 🔬 |
| **33** | Sample EMA / Always-Buy (test) | Test | — | sample_ema.py / always_buy.py | 🔬 |

**Pros:** the 00-07 numbers you already see stay the same. **Cons:** the top-level list is a
flat mix (ORB strategies scattered across 00/01/02/03/05/07, not grouped).

---

## SCHEME B — regroup by FAMILY (family = NN, every member = NN.MM)

| ID | Name | config key | hub slug / file | status |
|----|------|-----------|-----------------|--------|
| **00** | **ORB family** (opening-range breakout) | — | — | — |
| 00.01 | Mid-Day ORB (NIFTY, naked ATM) | orb_v1 | mid_orb_nifty | ✅ |
| 00.02 | ORB + Supertrend | orbst_v1 | orb_supertrend | ✅ |
| 00.03 | BankNifty ORB | banknifty_v1 | banknifty_hunt | ✅ |
| 00.04 | Long Straddle @ ORB | straddle_v1 | long_straddle_orb | ✅ |
| 00.05 | Debit Vertical @ ORB | dvert_v1 | debit_vertical_orb | ✅ |
| 00.06 | Ratio Backspread @ ORB | backspread_v1 | ratio_backspread | ✅ |
| 00.07 | Long Strangle @ ORB | — | long_strangle_orb | ❌ |
| **01** | **Chain-Zone family** (Auto Rev-Chain) | — | — | — |
| 01.01 | Long ATM | chainzone_v1 | chain_zone_longatm | ✅ |
| 01.02 | Credit spread | — | chain_zone_credit | 🔬 |
| 01.03 | Naked sell | — | chain_zone_naked | 🔬 |
| 01.04 | Positional (monthly) | — | chain_zone_positional | 🔬 |
| **02** | **Vol family** | — | — | — |
| 02.01 | Short-Vol Iron-Fly | shortvol_v1 | shortvol_ironfly | ❌ |
| 02.02 | Gamma Scalp | — | gamma_scalp | ❌ |
| **03** | **Pivot family** | — | — | — |
| 03.01 | Pivot-Extreme Continuation | — | pivot_continuation | 🔬 |
| **04** | **Range family** (Ars Chain) | — | range_trader.py | 💤 |
| 04.01 | Ars Chain (live) | ars_chain_v1 | range_trader.py | 💤 |
| 04.02 | Ars Chain (paper) | ars_chain_v1_paper | range_trader.py | 💤 |
| 04.03 | Range Breakout | range_v1 | range_trader.py | 💤 |
| **05** | **Indicator family** | — | — | 💤 |
| 05.01 | RSI (live) | rsi_v1 | 01_rsi_v1.py | 💤 |
| 05.02 | RSI (paper) | rsi_v1_paper | 01_rsi_v1.py | 💤 |
| 05.03 | EMA Crossover | ema_v1 | nifty_ema_trader.py | 💤 |
| 05.04 | VWAP-EMA Failure | — | vwap_ema_failure.py | 🔬 |
| **06** | **Scanner family** | — | — | — |
| 06.01 | Universe Scan | universe_v1 | universe_trader.py | 💤 |
| **07** | **External** | — | — | — |
| 07.01 | TradingView Webhook | webhook_v1 | webhook_executor.py | 💤 |
| **08** | **Bollinger family** (backtest) | — | — | 🔬 |
| 08.01 | BB Bounce v1 | — | user_bb1_v1.py | 🔬 |
| 08.02 | BB Bounce v2 | — | user_bb2_v1.py | 🔬 |
| 08.03 | Bollinger Bounce (Gemini) | — | user_bollinger_band_bounce_strategy_v1.py | 🔬 |
| **09** | **Breakout / misc (backtest)** | — | — | 🔬 |
| 09.01 | 52-Week (Gemini) | — | user_temp_52_week_gemni_v{1,2}.py | 🔬 |
| 09.02 | Sample EMA / Always-Buy (test) | — | sample_ema.py / always_buy.py | 🔬 |

**Pros:** clean hierarchy — every ORB thing is `00.xx`, every Chain-Zone is `01.xx`; the family
is obvious at a glance. **Cons:** the current 00-07 numbers you see today change (00 becomes a
family, mid_orb becomes 00.01).

---

## The registry (single source of truth, either scheme)
`_core/strategy_registry.py` + `strategy_registry.json`: `{ "<ID>": {name, family, config_key,
slug, live_file, status, parent} }` with helpers `id_for(config_key)`, `label(id)="ID - Name"`,
`resolve(any_alias)→id`, `tree()`. Every display surface (logs sidebar, RMS per-strategy table,
Orders/Positions, Reports, hub) renders `label(id)`. Internal plumbing untouched.

**Migration phases:** (1) build registry from this table → (2) wire display surfaces → (3) one
unified "Strategy Registry" view in the app (logical tree, files stay where they are) → (4)
[optional, later, risky] rename internal keys.
