"""Real ₹1,00,000 account model: NIFTY momentum vs Buy&Hold, AFTER Indian
transaction charges AND income tax.

Instrument: NIFTYBEES ETF (delivery) — with ₹1 lakh you cannot hold even 1 NIFTY
futures lot (margin ~₹1.2L+), so the honest retail vehicle is the index ETF.
Every charge is an explicit, labelled assumption (edit if your broker differs).

Strategy: L50/H10 breakout momentum (the validated pick), real trades, full
capital deployed each trade, ~64% of the time in cash.
"""
import numpy as np
import pandas as pd
from _common import load_daily, DEFAULT_DAILY

CAP = 100_000
# ---- NIFTYBEES ETF delivery charges (Zerodha, current) — EXPLICIT ASSUMPTIONS ----
BROKERAGE   = 0.0            # delivery brokerage = ₹0
STT_SELL    = 0.001 / 100    # equity ETF: STT 0.001% on SELL only (stocks would be 0.1%x2)
EXCH_TXN    = 0.00297 / 100  # NSE txn, both legs
SEBI        = 10 / 1e7       # ₹10 per crore
STAMP_BUY   = 0.015 / 100    # stamp duty, BUY only
GST         = 0.18           # on (brokerage + txn + sebi)
DP_SELL     = 16.0           # depository charge per sell (delivery)
STCG_TAX    = 0.20           # short-term (<12m) equity gains, Budget-2024 (was 15%)
LTCG_TAX    = 0.125          # long-term (>12m), 12.5% above ₹1.25L/yr
LTCG_EXEMPT = 125_000
IDLE_RATE   = 0.06           # liquid-fund yield on cash while flat (realistic)


def leg_charges(value, side):
    txn = EXCH_TXN * value
    sebi = SEBI * value
    stamp = STAMP_BUY * value if side == "BUY" else 0.0
    stt = STT_SELL * value if side == "SELL" else 0.0
    dp = DP_SELL if side == "SELL" else 0.0
    gst = GST * (BROKERAGE + txn + sebi)
    return BROKERAGE + txn + sebi + stamp + stt + dp + gst


def strategy_trades(c, L=50, H=10, stop=5.0):
    hiN = pd.Series(c).rolling(L).max().shift(1).values
    out, i, pos = [], L, None
    while i < len(c):
        if pos is None:
            if not np.isnan(hiN[i]) and c[i] >= hiN[i]:
                pos = i
            i += 1; continue
        held = i - pos
        if held >= H or c[i] <= c[pos] * (1 - stop / 100) or i == len(c) - 1:
            out.append((pos, i, c[i] / c[pos] - 1)); pos = None
        i += 1
    return out


def simulate(c, dates, trades, idle_rate):
    """Day-by-day ₹ account. In-position days earn NIFTY daily ret; flat days
    earn idle_rate. Charges deducted on buy & sell days. Returns (cash_path,
    gross_trade_pnl_total, total_charges)."""
    n = len(c)
    in_pos = np.zeros(n, bool)
    entry_days, exit_days = set(), set()
    for (ei, xi, _) in trades:
        for k in range(ei, xi + 1):
            in_pos[k] = True
        entry_days.add(ei); exit_days.add(xi)

    eq = CAP
    charges_tot = 0.0
    trade_pnl = 0.0
    pos_val = 0.0        # value invested in ETF
    for i in range(n):
        if i in entry_days:
            pos_val = eq
            ch = leg_charges(pos_val, "BUY"); charges_tot += ch; eq -= ch
        if in_pos[i] and i not in entry_days:
            r = c[i] / c[i - 1] - 1
            gain = pos_val * r
            pos_val += gain; eq += gain; trade_pnl += gain
        if not in_pos[i]:
            eq *= (1 + idle_rate / 252)     # idle cash earns liquid yield
        if i in exit_days:
            ch = leg_charges(pos_val, "SELL"); charges_tot += ch; eq -= ch
            pos_val = 0.0
    return eq, trade_pnl, charges_tot


def bh_after_tax(c, idle_unused=None):
    gross = CAP * (c[-1] / c[0] - 1)
    buy_ch = leg_charges(CAP, "BUY")
    sell_ch = leg_charges(CAP + gross, "SELL")
    net_gain = gross - buy_ch - sell_ch
    ltcg = max(0.0, (net_gain - LTCG_EXEMPT)) * LTCG_TAX   # held >1yr
    return CAP + net_gain - ltcg, buy_ch + sell_ch, ltcg


def cagr(final, yrs):
    return ((final / CAP) ** (1 / yrs) - 1) * 100


def main():
    df = load_daily(DEFAULT_DAILY)
    c = df["Close"].values
    dates = pd.to_datetime(df["Date"].values)
    yrs = (dates[-1] - dates[0]).days / 365.25
    trades = strategy_trades(c)
    print(f"NIFTY {dates[0].date()}..{dates[-1].date()} ({yrs:.1f} yrs) | "
          f"₹{CAP:,} start | {len(trades)} trades | instrument: NIFTYBEES ETF (delivery)\n")

    for idle_rate, tag in [(0.0, "cash idle @ 0%"), (IDLE_RATE, f"cash idle @ {IDLE_RATE*100:.0f}% liquid")]:
        pre_tax, trade_pnl, charges = simulate(c, dates, trades, idle_rate)
        # STCG on the ETF trading gains only (idle interest taxed at slab ~30%)
        stcg = max(0.0, trade_pnl_net(trade_pnl, charges)) * STCG_TAX
        after_tax = pre_tax - stcg
        print(f"--- Strategy ({tag}) ---")
        print(f"  before tax : ₹{pre_tax:,.0f}   (charges paid ₹{charges:,.0f})")
        print(f"  STCG @20%  : ₹{stcg:,.0f}")
        print(f"  AFTER TAX  : ₹{after_tax:,.0f}   |  net CAGR {cagr(after_tax, yrs):+.2f}%")
        print()

    bh_final, bh_ch, bh_ltcg = bh_after_tax(c)
    print(f"--- NIFTY Buy & Hold (1 buy, 1 sell in {yrs:.1f} yrs) ---")
    print(f"  charges ₹{bh_ch:,.0f} | LTCG @12.5% ₹{bh_ltcg:,.0f}")
    print(f"  AFTER TAX  : ₹{bh_final:,.0f}   |  net CAGR {cagr(bh_final, yrs):+.2f}%")


def trade_pnl_net(trade_pnl, charges):
    # approx net realized ETF gain for tax = gross trade pnl minus charges
    return trade_pnl - charges


if __name__ == "__main__":
    main()
