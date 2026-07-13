#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  05_backspread_trader.py — Strategy #05: Ratio Backspread @ Mid-day ORB    ║
# ║  Research : scratch/nifty_trend/runs/ratio_backspread/ (p=0.002, Sh 1.55)  ║
# ║  Config   : ../../nifty_config.json  →  key: "backspread_v1"               ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (NIFTY spot signal → 3-leg ratio backspread, net long-gamma)       │
# │                                                                          │
# │  Opening range : high/low of first `or_min` mins (default 30: 09:15-09:45)│
# │  Entry window  : h0:00–h1:00 (default 10:00–13:00, mid-day)               │
# │  UP breakout   : close > OR_high + orb_k×ATR → CALL backspread:           │
# │                  BUY 2×lots CE @(ATM + bs_off strikes) + SELL 1×lots ATM CE│
# │  DOWN breakout : close < OR_low − orb_k×ATR → PUT backspread (mirror)     │
# │  EXIT          : structure P&L as fraction of the SOLD ATM premium (ref): │
# │                  TP at +tp_frac×ref, SL at −sl_frac×ref; else 3:15 EOD.   │
# │  Max 2/day. 1x. skip-expiry (policy A): NO new entry on weekly-expiry day.│
# │                                                                          │
# │  Short is NEVER naked: the 2 long legs fill FIRST (risk-carrier, gated);  │
# │  the 1-lot ATM SELL is covered 2:1 (gate=False, protective-style). If the │
# │  SELL fails, the longs roll back so we never hold an unvalidated shape.   │
# │  All legs share a group_id. NEW_STRATEGY_CHECKLIST compliant.             │
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
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}

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
    "or_min": 30, "orb_k": 1.0, "h0": 10, "h1": 13, "atr_period": 14,
    "bs_off": 2, "tp_frac": 1.5, "sl_frac": 0.75,
    "qty": 1, "max_trades_per_day": 2, "symbol": "NIFTY",
    # ── net-structure trailing profit lock (task 67) ──
    # The fixed tp_frac/sl_frac band never locks in a peak — an 8000+ intraday
    # gain rode all the way back down. This adds a trailing floor on the SAME
    # net-structure P&L (`pnl_frac` = net value change / sold-ATM premium), using
    # the shared arm+gap+confirm+2-reading-peak state machine (risk_gate.
    # advance_trailing_lock — same one the KILL-ALL / per-instrument locks use).
    # Ships DISABLED — enable + paper-test before trusting it live.
    # arm_frac: peak must reach +this (× ATM prem) before the lock arms.
    # gap_frac: floor sits this far below the confirmed peak; ratchets up only.
    # confirm_secs: net P&L must stay below the floor this long before exit
    #               (whipsaw guard — a single dip tick won't fire it).
    "trail_enabled": False, "trail_arm_frac": 0.5, "trail_gap_frac": 0.3,
    "trail_confirm_secs": 45,
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


def compute_breakout(df, cfg):
    """Mid-day ORB breakout on the last CLOSED bar — matches intraday_engine 'tod_orb'
    (design the backspread backtest validated): breakout beyond OR ± k×ATR AND the bar
    inside the h0:00–h1:00 entry window. Returns dict(direction, spot, atr) or None."""
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
    # backtest (intraday_engine tod_orb) uses cutoff `tt <= or_end` — the bar LABELLED
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
    bt = bar["time"].time()
    if bt <= or_end:
        return None
    # tod_orb entry window
    if not (datetime(2000, 1, 1, int(p["h0"]), 0).time() <= bt
            <= datetime(2000, 1, 1, int(p["h1"]), 0).time()):
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


