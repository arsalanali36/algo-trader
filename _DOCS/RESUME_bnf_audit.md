# RESUME — 02.10.01 BNF audit (2026-08-31)

> Yeh file **"kahan chhoda tha"** batati hai. Kaam khatam hote hi ise update ya delete karo.
> Poori kahani: LESSONS TRAP #197/#198, ADR-023. Commits `a3cf897` … `2cd00f9`.

---

## Shuruaat

User: *"backtest keh raha hai 5 lot pe sirf EK baar ₹4,000 tak loss gaya, par real me
teen din se lagataar aur usse zyada — ye kaisa kachra backtest hai?"*

User sahi tha. Backtest ka **logic** theek tha; uske **numbers** jhooth bol rahe the —
teen alag wajah se, aur teenon ka rukh **profit ki taraf**.

---

## Kya-kya mila (sab naapa hua, andaaza nahi)

### 1. Lot-scale — page 5× chhota (TRAP #197, fix `a3cf897`)
Exit rule `* qty` (5 lot) pe, P&L `* lot` (1 lot) pe. Page ne 1-lot numbers pe "5 LOTS" label lagaya.

| | page | sach |
|---|---|---|
| worst trade | −₹10,428 | **−₹51,383** |
| avg loss | −₹1,408 | **−₹6,335** |
| >₹4k loss | 3/874 | **383/874 (44%)** |
| maxDD | −5.5% | **−23.2%** |

User ke asli losses (24/27/28/31 Aug) **corrected distribution ke andar** the.

### 2. Basket MTM ka apna shor — stop se sirf 2× chhota
Har entry ke turant baad ka pehla MTM:

| din | 4 legs premium | pehla MTM | points | stop (26.7pt) ka % |
|---|---|---|---|---|
| 24 Aug (Aug exp) | ₹66 | +₹18 | 0.15 | 0.5% |
| 26 Aug (Sep mly) | ₹1,686 | **+₹1,380** | 9.2 | 34% |
| 31 Aug | ₹1,661 | **−₹2,163** | 14.4 | **54%** |

26 Aug ko Sep monthly pe roll → legs **25× mehnge** → spread bhi → stop wahi.
Shor **dono taraf** jhoolta hai (26 Aug +1,380) = ye loss nahi, **naapne ka error** hai.

**KEY MATH:** noise/stop ratio **lots se nahi badalta** (dono lots se linear, cancel).
1 lot pe bhi 54%, 20 lot pe bhi 54%. Isliye "qty adjust karo" is problem ko **hal nahi karta**.

31 Aug ka SL: slippage **₹1,974 = stop ka 49%**. Bina slippage position −₹3,022 pe thi —
**stop lagta hi nahi**. Spot sirf **68 point** (0.12%) hila tha.

### 3. Real bid/ask study (`scratch/spread_study/`, `d1d6d1b`)
Collector 10-Jul se `ltp,bid,ask` per-minute likh raha hai (124 strikes) — data pehle se tha.

```
                          FIXED 26.7 pt      50% of CREDIT
BNF monthly  (median)          17.0%              3.0%
  DTE 1-7                       5.3%              1.8%
  DTE 22-40 (naya monthly)     24.9%              4.1%
NIFTY weekly                    1.1%              1.4%
```
Fixed stop monthly cycle ke saath **5× jhoolta** hai; credit-linked **flat** rehta hai.
NIFTY dono rules pe theek → **02.15 ka accha chalna uske %-exit ka saboot NAHI hai**.
24 Aug ko (Aug expiry se 1 din pehle) Sep chain ka spread **263 point** = fixed stop ka
987%, credit-linked ka 242% → **koi exit rule khaali book se nahi bachata**, iske liye
alag **spread-gate** chahiye.

### 4. Lake contamination (TRAP #198, `dff7996`) — sabse bada
Lake **ATM-relative** (offset window), trade **fixed strike**. ATM khiskte hi strike window
se bahar → `_px` chupchaap **intrinsic** (OTM = 0) → "short muft me buy-back".

