#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  06_shortvol_trader.py — Strategy #06: Short-Vol Iron-Fly @ 10:00 (V1)     ║
# ║  Research : scratch/nifty_trend/runs/shortvol_ironfly/ (REAL data, Sh 8.9) ║
# ║  Config   : ../../nifty_config.json  →  key: "shortvol_v1"                 ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (NIFTY options — 4-leg iron-fly, net short-vol / theta harvest)    │
# │                                                                          │
# │  Entry (once/day at h0:00, default 10:00 — after the open, IV known):    │
# │    BUY  CE wing (ATM + wing strikes)   ┐ protective longs go FIRST so the │
# │    BUY  PE wing (ATM − wing strikes)   ┘ shorts are NEVER naked           │
# │    SELL ATM CE  ┐ the theta/VRP engine (covered by the wings above)       │
# │    SELL ATM PE  ┘                                                         │
# │  Exit: structure P&L as fraction of net credit (ref):                    │
# │    TP +tp_frac·ref, SL −sl_frac·ref, else 3:15 EOD. Close SHORTS FIRST    │
# │    (buy them back) so only defined long wings ever linger.               │
# │  skip-expiry (policy A): no NEW entry on weekly-expiry day. 1x, defined   │
# │  risk = wing width − credit. Every leg via execution_gateway.            │
# │  This is the portfolio's INVERSE leg (wins on quiet days; corr −0.1 vs ORB)│
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

BASE_DIR    = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "nifty_config.json"

sys.path.insert(0, str(BASE_DIR))
import _paths  # noqa: F401
import dhan_master

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
FORCE_EXIT   = (15, 10)
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

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "timeframe": "5m", "symbol": "NIFTY",
    "h0": 10, "wing_off": 8, "tp_frac": 0.5, "sl_frac": 1.0,
    "qty": 1, "max_trades_per_day": 1,
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def fetch_spot(token, cid):
    """last NIFTY spot via index LTP (marketfeed) — cheap, no candle needed for a time-entry."""
    try:
        import shared_ltp_cache
        v = shared_ltp_cache.get_index("NIFTY")   # fresh (default staleness)
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    # REST fallback — only if the shared rate-limiter grants an ltp slot
    _slot = True
    try:
        import dhan_rate_limiter as _rl
        _slot = _rl.acquire("ltp")
    except ImportError:
        pass
    if _slot:
        try:
            r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                              json={"IDX_I": [13]},
                              headers={"access-token": token, "client-id": cid,
                                       "Content-Type": "application/json"}, timeout=8)
            if r.status_code == 200:
                return float(r.json()["data"]["IDX_I"]["13"]["last_price"])
        except Exception:
            pass
    # LAST RESORT — accept a STALE cached spot rather than "no spot" (same fix as
    # vrp_condor/vrp_straddle). Time-entry strategy: the morning poller-congestion
    # window is where the 60s cache + busy ltp slot produced "no spot" spam; an
    # older poller reading (≤10 min) is fine to hold/heartbeat on.
    try:
        import shared_ltp_cache
        v = shared_ltp_cache.get_index("NIFTY", max_age=600)
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    return None


def _opt_ltp(broker, sec_id):
    try:
        q = broker.quote(sec_id, "NSE_FNO")
        v = q.get("ltp") if isinstance(q, dict) else None
        return float(v) if v else None
    except Exception:
        return None


def _is_expiry_day(trad_sym, sec_id):
    try:
        import risk_gate
        return bool(risk_gate.is_expiry_day(trad_sym, sec_id))
    except Exception:
        return False


