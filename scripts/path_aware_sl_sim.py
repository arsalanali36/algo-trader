#!/usr/bin/env python3
"""
Path-aware Legacy-vs-Aggressive SL/Target simulation.

Gemini's earlier `simulation_vps_query.py` used only each trade's peak run-up
(MAX_LTP) and peak run-down (MIN_LTP) — it does NOT know the ORDER in which
they happened, so its "aggressive trailing" figure is an optimistic upper bound
(it silently assumes price ran up first, arming the trail, then came down).

This version replays each trade's ACTUAL 1-min premium bars in chronological
order, so we know whether the SL was hit BEFORE the target / before the trail
armed. Bars: disk `data/trade_ohlc/` first, else Dhan /v2/charts/intraday
(rate-limited). Aggressive SL curve = the REAL live engine
(`risk_gate.target_sl_level`) — not a re-implementation (Rule 6B).

Coverage is honest: contracts Dhan no longer serves (expired weekly index, per
TRAP #100) have no bars → those trades are reported as NO-DATA and fall back to
their actual P&L, and the covered-subset totals are shown separately so the
comparison isn't quietly padded with un-simulated rows.

Run ON THE VPS (needs trades.db + trade_ohlc + Dhan token):
    venv/bin/python scripts/path_aware_sl_sim.py [--from 2026-06-21] [--to 2026-07-14]
                                                 [--no-fetch] [--csv out.csv]
"""
import sys, os, json, csv, argparse, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa: sys.path bootstrap for _core/_data modules
import order_store
import risk_gate
try:
    import dhan_master
except Exception:
    dhan_master = None
try:
    import dhan_rate_limiter as _rl
except Exception:
    _rl = None

CONFIG = os.path.join(ROOT, "data", "config.json")
TRADE_OHLC = os.path.join(ROOT, "data", "trade_ohlc")
INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
# EOD squareoff cap for the --hold-eod ("full day hold beyond actual exit") mode.
EOD_HHMM = "15:15"


def _window(bars, et, xt, hold_eod):
    """Slice the day's bars to the sim window.

    Default: the ACTUAL holding period (entry_time .. actual exit_time) — asks
    'same entry/hold, different exit-rule, where would it exit?'.

    hold_eod=True: entry_time .. EOD (15:15), IGNORING the actual exit — asks
    'if I had NOT exited when I did and held to EOD, where would the legacy /
    aggressive rule have taken me out?'. This is the only place the two modes
    differ; everything downstream (replay, grid, train/OOS) is identical.
    """
    lo = (lambda b: (not et or b[0] >= et))
    if hold_eod:
        return [b for b in bars if lo(b) and b[0] <= EOD_HHMM]
    return [b for b in bars if lo(b) and (not xt or xt == "—" or b[0] <= xt)]

# ── Variations (per-lot ₹; target_sl_level scales by lots internally) ──
# Legacy = fixed target / fixed initial SL, no trailing.
# Aggressive = full live Default-TSL engine (trails, super-trails past the
# aggressive% of target). Same three pairs Gemini compared, so numbers line up.
VARIATIONS = [
    {"name": "V1 (Leg 5k/3k · Aggr 2k/1k)",
     "legacy": {"target": 5000, "sl": 3000},
     "aggr": dict(target_per_lot=2000, initial_sl_per_lot=1000, favour_step=100,
                  sl_move=100, aggressive_pct=30, aggressive_mult=2.0, min_cushion=0)},
    {"name": "V2 (Leg 3k/1.5k · Aggr 4k/1.5k)",
     "legacy": {"target": 3000, "sl": 1500},
     "aggr": dict(target_per_lot=4000, initial_sl_per_lot=1500, favour_step=200,
                  sl_move=200, aggressive_pct=30, aggressive_mult=2.0, min_cushion=0)},
    {"name": "V3 (Leg 8k/3k · Aggr 10k/1k)",
     "legacy": {"target": 8000, "sl": 3000},
     "aggr": dict(target_per_lot=10000, initial_sl_per_lot=1000, favour_step=100,
                  sl_move=100, aggressive_pct=30, aggressive_mult=2.0, min_cushion=0)},
]

