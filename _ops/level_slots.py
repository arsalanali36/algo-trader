"""
level_slots.py — Key-level → candle-pattern → next-candle-break → credit-spread slots.

User ka discretionary setup, algo se execute:
  • Har underlying (NIFTY / BANKNIFTY / F&O stock / BTC-Delta) ke 4 slot:
      I1, I2 = INDEX-level slots (level = underlying SPOT)
      P1, P2 = PREMIUM-level slots (level = ek option contract ka premium)
  • Slot: key level + zone ± (pt ya %) + price kahan se aa raha hai
      from="below"  → price neeche se level (resistance) ko chhu raha → BEARISH setup
                      → SELL CE (+ BUY CE hedge ≈ hedge_delta) → "Bear Call"
      from="above"  → price upar se level (support) pe aa raha  → BULLISH setup
                      → SELL PE (+ BUY PE hedge)                → "Bull Put"
  • State machine (sirf CLOSED candles pe, TF user ka):
      armed → (closed candle zone ko overlap kare) in_zone
            → (wahi candle engulf / hammer / inside-bar bane) pattern
            → (AGLI closed candle pattern-candle ka LOW (bearish) / HIGH (bullish) tode) FIRE
      Agli candle na tode → wapas armed (pattern reset, level dobara watch).
      valid_till ke baad naya entry nahi → expired. Din badla → runtime reset (idle) —
      key levels roz user khud arm karta hai (jaan-boojh ke: purana level chup-chaap
      agle din fire na ho).

Ye module SIRF state + pure decision hai — koi broker / order / Dhan / candle-fetch
import nahi (price_triggers.py / position_exit_rules.py ka idiom). Firing, candles,
spot, exit-rule arm sab caller (trader_dashboard.level_slots_loop / _fire_level_slot)
karta hai. Isliye standalone test hota hai (TEST/test_level_slots.py).

Patterns _CHARTING/patterns.py se (Rule 6B — range_trader ke Pine-validated helpers,
duplicate nahi). Confirmation ka "close vs wick" yahin decide hota hai; exit-side
confirmation Trade Manager (position_exit_rules) ka apna hai — dono alag.

State file: data/level_slots.json
  {"underlyings": {"NIFTY": {...meta}, "RELIANCE": {...}},   # user-added tabs
   "slots": {"<slot_id>": {...config + runtime}},
   "day": "YYYY-MM-DD"}                                       # runtime day-scope

Slot dict (config — user sets):
  id, sym, kind("idx"|"prem"), name,
  contract {opt("CE"|"PE"), strike, sec_id, trad_sym, symbol}   # prem only
  level(float), zone(float), zone_unit("pt"|"pct"), from_dir("below"|"above"),
  patterns(list ⊂ {"engulf","hammer","inside"}), sell_leg("atm"|"level"),
  hedge_delta(float), lots(int), tf("1m"|"3m"|"5m"|"15m"), valid_till("HH:MM" IST),
  mode("paper"), exit{rs_sl, rs_tg, ip_sl, ip_tg, il_sl, il_tg,
                      enabled{rs,ip,il}, confirm_mode, confirm_min}
Runtime (module sets):
  status: idle | armed | in_zone | pattern | firing | entered | failed | expired | cancelled
  armed_ts, armed_day, zone_ts, pattern{ts,o,h,l,c,name,break_level}, fired(bool),
  entry{...}, result(str), events[list of "HH:MM msg"] (last 30)
"""

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FILE = Path(__file__).resolve().parent.parent / "data" / "level_slots.json"
_FILE.parent.mkdir(exist_ok=True)
_LOCK = threading.Lock()

# Rule 6B — pattern helpers from the single charting source
try:
    import sys as _sys
    _ch = str(Path(__file__).resolve().parent.parent / "_CHARTING")
    if _ch not in _sys.path:
        _sys.path.insert(0, _ch)
    import patterns as _pat
except Exception:      # pragma: no cover — patterns missing = no pattern ever detected (safe)
    _pat = None

KINDS = ("idx", "prem")
SLOT_IDS = ("I1", "I2", "P1", "P2")
FROM_DIRS = ("below", "above")
PATTERNS = ("engulf", "hammer", "inside")
TFS = ("1m", "3m", "5m", "15m")
ACTIVE_STATES = ("armed", "in_zone", "pattern")
FIXED_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "BTC")


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _today():
    return _ist_now().strftime("%Y-%m-%d")


