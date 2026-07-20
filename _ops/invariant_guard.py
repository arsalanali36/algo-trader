#!/usr/bin/env python3
"""invariant_guard.py — PROACTIVE "does the app match reality + do the always-true
rules hold?" sentinel.

Philosophy (memory: feedback-proactive-safety-philosophy): catch a mistake BEFORE
it becomes a visible/expensive problem. You cannot pre-know every bug — but you CAN
detect "app ≠ reality" or "an always-true rule just broke" the moment it happens,
without knowing the bug's name. Every past bug should become an INVARIANT here so
it can never silently recur.

READ-ONLY: this module never places, cancels, or modifies an order. It only reads
order_store + the broker's real positions and RAISES A LOUD ALERT on any violation
(notify.error → dashboard banner). Safe to run every cycle.

Each invariant is a small function returning a list of Violation. Adding a new
guard = add one function to INVARIANTS. Fail-SAFE: if a check can't run (broker
fetch failed, etc.) it reports 'unknown', never a false 'all-clear' and never a
false alarm.

Run:
  python -X utf8 _ops/invariant_guard.py            # human summary
  python -X utf8 _ops/invariant_guard.py --json     # machine
  python -X utf8 _ops/invariant_guard.py --alert     # also fire notify alerts
Wired (read-only) into pos_monitor_loop every ~120s.
"""
import sys
import os
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    import _paths  # noqa: F401  (adds project dirs to sys.path)
except Exception:
    for _p in ("_core", "_data", "."):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", _p))

# ── sane bounds (tunable) ─────────────────────────────────────────────────────
MTM_SANE_ABS = 5_000_000        # any single position's implied value or day P&L
                                # beyond ±₹50L is almost certainly a phantom/bug
                                # (the 2026-07-03 phantom peak was ₹15.7L).


def _ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


class Violation:
    def __init__(self, severity, invariant, detail, key=None):
        self.severity = severity          # "RED" | "WARN" | "UNKNOWN"
        self.invariant = invariant
        self.detail = detail
        self.key = key or invariant       # stable notify key
    def __repr__(self):
        return f"[{self.severity}] {self.invariant}: {self.detail}"
    def as_dict(self):
        return {"severity": self.severity, "invariant": self.invariant,
                "detail": self.detail, "key": self.key}


# ── shared readers (read-only) ────────────────────────────────────────────────
def _app_live_net(date):
    """order_store's LIVE-kite net qty per contract (signed: BUY +, SELL −).
    Intraday today-open + positional (allow_overnight) carried-over; excludes
    paper, CAPITAL_BLOCKED, and non-kite legs. This is 'what the app believes it
    holds live'."""
    import order_store
    try:
        import risk_gate
    except Exception:
        risk_gate = None
    rows = list(order_store.trades_for(date).get("open", []) or [])
    lb = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30) - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        for r in order_store.trades_for_range(lb, date).get("open", []) or []:
            if risk_gate and risk_gate.allow_overnight(r.get("strategy")):
                rows.append(r)      # positional legs entered on a prior day
    except Exception:
        pass
    net, seen = {}, set()
    for p in rows:
        rid = p.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        if (p.get("mode") or "") != "live":
            continue
        if (p.get("broker") or "").lower() != "kite":
            continue
        if "CAPITAL_BLOCKED" in (p.get("tags") or []):
            continue
        s = p.get("sym") or ""
        if not s:
            continue
        q = int(p.get("qty") or 0)
        net[s] = net.get(s, 0) + (q if p.get("entry") == "BUY" else -q)
    return {k: v for k, v in net.items() if v != 0}


def _broker_net_kite():
    """Kite's REAL net per contract, keyed by Dhan trad_sym (resolve_dhan).
    Returns None if the broker can't be reached (→ UNKNOWN, never a false clear)."""
    try:
        from brokers import get_broker
        kb = get_broker("kite")
        raw = kb.positions() or {}        # {kite_sym: signed_net}
    except Exception:
        return None
    out = {}
    for kite_sym, qty in raw.items():
        if not qty:
            continue
        dsym = kite_sym
        try:
            if hasattr(kb, "resolve_dhan"):
                _sec, _trad, _lot = kb.resolve_dhan(kite_sym)
                if _trad:
                    dsym = _trad
        except Exception:
            pass
        out[dsym] = out.get(dsym, 0) + int(qty)
    return {k: v for k, v in out.items() if v != 0}


# ── invariants ────────────────────────────────────────────────────────────────
def inv_app_matches_broker(date):
    """#1 — app's live net == Kite's real net, per contract. Catches phantom /
    ghost / double-count / untracked — WITHOUT knowing the bug: app ≠ reality."""
    app = _app_live_net(date)
    brk = _broker_net_kite()
    if brk is None:
        return [Violation("UNKNOWN", "app_vs_broker",
                          "Kite positions unreachable — cannot verify app==reality this cycle",
                          key="inv_broker_unreachable")]
    out = []
    for sym in sorted(set(app) | set(brk)):
        a, b = app.get(sym, 0), brk.get(sym, 0)
        if a != b:
            out.append(Violation("RED", "app_vs_broker",
                                  f"{sym}: app says {a:+d}, Kite says {b:+d} (diff {a-b:+d}) "
                                  f"— phantom/ghost/untracked; investigate before acting",
                                  key=f"inv_netmismatch_{sym}"))
    return out


def inv_no_blank_symbol(date):
    """No open live position without an option symbol (the nameless-order bug)."""
    import order_store
    out = []
    for p in order_store.trades_for(date).get("open", []) or []:
        if "CAPITAL_BLOCKED" in (p.get("tags") or []):
            continue
        if not (p.get("sym") or "").strip():
            out.append(Violation("RED", "blank_symbol",
                                  f"open position id={p.get('id')} strat={p.get('strategy')} "
                                  f"sec_id={p.get('sec_id')} has NO symbol — cannot be exited safely",
                                  key=f"inv_blanksym_{p.get('id')}"))
    return out