_creds_cache = None
def _creds():
    global _creds_cache
    if _creds_cache is None:
        d = json.load(open(CONFIG))
        _creds_cache = (d.get("jwt_token"), str(d.get("client_id")))
    return _creds_cache


def _hhmm_from_epoch(ep):
    return datetime.utcfromtimestamp(int(ep) + 19800).strftime("%H:%M")


# (sec_id, date) -> [(hhmm, o,h,l,c), ...] sorted; [] if none. Cached.
_bar_cache = {}
def load_bars(sec_id, trad_sym, date_str, allow_fetch=True):
    key = (str(sec_id), date_str)
    if key in _bar_cache:
        return _bar_cache[key]
    bars = _load_disk(sec_id, date_str)
    if not bars and allow_fetch and sec_id:
        bars = _fetch_dhan(sec_id, trad_sym, date_str)
    _bar_cache[key] = bars
    return bars


def _load_disk(sec_id, date_str):
    p = os.path.join(TRADE_OHLC, f"{sec_id}_{date_str}.json")
    if not os.path.exists(p):
        return []
    try:
        raw = json.load(open(p))
    except Exception:
        return []
    out = []
    for k, v in raw.items():
        if not isinstance(v, (list, tuple)) or len(v) < 4:
            continue
        try:
            o, h, l, c = float(v[0]), float(v[1]), float(v[2]), float(v[3])
        except Exception:
            continue
        # key is either a raw epoch (str of int) or an "HH:MM"
        if ":" in str(k):
            hhmm = str(k)[:5]
        else:
            try:
                hhmm = _hhmm_from_epoch(k)
            except Exception:
                continue
        out.append((hhmm, o, h, l, c))
    out.sort(key=lambda r: r[0])
    return out


def _fetch_dhan(sec_id, trad_sym, date_str):
    import requests
    root = (trad_sym or "").split("-")[0]
    inst = "OPTIDX" if root in INDEX_UNDERLYINGS else "OPTSTK"
    seg = "NSE_FNO"
    token, cid = _creds()
    if not token:
        return []
    if _rl:
        try:
            _rl.acquire("candle")
        except Exception:
            pass
    try:
        r = requests.post("https://api.dhan.co/v2/charts/intraday",
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"},
                          json={"securityId": str(sec_id), "exchangeSegment": seg,
                                "instrument": inst, "expiryCode": 0,
                                "fromDate": date_str, "toDate": date_str}, timeout=12)
        d = r.json()
    except Exception as e:
        if _rl and "429" in str(e):
            try: _rl.note_429()
            except Exception: pass
        return []
    if not d.get("open"):
        return []
    out = []
    persist = {}
    for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
        out.append((_hhmm_from_epoch(ts), float(o), float(h), float(l), float(c)))
        persist[str(int(ts))] = [round(float(o), 2), round(float(h), 2),
                                 round(float(l), 2), round(float(c), 2)]
    out.sort(key=lambda r: r[0])
    # write-through so a re-run (e.g. the grid optimiser) reads from disk — no
    # repeat Dhan fetch. Don't clobber a daemon-captured file if one exists.
    try:
        p = os.path.join(TRADE_OHLC, f"{sec_id}_{date_str}.json")
        if persist and not os.path.exists(p):
            os.makedirs(TRADE_OHLC, exist_ok=True)
            json.dump(persist, open(p, "w"))
    except Exception:
        pass
    return out


def _mtm(side, entry_px, px, qty):
    """Unrealised ₹ P&L at premium `px` for this position."""
    return (entry_px - px) * qty if side == "SELL" else (px - entry_px) * qty


def replay_legacy(bars, side, entry_px, qty, target_rs, sl_rs, actual_rs):
    """Bar-by-bar. Conservative: within a bar, SL (adverse) is checked before
    target (favourable). Returns (status, rs)."""
    for (_hhmm, o, h, l, c) in bars:
        # adverse extreme = worst premium for us; favourable = best
        if side == "SELL":
            adv = _mtm(side, entry_px, h, qty)   # premium up = loss
            fav = _mtm(side, entry_px, l, qty)   # premium down = profit
        else:
            adv = _mtm(side, entry_px, l, qty)
            fav = _mtm(side, entry_px, h, qty)
        if adv <= -sl_rs:
            return "SL", -sl_rs
        if fav >= target_rs:
            return "TARGET", target_rs
    return "OPEN(actual)", actual_rs


