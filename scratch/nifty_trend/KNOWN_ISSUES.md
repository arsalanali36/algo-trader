# ⚠️ KNOWN ISSUES / TODO — option strategy pipeline

Open items to fix. Read before trusting a number that touches these.

---

## 🔴 #1 — Weekly-expiry day is HARDCODED to Thursday (wrong; SEBI/NSE changed it many times)

**Status:** DEFERRED (user will do from the home machine, 2026-07-10). **Do NOT trust near-expiry
premiums / T-driven P&L until fixed.**

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

**The FIX (do from home machine):**
1. **Build a historical expiry-day schedule from OFFICIAL NSE/SEBI circulars** — the exact effective
   date(s) each time the expiry weekday changed (Thu→…→Tue). User's instruction: *"jo official
   document me day/date mile wo expiry lena."* No guessing — use the circular's own dates.
   Likely shape: a small table `[(effective_from_date, weekday), …]` in `bs_option.py`, and
   `_next_weekly_expiry(ts)` picks the weekday whose rule was in force on `ts`.
2. Also handle **monthly** expiry day if any structure uses monthly (same circular history).
3. THEN re-run the affected strategies (`build_final.py`) so premium/theta are correct.

**Related decision (also deferred, same session):** expiry-policy — likely **skip new entries on the
(correct) expiry day** to kill 0DTE-lottery inflation + make results robust to the exact-day mess.
Options captured: (A) weekly + skip-expiry-day [recommended], (B) min days-to-expiry, (C) monthly.

**Where to look:** `bs_option.py` (`_next_weekly_expiry`, `tte_years`), `BS_OPTION_SIM.md`,
`option_structures.py` (uses `bs.tte_years`), `strategies/live/straddle_trader.py` (live uses
`risk_gate.is_expiry_day` — check that's also correct-day-aware).

---

## Fixed 2026-07-10 (for reference)
- ✅ Dashboard duration was `bars × 15min` hardcoded → 3× inflated for 5m strategies. Now `bars × tfMin()`.
- ✅ Trade "Side" showed "SHORT" for a long straddle (anything ≠ 'long' → SHORT). Now maps
  long/short/long_vol/short_vol properly.
- ✅ Confirmed NOT bugs: the shown premium is the combined BUY premium of both legs (not a sell);
  charges DO count both legs (2 in + 2 out = 4 orders).
