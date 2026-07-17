"""Ars chain — the exit-config comparison again, on the PROJECT'S OWN Sharpe.

Why this file exists: arschain_backtest.stats() rolled its own "sharpe" as
    mean/sd * sqrt(n_trades)
which is a full-sample t-statistic, NOT the annualised Sharpe every other run in this
repo reports. Over 8.5 years the two differ by ~sqrt(8.5) = 2.9x, so "Sharpe 3.85" was
really ~1.3 — and, worse, it was silently NOT comparable to the Sharpe>=1 deploy gate or
to ORB's 2.37. Rule 6B exists precisely to stop this: engine._annualize_sharpe() was
already the single source and should have been called from the start.

So: same engine, same trades, same BS repricing — but every metric now comes from
engine's own functions:
    equity  = START_CAP + cumulative net P&L, one row per TRADING DAY (flat days
              included as 0 — dropping them inflates Sharpe)
    sharpe  = engine._annualize_sharpe(eqdf)   ret.mean()/ret.std()*sqrt(252)
    maxdd%  = engine's underwater formula (e/peak - 1)*100

The t-stat is still printed, but under its real name, because it answers a DIFFERENT and
genuinely useful question (is this edge distinguishable from zero over the sample?).

Caveat that still bounds everything here: sigma = realised vol, which flatters an option
BUYER (see arschain_vrp_buy.py). And this is one run — no significance test, no train/OOS
split, no Monte-Carlo. run_hunt.py is what produces a shippable number; this does not.

Diagnostic/one-off. Reads the repo, writes nothing to it.
"""

import os
import pickle
import sys

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import arschain_backtest as ab   # noqa: E402  engine runner
import bs_option as bs           # noqa: E402
import engine as eng             # noqa: E402  THE canonical metrics (Rule 6B)

CACHE = os.path.join(_HERE, "trades_cache.pkl")

CONFIGS = (("jaisa abhi hai (trail ON, zone ON)", {}),
           ("trail OFF (zone ON)", dict(exit_atr=False)),
           ("zone OFF (trail ON)", dict(exit_zone=False)),
           ("dono OFF (sirf 3:15 + reversal)", dict(exit_atr=False, exit_zone=False)))


def t_stat(pnls):
    """Full-sample t-stat — what stats() mislabelled as Sharpe. Useful, different."""
    n = len(pnls)
    if n < 2:
        return 0.0
    mean = sum(pnls) / n
    sd = (sum((p - mean) ** 2 for p in pnls) / n) ** 0.5
    return (mean / sd * (n ** 0.5)) if sd else 0.0


def canonical(rows, all_days):
    """engine's own Sharpe/maxdd, on a daily equity curve over EVERY trading day."""
    pnl_by_day = {}
    for r in rows:
        d = pd.Timestamp(r["exit_dt"]).date()
        pnl_by_day[d] = pnl_by_day.get(d, 0.0) + r["pnl"]

    eq, run = [], eng.START_CAP
    for d in all_days:                      # flat days carried at 0 — never dropped
        run += pnl_by_day.get(d, 0.0)
        eq.append((pd.Timestamp(d), run))
    eqdf = pd.DataFrame(eq, columns=["Datetime", "equity"])

    sharpe, sortino, _ = eng._annualize_sharpe(eqdf)
    e = eqdf.equity.values
    peak = e.copy()
    for i in range(1, len(peak)):
        peak[i] = max(peak[i - 1], peak[i])
    maxdd_pct = ((e / peak - 1) * 100).min()
    return sharpe, sortino, maxdd_pct


def main():
    print("\n  bars...", flush=True)
    cont5 = ab.load_5m(None)
    daily = ab.daily_from_5m(cont5)
    all_days = list(daily["date"])
    lot = bs.get_nifty_lot()
    sig = bs.realised_vol_map(daily.set_index("date")["close"])

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = pickle.load(open(CACHE, "rb"))
            print("  cache mila: %s" % list(cache))
        except Exception:
            cache = {}

    # vrp 1.0 = realised vol (flatters the buyer); 1.2 = IV 20% over realised = NIFTY's
    # ordinary state, i.e. what a buyer actually pays. Both shown — the gap IS the finding.
    for mult, note in ((1.0, "realised vol — buyer ke haq me jhuka (asli nahi)"),
                       (1.2, "IV 20% upar — NIFTY ka aam, yahi asli keemat hai")):
        sig_m = {d: v * mult for d, v in sig.items()}
        print("\n  " + "=" * 108)
        print("  BUY @ ATM, 1 lot | vrp_mult=%.1f (%s)" % (mult, note))
        print("  " + "-" * 108)
        print("  %-34s %7s %12s %7s %7s %8s %9s %9s" % (
            "config", "trades", "NET Rs", "win%", "PF", "Sharpe", "maxDD %", "t-stat"))
        for label, over in CONFIGS:
            if label in cache:
                trades = cache[label]
            else:
                trades = ab.run_engine(cont5, daily, ab.engine_cfg(**over))
                cache[label] = trades
                pickle.dump(cache, open(CACHE, "wb"))
            rows = bs.reprice(trades, sig_m, lot, lots=1, itm_steps=0)
            pnls = [r["pnl"] for r in rows]
            st = ab.stats(pnls)
            sharpe, sortino, dd_pct = canonical(rows, all_days)
            gate = "  <-- gate PASS" if sharpe >= 1.0 else ""
            print("  %-34s %7d %12s %6.1f%% %7.2f %8.2f %8.1f%% %9.2f%s" % (
                label, len(pnls), f"{sum(pnls):,.0f}", st["win"], st["pf"],
                sharpe, dd_pct, t_stat(pnls), gate))
        print("  " + "=" * 108)
    print("  Sharpe = daily returns x sqrt(252) — ORB ke 2.37 aur >=1 gate se SEEDHA comparable")
    print("  t-stat = poore sample ka; 'edge zero se alag hai?' — Sharpe se ALAG sawaal")
    print()


if __name__ == "__main__":
    main()
