#!/usr/bin/env python3
"""
auto_data_downloader.py — VPS daemon that auto-downloads OHLC bars for every traded instrument.

Run:  nohup python3 auto_data_downloader.py >> logs/downloader.log 2>&1 &
Stop: pkill -f auto_data_downloader.py

Storage:
  data/trade_ohlc/{SECID}_{DATE}.json  — 1-min bars per instrument per day
  data/downloader_log.json             — {date: {sec_id: "ok"|"missing"|"token_expired"}}
  data/downloader_alert.json           — list of strings shown as dashboard banner

Dashboard reads /api/downloader-alerts (route in trader_dashboard.py) which returns this file.
"""

import json
import time
import datetime
import socket
import logging
import requests
from pathlib import Path
import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); import _paths  # refactor: _ops/ → root+_core/_data on path

# IPv4 force — Dhan rejects IPv6 (DH-905)
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR   = Path(__file__).resolve().parent.parent
DATA_DIR   = BASE_DIR / "data"
OHLC_DIR   = DATA_DIR / "trade_ohlc"
LOG_FILE   = DATA_DIR / "downloader_log.json"
ALERT_FILE = DATA_DIR / "downloader_alert.json"
CFG_FILE   = DATA_DIR / "config.json"

OHLC_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DOWNLOADER] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MARKET_OPEN  = datetime.time(9, 0)
MARKET_CLOSE = datetime.time(16, 0)   # 30-min buffer after 15:30


def _get_headers():
    cfg = json.loads(CFG_FILE.read_text()) if CFG_FILE.exists() else {}
    return {
        "access-token": cfg.get("jwt_token", ""),
        "client-id":    cfg.get("client_id", ""),
        "Content-Type": "application/json",
    }


def _load_log() -> dict:
    return json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else {}


def _save_log(dl_log: dict):
    LOG_FILE.write_text(json.dumps(dl_log, indent=2))


def _save_alerts(alerts: list):
    ALERT_FILE.write_text(json.dumps(alerts))


def fetch_orders() -> list:
    """Fetch all orders from Dhan. Returns list of dicts with filled orders only."""
    try:
        try:
            import dhan_rate_limiter as _rl
            _rl.set_context("Downloader:Orders")
            _rl.acquire("account")
        except ImportError:
            pass
        r = requests.get("https://api.dhan.co/v2/orders", headers=_get_headers(), timeout=10)
        if r.status_code in (401, 403):
            log.warning("Token expired (HTTP %s)", r.status_code)
            return None   # None = token error, [] = no orders
        r.raise_for_status()
        orders = r.json()
        if not isinstance(orders, list):
            orders = orders.get("data", [])
        filled = [o for o in orders if o.get("orderStatus") in ("TRADED", "PART_TRADED")]
        log.info("Fetched %d filled orders", len(filled))
        return filled
    except Exception as e:
        log.error("fetch_orders error: %s", e)
        return []


