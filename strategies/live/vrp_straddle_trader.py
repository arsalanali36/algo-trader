#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  vrp_straddle_trader.py — VRP "panic-fade" weekly short straddle (V1)      ║
# ║  Research : scratch/nifty_trend/vrp_*  (real IV lake, PF 4.4, p=0.0002)     ║
# ║  Design   : _ADR/ADR-006-positional-overnight-lane.md                      ║
# ║  Config   : ../../nifty_config.json  →  key: "vrp_v1"                      ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  EDGE: sell NIFTY vol ONLY when fear is high (IV-rank>0.80) — a "panic     │
# │  premium harvester". Structurally = 06_shortvol's 4-leg iron-fly, but:     │
# │    (1) ENTRY gated on vrp_signal (IV-rank>0.80), ONCE PER EXPIRY (weekly).  │
# │    (2) POSITIONAL — held to EXPIRY, NOT squared off at 3:15. Relies on the  │
# │        per-strategy `allow_overnight` RMS flag (ADR-006) so pos_monitor     │
# │        skips the blanket EOD close for this strategy only. Expiry-day       │
# │        squareoff (2:55) + ITM guard + RMS daily-loss breaker STILL apply.   │
# │    (3) STATE PERSISTS ACROSS DAYS (an overnight position is NOT forgotten   │
# │        on the day-rollover / the 9:10 restart — TRAP #3/#28).               │
# │  Defined-risk ONLY (wings mandatory — never naked overnight). Every leg     │
# │  via execution_gateway (RMS-gated, order_store-recorded).                   │
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
    t = (ist_now().hour, ist_now().minute)
    return MARKET_OPEN <= t < MARKET_CLOSE

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None


