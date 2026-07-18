#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  dist_ma_trader.py — Distance-from-20EMA extreme-oversold BUY (POSITIONAL) ║
# ║  Research : scratch/dist_ma/  (dist_ma.py backtest + dist_ma_engine.py)     ║
# ║            OOS +1.6%/trade PF 1.31; Rs1L 8-slot ~13.6% CAGR (portfolio.py)  ║
# ║  Config   : ../../nifty_config.json  →  key: "distma_v1"                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# EDGE: a large/mid-cap stock rarely trades >10% BELOW its 20 EMA. When it does
# and a bullish reversal candle forms, price snaps back toward the mean over the
# next few weeks. BUY-side only, EQUITY DELIVERY (CNC), positional (holds weeks).
#
# ⚠️ STAGED: PAPER + active:false. This is a POSITIONAL EQUITY strategy — unlike
# every other trader here it holds OVERNIGHT for weeks (allow_overnight lane,
# risk_gate._ALWAYS_OVERNIGHT). Decisions come from the SAME engine the backtest
# parity-check covers (dist_ma_engine.replay_upto) — live == backtest (Rule 10).
# Every order via execution_gateway (RMS-gated, order_store-recorded).
#
# DATA: acts on COMPLETED DAILY bars from the equity-daily lake (once per new
# trading day, not an intraday loop). A nightly lake update advances "today".

import argparse
import json
import logging
import math
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# IPv4 force — VPS pe IPv6 hoti hai, Dhan reject karta hai (DH-905)
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR    = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "nifty_config.json"
STATE_FILE  = lambda sid: BASE_DIR / "data" / f"{sid}_state.json"

sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE_DIR / "scratch" / "dist_ma"))   # engine lives here (Rule 6B, single source)
import _paths  # noqa: F401
import dist_ma_engine as engine
import dist_ma as dm

DEFAULTS = {
    "active": False, "mode": "paper",
    "capital": 100000,       # rupee book this strategy manages
    "max_slots": 10,         # max concurrent positions (diversification — see FINDINGS.md)
    "symbols": "",           # blank = whole equity-daily lake; else comma list
    # signal params — MUST match the validated engine (dist_ma_engine.DEFAULTS)
    "thresh": -10.0, "look": 3, "entry_win": 3, "max_hold": 40, "sl_atr": 1.5,
}


def _make_logger(sid):
    lf = BASE_DIR / "logs" / f"{sid}.log"; lf.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(sid); lg.setLevel(logging.INFO); lg.propagate = False
    if not lg.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(lf); fh.setFormatter(fmt); lg.addHandler(fh)
        if getattr(sys.stdout, "isatty", lambda: False)():
            sh = logging.StreamHandler(); sh.setFormatter(fmt); lg.addHandler(sh)
    return lg


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def load_creds():
    c = json.loads(CONFIG_FILE.read_text()); return c["jwt_token"], c["client_id"]

def _order_broker(cfg):
    return (cfg.get("broker") or cfg.get("order_broker") or "").lower() or None

def load_config(sid):
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**DEFAULTS, **cfg.get(sid, {})}
    except Exception:
        return dict(DEFAULTS)

def _engine_cfg(tc):
    return {k: tc.get(k, engine.DEFAULTS[k]) for k in
            ("thresh", "look", "entry_win", "max_hold", "sl_atr", "cost_pct", "slip_pct")
            if k in engine.DEFAULTS or k in tc}

def _symbols(tc):
    raw = tc.get("symbols") or ""
    if isinstance(raw, list):
        syms = [str(s).strip().upper() for s in raw if str(s).strip()]
    elif isinstance(raw, str) and raw.strip():
        import re
        syms = [s.strip().upper() for s in re.split(r"[,\s]+", raw) if s.strip()]
    else:
        syms = dm.symbols()                    # blank = whole lake
    return [s for s in syms if s]


# ─────────────────────── book state: persist + recover ─────────────────────
# POSITIONAL: book is NOT date-scoped — positions live across day-rollover and
# the daily restart. book[sym] = {entry_date, entry, sl, placed, qty, group,
# sec_id}. `placed`=True means a real (paper) order was sent (slot used); a
# shadow (slots-full) trade is tracked but never ordered — matches portfolio.py.
def save_book(sid, book, last_date):
    try:
        STATE_FILE(sid).write_text(json.dumps({"book": book, "last_date": last_date}))
    except Exception:
        pass

def load_book(sid):
    try:
        s = json.loads(STATE_FILE(sid).read_text())
        return s.get("book") or {}, s.get("last_date")
    except Exception:
        return {}, None

def _recover(sid, book, log):
    """Keep only `placed` positions that order_store still shows open (positional →
    7-day lookback, TRAP #119). A placed position order_store no longer shows =
    externally closed → drop. Shadow positions are engine-only, kept as-is."""
    try:
        import order_store
        t = ist_now()
        opens = order_store.trades_for_range(
            (t - timedelta(days=90)).strftime("%Y-%m-%d"), t.strftime("%Y-%m-%d")).get("open") or []
        live_secs = {str(p.get("sec_id")) for p in opens if p.get("strategy") == sid}
        for sym, b in list(book.items()):
            if b.get("placed") and str(b.get("sec_id")) not in live_secs:
                log.info(f"[RECOVER] {sym} placed-open but order_store flat — dropping (externally closed)")
                book.pop(sym, None)
        log.info(f"[RECOVER] book has {sum(1 for b in book.values() if b.get('placed'))} placed + "
                 f"{sum(1 for b in book.values() if not b.get('placed'))} shadow positions")
    except Exception as e:
        log.warning(f"[RECOVER] order_store check failed ({e}) — keeping persisted book")
    return book


