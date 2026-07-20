#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  vrp_condor_weekly_trader.py — VRP weekly condor, mild-IV gate (FORWARD-PAPER)║
# ║  Research : scratch/nifty_trend/vrp_ungated_backtest.py + vrp_ungated_ideas.py║
# ║            (real WEEK lake; best honest combo, in-sample PF 2.10/Sharpe 2.14) ║
# ║  Design   : _ADR/ADR-006-positional-overnight-lane.md (same overnight lane)  ║
# ║  Config   : ../../nifty_config.json  →  key: "vrp_condor_weekly_v1"          ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  WHY THIS EXISTS (forward-paper only, NOT proven):                        │
# │  Task 6 showed the UNGATED weekly condor is net-negative. The follow-up   │
# │  (vrp_ungated_ideas.py) found the best HONEST lift = a MILD IV-rank gate   │
# │  (≥0.5) + enter at T-4 DTE + exit the day BEFORE expiry (dodge 0DTE gamma).│
# │  In-sample PF 2.10 / Sharpe 2.14 / p=0.032 and it survives a raw OOS split │
# │  — BUT it FAILS deflated-Sharpe once the multi-combo search + tiny sample  │
# │  (n=38, OOS n=10) are accounted for. So it is a FORWARD-PAPER candidate,   │
# │  NOT a proven edge. This trader exists purely to accumulate REAL forward   │
# │  out-of-sample samples so we can re-run DSR in a few months on fresh data. │
# │                                                                            │
# │  FIDELITY (Rule 10 — matches vrp_ungated_backtest exactly):                │
# │    structure : iron condor, sell ATM±body_off(3), buy ATM±(body+wing)(3+5) │
# │    entry     : once per weekly expiry, ~T-4 DTE (first day busdays≤4), 09:20│
# │    gate      : IV-rank ≥ 0.50 (60-day, via vrp_signal — same basis as lake) │
# │    exit       : day BEFORE expiry (busdays≤1) EOD, OR 50%-of-credit target  │
# │    defined-risk: BUY wings FIRST, never naked overnight (ADR-006 mandatory) │
# │                                                                            │
# │  STAGED: PAPER only. allow_overnight is CODE-SET (risk_gate._ALWAYS_OVERNIGHT│
# │  — TRAP #119). Every leg via execution_gateway (RMS-gated, order_store).    │
# │  Known small gap: np.busday_count ignores NSE holidays → on a holiday week  │
# │  entry may land T-3 instead of T-4 (acceptable for forward-paper).          │
# └─────────────────────────────────────────────────────────────────────────┘

import json
import logging
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
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
sys.path.insert(0, str(Path(__file__).resolve().parent))   # for vrp_signal
import _paths  # noqa: F401
import dhan_master
import vrp_signal

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
    # market_calendar. Loop does nothing on any non-trading day (no roll/entry/
    # exit) — see vrp_condor_trader.py for the phantom weekend close+reopen this prevents.
    import market_calendar as mc
    return mc.is_market_open(ist_now(), MARKET_OPEN, MARKET_CLOSE)

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "symbol": "NIFTY",
    "body_off": 3, "wing_off": 5, "qty": 1, "tp_frac": 0.5,
    "iv_source": "atm", "vix_sec_id": "21",
    "enter_thr": 0.50,             # mild IV-rank gate (backtest sweet spot; NOT 0.80)
    "entry_dte": 4,                # enter at ~T-4 trading-days before expiry
    "exit_dte": 1,                 # exit the day BEFORE expiry (avoid 0DTE gamma)
    "entry_hm": [9, 20], "exit_hm": [15, 10],
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


# ─────────────────── expiry / DTE + live IV (matches the backtest) ──────────────────
def _this_expiry_date(sym, spot):
    """(exp_date, key) of the current weekly ATM contract. The expiry DATE comes from
    dhan_master.get_expiry_for_sec_id(sec_id) — NIFTY index trad_syms carry NO day
    (e.g. 'NIFTY-Jul2026-24200-CE'), so the day must NEVER be parsed from the string
    (repo rule / payoff note). key = ISO date string, used as the once-per-cycle dedup."""
    try:
        res = dhan_master.get_option_contract(sym, spot, "CE", 0)
        if res and res[0]:
            d = dhan_master.get_expiry_for_sec_id(res[0])
            if d:
                return d, d.isoformat()
    except Exception:
        pass
    return None, None