DEFAULTS = {
    "active": False, "mode": "paper", "symbol": "NIFTY",
    "wing_off": 10, "tp_frac": 0.5, "qty": 1,
    "iv_source": "atm",        # "atm" (live ATM CE/PE IV) or "vix" (India VIX LTP)
    "vix_sec_id": "21",        # India VIX IDX_I sec_id (verify vs scrip master before live)
    "enter_thr": 0.80, "entry_hm": [9, 30],
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


def _live_iv(tc, sym, spot, broker, token, cid, log):
    """today's NIFTY IV from the configured source. "atm" = mean of live ATM CE/PE
    implied-vol (matches the backtest); "vix" = India VIX LTP. None if unavailable
    (→ no entry; never guess an IV)."""
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
            log.warning(f"[VRP] VIX read failed: {e}"); return None
    # atm: BS-invert the live ATM CE/PE premium to implied vol. The Dhan/Kite quote
    # returns only LTP (no IV field), so we solve IV ourselves — the SAME definition
    # as the lake seed's BS-inverted IV, so live rank matches the seeded history.
    if broker is None:
        return None
    ce = dhan_master.get_option_contract(sym, spot, "CE", 0)
    pe = dhan_master.get_option_contract(sym, spot, "PE", 0)
    if not (ce and ce[0] and pe and pe[0]):
        return None
    k = _atm_strike(ce[1], spot)                      # exact strike from trad_sym
    tte = _tte_years(ce[1])                            # yrs to this weekly expiry
    if k is None or tte is None or tte <= 0:
        return None
    ce_prem, pe_prem = _opt_ltp(broker, ce[0]), _opt_ltp(broker, pe[0])
    iv = vrp_signal.atm_iv_from_premiums(spot, k, ce_prem, pe_prem, tte)
    if iv is None:
        log.info(f"[VRP] ATM IV solve failed (ce={ce_prem} pe={pe_prem} K={k} tte={tte:.4f}) — no entry")
    return iv


def _this_expiry(sym, spot):
    """expiry token of the current weekly ATM contract — the dedup key (one entry per
    expiry). Read straight from the broker's OWN trad_sym date field
    (NIFTY-28Jun2026-23950-CE → '28Jun2026'), never string-guessed (TRAP #11)."""
    try:
        res = dhan_master.get_option_contract(sym, spot, "CE", 0)
        if res and res[1] and "-" in res[1]:
            return res[1].split("-")[1]
    except Exception:
        pass
    return None


def _atm_strike(trad_sym, spot):
    """exact ATM strike from the resolved trad_sym (NIFTY-28Jun2026-23950-CE → 23950).
    Falls back to nearest-50 of spot if the symbol can't be parsed."""
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


def _tte_years(trad_sym):
    """years to this weekly expiry (15:30 IST on the expiry date parsed from trad_sym).
    None if unparseable — caller then skips (never solve IV against a guessed T)."""
    try:
        tok = str(trad_sym).split("-")[1]             # e.g. 28Jun2026
        exp = datetime.strptime(tok, "%d%b%Y").replace(hour=15, minute=30)
        secs = (exp - ist_now()).total_seconds()
        return secs / (365.25 * 24 * 3600) if secs > 0 else None
    except Exception:
        return None


# ─────────────────────────── state persist / recover ───────────────────────
# NOTE: positional — state is NOT date-scoped (an overnight position must survive
# the day-rollover and the daily 9:10 restart). `last_entry_expiry` dedups entries
# to one per weekly expiry.
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
            log.info(f"[RECOVER] re-attached overnight straddle {[l['trad_sym'] for l in pos['legs']]}")
            return pos, last_exp
        log.info("[RECOVER] state had legs but order_store shows some closed — clearing pos.")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — starting flat")
    return None, last_exp


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="vrp_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  vrp_straddle_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    pos, last_entry_expiry = _recover(strategy_id, log)
    iv_recorded_today = None       # date-str we already appended IV for

    while True:
        try:
            now = ist_now()
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            bname = _order_broker(tc)
            sym = tc.get("symbol", "NIFTY")

            if not tc.get("active", False):
                log.info("[VRP] Paused — active=false"); time.sleep(60); continue
            if not is_market_open():
                time.sleep(60); continue

            token, cid = load_creds()
            spot = fetch_spot(token, cid)
            if not spot:
                log.warning("[VRP] no spot"); time.sleep(20); continue
            broker = _get_broker(bname)

            # ── record today's IV into the rank history (once/day, restart-safe) ──
            today = str(now.date())
            iv_today = _live_iv(tc, sym, spot, broker, token, cid, log)
            if iv_today is not None and iv_recorded_today != today:
                hist = vrp_signal.record_today(today, iv_today)
                vrp_signal.save_history(hist)
                iv_recorded_today = today

            # ── manage OPEN straddle: %-of-credit profit-take (positional, NO 3:15) ──
            if pos is not None:
                reason = None
                if broker:
                    ltps = {l["sec_id"]: _opt_ltp(broker, l["sec_id"]) for l in pos["legs"]}
                    if all(v is not None for v in ltps.values()):
                        V = sum(l["w"] * ltps[l["sec_id"]] for l in pos["legs"])
                        pnl_frac = (V - pos["entry_val"]) / pos["ref"]
                        log.info(f"[VRP] open V {pos['entry_val']:+.1f}→{V:+.1f} "
                                 f"P&L {pnl_frac*100:+.0f}% of credit (tp+{int(pos['tp_frac']*100)})")
                        if pnl_frac >= pos["tp_frac"]:
                            reason = "VRP_TP"
                if reason:
                    log.info(f"[EXIT] straddle — {reason}")
                    _exit_straddle(strategy_id, sym, pos, mode, bname, reason, log)
                    pos = None
                # NOTE: no 3:15 force-exit here — held overnight to expiry. Expiry-day
                # squareoff (2:55) / ITM / RMS-loss are enforced by pos_monitor_loop.
                save_state(strategy_id, pos, last_entry_expiry)

            # ── ENTRY: IV-rank>0.80, once per expiry, after entry_hm ──
            if pos is None:
                eh, em = tc.get("entry_hm", [9, 30])
                cur_exp = _this_expiry(sym, spot)
                already = (cur_exp is not None and cur_exp == last_entry_expiry)
                if (now.hour, now.minute) >= (eh, em) and not already and iv_today is not None:
                    elig, rank = vrp_signal.rank_for(today, iv_today, thr=float(tc.get("enter_thr", 0.80)))
                    if elig:
                        log.info(f"[VRP] IV={iv_today:.1f} rank={rank:.2f} ≥ thr → ENTER (expiry {cur_exp})")
                        pos = _enter_straddle(strategy_id, sym, spot, tc, mode, bname, cur_exp, log)
                        if pos:
                            last_entry_expiry = cur_exp
                        else:
                            last_entry_expiry = cur_exp   # don't retry this expiry if entry declined
                        save_state(strategy_id, pos, last_entry_expiry)
                    elif rank is not None:
                        log.info(f"[VRP] IV={iv_today:.1f} rank={rank:.2f} < thr — no entry")

            _write_watch(strategy_id, sym, spot, pos, tc, now)

        except KeyboardInterrupt:
            log.info("[VRP] Stopped by user"); break
        except Exception as e:
            log.error(f"[VRP] Loop error: {e}", exc_info=True)
        time.sleep(30)


def _resolve(sym, spot, opt_type, off):
    r = dhan_master.get_option_contract(sym, spot, opt_type, off)
    return r if (r and r[0]) else None


def _enter_straddle(strategy_id, sym, spot, tc, mode, bname, cur_exp, log):
    """4-leg iron condor/fly: BUY wings FIRST (never naked overnight), then SELL ATM
    straddle. Any failure → rollback. Defined-risk mandatory for the overnight hold."""
    import execution_gateway as gw
    import risk_gate
    lots = int(tc.get("qty", 1)); wing = int(tc.get("wing_off", 10))
    gid = f"VRP_{int(time.time())}"
    atm_ce = _resolve(sym, spot, "CE", 0); atm_pe = _resolve(sym, spot, "PE", 0)
    # get_option_contract() inverts the PE offset (positive = OTM/LOWER strike), so the
    # OTM put wing needs a POSITIVE offset. -wing double-negated it → atm_idx+wing = ITM/
    # upper put (wrong side). Same bug/fix as the condor. TRAP #140.
    w_ce = _resolve(sym, spot, "CE", +wing); w_pe = _resolve(sym, spot, "PE", +wing)
    if not all([atm_ce, atm_pe, w_ce, w_pe]):
        log.error("[VRP] contract resolve failed — abort"); return None
    if len({str(atm_ce[0]), str(atm_pe[0]), str(w_ce[0]), str(w_pe[0])}) != 4:
        log.error("[VRP] duplicate strike in legs — abort"); return None
    # Overnight into expiry-day is fine (pos_monitor closes at 2:55); but don't OPEN
    # a fresh position on expiry day itself (no theta left, only gap risk).
    try:
        if risk_gate.is_expiry_day(atm_ce[1], atm_ce[0]):
            log.info("[VRP] expiry day — no fresh entry"); return None
    except Exception:
        pass

    placed = []
    def place(res, side, w, gate):
        sec, tsym, lot = res
        try:
            r = gw.execute_signal(strategy_id, sym, side, lots, (lot or 1), sec, tsym,
                                  seg="NSE_FNO", mode=mode, broker_name=bname, tag="VRP",
                                  instrument="options", group_id=gid, gate=gate, log=log.info)
        except Exception as e:
            log.error(f"[VRP] {side} {tsym} error: {e}"); return False
        if not r["ok"]:
            log.info(f"[VRP] {side} {tsym} not ok — {r.get('status')}: {r.get('reason')}"); return False
        prem = r.get("price") or _opt_ltp(_get_broker(bname), sec) or 0.0
        placed.append(dict(side=side, w=w, sec_id=sec, trad_sym=tsym, qty=r["qty"],
                           entry_prem=round(float(prem), 2)))
        return True

    if not place(w_ce, "BUY", +1, False) or not place(w_pe, "BUY", +1, False):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None
    if not place(atm_ce, "SELL", -1, True):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None
    if not place(atm_pe, "SELL", -1, False):
        _rollback(strategy_id, sym, placed, mode, bname, log); return None

    V = sum(l["w"] * l["entry_prem"] for l in placed)
    ref = abs(V) if abs(V) > 1e-6 else 1.0
    log.info(f"  ★ ENTRY VRP-CONDOR (±{wing}) net={V:+.1f} credit-ref={ref:.1f} expiry={cur_exp}")
    return dict(legs=placed, entry_val=V, ref=ref, group_id=gid, expiry=cur_exp,
                tp_frac=float(tc.get("tp_frac", 0.5)), entry_spot=round(spot, 1), direction="vrp")


def _rollback(strategy_id, sym, legs, mode, bname, log):
    import execution_gateway as gw
    for l in legs:
        try:
            log.info(f"[VRP] rollback — closing {l['trad_sym']}")
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="VRP", instrument="options", reason="VRP_ROLLBACK", log=log.info)
        except Exception as e:
            log.error(f"[VRP] rollback failed {l['trad_sym']}: {e}")


