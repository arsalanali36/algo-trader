#!/usr/bin/env python3
r"""
dist_ma_daily_update.py — keep the daily-equity lake fresh for dist_ma_trader.

WHY: dist_ma_trader.py (positional Distance-from-20EMA equity strategy) acts on
COMPLETED DAILY bars read from the equity-daily lake (dist_ma.EQ_DIR). On a fresh
machine (VPS) that lake is empty, and it never advances on its own — this script
fetches recent daily OHLC from Dhan for the F&O universe and merges it in, so the
trader's `latest_date()` moves forward each trading day.

The LIVE trader only needs ~40+ recent bars per symbol (EMA20 + ATR14 warmup) to
compute today's signal — NOT the full backtest history. So this fetches a short
window (default 150 sessions) and MERGES into whatever exists (never truncates a
richer local history).

Reuses the exact Dhan daily call from range_trader.fetch_daily (Rule 6B) + the
scrip-master sec_id + the cross-process rate-limiter.

Usage:
  python _ops/dist_ma_daily_update.py                 # all F&O symbols, merge
  python _ops/dist_ma_daily_update.py --symbols RELIANCE,TCS
  python _ops/dist_ma_daily_update.py --days 200 --dry-run
VPS: run daily post-close via a systemd timer (see runbook at bottom).
"""
import argparse
import os
import sys
from datetime import date, timedelta

import pandas as pd
import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "scratch", "dist_ma"))
import _paths  # noqa: F401
import dhan_master
import dist_ma as dm                      # for EQ_DIR + symbols() (single source of the lake path)

try:
    import dhan_rate_limiter as _rl
except Exception:
    _rl = None

HIST_URL = "https://api.dhan.co/v2/charts/historical"
CONFIG_FILE = os.path.join(HERE, "data", "config.json")


def creds():
    import json
    c = json.load(open(CONFIG_FILE))
    return (c.get("jwt_token") or c.get("access_token") or ""), str(c.get("client_id") or "")


def _hdrs(token, cid):
    return {"access-token": token, "client-id": cid, "Content-Type": "application/json"}


def fetch_daily(symbol, token, cid, days=150):
    """Recent daily OHLCV from Dhan /v2/charts/historical (same call as
    range_trader.fetch_daily; volume added best-effort)."""
    info = dhan_master.get_equity_info(symbol)
    if not info:
        return None
    sec_id, seg, inst = info
    today = date.today()
    frm = (today - timedelta(days=max(days * 2, 90))).isoformat()
    if _rl:
        try:
            _rl.acquire("candle")
        except Exception:
            pass
    try:
        r = requests.post(HIST_URL, json={
            "securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": frm, "toDate": today.isoformat()},
            headers=_hdrs(token, cid), timeout=12)
        if r.status_code == 429 and _rl:
            _rl.note_429()
        if r.status_code != 200:
            print(f"  {symbol}: HTTP {r.status_code}")
            return None
        d = r.json()
        ts = d.get("start_Time") or d.get("timestamp") or []
        if not ts:
            return None
        vol = d.get("volume") or [0] * len(ts)
        dts = pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=5, minutes=30)
        df = pd.DataFrame({
            "Date": pd.to_datetime(dts).normalize(),
            "Open": d.get("open", []), "High": d.get("high", []),
            "Low": d.get("low", []), "Close": d.get("close", []),
            "Volume": vol,
        })
        return df.tail(days).reset_index(drop=True)
    except Exception as e:
        print(f"  {symbol}: {e}")
        return None


def merge_write(symbol, df_new, dry=False):
    """Merge df_new into the lake CSV (union on Date, prefer new; never truncate)."""
    os.makedirs(dm.EQ_DIR, exist_ok=True)
    path = os.path.join(dm.EQ_DIR, symbol + ".csv")
    if os.path.exists(path):
        try:
            old = pd.read_csv(path, parse_dates=["Date"])
        except Exception:
            old = pd.DataFrame(columns=df_new.columns)
        combined = pd.concat([old, df_new], ignore_index=True)
        combined = combined.drop_duplicates(subset="Date", keep="last")
    else:
        combined = df_new.copy()
    combined = combined.sort_values("Date").reset_index(drop=True)
    added = len(combined) - (len(old) if os.path.exists(path) else 0)
    if not dry:
        out = combined.copy()
        out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
        out.to_csv(path, index=False)
    return len(combined), max(added, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    else:
        # default = existing lake ∪ full F&O universe, so the daily timer always
        # covers the whole universe (and auto-adds any new F&O name), never just
        # whatever happens to already be on disk.
        syms = set(dm.symbols())
        try:
            import fno_universe
            syms |= set(fno_universe.get_fno_symbols())
        except Exception:
            pass
        syms = sorted(syms)
    if not syms:
        print("no symbols to update"); return

    token, cid = creds()
    if not token:
        print("no Dhan token in data/config.json — cannot fetch"); return

    print(f"updating {len(syms)} symbols  (days={a.days}{' DRY' if a.dry_run else ''})  lake={dm.EQ_DIR}")
    ok = fail = 0
    for i, s in enumerate(syms, 1):
        df = fetch_daily(s, token, cid, a.days)
        if df is None or df.empty:
            fail += 1; continue
        total, added = merge_write(s, df, a.dry_run)
        ok += 1
        if i <= 5 or i % 50 == 0:
            print(f"  [{i}/{len(syms)}] {s}: {total} bars (+{added})")
    try:
        import dist_ma_engine
        print(f"done — {ok} updated, {fail} failed. latest_date now: {dist_ma_engine.latest_date()}")
    except Exception:
        print(f"done — {ok} updated, {fail} failed.")


if __name__ == "__main__":
    main()