def _hm():
    return _ist_now().strftime("%H:%M")


# ─────────────────────────── storage ───────────────────────────
def _read():
    try:
        d = json.loads(_FILE.read_text())
        if isinstance(d, dict):
            d.setdefault("underlyings", {})
            d.setdefault("slots", {})
            d.setdefault("day", _today())
            return d
    except Exception:
        pass
    return {"underlyings": {}, "slots": {}, "day": _today()}


def _write(d):
    tmp = _FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(d))
        tmp.replace(_FILE)          # atomic — half-written file kabhi nahi (autonomy step 6)
    except Exception:
        try:
            _FILE.write_text(json.dumps(d))
        except Exception:
            pass


def _roll_day(d):
    """New IST day → every slot's RUNTIME resets to idle (config stays). Returns
    True if anything changed. Entered/failed rows keep yesterday's info visible
    until the user re-arms (status stays but `fired` clears so a re-arm works)."""
    today = _today()
    if d.get("day") == today:
        return False
    for s in d["slots"].values():
        if s.get("status") in ACTIVE_STATES or s.get("status") == "firing":
            s["status"] = "idle"
            _ev(s, "naya din — slot reset (dobara arm karo)")
        s["fired"] = False
        s.pop("pattern", None)
        s.pop("zone_ts", None)
    d["day"] = today
    return True


def _ev(s, msg):
    ev = list(s.get("events") or [])      # copy — advance() works on a shallow copy
    ev.append(f"{_hm()} {msg}")
    s["events"] = ev[-30:]
    s["last_msg"] = msg


# ─────────────────────────── underlyings ───────────────────────────
def list_underlyings():
    with _LOCK:
        d = _read()
        return dict(d["underlyings"])


def add_underlying(sym, meta=None):
    sym = str(sym).upper().strip()
    if not sym:
        return False, "symbol khaali"
    with _LOCK:
        d = _read()
        if sym in d["underlyings"]:
            return True, d["underlyings"][sym]
        row = {"sym": sym, "added_ts": int(time.time())}
        if isinstance(meta, dict):
            row.update(meta)
        d["underlyings"][sym] = row
        # seed 4 empty slots (idle) so the UI always has I1/I2/P1/P2
        for sid in SLOT_IDS:
            key = f"{sym}:{sid}"
            if key not in d["slots"]:
                d["slots"][key] = _blank_slot(sym, sid)
        _write(d)
        return True, row


def remove_underlying(sym):
    sym = str(sym).upper().strip()
    if sym in FIXED_UNDERLYINGS:
        return False, f"{sym} fixed hai — hataya nahi ja sakta"
    with _LOCK:
        d = _read()
        live = [k for k, s in d["slots"].items()
                if s.get("sym") == sym and s.get("status") in ACTIVE_STATES + ("firing",)]
        if live:
            return False, f"{sym}: {len(live)} slot armed/active hai — pehle disarm karo"
        d["underlyings"].pop(sym, None)
        for k in [k for k, s in d["slots"].items() if s.get("sym") == sym]:
            d["slots"].pop(k, None)
        _write(d)
        return True, "removed"


def _blank_slot(sym, sid):
    kind = "idx" if sid.startswith("I") else "prem"
    return {
        "id": f"{sym}:{sid}", "sym": sym, "slot": sid, "kind": kind,
        "level": None, "zone": None, "zone_unit": "pt", "from_dir": "below",
        "patterns": list(PATTERNS), "sell_leg": "atm", "hedge_delta": 0.25,
        "lots": 1, "tf": "5m", "valid_till": "14:30", "mode": "paper",
        "entry_confirm": "close", "contract": None,
        "exit": {"rs_sl": None, "rs_tg": None, "ip_sl": None, "ip_tg": None,
                 "il_sl": None, "il_tg": None,
                 "enabled": {"rs": kind == "prem", "ip": kind == "idx", "il": False},
                 "confirm_mode": "close", "confirm_min": 2},
        "status": "idle", "fired": False, "events": [], "last_msg": "",
    }


