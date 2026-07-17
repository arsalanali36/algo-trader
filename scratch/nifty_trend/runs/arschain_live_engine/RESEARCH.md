# Ars chain — becho ya kharido? (2026-07-17 ka poora research record)

> **Ye kya hai:** ek din ka poora sawaal-jawaab, saboot ke saath. Kis baat ka saboot hai
> aur kis ka nahi — dono likhe hain. Ye **validated run NAHI hai** (na significance test,
> na train/OOS, na Monte-Carlo). Ye **evidence ka record** hai.
>
> **Isko kab padhna hai:** jab drawdown chal raha ho aur dimag kahe "band kar do". Tab ye
> batayega ki kaunsi baat naapi hui thi aur kaunsi sirf lag rahi thi.

---

## 🔴 FAISLA — significance test (jo poore din nahi hua tha)

`arschain_significance.py` — repo ka apna rotation-permutation, 1000 perms, spot pass:

```
2018-2026 (poora)         1950 trades | Sharpe 0.50 | null p95 0.55 | p=0.072   FAIL
2026 sirf (TV wala daur)   104 trades | Sharpe 0.66 | null p95 2.39 | p=0.371   FAIL
```

**User ne TV dikhaya: PF 2.024, +₹94,078 (+9.41%), 110 trades, Jan-Jul 2026.** Wo daur
sach me achha hai — mere data me bhi (2026: 104 trades, spot +59,995; 2025: **−64,587**).
**Par wo luck se alag NAHI hai.**

`null p95 = 2.39` sabse ahem number hai: **usi position series ko RANDOM jagah ghuma do**
— bilkul bekaar strategy — aur wo 6.5-mahine ki window me routinely **Sharpe 2.39** tak
pahunchti hai. Asli **0.66** hai. Random usse **37% baar** hara deta hai.

> **6.5 mahina / 110 trade itne chhote hain ki wahan sab kuch achha dikhta hai.**
> PF 2.0 dekhkar deploy karna theek isi jaal me girna hai. Gate isi liye banaya tha.

Poore 8.5 saal pe **p=0.072** — 0.05 ke kareeb, paar nahi. Signal me shayad kuch **dubla**
hai, par sabit nahi hota.