def _is_expiry_day(trad_sym, sec_id):
    """skip-expiry policy A — same guard as the backtest (no NEW entry on expiry day)."""
    try:
        import risk_gate
        return bool(risk_gate.is_expiry_day(trad_sym, sec_id))
    except Exception:
        return False


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
    """Restore today's open backspread from disk, only if order_store still shows
    ALL legs open (TRAP #28 — restart must not orphan/duplicate)."""
    pos = load_state(sid)
    if not pos or not pos.get("legs"):
        return None
    try:
        import order_store
        opens = order_store.trades_for(ist_now().strftime("%Y-%m-%d")).get("open") or []
        open_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        want = {str(l["sec_id"]) for l in pos["legs"]}
        if want.issubset(open_secs):
            log.info(f"[RECOVER] re-attached open backspread {[l['trad_sym'] for l in pos['legs']]}")
            return pos
        log.info("[RECOVER] disk state had a position but order_store shows leg(s) closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="backspread_v1"):
    log = _make_logger(strategy_id)
    log.info("=" * 62)
    log.info(f"  05_backspread_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
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
                log.info("[BSPRD] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[BSPRD] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            df = fetch_nifty(token, cid, tf_min)
            if df is None or df.empty:
                log.warning("[BSPRD] no candle data"); time.sleep(30); continue
            spot = float(df["close"].iloc[-1])

            # ── manage OPEN backspread: %-of-sold-ATM-premium exit ──
            if pos is not None:
                broker = _get_broker(bname)
                reason = None
                if broker:
                    vals = {}
                    for l in pos["legs"]:
                        vals[l["sec_id"]] = _opt_ltp(broker, l["sec_id"])
                    if all(v is not None for v in vals.values()):
                        # per-unit structure value with ratio weights (+2 longs / −1 short)
                        cur = sum(l["w"] * vals[l["sec_id"]] for l in pos["legs"])
                        ent = pos["entry_val"]
                        ref = pos["ref"] or 1.0
                        pnl_frac = (cur - ent) / ref
                        log.info(f"[BSPRD] open  val {ent:+.1f}→{cur:+.1f}  P&L {pnl_frac*100:+.0f}% of ATM-prem "
                                 f"(tp +{int(pos['tp_frac']*100)}% / sl -{int(pos['sl_frac']*100)}%)")
                        if pnl_frac >= pos["tp_frac"]:
                            reason = "BSPRD_TP"
                        elif pnl_frac <= -pos["sl_frac"]:
                            reason = "BSPRD_SL"
                        # ── net-structure trailing profit lock (task 67) ──
                        # Runs on the SAME net pnl_frac; only if the fixed band
                        # hasn't already decided to exit. Reuses the shared
                        # arm+gap+confirm+2-reading-peak machine so it can't drift
                        # from the KILL-ALL / per-instrument locks (Rule 6B).
                        if reason is None and tc.get("trail_enabled"):
                            import risk_gate
                            tstate = pos.setdefault("trail", {"armed": False, "peak": 0.0, "floor": None,
                                                              "breach_since": None, "fired": False, "prev_mtm": None})
                            tstate, changed = risk_gate.advance_trailing_lock(
                                tstate, pnl_frac,
                                float(tc.get("trail_arm_frac", 0.5)),
                                float(tc.get("trail_gap_frac", 0.3)),
                                float(tc.get("trail_confirm_secs", 45)),
                                time.time())
                            pos["trail"] = tstate
                            if changed:
                                save_state(strategy_id, pos)
                            if tstate.get("armed"):
                                log.info(f"[BSPRD] trail armed — peak {tstate['peak']:+.2f} "
                                         f"floor {tstate.get('floor')}  cur {pnl_frac:+.2f} "
                                         f"(× ATM-prem)")
                            if tstate.get("fired"):
                                reason = "BSPRD_TRAIL"
                if is_force_exit_time():
                    reason = "EOD_315_SQUAREOFF"
                if reason:
                    log.info(f"[EXIT] backspread — {reason}")
                    _exit_structure(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            if is_force_exit_time():
                time.sleep(60); continue

            # ── ENTRY ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 2)):
                if is_no_entry_time():
                    time.sleep(30); continue
                sig = compute_breakout(df, tc)
                log.info(f"[BSPRD] spot={spot:.1f}  pos=flat  trades={trades_today}  "
                         f"breakout={sig['direction'] if sig else 'none'}")
                if sig:
                    pos = _enter_backspread(strategy_id, sym, spot, sig["direction"], tc, mode, bname, log)
                    if pos:
                        trades_today += 1; save_state(strategy_id, pos)

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[BSPRD] Stopped by user"); break
        except Exception as e:
            log.error(f"[BSPRD] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _get_broker(bname):
    try:
        import risk_gate
        from brokers import get_broker
        return get_broker(str(bname or risk_gate.default_broker() or "dhan").lower())
    except Exception:
        return None


def _enter_backspread(strategy_id, sym, spot, direction, tc, mode, bname, log):
    """CALL (up) / PUT (down) ratio backspread via the gateway.
    BUY the 2 OTM lots FIRST (risk-carrier, RMS-gated) — only then SELL 1 ATM lot
    (covered 2:1, gate=False). SELL failure → roll the longs back. Returns pos/None."""
    import execution_gateway as gw
    lots = int(tc.get("qty", 1))
    off = int(tc.get("bs_off", 2))
    opt_type = "CE" if direction == "up" else "PE"
    off_signed = off if direction == "up" else -off
    gid = f"BSPRD_{int(time.time())}"
    legs = []

    # resolve BOTH contracts first (ATM for expiry-check + short leg; OTM for longs)
    res_a = dhan_master.get_option_contract(sym, spot, opt_type, 0)
    res_o = dhan_master.get_option_contract(sym, spot, opt_type, off_signed)
    if not res_a or not res_a[0] or not res_o or not res_o[0] or str(res_a[0]) == str(res_o[0]):
        log.error(f"[BSPRD] {sym} {opt_type} contracts not resolvable (ATM/OTM{off_signed:+d}) — abort")
        return None
    a_sec, a_sym, a_lot = res_a
    o_sec, o_sym, o_lot = res_o

    # skip-expiry policy A — same as backtest: no NEW entry on weekly-expiry day
    if _is_expiry_day(a_sym, a_sec):
        log.info("[BSPRD] expiry day — entry skipped (policy A, matches backtest)")
        return None

    # leg 1 — BUY 2×lots OTM (the risk carrier; RMS-gated)
    try:
        res = gw.execute_signal(strategy_id, sym, "BUY", 2 * lots, (o_lot or a_lot or 1),
                                o_sec, o_sym, seg="NSE_FNO", mode=mode, broker_name=bname,
                                tag="BSPRD", instrument="options", group_id=gid, log=log.info)
    except Exception as e:
        log.error(f"[BSPRD] OTM BUY error: {e}"); return None
    if not res["ok"]:
        log.info(f"[BSPRD] OTM BUY not ok — {res.get('status')}: {res.get('reason')}"); return None
    prem_o = res.get("price")
    if not prem_o or prem_o <= 0:
        prem_o = _opt_ltp(_get_broker(bname), o_sec) or 0.0
    legs.append(dict(side="BUY", w=+2, opt_type=opt_type, sec_id=o_sec, trad_sym=o_sym,
                     qty=res["qty"], entry_prem=round(float(prem_o), 2)))

    # ratio guard: short lots derived from the FILLED long qty (2:1 always holds)
    lot_size = int(o_lot or a_lot or 1)
    short_lots = int(legs[0]["qty"] // (2 * lot_size))
    if short_lots < 1:
        log.info("[BSPRD] long fill too small to cover a short (RMS sized-down) — rollback to stay validated shape")
        _rollback(strategy_id, sym, legs, mode, bname, log); return None

    # leg 2 — SELL 1×lots ATM (covered 2:1 by leg 1; gate=False protective-style)
    try:
        ress = gw.execute_signal(strategy_id, sym, "SELL", short_lots, (a_lot or lot_size),
                                 a_sec, a_sym, seg="NSE_FNO", mode=mode, broker_name=bname,
                                 tag="BSPRD", instrument="options", group_id=gid,
                                 gate=False, log=log.info)
    except Exception as e:
        log.error(f"[BSPRD] ATM SELL error: {e}")
        _rollback(strategy_id, sym, legs, mode, bname, log); return None
    if not ress["ok"]:
        log.info(f"[BSPRD] ATM SELL not ok — {ress.get('status')}: {ress.get('reason')}")
        _rollback(strategy_id, sym, legs, mode, bname, log); return None
    prem_a = ress.get("price")
    if not prem_a or prem_a <= 0:
        prem_a = _opt_ltp(_get_broker(bname), a_sec) or 0.0
    legs.append(dict(side="SELL", w=-1, opt_type=opt_type, sec_id=a_sec, trad_sym=a_sym,
                     qty=ress["qty"], entry_prem=round(float(prem_a), 2)))

    entry_val = round(2 * legs[0]["entry_prem"] - legs[1]["entry_prem"], 2)   # per-unit, ratio-weighted
    ref = legs[1]["entry_prem"] or 1.0                                        # sold ATM premium anchor
    label = "CALL-BACKSPREAD" if direction == "up" else "PUT-BACKSPREAD"
    log.info(f"  ★ ENTRY {label} → BUY 2x {legs[0]['trad_sym']} @{legs[0]['entry_prem']}  "
             f"SELL 1x {legs[1]['trad_sym']} @{legs[1]['entry_prem']}  net/unit={entry_val:+.1f} ref={ref:.1f}")
    return dict(legs=legs, entry_val=entry_val, ref=ref, group_id=gid, direction=direction,
                tp_frac=float(tc.get("tp_frac", 1.5)), sl_frac=float(tc.get("sl_frac", 0.75)),
                entry_spot=round(spot, 1),
                # net-structure trailing-lock state (task 67) — owned + persisted
                # here, restart-safe via save_state. Shape matches
                # risk_gate.advance_trailing_lock's state contract.
                trail={"armed": False, "peak": 0.0, "floor": None,
                       "breach_since": None, "fired": False, "prev_mtm": None})


def _rollback(strategy_id, sym, legs, mode, bname, log):
    """Half-open (longs filled, short failed) → close the longs so we never hold an
    unvalidated structure."""
    for l in legs:
        try:
            import execution_gateway as gw
            log.info(f"[BSPRD] rollback — closing {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="BSPRD", instrument="options", reason="BSPRD_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[BSPRD] rollback failed for {l['trad_sym']}: {e}")


def _exit_structure(strategy_id, sym, pos, mode, bname, reason, log):
    """Close the SHORT ATM leg FIRST (longs keep covering it till the last moment),
    then the 2-lot long leg."""
    import execution_gateway as gw
    ordered = sorted(pos["legs"], key=lambda l: 0 if l["side"] == "SELL" else 1)
    for l in ordered:
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="BSPRD", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[BSPRD] exit leg {l['trad_sym']} err: {e} (pos_monitor EOD still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "5m"),
                "levels": {"or_min": tc.get("or_min"), "window": f"{tc.get('h0')}:00-{tc.get('h1')}:00",
                           "bs_off": tc.get("bs_off")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else (1 if pos.get("direction") == "up" else -1),
                             "signal": "" if pos is None else f"backspread-{pos.get('direction')}",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Strategy #05 — Ratio Backspread @ Mid-day ORB Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="backspread_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
