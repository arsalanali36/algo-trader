#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  02_debit_vertical_trader.py — Strategy #02: Debit Vertical @ ORB (V1)     ║
# ║  Research : scratch/nifty_trend/runs/debit_vertical_orb/ (p=0.000)         ║
# ║  Config   : ../../nifty_config.json  →  key: "dvert_v1"                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (NIFTY spot signal → 2-leg directional debit spread)               │
# │                                                                          │
# │  Opening range : high/low of first `or_min` mins (from 09:15)            │
# │  UP breakout   : close > OR_high + orb_k×ATR → BULL CALL spread:          │
# │                  BUY ATM CE + SELL CE `wing_off` strikes OTM              │
# │  DOWN breakout : close < OR_low − orb_k×ATR → BEAR PUT spread:            │
# │                  BUY ATM PE + SELL PE `wing_off` strikes OTM              │
# │  EXIT          : net-debit % — target at +tp_frac of the net debit paid,  │
# │                  stop at −sl_frac; else 3:15 EOD. Defined risk = debit.   │
# │  Max 2 trades/day. 1x. The SELL wing is COVERED by the long ATM leg —     │
# │  BUY leg fills FIRST (short never naked); exit closes the SHORT first.    │
# │                                                                          │
# │  Every leg via execution_gateway (RMS gate + order_store); wing leg      │
# │  gate=False (protective/covered — blocking it would orphan the long).     │
# │  Both legs share a group_id. NEW_STRATEGY_CHECKLIST compliant.            │
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
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "nifty_config.json"

sys.path.insert(0, str(BASE_DIR))
import _paths  # noqa: F401  — sys.path bootstrap (_core/_data/_ops flat imports)
import dhan_master
from _CHARTING.indicators import wilder_atr as _atr   # canonical (Rule 6B/ADR-002)

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
FORCE_EXIT   = (15, 15)
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
NIFTY_SEC_ID, NIFTY_SEG, NIFTY_INST = "13", "IDX_I", "INDEX"
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}

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
    "active": False, "mode": "paper", "timeframe": "15m",
    "or_min": 15, "orb_k": 1.0, "atr_period": 14,
    "wing_off": 10, "tp_frac": 1.0, "sl_frac": 1.0,
    "qty": 1, "max_trades_per_day": 2, "symbol": "NIFTY",
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def fetch_nifty(token, cid, tf_min, days=5, use_cache=True):
    """Continuous `tf_min` NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85)."""
    if use_cache:
        try:
            import shared_candle_cache
            cached = shared_candle_cache.get(NIFTY_SEC_ID, tf_min, days, max_age=20.0)
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
            shared_candle_cache.put(NIFTY_SEC_ID, tf_min, days, c.to_dict("records"))
        except Exception:
            pass
        return df
    except Exception:
        return None


def compute_breakout(df, cfg):
    """Directional ORB breakout on the last CLOSED bar. Returns dict(direction, spot, atr)
    or None. Matches intraday_engine 'orb_break' used by the debit-vertical backtest."""
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
    # backtest (intraday_engine 'orb') uses cutoff `tt <= or_end` — the bar LABELLED
    # or_end is part of the opening range, entries strictly after it. Match exactly.
    or_bars = tday[tday["time"].dt.time <= or_end]
    if or_bars.empty:
        return None
    or_high = float(or_bars["high"].max()); or_low = float(or_bars["low"].min())

    i = len(df) - 2   # last CLOSED bar (last row = forming)
    if i < 1:
        return None
    bar = df.iloc[i]
    if bar["time"].date() != today:
        return None
    if bar["time"].time() <= or_end:
        return None

    k = float(p["orb_k"]); atr_i = float(df["atr"].iloc[i]); atr_p = float(df["atr"].iloc[i - 1])
    if atr_i <= 0:
        return None
    up_i, up_p = or_high + k * atr_i, or_high + k * atr_p
    lo_i, lo_p = or_low - k * atr_i, or_low - k * atr_p
    c_i, c_p = float(df["close"].iloc[i]), float(df["close"].iloc[i - 1])
    direction = None
    if c_i > up_i and c_p <= up_p:
        direction = "up"
    elif c_i < lo_i and c_p >= lo_p:
        direction = "down"
    if not direction:
        return None
    return dict(direction=direction, spot=c_i, atr=atr_i, bar_time=str(bar["time"]))


