"""
position_carry.py — per-position "carry overnight" (MIS → NRML) flag.

Zerodha: MIS = intraday (3:15 pe auto square), NRML = carry-forward. Hamare yahan
iska equivalent: ek day-scoped flag jo `pos_monitor_loop` ke 3:15 EOD-squareoff ko
US position ke liye SKIP karwa deta hai. Toggle ON = NRML (carry), OFF = MIS (3:15
square, default).

GROUP-WIDE by design: key = position ka group_id, isliye ek straddle/structure ke
SAARE legs ek hi toggle se carry hote hain (ya sab square) — hedge/pair kabhi
aadha-carry nahi (naked-overnight risk avoid, user decision). Legacy empty-group
leg → key = "id:<id>" (per-leg fallback).

Day-scoped (agle din file khaali = default wapas MIS): carry deliberate hai, bhoolne
pe raat bhar carry nahi rehta. PURE state — koi broker/order import nahi
(pos_monitor READ karta hai, /api/position-carry SET karta hai; standalone-testable).

PAPER ke liye ye flag hi kaafi hai (bas EOD-squareoff skip). LIVE me asli overnight
carry ke liye broker product NRML chahiye (order NRML se place ho, ya Kite
convert_position) — wo abhi wire NAHI hai (sab paper). Jab live jaayein tab.

State file: data/carry_groups.json -> {"day": "YYYY-MM-DD", "keys": [ ... ]}
"""

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "data" / "carry_groups.json"
_FILE.parent.mkdir(exist_ok=True)
_LOCK = threading.Lock()


def _today_ist():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _key(group_id, id_):
    """Carry key: group_id agar hai (group-wide), warna 'id:<id>' (per-leg)."""
    g = str(group_id or "").strip()
    if g:
        return g
    i = str(id_ or "").strip()
    return ("id:" + i) if i else ""


def _load_keys():
    """Today's carried keys (day-reset applied). Stored day != today → stale → []."""
    try:
        d = json.loads(_FILE.read_text())
        if isinstance(d, dict) and d.get("day") == _today_ist():
            k = d.get("keys")
            if isinstance(k, list):
                return [str(x) for x in k]
    except Exception:
        pass
    return []


def _write_keys(keys):
    try:
        _FILE.write_text(json.dumps({"day": _today_ist(), "keys": sorted(set(keys))}))
    except Exception:
        pass


def is_carried(group_id, id_=None):
    """True agar is position (uske group ya id se) carry-overnight flagged hai."""
    k = _key(group_id, id_)
    if not k:
        return False
    with _LOCK:
        return k in _load_keys()


def set_carry(group_id, id_, on):
    """Toggle carry for a position's GROUP (group_id) — ya empty-group leg ke liye
    us id pe. on=True → NRML (carry), False → MIS (3:15 square). Returns (key, on)."""
    k = _key(group_id, id_)
    if not k:
        return None, False
    with _LOCK:
        keys = set(_load_keys())
        if on:
            keys.add(k)
        else:
            keys.discard(k)
        _write_keys(keys)
        return k, bool(on)


def list_keys():
    with _LOCK:
        return _load_keys()


def clear_all():
    with _LOCK:
        _write_keys([])


if __name__ == "__main__":
    # pure self-test — no real file (uses a temp path)
    import os
    import tempfile
    _FILE = Path(tempfile.gettempdir()) / "carry_test.json"
    clear_all()
    assert _key("GRP1", "9") == "GRP1"          # group_id wins
    assert _key("", "9") == "id:9"              # empty group → per-leg
    assert _key("", "") == ""
    assert not is_carried("GRP1", "9")
    set_carry("GRP1", "9", True)                # carry the group
    assert is_carried("GRP1", "9")              # this leg
    assert is_carried("GRP1", "10")             # a SIBLING leg (same group) → also carried
    assert not is_carried("GRP2", "11")
    set_carry("GRP1", "9", False)               # toggle back to MIS
    assert not is_carried("GRP1", "9")
    assert not is_carried("GRP1", "10")
    set_carry("", "77", True)                   # empty-group leg → per-id
    assert is_carried("", "77")
    assert not is_carried("", "78")
    assert list_keys() == ["id:77"]
    try:
        os.remove(_FILE)
    except OSError:
        pass
    print("position_carry self-test — group-wide + per-leg + toggle + day-key all pass")