def replay_aggr(bars, side, entry_px, qty, cfg, lots, actual_rs, ride=False):
    """Bar-by-bar with the REAL live trail (risk_gate.target_sl_level).
    Conservative within a bar: check the SL (established from the peak up to the
    PRIOR bar) against this bar's adverse extreme first, then the fixed target,
    then advance the peak with this bar's favourable extreme.

    ride=True: the fixed target is NOT an exit — the position keeps riding and
    the trailing SL is the ONLY thing that closes it (the user's "6000 fixed na
    rakh ke badhta jaye jab tak SL na lage"). The cfg's target is still used for
    the trail's aggressive-phase reference (agg_at = target*pct/100), so the
    trail SHAPE is unchanged — only the target-hit exit is removed."""
    target_rs = cfg["target_per_lot"] * max(1, int(lots))
    peak = 0.0
    for (_hhmm, o, h, l, c) in bars:
        if side == "SELL":
            adv = _mtm(side, entry_px, h, qty)
            fav = _mtm(side, entry_px, l, qty)
        else:
            adv = _mtm(side, entry_px, l, qty)
            fav = _mtm(side, entry_px, h, qty)
        sl_level = risk_gate.target_sl_level(peak, cfg, lots)   # from prior peak
        if adv <= sl_level:
            tag = "TRAIL_SL" if sl_level >= 0 else "SL"
            return tag, round(sl_level, 2)
        if not ride and target_rs > 0 and fav >= target_rs:
            return "TARGET", target_rs
        if fav > peak:
            peak = fav
    return "OPEN(actual)", actual_rs


# ── grid-search parameter space (per-lot ₹) ──
# Legacy = fixed target / fixed SL.
LEG_TARGETS = [1000, 1500, 2000, 2500, 3000, 4000, 5000, 6000, 8000]
LEG_SLS     = [1000, 1500, 2000, 2500, 3000]
# Aggressive = live trail engine; sweep target, initial SL, and step (favour_step
# == sl_move). aggressive_pct/mult held at the deployed defaults.
AGG_TARGETS = [2000, 2500, 3000, 4000, 5000, 6000]
AGG_INIT_SL = [1000, 1500, 2000, 2500]
AGG_STEPS   = [100, 200, 300]
AGG_PCT, AGG_MULT = 30, 2.0


def _agg_cfg(target, init_sl, step):
    return dict(target_per_lot=target, initial_sl_per_lot=init_sl, favour_step=step,
                sl_move=step, aggressive_pct=AGG_PCT, aggressive_mult=AGG_MULT, min_cushion=0)


def _prep_covered(trades, allow_fetch, hold_eod=False):
    """Load + window bars once per trade; return list of covered replay inputs
    tagged with date (for the train/OOS split)."""
    cov = []
    for i, t in enumerate(trades):
        sec_id = t.get("sec_id"); trad_sym = t.get("sym") or ""
        date_str = t.get("entry_date") or ""
        et, xt = t.get("entry_time", ""), t.get("exit_time", "")
        bars = load_bars(sec_id, trad_sym, date_str, allow_fetch=allow_fetch)
        win = _window(bars, et, xt, hold_eod)
        if not win:
            continue
        lots = 1
        if dhan_master and sec_id:
            try:
                ls = dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and t["qty"]:
                    lots = max(1, round(t["qty"] / float(ls)))
            except Exception:
                pass
        # no-rule-fired fallback: in hold_eod mode the natural exit is the EOD
        # (last-bar) close, NOT the actual early exit; otherwise it's the actual.
        fb = float(t["pnl"])
        if hold_eod and win:
            fb = _mtm(t["entry"], t["entry_price"], win[-1][4], t["qty"])
        cov.append({"win": win, "side": t["entry"], "ep": t["entry_price"],
                    "qty": t["qty"], "lots": lots, "actual": float(t["pnl"]),
                    "fallback": fb, "date": date_str})
        if (i + 1) % 50 == 0:
            print(f"  bars {i+1}/{len(trades)}  covered={len(cov)}", flush=True)
    return cov