# ─────────────────────────── slots CRUD ───────────────────────────
def list_slots(sym=None):
    with _LOCK:
        d = _read()
        if _roll_day(d):
            _write(d)
        rows = [dict(s) for s in d["slots"].values()
                if sym is None or s.get("sym") == str(sym).upper()]
        rows.sort(key=lambda s: (s.get("sym", ""), SLOT_IDS.index(s["slot"]) if s.get("slot") in SLOT_IDS else 9))
        return rows


def get_slot(slot_id):
    with _LOCK:
        d = _read()
        s = d["slots"].get(slot_id)
        return dict(s) if s else None


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def validate_config(cfg, existing=None):
    """Pure validation of a slot's user config → (ok, cleaned_or_err).
    Never guesses a level; every money-relevant field must be explicit."""
    base = dict(existing or {})
    kind = base.get("kind") or cfg.get("kind")
    if kind not in KINDS:
        return False, "kind galat (idx/prem)"
    out = {}
    lvl = _num(cfg.get("level"))
    if lvl is None or lvl <= 0:
        return False, "Key level > 0 do"
    out["level"] = lvl
    zone = _num(cfg.get("zone"), 0.0)
    if zone < 0:
        return False, "Zone ± negative nahi ho sakta"
    out["zone"] = zone
    zu = str(cfg.get("zone_unit") or base.get("zone_unit") or "pt")
    if zu not in ("pt", "pct"):
        return False, "zone_unit pt/pct"
    out["zone_unit"] = zu
    fd = str(cfg.get("from_dir") or base.get("from_dir") or "below")
    if fd not in FROM_DIRS:
        return False, "from_dir below/above"
    out["from_dir"] = fd
    pats = cfg.get("patterns")
    if isinstance(pats, list):
        pats = [p for p in pats if p in PATTERNS]
        if not pats:
            return False, "kam se kam ek pattern chuno"
        out["patterns"] = pats
    sl = str(cfg.get("sell_leg") or base.get("sell_leg") or "atm")
    if sl not in ("atm", "level"):
        return False, "sell_leg atm/level"
    out["sell_leg"] = sl
    hd = _num(cfg.get("hedge_delta"), base.get("hedge_delta", 0.25))
    if not (0.02 <= hd <= 0.6):
        return False, "Hedge delta 0.02–0.60 ke beech do"
    out["hedge_delta"] = hd
    try:
        lots = int(cfg.get("lots", base.get("lots", 1)))
    except (TypeError, ValueError):
        return False, "Lots galat"
    if lots < 1:
        return False, "Lots >= 1"
    out["lots"] = lots
    tf = str(cfg.get("tf") or base.get("tf") or "5m")
    if tf not in TFS:
        return False, "TF 1m/3m/5m/15m"
    out["tf"] = tf
    vt = str(cfg.get("valid_till") or base.get("valid_till") or "14:30")
    try:
        h, m = vt.split(":")
        vt = f"{int(h):02d}:{int(m):02d}"
    except Exception:
        return False, "Valid till HH:MM"
    out["valid_till"] = vt
    ec = str(cfg.get("entry_confirm") or base.get("entry_confirm") or "close")
    if ec not in ("wick", "close"):
        return False, "entry_confirm wick/close"
    out["entry_confirm"] = ec
    out["mode"] = "paper"            # v1 HARD-LOCK (live = explicit code change + user go)
    if kind == "prem":
        c = cfg.get("contract") if isinstance(cfg.get("contract"), dict) else base.get("contract")
        if not c or not c.get("sec_id") or c.get("opt") not in ("CE", "PE"):
            return False, "Premium slot ke liye contract chuno (CE/PE + strike)"
        out["contract"] = {"opt": c["opt"], "strike": _num(c.get("strike")),
                           "sec_id": str(c["sec_id"]), "trad_sym": str(c.get("trad_sym") or ""),
                           "symbol": str(c.get("symbol") or "")}
    ex_in = cfg.get("exit") if isinstance(cfg.get("exit"), dict) else {}
    ex_old = base.get("exit") if isinstance(base.get("exit"), dict) else {}
    ex = dict(ex_old)
    for k in ("rs_sl", "rs_tg", "ip_sl", "ip_tg", "il_sl", "il_tg"):
        if k in ex_in:
            v = _num(ex_in.get(k))
            ex[k] = abs(v) if v is not None else None
    en_in = ex_in.get("enabled") if isinstance(ex_in.get("enabled"), dict) else None
    if en_in is not None:
        ex["enabled"] = {k: bool(en_in.get(k)) for k in ("rs", "ip", "il")}
    ex.setdefault("enabled", {"rs": kind == "prem", "ip": kind == "idx", "il": False})
    cm = str(ex_in.get("confirm_mode") or ex.get("confirm_mode") or "close")
    if cm not in ("wick", "close", "wait"):
        return False, "confirm_mode wick/close/wait"
    ex["confirm_mode"] = cm
    ex["confirm_min"] = _num(ex_in.get("confirm_min"), ex.get("confirm_min", 2)) or 2
    # at least one exit source with a real number — never arm a spread with no exit
    have = False
    for src, keys in (("rs", ("rs_sl", "rs_tg")), ("ip", ("ip_sl", "ip_tg")), ("il", ("il_sl", "il_tg"))):
        if ex["enabled"].get(src) and any(ex.get(k) for k in keys):
            have = True
    if not have:
        return False, "Kam se kam ek exit (₹ / index pt / index price) ON + value do"
    out["exit"] = ex
    return True, out


