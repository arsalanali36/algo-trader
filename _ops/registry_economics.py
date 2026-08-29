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
_HEDGE_KEYWORDS = ("fly", "condor", "iron", "spread", "hedge", "wing")
_SHORT_WORDS = ("short", "sell", "credit")        # explicit net-seller
_LONG_WORDS = ("long", "buy", "debit")            # explicit net-buyer
_CRYPTO_KEYWORDS = ("btc", "eth", "delta", "crypto")
# Delta (crypto) INR conversion — mirrors delta_ironfly_trader: rupee = pts x cv x usd_inr.
_CRYPTO_CV = {"btc": 0.001, "eth": 0.01}          # contract value (BTC/ETH per lot)
_USD_INR = 85.0                                   # Delta India rate (config default)
_DELTA_WING_DEFAULT = 2000.0                       # iron-fly wing (pts) if run omits it

_ECON_CACHE = {}   # slug -> (mtime, econ dict|None)
_REG_CACHE = {"mt": None, "idx": {}}   # slug -> registry entry (structure/legs/instrument)


def _registry_index():
    """{slug: registry-entry} from strategy_registry.json, mtime-cached. The registry's
    declared structure/legs/instrument is the SINGLE SOURCE OF TRUTH for how a strategy's
    capital is classified — so every current AND future strategy is handled by declaring
    those fields (already mandatory), not by teaching this module new opt_type strings."""
    try:
        rp = os.path.join(_ROOT, "strategy_registry.json")
        mt = os.path.getmtime(rp)
    except Exception:
        return {}
    if _REG_CACHE["mt"] == mt:
        return _REG_CACHE["idx"]
    idx = {}
    try:
        import json as _json
        with open(rp, encoding="utf-8") as fh:
            for _k, s in (_json.load(fh).get("strategies") or {}).items():
                sl = s.get("slug")
                if sl:
                    idx[sl] = s
    except Exception:
        idx = {}
    _REG_CACHE["mt"], _REG_CACHE["idx"] = mt, idx
    return idx


def _classify(structure, legs, instrument, run_net_short):
    """One capital-class decision for ALL strategies. Structure/legs decide the SHAPE
    (crypto / hedged-defined-risk / plain); explicit long/short words decide DIRECTION,
    and when the structure string is direction-ambiguous ("Strangle"/"Straddle" can be
    long OR short) the run's own net buy/sell decides. Returns:
    'crypto' | 'hedged' | 'naked_sell' | 'buy'."""
    st = str(structure or "").lower()
    ins = str(instrument or "").lower()
    try:
        nlegs = int(legs) if legs is not None else 0
    except Exception:
        nlegs = 0
    if any(k in ins for k in _CRYPTO_KEYWORDS):
        return "crypto"                              # Delta portfolio margin != NSE SPAN
    is_hedged = nlegs >= 4 or any(k in st for k in _HEDGE_KEYWORDS)
    if any(w in st for w in _SHORT_WORDS):
        net_short = True
    elif any(w in st for w in _LONG_WORDS):
        net_short = False
    else:
        net_short = run_net_short                    # ambiguous structure -> trust the run
    if is_hedged:
        # net-short = defined-risk seller (iron fly/condor, credit spread) -> SPAN x factor
        # net-long  = debit structure (backspread, long fly) -> premium paid
        return "hedged" if net_short else "buy"
    return "naked_sell" if net_short else "buy"


def _load_meta_params(slug):
    """Read the run's meta.json params (authoritative lots + structure the backtest was
    sized/built at). meta.json is a sibling of results.js. Returns {} on any failure."""
    try:
        import os as _os
        import json as _json
        d = _os.path.dirname(_bc._results_path(slug))
        with open(_os.path.join(d, "meta.json"), encoding="utf-8") as fh:
            return (_json.load(fh) or {}).get("params") or {}
    except Exception:
        return {}


