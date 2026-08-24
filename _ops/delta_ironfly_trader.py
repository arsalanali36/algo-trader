"""
delta_ironfly_trader.py — Delta Exchange India daily BTC Iron-Fly (PAPER, forward-test).

Phase-3 Step-2. Validated in Phase-2: SELL ATM CE+PE + BUY ~2000-pt wings (defined
risk), enter ~12h before the 12:00 UTC daily expiry (00:00 UTC / 05:30 IST), hold to
CASH-SETTLEMENT at 12:00 UTC. Significant + slippage-proof on 127 expiries.

24/7 market -> this does NOT go through execute_signal (which blocks weekends / uses
Dhan-Kite lots). Standalone, isolated, PAPER HARD-LOCK (no real order path at all here).
Live comes later (needs a trading key + whitelisted IP + user go-ahead).

Reuses: _ops/delta_feed (chain/ironfly/spot), _core/telegram_notify (alerts).
State: data/delta_paper_trades.json  (positions + completed, restart-safe).
"""
import os
import sys
import json
import time
import datetime as dt

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "_ops") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "_ops"))

import delta_feed  # noqa: E402

STORE = os.path.join(_ROOT, "data", "delta_paper_trades.json")
CONTRACT_VALUE = {"BTC": 0.001, "ETH": 0.01}

DEFAULTS = {
    "enabled": False,       # PAPER hard-lock stays; this just gates firing
    "underlying": "BTC",
    "lots": 1,
    "wing": 2000,           # points OTM for defined-risk wings (BTC)
    "entry_hour_utc": 0,    # 00:00 UTC = 05:30 IST (~12h before 12:00 UTC expiry)
    "entry_window_min": 15, # fire within this many minutes of entry_hour
    "execution": "sim",     # "sim" = internal simulation | "testnet" = real Delta testnet orders
    "exit_min_before": 5,   # testnet: close legs N min before 12:00 UTC settlement
}


# ---------- config (nifty_config._delta_ironfly, defaults if absent) ----------
def _config():
    cfg = dict(DEFAULTS)
    try:
        p = os.path.join(_ROOT, "nifty_config.json")
        with open(p, "r", encoding="utf-8") as f:
            c = (json.load(f) or {}).get("_delta_ironfly") or {}
        cfg.update({k: c[k] for k in c if k in DEFAULTS})
    except Exception:
        pass
    return cfg


# ---------- state store -------------------------------------------------------
def _load():
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"open": None, "completed": []}


def _save(st):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, default=str)
    os.replace(tmp, STORE)


# ---------- pure decision logic (testable) -----------------------------------
def should_enter(cfg, st, now_utc):
    """True if it's the entry window, feature on, and no open position today."""
    if not cfg["enabled"]:
        return False
    if st.get("open"):
        return False
    if now_utc.weekday() >= 5:
        pass  # crypto trades weekends too; no calendar gate
    mins = now_utc.hour * 60 + now_utc.minute
    start = cfg["entry_hour_utc"] * 60
    if not (start <= mins <= start + cfg["entry_window_min"]):
        return False
    # one entry per calendar day
    return st.get("last_entry_day") != now_utc.date().isoformat()


def settle_value(leg, spot):
    """Cash-settlement intrinsic per-BTC for one leg."""
    k = leg["strike"]
    if leg["cp"] == "C":
        return max(0.0, spot - k)
    return max(0.0, k - spot)


def position_pnl(pos, spot):
    """Net P&L in points (per-BTC) at given spot (settlement or live)."""
    pnl = 0.0
    for l in pos["legs"]:
        settle = settle_value(l, spot)
        # short: collected entry, owes settle ; long: paid entry, gets settle
        sign = 1 if l["side"] == "SELL" else -1
        pnl += sign * (l["entry_premium"] - settle)
    return pnl


# ---------- orchestration -----------------------------------------------------
def _tg(text):
    try:
        from _core import telegram_notify as tg
        if tg.is_enabled():
            tg.send_raw(text)
    except Exception:
        pass


