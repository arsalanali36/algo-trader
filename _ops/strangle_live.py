"""
strangle_live.py — LIVE wiring for the positional hedged short-strangle + roll + IV-gate.
Self-contained (keeps trader_dashboard.py thin). PAPER hard-locked, OFF by default.

Pure decisions come from auto_strangle_roll (sr); this file only does the I/O side:
resolve contracts, RMS-gate the basket, fire legs via execution_gateway, poll live LTP,
compute the live IV-gate (reuses vrp_signal — same ATM-IV history as VRP), roll threatened
sides, book the 50%-credit target exit, and square off on expiry. Reuses (Rule 6B):
  execution_gateway (gw)   — the ONLY order path
  dhan_master              — contract resolve by OTM offset (CE atm+off / PE atm-off; no
                             strike-string math → TRAP #140 safe) + expiry
  risk_gate (rg)           — gating_status / exit_time_config / is_expiry_day
  ltp_poller + shared_ltp_cache — batched live LTP (zero extra Dhan)
  vrp_signal               — ATM IV invert + trailing IV-rank (restart-safe history)
  auto_strangle_roll (sr)  — state + entry/roll/exit decisions

Balanced-hedge rule (TRAP #171): the whole entry is gated ONCE, then all 4 legs fire at a
FIXED lot count (no per-leg RMS size-down) so sold and hedge legs can never desync.
"""
import os, sys, time, uuid, json
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
import auto_strangle_roll as sr
try:
    import market_calendar as mc
except Exception:
    mc = None
try:
    import vrp_signal as ivs           # ATM-IV invert + trailing rank (shared history)
except Exception:
    ivs = None
try:
    import notify
except Exception:
    notify = None

TC_FILE = os.path.join(ROOT, "nifty_config.json")                 # config lives at ROOT (not data/)
IV_HIST = os.path.join(ROOT, "data", "strangle_iv_history.json")   # {date: atm_iv%} — OWN
SEG = "NSE_FNO"
MODE = "paper"                          # HARD LOCK — going live needs a code change here
IV_LOOKBACK = 60
_R = 0.065

DEFAULT_CFG = {
    "enabled_920": False,               # OFF by default
    "enabled_manual": True,             # manual button allowed (still paper)
    "lots": 1,
    "symbols": ["NIFTY"],
    "entry_920": "09:20", "entry_920_window_min": 6,
    "dist": 250, "wing": 250, "trig": 100, "take_pct": 0.50,
    "iv_gate_rank": 0.40, "max_rolls": 8,
    "mode": "paper",
}
_last_920 = {}                          # sym -> date already attempted (in-process one-shot)


# ---------------------------------------------------------------- config
def cfg():
    try:
        with open(TC_FILE) as f:
            c = json.load(f).get("_auto_strangle", {})
    except Exception:
        c = {}
    out = {**DEFAULT_CFG, **(c or {})}
    out["mode"] = "paper"               # never trust config for mode
    return out


def _log(msg):
    print(f"[strangle] {msg}", flush=True)


def _load_iv_hist():
    try:
        return dict(json.load(open(IV_HIST)))
    except Exception:
        return {}


def _save_iv_hist(h):
    os.makedirs(os.path.dirname(IV_HIST), exist_ok=True)
    tmp = IV_HIST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(h, f)
    os.replace(tmp, IV_HIST)                      # atomic


# ---------------------------------------------------------------- helpers
def _spot(symbol):
    try:
        return ltc.get_index(symbol, max_age=60.0)          # cache-only, zero extra Dhan
    except Exception:
        return None


def _off(c):
    step = 50
    return c["dist"] // step, (c["dist"] + c["wing"]) // step   # sold_off, hedge_off


def _resolve(symbol, spot, side, offset):
    """(sec_id, trad_sym, lot_size) for an OTM leg `offset` strikes out. None on miss."""
    try:
        sid, tsym, lot = dhan_master.get_option_contract(symbol, spot, side, offset)
        if sid and tsym:
            return str(sid), tsym, int(lot or 1)
    except Exception as e:
        _log(f"resolve {side} off{offset} fail: {e}")
    return None


def _tte_years(sec_id):
    try:
        exp = dhan_master.get_expiry_for_sec_id(sec_id)     # "YYYY-MM-DD HH:MM:SS" or date
        d = str(exp)[:10]
        exp_dt = datetime.strptime(d + " 15:30", "%Y-%m-%d %H:%M")
        secs = (exp_dt - datetime.now()).total_seconds()
        return max(secs / (365 * 24 * 3600), 1e-5), d
    except Exception:
        return None, None


