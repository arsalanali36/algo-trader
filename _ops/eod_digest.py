#!/usr/bin/env python3
"""
eod_digest.py — EOD digest across all mission strategies ("aapki 7 jodi aankhein").

Ek command me, har strategy ke liye:
  - log health : heartbeat gaps, PAPER/LIVE banner (TRAP #57 check), errors, recovers
  - activity   : signals seen, ★ ENTRY, [ENTRY SKIP]+reason, [EXIT]+reason
  - order_store: legs, modes, ₹0-price fills (TRAP #1), still-OPEN rows (TRAP #28/#36),
                 completed trades + P&L + exit reasons
  - cross-check: log-entries vs store-rows mismatch (ghost / unlogged orders)
  - optional   : _ops/backtest_expectations.json ke against actuals

Usage:
    python -X utf8 _ops/eod_digest.py                     # today
    python -X utf8 _ops/eod_digest.py --date 2026-07-13
    python -X utf8 _ops/eod_digest.py --ids chainzone_v1,orbst_v1
    python -X utf8 _ops/eod_digest.py --expect-mode live  # jab live jao

Exit code = number of RED strategies (cron/notification friendly).
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import _paths  # noqa: F401  — sys.path bootstrap (_core flat imports)
import order_store

try:  # Windows console safety (repo convention: run with -X utf8)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TC_FILE = BASE_DIR / "nifty_config.json"
_SKIP_KEYS = {"_risk", "webhooks"}          # non-strategy config keys


def discover_ids(date, logs_dir):
    """SAB strategies — current + future, koi hardcoded list nahi.
    = config me active:true keys  ∪  jinke log me is date ki lines hain.
    (webhook_* skip — uska apna monitor hai, per-strategy log file nahi.)"""
    ids = set()
    try:
        cfg = json.loads(TC_FILE.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    keys = [k for k in cfg
            if isinstance(cfg.get(k), dict) and k not in _SKIP_KEYS
            and not k.startswith("webhook")]
    for k in keys:
        if cfg[k].get("active"):
            ids.add(k)
    date_b = date.encode() if isinstance(date, str) else date
    for k in keys:                            # inactive but ran that day → include
        if k in ids:
            continue
        f = logs_dir / f"{k}.log"
        try:
            if f.exists() and date_b in f.read_bytes():
                ids.add(k)
        except Exception:
            pass
    return sorted(ids)

MARKET_OPEN, MARKET_CLOSE = "09:16", "15:25"
GAP_YELLOW_MIN, GAP_RED_MIN = 3, 10          # heartbeat-gap thresholds (minutes)

LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\s+(\w+)\s+(.*)$")
EXPECT_FILE = BASE_DIR / "_ops" / "backtest_expectations.json"


def ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────────────────
#  Log parsing
# ──────────────────────────────────────────────────────────────────────────
def parse_log(sid, date, logs_dir):
    """Return a dict of everything the day's log tells us for one strategy."""
    out = dict(exists=False, lines=0, first=None, last=None, mode_banner=None,
               entries=[], skips=[], exits=[], errors=[], warnings=0,
               recovers=[], signals=Counter(), paused=0, closed_loops=0,
               loop_errors=0, gaps=[], gap_red_th=GAP_RED_MIN,
               log_first_date=None, log_last_date=None, cadence_min=None)
    f = logs_dir / f"{sid}.log"
    if not f.exists():
        return out
    out["exists"] = True

    times = []          # every log timestamp this date (heartbeat evidence)
    prev_sig = None
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        out["errors"].append(f"(log read fail: {e})")
        return out

    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if not m:
            continue
        ld = m.group(1)                                  # log line ka date
        if out["log_first_date"] is None or ld < out["log_first_date"]:
            out["log_first_date"] = ld
        if out["log_last_date"] is None or ld > out["log_last_date"]:
            out["log_last_date"] = ld
        if ld != date:
            continue
        t, lvl, msg = m.group(2), m.group(3), m.group(4)
        out["lines"] += 1
        times.append(t)
        out["first"] = out["first"] or t
        out["last"] = t

        if f"|  {sid}  |" in msg:                       # startup banner
            out["mode_banner"] = "LIVE" if "LIVE" in msg else "PAPER"
        if "★ ENTRY" in msg:
            out["entries"].append((t, msg.strip()))
        elif "[ENTRY SKIP]" in msg:
            out["skips"].append((t, msg.split("[ENTRY SKIP]", 1)[1].strip()))
        elif "[EXIT]" in msg:
            out["exits"].append((t, msg.split("[EXIT]", 1)[1].strip()))
        elif "[RECOVER]" in msg:
            out["recovers"].append((t, msg.strip()))
        elif "Paused — active=false" in msg:
            out["paused"] += 1
        elif "Market closed" in msg:
            out["closed_loops"] += 1

        sm = re.search(r"(?:signal|breakout)=(\w+)", msg)   # STRDL/DVERT/BSPRD → breakout=
        if sm:
            s = sm.group(1)
            if s != "none" and s != prev_sig:
                out["signals"][s] += 1
            prev_sig = s

        if lvl == "ERROR":
            out["errors"].append(f"{t} {msg.strip()[:110]}")
            if "Loop error" in msg:
                out["loop_errors"] += 1
        elif lvl == "WARNING":
            out["warnings"] += 1

    # heartbeat gaps inside market hours — position-OPEN windows excluded
    # (chainzone/orb family sirf flat me heartbeat likhti hai; open pos = quiet OK)
    open_iv, cur = [], None
    for t, _ in out["entries"]:
        if cur is None:
            cur = t
        nxt = next((xt for xt, _ in out["exits"] if xt >= t), "23:59:59")
        open_iv.append((cur, nxt)); cur = None

    def _in_open(a, b):
        return any(s <= a and b <= e for s, e in open_iv)

    hb = [t for t in times if MARKET_OPEN <= t[:5] <= MARKET_CLOSE]
    deltas, pairs = [], []
    for a, b in zip(hb, hb[1:]):
        da = datetime.strptime(a, "%H:%M:%S")
        db = datetime.strptime(b, "%H:%M:%S")
        gap = (db - da).total_seconds() / 60
        # Squareoff/no-entry ke baad (CAS 15:10 se) flat trader by-design chup rehta hai
        # aur sirf ~15:25 pe "Market closed" likhta hai — ye EOD-quiet gap benign hai.
        # a>=15:08 (last poll just before the 15:10 squareoff) YA b>=15:20 (market-close
        # pe resume) dono isi quiet ko pakadte hain; asli mid-day freeze dono se pehle hota.
        if a >= "15:08" or b >= "15:20":
            continue
        if _in_open(a, b):      # position-open window me heartbeat chup = OK
            continue
        deltas.append(gap)
        pairs.append((a, b, round(gap, 1)))

    # Cadence-aware: har strategy ka apna natural log-interval hota hai (orb ~5m,
    # rsi ~2m). Us cadence ke around ka gap NORMAL hai — sirf "freeze/mar gaya"
    # wale bade gap flag karo (median se kaafi bada). Warna 60-70 jhoothe alarm.
    if deltas:
        med = statistics.median(deltas)
        out["cadence_min"] = round(med, 1)
        # Asli "process froze/mar gaya" 7min+ hota hai; 3-6min ke chhote gaps
        # (Dhan slow tick, ek-do loop late) actionable nahi — unhe noise mat banao.
        yellow_th = max(7.0, med * 3)
        red_th = max(GAP_RED_MIN, med * 5)      # 10min+ ya cadence-se-bahut-bada
        out["gap_red_th"] = red_th
        flagged = [(a, b, g) for (a, b, g) in pairs if g >= yellow_th]
        flagged.sort(key=lambda x: x[2], reverse=True)
        out["gaps"] = flagged[:4]           # sirf top-4 bade — wall-of-gaps nahi
    return out


