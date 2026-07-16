# 📊 Strategy Results Contract — `results.js` schema

> **Purpose:** The dashboard template (`dashboard_intraday.html`) renders ANY strategy's
> backtest by reading a single `results.js` file that sets `window.RESULTS = {...}`.
> Every strategy you build must emit `results.js` in EXACTLY this shape. Swap the file →
> the same dashboard shows the new strategy. Read this with `BS_OPTION_SIM.md` (trade
> P&L must be **option-premium** based, not spot).

The report generator (`intraday_report.py`) is the reference producer — mirror its output.

```js
window.RESULTS = {
  meta: {
    window:  ["2022-01-03", "2026-07-09"],   // [start, end] ISO dates
    days:    1111,                            // trading days in window
    start_cap: 1000000,                       // ₹ starting capital
    design:  "Mid-Day Opening-Range Breakout (11:00-13:00)",  // human title
    tf:      "15m",
    candles: [["2022-01-03", 17392.3, 17646.1, 17392.3, 17635.3], ...]  // daily NIFTY OHLC (for the trade chart), [date, o, h, l, c]
  },
  meta.passes:  ["instrument", "rms", "bs"],   // enables the 3-pass toggle
  meta.periods: ["full", "train", "oos"],
  meta.instrument: "NIFTY 50", meta.lot_size: 65, meta.lots: 1,   // for the info panel

  combos: {
    // NEW schema — key = "<pass>|<period>". The dashboard shows a PASS toggle
    // (① Instrument → ② +RMS → ③ +Black-Scholes) and a PERIOD toggle (full/train/oos).
    //   instrument = raw signal P&L on spot (no RMS, no options)
    //   rms        = same trades + account daily loss/profit caps
    //   bs         = pass-2 trades repriced into ATM CE/PE premium (Black-Scholes,
    //                real Zerodha charges) — the DEPLOYABLE truth (default view)
    "instrument|full": { /* one combo object, schema below */ },
    "rms|full": { ... }, "bs|full": { ... },
    "instrument|train": { ... }, "rms|train": { ... }, "bs|train": { ... },
    "instrument|oos": { ... },  "rms|oos": { ... },  "bs|oos": { ... }
    // (LEGACY single-axis "full"/"train"/"oos" keys still render — the dashboard
    //  auto-detects meta.passes and falls back, hiding the PASS toggle.)
  }
};
```

**Only the `bs|*` passes carry option fields** (`strike/opt_type/entry_prem/exit_prem`,
and gross/fee/pnl on the premium). `instrument|*` and `rms|*` are spot-notional
(gross = points × qty, rough fee) — the same strategy, one layer less realistic each.
`run_hunt.py` is the reference producer; the cleanest reuse is just to run it.

## ⚠️ `annual_return` is NOT a CAGR — never quote it as one

Every number in this file is produced at **`lots=1`** (`bs_option.reprice(..., lots=1)`)
against a **hardcoded `engine.py:21` → `START_CAP = 1_000_000`**. So:

```
net_pct = (one lot's rupee P&L) / (an arbitrary 10L we typed into engine.py)
```

There is **no compounding** (equity doubles → the next trade is still 1 lot) and the
denominator is **not capital-at-risk** (Mid-Day ORB's worst drawdown across 4.5 years is
₹17,690 — the other ~₹9.8L never worked, but is fully counted in the divide). That is why
every strategy here reads 3-8% "annual_return" and looks worse than an FD. It is the wrong
question, not a bad edge.

**These fields are comparable ACROSS strategies in this repo** (same denominator, same lot
count) — that is all they are for. Do not compare them to an FD, an index, or any external
return. For a real, sizing-aware CAGR + realised drawdown, run:

```bash
python honest_sizing.py --all --dd-budget 10,15 --max-lots 50
python honest_sizing.py --slug <slug> --check     # lots=1 must reproduce real_cost net
```

It takes a **drawdown budget** (the actual binding constraint), sizes lots to the run's own
Monte-Carlo **worst-5%** drawdown, compounds over the real `all_trades` sequence, and reports
the CAGR and realised DD the same trades would have produced. ORB: 7.6% → **29.1%** at a 10%
DD budget (lots 3→10, realised DD −4.9%), same trades / charges / DOM slip.

**Always pass `--max-lots`.** Uncapped, the same math compounds a 1686-trade edge into
four-figure crores. Treat that as a **free diagnostic**: any strategy whose honest CAGR
swings wildly with the cap has a backtest that is better than the truth. Full reasoning:
`LESSONS.md` TRAP #127.

## Combo object (one per view)

