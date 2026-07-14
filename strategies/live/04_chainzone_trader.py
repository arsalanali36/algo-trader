#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  04_chainzone_trader.py — Strategy #04: Auto Rev-Chain Zone Breakout       ║
# ║  (user's Pine "Ars_Auto_Rev_Chain" ported + validated for NIFTY options)  ║
# ║  Research : scratch/nifty_trend/runs/chain_zone_longatm/ (p=0.000, Sh 1.95)║
# ║  Config   : ../../nifty_config.json  →  key: "chainzone_v1"                ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (NIFTY spot signal → ATM option BUY, long + short)                │
# │                                                                          │
# │  Lines (from PREVIOUS completed day, no lookahead):                      │
# │    RED (resistance): prevHigh + R1..R5 + chain of higher-highs (≤20d,    │
# │                      each higher than the last, ≤10% jump — index rule)  │
# │    GREEN (support) : prevLow  + S1..S5 + chain of lower-lows             │
# │    NEUTRAL         : Pivot P + prevClose                                 │
# │  Zone : a KEY candle (engulf / hammer / harami) that TOUCHES a line      │
# │         (±touch_tol pts) seeds a zone box (bearish@red / bullish@green). │
# │  Entry: within `zone_age` bars, next candle BREAKS OUT of the zone in    │
# │         the same colour + prev candle same colour + candle ≤ max_cs pts. │
# │         SHORT breakout → BUY PE.   LONG breakout → BUY CE.               │
# │  Exit : stop_only — spot ATR stop (atr_sl×ATR), NO target, ride to 3:15. │
# │  Max 2 trades/day. 1x, no leverage. skip-expiry handled by RMS guards.   │
# │                                                                          │
# │  compute_signal() replays the SAME state machine as the backtest         │
# │  (scratch/nifty_trend/intraday_engine._chain_zone_signals) so live ==    │
# │  backtest == Pine. Every order goes through execution_gateway (RMS +     │
# │  order_store) — NEW_STRATEGY_CHECKLIST compliant.                        │
# └─────────────────────────────────────────────────────────────────────────┘

