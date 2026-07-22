#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  orb_overnight_trader.py  —  Overnight ORB  (hold to next-day 09:20)        ║
# ║  Research : scratch/nifty_trend/ overnight variant — p=0.000, MC not-overfit║
# ║           : train Sh 1.78 / OOS 1.38 (BS monthly ATM buy), slip-4x robust   ║
# ║  Config   : ../../nifty_config.json  →  key: "orb_overnight_v1"             ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
#  SAME ENTRY as orb_trader.py (Mid-Day ORB breakout, buy ATM CE/PE) — the ONLY
#  differences (Rule 10, matches the backtested overnight variant EXACTLY):
#    • NO 3:15 force-exit. Entry-day: ATR stop active (spot). NO RR target.
#    • If the ATR stop is NOT hit by end of the entry day → HOLD OVERNIGHT.
#    • Next trading day at ≥ 09:20 IST → square off (settled open, not 09:15 tick).
#    • Positional durability (TRAP #119): state + recovery survive across days;
#      config_key MUST be in risk_gate._ALWAYS_OVERNIGHT so pos_monitor's 3:15
#      squareoff skips it.
#
#  INSTRUMENT — MONTHLY ATM (matches backtest, Rule 10): buys the near-month MONTHLY
#  expiry via dhan_master.get_monthly_option_contract, NOT the nearest weekly. The
#  backtest priced a monthly ATM buy (bs_option.reprice_positional / _next_monthly_expiry),
#  so a 1-night hold never crosses a weekly 0DTE. A weekly held overnight would bleed
#  far more theta AND die on its own expiry day; monthly sidesteps both. (Residual: on
#  the ~1 monthly-expiry day/month, an entry can't hold a dead contract overnight —
#  pos_monitor's EXPIRY squareoff force-closes it same-day; backtest models that as
#  intrinsic roll. Rare, safe direction.)
#
#  BS-vs-real caveat (TRAP #136): backtest P&L is Black-Scholes monthly ATM BUY;
#  an option BUYER holding overnight still bleeds theta BS under-prices. Deployed PAPER —
#  watch the dashboard Real-vs-BS compare on live fills before any real capital.

import json
import logging
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# IPv4 force — VPS IPv6 → Dhan DH-905
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR    = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "nifty_config.json"

sys.path.insert(0, str(BASE_DIR))
import _paths  # noqa: F401  (sys.path bootstrap)
import dhan_master
from _CHARTING.indicators import wilder_atr as _atr   # canonical, matches backtest (ADR-002)
from strategies.signals import orb as _orb   # SINGLE-SOURCE ORB signal (same code the backtest runs)

MARKET_OPEN   = (9, 16)
MARKET_CLOSE  = (15, 25)
NEXTDAY_EXIT  = (9, 20)          # carried overnight position exits here next trading day
INTRADAY_URL  = "https://api.dhan.co/v2/charts/intraday"
NIFTY_SEC_ID, NIFTY_SEG, NIFTY_INST = "13", "IDX_I", "INDEX"
STATE_FILE = lambda sid: BASE_DIR / "data" / f"{sid}_state.json"


def _make_logger(strategy_id):
    log_file = BASE_DIR / "logs" / f"{strategy_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(strategy_id)
    lg.setLevel(logging.INFO); lg.propagate = False
    if not lg.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_file); fh.setFormatter(fmt); lg.addHandler(fh)
        if getattr(sys.stdout, "isatty", lambda: False)():
            sh = logging.StreamHandler(); sh.setFormatter(fmt); lg.addHandler(sh)
    return lg


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def is_market_open():
    t = (ist_now().hour, ist_now().minute)
    return MARKET_OPEN <= t < MARKET_CLOSE

def _exit_times():
    """(force_exit_hm, no_entry_hm) from RMS single-source — used ONLY to block LATE
    entries (never for a 3:15 squareoff; this strategy holds overnight)."""
    try:
        import risk_gate as _rg
        return _rg.exit_time_config()
    except Exception:
        return (15, 15), (15, 15)

def is_no_entry_time():
    return (ist_now().hour, ist_now().minute) >= _exit_times()[1]

def is_nextday_exit_time():
    return (ist_now().hour, ist_now().minute) >= NEXTDAY_EXIT

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "timeframe": "15m",
    "or_min": 30, "orb_k": 1.0, "atr_period": 14, "atr_sl": 1.5,
    "win_start": "11:00", "win_end": "14:00",          # matches backtest (h0=11, h1=14)
    "qty": 1, "max_trades_per_day": 2, "strike_offset": 0, "symbol": "NIFTY",
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)

def _hm(s):
    try:
        h, m = str(s).split(":"); return (int(h), int(m))
    except Exception:
        return (11, 0)


