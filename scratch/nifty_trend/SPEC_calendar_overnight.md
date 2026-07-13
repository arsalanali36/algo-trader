# SPEC — Directional Calendar + Overnight Short-Vol (Ravish video → NIFTY)

**Source:** "Undiscovered Traders" podcast w/ Ravish (delta-neutral + time-spread / theta machine).
**Owner ask (2026-07-13):** "dono rasta explore karo, overnight bhi kar lo."
**Status:** SPEC / mockup — awaiting approval before any code.
**Fits:** OPTION_STRATEGY_MISSION Track-B (real option-lake, `real_struct2` held-strike engine).

---

## 0. Honest scope — kya naya hai, kya pehle reject ho chuka

| Video ka piece | Humne pehle test kiya? | Result | Is spec me? |
|---|---|---|---|
| **Neutral** calendar (sell-weekly/buy-monthly, ATM) | ✅ haan | +18% par **Sharpe 0.12, −₹48.5k tail** ❌ | ❌ NO re-tread |
| Positional iron-fly (weekly, hedged, 4-5d) | ✅ haan | −8%, Sh −0.47 ❌ | ❌ NO re-tread |
| Positional iron-condor (weekly, hedged) | ✅ haan | −7%, Sh −0.41 ❌ | ⚠️ sirf naye gate ke saath (Variant B) |
| Intraday short-straddle/fly | ✅ haan | naked ~breakeven (tail), hedged −41% ❌ | ❌ NO re-tread |
| **DIRECTIONAL** calendar (theta machine, strike = move-target) | ❌ **NAHI** | — | ✅ **Variant A — the real new idea** |
| Overnight hold (mission tha intraday-only) | ❌ naya lane | ADR-006 `allow_overnight()` ab hai | ✅ dono variants |

**Bottom line:** video ki asli "edge" 2 cheezon se aati hai jo hum abhi tak nahi lagaye —
(1) calendar ko **directional** use karna (neutral nahi), (2) **overnight/multi-day theta carry**.
Yeh spec sirf inhi 2 axes ko test karta hai. Condor/fly wali neutral side chhod di (already dead).

---

## VARIANT A — Directional Calendar ("Theta Machine")

### Idea (video ka exact structure)
- **Sell** front-week option at strike K, **Buy** same strike K in **next expiry** (monthly ya next-week).
- K = ek **directional move-target** (spot se thoda OTM, ~20-30 delta). Neutral calendar me K=ATM;
  yahan K ko trend-direction me rakhte hain → agar spot dhीरे-dhीरे K ki taraf jaaye, dono theta
  aur delta profit dete hain. "Slow grind" ideal.
- Bullish → CALL calendar (K spot ke upar). Bearish → PUT calendar (K spot ke neeche).
- **Net DEBIT** paid = back-premium − front-premium → **max loss = debit** (defined risk, low margin).
  Yehi is variant ki khoobi: naked strangle jaisa undefined tail nahi.

### Directional signal (kaunsa side + strike)
Do options, dono existing infra se — koi naya indicator nahi (Rule 6B):
- **Signal-A1 (reuse):** ORB / chain_zone jaisa jo already validated hai — us din ka bias (long/short)
  → CALL ya PUT calendar decide. Strike K = ATM ± `k_off` strikes trend direction me.
- **Signal-A2 (neutral fallback):** agar koi strong bias nahi → ATM calendar (pure theta), par yeh
  wahi neutral form hai jo reject hua — isliye **default = directional only**, neutral ko sirf
  ablation ke liye rakho.

### Entry / hold / exit
- **Entry:** signal-bar ke baad (intraday), OR fixed time (e.g. 14:45 pre-close, video-style).
- **Hold:** overnight allowed (ADR-006). Max hold = front-expiry se 1 din pehle (front ki expiry-day
  gamma/pin risk avoid). Typically 2-5 trading days.