def _exit_straddle(strategy_id, sym, pos, mode, bname, reason, log):
    """Close SHORTS first (buy back) so only defined long wings ever linger."""
    import execution_gateway as gw
    for l in sorted(pos["legs"], key=lambda l: 0 if l["side"] == "SELL" else 1):
        try:
            gw.execute_exit(strategy_id, sym, l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg="NSE_FNO", mode=mode, broker_name=bname,
                            tag="VRP", instrument="options", reason=reason,
                            group_id=pos.get("group_id", ""), log=log.info)
        except Exception as e:
            log.error(f"[VRP] exit leg {l['trad_sym']} err: {e} (pos_monitor still protects)")


def _write_watch(sid, sym, spot, pos, tc, now):
    try:
        data = {"updated": now.strftime("%Y-%m-%d %H:%M:%S"), "strategy": sid,
                "levels": {"iv_source": tc.get("iv_source"), "thr": tc.get("enter_thr"),
                           "wing_off": tc.get("wing_off")},
                "symbols": [{"sym": sym, "close": round(spot, 1),
                             "pos": 0 if pos is None else 2,
                             "signal": "" if pos is None else "vrp-condor",
                             "stop": None, "target": None}]}
        (BASE_DIR / "data" / f"{sid}_watch.json").write_text(json.dumps(data, indent=2))
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="VRP panic-fade weekly short-straddle trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="vrp_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
