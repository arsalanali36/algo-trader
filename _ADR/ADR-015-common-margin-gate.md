# ADR-015 — Common margin gate (position_margin / margin_breakdown, single source)

Status: ACCEPTED — built 2026-07-28, VPS-live `13ca6ef`.
Date: 2026-07-28

## Context

The project already has a single entry point for the two other cross-cutting
money concerns: **orders** go through `execution_gateway.execute_signal/exit`,
and **risk** goes through `strategy_safety.gate_entry`. Margin/"capital-in-use"
had NO such gate — it was computed inline at three different levels, and each
feature picked one more or less arbitrarily:

- **per-leg NAKED** — `sum(_leg_capital(leg))` (each SELL leg's standalone
  SPAN, no hedge benefit)
- **hedged BASKET** — `_group_capital(legs)` (broker basket margin, capped at
  the per-leg sum)
- **raw** — `kite_basket_margin(legs)` directly

Result (user-reported 2026-07-28): the RMS number, the Open-Positions display,
and the Today's-Peak margin chart all showed **different capital for the SAME
positions** — ₹2.8L vs ₹4.9L vs ₹21.8L. A VRP condor "used" more than it really
did in one place and less in another; new entries got false-blocked; the margin
chart peaked ~8× reality. Every one of these was a symptom of "no single margin
truth", the exact shape Rule 6B (duplicate logic → divergence) warns about.

User asked directly: *"risk aur order gate hai, margin ka koi common gate nahi
kya? har koi apna margin calc karta hai?"* — and approved building one.

## Decision

One public margin entry point in `risk_gate.py`, mirroring the order/risk gates:

- **`position_margin(legs, rc=None)`** — THE canonical capital-in-use for a
  position or leg-group. Broker's real hedged **basket** margin for a multi-leg
  F&O structure, **per-leg sum** otherwise, and **never more than the per-leg
  sum** (conservative). This is the same value `capital_in_use` sums. A single
  leg is just `position_margin([leg])`. (Internally = `_group_capital`.)
- **`margin_breakdown(legs, rc=None)`** — display helper for the payoff panel:
  `{hedged, standalone, benefit}` where hedged = `position_margin`, standalone =
  per-leg naked sum, benefit = standalone − hedged.
- **`_leg_capital` and `kite_basket_margin` are now PRIVATE to `risk_gate.py`.**
  Every other file uses `position_margin` (a position/group's capital) or
  `margin_breakdown` (the naked-vs-hedged split). Enforced by a new commit-time
  **`architecture_audit` MARGIN-GATE check** that FAILs any `_leg_capital(` /
  `kite_basket_margin(` call outside `risk_gate.py` (escape:
  `# margin-gate-ok: <reason>`; the audit file self-exempts, like
  `_PY_LABEL_ALLOW`).

All external callers migrated: `payoff.basket_margin` → `margin_breakdown`;
`trader_dashboard` straddle-preview + 2 fire-capital blocks + margin chart +
per-position display + RMS summary group_margin → `position_margin`.

## Consequence (trade-offs accepted)

- **Single-leg positions are a byte-identical no-op** (`_group_capital` returns
  the per-leg value for `len(fno) < 2`), so the per-position display and RMS
  numbers for naked single legs are unchanged.
- **Multi-leg groups now show the consistent basket margin EVERYWHERE** — this
  is the intended behavioural fix: the VRP condor / straddle previously showed
  per-leg naked (5–10× over) in the chart and a different number in RMS; now all
  surfaces agree with `capital_in_use`.
- **Conservative by construction** — `position_margin` can never report MORE
  than the per-leg naked sum, so the gate can only ever tighten, never loosen, a
  capital estimate vs the old worst-case. No cross-STRATEGY netting is claimed
  (over-estimate stays possible, under-estimate does not).
- **Fallback chain unchanged** — basket disabled / non-kite broker / basket API
  fail / single leg → per-leg sum (the old conservative number). Kill-switch
  `_risk.global.basket_margin_enabled` still applies.
- **The audit rule makes it durable** — a future feature that reaches for
  `_leg_capital`/`kite_basket_margin` directly is blocked at commit time, so the
  three-way disagreement can't silently reappear (the 26th offender can't be
  written, not just the 25 existing ones cleaned up).

## Pre-mortem (shapes checked)

- #4 duplicate logic → the whole point: collapse 3 margin calcs into 1 public fn. ✓
- #8 hardcoded/inline vs canonical helper → audit MARGIN-GATE blocks inline calls. ✓
- #2 built≠wired → all 6 external call sites migrated + grep-verified zero
  `_leg_capital(`/`kite_basket_margin(` remain outside risk_gate.py. ✓
- #7 deploy drift → surgical checkout (TRAP #144), md5 local==VPS ×4. ✓
- #9 (n/a — display/estimate only, no order path touched).

## Verified

`architecture_audit` **0 FAIL**; guard self-tested (flags both private fns,
honors `# margin-gate-ok:`, risk_gate self-exempt, proper `position_margin`/
`margin_breakdown` usage clean); py_compile OK ×4; single-leg no-op confirmed via
`_group_capital` len-guard; VPS venv `position_margin`/`margin_breakdown`
callable; dashboard+monitor restart, strategy PIDs 16==16, zero errors.

Related: `execution_gateway` (orders) + `strategy_safety.gate_entry` (risk) — the
two precedent single-gates this mirrors; TRAP #90 (executing-broker margin,
`broker_real_margin` single entry point); Rule 6B (duplicate → divergence);
memory `project_code3b_capital_overcount_phantom_exit`.
