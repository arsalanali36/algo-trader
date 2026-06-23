#!/usr/bin/env python3
r"""
download_equity_history.py — Equity 1-min history backfiller (volume included)

Kyun: purana on-demand downloader ek-ek din maangta tha (52 sym x ~540 din =
~20k requests) -> Dhan rate-limit (DH-904) storm -> fail hone pe EMPTY header-only
CSV likh deta tha -> woh file phir re-download block kar deti thi. Result: 2025 ka
saara data khaali, jabki Dhan ke paas hai (single-day probe = 375 bars/din).

Yeh script:
  * Dhan /v2/charts/intraday ko 89-DIN ke CHUNKS me maangta hai (Dhan limit: max
    90 din/request) -> ~7 requests/symbol, total ~360 -> rate-limit safe.
  * Response ko per-day CSVs me split karta hai, VOLUME ke saath.
  * Timezone-SAFE: epoch ko UTC maan kar +5:30 IST shift (kisi bhi machine pe —
    local IST ya VPS UTC — identical 09:15..15:29 bars). Ref: dhan_broker.py.
  * Empty-file POISONING fix: rate-limit/error pe file likhta hi NAHI (missing
    chhodta hai -> agli baar retry). Sirf genuine holiday (chunk me jis weekday
    ka data nahi aaya) ko empty marker deta hai.
  * Idempotent: jo din pehle se POPULATED hai use skip; sirf missing/empty bharta.

Usage:
  python download_equity_history.py                       # 2025-01-01 -> today, all symbols
  python download_equity_history.py --from 2025-01-01 --to 2026-06-23
  python download_equity_history.py --symbols RELIANCE,TCS
  python download_equity_history.py --dry-run             # plan only, no API calls
"""

import os
import sys
import json
import time
import glob
import argparse
import datetime as dt
from datetime import timezone, timedelta

import requests
import pandas as pd

import dhan_master

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- equity data root: same logic as _TOOLS/backtest_engine.py ----
_WIN_EQUITY_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Equity"
if os.path.isdir(_WIN_EQUITY_DIR):
    EQUITY_DATA_ROOT = _WIN_EQUITY_DIR
else:
    EQUITY_DATA_ROOT = os.path.join(HERE, "_TRADING_DATA", "Equity")
os.makedirs(EQUITY_DATA_ROOT, exist_ok=True)

CONFIG_FILE = os.path.join(HERE, "data", "config.json")
CHUNK_DAYS  = 89          # Dhan max 90/request — 89 safe
REQ_GAP     = 0.8         # base spacing between requests (s)
POP_BYTES   = 100         # file > this = populated (real data)
IST_SHIFT   = timedelta(hours=5, minutes=30)


def creds():
    c = json.load(open(CONFIG_FILE))
    return (c.get("jwt_token") or c.get("access_token") or ""), str(c.get("client_id") or "")


def symbol_list(arg):
    if arg:
        return [s.strip().upper() for s in arg.split(",") if s.strip()]
    # existing folders = ground truth of what we track
    syms = sorted(d for d in os.listdir(EQUITY_DATA_ROOT)
                  if os.path.isdir(os.path.join(EQUITY_DATA_ROOT, d)))
    if syms:
        return syms
    try:
        from universe import NIFTY50
        return list(NIFTY50)
    except Exception:
        return []


def trading_weekdays(d0, d1):
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def is_populated(path):
    return os.path.exists(path) and os.path.getsize(path) > POP_BYTES


