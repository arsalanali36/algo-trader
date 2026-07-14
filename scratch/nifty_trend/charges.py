"""charges.py — DATE-AWARE Zerodha F&O charges. SINGLE SOURCE OF TRUTH (Rule 6B).

WHY THIS FILE EXISTS (2026-07-14)
---------------------------------
STT/txn rates have changed TWICE inside our 4.5-year backtest window, so a single
global constant misprices one side of every boundary (same failure shape as the
expiry-weekday bug, KNOWN_ISSUES #1 -> expiry_calendar.py). bs_option.calc_charges
was still on PRE-Oct-2024 rates (0.0625% STT / 0.053% txn) — ~2.4x under-priced STT
for anything after Apr-2026.

VERIFIED REGIME TABLE (checked live against zerodha.com/charges on 2026-07-14 +
Zerodha z-connect bulletin "Revision in exchange transaction charges and STT from
October 1, 2024"):

  OPTIONS (per premium turnover)             pre-2024-10-01   2024-10-01..   2026-04-01..
    STT (SELL side)                          0.0625%          0.10%          0.15%
    exchange txn (both legs)                 0.053%*          0.03503%       0.03553%
  FUTURES (per contract turnover)
    STT (SELL side)                          0.0125%          0.02%          0.05%
    exchange txn (both legs)                 0.00183%         0.00173%       0.00183%
  CONSTANT ACROSS REGIMES
    brokerage      Rs 20 / executed order (flat)
    SEBI           Rs 10 / crore  (0.00001%)
    stamp duty     options 0.003% BUY side · futures 0.002% BUY side
    GST            18% on (brokerage + txn + SEBI)

  * 0.053% = the rate Zerodha itself displayed/billed pre-Oct-2024 (and what this
    codebase + templates/index.html calcCharges always used) — kept for regime 1 so
    pre-Oct-2024 trades reproduce the previously recorded fee exactly.

⚠️ Rs 40/order claim (SEBI 50% cash-collateral rule) is NOT on Zerodha's charges
page — flat Rs 20 is what's listed, so Rs 20 is what we model. If the account ever
gets billed Rs 40/order, bump BROKERAGE_PER_ORDER (one place).

Effective-date source for the 2026-04-01 boundary: Budget 2026 (user-verified);
current rates independently confirmed live on zerodha.com/charges 2026-07-14.

USAGE
-----
    import charges
    fee = charges.option_charges(ep, xp, qty, entry_side="BUY", when=entry_ts)
    # when accepts date/datetime/pd.Timestamp/np.datetime64/ISO str; None = TODAY
    # (current regime) — historical trades MUST pass their entry timestamp.

bs_option.calc_charges(..., when=) delegates here — all engines go through that.
"""
import datetime as _dt

import numpy as np

# (effective_from ISO, rates) — ascending; first row = default for anything earlier.
OPT_REGIMES = [
    ("1900-01-01", dict(stt_sell=0.000625, txn=0.00053)),
    ("2024-10-01", dict(stt_sell=0.00100,  txn=0.0003503)),   # Budget-2024 hike + NSE true-to-label
    ("2026-04-01", dict(stt_sell=0.00150,  txn=0.0003553)),   # Budget-2026 hike (current)
]
FUT_REGIMES = [
    ("1900-01-01", dict(stt_sell=0.000125, txn=0.0000183)),
    ("2024-10-01", dict(stt_sell=0.00020,  txn=0.0000173)),
    ("2026-04-01", dict(stt_sell=0.00050,  txn=0.0000183)),   # Budget-2026 hike (current)
]

BROKERAGE_PER_ORDER = 20.0     # flat, per executed order (2 orders per round trip)
SEBI_FRAC = 0.0000001          # Rs 10 / crore, on total turnover
STAMP_OPT_BUY = 0.00003        # 0.003% on BUY-side premium (options)
STAMP_FUT_BUY = 0.00002        # 0.002% on BUY side (futures)
GST_FRAC = 0.18                # on brokerage + txn + SEBI


def _to_date(when):
    """None -> today (current regime). Accepts date/datetime/pd.Timestamp/
    np.datetime64/ISO string."""
    if when is None:
        return _dt.date.today()
    if isinstance(when, _dt.datetime):
        return when.date()
    if isinstance(when, _dt.date):
        return when
    return _dt.date.fromisoformat(str(when)[:10])


def _regime(regimes, when):
    d = _to_date(when)
    r = regimes[0][1]
    for iso, vals in regimes:
        if _dt.date.fromisoformat(iso) <= d:
            r = vals
        else:
            break
    return r


def opt_rates(when=None):
    """{'stt_sell':..,'txn':..} in force on `when` (options)."""
    return _regime(OPT_REGIMES, when)


def fut_rates(when=None):
    return _regime(FUT_REGIMES, when)


def option_charges(entry_prem, exit_prem, qty, entry_side="BUY", when=None):
    """Full Zerodha F&O round-trip charge for ONE option leg, rates as of `when`.
    Same formula shape as the old bs_option.calc_charges — only STT/txn are
    regime-looked-up. when=None = TODAY (forward-looking callers)."""
    r = opt_rates(when)
    buy_px = entry_prem if entry_side == "BUY" else exit_prem
    sell_px = exit_prem if entry_side == "BUY" else entry_prem
    buy_turn, sell_turn = buy_px * qty, sell_px * qty
    total = buy_turn + sell_turn
    brokerage = 2.0 * BROKERAGE_PER_ORDER
    stt = r["stt_sell"] * sell_turn
    exch = r["txn"] * total
    sebi = SEBI_FRAC * total
    stamp = STAMP_OPT_BUY * buy_turn
    gst = GST_FRAC * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst


def futures_charges(entry_px, exit_px, qty, entry_side="BUY", when=None):
    """Round-trip futures charge (no engine trades futures today — completeness)."""
    r = fut_rates(when)
    buy_px = entry_px if entry_side == "BUY" else exit_px
    sell_px = exit_px if entry_side == "BUY" else entry_px
    buy_turn, sell_turn = buy_px * qty, sell_px * qty
    total = buy_turn + sell_turn
    brokerage = 2.0 * BROKERAGE_PER_ORDER   # (0.03% cap rarely binds for 1-lot index futures)
    stt = r["stt_sell"] * sell_turn
    exch = r["txn"] * total
    sebi = SEBI_FRAC * total
    stamp = STAMP_FUT_BUY * buy_turn
    gst = GST_FRAC * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst


def opt_rates_vec(dates):
    """Vectorized regime lookup: array-like of dates -> (stt_sell[], txn[]) float
    arrays. For ml_gp_precompute-style whole-lake passes."""
    d64 = np.asarray(dates, dtype="datetime64[D]")
    stt = np.full(d64.shape, OPT_REGIMES[0][1]["stt_sell"], dtype=float)
    txn = np.full(d64.shape, OPT_REGIMES[0][1]["txn"], dtype=float)
    for iso, vals in OPT_REGIMES[1:]:
        m = d64 >= np.datetime64(iso)
        stt[m] = vals["stt_sell"]
        txn[m] = vals["txn"]
    return stt, txn


if __name__ == "__main__":
    # sanity: one leg, prem 100->110, qty 75, across the three regimes
    for iso in ("2024-06-03", "2025-06-03", "2026-06-03"):
        f = option_charges(100, 110, 75, when=iso)
        r = opt_rates(iso)
        print(f"{iso}: stt_sell={r['stt_sell']*100:.4f}% txn={r['txn']*100:.5f}% -> fee Rs {f:.2f}")
    print("today:", round(option_charges(100, 110, 75), 2))
