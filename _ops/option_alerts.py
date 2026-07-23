"""
option_alerts.py — real-time "unusual option behaviour" watcher.

Reads the per-minute option-chain series (via option_curves — the SAME data the
/curves page shows) and, during market hours, fires a notification (bell + toast +
sound, via _core/notify) when something worth a glance happens:

  ⚡ Gamma spike      — ATM straddle gamma jumps unusually vs its recent trend
                        (z-score) → spot is pinning a strike / expiry gamma → big
                        whippy moves likely.
  📉 Straddle crush   — ATM straddle premium collapsing fast (theta/IV crush) →
                        good for a seller, painful for a buyer.
  📈 Straddle pop     — ATM straddle premium expanding fast → a vol event / move.
  🔄 Call/Put unwind  — CE (or PE) open-interest dropping fast → writers/holders
                        exiting that side.

READ-ONLY: no order/live path, ever. It only reads the collector CSV and calls
notify. Thresholds live in nifty_config.json["_option_alerts"] (hot-reloaded);
sensible defaults below. Per-alert cooldown keeps it from nagging while a
condition persists.

Run standalone to see what WOULD fire today (no notifications sent):
    python -X utf8 _ops/option_alerts.py --dry NIFTY
"""
import os
import sys
import time
import json
from datetime import datetime, timezone, timedelta

# path bootstrap so standalone runs can import project modules
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _paths  # noqa: F401  (sets up the full folder path map when available)
except Exception:
    pass

import option_curves as oc

IST = timezone(timedelta(hours=5, minutes=30))
CONFIG_FILE = os.path.join(_ROOT, "nifty_config.json")

DEFAULTS = {
    "enabled": True,
    "underlyings": ["NIFTY", "BANKNIFTY"],
    "gamma_spike_z": 2.2,      # ATM gamma z-score over the trailing window
    "gamma_win": 20,           # trailing points (~minutes) for the z-score baseline
    "straddle_win": 10,        # lookback minutes for straddle move
    "straddle_pct": 3.0,       # min % move
    "straddle_pts": 12.0,      # AND min absolute points (ignore tiny %)
    "oi_win": 10,              # lookback minutes for OI unwind
    "oi_unwind_pct": 8.0,      # min % OI drop on a side
    "iv_win": 10,              # lookback minutes for ATM-IV move
    "iv_move_pct": 6.0,        # min % ATM-IV move (crush/pop)
    "decay_edge_pct": 5.0,     # actual-straddle vs theoretical gap % (decay edge/lag)
    "delta_drift": 0.25,       # |net straddle delta| threshold → directional
    "wall_shift_win": 15,      # lookback minutes for max-OI wall shift
    "ivrank_high": 70.0,       # IV Rank ≥ → premium-selling attractive
    "ivrank_low": 20.0,        # IV Rank ≤ → seller avoid (no edge)
    "vrp_min": 0.0,            # atm_iv − realized_vol ≤ this → VRP edge gone
    "term_gap": 1.5,           # near-IV − next-IV ≥ → backwardation (event priced)
    "skew_thr": 3.0,           # put_skew (OTM put−call IV) level for a steepening alert
    "skew_jump": 1.5,          # put_skew rise over skew_win to fire
    "skew_win": 15,            # lookback minutes for skew steepening
    "oi_bomb": 500000,         # single-strike 1-min OI add/unwind (units) → big-bet alert
    "cooldown_min": 12,        # per-alert-type cooldown
}

_last_fired = {}  # key -> epoch seconds (in-process cooldown)


def _cfg():
    c = dict(DEFAULTS)
    try:
        raw = json.loads(open(CONFIG_FILE, encoding="utf-8").read())
        c.update(raw.get("_option_alerts") or {})
    except Exception:
        pass
    return c


def _ist_now():
    return datetime.now(timezone.utc).astimezone(IST)


def _today():
    return _ist_now().strftime("%Y-%m-%d")


def _at_or_before(points, secs_ago):
    """The most recent point at least `secs_ago` seconds before the last point."""
    if not points:
        return None
    target = points[-1]["t"] - secs_ago
    chosen = None
    for p in points:
        if p["t"] <= target:
            chosen = p
        else:
            break
    return chosen


def _mean_std(xs):
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n < 5:
        return None, None, 0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / n
    return m, var ** 0.5, n