# ──────────────────────────────────────────────────────────────────────────
#  order_store cross-section
# ──────────────────────────────────────────────────────────────────────────
def store_section(sid, date):
    rows = order_store.query(date=date, strategy=sid)
    live_rows = [r for r in rows if (r.get("mode") or "") == "live"]
    zero_price = [r for r in rows
                  if (r.get("price") or 0) <= 0
                  and (r.get("status") or "").upper() not in ("CAPITAL_BLOCKED", "REJECTED")]
    open_rows = [r for r in rows if (r.get("status") or "").upper() == "OPEN"]
    blocked = [r for r in rows if (r.get("status") or "").upper() == "CAPITAL_BLOCKED"]
    net = order_store.trades_for(date, strategy=sid)   # {details, open, count}
    completed = net.get("details", [])
    pnl = round(sum(tr.get("pnl") or 0 for tr in completed), 2)
    return dict(rows=rows, live_rows=live_rows, zero_price=zero_price,
                open_rows=open_rows, blocked=blocked, completed=completed, pnl=pnl)


# ──────────────────────────────────────────────────────────────────────────
#  Verdict
# ──────────────────────────────────────────────────────────────────────────
def judge(sid, lg, st, expect_mode, market_day, date, mode_explicit=False):
    """Return (colour, [reasons]) — colour in GREY/RED/YELLOW/GREEN.
    GREY = strategy is date ko chali hi nahi (deploy baad me / inactive) — issue nahi."""
    red, yellow = [], []

    # ── "chali hi nahi" ko samajhdaari se handle karo ──
    if lg["lines"] == 0:
        if not st["rows"]:
            # us date ka log bilkul nahi + store me kuch nahi. Asli problem TABHI hai
            # jab strategy us waqt EXIST karti thi (log baaki dino ka hai) AND market day tha.
            existed_then = (lg["log_first_date"] is not None
                            and lg["log_first_date"] <= date)
            if existed_then and market_day:
                red.append("koi log line nahi is date ki — process chala hi nahi "
                           f"(log to hai {lg['log_first_date']}→{lg['log_last_date']} ka)")
            else:
                return "GREY", ["is date ko ye strategy chali hi nahi "
                                "(deploy baad me hua ya inactive) — normal, koi issue nahi"]
        # store me rows hain par log line nahi — genuinely odd, checks chalein

    # ── mode (TRAP #57) — sirf tab jab expected mode config me EXPLICIT ho ──
    if mode_explicit:
        if lg["mode_banner"] and lg["mode_banner"] != expect_mode.upper():
            red.append(f"TRAP #57: banner {lg['mode_banner']} hai, expected {expect_mode.upper()}")
        bad_mode = [r for r in st["rows"] if (r.get("mode") or "paper") != expect_mode]
        if bad_mode:
            red.append(f"order_store me {len(bad_mode)} row(s) mode≠{expect_mode} (TRAP #57)")

    if st["zero_price"]:
        red.append(f"TRAP #1: {len(st['zero_price'])} ₹0/None-price fill row(s)")
    if st["open_rows"]:
        red.append(f"{len(st['open_rows'])} position STILL OPEN in store (EOD flat nahi — TRAP #28/#36)")
    if lg["entries"] and not st["rows"]:
        red.append("log me ENTRY hai par order_store KHALI — recording gap")
    # "ghost" sirf tab jab store me rows hain, log me koi ENTRY/RECOVER nahi, AUR koi
    # completed round-trip bhi nahi. Legacy trader (range/rsi) ka log '★ ENTRY' format
    # nahi likhta par woh genuinely trade karte hain → completed trades = ghost nahi.
    if st["rows"] and not lg["entries"] and not lg["recovers"] and not st["completed"]:
        red.append(f"store me {len(st['rows'])} row(s) par log me koi ENTRY nahi — ghost?")
    if lg["loop_errors"]:
        red.append(f"{lg['loop_errors']} Loop error(s) — traceback log me")
    for a, b, g in lg["gaps"]:
        (red if g >= lg.get("gap_red_th", GAP_RED_MIN) else yellow).append(
            f"heartbeat gap {g} min ({a}→{b})"
            + (f" — cadence ~{lg['cadence_min']}m" if lg.get("cadence_min") else ""))
    if lg["errors"] and not lg["loop_errors"]:
        yellow.append(f"{len(lg['errors'])} ERROR line(s)")
    if lg["paused"] and not lg["entries"]:
        yellow.append("active=false — poora din paused")
    if st["blocked"]:
        yellow.append(f"{len(st['blocked'])} CAPITAL_BLOCKED row(s) — RMS ne roka, dekh lo kyun")

    colour = "RED" if red else ("YELLOW" if yellow else "GREEN")
    return colour, red + yellow


