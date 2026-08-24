"""
weekly_ironfly.py — PURE state + decision for the WEEKLY POSITIONAL IRON-FLY.
No broker / order / Dhan import → standalone-testable. Firing legs (via
execution_gateway), live LTP, front-weekly-expiry and squareoff are the CALLER's
job (weekly_ironfly_live).

STRATEGY (backtest-validated 2026-08-24, scratch/weekly_ironfly, REAL lake premium
2021-07..2026-07, 5 lots: net +Rs 21.3L, Sharpe 1.93, PF 1.96, every year green,
worst week -Rs 35,645, p=0.000, OOS > train):
  Entry : first trading day AFTER a weekly expiry, 09:20. SELL ATM CE + SELL ATM PE
          (short straddle, max premium) + BUY CE @ ATM+wing / BUY PE @ ATM-wing
          (iron-fly, defined risk). wing=250 (+-5 strikes). one entry per weekly cycle.
  Size  : 5 lots (mirror-lots, single basket gate — never desync sold vs hedge).
  Exit  : running P&L >= take_pct(0.50) x entry NET credit -> close ALL legs.
          else weekly-expiry squareoff (caller). NO stop-loss — the wings ARE the
          defined-risk cap (worst week bounded ~ wing-width minus credit).

Reuses (Rule 6B) auto_strangle_roll.{build_position, position_mtm, check_exit} for the
P&L / target decisions — same math as the strangle, just ATM-sold + no roll + no IV-gate.
Store IS the source of truth for "open?" across restarts (TRAP #76). NOT day-scoped
(positional, multi-day). Own store file — never mixed with the strangle's positions.
"""
import os, sys, json, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import auto_strangle_roll as _sr   # reuse build_position / position_mtm / check_exit (Rule 6B)

DATA = os.path.join(os.path.dirname(HERE), "data")
STORE = os.path.join(DATA, "weekly_ironfly_positions.json")
_LOCK = threading.Lock()

DEF = dict(dist=0, wing=250, take_pct=0.50, lots=5, step=50)


# ------------------------------------------------------------ store (multi-day + expiry marker)
def _read_all():
    try:
        with open(STORE) as f:
            return json.load(f)
    except Exception:
        return {"positions": [], "last_expiry_seen": None}


def _read():
    return _read_all().get("positions", [])


def _write_all(doc):
    os.makedirs(DATA, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, default=str)
    os.replace(tmp, STORE)                       # atomic


def last_expiry_seen():
    return _read_all().get("last_expiry_seen")


def set_last_expiry_seen(exp):
    with _LOCK:
        doc = _read_all()
        doc["last_expiry_seen"] = exp
        _write_all(doc)


def list_open(symbol=None):
    return [p for p in _read() if p.get("status") == "open"
            and (symbol is None or p.get("symbol") == symbol)]


def has_open(symbol=None):
    return len(list_open(symbol)) > 0


def get(pid):
    return next((p for p in _read() if p.get("id") == pid), None)


def add(pos):
    with _LOCK:
        doc = _read_all()
        doc.setdefault("positions", []).append(pos)
        _write_all(doc)
    return pos


def update(pid, mut):
    with _LOCK:
        doc = _read_all()
        ps = doc.get("positions", [])
        for i, p in enumerate(ps):
            if p.get("id") == pid:
                ps[i] = mut(dict(p)); _write_all(doc)
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


# ------------------------------------------------------------ entry-day decision (PURE)
def should_enter(front_expiry_today, marker, has_open_pos):
    """Fire once on the first trading day of a fresh weekly cycle (= the day AFTER the
    previous expiry, when the front weekly expiry rolls forward).

      (do_enter, new_marker, reason)

    front_expiry_today : "YYYY-MM-DD" front weekly expiry as of today (caller: dhan_master).
    marker             : last front-expiry we acted under (persisted). None on first run.
    has_open_pos       : bool — a position is already open.

    Rules:
      - front unknown            -> no entry (don't guess).
      - already open             -> no entry (one per cycle, positional hold).
      - marker is None (bootstrap) -> DON'T enter; just adopt today's front as the marker
                                      (avoids a mid-week first entry on fresh deploy).
                                      Entry begins from the next expiry-roll.
      - front != marker          -> ENTER (a new cycle started since we last acted), advance marker.
      - front == marker          -> no entry (same cycle).
    """
    if not front_expiry_today:
        return False, marker, "no_front_expiry"
    if has_open_pos:
        return False, marker, "already_open"
    if marker is None:
        return False, front_expiry_today, "bootstrap_marker_set"
    if front_expiry_today != marker:
        return True, front_expiry_today, "new_cycle"
    return False, marker, "same_cycle"


