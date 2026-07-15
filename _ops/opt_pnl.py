#!/usr/bin/env python3
"""opt_pnl.py — per-trade "what-if" P&L under the two OPTIMISED SL/Target
profiles that the grid-search picked (best-fixed + best-aggressive).

Display-only analysis feeding the Stats page's "Opt Fixed" / "Opt Aggr" columns
so the user can see, at a glance, what each real trade WOULD have made under the
optimised exit rules — i.e. "kya future ke liye possible hai".

    • Best FIXED  (legacy)      = Target ₹4000/lot,  SL ₹1000/lot
    • Best AGGRESSIVE (trail)   = Target ₹6000/lot,  init SL ₹2500/lot, step ₹100
      (both from scripts/path_aware_sl_sim.py's --optimize run, CLAUDE.md 2026-07-14)

This module does NOT touch any live order/exit path — it only replays already-
completed trades against their historical 1-min premium bars (Rule 10: zero live
behaviour change). The replay engine itself is REUSED verbatim from
scripts/path_aware_sl_sim.py (Rule 6B — no re-implementation).

Numbers returned are GROSS ₹ (premium-based, same as the sim). The dashboard nets
them with its own charge model so the column is comparable to the Net column.

Coverage is honest: a trade whose contract Dhan no longer serves (expired weekly
index, TRAP #100) has no bars on disk → it is reported covered=False and falls
back to its ACTUAL P&L, so the totals are never quietly padded with un-simulated
rows. Default is disk-bars-only (allow_fetch=False) so a dashboard load never
triggers a Dhan-fetch storm; a manual ?fetch=1 backfills from Dhan (rate-limited).
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa: F401  sys.path bootstrap for _core/_data modules
# path_aware_sl_sim lives in scripts/ (not a normal import dir) — add it.
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import path_aware_sl_sim as pas  # reuse load_bars/replay_legacy/replay_aggr/_agg_cfg

try:
    import dhan_master
except Exception:
    dhan_master = None

CACHE = os.path.join(ROOT, "data", "opt_pnl_cache.json")

# Grid-search winners (per-lot ₹). Keep in sync with path_aware_sl_sim.py.
FIX = {"target": 4000, "sl": 1000}          # best legacy (fixed target / fixed SL)
AGG_PARAMS = {"target": 6000, "sl": 2500, "step": 100}
AGG = pas._agg_cfg(AGG_PARAMS["target"], AGG_PARAMS["sl"], AGG_PARAMS["step"])
# Aggr→EOD = "replace the strategy's ATR SL with this trail and RIDE to 15:15"
# (ride mode: trail is the only exit, no target cap; init SL ₹1,500/lot = the
# data-best from the hold-EOD comparison). Window = entry..EOD, fallback = EOD
# close. This answers "ATR SL vs aggressive-trail-to-EOD" — directional, NOT a
# validated backtest (a real strategy re-backtest is needed before going live).
AGG_EOD_PARAMS = {"target": 6000, "sl": 1500, "step": 100}
AGG_EOD = pas._agg_cfg(AGG_EOD_PARAMS["target"], AGG_EOD_PARAMS["sl"], AGG_EOD_PARAMS["step"])


def _load_cache():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def _save_cache(c):
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        json.dump(c, open(tmp, "w"))
        os.replace(tmp, CACHE)
    except Exception as e:
        print("[opt_pnl] cache save fail:", e, flush=True)


def _lots(sec_id, qty):
    lots = 1
    if dhan_master and sec_id:
        try:
            ls = dhan_master.get_lot_size_by_sec_id(sec_id)
            if ls and qty:
                lots = max(1, round(qty / float(ls)))
        except Exception:
            pass
    return lots


def compute_for_trades(trades, allow_fetch=False):
    """`trades` = order_store `details` list (completed trades only used).
    Returns {trade_id: {"fix": ₹, "aggr": ₹, "covered": bool}} — GROSS ₹.

    Cached by trade id + a signature (entry_price/exit_time/qty) so an edited or
    re-netted trade recomputes; a covered result is never re-fetched.
    """
    cache = _load_cache()
    out = {}
    dirty = False
    for t in trades:
        tid = t.get("id")
        if tid is None or t.get("pnl") is None:
            continue
        tid = str(tid)
        sig = "{}|{}|{}".format(t.get("entry_price"), t.get("exit_time"), t.get("qty"))
        c = cache.get(tid)
        # reuse cache unless the trade changed, the schema grew (aggr_eod added),
        # or a not-covered row could now improve with a fresh fetch
        if c and c.get("sig") == sig and "aggr_eod" in c and (c.get("covered") or not allow_fetch):
            out[tid] = {"fix": c["fix"], "aggr": c["aggr"], "aggr_eod": c["aggr_eod"],
                        "covered": c["covered"]}
            continue

        sec_id = t.get("sec_id")
        sym = t.get("sym") or ""
        date_str = t.get("entry_date") or ""
        et, xt = t.get("entry_time", ""), t.get("exit_time", "")
        side = t.get("entry")
        ep = t.get("entry_price")
        qty = t.get("qty")
        actual = float(t["pnl"])

        bars = pas.load_bars(sec_id, sym, date_str, allow_fetch=allow_fetch)
        win = pas._window(bars, et, xt, False)          # actual hold (same-hold)
        win_eod = pas._window(bars, et, xt, True)        # entry..15:15 (ride)

        if win and ep and qty and side:
            lots = _lots(sec_id, qty)
            _, fix_rs = pas.replay_legacy(win, side, ep, qty,
                                          FIX["target"] * lots, FIX["sl"] * lots, actual)
            _, agg_rs = pas.replay_aggr(win, side, ep, qty, AGG, lots, actual)
            # ride→EOD: no target cap, trail-only exit, fallback = EOD close
            if win_eod:
                fb_eod = pas._mtm(side, ep, win_eod[-1][4], qty)
                _, aggeod_rs = pas.replay_aggr(win_eod, side, ep, qty, AGG_EOD,
                                               lots, fb_eod, ride=True)
            else:
                aggeod_rs = agg_rs
            covered = True
        else:
            fix_rs = agg_rs = aggeod_rs = actual
            covered = False

        rec = {"fix": round(fix_rs, 2), "aggr": round(agg_rs, 2),
               "aggr_eod": round(aggeod_rs, 2), "covered": covered, "sig": sig}
        cache[tid] = rec
        dirty = True
        out[tid] = {"fix": rec["fix"], "aggr": rec["aggr"],
                    "aggr_eod": rec["aggr_eod"], "covered": covered}

    if dirty:
        _save_cache(cache)
    return out


if __name__ == "__main__":
    # quick manual check: python _ops/opt_pnl.py 2026-06-21 2026-07-14 [--fetch]
    import order_store
    a = sys.argv
    d_from = a[1] if len(a) > 1 else "2026-06-21"
    d_to = a[2] if len(a) > 2 else "2026-07-14"
    fetch = "--fetch" in a
    det = order_store.trades_for_range(d_from, d_to)["details"]
    m = compute_for_trades(det, allow_fetch=fetch)
    cov = sum(1 for v in m.values() if v["covered"])
    tf = sum(v["fix"] for v in m.values())
    ta = sum(v["aggr"] for v in m.values())
    te = sum(v["aggr_eod"] for v in m.values())
    print("trades {}  covered {}  Σfix ₹{:,.0f}  Σaggr ₹{:,.0f}  Σaggr→EOD ₹{:,.0f}".format(
        len(m), cov, tf, ta, te))