def _opt_ltp(broker, sec_id):
    try:
        q = broker.quote(sec_id, "NSE_FNO")
        v = q.get("ltp") if isinstance(q, dict) else None
        return float(v) if v else None
    except Exception:
        return None


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
    """Restore today's open spread from disk, but ONLY if order_store still shows
    BOTH legs open at the broker (TRAP #28 — restart must not orphan/duplicate)."""
    pos = load_state(sid)
    if not pos or not pos.get("legs"):
        return None
    try:
        import order_store
        opens = order_store.trades_for(ist_now().strftime("%Y-%m-%d")).get("open") or []
        open_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        want = {str(l["sec_id"]) for l in pos["legs"]}
        if want.issubset(open_secs):
            log.info(f"[RECOVER] re-attached open spread {[l['trad_sym'] for l in pos['legs']]}")
            return pos
        log.info("[RECOVER] disk state had a spread but order_store shows leg(s) closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="dvert_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  02_debit_vertical_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos = _recover(strategy_id, log)
    # Restart-safe day count: order_store is the durable record. The old
    # `0 if pos is None else 1` silently reset the day's count to 0 on any
    # restart that happened AFTER the position closed, handing the strategy its
    # whole max-trades quota again (seen live 2026-07-16 on orbst). None = can't
    # read -> fall back to the old in-memory guess rather than assume 0.
    import risk_gate as _rg_cnt   # local: not every trader imports risk_gate at module level
    _et = _rg_cnt.entries_today(strategy_id)
    trades_today = _et if _et is not None else (0 if pos is None else 1)
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
            tf_min = _TF_MIN.get(tc.get("timeframe", "15m"), 15)

            if last_date != now.date():
                last_date = now.date(); pos = None; trades_today = 0
                save_state(strategy_id, None); log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[DVERT] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[DVERT] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            df = fetch_nifty(token, cid, tf_min)
            if df is None or df.empty:
                log.warning("[DVERT] no candle data"); time.sleep(30); continue
            spot = float(df["close"].iloc[-1])

            # ── manage OPEN spread: net-debit % exit ──
            if pos is not None:
                broker = _get_broker(bname)
                l_long = next((l for l in pos["legs"] if l["side"] == "BUY"), None)
                l_short = next((l for l in pos["legs"] if l["side"] == "SELL"), None)
                reason = None
                if broker and l_long and l_short:
                    ltp_l = _opt_ltp(broker, l_long["sec_id"])
                    ltp_s = _opt_ltp(broker, l_short["sec_id"])
                    if ltp_l is not None and ltp_s is not None:
                        net_now = ltp_l - ltp_s
                        net_ent = pos["entry_net"]
                        pnl_frac = (net_now - net_ent) / net_ent if net_ent > 0 else 0.0
                        log.info(f"[DVERT] spread open  net {net_ent:.1f}→{net_now:.1f}  "
                                 f"P&L {pnl_frac*100:+.0f}%  (tp +{int(pos['tp_frac']*100)}% / sl -{int(pos['sl_frac']*100)}%)")
                        if pnl_frac >= pos["tp_frac"]:
                            reason = "DVERT_TP"
                        elif pnl_frac <= -pos["sl_frac"]:
                            reason = "DVERT_SL"
                if is_force_exit_time():
                    reason = "EOD_315_SQUAREOFF"
                if reason:
                    log.info(f"[EXIT] spread — {reason}")
                    _exit_spread(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            if is_force_exit_time():
                time.sleep(60); continue

            # ── ENTRY: on breakout, bull-call / bear-put spread ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 2)):
                if is_no_entry_time():
                    time.sleep(30); continue
                sig = compute_breakout(df, tc)
                if sig:
                    # TRAP #108 finalized-bar guard: df may come from shared_candle_cache
                    # (intraday-built, <=20s stale, sometimes PRE-revision) so a marginal cross
                    # on an unrevised bar can become a phantom entry the backtest never took
                    # (proven 2026-07-13). Re-confirm on a FRESH cache-bypass Dhan pull first.
                    _fresh = fetch_nifty(token, cid, tf_min, use_cache=False)
                    if _fresh is not None and not _fresh.empty:
                        _sig2 = compute_breakout(_fresh, tc)
                        _d1 = sig.get("direction") or sig.get("signal")
                        _d2 = (_sig2.get("direction") or _sig2.get("signal")) if _sig2 else None
                        if not _sig2 or _d2 != _d1:
                            log.info("[DVERT] signal "+str(_d1)+" NOT confirmed on fresh Dhan candle - skip (TRAP#108 guard)")
                            sig = None
                        else:
                            sig = _sig2
                    else:
                        log.warning("[DVERT] fresh Dhan candle unavailable - proceeding on cached signal (TRAP#108 fail-open)")
                log.info(f"[DVERT] spot={spot:.1f}  pos=flat  trades={trades_today}  "
                         f"breakout={sig['direction'] if sig else 'none'}")
                if sig:
                    pos = _enter_spread(strategy_id, sym, spot, sig["direction"], tc, mode, bname, log)
                    if pos:
                        trades_today += 1; save_state(strategy_id, pos)

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[DVERT] Stopped by user"); break
        except Exception as e:
            log.error(f"[DVERT] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _get_broker(bname):
    try:
        import risk_gate
        from brokers import get_broker
        return get_broker(str(bname or risk_gate.default_broker() or "dhan").lower())
    except Exception:
        return None


def _enter_spread(strategy_id, sym, spot, direction, tc, mode, bname, log):
    """Bull-call (up) / bear-put (down) debit spread via the gateway.
    BUY ATM leg FIRST (gated) — only then SELL the wing (gate=False, covered by the
    long; blocking the wing would leave a lone long, so on wing failure we roll the
    long back). Returns pos dict or None."""
    import execution_gateway as gw
    lots = int(tc.get("qty", 1))
    wing = int(tc.get("wing_off", 10))
    opt_type = "CE" if direction == "up" else "PE"
    gid = f"DVERT_{int(time.time())}"
    legs = []

    # leg 1 — BUY ATM (the risk carrier; RMS-gated)
    res_c = dhan_master.get_option_contract(sym, spot, opt_type, 0)
    if not res_c or not res_c[0]:
        log.error(f"[DVERT] {sym} ATM {opt_type} contract not found — abort"); return None
    sec_id, trad_sym, lot_size = res_c
    try:
        res = gw.execute_signal(strategy_id, sym, "BUY", lots, (lot_size or 1), sec_id, trad_sym,
                                seg="NSE_FNO", mode=mode, broker_name=bname, tag="DVERT",
                                instrument="options", group_id=gid, log=log.info)
    except Exception as e:
        log.error(f"[DVERT] ATM BUY error: {e}"); return None
    if not res["ok"]:
        log.info(f"[DVERT] ATM BUY not ok — {res.get('status')}: {res.get('reason')}"); return None
    prem_l = res.get("price")
    if not prem_l or prem_l <= 0:
        prem_l = _opt_ltp(_get_broker(bname), sec_id) or 0.0
    legs.append(dict(side="BUY", opt_type=opt_type, sec_id=sec_id, trad_sym=trad_sym,
                     qty=res["qty"], entry_prem=round(float(prem_l), 2)))

    # leg 2 — SELL wing (covered by leg 1; gate=False like a protective leg,
    # qty matched to the FILLED long qty so the short is never oversized)
    res_w = dhan_master.get_option_contract(sym, spot, opt_type, wing)
    if not res_w or not res_w[0] or str(res_w[0]) == str(sec_id):
        log.error(f"[DVERT] wing {opt_type} (+{wing}) contract not found/same-strike — rollback")
        _rollback(strategy_id, sym, legs, mode, bname, log); return None
    wsec, wsym, wlot = res_w
    wing_lots = max(1, int(legs[0]["qty"] // int(wlot or lot_size or 1)))
    try:
        resw = gw.execute_signal(strategy_id, sym, "SELL", wing_lots, (wlot or lot_size or 1),
                                 wsec, wsym, seg="NSE_FNO", mode=mode, broker_name=bname,
                                 tag="DVERT", instrument="options", group_id=gid,
                                 gate=False, log=log.info)
    except Exception as e:
        log.error(f"[DVERT] wing SELL error: {e}")
        _rollback(strategy_id, sym, legs, mode, bname, log); return None
    if not resw["ok"]:
        log.info(f"[DVERT] wing SELL not ok — {resw.get('status')}: {resw.get('reason')}")
        _rollback(strategy_id, sym, legs, mode, bname, log); return None
    prem_s = resw.get("price")
    if not prem_s or prem_s <= 0:
        prem_s = _opt_ltp(_get_broker(bname), wsec) or 0.0
    legs.append(dict(side="SELL", opt_type=opt_type, sec_id=wsec, trad_sym=wsym,
                     qty=resw["qty"], entry_prem=round(float(prem_s), 2)))

    net = round(legs[0]["entry_prem"] - legs[1]["entry_prem"], 2)
    label = "BULL-CALL" if direction == "up" else "BEAR-PUT"
    log.info(f"  ★ ENTRY {label} → BUY {legs[0]['trad_sym']} @{legs[0]['entry_prem']}  "
             f"SELL {legs[1]['trad_sym']} @{legs[1]['entry_prem']}  net debit={net}")
    return dict(legs=legs, entry_net=net if net > 0 else 1.0, group_id=gid,
                direction=direction,
                tp_frac=float(tc.get("tp_frac", 1.0)), sl_frac=float(tc.get("sl_frac", 1.0)),
                entry_spot=round(spot, 1))


def _rollback(strategy_id, sym, legs, mode, bname, log):
    """If the spread half-opened (long filled, wing failed), close the lone long so the
    strategy never holds a different structure than it was validated on."""
    for l in legs:
        try:
            import execution_gateway as gw
            log.info(f"[DVERT] rollback — closing lone leg {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="DVERT", instrument="options", reason="DVERT_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[DVERT] rollback failed for {l['trad_sym']}: {e}")


def _exit_spread(strategy_id, sym, pos, mode, bname, reason, log):
    """Close SHORT wing FIRST (so the long is never what covers a lingering short),
    then the long ATM leg."""
    import execution_gateway as gw
    ordered = sorted(pos["legs"], key=lambda l: 0 if l["side"] == "SELL" else 1)
    for l in ordered:
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="DVERT", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[DVERT] exit leg {l['trad_sym']} err: {e} (pos_monitor EOD still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "15m"),
                "levels": {"or_min": tc.get("or_min"), "wing_off": tc.get("wing_off")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else (1 if pos.get("direction") == "up" else -1),
                             "signal": "" if pos is None else f"debit-{pos.get('direction')}",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Strategy #02 — Debit Vertical @ ORB Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="dvert_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
