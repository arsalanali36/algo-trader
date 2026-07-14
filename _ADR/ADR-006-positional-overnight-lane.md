# ADR-006 — Positional (overnight-hold) execution lane for the VRP "panic-fade" strategy

> **SHELVED 2026-07-14 (user decision).** VRP parked — only ~3 trades/yr, too infrequent
> for the user's goal. Code stays (`active:false` both machines, trades nothing); the
> `allow_overnight` lane + IV-source fix (commit 533cbd0) are kept as reusable infra.
> KNOWN-UNFIXED if ever revived: live entry fires ~162x vs validated ~15x — must be
> rewritten to sample IV-rank at dte_entry (not first-qualifying-day). Do not resume
> unless the user revisits this strategy.

# ADR-006 — Positional (overnight-hold) execution lane for the VRP "panic-fade" strategy

Status: PROPOSED (design only — no live money-path code yet)
Date: 2026-07-12

## Context

Har existing strategy INTRADAY hai — `pos_monitor_loop` (`trader_dashboard.py:5714`)
har option position ko blanket 3:15 PM squareoff karta hai, chahe kisi bhi
strategy ne kholi ho ("no overnight holds, ever" — project rule). Yeh rule
jaan-boojh kar hai (overnight gap risk se bachne ko).

Ek NAYI strategy validate hui — **VRP panic-fade**: jab NIFTY ka IV-rank top-20%
mein ho (war/election/tariff jaise fear-spike ke waqt), tab weekly ATM short
straddle bech ke **expiry tak hold** karo. 5yr real-lake data (ATM IV + spot):
- IV-rank>0.80 filter: n=15, PF 4.4, significance p=0.0002 (vs random weeks),
  OOS dono half profitable, Monte-Carlo 100% paths profit.
- Edge sirf POSITIONAL hold se aata hai (poora premium/theta). Intraday version
  (#06) cost mein mar chuka hai (TRAP #109).
- Portfolio value: short-vol = ORB/chain-zone/backspread (long-gamma) ka INVERSE
  leg (corr −0.05..−0.11, #06 se already confirmed).

To edge ko capture karne ke liye position ko **overnight hold** karna PADEGA —
jo current architecture ke core rule se takraata hai. Yeh ADR us takraav ko
kaise safely resolve karein, wahi decide karta hai.

## Decision

Ek **per-strategy opt-in "overnight lane"** banayenge — blanket rule ko todenge
NAHI, sirf ek naye explicit flag ke peeche exception denge. 4 hisse:

1. **Flag (RMS per-strategy override):** `nifty_config.json["_risk"]["per_strategy"]
   [<sid>]["allow_overnight"] = true`. Naya helper `risk_gate.allow_overnight(sid)`
   (default False; global default kabhi True nahi — opt-in only).

2. **Monitor hook (`pos_monitor_loop`):** blanket 3:15 squareoff se PEHLE check —
   agar is position ka owning-strategy (correlationId prefix se) `allow_overnight`
   hai, to `EOD_315_SQUAREOFF` **skip** karo. BAAKI sab guard ZINDA rahenge:
   - Expiry-day squareoff (`EXPIRY_EOD_SQUAREOFF` 2:55) — final din force-close, overnight tail cap.
   - Expiry-day ITM guard (`EXPIRY_ITM_SQUAREOFF`).
   - RMS daily-loss breaker (`RMS_MAXLOSS`) — supreme, ise koi bypass nahi.
   Yani position sirf usi expiry-cycle ke andar overnight hold hoti hai, hamesha
   ke liye nahi.

3. **Defined-risk MANDATORY for overnight:** overnight naked short option ALLOW
   NAHI — entry ke waqt hedge wings zaroori (iron condor/strangle). Kyun: overnight
   gap (real war/crash) naked pe unbounded loss de sakta hai; wings se max-loss
   bounded + margin bhi ~₹1.5L se ~₹30-40k gir jaata hai (capital efficiency).
   Enforce: agar `allow_overnight` true hai to entry-path hedge resolve na hone pe
   entry SKIP kare (naked overnight kabhi na jaaye).

4. **Live signal (IV-rank):** entry signal ke liye live ATM IV chahiye + trailing
   history. Seed = lake (`optlake_load.iv_rank_daily`), aage roz live ATM IV
   (Dhan option quote ka `iv` field) append karke rank compute. Ek naya lightweight
   positional trader (`strategies/live/vrp_straddle_trader.py`) — weekly, ek-entry-
   per-expiry, gate_entry + smart_order.execute + order_store (Critical Rule 6/8),
   entry sirf jab rank>0.80.

## Consequence (trade-offs accepted)

- **Accepted:** "no overnight" ab absolute nahi — ek explicit, per-strategy,
  defined-risk-only exception hai. Blanket default sab baaki strategies ke liye
  waisa hi (opt-in flag off).
- **Accepted:** overnight gap risk maujood rahega, par (a) wings se bounded,
  (b) final expiry din force-closed, (c) RMS loss-cap supreme. Teen-layer guard.
- **Accepted:** kam trades (~3/saal) — slow validation, live paise se pehle lamba
  paper-observe zaroori. Yeh strategy ka swabhaav hai.
- **Cost:** ek naya code-path (positional trader + monitor exception) = naya
  surface jo intraday assumptions tod sakta hai — isliye PRE-MORTEM (neeche) +
  paper-first mandatory.
- **Rejected alt:** "intraday hi rakho" — edge hi mar jaata (TRAP #109). "Naked
  overnight allow karo" — tail risk unacceptable. "Har strategy overnight" —
  blanket safety khatam.

## Rollout (strict order — koi step skip nahi)
1. Yeh ADR + PRE-MORTEM (done).
2. `allow_overnight` flag + `risk_gate.allow_overnight()` helper + monitor-hook
   exception (code review, py_compile, market-closed deploy).
3. `vrp_straddle_trader.py` — PAPER only (`active:false`, `mode:paper`).
4. Forward paper-observe (agle high-IV events pe real out-of-sample samples).
5. Live sirf jab paper forward-results backtest se match karein (standard rule).
