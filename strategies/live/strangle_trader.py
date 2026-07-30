#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  strangle_trader.py — Long Strangle @ ORB breakout  (Version 1)           ║
# ║  Research : scratch/nifty_trend/runs/long_strangle_orb/  (00.07)          ║
# ║  Config   : ../../nifty_config.json  →  key: "strangle_v1"                ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (NIFTY spot signal → 2-leg OTM strangle, direction-agnostic)       │
# │                                                                          │
# │  Opening range : high/low of first `or_min` mins (from 09:15)            │
# │  BREAKOUT      : close breaks OR_high+orb_k×ATR (up) OR OR_low−orb_k×ATR  │
# │                  (down) — EITHER way, ONLY inside win_start..win_end.     │
# │  ENTRY        : BUY OTM CE (+strike_offset) + BUY OTM PE (+strike_offset).│
# │                 A long strangle — cheaper than a straddle, needs a bigger │
# │                 move to pay, capped loss = premium paid.                  │
# │  EXIT         : combined-premium % — take profit at +tp_frac of the       │
# │                 total premium paid, stop at −sl_frac; else 3:15 EOD.      │
# │  Max 2 trades/day. 1x, no leverage (defined risk = premium paid).        │
# │                                                                          │
# │  MATCHES BACKTEST (Rule 10 / fidelity): strike_offset=2 (both legs 2      │
# │  strikes OTM ≡ backtest CE+2/PE−2), win 11:00–13:00 (h0=11/h1=13),        │
# │  tp_frac 0.75, sl_frac 1.0, or_min 15, orb_k 0.5, 5m. Change any of these │
# │  and the validated number no longer holds.                               │
# │                                                                          │
# │  ⚠️  Deployed for FORWARD-PAPER observation only. This run FAILED the      │
# │  significance gate (p=0.072) AND loses on real premium (bs_vs_reallake:   │
# │  REAL Sharpe −2.28, −₹3.55L). Paper-watch to see it live; do NOT flip     │
# │  to real money on the BS-modeled number.                                 │
# │                                                                          │
# │  Every leg goes through execution_gateway (RMS gate + order_store) —      │
# │  NEW_STRATEGY_CHECKLIST compliant. Both legs share a group_id.           │
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
from _CHARTING.indicators import wilder_atr as _atr   # canonical (Rule 6B/ADR-002) — matches backtest
from strategies.signals import orb as _orb   # SINGLE-SOURCE ORB signal (same code the backtest runs)

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
FORCE_EXIT   = (15, 10)
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

