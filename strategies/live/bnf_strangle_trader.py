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

def _is_positional(sid):
    try:
        import risk_gate as _rg
        return _rg.max_hold_days(sid) is not None
    except Exception:
        return False

def load_state(sid):
    try:
        d = json.loads(STATE_FILE(sid).read_text())
        pos = d.get("pos")
        if not pos:
            return None
        # Positional (max_hold set): state SURVIVES across days — the carried leg is
        # re-validated against order_store in _recover. Intraday: only today's state.
        if _is_positional(sid) or d.get("date") == str(ist_now().date()):
            return pos
    except Exception:
        pass
    return None

def _recover(sid, log):
    """Restore an open strangle from disk, but ONLY if order_store still shows ALL
    legs open (TRAP #28 — restart must not orphan/duplicate). Positional strategies
    look back across days (trades_for_range); intraday only today (TRAP #119)."""
    pos = load_state(sid)
    if not pos or not pos.get("legs"):
        return None
    try:
        import order_store
        if _is_positional(sid):
            import datetime as _d
            t = ist_now().date(); f = (t - _d.timedelta(days=400)).isoformat()
            opens = order_store.trades_for_range(f, t.isoformat()).get("open") or []
        else:
            opens = order_store.trades_for(ist_now().strftime("%Y-%m-%d")).get("open") or []
        open_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        want = {str(l["sec_id"]) for l in pos["legs"]}
        if want.issubset(open_secs):
            log.info(f"[RECOVER] re-attached open strangle {[l['trad_sym'] for l in pos['legs']]} "
                     f"(entry {pos.get('entry_date','?')})")
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
                last_date = now.date(); trades_today = 0
                if pos is not None and _is_positional(strategy_id):
                    # positional carry: re-validate the held leg against order_store
                    # (don't blindly trust in-memory across the overnight boundary)
                    pos = _recover(strategy_id, log)
                    if pos is not None:
                        # a carried position means this day already "has" a trade — no
                        # fresh 09:20 entry even if it exits mid-window (matches backtest
                        # run_positional: next entry only AFTER the exit day).
                        trades_today = int(tc.get("max_trades_per_day", 1))
                    log.info(f"── New day: {last_date} — positional carry, pos={'OPEN' if pos else 'flat'} ──")
                else:
                    pos = None; save_state(strategy_id, None); log.info(f"── New day: {last_date} ──")

            if not tc.get("active", False):
                log.info("[BNFSTR] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                log.info(f"[BNFSTR] Market closed ({now.strftime('%H:%M')} IST)"); time.sleep(60); continue

            token, cid = load_creds()
            spot = _bnf_spot(token, cid, log)

            # ── positional max-hold: EOD exit fires only once max_hold trading days
            # have elapsed since entry (intraday = always due at exit_time) ──
            _mh = _rg_cnt.max_hold_days(strategy_id)
            _eod_due = True
            if _mh is not None and pos is not None:
                try:
                    import market_calendar as _mc
                    _held = _mc.trading_days_between(pos.get("entry_date"))
                    _eod_due = _held >= _mh
                except Exception:
                    _eod_due = True    # calendar fail -> conservative: allow EOD exit

            # ── manage OPEN strangle ──
            if pos is not None and pos.get("exit_mode") == "basket_rs":
                # 02.10.01 hedged: whole-basket ₹ SL/Target, ORDERED square-off
                reason = None
                combined = _basket_mtm(pos, bname)
                if combined is not None:
                    import position_exit_rules as _per
                    tgt = pos.get("basket_target_rs"); sl = -abs(pos.get("basket_sl_rs") or 0)
                    log.info(f"[BNFSTRH] hedged basket MTM ₹{combined:+,.0f}  "
                             f"(tp +₹{tgt:.0f} / sl -₹{abs(sl):.0f})")
                    d = _per.check_exit(combined, tgt, sl)
                    if d == "target":
                        reason = "BNFSTRH_BASKET_TARGET"
                    elif d == "sl":
                        reason = "BNFSTRH_BASKET_SL"
                if now_hm >= exit_hm and _eod_due:
                    reason = "BNFSTRH_EOD"
                if reason:
                    log.info(f"[EXIT] hedged strangle — {reason}")
                    _exit_hedged(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)
            # ── manage OPEN strangle: combined-premium POINTS exit (naked parent) ──
            elif pos is not None:
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
                if now_hm >= exit_hm and _eod_due:
                    reason = "BNF_STRANGLE_EOD"
                if reason:
                    log.info(f"[EXIT] strangle — {reason}")
                    _exit_strangle(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None; save_state(strategy_id, None)

            # ── ENTRY: SELL 6-strike OTM CE + PE, only in the 09:20 window ──
            in_entry_window = (now_hm >= entry_hm) and (now.hour * 60 + now.minute) < (entry_hm[0] * 60 + entry_hm[1] + win_min)
            # positional: don't open within exp_squareoff_days trading days of expiry
            # (a 1-overnight hold would otherwise carry into expiry-week gamma) —
            # matches backtest run_positional exp_squareoff_days=2.
            _near_expiry = False
            if _mh is not None and pos is None:
                try:
                    import expiry_calendar as _xc, market_calendar as _mc3
                    _buf = int(tc.get("exp_squareoff_days", 2))
                    _nx = _xc.banknifty_next_expiry(now.date())
                    if _nx is not None and _mc3.trading_days_between(now.date(), _nx) <= _buf:
                        _near_expiry = True
                except Exception:
                    _near_expiry = False
            if pos is None and trades_today < int(tc.get("max_trades_per_day", 1)) and now_hm < exit_hm:
                if _is_expiry_day() or _near_expiry:
                    log.info("[BNFSTR] expiry / within exp-buffer — no entry "
                             "(matches backtest skip_expiry / exp_squareoff_days)")
                elif in_entry_window:
                    if spot is None or spot <= 0:
                        log.warning("[BNFSTR] no spot at entry window — retrying")
                    else:
                        log.info(f"[BNFSTR] ENTRY WINDOW  spot={spot:.1f}  off={tc.get('strike_offset')} OTM  "
                                 f"trades={trades_today}")
                        if (tc.get("hedge") or {}).get("enabled") or tc.get("exit_mode") == "basket_rs":
                            pos = _enter_hedged_strangle(strategy_id, sym, spot, tc, mode, bname, log)
                        else:
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
    """SELL OTM CE + OTM PE via the gateway, BALANCED — same lots on BOTH legs.
    Both legs use +strike_offset — for a PE, dhan_master.get_option_contract INVERTS
    the offset (positive → strike BELOW spot), so +offset lands OTM on BOTH sides
    (never negative-PE — TRAP #140 guard).

    The pair is gated + sized ONCE up front (real strangle basket margin), then both
    legs placed at that SAME lot count with gate=False — mirrors _enter_hedged_strangle
    (Rule 6B). Per-leg gating (the old path) let RMS size the two legs to DIFFERENT lot
    counts under tight capital, leaving the strangle lopsided (e.g. CE 150 / PE 120) —
    which is not the symmetric strangle the backtest measured (Rule 10). Sizing the
    pair together keeps it symmetric AND still lets it trade a smaller (equal) size when
    capital is tight. Returns pos dict or None (rolls back leg-1 if leg-2 fails → never
    a lone naked leg). Exit stays the combined-premium tp_pt/sl_pt path (unchanged)."""
    import execution_gateway as gw
    import risk_gate as rg
    lots = int(tc.get("qty", 1))
    off = int(tc.get("strike_offset", 6))
    gid = f"BNFSTR_{int(time.time())}"

    # ── resolve BOTH contracts first (need both to size the pair together) ──
    resolved, lot_size = [], 0
    for opt_type in ("CE", "PE"):
        res_c = dhan_master.get_option_contract(sym, spot, opt_type, off)  # +off = OTM both sides
        if not res_c or not res_c[0]:
            log.error(f"[BNFSTR] {sym} {opt_type} contract not found — abort")
            return None
        sec_id, trad_sym, lot = res_c
        lot_size = int(lot or lot_size or 1)
        resolved.append({"opt_type": opt_type, "sec_id": str(sec_id), "trad_sym": trad_sym})

    # ── pre-warm both legs' LTP (one batched poller call) for pricing/margin ──
    try:
        import ltp_poller, shared_ltp_cache as slc
        watch = [(l["sec_id"], "NSE_FNO") for l in resolved]
        ltp_poller.request_watch(watch)
        for _ in range(10):
            if all(slc.get(s, max_age=30) for s, _sg in watch):
                break
            time.sleep(0.5)
    except Exception:
        pass

    # ── gate the WHOLE strangle ONCE + size the PAIR down together (never lopsided) ──
    try:
        blocked, why, _hard = rg.gating_status(strategy_id, mode=mode)
        if blocked:
            log.info(f"[BNFSTR] RMS blocked — {why}"); return None
    except Exception:
        pass
    # Size the PAIR down together (never lopsided) via the shared helper — same
    # smart size-down every hedged strategy uses (rg.affordable_lots, Rule 6B).
    def _basket_at(n):
        _q = int(n) * lot_size
        return [{"sec_id": l["sec_id"], "entry": "SELL", "qty": _q, "sym": l["trad_sym"],
                 "entry_price": _px_leg(l["sec_id"], bname), "segment": "NSE_FNO"} for l in resolved]
    sized, _need, _why = rg.affordable_lots(strategy_id, lots, _basket_at, mode=mode)
    if sized < 1:
        log.info(f"[BNFSTR] strangle margin fit nahi hua (even 1 lot) — {_why} — skip")
        return None
    if sized < lots:
        log.info(f"[BNFSTR] [SIZE-DOWN] strangle lots {lots} -> {sized} (₹{_need:,.0f}) — both legs")

    # ── place both legs at the SAME sized lots (gate=False; pair already gated) ──
    legs = []
    for l in resolved:
        try:
            res = gw.execute_signal(strategy_id, sym, "SELL", sized, lot_size, l["sec_id"], l["trad_sym"],
                                    seg="NSE_FNO", mode=mode, broker_name=bname, tag="BNFSTR",
                                    source="strategy", instrument="options", group_id=gid,
                                    gate=False, log=log.info)
        except Exception as e:
            log.error(f"[BNFSTR] {l['opt_type']} entry error: {e}")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        if not res.get("ok"):
            log.info(f"[BNFSTR] {l['opt_type']} entry not ok — {res.get('status')}: {res.get('reason')}")
            _rollback(strategy_id, sym, legs, mode, bname, log); return None
        prem = res.get("price")
        if not prem or prem <= 0:
            prem = _opt_ltp(_get_broker(bname), l["sec_id"]) or 0.0
        legs.append(dict(opt_type=l["opt_type"], sec_id=l["sec_id"], trad_sym=l["trad_sym"],
                         qty=res.get("qty") or (sized * lot_size), entry_prem=round(float(prem), 2)))
    credit = round(sum(l["entry_prem"] for l in legs), 2)
    log.info(f"  ★ ENTRY SHORT STRANGLE → SELL CE {legs[0]['trad_sym']} @{legs[0]['entry_prem']} + "
             f"PE {legs[1]['trad_sym']} @{legs[1]['entry_prem']}  credit={credit}  "
             f"(off={off} OTM, {sized} lot × {lot_size} = {sized * lot_size} qty each leg)")
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


# ── 02.10.01 hedged live twin: far-OTM wings + ₹ basket exit ─────────────────
def _px_leg(sec, bname):
    """Warm LTP for a leg (poller cache first, REST fallback)."""
    try:
        import shared_ltp_cache as slc
        v = slc.get(str(sec), max_age=30)
        if v:
            return float(v)
    except Exception:
        pass
    return _opt_ltp(_get_broker(bname), sec) or 0.0


def _enter_hedged_strangle(strategy_id, sym, spot, tc, mode, bname, log):
    """HEDGED short strangle (02.10.01). BUY far-OTM wings FIRST (margin drops),
    then SELL the strike_offset-OTM shorts — unwind-safe, NEVER a lone naked leg.
    Whole structure gated ONCE (basket margin), not per-leg. Mirrors
    atm_straddle_roller._enter_hedged_straddle (Rule 6B). Returns pos dict or None."""
    import execution_gateway as gw
    import strategy_safety as ss
    import risk_gate as rg
    import leg_collision as lc
    lots = int(tc.get("qty", 1))
    off = int(tc.get("strike_offset", 6))
    wing_strikes = int((tc.get("hedge") or {}).get("wing_strikes", 5))
    gid = f"BNFSTRH_{int(time.time())}"

    # Contracts ANOTHER LIVE strategy already holds open — never share a leg with
    # it (broker fungibility would net the two → broken structure, see leg_collision).
    # Paper entries don't reach the broker, so no shift for them (occ stays empty).
    occ = lc.occupied_sec_ids(strategy_id) if str(mode).lower() == "live" else set()

    # ── resolve shorts (off OTM) + wings (off + wing_strikes further OTM) ──
    shorts, wings, lot_size = [], [], 0
    for ot in ("CE", "PE"):
        sec, tsym, lot, _uoff = lc.clear_leg(sym, spot, ot, off, occ,
                                             dhan_master.get_option_contract, log=log.info)
        if not sec:
            log.error(f"[BNFSTRH] {ot} short contract resolve/collision — abort"); return None
        occ.add(str(sec))                       # my leg now occupies this contract
        lot_size = int(lot or lot_size or 1)
        shorts.append({"opt_type": ot, "sec_id": str(sec), "trad_sym": tsym, "lot": lot_size})
        try:
            hsec, htsym, hlot = ss.compute_hedge_target(
                strategy_id, sym, spot, ot, off, quote_fn=None,
                min_strikes_override=wing_strikes, max_premium_override=None,
                max_search=1, log=log.info, avoid=occ)
        except Exception as he:
            hsec = None; log.error(f"[BNFSTRH] {ot} wing resolve err: {he}")
        if not hsec:
            log.error(f"[BNFSTRH] {ot} wing resolve/collision fail — no naked, abort"); return None
        occ.add(str(hsec))
        wings.append({"opt_type": ot, "sec_id": str(hsec), "trad_sym": htsym, "lot": int(hlot or lot_size)})

    q = lots * lot_size

    # ── pre-warm all 4 legs' LTP (poller batches one call) before pricing/margin ──
    try:
        import ltp_poller, shared_ltp_cache as slc
        watch = [(l["sec_id"], "NSE_FNO") for l in shorts + wings]
        ltp_poller.request_watch(watch)
        for _ in range(10):
            if all(slc.get(s, max_age=30) for s, _sg in watch):
                break
            time.sleep(0.5)
    except Exception:
        pass

    # ── gate the WHOLE hedged structure ONCE (RMS + real basket margin) ──
    try:
        blocked, why, _hard = rg.gating_status(strategy_id, mode=mode)
        if blocked:
            log.info(f"[BNFSTRH] RMS blocked — {why}")
            # Record the blocked hedged entry → Broker Orders page (Blocked bucket) +
            # replayable. Basket gated here, before execute_signal. Fail-safe.
            try:
                import skipped_store
                skipped_store.record_skip(strategy=strategy_id, symbol=sym, side="SELL",
                    trad_sym=shorts[0]["trad_sym"], sec_id=shorts[0]["sec_id"],
                    intended_lots=lots, lot_size=lot_size, block_reason=why, mode=mode)
            except Exception:
                pass
            return None
    except Exception:
        pass
    def _basket_at(n):
        _q = int(n) * lot_size
        return [{"sec_id": l["sec_id"], "entry": "SELL", "qty": _q, "sym": l["trad_sym"],
                 "entry_price": _px_leg(l["sec_id"], bname), "segment": "NSE_FNO"} for l in shorts] \
             + [{"sec_id": l["sec_id"], "entry": "BUY", "qty": _q, "sym": l["trad_sym"],
                 "entry_price": _px_leg(l["sec_id"], bname), "segment": "NSE_FNO"} for l in wings]
    _sz, _need, _why = rg.affordable_lots(strategy_id, lots, _basket_at, mode=mode)
    if _sz < 1:
        log.info(f"[BNFSTRH] basket margin fit nahi even for 1 lot — {_why}")
        try:
            import skipped_store
            skipped_store.record_skip(strategy=strategy_id, symbol=sym, side="SELL",
                trad_sym=shorts[0]["trad_sym"], sec_id=shorts[0]["sec_id"],
                intended_lots=lots, lot_size=lot_size, block_reason=_why, mode=mode)
        except Exception:
            pass
        return None
    if _sz < lots:
        log.info(f"[BNFSTRH] [SIZE-DOWN] hedged strangle {lots}->{_sz} lots — ₹{_need:,.0f} ({_why})")
    lots, q = _sz, _sz * lot_size

    # ── place: BUY wings FIRST (margin reduced), then SELL shorts; unwind on fail ──
    placed, legs = [], []

    def _unwind(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(strategy_id, sym, p["sec_id"], p["trad_sym"], q, entry_side=p["side"],
                                seg="NSE_FNO", mode=mode, broker_name=bname, group_id=gid,
                                reason=reason, tag="BNFSTRH", source="strategy", instrument="options",
                                log=log.info)
            except Exception as ue:
                log.error(f"[BNFSTRH] unwind FAIL {p['trad_sym']}: {ue}")

    # POSITIONAL (max_hold set) must carry overnight → NRML; intraday twin stays MIS
    # (default). MIS would be broker-auto-squared ~15:20, defeating the overnight hold.
    _prod = "NRML" if _is_positional(strategy_id) else None
    for grp, side, tag in ((wings, "BUY", "BNFSTRH_HEDGE"), (shorts, "SELL", "BNFSTRH")):
        for l in grp:
            try:
                r = gw.execute_signal(strategy_id, sym, side, lots, l["lot"], l["sec_id"], l["trad_sym"],
                                      seg="NSE_FNO", mode=mode, broker_name=bname, source="strategy",
                                      tag=tag, group_id=gid, gate=False, product=_prod,
                                      extra_tags=(["BNFSTRH", "HEDGE"] if side == "BUY" else ["BNFSTRH"]),
                                      instrument="options", log=log.info)
            except Exception as e:
                r = {"ok": False, "reason": str(e)}
            if not r.get("ok"):
                log.info(f"[BNFSTRH] {l['opt_type']} {side} fail ({r.get('reason') or r.get('status')}) "
                         f"— unwind, abort (no naked)")
                _unwind("BNFSTRH_ABORT"); return None
            placed.append({"sec_id": l["sec_id"], "trad_sym": l["trad_sym"], "side": side})
            prem = r.get("price")
            if not prem or prem <= 0:
                prem = _px_leg(l["sec_id"], bname)
            legs.append(dict(opt_type=l["opt_type"], side=side, sec_id=l["sec_id"], trad_sym=l["trad_sym"],
                             qty=r.get("qty") or q, entry_prem=round(float(prem or 0), 2)))

    net_credit = round(sum(x["entry_prem"] for x in legs if x["side"] == "SELL")
                       - sum(x["entry_prem"] for x in legs if x["side"] == "BUY"), 2)
    # basket cap ab EK resolver se (per-lot scale + apne exit se conflict pe loud)
    import basket_risk as _brisk
    _br = _brisk.resolve("bnf_strangle_hedged", tc, lots=lots, lot_size=lot_size,
                         log=log.info)
    log.info(f"  ★ ENTRY HEDGED STRANGLE ({len(legs)} legs) net_credit≈{net_credit}  "
             f"wings={wing_strikes} strikes OTM  basket ±₹{_br['sl_rs']:.0f}"
             + (f"  [{_br['note']}]" if _br.get("note") else ""))
    return dict(legs=legs, entry_credit=net_credit, group_id=gid, hedged=True, exit_mode="basket_rs",
                basket_target_rs=float(_br["target_rs"]),
                basket_sl_rs=float(_br["sl_rs"]),
                entry_date=str(ist_now().date()),
                entry_spot=round(spot, 1))


def _basket_mtm(pos, bname):
    """Live combined ₹ MTM across all legs (side-aware). None if any leg LTP
    missing/stale → caller FREEZES (never fire on bad data, TRAP #1)."""
    broker = _get_broker(bname)
    total = 0.0
    for l in pos["legs"]:
        ltp = _opt_ltp(broker, l["sec_id"]) if broker else None
        if ltp is None or ltp <= 0:
            return None
        qty = abs(float(l.get("qty") or 0)); ent = float(l.get("entry_prem") or 0)
        total += ((ent - ltp) if str(l.get("side", "SELL")).upper() == "SELL" else (ltp - ent)) * qty
    return total


def _exit_hedged(strategy_id, sym, pos, mode, bname, reason, log):
    """ORDERED basket square-off (shorts buy-to-close FIRST, then wings) via the
    shared execution_gateway helper — never strips the hedge before the shorts."""
    import execution_gateway as gw
    norm = [{"strategy": strategy_id, "symbol": sym, "sec_id": l["sec_id"], "trad_sym": l["trad_sym"],
             "qty": l["qty"], "entry_side": str(l.get("side", "SELL")).upper(), "mode": mode,
             "broker_name": bname, "group_id": pos.get("group_id", ""), "source": "strategy",
             "seg": "NSE_FNO"} for l in pos["legs"]]
    gw.execute_basket_exit(norm, reason=reason, log=log.info)


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
