# Range-Extreme Short Strangle — Research & Decision Doc

**Status:** research complete → forward-paper candidate (NOT live-deployed yet)
**Owner:** Arsalan | **Date:** 2026-07-17 | **Branch:** `feat/range-strangle-mission`
**Instruments confirmed:** NIFTY (5-yr) + BANKNIFTY (genuine weekly 2021→Nov-2024)
**Role:** small complementary sleeve to the ORB mission ("dessert", not main course)

> Isi format me jaisa webhook ke liye banaya tha — poori baat-cheet ka nichod, taaki dobara
> na dohrana pade aur live jaane se pehle sab ek jagah ho.

---

## 1. Idea (user's, verbatim intent)

Pichle N-din (5-10) ka HIGH aur LOW nikaalo. Dono taraf option **BECHO** — CE ko high pe, PE ko
low pe — aur beech ka **premium decay** khaao. Do guards user ne khud diye:
- **"Pukka" extreme** — jo high/low ho wo *waakai* ho; kal-parso ka fresh high (jo tootne wala hai)
  pe mat becho. Aged + confirmed level pe becho.
- **Intraday, no overnight** — entry ~**09:20** (din shuru), exit **usi din** → overnight gap ka
  khatra hai hi nahi.

## 2. The rig (how it was tested — honest by construction)

- **REAL option premium** from the data-lake (`OptChainLake/{NIFTY,BANKNIFTY}`, 5-min bars).
  BS (Black-Scholes) se short-vol test karna JHOOTHA hai (TRAP #106/#114 — BS ko VRP dikhta hi
  nahi, har short-vol −95..−100% padhta hai). Isliye lake se hi.
- **Real Zerodha charges** (`charges.py`, date-aware STT: Budget-24/26 regimes).
- **DOM-measured slippage** (`bs.slip_cost_leg`, bhai ke order-book se calibrated, ATM ~0.11%/leg).
- **Gates:** significance (bootstrap p<0.05), train/OOS split (2025-07-01), **DSR** (deflated
  Sharpe — multiple-testing guard), **tail-stress** (force 2% crash days, Sharpe survive kare?),
  Monte-Carlo.
- Window: 2021-08 → 2026-07 (lake start). 1× no-leverage, 1 lot default.

## 3. The journey (kya try kiya, kya nikla)

