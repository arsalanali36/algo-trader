#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  orb_trader.py  —  Mid-Day Opening-Range Breakout  (Version 1)             ║
# ║  Research      : scratch/nifty_trend/ (tod_orb winner, train0.95≈OOS0.96)  ║
# ║  Config file   : ../../nifty_config.json  →  key: "orb_v1"                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (NIFTY spot signal → ATM option execution, long + short)          │
# │                                                                          │
# │  Opening range   : high/low of the first `or_min` mins (09:15–09:45)     │
# │  Entry window    : only 11:00–13:00 (mid-day; morning noise settled)     │
# │  LONG  (BUY CE)  : close breaks ABOVE  OR_high + orb_k×ATR                │
# │  SHORT (BUY PE)  : close breaks BELOW  OR_low  − orb_k×ATR                │
# │  Stop / Target   : on SPOT — entry ∓ atr_sl×ATR, target at RR × stop      │
# │  FORCE EXIT      : 3:15 PM (RMS single-source) — never overnight          │
# │  Max 2 trades/day.  1x, no leverage (position sizing = config lots).      │
# │                                                                          │
# │  Backtest fidelity: ATR(14) needs continuous history — we fetch the last │
# │  few days of 15m bars so ATR is warm at 11:00 (TRAP #85), but OR +       │
# │  signals only use TODAY's bars.                                          │
# └─────────────────────────────────────────────────────────────────────────┘

import json
import logging
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# IPv4 force — VPS pe IPv6 hoti hai, Dhan reject karta hai (DH-905)
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR    = Path(__file__).resolve().parent.parent.parent   # project root
CONFIG_FILE = BASE_DIR / "data" / "config.json"               # Dhan JWT token
TC_FILE     = BASE_DIR / "nifty_config.json"                  # strategy params

sys.path.insert(0, str(BASE_DIR))
import _paths  # sys.path bootstrap — _core/_data/_ops flat imports resolve
import dhan_master
from _CHARTING.indicators import wilder_atr as _atr   # canonical (Rule 6B/ADR-002) — matches backtest

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
FORCE_EXIT   = (15, 15)
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

# NIFTY spot index — well-known ids used across the project (NIFTY_SEC_ID=13).
# NOT a "hardcoded market value" (lot-size/strike come from the scrip master).
NIFTY_SEC_ID, NIFTY_SEG, NIFTY_INST = "13", "IDX_I", "INDEX"

STATE_FILE = lambda sid: BASE_DIR / "data" / f"{sid}_state.json"


def _make_logger(strategy_id):
    log_file = BASE_DIR / "logs" / f"{strategy_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(strategy_id)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_file); fh.setFormatter(fmt)
        lg.addHandler(fh)
        # console handler ONLY on an interactive TTY. Under systemd/Popen, stdout is
        # already redirected INTO log_file — a StreamHandler would double-write it.
        if getattr(sys.stdout, "isatty", lambda: False)():
            sh = logging.StreamHandler(); sh.setFormatter(fmt)
            lg.addHandler(sh)
    return lg


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def is_market_open():
    t = (ist_now().hour, ist_now().minute)
    return MARKET_OPEN <= t < MARKET_CLOSE

def _exit_times():
    try:
        import risk_gate as _rg
        return _rg.exit_time_config()
    except Exception:
        return FORCE_EXIT, FORCE_EXIT

def is_force_exit_time():
    return (ist_now().hour, ist_now().minute) >= _exit_times()[0]

def is_no_entry_time():
    return (ist_now().hour, ist_now().minute) >= _exit_times()[1]

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "timeframe": "15m",
    "or_min": 30, "orb_k": 1.0, "atr_period": 14, "atr_sl": 2.5, "rr": 1.5,
    "win_start": "11:00", "win_end": "13:00",
    "qty": 1, "max_trades_per_day": 2, "strike_offset": 0, "symbol": "NIFTY",
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def _hm(s):
    """'11:00' -> (11, 0)."""
    try:
        h, m = str(s).split(":"); return (int(h), int(m))
    except Exception:
        return (11, 0)


