"""
universe_trader.py — best-in-class universe scanner engine.

Scans a whole universe (Nifty-50) on real-time Dhan Data API candles, runs a
pluggable strategy on each symbol, and on a signal fires a marketable-limit
order routed to: cash equity / the stock's option / an index option.

- Candles  : Dhan Data API (broker.intraday_candles).
- Execution: smart_order (BUY=ask, SELL=bid) via WebSocket bid/ask feed.
- Paper == Live: identical intended-fill logging; live also fires real order.
- Caps     : max_concurrent_positions + max_trades_per_symbol.

Run:  python universe_trader.py --paper --id universe_v1
Dashboard spawns it the same way as other traders; log format is compatible
with the existing P&L parser.
"""

import argparse
import json
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- IPv4 force (DH-905) ---
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

import dhan_feed
import smart_order
import strategies
import universe
from brokers import get_broker

BASE_DIR = Path(__file__).resolve().parent
TC_FILE  = BASE_DIR / "nifty_config.json"

TF_INTERVAL = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}
TF_SECS     = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800}

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
EXIT_TIME    = (15, 15)


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)


def is_market_open():
    n = ist_now()
    if n.weekday() >= 5:
        return False
    return MARKET_OPEN <= (n.hour, n.minute) < MARKET_CLOSE


def is_exit_time():
    n = ist_now()
    return (n.hour, n.minute) >= EXIT_TIME


# ---------------- config / state ----------------
DEFAULT_CFG = {
    "universe": "nifty50", "strategy": "sample_ema", "route": "equity",
    "index": "NIFTY", "broker": "dhan", "order_style": "marketable_limit",
    "limit_buffer_bps": 10, "max_concurrent_positions": 5,
    "max_trades_per_symbol": 2, "qty": 1, "lots": 1, "strike_offset": 0,
    "timeframe": "1m", "fast_ema": 9, "slow_ema": 20,
}

_state = {}   # symbol -> {position, trades_today, open_inst:(sec_id,seg,trad_sym,qty)}


def load_config(sid):
    try:
        allc = json.loads(TC_FILE.read_text())
        if sid in allc:
            return {**DEFAULT_CFG, **allc[sid]}
    except Exception:
        pass
    return dict(DEFAULT_CFG)


def get_state(sym):
    if sym not in _state:
        _state[sym] = {"position": None, "trades_today": 0, "open_inst": None}
    return _state[sym]


def reset_daily():
    for s in list(_state.keys()):
        _state[s] = {"position": None, "trades_today": 0, "open_inst": None}


def n_open():
    return sum(1 for s in _state.values() if s["position"] is not None)


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S},000  INFO      {msg}", flush=True)


# ---------------- routing ----------------
def resolve_route(symbol, signal, spot, cfg):
    """Return (sec_id, seg, trad_sym) for where the order goes, per cfg['route'].
    signal BUY -> bullish (CE / buy equity); SELL -> bearish (PE / sell equity)."""
    route = cfg.get("route", "equity")
    off   = int(cfg.get("strike_offset", 0))
    if route == "equity":
        sid = universe.equity_secid(symbol)
        return (sid, "NSE_EQ", symbol) if sid else (None, None, None)
    if route == "stock_option":
        opt = "CE" if signal == "BUY" else "PE"
        sid, tsym = universe.stock_option_atm(symbol, spot, opt, off)
        return (sid, "NSE_FNO", tsym) if sid else (None, None, None)
    if route == "index_option":
        idx = cfg.get("index", "NIFTY")
        opt = "CE" if signal == "BUY" else "PE"
        sid, tsym = universe.index_option_atm(idx, spot, opt, off)
        return (sid, "NSE_FNO", tsym) if sid else (None, None, None)
    return (None, None, None)


def order_side_for(signal, route):
    """For equity we go long/short directly. For options we BUY the premium
    (CE for bullish, PE for bearish)."""
    if route == "equity":
        return signal  # BUY or SELL
    return "BUY"        # buying option premium