def _equity_ref(sym):
    """(sec_id, trad_sym) for an NSE equity. trad_sym = the symbol itself."""
    try:
        import dhan_master
        info = dhan_master.get_equity_info(sym)
        sec = info[0] if info else None
        return (str(sec) if sec else None), sym
    except Exception:
        return None, sym


# ─────────────────────────── daily decision ────────────────────────────────
def process_day(sid, tc, D, book, mode, bname, log, place=True):
    """Run one completed trading day's decisions. Mutates `book`. `place=False`
    = dry-run (no orders, no gateway) for verification."""
    ecfg = _engine_cfg(tc)
    cap = float(tc.get("capital", 100000)); slots = int(tc.get("max_slots", 10))
    alloc = cap / max(slots, 1)
    syms = _symbols(tc)
    placed_ct = sum(1 for b in book.values() if b.get("placed"))
    n_enter = n_exit = n_shadow = 0

    for sym in syms:
        try:
            trades, openpos = engine.replay_upto(sym, D, ecfg)
        except Exception:
            continue
        b = book.get(sym)

        # ── EXIT: tracked position no longer open per engine ──
        if b and (openpos is None or openpos["entry_date"] != b["entry_date"]):
            # find the completed trade matching our entry_date for exit price/reason
            done = next((t for t in trades if t["entry_date"] == b["entry_date"]), None)
            reason = (done or {}).get("reason", "DISTMA_EXIT")
            if b.get("placed") and place:
                sec, tsym = b.get("sec_id"), sym
                import execution_gateway as gw
                gw.execute_exit(sid, sym, sec, tsym, int(b.get("qty") or 0), entry_side="BUY",
                                seg="NSE_EQ", mode=mode, broker_name=bname, tag="DISTMA",
                                instrument="equity", product="CNC",
                                reason=f"DISTMA_{reason}", log=log.info)
            log.info(f"[EXIT] {sym} ({'placed' if b.get('placed') else 'shadow'}) "
                     f"entry {b['entry_date']} → {reason}")
            book.pop(sym, None); n_exit += 1
            b = None
            placed_ct = sum(1 for bb in book.values() if bb.get("placed"))

        # ── ENTER: engine opened a position TODAY and we aren't tracking it ──
        if openpos and openpos["entry_date"] == D and not book.get(sym):
            entry = float(openpos["entry"])
            can = placed_ct < slots
            qty = int(alloc / entry) if entry > 0 else 0
            rec = {"entry_date": D, "entry": round(entry, 2), "sl": round(float(openpos["sl"]), 2),
                   "placed": False, "qty": 0, "sec_id": None}
            if can and qty > 0:
                sec, tsym = _equity_ref(sym)
                ok = False
                if place and sec:
                    import execution_gateway as gw
                    r = gw.execute_signal(sid, sym, "BUY", qty, 1, sec, tsym, seg="NSE_EQ",
                                          mode=mode, broker_name=bname, tag="DISTMA",
                                          instrument="equity", product="CNC", est_price=entry,
                                          log=log.info)
                    ok = bool(r.get("ok")); qty = r.get("qty", qty) if ok else qty
                elif not place:
                    ok = True                       # dry-run: pretend placed
                if ok:
                    rec.update(placed=True, qty=qty, sec_id=(sec if place else "DRY"))
                    placed_ct += 1
                    log.info(f"[ENTER] {sym} BUY {qty} @ ~{entry:.1f}  SL {rec['sl']:.1f}  "
                             f"(Rs{qty*entry:,.0f}, slot {placed_ct}/{slots})")
                    n_enter += 1
                else:
                    log.info(f"[ENTER-SKIP] {sym} order not ok — tracking as shadow")
                    n_shadow += 1
            else:
                log.info(f"[SHADOW] {sym} signal @ ~{entry:.1f} but slots full "
                         f"({placed_ct}/{slots}) — tracked, no order")
                n_shadow += 1
            book[sym] = rec

    log.info(f"[DAY {D}] enter={n_enter} exit={n_exit} shadow={n_shadow}  "
             f"placed_open={sum(1 for b in book.values() if b.get('placed'))}")
    return book


# ─────────────────────────────── main loop ─────────────────────────────────
def run(paper_mode=True, strategy_id="distma_v1"):
    log = _make_logger(strategy_id)
    try:
        from singleton_guard import acquire_singleton
        if not acquire_singleton(strategy_id):
            log.warning(f"[SINGLETON] another {strategy_id} live — exiting"); return
    except Exception:
        pass
    log.info("=" * 62)
    log.info(f"  dist_ma_trader.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    book, last_date = load_book(strategy_id)
    book = _recover(strategy_id, book, log)

    while True:
        try:
            tc = load_config(strategy_id)
            mode = "paper" if paper_mode else tc.get("mode", "paper")
            bname = _order_broker(tc)
            if not tc.get("active", False):
                log.info("[DISTMA] Paused — active=false"); time.sleep(120); continue

            D = engine.latest_date()
            if not D:
                log.warning("[DISTMA] no daily lake data"); time.sleep(300); continue
            if D == last_date:
                log.info(f"[DISTMA] up to date ({D}) — waiting for next trading day")
                time.sleep(300); continue

            log.info(f"[DISTMA] new completed day {D} (last {last_date}) — running decisions")
            book = process_day(strategy_id, tc, D, book, mode, bname, log, place=True)
            last_date = D
            save_book(strategy_id, book, last_date)

        except KeyboardInterrupt:
            log.info("[DISTMA] stopped by user"); break
        except Exception as e:
            log.error(f"[DISTMA] loop error: {e}", exc_info=True)
        time.sleep(120)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Distance-from-20EMA positional equity trader")
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--id", default="distma_v1")
    args = ap.parse_args()
    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS!\nCtrl+C within 5s to cancel...\n"); time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