def save_slot(slot_id, cfg):
    """Upsert config on an existing slot. Armed/active slot ka config badalna =
    disarm + save (level badla to purana watch invalid hai)."""
    with _LOCK:
        d = _read()
        _roll_day(d)
        s = d["slots"].get(slot_id)
        if not s:
            return False, "slot nahi mila"
        ok, res = validate_config(cfg, existing=s)
        if not ok:
            return False, res
        was_active = s.get("status") in ACTIVE_STATES
        s.update(res)
        if was_active:
            s["status"] = "idle"
            s.pop("pattern", None)
            s.pop("zone_ts", None)
            _ev(s, "config badla — disarmed (dobara ARM karo)")
        else:
            _ev(s, "config saved")
        d["slots"][slot_id] = s
        _write(d)
        return True, dict(s)


def arm(slot_id, spot_now=None):
    """Arm a configured slot. Refuses if: no level, already fired today, past
    valid_till, or spot is ALREADY beyond the level on the far side (setup
    invalid — price_triggers' instant-fire reject, same shape)."""
    with _LOCK:
        d = _read()
        _roll_day(d)
        s = d["slots"].get(slot_id)
        if not s:
            return False, "slot nahi mila"
        if s.get("level") is None:
            return False, "pehle level set karo"
        if s.get("fired"):
            return False, "aaj is slot se entry ho chuki — ek slot, ek entry/din"
        if s.get("status") in ACTIVE_STATES:
            return True, dict(s)
        if _hm() >= str(s.get("valid_till") or "23:59"):
            return False, f"valid till {s.get('valid_till')} nikal gaya"
        if spot_now is not None:
            lo, hi = zone_band(s)
            if s["from_dir"] == "below" and float(spot_now) > hi:
                return False, (f"price ({float(spot_now):.1f}) level {s['level']:.1f} ke UPAR hai — "
                               f"'neeche se aaye' setup invalid; direction check karo")
            if s["from_dir"] == "above" and float(spot_now) < lo:
                return False, (f"price ({float(spot_now):.1f}) level {s['level']:.1f} ke NEECHE hai — "
                               f"'upar se aaye' setup invalid; direction check karo")
        s["status"] = "armed"
        s["armed_ts"] = int(time.time())
        s["armed_day"] = _today()
        s["armed_spot"] = float(spot_now) if spot_now is not None else None
        s.pop("pattern", None)
        s.pop("zone_ts", None)
        s["result"] = ""
        _ev(s, f"ARMED — level {s['level']:g} ±{s.get('zone') or 0:g}{s.get('zone_unit')} "
               f"({'neeche se' if s['from_dir'] == 'below' else 'upar se'}), TF {s['tf']}")
        _write(d)
        return True, dict(s)