def _get_broker(bname):
    try:
        import risk_gate
        from brokers import get_broker
        return get_broker(str(bname or risk_gate.default_broker() or "dhan").lower())
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
    pos = load_state(sid)
    if not pos or not pos.get("legs"):
        return None
    try:
        import order_store
        opens = order_store.trades_for(ist_now().strftime("%Y-%m-%d")).get("open") or []
        open_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        want = {str(l["sec_id"]) for l in pos["legs"]}
        if want.issubset(open_secs):
            log.info(f"[RECOVER] re-attached iron-fly {[l['trad_sym'] for l in pos['legs']]}")
            return pos
        log.info("[RECOVER] disk state had legs but order_store shows some closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="shortvol_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  06_shortvol_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
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
            h0 = int(tc.get("h0", 10))

            if last_date != now.date():
                last_date = now.date(); pos = None; trades_today = 0
                save_state(strategy_id, None); log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[SVOL] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[SVOL] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            spot = fetch_spot(token, cid)
            if not spot:
                log.warning("[SVOL] no spot"); time.sleep(20); continue

            # ── manage OPEN iron-fly: %-of-credit exit ──
            if pos is not None:
                broker = _get_broker(bname)
                reason = None
                if broker:
                    ltps = {l["sec_id"]: _opt_ltp(broker, l["sec_id"]) for l in pos["legs"]}
                    if all(v is not None for v in ltps.values()):
                        V = sum(l["w"] * ltps[l["sec_id"]] for l in pos["legs"])  # w:+1 buy/-1 sell
                        pnl_frac = (V - pos["entry_val"]) / pos["ref"]
                        log.info(f"[SVOL] open  V {pos['entry_val']:+.1f}→{V:+.1f}  "
                                 f"P&L {pnl_frac*100:+.0f}% of credit (tp+{int(pos['tp_frac']*100)}/sl-{int(pos['sl_frac']*100)})")
                        if pnl_frac >= pos["tp_frac"]:
                            reason = "SVOL_TP"
                        elif pnl_frac <= -pos["sl_frac"]:
                            reason = "SVOL_SL"
                if is_force_exit_time():
                    reason = "EOD_315_SQUAREOFF"
                if reason:
                    log.info(f"[EXIT] iron-fly — {reason}")
                    _exit_ironfly(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            if is_force_exit_time():
                time.sleep(60); continue

            # ── ENTRY: once/day at h0:00 ──
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 1)):
                if (now.hour, now.minute) >= (h0, 0) and now.hour < 15:
                    pos = _enter_ironfly(strategy_id, sym, spot, tc, mode, bname, log)
                    if pos:
                        trades_today += 1; save_state(strategy_id, pos)
                    else:
                        # avoid hammering entry every 30s if it declined (expiry/gate) — mark day done
                        trades_today = int(tc.get("max_trades_per_day", 1))
                else:
                    log.info(f"[SVOL] spot={spot:.1f} waiting for {h0}:00 entry (now {now.strftime('%H:%M')})")

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[SVOL] Stopped by user"); break
        except Exception as e:
            log.error(f"[SVOL] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _resolve(sym, spot, opt_type, off):
    r = dhan_master.get_option_contract(sym, spot, opt_type, off)
    return r if (r and r[0]) else None


def _enter_ironfly(strategy_id, sym, spot, tc, mode, bname, log):
    """Place the 4-leg iron-fly. BUY wings FIRST (protective, shorts never naked),
    then SELL the ATM straddle (covered). Any failure → rollback all placed legs."""
    import execution_gateway as gw
    lots = int(tc.get("qty", 1)); wing = int(tc.get("wing_off", 8))
    gid = f"SVOL_{int(time.time())}"
    # resolve all 4 contracts up front (also gives ATM for the expiry-day check)
    atm_ce = _resolve(sym, spot, "CE", 0); atm_pe = _resolve(sym, spot, "PE", 0)
    # get_option_contract() inverts the PE offset (positive = OTM/LOWER strike), so the
    # OTM put wing needs a POSITIVE offset. -wing double-negated it → atm_idx+wing = ITM/
    # upper put (wrong side). Same bug/fix as the condor. TRAP #140.
    w_ce = _resolve(sym, spot, "CE", +wing); w_pe = _resolve(sym, spot, "PE", +wing)
    if not all([atm_ce, atm_pe, w_ce, w_pe]):
        log.error("[SVOL] contract resolve failed — abort"); return None
    if len({str(atm_ce[0]), str(atm_pe[0]), str(w_ce[0]), str(w_pe[0])}) != 4:
        log.error("[SVOL] duplicate strike in legs — abort"); return None
    if _is_expiry_day(atm_ce[1], atm_ce[0]):
        log.info("[SVOL] expiry day — entry skipped (policy A)"); return None

    placed = []

    def place(res, side, w, gate):
        sec, tsym, lot = res
        try:
            r = gw.execute_signal(strategy_id, sym, side, lots, (lot or 1), sec, tsym,
                                  seg="NSE_FNO", mode=mode, broker_name=bname, tag="SVOL",
                                  instrument="options", group_id=gid, gate=gate, log=log.info)
        except Exception as e:
            log.error(f"[SVOL] {side} {tsym} error: {e}"); return False
        if not r["ok"]:
            log.info(f"[SVOL] {side} {tsym} not ok — {r.get('status')}: {r.get('reason')}"); return False
        prem = r.get("price") or _opt_ltp(_get_broker(bname), sec) or 0.0
        placed.append(dict(side=side, w=w, sec_id=sec, trad_sym=tsym, qty=r["qty"],
                           entry_prem=round(float(prem), 2)))
        return True

    # 1-2: BUY wings first (protective; gate=False, small debit, defines risk)
    if not place(w_ce, "BUY", +1, False) or not place(w_pe, "BUY", +1, False):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None
    # 3: SELL ATM CE (risk-carrier — RMS-gated for margin)
    if not place(atm_ce, "SELL", -1, True):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None
    # 4: SELL ATM PE (covered by structure; gate=False)
    if not place(atm_pe, "SELL", -1, False):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None

    V = sum(l["w"] * l["entry_prem"] for l in placed)     # net structure value (neg = net credit)
    ref = abs(V) if abs(V) > 1e-6 else 1.0
    log.info(f"  ★ ENTRY IRON-FLY (±{wing}) net={V:+.1f} credit-ref={ref:.1f}  "
             + " ".join(f"{l['side'][0]}{l['trad_sym'].split('-')[-2]}{l['trad_sym'].split('-')[-1]}@{l['entry_prem']}" for l in placed))
    return dict(legs=placed, entry_val=V, ref=ref, group_id=gid,
                tp_frac=float(tc.get("tp_frac", 0.5)), sl_frac=float(tc.get("sl_frac", 1.0)),
                entry_spot=round(spot, 1), direction="shortvol")


def _rollback(strategy_id, sym, legs, mode, bname, log):
    for l in legs:
        try:
            import execution_gateway as gw
            log.info(f"[SVOL] rollback — closing {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="SVOL", instrument="options", reason="SVOL_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[SVOL] rollback failed {l['trad_sym']}: {e}")


def _exit_ironfly(strategy_id, sym, pos, mode, bname, reason, log):
    """Close SHORTS first (buy them back) so only defined long wings ever linger, then wings."""
    import execution_gateway as gw
    ordered = sorted(pos["legs"], key=lambda l: 0 if l["side"] == "SELL" else 1)
    for l in ordered:
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="SVOL", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[SVOL] exit leg {l['trad_sym']} err: {e} (pos_monitor EOD still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "tf": tc.get("timeframe", "5m"),
                "levels": {"entry": f"{tc.get('h0')}:00", "wing_off": tc.get("wing_off")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else 2,   # 2 = short-vol structure open
                             "signal": "" if pos is None else "iron-fly", "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Strategy #06 — Short-Vol Iron-Fly Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="shortvol_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
