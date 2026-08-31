# ADR-023 — Backtest evidence standard: real premium, real scale, real coverage

**Date:** 2026-08-31
**Status:** Accepted, enforced (3 guards + 1 audit check). Applies to EVERY option backtest.

## Context

Ek hi din me 02.10.01 (BNF hedged strangle) ke published run me **teen alag jhooth** mile,
aur teenon ne number ko **behtar** dikhaya. Strategy live thi, asli paisa laga hua tha.
User ne yeh nahi poocha tha ki "backtest check karo" — usne poocha tha *"real me lagataar
loss kyun ho raha hai jabki backtest ek hi baar ₹4,000 dikhata hai?"*

**Jhooth 1 — scale.** Exit rule 5 lot pe chalta tha (`basket = ... * qty`), par P&L 1 lot pe
record hoti thi (`gross = ... * lot`). Page ne un 1-lot numbers pe "5 LOTS" label laga diya.
Har rupaya **5× chhota**. (TRAP #197)

**Jhooth 2 — synthetic pricing.** Wings Black-Scholes se the, lake se nahi. Sigma entry pe
freeze — vol expansion, IV crush, skew, spread kuch capture nahi. Result: 4/874 trades apne
**structural wing-cap ke bahar** nikle, yaani "defined risk" ka daawa hi model me toot raha
tha. Ye caveat run ki apni file me likha tha — kisi ne kabhi quantify nahi kiya.

**Jhooth 3 — coverage.** Lake **ATM-relative** hai (offset window), trade **fixed strike**
hold karti hai. ATM khiskte hi strike window se bahar, aur reader chupchaap **intrinsic**
(OTM = 0) laga deta tha = "short muft me buy-back". 167/874 trades (19.1%) contaminated,
**88% win rate** vs clean ke 48%, aur unme **80.1% of the reported profit**. (TRAP #198)

Teenon ka rukh ek hi taraf tha: **profit ki taraf.** Aur teenon ke number "plausible" lagte
the — isliye koi review inhe pakad nahi paaya. Sharpe/PF/Win% **lot-invariant** hain, isliye
wo poore waqt "sahi" dikhte rahe; sirf ₹ aur maxDD% jhooth bol rahe the — aur **sizing wahin
se hoti hai**.

## Decision

Koi bhi option backtest tab tak **evidence nahi** hai jab tak teenon shart poori na hon:

### 1. REAL premium — structure ke SAARE legs
`bs_option.reprice*` ya kisi bhi BS/synthetic pricer ka output **evidence nahi hai**.
"Shorts real + wings BS" bhi **fail** — wing hi wo hissa hai jo tail me bachata hai.
Data na ho to backtest **chhota karo ya roko**; gap synthetic se **mat bharo**.
Run page pe BS pass ko "deployable P&L" **kabhi mat likho**.

### 2. Ek hi LOT SCALE — exit rule aur P&L dono
Jis qty pe exit rule chalta hai, P&L usi qty pe record ho. Ek function me `lot` aur `qty`
dono ka hona hi red flag hai. Comment se kaam nahi chalta — TRAP #197 me comment
`# (all × lot)` **theek likha tha aur phir bhi galat tha**.

### 3. LAKE COVERAGE — strike ki poori zindagi window ke andar
Fixed-strike positional strategy ATM-relative lake se tabhi backtest ho sakti hai jab strike
**poore hold** ke dauraan window me rahe. Miss pe **`None`/skip** — kabhi intrinsic nahi.
Window ko ATM ki **drift** se chauda hona chahiye, sirf entry ke strikes rakhne jitna nahi.

## Enforcement (guess pe nahi, code pe)

| guard | kahan | kya rokta hai |
|---|---|---|
| `_assert_lot_scale(df, sl)` | `build_bnf_positional_run.py` | SL-exit trades ka median gross basket-SL ka 0.4–2.0× na ho to **publish block**. Buggy scale pe median −₹870 vs ₹4,000 → caught. |
| `_assert_lake_coverage(df, 5%)` | wahi builder | >5% trades me koi leg window ke bahar → **publish block**. Published config 19.1% pe trip hota hai. |
| audit check 11 `LAKE-SILENT-INTRINSIC` | `_TOOLS/architecture_audit.py` | ATM-offset window lookup me intrinsic fallback = **commit block**. Escape: `# intrinsic-ok: <reason>`. |
| provenance badge | `dashboard_intraday.html` | `meta.real_cost.method` se **derive** hota hai (hand-written label pe bharosa nahi). REAL+BS → "NOT PROVEN"; fail pe verdict chip forcibly "NOT PROVEN" (pehle BS numbers pe "GENUINE EDGE" chhapta tha). |
| `base.STRICT` + `oob_report()` | `bnf_920_strangle_intraday._px` | miss ab **counted**; STRICT pe `LakeCoverageError`. Legacy intrinsic return ~50 call-sites ke liye bacha hai par **chup nahi** hai. |
| `run_positional(strict_skip=True)` | `bnf_hedged_backtest.py` | trade se PEHLE check; price na ho sake to **skip + count** (`df.attrs["skipped_unpriceable"]`), banao mat. |
| `lake_coverage_check.py` | standalone | lake ki files seedha padhta hai — **backtest chalata hi nahi**, isliye usi substitution se dhokha nahi kha sakta jo wo dhoondh raha hai. |

## Consequences

- **02.10.01 ka koi bharosemand backtest maujood nahi.** Run page `NOT PROVEN` mark; strategy
  PAPER pe; koi live paisa nahi. Re-run tabhi jab lake ±20 tak ho.
- 37 run pages me se **8 REAL premium, 29–31 NOT PROVEN**. Kai deployed the.
- **02.17 weekly iron-fly (asli paisa) SAFE hai** — `scratch/strangle_roll/engine.py::_prem()`
  absolute strike se dhoondhta hai, miss pe `None`, caller trade skip karta hai. Ek repo me
  do lake reader the; **ek sach bolta hai, ek nahi.** Naya reader hamesha `_prem` wala shape le.
- Lake ±10 → **±20** (`optchain_dl.py --off-range`), aur `load_grid`/`_px` ka hardcoded 10
  hata ke **disk se auto-detect** (`g["WIN"]`) — warna chaudi lake ka koi fayda hi nahi hota.

## Jo is ADR ne NAHI kaha

Ye standard batata hai kaunsa number **bharosemand** hai — **achha** nahi. 02.10.01 ka
imaandar number abhi bhi unknown hai, aur negative bhi ho sakta hai. Deploy gate purana hi
rehta hai (Sharpe ≥ 1, p < 0.05, min(train, OOS)) — ab uske input sach hone chahiye.

**Related:** LESSONS TRAP #197, #198 · ADR-022 (risk-first sizing) ·
`feedback_no_blackscholes_backtest` · `feedback_backtest_realism_checklist`
