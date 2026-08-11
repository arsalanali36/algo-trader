"""
auto_strangle_roll.py — POSITIONAL hedged short-strangle with roll-away + IV-gate.
PURE state + decision (no broker / order / Dhan import) — standalone-testable,
mirrors auto_straddle.py. Firing legs (via execution_gateway), live LTP, live IV
and weekly-expiry squareoff are the CALLER's job (trader_dashboard).

STRATEGY (backtest-validated 2026-08-11, scratch/strangle_roll):
  Entry  : 09:20, IF trailing-60d ATM-IV rank >= iv_gate_rank. Sell CE @ spot+dist,
           PE @ spot-dist; BUY a protective wing @ spot±(dist+wing) each side (defined
           risk → low margin → more lots). dist=250, wing=250, trig=100.
  Roll   : spot within trig of an OPEN sold leg → buy-to-close that side (sold+hedge),
           re-sell + re-hedge `dist` from the CURRENT spot (threatened side only).
  Exit   : running P&L >= take_pct(0.5) × entry NET credit → close ALL legs.
           else weekly-expiry squareoff (caller).

Reuses auto_straddle.net_credit / check_exit_net (Rule 6B). State is NOT day-scoped
(positional, multi-day): data/strangle_positions.json = {"positions": [...]}. The store
IS the source of truth for "open?" across restarts (TRAP #76 pattern).
"""
import os, sys, json, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import auto_straddle as _as   # reuse net_credit / check_exit_net (Rule 6B)

DATA = os.path.join(os.path.dirname(HERE), "data")
STORE = os.path.join(DATA, "strangle_positions.json")
_LOCK = threading.Lock()

DEF = dict(dist=250, wing=250, trig=100, take_pct=0.50, iv_gate_rank=0.40, step=50)


# ------------------------------------------------------------ store (multi-day)
def _read():
    try:
        with open(STORE) as f:
            d = json.load(f)
        return d.get("positions", [])
    except Exception:
        return []


def _write(positions):
    os.makedirs(DATA, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"positions": positions}, f, indent=1, default=str)
    os.replace(tmp, STORE)


def list_open(symbol=None):
    return [p for p in _read() if p.get("status") == "open"
            and (symbol is None or p.get("symbol") == symbol)]


def has_open(symbol):
    return len(list_open(symbol)) > 0


def get(pid):
    return next((p for p in _read() if p.get("id") == pid), None)


def add(pos):
    with _LOCK:
        ps = _read(); ps.append(pos); _write(ps)
    return pos


def update(pid, mut):
    """mut(pos)->pos ; atomic read-modify-write."""
    with _LOCK:
        ps = _read()
        for i, p in enumerate(ps):
            if p.get("id") == pid:
                ps[i] = mut(dict(p)); _write(ps)
                return ps[i]
    return None


def set_status(pid, status, result="", exit_net=None):
    def m(p):
        p["status"] = status
        if result:
            p["result"] = result
        if exit_net is not None:
            p["exit_net"] = exit_net
        p["closed_ts"] = time.time()
        return p
    return update(pid, m)


# ------------------------------------------------------------ entry gate + specs
def entry_allowed(iv_rank, iv_gate_rank=DEF["iv_gate_rank"]):
    """True only if today's trailing IV rank clears the gate. None rank → block
    (no IV → don't guess, don't enter)."""
    if iv_rank is None:
        return False
    try:
        return float(iv_rank) >= float(iv_gate_rank)
    except (TypeError, ValueError):
        return False


def _round(x, step):
    return int(round(x / step) * step)


def side_strikes(spot, side, dist=DEF["dist"], wing=DEF["wing"], step=DEF["step"]):
    """(sold_strike, hedge_strike) for one side at `dist` from spot, hedge `wing` beyond."""
    if side == "CE":
        sk = _round(spot + dist, step); hk = _round(sk + wing, step)
    else:
        sk = _round(spot - dist, step); hk = _round(sk - wing, step)
    return sk, hk


def entry_spec(spot, dist=DEF["dist"], wing=DEF["wing"], step=DEF["step"]):
    """All 4 legs' strikes for a fresh entry. Caller resolves sec_id/trad_sym/price."""
    out = {}
    for side in ("CE", "PE"):
        sk, hk = side_strikes(spot, side, dist, wing, step)
        out[side] = {"sell_k": sk, "hedge_k": hk}
    return out


# ------------------------------------------------------------ roll decision
def sold_leg(pos, side):
    return next((l for l in pos["legs"] if l["opt_type"] == side
                 and l["role"] == "SELL" and l.get("status", "open") == "open"), None)


def rolls_needed(pos, spot):
    """Sides whose OPEN sold leg is within `trig` of spot (threatened). Threatened-only
    roll (backtest winner). Returns [] if data degenerate."""
    trig = pos.get("trig", DEF["trig"])
    out = []
    for side in ("CE", "PE"):
        sl = sold_leg(pos, side)
        if sl and abs(float(spot) - float(sl["strike"])) <= trig:
            out.append(side)
    return out


# ------------------------------------------------------------ P&L / exit (roll-aware)
def _cum_cash(pos):
    """points collected(-paid) so far across all fills (SELL +entry/-exit, BUY -entry/+exit)."""
    c = 0.0
    for l in pos["legs"]:
        sgn = 1.0 if l["side"] == "SELL" else -1.0
        c += sgn * float(l.get("entry_price", 0))
        if l.get("status") == "closed" and l.get("exit_price") is not None:
            c += -sgn * float(l["exit_price"])
    return round(c, 2)


