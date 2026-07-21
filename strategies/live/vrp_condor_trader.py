#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  vrp_condor_trader.py — #10 VRP Overnight Condor (V1, PAPER)               ║
# ║  Research : scratch/nifty_trend/build_vrp.py  (real WEEK lake, tail-stress ║
# ║            Sharpe ~4.2, DSR 1.0@N=120, 559 trades, defined-risk clamped)    ║
# ║  Design   : _ADR/ADR-006-positional-overnight-lane.md (same lane as VRP)   ║
# ║  Config   : ../../nifty_config.json  →  key: "vrp_condor_v1"                ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  EDGE: implied vol > realized vol (VRP). Sell an OTM iron condor near the  │
# │  close, hold ONE night (bounded gap risk = the premium you're paid for),   │
# │  buy it back the NEXT session near close. DAILY & systematic (~250/yr) —    │
# │  the high frequency is what makes it statistically robust (unlike the       │
# │  once-per-expiry VRP straddle, which was too infrequent and got shelved).   │
# │                                                                            │
# │  This is a DELIBERATE, DEFINED-RISK overnight bet — the ONLY reason it's    │
# │  allowed past the house "no overnight" rule is the mandatory BUY wings that │
# │  cap the loss (ADR-006 allow_overnight lane). Structurally = vrp_straddle_  │
# │  trader but body is OTM (±body_off) and the hold is exactly one session.    │
# │                                                                            │
# │  ⚠️ STAGED: PAPER + active:false. Backtest Sharpe is FLATTERED by a benign  │
# │  2021-26 sample (no COVID-scale crash). MUST be watched on paper (esp. gap  │
# │  nights) before any live money. Every leg via execution_gateway            │
# │  (RMS-gated, order_store-recorded). BUY wings FIRST, never naked overnight. │
# └─────────────────────────────────────────────────────────────────────────┘

import json
import logging
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: F401
import dhan_master

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
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
    # Trading-day (weekend + NSE holiday) + time-of-day gate — SINGLE SOURCE:
    # market_calendar. Without the trading-day part the daily 15:10 roll fired on
    # Saturday: it EXITed Friday's held condor (entry_date != "today") and
    # re-ENTERed a fresh one at stale weekend prices — a phantom close+reopen that
    # split the real Fri→Mon hold, corrupted P&L day-attribution and broke BS (no
    # weekend spot). Now the loop does nothing on any non-trading day; a held
    # condor simply carries to the next real session.
    import market_calendar as mc
    return mc.is_market_open(ist_now(), MARKET_OPEN, MARKET_CLOSE)

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "symbol": "NIFTY",
    "body_off": 3, "wing_off": 5, "qty": 1,
    "entry_hm": [15, 10],      # enter near close (matches backtest's ~15:15 entry)
    "exit_hm": [15, 10],       # exit near the NEXT session's close (one-night hold)
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def fetch_spot(token, cid):
    try:
        import shared_ltp_cache
        v = shared_ltp_cache.get_index("NIFTY")
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    try:
        import dhan_rate_limiter as _rl
        if not _rl.acquire("ltp"):
            return None
    except ImportError:
        pass
    try:
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp", json={"IDX_I": [13]},
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"}, timeout=8)
        if r.status_code != 200:
            return None
        return float(r.json()["data"]["IDX_I"]["13"]["last_price"])
    except Exception:
        return None


def _opt_ltp(broker, sec_id):
    try:
        q = broker.quote(sec_id, "NSE_FNO")
        return float(q.get("ltp")) if isinstance(q, dict) and q.get("ltp") else None
    except Exception:
        return None


def _get_broker(bname):
    try:
        import risk_gate
        from brokers import get_broker
        return get_broker(str(bname or risk_gate.default_broker() or "dhan").lower())
    except Exception:
        return None


def _resolve(sym, spot, opt_type, off):
    r = dhan_master.get_option_contract(sym, spot, opt_type, off)
    return r if (r and r[0]) else None