def download_bars(sec_id: str, exchange: str, instrument: str, date_str: str, force: bool = False) -> bool:
    """Download 1-min OHLC bars for one instrument on one date. Returns True on success.
    force=True re-fetches even if a file already exists (used for the post-close
    full-day capture of order_store trades — a mid-day file is only a partial day)
    and MERGES into whatever's already saved rather than overwriting."""
    out_file = OHLC_DIR / f"{sec_id}_{date_str}.json"
    if out_file.exists() and not force:
        return True  # already have it

    time.sleep(3)  # rate-limit guard
    try:
        # shared cross-process limiter — this daemon's candle bursts (one per
        # new fill) previously bypassed it entirely and collided with the
        # live strategies' own candle sweeps (TRAP #95)
        try:
            import dhan_rate_limiter as _rl
            _rl.set_context("Downloader:Bars")
            if not _rl.acquire("candle", timeout=20):
                return False   # gate saturated — retried on the next 5-min cycle
        except ImportError:
            _rl = None
        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            headers=_get_headers(),
            json={
                "securityId":      sec_id,
                "exchangeSegment": exchange,
                "instrument":      instrument,
                "expiryCode":      0,
                "fromDate":        date_str,
                "toDate":          date_str,
            },
            timeout=20,
        )
        if r.status_code in (401, 403):
            return None  # token error

        d = r.json()
        if d.get("errorCode") == "DH-904":
            if _rl:
                _rl.note_429()   # tell every other process to back off too
            log.warning("DH-904 rate limit, sleeping 15s")
            time.sleep(15)
            return False

        if "open" not in d or not d["open"]:
            log.warning("No data: %s %s", sec_id, date_str)
            return False

        bars = {}
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
            # Key by RAW Dhan epoch (str), NOT a machine-local HH:MM string —
            # the dashboard's premium-chart reader applies the +19800 IST-display
            # shift at read time (same transform as the live path), so an epoch
            # key is timezone-unambiguous regardless of this daemon's machine TZ.
            # (Old HH:MM-keyed files stay on disk but the reader skips them.)
            bars[str(int(ts))] = [round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2)]

        # Merge into any existing file (a mid-day capture or a chart write-through
        # may already hold part of the day) rather than clobbering it.
        if out_file.exists():
            try:
                existing = json.loads(out_file.read_text())
                if isinstance(existing, dict):
                    existing.update(bars)
                    bars = existing
            except Exception:
                pass
        out_file.write_text(json.dumps(bars))
        log.info("Saved %d bars → %s", len(bars), out_file.name)
        return True

    except Exception as e:
        log.error("download_bars error %s %s: %s", sec_id, date_str, e)
        return False


def process_orders(orders: list, dl_log: dict) -> bool:
    """Download bars for all filled orders. Returns False if token error encountered."""
    for o in orders:
        sec_id   = str(o.get("securityId", ""))
        exchange = o.get("exchangeSegment", "")
        instr    = o.get("transactionType", "")  # not used — we derive below
        date_str = (o.get("createTime") or o.get("updateTime") or "")[:10]

        if not sec_id or not date_str or date_str < "2026-01-01":
            continue

        # Derive instrument type from exchange
        if exchange in ("NSE_FNO", "BSE_FNO"):
            instrument = "OPTIDX" if "NIFTY" in o.get("tradingSymbol", "") else "OPTSTK"
        elif exchange in ("IDX_I",):
            instrument = "INDEX"
        else:
            instrument = "EQUITY"

        day_log = dl_log.setdefault(date_str, {})
        if day_log.get(sec_id) == "ok":
            continue  # already downloaded in a previous run

        result = download_bars(sec_id, exchange, instrument, date_str)
        if result is None:
            log.warning("Token error during download")
            return False
        day_log[sec_id] = "ok" if result else "missing"

    return True  # no token error


INDEX_ROOTS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}


def process_order_store_trades(dl_log: dict) -> bool:
    """Capture full-day 1-min premium bars for EVERY option contract traded today
    per order_store — regardless of which broker placed it. Why this exists in
    addition to process_orders(): that one only sees DHAN-placed orders (Dhan
    /v2/orders), but the algo trades on Kite/Zerodha, so those trades would never
    get their premium bars saved and their charts would vanish after the contract
    expires (#5, 2026-07-07). The premium candle data itself always comes from
    Dhan's charts API by securityId (broker-agnostic) — only the LIST of which
    securityIds to grab needs to come from order_store instead of Dhan orders.

    Runs only AFTER market close (15:35 IST — VPS clock is IST) so it grabs the
    FULL day in one shot; marks each contract 'final' so it's fetched exactly once
    per day. Returns False on a token error (so run_once can surface the alert)."""
    if datetime.datetime.now().time() < datetime.time(15, 35):
        return True  # capture the full day once, after close — not mid-session
    try:
        import order_store
    except Exception as e:
        log.error("order_store import failed: %s", e)
        return True
    today = datetime.date.today().isoformat()
    try:
        with order_store._lock, order_store._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT sec_id, trad_sym FROM orders "
                "WHERE substr(ts,1,10)=? AND sec_id IS NOT NULL AND sec_id!='' "
                "AND trad_sym IS NOT NULL AND trad_sym!=''",
                (today,)).fetchall()
    except Exception as e:
        log.error("order_store read failed: %s", e)
        return True

    day_log = dl_log.setdefault(today, {})
    n_done = 0
    for sec_id, trad_sym in rows:
        sec_id = str(sec_id)
        if "-CE" not in trad_sym and "-PE" not in trad_sym:
            continue  # options only — premium chart
        key = f"os_{sec_id}"
        if day_log.get(key) == "final":
            continue
        root = trad_sym.split("-")[0].upper()
        instrument = "OPTIDX" if root in INDEX_ROOTS else "OPTSTK"
        result = download_bars(sec_id, "NSE_FNO", instrument, today, force=True)
        if result is None:
            log.warning("Token error during order_store capture")
            return False
        day_log[key] = "final" if result else "missing"
        if result:
            n_done += 1
    if n_done:
        log.info("order_store capture: %d option contract(s) saved (broker-agnostic)", n_done)
    return True


