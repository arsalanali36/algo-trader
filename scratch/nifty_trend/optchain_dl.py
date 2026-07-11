"""Dhan rollingoption downloader — REAL 5yr expired-option premium + IV + OI data-lake.

Endpoint: POST /v2/charts/rollingoption (paid "Expired Options Data" add-on).
Per call returns, for ONE relative strike (rolling ATM±n) + ONE side (CE/PE), a 5-min
series of open/high/low/close/volume/IV/OI/strike/spot. 30-day max per call.

We build the full NIFTY chain: offsets ATM, ATM±1..±10 (21) × CE+PE × {WEEK,MONTH}.
Priority order so USABLE data lands first: WEEK before MONTH, RECENT before old,
ATM outward (0,±1,±2…). Track-B (IV/OI) strategies can start on partial data.

Rate: lowest priority ("account") via dhan_rate_limiter so it NEVER starves live
orders; hard backoff on DH-904. Token re-read each call (survives a mid-run refresh).
Resumable: a manifest of done (series,chunk) keys is skipped on restart.

Store: optlake/NIFTY/<WEEK|MONTH>/<CE|PE>_<off>.csv  (append + dedup on timestamp).
Run on the VPS (token lives there):  venv/bin/python scratch/nifty_trend/optchain_dl.py
"""
import os
import sys
import csv
import json
import time
import datetime as dt
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake", "NIFTY")
MANIFEST = os.path.join(LAKE, "_done.json")
CONFIG = os.path.join(ROOT, "data", "config.json")
URL = "https://api.dhan.co/v2/charts/rollingoption"

START = dt.date(2021, 7, 1)      # ~5yr (Dhan expired-option depth)
CHUNK_DAYS = 28                  # < 30-day API cap
COLS = ["timestamp", "open", "high", "low", "close", "volume", "iv", "oi", "strike", "spot"]
OFFSETS = [0] + [s * n for n in range(1, 11) for s in (+1, -1)]   # 0,+1,-1,+2,-2,...,+10,-10
SIDES = [("CE", "CALL"), ("PE", "PUT")]
FLAGS = ["WEEK", "MONTH"]


def _tok():
    c = json.load(open(CONFIG))
    return {"access-token": c["jwt_token"], "client-id": c["client_id"],
            "Content-Type": "application/json"}


def _rl_acquire():
    try:
        sys.path.insert(0, ROOT)
        import dhan_rate_limiter as rl
        for _ in range(60):
            if rl.acquire("account"):     # lowest priority — yields to live orders/ltp
                return rl
            time.sleep(1.0)
        return rl
    except Exception:
        return None


def _strike_str(off):
    return "ATM" if off == 0 else (f"ATM+{off}" if off > 0 else f"ATM{off}")


def _chunks():
    today = dt.date.today()
    out = []
    d = START
    while d <= today:
        e = min(d + dt.timedelta(days=CHUNK_DAYS - 1), today)
        out.append((d, e)); d = e + dt.timedelta(days=1)
    return out[::-1]              # recent-first


def _load_manifest():
    try:
        return set(json.load(open(MANIFEST)))
    except Exception:
        return set()


def _save_manifest(done):
    os.makedirs(LAKE, exist_ok=True)
    json.dump(sorted(done), open(MANIFEST, "w"))


def _series_path(flag, side, off):
    d = os.path.join(LAKE, flag)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{side}_{_strike_str(off).replace('+','p').replace('-','m')}.csv")


def _fetch(flag, dtype, off, frm, to, headers):
    body = {"exchangeSegment": "NSE_FNO", "securityId": 13, "instrument": "OPTIDX",
            "interval": "5", "expiryFlag": flag, "expiryCode": 1, "strike": _strike_str(off),
            "drvOptionType": dtype,
            "requiredData": ["open", "high", "low", "close", "volume", "iv", "oi", "strike", "spot"],
            "fromDate": str(frm), "toDate": str(to)}
    r = requests.post(URL, json=body, headers=headers, timeout=30)
    return r


def _append(path, rows):
    """append rows (dedup on timestamp against existing file)."""
    seen = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                seen.add(line.split(",", 1)[0])
    new = [r for r in rows if str(r[0]) not in seen]
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(COLS)
        w.writerows(new)
    return len(new)


def main():
    os.makedirs(LAKE, exist_ok=True)
    done = _load_manifest()
    chunks = _chunks()
    # priority task order: WEEK first, ATM outward, recent chunks first
    tasks = [(flag, side, dtype, off, frm, to)
             for flag in FLAGS
             for off in OFFSETS
             for (side, dtype) in SIDES
             for (frm, to) in chunks]
    total = len(tasks)
    print(f"optchain_dl: {total} calls  ({len(OFFSETS)} offsets x {len(SIDES)} sides x "
          f"{len(FLAGS)} flags x {len(chunks)} chunks)  lake={LAKE}", flush=True)
    rl = _rl_acquire()
    ok = skip = empty = err = 0
    t0 = time.time()
    for i, (flag, side, dtype, off, frm, to) in enumerate(tasks):
        key = f"{flag}|{side}|{off}|{frm}"
        if key in done:
            skip += 1; continue
        try:
            if rl:
                for _ in range(120):
                    if rl.acquire("account"):
                        break
                    time.sleep(1.0)
            r = _fetch(flag, dtype, off, frm, to, _tok())
            if r.status_code == 429:
                if rl:
                    rl.note_429()
                print(f"  [{i+1}/{total}] 429 — backoff 20s ({key})", flush=True)
                time.sleep(20); continue
            if r.status_code != 200:
                err += 1
                if err <= 20:
                    print(f"  [{i+1}/{total}] HTTP {r.status_code} {key}: {str(r.text)[:100]}", flush=True)
                done.add(key); continue      # non-retryable input error → mark done, skip
            d = r.json()
            s = (d.get("data") or {}).get(side.lower()) or {}
            n = len(s.get("close") or [])
            if n == 0:
                empty += 1; done.add(key)
                if empty <= 10:
                    print(f"  [{i+1}/{total}] empty {key}", flush=True)
            else:
                rows = list(zip(s["timestamp"], s["open"], s["high"], s["low"], s["close"],
                                s.get("volume", [0] * n), s.get("iv", [""] * n),
                                s.get("oi", [""] * n), s.get("strike", [""] * n),
                                s.get("spot", [""] * n)))
                added = _append(_series_path(flag, side, off), rows)
                ok += 1; done.add(key)
                if ok % 25 == 0 or i < 5:
                    rate = (i + 1) / max(time.time() - t0, 1)
                    eta = (total - i - 1) / max(rate, 0.01) / 60
                    print(f"  [{i+1}/{total}] {key}: +{added} rows (ok={ok} empty={empty} err={err}) "
                          f"~{rate:.1f}/s ETA {eta:.0f}m", flush=True)
            if (i + 1) % 40 == 0:
                _save_manifest(done)
        except Exception as e:
            err += 1
            if err <= 20:
                print(f"  [{i+1}/{total}] EXC {key}: {e}", flush=True)
            time.sleep(2)
        time.sleep(0.15)          # gentle base spacing on top of the limiter
    _save_manifest(done)
    print(f"\nDONE in {(time.time()-t0)/60:.0f}m  ok={ok} empty={empty} err={err} skip={skip}", flush=True)


if __name__ == "__main__":
    main()
