"""
m_pattern_ironfly_live.py — LIVE wiring for the IV-pop M-rollover IRON-FLY (02.18).
Self-contained (keeps trader_dashboard.py thin). PAPER hard-locked, OFF by default.

Pure decisions come from m_pattern_ironfly (mpf) + the SINGLE-SOURCE signal
(strategies/signals/m_pattern.detect — the SAME code the backtest measured, ADR-010).
This file does the I/O side: read the live ATM combined-premium series (option_curves,
the /curves collector), run the M-detector, on the FIRST M-rollover of the day fire the
4 iron-fly legs HEDGE-first via execution_gateway (never a naked sold leg), poll live LTP,
book the 50%-credit target, and square off at +1 trading day (or the weekly expiry backstop).

Reuses (Rule 6B):
  m_pattern.detect (mp)    — the ONE M-signal (backtest == live)
  option_curves (oc)       — live ATM straddle premium series (zero extra Dhan; collector lake)
  execution_gateway (gw)   — the ONLY order path
  dhan_master              — ATM/OTM contract resolve by offset (TRAP #140 safe) + front expiry
  risk_gate (rg)           — gating_status / exit_time_config
  market_calendar (mc)     — trading-day gate (TRAP #142)
  ltp_poller + shared_ltp_cache — batched live LTP
  m_pattern_ironfly (mpf)  — state + build/exit/dedup/time-exit decisions

Balanced-hedge rule (TRAP #171): the whole entry is gated ONCE, then all 4 legs fire at a
FIXED lot count (gate=False) so sold and hedge can never desync.
Rule 10: backtest-impl != live-impl -> PAPER forward-validate before real money.
"""
import os, sys, time, uuid, json, re
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, ROOT, os.path.join(ROOT, "_core"), os.path.join(ROOT, "_data"),
          os.path.join(ROOT, "strategies", "signals")):
    if p not in sys.path:
        sys.path.insert(0, p)

import execution_gateway as gw
import risk_gate as rg
import dhan_master
import ltp_poller
import shared_ltp_cache as ltc
import option_curves as oc
import m_pattern as mp
import m_pattern_ironfly as mpf
try:
    import market_calendar as mc
except Exception:
    mc = None
try:
    import notify
except Exception:
    notify = None

IST = timezone(timedelta(hours=5, minutes=30))
TC_FILE = os.path.join(ROOT, "nifty_config.json")
SEG = "NSE_FNO"
PRODUCT = "NRML"                       # positional/overnight -> NRML (never MIS)
STRAT_ID = "m_pattern_ironfly_v1"
SRC = "m_pattern_ironfly"

DEFAULT_CFG = {
    "enabled": False,                  # OFF by default
    "lots": 5,
    "symbols": ["NIFTY"],
    "m_strictness": "medium",          # deployed
    "wing": 250, "take_pct": 0.50, "max_hold_days": 1,
    "mode": "paper",
}
LOG_FILE = os.path.join(ROOT, "logs", "m_pattern_ironfly.log")
_LOG_MAX_LINES = 3000


def cfg():
    try:
        with open(TC_FILE) as f:
            c = json.load(f).get("_m_pattern_ironfly", {})
    except Exception:
        c = {}
    out = {**DEFAULT_CFG, **(c or {})}
    if out.get("mode") not in ("paper", "live"):
        out["mode"] = "paper"          # fail-safe -> paper
    return out


def _log(msg):
    line = f"[mpfly] {msg}"
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


def _today():
    return datetime.now(IST).strftime("%Y-%m-%d")


def _ist_hm(t):
    """Epoch seconds -> IST minute-of-day int (HHMM), matching the backtest's hhmm."""
    d = datetime.fromtimestamp(int(t), IST)
    return d.hour * 100 + d.minute


# ---------------------------------------------------------------- signal (SINGLE SOURCE)
def live_series(symbol, date):
    """[(hhmm, atm_combined_premium)] for today from the /curves collector. Zero extra Dhan."""
    try:
        d = oc.curves(symbol, date)
        pts = d.get("points") or []
    except Exception as e:
        _log(f"{symbol}: curves read fail: {e}"); return []
    out = []
    for p in pts:
        s = p.get("straddle")
        t = p.get("t")
        if s is not None and t is not None:
            out.append((_ist_hm(t), float(s)))
    return out


def detect(symbol, date, c=None):
    c = c or cfg()
    params = mp.M_PRESETS.get(c.get("m_strictness", "medium"), mp.M_PRESETS["medium"])
    return mp.detect(live_series(symbol, date), params)     # (hm, spike_ratio) | None


# ---------------------------------------------------------------- helpers
def _spot(symbol):
    try:
        return ltc.get_index(symbol, max_age=60.0)
    except Exception:
        return None


def _resolve(symbol, spot, side, offset):
    try:
        sid, tsym, lot = dhan_master.get_option_contract(symbol, spot, side, offset)
        if sid and tsym:
            return str(sid), tsym, int(lot or 1)
    except Exception as e:
        _log(f"resolve {side} off{offset} fail: {e}")
    return None


