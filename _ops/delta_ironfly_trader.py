"""
delta_ironfly_trader.py — Delta Exchange India daily BTC Iron-Fly (PAPER, forward-test).

Phase-3 Step-2. Validated in Phase-2: SELL ATM CE+PE + BUY ~2000-pt wings (defined
risk), enter ~12h before the 12:00 UTC daily expiry (00:00 UTC / 05:30 IST), hold to
CASH-SETTLEMENT at 12:00 UTC.

!! BACKTEST CAVEAT (2026-08-29, LESSONS TRAP #190) -- the code in THIS file is correct;
   the study that justified it was not. backtest_delta.py's build(ref_h=6) picked the ATM
   strike from spot 6h AFTER entry (entry 05:30 IST, strike from 11:30 IST) = lookahead.
   That one line was the entire edge: same script, fixed, deployed H=12 goes
   +344.3/trade Sharpe 9.81 -> -54.7/trade Sharpe -1.31 (500d); every entry time turns
   negative. Corrected full audit: -152.6/trade, Sharpe -4.50, p=1.000. A direct VRP
   measurement finds no vol premium on BTC at all (weekly -100, daily -10 on the true
   200-pt strike grid). The earlier "significant + slippage-proof on 127 expiries" claim
   is WITHDRAWN -- slippage-robustness was a symptom of the bias, not evidence.
   Kept running by user decision; short-run profit at ~55% win rate is not evidence.
   Do NOT size up or move to real money on those stats.
   Detail: scratch/delta_weekly_fly/README.md

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
    "usd_inr": 85.0,        # crypto P&L shown in INR (unified infra) — Delta India rate
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


def live_mtm(pos):
    """Live mark-to-market P&L of the open iron-fly using CURRENT option marks
    (not settlement intrinsic) — mirrors the Orders-page live MTM. Display-only.
    Returns {pnl_pts, pnl_usd, pct_of_credit, spot, complete, legs:[...]} or None."""
    if not pos or not pos.get("legs"):
        return None
    und = pos.get("underlying", "BTC")
    try:
        by = {t.get("symbol"): t for t in delta_feed._all_option_tickers(und)}
    except Exception:
        by = {}
    cv = pos.get("contract_value") or CONTRACT_VALUE.get(und, 0.001)
    lots = pos.get("lots") or 1
    pts = 0.0
    complete = True
    legs_out = []
    for l in pos["legs"]:
        entry = l.get("entry_fill")
        if entry is None:
            entry = l.get("entry_premium")
        t = by.get(l.get("symbol")) or {}
        ltp = delta_feed._f(t.get("mark_price"))
        sign = 1 if l.get("side") == "SELL" else -1
        leg_pts = None
        if ltp is None:
            complete = False
        else:
            leg_pts = sign * ((entry or 0) - ltp)
            pts += leg_pts
        legs_out.append({"symbol": l.get("symbol"), "side": l.get("side"),
                         "strike": l.get("strike"), "cp": l.get("cp"),
                         "entry": entry, "ltp": ltp, "pnl_pts": leg_pts})
    usd = pts * cv * lots
    credit = pos.get("net_credit_pts") or 0
    pct = (pts / credit * 100.0) if credit else None
    return {"pnl_pts": pts, "pnl_usd": usd, "pct_of_credit": pct,
            "spot": delta_feed.spot(und), "complete": complete, "legs": legs_out}


# ---------- orchestration -----------------------------------------------------
def _tg(text):
    try:
        from _core import telegram_notify as tg
        if tg.is_enabled():
            tg.send_raw(text)
    except Exception:
        pass


def _usd_inr():
    try:
        return float(_config().get("usd_inr") or 85.0)
    except Exception:
        return 85.0


def _record_leg(leg, *, cv, lots, group_id, action, mode="paper",
                strategy="delta_ironfly_btc", ts=None, log=print):
    """Mirror ONE Delta fill into the shared order_store in INR — so crypto shows
    up in the SAME infra (Broker Orders / Open Positions / Completed / Stats) as
    NSE, and inherits all its netting/reconcile hardening. broker='delta' +
    segment='crypto' keep it cleanly filterable/isolatable later. Best-effort:
    a failure here never breaks the actual trade. INR price = premium(USD-pts) x
    contract_value(BTC) x USD/INR = rupee value PER LOT (qty=lots) → the existing
    (exit-entry)*qty gross math yields correct INR P&L."""
    try:
        import order_store
    except Exception:
        try:
            _core = os.path.join(_ROOT, "_core")
            if _core not in sys.path:
                sys.path.insert(0, _core)
            import order_store
        except Exception as e:
            log(f"[delta-fly] order_store import fail (mirror skipped): {e}")
            return
    fill = leg.get("entry_fill") if action == "entry" else leg.get("exit_fill")
    if fill is None:
        return
    price_inr = round(float(fill) * cv * _usd_inr(), 2)
    if price_inr <= 0:
        return
    # entry keeps the leg's own side; exit is the closing (opposite) side
    side = leg["side"] if action == "entry" else ("BUY" if leg["side"] == "SELL" else "SELL")
    sym = leg["symbol"]
    try:
        order_store.record(side, int(lots), price_inr, source="delta_ironfly",
                           strategy=strategy, mode=mode, broker="delta",
                           segment="crypto", symbol=sym, instrument="BTC",
                           trad_sym=sym, sec_id=sym,
                           broker_order_id=str(leg.get("order_id") or ""),
                           status="filled", group_id=group_id,
                           product_type="NRML", ts=ts)
    except Exception as e:
        log(f"[delta-fly] order_store.record fail ({action} {sym}): {e}")


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
    # CALL and PUT strikes differ near the tails — snap each leg to a strike that
    # ACTUALLY exists for its own option type (merging them picks e.g. an 82800
    # that lists only as a PUT → C-BTC-82800 "unknown product" reject). TRAP: fly
    # legs = up-wing CALL, dn-wing PUT, ATM needs BOTH.
    cpre, ppre = f"C-{underlying}-", f"P-{underlying}-"
    call_strk = sorted({int(s.split("-")[2]) for s in opts
                        if s.startswith(cpre) and s.endswith("-" + code)})
    put_strk = sorted({int(s.split("-")[2]) for s in opts
                       if s.startswith(ppre) and s.endswith("-" + code)})
    both = sorted(set(call_strk) & set(put_strk))
    if not spot or not both or not call_strk or not put_strk:
        return None
    atm = min(both, key=lambda x: abs(x - spot))
    up = min(call_strk, key=lambda x: abs(x - (atm + wing)))
    dn = min(put_strk, key=lambda x: abs(x - (atm - wing)))
    U = underlying
    legs = [("BUY", f"C-{U}-{up}-{code}", "C", up),     # wings FIRST (defined-risk)
            ("BUY", f"P-{U}-{dn}-{code}", "P", dn),
            ("SELL", f"C-{U}-{atm}-{code}", "C", atm),
            ("SELL", f"P-{U}-{atm}-{code}", "P", atm)]
    return {"code": code, "atm": atm, "spot": spot, "wing_up": up, "wing_dn": dn,
            "legs": legs}


def _fill_of(r):
    """A leg counts as OPEN only if the broker actually FILLED it.

    TRAP #202: Delta returns state="cancelled" + unfilled_size=N + reason
    "order_size_not_available_in_orderbook" when the book is too thin for a MARKET
    order. That is NOT the literal string "rejected", so a `status == "rejected"`
    guard lets an unfilled leg through, `placed` stops meaning "held", and the
    wings-first ordering that makes this structure defined-risk is silently void.
    Only a real fill price counts. Returns (fill_price, reason_if_not_filled)."""
    stt = str((r or {}).get("status") or "").lower()
    fill = (r or {}).get("fill_price")
    raw = ((r or {}).get("raw") or {}).get("result") or {}
    if fill is None:
        why = raw.get("cancellation_reason") or (r or {}).get("reason") or ""
        return None, ("state=%s unfilled=%s %s"
                      % (stt or "?", raw.get("unfilled_size", "?"), why)).strip()
    if stt in ("cancelled", "canceled", "rejected"):
        return None, "state=%s" % stt
    try:
        if float(raw.get("unfilled_size") or 0) > 0:
            return None, "partial fill, unfilled=%s" % raw.get("unfilled_size")
    except (TypeError, ValueError):
        pass
    return fill, ""


def _unwind_testnet(broker, placed, lots, log):
    """Close legs we ACTUALLY hold (opposite market orders), then report failures.

    Only ever unwinds a leg with a real entry_fill: sending an opposite order for a
    leg that never filled would OPEN a new inverted position, not close anything.
    Any leg still held after the attempt is a LOUD alarm - a half-unwound fly is
    exactly the naked shape this path exists to prevent."""
    stuck = []
    for lg in placed:
        if lg.get("entry_fill") is None:
            continue                      # never opened -> nothing to close
        opp = "SELL" if lg["side"] == "BUY" else "BUY"
        try:
            r = broker.place_order(opp, lg["symbol"], qty=lots, order_type="MARKET")
            f, why = _fill_of(r)
            if f is None:
                stuck.append("%s (%s)" % (lg["symbol"], why))
                log(f"[delta-fly] unwind NOT FILLED {lg['symbol']}: {why}")
            else:
                log(f"[delta-fly] unwound {lg['symbol']} @ {f}")
        except Exception as e:
            stuck.append("%s (%s)" % (lg["symbol"], e))
            log(f"[delta-fly] unwind FAIL {lg['symbol']}: {e}")
    if stuck:
        log("[delta-fly] UNWIND INCOMPLETE - still exposed: " + "; ".join(stuck))
        _tg("\U0001f534 <b>Delta Iron-Fly - UNWIND INCOMPLETE</b>\n"
            "Entry aborted but these legs could NOT be closed (still open at Delta):\n"
            + "\n".join(stuck) + "\nManual check needed.")
    return stuck


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
        fill, why = _fill_of(r)
        if fill is None:
            # NOT filled (rejected OR cancelled-for-no-depth OR partial). Abort the
            # whole structure - a partial iron-fly is a naked short, not a position.
            log(f"[delta-fly] leg NOT FILLED {side} {sym}: {why} - aborting + unwinding")
            held = len([l for l in placed if l.get("entry_fill") is not None])
            _unwind_testnet(b, placed, lots, log)
            _tg("\U0001f534 <b>Delta Iron-Fly - ENTRY ABORTED</b>\n"
                "leg %s %s not filled (%s)\n"
                "%d filled leg(s) unwound - no position taken." % (side, sym, why, held))
            return st
        placed.append({"cp": cp, "strike": strike, "side": side, "symbol": sym,
                       "entry_fill": fill, "order_id": r.get("order_id")})
    cv = CONTRACT_VALUE.get(cfg["underlying"], 0.001)
    credit = sum((l["entry_fill"] or 0) for l in placed if l["side"] == "SELL") \
        - sum((l["entry_fill"] or 0) for l in placed if l["side"] == "BUY")
    group_id = "deltafly_" + now_utc.strftime("%Y%m%d_%H%M%S")
    pos = {"underlying": cfg["underlying"], "expiry": setup["code"],
           "atm": setup["atm"], "wing": cfg["wing"], "lots": lots,
           "contract_value": cv, "legs": placed, "entry_spot": setup["spot"],
           "net_credit_pts": credit, "entry_time": now_utc.isoformat(),
           "mode": "testnet", "group_id": group_id}
    pos["reconcile"] = _reconcile_testnet(b, pos, log)
    if pos["reconcile"] != "match":
        # Broker disagrees with what we think we hold. Reconcile is a GATE, not a
        # footnote: unwind and take no position rather than run a structure whose
        # real shape we cannot confirm.
        log("[delta-fly] reconcile MISMATCH at entry - unwinding, no position")
        _unwind_testnet(b, placed, lots, log)
        _tg("\U0001f534 <b>Delta Iron-Fly - ENTRY ABORTED (reconcile)</b>\n"
            "%s\nLegs unwound - no position taken." % pos["reconcile"])
        return st
    st["open"] = pos
    st["last_entry_day"] = now_utc.date().isoformat()
    _save(st)
    # mirror into shared order_store (INR) — unified infra
    for lg in placed:
        _record_leg(lg, cv=cv, lots=lots, group_id=group_id, action="entry", log=log)
    log(f"[delta-fly] TESTNET ENTER iron-fly ATM {setup['atm']} exp {setup['code']} "
        f"credit {credit:.1f}pts lots {lots} | reconcile: {pos['reconcile']}")
    _tg(f"🦋 <b>Delta BTC Iron-Fly — TESTNET ENTRY</b>\n"
        f"ATM {setup['atm']} · exp {setup['code']} · {lots} lot\n"
        f"legs: " + " · ".join(f"{l['side'][0]} {l['strike']}{l['cp']}@{l['entry_fill']}"
                               for l in placed) + "\n"
        f"net credit {credit:.1f} pts · reconcile: {pos['reconcile']}\n"
        f"(paper testnet — visible on Delta testnet platform)")
    return st


def _update_runup_tags(st, log=print):
    """Track per-leg MAX_LTP/MIN_LTP (in INR-per-lot, same units order_store stored)
    for the open crypto fly so the Open Positions Run-Up/Run-Down columns populate —
    the NSE pos_monitor only tracks Dhan-fed legs, never crypto. Self-contained here
    (delta trader already ticks + has delta_feed). Best-effort."""
    pos = st.get("open")
    if not pos or pos.get("mode") != "testnet":
        return
    try:
        import order_store
    except Exception:
        try:
            _core = os.path.join(_ROOT, "_core")
            if _core not in sys.path:
                sys.path.insert(0, _core)
            import order_store
        except Exception:
            return
    und = pos.get("underlying", "BTC")
    try:
        marks = {t.get("symbol"): delta_feed._f(t.get("mark_price"))
                 for t in delta_feed._all_option_tickers(und)}
        rows = order_store.query(date=order_store.ist_now_str()[:10], limit=9000)
    except Exception:
        return
    # match by THIS position's group_id — a trad_sym repeats across the day's flies
    # (same strike reused), so trad_sym alone would tag a stale closed row.
    gid = pos.get("group_id")
    open_row = {}
    for r in rows:
        if (r.get("broker") == "delta" and str(r.get("status") or "") == "filled"
                and (not gid or r.get("group_id") == gid)):
            open_row.setdefault(r.get("trad_sym"), r)
    cv, rate = pos["contract_value"], _usd_inr()
    changed = False
    for lg in pos["legs"]:
        if lg.get("exit_fill") is not None:
            continue
        m = marks.get(lg["symbol"])
        if m is None:
            continue
        px = round(m * cv * rate, 2)
        lg["max_ltp"] = round(max(lg.get("max_ltp") or px, px), 2)
        lg["min_ltp"] = round(min(lg.get("min_ltp") or px, px), 2)
        row = open_row.get(lg["symbol"])
        if row and row.get("id"):
            try:
                order_store.update_tag_fields(row["id"], {
                    "MAX_LTP": str(lg["max_ltp"]), "MIN_LTP": str(lg["min_ltp"])})
            except Exception:
                pass
        changed = True
    if changed:
        _save(st)


def _last_fill_price(b, symbol, opp_side):
    """Most-recent fill price for `symbol` on the closing side (opp_side) — used to
    capture the REAL price a leg was liquidated/closed at. opp_side in buy/sell."""
    try:
        r = b._signed("GET", "/v2/fills",
                      params={"contract_types": "call_options,put_options",
                              "page_size": 200})
        res = (r or {}).get("result", []) if isinstance(r, dict) else []
        rows = [x for x in res if x.get("product_symbol") == symbol
                and str(x.get("side", "")).lower() == opp_side]
        rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        if rows:
            return float(rows[0].get("price"))
    except Exception:
        pass
    return None


def reconcile_liquidations(cfg, st, now_utc, log=print):
    """Detect legs Delta auto-liquidated (in our store, GONE from the broker) and
    record the close into order_store + the local store — so the app reflects a
    mid-day liquidation immediately instead of only at settlement. Loud alert on a
    BROKEN structure (some legs gone → possible naked risk)."""
    pos = st.get("open")
    if not pos or pos.get("mode") != "testnet":
        return st
    b = _testnet_broker()
    if b is None:
        return st
    try:
        live = b.positions()
    except Exception:
        return st
    cv, lots = pos["contract_value"], pos["lots"]
    gid = pos.get("group_id") or ("deltafly_" + str(pos.get("entry_time", ""))[:19])
    gone = []
    for lg in pos["legs"]:
        if lg.get("exit_fill") is not None:
            continue   # already closed/recorded
        if lg.get("entry_fill") is None:
            continue   # never opened -> absent at broker is correct, not a liquidation
        held = abs(live.get(lg["symbol"], 0) or 0)
        if held == 0:
            opp = "buy" if lg["side"] == "SELL" else "sell"
            ef = _last_fill_price(b, lg["symbol"], opp)
            if ef is None:
                log(f"[delta-fly] LIQUIDATED {lg['symbol']} but no fill price — retry next tick")
                continue
            lg["exit_fill"] = ef
            lg["liquidated"] = True
            _record_leg(lg, cv=cv, lots=lots, group_id=gid, action="exit", log=log)
            gone.append(lg)
    if not gone:
        return st
    remaining = [l for l in pos["legs"] if l.get("exit_fill") is None]
    _save(st)
    if remaining:
        # broken hedge — some legs closed, others still open → possible naked risk
        log(f"[delta-fly] ⚠️ BROKEN STRUCTURE: {len(gone)} leg(s) liquidated, "
            f"{len(remaining)} still open — possible naked exposure")
        _tg(f"⚠️ <b>Delta Iron-Fly — LEG LIQUIDATED</b>\n"
            f"{len(gone)} leg(s) auto-closed by Delta (isolated-margin), "
            f"{len(remaining)} still open → structure broken, check exposure.\n"
            f"liquidated: " + ", ".join(f"{l['side'][0]} {l['strike']}{l['cp']}@{l['exit_fill']}"
                                        for l in gone))
    else:
        # all legs gone → settle the position out
        net_pts = sum((1 if l["side"] == "SELL" else -1)
                      * ((l["entry_fill"] or 0) - (l.get("exit_fill") or 0))
                      for l in pos["legs"])
        rec = dict(pos)
        rec.update({"exit_time": now_utc.isoformat(), "pnl_pts": net_pts,
                    "pnl_usd": net_pts * cv * lots, "exit_reason": "liquidated"})
        st["completed"] = (st.get("completed") or []) + [rec]
        st["open"] = None
        _save(st)
        log(f"[delta-fly] all legs liquidated/closed → position settled "
            f"P&L {net_pts:+.1f}pts")
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
    gid = pos.get("group_id") or ("deltafly_" + str(pos.get("entry_time", ""))[:19])
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
        if held > 0:   # only mirror legs we actually closed (skip already-gone)
            _record_leg(lg, cv=cv, lots=lots, group_id=gid, action="exit", log=log)
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
        st = reconcile_liquidations(cfg, st, now_utc, log)   # catch mid-day liquidations
        _update_runup_tags(st, log)                          # Run-Up/Run-Down for crypto legs
        st = maybe_exit_testnet(cfg, st, now_utc, log)
        # robustness: a leftover non-testnet (sim/paper) open would otherwise never
        # settle in testnet mode and block ALL future entries forever — settle it via
        # sim at its own expiry so the slot frees up (fixes the silent-miss class).
        op = st.get("open")
        if op and op.get("mode") != "testnet":
            st = maybe_exit(cfg, st, now_utc, log)
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