def _hm(s):
    """'11:00' -> (11, 0)."""
    try:
        h, m = str(s).split(":"); return (int(h), int(m))
    except Exception:
        return (11, 0)

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "timeframe": "5m",
    "or_min": 15, "orb_k": 0.5, "atr_period": 14,
    "win_start": "11:00", "win_end": "13:00",
    "strike_offset": 2,            # both legs 2 strikes OTM ≡ backtest CE+2/PE−2
    "tp_frac": 0.75, "sl_frac": 1.0,
    "qty": 1, "max_trades_per_day": 2, "symbol": "NIFTY",
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def fetch_nifty(token, cid, tf_min, days=5):
    """Continuous `tf_min` NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85)."""
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
    """Direction-agnostic ORB breakout on the last CLOSED bar, gated to the
    win_start..win_end entry window.

    SIGNAL = the SINGLE-SOURCE `orb.orb_signal_last` (tod_orb — WITH the h0/h1 window) —
    the EXACT backtest signal (option_structures.entry_signal delegates to
    intraday_engine.design_signals('tod_orb'), which also calls this), so a live entry
    fires iff the backtest fired on that bar. Removes the OR-boundary (`<` vs `<=`) and
    previous-vs-current-bar-ATR crossover drift the old inline port had.
    Returns dict(direction 'up'/'down', spot, atr) or None."""
    p = cfg
    period = int(p["atr_period"])
    if len(df) < period + 3:
        return None
    df = df.copy().reset_index(drop=True)
    df["atr"] = _atr(df, period)
    today = ist_now().date()
    # config → shared param names. Window hours from win_start/win_end (backtest tod_orb
    # uses hour-granularity h0/h1; strangle_v1 = 11:00/13:00).
    ws, we = _hm(p["win_start"]), _hm(p["win_end"])
    sig_params = dict(or_min=int(p["or_min"]), orb_k=float(p["orb_k"]),
                      atr_period=period, h0=ws[0], h1=we[0])
    side = _orb.orb_signal_last(df, sig_params, dt_col="time", hi="high", lo="low", cl="close")
    if not side:
        return None
    i = len(df) - 2   # last CLOSED bar (last row = forming)
    if i < 1 or df.iloc[i]["time"].date() != today:
        return None
    atr_i = float(df["atr"].iloc[i])
    if atr_i <= 0:
        return None
    direction = "up" if side == "long" else "down"
    return dict(direction=direction, spot=float(df["close"].iloc[i]), atr=atr_i,
                bar_time=str(df.iloc[i]["time"]))


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
    """Restore today's open strangle from disk, but ONLY if order_store still shows
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
            log.info(f"[RECOVER] re-attached open strangle {[l['trad_sym'] for l in pos['legs']]}")
            return pos
        log.info("[RECOVER] disk state had a strangle but order_store shows leg(s) closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="strangle_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  strangle_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos = _recover(strategy_id, log)
    # Restart-safe day count (TRAP: restart after close hands back the whole quota).
    import risk_gate as _rg_cnt
    _et = _rg_cnt.entries_today(strategy_id)
    trades_today = _et if _et is not None else (0 if pos is None else 1)
    last_date = ist_now().date()

    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            bname = _order_broker(tc)
            sym = tc.get("symbol", "NIFTY")
            tf_min = _TF_MIN.get(tc.get("timeframe", "5m"), 5)

            if last_date != now.date():
                last_date = now.date(); pos = None; trades_today = 0
                save_state(strategy_id, None); log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[STRGL] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[STRGL] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            df = fetch_nifty(token, cid, tf_min)
            if df is None or df.empty:
                log.warning("[STRGL] no candle data"); time.sleep(30); continue
            spot = float(df["close"].iloc[-1])

            # ── manage OPEN strangle: combined-premium % exit ──
            if pos is not None:
                broker = _get_broker(bname)
                ltps = [_opt_ltp(broker, l["sec_id"]) for l in pos["legs"]] if broker else [None, None]
                reason = None
                if all(v is not None for v in ltps):
                    comb_now = sum(ltps)
                    comb_ent = pos["entry_prem_total"]
                    pnl_frac = (comb_now - comb_ent) / comb_ent if comb_ent > 0 else 0.0
                    log.info(f"[STRGL] strangle open  prem {comb_ent:.1f}→{comb_now:.1f}  "
                             f"P&L {pnl_frac*100:+.0f}%  (tp +{int(pos['tp_frac']*100)}% / sl -{int(pos['sl_frac']*100)}%)")
                    if pnl_frac >= pos["tp_frac"]:
                        reason = "STRANGLE_TP"
                    elif pnl_frac <= -pos["sl_frac"]:
                        reason = "STRANGLE_SL"
                if is_force_exit_time():
                    reason = "EOD_315_SQUAREOFF"
                if reason:
                    log.info(f"[EXIT] strangle — {reason}")
                    _exit_strangle(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            if is_force_exit_time():
                time.sleep(60); continue

            # ── ENTRY: on breakout inside window, buy OTM CE + OTM PE ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 2)):
                if is_no_entry_time():
                    time.sleep(30); continue
                sig = compute_breakout(df, tc)
                log.info(f"[STRGL] spot={spot:.1f}  window={tc['win_start']}-{tc['win_end']}  "
                         f"pos=flat  trades={trades_today}  breakout={sig['direction'] if sig else 'none'}")
                if sig:
                    pos = _enter_strangle(strategy_id, sym, spot, tc, mode, bname, log)
                    if pos:
                        trades_today += 1; save_state(strategy_id, pos)

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[STRGL] Stopped by user"); break
        except Exception as e:
            log.error(f"[STRGL] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _get_broker(bname):
    try:
        import risk_gate
        from brokers import get_broker
        return get_broker(str(bname or risk_gate.default_broker() or "dhan").lower())
    except Exception:
        return None


def _enter_strangle(strategy_id, sym, spot, tc, mode, bname, log):
    """BUY OTM CE + OTM PE via the gateway. Both legs use +strike_offset — for a PE,
    dhan_master.get_option_contract INVERTS the offset (positive → strike BELOW spot),
    so +offset lands OTM on BOTH sides (never negative-PE — TRAP #140 guard). Returns
    pos dict or None (rolls back leg-1 if leg-2 fails)."""
    import execution_gateway as gw
    lots = int(tc.get("qty", 1))
    off = int(tc.get("strike_offset", 2))
    gid = f"STRGL_{int(time.time())}"
    legs = []
    for opt_type in ("CE", "PE"):
        res_c = dhan_master.get_option_contract(sym, spot, opt_type, off)  # +off = OTM both sides
        if not res_c or not res_c[0]:
            log.error(f"[STRGL] {sym} {opt_type} contract not found — abort")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        sec_id, trad_sym, lot_size = res_c
        try:
            res = gw.execute_signal(strategy_id, sym, "BUY", lots, (lot_size or 1), sec_id, trad_sym,
                                    seg="NSE_FNO", mode=mode, broker_name=bname, tag="STRGL",
                                    instrument="options", group_id=gid, log=log.info)
        except Exception as e:
            log.error(f"[STRGL] {opt_type} entry error: {e}")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        if not res["ok"]:
            log.info(f"[STRGL] {opt_type} entry not ok — {res.get('status')}: {res.get('reason')}")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        prem = res.get("price")
        if not prem or prem <= 0:
            prem = _opt_ltp(_get_broker(bname), sec_id) or 0.0
        legs.append(dict(opt_type=opt_type, sec_id=sec_id, trad_sym=trad_sym,
                         qty=res["qty"], entry_prem=round(float(prem), 2)))
    total = round(sum(l["entry_prem"] for l in legs), 2)
    log.info(f"  ★ ENTRY STRANGLE → BUY CE {legs[0]['trad_sym']} @{legs[0]['entry_prem']} + "
             f"PE {legs[1]['trad_sym']} @{legs[1]['entry_prem']}  total prem={total}  (off={off} OTM)")
    return dict(legs=legs, entry_prem_total=total if total > 0 else 1.0, group_id=gid,
                tp_frac=float(tc.get("tp_frac", 0.75)), sl_frac=float(tc.get("sl_frac", 1.0)),
                entry_spot=round(spot, 1))


def _rollback(strategy_id, sym, legs, mode, bname, log):
    """If a strangle half-opened (leg-1 filled, leg-2 failed), close leg-1 so we
    don't unintentionally hold a single directional leg."""
    for l in legs:
        try:
            import execution_gateway as gw
            log.info(f"[STRGL] rollback — closing lone leg {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side="BUY", seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="STRGL", instrument="options", reason="STRANGLE_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[STRGL] rollback failed for {l['trad_sym']}: {e}")


def _exit_strangle(strategy_id, sym, pos, mode, bname, reason, log):
    import execution_gateway as gw
    for l in pos["legs"]:
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side="BUY", seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="STRGL", instrument="options", reason=reason, group_id=pos.get("group_id", ""),
                            log=log.info)
        except Exception as e:
            log.error(f"[STRGL] exit leg {l['trad_sym']} err: {e} (pos_monitor EOD still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "5m"),
                "levels": {"or_min": tc.get("or_min"),
                           "win_start": tc.get("win_start"), "win_end": tc.get("win_end")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else 1,
                             "signal": "" if pos is None else "strangle",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Long Strangle @ ORB breakout Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="strangle_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
