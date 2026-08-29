"""Registry economics — per-run lot-independent P&L / charge / capital model.

Lets the /registry2 page rescale Net / Tax / Capital / Return-on-capital to ANY lot
count CLIENT-SIDE, instantly, WITHOUT touching the backtest SHAPE (Sharpe / Win% / PF /
MaxDD% are lot-independent ratios — they never change with size). Display-only; reuses
backtest_calendar._load_results (mtime-cached) + charges.py (Rule 6B). No order/risk path.

The economics are LINEAR in lots N, so the client scales with plain arithmetic:
    gross(N)   = gross_per_lot * N
    charges(N) = flat_charge + per_lot_charge * N   # brokerage Rs20/order is FLAT (does
                                                     # NOT scale); STT/exch/GST scale with
                                                     # turnover (per lot)
    net(N)     = gross(N) - charges(N)
    capital(N) = capital_per_lot * N
    roc(N)     = net(N) / capital(N) * 100

Because brokerage is flat, net(N) is BETTER than a naive net(1)*N at higher lots (the flat
cost amortises) — the honest StockMock lesson. All numbers come from the bs|full combo's
all_trades[] (real premium + real date-aware charges).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "scratch", "nifty_trend"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backtest_calendar as _bc  # noqa: E402  (mtime-cached results.js reader)

_BROKERAGE_PER_ORDER = 20.0   # Zerodha F&O flat Rs20/executed order (charges.py); a
                              # round-trip leg = entry + exit = 2 orders = Rs40 flat.
_SPAN_PCT = 0.11              # ~naked short-option SPAN as % of notional (fair NIFTY proxy)
# A DEFINED-RISK hedged structure (iron-fly / condor / vertical) blocks far LESS margin
# than the naked SPAN on its short legs, because the long wings cut the broker's scenario
# risk. Calibrated against a real live NIFTY weekly iron-fly (Sensibull margin 1.38L on
# 2 lots = ~69k/lot vs naked-SPAN ~1.5L/lot => ~0.5). Rough, labelled "HEDGED~est".
_HEDGE_MARGIN_FACTOR = 0.5
_HEDGE_KEYWORDS = ("fly", "condor", "iron", "spread", "hedge")

_ECON_CACHE = {}   # slug -> (mtime, econ dict|None)


def _one_run(slug):
    data = _bc._load_results(slug)
    combo, _key = _bc._pick_combo(data, "bs", "full")
    trades = (combo or {}).get("all_trades") or []
    if not trades:
        return None
    lot_size = int(data.get("lot_size") or (data.get("meta") or {}).get("lot_size") or 0) or None

    gross = 0.0
    fee = 0.0
    n = 0
    buy_prem = []       # premium PAID per position (debit legs) -> capital for a BUY
    sell_notional = []  # notional of short legs -> SPAN base for a SELL
    lot_mults = []      # per-trade lots-baked-in (qty / lot_size) -> normalise to 1 lot
    legs_per_trade = 2  # orders/round-trip: 1-leg=2, straddle=4, iron-fly/condor=8
    hedged = False
    for t in trades:
        g = float(t.get("gross") or 0)
        f = float(t.get("fee") or 0)
        gross += g
        fee += f
        n += 1
        ep = float(t.get("entry_prem") or 0)
        xp = float(t.get("exit_prem") or 0)
        q = float(t.get("qty") or 0)
        es = float(t.get("entry_spot") or 0)
        ot = str(t.get("opt_type") or "").lower()
        if any(k in ot for k in _HEDGE_KEYWORDS):
            hedged = True
        # legs baked into a single stored row (a "fly CE+PE" row = 4 legs = 8 orders)
        if "+" in ot:                       # combined multi-leg row (CE+PE, fly CE+PE)
            legs_per_trade = max(legs_per_trade, 8 if hedged else 4)
        # lots baked into this run's qty (backtest may size at N lots, e.g. 5)
        if lot_size and q > 0 and q % lot_size == 0:
            lot_mults.append(int(round(q / lot_size)))
        # A BUY leg profits when premium RISES (gross sign == premium-move sign);
        # a SELL leg profits when premium FALLS.
        is_buy = (g >= 0) == ((xp - ep) >= 0)
        if ep > 0 and q > 0 and is_buy:
            buy_prem.append(ep * q)          # rupee premium paid (at the run's native qty)
        elif q > 0 and es > 0 and not is_buy:
            sell_notional.append(es * q)     # short notional (at the run's native qty)

    # lots the backtest sized at (median, robust to odd rows); 1 if not derivable
    lots_in_run = int(sorted(lot_mults)[len(lot_mults) // 2]) if lot_mults else 1
    lots_in_run = max(1, lots_in_run)

    # ---- charge split: flat brokerage (does NOT scale with lots) vs turnover (per lot).
    # flat_charge is the WHOLE run's brokerage (lot-independent). Only the turnover part
    # (STT/exch/GST) scales per lot, so divide just that by lots_in_run. ----
    flat = n * legs_per_trade * _BROKERAGE_PER_ORDER
    if flat > fee:                           # run modelled little/no brokerage -> don't
        flat = fee                           # over-subtract; treat all charge as flat
    per_lot_charge = max(0.0, fee - flat) / lots_in_run

    # ---- capital per ONE lot: premium paid (BUY-dominant) or ~SPAN (SELL-dominant),
    # normalised out of the run's native lot count. A hedged/defined-risk structure
    # (iron-fly/condor/spread) blocks far less than naked SPAN -> apply hedge factor. ----
    cap = None
    nb, ns = len(buy_prem), len(sell_notional)
    if nb >= ns and buy_prem:
        cap = (sum(buy_prem) / nb) / lots_in_run       # premium paid = capital for a BUY
        side = "BUY"
    elif sell_notional:
        cap = (sum(sell_notional) / ns) * _SPAN_PCT / lots_in_run   # ~naked short SPAN
        if hedged:
            cap *= _HEDGE_MARGIN_FACTOR                 # defined-risk: wings cut margin
            side = "HEDGED~"
        else:
            side = "SELL"
    else:
        side = "n/a"

    return {
        "gross_per_lot": round(gross / lots_in_run, 2),   # normalised to ONE lot
        "flat_charge": round(flat, 2),                    # whole-run flat (lot-independent)
        "per_lot_charge": round(per_lot_charge, 2),       # turnover charge for ONE lot
        "net_per_lot": round(gross / lots_in_run - (flat + per_lot_charge), 2),  # 1-lot net
        "capital_per_lot": (round(cap) if cap else None),
        "capital_kind": side,           # BUY=premium, SELL=~SPAN, HEDGED~=defined-risk est
        "lot_size": lot_size,
        "lots_in_run": lots_in_run,     # lots the backtest was sized at (for transparency)
        "trades": n,
    }


def economics(slug):
    """Per-run economics (lot-independent), cached on results.js mtime. None if no run."""
    try:
        mt = os.path.getmtime(_bc._results_path(slug))
    except Exception:
        return None
    hit = _ECON_CACHE.get(slug)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        e = _one_run(slug)
    except Exception:
        e = None
    if len(_ECON_CACHE) > 300:
        _ECON_CACHE.pop(next(iter(_ECON_CACHE)))
    _ECON_CACHE[slug] = (mt, e)
    return e


def all_economics(slugs=None):
    """{slug: econ} for the given slugs (default: every run in runs/index.json)."""
    if slugs is None:
        try:
            slugs = [r.get("slug") for r in _bc.list_runs() if isinstance(r, dict)]
        except Exception:
            slugs = []
    out = {}
    for s in slugs:
        if not s:
            continue
        e = economics(s)
        if e:
            out[s] = e
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(all_economics(), indent=1, default=str))
