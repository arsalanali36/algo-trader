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


def set_rule(key, group_id, ids, target_rs, sl_rs, mode, **extra):
    """Store a group's exit rule.

    The 6 positional args are the original ₹-combined-MTM rule and behave exactly
    as before. `extra` carries the optional Trade-Manager fields — every one of
    them defaults to off, so a rule written WITHOUT them is byte-identical to a
    pre-Trade-Manager rule and the monitor treats it identically:

      entry_spot   float  underlying spot AT ARM TIME — frozen, never recomputed
                          (else "25 pt below" silently drifts every cycle)
      dir          +1/-1  which way the position wants the index to go
      idx_pt_tg/sl float  ± index points from entry_spot
      idx_px_tg/sl float  absolute index level
      enabled      dict   {"rs","pp","ip","il"} → bool, per-source toggle
      tf           str    candle timeframe for close-confirmation ("5m")
      confirm_mode str    "wick" | "close" | "wait"
      confirm_min  float  minutes price must stay beyond, for "wait"
    """
    with _LOCK:
        d = _read()
        row = {
            "key": key,
            "group_id": (group_id or ""),
            "ids": [str(i) for i in (ids or [])],
            "target_rs": float(target_rs or 0),
            "sl_rs": float(sl_rs or 0),
            "mode": (mode or "paper"),
            "created_ts": int(time.time()),
        }
        for k in ("entry_spot", "dir", "idx_pt_tg", "idx_pt_sl",
                  "idx_px_tg", "idx_px_sl"):
            if extra.get(k) is not None:
                try:
                    row[k] = float(extra[k])
                except (TypeError, ValueError):
                    pass
        if isinstance(extra.get("enabled"), dict):
            row["enabled"] = {str(a): bool(b) for a, b in extra["enabled"].items()}
        if extra.get("tf"):
            row["tf"] = str(extra["tf"])
        if extra.get("confirm_mode"):
            row["confirm_mode"] = str(extra["confirm_mode"])
        if extra.get("confirm_min") is not None:
            try:
                row["confirm_min"] = float(extra["confirm_min"])
            except (TypeError, ValueError):
                pass
        # carry any live confirmation progress across a re-arm of the same key
        old = d["rules"].get(key) or {}
        if isinstance(old.get("conf"), dict):
            row["conf"] = old["conf"]
        d["rules"][key] = row
        _write(d)
        return dict(row)


def update_conf(key, conf):
    """Persist a rule's confirmation progress (breach timer / candle bucket).

    On disk, NOT in RAM: a monitor restart mid-confirmation must not silently
    restart the confirm window and let a genuinely-breached level sit un-fired
    (PRE-MORTEM shape #3). Missing rule → no-op."""
    with _LOCK:
        d = _read()
        r = d["rules"].get(key)
        if not r:
            return False
        r["conf"] = dict(conf or {})
        _write(d)
        return True


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


def trigger_levels(rule):
    """Pure → every ENABLED index-based trigger as {src, side, level}.

    Two sources, both compared in index-PRICE space so they can be ranked
    against each other and against spot:
      ip  index points  → entry_spot ± N   (needs the FROZEN entry_spot)
      il  index price   → the level as typed
    A missing/blank value disables just that side. No entry_spot → 'ip' can't
    be placed at all, so it is skipped (never guessed from live spot)."""
    out = []
    en = rule.get("enabled") or {}
    dir_ = 1 if float(rule.get("dir") or 1) >= 0 else -1
    es = rule.get("entry_spot")
    if en.get("ip") and es is not None:
        for side, key in (("target", "idx_pt_tg"), ("sl", "idx_pt_sl")):
            v = rule.get(key)
            if v is None or float(v) <= 0:
                continue
            off = float(v) * dir_ * (1 if side == "target" else -1)
            out.append({"src": "ip", "side": side, "level": float(es) + off})
    if en.get("il"):
        for side, key in (("target", "idx_px_tg"), ("sl", "idx_px_sl")):
            v = rule.get(key)
            if v is None or float(v) <= 0:
                continue
            out.append({"src": "il", "side": side, "level": float(v)})
    return out


def is_beyond(price, level, side, dir_=1):
    """Pure → is `price` past `level` on the exit side of it?

    dir +1 (position wants the index UP): target is above, stop is below.
    dir -1 mirrors both. `price=None` (no spot) → False → FREEZE, never fire."""
    if price is None or level is None:
        return False
    try:
        p, l = float(price), float(level)
    except (TypeError, ValueError):
        return False
    up = (side == "target") if dir_ >= 0 else (side == "sl")
    return p >= l if up else p <= l