def fetch_nifty_15m(token, cid, days=5):
    """Continuous 15m NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85).
    Returns df[time, open, high, low, close] or None."""
    try:
        import shared_candle_cache
        cached = shared_candle_cache.get(NIFTY_SEC_ID, 15, max_age=20.0)
        if cached:
            df = pd.DataFrame(cached); df["time"] = pd.to_datetime(df["time"])
            return df.dropna() if not df.empty else None
    except Exception:
        pass
    try:
        to = ist_now().date(); fr = to - timedelta(days=days)
        body = {"securityId": NIFTY_SEC_ID, "exchangeSegment": NIFTY_SEG,
                "instrument": NIFTY_INST, "interval": 15,
                "fromDate": str(fr), "toDate": str(to)}
        try:
            import dhan_rate_limiter as _rl
            if not _rl.acquire("candle"):
                return None
        except ImportError:
            _rl = None
        r = requests.post(INTRADAY_URL, json=body,
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"}, timeout=10)
        if r.status_code == 429 and _rl:
            _rl.note_429()
        if r.status_code != 200:
            return None
        d = r.json()
        rows = list(zip(d.get("timestamp", []), d.get("open", []), d.get("high", []),
                        d.get("low", []), d.get("close", [])))
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
        df["time"] = (pd.to_datetime(df["time"], unit="s", utc=True)
                        .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
        df = df.dropna()
        # session filter 09:15–15:29 weekdays
        tt = df["time"].dt.time
        df = df[(df["time"].dt.weekday < 5) &
                (tt >= datetime(2000, 1, 1, 9, 15).time()) &
                (tt <= datetime(2000, 1, 1, 15, 29).time())].reset_index(drop=True)
        if df.empty:
            return None
        try:
            import shared_candle_cache
            c = df.copy(); c["time"] = c["time"].astype(str)
            shared_candle_cache.put(NIFTY_SEC_ID, 15, c.to_dict("records"))
        except Exception:
            pass
        return df
    except Exception:
        return None


def compute_signal(df, cfg):
    """Return dict(signal='long'/'short', entry_spot, atr, stop, target) or None.
    Uses TODAY's opening range + mid-day window breakout with orb_k×ATR strength,
    on the last CLOSED bar. ATR is continuous (multi-day df)."""
    p = cfg
    period = int(p["atr_period"])
    if len(df) < period + 3:
        return None
    df = df.copy().reset_index(drop=True)
    df["atr"] = _atr(df, period)
    today = ist_now().date()
    tday = df[df["time"].dt.date == today].reset_index(drop=True)
    if len(tday) < 3:
        return None

    or_end = (datetime.combine(today, datetime.min.time())
              .replace(hour=9, minute=15) + timedelta(minutes=int(p["or_min"]))).time()
    or_bars = tday[tday["time"].dt.time < or_end]
    if or_bars.empty:
        return None
    or_high = float(or_bars["high"].max()); or_low = float(or_bars["low"].min())

    # last CLOSED bar = second-to-last row of the continuous df (last row = forming)
    i = len(df) - 2
    if i < 1:
        return None
    bar = df.iloc[i]
    # only signal inside the entry window, and on a TODAY bar
    if bar["time"].date() != today:
        return None
    ws, we = _hm(p["win_start"]), _hm(p["win_end"])
    bt = (bar["time"].hour, bar["time"].minute)
    if not (ws <= bt <= we):
        return None

    k = float(p["orb_k"]); atr_i = float(df["atr"].iloc[i]); atr_p = float(df["atr"].iloc[i - 1])
    if atr_i <= 0:
        return None
    up_i, up_p = or_high + k * atr_i, or_high + k * atr_p
    lo_i, lo_p = or_low - k * atr_i, or_low - k * atr_p
    c_i, c_p = float(df["close"].iloc[i]), float(df["close"].iloc[i - 1])

    side = None
    if c_i > up_i and c_p <= up_p:
        side = "long"
    elif c_i < lo_i and c_p >= lo_p:
        side = "short"
    if not side:
        return None

    entry_spot = c_i
    stop_dist = float(p["atr_sl"]) * atr_i
    if side == "long":
        stop = entry_spot - stop_dist; target = entry_spot + float(p["rr"]) * stop_dist
    else:
        stop = entry_spot + stop_dist; target = entry_spot - float(p["rr"]) * stop_dist
    return dict(signal=side, entry_spot=entry_spot, atr=atr_i, stop=stop, target=target,
                bar_time=str(bar["time"]))


# ─────────────────────────── state persist / recover ───────────────────────
def save_state(sid, pos):
    try:
        STATE_FILE(sid).write_text(json.dumps({"pos": pos, "date": str(ist_now().date())}))
    except Exception:
        pass

def load_state(sid):
    try:
        d = json.loads(STATE_FILE(sid).read_text())
        if d.get("date") == str(ist_now().date()):
            return d.get("pos")
    except Exception:
        pass
    return None

def _recover(sid, log):
    """Restore today's open position from disk, but ONLY if order_store still
    shows it open at the broker (TRAP #28 — restart must not orphan/duplicate)."""
    pos = load_state(sid)
    if not pos:
        return None
    try:
        import order_store
        opens = order_store.trades_for(ist_now().strftime("%Y-%m-%d")).get("open") or []
        for p in opens:
            if p.get("strategy") == sid and str(p.get("sec_id")) == str(pos.get("sec_id")):
                log.info(f"[RECOVER] re-attached open {pos.get('trad_sym')} "
                         f"stop={pos.get('stop'):.1f} target={pos.get('target'):.1f}")
                return pos
        log.info("[RECOVER] disk state had a position but order_store shows it closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="orb_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  orb_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos = _recover(strategy_id, log)     # dict or None
    trades_today = 0 if pos is None else 1
    last_date = ist_now().date()

    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else (tc.get("mode", "paper"))
            if paper_mode:
                mode = "paper"

            if last_date != now.date():
                last_date = now.date(); pos = None; trades_today = 0
                save_state(strategy_id, None)
                log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[ORB] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[ORB] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            sym = tc.get("symbol", "NIFTY")
            token, cid = load_creds()
            df = fetch_nifty_15m(token, cid)
            if df is None or df.empty:
                log.warning("[ORB] no candle data"); time.sleep(30); continue
            spot = float(df["close"].iloc[-1])
            cur = df.iloc[-1]     # forming bar — its high/low = extremes so far

            # ── manage OPEN position: spot stop/target (matches backtest) ──
            if pos is not None:
                hit = None
                if pos["signal"] == "long":
                    if float(cur["low"]) <= pos["stop"]:   hit = ("ORB_SL", pos["stop"])
                    elif float(cur["high"]) >= pos["target"]: hit = ("ORB_TARGET", pos["target"])
                else:
                    if float(cur["high"]) >= pos["stop"]:  hit = ("ORB_SL", pos["stop"])
                    elif float(cur["low"]) <= pos["target"]: hit = ("ORB_TARGET", pos["target"])
                if is_force_exit_time():
                    hit = ("EOD_315_SQUAREOFF", spot)
                if hit:
                    reason, lvl = hit
                    log.info(f"[EXIT] {pos['trad_sym']} — {reason} @ spot {lvl:.1f}")
                    _do_exit(strategy_id, sym, pos, mode, _order_broker(tc), reason, log)
                    pos = None; save_state(strategy_id, None)

            if is_force_exit_time():
                time.sleep(60); continue

            # ── ENTRY ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 2)):
                if is_no_entry_time():
                    time.sleep(30); continue
                sig = compute_signal(df, tc)
                ws, we = _hm(tc["win_start"]), _hm(tc["win_end"])
                log.info(f"[ORB] spot={spot:.1f}  window={tc['win_start']}-{tc['win_end']}  "
                         f"pos=flat  trades={trades_today}  signal={sig['signal'] if sig else 'none'}")
                if sig:
                    opt_type = "CE" if sig["signal"] == "long" else "PE"
                    res = dhan_master.get_option_contract(sym, sig["entry_spot"], opt_type,
                                                          int(tc.get("strike_offset", 0)))
                    if not res or not res[0]:
                        log.error(f"[ORB] {sym} {opt_type} contract not found"); time.sleep(30); continue
                    sec_id, trad_sym, lot_size = res
                    lots = int(tc.get("qty", 1))
                    ok, qty = _do_entry(strategy_id, sym, sec_id, trad_sym, lots, (lot_size or 1),
                                        mode, _order_broker(tc), opt_type, log)
                    if ok:
                        pos = dict(signal=sig["signal"], opt_type=opt_type, sec_id=sec_id,
                                   trad_sym=trad_sym, qty=qty, side="BUY",
                                   entry_spot=sig["entry_spot"], stop=sig["stop"], target=sig["target"])
                        trades_today += 1; save_state(strategy_id, pos)
                        log.info(f"  ★ ENTRY {sig['signal'].upper()} → BUY {opt_type} {trad_sym}  "
                                 f"spot={sig['entry_spot']:.1f} stop={sig['stop']:.1f} target={sig['target']:.1f}")

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[ORB] Stopped by user"); break
        except Exception as e:
            log.error(f"[ORB] Loop error: {e}", exc_info=True)
        time.sleep(30)      # frequent loop for responsive spot-based exits


def _do_entry(strategy_id, sym, sec_id, trad_sym, lots, lot_size, mode, broker, opt_type, log):
    """BUY ATM option via the gateway (RMS gate + order_store, Rule 6B/ADR-001)."""
    try:
        import execution_gateway as gw
        res = gw.execute_signal(strategy_id, sym, "BUY", lots, lot_size, sec_id, trad_sym,
                                seg="NSE_FNO", mode=mode, broker_name=broker, tag="ORB",
                                instrument="options", log=log.info)
        if res["ok"]:
            return True, res["qty"]
        log.info(f"  [ENTRY SKIP] {sym} — {res.get('status')}: {res.get('reason')}")
    except Exception as e:
        log.error(f"  [ENTRY ERR] {sym}: {e}")
    return False, 0


def _do_exit(strategy_id, sym, pos, mode, broker, reason, log):
    try:
        import execution_gateway as gw
        gw.execute_exit(strategy_id, sym, pos["sec_id"], pos["trad_sym"], pos["qty"],
                        entry_side=pos["side"], seg="NSE_FNO", mode=mode, broker_name=broker,
                        tag="ORB", instrument="options", reason=reason, log=log.info)
    except Exception as e:
        log.error(f"  [EXIT ERR] {sym}: {e} (pos_monitor SL/EOD still protects via order_store)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "15m"),
                "levels": {"win_start": tc.get("win_start"), "win_end": tc.get("win_end")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else (1 if pos["signal"] == "long" else -1),
                             "signal": "" if pos is None else pos["signal"],
                             "stop": None if pos is None else round(pos["stop"], 1),
                             "target": None if pos is None else round(pos["target"], 1)}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Mid-Day ORB Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="orb_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