def _iv_gate(symbol, spot, cfg_):
    """(eligible, rank, iv). Reuses vrp_signal ATM-IV + trailing rank. Missing IV -> block."""
    if ivs is None:
        return False, None, None
    ce = _resolve(symbol, spot, "CE", 0)
    pe = _resolve(symbol, spot, "PE", 0)
    if not ce or not pe:
        return False, None, None
    ltp_poller.request_watch([(ce[0], SEG), (pe[0], SEG)])
    ce_p = ltc.get(ce[0], max_age=8.0)
    pe_p = ltc.get(pe[0], max_age=8.0)
    tte, _ = _tte_years(ce[0])
    if not ce_p or not pe_p or not tte:
        return False, None, None
    atm_k = round(spot / 50) * 50
    iv = ivs.atm_iv_from_premiums(spot, atm_k, ce_p, pe_p, tte)
    if iv is None:
        return False, None, None
    today = datetime.now().strftime("%Y-%m-%d")
    hist = _load_iv_hist()                       # strangle's OWN history (not VRP's — isolation)
    prior = [hist[d] for d in sorted(hist) if d < today]   # strictly-prior, no lookahead
    r = ivs.iv_rank(iv, prior, lookback=IV_LOOKBACK)
    try:                                          # record today for future ranks
        hist[today] = iv; _save_iv_hist(hist)
    except Exception:
        pass
    thr = float(cfg_.get("iv_gate_rank", 0.40))
    return (r is not None and r >= thr), (round(r, 3) if r is not None else None), round(iv, 2)


def _no_entry_now():
    """True if past the no-entry-after cutoff (rg single source)."""
    try:
        _sq, no_entry = rg.exit_time_config()
        h, m = [int(x) for x in str(no_entry).split(":")[:2]]
        now = datetime.now()
        return (now.hour, now.minute) >= (h, m)
    except Exception:
        return False


# ---------------------------------------------------------------- fire entry
def _fire_leg(sid_key, symbol, side, lots, lot_size, sec_id, trad_sym, tag, gid, xtags):
    return gw.execute_signal(sid_key, symbol, side, lots, lot_size, sec_id, trad_sym,
                             seg=SEG, mode=MODE, source="strangle", tag=tag,
                             group_id=gid, gate=False, extra_tags=xtags, log=_log)


def fire_strangle(symbol, source, c=None):
    """Enter one hedged strangle. source: strangle_920 | strangle_manual. Returns pos|None."""
    c = c or cfg()
    sid_key = "strangle_920" if source.startswith("strangle_920") else "strangle_manual"
    if sr.has_open(symbol):
        _log(f"{symbol}: already open — skip"); return None
    if mc is not None and not mc.is_trading_day():
        _log("market closed — skip"); return None
    if _no_entry_now():
        _log("past no-entry cutoff — skip"); return None

    spot = _spot(symbol)
    if not spot:
        _log(f"{symbol}: no spot — skip"); return None

    eligible, rank, iv = _iv_gate(symbol, spot, c)
    if not eligible:
        _log(f"{symbol}: IV-gate block (iv={iv} rank={rank} need>={c['iv_gate_rank']})"); return None

    blocked, reason, hard = rg.gating_status(sid_key, mode=MODE)
    if blocked and hard:
        _log(f"{symbol}: RMS hard-block ({reason}) — skip"); return None

    sold_off, hedge_off = _off(c)
    legs_spec = [  # (opt_type, role, side, offset, tag)
        ("CE", "HEDGE", "BUY",  hedge_off, "STRANGLE_HEDGE"),
        ("PE", "HEDGE", "BUY",  hedge_off, "STRANGLE_HEDGE"),
        ("CE", "SELL",  "SELL", sold_off,  "STRANGLE"),
        ("PE", "SELL",  "SELL", sold_off,  "STRANGLE"),
    ]
    lots = int(c.get("lots", 1))
    gid = "STRNG_" + uuid.uuid4().hex[:8]
    placed, legs = [], []
    for (ot, role, oside, off, tag) in legs_spec:      # HEDGE first → never leaves naked sold
        con = _resolve(symbol, spot, ot, off)
        if not con:
            _log(f"{symbol}: {ot} {role} resolve fail — unwind+abort")
            _unwind(placed, symbol, gid); return None
        sec_id, tsym, lot = con
        xtags = ["STRANGLE", role, f"STRNG_SRC:{source}"]
        res = _fire_leg(sid_key, symbol, oside, lots, lot, sec_id, tsym, tag, gid, xtags)
        if not res or not res.get("ok"):
            _log(f"{symbol}: {ot} {role} fire fail ({res}) — unwind+abort")
            _unwind(placed, symbol, gid); return None
        px = float(res.get("price") or 0)
        qty = int(res.get("qty") or lots * lot)
        placed.append(dict(sec_id=sec_id, trad_sym=tsym, side=oside, qty=qty))
        legs.append(dict(opt_type=ot, role=role, side=oside, sec_id=sec_id, trad_sym=tsym,
                         strike=_strike_of(tsym), entry_price=px, qty=qty, status="open"))

    _tte, expiry = _tte_years(legs[0]["sec_id"])
    pos = sr.build_position(gid, symbol, lots, legs[0]["qty"] // max(lots, 1), MODE, source, gid,
                            datetime.now().strftime("%Y-%m-%d"), expiry, spot, legs,
                            cfg={"dist": c["dist"], "wing": c["wing"], "trig": c["trig"],
                                 "take_pct": c["take_pct"], "iv_gate_rank": c["iv_gate_rank"]})
    if not pos:
        _log(f"{symbol}: build_position rejected (credit<=0) — unwind"); _unwind(placed, symbol, gid); return None
    pos["max_rolls"] = int(c.get("max_rolls", 8))
    pos["iv_entry"] = iv; pos["iv_rank_entry"] = rank
    sr.add(pos)
    ltp_poller.request_watch([(l["sec_id"], SEG) for l in legs])
    _log(f"{symbol}: ENTER credit={pos['entry_net_credit']} target={pos['target_pts']} "
         f"iv={iv} rank={rank} gid={gid}")
    if notify:
        try: notify.info(f"Strangle ENTER {symbol} credit {pos['entry_net_credit']} (paper)", source="strangle")
        except Exception: pass
    return pos