def _busdays_to_expiry(today_d, exp_d):
    """business days from today→expiry (T-4 => 4, day-before => 1, expiry-day => 0).
    NSE holidays NOT accounted (rare; may shift entry by ~1 day on a holiday week)."""
    try:
        return int(np.busday_count(today_d, exp_d))
    except Exception:
        return None


def _atm_strike(trad_sym, spot):
    try:
        parts = str(trad_sym).split("-")
        if len(parts) >= 3 and parts[2].isdigit():
            return float(parts[2])
    except Exception:
        pass
    try:
        return float(round(spot / 50.0) * 50)
    except Exception:
        return None


def _tte_years(exp_date):
    """years to this weekly expiry (15:30 IST on exp_date). None if past/invalid.
    exp_date is a datetime.date from dhan_master.get_expiry_for_sec_id (never parsed
    from a trad_sym string — NIFTY index syms carry no day)."""
    try:
        exp = datetime(exp_date.year, exp_date.month, exp_date.day, 15, 30)
        secs = (exp - ist_now()).total_seconds()
        return secs / (365.25 * 24 * 3600) if secs > 0 else None
    except Exception:
        return None


def _live_iv(tc, sym, spot, broker, token, cid, log):
    """today's NIFTY ATM IV — mean of live ATM CE/PE BS-inverted implied-vol (the SAME
    quantity the backtest/lake used), so live rank matches. None → no entry (never guess)."""
    src = str(tc.get("iv_source", "atm")).lower()
    if src == "vix":
        try:
            r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                              json={"IDX_I": [int(tc.get("vix_sec_id", 21))]},
                              headers={"access-token": token, "client-id": cid,
                                       "Content-Type": "application/json"}, timeout=8)
            v = r.json()["data"]["IDX_I"][str(tc.get("vix_sec_id", 21))]["last_price"]
            return float(v) if v and 3 < float(v) < 120 else None
        except Exception as e:
            log.warning(f"[VRPW] VIX read failed: {e}"); return None
    if broker is None:
        return None
    ce = dhan_master.get_option_contract(sym, spot, "CE", 0)
    pe = dhan_master.get_option_contract(sym, spot, "PE", 0)
    if not (ce and ce[0] and pe and pe[0]):
        return None
    k = _atm_strike(ce[1], spot)
    try:
        ed = dhan_master.get_expiry_for_sec_id(ce[0])
    except Exception:
        ed = None
    tte = _tte_years(ed) if ed else None
    if k is None or tte is None or tte <= 0:
        return None
    ce_prem, pe_prem = _opt_ltp(broker, ce[0]), _opt_ltp(broker, pe[0])
    iv = vrp_signal.atm_iv_from_premiums(spot, k, ce_prem, pe_prem, tte)
    if iv is None:
        log.info(f"[VRPW] ATM IV solve failed (ce={ce_prem} pe={pe_prem} K={k} tte={tte:.4f}) — no entry")
    return iv


# ─────────────────────────── state persist / recover ───────────────────────
# Positional (held to ~expiry): state NOT date-scoped — must survive day-rollover +
# the 9:10 restart (TRAP #3/#28/#76). last_entry_expiry dedups to one decision per cycle.
def save_state(sid, pos, last_entry_expiry):
    try:
        STATE_FILE(sid).write_text(json.dumps({"pos": pos, "last_entry_expiry": last_entry_expiry}))
    except Exception:
        pass

def load_state(sid):
    try:
        d = json.loads(STATE_FILE(sid).read_text())
        return d.get("pos"), d.get("last_entry_expiry")
    except Exception:
        return None, None

