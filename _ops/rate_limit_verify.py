"""
rate_limit_verify.py — automated market-open verification of the TRAP #95
DH-904 rate-limit architecture fix.

Runs once each trading morning (systemd timer, ~09:55 IST — after the
09:15-09:35 warmup candle-storm, the heaviest-traffic window of the day) and
judges whether the fix is holding under real multi-strategy load: it reads the
last WINDOW_MIN minutes of dhan_rate_limit_events.json, counts 429s /
timeouts / throttles broken down by priority and context, writes a dated
report to logs/, and surfaces a one-line verdict on the dashboard alert banner
(data/downloader_alert.json) — GREEN when 429s are low, RED when still
elevated (with the top offending context named so it's immediately actionable).

Read-only except for its own report file + the banner. Safe to run any time
with --dry (prints, writes nothing).

  python rate_limit_verify.py           # write report + banner
  python rate_limit_verify.py --dry     # print only
  python rate_limit_verify.py --window 40
"""
import json
import sys
import time
import collections
from pathlib import Path
from datetime import datetime, timedelta, timezone

BASE = Path(__file__).resolve().parent.parent
EVENTS_FILE = BASE / "data" / "dhan_rate_limit_events.json"
BANNER_FILE = BASE / "data" / "downloader_alert.json"
LOG_DIR = BASE / "logs"

WINDOW_MIN = 40          # lookback minutes (09:15 open -> ~09:55 run = full warmup)
GREEN_MAX_429 = 10       # <= this many 429s in the window = fix holding
YELLOW_MAX_429 = 35      # <= this = borderline; above = still broken


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _load_events(window_min):
    try:
        data = json.loads(EVENTS_FILE.read_text())
    except Exception:
        return []
    cutoff = time.time() - window_min * 60
    return [e for e in data if e.get("ts", 0) >= cutoff]


def _fmt_counter(c, n=6):
    return ", ".join(f"{k}×{v}" for k, v in c.most_common(n)) or "none"


def analyse(window_min=WINDOW_MIN):
    ev = _load_events(window_min)
    by_kind = collections.Counter(e.get("kind") for e in ev)
    n429 = by_kind.get("429", 0)
    ntimeout = by_kind.get("timeout", 0)
    nthrottle = by_kind.get("throttle", 0)

    r429 = [e for e in ev if e.get("kind") == "429"]
    to = [e for e in ev if e.get("kind") == "timeout"]
    ctx_429 = collections.Counter(e.get("context", "?") for e in r429)
    prio_429 = collections.Counter(str(e.get("priority")) for e in r429)
    ctx_to = collections.Counter(e.get("context", "?") for e in to)

    if n429 <= GREEN_MAX_429:
        verdict, emoji = "GREEN", "✅"
    elif n429 <= YELLOW_MAX_429:
        verdict, emoji = "YELLOW", "⚠️"
    else:
        verdict, emoji = "RED", "\U0001f6a8"

    return {
        "window_min": window_min, "total": len(ev),
        "n429": n429, "ntimeout": ntimeout, "nthrottle": nthrottle,
        "ctx_429": ctx_429, "prio_429": prio_429, "ctx_to": ctx_to,
        "verdict": verdict, "emoji": emoji,
    }


def _report_text(a):
    L = []
    L.append(f"Rate-Limit Verify @ {_ist_now():%Y-%m-%d %H:%M} IST "
             f"(last {a['window_min']} min)")
    L.append(f"VERDICT: {a['emoji']} {a['verdict']}")
    L.append(f"  429s: {a['n429']}   timeouts: {a['ntimeout']}   "
             f"throttles: {a['nthrottle']}   total events: {a['total']}")
    L.append(f"  429 by priority: {_fmt_counter(a['prio_429'])}")
    L.append(f"  429 by context : {_fmt_counter(a['ctx_429'])}")
    L.append(f"  timeout by ctx : {_fmt_counter(a['ctx_to'])}")
    if a["verdict"] == "GREEN":
        L.append("  => TRAP #95 fix holding under live multi-strategy load.")
    else:
        top = a["ctx_429"].most_common(1)
        who = top[0][0] if top else "?"
        L.append(f"  => Still elevated. Biggest offender: {who}. "
                 "Check that context's call path for a missing acquire()/cache-read.")
    return "\n".join(L)


def _banner_line(a):
    if a["verdict"] == "GREEN":
        return (f"✅ Rate-limit fix verified (TRAP #95): only {a['n429']} "
                f"DH-904s in the {a['window_min']}-min market-open window — healthy.")
    top = a["ctx_429"].most_common(1)
    who = top[0][0] if top else "?"
    return (f"{a['emoji']} Rate-limit still {a['verdict']}: {a['n429']} DH-904s in "
            f"{a['window_min']} min (top: {who}). Rate Limit Room dekho (Logs tab).")


def _push_banner(line):
    try:
        try:
            alerts = json.loads(BANNER_FILE.read_text())
            if not isinstance(alerts, list):
                alerts = []
        except Exception:
            alerts = []
        alerts = [a for a in alerts if "Rate-limit" not in a and "TRAP #95" not in a]
        alerts.insert(0, line)
        BANNER_FILE.write_text(json.dumps(alerts[:20]))
    except Exception as e:
        print("[rate_limit_verify] banner write failed:", e, flush=True)


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    window = WINDOW_MIN
    if "--window" in args:
        try:
            window = int(args[args.index("--window") + 1])
        except (ValueError, IndexError):
            pass

    a = analyse(window)
    report = _report_text(a)
    print(report, flush=True)

    if dry:
        return

    LOG_DIR.mkdir(exist_ok=True)
    (LOG_DIR / f"rate_limit_verify_{_ist_now():%Y-%m-%d}.txt").write_text(report)
    _push_banner(_banner_line(a))
    # exit 1 on non-green so systemd surfaces it (like health_check.py's convention)
    sys.exit(0 if a["verdict"] == "GREEN" else 1)


if __name__ == "__main__":
    main()