def fetch_chunk(sym, sec, seg, inst, frm, to, token, cid):
    """Return dict{date_iso: DataFrame} for [frm,to]; '' on rate-limit/error."""
    for attempt in range(4):
        try:
            r = requests.post(
                "https://api.dhan.co/v2/charts/intraday",
                headers={"access-token": token, "client-id": cid,
                         "Content-Type": "application/json"},
                json={"securityId": sec, "exchangeSegment": seg, "instrument": inst,
                      "expiryCode": 0, "fromDate": frm.isoformat(), "toDate": to.isoformat()},
                timeout=30,
            )
            d = r.json()
        except Exception as e:
            print(f"      ! net error {e} (retry)")
            time.sleep((attempt + 1) * 4)
            continue
        if isinstance(d, dict) and d.get("errorCode"):
            ec = d.get("errorCode")
            if ec in ("DH-904",):           # rate limit -> backoff + retry
                time.sleep((attempt + 1) * 5)
                continue
            print(f"      ! Dhan {ec}: {str(d.get('errorMessage'))[:70]}")
            return ""                        # other error -> skip (don't poison)
        ts = d.get("timestamp") or []
        if not ts:
            return {}                        # genuine empty (holidays only in range)
        op, hi, lo, cl = d["open"], d["high"], d["low"], d["close"]
        vol = d.get("volume") or [0] * len(ts)
        by_date = {}
        for t, o, h, l, c, v in zip(ts, op, hi, lo, cl, vol):
            ist = dt.datetime.fromtimestamp(t, tz=timezone.utc) + IST_SHIFT
            key = ist.strftime("%Y-%m-%d")
            by_date.setdefault(key, []).append(
                {"Datetime": ist.strftime("%Y-%m-%d %H:%M:%S"),
                 "Open": o, "High": h, "Low": l, "Close": c, "Volume": v})
        return {k: pd.DataFrame(v) for k, v in by_date.items()}
    return ""                                # exhausted retries -> skip


def backfill(symbols, d_from, d_to, dry):
    token, cid = creds()
    if not token:
        print("ERROR: no Dhan token in data/config.json"); return
    grand_new = grand_hol = grand_skip = grand_fail = 0

    for si, sym in enumerate(symbols, 1):
        info = dhan_master.get_equity_info(sym)
        if not info:
            print(f"[{si}/{len(symbols)}] {sym}: no sec_id (skip)"); continue
        sec, seg, inst = info
        eq_dir = os.path.join(EQUITY_DATA_ROOT, sym)
        os.makedirs(eq_dir, exist_ok=True)
        new = hol = skip = fail = 0

        c0 = d_from
        while c0 <= d_to:
            c1 = min(c0 + timedelta(days=CHUNK_DAYS - 1), d_to)
            wkdays = trading_weekdays(c0, c1)
            # chunk skip: agar har weekday already populated hai -> request hi mat karo
            need = [d for d in wkdays
                    if not is_populated(os.path.join(eq_dir, f"{sym}_{d.isoformat()}.csv"))]
            if not need:
                skip += len(wkdays); c0 = c1 + timedelta(days=1); continue
            if dry:
                print(f"[{si}/{len(symbols)}] {sym} {c0}..{c1}: would fetch ({len(need)} missing days)")
                c0 = c1 + timedelta(days=1); continue

            res = fetch_chunk(sym, sec, seg, inst, c0, c1, token, cid)
            if res == "":                    # rate-limit/error -> leave missing, no poison
                fail += len(need)
                print(f"[{si}/{len(symbols)}] {sym} {c0}..{c1}: FAILED (will retry next run)")
                c0 = c1 + timedelta(days=1); time.sleep(REQ_GAP); continue

            for d in wkdays:
                key = d.isoformat()
                fpath = os.path.join(eq_dir, f"{sym}_{key}.csv")
                if key in res and not res[key].empty:
                    res[key].to_csv(fpath, index=False)     # overwrites empty too
                    new += 1
                elif not os.path.exists(fpath):
                    # weekday in a successful chunk with no bars = holiday -> mark
                    pd.DataFrame(columns=["Datetime","Open","High","Low","Close","Volume"]).to_csv(fpath, index=False)
                    hol += 1
            c0 = c1 + timedelta(days=1)
            time.sleep(REQ_GAP)

        print(f"[{si}/{len(symbols)}] {sym}: +{new} new, {hol} holiday, {skip} already, {fail} failed")
        grand_new += new; grand_hol += hol; grand_skip += skip; grand_fail += fail

    print(f"\n=== DONE: +{grand_new} day-files written, {grand_hol} holidays marked, "
          f"{grand_skip} already-had, {grand_fail} failed (re-run to retry failed) ===")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2025-01-01")
    ap.add_argument("--to",   dest="d_to",   default=dt.date.today().isoformat())
    ap.add_argument("--symbols", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    d_from = dt.date.fromisoformat(a.d_from)
    d_to   = dt.date.fromisoformat(a.d_to)
    syms   = symbol_list(a.symbols)
    print(f"Equity backfill: {len(syms)} symbols, {d_from} -> {d_to}, "
          f"{CHUNK_DAYS}-day chunks{' [DRY-RUN]' if a.dry_run else ''}")
    print(f"Data root: {EQUITY_DATA_ROOT}\n")
    backfill(syms, d_from, d_to, a.dry_run)


if __name__ == "__main__":
    main()