def optimize(trades, allow_fetch, hold_eod=False):
    cov = _prep_covered(trades, allow_fetch, hold_eod=hold_eod)
    n = len(cov)
    print(f"\nCovered trades for optimisation: {n}", flush=True)
    if n < 20:
        print("Too few covered trades to optimise meaningfully."); return
    # train/OOS split by DATE (earlier = train), so 'best' is picked without
    # seeing the out-of-sample tail — the overfit guard.
    cov.sort(key=lambda c: c["date"])
    cut = int(n * 0.65)
    cut_date = cov[cut]["date"]
    train = cov[:cut]; oos = cov[cut:]
    print(f"Train {len(train)} (..{train[-1]['date']})  |  OOS {len(oos)} ({oos[0]['date']}..)")

    def eval_leg(subset, tgt, sl):
        return sum(replay_legacy(c["win"], c["side"], c["ep"], c["qty"],
                                 tgt * c["lots"], sl * c["lots"], c["fallback"])[1] for c in subset)

    def eval_agg(subset, tgt, isl, step):
        return sum(replay_aggr(c["win"], c["side"], c["ep"], c["qty"],
                               _agg_cfg(tgt, isl, step), c["lots"], c["fallback"])[1] for c in subset)

    # ── LEGACY grid ──
    leg = []
    for tgt in LEG_TARGETS:
        for sl in LEG_SLS:
            leg.append({"p": f"T{tgt}/SL{sl}", "tgt": tgt, "sl": sl,
                        "train": eval_leg(train, tgt, sl), "oos": eval_leg(oos, tgt, sl),
                        "all": eval_leg(cov, tgt, sl)})
    # ── AGGRESSIVE grid ──
    agg = []
    for tgt in AGG_TARGETS:
        for isl in AGG_INIT_SL:
            for step in AGG_STEPS:
                agg.append({"p": f"T{tgt}/SL{isl}/step{step}", "tgt": tgt, "isl": isl, "step": step,
                            "train": eval_agg(train, tgt, isl, step),
                            "oos": eval_agg(oos, tgt, isl, step),
                            "all": eval_agg(cov, tgt, isl, step)})

    def _inr(x): return f"{round(x):,}"
    base_train = sum(c["actual"] for c in train)
    base_oos = sum(c["actual"] for c in oos)
    base_all = sum(c["actual"] for c in cov)
    print(f"\nActual net — train ₹{_inr(base_train)} | OOS ₹{_inr(base_oos)} | all ₹{_inr(base_all)}")

    def report(name, grid):
        print(f"\n{'='*72}\n{name}\n{'='*72}")
        # ROBUST ranking = min(train, OOS): rewards combos that hold up on BOTH
        # halves, not one lucky half (TRAP #103 — OOS-blind ranking overfits).
        by_min = sorted(grid, key=lambda r: min(r["train"], r["oos"]), reverse=True)
        print("Top by MIN(train,OOS) — most robust (both halves must hold):")
        print(f"  {'params':22s} {'train':>9} {'oos':>9} {'all':>9} {'min':>9}")
        for r in by_min[:8]:
            print(f"  {r['p']:22s} {_inr(r['train']):>9} {_inr(r['oos']):>9} "
                  f"{_inr(r['all']):>9} {_inr(min(r['train'],r['oos'])):>9}")
        best = by_min[0]
        # in-sample-best (what pure 'best number' maximisation would pick — overfit)
        by_all = sorted(grid, key=lambda r: r["all"], reverse=True)[0]
        print(f"\n  → ROBUST pick (max min-of-both-halves): {best['p']}  "
              f"train ₹{_inr(best['train'])} / OOS ₹{_inr(best['oos'])} / all ₹{_inr(best['all'])}")
        print(f"  → In-sample best (all, OVERFIT-prone): {by_all['p']}  all ₹{_inr(by_all['all'])} "
              f"(train ₹{_inr(by_all['train'])} / OOS ₹{_inr(by_all['oos'])})")
        return best, by_all

    lb = report("LEGACY (fixed target / SL)", leg)
    ab = report("AGGRESSIVE (live trail engine)", agg)
    return lb, ab


