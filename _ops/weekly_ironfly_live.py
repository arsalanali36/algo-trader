"""
weekly_ironfly_live.py — LIVE wiring for the WEEKLY POSITIONAL IRON-FLY (02.17).
Self-contained (keeps trader_dashboard.py thin). PAPER hard-locked, OFF by default.

Pure decisions come from weekly_ironfly (wf); this file does the I/O side: detect the
day-after-expiry entry day (front weekly expiry roll via dhan_master), resolve contracts,
RMS-gate the basket ONCE, fire the 4 legs HEDGE-first via execution_gateway (never leaves
a naked sold leg), poll live LTP, book the 50%-credit target, and square off on expiry.

Reuses (Rule 6B):
  execution_gateway (gw)   — the ONLY order path
  dhan_master              — ATM/OTM contract resolve by offset (CE atm+off / PE atm-off;
                             no strike-string math -> TRAP #140 safe) + front expiry
  risk_gate (rg)           — gating_status / exit_time_config
  market_calendar (mc)     — trading-day gate (TRAP #142)
  ltp_poller + shared_ltp_cache — batched live LTP (zero extra Dhan)
  weekly_ironfly (wf)      — state + entry-day/build/exit decisions

Balanced-hedge rule (TRAP #171): the whole entry is gated ONCE, then all 4 legs fire at a
FIXED lot count (gate=False, no per-leg RMS size-down) so sold and hedge can never desync.
"""
import os, sys, time, uuid, json, re
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, ROOT, os.path.join(ROOT, "_core"), os.path.join(ROOT, "_data"),
          os.path.join(ROOT, "strategies", "live")):
    if p not in sys.path:
        sys.path.insert(0, p)

import execution_gateway as gw
import risk_gate as rg
import dhan_master
import ltp_poller
import shared_ltp_cache as ltc
import weekly_ironfly as wf
try:
    import market_calendar as mc
except Exception:
    mc = None
try:
    import notify
except Exception:
    notify = None

TC_FILE = os.path.join(ROOT, "nifty_config.json")     # config lives at ROOT (not data/)
SEG = "NSE_FNO"
# mode is CONFIG-DRIVEN (paper default): _weekly_ironfly.mode = "paper" | "live".
# Positional/overnight → entries fire NRML (never MIS, else broker squares off at 3:20).
PRODUCT = "NRML"
STRAT_ID = "weekly_ironfly_v1"
SRC = "weekly_ironfly"

DEFAULT_CFG = {
    "enabled": False,             # OFF by default (auto day-after-expiry entry)
    "enabled_manual": True,       # manual button allowed (still paper)
    "lots": 5,
    "symbols": ["NIFTY"],
    "entry_hm": "09:20", "entry_window_min": 6,
    "wing": 250, "take_pct": 0.50,
    "mode": "paper",
}
_last_day = {}                    # sym -> date already attempted (in-process one-shot)

LOG_FILE = os.path.join(ROOT, "logs", "weekly_ironfly.log")
_LOG_MAX_LINES = 3000


def cfg():
    try:
        with open(TC_FILE) as f:
            c = json.load(f).get("_weekly_ironfly", {})
    except Exception:
        c = {}
    out = {**DEFAULT_CFG, **(c or {})}
    if out.get("mode") not in ("paper", "live"):
        out["mode"] = "paper"     # anything unexpected → paper (fail-safe)
    return out


def _log(msg):
    line = f"[ironfly] {msg}"
    print(line, flush=True)
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{ts} {line}\n")
        if os.path.getsize(LOG_FILE) > _LOG_MAX_LINES * 180:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-_LOG_MAX_LINES:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(tail)
    except Exception:
        pass


# ---------------------------------------------------------------- helpers
def _spot(symbol):
    try:
        return ltc.get_index(symbol, max_age=60.0)         # cache-only, zero extra Dhan
    except Exception:
        return None


def _resolve(symbol, spot, side, offset):
    """(sec_id, trad_sym, lot_size) for a leg `offset` strikes out. offset 0 = ATM.
    dhan_master inverts the PE offset internally (positive = OTM) -> TRAP #140 safe."""
    try:
        sid, tsym, lot = dhan_master.get_option_contract(symbol, spot, side, offset)
        if sid and tsym:
            return str(sid), tsym, int(lot or 1)
    except Exception as e:
        _log(f"resolve {side} off{offset} fail: {e}")
    return None