```js
{
  dna: { or_min:30, orb_k:1.0, atr_sl:2.5, rr:1.5, exit:"atr_rr" },  // optimized params (any keys)

  metrics: {
    // headline
    // ⚠️ net_pct / annual_return / calmar are FIXED-1-LOT numbers measured against a
    //    hardcoded start_cap. annual_return is NOT a CAGR — see the warning below.
    trades, net_pct, net_abs, final_cap, start_cap,
    sharpe, sortino, calmar, annual_return, maxdd, underwater_days, years,
    win_rate, wl_ratio, profit_factor, expectancy,
    avg_win, avg_loss, largest_win, largest_loss,
    total_wins, total_losses,
    win_long, win_short, pct_long, pct_short,
    avg_bars, win_avg_bars, loss_avg_bars,
    win_streak, loss_streak,
    fees,                                    // total ₹ charges (SUM of per-trade calcCharges)
    trades_per_day, trades_per_week, trades_per_month
  },

  equity:     [1000000, 1002340, ...],       // ~400 downsampled equity points (₹)
  benchmark:  [1000000, ...],                 // NIFTY buy&hold, same length, normalised to start_cap
  labels:     ["22-01", ...],                 // x-axis labels, same length as equity
  underwater: [0, -0.4, ...],                 // ~400 drawdown % points (<=0)
  worst_periods: [{ rank:1, x:1819, dd:-4.4, frac:0.62 }, ...],  // frac = 0..1 position on the curve

  monthly:  { "2022": {1:1.3, 2:0.3, ...}, "2023": {...} },  // year -> {month(1-12): return %}

  significance: { real_sharpe:1.71, p_value:0.000, null_p95:0.78, null_mean:0.08,
                  n_perm:1000, significant:true },

  mc: {                                       // Monte Carlo (1000 trade-bootstrap paths)
    table: { net:[orig,worst5,median,best5], maxdd:[...], sharpe:[...] },
    sharpe_dist: { original, median, best5, worst5 },
    paths: [[...120 pts...], ...60 paths],
    orig_path: [...120 pts...]
  },

  opt_table: [{ params:{...}, train_sharpe, oos_sharpe, net, dd, trades }, ...top 8],  // [] if none

  all_trades: [ /* EVERY trade — see below (OPTION-PREMIUM based) */ ],
  trades:     [ /* last 10, legacy — can mirror all_trades tail */ ]
}
```

## `all_trades[]` — one object per trade  ⚠️ OPTION-PREMIUM, not spot

The dashboard's Trades table + full-screen chart + month-modal + subtotals all read these.
P&L / gross / tax MUST be on the **traded ATM option premium** (per `BS_OPTION_SIM.md`),
NOT the index. Keep the spot levels too (for the candlestick chart markers).

```js
{
  side:      "long",              // "long" (bought CE) | "short" (bought PE)
  opt_type:  "CE",               // "CE" | "PE"
  strike:    17900,              // ATM strike traded
  entry_dt:  "2022-01-05 12:45", // "YYYY-MM-DD HH:MM" IST
  exit_dt:   "2022-01-05 15:15",

  entry_spot: 17892.2,           // NIFTY index at entry  (chart markers use these)
  exit_spot:  17935.4,           // NIFTY index at exit
  points:     43.2,              // spot index points moved (signed by side)

  entry_prem: 98.5,              // ATM option PREMIUM at entry (Black-Scholes)
  exit_prem:  121.0,             // ATM option premium at exit  (Black-Scholes, new spot + less TTE)
  qty:        75,                // lots × lot_size (NIFTY lot from scrip master, never hardcode)

  gross:      1687.5,            // (exit_prem - entry_prem) × qty   [premium P&L before costs]
  fee:        62.0,              // calcCharges(entry_prem, exit_prem, qty)  [real Zerodha F&O]
  pnl:        1625.5,            // gross - fee  [NET ₹, this drives everything]

  bars:       10,                // holding bars (for Duration col)
  reason:     "EOD 3:15"         // exit reason (Target / ATR SL / EOD 3:15 / ...)
}
```

**Column mapping in the dashboard** (`TCOLS`): Date, Side, In, Out, Dur, Entry (spot),
Exit (spot), **Entry Prem, Exit Prem** (add once premiums exist), Qty, Points, Gross ₹,
Tax ₹, Net ₹, % Move, Exit Reason. Month subtotals + GRAND TOTAL sum gross/fee/pnl.

## Notes
- All ₹ display uses Indian lakh grouping (the dashboard's `inr()` handles it).
- `candles` = **daily** NIFTY OHLC over the full window (chart is zoomable per-trade; daily is enough).
- If a field is genuinely N/A for a strategy, omit it — the dashboard degrades gracefully
  (e.g. `opt_table: []` hides the sweep panel).
- Keep `results.js` self-contained (`window.RESULTS = {...};`) — the dashboard loads it via
  `<script src="results.js">`. One strategy = one results.js.