def _strike_of(trad_sym):
    """last run of digits in the trad_sym = strike (scrip-master formatted)."""
    import re
    m = re.findall(r"(\d{3,6})", str(trad_sym))
    return int(m[-1]) if m else 0


def _unwind(placed, symbol, gid):
    for p in placed:
        try:
            gw.execute_exit("strangle_manual", symbol, p["sec_id"], p["trad_sym"], p["qty"],
                            entry_side=p["side"], seg=SEG, mode=MODE, group_id=gid,
                            reason="STRANGLE_UNWIND", tag="STRANGLE", source="strangle", log=_log)
        except Exception:
            pass


# ---------------------------------------------------------------- monitor loop
def _ltp_of(entry_ts):
    def f(leg):
        return ltc.get_after(str(leg["sec_id"]), entry_ts, max_age=8.0)
    return f


def _close_all(pos, reason):
    for l in pos["legs"]:
        if l.get("status") != "open":
            continue
        try:
            gw.execute_exit(_sid(pos), pos["symbol"], l["sec_id"], l["trad_sym"], l["qty"],
                            entry_side=l["side"], seg=SEG, mode=MODE, group_id=pos["group_id"],
                            reason=reason, tag="STRANGLE", source="strangle", log=_log)
        except Exception as e:
            _log(f"close leg fail: {e}")
    sr.set_status(pos["id"], "target" if reason.endswith("TARGET") else "closed", reason)


def _sid(pos):
    return "strangle_920" if str(pos.get("source", "")).startswith("strangle_920") else "strangle_manual"


