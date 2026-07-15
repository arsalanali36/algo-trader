#!/usr/bin/env python3
"""_ops/backfill_trade_ohlc.py — fill missing per-trade premium OHLC from Dhan's
paid Expired-Options add-on (rollingoption), so the Stats-page "Opt Fixed/Aggr/
Aggr->EOD" what-if columns stop showing '-' for old dates.

WHY: opt_pnl.py replays each completed trade against its 1-min premium bars on disk
(data/trade_ohlc/). Those bars were only captured going-forward (~July) and Dhan's
LIVE intraday endpoint drops expired weekly-index contracts (TRAP #100) — so old
trades had no bars -> covered=False -> '-'. Dhan's EXPIRED-options add-on DOES serve
them (index AND stock, verified), just at 5-min granularity.

WHAT: for every completed trade in the range with no disk bars, reconstruct the exact
held-strike premium series for its trade-date via _data/opt_hist.held-strike logic and
write data/trade_ohlc/{sec_id}_{date}.json (epoch-keyed [o,h,l,c] — the format
opt_pnl/_load_disk already reads). Idempotent (skips existing files), rate-limited
(dhan_rate_limiter 'account' = lowest priority, yields to live orders/LTP).

Display-only: touches NO live order/exit path. 5-min bars => the what-if is slightly
coarser than a 1-min replay (a wide 5-min candle's high/low spans more) — approximate,
never used for a real order.

Run ON THE VPS (needs trades.db + Dhan token + expired-options subscription):
    venv/bin/python _ops/backfill_trade_ohlc.py 2026-06-01 2026-06-30
    venv/bin/python _ops/backfill_trade_ohlc.py 2026-06-01 2026-06-30 --dry
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa: F401  sys.path bootstrap for _core/_data modules

import opt_hist
import order_store
import universe

try:
    import dhan_rate_limiter as _rl
except Exception:
    _rl = None

TRADE_OHLC = os.path.join(ROOT, "data", "trade_ohlc")
CONFIG = os.path.join(ROOT, "data", "config.json")
OFF_RANGE = 8            # scan ATM +-8 strikes to locate the held strike
_map_cache = {}          # (u_secid,instr,flag,side,date) -> {round(strike): {epoch:[o,h,l,c]}}


def _headers():
    c = json.load(open(CONFIG))
    return {"access-token": c["jwt_token"], "client-id": c["client_id"],
            "Content-Type": "application/json"}


def parse_trad_sym(sym):
    """'NIFTY-24Jun2026-24050-PE' / 'BAJAJ-AUTO-26Jun2026-9000-CE' ->
       (root, strike, 'CE'|'PE'). Root re-joins hyphenated names (BAJAJ-AUTO)."""
    parts = (sym or "").split("-")
    if len(parts) < 4:
        return None
    cp = parts[-1].upper()
    if cp not in ("CE", "PE"):
        return None
    try:
        strike = float(parts[-2])
    except Exception:
        return None
    root = "-".join(parts[:-3]).upper()
    if not root:
        return None
    return root, strike, cp


def _underlying(root):
    """root -> (underlying_secid, instrument, [flags to try]) or None."""
    if root in opt_hist.INDEX_UNDERLYINGS:
        sid, instr = opt_hist.INDEX_UNDERLYINGS[root]
        return sid, instr, ["WEEK", "MONTH"]     # index weekly first, then monthly
    sid = universe.equity_secid(root)             # stock underlying = NSE_EQ id
    if not sid:
        return None
    return int(sid), "OPTSTK", ["MONTH"]          # stocks: monthly only


def _strike_map(headers, u_secid, instr, flag, side, dtype, date_str, rl):
    """All offsets for (underlying,flag,side,date) fetched once -> strike->bars.
    Cached so multiple trades on the same underlying/day reuse the fetches."""
    ck = (u_secid, instr, flag, side, date_str)
    if ck in _map_cache:
        return _map_cache[ck]
    m = {}
    offs = [0] + [s * k for k in range(1, OFF_RANGE + 1) for s in (1, -1)]
    for off in offs:
        rows, _status = opt_hist.fetch_rolling(headers, u_secid, instr, flag, off,
                                               dtype, side, date_str, date_str, rl=rl)
        for row in rows:
            ts, o, h, l, c, strike = row[0], row[1], row[2], row[3], row[4], row[8]
            if strike is None:
                continue
            m.setdefault(round(strike), {})[str(int(ts))] = [
                round(o, 2), round(h, 2), round(l, 2), round(c, 2)]
    _map_cache[ck] = m
    return m


def main():
    a = sys.argv[1:]
    dry = "--dry" in a
    a = [x for x in a if not x.startswith("--")]
    if len(a) < 2:
        print("usage: backfill_trade_ohlc.py <from YYYY-MM-DD> <to YYYY-MM-DD> [--dry]")
        return
    d_from, d_to = a[0], a[1]
    trades = order_store.trades_for_range(d_from, d_to).get("details", [])
    headers = _headers()

    todo = []
    for t in trades:
        if t.get("pnl") is None:
            continue
        sec_id = t.get("sec_id")
        date_str = t.get("entry_date") or ""
        sym = t.get("sym") or ""
        if not sec_id or not date_str:
            continue
        out_p = os.path.join(TRADE_OHLC, "%s_%s.json" % (sec_id, date_str))
        if os.path.exists(out_p):
            continue                       # already have bars
        p = parse_trad_sym(sym)
        if not p:
            continue
        root, strike, cp = p
        u = _underlying(root)
        if not u:
            continue
        u_secid, instr, flags = u
        side = "ce" if cp == "CE" else "pe"
        dtype = "CALL" if cp == "CE" else "PUT"
        todo.append((out_p, sec_id, date_str, sym, u_secid, instr, flags,
                     side, dtype, round(strike)))

    print("backfill: %d completed trades in range, %d missing bars to fetch%s"
          % (len(trades), len(todo), "  [DRY RUN]" if dry else ""), flush=True)
    if dry:
        for (out_p, sec_id, date_str, sym, u_secid, instr, flags, side, dtype, strike) in todo[:60]:
            print("  would fetch %-32s strike=%s %s flags=%s (u=%s %s)"
                  % (sym, strike, side.upper(), flags, u_secid, instr))
        if len(todo) > 60:
            print("  ... +%d more" % (len(todo) - 60))
        return

    rl = _rl
    filled = nodata = 0
    for (out_p, sec_id, date_str, sym, u_secid, instr, flags, side, dtype, strike) in todo:
        bars = {}
        used_flag = None
        for flag in flags:
            m = _strike_map(headers, u_secid, instr, flag, side, dtype, date_str, rl)
            if strike in m and m[strike]:
                bars = m[strike]
                used_flag = flag
                break
        if not bars:
            nodata += 1
            if nodata <= 25:
                print("  no-data %-32s strike=%s %s %s" % (sym, strike, side.upper(), date_str), flush=True)
            continue
        try:
            os.makedirs(TRADE_OHLC, exist_ok=True)
            tmp = out_p + ".tmp"
            json.dump(bars, open(tmp, "w"))
            os.replace(tmp, out_p)
            filled += 1
            if filled <= 40 or filled % 25 == 0:
                print("  +%-32s strike=%s %s %s (%s, %d bars)"
                      % (sym, strike, side.upper(), date_str, used_flag, len(bars)), flush=True)
        except Exception as e:
            print("  WRITE-FAIL %s: %s" % (out_p, e), flush=True)

    print("\nDONE  filled=%d  no-data=%d  (cache groups=%d)"
          % (filled, nodata, len(_map_cache)), flush=True)


if __name__ == "__main__":
    main()
