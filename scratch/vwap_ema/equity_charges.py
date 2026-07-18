"""equity_charges.py — Zerodha EQUITY INTRADAY (MIS) round-trip cost.

Not F&O. charges.py in nifty_trend is options/futures only, so this is a small
separate equity-intraday model (Rule 6B: new instrument class = new source, one file).

Zerodha equity intraday, verified against zerodha.com/charges (2026):
  brokerage : 0.03% of turnover OR Rs 20 per order, whichever LOWER (x2 orders)
  STT       : 0.025% on SELL turnover only
  exch txn  : 0.00297% (NSE) on total (buy+sell) turnover
  SEBI      : Rs 10 / crore (0.00001%) on total turnover
  stamp     : 0.003% on BUY turnover only
  GST       : 18% on (brokerage + exch txn + SEBI)

Returns total round-trip cost in Rs for a position of `qty` shares entered at
`entry_px` and exited at `exit_px` (direction-agnostic — buy & sell legs both
happen either way; STT/stamp are per-side by BUY/SELL not by entry/exit).
"""

BROKERAGE_RATE = 0.0003
BROKERAGE_CAP = 20.0
STT_SELL = 0.00025
TXN = 0.0000297
SEBI = 0.0000001
STAMP_BUY = 0.00003
GST = 0.18


def roundtrip_cost(entry_px, exit_px, qty, side):
    """side: 'LONG' or 'SHORT'. Returns Rs total charges for the round trip."""
    if side == "LONG":
        buy_px, sell_px = entry_px, exit_px
    else:
        buy_px, sell_px = exit_px, entry_px
    buy_turn = buy_px * qty
    sell_turn = sell_px * qty
    total_turn = buy_turn + sell_turn

    brokerage = min(BROKERAGE_RATE * buy_turn, BROKERAGE_CAP) + \
                min(BROKERAGE_RATE * sell_turn, BROKERAGE_CAP)
    stt = STT_SELL * sell_turn
    txn = TXN * total_turn
    sebi = SEBI * total_turn
    stamp = STAMP_BUY * buy_turn
    gst = GST * (brokerage + txn + sebi)
    return brokerage + stt + txn + sebi + stamp + gst
