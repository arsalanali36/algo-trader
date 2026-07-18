# Crabel ORB on NIFTY — Findings

**Source:** Toby Crabel, *Day Trading With Short Term Price Patterns and Opening Range
Breakout* (1990) — the strategy walked through in the "$1000 book" YouTube video.
**Ported by:** `crabel_orb.py` · **Data:** `nifty_1min.csv` (2018→2026, 8.5yr, 610 armed trades).

## Rules (faithful, no-look-ahead)
- **Arm** tomorrow if today is `NR4` (narrowest range of last 4) **or** `NR7` **or** `inside-day` (H≤ydayH & L≥ydayL).
- **Stretch** = 10-day SMA of `min(High−Open, Open−Low)` (the smaller poke past the open).
- On an armed day: **buy-stop = Open+stretch**, **sell-stop = Open−stretch**, OCO (first touched wins).
- **Stop-loss = opposite band** → stop distance = `2×stretch` = "risk per trade" (R).
- **10:30 cutoff** — no entry after (Crabel: edge strongest right after open).
- **Exit at close**, never overnight.
- **Fill logic:** stop fills at the *worse* of {band, bar open} — gap-through fills at the gap.
- Arm + stretch computed from **already-closed** days → shifted 1 day.
- Modes: `bidir` (both stops) · `trend50` (50-DMA filter: above→long only, below→short only).

## Result — same verdict as the video

| Version | avg R | sum R | Net % | CAGR | MaxDD | Sharpe | vs Buy-Hold (+120%) |
|---|---|---|---|---|---|---|---|
| Bidir — **gross** | 0.202 | 123.2 | +221.9% | 14.8% | −15.2% | 2.24 | ✅ crushes |
| Bidir — **net** (1bp+2bp/fill) | 0.045 | 27.7 | +24.0% | 2.6% | −33.0% | 0.50 | ❌ loses |
| Trend50 — gross | 0.155 | 54.9 | +67.2% | 6.3% | −13.8% | 1.74 | ❌ |
| Trend50 — **net** | −0.002 | −0.6 | −4.0% | −0.5% | −23.8% | −0.02 | ❌ dead |

**Cost breakeven ≈ 8 bp round-trip:** `0→0.202R · 4→0.098R · 6→0.045R · 8→−0.007R · 10→−0.059R`.

## Learnings (the point)
1. **Gross curve is a trap.** It beats buy-hold and looks incredible; costing it collapses the whole edge. Never call an ORB-type scalp a "winner" from the gross number — cost it first.
2. **Cost scales with trade-count; the edge doesn't.** 610 trades × 0.2R-gross is the thinnest edge — any cost proportional to activity dominates it. (Same shape as the exit-rule / daily-discipline research: *trade less + better*.)
3. **India makes it worse, not better.** NIFTY spot isn't tradable. Futures ≈3-5bp → thin positive but under buy-hold with −33% DD. **Options** (ATM spread + theta on a stop-reverse intraday scalp) ≫ 8bp → dies.
4. **Slippage modeled generously** (half favourable). Real stop orders fill on momentum = adverse-skewed → live would underperform this table.
5. The contraction→expansion pattern **is real** on NIFTY (it exists gross) — it's just not monetizable after friction on a liquid index. Crabel's own strategy got the same verdict on QQQ/SPY; the video's "try more inefficient markets" note applies here too.

**Not deployable on NIFTY.** Display/research only.
