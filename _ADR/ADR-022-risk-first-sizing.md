# ADR-022 — Risk% constant, LOTS derived (risk-first sizing)

**Date:** 2026-08-30
**Status:** Accepted, opt-in (per-strategy `risk_pct`); abhi sirf 02.10.01 pe on.

## Context

Do knob alag-alag set ho rahe the:

```
position SIZE = lots            (Goal Planner / config badalta hai)
risk CAP      = ₹4,000 ABSOLUTE (ek baar likha, dobara kabhi nahi chhua)
```

Par asli risk = `f(size, stop-distance)`. ₹ ko fix rakho aur lots badhao to
**stop-distance apne aap sikudti hai** — kisi ne SL nahi badla, phir bhi SL tight.

Naapa gaya (`bnf_strangle_hedged`): backtest `sl:4000 @ 5 lots` = **₹800/lot**;
live 11 lots pe wahi ₹4,000 = **₹364/lot**. Aur ye sirf theory nahi thi — uske
hedged sibling ke **23% legs** basket-₹ cap se marte the jabki uska paper twin
apne hi validated exit pe nikalta tha. Ek naam ke neeche do alag strategy.

User ka apna framing (yahi asli requirement hai):

> *"4 lakh me mera 5 lot 4-leg hedge ban jaata, to uska 1% mere liye sahi hai.
> Jaise-jaise scale karenge wahi 1% ka constraint rahe — lot risk se adjust ho,
> aur AUTO ho, mujhe yaad na dilana pade."*

## Decision

Rishta ULTA kar diya:

```
PEHLE :  lots FIX  →  risk float karta hai   (capital badha? risk% chup-chaap badal gaya)
AB    :  risk% FIX →  lots DERIVED           (capital badha? lots khud badh gaye)

risk_budget  = capital × risk%
per_lot_risk = validated stop × lot_size   |  ya seedha `risk_per_lot_rs`
lots         = floor(risk_budget / per_lot_risk)
basket_sl    = lots × per_lot_risk          (budget se kabhi upar nahi)
```

`_core/basket_risk.py` = single source: `resolve()` (per-lot cap + coherence
verdict) aur `sizing()` (risk-first lots).

**Per-lot risk teen shakl me aa sakta hai** — structure decide karta hai:
| shakl | risk/lot | misaal |
|---|---|---|
| stop-based | stop_points × lot_size | 02.10.01 |
| defined-risk | wing − credit | 02.17 iron-fly |
| long option BUY | premium × lot_size | 04.03.02 |

Aakhri do me "stop points" hota hi nahi → `risk_per_lot_rs` seedha diya jaata hai.
`basket_sl_per_lot_rs` **khud hi** per-lot risk hai, isliye wahi source bhi hai —
same number do jagah likhwana drift ka naya darwaza kholta.

**Wiring = Goal Planner, live order-path NAHI.** Per-trade risk cap solver ke
**maujooda** `capacity_lots` ceiling me fold hota hai (`_risk_cap_lots`), solver ka
loop chhua tak nahi. Live traders apna static `qty` hi padhte hain — yaani ye cap
future *proposals* pe hai, koi live position resize nahi karta.

## Consequences

**Accepted:**
- Live entry-path me lots resolve hone se **pehle** `lot_size` pata hi nahi hota
  (wo contract resolve karne pe milta hai) — isliye sizing wahan nahi, planner me.
  Trade-off: cap tabhi lagta hai jab plan banta/apply hota hai, entry pe nahi.
- **Opt-in.** `risk_pct` + capital + per-lot risk teeno chahiye; ek bhi na ho to
  sizing OFF aur purana static `qty` chalta hai. Koi silent resize nahi.
- Lot size **guess nahi** — config, warna strategy ke apne aakhri trade ke sec_id se.
- Har strategy pe **nahi** lagaya. 02.17 / 04.03.02 ke backtest me koi ₹-cap tha hi
  nahi; unpe risk_pct daalna wo cheez add karna hota jo validate nahi hui (Rule 10).

**Rule-10 ka mahin farq — ye is ADR ka asli safeguard hai:**
> **LOTS pe chhat lagana backtest ko todta NAHI** — per-lot behaviour bilkul same
> rehta hai (stop-distance same), wo sirf sizing hai jo strategy ki validated logic
> ke *bahar* hai. **SL/target daalna behaviour BADAL deta hai** aur validated number
> ko fiction bana deta hai.

**Khula:**
- `basket_sl_per_lot_rs` set na ho to legacy absolute `basket_sl_rs` chalta hai
  (aaj ka behaviour bit-exact) — yaani purana shape abhi bhi possible hai.
- Coherence guard `sl_pt` pe bharosa karta hai jahan wo asli exit ho; `basket_rs`
  mode me use jaan-boojh kar ignore karta hai (wahan wo dead config hai) — is
  distinction ko naya strategy add karte waqt dhyan me rakhna.

Related: LESSONS TRAP #195, memory `project_code3b_basket_risk_scaling`,
ADR-015 (margin single gate), `feedback_backtest_fidelity_rule` (Rule 10).
