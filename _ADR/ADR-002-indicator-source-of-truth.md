# ADR-002: Indicator source of truth — strategy ka Pine-matched formula, `ta` library nahi

## Status: Decided (2026-07-07)

## Context

Repo mein ek hi indicator ke 2+ "sach" the:

- **RSI 4 jagah:** `_TRADERS/01_rsi_v1.py` `_compute_rsi` (Wilder, com=period-1 —
  LIVE trades isi pe chalte hain), `_TOOLS/backtest_engine.py` `_compute_rsi`
  (same formula, 2026-07-03 ko inline copy), `strategies/rsi_v1.py` `_rsi`
  (same formula, teesri copy), aur `_CHARTING/indicators.py` ka `ta`-library
  RSIIndicator (dashboard chart isse plot hota tha).
- **ATR 2 jagah:** `range_trader.py` `compute_atr` (Wilder RMA alpha=1/period,
  Pine-validated) vs `_CHARTING` ka `ta` AverageTrueRange.
- **EMA 4 jagah inline:** `nifty_ema_trader.py`, `backtest_engine.py` (×2),
  `strategies/vwap_ema_failure.py` — sab `ewm(span, adjust=False)` one-liners.

Risk: signal ek formula se, chart doosre se — mismatch dikhta to debugging
mein pura din jata (TRAP #84 family: duplicate file/formula divergence).

## Decision

**Strategy ka Wilder/Pine-matched formula = "sach"** — kyunki wahi live trades
decide karta hai aur TradingView ke against 90.2% exact validate ho chuka hai
(VALIDATION_PLAYBOOK.md). `ta`-library wala sirf chart cosmetic tha.

- `_CHARTING/indicators.py` ab SINGLE source of truth: canonical pure-pandas
  `wilder_rsi()`, `wilder_atr()`, `pine_ema()` (tier 1) + `ta`-based
  chart-only extras SMA/VWAP/BBANDS (tier 2, **lazy import** — live trader
  process ko `ta` package ki zaroorat nahi).
- INDICATOR_REGISTRY ke RSI/ATR/EMA ab canonical functions pe point karte
  hain → chart aur signal EK calculation.
- Saare consumers import-alias se migrate: `_compute_rsi`/`compute_atr`/`_rsi`
  naam wahi rahe (call sites untouched), body ab import hai.

## Consequence

- Signal-vs-chart mismatch structurally impossible (RSI/ATR/EMA ke liye).
- Numeric equivalence PROVEN: synthetic-data test, old vs new **max diff 0.0**
  (bit-identical) — isliye validate_strategy.py ka 90.2% score unchanged rehta
  hai, re-run ki zaroorat nahi thi.
- Chart RSI/ATR values ab Wilder formula se — `ta`-library wale se marginally
  alag dikh sakte hain (pehle wale chart "galat" the, ab wahi dikhte hain jo
  trades ne dekha).
- Chart EMA ab bar-1 se values deta hai (`ta` wala pehle n-1 bars NaN rakhta
  tha) — cosmetic, aur Pine ke behavior se zyada match.
- Trade-off: `_TRADERS/` files ab `_CHARTING/` package pe depend karte hain —
  VPS deploy mein `_CHARTING/` folder zaroor jaana chahiye (git-archive sync
  mein already jaata hai).
- architecture_audit.py ka DUP-INDICATOR check ab is decision ko enforce
  karta hai — nayi copy commit hi nahi ho sakti (pre-commit hook).