# ── RIDE cushion sweep ──────────────────────────────────────────────────────
# Ride mode (no target cap) with cushion=0 makes the trailing SL hug the peak,
# so any intrabar wick past the peak stops the position out — great in flat
# demos, whipsaw-prone on real wicky 1-min bars. A larger min_cushion keeps the
# SL further below the peak so noise doesn't cut a runner. This sweep answers
# 'which cushion rides winners without dying to noise?' on the REAL bars.
RIDE_BASE = dict(target_per_lot=6000, initial_sl_per_lot=2500, favour_step=100,
                 sl_move=100, aggressive_pct=30, aggressive_mult=2.0)
RIDE_CUSHIONS = [0, 250, 500, 1000, 1500, 2000, 3000]   # per-lot ₹


def _ride_sweep_report(cov, base, cushions):
    """cov = list of {win,side,ep,qty,lots,fallback,actual,date}. Prints a
    per-cushion train/OOS/all table for RIDE mode + the capped baseline, ranked
    by MIN(train,OOS) (TRAP #103 robust ranking)."""
    n = len(cov)
    if n < 20:
        print(f"Too few covered trades ({n}) to sweep meaningfully."); return
    cov.sort(key=lambda c: c["date"])
    cut = int(n * 0.65)
    train, oos = cov[:cut], cov[cut:]

    def eval_ride(subset, cush):
        cfg = dict(base, min_cushion=cush)
        return sum(replay_aggr(c["win"], c["side"], c["ep"], c["qty"], cfg,
                               c["lots"], c["fallback"], ride=True)[1] for c in subset)

    def eval_cap(subset):   # current live: fixed target exit, base cushion 0
        cfg = dict(base, min_cushion=0)
        return sum(replay_aggr(c["win"], c["side"], c["ep"], c["qty"], cfg,
                               c["lots"], c["fallback"], ride=False)[1] for c in subset)

    def _inr(x): return f"{round(x):,}"
    print(f"\n{'='*72}\nRIDE cushion sweep (no target cap; trail = only exit)\n{'='*72}")
    print(f"Covered {n}  |  Train {len(train)} (..{train[-1]['date']})  |  "
          f"OOS {len(oos)} ({oos[0]['date']}..)")
    print(f"Actual net — train ₹{_inr(sum(c['actual'] for c in train))} | "
          f"OOS ₹{_inr(sum(c['actual'] for c in oos))} | "
          f"all ₹{_inr(sum(c['actual'] for c in cov))}")
    cap = {"train": eval_cap(train), "oos": eval_cap(oos), "all": eval_cap(cov)}
    print(f"\nAggr CAP (current live, target 6000): "
          f"train ₹{_inr(cap['train'])} | OOS ₹{_inr(cap['oos'])} | all ₹{_inr(cap['all'])}")

    rows = []
    for cu in cushions:
        rows.append({"cush": cu, "train": eval_ride(train, cu),
                     "oos": eval_ride(oos, cu), "all": eval_ride(cov, cu)})
    rows.sort(key=lambda r: min(r["train"], r["oos"]), reverse=True)
    print(f"\nRIDE by MIN(train,OOS) — robust (both halves hold):")
    print(f"  {'cushion/lot':>12} {'train':>10} {'oos':>10} {'all':>10} {'min':>10}")
    for r in rows:
        print(f"  ₹{r['cush']:>10,} {_inr(r['train']):>10} {_inr(r['oos']):>10} "
              f"{_inr(r['all']):>10} {_inr(min(r['train'],r['oos'])):>10}")
    best = rows[0]
    vs_cap = best["all"] - cap["all"]
    print(f"\n  → ROBUST ride cushion: ₹{best['cush']:,}/lot  "
          f"(all ₹{_inr(best['all'])}, {'+' if vs_cap>=0 else ''}{_inr(vs_cap)} vs cap)")
    print(f"  → In-sample best: ₹{max(rows, key=lambda r: r['all'])['cush']:,}/lot "
          f"(all ₹{_inr(max(r['all'] for r in rows))}) — OVERFIT-prone")
    return best, cap


