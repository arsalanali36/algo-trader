# ADR-003: Backtest aur live risk rules — kya share hota hai, kya nahi (aur kyun)

## Status: Decided (2026-07-07)

## Context

2026-07-05 ko confirm hua tha: `_TOOLS/backtest_engine.py` risk_gate/
strategy_safety/smart_order kuch import nahi karta — "reality" ki teesri
duplicate jagah ban sakta tha (live-execution aur chart-indicator ke baad).
Sawaal: kya backtest ko poore live RMS rules simulate karne chahiye?

## Decision

**Partial share, explicit flag — silent duplicate simulation kabhi nahi.**

1. Jo live rules DETERMINISTIC hain (pure config, koi live state nahi), wo
   backtest mein enforce hote hain:
   - max 2 trades/day + 3:15 squareoff — runners pehle se enforce karte hain.
   - per-index max-premium cap — `execution_gateway.execute_signal(
     mode="backtest")` se available (option-premium paths ke liye).
2. Jo rules LIVE STATE se chalte hain (daily-loss cap aaj ke realized P&L se,
   concentration aaj ke order_store se, broker-funds asli balance se) — wo
   backtest mein **simulate NAHI hote**, kyunki historical simulation mein
   "aaj ka live state" inject karna jhootha number deta. Jab tak backtest
   apna simulated-capital ledger maintain nahi karta (future work), inhe
   chalane ka dawa karna misleading hai.
3. Ye gap ab CHUP nahi hai: har `run_backtest()` result mein `risk_note`
   field jaata hai jo saaf batata hai kya enforce hua kya nahi. Ye Rule 6B
   point 4 ka implementation hai ("chup-chaap shortcut mat lo — flag karo").

## Consequence

- backtest_engine ab `execution_gateway` import karta hai — mechanical audit
  ka BACKTEST-RISK check structurally satisfied (aur sahi wajah se, sirf
  dikhawe ka import nahi: backtest gate + risk_note isi contract ke hisse hain).
- Backtest results NUMERICALLY unchanged (koi naya filter default-on nahi) —
  "purana behavior exactly preserve" rule.
- Future: simulated-capital ledger banao to daily-loss/concentration bhi
  `mode="backtest"` gate mein aa sakte hain — us waqt naya ADR.
