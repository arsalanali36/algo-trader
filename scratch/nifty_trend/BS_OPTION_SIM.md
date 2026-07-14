# 🎯 ATM Option Premium Simulation (Black-Scholes) — backtest spec

> **Why:** Signals are found on NIFTY **spot** (index), but the strategy trades **ATM options**.
> Real expired-weekly option 1-min data doesn't exist (Dhan drops it — CODE3B LESSONS TRAP #100),
> so we price the ATM option at entry & exit with **Black-Scholes** to get a realistic
> option-premium P&L (delta + theta + real cost), instead of a misleading spot-notional P&L.
> Every backtest that trades options MUST do this. Output goes in `all_trades[]` per
> `RESULTS_SCHEMA.md`.

## Inputs per trade

| Symbol | Meaning | Source |
|--------|---------|--------|
| `S` | NIFTY spot at the moment (entry / exit) | backtest bar |
| `K` | ATM strike = `round(S_entry/50)*50` | fixed at entry |
| `T` | time to expiry in years = `max(trading_secs_left_to_weekly_expiry, 0)/ (365*86400)` | date → that week's Thu 15:30 IST |
| `sigma` | implied vol (annualised, e.g. 0.15) | **India VIX** that day / 100; fallback = 20-day realised vol of NIFTY log-returns × √252 |
| `r` | risk-free rate | 0.065 (constant; effect is small) |
| `opt_type` | CE for a long signal, PE for a short signal | strategy side |

`lot_size` — from the Dhan scrip master (`get_option_contract` 3rd return), **never hardcode**.
`qty = lots × lot_size`.

## Black-Scholes price (pure, no external option data)

```python
import math
def _norm_cdf(x): return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bs_price(S, K, T, sigma, r=0.065, opt="CE"):
    if T <= 0 or sigma <= 0:
        # at/after expiry -> intrinsic only
        return max(0.0, S - K) if opt == "CE" else max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if opt == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)
```

**Sanity (ATM approx `≈ 0.4·S·σ·√T`):** S=22000, σ=0.15, T=2/365 → ≈ ₹98. Real ATM weekly
premiums run ~₹80-150, so the model is in the right zone. Cross-check a few before trusting.

## Per-trade flow

```
K          = round(entry_spot / 50) * 50
entry_prem = bs_price(entry_spot, K, T_entry, sigma_entry, opt=opt_type)
exit_prem  = bs_price(exit_spot,  K, T_exit,  sigma_exit,  opt=opt_type)   # SAME K; T_exit < T_entry; sigma can update intraday but same-day ≈ same
qty        = lots * lot_size
gross      = (exit_prem - entry_prem) * qty          # we BUY the option, so long premium
fee        = calc_charges(entry_prem, exit_prem, qty, when=entry_ts)   # DATE-AWARE — see below
pnl        = gross - fee
```

We **buy** the ATM option in both directions (CE for long, PE for short) — so `gross` is
always `(exit_prem − entry_prem) × qty`, no sign flip. (If a strategy SELLS options instead,
flip the sign and use SELL-side margin, but the default is buy-ATM.)

## Real Zerodha F&O charges — DATE-AWARE since 2026-07-14 (`charges.py` = single source)

STT/txn changed **2024-10-01** (Budget-2024: options STT sell 0.0625%→0.10%, txn
0.053%→0.03503%) and **2026-04-01** (Budget-2026: STT →0.15%, txn 0.03553% — verified live
against zerodha.com/charges 2026-07-14). A 4.5yr backtest spans all three regimes, so
`bs_option.calc_charges(entry_prem, exit_prem, qty, entry_side="BUY", when=entry_ts)` looks
the rates up by the trade's ENTRY date (`charges.OPT_REGIMES`); `when=None` = today's rates.
Formula shape (brokerage ₹20×2, SEBI ₹10/crore, stamp 0.003% buy, GST 18% on
brokerage+txn+SEBI) is unchanged — only STT/txn are regime-looked-up. Full table + the ₹40
cash-collateral-brokerage flag: `charges.py` docstring + `STT_RECOST_2026-07-14.md`.

1-lot NIFTY (premium ~₹100, lot 75) ⇒ ~₹60-70 round-trip (regime-dependent) — NOT the ₹240+
a spot-notional model gives. **This is the correct tax basis.** (User will also state cost
assumptions in the prompt; honour those if given, else use this.) The app's live
`calcCharges` (index.html) carries the CURRENT regime only — it costs today's trades.

## Why this makes the result realistic (vs spot)
- **Delta (~0.5 ATM):** a 100-pt index win → premium moves ~50 pt → option P&L ≈ half the naive
  spot P&L. Position sizing/returns change accordingly.
- **Theta (decay):** premium bleeds intraday even on a flat index → a break-even spot day can be
  a losing option day. Weekly options near expiry decay fast.
- **Correct ₹ scale:** premium×lot is the real money at risk → RMS daily loss/profit caps and
  the daily-loss breaker fire at the RIGHT levels (the whole point of the two-stage RMS
  validation, LESSONS TRAP #104).

## Sizing & RMS (keep consistent with live)
- `qty` from lots (config) × real lot_size. Enforce **1x / no-leverage** unless the prompt says
  otherwise (`leverage_cap`), so returns reflect edge not bet size (LESSONS TRAP #105).
- Feed the option `pnl` (net) into the RMS-overlay validation (`intraday_engine.backtest(rms_caps=)`)
  BEFORE deploy, and set per-strategy caps at the real option-P&L scale.

## Data: India VIX
- Fetch daily India VIX for the window (Dhan index feed if available; else compute a realised-vol
  proxy: 20-day rolling std of NIFTY 1-min/close log-returns, annualised ×√252). Map date→σ.
- ATM IV ≈ India VIX is a good first approximation; a small term-structure/skew refinement is
  optional and not needed for a first realistic pass.

---
**Output:** put `strike, opt_type, entry_prem, exit_prem, qty, gross, fee, pnl` (+ existing
`entry_spot/exit_spot/points`) into each `all_trades[]` object (RESULTS_SCHEMA.md). The dashboard
then shows real premiums + real tax + correct totals with zero extra work.