### ML se optimise kar sakte hain?
**Nahi — ML ki kami se nahi, balki optimise karne ko kuch hai hi nahi.** ML maujood pattern
dhoondhta hai; base p=0.371 hai. 104 trades pe mining = **noise me pattern** (wo hamesha
mil jaata hai) → live me marega. **Is repo me ye ho chuka hai:** ML mining Tasks 0-4,
**~4 million rules, ZERO ne gate paar kiya**; best GP rule val-Sharpe 2.25 par **DSR 0.02
vs ~3.9 bar**; ek "golden rule" (val-Sharpe 7.1) **94% expiry-day artifact** nikla
(TRAP #114). `ml_gate.py` ka deflated-Sharpe theek isi ke liye hai — aur wo sahi karega.
**ML tab kaam ka hai jab edge maujood ho aur use sharpen karna ho. Pehle edge sabit karo.**

### Bacha hua ek raasta
p=0.072 wala dubla edge option ke kharche (1.06 pt/trade) me se nikalna lagbhag namumkin
hai. **FUTURES** — DOM me spread **0.008%** naapa hua vs option 0.11-0.16% (**15x sasta**),
na theta, na VRP, delta 1. **Aaj tak kabhi dekha nahi gaya. Agla kaam wahi hai.**

---

## 🛑 Ek line me — aur ye wo NAHI hai jo is doc ne din bhar kaha

**Is signal ka apna edge 1.2 point/trade hai. Use trade karne ka kharcha 1.06 point hai.
Bas. Kuch nahi bachta.**

```
① Instrument/spot   1,949 trades   +152,777   win 39.7%   PF 1.07
                                                          ^^^^^^^^
                    ₹100 haar ke ₹107 kamana — aur broker+spread ₹69/trade le jaate hain

1.20 point   signal ka edge (kaatne se PEHLE)
-1.06 point  charges (₹56) + slippage (₹13) = ₹69/trade
------------
~0.14 point  = ₹9/trade bacha
```

**Isi liye is doc me kuch bhi kaam nahi kar raha:** BUY/SELL, ATM/ITM, trailing, target,
VRP — sab us **0.14 point** ko idhar-udhar ghuma rahe the. Neeche kuch tha hi nahi.

### Aur "becho mat, kharido" wala natija ASLI PREMIUM PE GIR GAYA

Poora doc **BS-modelled premium** pe likha gaya. Usi shaam **asli lake premium** pe wahi
trades reprice hue (`real_struct2._px`, held-strike) — **jawaab ULTA:**

| wahi 1,100 trades, wahi 2021-07→2026-07 window | BS @1.0 | BS @1.3 | **ASLI** |
|---|---|---|---|
| **BUY** dono OFF | +2,19,257 | +48,538 | **−2,23,839** |
| **SELL** dono OFF | −5,93,858 | −4,36,444 | **−96,109** |

**Asli premium pe DONO haarte hain, aur BUY ZYADA haarta hai.** Do confound jaanche, dono
clean: daur ka nahi (BS usi window pe chalaya), `_px` fallback ka nahi (exit pe held strike
97% baar ±2 ke andar, window bahar kabhi nahi, fallback **0/1100**).

> **Isliye: is doc me BUY-vs-SELL ka koi bhi number mat uthao. Wo BS ke andar ka sach tha.**
> Baaki cheezein (distribution, MFE/MAE, trailing, 2-trade ki ginti, asli VRP naap) apni
> jagah theek hain — par wo sab bhi **us 0.14 point ke upar** ka shor hai.

**Agla sawaal (aur pehla bhi yahi hona chahiye tha):** kya 1.2 point sach me hai?
**p-value kabhi nikala hi nahi.** Aur agar hai, to shayad **futures** pe bache — DOM me
FUT spread **0.008%** naapa hua hai vs option 0.11-0.16% (**15× sasta**), na theta, na
VRP, delta 1.

---

## Sawaal kahan se aaya

Arsalan ki thesis thi: *"decay (theta) bechne wale ko faida deta hai, isliye bechna
chahiye."* Wajah waajib thi — aur live config wahi karti hai (`opt_action=SELL`).

Poochha gaya: *"dono side nikalo — CE kharidna vs PE bechna."*

---

## METHOD (taaki baad me bharosa rahe ki kaise bana)

- **Engine:** `range_trader.run_signal_engine` — **live wala, koi copy nahi**
  (`trades_out=` collector se). Ye ahem hai: is repo me pehle "backtest copy" aur "live
  engine" alag ho chuke hain aur unke jawaab alag the (LESSONS TRAP #131).
- **Data:** 2018-01-01 → 2026-07-09, **2,103 trading din, 1,950 trades**, NIFTY 5m.
- **Warm-up:** 6 din — **theek utna jitna live ko milta hai** (`days_back=5`). Zyada dena
  matlab ek aisi strategy naapna jo hum chalate hi nahi.
- **Daily bars:** 1-min files se banaye gaye, `nifty_daily.csv` ko haath nahi lagaya
  (TRAP #126 — ek script ne 4 saal ka data uda diya tha).
- **Pricing:** `bs_option.py` — Black-Scholes ATM premium, **asli Zerodha charges**
  (date-aware) + **DOM se naapi hui slippage**.
- **Lot:** 65, scrip master se. 1 lot, koi compounding nahi.

### ⚠️ Method ki 3 seemayein — inhe yaad rakhna

1. **BS ka modelled premium hai, asli expired-option data nahi.** Asli data
   (`OptChainLake`) maujood hai — is se dobara naapna abhi baaki hai.
2. **Sigma = realised vol.** Ye **kharidne wale ke haq me jhukta hai** (neeche VRP wala
   section). Isi liye har BUY number ki upar wali seema hai, neeche wali nahi.
3. **Model IV crash (vol crush) dekh hi nahi sakta** — entry aur exit pe wahi sigma
   lagata hai. Asli bazaar me IV girti hai; us waqt nuksan model se zyada hoga.

---

## 1. BECHNA vs KHARIDNA — sabse mazboot natija

Ek hi signal, ek hi trades, sirf structure badla (1 lot, ATM):

| pass | trades | NET ₹ | win% | PF |
|---|---|---|---|---|
| ① Instrument (spot points × 65) | 1,949 | +1,52,777 | 39.7% | 1.07 |
| ③ **BUY ATM** | 1,949 | **+2,13,524** | 35.8% | 1.20 |
| ③ SELL ATM (naked) | 1,949 | **−3,30,342** | 40.6% | 0.73 |
| ③ SELL spread (defined risk) | 1,949 | −4,29,848 | 38.7% | 0.65 |

### Seller ko VRP tohfe me diya, phir bhi haara

Aitraaz waajib tha: *realised vol pe bechne wale ko VRP milta hi nahi — TRAP #106 me
iron-fly BS pe −100% padhi thi aur asli premium pe +61%.* To seller ko VRP de kar dekha:

| vrp_mult | SELL net ₹ | PF | matlab |
|---|---|---|---|
| 1.0 | −3,30,342 | 0.73 | realised vol |
| 1.2 | −2,44,689 | 0.80 | **IV 20% upar — NIFTY ka aam** |
| 1.5 | −1,38,746 | 0.88 | IV 50% upar (bemaani udaar) |

**IV 50% upar maan lo — tab bhi PF 1.00 tak nahi pahunchta.** Ye BS ka artifact nahi hai.

**Kyun:** ye edge **bade winners** deta hai. Bechne me aap winner **cap** kar dete hain.
Jo VRP milta hai (kuch sau rupaye/trade) wo ek kate hue 300-point winner ki bharpai nahi
kar sakta. Isi liye seller ka win% ZYADA hai (40.6% vs 35.8%) par paisa KAM.

### Do alag engine, ek hi jawaab

| | BUY | SELL |
|---|---|---|
| `intraday_engine.chain_zone` (2026-07-16) | +₹4,43,539 | −₹3,93,963 |
| **live `range_trader`** (2026-07-17) | **+₹1,93,645 … +₹4,84,161** | **−₹3,30,342** |

Alag implementation, alag din, wahi nishaan. Isse pehle wala finding *"PROVEN NAHI —
live engine pe kabhi nahi chala"* flag ke saath tha. **Wo flag ab hat gaya.**

---

## 2. ATM vs ITM — Arsalan ka sawaal, jawaab ULTA nikla

Sawaal: *"ATM dekha, ITM 1/2/3/4 pe jayen to? NIFTY me liquidity ka masla nahi hai."*

Liquidity ki baat sahi thi. Natija phir bhi ulta:

| strike | NET ₹ | PF | maxDD ₹ | capital/lot |
|---|---|---|---|---|
| **ATM** | **+1,93,645** | **1.18** | −57,088 | **4,572** |
| ITM 1 | +1,08,733 | 1.08 | −89,421 | 6,498 |
| ITM 2 | +38,085 | 1.02 | −1,13,188 | 8,861 |
| ITM 3 | −12,426 | 0.99 | −1,43,407 | 11,487 |
| ITM 4 | −64,443 | 0.97 | −1,83,342 | 14,291 |

**Jitna andar, utna bura — aur capital 3× zyada.**

**Kyun:** ITM option seedhi line me chalta hai (delta ~1, gamma kam). ATM me gamma hai.
Jo bade winner is edge ki jaan hain, ATM unme **multiple** deta hai, ITM sirf **jodta**
hai. Ulta side bhi: adverse move pe ATM ka delta kam hai, to nuksan bhi kam.
**Jo cheez ITM ka faida lagti hai (zyada delta), wahi is edge ke liye zeher hai.**

**Sabse achha strike sabse sasta bhi hai.**

---

## 3. SCALING — Arsalan ki baat, aur wo socha usse behtar nikli

Unka point: *"bechne me ₹1.5L margin lagta hai, kharidne me ₹25-30k — to lot badha
sakte hain, scale kar sakte hain."*

ATM ka asli capital/lot (9-saal ka ausat bekaar hai — 2018 me NIFTY 10,500 tha):

| saal | trades | avg cap/lot | sabse mehnga |
|---|---|---|---|
| 2024 | 228 | 5,854 | 23,254 |
| 2025 | 216 | 5,302 | 17,254 |
| **2026** | **104** | **8,477** | **21,536** |

**Jis ₹1.5 lakh me 1 lot bechte hain, usi me ~7 lot kharid sakte hain** (mehenge din pe
bhi), aam din **~17 lot**. Unka andaaza ₹25-30k tha; asli ₹8.5k hai.

---

## 4. EXIT RULES — "trailing SL dushman hai" — sach nikla

Sawaal: *"aap keh rahe ho mera trailing SL trade jaldi kaat de raha hai?"*

Pehla ishara exit-reason breakdown se aaya (`ATR_TRAILING` 1,326 trade, net −₹4.16L
jabki `3:15 Daily Exit` 359 trade, +₹4.68L) — **par wo saboot NAHI tha**: trailing pe
wahi trade katte hain jo khilaaf gaye; wo bina trailing ke jeetne nahi lag jaate.

Asli test = engine ko trailing OFF karke **dobara chalao**:

| config | trades | NET ₹ | win% | PF | maxDD ₹ |
|---|---|---|---|---|---|
| **jaisa abhi hai** (trail ON, zone ON) | 1,950 | 1,93,645 | 35.9% | 1.18 | −57,088 |
| trail OFF (zone ON) | 1,740 | 3,76,266 | 42.1% | 1.28 | −69,366 |
| zone OFF (trail ON) | 1,949 | 2,13,524 | 35.8% | 1.20 | −58,035 |
| **dono OFF** (3:15 + reversal) | 1,723 | **4,84,161** | **42.7%** | **1.35** | **−45,954** |

**Har metric pe behtar — net 2.5×, win% 36→43, aur maxDD bhi kam.**

### "dono OFF" ka matlab kya hai

Pehla signal milte hi ghus jao, **ulte signal pe palat jao** (REVERSAL = purani band +
nayi chalu, *usi bar, usi price pe*), 3:15 pe flat. Beech me kabhi bahar nahi baithte.

### Aur ismein sabse gehri baat

Arsalan ne poochha: *"pehli entry ka exit 3:15 pe hoga to doosri entry ko jagah kahan?"*

Ginti:

```
1 trade waale din:  784        exit reasons (abhi):
2 trade waale din:  583          ATR_TRAILING    1326
koi trade nahi:     736          3:15 Daily Exit  359
                                 ZONE_SHORT       150
                                 ZONE_LONG        115
                                 REVERSAL           0   <-- ek bhi nahi
```

**583 din pe do trade hote hain (43% active din) — aur REVERSAL ek bhi nahi.** Matlab
abhi doosra trade reversal se nahi aata: **trailing SL pehle trade ko kaat kar jagah
banati hai, phir naya signal ghusta hai.** Exits OFF karo to wo 227 trade khatam ho
jaate hain.

**Aur asli baat:** 227 trade **KAM** lene ke bawajood paisa ₹1.93L → ₹4.84L.
Yaani wo "doosre" trade milaakar **nuksan** de rahe the. Trailing kaatti hai, strategy
dobara ghusti hai, aur wo re-entry paisa khaati hai.

### 🛑 Ye sirf KHARIDNE pe laagu hai

"SL ki zaroorat nahi" is liye sach hai kyunki **kharide hue option me nuksan premium pe
capped hai**. Live strategy abhi **BECHTI** hai — wahan trailing hatana khatarnaak hai,
kyunki bechne me nuksan ki koi chhat nahi. **Dono cheezein saath badalti hain, ya koi
nahi.**

---

## 5. Har trade ka distribution (ATM, 1 lot, 1,950 trades)

| percentile | asli P&L | MFE (peak pe kitna de raha tha) | MAE (kitna khilaaf gaya) |
|---|---|---|---|
| p1 | −2,629 | 20 | −2,622 |
| p25 | −923 | 321 | −1,067 |
| **p50** | **−366** | **851** | **−718** |
| p75 | +461 | 2,074 | −445 |
| p90 | +2,090 | 4,143 | −268 |
| p99 | +8,208 | 11,475 | −67 |
| **worst** | **−5,292** | 0 | −5,284 |
| **best** | **+28,596** | 34,983 | −5 |

**Beech ka trade HAARTA hai (−366). Poora paisa upar ke 10-25% se aata hai.**
👉 Isi liye koi bhi target lagana is strategy ko maar deta hai.

### Target sweep — har target paisa khaata hai

```
SL \ TP       koi nahi     2,000     3,000     5,000     7,500    10,000    15,000
koi nahi           +0%      -79%      -50%      -25%      -16%      -15%      -17%
1,500              -4%      -74%      -43%      -18%       -8%      -10%      -18%
3,000              +2%      -77%      -48%      -23%      -14%      -12%      -15%
5,000              +0%      -79%      -50%      -25%      -16%      -14%      -17%
```

- **Target: koi nahi.** ₹2,000 ka target = −79%. MFE p99 = 11,475 — target lagao to
  wahi 1% trade kat jaata hai jo poora saal kamata hai.
- **SL: koi bhi SL kuch nahi deta.** Sabse achha ₹3,000 = +2.2% = **noise**.

### Max loss — kya maan kar chalein

| | |
|---|---|
| **Pukka (ganit)** | nuksan **premium tak hi** — 2026 me ₹8,477 avg, ₹21,536 mehenge din |
| **Naapa hua (model)** | 8.5 saal ka sabse bura trade **−₹5,292** = premium ka 58% |
| **VRP pe check kiya** | 1.0 → −5,292 · 1.2 → −5,478 · 1.5 → −5,687 — **tika hua hai** |

**Kyun tika:** ek trade ka nuksan **spot ke move × delta** se aata hai. ATM delta ~0.5
rehta hai chahe sigma kuch bhi ho. Sigma sirf **time value** badhata hai, jo kuch ghanton
me thodi hi ghisti hai.

**Phir poora saal sigma se kyun hilta hai?** Kyunki wo 1,950 trade ka **jod** hai — har
trade se thoda theta katta hai, 1,950 baar katne se pahaad ban jaata hai.

**Planning ke liye: −5,292 mat maano, premium (₹8,500-21,500) maano.** Wahi structure pe
tika hai, model pe nahi. Aur vol-crush model me hai hi nahi.

---

## 6. 🛑 JO SABIT NAHI HUA — VRP kharidne wale se bhi wasoolo

Seller ko VRP de kar test kiya tha. **Buyer pe wahi test pehle nahi kiya — wo galti thi.**
Galti ki disha bhi ulti likhi thi (docstring me "conservative" likha tha; asal me **udaar**
hai):

> sigma = realised → premium asli se **sasta** modelled
> → buyer kam theta bharta hai **aur** gamma zyada milta hai (ATM gamma ~ 1/(S·σ·√T))
> **Dono buyer ko phulate hain.**

| vrp | jaisa abhi hai | dono OFF |
|---|---|---|
| 1.0 | +1,93,645 (PF 1.18) | +4,84,161 (PF 1.35) |
| **1.2 (NIFTY ka aam)** | **+96,805 (PF 1.09)** | **+2,99,160 (PF 1.20)** |
| 1.5 | −28,299 (PF 0.98) | +56,642 (PF 1.04) |

**IV sirf 20% upar maano to abhi ki config ka munafa AADHA ho jaata hai. IV 50% pe
negative.**

### 🔬 Aur phir ASLI VRP naapa gaya — 1.2 bhi udaar tha

`OptChainLake/NIFTY/WEEK/{CE,PE}_ATM.csv` me **`iv` column** hai = asli ATM IV, disk pe.
Us se `vrp_mult = IV / realised_vol` **naapa** (maana nahi), 1,238 din, 2021-2026:

```
median 1.27  |  mean 1.33  |  p25 1.04 · p75 1.49
IV > realised: 78.5% din

2021 1.27 · 2022 1.31 · 2023 1.33 · 2024 1.33 · 2025 1.19 · 2026 1.06
```

**Arsalan ki thesis ka mool SACH hai, aur ab naapa hua hai:** bazaar 78.5% din hilne se
zyada charge karta hai, median 27% zyada. VRP asli cheez hai.

**Par mera "asli" column (1.2) bhi udaar tha — asli ~1.3 hai.** Us pe:

| config | @ vrp 1.3 = ASLI | Sharpe (~) |
|---|---|---|
| jaisa abhi hai | +52,836 (PF 1.05) | **~0.20** |
| **dono OFF** | **+2,14,442 (PF 1.14)** | **~0.59** |
| SELL | −2,06,915 (PF 0.82) | negative |

**Nateeja:** BUY vs SELL ka farq **aur mazboot** hua (₹4.2L, bina kisi assumption ke).
BUY khud **aur kamzor** hua (Sharpe ~0.59, gate se door).

**Ummeed ki kiran (naapi hui):** 2025 → 1.19, 2026 → **1.06**. VRP girta ja raha hai =
option sasta = buyer ke haq me. Ye alag se dekhne layak hai.

⚠️ Caveat: lake ki ATM IV vs daily-close se nikali realised vol — do alag estimator.
Ratio 1.27 published NIFTY VRP research (1.2-1.4) se milta hai, par aakhri lafz asli
premium pe held-strike repricing hoga.

---

## 7. EXPIRY KE DIN — Arsalan ka sawaal, dono taraf sahi

Sawaal: *"expiry ke din to ATM 200 se 50 ho jaata hai — sell me dikkat degi, buy me
shayad ulta faida. Check kar lijiye."*

Model expiry ko **sahi** handle karta hai (code padha, maana nahi): `_next_weekly_expiry`
ka `days_ahead = (weekday - ts.weekday()) % 7` expiry ke din **0** deta hai → TTE = us din
ke bache hue ghante. Weekday bhi `expiry_calendar` se (Thu→Tue 2025-09-01 included).

```
1,950 trades me se — expiry ke din: 389 (20%) | baaki: 1,561

vrp=1.0             trades      NET ₹   avg/trade   avg premium
  BUY  expiry din      389    +45,262        +116        17.7   <-- sasta
  BUY  baaki din      1561   +148,382         +95        83.5
  SELL expiry din      389   -153,736        -395        18.1   <-- kabristan
  SELL baaki din      1561   -173,171        -111        83.4
```

1. **"200 → 50" model me hai** — expiry din avg premium ₹17.7 vs ₹83.5.
2. **Expiry SELL ka kabristan hai** — −₹395/trade vs −₹111 = **3.5× bura**. ₹18 uthao,
   badle me poora gamma jokhim. Live strategy 20% trade **yahin** kar rahi hai.
3. **Expiry BUY behtar hai** — +₹116/trade vs +₹95. ₹18 ka lottery ticket, gamma bharpoor.

**Par:** vrp 1.2 pe BUY expiry **+116 → +30**. Expiry din premium lagbhag poora extrinsic
hai, to 20% zyada bharna aadha edge kha jaata hai.

### 🛑 Aur yahan ek asli suraakh nikla

Live strategy ke **expiry ke apne niyam** hain (`risk_gate`) jo **mere backtest me hain
hi nahi**:

- `EXPIRY_NO_ENTRY_AFTER_HM = (14,00)` — 2 baje ke baad koi entry nahi
- `EXPIRY_EOD_HM = (14,55)` — 2:55 pe squareoff (3:15 nahi)
- `EXPIRY_ITM_SQUAREOFF` — ITM hote hi turant kaat do

**Yaani 389 trade (20%) pe backtest live se ALAG chal raha hai.** Ye Rule 10 ka ulta roop.
Saath hi model expiry din bhi daily-close se nikali realised vol lagata hai — jabki expiry
din IV ka behaviour bilkul alag hota hai. **Model theek wahin sabse kamzor hai jahan 20%
trade ho rahe hain.** Expiry ke saare numbers (dono taraf ke) is haad tak shaki hain.

### Aur "Sharpe 3.85" — wo number galat tha

Arsalan ne pakda: *"PF 1.35, Sharpe 3.85, win 42.7 — too good to be true lag raha hai."*

**Sahi pakda — par ulti wajah se.** Number galat nahi tha, uska **naam** galat tha:

```python
sharpe = mean / sd * (len(pnls) ** 0.5)     # <-- ye t-STATISTIC hai, Sharpe nahi
```

Project ka asli formula (`engine.py:161`) `ret.mean()/ret.std()*sqrt(252)` hai — **daily
returns pe**. 8.5 saal pe dono me ~√8.5 = 2.9× ka farq hai.

| config | maine kaha "Sharpe" | **asli Sharpe (~)** |
|---|---|---|
| jaisa abhi hai | 2.10 | **~0.72** |
| dono OFF | 3.85 | **~1.32** |
| **dono OFF @ IV 20% upar** | 2.41 | **~0.83** |

> **Yaani: abhi ki live config ka Sharpe ~0.37-0.72 hai — deploy gate (≥1) se NEECHE.
> Aur "dono OFF" bhi asli IV pe ~0.83 — gate se neeche.**

---

## Nateeja — kya pukka, kya nahi

### ✅ Mazboot (VRP ke har level pe zinda, do alag engine pe)
1. **Becho mat, kharido.** ASLI naapi hui VRP (1.3) pe BUY +₹2,14,442 vs SELL −₹2,06,915
   = **₹4.2 lakh, bina kisi assumption ke**. Expiry ke din ye aur tez: SELL −₹395/trade
2. **ATM, ITM nahi** — jitna andar utna bura, capital bhi 3× zyada
3. **Target koi nahi** — har target −8% se −79%
4. **Max loss BUY me structurally capped** — premium se aage nahi
5. **Scaling asli hai** — ₹1.5L me 1 lot bechne ke bajaye ~7-17 lot kharid sakte hain
6. **Trailing paisa kha rahi hai** — har VRP level pe (96,805 vs 2,99,160 @ 1.2)

### ❌ Sabit NAHI hua
1. **Ki ye deploy karne layak hai.** ASLI naapi hui VRP pe Sharpe **~0.59** < 1
   (abhi wali live config ~0.20). Palat jaana zaroori hai, par **kaafi nahi**.
2. **Expiry ke 389 trade (20%) pe backtest live se ALAG chalta hai** — live ke
   2pm/2:55/ITM guards model me hain hi nahi
2. **Significance test nahi hua** (p-value), **train/OOS nahi**, **Monte-Carlo nahi**
3. **Asli expired-option premium pe nahi naapa** (`OptChainLake` maujood hai)
4. **Pass ② (+RMS caps) nahi chala**
5. **Vol crush model me hai hi nahi**

### 🛑 Isliye
- **Rule 10:** exit ya option-side badalna = strategy badalna = validated number jhooth.
  Live ko haath nahi lagaya, aur bina re-backtest ke lagana bhi nahi chahiye.
- **Agla kadam:** `run_hunt.py --name arschain_live_engine` — significance + train/OOS +
  MC + 3-pass. **Wahi batayega ki ye ship karne layak hai ya nahi.**

---

## Galtiyan — dono taraf ki (ye sabse kaam ki cheez hai)

### Arsalan ki
| kya socha | asli |
|---|---|
| **"decay/VRP bechne wale ko faida deta hai"** | **mool SACH — naapa gaya: 78.5% din IV > realised, median 27% zyada.** Bas adhoora: us VRP ko **bech kar** uthane ka kharcha (winner cap) VRP se bada hai |
| **"expiry me sell ko dikkat, buy ko shayad faida"** | **dono sahi** — SELL −₹395/trade (3.5× bura), BUY +₹116 (behtar) |
| "ITM me jayen to behtar" | ulta — ATM sabse achha aur sabse sasta |
| "buy me ₹25-30k lagega" | ₹8,477 — socha usse sasta |
| **"trailing SL dushman hai"** | **sahi — ₹1.83L kha rahi thi** |
| **"3.85 too good to be true"** | **sahi — wo t-stat tha, Sharpe ~1.32** |
| **"2 trade ki jagah kahan?"** | **sahi — 583 din pe 2 trade, aur REVERSAL 0** |

### Claude ki
| galti | asar | jad |
|---|---|---|
| `exit_zone` ki jagah `exit_main` bheja | **poora 9-saal ka run zone-exit OFF pe chala** | `validate_strategy` pehle se translate karta tha — maine wapas likh diya |
| `stats()` me apna "Sharpe" likha (t-stat) | har number 2.9× phoola, gate se compare hi nahi ho sakta tha | `engine._annualize_sharpe` pehle se tha — maine wapas likh diya |
| Buyer pe VRP test nahi kiya | BUY ke saare numbers udaar the | seller pe kiya tha, buyer pe bhool gaya |
| docstring me "conservative" likha | disha hi ulti | bina soche likh diya |
| "cap zyada bandhta hi nahi (0.93/din)" | galat — 736 khaali din ausat me mila diye | ausat ne sach chhupa liya |
| exit-reason breakdown ko saboot maana | wo saboot tha hi nahi | selection effect — trailing pe wahi katte hain jo khilaaf gaye |

**Dono badi galtiyon ki ek hi jad: jo cheez pehle se maujood thi, maine dobara likh di
(Rule 6B).** Aur dono baar **Arsalan ne pakdi, maine nahi** — ek baar "zone exit ka kya
hua?" poochh kar, doosri baar "too good to be true" keh kar.

---

## 📌 Drawdown me ye padhna

1. **Ye strategy 36% baar jeetti hai. Beech ka trade HAARTA hai (−₹366).** Ye tootna
   nahi hai — ye design hai. Paisa upar ke 10% se aata hai.
2. **Lagataar 5-10 haar bilkul normal hai** — 64% trade haarte hain.
3. **Ek trade ₹8,500-21,500 se zyada nahi le ja sakta** (kharidne pe). Ye pukka hai.
4. **Agar ghabra kar target lagane ka mann kare** — table dekho: har target −8% se −79%.
   Ye 1,950 trade pe naapa hua hai, raay nahi.
5. **Agar trailing SL lagane ka mann kare** — wo ₹1.83 lakh kha chuki hai.
6. **Par agar Sharpe/DD backtest se bahar ja rahe hain** — to shayad ye sach me tooti
   hai. Tab `run_hunt` dobara chalao. Ye doc jawaab nahi, sirf **base rate** deta hai.

---

## Files (sab `scratch/nifty_trend/`)

| file | kaam |
|---|---|
| `arschain_backtest.py` | live engine 9 saal pe + spot/BUY/SELL/spread 4 pricing + `vrp_sweep()` |
| `arschain_exits.py` | strike ladder + distribution/MFE/MAE + SL-target sweep + exit on/off |
| `bs_option.py` | `reprice(itm_steps=)` **naya param** — ATM se ITM ladder |
| `engine.py` | `_annualize_sharpe()` = **asli Sharpe ka single source** — apna mat likhna |

**Reproduce:** `python -X utf8 scratch/nifty_trend/arschain_exits.py --itm 0`

---

## 🔴 AGLA KAAM — live me strategy chal hi nahi rahi (2026-07-17 raat, user ne pakda)

User: *"webhook long ko exit karke ulti position banata hi nahi — aaj tak ek trade nahi."*
Aur phir, khud: *"entry mai webhook se leta hoon, baaki apna dimag, darr, algo me tricks
laga kar nikalta hoon."*

**Data poori tarah unke saath hai.** `arschain_MAIN` ke 10 LIVE trades ke exit reasons:

```
DEFAULT_TSL_SL:-2000 / 1200 / -1600   3   <-- RMS ka trailing SL
RMS_MAXLOSS (daily cap ₹4,000)        2   <-- RMS
EXPIRY_ITM_SQUAREOFF                  2   <-- RMS ka expiry guard
EXTERNALLY_CLOSED                     2
MANUAL_CLOSE                          1
TV_EXIT / REVERSAL / ATR_TRAILING     0   <-- strategy ka apna exit: KABHI NAHI
```

**Strategy ne aaj tak ek bhi live trade band nahi kiya.** Live me DO trailing SL chal rahe
hain — strategy ki apni ATR trail (Pine me) aur **RMS ka DEFAULT_TSL** (14-July ko 7
strategies pe laga) — aur **RMS wala hamesha pehle pahunchta hai.**

**Reversal isliye nahi hota:** ulta signal aane tak position zinda hi nahi bachti. Webhook
ka reversal code (`webhook_executor.py:616-638`) bilkul theek hai — **use mauka hi nahi
milta.** (Mera backtest bhi 8.5 saal me `REVERSAL = 0` deta hai — wahan strategy ki apni
ATR trail pehle maarti hai. Live me RMS. Do alag wajah, ek hi natija.)

### Isi se ₹1 lakh ka farq bhi khulta hai
TV **+₹94,078** (Jan-Jul 2026, strategy apne exits pe) vs live **−₹5,164** (RMS ke exits
pe). **Ye do alag strategy hain. TV jo dikha raha hai, wo chalayi hi nahi ja rahi.**
= **Rule 10 apne sabse literal roop me.**

### Aur mera poora backtest bhi is live strategy ka NAHI tha
Maine strategy ke apne exits naape (ATR trail / zone / 3:15). Live me RMS kaatta hai.
**Mere saare numbers us cheez ke hain jo chalayi hi nahi ja rahi.**

### Judi hui baat — TRAP #128 abhi bhi kaat raha hai
`DEFAULT_TSL_SL:-1600` dikha jabki user ki set ki hui SL **₹2,500/lot** thi → RMS override
galat config key (`webhook_v1`) pe, live id `arschain_MAIN` hai → lookup fail → **chupchaap
global ₹1,000/lot**. In 10 me se kuch trade **galat SL** pe mare.

### Counterfactual feature — recover kiya, par wo ye sawaal nahi naapta
`_counterfactual_RECOVERED.py` (254 lines, `a39e238^` se; 2026-07-01 ko user ne delete
karwaya tha). **Waise ka waisa zinda mat karo — do wajah:**
1. **Galat sawaal:** wo "algo vs manual panic" tolta hai. Yahan manual sirf **1/10** tha;
   baaki 9 **RMS** ne mare — wo file un 9 ko "algo" hi maanti hai.
2. **Maanyata ULTI ho chuki:** file maanti hai Dhan=algo / Kite=manual. Asliyat ab
   **Kite=algo / Dhan=manual** ([[project_code3b_dhan_manual_kite_algo]]). Waise chalaya
   to **ulta jawaab** dega.

Reuse karne layak: `_fifo_match`, `_build_timeline`, `_entry_markers`. Dimag naya chahiye.

### Jo asli me chahiye (naya analysis)
Har live trade pe: (1) entry — `order_store` me hai ✅ · (2) **strategy ka apna exit kya
hota** — engine ko us entry se aage chalana ❌ naya kaam · (3) us exit pe **asli premium** —
lake me hai ⚠️ par lake **ATM±10** ka hai aur live **`strike_offset=-1`** pe chalti hai.

### Faisla jo sabse pehle chahiye (futures se bhi pehle)
**Live me strategy ke exits chalne dene hain, ya RMS ke?** Abhi dono hain aur RMS jeet raha
hai — **aur us combination ka backtest kabhi kisi ne kiya hi nahi.** Jab tak ye tay nahi,
har number (mera bhi, TV ka bhi) kisi aur strategy ka number hai.

**User ka darr = design ka signal, kamzori nahi:** *"trail itni dheere khisakti hai ki
lagta hai kuch karen."* Jo exit itni dheere chale ki insaan use pakad na sake, wo us
insaan ke liye galat exit hai. (Aur 8.5 saal ka data bhi wahi kehta hai: `ATR_TRAILING`
1,326 trades **−₹4.16L**, `3:15 Daily Exit` 359 trades **+₹4.68L**. Jis exit ko dekhna
na pade, use todne ka mann bhi nahi karta. ⚠️ Ye BS-model se hai — asli premium pe verify
zaroori.)