def ride_sweep(trades, allow_fetch, hold_eod=False, base=None, cushions=None):
    cov = _prep_covered(trades, allow_fetch, hold_eod=hold_eod)
    print(f"\nCovered trades for ride sweep: {len(cov)}", flush=True)
    return _ride_sweep_report(cov, base or RIDE_BASE, cushions or RIDE_CUSHIONS)


def main():
    ap = argparse.ArgumentParser()
    ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    ap.add_argument("--from", dest="dfrom", default="2026-06-21")
    ap.add_argument("--to", dest="dto", default=ist.strftime("%Y-%m-%d"))
    ap.add_argument("--no-fetch", action="store_true", help="disk bars only, no Dhan calls")
    ap.add_argument("--optimize", action="store_true", help="grid-search best legacy + aggressive params")
    ap.add_argument("--hold-eod", action="store_true",
                    help="hold each trade to EOD (15:15) beyond its actual exit, "
                         "then apply the legacy/aggressive rule")
    ap.add_argument("--ride-sweep", action="store_true",
                    help="RIDE mode (no target cap): sweep min_cushion to find the "
                         "one that rides winners without whipsawing on noise. "
                         "Implies hold-to-EOD (ride needs room past the old target).")
    ap.add_argument("--csv", default=os.path.join(ROOT, "data", "path_aware_sl_sim.csv"))
    args = ap.parse_args()

    # ride only makes sense holding past the fixed target → force hold-eod for it.
    if args.ride_sweep:
        args.hold_eod = True
    mode_note = "HOLD-TO-EOD (beyond actual exit)" if args.hold_eod else "actual holding window"
    print(f"Window mode: {mode_note}", flush=True)

    if args.ride_sweep:
        trades = order_store.trades_for_range(args.dfrom, args.dto)["details"]
        trades = [t for t in trades if t.get("pnl") is not None]
        print(f"Completed trades {args.dfrom}..{args.dto}: {len(trades)}", flush=True)
        ride_sweep(trades, allow_fetch=not args.no_fetch, hold_eod=True)
        return

    if args.optimize:
        trades = order_store.trades_for_range(args.dfrom, args.dto)["details"]
        trades = [t for t in trades if t.get("pnl") is not None]
        print(f"Completed trades {args.dfrom}..{args.dto}: {len(trades)}", flush=True)
        optimize(trades, allow_fetch=not args.no_fetch, hold_eod=args.hold_eod)
        return

    trades = order_store.trades_for_range(args.dfrom, args.dto)["details"]
    trades = [t for t in trades if t.get("pnl") is not None]
    print(f"Completed trades {args.dfrom}..{args.dto}: {len(trades)}", flush=True)

    rows = []
    n_cov = 0
    tot_actual = 0.0
    tot = {v["name"]: {"legacy": 0.0, "aggr": 0.0, "ride": 0.0} for v in VARIATIONS}
    tot_cov = {v["name"]: {"legacy": 0.0, "aggr": 0.0, "ride": 0.0} for v in VARIATIONS}

    for i, t in enumerate(trades):
        side = t["entry"]
        ep = t["entry_price"]
        qty = t["qty"]
        actual = float(t["pnl"])
        tot_actual += actual
        sec_id = t.get("sec_id")
        trad_sym = t.get("sym") or ""
        date_str = t.get("entry_date") or ""
        et, xt = t.get("entry_time", ""), t.get("exit_time", "")

        lots = 1
        if dhan_master and sec_id:
            try:
                ls = dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and qty:
                    lots = max(1, round(qty / float(ls)))
            except Exception:
                pass

        bars = load_bars(sec_id, trad_sym, date_str, allow_fetch=not args.no_fetch)
        # default: actual holding period (entry..exit). --hold-eod: entry..15:15.
        win = _window(bars, et, xt, args.hold_eod)
        covered = len(win) >= 1
        if covered:
            n_cov += 1

        # no-rule-fired fallback (see _prep_covered): EOD close in hold_eod mode.
        fb = actual
        if args.hold_eod and win:
            fb = _mtm(side, ep, win[-1][4], qty)

        row = {"date": date_str, "time": f"{et}->{xt}", "sym": trad_sym,
               "side": side, "qty": qty, "lots": lots, "actual": round(actual, 2),
               "eod_fallback": round(fb, 2), "bars": len(win), "covered": covered}
        for v in VARIATIONS:
            if covered:
                ls_stat, ls_rs = replay_legacy(win, side, ep, qty,
                                               v["legacy"]["target"] * lots,
                                               v["legacy"]["sl"] * lots, fb)
                ag_stat, ag_rs = replay_aggr(win, side, ep, qty, v["aggr"], lots, fb)
                # RIDE: same aggressive trail, but NO target cap — rides until SL.
                rd_stat, rd_rs = replay_aggr(win, side, ep, qty, v["aggr"], lots, fb, ride=True)
                tot_cov[v["name"]]["legacy"] += ls_rs
                tot_cov[v["name"]]["aggr"] += ag_rs
                tot_cov[v["name"]]["ride"] += rd_rs
            else:
                ls_stat, ls_rs = "NO_DATA", actual
                ag_stat, ag_rs = "NO_DATA", actual
                rd_stat, rd_rs = "NO_DATA", actual
            tot[v["name"]]["legacy"] += ls_rs
            tot[v["name"]]["aggr"] += ag_rs
            tot[v["name"]]["ride"] += rd_rs
            row[v["name"] + " Leg"] = ls_stat
            row[v["name"] + " Leg₹"] = round(ls_rs, 2)
            row[v["name"] + " Aggr"] = ag_stat
            row[v["name"] + " Aggr₹"] = round(ag_rs, 2)
            row[v["name"] + " Ride"] = rd_stat
            row[v["name"] + " Ride₹"] = round(rd_rs, 2)
        rows.append(row)
        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(trades)}  covered={n_cov}", flush=True)

    # ── write CSV ──
    if rows:
        cols = list(rows[0].keys())
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)

    def _inr(x): return f"{round(x):,}"
    print("\n" + "=" * 68)
    print(f"COVERAGE: {n_cov}/{len(trades)} trades had real 1-min bars "
          f"({100*n_cov/max(1,len(trades)):.0f}%). "
          f"Rest = NO-DATA (fell back to actual).")
    print("=" * 68)
    print(f"\nActual net (all {len(trades)}): ₹{_inr(tot_actual)}")

    print("\n── COVERED SUBSET ONLY (apples-to-apples, path-aware) ──")
    cov_actual = sum(r["actual"] for r in rows if r["covered"])
    print(f"  Actual net (covered {n_cov}):  ₹{_inr(cov_actual)}")
    print(f"  {'variation':34s} {'Legacy':>12} {'Aggr(cap)':>12} {'Aggr-RIDE':>12}")
    for v in VARIATIONS:
        L = tot_cov[v["name"]]["legacy"]; A = tot_cov[v["name"]]["aggr"]; R = tot_cov[v["name"]]["ride"]
        print(f"  {v['name']:34s} ₹{_inr(L):>11} ₹{_inr(A):>11} ₹{_inr(R):>11}")

    print("\n── FULL SET (NO-DATA rows carry their actual P&L) ──")
    print(f"  {'variation':34s} {'Legacy':>12} {'Aggr(cap)':>12} {'Aggr-RIDE':>12}")
    for v in VARIATIONS:
        L = tot[v["name"]]["legacy"]; A = tot[v["name"]]["aggr"]; R = tot[v["name"]]["ride"]
        print(f"  {v['name']:34s} ₹{_inr(L):>11} ₹{_inr(A):>11} ₹{_inr(R):>11}")
    print("\n  Aggr(cap) = fixed target exit (current live).  "
          "Aggr-RIDE = no target cap, rides until trailing SL.")
    print(f"\nCSV: {args.csv}")


if __name__ == "__main__":
    main()