# ─────────────────────────── state persist / recover ───────────────────────
# Positional (one-night hold): state is NOT date-scoped — an overnight position
# must survive the day-rollover + the daily 9:10 restart (TRAP #3/#28/#76).
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
    pos = load_state(sid)
    if not pos or not pos.get("legs"):
        return None
    try:
        import order_store
        from datetime import timedelta as _td
        # POSITIONAL: an overnight leg is dated its ENTRY day, so a restart on the
        # exit-day (or any later day) must look back a few days — a plain today-only
        # query would find nothing and wrongly CLEAR a genuinely-open overnight
        # position on the very next session. trades_for_range pairs entry+exit across
        # dates, so a leg still shows "open" only if it truly has no exit yet.
        _t = ist_now()
        opens = order_store.trades_for_range(
            (_t - _td(days=7)).strftime("%Y-%m-%d"), _t.strftime("%Y-%m-%d")).get("open") or []
        open_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        want = {str(l["sec_id"]) for l in pos["legs"]}
        if want.issubset(open_secs):
            log.info(f"[RECOVER] re-attached overnight condor {[l['trad_sym'] for l in pos['legs']]}")
            return pos
        log.info("[RECOVER] state had legs but order_store shows some closed — clearing pos.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="vrp_condor_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  vrp_condor_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos = _recover(strategy_id, log)

    _last_hb = ""   # periodic heartbeat throttle — this trader only acts near entry_hm (15:10),
                    # so without a heartbeat the EOD health-check saw a ~3hr "gap" and flagged it.
    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            bname = _order_broker(tc)
            sym = tc.get("symbol", "NIFTY")

            if not tc.get("active", False):
                log.info("[VRPC] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                time.sleep(60); continue

            token, cid = load_creds()
            spot = fetch_spot(token, cid)
            if not spot:
                log.warning("[VRPC] no spot"); time.sleep(20); continue

            # Heartbeat so health-check sees the process is alive between market-open
            # and the 15:10 entry window (was silent → false "heartbeat gap").
            # MUST stay under health_check's floor of 180s (`hb_limit`, health_check.py)
            # — at the original ~5 min this logged slower than that floor, so the 09:20
            # preflight reported a false RED for a perfectly healthy condor.
            _hb = f"{now.hour}:{now.minute // 2}"
            if _hb != _last_hb:
                _last_hb = _hb
                eh0, em0 = tc.get("entry_hm", [15, 10])
                log.info(f"[VRPC] spot={spot:.1f}  pos={'held' if pos else 'flat'}  "
                         f"waiting entry {eh0:02d}:{em0:02d}")

            xh, xm = tc.get("exit_hm", [15, 10])
            eh, em = tc.get("entry_hm", [15, 10])
            today = str(now.date())

            # ── EXIT: a held condor is closed near the NEXT session's close (one-night hold) ──
            if pos is not None:
                held_new_session = pos.get("entry_date") != today
                if held_new_session and (now.hour, now.minute) >= (xh, xm):
                    log.info(f"[EXIT] one-night condor (entered {pos.get('entry_date')}) → next-session close")
                    _exit_condor(strategy_id, sym, pos, mode, bname, "VRPC_NEXT_CLOSE", log)
                    pos = None
                    save_state(strategy_id, pos)
                # NOTE: no 3:15 force-exit — held overnight via allow_overnight (ADR-006).
                # Expiry-day 2:55 squareoff + ITM guard + RMS daily-loss STILL apply (pos_monitor).

            # ── ENTRY: flat + near close + not expiry day + not already entered today ──
            if pos is None and (now.hour, now.minute) >= (eh, em) and (now.hour, now.minute) < MARKET_CLOSE:
                pos = _enter_condor(strategy_id, sym, spot, tc, mode, bname, today, log)
                if pos is not None:
                    save_state(strategy_id, pos)

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[VRPC] Stopped by user"); break
        except Exception as e:
            log.error(f"[VRPC] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _enter_condor(strategy_id, sym, spot, tc, mode, bname, today, log):
    """4-leg OTM iron condor: BUY wings FIRST (never naked overnight), then SELL the
    OTM body. Any failure → rollback. Defined-risk mandatory for the overnight hold."""
    import execution_gateway as gw
    import risk_gate
    lots = int(tc.get("qty", 1)); body = int(tc.get("body_off", 3)); wing = int(tc.get("wing_off", 5))
    gid = f"VRPC_{int(time.time())}"
    # NOTE: get_option_contract() already inverts the offset for PE (positive offset = OTM,
    # i.e. LOWER strike). So OTM puts need POSITIVE offsets here — passing -body/-(body+wing)
    # double-negated it → atm_idx+body → ITM puts (wrong side), collapsing both bodies onto the
    # same strike + losing a wing (only 3 legs land). Same bug/fix as the weekly condor. TRAP #140.
    b_ce = _resolve(sym, spot, "CE", +body); b_pe = _resolve(sym, spot, "PE", +body)
    w_ce = _resolve(sym, spot, "CE", +(body + wing)); w_pe = _resolve(sym, spot, "PE", +(body + wing))
    if not all([b_ce, b_pe, w_ce, w_pe]):
        log.error("[VRPC] contract resolve failed — abort"); return None
    if len({str(b_ce[0]), str(b_pe[0]), str(w_ce[0]), str(w_pe[0])}) != 4:
        log.error("[VRPC] duplicate strike in legs — abort"); return None
    try:
        if risk_gate.is_expiry_day(b_ce[1], b_ce[0]):
            log.info("[VRPC] expiry day — no fresh overnight entry (only gap risk, no theta)"); return None
    except Exception:
        pass

    placed = []
    def place(res, side, w, gate):
        sec, tsym, lot = res
        try:
            r = gw.execute_signal(strategy_id, sym, side, lots, (lot or 1), sec, tsym,
                                  seg="NSE_FNO", mode=mode, broker_name=bname, tag="VRPC",
                                  instrument="options", group_id=gid, gate=gate, log=log.info)
        except Exception as e:
            log.error(f"[VRPC] {side} {tsym} error: {e}"); return False
        if not r["ok"]:
            log.info(f"[VRPC] {side} {tsym} not ok — {r.get('status')}: {r.get('reason')}"); return False
        prem = r.get("price") or _opt_ltp(_get_broker(bname), sec) or 0.0
        placed.append(dict(side=side, w=w, sec_id=sec, trad_sym=tsym, qty=r["qty"],
                           entry_prem=round(float(prem), 2)))
        return True

    if not place(w_ce, "BUY", +1, False) or not place(w_pe, "BUY", +1, False):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None
    if not place(b_ce, "SELL", -1, True):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None
    if not place(b_pe, "SELL", -1, False):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None

    V = sum(l["w"] * l["entry_prem"] for l in placed)
    ref = abs(V) if abs(V) > 1e-6 else 1.0
    log.info(f"  ★ ENTRY VRP-CONDOR body±{body} wing±{body+wing} net={V:+.1f} ref={ref:.1f} (one-night)")
    return dict(legs=placed, entry_val=V, ref=ref, group_id=gid, entry_date=today,
                entry_spot=round(spot, 1), direction="vrp_condor")


def _rollback(strategy_id, sym, legs, mode, bname, log):
    import execution_gateway as gw
    for l in legs:
        try:
            log.info(f"[VRPC] rollback — closing {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="VRPC", instrument="options", reason="VRPC_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[VRPC] rollback failed {l['trad_sym']}: {e}")


def _exit_condor(strategy_id, sym, pos, mode, bname, reason, log):
    """Close SHORTS first (buy back) so only defined long wings ever linger."""
    import execution_gateway as gw
    for l in sorted(pos["legs"], key=lambda l: 0 if l["side"] == "SELL" else 1):
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="VRPC", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[VRPC] exit leg {l['trad_sym']} err: {e} (pos_monitor still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "levels": {"body_off": tc.get("body_off"), "wing_off": tc.get("wing_off")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else 2,
                             "signal": "" if pos is None else "vrp-condor",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="#10 VRP overnight iron-condor trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="vrp_condor_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