def _front_expiry(symbol, spot):
    """Front weekly expiry date 'YYYY-MM-DD' (nearest listed >= today) from the ATM CE."""
    con = _resolve(symbol, spot, "CE", 0)
    if not con:
        return None
    try:
        exp = dhan_master.get_expiry_for_sec_id(con[0])
        return str(exp)[:10] if exp else None
    except Exception:
        return None


def _strike_of(trad_sym):
    m = re.findall(r"(\d{3,6})", str(trad_sym))
    return int(m[-1]) if m else 0


def _no_entry_now():
    try:
        _sq, no_entry = rg.exit_time_config()
        h, m = rg.hm_tuple(no_entry)          # (H, M) tuple — NOT "HH:MM" (was silently dead)
        now = datetime.now()
        return (now.hour, now.minute) >= (h, m)
    except Exception:
        return False


# ---------------------------------------------------------------- fire entry
def _fire_leg(symbol, side, lots, lot_size, sec_id, trad_sym, tag, gid, xtags, mode):
    return gw.execute_signal(STRAT_ID, symbol, side, lots, lot_size, sec_id, trad_sym,
                             seg=SEG, mode=mode, source=SRC, tag=tag, product=PRODUCT,
                             group_id=gid, gate=False, extra_tags=xtags, log=_log)


def _unwind(placed, symbol, gid, mode):
    for p in placed:
        try:
            gw.execute_exit(STRAT_ID, symbol, p["sec_id"], p["trad_sym"], p["qty"],
                            entry_side=p["side"], seg=SEG, mode=mode, group_id=gid,
                            reason="IRONFLY_UNWIND", tag="IRONFLY", source=SRC, log=_log)
        except Exception:
            pass


def fire_ironfly(symbol, source, front_expiry=None, c=None):
    """Enter one weekly iron-fly. Returns pos|None. HEDGE legs first (never naked)."""
    c = c or cfg()
    mode = c.get("mode", "paper")
    if wf.has_open(symbol):
        _log(f"{symbol}: already open — skip"); return None
    if mc is not None and not mc.is_trading_day():
        _log("market closed — skip"); return None
    if _no_entry_now():
        _log("past no-entry cutoff — skip"); return None

    spot = _spot(symbol)
    if not spot:
        _log(f"{symbol}: no spot — skip"); return None
    if front_expiry is None:
        front_expiry = _front_expiry(symbol, spot)

    blocked, reason, hard = rg.gating_status(STRAT_ID, mode=mode)
    if blocked and hard:
        _log(f"{symbol}: RMS hard-block ({reason}) — skip"); return None

    wing_off = int(c.get("wing", 250)) // 50
    legs_spec = [   # (opt_type, role, side, offset, tag)  — HEDGE first
        ("CE", "HEDGE", "BUY",  wing_off, "IRONFLY_HEDGE"),
        ("PE", "HEDGE", "BUY",  wing_off, "IRONFLY_HEDGE"),
        ("CE", "SELL",  "SELL", 0,        "IRONFLY"),
        ("PE", "SELL",  "SELL", 0,        "IRONFLY"),
    ]
    lots = int(c.get("lots", 5))
    gid = "IRNFLY_" + uuid.uuid4().hex[:8]
    placed, legs = [], []
    for (ot, role, oside, off, tag) in legs_spec:
        con = _resolve(symbol, spot, ot, off)
        if not con:
            _log(f"{symbol}: {ot} {role} resolve fail — unwind+abort")
            _unwind(placed, symbol, gid, mode); return None
        sec_id, tsym, lot = con
        xtags = ["IRONFLY", role, f"IRONFLY_SRC:{source}"]
        res = _fire_leg(symbol, oside, lots, lot, sec_id, tsym, tag, gid, xtags, mode)
        if not res or not res.get("ok"):
            _log(f"{symbol}: {ot} {role} fire fail ({res}) — unwind+abort")
            _unwind(placed, symbol, gid, mode); return None
        px = float(res.get("price") or 0)
        qty = int(res.get("qty") or lots * lot)
        placed.append(dict(sec_id=sec_id, trad_sym=tsym, side=oside, qty=qty))
        legs.append(dict(opt_type=ot, role=role, side=oside, sec_id=sec_id, trad_sym=tsym,
                         strike=_strike_of(tsym), entry_price=px, qty=qty, status="open"))

    pos = wf.build_position(gid, symbol, lots, legs[0]["qty"] // max(lots, 1), mode, source, gid,
                            datetime.now().strftime("%Y-%m-%d"), front_expiry, spot, legs,
                            cfg={"wing": c["wing"], "take_pct": c["take_pct"]})
    if not pos:
        _log(f"{symbol}: build_position rejected (credit<=0) — unwind"); _unwind(placed, symbol, gid, mode); return None
    wf.add(pos)
    ltp_poller.request_watch([(l["sec_id"], SEG) for l in legs])
    _log(f"{symbol}: ENTER iron-fly credit={pos['entry_net_credit']} target={pos['target_pts']} "
         f"expiry={front_expiry} gid={gid}")
    if notify:
        try: notify.info(f"Iron-fly ENTER {symbol} credit {pos['entry_net_credit']} (paper)", source=SRC)
        except Exception: pass
    return pos


