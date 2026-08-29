"""
m_pattern_ironfly.py — PURE state + decision for the IV-pop M-rollover IRON-FLY (02.18).
No broker / order / Dhan import -> standalone-testable. Firing legs (execution_gateway),
live LTP, the M-signal read (option_curves) and squareoff are the CALLER's job
(m_pattern_ironfly_live).

STRATEGY (backtest-validated 2026-08-28, scratch/nifty_m_pattern, REAL lake premium
2021-07..2026-07, 5 lots: 193 trades, net +Rs 3.6L, Sharpe 0.64, PF 1.34, win 65%,
train & OOS both green; entry-time permutation p=0.009 -> M-timing edge REAL; Sharpe<1
+ 9-cell selection -> FORWARD-PAPER, not real money, Rule 10):
  Signal : ATM combined premium (delta-neutral -> IV pop) forms a double-top "M" and
           breaks its neckline (strategies/signals/m_pattern.detect, SINGLE SOURCE).
  Entry  : on the FIRST M-rollover of the day -> SELL ATM CE + SELL ATM PE + BUY wings
           (+-250) = iron-fly, defined risk. one entry per symbol per day.
  Size   : 5 lots (single basket gate — never desync sold vs hedge).
  Exit   : running P&L >= take_pct(0.50) x entry NET credit -> close ALL. else square off
           at 15:20 on the +max_hold_days(1) trading day. NO stop-loss (wings = risk cap).

Reuses (Rule 6B) auto_strangle_roll.{build_position, position_mtm, check_exit}. Store IS
the source of truth for "open?" across restarts (TRAP #76). NOT day-scoped for positions
(hold ~1 day, can cross a date). Own store file. Per-day `fired` marker = one entry/day.
"""
import os, sys, json, time, threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import auto_strangle_roll as _sr   # reuse build_position / position_mtm / check_exit (Rule 6B)

DATA = os.path.join(os.path.dirname(HERE), "data")
STORE = os.path.join(DATA, "m_pattern_ironfly_positions.json")
_LOCK = threading.Lock()

DEF = dict(dist=0, wing=250, take_pct=0.50, lots=5, step=50, max_hold_days=1)


# ------------------------------------------------------------ store
def _read_all():
    try:
        with open(STORE) as f:
            return json.load(f)
    except Exception:
        return {"positions": [], "fired": {}}


def _read():
    return _read_all().get("positions", [])


def _write_all(doc):
    os.makedirs(DATA, exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=1, default=str)
    os.replace(tmp, STORE)                       # atomic


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


# ------------------------------------------------------------ per-day dedup (one entry/day/symbol)
def fired_today(symbol, today):
    return _read_all().get("fired", {}).get(symbol) == today


def mark_fired(symbol, today):
    with _LOCK:
        doc = _read_all()
        doc.setdefault("fired", {})[symbol] = today
        _write_all(doc)


# ------------------------------------------------------------ build / P&L / exit (reuse strangle helpers)
def build_position(pid, symbol, lots, lot_size, mode, source, group_id,
                   entry_date, expiry_date, spot, legs, cfg=None):
    cfg = {**DEF, **(cfg or {})}
    pos = _sr.build_position(pid, symbol, lots, lot_size, mode, source, group_id,
                             entry_date, expiry_date, spot, legs,
                             cfg={"dist": cfg["dist"], "wing": cfg["wing"],
                                  "trig": 0, "take_pct": cfg["take_pct"],
                                  "iv_gate_rank": 0})
    if pos:
        pos["max_hold_days"] = cfg["max_hold_days"]
    return pos


def position_mtm(pos, ltp_of):
    return _sr.position_mtm(pos, ltp_of)


def check_exit(pos, ltp_of):
    return _sr.check_exit(pos, ltp_of)          # target-only; loss capped by wings


# ------------------------------------------------------------ time-exit (PURE)
def hold_expired(pos, today, is_trading_day, max_hold_days=None):
    """True once `max_hold_days` trading days have elapsed since entry_date (inclusive of
    entry day = day 0). Caller passes today's date + an is_trading_day(date_str) callback."""
    from datetime import date, timedelta
    mh = max_hold_days if max_hold_days is not None else pos.get("max_hold_days", DEF["max_hold_days"])
    ed = pos.get("entry_date")
    if not ed:
        return False
    try:
        d0 = date.fromisoformat(str(ed)[:10]); dt = date.fromisoformat(str(today)[:10])
    except Exception:
        return False
    if dt <= d0:
        return False
    n = 0; cur = d0
    while cur < dt:
        cur = cur + timedelta(days=1)
        if is_trading_day(cur.isoformat()):
            n += 1
        if n >= mh:
            return cur <= dt        # deadline reached on/before today
    return n >= mh


# ------------------------------------------------------------ self-test
if __name__ == "__main__":
    # iron-fly build + 50% target (real-shape legs) — same math as weekly_ironfly
    legs = [
        dict(opt_type="CE", role="SELL",  side="SELL", strike=24000, entry_price=150, qty=325),
        dict(opt_type="PE", role="SELL",  side="SELL", strike=24000, entry_price=140, qty=325),
        dict(opt_type="CE", role="HEDGE", side="BUY",  strike=24250, entry_price=55,  qty=325),
        dict(opt_type="PE", role="HEDGE", side="BUY",  strike=23750, entry_price=50,  qty=325),
    ]
    pos = build_position("t1", "NIFTY", 5, 65, "paper", "m_pattern_ironfly", "g1",
                         "2026-08-28", None, 24010, legs)
    assert pos and pos["entry_net_credit"] == 185 and pos["target_pts"] == 92.5, pos["entry_net_credit"]
    assert pos["max_hold_days"] == 1
    ltps = {("SELL", 24000, "CE"): 70, ("SELL", 24000, "PE"): 60,
            ("HEDGE", 24250, "CE"): 22, ("HEDGE", 23750, "PE"): 20}
    lt = lambda l: ltps[(l["role"], l["strike"], l["opt_type"])]
    r, m = check_exit(pos, lt)
    assert r == "target" and m == 97.0, (r, m)     # 185-88 = 97 >= 92.5
    assert position_mtm(pos, lambda l: None) is None    # freeze on missing price (TRAP #1)

    # time-exit: entry Fri 2026-08-28, +1 trading day. Sat/Sun not trading; Mon 08-31 = day 1.
    td = lambda d: d not in ("2026-08-29", "2026-08-30")   # weekend off
    assert hold_expired(pos, "2026-08-28", td) is False    # entry day
    assert hold_expired(pos, "2026-08-29", td) is False    # Sat (0 trading days)
    assert hold_expired(pos, "2026-08-31", td) is True     # Mon = +1 trading day -> exit
    print("m_pattern_ironfly self-test PASS")