def inv_no_bad_price(date):
    """No filled open position at price<=0 or qty<=0 (₹0-fill → fake P&L, TRAP #1)."""
    import order_store
    out = []
    for p in order_store.trades_for(date).get("open", []) or []:
        if "CAPITAL_BLOCKED" in (p.get("tags") or []):
            continue
        px = float(p.get("entry_price") or 0)
        q = int(p.get("qty") or 0)
        if px <= 0 or q <= 0:
            out.append(Violation("RED", "bad_price_qty",
                                  f"open id={p.get('id')} {p.get('sym')} price={px} qty={q} "
                                  f"— ₹0/qty0 fabricates P&L",
                                  key=f"inv_badpx_{p.get('id')}"))
    return out


def inv_no_duplicate_trade_id(date):
    """No single broker trade-id recorded on more than one row (double-count source).
    A strategy exit stores the raw trade-id in correlation_id; a manual row stores
    MANUAL_TID_<id>. The SAME underlying trade-id on 2 rows = the same fill counted
    twice (the 2026-07-20 phantom)."""
    import sqlite3
    import order_store
    out = []
    try:
        c = sqlite3.connect(str(order_store.DB_PATH)); c.row_factory = sqlite3.Row
        rows = c.execute("SELECT id, correlation_id, trad_sym FROM orders WHERE date=?", (date,)).fetchall()
        c.close()
    except Exception:
        return [Violation("UNKNOWN", "duplicate_trade_id", "could not read order_store")]
    seen = {}
    for r in rows:
        corr = str(r["correlation_id"] or "")
        if not corr:
            continue
        tid = corr[len("MANUAL_TID_"):] if corr.startswith("MANUAL_TID_") else corr
        if not tid.isdigit():
            continue                       # only real broker trade-ids
        seen.setdefault(tid, []).append(r["id"])
    for tid, ids in seen.items():
        if len(ids) > 1:
            out.append(Violation("RED", "duplicate_trade_id",
                                  f"broker trade-id {tid} recorded on {len(ids)} rows {ids} "
                                  f"— same fill counted twice",
                                  key=f"inv_duptid_{tid}"))
    return out


def inv_mtm_sane(date):
    """No open position whose implied notional is absurd (phantom ₹-lakh, TRAP #92-94)."""
    import order_store
    out = []
    for p in order_store.trades_for(date).get("open", []) or []:
        val = abs(float(p.get("entry_price") or 0) * int(p.get("qty") or 0))
        if val > MTM_SANE_ABS:
            out.append(Violation("RED", "mtm_sane",
                                  f"open id={p.get('id')} {p.get('sym')} implied value ₹{val:,.0f} "
                                  f"exceeds sane ₹{MTM_SANE_ABS:,} — likely phantom",
                                  key=f"inv_mtm_{p.get('id')}"))
    return out


INVARIANTS = [
    inv_app_matches_broker,
    inv_no_blank_symbol,
    inv_no_bad_price,
    inv_no_duplicate_trade_id,
    inv_mtm_sane,
]


def check_all(date=None):
    date = date or _ist_today()
    violations = []
    for fn in INVARIANTS:
        try:
            violations.extend(fn(date) or [])
        except Exception as e:
            violations.append(Violation("UNKNOWN", fn.__name__,
                                        f"invariant raised {e!r} — could not verify",
                                        key=f"inv_err_{fn.__name__}"))
    return violations


def _fired_keys_path():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "data" / "invariant_fired_keys.json"


def _load_fired_keys():
    try:
        return set(json.loads(_fired_keys_path().read_text()))
    except Exception:
        return set()


def _save_fired_keys(keys):
    try:
        p = _fired_keys_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(sorted(keys)))
    except Exception:
        pass


def run(date=None, alert=False, log=print):
    """Check + (optionally) fire loud alerts. Returns the violation list.
    Read-only. Call every ~120s from pos_monitor_loop (alert=True).

    Auto-clears: a violation that was firing last cycle but is now clean gets its
    bell resolved ('✓ fixed'), so a stale red badge never lingers (a stale alert
    erodes trust as much as a missing one — notify.py's own lesson)."""
    v = check_all(date)
    if alert:
        try:
            import notify
            cur = {x.key for x in v if x.severity in ("RED", "UNKNOWN")}
            for x in v:
                if x.severity in ("RED", "UNKNOWN"):
                    notify.error(f"INVARIANT {x.invariant}: {x.detail}",
                                 key=x.key, source="invariant_guard")
            for stale in (_load_fired_keys() - cur):     # was firing, now clean
                try:
                    notify.resolve(stale)
                except Exception:
                    pass
            _save_fired_keys(cur)
        except Exception as e:
            log(f"[invariant_guard] alert failed: {e}", flush=True)
    return v


def main():
    date = _ist_today()
    as_json = "--json" in sys.argv
    alert = "--alert" in sys.argv
    v = run(date, alert=alert)
    if as_json:
        print(json.dumps([x.as_dict() for x in v]))
    else:
        reds = [x for x in v if x.severity == "RED"]
        unk = [x for x in v if x.severity == "UNKNOWN"]
        print(f"Invariant guard — {date} — {len(reds)} RED, {len(unk)} UNKNOWN")
        print("-" * 60)
        for x in v:
            print(" ", x)
        if not v:
            print("  ✅ all invariants hold — app matches reality")
    sys.exit(1 if any(x.severity == "RED" for x in v) else 0)


if __name__ == "__main__":
    main()