# ---------------------------------------------------------------- monitor / exit
def _ltp_of(entry_ts):
    def f(leg):
        return ltc.get_after(str(leg["sec_id"]), entry_ts, max_age=8.0)
    return f


def _close_all(pos, reason):
    for l in pos["legs"]:
        if l.get("status") != "open":
            continue
        try:
            gw.execute_exit(STRAT_ID, pos["symbol"], l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg=SEG, mode=pos.get("mode", "paper"),
                            group_id=pos["group_id"],
                            reason=reason, tag="IRONFLY", source=SRC, log=_log)
        except Exception as e:
            _log(f"close leg fail: {e}")
    wf.set_status(pos["id"], "target" if reason.endswith("TARGET") else "closed", reason)


def _maybe_enter(c):
    """Day-after-expiry entry check (once/day/symbol)."""
    if not c.get("enabled"):
        return
    if mc is not None and not mc.is_trading_day():
        return
    eh, em = [int(x) for x in str(c["entry_hm"]).split(":")]
    now = datetime.now()
    delta = (now.hour * 60 + now.minute) - (eh * 60 + em)
    if not (0 <= delta <= int(c["entry_window_min"])):
        return
    today = now.strftime("%Y-%m-%d")
    for sym in c.get("symbols", ["NIFTY"]):
        if _last_day.get(sym) == today:
            continue
        _last_day[sym] = today            # one attempt/symbol/day
        if wf.has_open(sym):
            # LOUD — a silent skip here hid a dead expiry-squareoff for a whole cycle.
            op = [p["id"] + "@" + str(p.get("expiry_date")) for p in wf.list_open(sym)]
            _log(f"{sym}: entry window but position still open → skip ({', '.join(op)})")
            continue
        spot = _spot(sym)
        if not spot:
            continue
        front = _front_expiry(sym, spot)
        do, new_marker, why = wf.should_enter(front, wf.last_expiry_seen(), wf.has_open(sym))
        if new_marker != wf.last_expiry_seen():
            wf.set_last_expiry_seen(new_marker)
        if not do:
            _log(f"{sym}: no entry ({why}, front={front})")
            continue
        _log(f"{sym}: day-after-expiry entry ({why}, front={front})")
        try:
            fire_ironfly(sym, "weekly_ironfly", front, c)
        except Exception as e:
            _log(f"{sym} fire error: {e}")


def ironfly_loop():
    """~3s: day-after-expiry 9:20 entry + 50%-credit target exit + weekly-expiry squareoff."""
    _log("loop started")
    while True:
        try:
            c = cfg()
            today = datetime.now().strftime("%Y-%m-%d")
            _maybe_enter(c)
            for pos in wf.list_open():
                sym = pos["symbol"]
                ltp_poller.request_watch([(l["sec_id"], SEG) for l in pos["legs"]
                                          if l.get("status") == "open"])
                # Past the expiry DATE = contracts are gone (cash-settled at the broker);
                # nothing to close, just retire the state so the next cycle can enter.
                # (2026-09-02: the 26-Aug fly expired 01-Sep but stayed "open" here because
                # the squareoff below never fired — see hm_tuple — and today's 09:20 entry
                # was skipped as "already open".)
                if pos.get("expiry_date") and today > str(pos["expiry_date"]):
                    _log(f"{sym}: position {pos['id']} expired {pos['expiry_date']} — settled at broker, "
                         f"retiring state (no orders)")
                    wf.set_status(pos["id"], "expired", "IRONFLY_EXPIRED_SETTLED")
                    continue
                # weekly-expiry squareoff (expiry DAY, at the configured squareoff time)
                if pos.get("expiry_date") and today >= str(pos["expiry_date"]):
                    try:
                        sqh, _ = rg.exit_time_config()
                        h, m = rg.hm_tuple(sqh)   # (H, M) tuple — NOT "HH:MM" (was silently dead)
                        if (datetime.now().hour, datetime.now().minute) >= (h, m):
                            _log(f"{sym}: expiry squareoff (exp={pos['expiry_date']})")
                            _close_all(pos, "IRONFLY_EXPIRY"); continue
                    except Exception as e:
                        _log(f"{sym}: expiry-squareoff check error: {e}")
                # 50% target exit
                r, mtm = wf.check_exit(pos, _ltp_of(pos.get("created_ts", 0)))
                if r == "target":
                    _log(f"{sym}: TARGET hit mtm={mtm} (>= {pos['target_pts']}) — close all")
                    _close_all(pos, "IRONFLY_TARGET")
        except Exception as e:
            _log(f"loop error: {e}")
        time.sleep(3)