def disarm(slot_id, reason="disarmed"):
    with _LOCK:
        d = _read()
        s = d["slots"].get(slot_id)
        if not s:
            return False, "slot nahi mila"
        if s.get("status") == "firing":
            return False, "abhi fire ho raha hai — ruk jao"
        if s.get("status") in ACTIVE_STATES:
            s["status"] = "idle"
            s.pop("pattern", None)
            s.pop("zone_ts", None)
            _ev(s, reason)
            _write(d)
        return True, dict(s)


def claim(slot_id):
    """Atomic one-shot: pattern-confirmed slot → firing (persist BEFORE any order).
    False if not in 'pattern' state or already fired."""
    with _LOCK:
        d = _read()
        s = d["slots"].get(slot_id)
        if not s or s.get("fired") or s.get("status") != "pattern":
            return False
        s["fired"] = True
        s["status"] = "firing"
        s["fired_ts"] = int(time.time())
        _ev(s, "BREAK confirmed → firing")
        _write(d)
        return True


def set_result(slot_id, ok, msg, entry=None):
    with _LOCK:
        d = _read()
        s = d["slots"].get(slot_id)
        if not s:
            return False
        s["status"] = "entered" if ok else "failed"
        s["result"] = msg
        if ok and isinstance(entry, dict):
            s["entry"] = entry
        if not ok:
            s["fired"] = False       # failed fire → user may re-arm (no position exists)
        _ev(s, ("ENTERED " if ok else "FAIL ") + msg)
        _write(d)
        return True


def set_status(slot_id, status, msg=""):
    with _LOCK:
        d = _read()
        s = d["slots"].get(slot_id)
        if not s:
            return False
        s["status"] = status
        if msg:
            _ev(s, msg)
        _write(d)
        return True


# ─────────────────────────── pure decision ───────────────────────────
def zone_band(s):
    lvl = float(s["level"])
    z = float(s.get("zone") or 0)
    if str(s.get("zone_unit")) == "pct":
        z = lvl * z / 100.0
    return lvl - z, lvl + z


def is_bearish(s):
    """from below (resistance) → bearish → sell CE, break = pattern LOW."""
    return str(s.get("from_dir")) == "below"


def candle_in_zone(c, lo, hi):
    try:
        return float(c["high"]) >= lo and float(c["low"]) <= hi
    except (KeyError, TypeError, ValueError):
        return False