def gap_check(dl_log: dict) -> list:
    """
    Find trading days where some instruments have 'missing' status.
    Returns list of human-readable alert strings for the dashboard banner.
    """
    alerts = []
    today  = datetime.date.today()
    cutoff = today - datetime.timedelta(days=60)  # Dhan retains ~60 days

    for date_str, instruments in sorted(dl_log.items(), reverse=True):
        date = datetime.date.fromisoformat(date_str)
        if date < cutoff:
            continue

        missing = [sid for sid, status in instruments.items() if status != "ok"]
        if not missing:
            continue

        days_ago = (today - date).days
        # Estimate if options from that date might expire soon (weekly = ~7 days)
        urgent = days_ago >= 5

        label = date.strftime("%d %b")
        if urgent:
            alerts.append(f"🚨 {label} ka data missing ({len(missing)} instruments) — contract expire hone wala hai! Token update karo.")
        else:
            alerts.append(f"⚠️ {label} ka data missing ({len(missing)} instruments) — token update karein.")

    return alerts


def run_once():
    """One full cycle: fetch orders → download bars → gap check → update alerts."""
    dl_log = _load_log()
    alerts = []

    orders = fetch_orders()

    if orders is None:
        # Token expired
        today = datetime.date.today().isoformat()
        dl_log.setdefault(today, {})["_token"] = "token_expired"
        _save_log(dl_log)
        alerts.append(f"🔴 Dhan token expire ho gaya — dashboard mein Control tab > token update karein. {today} ka data nahi bacha.")
        _save_alerts(alerts)
        log.warning("Token expired — alert written")
        return

    if orders:
        token_ok = process_orders(orders, dl_log)
        _save_log(dl_log)
        if not token_ok:
            alerts.append(f"🔴 Dhan token expire — {datetime.date.today().isoformat()} ka data nahi bacha. Control tab mein update karein.")
            _save_alerts(alerts)
            return

    # Broker-agnostic full-day capture from order_store (catches Kite trades the
    # Dhan-orders poll above can't see — #5). Post-close only; idempotent.
    os_ok = process_order_store_trades(dl_log)
    _save_log(dl_log)
    if not os_ok:
        alerts.append(f"🔴 Dhan token expire — {datetime.date.today().isoformat()} ke trade charts save nahi hue. Control tab mein update karein.")
        _save_alerts(alerts)
        return

    alerts = gap_check(dl_log)
    _save_alerts(alerts)

    if alerts:
        log.info("Alerts: %s", alerts)
    else:
        log.info("All good — no missing data")


def is_market_hours() -> bool:
    now = datetime.datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def main():
    log.info("Auto data downloader started. PID=%d", __import__("os").getpid())
    log.info("OHLC dir: %s", OHLC_DIR)

    while True:
        try:
            run_once()
        except Exception as e:
            log.error("run_once crashed: %s", e)

        # Sleep: 5 min during market hours, 60 min otherwise
        sleep_sec = 300 if is_market_hours() else 3600
        log.info("Sleeping %ds until next check", sleep_sec)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