import json
import logging
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
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
import _paths  # noqa: F401  — sys.path bootstrap (_core/_data/_ops flat imports)
import dhan_master
from _CHARTING.indicators import wilder_atr as _atr   # canonical (Rule 6B/ADR-002)

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
FORCE_EXIT   = (15, 15)
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
NIFTY_SEC_ID, NIFTY_SEG, NIFTY_INST = "13", "IDX_I", "INDEX"
_TF_MIN = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "7m": 7, "10m": 10, "15m": 15}

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
    "active": False, "mode": "paper", "timeframe": "5m",
    "chain_lookback": 20, "max_jump": 10.0, "touch_tol": 5.0,
    "zone_age": 2, "max_cs": 40.0, "hawa": False, "hawa_k": 3,
    "atr_period": 14, "atr_sl": 2.5,
    "qty": 1, "max_trades_per_day": 2, "strike_offset": 0, "symbol": "NIFTY",
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def fetch_nifty(token, cid, tf_min, days=10, use_cache=True):
    """Continuous `tf_min` NIFTY spot bars for the last `days` (chain needs prior
    days for pivots/chain levels + ATR warm-up, TRAP #85)."""
    if use_cache:
        try:
            import shared_candle_cache
            cached = shared_candle_cache.get(NIFTY_SEC_ID, tf_min, max_age=20.0)
            if cached:
                df = pd.DataFrame(cached); df["time"] = pd.to_datetime(df["time"])
                return df.dropna() if not df.empty else None
        except Exception:
            pass
    try:
        to = ist_now().date(); fr = to - timedelta(days=days)
        body = {"securityId": NIFTY_SEC_ID, "exchangeSegment": NIFTY_SEG,
                "instrument": NIFTY_INST, "interval": int(tf_min),
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
            shared_candle_cache.put(NIFTY_SEC_ID, tf_min, c.to_dict("records"))
        except Exception:
            pass
        return df
    except Exception:
        return None


# ─────────── chain-zone signal (EXACT port of intraday_engine._chain_zone_signals) ───────────
def _daily_levels(df, lookback=20, max_jump=10.0):
    """date -> dict(res, sup, neutral) from the PREVIOUS completed day (no lookahead).
    Mirrors scratch/nifty_trend/intraday_engine._daily_levels."""
    dd = df.copy()
    dd["day"] = dd["time"].dt.date
    daily = dd.groupby("day").agg(H=("high", "max"), L=("low", "min"),
                                  C=("close", "last")).reset_index()
    dates = list(daily["day"].values)
    Hs, Ls, Cs = daily.H.values, daily.L.values, daily.C.values
    out = {}
    for j in range(len(dates)):
        if j == 0:
            out[dates[j]] = None; continue
        ph, pl, pc = Hs[j - 1], Ls[j - 1], Cs[j - 1]
        P = (ph + pl + pc) / 3.0
        rng = ph - pl
        R1, S1 = 2 * P - pl, 2 * P - ph
        R2, S2 = P + rng, P - rng
        R3, S3 = ph + 2 * (P - pl), pl - 2 * (ph - P)
        R4, S4 = R3 + rng, S3 - rng
        R5, S5 = R4 + rng, S4 - rng
        res = [ph, R1, R2, R3, R4, R5]
        sup = [pl, S1, S2, S3, S4, S5]
        thr = ph
        for k in range(2, min(lookback, j) + 1):
            hh = Hs[j - k]
            if hh > thr and (hh - thr) / thr * 100.0 <= max_jump:
                res.append(hh); thr = hh
        thr = pl
        for k in range(2, min(lookback, j) + 1):
            ll = Ls[j - k]
            if ll < thr and (thr - ll) / thr * 100.0 <= max_jump:
                sup.append(ll); thr = ll
        out[dates[j]] = dict(res=res, sup=sup, neutral=[P, pc])
    return out


def _candle_patterns(df, min_body=0.5, wick_ratio=2.5):
    """Vectorised bullish/bearish key-candle flags — Pine parity (AA_CandlePatterns)."""
    O, H, L, C = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    body = np.abs(C - O)
    up_wick = H - np.maximum(O, C)
    lo_wick = np.minimum(O, C) - L
    green = C > O; red = C < O
    grn_ham = green & (body >= min_body) & (lo_wick >= wick_ratio * body) & (up_wick <= body)
    red_ham = red & (body >= min_body) & (lo_wick >= wick_ratio * body) & (up_wick <= body)
    inv_red = red & (body >= min_body) & (up_wick >= wick_ratio * body) & (lo_wick <= body)
    Op, Cp = np.roll(O, 1), np.roll(C, 1)
    prev_red, prev_grn = Cp < Op, Cp > Op
    body_p = np.abs(Cp - Op)
    bull_eng = green & prev_red & (body_p >= min_body) & (O <= Cp) & (C >= Op)
    bear_eng = red & prev_grn & (body_p >= min_body) & (O >= Cp) & (C <= Op)
    lo_c, hi_c = np.minimum(O, C), np.maximum(O, C)
    bull_har = green & prev_red & (lo_c >= Cp) & (hi_c <= Op)
    bear_har = red & prev_grn & (lo_c >= Op) & (hi_c <= Cp)
    if len(O):
        bull_har[0] = bear_eng[0] = bull_eng[0] = False
    bearish = bear_eng | bear_har | inv_red | red_ham
    bullish = bull_eng | bull_har | grn_ham
    return bullish, bearish


def compute_signal(df, cfg):
    """Replay the chain-zone state machine over the fetched bars; emit a signal only
    if the LAST CLOSED bar (index -2, last row = forming) fired. stop_only exit."""
    p = cfg
    period = int(p["atr_period"])
    if len(df) < period + 5:
        return None
    df = df.copy().reset_index(drop=True)
    atr = _atr(df, period).values
    levels = _daily_levels(df, int(p["chain_lookback"]), float(p["max_jump"]))
    bull, bear = _candle_patterns(df)
    O, H, L, C = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    day = df["time"].dt.date.values
    tol = float(p["touch_tol"]); max_age = int(p["zone_age"]); max_cs = float(p["max_cs"])
    hawa = bool(p["hawa"]); hawa_k = int(p["hawa_k"])
    n = len(df)
    sig_bar = n - 2                       # last CLOSED bar
    if sig_bar < 1:
        return None

    red_zone = green_zone = None
    last_res_bar = last_sup_bar = -10 ** 9
    fired = None
    for i in range(1, sig_bar + 1):
        lv = levels.get(day[i])
        if lv is None:
            continue
        lo, hi = L[i] - tol, H[i] + tol
        t_res = any(lo <= x <= hi for x in lv["res"])
        t_sup = any(lo <= x <= hi for x in lv["sup"])
        t_neu = any(lo <= x <= hi for x in lv["neutral"])
        if t_res:
            last_res_bar = i
        if t_sup:
            last_sup_bar = i
        at_res = t_res or t_neu or (hawa and i - last_res_bar <= hawa_k)
        at_sup = t_sup or t_neu or (hawa and i - last_sup_bar <= hawa_k)
        if bear[i] and at_res and not t_sup:
            red_zone = dict(lower=L[i], upper=H[i], bar=i)
        if bull[i] and at_sup and not t_res:
            green_zone = dict(lower=L[i], upper=H[i], bar=i)
        cs = H[i] - L[i]
        if (red_zone is not None and i > red_zone["bar"] and i - red_zone["bar"] <= max_age
                and C[i] < red_zone["lower"] and C[i] < O[i] and C[i - 1] < O[i - 1] and cs <= max_cs):
            if i == sig_bar:
                fired = "short"
            red_zone = None
        if (green_zone is not None and i > green_zone["bar"] and i - green_zone["bar"] <= max_age
                and C[i] > green_zone["upper"] and C[i] > O[i] and C[i - 1] > O[i - 1] and cs <= max_cs):
            if i == sig_bar:
                fired = "long"
            green_zone = None

    if fired is None:
        return None
    # only trade TODAY's closed bar (levels for older days are just warm-up)
    if day[sig_bar] != ist_now().date():
        return None
    atr_i = float(atr[sig_bar])
    if atr_i <= 0 or np.isnan(atr_i):
        return None
    entry = float(C[sig_bar])
    stop_dist = float(p["atr_sl"]) * atr_i
    stop = entry - stop_dist if fired == "long" else entry + stop_dist
    return dict(signal=fired, entry_spot=entry, atr=atr_i, stop=stop,
                bar_time=str(df["time"].iloc[sig_bar]))


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
    """Restore today's open position from disk only if order_store still shows it
    open at the broker (TRAP #28 — restart must not orphan/duplicate)."""
    pos = load_state(sid)
    if not pos:
        return None
    try:
        import order_store
        opens = order_store.trades_for(ist_now().strftime("%Y-%m-%d")).get("open") or []
        for pp in opens:
            if pp.get("strategy") == sid and str(pp.get("sec_id")) == str(pos.get("sec_id")):
                log.info(f"[RECOVER] re-attached open {pos.get('trad_sym')} stop={pos.get('stop'):.1f}")
                return pos
        log.info("[RECOVER] disk state had a position but order_store shows it closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="chainzone_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  04_chainzone_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos = _recover(strategy_id, log)
    trades_today = 0 if pos is None else 1
    last_date = ist_now().date()

    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            if paper_mode:
                mode = "paper"
            bname = _order_broker(tc)
            sym = tc.get("symbol", "NIFTY")
            tf_min = _TF_MIN.get(tc.get("timeframe", "5m"), 5)

            if last_date != now.date():
                last_date = now.date(); pos = None; trades_today = 0
                save_state(strategy_id, None); log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[CHAIN] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[CHAIN] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            df = fetch_nifty(token, cid, tf_min)
            if df is None or df.empty:
                log.warning("[CHAIN] no candle data"); time.sleep(30); continue
            spot = float(df["close"].iloc[-1])
            cur = df.iloc[-1]     # forming bar — its high/low = extremes so far

            # ── manage OPEN position: stop_only (spot ATR stop; NO target — ride to EOD) ──
            if pos is not None:
                hit = None
                if pos["signal"] == "long":
                    if float(cur["low"]) <= pos["stop"]:
                        hit = ("CHAIN_SL", pos["stop"])
                else:
                    if float(cur["high"]) >= pos["stop"]:
                        hit = ("CHAIN_SL", pos["stop"])
                if is_force_exit_time():
                    hit = ("EOD_315_SQUAREOFF", spot)
                if hit:
                    reason, lvl = hit
                    log.info(f"[EXIT] {pos['trad_sym']} — {reason} @ spot {lvl:.1f}")
                    _do_exit(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            if is_force_exit_time():
                time.sleep(60); continue

            # ── ENTRY ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 2)):
                if is_no_entry_time():
                    time.sleep(30); continue
                sig = compute_signal(df, tc)
                if sig:
                    # TRAP #108 finalized-bar guard: df may come from shared_candle_cache
                    # (intraday-built, <=20s stale, sometimes PRE-revision) so a marginal cross
                    # on an unrevised bar can become a phantom entry the backtest never took
                    # (proven 2026-07-13). Re-confirm on a FRESH cache-bypass Dhan pull first.
                    _fresh = fetch_nifty(token, cid, tf_min, use_cache=False)
                    if _fresh is not None and not _fresh.empty:
                        _sig2 = compute_signal(_fresh, tc)
                        _d1 = sig.get("direction") or sig.get("signal")
                        _d2 = (_sig2.get("direction") or _sig2.get("signal")) if _sig2 else None
                        if not _sig2 or _d2 != _d1:
                            log.info("[CHAIN] signal "+str(_d1)+" NOT confirmed on fresh Dhan candle - skip (TRAP#108 guard)")
                            sig = None
                        else:
                            sig = _sig2
                    else:
                        log.warning("[CHAIN] fresh Dhan candle unavailable - proceeding on cached signal (TRAP#108 fail-open)")
                log.info(f"[CHAIN] spot={spot:.1f}  pos=flat  trades={trades_today}  "
                         f"signal={sig['signal'] if sig else 'none'}")
                if sig:
                    opt_type = "CE" if sig["signal"] == "long" else "PE"
                    res = dhan_master.get_option_contract(sym, sig["entry_spot"], opt_type,
                                                          int(tc.get("strike_offset", 0)))
                    if not res or not res[0]:
                        log.error(f"[CHAIN] {sym} {opt_type} contract not found"); time.sleep(30); continue
                    sec_id, trad_sym, lot_size = res
                    lots = int(tc.get("qty", 1))
                    ok, qty = _do_entry(strategy_id, sym, sec_id, trad_sym, lots, (lot_size or 1),
                                        mode, bname, log)
                    if ok:
                        pos = dict(signal=sig["signal"], opt_type=opt_type, sec_id=sec_id,
                                   trad_sym=trad_sym, qty=qty, side="BUY",
                                   entry_spot=sig["entry_spot"], stop=sig["stop"])
                        trades_today += 1; save_state(strategy_id, pos)
                        log.info(f"  ★ ENTRY {sig['signal'].upper()} → BUY {opt_type} {trad_sym}  "
                                 f"spot={sig['entry_spot']:.1f} stop={sig['stop']:.1f} (ride-to-EOD)")

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[CHAIN] Stopped by user"); break
        except Exception as e:
            log.error(f"[CHAIN] Loop error: {e}", exc_info=True)
        time.sleep(30)      # frequent loop for responsive spot-based stop


def _do_entry(strategy_id, sym, sec_id, trad_sym, lots, lot_size, mode, broker, log):
    """BUY ATM option via the gateway (RMS gate + order_store, Rule 6B/ADR-001)."""
    try:
        import execution_gateway as gw
        res = gw.execute_signal(strategy_id, sym, "BUY", lots, lot_size, sec_id, trad_sym,
                                seg="NSE_FNO", mode=mode, broker_name=broker, tag="CHAIN",
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
                        tag="CHAIN", instrument="options", reason=reason, log=log.info)
    except Exception as e:
        log.error(f"  [EXIT ERR] {sym}: {e} (pos_monitor SL/EOD still protects via order_store)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "5m"),
                "levels": {"touch_tol": tc.get("touch_tol"), "zone_age": tc.get("zone_age"),
                           "max_cs": tc.get("max_cs")},
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
    ap = argparse.ArgumentParser(description="Strategy #04 — Auto Rev-Chain Zone Breakout")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="chainzone_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