- **Exit (whichever first):**
  - Profit: spread value +X% of debit (video: 30-50% book kar leta hai, poora peak wait nahi).
  - Stop: spread value −Y% of debit (defined; debit hi max loss but jaldi cut).
  - Time: front-expiry − 1 day pe force-close (front theta khatam, back leg naked theta na khaaye).
  - 3:15 nahi lagega (overnight lane) — par expiry-day pe intraday squareoff rule laagu.

### Why yeh neutral calendar se alag survive kar sakta hai
Neutral calendar sirf theta pe jeeti thi → cost + vega swings ne khaya (−₹48.5k tail).
Directional calendar me theta + **ek chhota delta edge** dono hain; agar underlying signal (ORB/chain)
ka apna proven positive expectancy hai, calendar structure us edge ko **theta-cushioned** deliver karta
hai (loss days pe front theta kuchh loss absorb karta). Yeh hypothesis hai — backtest confirm karega.

---

## VARIANT B — Overnight Short Strangle / Condor (honest re-test)

Mission tracker khud kehta hai: revisit only if IV-gate ko **aur qualifying-day sources** ke saath
combine karo taaki trade-count > 100 ho (naya signal-design, knob nahi). Yeh variant wahi karta hai.

### Structure
- **Iron condor** (defined-risk, mission rule: naked short ko wing chahiye): sell ~15-20 delta CE+PE,
  buy wings ~5-8 strikes further. Net credit.
- Overnight: enter T-day, hold 1-4 days, exit on target/stop/DTE. (Pehle intraday tha → naya lane.)

### The NEW gate (yehi difference)
Pichhli baar sirf `iv_rank ≥ 0.5` tha → 36 trades/5yr (too few). Ab **multi-source qualifying day**:
- `iv_rank ≥ 0.5` **OR** intraday IV-spike (front IV > N-day avg × 1.2) **OR**
  "directional-quiet" day (ADX/realized-range low, no strong trend) — sell only when the day is
  BOTH vol-rich AND range-bound.
- Target: qualifying days badha ke trades ≥ 100 laao **bina** har din bech ke (jo tail deta hai).

Agar yeh gate combine karke bhi trades < 100 ya Sharpe < 1 → **honestly reject**, mission table me
"exhausted, confirmed" likh do. (Yeh variant ka openly-stated risk hai.)

---

## 1. India frictions — dono variants me model karo

