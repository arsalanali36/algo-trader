# ADR-004: Real option-chain data-lake (Dhan rollingoption) + real-premium backtest layer

## Status: Decided (2026-07-11)

## Context

Saari option strategies ab tak **Black-Scholes-from-realized-vol** (`scratch/nifty_trend/
bs_option.py`) pe backtest hoti thi — kyunki maana gaya tha ki Dhan expired-weekly ka
historical premium drop kar deta hai (LESSONS TRAP #100) aur real IV/OI historical milta
hi nahi. Iski do badi seemayein thi:
- **Sellers validate nahi hote** — BS-from-realized VRP ko price-away kar deta hai, to
  koi bhi net-short-premium structure (short straddle/condor/fly) hamesha dead dikhta tha
  (LESSONS TRAP #106). Portfolio ki "inverse leg" (theta/short-vol) banai hi nahi ja sakti thi.
- **OI/PCR/greeks strategies (Track-B) blocked** — historical chain data ke bina.

2026-07-11: user ke Dhan account pe **paid "Expired Options Data"** add-on nikla. Live-probe
se confirm hua ki `POST /v2/charts/rollingoption` REAL 5-year expired-option premium + IV + OI
(rolling ATM±N CE/PE, weekly+monthly) deta hai. Sawaal: is data ko kaise integrate karein
bina existing BS pipeline toDe?

## Decision

**Real premium ko ek SEPARATE, parallel data + engine layer banao — BS ko replace nahi,
uske bagal me. Data lake pe, live token ke paas (VPS).**

1. **Downloader** (`scratch/nifty_trend/optchain_dl.py`): rollingoption → per-series CSV
   (`_TRADING_DATA/OptChainLake/NIFTY/<WEEK|MONTH>/<CE|PE>_<off>.csv`). Resumable manifest,
   `dhan_rate_limiter` **"account"** (lowest) priority so live orders kabhi na ruke,
   recent-first + ATM-outward order taaki usable data pehle aaye. Exact API params +
   gotchas: session-memory `dhan_rollingoption_data`.
2. **Loader** (`optlake_load.py`): epoch→IST, IV-outlier clean, resample, ATM/iron-fly/chain
   frames. **Coverage-guard** (TRAP #107): adhura-download series inner-join me silently
   truncate karke bogus backtest de sakti hai → `ironfly_frame` 90%-day-coverage assert
   karta hai, warna `None`.
3. **Real-premium backtest engine** (`real_struct.py`): `bs_option`/`option_structures` ka
   REAL-premium sibling — same engine-shaped `res`, same `engine.metrics`/montecarlo/3-pass
   dashboard, bas legs REAL premium+IV se price hote hain (BS nahi). `slip_frac` knob
   (bid-ask+impact haircut).
4. **Backtest surface routing (kab kya use karein):**
   - Net-LONG-premium / directional (straddle/vertical/backspread) → BS-from-realized OK
     (`bs_option`, existing) YA real (behtar). Dono chalta hai.
   - **Net-SHORT-premium (theta/VRP) → REAL premium MANDATORY** (`real_struct` + lake).
     BS-from-realized se kabhi validate mat karo (TRAP #106).
   - OI/PCR/max-pain/IV-rank (Track-B) → real lake (OI+IV columns).
5. **Data + compute co-located on VPS** — lake bada hai (~GB) aur token VPS pe hai, isliye
   real-data backtests VPS pe chalte hain (local nahi). Local repo me sirf CODE, data nahi.

## Consequence

- ✅ **Track-B unlocked 5 saal ka backtest** — hafton ke forward-collector ka wait khatam.
  Short-vol iron-fly (#06) real premium pe Sharpe 8.9 (BS pe −2.24 tha), portfolio ki asli
  inverse leg (corr −0.05..−0.11 vs ORB-family) ban gayi.
- ✅ **BS pipeline untouched** — existing #01-#05 dashboards jaise the waise. Naya layer
  additive hai, koi regression nahi.
- ⚠️ **Token-dependency + tier-dependency** — rollingoption paid add-on chahiye (17-Jul tak
  active); token roz refresh hota hai (downloader har call pe re-read karta hai). Add-on
  lapse hua to real backtests band, BS fallback rehta hai.
- ⚠️ **Slippage/fill imaandaari** — lake OHLC deta hai, bid-ask nahi. `slip_frac` parametric
  haircut hai (0.5% default, sweep dikhata hai edge slippage-robust hai). Real spread se
  calibrate karna future work (brother's DOM data se, user local laayega — DO-NOT-TOUCH VPS).
- ⚠️ **VPS-only compute** — real-data strategies VPS pe hi run/rebuild hoti hain; local sirf
  code edit + dashboard-parity. Backtest chalane ke liye code sync (`scp *.py`) + VPS execute.
- **Greeks:** rollingoption IV deta hai, greeks nahi — greeks REAL IV se BS-formula pe exactly
  compute karo (approximation nahi).
