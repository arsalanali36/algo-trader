#!/usr/bin/env python3
"""Pine2Python drift — 04.04 DirectWebhook (TV pine) vs 04.03 Pine2Python (python).

WHY THIS EXISTS
    04.03 exists for exactly one reason: to reproduce 04.04 well enough that the
    TradingView webhook (₹1,600/month, ₹19,200/yr) can be switched off. Offline
    fidelity is 90.2% exact / 93% entry (_TOOLS/validate_strategy.py, historical,
    against a pasted TV export). What was never measured is the LIVE daily gap —
    so the retire-the-webhook decision has never had a number behind it.

    And the honest question isn't "is it 1:1 exact?" — pine and python will never
    fully agree (different data source, different bar-close timing). It's:

        is the drift worth less than ₹19,200/year?

    That is measurable. This measures it.

WHAT IT COMPARES
    TV side     — logs/webhook_v1.log's own ENTRY/EXIT lines for arschain_MAIN.
                  That is what TradingView actually SENT, not what we did with it:
                  an RMS block or a dedup skip is our side's business, not the
                  pine's opinion. Skips are still surfaced separately.
    Python side — range_v1's own order_store rows for the day.

                  signal_replay.run_for() was the first choice (Rule 6B — reuse the
                  replay engine, don't write a second one), but it can't take this
                  strategy: it requires a compute_signal(df,cfg) contract and
                  range_trader's engine is run_signal_engine(df_1m, key_levels, cfg).
                  Its own comment is also explicit that importing these legacy
                  engines is unsafe — "unka module-level import heavy side-effects
                  chalata hai (equity cache, feed, yahan tak ki token-print bug) —
                  unhe kabhi import mat karo". A drift tool must not start a feed.

                  order_store answers the question anyway, and separates the two
                  causes that matter:
                      row filled/paper        -> signalled AND entered
                      row blocked (+ reason)  -> signalled, our RMS stopped it
                      no row                  -> never signalled  <- the real gap
                  So an RMS block is never miscounted as a fidelity gap.

MATCHING
    Both run 5m. TV stamps the bar close; the python loop wakes every 300s and acts
    on the last CLOSED bar, so the same decision can land minutes apart in wall
    clock while being the SAME BAR. Matching is therefore by bar, with a tolerance
    window — never by exact timestamp.

READ-ONLY. Places nothing, changes nothing, touches no config.

    venv/bin/python _ops/pine2python_drift.py [--date YYYY-MM-DD] [--json]
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa: sys.path bootstrap

REF = "arschain_MAIN"   # 04.04 DirectWebhook — the reference (pine on TV)
MIR = "range_v1"        # 04.03 Pine2Python  — the mirror (python)
WH_LOG = os.path.join(ROOT, "logs", "webhook_v1.log")
BAR_MIN = 5             # both sides run 5m
TOL_BARS = 1            # same decision, one bar apart = still a match


def ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _bar(hhmm):
    """Floor a HH:MM to its 5m bar index — the unit both sides actually agree in."""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    return (h * 60 + m) // BAR_MIN


# "2026-07-17 10:05:08,000  INFO  ENTRY arschain_MAIN SHORT NIFTY SELL 130 ..."
_ENTRY = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d[,\d]*\s+\w+\s+ENTRY\s+(\S+)\s+(LONG|SHORT)\s+(\S+)")
_EXIT = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d[,\d]*\s+\w+\s+EXIT\s+(\S+)")
_SKIP = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d):\d\d[,\d]*\s+\w+\s+(ENTRY|EXIT) (?:skip|blocked) (\S+)\s+—\s+(.*)$")


def tv_signals(date_str):
    """What TradingView SENT for the reference strategy — plus what we skipped."""
    out, skips = [], []
    if not os.path.exists(WH_LOG):
        return out, skips
    with open(WH_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith(date_str):
                continue
            m = _SKIP.match(line)
            if m and REF in m.group(4):
                skips.append({"time": m.group(2), "bar": _bar(m.group(2)),
                              "kind": m.group(3), "why": m.group(5).strip()[:90]})
                continue
            m = _ENTRY.match(line)
            if m and m.group(3) == REF:
                out.append({"time": m.group(2), "bar": _bar(m.group(2)),
                            "kind": "ENTRY", "dir": m.group(4)})
                continue
            m = _EXIT.match(line)
            if m and REF in m.group(3):
                out.append({"time": m.group(2), "bar": _bar(m.group(2)),
                            "kind": "EXIT", "dir": ""})
    return out, skips


def py_signals(date_str):
    """What range_v1 actually decided that day, from its own order_store rows.

    NIFTY only — 04.03 mirrors a NIFTY-only pine, and its other symbols (if any
    creep back into the config) are not part of this comparison.

    Direction is read from the CONTRACT's own CE/PE + the strategy's long/short
    option-type config, never from the order side: this family sells for BOTH
    directions (long -> sell PE, short -> sell CE), so `SELL` carries no direction
    information at all. That exact inference is what cost a real trade on
    2026-07-16 (TRAP #128).
    """
    import order_store
    import dhan_master
    import webhook_executor as _wh
    try:
        rows = order_store.trades_for(date_str)
    except Exception as e:
        return None, "order_store read fail: %s" % e

    cfg = _wh._strat_cfg(REF)          # the pine's own long/short opt-type mapping
    long_ot = (cfg.get("long_opt_type") or "").strip().upper()
    short_ot = (cfg.get("short_opt_type") or "").strip().upper()

    legs = list(rows.get("open") or []) + list(rows.get("blocked") or [])
    for d in (rows.get("details") or []):
        legs.append({"strategy": d.get("strategy"), "symbol": d.get("symbol"),
                     "entry_time": d.get("entry_time"), "sec_id": d.get("sec_id"),
                     "entry": d.get("entry"), "status": "filled", "tags": d.get("tags") or []})

    out = []
    for p in legs:
        if (p.get("strategy") or "") != MIR or (p.get("symbol") or "").upper() != "NIFTY":
            continue
        t = str(p.get("entry_time") or p.get("ts") or "")[-8:][:5]
        if not re.match(r"^\d\d:\d\d$", t):
            continue
        ot = dhan_master.get_option_type_by_sec_id(p.get("sec_id"))
        direction = ("LONG" if ot and ot == long_ot else
                     "SHORT" if ot and ot == short_ot else "?")
        blocked = (p.get("status") == "blocked" or
                   any(str(x).startswith("CAPITAL_BLOCKED") for x in (p.get("tags") or [])))
        why = ""
        if blocked:
            why = next((str(x)[:70] for x in (p.get("tags") or [])
                        if not str(x).startswith(("MAX_LTP", "MIN_LTP", "CONF_", "PREV_"))), "blocked")
        out.append({"time": t, "bar": _bar(t), "kind": "ENTRY", "dir": direction,
                    "blocked": blocked, "why": why})
    out.sort(key=lambda x: x["bar"])
    return out, None


def align(tv, py):
    """Pair by bar (±TOL_BARS). Same bar + same direction = MATCH."""
    rows, used = [], set()
    for a in tv:
        hit = None
        for i, b in enumerate(py):
            if i in used or b["kind"] != a["kind"]:
                continue
            if abs(b["bar"] - a["bar"]) <= TOL_BARS:
                hit = i
                break
        if hit is None:
            rows.append({"v": "TV-ONLY", "tv": a, "py": None})
        else:
            used.add(hit)
            b = py[hit]
            same = (not a["dir"]) or (a["dir"] == b["dir"])
            v = "MATCH" if same else "DIR-MISMATCH"
            if same and b.get("blocked"):
                v = "MATCH (RMS-blocked)"   # pine agreed; our RMS stopped the order
            rows.append({"v": v, "tv": a, "py": b})
    for i, b in enumerate(py):
        if i not in used:
            rows.append({"v": "PY-ONLY", "tv": None, "py": b})
    rows.sort(key=lambda r: (r["tv"] or r["py"])["bar"])
    return rows


def _totals(since, upto, as_json):
    """Add the days up — one day proves nothing, the running total is the answer.

    ⚠️ Only count from 2026-07-17. Before that 04.03 ran 25 symbols (the union of
    04.01+04.02), so stocks ate its capital and NIFTY got CAPITAL_BLOCKED — one
    NIFTY trade in a MONTH. The mirror was structurally handicapped, and a drift
    number over that period would measure a config bug, not fidelity. The clean
    sample starts the day it went NIFTY-only.
    """
    d0 = datetime.strptime(since, "%Y-%m-%d").date()
    d1 = datetime.strptime(upto, "%Y-%m-%d").date()
    days, tot = [], dict(match=0, tv_only=0, py_only=0, dir_mismatch=0)
    d = d0
    while d <= d1:
        ds = d.isoformat()
        tv, _ = tv_signals(ds)
        py, err = py_signals(ds)
        if not err and (tv or py):
            rows = align(tv, py)
            k = dict(date=ds,
                     match=sum(1 for r in rows if r["v"].startswith("MATCH")),
                     tv_only=sum(1 for r in rows if r["v"] == "TV-ONLY"),
                     py_only=sum(1 for r in rows if r["v"] == "PY-ONLY"),
                     dir_mismatch=sum(1 for r in rows if r["v"] == "DIR-MISMATCH"))
            days.append(k)
            for x in tot:
                tot[x] += k[x]
        d += timedelta(days=1)
    n = sum(tot.values())
    tot["signals"] = n
    tot["fidelity_pct"] = round(100.0 * tot["match"] / n, 1) if n else None
    if as_json:
        print(json.dumps({"since": since, "upto": upto, "days": days, "total": tot}, indent=2))
        return 0
    print()
    print("  Pine2Python drift — %s .. %s  (%d trading days ke saath)" % (since, upto, len(days)))
    print("  " + "-" * 64)
    print("  %-12s %6s %8s %8s %6s" % ("date", "match", "TV-only", "PY-only", "dir!="))
    for k in days:
        print("  %-12s %6d %8d %8d %6d" % (k["date"], k["match"], k["tv_only"], k["py_only"], k["dir_mismatch"]))
    print("  " + "-" * 64)
    print("  %-12s %6d %8d %8d %6d" % ("TOTAL", tot["match"], tot["tv_only"], tot["py_only"], tot["dir_mismatch"]))
    print()
    if n:
        print("  %d signals me se %d match  ->  fidelity %s%%" % (n, tot["match"], tot["fidelity_pct"]))
    else:
        print("  Abhi tak koi signal nahi — aur data chahiye.")
    print()
    print("  Faisla fidelity% se nahi hota — TV-only trades ka ASLI P&L jodo.")
    print("  Wo saal me ₹19,200 se kam ho -> webhook band, wahi bachat hai.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=ist_today())
    ap.add_argument("--since", help="YYYY-MM-DD — is din se aaj tak ka jod (roz ka jama)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.since:
        return _totals(a.since, a.date, a.json)

    tv, skips = tv_signals(a.date)
    py, err = py_signals(a.date)

    rep = {"date": a.date, "reference": REF, "mirror": MIR,
           "tv_signals": len(tv), "py_signals": None if py is None else len(py),
           "replay_error": err, "skips": skips, "rows": []}
    if py is not None:
        rows = align(tv, py)
        rep["rows"] = rows
        rep["match"] = sum(1 for r in rows if r["v"].startswith("MATCH"))
        rep["tv_only"] = sum(1 for r in rows if r["v"] == "TV-ONLY")
        rep["py_only"] = sum(1 for r in rows if r["v"] == "PY-ONLY")
        rep["dir_mismatch"] = sum(1 for r in rows if r["v"] == "DIR-MISMATCH")
        rep["fidelity_pct"] = round(100.0 * rep["match"] / len(rows), 1) if rows else None

    if a.json:
        print(json.dumps(rep, indent=2))
        return 0

    print()
    print("  Pine2Python drift — %s" % a.date)
    print("  04.04 DirectWebhook (TV pine)  vs  04.03 Pine2Python (python)")
    print("  " + "-" * 64)
    if err:
        print("  🔴 python side replay nahi hua: %s" % err)
        print("     (TV ne %d signal bheje the)" % len(tv))
        return 1
    print("  TV ne bheje  : %d" % len(tv))
    print("  Python ne diye: %d" % len(py))
    print()
    if not rep["rows"]:
        print("  Dono taraf koi signal nahi — aaj compare karne ko kuch nahi.")
    else:
        print("  %-6s %-14s %-14s %s" % ("bar", "TV", "Python", "verdict"))
        for r in rep["rows"]:
            t, p = r["tv"], r["py"]
            print("  %-6s %-14s %-14s %s" % (
                (t or p)["time"],
                ("%s %s" % (t["kind"], t["dir"])).strip() if t else "—",
                ("%s %s" % (p["kind"], p["dir"])).strip() if p else "—",
                r["v"]))
        print()
        print("  MATCH %d | TV-only %d | PY-only %d | dir-mismatch %d  ->  fidelity %s%%"
              % (rep["match"], rep["tv_only"], rep["py_only"], rep["dir_mismatch"],
                 rep["fidelity_pct"]))
    if skips:
        print()
        print("  humne jo skip kiya (ye fidelity nahi — hamari RMS/dedup hai):")
        for s in skips:
            print("    %s %-5s %s" % (s["time"], s["kind"], s["why"]))
    print()
    print("  NOTE: TV-only = pine ne kaha, python ne nahi -> asli fidelity gap.")
    print("        PY-only = python ne kaha, pine ne nahi -> bhi gap (ulti taraf).")
    print("        Ek din ka number kuch nahi kehta. Ye roz chalao, jodo — jab")
    print("        drift ka saalana kharcha ₹19,200 se kam ho, webhook band karo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
