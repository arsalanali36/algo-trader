#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  bnf_strangle_trader.py — BankNifty 9:20 SHORT strangle  (Version 1)      ║
# ║  Research : scratch/nifty_trend/bnf_920_strangle_intraday.py + bnf_detail ║
# ║  Config   : ../../nifty_config.json  →  key: "bnf_strangle_v1"            ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  LOGIC (time-based, no signal — pure premium seller)                      │
# │                                                                          │
# │  ENTRY  : at 09:20 (once/day, inside entry_window_min), SELL 6-strike     │
# │           OTM CE + 6-strike OTM PE on BANKNIFTY (naked short strangle).   │
# │  EXIT   : combined-premium POINTS — book at +tp_pt (credit decayed),      │
# │           stop at −sl_pt (credit expanded); else force-close exit_time    │
# │           (14:55, matches the backtest hold-window).                      │
# │  Skips BankNifty monthly-expiry day (backtest skip_expiry=True).          │
# │  Max 1 trade/day. 1x, no leverage.                                        │
# │                                                                          │
# │  MATCHES BACKTEST (Rule 10 / fidelity): 6-strike OTM, tp 50 / sl 50 pt,   │
# │  entry 09:20, exit 14:55, NAKED (no wings). Change any of these and the   │
# │  validated number (Sharpe 3.05, +₹8.5L, worst −₹7,298) no longer holds.   │
# │                                                                          │
# │  ⚠️  FORWARD-PAPER ONLY. Passes every robustness gate (OOS>train, p=0.003,│
# │  MC not-overfit, slip×3) BUT it is a NAKED seller — a >5% intraday BNF    │
# │  gap-day (not in the 2021-26 sample) can blow past the 50-pt SL. Do NOT   │
# │  flip to real money until paper-validated + a disaster-wing is added.     │
# │  Memory: project_code3b_bnf_920_strangle.                                │
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

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
LTP_URL = "https://api.dhan.co/v2/marketfeed/ltp"
BNF_SEC_ID, BNF_SEG = "25", "IDX_I"     # BANKNIFTY index spot

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

def _hm(s, default=(9, 20)):
    try:
        h, m = str(s).split(":"); return (int(h), int(m))
    except Exception:
        return default

def _exit_times():
    """RMS single-source no-entry/force-exit (backstop). The strategy's OWN
    exit_time (14:55) fires first to match the backtest; this is the safety net."""
    try:
        import risk_gate as _rg
        return _rg.exit_time_config()
    except Exception:
        return (15, 10), (15, 10)

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "symbol": "BANKNIFTY",
    "strike_offset": 6,                 # 6 strikes OTM both sides ≡ backtest ATM±6
    "tp_pt": 50.0, "sl_pt": 50.0,       # combined-premium points
    "entry_time": "09:20", "entry_window_min": 10, "exit_time": "14:55",
    "qty": 1, "max_trades_per_day": 1,
}

def load_config(strategy_id):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(strategy_id, {})}
    except Exception:
        return dict(DEFAULTS)


