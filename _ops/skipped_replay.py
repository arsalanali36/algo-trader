"""Offline replay of RMS-blocked entry-signals -> hypothetical P&L.

`_core/skipped_store.py` har blocked entry ka **entry-intent + us-second ka
premium** record karta hai. Ye tool un records ko leke, us contract ka REAL
premium-path disk se (`data/trade_ohlc/<sec_id>_<date>.json`) padhkar, "agar wo
trade block na hota to kya hota" ka hypothetical P&L nikaalta hai. Yehi ekmatra
data-backed tareeka hai "block na kiye hote to..." wale sawaal ka jawab dene ka.

v1 (ye file):
- Premium source = ON-DISK bars only (`data/trade_ohlc/`, jo auto_data_downloader
  capture karta hai) -> NETWORK/credentials ki zaroorat nahi, VPS pe chalao.
- Har covered signal pe: entry premium, MFE (max favourable), MAE (max adverse),
  aur EOD(15:15) premium -> per-lots ₹ what-if (LONG option = BUY).
- Coverage HONESTLY report hoti hai: jo blocked contracts kabhi order nahi hue
  unke bars disk pe shayad na hon -> "no_data" count dikhta hai (chhupaya nahi).

Phase-2 (baad me, zaroorat pade to): jo contracts disk pe nahi -> `_data/opt_hist.
py` (Dhan expired-options rollinglake) se fetch + strategy ka EXACT exit-rule
(RSI midline / SL / 15:15) replay. Wo network+VPS-only + rate-limited hai, isliye
alag flag ke peeche rakha jaayega.

Usage:
  python _ops/skipped_replay.py --from 2026-07-01 --to 2026-07-31
  python _ops/skipped_replay.py --from 2026-07-17 --to 2026-07-17 --strategy rsi_v1_PAPER
"""
import os
import sys
import json
import argparse
import datetime

# --- path bootstrap (subfolder me hai -> pehle root ko sys.path pe daalo) ---
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import _paths  # noqa: F401  (registers all folders on sys.path)
import skipped_store

_OHLC_DIR = os.path.join(_ROOT, "data", "trade_ohlc")
_EOD_HM = "15:15"   # intraday exit proxy (no-target-hit natural close)


def _min_str_from_key(k):
    """trade_ohlc key -> 'HH:MM' (IST). Key epoch-seconds ya 'HH:MM' ho sakta hai."""
    ks = str(k)
    if ":" in ks and len(ks) <= 5:
        return ks
    try:
        ep = int(float(ks))
        # bars IST-as-UTC epoch me store hote hain (auto_data_downloader convention)
        t = datetime.datetime.utcfromtimestamp(ep)
        return t.strftime("%H:%M")
    except Exception:
        return None


def _close_from_val(v):
    if isinstance(v, dict):
        for k in ("close", "c", "Close", "ltp"):
            if k in v and v[k] is not None:
                try:
                    return float(v[k])
                except Exception:
                    return None
        return None
    try:
        return float(v)
    except Exception:
        return None


def load_premium_path(sec_id, date, from_hhmm):
    """Return sorted [(hhmm, close), ...] for sec_id on date, at/after from_hhmm.
    Empty list if the file/bars don't exist."""
    if not sec_id:
        return []
    path = os.path.join(_OHLC_DIR, "%s_%s.json" % (sec_id, date))
    if not os.path.exists(path):
        return []
    try:
        raw = json.load(open(path))
    except Exception:
        return []
    items = raw.items() if isinstance(raw, dict) else []
    out = []
    for k, v in items:
        hhmm = _min_str_from_key(k)
        c = _close_from_val(v)
        if hhmm and c is not None:
            out.append((hhmm, c))
    out.sort()
    if from_hhmm:
        out = [(t, c) for (t, c) in out if t >= from_hhmm]
    return out


def replay_one(rec):
    """One skipped-signal -> what-if dict. entry_premium recorded se, exit disk-bars
    se. LONG option (BUY): pnl = (exit - entry) * qty."""
    sec_id = rec.get("sec_id")
    date = rec.get("date")
    et = (rec.get("ts") or "")[11:16]  # entry HH:MM from ts
    entry = rec.get("entry_premium")
    qty = rec.get("intended_qty")
    path = load_premium_path(sec_id, date, et)
    d = {"date": date, "strategy": rec.get("strategy"), "symbol": rec.get("symbol"),
         "trad_sym": rec.get("trad_sym"), "side": rec.get("side"),
         "reason": rec.get("block_reason"), "entry_time": et,
         "entry_premium": entry, "qty": qty, "covered": False}
    if not path or not entry or not qty:
        d["note"] = "no_data" if not path else "no_entry_premium_or_qty"
        return d
    closes = [c for _, c in path]
    eod = None
    for t, c in path:
        if t >= _EOD_HM:
            eod = c
            break
    if eod is None:
        eod = closes[-1]   # last available bar
    mfe = max(closes)
    mae = min(closes)
    d.update({
        "covered": True,
        "exit_eod_premium": round(eod, 2),
        "mfe_premium": round(mfe, 2), "mae_premium": round(mae, 2),
        # LONG option what-if (₹): natural EOD hold, plus best/worst-case bounds
        "pnl_eod": round((eod - entry) * qty, 0),
        "pnl_best": round((mfe - entry) * qty, 0),
        "pnl_worst": round((mae - entry) * qty, 0),
    })
    return d


def run(date_from, date_to, strategy=None):
    recs = skipped_store.query(date_from, date_to, strategy)
    results = [replay_one(r) for r in recs]
    covered = [r for r in results if r["covered"]]
    nodata = [r for r in results if not r["covered"]]

    print("Skipped-signal replay  %s -> %s%s" %
          (date_from, date_to, ("  strategy=%s" % strategy) if strategy else ""))
    print("  total blocked signals : %d" % len(results))
    print("  covered (disk bars)   : %d" % len(covered))
    print("  no premium data       : %d" % len(nodata))
    if covered:
        tot_eod = sum(r["pnl_eod"] for r in covered)
        tot_best = sum(r["pnl_best"] for r in covered)
        tot_worst = sum(r["pnl_worst"] for r in covered)
        print("  --- covered what-if (LONG option, GROSS, per recorded qty) ---")
        print("  EOD-hold  total : Rs %s" % f"{tot_eod:,.0f}")
        print("  best-case total : Rs %s" % f"{tot_best:,.0f}")
        print("  worst-case total: Rs %s" % f"{tot_worst:,.0f}")
        print("  %-12s %-26s %-6s %10s %10s %10s" %
              ("date", "symbol", "side", "eod", "best", "worst"))
        for r in covered:
            print("  %-12s %-26s %-6s %10s %10s %10s" % (
                r["date"], (r["trad_sym"] or r["symbol"])[:26], r["side"],
                f"{r['pnl_eod']:,.0f}", f"{r['pnl_best']:,.0f}", f"{r['pnl_worst']:,.0f}"))
    if nodata:
        print("  --- no disk bars (phase-2 = expired-lake fetch) ---")
        for r in nodata[:40]:
            print("  %-12s %-26s %-6s  %s" % (
                r["date"], (r["trad_sym"] or r["symbol"] or "?")[:26],
                r["side"], r.get("reason", "")[:40]))
    return {"covered": covered, "no_data": nodata}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", required=True)
    ap.add_argument("--to", dest="date_to", required=True)
    ap.add_argument("--strategy", default=None)
    a = ap.parse_args()
    run(a.date_from, a.date_to, a.strategy)