if __name__ == "__main__":
    # ---- stubbed integration test (no live Dhan): entry -> decay -> target ----
    LOT = 65
    calls = {"signal": [], "exit": []}
    _prices = {}

    def fake_contract(symbol, spot, ot, off):
        atm = round(spot / 50) * 50
        strike = atm + off * 50 if ot == "CE" else atm - off * 50
        return f"{ot}{strike}", f"NIFTY{strike}{ot}", LOT
    dhan_master.get_option_contract = fake_contract
    dhan_master.get_expiry_for_sec_id = lambda s: "2026-09-01 15:30:00"
    gw.execute_signal = lambda *a, **k: (calls["signal"].append((a, k)) or
                                         {"ok": True, "price": _prices.get(a[5], 50), "qty": a[3] * a[4]})
    gw.execute_exit = lambda *a, **k: (calls["exit"].append((a, k)) or
                                       {"ok": True, "price": _prices.get(a[2], 20), "qty": a[4]})
    rg.gating_status = lambda *a, **k: (False, "", False)
    rg.exit_time_config = lambda: ("23:59", "23:59")
    mc = None; globals()["mc"] = None
    ltp_poller.request_watch = lambda pairs: None
    ltc.get_index = lambda sym, max_age=60: 24010
    ltc.get_after = lambda sid, ts, max_age=8.0: _prices.get(sid, 50)
    ltc.get = lambda sid, max_age=3.0: _prices.get(sid, 50)

    wf.STORE = os.path.join(os.path.dirname(wf.STORE), "weekly_ironfly_positions_TEST.json")
    if os.path.exists(wf.STORE): os.remove(wf.STORE)

    # entry premiums: SELL ATM 150/140 ; BUY wings 55/50 -> net credit 185, target 92.5
    _prices.update({"CE24000": 150, "PE24000": 140, "CE24250": 55, "PE23750": 50})
    pos = fire_ironfly("NIFTY", "weekly_ironfly", "2026-09-01", cfg())
    assert pos and len(calls["signal"]) == 4, ("entry legs", len(calls["signal"]))
    assert pos["entry_net_credit"] == 185 and pos["target_pts"] == 92.5, pos["entry_net_credit"]
    # HEDGE fired before SELL (never naked)
    sides = [a[0][2] for a in calls["signal"]]
    assert sides == ["BUY", "BUY", "SELL", "SELL"], sides
    print("entry OK: credit", pos["entry_net_credit"], "target", pos["target_pts"], "order", sides)

    # decay -> target: sold 70/60, hedge 22/20 -> flatten 185-88=97 >= 92.5
    _prices.update({"CE24000": 70, "PE24000": 60, "CE24250": 22, "PE23750": 20})
    p = wf.get(pos["id"])
    r, mtm = wf.check_exit(p, _ltp_of(p.get("created_ts", 0)))
    assert r == "target" and mtm == 97.0, (r, mtm)
    print("exit-decision OK: mtm", mtm, "reason", r)

    # entry-day marker gate
    assert wf.should_enter("2026-09-08", "2026-09-01", False)[0] is True
    assert wf.should_enter("2026-09-01", "2026-09-01", False)[0] is False
    os.remove(wf.STORE)
    print("\nweekly_ironfly_live stubbed integration test PASS")