def fetch_nifty_15m(token, cid, days=5, use_cache=True):
    """Continuous 15m NIFTY spot bars (ATR warm-up, TRAP #85)."""
    if use_cache:
        try:
            import shared_candle_cache
            cached = shared_candle_cache.get(NIFTY_SEC_ID, 15, days, max_age=20.0)
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
        tt = df["time"].dt.time
        df = df[(df["time"].dt.weekday < 5) &
                (tt >= datetime(2000, 1, 1, 9, 15).time()) &
                (tt <= datetime(2000, 1, 1, 15, 29).time())].reset_index(drop=True)
        if df.empty:
            return None
        try:
            import shared_candle_cache
            c = df.copy(); c["time"] = c["time"].astype(str)
            shared_candle_cache.put(NIFTY_SEC_ID, 15, days, c.to_dict("records"))
        except Exception:
            pass
        return df
    except Exception:
        return None


def compute_signal(df, cfg):
    """ORB breakout on TODAY's opening range + mid-day window. Returns dict with
    signal/entry_spot/atr/stop (NO target — overnight variant exits next-day 09:20).

    SIGNAL = the SINGLE-SOURCE `orb.orb_signal_last` (tod_orb — WITH the h0/h1 window) —
    the EXACT backtest signal (build_overnight_orb.py runs
    intraday_engine.design_signals('tod_orb'), which also calls this), so a live entry
    fires iff the backtest fired on that bar. Removes the OR-boundary (`<` vs `<=`) and
    previous-vs-current-bar-ATR crossover drift the old inline port had. Only the ATR
    stop below is exit sizing (not signal)."""
    p = cfg
    period = int(p["atr_period"])
    if len(df) < period + 3:
        return None
    df = df.copy().reset_index(drop=True)
    df["atr"] = _atr(df, period)
    today = ist_now().date()
    # config → shared param names. Window hours from win_start/win_end (backtest tod_orb
    # uses hour-granularity h0/h1; orb_overnight_v1 = 11:00/14:00).
    ws, we = _hm(p["win_start"]), _hm(p["win_end"])
    sig_params = dict(or_min=int(p["or_min"]), orb_k=float(p["orb_k"]),
                      atr_period=period, h0=ws[0], h1=we[0])
    side = _orb.orb_signal_last(df, sig_params, dt_col="time", hi="high", lo="low", cl="close")
    if not side:
        return None
    i = len(df) - 2                        # last CLOSED bar
    if i < 1 or df.iloc[i]["time"].date() != today:
        return None
    atr_i = float(df["atr"].iloc[i])
    if atr_i <= 0:
        return None
    entry_spot = float(df["close"].iloc[i])
    stop_dist = float(p["atr_sl"]) * atr_i
    stop = entry_spot - stop_dist if side == "long" else entry_spot + stop_dist
    return dict(signal=side, entry_spot=entry_spot, atr=atr_i, stop=stop,
                bar_time=str(df.iloc[i]["time"]))


# ───────────── positional state (survives across days — TRAP #119) ─────────────
def save_state(sid, pos):
    try:
        STATE_FILE(sid).write_text(json.dumps({"pos": pos}))
    except Exception:
        pass

def load_state(sid):
    try:
        return json.loads(STATE_FILE(sid).read_text()).get("pos")
    except Exception:
        return None