def evaluate(underlying, date=None, cfg=None, points=None):
    """Pure: returns a list of {key, level, msg} for the current state. No side effects.
    `points` (a curves() series) can be passed to evaluate an arbitrary window — used
    for replay/calibration; otherwise today's live series is read."""
    cfg = cfg or _cfg()
    date = date or _today()
    if points is None:
        d = oc.curves(underlying, date)
        pts = d.get("points") or []
    else:
        pts = points
    if len(pts) < 6:
        return []
    cur = pts[-1]
    out = []
    U = underlying

    # ── ⚡ Gamma spike (z-score vs trailing window) ─────────────────────────
    win = pts[-int(cfg["gamma_win"]):]
    gammas = [p["gamma"] for p in win[:-1] if p.get("gamma") is not None]
    m, sd, n = _mean_std(gammas)
    if m is not None and sd and cur.get("gamma") is not None:
        z = (cur["gamma"] - m) / sd
        if z >= cfg["gamma_spike_z"] and cur["gamma"] > m:
            out.append({
                "key": f"opt_gamma_spike_{U}",
                "level": "warn",
                "msg": f"⚡ {U} ATM gamma spike — {cur['gamma']:.5f} (z={z:.1f} vs recent) @ {cur['atm']:.0f}. "
                       f"Spot strike pe pin ho raha / expiry gamma — tez whippy move sambhav.",
            })

    # ── 📉/📈 Straddle crush / pop (fast premium move) ──────────────────────
    past = _at_or_before(pts, int(cfg["straddle_win"]) * 60)
    if past and past.get("straddle") and cur.get("straddle"):
        chg = cur["straddle"] - past["straddle"]
        pct = chg / past["straddle"] * 100.0
        if -chg >= cfg["straddle_pts"] and -pct >= cfg["straddle_pct"]:
            out.append({
                "key": f"opt_straddle_crush_{U}",
                "level": "warn",
                "msg": f"📉 {U} ATM straddle tez tut raha — {past['straddle']:.0f}→{cur['straddle']:.0f} "
                       f"({pct:+.1f}%) {int(cfg['straddle_win'])}m me. Theta/IV crush — seller-friendly.",
            })
        elif chg >= cfg["straddle_pts"] and pct >= cfg["straddle_pct"]:
            out.append({
                "key": f"opt_straddle_pop_{U}",
                "level": "warn",
                "msg": f"📈 {U} ATM straddle tez chadh raha — {past['straddle']:.0f}→{cur['straddle']:.0f} "
                       f"({pct:+.1f}%) {int(cfg['straddle_win'])}m me. Vol/event — move aa sakta hai.",
            })

    # ── 🔄 Call / Put OI unwinding (fast) ───────────────────────────────────
    oipast = _at_or_before(pts, int(cfg["oi_win"]) * 60)
    if oipast:
        for side, field, lbl in (("Call", "ce_oi", "CE"), ("Put", "pe_oi", "PE")):
            a, b = oipast.get(field), cur.get(field)
            if a and b and a > 0:
                drop_pct = (a - b) / a * 100.0
                if drop_pct >= cfg["oi_unwind_pct"]:
                    out.append({
                        "key": f"opt_{lbl.lower()}_unwind_{U}",
                        "level": "warn",
                        "msg": f"🔄 {U} {side} OI tez unwind ho rahi — {lbl} OI −{drop_pct:.1f}% "
                               f"{int(cfg['oi_win'])}m me. {side} writers/holders nikal rahe.",
                    })

    # ── 📉/📈 ATM IV crush / pop (fast IV move; chart overlays it vs VIX) ────
    ivpast = _at_or_before(pts, int(cfg["iv_win"]) * 60)
    if ivpast and ivpast.get("atm_iv") and cur.get("atm_iv"):
        a0, a1 = ivpast["atm_iv"], cur["atm_iv"]
        ivpct = (a1 - a0) / a0 * 100.0
        vixs = f" VIX {cur['vix']:.1f}." if cur.get("vix") is not None else ""
        if -ivpct >= cfg["iv_move_pct"]:
            out.append({
                "key": f"opt_iv_crush_{U}",
                "level": "warn",
                "msg": f"📉 {U} ATM IV crush — {a0:.1f}→{a1:.1f}% ({ivpct:+.1f}%) {int(cfg['iv_win'])}m me.{vixs} "
                       f"Premium tez bleed — seller-friendly.",
            })
        elif ivpct >= cfg["iv_move_pct"]:
            out.append({
                "key": f"opt_iv_pop_{U}",
                "level": "warn",
                "msg": f"📈 {U} ATM IV chadh rahi — {a0:.1f}→{a1:.1f}% ({ivpct:+.1f}%) {int(cfg['iv_win'])}m me.{vixs} "
                       f"Vol/event sambhav — buyer-friendly.",
            })

    # ── 🎯 Theo-vs-actual decay edge (actual straddle vs BS pure-theta line) ──
    if cur.get("straddle") and cur.get("straddle_theo"):
        gap = cur["straddle_theo"] - cur["straddle"]
        gpct = gap / cur["straddle_theo"] * 100.0
        if gpct >= cfg["decay_edge_pct"]:
            out.append({
                "key": f"opt_decay_edge_{U}",
                "level": "warn",
                "msg": f"🎯 {U} decay edge — actual straddle {cur['straddle']:.0f} theoretical {cur['straddle_theo']:.0f} "
                       f"se {gpct:.1f}% neeche. Decay schedule se aage = seller edge confirm.",
            })
        elif -gpct >= cfg["decay_edge_pct"]:
            out.append({
                "key": f"opt_decay_lag_{U}",
                "level": "warn",
                "msg": f"⚠️ {U} decay lag — actual straddle {cur['straddle']:.0f} theoretical {cur['straddle_theo']:.0f} "
                       f"se {-gpct:.1f}% upar. IV expand / decay peeche — seller ke liye caution.",
            })

    # ── 🧭 Net straddle delta drift (spot ATM se door → directional) ─────────
    nd = cur.get("net_delta")
    if nd is not None and abs(nd) >= cfg["delta_drift"]:
        dirn = "CE-heavy (spot upar)" if nd > 0 else "PE-heavy (spot neeche)"
        out.append({
            "key": f"opt_delta_drift_{U}",
            "level": "warn",
            "msg": f"🧭 {U} straddle directional ho gaya — net delta {nd:+.2f} ({dirn}) @ ATM {cur['atm']:.0f}. "
                   f"Neutral nahi raha — hedge / roll / adjust.",
        })

    # ── 🧱 OI wall shift (call/put max-OI strike moved) ─────────────────────
    wpast = _at_or_before(pts, int(cfg["wall_shift_win"]) * 60)
    if wpast:
        for side, field, key in (("Call", "call_wall", "call_wall_shift"),
                                  ("Put", "put_wall", "put_wall_shift")):
            a0, a1 = wpast.get(field), cur.get(field)
            if a0 and a1 and a0 != a1:
                arr = "↑" if a1 > a0 else "↓"
                out.append({
                    "key": f"opt_{key}_{U}",
                    "level": "warn",
                    "msg": f"🧱 {U} {side} wall shift {arr} — max-OI {side.lower()} strike {a0:.0f}→{a1:.0f} "
                           f"{int(cfg['wall_shift_win'])}m me. Resistance/support level khisak raha.",
                })

    # ── 🔥/🧊 IV Rank extremes (premium-selling context) ────────────────────
    ivr = cur.get("iv_rank")
    if ivr is not None:
        if ivr >= cfg["ivrank_high"]:
            out.append({"key": f"opt_ivrank_high_{U}", "level": "warn",
                        "msg": f"🔥 {U} IV Rank {ivr:.0f} — IV pichhle dino ke top pe. Premium selling attractive (VRP setup)."})
        elif ivr <= cfg["ivrank_low"]:
            out.append({"key": f"opt_ivrank_low_{U}", "level": "warn",
                        "msg": f"🧊 {U} IV Rank {ivr:.0f} — IV range ke neeche. Seller edge weak — naya short avoid."})

    # ── ⚠️ VRP edge flip (implied ≤ realized = seller cushion gaya) ──────────
    _iv0, _rv = cur.get("atm_iv"), cur.get("realized_vol")
    if _iv0 is not None and _rv is not None and (_iv0 - _rv) <= cfg["vrp_min"]:
        out.append({"key": f"opt_vrp_gone_{U}", "level": "warn",
                    "msg": f"⚠️ {U} VRP edge gaya — IV {_iv0:.1f} ≤ realized {_rv:.1f} ({_iv0 - _rv:+.1f}). "
                           f"Seller ka cushion khatam — short risky."})

    # ── 📐 Term backwardation (near-week IV >> next-week = event priced) ─────
    _nr, _nx = cur.get("iv_near"), cur.get("iv_next")
    if _nr is not None and _nx is not None and (_nr - _nx) >= cfg["term_gap"]:
        out.append({"key": f"opt_term_back_{U}", "level": "warn",
                    "msg": f"📐 {U} term backwardation — near {_nr:.1f} > next {_nx:.1f} ({_nr - _nx:+.1f} vol). "
                           f"Near-term event/uncertainty priced — crush setup."})

    # ── 🩹 Put-skew steepening (OTM puts IV climbing = hidden hedging bid) ───
    _ps = cur.get("put_skew")
    _pspast = _at_or_before(pts, int(cfg["skew_win"]) * 60)
    if _ps is not None and _ps >= cfg["skew_thr"]:
        _prev = _pspast.get("put_skew") if _pspast else None
        _jump = _ps - _prev if _prev is not None else _ps
        if _jump >= cfg["skew_jump"] or _prev is None:
            out.append({"key": f"opt_skew_steepen_{U}", "level": "warn",
                        "msg": f"🩹 {U} put-skew steep — OTM puts IV calls se {_ps:.1f} vol upar (+{_jump:.1f} {int(cfg['skew_win'])}m). "
                               f"Chhupi hedging / crash-protection buying — aksar spot girne se pehle."})

    # ── 💣 OI bomb (big single-strike 1-min OI add / unwind) ─────────────────
    _add, _cut = cur.get("oi_add_max") or 0, cur.get("oi_cut_max") or 0
    if _add >= cfg["oi_bomb"] and _add >= _cut:
        out.append({"key": f"opt_oi_bomb_{U}", "level": "warn",
                    "msg": f"💣 {U} bada OI ADD — {cur.get('oi_add_strike'):.0f} strike pe +{_add:,.0f} OI 1-min me. "
                           f"Institutional bet/hedge ban raha."})
    elif _cut >= cfg["oi_bomb"]:
        out.append({"key": f"opt_oi_unwind_{U}", "level": "warn",
                    "msg": f"💣 {U} bada OI UNWIND — {cur.get('oi_cut_strike'):.0f} strike pe −{_cut:,.0f} OI 1-min me. "
                           f"Badi position kat rahi."})

    # tag each alert with which chart panel + the triggering point's time, so the
    # /curves page can drop a hover-able marker on the right curve at that moment.
    _PANEL = {"gamma_spike": ("gamma", "⚡"), "straddle_crush": ("straddle", "📉"),
              "straddle_pop": ("straddle", "📈"), "ce_unwind": ("oi", "🔄"), "pe_unwind": ("oi", "🔄"),
              "iv_crush": ("iv", "📉"), "iv_pop": ("iv", "📈"),
              "decay_edge": ("decay", "🎯"), "decay_lag": ("decay", "⚠️"),
              "delta_drift": ("delta", "🧭"),
              "call_wall_shift": ("walls", "🧱"), "put_wall_shift": ("walls", "🧱"),
              "ivrank_high": ("ivrank", "🔥"), "ivrank_low": ("ivrank", "🧊"),
              "vrp_gone": ("rvivsp", "⚠️"), "term_back": ("term", "📐"),
              "skew_steepen": ("skew", "🩹"), "oi_bomb": ("heatmap", "💣"), "oi_unwind": ("heatmap", "💣")}
    for a in out:
        typ = a["key"][4:].rsplit("_", 1)[0] if a["key"].startswith("opt_") else a["key"]
        panel, tag = _PANEL.get(typ, ("straddle", "•"))
        a.update({"u": U, "t": cur["t"], "panel": panel, "tag": tag})
    return out