| | trades | net | avg | win% |
|---|---|---|---|---|
| contaminated | **167 (19.1%)** | **₹40.4L** | +₹24,211 | **88.0%** |
| clean | 707 | ₹10.1L | +₹1,423 | 48.2% |

**19% trades me 80% profit.** Clean subset bhi jawab nahi (kam-movement din ka biased sample).
Wings real ho hi nahi sakte the — wo offset **±11** pe hain, lake **±10** tak thi.

---

## Halat abhi (2026-08-31)

```
LIVE + active strategies      : KOI NAHI
bnf_strangle_hedged (02.10.01): PAPER, active   <- [PX] data collect kar rahi
chainzone_v1                  : BAND (BS-only proof pe live tha)
02.17 weekly iron-fly         : SAFE — alag reader (_prem -> None -> skip)
run pages                     : 8 REAL premium | 29-31 NOT PROVEN badge
audit                         : 0 FAIL
```

**Download chal raha hai (LOCAL):** `optchain_dl.py --underlying NIFTY,BANKNIFTY
--interval 1 --off-range 20` → log `logs/optchain_dl_off20.log`. Resumable.
Target: har `<SYM>/<FLAG>/` me **82 files** (41 offsets × CE/PE).
Dhan Data-API add-on **flat ₹499/mo, 18-Sep-2026 tak** — extra charge nahi.

---

## AGLA KAAM — isi kram me

1. **Download poora hone ka wait** (`tail logs/optchain_dl_off20.log`, files 82/82).
2. **`python scratch/nifty_trend/lake_coverage_check.py --hold 1`**
   → BANKNIFTY/MONTH pe 02.10.01 ke legs **~100% CLEAN** aane chahiye (aaj 0.0%).
   Na aaye to lake aur chaudi karo — aage mat badho.
3. **`python scratch/nifty_trend/honest_bnf_backtest.py`**
   → pehla sacha number. `skip` count **0 ke paas** hona chahiye; zyada ho to bharosa mat karo.
4. **Kal ka `[PX]` log padho** (`logs/bnf_strangle_hedged.log`, `86ddf58` se):
   `src=ask` = sahi jagah order; `src=feed_ltp` = last-trade pe → BUY bid pe baithta hai →
   fill nahi → chase. Isse **spread vs 42-second leg-risk** alag ho jayenge.
5. Uske baad hi stop-rule ka sawaal: credit ka %? spread ka multiple? **koi number bina
   backtest ke mat bolo** ([[feedback_no_unbacktested_money_knobs]]).

---

## Jo AB BHI khula hai

- **02.10.01 ka imaandar number abhi bhi unknown** — negative bhi ho sakta hai. User ne
  bola: *"koi baat nahi, acchi nahi hui to reality to pata rahegi."*
- **Spread-gate ka threshold** — 987%-wale din skip karne ke liye. Data chahiye.
- **Baaki 29 NOT PROVEN runs** — kai deployed hain, koi re-priced nahi hua.
- **NIFTY/WEEK 02.17 ke legs 98.0%** (±18 window pe) — 100% nahi; `_prem` skip karta hai
  isliye imaandar hai, par kuch trades gir jaate hain.
- **`real_struct2.py`** me `_px` ki doosri copy — ab loud hai, par uske runs kabhi
  re-check nahi hue.

---

## Guards jo lag chuke (dobara na ho)

`_assert_lot_scale` · `_assert_lake_coverage` · audit check 11 `LAKE-SILENT-INTRINSIC` ·
provenance badge (meta se derive) · `base.STRICT`/`oob_report()` · `strict_skip` ·
`lake_coverage_check.py` · `load_grid`/`_px` ka window **disk se auto-detect** (`g["WIN"]`).

Detail: **ADR-023**.
