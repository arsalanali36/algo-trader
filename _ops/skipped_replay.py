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


def _squareoff_hm():
    """Intraday exit proxy = the configured squareoff time (single source, so it
    tracks the CAS 15:10 change instead of a hardcode)."""
    try:
        cfg = json.load(open(os.path.join(_ROOT, "nifty_config.json"), encoding="utf-8"))
        v = cfg.get("_risk", {}).get("global", {}).get("auto_squareoff_at")
        if v and ":" in str(v):
            return str(v)[:5]
    except Exception:
        pass
    return "15:10"


def _lake_replay(rec):
    """Phase-2 fallback: jab disk bars nahi (blocked contract kabhi trade nahi hua),
    to collector lake (opt_whatif) se REAL premium price karo — entry @ signal-minute,
    exit @ squareoff proxy. Sirf NIFTY/BANKNIFTY index options (unhi ka collector lake).
    opt_whatif ki apni per-minute MTM series se eod/best(MFE)/worst(MAE) nikaalte hain."""
    trad = (rec.get("trad_sym") or "")
    parts = trad.split("-")
    if len(parts) < 4:
        return None
    u = parts[0].upper()
    if u not in ("NIFTY", "BANKNIFTY"):      # lake index-only; stocks -> honest no_data
        return None
    ot = parts[-1].upper()
    if ot not in ("CE", "PE"):
        return None
    try:
        strike = float(parts[-2])
    except Exception:
        return None
    et = (rec.get("ts") or "")[11:16]
    if not et:
        return None
    lots = int(rec.get("intended_lots") or 1)
    side = (rec.get("side") or "BUY").upper()
    xt = _squareoff_hm()
    try:
        import opt_whatif as w
        r = w.run(u, rec.get("date"), et, xt, lots,
                  [{"side": side, "strike": strike, "type": ot}])
    except Exception:
        return None
    if not r or not r.get("ok") or not r.get("legs"):
        return None
    L = r["legs"][0]
    vals = [p["mtm"] for p in (r.get("mtm") or []) if "mtm" in p]
    eod = r.get("total")
    return {
        "src": "lake", "exit_time": xt,
        "entry_premium": L.get("entry"), "exit_eod_premium": L.get("exit"),
        "pnl_eod": eod,
        "pnl_best": (max(vals) if vals else eod),
        "pnl_worst": (min(vals) if vals else eod),
    }


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
        lake = _lake_replay(rec)      # Phase-2: collector-lake pricing (index options)
        if lake:
            d.update({"covered": True, "src": "lake",
                      "entry_premium": lake["entry_premium"],
                      "exit_eod_premium": lake["exit_eod_premium"],
                      "pnl_eod": lake["pnl_eod"], "pnl_best": lake["pnl_best"],
                      "pnl_worst": lake["pnl_worst"], "exit_time": lake["exit_time"]})
            return d
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
        "covered": True, "src": "disk",
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

    disk = [r for r in covered if r.get("src") == "disk"]
    lake = [r for r in covered if r.get("src") == "lake"]
    print("Skipped-signal replay  %s -> %s%s" %
          (date_from, date_to, ("  strategy=%s" % strategy) if strategy else ""))
    print("  total blocked signals : %d" % len(results))
    print("  covered               : %d  (disk bars %d + collector lake %d)"
          % (len(covered), len(disk), len(lake)))
    print("  no premium data       : %d" % len(nodata))
    if covered:
        tot_eod = sum(r["pnl_eod"] for r in covered)
        tot_best = sum(r["pnl_best"] for r in covered)
        tot_worst = sum(r["pnl_worst"] for r in covered)
        print("  --- covered what-if (side-aware, GROSS, per recorded qty; exit = squareoff/EOD proxy) ---")
        print("  hold-to-exit total : Rs %s" % f"{tot_eod:,.0f}")
        print("  best-case total    : Rs %s" % f"{tot_best:,.0f}")
        print("  worst-case total   : Rs %s" % f"{tot_worst:,.0f}")
        print("  %-11s %-26s %-4s %-5s %9s %9s %9s" %
              ("date", "symbol", "side", "src", "exit", "best", "worst"))
        for r in covered:
            print("  %-11s %-26s %-4s %-5s %9s %9s %9s" % (
                r["date"], (r["trad_sym"] or r["symbol"])[:26], r["side"], r.get("src", ""),
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
    ap.add_argument("--date", default=None, help="single day (sets --from = --to)")
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--strategy", default=None)
    a = ap.parse_args()
    df = a.date_from or a.date
    dt = a.date_to or a.date
    if not df or not dt:
        ap.error("give --date YYYY-MM-DD  (or --from/--to)")
    run(df, dt, a.strategy)