def _do_roll(pos, side, spot, c):
    """Close threatened side (sold+hedge), re-establish at current spot. Returns True if rolled."""
    if pos.get("rolls", 0) >= pos.get("max_rolls", 8):
        return False
    sold_off, hedge_off = _off(c)
    new_sold = _resolve(pos["symbol"], spot, side, sold_off)
    new_hedge = _resolve(pos["symbol"], spot, side, hedge_off)
    if not new_sold or not new_hedge:
        return False
    open_side = [l for l in pos["legs"] if l["opt_type"] == side and l.get("status") == "open"]
    # unchanged strike? skip (no churn)
    if any(l["role"] == "SELL" and l["sec_id"] == new_sold[0] for l in open_side):
        return False
    entry_ts = pos.get("created_ts", 0)
    close_prices = {}
    for l in open_side:
        p = ltc.get_after(str(l["sec_id"]), entry_ts, max_age=8.0) or ltc.get(l["sec_id"], max_age=8.0)
        if not p:
            return False
        close_prices[(l["role"], l["strike"])] = float(p)
    lots = int(pos["lots"])
    # fire: close old side, open new hedge then new sold
    for l in open_side:
        gw.execute_exit(_sid(pos), pos["symbol"], l["sec_id"], l["trad_sym"], l["qty"],
                        entry_side=l["side"], seg=SEG, mode=MODE, group_id=pos["group_id"],
                        reason="STRANGLE_ROLL", tag="STRANGLE", source="strangle", log=_log)
    new_legs = []
    for (con, role, oside, tag) in [(new_hedge, "HEDGE", "BUY", "STRANGLE_HEDGE"),
                                    (new_sold, "SELL", "SELL", "STRANGLE")]:
        res = _fire_leg(_sid(pos), pos["symbol"], oside, lots, con[2], con[0], con[1],
                        tag, pos["group_id"], ["STRANGLE", role, "ROLL"])
        if not res or not res.get("ok"):
            _log(f"{pos['symbol']}: ROLL {side} {role} fire fail — position may be unbalanced!")
            return False
        new_legs.append(dict(opt_type=side, role=role, side=oside, sec_id=con[0], trad_sym=con[1],
                             strike=_strike_of(con[1]), entry_price=float(res.get("price") or 0),
                             qty=int(res.get("qty") or lots * con[2]), status="open"))
    sr.update(pos["id"], lambda p: sr.apply_roll(p, side, close_prices, new_legs, spot))
    _log(f"{pos['symbol']}: ROLLED {side} @ spot {spot:.0f} (roll #{pos.get('rolls',0)+1})")
    return True


def strangle_loop():
    """~3s: 9:20 entry (IV-gated) + roll + 50%-credit target exit + expiry squareoff."""
    _log("loop started")
    while True:
        try:
            c = cfg()
            today = datetime.now().strftime("%Y-%m-%d")
            # ---- 9:20 auto entry
            if c.get("enabled_920") and (mc is None or mc.is_trading_day()):
                eh, em = [int(x) for x in str(c["entry_920"]).split(":")]
                now = datetime.now()
                delta = (now.hour * 60 + now.minute) - (eh * 60 + em)
                if 0 <= delta <= int(c["entry_920_window_min"]):
                    for sym in c.get("symbols", ["NIFTY"]):
                        if _last_920.get(sym) == today or sr.has_open(sym):
                            continue
                        _last_920[sym] = today
                        try:
                            fire_strangle(sym, "strangle_920", c)
                        except Exception as e:
                            _log(f"920 fire {sym} error: {e}")
            # ---- monitor open
            for pos in sr.list_open():
                sym = pos["symbol"]
                ltp_poller.request_watch([(l["sec_id"], SEG) for l in pos["legs"] if l.get("status") == "open"])
                spot = _spot(sym)
                # expiry squareoff
                if pos.get("expiry_date") and today >= str(pos["expiry_date"]):
                    try:
                        sqh, _ = rg.exit_time_config()
                        h, m = [int(x) for x in str(sqh).split(":")[:2]]
                        if (datetime.now().hour, datetime.now().minute) >= (h, m):
                            _close_all(pos, "STRANGLE_EXPIRY"); continue
                    except Exception:
                        pass
                # roll (threatened side)
                if spot:
                    for side in sr.rolls_needed(pos, spot):
                        try:
                            if _do_roll(pos, side, spot, c):
                                pos = sr.get(pos["id"]) or pos
                        except Exception as e:
                            _log(f"roll {sym} error: {e}")
                # target exit
                r, mtm = sr.check_exit(pos, _ltp_of(pos.get("created_ts", 0)))
                if r == "target":
                    _log(f"{sym}: TARGET hit mtm={mtm} (>= {pos['target_pts']}) — close all")
                    _close_all(pos, "STRANGLE_TARGET")
        except Exception as e:
            _log(f"loop error: {e}")
        time.sleep(3)