# ------------------------------------------------------------ leg spec (iron fly)
def _round(x, step):
    return int(round(x / step) * step)


def entry_spec(spot, wing=DEF["wing"], step=DEF["step"]):
    """Iron-fly strikes: SELL ATM CE+PE, BUY wings +-wing. Caller resolves sec_id/price."""
    atm = _round(spot, step)
    return {
        "CE_sell": atm, "PE_sell": atm,
        "CE_hedge": atm + wing, "PE_hedge": atm - wing,
    }


# ------------------------------------------------------------ build / P&L / exit (reuse strangle helpers)
def build_position(pid, symbol, lots, lot_size, mode, source, group_id,
                   entry_date, expiry_date, spot, legs, cfg=None):
    cfg = {**DEF, **(cfg or {})}
    # auto_strangle_roll.build_position computes net credit + target_pts = take_pct * credit.
    pos = _sr.build_position(pid, symbol, lots, lot_size, mode, source, group_id,
                             entry_date, expiry_date, spot, legs,
                             cfg={"dist": cfg["dist"], "wing": cfg["wing"],
                                  "trig": 0, "take_pct": cfg["take_pct"],
                                  "iv_gate_rank": 0})
    return pos


def position_mtm(pos, ltp_of):
    return _sr.position_mtm(pos, ltp_of)


def check_exit(pos, ltp_of):
    return _sr.check_exit(pos, ltp_of)          # target-only; loss capped by wings


# ------------------------------------------------------------ self-test
if __name__ == "__main__":
    # ---- entry-day gate
    assert should_enter("2026-08-25", None, False)[:2] == (False, "2026-08-25")   # bootstrap
    assert should_enter("2026-09-01", "2026-08-25", False)[0] is True             # rolled -> enter
    assert should_enter("2026-09-01", "2026-09-01", False)[0] is False            # same cycle
    assert should_enter("2026-09-01", "2026-08-25", True)[0] is False             # already open
    assert should_enter(None, "2026-08-25", False)[0] is False                    # no front

    # ---- iron-fly spec
    sp = entry_spec(24010)
    assert sp == {"CE_sell": 24000, "PE_sell": 24000, "CE_hedge": 24250, "PE_hedge": 23750}, sp

    # ---- build + target + exit (real-shape legs)
    legs = [
        dict(opt_type="CE", role="SELL",  side="SELL", strike=24000, entry_price=150, qty=325),
        dict(opt_type="PE", role="SELL",  side="SELL", strike=24000, entry_price=140, qty=325),
        dict(opt_type="CE", role="HEDGE", side="BUY",  strike=24250, entry_price=55,  qty=325),
        dict(opt_type="PE", role="HEDGE", side="BUY",  strike=23750, entry_price=50,  qty=325),
    ]
    pos = build_position("t1", "NIFTY", 5, 65, "paper", "weekly_ironfly", "g1",
                         "2026-08-25", "2026-09-01", 24010, legs)
    # net credit = (150+140) - (55+50) = 185 ; target 50% = 92.5
    assert pos and pos["entry_net_credit"] == 185 and pos["target_pts"] == 92.5, pos["entry_net_credit"]

    # decay: sold fall, hedge fall -> flatten P&L rises toward credit
    ltps = {("SELL", 24000, "CE"): 70, ("SELL", 24000, "PE"): 60,
            ("HEDGE", 24250, "CE"): 22, ("HEDGE", 23750, "PE"): 20}
    lt = lambda l: ltps[(l["role"], l["strike"], l["opt_type"])]
    # cum_cash 185 ; flatten: -70 -60 +22 +20 = 185-88 = 97 >= 92.5 -> target
    r, m = check_exit(pos, lt)
    assert r == "target" and m == 97.0, (r, m)

    # freeze on missing leg price (TRAP #1)
    assert position_mtm(pos, lambda l: None) is None
    print("weekly_ironfly self-test PASS")
