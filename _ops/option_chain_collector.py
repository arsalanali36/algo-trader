"""
option_chain_collector.py — live NIFTY + BANKNIFTY option-chain + India-VIX snapshot collector.

WHY (req 37.1): the strategies that trade the option CHAIN itself (OI / IV / greeks /
premium structure — gamma scalp, VIX-crush, OI/PCR/Max-Pain, delta-neutral) cannot be
backtested on history because per-strike OI/IV/greeks snapshots simply don't exist on disk
and Dhan does NOT serve them historically. The only way to get this data is to collect it
LIVE, going forward. This daemon does exactly that — every minute during market hours it
pulls the full option chain for NIFTY + BANKNIFTY (Dhan `/v2/optionchain`), keeps ATM±N
strikes, and appends OI/chg-OI/IV/LTP/volume/greeks per strike to a per-day CSV in the
data-lake, alongside spot + India VIX aligned to the same timestamp.

Once enough real data accumulates, the Track-B strategies validate on REAL premium/IV
instead of Black-Scholes-modeled premium. Until then this file just quietly builds the lake.

Design notes:
- Self-contained: only stdlib + requests. Reads the Dhan token straight from data/config.json
  (same file every other module uses), resolves India-VIX sec_id from the scrip master CSV.
  Deliberately does NOT import the project's _paths / rate-limiter machinery — the
  `/v2/optionchain` endpoint is a SEPARATE Dhan rate bucket (1 req / 3s) that no other
  process in this codebase touches, so a local 3.2s spacer is enough and keeps the daemon
  robust + trivially deployable.
- IPv4 force (DH-905) at top — Dhan rejects IPv6 on the VPS.
- Never fabricates: on any token/network/parse failure it logs + skips that cycle, never
  writes a guessed row.

Run:
  python option_chain_collector.py --once     # one snapshot now (probe / verify schema)
  python option_chain_collector.py            # daemon: 1-min snapshots, market hours only
  python option_chain_collector.py --strikes 10 --interval 60
"""

import argparse
import csv
import json
import os
import socket
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# --- IPv4 force (DH-905) — MUST be before any Dhan network call ---
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

IST = timezone(timedelta(hours=5, minutes=30))

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)                     # _ops/ -> project root
CONFIG_FILE = os.path.join(PROJECT, "data", "config.json")
SCRIP_MASTER = os.path.join(PROJECT, "data", "api-scrip-master.csv")

# Data-lake root: sits one level ABOVE the project dir (same place the equity/index
# lakes live: D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA). Fall back to an in-project
# folder if that parent isn't present (VPS uses in-project _TRADING_DATA).
_PARENT_LAKE = os.path.join(os.path.dirname(PROJECT), "._TRADING DATA")
if os.path.isdir(_PARENT_LAKE):
    LAKE = _PARENT_LAKE
else:
    LAKE = os.path.join(PROJECT, "_TRADING_DATA")
OC_DIR = os.path.join(LAKE, "OptionChain")

OC_URL   = "https://api.dhan.co/v2/optionchain"
EXP_URL  = "https://api.dhan.co/v2/optionchain/expirylist"
LTP_URL  = "https://api.dhan.co/v2/marketfeed/ltp"

# Optional: cooperate with the project's cross-process Dhan rate-limiter for the VIX LTP
# call (marketfeed/ltp is a SHARED account bucket the live strategies also hit — without
# this the collector's VIX call competes with them and 429s, TRAP #2). The option-chain
# endpoint is its own separate bucket, so it doesn't need this. Degrades gracefully if the
# limiter isn't importable (keeps the daemon standalone-deployable).
try:
    sys.path.insert(0, os.path.join(PROJECT, "_data"))
    import dhan_rate_limiter as _rl          # type: ignore
except Exception:
    _rl = None

# Underlyings: NIFTY sec_id 13, BANKNIFTY sec_id 25, both IDX_I (per CLAUDE.md Critical Rule 2).
UNDERLYINGS = [
    {"name": "NIFTY",     "scrip": 13, "seg": "IDX_I", "step": 50},
    {"name": "BANKNIFTY", "scrip": 25, "seg": "IDX_I", "step": 100},
]