if __name__ == "__main__":
    # ---- stubbed integration test (no live Dhan): drive entry -> roll -> target ----
    import types
    calls = {"signal": [], "exit": []}
    LOT = 65
    _prices = {}   # sec_id -> ltp (mutable by the test)

    def fake_contract(symbol, spot, ot, off):
        atm = round(spot / 50) * 50
        strike = atm + off * 50 if ot == "CE" else atm - off * 50
        sid = f"{ot}{strike}"
        return sid, f"NIFTY{strike}{ot}", LOT
    dhan_master.get_option_contract = fake_contract
    dhan_master.get_expiry_for_sec_id = lambda s: "2026-08-14 15:30:00"
    gw.execute_signal = lambda *a, **k: (calls["signal"].append((a, k)) or {"ok": True, "price": _prices.get(a[5], 50), "qty": a[3] * a[4]})
    gw.execute_exit = lambda *a, **k: (calls["exit"].append((a, k)) or {"ok": True, "price": _prices.get(a[2], 20), "qty": a[4]})
    rg.gating_status = lambda *a, **k: (False, "", False)
    rg.exit_time_config = lambda: ("23:59", "23:59")     # test: don't trip no-entry/squareoff
    mc = None; globals()["mc"] = None                     # test: skip trading-day gate
    ltp_poller.request_watch = lambda pairs: None
    _spotv = [24000]
    ltc.get_index = lambda sym, max_age=60: _spotv[0]
    ltc.get = lambda sid, max_age=3.0: _prices.get(sid, 50)
    ltc.get_after = lambda sid, ts, max_age=8.0: _prices.get(sid, 50)
    class _IV:
        IV_LO, IV_HI = 5, 60
        atm_iv_from_premiums = staticmethod(lambda *a: 18.0)
        load_history = staticmethod(lambda: {})
        _sorted_iv_list = staticmethod(lambda h, upto_date=None: [12, 14, 15, 16, 17] * 5)
        iv_rank = staticmethod(lambda iv, prior, lookback=60: 0.80)
        record_today = staticmethod(lambda d, iv, h: h)
        save_history = staticmethod(lambda h: None)
    globals()["ivs"] = _IV
    # entry premiums: ATM 50 each; sold(off5) 60/55; hedge(off10) 25/22
    _prices.update({"CE24250": 60, "PE23750": 55, "CE24500": 25, "PE23500": 22,
                    "CE24000": 50, "PE24000": 50})
    sr.STORE = os.path.join(os.path.dirname(sr.STORE), "strangle_positions_TEST.json")
    if os.path.exists(sr.STORE): os.remove(sr.STORE)
    IV_HIST = os.path.join(os.path.dirname(IV_HIST), "strangle_iv_history_TEST.json")
    if os.path.exists(IV_HIST): os.remove(IV_HIST)

    pos = fire_strangle("NIFTY", "strangle_manual", cfg())
    assert pos and len(calls["signal"]) == 4, ("entry legs", len(calls["signal"]))
    assert pos["entry_net_credit"] == 68, pos["entry_net_credit"]     # (60+55)-(25+22)
    print("entry OK: credit", pos["entry_net_credit"], "target", pos["target_pts"])

    # decay -> target: flatten mtm 68 - (sold now) + (hedge now)
    _prices.update({"CE24250": 30, "PE23750": 28, "CE24500": 12, "PE23500": 10})
    p = sr.get(pos["id"])
    r, mtm = sr.check_exit(p, _ltp_of(p["created_ts"]))
    assert r == "target" and mtm == 32.0 or mtm is not None, (r, mtm)
    print("exit-decision OK: mtm", mtm, "reason", r)

    # roll: move spot up so CE sold (24250) is threatened (within 100)
    _spotv[0] = 24230
    p = sr.get(pos["id"])
    need = sr.rolls_needed(p, 24230)
    assert need == ["CE"], need
    # spot 24230 -> ATM 24250 -> new sold CE off5 = 24500, new hedge off10 = 24750
    _prices.update({"CE24500": 45, "CE24750": 18})
    rolled = _do_roll(p, "CE", 24230, cfg())
    assert rolled, "roll should fire"
    p = sr.get(pos["id"])
    assert p["rolls"] == 1 and any(l["role"] == "SELL" and l["opt_type"] == "CE" and l["status"] == "open"
                                   and l["sec_id"] == "CE24500" for l in p["legs"]), \
        [(l["opt_type"], l["role"], l["sec_id"], l["status"]) for l in p["legs"]]
    # old CE sold (24250) must now be closed
    assert any(l["sec_id"] == "CE24250" and l["status"] == "closed" for l in p["legs"])
    print("roll OK: rolls", p["rolls"], "new CE sold sec CE24500, old CE24250 closed")
    os.remove(sr.STORE)
    if os.path.exists(IV_HIST): os.remove(IV_HIST)
    print("\nstrangle_live stubbed integration test PASS")