def _recover(sid, log):
    """Restore an open position (possibly opened a PRIOR day) if order_store still
    shows it open — range lookback, not today-only (positional, TRAP #119)."""
    pos = load_state(sid)
    if not pos:
        return None
    try:
        import order_store
        today = ist_now().strftime("%Y-%m-%d")
        start = (ist_now() - timedelta(days=7)).strftime("%Y-%m-%d")
        opens = (order_store.trades_for_range(start, today) or {}).get("open") or []
        for p in opens:
            if p.get("strategy") == sid and str(p.get("sec_id")) == str(pos.get("sec_id")):
                log.info(f"[RECOVER] re-attached overnight {pos.get('trad_sym')} "
                         f"entry_date={pos.get('entry_date')} stop={pos.get('stop')}")
                return pos
        log.info("[RECOVER] disk state had a position but order_store shows it closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="orb_overnight_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting")
        return
    log.info("=" * 62)
    log.info(f"  orb_overnight_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos = _recover(strategy_id, log)
    import risk_gate as _rg_cnt
    _et = _rg_cnt.entries_today(strategy_id)
    trades_today = _et if _et is not None else (0 if pos is None else 1)
    last_date = ist_now().date()

    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else (tc.get("mode", "paper"))

            if last_date != now.date():
                # NEW day. Do NOT null an overnight-carried position — it must be
                # squared off at ≥09:20 below. Only the day-scoped trade counter resets.
                last_date = now.date(); trades_today = 0
                log.info(f"── New day: {last_date} ── (carry={pos.get('trad_sym') if pos else 'none'})")

            if not tc.get("active", False):
                log.info("[ORBO] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[ORBO] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            sym = tc.get("symbol", "NIFTY")
            token, cid = load_creds()
            df = fetch_nifty_15m(token, cid)
            if df is None or df.empty:
                log.warning("[ORBO] no candle data"); time.sleep(30); continue
            spot = float(df["close"].iloc[-1])
            cur = df.iloc[-1]

            # ── manage OPEN position ──
            if pos is not None:
                carried = str(pos.get("entry_date")) != str(now.date())
                hit = None
                if carried:
                    # overnight carry → exit at ≥09:20 next trading day (settled open)
                    if is_nextday_exit_time():
                        hit = ("ORB_OVN_NEXTDAY", spot)
                else:
                    # same-day: ATR stop active (spot), NO target, NO 3:15 exit
                    if pos["signal"] == "long" and float(cur["low"]) <= pos["stop"]:
                        hit = ("ORB_SL", pos["stop"])
                    elif pos["signal"] == "short" and float(cur["high"]) >= pos["stop"]:
                        hit = ("ORB_SL", pos["stop"])
                if hit:
                    reason, lvl = hit
                    log.info(f"[EXIT] {pos['trad_sym']} — {reason} @ spot {lvl:.1f}")
                    _do_exit(strategy_id, sym, pos, mode, _order_broker(tc), reason, log)
                    pos = None; save_state(strategy_id, None)

            # ── ENTRY (only when flat; mid-day window; not past no-entry time) ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 2)):
                if is_no_entry_time():
                    time.sleep(30); continue
                sig = compute_signal(df, tc)
                if sig:
                    _fresh = fetch_nifty_15m(token, cid, use_cache=False)   # TRAP #108 confirm
                    if _fresh is not None and not _fresh.empty:
                        _s2 = compute_signal(_fresh, tc)
                        if not _s2 or _s2["signal"] != sig["signal"]:
                            log.info(f"[ORBO] signal {sig['signal']} NOT confirmed on fresh candle — skip (TRAP#108)")
                            sig = None
                        else:
                            sig = _s2
                    else:
                        log.warning("[ORBO] fresh candle unavailable — proceeding on cached (TRAP#108 fail-open)")
                log.info(f"[ORBO] spot={spot:.1f}  window={tc['win_start']}-{tc['win_end']}  "
                         f"pos=flat  trades={trades_today}  signal={sig['signal'] if sig else 'none'}")
                if sig:
                    opt_type = "CE" if sig["signal"] == "long" else "PE"
                    # MONTHLY ATM (not nearest weekly) — matches the backtest's
                    # reprice_positional monthly-ATM pricing so live == validated numbers
                    # (Rule 10). A weekly held overnight bleeds far more theta AND dies on
                    # expiry day; monthly avoids both (see LESSONS / TRAP #136).
                    res = dhan_master.get_monthly_option_contract(sym, sig["entry_spot"], opt_type,
                                                                  int(tc.get("strike_offset", 0)))
                    if not res or not res[0]:
                        log.error(f"[ORBO] {sym} {opt_type} contract not found"); time.sleep(30); continue
                    sec_id, trad_sym, lot_size = res
                    lots = int(tc.get("qty", 1))
                    ok, qty = _do_entry(strategy_id, sym, sec_id, trad_sym, lots, (lot_size or 1),
                                        mode, _order_broker(tc), opt_type, log)
                    if ok:
                        pos = dict(signal=sig["signal"], opt_type=opt_type, sec_id=sec_id,
                                   trad_sym=trad_sym, qty=qty, side="BUY",
                                   entry_spot=sig["entry_spot"], stop=sig["stop"],
                                   entry_date=str(now.date()))
                        trades_today += 1; save_state(strategy_id, pos)
                        log.info(f"  ★ ENTRY {sig['signal'].upper()} → BUY {opt_type} {trad_sym}  "
                                 f"spot={sig['entry_spot']:.1f} stop={sig['stop']:.1f}  (hold overnight)")

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[ORBO] Stopped by user"); break
        except Exception as e:
            log.error(f"[ORBO] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _do_entry(strategy_id, sym, sec_id, trad_sym, lots, lot_size, mode, broker, opt_type, log):
    try:
        import execution_gateway as gw
        res = gw.execute_signal(strategy_id, sym, "BUY", lots, lot_size, sec_id, trad_sym,
                                seg="NSE_FNO", mode=mode, broker_name=broker, tag="ORB_OVN",
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
                        tag="ORB_OVN", instrument="options", reason=reason, log=log.info)
    except Exception as e:
        log.error(f"  [EXIT ERR] {sym}: {e} (pos_monitor still protects via order_store)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "15m"),
                "levels": {"win_start": tc.get("win_start"), "win_end": tc.get("win_end")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else (1 if pos["signal"] == "long" else -1),
                             "signal": "" if pos is None else pos["signal"],
                             "stop": None if pos is None else round(pos["stop"], 1),
                             "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Overnight ORB Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="orb_overnight_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
