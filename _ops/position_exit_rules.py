"""
position_exit_rules.py — per-GROUP combined-MTM auto-exit rule store (#02).

A rule says: when a position GROUP's live COMBINED mark-to-market (₹, summed
across all its legs — exactly what the payoff panel's combined-premium chart
shows) crosses `target_rs` (>= profit) or `sl_rs` (<= loss), the WHOLE group
squares off together.

PURE state + decision only (no broker / order / Dhan import) — standalone
self-testable, mirrors auto_straddle.py / price_triggers.py. The monitor that
computes live MTM and the square-off (via execution_gateway.execute_exit,
respecting each leg's OWN mode — paper→paper, live→REAL) live in
trader_dashboard._run_position_exit_rules; this module only stores/loads/clears.

Keyed by group identity: `g:<group_id>` when the legs share a group_id, else
`i:<sorted leg ids>`. Rules persist on disk (restart-safe) and auto-clear the
moment the group is flat (the monitor removes a rule whose legs are all gone).

State file: data/position_exit_rules.json -> {"rules": {key: rule, ...}}
Rule: {key, group_id, ids:[...], target_rs, sl_rs, mode, created_ts}
"""

import json
import threading
import time
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "data" / "position_exit_rules.json"
_FILE.parent.mkdir(exist_ok=True)
_LOCK = threading.Lock()


def _read():
    try:
        d = json.loads(_FILE.read_text())
        if isinstance(d, dict) and isinstance(d.get("rules"), dict):
            return d
    except Exception:
        pass
    return {"rules": {}}


def _write(d):
    try:
        _FILE.write_text(json.dumps(d))
    except Exception:
        pass


def rule_key(group_id, ids):
    """Canonical identity for a group — prefer the durable group_id link, else
    the sorted leg-id set (so the same group resolves to the same key whether
    it was armed by ids= or group_id=)."""
    gid = (group_id or "").strip()
    if gid:
        return "g:" + gid
    return "i:" + ",".join(sorted(str(i) for i in (ids or [])))


def set_rule(key, group_id, ids, target_rs, sl_rs, mode):
    with _LOCK:
        d = _read()
        d["rules"][key] = {
            "key": key,
            "group_id": (group_id or ""),
            "ids": [str(i) for i in (ids or [])],
            "target_rs": float(target_rs or 0),
            "sl_rs": float(sl_rs or 0),
            "mode": (mode or "paper"),
            "created_ts": int(time.time()),
        }
        _write(d)
        return dict(d["rules"][key])


def clear_rule(key):
    with _LOCK:
        d = _read()
        if key in d["rules"]:
            d["rules"].pop(key, None)
            _write(d)
            return True
        return False


def list_rules():
    with _LOCK:
        return [dict(r) for r in _read().get("rules", {}).values()]


def get_rule(key):
    with _LOCK:
        r = _read().get("rules", {}).get(key)
        return dict(r) if r else None


def check_exit(combined_mtm, target_rs, sl_rs):
    """Pure exit decision on a group's live combined MTM (₹, whole position).
    Returns 'target' | 'sl' | None. `combined_mtm=None` (incomplete/stale leg
    data) → None → FREEZE (never fire on bad data — TRAP #1 shape).
    target_rs > 0 arms the profit side; sl_rs < 0 arms the loss side; a 0 on
    either side disables just that side."""
    if combined_mtm is None:
        return None
    try:
        m = float(combined_mtm)
        t = float(target_rs or 0)
        s = float(sl_rs or 0)
    except (TypeError, ValueError):
        return None
    if t > 0 and m >= t:
        return "target"
    if s < 0 and m <= s:
        return "sl"
    return None


if __name__ == "__main__":
    import tempfile
    _FILE = Path(tempfile.mkdtemp()) / "position_exit_rules.json"
    # pure decision
    assert check_exit(6000, 5000, -4000) == "target"
    assert check_exit(-4500, 5000, -4000) == "sl"
    assert check_exit(1000, 5000, -4000) is None
    assert check_exit(None, 5000, -4000) is None          # bad data → freeze
    assert check_exit(-4000, 5000, -4000) == "sl"          # boundary
    assert check_exit(9999, 0, -4000) is None              # target disabled (0)
    assert check_exit(9999, 5000, 0) == "target"           # sl disabled (0)
    # store
    k = rule_key("STRAD_x", ["3", "1", "2"])
    assert k == "g:STRAD_x"
    assert rule_key("", ["3", "1", "2"]) == "i:1,2,3"
    set_rule(k, "STRAD_x", ["1", "2"], 5000, -4000, "paper")
    assert get_rule(k)["target_rs"] == 5000
    assert len(list_rules()) == 1
    assert clear_rule(k) is True
    assert clear_rule(k) is False
    assert get_rule(k) is None
    print("position_exit_rules ok — all pure/store tests pass")