def _recover(sid, log):
    pos, last_exp = load_state(sid)
    if not pos or not pos.get("legs"):
        return None, last_exp
    try:
        import order_store
        from datetime import timedelta as _td
        _t = ist_now()
        opens = order_store.trades_for_range(
            (_t - _td(days=9)).strftime("%Y-%m-%d"), _t.strftime("%Y-%m-%d")).get("open") or []
        open_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        want = {str(l["sec_id"]) for l in pos["legs"]}
        if want.issubset(open_secs):
            log.info(f"[RECOVER] re-attached weekly condor {[l['trad_sym'] for l in pos['legs']]}")
            return pos, last_exp
        log.info("[RECOVER] state had legs but order_store shows some closed — clearing pos.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None, last_exp


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="vrpw_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  vrp_condor_weekly_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos, last_entry_expiry = _recover(strategy_id, log)
    iv_recorded_today = None
    _last_hb = ""

    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            bname = _order_broker(tc)
            sym = tc.get("symbol", "NIFTY")

            if not tc.get("active", False):
                log.info("[VRPW] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                time.sleep(60); continue

            token, cid = load_creds()
            spot = fetch_spot(token, cid)
            if not spot:
                log.warning("[VRPW] no spot"); time.sleep(20); continue
            broker = _get_broker(bname)

            exp_d, cur_exp = _this_expiry_date(sym, spot)
            dte = _busdays_to_expiry(now.date(), exp_d) if exp_d else None
            eh, em = tc.get("entry_hm", [9, 20]); xh, xm = tc.get("exit_hm", [15, 10])
            entry_dte = int(tc.get("entry_dte", 4)); exit_dte = int(tc.get("exit_dte", 1))

            # ── heartbeat (health-check floor is 180s; this trader idles most of the day) ──
            _hb = f"{now.hour}:{now.minute // 2}"
            if _hb != _last_hb:
                _last_hb = _hb
                log.info(f"[VRPW] spot={spot:.1f} dte={dte} pos={'held' if pos else 'flat'} "
                         f"exp={cur_exp} lastEntryExp={last_entry_expiry}")

            # ── record today's IV into the rank history (once/day, restart-safe) ──
            today = str(now.date())
            iv_today = _live_iv(tc, sym, spot, broker, token, cid, log)
            if iv_today is not None and iv_recorded_today != today:
                hist = vrp_signal.record_today(today, iv_today)
                vrp_signal.save_history(hist)
                iv_recorded_today = today

            # ── manage OPEN condor: 50%-of-credit target OR pre-expiry exit (day before) ──
            if pos is not None:
                reason = None
                if broker:
                    ltps = {l["sec_id"]: _opt_ltp(broker, l["sec_id"]) for l in pos["legs"]}
                    if all(v is not None for v in ltps.values()):
                        V = sum(l["w"] * ltps[l["sec_id"]] for l in pos["legs"])
                        pnl_frac = (V - pos["entry_val"]) / pos["ref"]
                        log.info(f"[VRPW] open V {pos['entry_val']:+.1f}→{V:+.1f} "
                                 f"P&L {pnl_frac*100:+.0f}% of credit (tp+{int(pos['tp_frac']*100)})")
                        if pnl_frac >= pos["tp_frac"]:
                            reason = "VRPW_TP"
                if reason is None and dte is not None and dte <= exit_dte and (now.hour, now.minute) >= (xh, xm):
                    reason = "VRPW_PREEXPIRY"        # exit the day before expiry (dodge 0DTE gamma)
                if reason:
                    log.info(f"[EXIT] weekly condor — {reason} (dte={dte})")
                    _exit_condor(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None
                # NOTE: no 3:15 force-exit (allow_overnight). Expiry-day 2:55 squareoff +
                # ITM guard + RMS daily-LOSS breaker STILL apply (pos_monitor backstop).
                save_state(strategy_id, pos, last_entry_expiry)

            # ── ENTRY: once per expiry, EXACTLY at T-4 DTE, after entry_hm, if IV-rank ≥ thr ──
            # dte == entry_dte (not a 1..4 window) — a cold-start mid-cycle (e.g. boot at dte=1)
            # must NOT enter late; it missed the T-4 decision, exactly like the backtest skips
            # a cycle whose tdte==4 day doesn't exist. Matches vrp_ungated_backtest dte4 exactly.
            if pos is None and dte is not None and dte == entry_dte:
                already = (cur_exp is not None and cur_exp == last_entry_expiry)
                if (now.hour, now.minute) >= (eh, em) and not already and iv_today is not None:
                    elig, rank = vrp_signal.rank_for(today, iv_today, thr=float(tc.get("enter_thr", 0.50)))
                    if elig:
                        log.info(f"[VRPW] IV={iv_today:.1f} rank={rank:.2f} ≥ thr, dte={dte} → ENTER (expiry {cur_exp})")
                        pos = _enter_condor(strategy_id, sym, spot, tc, mode, bname, today, cur_exp, log)
                        last_entry_expiry = cur_exp        # one decision per cycle (fires or not)
                        save_state(strategy_id, pos, last_entry_expiry)
                    elif rank is not None:
                        log.info(f"[VRPW] IV={iv_today:.1f} rank={rank:.2f} < thr, dte={dte} — skip this cycle")
                        last_entry_expiry = cur_exp        # one shot: don't retry later days this cycle
                        save_state(strategy_id, pos, last_entry_expiry)

            _write_watch(strategy_id, sym, spot, pos, tc, now, dte)

        except KeyboardInterrupt:
            log.info("[VRPW] Stopped by user"); break
        except Exception as e:
            log.error(f"[VRPW] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _enter_condor(strategy_id, sym, spot, tc, mode, bname, today, cur_exp, log):
    """4-leg OTM iron condor: BUY wings FIRST (never naked overnight), then SELL the OTM
    body. Any failure → rollback. Defined-risk mandatory for the overnight hold (ADR-006)."""
    import execution_gateway as gw
    import risk_gate
    lots = int(tc.get("qty", 1)); body = int(tc.get("body_off", 3)); wing = int(tc.get("wing_off", 5))
    gid = f"VRPW_{int(time.time())}"
    # NOTE: get_option_contract() already inverts the offset for PE (positive offset = OTM,
    # i.e. LOWER strike). So OTM puts need POSITIVE offsets here — passing -body/-(body+wing)
    # double-negated it → atm_idx+body → ITM puts (wrong side). Matches vrp_ungated_backtest's
    # iron_condor: sell K-short_off (below), buy K-(short_off+wing) (further below). TRAP #140.
    b_ce = _resolve(sym, spot, "CE", +body); b_pe = _resolve(sym, spot, "PE", +body)
    w_ce = _resolve(sym, spot, "CE", +(body + wing)); w_pe = _resolve(sym, spot, "PE", +(body + wing))
    if not all([b_ce, b_pe, w_ce, w_pe]):
        log.error("[VRPW] contract resolve failed — abort"); return None
    if len({str(b_ce[0]), str(b_pe[0]), str(w_ce[0]), str(w_pe[0])}) != 4:
        log.error("[VRPW] duplicate strike in legs — abort"); return None
    try:
        if risk_gate.is_expiry_day(b_ce[1], b_ce[0]):
            log.info("[VRPW] expiry day — no fresh entry (no theta, only gap risk)"); return None
    except Exception:
        pass

    placed = []
    def place(res, side, w, gate):
        sec, tsym, lot = res
        try:
            r = gw.execute_signal(strategy_id, sym, side, lots, (lot or 1), sec, tsym,
                                  seg="NSE_FNO", mode=mode, broker_name=bname, tag="VRPW",
                                  instrument="options", group_id=gid, gate=gate, log=log.info)
        except Exception as e:
            log.error(f"[VRPW] {side} {tsym} error: {e}"); return False
        if not r["ok"]:
            log.info(f"[VRPW] {side} {tsym} not ok — {r.get('status')}: {r.get('reason')}"); return False
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
    log.info(f"  ★ ENTRY VRP-WEEKLY-CONDOR body±{body} wing±{body+wing} net={V:+.1f} ref={ref:.1f} expiry={cur_exp}")
    return dict(legs=placed, entry_val=V, ref=ref, group_id=gid, entry_date=today, expiry=cur_exp,
                tp_frac=float(tc.get("tp_frac", 0.5)), entry_spot=round(spot, 1), direction="vrp_condor_weekly")


def _rollback(strategy_id, sym, legs, mode, bname, log):
    import execution_gateway as gw
    for l in legs:
        try:
            log.info(f"[VRPW] rollback — closing {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="VRPW", instrument="options", reason="VRPW_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[VRPW] rollback failed {l['trad_sym']}: {e}")


def _exit_condor(strategy_id, sym, pos, mode, bname, reason, log):
    """Close SHORTS first (buy back) so only defined long wings ever linger."""
    import execution_gateway as gw
    for l in sorted(pos["legs"], key=lambda l: 0 if l["side"] == "SELL" else 1):
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="VRPW", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[VRPW] exit leg {l['trad_sym']} err: {e} (pos_monitor still protects)")


def _write_watch(sid, sym, spot, pos, tc, now, dte):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "levels": {"body_off": tc.get("body_off"), "wing_off": tc.get("wing_off"),
                           "enter_thr": tc.get("enter_thr"), "dte": dte},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else 2,
                             "signal": "" if pos is None else "vrp-weekly-condor",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="VRP weekly condor (mild-IV gate) forward-paper trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="vrpw_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