def mode_is_explicit(sid):
    """config me is strategy ka `mode` key literally present hai?"""
    try:
        cfg = json.loads(TC_FILE.read_text(encoding="utf-8"))
        return (cfg.get(sid) or {}).get("mode") in ("paper", "live")
    except Exception:
        return False


def expected_mode_for(sid, fallback="paper"):
    """Per-strategy expected mode — config ka `mode` key (TRAP #57 fix isse persist
    karta hai) > fallback. eod_digest CLI aur eod_report dono isi se."""
    try:
        cfg = json.loads(TC_FILE.read_text(encoding="utf-8"))
        m = (cfg.get(sid) or {}).get("mode")
        return m if m in ("paper", "live") else fallback
    except Exception:
        return fallback


def load_expectations():
    if EXPECT_FILE.exists():
        try:
            return json.loads(EXPECT_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  (expectations file unreadable: {e})")
    return {}


# ──────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="EOD digest across ALL strategies (auto-discovered)")
    ap.add_argument("--date", default=ist_today())
    ap.add_argument("--ids", default="", help="comma list — default: auto-discover (active ∪ ran-that-day)")
    ap.add_argument("--expect-mode", default="paper", choices=["paper", "live"],
                    help="TRAP #57 check: har strategy is mode me honi chahiye")
    ap.add_argument("--logs", default=str(BASE_DIR / "logs"))
    args = ap.parse_args()

    logs_dir = Path(args.logs)
    ids = ([s.strip() for s in args.ids.split(",") if s.strip()]
           or discover_ids(args.date, logs_dir))
    if not ids:
        print("koi strategy nahi mili (na active config, na aaj ka log)"); sys.exit(0)
    date = args.date
    expectations = load_expectations()

    def expected_mode(sid):
        return expected_mode_for(sid, args.expect_mode)

    print("═" * 74)
    print(f"  EOD DIGEST  |  {date}  |  expect-mode: per-config (fallback {args.expect_mode.upper()})"
          f"  |  {len(ids)} strategies")
    print("═" * 74)

    summary = []
    # market_day heuristic: kisi bhi strategy ke log me is date ki market-hours line
    parsed = {sid: parse_log(sid, date, logs_dir) for sid in ids}
    market_day = any(p["lines"] > p["closed_loops"] for p in parsed.values())

    for sid in ids:
        lg = parsed[sid]
        st = store_section(sid, date)
        colour, reasons = judge(sid, lg, st, expected_mode(sid), market_day,
                                date, mode_is_explicit(sid))
        summary.append((sid, colour, st["pnl"], len(lg["entries"]),
                        len(st["completed"]), reasons))

        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "GREY": "⚪"}[colour]
        print(f"\n{icon} {sid}  [{lg['mode_banner'] or '?'}]"
              f"  log {lg['first'] or '--'}→{lg['last'] or '--'}"
              f"  ({lg['lines']} lines)")

        sig_txt = ", ".join(f"{k}×{v}" for k, v in lg["signals"].items()) or "none"
        print(f"   signals: {sig_txt}   entries: {len(lg['entries'])}"
              f"   skips: {len(lg['skips'])}   exits: {len(lg['exits'])}"
              f"   err/warn: {len(lg['errors'])}/{lg['warnings']}")

        for t, e in lg["entries"]:
            print(f"   ★ {t}  {e[:96]}")
        for t, s in lg["skips"][:6]:
            print(f"   ⤳ SKIP {t}  {s[:88]}")
        if len(lg["skips"]) > 6:
            print(f"   ⤳ ... +{len(lg['skips']) - 6} more skips")
        for t, x in lg["exits"]:
            print(f"   ⏹ {t}  {x[:92]}")
        for t, r in lg["recovers"]:
            print(f"   ♻ {t}  {r[:92]}")

        if st["completed"]:
            print(f"   trades ({len(st['completed'])}), P&L ₹{st['pnl']:+,.0f}:")
            for tr in st["completed"]:
                print(f"     {tr['sym']:<28} {tr['entry_time']}→{tr['exit_time']}"
                      f"  {tr['entry_price']:.2f}→{tr['exit_price']:.2f}"
                      f"  ₹{tr['pnl']:+,.0f}  [{tr.get('exit_reason') or '?'}]")
        elif st["rows"]:
            print(f"   store: {len(st['rows'])} leg(s), no completed round-trip yet")

        exp = expectations.get(sid)
        if exp and st["completed"]:
            wins = sum(1 for tr in st["completed"] if (tr.get("pnl") or 0) > 0)
            wr = 100.0 * wins / len(st["completed"])
            print(f"   vs backtest: trades/day exp {exp.get('trades_per_day', '?')}"
                  f" got {len(st['completed'])} | win% exp {exp.get('win_rate', '?')}"
                  f" got {wr:.0f}")

        for r in reasons:
            print(f"   ⚠ {r}")
        for e in lg["errors"][:3]:
            print(f"   ✗ {e}")

    # ── summary table ──
    print("\n" + "═" * 74)
    print(f"  {'strategy':<16}{'status':<9}{'entries':<9}{'trades':<8}{'P&L ₹':<12}issues")
    print("─" * 74)
    reds = greys = 0
    for sid, colour, pnl, n_ent, n_tr, reasons in summary:
        reds += colour == "RED"
        greys += colour == "GREY"
        print(f"  {sid:<18}{colour:<9}{n_ent:<9}{n_tr:<8}{pnl:<+12,.0f}{len(reasons)}")
    tot = sum(s[2] for s in summary)
    print("─" * 74)
    tail = (f"🔴 {reds} RED — pehle inko dekho" if reds else "sab clear")
    if greys:
        tail += f"  ({greys} ⚪ chali hi nahi — ignore)"
    print(f"  {'TOTAL':<18}{'':<9}{'':<9}{'':<8}{tot:<+12,.0f}{tail}")
    if args.expect_mode == "paper":
        print("  note: paper fill = LTP, na slippage na charges — backtest me costs THE,")
        print("        isliye paper P&L backtest se thoda BEHTAR dikhega. Utna discount karo.")
    print("═" * 74)
    sys.exit(min(reds, 20))


if __name__ == "__main__":
    main()