# ---------------- main ----------------
def main(sid, once=False):
    mode = "live" if "--live" in sys.argv else "paper"
    broker = None
    creds = json.loads((BASE_DIR / "data" / "config.json").read_text())
    last_day = None
    feed_started = False
    log(f"[BOOT] universe_trader id={sid} mode={mode} once={once}")

    while True:
        n = ist_now()
        if n.date() != last_day:
            reset_daily()
            last_day = n.date()

        if not once and not is_market_open():
            time.sleep(60)
            continue

        cfg = load_config(sid)
        if broker is None:
            broker = get_broker(cfg.get("broker", "dhan"), creds)

        symbols = universe.resolve_universe(cfg.get("universe", "nifty50"))
        eqmap = universe.equity_secids(symbols)   # symbol -> equity sec_id

        # start feed once (all equity sec_ids for live bid/ask)
        if not feed_started:
            dhan_feed.start(creds, [("NSE_EQ", s) for s in eqmap.values()])
            feed_started = True
            log(f"[FEED] subscribed {len(eqmap)} equities")

        # 3:15 exit-all
        if not once and is_exit_time():
            for sym, st in _state.items():
                if st["position"] and st["open_inst"]:
                    s_id, seg, tsym, qty = st["open_inst"]
                    ex_side = "SELL" if st["position"] == "LONG" else "BUY"
                    smart_order.execute(ex_side, sym, s_id, seg, qty, tsym, mode,
                                        broker, cfg.get("limit_buffer_bps", 10),
                                        log=log, tag="EXIT")
                    st["position"] = None
                    st["open_inst"] = None
            log("[EXIT] 3:15 square-off done")
            time.sleep(120)
            continue

        tf = cfg.get("timeframe", "1m")
        interval = TF_INTERVAL.get(tf, 1)
        strat = strategies.load(cfg.get("strategy", "sample_ema"))
        max_conc = int(cfg.get("max_concurrent_positions", 5))
        max_tr   = int(cfg.get("max_trades_per_symbol", 2))
        qty      = int(cfg.get("qty", 1)) * int(cfg.get("lots", 1))

        for sym, eq_sid in eqmap.items():
            st = get_state(sym)
            try:
                df = broker.intraday_candles(eq_sid, "NSE_EQ", "EQUITY",
                                             days=3, interval=interval)
            except Exception as e:
                continue
            if df is None or df.empty:
                continue
            spot = float(df.iloc[-1]["close"])
            sig = strat.evaluate(df, cfg, st["position"])
            if not sig:
                continue

            # EXIT
            if sig == "EXIT" and st["position"] and st["open_inst"]:
                s_id, seg, tsym, oq = st["open_inst"]
                ex_side = "SELL" if st["position"] == "LONG" else "BUY"
                smart_order.execute(ex_side, sym, s_id, seg, oq, tsym, mode,
                                    broker, cfg.get("limit_buffer_bps", 10),
                                    log=log, tag="EXIT")
                st["position"] = None
                st["open_inst"] = None
                continue

            if sig not in ("BUY", "SELL"):
                continue

            want = "LONG" if sig == "BUY" else "SHORT"
            if st["position"] == want:
                continue  # already in that direction
            if st["trades_today"] >= max_tr:
                continue
            if st["position"] is None and n_open() >= max_conc:
                continue  # concurrency cap (don't block flips of existing pos)

            # flip: close existing opposite first
            if st["position"] and st["open_inst"]:
                s_id, seg, tsym, oq = st["open_inst"]
                ex_side = "SELL" if st["position"] == "LONG" else "BUY"
                smart_order.execute(ex_side, sym, s_id, seg, oq, tsym, mode,
                                    broker, cfg.get("limit_buffer_bps", 10),
                                    log=log, tag="FLIP")
                st["position"] = None
                st["open_inst"] = None

            # open new
            route = cfg.get("route", "equity")
            sec_id, seg, tsym = resolve_route(sym, sig, spot, cfg)
            if not sec_id:
                log(f"[SKIP] {sym} {sig} — route '{route}' unresolved")
                continue
            o_side = order_side_for(sig, route)
            r = smart_order.execute(o_side, sym, sec_id, seg, qty, tsym, mode,
                                    broker, cfg.get("limit_buffer_bps", 10),
                                    log=log, tag="UNIV")
            if r.get("ok"):
                st["position"] = want
                st["open_inst"] = (sec_id, seg, tsym, qty)
                st["trades_today"] += 1

        if once:
            log(f"[ONCE] scan complete — open positions: {n_open()}")
            return
        time.sleep(TF_SECS.get(tf, 60))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--once", action="store_true", help="single test scan, ignore market hours")
    ap.add_argument("--id", default="universe_v1")
    args = ap.parse_args()
    main(args.id, once=args.once)