def _bnf_spot(token, cid, log):
    """BankNifty index spot — cache-first (poller-warmed), REST fallback. No candles."""
    try:
        import shared_ltp_cache
        v = shared_ltp_cache.get_index("BANKNIFTY", max_age=60.0)
        if v and v > 0:
            return float(v)
    except Exception:
        pass
    try:
        import dhan_rate_limiter as _rl
        if not _rl.acquire("ltp"):
            return None
        r = requests.post(LTP_URL, json={BNF_SEG: [int(BNF_SEC_ID)]},
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"}, timeout=8)
        if r.status_code == 429:
            _rl.note_429(); return None
        if r.status_code != 200:
            return None
        node = (r.json().get("data", {}) or {}).get(BNF_SEG, {}) or {}
        for _sid, q in node.items():
            ltp = float(q.get("last_price") or q.get("ltp") or 0)
            if ltp > 0:
                return ltp
    except Exception as e:
        log.warning(f"[BNFSTR] spot fetch fail: {e}")
    return None


def _opt_ltp(broker, sec_id):
    try:
        q = broker.quote(sec_id, "NSE_FNO")
        v = q.get("ltp") if isinstance(q, dict) else None
        return float(v) if v else None
    except Exception:
        return None


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
    """Restore today's open strangle from disk, but ONLY if order_store still shows
    BOTH legs open (TRAP #28 — restart must not orphan/duplicate)."""
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
        log.info("[RECOVER] disk had a strangle but order_store shows leg(s) closed — clearing.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None


def _is_expiry_day():
    try:
        import expiry_calendar as xc
        return xc.is_banknifty_expiry_day(ist_now().date())
    except Exception:
        return False


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="bnf_strangle_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  bnf_strangle_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
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
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            bname = _order_broker(tc)
            sym = str(tc.get("symbol", "BANKNIFTY")).upper()
            entry_hm = _hm(tc.get("entry_time", "09:20"), (9, 20))
            exit_hm = _hm(tc.get("exit_time", "14:55"), (14, 55))
            win_min = int(tc.get("entry_window_min", 10))
            tp_pt = float(tc.get("tp_pt", 50)); sl_pt = float(tc.get("sl_pt", 50))
            now_hm = (now.hour, now.minute)

            if last_date != now.date():
                last_date = now.date(); pos = None; trades_today = 0
                save_state(strategy_id, None); log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[BNFSTR] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[BNFSTR] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            spot = _bnf_spot(token, cid, log)

            # ── manage OPEN strangle: combined-premium POINTS exit ──
            if pos is not None:
                broker = _get_broker(bname)
                ltps = [_opt_ltp(broker, l["sec_id"]) for l in pos["legs"]] if broker else [None, None]
                reason = None
                if all(v is not None for v in ltps):
                    comb_now = sum(ltps)
                    comb_ent = pos["entry_credit"]
                    mtm_pt = comb_ent - comb_now          # +ve = profit (credit decayed) for a SHORT
                    log.info(f"[BNFSTR] strangle open  credit {comb_ent:.1f}→{comb_now:.1f}  "
                             f"P&L {mtm_pt:+.1f}pt  (tp +{tp_pt:.0f} / sl -{sl_pt:.0f})")
                    if mtm_pt >= tp_pt:
                        reason = "BNF_STRANGLE_TP"
                    elif mtm_pt <= -sl_pt:
                        reason = "BNF_STRANGLE_SL"
                if now_hm >= exit_hm:
                    reason = "BNF_STRANGLE_EOD"
                if reason:
                    log.info(f"[EXIT] strangle — {reason}")
                    _exit_strangle(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            # ── ENTRY: SELL 6-strike OTM CE + PE, only in the 09:20 window ──
            in_entry_window = (now_hm >= entry_hm) and (now.hour * 60 + now.minute) < (entry_hm[0] * 60 + entry_hm[1] + win_min)
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 1)) and now_hm < exit_hm:
                if _is_expiry_day():
                    log.info("[BNFSTR] BankNifty expiry day — no entry (matches backtest skip_expiry)")
                elif in_entry_window:
                    if spot is None or spot <= 0:
                        log.warning("[BNFSTR] no spot at entry window — retrying")
                    else:
                        log.info(f"[BNFSTR] ENTRY WINDOW  spot={spot:.1f}  off={tc.get('strike_offset')} OTM  "
                                 f"trades={trades_today}")
                        pos = _enter_strangle(strategy_id, sym, spot, tc, mode, bname, log)
                        if pos:
                            trades_today += 1; save_state(strategy_id, pos)
                elif now_hm < entry_hm:
                    log.info(f"[BNFSTR] waiting for entry window {entry_hm[0]:02d}:{entry_hm[1]:02d}  spot={spot or 0:.0f}")

            _write_watch(strategy_id, sym, spot or 0, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[BNFSTR] Stopped by user"); break
        except Exception as e:
            log.error(f"[BNFSTR] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _enter_strangle(strategy_id, sym, spot, tc, mode, bname, log):
    """SELL OTM CE + OTM PE via the gateway. Both legs use +strike_offset — for a PE,
    dhan_master.get_option_contract INVERTS the offset (positive → strike BELOW spot),
    so +offset lands OTM on BOTH sides (never negative-PE — TRAP #140 guard). Returns
    pos dict or None (rolls back leg-1 if leg-2 fails → never a lone naked leg)."""
    import execution_gateway as gw
    lots = int(tc.get("qty", 1))
    off = int(tc.get("strike_offset", 6))
    gid = f"BNFSTR_{int(time.time())}"
    legs = []
    for opt_type in ("CE", "PE"):
        res_c = dhan_master.get_option_contract(sym, spot, opt_type, off)  # +off = OTM both sides
        if not res_c or not res_c[0]:
            log.error(f"[BNFSTR] {sym} {opt_type} contract not found — abort")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        sec_id, trad_sym, lot_size = res_c
        try:
            res = gw.execute_signal(strategy_id, sym, "SELL", lots, (lot_size or 1), sec_id, trad_sym,
                                    seg="NSE_FNO", mode=mode, broker_name=bname, tag="BNFSTR",
                                    instrument="options", group_id=gid, log=log.info)
        except Exception as e:
            log.error(f"[BNFSTR] {opt_type} entry error: {e}")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        if not res["ok"]:
            log.info(f"[BNFSTR] {opt_type} entry not ok — {res.get('status')}: {res.get('reason')}")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        prem = res.get("price")
        if not prem or prem <= 0:
            prem = _opt_ltp(_get_broker(bname), sec_id) or 0.0
        legs.append(dict(opt_type=opt_type, sec_id=sec_id, trad_sym=trad_sym,
                         qty=res["qty"], entry_prem=round(float(prem), 2)))
    credit = round(sum(l["entry_prem"] for l in legs), 2)
    log.info(f"  ★ ENTRY SHORT STRANGLE → SELL CE {legs[0]['trad_sym']} @{legs[0]['entry_prem']} + "
             f"PE {legs[1]['trad_sym']} @{legs[1]['entry_prem']}  credit={credit}  (off={off} OTM)")
    return dict(legs=legs, entry_credit=credit if credit > 0 else 1.0, group_id=gid,
                tp_pt=float(tc.get("tp_pt", 50)), sl_pt=float(tc.get("sl_pt", 50)),
                entry_spot=round(spot, 1))


def _rollback(strategy_id, sym, legs, mode, bname, log):
    """If leg-1 sold but leg-2 failed, BUY back leg-1 so we never hold a lone naked leg."""
    for l in legs:
        try:
            import execution_gateway as gw
            log.info(f"[BNFSTR] rollback — buying back lone leg {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side="SELL", seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="BNFSTR", instrument="options", reason="BNF_STRANGLE_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[BNFSTR] rollback failed for {l['trad_sym']}: {e}")


def _exit_strangle(strategy_id, sym, pos, mode, bname, reason, log):
    import execution_gateway as gw
    for l in pos["legs"]:
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side="SELL", seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="BNFSTR", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[BNFSTR] exit leg {l['trad_sym']} err: {e} (pos_monitor EOD still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "levels": {"entry_time": tc.get("entry_time"), "exit_time": tc.get("exit_time"),
                           "off": tc.get("strike_offset")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else 1,
                             "signal": "" if pos is None else "short-strangle",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="BankNifty 9:20 SHORT strangle Trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="bnf_strangle_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
