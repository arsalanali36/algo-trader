#!/usr/bin/env python3
"""
signal_replay.py — TRAP #108 detector: "kya LIVE trader ne wahi kiya jo uska
apna signal-code kehta hai?"

Market close ke baad, har strategy ke liye:
  1. Us din ke REAL candles trader ke APNE fetch path se laao (same API, same resample)
  2. Trader ka APNA compute_signal/compute_breakout bar-by-bar offline chalao
     (module ki ist_now() ko replay-clock se patch karke — kyunki signal fns
      wall-clock date pe slice karti hain)
  3. Jo offline signals nikle, unhe log ke ★ ENTRY / [ENTRY SKIP] se diff karo

STRATEGY COVERAGE — koi hardcoded list NAHI (current + future sab):
  ids auto-discover hote hain (eod_digest.discover_ids — active config ∪ ran-that-day),
  trader script health_check.TRADER_SCRIPTS se resolve hota hai (Rule 6B: single
  source, teesri copy nahi), signal fn auto-detect (compute_signal → 'signal' key,
  compute_breakout → 'direction' key). NEW_STRATEGY_CHECKLIST template follow karne
  wali har future strategy automatically covered. Legacy engines (range/rsi/ema —
  jo compute_* pattern pe nahi hain) gracefully "replay N/A" dikhte hain.

Verdicts per signal:
  MATCH   — offline signal ↔ log ENTRY (±match-window min)          🟢
  GATED   — signal tha par window/max-trades/late/SKIP ne roka       🟢 (expected)
  MISS    — offline signal, par live ne NA entry ki NA skip logged   🔴 TRAP #108!
            (annotated: trader start ke pehle / down ho chuka tha)
  EXTRA   — live ENTRY jiska koi offline signal nahi                 🔴 TRAP #108!

Usage:
    python -X utf8 _ops/signal_replay.py                     # today, auto-discover
    python -X utf8 _ops/signal_replay.py --date 2026-07-13 --ids chainzone_v1

NOTE: Dhan intraday history kuch hi din rakhti hai — replay USI SHAAM chalao.
Exit code = strategies with MISS/EXTRA/data-issue.
"""
import argparse
import importlib.util
import inspect
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import _paths  # noqa: F401
import health_check                      # TRADER_SCRIPTS + _base (Rule 6B — extend, duplicate nahi)
from eod_digest import discover_ids      # shared auto-discovery

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# sirf EXCEPTIONS ke liye: sid -> (script_path, fn_name). Normal case auto-resolve hota hai.
OVERRIDES = {}

LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\s+\w+\s+(.*)$")
NO_ENTRY_FALLBACK = (15, 15)      # risk_gate default — late signals gated


def ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def resolve_strategy(sid):
    """(module, sig_fn, dir_key) — script health_check se, fn auto-detect."""
    if sid in OVERRIDES:
        path, fn_name = OVERRIDES[sid]
        path = Path(path)
    else:
        path = health_check.TRADER_SCRIPTS.get(health_check._base(sid))
        fn_name = None
    if not path or not Path(path).exists():
        raise LookupError("live trader script resolve nahi hua (health_check.TRADER_SCRIPTS)")
    # Import se PEHLE source-scan: legacy engines (range/rsi/ema) me compute_* nahi hota,
    # aur unka module-level import heavy side-effects chalata hai (equity cache, feed,
    # yahan tak ki token-print bug) — unhe kabhi import mat karo.
    src = Path(path).read_text(encoding="utf-8", errors="replace")
    if not fn_name and not re.search(r"^def compute_(signal|breakout)\(", src, re.M):
        raise LookupError("compute_signal/compute_breakout nahi mila — legacy engine, replay N/A")
    spec = importlib.util.spec_from_file_location(f"replay_{sid}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if fn_name:
        fn = getattr(mod, fn_name)
        dir_key = "signal" if fn_name == "compute_signal" else "direction"
    elif hasattr(mod, "compute_signal"):
        fn, dir_key = mod.compute_signal, "signal"
    elif hasattr(mod, "compute_breakout"):
        fn, dir_key = mod.compute_breakout, "direction"
    else:
        raise LookupError("compute_signal/compute_breakout nahi mila — legacy engine, replay N/A")
    if not hasattr(mod, "load_config") or not hasattr(mod, "load_creds"):
        raise LookupError("load_config/load_creds nahi — checklist-template trader nahi hai")
    # Contract check: compute fn (df, cfg) hi ho — warna galat args pass honge
    # (e.g. nifty_ema_trader ka compute_signal(df, fast, slow) — template nahi)
    p = list(inspect.signature(fn).parameters)
    if p[:2] != ["df", "cfg"]:
        raise LookupError(f"compute fn ka contract (df,cfg) nahi hai ({p[:2]}) — replay N/A")
    return mod, fn, dir_key


def fetch_day_df(mod, cfg, days_back):
    """Trader ke apne fetch_* path se candles — same API, same resample."""
    fetch = next((getattr(mod, n) for n in dir(mod)
                  if n.startswith("fetch_") and callable(getattr(mod, n))), None)
    if fetch is None:
        raise RuntimeError("no fetch_* function in module")
    params = inspect.signature(fetch).parameters
    if list(params)[:2] != ["token", "cid"]:
        raise RuntimeError(f"fetch fn ka contract (token,cid,...) nahi ({list(params)[:2]}) — replay N/A")
    token, cid = mod.load_creds()
    kwargs = {"days": days_back} if "days" in params else {}
    if "tf_min" in params:
        tf = str(cfg.get("timeframe", "5m"))
        tf_map = getattr(mod, "_TF_MIN", {})
        kwargs["tf_min"] = tf_map.get(tf) or int(re.sub(r"\D", "", tf) or 5)
    return fetch(token, cid, **kwargs)


def parse_log_events(sid, date_str):
    """Return dict of log events + run window from logs/{sid}.log for date."""
    ev = dict(entries=[], skips=[], exits=[], expiry_skip=False, paused=False, ran=False,
              first=None, last=None)
    entries, skips, exits = ev["entries"], ev["skips"], ev["exits"]
    f = BASE_DIR / "logs" / f"{sid}.log"
    if not f.exists():
        return ev
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LOG_LINE.match(line)
        if not m or m.group(1) != date_str:
            continue
        ev["ran"] = True
        t, msg = m.group(2), m.group(3)
        ev["first"] = ev["first"] or t
        ev["last"] = t
        if "★ ENTRY" in msg:
            sm = re.search(r"spot=([\d.]+)", msg)
            entries.append(dict(time=t, msg=msg.strip(),
                                spot=float(sm.group(1)) if sm else None))
        elif "[ENTRY SKIP]" in msg:
            skips.append(dict(time=t, msg=msg.strip()))
        elif "[EXIT]" in msg:
            exits.append(dict(time=t, msg=msg.strip()))
        elif "expiry day — entry skipped" in msg:
            ev["expiry_skip"] = True
        elif "Paused — active=false" in msg:
            ev["paused"] = True
    return ev


def replay_signals(mod, sig_fn, dir_key, cfg, df, target_date):
    """Bar-by-bar: har forming-bar tak ka df de kar signal fn chalao —
    bilkul waise jaise live loop har cycle me dekhta hai."""
    day_mask = df["time"].dt.date == target_date
    day_idx = list(df.index[day_mask])
    if not day_idx:
        return None   # data unavailable

    real_ist_now = getattr(mod, "ist_now", None)
    clock = {"now": None}
    mod.ist_now = lambda: clock["now"]          # signal fns wall-clock slice karti hain

    signals, seen = [], set()
    try:
        for i in day_idx[1:]:                    # forming bar = i, closed = i-1
            bar_t = df.loc[i, "time"]
            clock["now"] = bar_t.to_pydatetime()
            try:
                sig = sig_fn(df.iloc[: i + 1], cfg)
            except Exception as e:
                signals.append(dict(time=None, dir=f"(fn error: {e})", spot=None,
                                    verdict="ERROR"))
                continue
            if not sig:
                continue
            d = sig.get(dir_key, "?")
            spot = sig.get("entry_spot") or sig.get("spot")
            closed_t = df.loc[i - 1, "time"]
            key = (str(closed_t), d)
            if key in seen:                      # state-machine refire dedupe
                continue
            seen.add(key)
            signals.append(dict(time=closed_t, dir=d, spot=spot, verdict=None,
                                stop=sig.get("stop")))
    finally:
        if real_ist_now is not None:
            mod.ist_now = real_ist_now
    return signals


def gate_in_position(signals, entries, exits):
    """Single-position-hold strategies (chainzone/straddle/condor/backspread ride one
    position at a time): while a logged position is open, the live loop's 'if flat'
    guard blocks re-entry — so an offline signal INSIDE a [entry → next EXIT] window is
    expected to be skipped, NOT a MISS (TRAP #108 false-positive otherwise). The opening
    signal lands just before its own ★ ENTRY log line (bar-close < entry-log time) so the
    strict `<` leaves it ungated for diff() to MATCH."""
    if not entries:
        return
    ex_times = sorted(e["time"] for e in exits)
    used = [False] * len(ex_times)
    intervals = []
    for en in sorted(e["time"] for e in entries):
        xt = "23:59:59"                      # no exit logged → held to EOD (intraday)
        for k, x in enumerate(ex_times):
            if not used[k] and x >= en:
                used[k] = True; xt = x; break
        intervals.append((en, xt))
    for s in signals:
        if s["verdict"] or s["time"] is None:
            continue
        st = s["time"].strftime("%H:%M:%S")
        if any(en < st <= xt for en, xt in intervals):
            s["verdict"] = "GATED(in-position)"


def apply_gates(signals, cfg, expiry_skip, paused):
    """Live loop ke config-gates offline signals pe bhi lagao — warna har
    window-ke-bahar signal jhootha MISS banega."""
    win_s, win_e = cfg.get("win_start"), cfg.get("win_end")
    max_tr = int(cfg.get("max_trades_per_day", cfg.get("max_trades_per_symbol", 99)))
    taken = 0
    for s in signals:
        if s["verdict"]:                          # ERROR rows
            continue
        hm = s["time"].strftime("%H:%M")
        if paused:
            s["verdict"] = "GATED(paused)"
        elif expiry_skip:
            s["verdict"] = "GATED(expiry-day)"
        elif win_s and win_e and not (str(win_s) <= hm <= str(win_e)):
            s["verdict"] = "GATED(window)"
        elif (s["time"].hour, s["time"].minute) >= NO_ENTRY_FALLBACK:
            s["verdict"] = "GATED(late)"
        elif taken >= max_tr:
            s["verdict"] = "GATED(max-trades)"
        else:
            taken += 1                            # expected live entry
    return signals


def diff(signals, entries, skips, match_min):
    """Signals ↔ log entries match. Mutates verdicts; returns leftover EXTRA entries."""
    used = set()
    for s in signals:
        if s["verdict"]:
            continue
        st = s["time"].strftime("%H:%M:%S")
        hi = (s["time"] + timedelta(minutes=match_min)).strftime("%H:%M:%S")
        hit = next((j for j, e in enumerate(entries)
                    if j not in used and st <= e["time"] <= hi), None)
        if hit is not None:
            used.add(hit)
            e = entries[hit]
            s["verdict"] = "MATCH"
            if s["spot"] and e["spot"] and abs(s["spot"] - e["spot"]) / e["spot"] > 0.002:
                s["verdict"] = f"MATCH(spot drift {s['spot']:.1f} vs {e['spot']:.1f})"
        elif any(st <= k["time"] <= hi for k in skips):
            s["verdict"] = "GATED(skip-logged)"   # RMS/capital ne roka — reason log me
        else:
            s["verdict"] = "MISS"
    extra = [e for j, e in enumerate(entries) if j not in used]
    return extra


def run_for(sid, date_str, match_min=6):
    """Ek strategy ka poora replay pipeline — CLI aur eod_report dono isi se.
    Returns dict: status ok|skip|fail, note, signals, extra, miss/match/gated/err counts."""
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    days_back = max((datetime.now().date() - target).days + 6, 6)
    res = dict(sid=sid, status="ok", note="", signals=[], extra=[],
               miss=0, match=0, gated=0, err=0, entries=0)
    try:
        mod, sig_fn, dir_key = resolve_strategy(sid)
    except LookupError as e:
        res.update(status="skip", note=str(e)); return res
    except Exception as e:
        res.update(status="fail", note=f"module load fail: {e}"); return res
    try:
        cfg = mod.load_config(sid)
        df = fetch_day_df(mod, cfg, days_back)
    except Exception as e:
        res.update(status="fail", note=f"fetch fail: {e}"); return res
    if df is None or getattr(df, "empty", True):
        res.update(status="fail", note="no candle data — token/API check karo"); return res

    # match-window MUST span the strategy's own bar interval. The offline replay stamps
    # a signal at the CLOSED bar's timestamp (i-1), but the live loop only ACTS on the
    # NEXT forming bar (~1 bar later) + loop latency + fill lag — so a genuine matching
    # live entry naturally lands ~timeframe minutes after the offline signal time. A
    # fixed 6-min window splits ONE real signal into a false MISS(offline)+EXTRA(live)
    # pair for any TF ≳ 3m (proven live: 15m orb signal stamped 11:00 vs entry logged
    # 11:15, identical spot). Raise the floor to the bar interval + ~3m slack so a real
    # 1-bar-later entry matches; genuine drift (no counterpart even in the wide window)
    # still surfaces as MISS/EXTRA.
    try:
        _tf_min = int(re.sub(r"\D", "", str(cfg.get("timeframe", "5m"))) or 5)
        match_min = max(match_min, _tf_min + 3)
    except Exception:
        pass

    ev = parse_log_events(sid, date_str)
    signals = replay_signals(mod, sig_fn, dir_key, cfg, df, target)
    if signals is None:
        res.update(status="fail",
                   note=f"{date_str} ke bars fetch-window me nahi — replay USI SHAAM chalao")
        return res

    gate_in_position(signals, ev["entries"], ev["exits"])   # in-position skips ≠ MISS
    apply_gates(signals, cfg, ev["expiry_skip"], ev["paused"])
    res["entries"] = len(ev["entries"])

    if not ev["ran"]:
        for s in signals:
            if not s["verdict"]:
                s["verdict"] = "INFO(trader-not-running)"
        res.update(status="skip", signals=signals,
                   note=f"log me {date_str} ki koi line nahi — trader ran nahi; "
                        f"{len(signals)} offline signal(s) info-only")
        return res

    res["extra"] = diff(signals, ev["entries"], ev["skips"], match_min)

    # MISS ko context do: process us waqt zinda tha ya nahi (drift vs down)
    for s in signals:
        if s["verdict"] == "MISS" and s["time"] is not None:
            st = s["time"].strftime("%H:%M:%S")
            if ev["first"] and st < ev["first"]:
                s["verdict"] = "MISS(trader start %s ke PEHLE — late start!)" % ev["first"][:5]
            elif ev["last"] and st > ev["last"]:
                s["verdict"] = "MISS(trader %s pe DOWN ho chuka tha!)" % ev["last"][:5]

    res["signals"] = signals
    res["miss"] = sum(1 for s in signals if str(s["verdict"]).startswith("MISS"))
    res["err"] = sum(1 for s in signals if s["verdict"] == "ERROR")
    res["match"] = sum(1 for s in signals if str(s["verdict"]).startswith("MATCH"))
    res["gated"] = sum(1 for s in signals if "GATED" in str(s["verdict"]))
    return res


def main():
    ap = argparse.ArgumentParser(description="Replay signal fns vs live log (TRAP #108)")
    ap.add_argument("--date", default=ist_today())
    ap.add_argument("--ids", default="", help="comma list — default: auto-discover")
    ap.add_argument("--match-min", type=int, default=6,
                    help="signal→entry max latency minutes (loop ~30s + candle close)")
    args = ap.parse_args()

    ids = ([s.strip() for s in args.ids.split(",") if s.strip()]
           or discover_ids(args.date, BASE_DIR / "logs"))
    print("═" * 74)
    print(f"  SIGNAL REPLAY vs LIVE LOG  |  {args.date}  |  TRAP #108 detector"
          f"  |  {len(ids)} strategies")
    print("═" * 74)

    bad_strategies = 0
    for sid in ids:
        print(f"\n── {sid} " + "─" * (66 - len(sid)))
        r = run_for(sid, args.date, args.match_min)

        if r["status"] == "skip" and not r["signals"]:
            print(f"   ⚪ {r['note']}"); continue
        if r["status"] == "fail":
            print(f"   ⚪ {r['note']}"); bad_strategies += 1; continue
        if r["status"] == "skip":
            print(f"   ⚪ {r['note']}:")
            for s in r["signals"]:
                t = s["time"].strftime("%H:%M") if s["time"] is not None else "--:--"
                spot_txt = " spot=%.1f" % s["spot"] if s.get("spot") else ""
                print(f"      {t}  {str(s['dir']).upper()}{spot_txt}  {s['verdict']}")
            continue

        if not r["signals"] and not r["entries"]:
            print("   🟢 offline: 0 signals, live: 0 entries — dono agree (no-trade day)")
        for s in r["signals"]:
            t = s["time"].strftime("%H:%M") if s["time"] is not None else "--:--"
            icon = "🔴" if str(s["verdict"]).startswith(("MISS", "ERROR")) else "🟢"
            spot_txt = f" spot={s['spot']:.1f}" if s.get("spot") else ""
            print(f"   {icon} {t}  {str(s['dir']).upper():<7}{spot_txt:<16} {s['verdict']}")
        for e in r["extra"]:
            print(f"   🔴 {e['time'][:5]}  EXTRA — live entry, offline signal NAHI: {e['msg'][:70]}")

        if r["miss"] or r["extra"] or r["err"]:
            bad_strategies += 1
            print(f"   ⚠ TRAP #108 suspect: {r['miss']} MISS, {len(r['extra'])} EXTRA,"
                  f" {r['err']} ERROR — live code vs signal code me drift,"
                  f" LIVE JAANE SE PEHLE SULJHAO")
        else:
            print(f"   ✅ {r['match']} match, {r['gated']} gated — live loop == signal code")

    print("\n" + "═" * 74)
    verdict = ("🔴 %d strategy(ies) me drift/data issue" % bad_strategies
               if bad_strategies
               else "✅ sab clean — jo code backtest me tha, wahi live chala")
    print("  VERDICT: " + verdict)
    print("═" * 74)
    sys.exit(min(bad_strategies, 20))


if __name__ == "__main__":
    main()