def _one_run(slug, reg=None):
    data = _bc._load_results(slug)
    combo, _key = _bc._pick_combo(data, "bs", "full")
    trades = (combo or {}).get("all_trades") or []
    if not trades:
        return None
    lot_size = int(data.get("lot_size") or (data.get("meta") or {}).get("lot_size") or 0) or None

    # Authoritative sizing from meta.params.lots (backtest's own record) — more reliable
    # than deriving lots from qty/lot_size (BankNifty's backtest lot != the current
    # lot_size, so qty is not divisible).
    _mp = _load_meta_params(slug)
    meta_lots = _mp.get("lots")
    try:
        meta_lots = int(meta_lots) if meta_lots else None
    except Exception:
        meta_lots = None
    # Structure/legs/instrument: registry entry is authoritative; meta.params is a backup.
    reg = reg or {}
    structure = reg.get("structure") or _mp.get("structure")
    legs = reg.get("legs") or _mp.get("legs")
    instrument = reg.get("instrument") or _mp.get("instrument")

    gross = 0.0
    fee = 0.0
    n = 0
    buy_prem = []       # premium PAID per position (debit legs) -> capital for a BUY
    sell_notional = []  # notional of short legs -> SPAN base for a SELL
    lot_mults = []      # per-trade lots-baked-in (qty / lot_size) -> normalise to 1 lot
    legs_per_trade = 2  # orders/round-trip: 1-leg=2, straddle=4, iron-fly/condor=8
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
        # legs baked into a single stored row (a "fly CE+PE" row = 4 legs = 8 orders)
        if "+" in ot or "fly" in ot or "condor" in ot:
            legs_per_trade = max(legs_per_trade, 8 if ("fly" in ot or "condor" in ot) else 4)
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

    # lots the backtest sized at: meta.params.lots wins (authoritative); else derive from
    # qty/lot_size (median, robust to odd rows); else 1.
    if meta_lots and meta_lots > 0:
        lots_in_run = meta_lots
    elif lot_mults:
        lots_in_run = int(sorted(lot_mults)[len(lot_mults) // 2])
    else:
        lots_in_run = 1
    lots_in_run = max(1, lots_in_run)

    # ---- charge split: flat brokerage (does NOT scale with lots) vs turnover (per lot).
    # flat_charge is the WHOLE run's brokerage (lot-independent). Only the turnover part
    # (STT/exch/GST) scales per lot, so divide just that by lots_in_run. ----
    flat = n * legs_per_trade * _BROKERAGE_PER_ORDER
    if flat > fee:                           # run modelled little/no brokerage -> don't
        flat = fee                           # over-subtract; treat all charge as flat
    per_lot_charge = max(0.0, fee - flat) / lots_in_run

    # ---- capital per ONE lot, by ONE classifier keyed off the registry's declared
    # structure (single source of truth for every current + future strategy). ----
    nb, ns = len(buy_prem), len(sell_notional)
    run_net_short = ns >= nb
    basis = _classify(structure, legs, instrument, run_net_short)
    cap, kind = None, "n/a"
    if basis == "crypto":
        # Delta defined-risk iron-fly: real capital/margin ~= MAX LOSS (portfolio margin
        # can't lose more than that). max_loss_pts = wing - net_credit; INR = pts x cv x
        # usd_inr (same convention as delta_ironfly_trader). gross is ALREADY INR.
        wing = float((combo.get("dna") or {}).get("wing") or _DELTA_WING_DEFAULT)
        ci = str(instrument or "").lower()
        cv = next((v for k, v in _CRYPTO_CV.items() if k in ci), 0.001)
        mls = [max(wing - float(t.get("entry_prem") or 0), 1.0)
               for t in trades if float(t.get("entry_prem") or 0) > 0]
        if mls:
            cap = (sum(mls) / len(mls)) * cv * _USD_INR / lots_in_run
            kind = "CRYPTO~"
        else:
            cap, kind = None, "CRYPTO"
    elif basis == "hedged" and sell_notional:
        cap = (sum(sell_notional) / ns) * _SPAN_PCT * _HEDGE_MARGIN_FACTOR / lots_in_run
        kind = "HEDGED~"
    elif basis == "naked_sell" and sell_notional:
        cap = (sum(sell_notional) / ns) * _SPAN_PCT / lots_in_run    # full naked SPAN
        kind = "SELL~"
    elif buy_prem:                                                   # buy / debit / equity
        cap = (sum(buy_prem) / nb) / lots_in_run                     # premium/value paid
        kind = "BUY"
    elif sell_notional:                                             # fallback if only shorts
        cap = (sum(sell_notional) / ns) * _SPAN_PCT / lots_in_run
        kind = "SELL~"

    return {
        "gross_per_lot": round(gross / lots_in_run, 2),   # normalised to ONE lot
        "flat_charge": round(flat, 2),                    # whole-run flat (lot-independent)
        "per_lot_charge": round(per_lot_charge, 2),       # turnover charge for ONE lot
        "net_per_lot": round(gross / lots_in_run - (flat + per_lot_charge), 2),  # 1-lot net
        "capital_per_lot": (round(cap) if cap else None),
        "capital_kind": kind,       # BUY=premium, SELL~=naked SPAN, HEDGED~=defined-risk, CRYPTO=n/a
        "capital_basis": basis,     # classifier decision (crypto/hedged/naked_sell/buy)
        "lot_size": lot_size,
        "lots_in_run": lots_in_run,     # lots the backtest was sized at (for transparency)
        "trades": n,
    }


def economics(slug):
    """Per-run economics (lot-independent), cached on results.js + registry mtime. The run
    is classified by its registry entry (structure/legs/instrument) when one exists."""
    try:
        mt = os.path.getmtime(_bc._results_path(slug))
    except Exception:
        return None
    reg = _registry_index().get(slug) or {}
    key = (mt, _REG_CACHE["mt"])          # invalidate if the registry changed too
    hit = _ECON_CACHE.get(slug)
    if hit and hit[0] == key:
        return hit[1]
    try:
        e = _one_run(slug, reg)
    except Exception:
        e = None
    if len(_ECON_CACHE) > 300:
        _ECON_CACHE.pop(next(iter(_ECON_CACHE)))
    _ECON_CACHE[slug] = (key, e)
    return e


def all_economics(slugs=None):
    """{slug: econ}. Default = every registry strategy with a backtest run (so each is
    classified by its declared structure) UNION any other run in runs/index.json."""
    if slugs is None:
        slugs = list(_registry_index().keys())
        try:
            slugs += [r.get("slug") for r in _bc.list_runs() if isinstance(r, dict)]
        except Exception:
            pass
    out = {}
    for s in dict.fromkeys(slugs):        # de-dup, preserve order
        if not s:
            continue
        e = economics(s)
        if e:
            out[s] = e
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(all_economics(), indent=1, default=str))