| Friction | Kaise model |
|---|---|
| **Charges** | Real Zerodha F&O per leg per side — `bs_option.calc_charges` / existing engine (already wired). Calendar = 2 legs entry + 2 exit; condor = 4+4. |
| **Slippage** | DOM-calibrated `bs_option.slip_cost_leg()` / `dom_cost.py` (ADR-005) — per leg. ATM ≈0.13%, wings ≈0.24%. **Calendar back-leg (monthly) thin ho sakti hai → wing-band spread use karo, ATM nahi.** |
| **Margin (condor short legs)** | Real Dhan/Kite margin via `risk_gate.broker_real_margin()`. Calendar = debit → margin ≈ debit (defined), condor = SPAN+exposure minus wing benefit. **SEBI ne expiry-day pe calendar/spread margin-benefit hataya** → front-expiry-day pe margin spike model karo (ya us din enter hi mat karo). |
| **Lot size** | Scrip master se (`dhan_master.get_lot_size_by_sec_id()`) — hardcode nahi. |
| **Expiry calendar** | `expiry_calendar.py` (NIFTY weekly Tue eff 2025-09-01, monthly last-Tue). Front vs back expiry pair isi se resolve. |
| **Overnight gap** | Yeh naya risk hai (intraday me nahi tha). Calendar = defined debit so gap-capped. Condor = wings cap the gap. Report worst-overnight-gap day explicitly (TRAP #109 worst-day sanity via `data_integrity.py`). |

---

## 2. Data & backtest design

- **Data = REAL lake, NOT Black-Scholes.** Calendar vega/term-structure-sensitive hai — BS-from-realized
  σ do alag expiries ka IV term-structure nahi dekh sakti (docs me explicitly noted). So:
  - `optlake_load.load_series("WEEK", side, off, tf)` = front leg (real premium+IV+OI).
  - `optlake_load.load_series("MONTH", side, off, tf)` = back leg (same-ish ATM, real).
  - Dono ko `Datetime` pe align → per-bar spread value = back_close − front_close (debit).
- **Engine = `real_struct2.py`** (held-strike, tracks entry strike through ±10 offset grid — the TRAP #109
  fix). Naya calendar structure iske andar add karo (Rule 6B — alag simulator mat likho):
  - `real_struct2` me `calendar_directional` structure (front SELL + back BUY, held strikes both expiries).
  - Reuse existing charges + `slip_cost_leg` + margin hooks.
- **3-pass display:** mandatory — ① Instrument (spread P&L, no RMS) → ② +RMS (caps) → ③ deployable
  (real premium + charges + slip). `run_hunt.py --name calendar_directional` reference producer;
  `runs/calendar_directional/` folder; hub auto-lists.
- **Overnight variant needs multi-day P&L grid** — `real_struct2` ko intraday se multi-day hold pe
  extend karna padega (positional_vol.py already positional condor karta hai → us pattern se).

---

## 3. Params (sweep grid — optimize + significance)

**Variant A (calendar):**
- `bias_signal`: {orb, chain_zone, none(neutral-ablation)}
- `k_off`: strikes OTM from ATM in bias direction {0, 1, 2, 3}
- `back_expiry`: {next_week, monthly}
- `entry_time`: {signal_bar, 14:45}
- `tp_frac` (of debit): {0.3, 0.5, 0.75}
- `sl_frac` (of debit): {0.5, 1.0}
- `max_hold_days`: {2, 3, 5}

**Variant B (overnight condor):**
- `short_delta`: {0.15, 0.20}, `wing_off`: {5, 8}
- `qualify`: {iv_rank, iv_spike, range_quiet, any-2-of-3}
- `tp_frac`/`sl_frac` of credit, `max_hold_days`: {1, 2, 4}

---

## 4. Validation gates (mission-locked — sab pass karne honge)
Sharpe ≥ 1 · MaxDD ≤ 20% · **Trades ≥ 100** · significance p < 0.05 (rotation) ·
optimize rank = min(train, OOS) (TRAP #103) · Monte-Carlo original ≈ median ·
worst-day sanity (TRAP #109) via `data_integrity.py` · beat NIFTY buy&hold risk-adjusted.

**Extra for overnight:** worst-overnight-gap day report + max consecutive overnight-loss streak.

---

## 5. Build order (per-strategy, mission rule)
1. **Variant A first** (naya, defined-risk, low-margin, highest chance). Backtest → 3-pass dashboard
   → user approve → live paper trader (`strategies/live/NN_calendar_trader.py`, execution_gateway,
   overnight lane) → VPS paper.
2. **Variant B next** (honest re-test; may reject).

Har ek: design → backtest → dashboard → approve → NEXT. Never batch.

---

## 6. Decisions (LOCKED with user 2026-07-13)
1. **Back-expiry:** SWEEP both — monthly AND next-week; ship whichever Sharpe wins.
2. **Entry style:** SWEEP both — signal-driven (ORB/chain bias) AND fixed-time (14:45 pre-close).
3. **Overnight cap:** 1 position at a time (most conservative; gap risk minimal).
4. **Variant B:** YES, test with the new multi-source qualify gate. If trades<100 or Sharpe<1 →
   honestly reject + mark mission table "exhausted, confirmed".

## 7. RESUME HERE (build state)
- [ ] Verify lake: WEEK + MONTH CE/PE ATM±k on disk (`optlake_load.available()`).
- [ ] Extend `real_struct2.py` with `calendar_directional` structure (front SELL + back BUY, held strikes,
      multi-day hold, reuse charges + slip_cost_leg + margin).
- [ ] `build_calendar.py` → `run_hunt.py --name calendar_directional` (3-pass, sweep grid §3).
- [ ] Dashboard `runs/calendar_directional/` + hub row.
- [ ] User approve → `strategies/live/NN_calendar_trader.py` (overnight lane) → VPS paper.
- [ ] THEN Variant B overnight condor (build_overnight_condor.py).
