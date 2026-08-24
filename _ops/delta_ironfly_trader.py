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


def tick(now_utc=None, log=print):
    cfg = _config()
    st = _load()
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
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