def _front_expiry(symbol, spot):
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
        h, m = [int(x) for x in str(no_entry).split(":")[:2]]
        now = datetime.now(IST)
        return (now.hour, now.minute) >= (h, m)
    except Exception:
        return False


# ---------------------------------------------------------------- fire entry (HEDGE-first)
def _fire_leg(symbol, side, lots, lot_size, sec_id, trad_sym, tag, gid, xtags, mode):
    return gw.execute_signal(STRAT_ID, symbol, side, lots, lot_size, sec_id, trad_sym,
                             seg=SEG, mode=mode, source=SRC, tag=tag, product=PRODUCT,
                             group_id=gid, gate=False, extra_tags=xtags, log=_log)


def _unwind(placed, symbol, gid, mode):
    for p in placed:
        try:
            gw.execute_exit(STRAT_ID, symbol, p["sec_id"], p["trad_sym"], p["qty"],
                            entry_side=p["side"], seg=SEG, mode=mode, group_id=gid,
                            reason="MPFLY_UNWIND", tag="MPFLY", source=SRC, log=_log)
        except Exception:
            pass


def fire_ironfly(symbol, source, c=None):
    """Enter one iron-fly (HEDGE legs first -> never naked). Returns pos|None."""
    c = c or cfg()
    mode = c.get("mode", "paper")
    if mpf.has_open(symbol):
        _log(f"{symbol}: already open — skip"); return None
    if mc is not None and not mc.is_trading_day():
        _log("market closed — skip"); return None
    if _no_entry_now():
        _log("past no-entry cutoff — skip"); return None

    spot = _spot(symbol)
    if not spot:
        _log(f"{symbol}: no spot — skip"); return None
    front = _front_expiry(symbol, spot)

    blocked, reason, hard = rg.gating_status(STRAT_ID, mode=mode)
    if blocked and hard:
        _log(f"{symbol}: RMS hard-block ({reason}) — skip"); return None

    wing_off = int(c.get("wing", 250)) // 50
    legs_spec = [   # (opt_type, role, side, offset, tag) — HEDGE first
        ("CE", "HEDGE", "BUY",  wing_off, "MPFLY_HEDGE"),
        ("PE", "HEDGE", "BUY",  wing_off, "MPFLY_HEDGE"),
        ("CE", "SELL",  "SELL", 0,        "MPFLY"),
        ("PE", "SELL",  "SELL", 0,        "MPFLY"),
    ]
    lots = int(c.get("lots", 5))
    gid = "MPFLY_" + uuid.uuid4().hex[:8]
    placed, legs = [], []
    for (ot, role, oside, off, tag) in legs_spec:
        con = _resolve(symbol, spot, ot, off)
        if not con:
            _log(f"{symbol}: {ot} {role} resolve fail — unwind+abort")
            _unwind(placed, symbol, gid, mode); return None
        sec_id, tsym, lot = con
        xtags = ["MPFLY", role, f"MPFLY_SRC:{source}"]
        res = _fire_leg(symbol, oside, lots, lot, sec_id, tsym, tag, gid, xtags, mode)
        if not res or not res.get("ok"):
            _log(f"{symbol}: {ot} {role} fire fail ({res}) — unwind+abort")
            _unwind(placed, symbol, gid, mode); return None
        px = float(res.get("price") or 0)
        qty = int(res.get("qty") or lots * lot)
        placed.append(dict(sec_id=sec_id, trad_sym=tsym, side=oside, qty=qty))
        legs.append(dict(opt_type=ot, role=role, side=oside, sec_id=sec_id, trad_sym=tsym,
                         strike=_strike_of(tsym), entry_price=px, qty=qty, status="open"))

    pos = mpf.build_position(gid, symbol, lots, legs[0]["qty"] // max(lots, 1), mode, source, gid,
                             _today(), front, spot, legs,
                             cfg={"wing": c["wing"], "take_pct": c["take_pct"],
                                  "max_hold_days": c["max_hold_days"]})
    if not pos:
        _log(f"{symbol}: build_position rejected (credit<=0) — unwind"); _unwind(placed, symbol, gid, mode); return None
    mpf.add(pos)
    ltp_poller.request_watch([(l["sec_id"], SEG) for l in legs])
    _log(f"{symbol}: ENTER iron-fly credit={pos['entry_net_credit']} target={pos['target_pts']} "
         f"hold={c['max_hold_days']}d expiry={front} gid={gid}")
    if notify:
        try: notify.info(f"M-fly ENTER {symbol} credit {pos['entry_net_credit']} (paper)", source=SRC)
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
                            group_id=pos["group_id"], reason=reason, tag="MPFLY", source=SRC, log=_log)
        except Exception as e:
            _log(f"close leg fail: {e}")
    mpf.set_status(pos["id"], "target" if reason.endswith("TARGET") else "closed", reason)


def _is_trading_day(date_str):
    if mc is None:
        return True
    try:
        from datetime import date
        return mc.is_trading_day(date.fromisoformat(date_str))
    except Exception:
        return True