def enter(cfg, st, now_utc, log=print):
    setup = delta_feed.ironfly_setup(cfg["underlying"], expiry_code=None,
                                     wing=cfg["wing"])
    if not setup or setup.get("net_credit_pts") is None:
        log("[delta-fly] no clean setup (missing premium) — skip")
        return st
    # entry expiry must be TODAY's (dte 0). ironfly picks soonest; guard:
    if setup.get("dte") not in (0, None):
        log(f"[delta-fly] soonest expiry dte={setup.get('dte')} (no same-day expiry yet) — skip")
        return st
    legs = [{"cp": l["cp"], "strike": l["strike"], "side": l["side"],
             "entry_premium": l["premium"], "symbol": l["symbol"]}
            for l in setup["legs"]]
    lots = cfg["lots"]
    cv = CONTRACT_VALUE.get(cfg["underlying"], 0.001)
    pos = {"underlying": cfg["underlying"], "expiry": setup["expiry"],
           "atm": setup["atm"], "wing": setup["wing"], "lots": lots,
           "contract_value": cv, "legs": legs,
           "net_credit_pts": setup["net_credit_pts"],
           "max_loss_pts": setup["max_loss_pts"],
           "entry_spot": setup["spot"],
           "entry_time": now_utc.isoformat(), "mode": "paper"}
    st["open"] = pos
    st["last_entry_day"] = now_utc.date().isoformat()
    _save(st)
    credit_usd = setup["net_credit_pts"] * cv * lots
    log(f"[delta-fly] PAPER ENTER iron-fly ATM {setup['atm']} exp {setup['expiry']} "
        f"credit {setup['net_credit_pts']:.1f}pts (~${credit_usd:.2f}) lots {lots}")
    _tg(f"🦋 <b>Delta BTC Iron-Fly — PAPER ENTRY</b>\n"
        f"ATM {setup['atm']} · exp {setup['expiry_label']} · {lots} lot\n"
        f"SELL {setup['atm']}CE {legs[0]['entry_premium']:.1f} / "
        f"{setup['atm']}PE {legs[1]['entry_premium']:.1f}\n"
        f"BUY {legs[2]['strike']}CE {legs[2]['entry_premium']:.1f} / "
        f"{legs[3]['strike']}PE {legs[3]['entry_premium']:.1f}\n"
        f"Net credit {setup['net_credit_pts']:.1f} pts (~${credit_usd:.2f}) · "
        f"max loss {setup['max_loss_pts']:.1f} pts")
    return st


def maybe_exit(cfg, st, now_utc, log=print):
    pos = st.get("open")
    if not pos:
        return st
    # expiry is 12:00 UTC of the position's expiry date
    try:
        exp_d = dt.datetime.strptime(pos["expiry"], "%d%m%y").date()
    except ValueError:
        return st
    exp_ts = dt.datetime(exp_d.year, exp_d.month, exp_d.day, 12, 0, tzinfo=dt.timezone.utc)
    if now_utc < exp_ts:
        return st
    spot = delta_feed.spot(cfg["underlying"])
    if spot is None:
        log("[delta-fly] settlement due but no spot — retry next tick")
        return st
    pnl_pts = position_pnl(pos, spot)
    cv, lots = pos["contract_value"], pos["lots"]
    pnl_usd = pnl_pts * cv * lots
    rec = dict(pos)
    rec.update({"exit_time": now_utc.isoformat(), "settle_spot": spot,
                "pnl_pts": pnl_pts, "pnl_usd": pnl_usd})
    st["completed"] = (st.get("completed") or []) + [rec]
    st["open"] = None
    _save(st)
    log(f"[delta-fly] PAPER SETTLE exp {pos['expiry']} spot {spot:.0f} "
        f"P&L {pnl_pts:+.1f}pts (~${pnl_usd:+.2f})")
    _tg(f"🦋 <b>Delta BTC Iron-Fly — PAPER SETTLE</b>\n"
        f"exp {pos['expiry']} · settle spot {spot:.0f}\n"
        f"P&L <b>{pnl_pts:+.1f} pts</b> (~${pnl_usd:+.2f}) · {lots} lot")
    return st


# ============ TESTNET execution (real Delta testnet orders + reconcile) ========
def _code_date(code):
    return dt.datetime.strptime(code, "%d%m%y").date()


def _testnet_broker():
    """DeltaBroker in testnet mode, or None if not testnet/no-creds (SAFETY:
    never place real MAINNET orders from here)."""
    try:
        from brokers.delta_broker import DeltaBroker
    except Exception:
        import sys as _s
        _s.path.insert(0, os.path.join(_ROOT, "brokers"))
        from delta_broker import DeltaBroker
    b = DeltaBroker()
    if not b.testnet or not b.has_creds():
        return None
    return b