def position_mtm(pos, ltp_of):
    """Running P&L in points if we flatten NOW. ltp_of(leg)->float|None.
    Returns (mtm|None). None if ANY open leg's LTP missing (freeze — TRAP #1)."""
    c = _cum_cash(pos)
    for l in pos["legs"]:
        if l.get("status") != "open":
            continue
        p = ltp_of(l)
        try:
            p = float(p)
        except (TypeError, ValueError):
            return None
        if p <= 0:
            return None
        c += (p if l["side"] == "BUY" else -p)   # flatten: buy back sold(-), sell hedge(+)
    return round(c, 2)


def check_exit(pos, ltp_of):
    """(reason|None, mtm|None). Target only — loss is managed by roll+hedge cap."""
    mtm = position_mtm(pos, ltp_of)
    if mtm is None:
        return None, None
    if mtm >= float(pos.get("target_pts", 1e9)):
        return "target", mtm
    return None, mtm


# ------------------------------------------------------------ build a new position
def build_position(pid, symbol, lots, lot_size, mode, source, group_id,
                   entry_date, expiry_date, spot, legs, cfg=None):
    """`legs`: list of {opt_type,role,side,sec_id,trad_sym,strike,entry_price,qty}
    already filled by the caller (real premiums). Computes entry NET credit + target."""
    cfg = {**DEF, **(cfg or {})}
    net, ok = _as.net_credit(legs, lambda l: l.get("entry_price"))
    if not ok or net is None or net <= 0:
        return None
    for l in legs:
        l.setdefault("status", "open")
    return dict(id=pid, symbol=symbol, lots=lots, lot_size=lot_size, mode=mode,
                source=source, group_id=group_id, entry_date=entry_date,
                expiry_date=expiry_date, entry_spot=round(float(spot), 1),
                dist=cfg["dist"], wing=cfg["wing"], trig=cfg["trig"],
                take_pct=cfg["take_pct"], iv_gate_rank=cfg["iv_gate_rank"],
                entry_net_credit=net, target_pts=round(cfg["take_pct"] * net, 2),
                legs=legs, rolls=0, roll_log=[], status="open", result="",
                created_ts=time.time(), closed_ts=None, exit_net=None)


def apply_roll(pos, side, close_prices, new_legs, spot, ts=None):
    """Close side's open legs at close_prices{leg_key:price}, append new_legs (SELL+HEDGE).
    Returns mutated pos (does NOT persist — caller update()s)."""
    for l in pos["legs"]:
        if l["opt_type"] == side and l.get("status") == "open":
            key = (l["role"], l["strike"])
            if key in close_prices:
                l["status"] = "closed"; l["exit_price"] = close_prices[key]
    for nl in new_legs:
        nl.setdefault("status", "open")
        pos["legs"].append(nl)
    pos["rolls"] = pos.get("rolls", 0) + 1
    pos.setdefault("roll_log", []).append(
        {"ts": ts or time.time(), "side": side, "spot": round(float(spot), 1)})
    return pos


# ------------------------------------------------------------ self-test
if __name__ == "__main__":
    # one hedged-strangle life: enter, price moves, hit 50% target.
    spot = 24000
    spec = entry_spec(spot)            # CE sell 24250 hedge 24500 ; PE sell 23750 hedge 23500
    assert spec["CE"]["sell_k"] == 24250 and spec["CE"]["hedge_k"] == 24500
    assert spec["PE"]["sell_k"] == 23750 and spec["PE"]["hedge_k"] == 23500
    legs = [
        dict(opt_type="CE", role="SELL",  side="SELL", strike=24250, entry_price=60, qty=65),
        dict(opt_type="CE", role="HEDGE", side="BUY",  strike=24500, entry_price=25, qty=65),
        dict(opt_type="PE", role="SELL",  side="SELL", strike=23750, entry_price=55, qty=65),
        dict(opt_type="PE", role="HEDGE", side="BUY",  strike=23500, entry_price=22, qty=65),
    ]
    pos = build_position("t1", "NIFTY", 1, 65, "paper", "sched_920", "g1",
                         "2026-08-11", "2026-08-14", spot, legs)
    # entry net credit = (60+55) - (25+22) = 68 ; target = 34
    assert pos["entry_net_credit"] == 68 and pos["target_pts"] == 34.0, pos["entry_net_credit"]

    # premiums decay: sold fall, hedge fall. flatten P&L should be positive.
    ltps = {("SELL", 24250): 30, ("HEDGE", 24500): 12, ("SELL", 23750): 28, ("HEDGE", 23500): 10}
    mtm = position_mtm(pos, lambda l: ltps[(l["role"], l["strike"])])
    # cum_cash 68 ; flatten: -30 -28 +12 +10 = 68-36 = 32  -> below 34, no exit
    assert mtm == 32.0, mtm
    assert check_exit(pos, lambda l: ltps[(l["role"], l["strike"])])[0] is None

    # decay a bit more -> cross target
    ltps2 = {("SELL", 24250): 28, ("HEDGE", 24500): 12, ("SELL", 23750): 26, ("HEDGE", 23500): 10}
    r, m = check_exit(pos, lambda l: ltps2[(l["role"], l["strike"])])
    assert r == "target" and m == 36.0, (r, m)

    # IV gate
    assert entry_allowed(0.55, 0.40) is True
    assert entry_allowed(0.30, 0.40) is False
    assert entry_allowed(None) is False

    # freeze on missing leg price
    assert position_mtm(pos, lambda l: None) is None

    # roll: CE threatened (spot 24240 within 100 of 24250)
    assert rolls_needed(pos, 24240) == ["CE"]
    print("auto_strangle_roll self-test PASS")