CSV_COLS = ["datetime", "underlying", "spot", "vix", "expiry", "strike", "opt_type",
            "ltp", "oi", "prev_oi", "chg_oi", "volume", "iv",
            "delta", "theta", "gamma", "vega"]


def log(msg):
    print(f"[{datetime.now(IST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_creds():
    cfg = json.loads(open(CONFIG_FILE, encoding="utf-8").read())
    return cfg["jwt_token"], cfg["client_id"]


def hdrs(token, cid):
    return {"access-token": token, "client-id": cid, "Content-Type": "application/json"}


def resolve_vix_sec_id():
    """Find India VIX sec_id from the Dhan scrip master (never hardcode — no-assumptions rule).
    Returns int sec_id or None. VIX is an index (IDX_I segment) named like 'INDIA VIX'."""
    if not os.path.exists(SCRIP_MASTER):
        return None
    try:
        with open(SCRIP_MASTER, encoding="utf-8", errors="ignore") as f:
            r = csv.DictReader(f)
            for row in r:
                sym = (row.get("SEM_TRADING_SYMBOL") or row.get("SEM_CUSTOM_SYMBOL") or "").upper()
                seg = (row.get("SEM_SEGMENT") or row.get("SEM_EXM_EXCH_ID") or "").upper()
                if "VIX" in sym and ("I" == seg or "IDX" in seg or "INDEX" in seg):
                    sid = row.get("SEM_SMST_SECURITY_ID") or row.get("SECURITY_ID")
                    if sid:
                        return int(float(sid))
    except Exception as e:
        log(f"VIX sec_id resolve failed: {e}")
    return None


# --- Dhan calls (option-chain endpoint = its own 1req/3s bucket; caller spaces them) ---

def fetch_expiry_list(token, cid, scrip, seg):
    body = {"UnderlyingScrip": scrip, "UnderlyingSeg": seg}
    r = requests.post(EXP_URL, json=body, headers=hdrs(token, cid), timeout=10)
    r.raise_for_status()
    data = r.json().get("data", []) or []
    return sorted(data)  # ascending dates -> [0] = nearest


def fetch_chain(token, cid, scrip, seg, expiry):
    body = {"UnderlyingScrip": scrip, "UnderlyingSeg": seg, "Expiry": expiry}
    r = requests.post(OC_URL, json=body, headers=hdrs(token, cid), timeout=15)
    r.raise_for_status()
    return r.json().get("data", {}) or {}


def fetch_vix(token, cid, vix_sec_id):
    """India VIX LTP via marketfeed/ltp (shared account bucket). Cooperates with the
    cross-process rate-limiter and retries a few times, since live strategies hammer the
    same ~1req/3s bucket. Returns float or None (never a guessed value)."""
    if not vix_sec_id:
        return None
    for _ in range(3):
        try:
            if _rl:
                _rl.acquire("ltp")
            r = requests.post(LTP_URL, json={"IDX_I": [int(vix_sec_id)]},
                              headers=hdrs(token, cid), timeout=8)
            if r.status_code == 429:
                if _rl:
                    _rl.note_429()
                time.sleep(1.5)
                continue
            if r.status_code != 200:
                return None
            node = r.json().get("data", {}).get("IDX_I", {}) or {}
            for _sid, v in node.items():
                return float(v.get("last_price") or v.get("ltp") or 0) or None
        except Exception:
            time.sleep(1.0)
    return None


def _leg_row(dt, uname, spot, vix, expiry, strike, opt_type, leg):
    """Flatten one CE/PE leg dict from Dhan's option chain into a CSV row."""
    g = leg.get("greeks", {}) or {}
    oi = leg.get("oi")
    prev_oi = leg.get("previous_oi")
    chg = (oi - prev_oi) if (oi is not None and prev_oi is not None) else None
    return {
        "datetime": dt, "underlying": uname, "spot": spot, "vix": vix,
        "expiry": expiry, "strike": strike, "opt_type": opt_type,
        "ltp": leg.get("last_price"), "oi": oi, "prev_oi": prev_oi, "chg_oi": chg,
        "volume": leg.get("volume"),
        "iv": leg.get("implied_volatility"),
        "delta": g.get("delta"), "theta": g.get("theta"),
        "gamma": g.get("gamma"), "vega": g.get("vega"),
    }


def snapshot_underlying(token, cid, u, expiry, strikes_each_side, vix, dt):
    """Return (rows, spot) for one underlying's ATM±N strikes at time dt."""
    data = fetch_chain(token, cid, u["scrip"], u["seg"], expiry)
    spot = data.get("last_price")
    oc = data.get("oc", {}) or {}
    if not oc or spot is None:
        return [], spot

    # ATM = nearest step; keep ATM±N strikes.
    atm = round(float(spot) / u["step"]) * u["step"]
    lo = atm - strikes_each_side * u["step"]
    hi = atm + strikes_each_side * u["step"]

    rows = []
    for k_str, node in oc.items():
        try:
            strike = float(k_str)
        except (TypeError, ValueError):
            continue
        if strike < lo - 1 or strike > hi + 1:
            continue
        ce = node.get("ce") or {}
        pe = node.get("pe") or {}
        if ce:
            rows.append(_leg_row(dt, u["name"], spot, vix, expiry, strike, "CE", ce))
        if pe:
            rows.append(_leg_row(dt, u["name"], spot, vix, expiry, strike, "PE", pe))
    rows.sort(key=lambda r: (r["strike"], r["opt_type"]))
    return rows, spot


def write_rows(uname, day_str, rows):
    d = os.path.join(OC_DIR, uname)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{uname}_{day_str}.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS)
        if new:
            w.writeheader()
        w.writerows(rows)
    return path