| Version | Result | Verdict |
|---|---|---|
| **Naked, overnight** (enter 15:10, hold 1 night) | strong (PF 1.9, OOS holds) but tail **UNBOUNDED** → gap tail −₹9,473, tail-stress 0.35, DSR fail | ❌ gap tail |
| Naked, hold-to-expiry | OOS negative (PF 0.52), −₹34k tails | ❌ |
| Tight target/SL sweep (10-40pt, %-based, trailing) | overnight: no better than pure hold, SL can't stop overnight gap | ❌ no gain |
| **Naked, INTRADAY 9:20** (user's actual intent) | PF 1.41, Sh 1.82, train 1.40≈OOS 1.47 (consistent), **tail SL-bounded −₹5,104**; DSR + tail-stress fail (edge too thin) | ⚠️ real but modest |
| ORB-fail filter (opening-range hold) | direction right; soft (buf=1.0) full PF 1.57 but **OOS decays** (1.67→1.18) | ❌ regime |
| Weekly-pivot R3/S3 strikes | R3/S3 best (PF 1.28 p=0.021) but **train weak (1.15)** = regime-driven | ❌ |
| **+ IV / vol-regime filter** (sell only when premium RICH) | **PF 1.41→2.04, Sh 1.82→3.70, tail-stress 0.12→1.87 PASS**, train 2.03≈OOS 2.12 | ✅ **breakthrough** |
| Hedge (condor wings) to cut margin | edge KILLED (PF 2.04→0.38) — wings eat thin far-OTM premium | ❌ not viable |
| BANKNIFTY (cross-instrument) | genuine-weekly PF 1.44→1.97 with same IV filter | ✅ **confirms edge real** |

**Sabse bada sabak:** har version me OOS (2025-26) train se behtar aata tha = recent daur
short-vol ke liye asaadharan calm/range-bound = har full-window number inflate. **Isi wajah se
naked/pivot versions DSR/tail-stress pe girte the** — jab tak IV filter ne edge ko double nahi kar
diya (rich premium = zyada cushion = tail-stress bhi survive).

## 4. FINAL STRATEGY SPEC (deployable config)

**Entry (roz ~09:20 IST):**
- Lookback **10 din** ka high & low (daily OHLC).
- Filter — **age ≥ 2 din** (extreme kal-parso ka fresh nahi) AND spot **≥ 0.5% door** AND spot
  extreme ke andar (touch nahi kiya).
- **🔑 IV filter — sirf tab enter jab IV-rank ≥ 0.5** (premium rich; NIFTY lake ki ATM IV-rank vs
  trailing 60 din; BANKNIFTY me IV missing → straddle-premium-%-rank proxy). **Yehi poori edge ki
  jaan hai** — low-IV din pe bechna paisa ganwata hai (monotonic: IV↑ → PF↑, 1.95→8.12).

**Strikes:**
- SELL CE @ 10-din-high ka nearest strike (NIFTY step 50, BANKNIFTY step 100).
- SELL PE @ 10-din-low ka nearest strike.
- **NAKED** (no wings — hedge edge ko maar deta hai, §6).
- Dono strikes ATM ke ±10 offset (±500 NIFTY / ±1000 BNF) ke andar honi chahiye (real data),
  warna trade skip.

**Exit (usi din — NO overnight):**
- Combined premium **40 pt** gire = target book (NIFTY ~₹2,600/lot).
- Combined premium **40 pt** chade = SL (~₹2,600/lot). **SL intraday me asli kaam karta** (market
  khuli, gap paar nahi kudta) → tail bounded ~−₹5,104/lot.
- Warna 15:15 EOD force-exit.

**Size/data:** 1 lot; real lake premium + Zerodha charges + DOM slip. Lot ₹-P&L scale karta,
edge-metrics (PF/Sharpe/win%) lot-independent.

## 5. RESULTS

### NIFTY alone (IV-rank ≥ 0.5)
- **123 trades** (~27/saal), net **₹44,841** (~₹9,938/saal/lot), **PF 2.04**, Sharpe **3.70**,
  **win 80%**, worst −₹5,104 (=~6 avg-jeet), train 2.03 ≈ OOS 2.12.
- IV≥0.4 = 148 trades/₹50,667 (~33/saal); IV≥0.3 = 168 trades/₹54,991 (~37/saal) — threshold
  neeche = zyada trades + zyada paisa, same PF (~2.06), tail-stress pass. **≥0.3-0.4 = more-trades pick.**
- Saal: 2021 +11,432 · 2022 +10,810 · 2023 +8,866 · **2024 −4,620** · 2025 +15,933 (4/5 green).

### BANKNIFTY (cross-instrument confirmation)
- Genuine weekly (2021→Nov-2024): baseline PF 1.44 → straddle-rank≥0.5 (T80/SL80) **PF 1.97**.
- **NSE ne BANKNIFTY weekly Nov-2024 me BAND kar diya** → forward doubling nahi deta; post-2024
  "weekly" actually monthly (full-window PF 3.5-6 = **mirage, mat maano**).

### COMBINED (NIFTY IV≥0.5 + BANKNIFTY genuine-weekly rank≥0.5)
| | |
|---|---|
| Total trades | **167** (NIFTY 123 + BNF 44) ~37/saal |
| Net | **₹71,279** (~₹15,800/saal) |
| Win rate | **79%** (132/35) |
| Profit Factor | **2.01** |
| Sharpe (combined) | **3.55** |
| Max Drawdown | **−₹13,458** |
| Avg / trade | ₹427 |
| Best day / Worst day | +₹9,674 / −₹9,521 (~9 avg-jeet) |
| Both same day | sirf 7 (zyadatar alag din = diversification) |

## 6. Key decisions (why-this-not-that)

- **Naked kyun, hedge kyun nahi?** Hedge (condor wings) margin ~4-5x ghatata hai PAR edge maar
  deta (IV filter ke saath BHI PF 2.04→0.38). Strategy far-OTM sasta premium bechti hai; wings us
  patli premium se zyada kharch kar dete. **Naked hi eklauta viable form.**
- **Naked "unlimited risk" nahi:** intraday SL actual loss ~₹5-9k/lot pe cap karta (overnight gap
  hai hi nahi). Sirf **MARGIN** high (~₹1.2-2L SPAN), **risk nahi** — broker SL ko nahi jaanta.
- **IV filter kyun mandatory:** bina uske low-IV din edge ko ~breakeven/loss kar dete; ye VRP ka
  asli condition (sell when premium rich).
- **DSR borderline pe kyun deploy-worthy?** DSR N=30 pe 0.45 (fail) par N=1-5 pe 0.77-0.99 —
  30 configs 30 **independent** ideas nahi (ek idea ke variations), toh fair N chhota. Aur DSR jo
  nahi dekh sakta wo hai **monotonic VRP relationship** (IV↑→PF↑ smoothly) = structure, luck nahi.
  Yehi standard VRP-condor pe bhi laga tha.

## 7. Portfolio role — ORB ka DIVERSIFIER (not standalone hero)

- Strangle standalone modest hai; **asli value ORB ke saath**: daily-P&L correlation **−0.50**
  (range din strangle jeeta, breakout din ORB) → ORB ka drawdown smooth + extra profit.
- **Timing:** strangle 9:20 pe enter (ORB ke 11:00 se pehle), **blind**. 47% strangle-dino pe ORB
  bhi fire karta — mutually exclusive nahi. ORB-quiet (range) din pe strangle sabse zyada kamati
  (+₹454, 88% win); ORB-fire (trend) din pe kam par phir bhi +₹264 (72% win) — dubti nahi.
- **Capital:** poori ORB family (6 long/debit strategies) 1-lot pe sirf **~₹41,000** (options
  KHARIDTI = premium-paid). **Ek short strangle akele ~₹1.2-2L** (SELL SPAN margin) = poori ORB
  family se 3-5x zyada. Toh 1 lot pe sab ₹10L cap me fit → **koi block nahi**. Blocking sirf
  strangle ke LOTS badhane pe.

## 8. Deploy pre-reqs (LIVE jaane se PEHLE)

1. **Forward-paper first** — short-vol backtest me hamesha achha dikhta jab tak asli crash na aaye.
   Chhoti size, 2-3 mahina paper, khaas kar high-vol/gap din watch.
2. **Capital Priority Reservation** (isko build kar rahe hain, separate) — strangle 9:20 pe pehle
   enter karti hai, ORB ka capital block kar sakti hai scaling pe. ORB/mission ka capital upfront
   reserve → strangle sirf discretionary/leftover le. (1 lot pe abhi zaroori nahi, scaling pe hai.)
3. **`risk_gate._ALWAYS_OVERNIGHT` me register NAHI** karna (ye intraday hai — 3:15 squareoff hi sahi).
4. **Backtest-fidelity (Rule 10):** jo config yahan validate hui (IV≥0.5, T40/SL40, 9:20) wahi live
   me — koi extra tweak (trailing/extra-filter) live pe daala to ye number jhooth ho jayega.
5. **Live IV-rank source:** 9:20 pe IV-rank chahiye → India VIX / option-chain collector se
   (repo me `algo-optionchain` collector + `india_vix_daily.csv` hai).

## 9. Honest caveats / what's NOT done

- **2021-26 me koi COVID-scale crash nahi** → tail-stress heuristic, asli limit-lock crash SL se
  zyada slip kar sakta (rare). Ye forward-paper candidate hai, deploy-on-backtest nahi.
- **BANKNIFTY forward doubling nahi** (weekly discontinued) — forward ~NIFTY-only ~37/saal.
- **Monthly-BANKNIFTY / doosre index** (FINNIFTY/SENSEX) explore nahi kiye — frequency badhane ka
  future scope.
- **Capacity modelled nahi** — kitne lots tak fills degrade nahi honge, wo naapa nahi.
- Combined stats me BANKNIFTY sirf ≤Nov-2024 contribute karta (recent NIFTY-only).

## 10. FAQ (plain Hinglish)

**Q: Ye strategy ek line me?**
Roz 9:20 pe, agar IV rich ho (premium mota), pichle 10-din ke high pe CE aur low pe PE **becho**,
usi din 40-pt decay book / 40-pt SL / 3:15 exit. Range-bound din pe theta kama lete, no overnight.

**Q: Sabse bada risk?**
Ek din ki bad move — par intraday SL ~₹5-9k/lot pe cap karti (overnight gap nahi). "Margin-hungry"
hai (₹1.2-2L SPAN) par "khatarnak" nahi. Live se pehle forward-paper.

**Q: Akele deploy karun?**
Behtar hai **ORB ke saath dessert** ki tarah — akela modest (~₹10k/saal/lot), par ORB se −0.5
correlated hone ki wajah se poori book ka drawdown ghatata + extra profit deta.

**Q: IV filter hata dun to zyada trades?**
Haan par edge ~aadhi (PF 2.04→1.41) aur tail-stress fail. IV filter hi is strategy ki jaan hai.
Zyada trades chahiye to IV≥0.3 (37/saal) — thoda kam quality par phir bhi solid.

**Q: Hedge laga ke margin ghata dun?**
Nahi — hedge edge maar deta (PF 2.04→0.38). Margin ko **sizing + capital-reservation** se manage
karo, wings se nahi.

**Q: Kitna kama sakti hai realistically?**
1 lot NIFTY ~₹10k/saal; +BANKNIFTY historical ~₹15.8k/saal. Lots se scale karti par tail bhi —
DD-budget se size karo. Ye main income nahi, **portfolio smoother + bonus** hai.

---

## Files (scratch/nifty_trend/)
- `probe_range_strangle*.py` — journey probes (positional/intraday/orb/target)
- `probe_weekly_pivot_strangle.py` — R3/S3 pivot variant (rejected)
- `probe_bnf_strangle.py` — BANKNIFTY runner (loader repoint + step 100)
- `build_range_strangle_intraday.py` — 3-pass build → `runs/range_strangle_intraday/`
- `correlate_strangle_orb.py` — ORB correlation + portfolio + capital
- `gate_softorb.py` — soft-ORB gate (rejected)
- Data-lake: `_TRADING_DATA/OptChainLake/{NIFTY,BANKNIFTY}/WEEK/` (5-min, real premium)
