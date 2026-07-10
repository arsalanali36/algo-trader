# ⚠️ KNOWN ISSUES / TODO — option strategy pipeline

Open items to fix. Read before trusting a number that touches these.

---

## ✅ #1 — Weekly-expiry day was HARDCODED to Thursday — FIXED 2026-07-10 (home machine)

**Status:** ✅ FIXED. Official circulars pulled, `expiry_calendar.py` filled, `bs_option._next_weekly_expiry`
wired to the schedule, `build_final.py` re-run. Live side verified already-correct (see bottom).

**What was done:**
- `expiry_calendar.py` filled from OFFICIAL circulars (no guessing):
  - **NIFTY weekly: Thursday → Tuesday, effective 2025-09-01** (last Thu weekly 28-Aug-2025, first
    Tue weekly 02-Sep-2025). Source: SEBI cir SEBI/HO/MRD/MRD-TPD-1/P/CIR/2025/76 (26-May-2025) +
    NSE cir 111/2025 (25-Jun-2025).
  - **NIFTY monthly: last Thursday → last Tuesday, effective 2025-09-01** (same change).
  - The **Monday** move announced 04-Mar-2025 (eff 05-Apr-2025) was **withheld 27-Mar-2025 and never
    took effect** → no Monday row; Thursday ran uninterrupted until the Tuesday switch.
  - **BANKNIFTY weekly discontinued** — last weekly 13-Nov-2024, none from 20-Nov-2024 (SEBI 01-Oct-2024,
    one weekly/exchange → NIFTY only). Recorded; backtests are NIFTY-only anyway.
- `bs_option._next_weekly_expiry(ts)` now calls `expiry_calendar.weekly_expiry_weekday(ts.date())`
  instead of `weekday=3`. Verified: pre-2025-09-01 → Thursday, from 2025-09-01 → Tuesday.
- **Net effect:** only the **Sep-2025→present (~10mo)** window changes (Thu→Tue); the earlier 8yr is
  unchanged (was Thursday either way). That window is the OOS/recent primary judge, so re-ran
  `build_final.py` to refresh #1 (straddle) + #2 (debit vertical). The 9-Jul-2026 "expiry" ₹57
  artifact is gone (Jul-2026 weekday is now Tuesday, so a Thursday is no longer treated as 0DTE).

**Original bug (for reference):** `bs_option.py` → `_next_weekly_expiry(ts, weekday=3, ...)` assumed
**Thursday** for EVERY date in the 2018-2026 backtest, mis-pricing `T` (time-to-expiry) for the
Black-Scholes premium + theta, worst near expiry (T→0).

**The bug:** `bs_option.py` → `_next_weekly_expiry(ts, weekday=3, ...)` assumes **Thursday** (weekday=3)
for EVERY date in the 2018-2026 backtest. This sets `T` (time-to-expiry) for the Black-Scholes premium
+ theta on every trade.

**The reality (user, domain expert):** NSE/SEBI have changed the NIFTY weekly-expiry day **multiple
times** over the years — Thursday, then Tuesday, at points Wednesday, etc. As of 2026 the **current
NIFTY weekly expiry = TUESDAY**. So one fixed weekday is wrong for large stretches of the 8.5yr window.

**Why it matters:**
- `T` wrong → ATM premium + theta decay mis-priced, WORST near expiry (T→0).
- Directly feeds the **0DTE-inflation** concern: the model can think a non-expiry day is "expiry"
  (cheap ~0DTE straddle) or vice-versa, distorting % returns. (First caught 2026-07-10: a 9-Jul-2026
  "expiry" straddle priced at ~₹57 combined because the code called that Thursday an expiry day.)
- Every straddle/strangle/structure result inherits this.

**✅ Live side verified OK (not affected):** `strategies/live/straddle_trader.py` uses
`risk_gate.is_expiry_day` (`_core/risk_gate.py:1322`), which derives the expiry date from the REAL
contract (trad_sym parse OR `dhan_master` sec_id lookup) — never a hardcoded weekday. So live is
already correct-day-aware. The Thursday bug was **backtest-only** (`bs_option`).

**✅ RESOLVED — expiry-day entry POLICY = A (skip new entries on the correct expiry day).** Applied in
`option_structures.backtest_structure` (`skip_expiry` default ON; `EXP` array from `expiry_calendar`);
`build_final.py` sets it for all Track-A structures. Re-run 2026-07-10 (500-perm). Effect: headline %
LOWER (0DTE-lottery removed) but HONEST — and it pushed the straddle from p=0.054 (fail) to p=0.036
(pass). Final: #2 Debit Vertical p=0.000, #1 Long Straddle p=0.036 — both SIGNIFICANT. Pass
`skip_expiry=False` to reproduce the old expiry-allowed numbers.

---

## Fixed 2026-07-10 (for reference)
- ✅ Dashboard duration was `bars × 15min` hardcoded → 3× inflated for 5m strategies. Now `bars × tfMin()`.
- ✅ Trade "Side" showed "SHORT" for a long straddle (anything ≠ 'long' → SHORT). Now maps
  long/short/long_vol/short_vol properly.
- ✅ Confirmed NOT bugs: the shown premium is the combined BUY premium of both legs (not a sell);
  charges DO count both legs (2 in + 2 out = 4 orders).