def advance_confirm(state, beyond_now, beyond_on_close, now_ts,
                    mode="close", confirm_min=2.0):
    """Pure confirmation state machine → (new_state, fire).

      wick   fire the moment price is beyond the level (broker-like)
      close  fire only when a CLOSED candle is beyond it — a wick that comes
             back inside never fires
      wait   candle closed beyond AND price still beyond `confirm_min` minutes
             later — the fake-breakout filter

    `beyond_on_close=None` means "the closed candle isn't known yet" (data not
    in cache, TF boundary not reached). That is NOT treated as 'inside': the
    machine holds and never fires on it — same freeze-on-unknown rule as
    check_exit()'s combined_mtm=None.

    Caller owns `state` and its disk persistence (see update_conf)."""
    st = dict(state or {})
    if mode == "wick":
        return st, bool(beyond_now)

    if not beyond_now:                 # price came back inside → reset progress
        st.pop("since", None)
        return st, False

    if beyond_on_close is None:        # unknown → hold, never fire
        return st, False
    if not beyond_on_close:            # touched but closed back inside
        st.pop("since", None)
        return st, False

    if mode == "close":
        return st, True

    # mode == "wait": candle closed beyond → start/continue the timer
    if "since" not in st:
        st["since"] = float(now_ts)
        return st, False
    waited = float(now_ts) - float(st["since"])
    return st, waited >= float(confirm_min) * 60.0


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

    # ── INERT-SHIP PROOF: a rule stored the old way must be byte-identical ──
    set_rule("g:old", "old", ["1"], 5000, -4000, "paper")
    old_row = get_rule("g:old")
    assert set(old_row) == {"key", "group_id", "ids", "target_rs", "sl_rs",
                            "mode", "created_ts"}, old_row
    assert trigger_levels(old_row) == []          # no index triggers at all
    clear_rule("g:old")

    # ── index triggers (dir +1: target above, stop below) ──
    set_rule("g:tm", "tm", ["1"], 4000, -2000, "paper",
             entry_spot=25412, dir=1, idx_pt_tg=50, idx_pt_sl=25,
             idx_px_tg=25500, idx_px_sl=25200,
             enabled={"rs": True, "ip": True, "il": True},
             tf="5m", confirm_mode="wait", confirm_min=2)
    r = get_rule("g:tm")
    lv = {(x["src"], x["side"]): x["level"] for x in trigger_levels(r)}
    assert lv[("ip", "target")] == 25462 and lv[("ip", "sl")] == 25387, lv
    assert lv[("il", "target")] == 25500 and lv[("il", "sl")] == 25200, lv

    # entry_spot missing → index-points trigger is SKIPPED, never guessed
    r2 = dict(r); r2.pop("entry_spot")
    assert [x["src"] for x in trigger_levels(r2)] == ["il", "il"]

    # a source toggled off disappears entirely
    r3 = dict(r); r3["enabled"] = {"ip": True, "il": False}
    assert {x["src"] for x in trigger_levels(r3)} == {"ip"}

    # ── is_beyond, both directions + freeze ──
    assert is_beyond(25470, 25462, "target", 1) is True
    assert is_beyond(25450, 25462, "target", 1) is False
    assert is_beyond(25380, 25387, "sl", 1) is True
    assert is_beyond(25390, 25387, "sl", 1) is False
    assert is_beyond(25380, 25387, "target", -1) is True     # dir -1 mirrors
    assert is_beyond(None, 25387, "sl", 1) is False          # no spot → freeze
    assert is_beyond(25380, None, "sl", 1) is False

    # ── confirmation state machine ──
    # wick: fires on touch, ignores candles entirely
    assert advance_confirm({}, True, None, 0, "wick")[1] is True
    assert advance_confirm({}, False, True, 0, "wick")[1] is False
    # close: wick-only touch must NOT fire; closed-beyond must
    assert advance_confirm({}, True, False, 0, "close")[1] is False
    assert advance_confirm({}, True, True, 0, "close")[1] is True
    # close: candle unknown → HOLD (never fire on unknown)
    assert advance_confirm({}, True, None, 0, "close")[1] is False
    # wait: closed beyond starts the timer, fires only after confirm_min
    s, f = advance_confirm({}, True, True, 1000, "wait", 2)
    assert f is False and s["since"] == 1000
    assert advance_confirm(s, True, True, 1000 + 119, "wait", 2)[1] is False
    assert advance_confirm(s, True, True, 1000 + 120, "wait", 2)[1] is True
    # wait: price comes back inside → timer RESETS (fake breakout survived)
    s2, f2 = advance_confirm(s, False, True, 1000 + 60, "wait", 2)
    assert f2 is False and "since" not in s2
    # wait: restart mid-confirm — state came off disk, timer keeps running
    s3, f3 = advance_confirm({"since": 1000}, True, True, 1000 + 130, "wait", 2)
    assert f3 is True
    clear_rule("g:tm")
    print("position_exit_rules ok — all pure/store tests pass")