def _log_dir():
    d = os.path.join(_ROOT, "data", "option_alerts")
    os.makedirs(d, exist_ok=True)
    return d


def _log_alert(a):
    """Append a fired alert to data/option_alerts/<U>_<date>.jsonl (chart markers)."""
    try:
        path = os.path.join(_log_dir(), f"{a.get('u', 'NIFTY')}_{_today()}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": a.get("t"), "panel": a.get("panel"), "tag": a.get("tag"),
                                "key": a.get("key"), "msg": a.get("msg")}) + "\n")
    except Exception as e:
        print(f"[option-alerts] log write fail: {e}", flush=True)


def read_log(u, date):
    """Fired alerts for a day (for the /curves markers). Display-only."""
    path = os.path.join(_ROOT, "data", "option_alerts", f"{u}_{date}.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except Exception:
        pass
    return out


def replay_day(underlying, date, cfg=None):
    """Regenerate the fired-alert log for a STORED day by replaying evaluate() minute
    by minute (same per-key cooldown as the live loop, but measured in market-time).
    The live watch_loop only fires during market hours, so this is how you get markers
    onto a past / already-closed day's /curves chart. Overwrites the day's log (backs up
    the existing one to .bak first). Display-only. Returns the number of markers written."""
    cfg = cfg or _cfg()
    d = oc.curves(underlying, date)
    pts = d.get("points") or []
    if len(pts) < 6:
        return 0
    fired, last = [], {}
    cd = cfg["cooldown_min"] * 60
    for i in range(6, len(pts) + 1):
        for a in evaluate(underlying, date=date, cfg=cfg, points=pts[:i]):
            k, t = a["key"], a["t"]
            if t - last.get(k, -1e18) >= cd:   # cooldown in market-time
                last[k] = t
                fired.append(a)
    path = os.path.join(_log_dir(), f"{underlying}_{date}.jsonl")
    if os.path.exists(path):
        os.replace(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as f:
        for a in fired:
            f.write(json.dumps({"t": a.get("t"), "panel": a.get("panel"), "tag": a.get("tag"),
                                "key": a.get("key"), "msg": a.get("msg")}) + "\n")
    return len(fired)


def _fire(alert, cfg):
    """notify with per-key cooldown."""
    import notify
    now = time.time()
    last = _last_fired.get(alert["key"], 0)
    if now - last < cfg["cooldown_min"] * 60:
        return False
    _last_fired[alert["key"]] = now
    notify.push(alert["msg"], level=alert.get("level", "warn"), key=alert["key"], source="chain")
    _log_alert(alert)   # chart marker record
    return True


def _market_open(cfg):
    try:
        import market_calendar as mc
        now = _ist_now()
        if not mc.is_trading_day(now.date()):
            return False
    except Exception:
        now = _ist_now()
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 30)


def watch_loop(interval=60, on_fire=None):
    """Daemon loop — evaluate every `interval`s during market hours, fire alerts.
    `on_fire(alert)` (optional): called for each alert that actually fires (after
    cooldown) — the auto-straddle feature (C) hooks in here to place an order.
    This module stays pure/read-only; the order-firing decision lives entirely in
    the caller's callback."""
    print("[option-alerts] watch loop started", flush=True)
    while True:
        try:
            cfg = _cfg()
            if cfg.get("enabled") and _market_open(cfg):
                for u in cfg.get("underlyings", ["NIFTY"]):
                    for a in evaluate(u, cfg=cfg):
                        if _fire(a, cfg):
                            print(f"[option-alerts] {a['msg']}", flush=True)
                            if on_fire:
                                try:
                                    on_fire(a)
                                except Exception as e:
                                    print(f"[option-alerts] on_fire err: {e}", flush=True)
        except Exception as e:
            print(f"[option-alerts] loop error: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry" in args
    ul = [a for a in args if not a.startswith("-")] or ["NIFTY"]
    # --replay [DATE]: rebuild a stored day's marker log (for showing markers on a
    # closed day). e.g.  python _ops/option_alerts.py --replay 2026-07-23 NIFTY BANKNIFTY
    if "--replay" in args:
        rd = next((a for a in args if a[:1].isdigit() and "-" in a), None) or _today()
        ul = [a for a in args if not a.startswith("-") and not (a[:1].isdigit() and "-" in a)] or ["NIFTY"]
        for u in ul:
            n = replay_day(u, rd)
            print(f"[replay] {u} {rd} -> {n} markers written")
        sys.exit(0)
    for u in ul:
        res = evaluate(u)
        print(f"=== {u} ({_today()}) — {len(res)} alert(s) now ===")
        for a in res:
            print("  " + a["msg"])
        if not res:
            print("  (nothing unusual right now)")