def _sq_time_reached():
    try:
        sqh, _ = rg.exit_time_config()
        h, m = [int(x) for x in str(sqh).split(":")[:2]]
        now = datetime.now(IST)
        return (now.hour, now.minute) >= (h, m)
    except Exception:
        return False


def _maybe_enter(c):
    """One M-rollover entry per symbol per day."""
    if not c.get("enabled"):
        return
    if mc is not None and not mc.is_trading_day():
        return
    if _no_entry_now():
        return
    today = _today()
    for sym in c.get("symbols", ["NIFTY"]):
        if mpf.fired_today(sym, today) or mpf.has_open(sym):
            continue
        sig = detect(sym, today, c)
        if not sig:
            continue
        mpf.mark_fired(sym, today)            # one attempt/symbol/day (mark before fire)
        _log(f"{sym}: M-rollover detected @ {sig[0]} (spike x{sig[1]:.2f}) — firing iron-fly")
        try:
            fire_ironfly(sym, SRC, c)
        except Exception as e:
            _log(f"{sym} fire error: {e}")


def mpfly_loop():
    """~20s: M-rollover entry + 50%-credit target + (+max_hold_days) time-exit + expiry backstop."""
    _log("loop started")
    while True:
        try:
            c = cfg()
            today = _today()
            _maybe_enter(c)
            for pos in mpf.list_open():
                sym = pos["symbol"]
                ltp_poller.request_watch([(l["sec_id"], SEG) for l in pos["legs"]
                                          if l.get("status") == "open"])
                # 50% credit target (primary)
                r, mtm = mpf.check_exit(pos, _ltp_of(pos.get("created_ts", 0)))
                if r == "target":
                    _log(f"{sym}: TARGET hit mtm={mtm} (>= {pos['target_pts']}) — close all")
                    _close_all(pos, "MPFLY_TARGET"); continue
                # +max_hold_days time-exit (at squareoff time)
                if mpf.hold_expired(pos, today, _is_trading_day) and _sq_time_reached():
                    _log(f"{sym}: +{pos.get('max_hold_days',1)}d time-exit — close all")
                    _close_all(pos, "MPFLY_TIMEEXIT"); continue
                # weekly-expiry backstop (never carry a dead contract)
                if pos.get("expiry_date") and today >= str(pos["expiry_date"]) and _sq_time_reached():
                    _log(f"{sym}: expiry backstop squareoff (exp={pos['expiry_date']})")
                    _close_all(pos, "MPFLY_EXPIRY")
        except Exception as e:
            _log(f"loop error: {e}")
        time.sleep(20)


if __name__ == "__main__":
    # ---- stubbed integration test (no live Dhan): M-signal -> entry -> decay -> target ----
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

    mpf.STORE = os.path.join(os.path.dirname(mpf.STORE), "m_pattern_ironfly_positions_TEST.json")
    if os.path.exists(mpf.STORE): os.remove(mpf.STORE)

    # 1) shared M-detector fires on a synthetic double-top series (via a fake curves)
    def ramp(x0, v0, v1, n):
        return [{"t": (x0 - 900) * 60 + 34200 - 19800, "straddle": v0 + (v1 - v0) * i / (n - 1)}
                for i, x0 in enumerate(range(x0, x0 + n))]
    series_pts = ([{"t": (m - 900) * 60 + 34200 - 19800, "straddle": 100.0 + (m % 3) * .1} for m in range(900, 990)]
                  + ramp(990, 100, 130, 9)[1:] + ramp(998, 130, 116, 11)[1:]
                  + ramp(1008, 116, 126, 9)[1:] + ramp(1016, 126, 108, 9)[1:])
    oc.curves = lambda sym, date: {"points": series_pts}
    sig = detect("NIFTY", "2026-08-28", cfg())
    assert sig is not None, "expected an M signal from live_series"
    print("signal OK:", sig)

    # 2) entry premiums: SELL ATM 150/140 ; BUY wings 55/50 -> credit 185, target 92.5
    _prices.update({"CE24000": 150, "PE24000": 140, "CE24250": 55, "PE23750": 50})
    pos = fire_ironfly("NIFTY", SRC, cfg())
    assert pos and len(calls["signal"]) == 4, ("entry legs", len(calls["signal"]))
    assert pos["entry_net_credit"] == 185 and pos["target_pts"] == 92.5, pos["entry_net_credit"]
    sides = [a[0][2] for a in calls["signal"]]
    assert sides == ["BUY", "BUY", "SELL", "SELL"], sides        # HEDGE before SELL (never naked)
    print("entry OK: credit", pos["entry_net_credit"], "target", pos["target_pts"], "order", sides)

    # 3) decay -> target: sold 70/60, hedge 22/20 -> 185-88=97 >= 92.5
    _prices.update({"CE24000": 70, "PE24000": 60, "CE24250": 22, "PE23750": 20})
    p = mpf.get(pos["id"])
    r, mtm = mpf.check_exit(p, _ltp_of(p.get("created_ts", 0)))
    assert r == "target" and mtm == 97.0, (r, mtm)
    print("exit-decision OK: mtm", mtm, "reason", r)

    os.remove(mpf.STORE)
    print("\nm_pattern_ironfly_live stubbed integration test PASS")
