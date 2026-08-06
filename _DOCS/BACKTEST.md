# 🔬 BACKTEST — research engine (`scratch/nifty_trend`)

Har shipped strategy ka validated Sharpe/net yahin se banta hai. Ye doc = **kaun sa file kya +
3-pass pipeline + `runs/<slug>/` → dashboard wiring**. Do contract docs PEHLE padho (mandatory):

- **[`../scratch/nifty_trend/RESULTS_SCHEMA.md`](../scratch/nifty_trend/RESULTS_SCHEMA.md)** — `results.js` ka permanent format (`combos["<pass>|<period>"]`).
- **[`../scratch/nifty_trend/BS_OPTION_SIM.md`](../scratch/nifty_trend/BS_OPTION_SIM.md)** — Black-Scholes option-premium simulation contract.

> ⚠️ `scratch/` `.gitignore` me hai PAR `scratch/nifty_trend/` **JUNK NAHI** — git force-added,
> architecture_audit ke andar. Naya file yahan → `git add -f` (warna VPS tak kabhi nahi jaayegi).

---

## 1. 3-PASS pipeline (mandatory display model)

Har result **teen alag pass** me, ek dashboard ke andar PASS toggle se. `results.js` me
`"<pass>|<period>"` keyed (`bs|full`, `rms|oos`…). Backtest ka P&L = **option-premium based**
(spot-notional NAHI — asli expired-weekly data milta nahi, TRAP #100 → BS se simulate).

```
 ① instrument  raw signal spot P&L (no RMS, no options)
      ▼
 ② + RMS       same trades + account daily loss/profit caps
      ▼
 ③ + BS        pass-② trades ATM CE/PE premium me repriced (Black-Scholes delta+theta
               + real Zerodha date-aware charges + DOM slip) = ASLI DEPLOYABLE TRUTH
```

Periods: `full` / `train` / `oos` → 9 combos (`bs|full`, `rms|oos`, …).

## 2. Reference producer + family producers

- **`run_hunt.py`** = reference producer + sabse aasan reuse: `--name <slug>` → screen (all
  designs × TFs) → optimize (rank `min(train,oos)`, TRAP #103 overfit-guard) → significance
  (rotation-permutation p<0.05) → 3-pass × 3-period → Monte-Carlo → `runs/<slug>/{results.js,
  index.html,meta.json}` + `runs/index.json` append. `write_run()` = shared emit (drift-proof).
- **`build_*.py`** = per-family producers jo `run_hunt` ke building blocks reuse karte (banknifty
  = alag store, vrp/vrp_weekly, meanrev, overnight_orb, range_strangle, shortvol, chain…) — jo
  `run_hunt` ke generic spot-design me fit nahi hote unke apne producer (`daily_extend` inhe
  frozen-param se roz aaj tak extend karta, §5).

## 3. Key engine files

| File | Kaam |
|---|---|
| **`intraday_engine.py`** | Multi-design signal engine — NIFTY spot, 1x, long+short. Signal designs (`tod_orb` etc.) — spot-series signals `strategies/signals/*` ko delegate karte (ADR-010, live == backtest). |
| **`option_structures.py`** | Multi-leg STRUCTURE backtester (straddle/strangle/vertical/condor/backspread) — BS premium, per-leg. |
| **`bs_option.py`** | Pass-③ BS engine (single source): pricing + `calc_charges(when=)` + σ-proxy + weekly-expiry T + lot (scrip master) + reprice. `_core/payoff.py` bhi isse import karta (Rule 6B). |
| **`charges.py`** | **DATE-AWARE Zerodha F&O charges — SINGLE SOURCE** (Rule 6B). STT/txn kabhi hardcode nahi; Budget-2026 rates (STT sell 0.15% from 2026-04-01). |
| **`dom_spread` / `dom_cost` / `dom_recost`** | Bhai ke 20-level DOM order-book se REAL bid/ask spread measure (`dom_spread`) → `dom_spread_calib.json` (`dom_cost` loader) → recorded run ko re-cost bina re-run (`dom_recost`). `bs_option.slip_cost_leg()` wire (ADR-005). |
| **`ml_*.py`** | ML mining (LightGBM+SHAP / GP) + `ml_gate` (deflated-Sharpe + purged-CV + lockbox). Verdict: ~4M rules, koi formal gate clear nahi (saturated) — DO-NOT-REDO list CLAUDE.md me. |
| **`honest_sizing.py`** | Sizing-aware CAGR (results.js `net_pct` = lots=1 pe = CAGR NAHI, TRAP #127). DD-budget → MC-worst5 DD pe size → compound on real trade sequence. |

## 4. Output → dashboard wiring

```
 producer → runs/<slug>/results.js (RESULTS_SCHEMA format) + meta.json + index.html
                     │  append → runs/index.json
                     ▼
   Strategy Lab hub (hub.html) auto-lists  ·  Stats backtest view (Live⟷Backtest toggle)
```

- **`_ops/backtest_calendar.py`** (§ ARCHITECTURE) = `results.js` ko live-`calendar-summary`
  shape me deta (bs|full `all_trades` entry-date bucket) → Stats tab me din-wise backtest.
- Dashboard `dashboard_intraday.html` = ONE reusable template — plain nayi HTML mat banao,
  `results.js` schema me emit karo.

## 5. Gates + honesty (deploy-decision)

- **Deploy gate = Sharpe≥1 + p<0.05 + `min(train,OOS)`** (Monte-Carlo not-overfit). Ye gate
  honest metrics pe — `honest_sizing`/compounding = truth-detector (Sharpe>4, uncapped-blowup =
  red flag). `bs|full` P&L = real Zerodha charges minus (grep-able net).
- **Continuous:** `daily_extend.py` (16:25 timer) deployed runs ko FROZEN params se roz aaj tak
  extend (identity preserve — p_value/significance at-hunt frozen; sirf data window badhta).
- **Rule 10 (backtest-fidelity):** live config ≠ backtested config → number jhooth. Koi live
  tweak jo backtest me nahi tha → pehle backtest me daalo + re-run, phir enable.

---

## Cross-reference
- Results/BS contract: [`RESULTS_SCHEMA.md`](../scratch/nifty_trend/RESULTS_SCHEMA.md) · [`BS_OPTION_SIM.md`](../scratch/nifty_trend/BS_OPTION_SIM.md)
- StockMock-style leg-basket: [`../STOCKMOCK_PIPELINE.md`](../STOCKMOCK_PIPELINE.md)
- Backtest → live signal parity: [`ARCHITECTURE.md`](ARCHITECTURE.md) §9 + ADR-010
- Per-module (auto): [`MODULES.md`](MODULES.md)