def market_open(now_ist):
    if now_ist.weekday() >= 5:            # Sat/Sun
        return False
    hm = now_ist.hour * 60 + now_ist.minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 30)


def run_snapshot(token, cid, vix_sec_id, strikes, expiry_cache, force=False):
    now = datetime.now(IST)
    if not force and not market_open(now):
        return False
    dt = now.strftime("%Y-%m-%d %H:%M:%S")
    day_str = now.strftime("%Y-%m-%d")
    vix = fetch_vix(token, cid, vix_sec_id)

    got_any = False
    for i, u in enumerate(UNDERLYINGS):
        try:
            # nearest expiry, cached per (underlying, day) — refetch each new day
            ck = (u["name"], day_str)
            if ck not in expiry_cache:
                exps = fetch_expiry_list(token, cid, u["scrip"], u["seg"])
                if not exps:
                    log(f"{u['name']}: no expiries returned, skip")
                    continue
                expiry_cache[ck] = exps[0]
                time.sleep(3.2)  # respect option-chain 1req/3s bucket before the chain call
            expiry = expiry_cache[ck]

            rows, spot = snapshot_underlying(token, cid, u, expiry, strikes, vix, dt)
            if rows:
                path = write_rows(u["name"], day_str, rows)
                got_any = True
                log(f"{u['name']} spot={spot} vix={vix} exp={expiry} "
                    f"strikes={len(rows)//2} -> {os.path.basename(path)}")
            else:
                log(f"{u['name']}: empty chain (spot={spot}), skip")
        except requests.HTTPError as e:
            log(f"{u['name']} HTTP error: {e} — skip (no row written)")
        except Exception as e:
            log(f"{u['name']} error: {e} — skip (no row written)")
        if i < len(UNDERLYINGS) - 1:
            time.sleep(3.2)  # space chain calls (1 req / 3s bucket)
    return got_any


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one snapshot now (probe), ignore market hours")
    ap.add_argument("--strikes", type=int, default=10, help="strikes each side of ATM (default 10)")
    ap.add_argument("--interval", type=int, default=60, help="seconds between snapshots (default 60)")
    args = ap.parse_args()

    token, cid = load_creds()
    vix_sec_id = resolve_vix_sec_id()
    log(f"collector start — strikes=ATM±{args.strikes} interval={args.interval}s "
        f"vix_sec_id={vix_sec_id} lake={OC_DIR}")

    expiry_cache = {}
    if args.once:
        ok = run_snapshot(token, cid, vix_sec_id, args.strikes, expiry_cache, force=True)
        log(f"--once done (wrote_data={ok})")
        return

    while True:
        try:
            run_snapshot(token, cid, vix_sec_id, args.strikes, expiry_cache)
        except Exception as e:
            log(f"loop error: {e}")
        # re-read token each cycle in case it was refreshed mid-day (expires ~24h)
        try:
            token, cid = load_creds()
        except Exception:
            pass
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