def _testnet_ironfly(broker, underlying, wing):
    """Resolve the daily iron-fly legs from TESTNET-listed strikes for the
    nearest dte>=0 expiry. Returns dict or None. Wings BUY first (defined-risk)."""
    prods = broker._products()
    pre = (f"C-{underlying}-", f"P-{underlying}-")
    opts = [s for s, p in prods.items()
            if s.startswith(pre) and p.get("state") == "live"]
    if not opts:
        return None
    today = dt.date.today()
    codes = sorted({s.split("-")[3] for s in opts}, key=_code_date)
    code = next((c for c in codes if (_code_date(c) - today).days >= 0), None)
    if not code:
        return None
    spot = broker.quote(f"{underlying}USD").get("ltp")
    strikes = sorted({int(s.split("-")[2]) for s in opts if s.endswith("-" + code)})
    if not spot or not strikes:
        return None
    atm = min(strikes, key=lambda x: abs(x - spot))
    up = min(strikes, key=lambda x: abs(x - (atm + wing)))
    dn = min(strikes, key=lambda x: abs(x - (atm - wing)))
    U = underlying
    legs = [("BUY", f"C-{U}-{up}-{code}", "C", up),     # wings FIRST (defined-risk)
            ("BUY", f"P-{U}-{dn}-{code}", "P", dn),
            ("SELL", f"C-{U}-{atm}-{code}", "C", atm),
            ("SELL", f"P-{U}-{atm}-{code}", "P", atm)]
    return {"code": code, "atm": atm, "spot": spot, "wing_up": up, "wing_dn": dn,
            "legs": legs}


def _unwind_testnet(broker, placed, lots, log):
    """Close whatever legs were already filled (opposite market orders)."""
    for lg in placed:
        opp = "SELL" if lg["side"] == "BUY" else "BUY"
        try:
            broker.place_order(opp, lg["symbol"], qty=lots, order_type="MARKET")
            log(f"[delta-fly] unwound {lg['symbol']}")
        except Exception as e:
            log(f"[delta-fly] unwind FAIL {lg['symbol']}: {e}")


def _reconcile_testnet(broker, pos, log):
    """Compare our recorded legs to actual testnet positions. Returns status str."""
    live = {p["symbol"]: p for p in broker.positions_detailed()}
    lots = pos["lots"]
    ok, notes = True, []
    for lg in pos["legs"]:
        want = lots if lg["side"] == "BUY" else -lots
        got = live.get(lg["symbol"], {}).get("size")
        if got != want:
            ok = False
            notes.append(f"{lg['symbol']}: want {want} got {got}")
    status = "match" if ok else "MISMATCH: " + "; ".join(notes)
    log(f"[delta-fly] testnet reconcile: {status}")
    return status


def enter_testnet(cfg, st, now_utc, log=print):
    b = _testnet_broker()
    if b is None:
        log("[delta-fly] execution=testnet but broker not testnet/no-creds — SKIP "
            "(never places mainnet real orders)")
        return st
    setup = _testnet_ironfly(b, cfg["underlying"], cfg["wing"])
    if not setup:
        log("[delta-fly] testnet iron-fly resolve failed — skip")
        return st
    lots = cfg["lots"]
    placed = []
    for side, sym, cp, strike in setup["legs"]:
        r = b.place_order(side, sym, qty=lots, order_type="MARKET")
        if r.get("status") == "rejected":
            log(f"[delta-fly] leg REJECTED {side} {sym}: {r.get('reason')} — unwinding")
            _unwind_testnet(b, placed, lots, log)
            return st
        placed.append({"cp": cp, "strike": strike, "side": side, "symbol": sym,
                       "entry_fill": r.get("fill_price"), "order_id": r.get("order_id")})
    cv = CONTRACT_VALUE.get(cfg["underlying"], 0.001)
    credit = sum((l["entry_fill"] or 0) for l in placed if l["side"] == "SELL") \
        - sum((l["entry_fill"] or 0) for l in placed if l["side"] == "BUY")
    pos = {"underlying": cfg["underlying"], "expiry": setup["code"],
           "atm": setup["atm"], "wing": cfg["wing"], "lots": lots,
           "contract_value": cv, "legs": placed, "entry_spot": setup["spot"],
           "net_credit_pts": credit, "entry_time": now_utc.isoformat(),
           "mode": "testnet"}
    pos["reconcile"] = _reconcile_testnet(b, pos, log)
    st["open"] = pos
    st["last_entry_day"] = now_utc.date().isoformat()
    _save(st)
    log(f"[delta-fly] TESTNET ENTER iron-fly ATM {setup['atm']} exp {setup['code']} "
        f"credit {credit:.1f}pts lots {lots} | reconcile: {pos['reconcile']}")
    _tg(f"🦋 <b>Delta BTC Iron-Fly — TESTNET ENTRY</b>\n"
        f"ATM {setup['atm']} · exp {setup['code']} · {lots} lot\n"
        f"legs: " + " · ".join(f"{l['side'][0]} {l['strike']}{l['cp']}@{l['entry_fill']}"
                               for l in placed) + "\n"
        f"net credit {credit:.1f} pts · reconcile: {pos['reconcile']}\n"
        f"(paper testnet — visible on Delta testnet platform)")
    return st


