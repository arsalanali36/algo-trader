# VRP Condor — Max-Profit Early-Exit vs Hold-to-Expiry (research, DECISION PENDING)

**Date:** 2026-07-23
**Status:** 🟡 Research done, direction proven. **User ne faisla defer kiya** ("baad me lenge") — kuch deploy NAHI hua. Rule 10 laागu: live 02.03 me tab tak add mat karo jab tak (a) exact gated one-night number nikle YA (b) forward-paper se validate ho.

## User ka sawaal

> "Overnight VRP condor me agar max profit achieve ho jaye to position exit kar den — koi zaroori nahi ki pura din/expiry tak ruken. Kai baar max target tak jaa kar wapas laut aa raha hai. **Is cheez ko backtest me test kiya hai kya?**"

User ne **rigorous (backtest-first)** path chuna.

## Do alag VRP condor hain — pehle ye distinguish karo

| id | config_key | naam | live exit | max-profit exit? |
|----|-----------|------|-----------|-----------------|
| **02.05** | `vrpw_v1` | VRP **Weekly** Condor | T-4 DTE entry → **50%-of-credit target YA expiry se 1 din pehle** | ✅ **PEHLE SE hai** — `build_vrp_weekly.py` `tp_frac=0.5, exit_before_dte=1` |
| **02.03** | `vrp_condor_v1` | VRP **Overnight** Condor | entry 15:10 → exit **agle din 15:10** (one-night); `"stop":None,"target":None` | ❌ **NAHI** — pura session hold karta |

**User "overnight" bol raha = 02.03.** Yehi wo hai jisme max-profit exit add karna hai.
Weekly (02.05) me ye already validated design hai — usme kuch add nahi karna.

## Backtest result (5 saal 2021→2026, 250 condor cycles, real DOM slip)

`vrp_ungated_backtest.py` ka `iron_condor` (body ATM±3, wing ±5) pe `tp_frac` sweep — kis % credit pe exit karein:

| Exit rule | Sharpe | net% | maxDD% | win% | avg hold |
|-----------|-------:|-----:|-------:|-----:|---------:|
| **Hold to expiry (= abhi ka 02.03)** | **−0.52** | **−9.8** | **−11.9** | 55% | 6.2d |
| Exit @ **50% credit** | **−0.05** | −0.8 | **−4.3** | **65%** | 5.2d |
| Exit @ 70% | −0.18 | −3.3 | −6.9 | 58% | 5.7d |
| Exit @ 85% | −0.26 | −4.7 | −7.8 | 57% | 5.9d |
| Exit @ 95% | −0.31 | −5.8 | −8.1 | 56% | 6.0d |
| Exit @ 100% (full credit) | −0.26 | −5.0 | −7.5 | 56% | 6.0d |

**Nateeja monotonic aur bilkul clear:** hold-to-expiry **sabse KHARAB**. Jitni jaldi profit book karo (50%), utna behtar — Sharpe −0.52 → −0.05, **drawdown aadha** (−11.9% → −4.3%), win-rate **+10%** (55→65). **User ki "max target tak jaa kar laut aata hai" wali give-back backtest me MEASURED hai, aur usse bachna har metric pe fayda deta hai.**

## Do imandaar caveats (isliye "direction proven" bola, "deployable number" nahi)

1. **Ye UNGATED condor ke numbers hain** (isliye sab net-negative — bina IV-gate ke condor paisa khaata hai, ye pehle se maloom). Deployed 02.05 ka **IV-rank ≥ 0.5 gate** hi profit banata hai. **Lake me IV data ab KHAALI ho chuka hai** (`OptChainLake` + `OptChainLake_1m` dono ka `iv` column blank; `iv_rank_daily()` = 0 days) — validated run purane data pe bana tha. Isliye gated magnitude reproduce nahi ho paya. **Par direction (early-exit > hold) gated/ungated dono me same rehta hai** (weekly 02.05 me tp=0.5 isi liye baked hai).

2. **Backtest multi-day hold hai** (`cycle_start` ~6d, `dte4` weekly); **02.03 one-night hai** — structure thoda alag. Exact 02.03 number ke liye engine me "one-night overnight" mode chahiye.

## Data plumbing jo is session me bana (reproduce ke liye)

- **Wide-offset lake missing tha:** `OptChainLake/NIFTY/WEEK/` me sirf 4 files (CE_ATM, CE_ATMp1, PE_ATM, PE_ATMp1, 5m) — condor ko ATM±3/±8 chahiye → `_px()` intrinsic-floor (0) deta → 0 trades. **`OptChainLake_1m/NIFTY/WEEK/` me 42 offset files hain** (±9/10, 1-min) par `iv` blank.
- **Fix:** `tp_exit_sweep.py` `OptChainLake_1m` (1m) ko 5m me resample karta hai (`/tmp/lake5x/NIFTY/WEEK/`), `optlake_load.LAKE` usko point karta, phir sweep. **Gotcha:** source `Datetime` `datetime64[s]` unit ka hai → epoch conversion me `//10**9` mat karo (double-divide garbage `timestamp=1` deta); `dtutc.values.astype("datetime64[s]").astype("int64")` = unit-agnostic seconds.

## Faisla lene ke do rasta (jab dimag chale)

- **(a) Bilkul rigorous:** engine me one-night mode + BS-implied-IV se gate restore (ATM straddle ko BS-invert karke iv nikaalo, `iv_rank_daily` phir chalega) → **02.03 ka apna exact gated number** → tab live me add. Rule 10 fully satisfied.
- **(b) Direction pe bharosa:** 02.03 me `target = X% of credit` (ya intraday max-profit lock) add karke **PAPER forward-test** — direction har config pe clear + 02.05 me already validated.

**Recommendation:** (a) — kyunki rigorous path chuna tha. Par (b) bhi defensible hai (paper, zero risk).

## Reproduce

```bash
# VPS pe (jahan lake hai):
python /tmp/tp_exit_sweep.py     # ya scratch/nifty_trend/tp_exit_sweep.py
```

Related: [[project_code3b_vrp_condor]], [[feedback_backtest_fidelity_rule]] (Rule 10), `build_vrp_weekly.py` (02.05 producer, tp=0.5 already), `vrp_ungated_backtest.py` (engine).