def detect_pattern(prev, cur, bearish, wanted, min_body):
    """Name of the first matching pattern on `cur` (closed) given `prev`, or None.
    bearish (at resistance): bear engulfing / shooting star (inv red hammer) /
    inside bar.  bullish (at support): bull engulfing / green hammer / inside bar.
    min_body is in price units (caller scales to the instrument)."""
    if _pat is None or not cur:
        return None
    try:
        o, h, l, c = float(cur["open"]), float(cur["high"]), float(cur["low"]), float(cur["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if prev:
        try:
            po, ph, pl, pc = (float(prev["open"]), float(prev["high"]),
                              float(prev["low"]), float(prev["close"]))
        except (KeyError, TypeError, ValueError):
            prev = None
    if "engulf" in wanted and prev:
        if bearish and _pat.bear_engulfing(po, ph, pl, pc, o, h, l, c):
            return "bear_engulf"
        if not bearish and _pat.bull_engulfing(po, ph, pl, pc, o, h, l, c):
            return "bull_engulf"
    if "hammer" in wanted:
        if bearish and _pat.inv_red_hammer(o, h, l, c, min_body=min_body):
            return "shooting_star"
        if not bearish and _pat.green_hammer(o, h, l, c, min_body=min_body):
            return "hammer"
    if "inside" in wanted and prev and _pat.inside_bar(ph, pl, h, l):
        return "inside_bar"
    return None


def break_confirmed(nxt, pattern, bearish, mode="close"):
    """Did the candle AFTER the pattern candle break it?
      bearish → below pattern LOW;  bullish → above pattern HIGH.
      mode close → the closed candle's CLOSE is beyond;  wick → its extreme is."""
    try:
        bl = float(pattern["break_level"])
        if bearish:
            return (float(nxt["close"]) < bl) if mode == "close" else (float(nxt["low"]) < bl)
        return (float(nxt["close"]) > bl) if mode == "close" else (float(nxt["high"]) > bl)
    except (KeyError, TypeError, ValueError):
        return False


def advance(s, bars, now_hm=None, entry_confirm="close"):
    """Pure state step. `bars` = CLOSED candles oldest→newest (dicts with
    time/open/high/low/close). Mutates a COPY and returns (slot, fire:bool,
    changed:bool). Never fires on missing/unknown data (freeze)."""
    s = dict(s)
    now_hm = now_hm or _hm()
    st = s.get("status")
    if st not in ACTIVE_STATES:
        return s, False, False
    if now_hm >= str(s.get("valid_till") or "23:59"):
        s["status"] = "expired"
        s.pop("pattern", None)
        _ev(s, f"valid till {s.get('valid_till')} — koi entry nahi, slot expired")
        return s, False, True
    if not bars:
        return s, False, False
    lo, hi = zone_band(s)
    bearish = is_bearish(s)
    last = bars[-1]
    prev = bars[-2] if len(bars) >= 2 else None
    last_ts = int(last.get("time") or 0)
    if s.get("seen_ts") == last_ts:
        return s, False, False          # this closed candle already evaluated
    s["seen_ts"] = last_ts
    changed = True

    # ── pattern → did the NEXT closed candle break it? ──
    if st == "pattern":
        pat = s.get("pattern") or {}
        if last_ts <= int(pat.get("ts") or 0):
            return s, False, False      # not a newer candle yet
        if break_confirmed(last, pat, bearish, entry_confirm):
            _ev(s, f"agli candle ne {'LOW' if bearish else 'HIGH'} {pat['break_level']:g} toda "
                   f"(close {float(last['close']):g}) → ENTRY")
            return s, True, True
        # next candle didn't break → re-watch the level (pattern reset)
        s["status"] = "armed"
        s.pop("pattern", None)
        _ev(s, f"agli candle ne {pat.get('break_level'):g} nahi toda — pattern reset, level dobara watch")
        st = "armed"

    # ── armed / in_zone: is the just-closed candle in the zone, and a pattern? ──
    inz = candle_in_zone(last, lo, hi)
    if not inz:
        if st == "in_zone":
            s["status"] = "armed"
            _ev(s, "candle zone se bahar — watch jaari")
        return s, False, changed
    if st == "armed":
        s["status"] = "in_zone"
        s["zone_ts"] = last_ts
        _ev(s, f"candle zone {lo:g}–{hi:g} me (H {float(last['high']):g} / L {float(last['low']):g})")
    min_body = max(float(s["level"]) * 0.0001, 0.01)
    name = detect_pattern(prev, last, bearish, s.get("patterns") or PATTERNS, min_body)
    if name:
        bl = float(last["low"]) if bearish else float(last["high"])
        s["status"] = "pattern"
        s["pattern"] = {"ts": last_ts, "open": float(last["open"]), "high": float(last["high"]),
                        "low": float(last["low"]), "close": float(last["close"]),
                        "name": name, "break_level": bl}
        _ev(s, f"PATTERN {name} @ zone — agli candle {'LOW' if bearish else 'HIGH'} {bl:g} tode to entry")
    return s, False, changed


def apply_runtime(slot_id, s_new):
    """Persist the runtime fields produced by advance() (config untouched)."""
    with _LOCK:
        d = _read()
        s = d["slots"].get(slot_id)
        if not s:
            return False
        for k in ("status", "pattern", "zone_ts", "seen_ts", "events", "last_msg"):
            if k in s_new:
                s[k] = s_new[k]
            elif k in ("pattern", "zone_ts"):
                s.pop(k, None)
        _write(d)
        return True


def active_slots():
    """Slots the watch loop must evaluate (day-rolled)."""
    with _LOCK:
        d = _read()
        if _roll_day(d):
            _write(d)
        return [dict(s) for s in d["slots"].values() if s.get("status") in ACTIVE_STATES]


def option_side(s):
    """(opt_type_to_SELL, dir_for_exit_rule): bearish → CE, dir -1; bullish → PE, +1."""
    return ("CE", -1) if is_bearish(s) else ("PE", +1)


def ensure_fixed():
    """Seed the 3 fixed tabs (idempotent) — called at page/loop start."""
    for sym in FIXED_UNDERLYINGS:
        add_underlying(sym, {"fixed": True})