def maybe_exit_testnet(cfg, st, now_utc, log=print):
    pos = st.get("open")
    if not pos or pos.get("mode") != "testnet":
        return st
    try:
        exp_d = _code_date(pos["expiry"])
    except ValueError:
        return st
    exp_ts = dt.datetime(exp_d.year, exp_d.month, exp_d.day, 12, 0, tzinfo=dt.timezone.utc)
    close_at = exp_ts - dt.timedelta(minutes=int(cfg.get("exit_min_before", 5)))
    if now_utc < close_at:
        return st
    b = _testnet_broker()
    if b is None:
        log("[delta-fly] testnet exit: broker unavailable — retry next tick")
        return st
    live = b.positions()
    cv, lots = pos["contract_value"], pos["lots"]
    net_pts = 0.0
    for lg in pos["legs"]:
        held = abs(live.get(lg["symbol"], 0) or 0)
        exit_fill = None
        if held > 0:
            opp = "SELL" if lg["side"] == "BUY" else "BUY"
            r = b.place_order(opp, lg["symbol"], qty=held, order_type="MARKET")
            exit_fill = r.get("fill_price")
        ef = exit_fill if exit_fill is not None else 0.0
        sign = 1 if lg["side"] == "SELL" else -1
        net_pts += sign * ((lg["entry_fill"] or 0) - ef)
        lg["exit_fill"] = ef
    pnl_usd = net_pts * cv * lots
    rec = dict(pos)
    rec.update({"exit_time": now_utc.isoformat(), "pnl_pts": net_pts, "pnl_usd": pnl_usd})
    st["completed"] = (st.get("completed") or []) + [rec]
    st["open"] = None
    _save(st)
    log(f"[delta-fly] TESTNET EXIT exp {pos['expiry']} P&L {net_pts:+.1f}pts "
        f"(~${pnl_usd:+.2f})")
    _tg(f"🦋 <b>Delta BTC Iron-Fly — TESTNET EXIT</b>\n"
        f"exp {pos['expiry']} · P&L <b>{net_pts:+.1f} pts</b> (~${pnl_usd:+.2f}) · {lots} lot")
    return st


def tick(now_utc=None, log=print):
    cfg = _config()
    st = _load()
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    testnet = str(cfg.get("execution", "sim")).lower() == "testnet"
    if testnet:
        st = maybe_exit_testnet(cfg, st, now_utc, log)
        if should_enter(cfg, st, now_utc):
            st = enter_testnet(cfg, st, now_utc, log)
    else:
        st = maybe_exit(cfg, st, now_utc, log)
        if should_enter(cfg, st, now_utc):
            st = enter(cfg, st, now_utc, log)
    return st


def run_loop(interval=60):
    print(f"[delta-fly] paper loop started (entry {DEFAULTS['entry_hour_utc']:02d}:00 UTC, "
          f"settle 12:00 UTC). enabled={_config()['enabled']}")
    while True:
        try:
            tick()
        except Exception as e:
            print(f"[delta-fly] tick error: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--dry", action="store_true", help="one tick now, print state")
    args = ap.parse_args()
    if args.loop:
        run_loop()
    else:
        st = tick()
        print(json.dumps({"open": st.get("open"),
                          "completed_n": len(st.get("completed") or [])},
                         indent=1, default=str))
