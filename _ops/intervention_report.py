"""
intervention_report.py — "manual cut" counterfactual: for each position the USER
closed by hand, what would the STRATEGY's own exit have given? Quantifies whether
manual intervention was net + or − for the day (live AND paper).

DISPLAY-ONLY. Reads order_store + the captured 1-min premium bars (data/trade_ohlc/)
+ the equity candle lake for the RSI-midline replay. Touches NO order/risk path.

A "manual cut" = an exit leg the STRATEGY did not decide:
  - source == 'manual', or tags include MANUAL_EXIT_BROKER / MANUAL_CLOSE
  - status externally_closed (broker showed flat — user closed on Kite)
  - a broker-mirror exit (source broker_reconcile/broker_sync, BROKER_MIRROR) with
    no strategy-exit tag — the app didn't place it → external = manual
Strategy exits (RSI_MIDLINE_EXIT / SL_HIT / *_TARGET / *_SQUAREOFF / GROUP_* /
ATR_TRAILING / DEFAULT_TSL_* / EXPIRY_*) are NOT cuts.

Counterfactual per cut = the strategy's own exit, priced on the option's REAL
1-min premium bars:
  - RSI strategies (rsi_v1*): RSI-50 midline on the underlying's 2m candles
    (equity lake; falls back to the SL/TP/EOD bound + label if candles unavailable).
  - everything else: the trade's own RMS SL/target (from its entry tags) + 3:15 EOD
    + expiry-worthless — whichever fires first.

impact = actual − counterfactual  (per cut). >0 = your cut HELPED (you did better
than the strategy would have); <0 = your cut HURT.
"""
import os
import sys
import json
import csv
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
# Standalone/EOD-timer runs (python _ops/intervention_report.py --all) only have
# _ops + PROJECT on the path — but dhan_master lives in _data/. Without this, the
# _lot() lookup's `import dhan_master` silently fails → lot 0 → the SL is dropped
# from the counterfactual (INFY 2026-08-03: cf ran to 3:15 -6860 instead of the
# real RMS-SL -7500). _paths (root) puts every project folder on sys.path.
try:
    import _paths  # noqa: F401
except Exception:
    pass

STORE_DIR = os.path.join(PROJECT, "data", "intervention")

_STRAT_EXIT = ("RSI_MIDLINE_EXIT", "SL_HIT", "EOD_315_SQUAREOFF", "EOD_SQUAREOFF",
               "ATR_TRAILING", "GROUP_SL", "GROUP_TARGET", "DEFAULT_TSL",
               "EXPIRY_", "_TARGET", "STRADDLE_TARGET", "STRADDLE_SL", "RMS_")
_MANUAL_TAG = ("MANUAL_EXIT_BROKER", "MANUAL_CLOSE")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ist_today():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _slabel(strategy):
    """Registry display name ("NN.MM - Name") for a config key — the SAME name the
    user reads in the strategy registry (TRAP #132 single labeller). Raw fallback
    only if the registry can't load."""
    try:
        import strategy_registry as sr
        return sr.label(strategy)
    except Exception:
        return str(strategy or "")


def _rows_for(date, mode=None):
    """Raw order rows for a date (NO dead-filter — we WANT externally_closed cuts)."""
    import sqlite3
    db = os.path.join(PROJECT, "data", "trades.db")
    if not os.path.exists(db):
        return []
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    q = "SELECT id,ts,strategy,mode,source,side,symbol,trad_sym,sec_id,qty,price,status,tags FROM orders WHERE date=?"
    args = [date]
    if mode in ("live", "paper"):
        q += " AND mode=?"
        args.append(mode)
    out = [dict(r) for r in c.execute(q + " ORDER BY id", args)]
    c.close()
    return out


def _tags(r):
    try:
        t = json.loads(r.get("tags") or "[]")
        return t if isinstance(t, list) else []
    except Exception:
        return []


def _is_entry(r):
    """Opening leg = carries the entry SL/target stamp (SL_TYPE/SL_VAL) or an
    entry marker. Exits carry an exit reason instead."""
    t = " ".join(_tags(r))
    return ("SL_TYPE" in t or "SL_VAL" in t) and not any(x in t for x in _MANUAL_TAG)


def _classify_exit(exit_row, entry_row):
    """('cut'|'strategy'|'unknown', reason_label)."""
    t = _tags(exit_row)
    tj = " ".join(t)
    src = str(exit_row.get("source") or "")
    st = str(exit_row.get("status") or "").lower()
    if any(m in tj for m in _MANUAL_TAG) or src == "manual":
        return "cut", "manual close"
    if any(s in tj for s in _STRAT_EXIT):
        # a genuine strategy exit reason present → strategy, even if broker-mirrored
        return "strategy", next((s for s in _STRAT_EXIT if s in tj), "strategy")
    if st == "externally_closed" or str(entry_row.get("status") or "").lower() == "externally_closed":
        return "cut", "external close (broker flat)"
    if src in ("broker_reconcile", "broker_sync") or "BROKER_MIRROR" in tj:
        return "cut", "external close (broker mirror)"
    return "unknown", "unknown exit"


def _pairs(rows):
    """FIFO-pair opposite legs per (strategy, sec_id). Returns list of
    {entry, exit, strategy, sec_id, symbol}. Handles BUY-open (RSI) and SELL-open
    (range/straddle). Legs with side 'BUY'/'SELL' only."""
    from collections import defaultdict
    byk = defaultdict(list)
    for r in rows:
        if r.get("side") in ("BUY", "SELL"):
            byk[(r.get("strategy"), str(r.get("sec_id")))].append(r)
    pairs = []
    for (strat, sid), legs in byk.items():
        legs.sort(key=lambda x: x["id"])
        # entry = first leg that looks like an opening leg; else first leg
        opens = [l for l in legs if _is_entry(l)]
        entry = opens[0] if opens else legs[0]
        # ALL opposite-side legs after the entry, FIFO-capped at entry qty — a
        # position can be closed in parts (INFY 2026-08-03: 1200 entry, 800 cut at
        # 12:41 + 400 at 13:32). Using only the first leg with the ENTRY qty (the
        # old bug) mis-priced the P&L and dropped the later leg entirely.
        opp = [l for l in legs if l["id"] > entry["id"] and l["side"] != entry["side"]]
        if not opp:
            continue
        eq = int(entry.get("qty") or 0)
        exits, used = [], 0
        for l in opp:
            if eq and used >= eq:
                break
            exits.append(l)
            used += int(l.get("qty") or 0)
        pairs.append({"entry": entry, "exit": exits[0], "exits": exits, "strategy": strat,
                      "sec_id": sid, "symbol": entry.get("symbol")})
    return pairs


def _exit_summary(pair):
    """(avg_exit_px, total_closed_qty, first_exit_hm, legs) — qty-weighted across
    all exit legs, FIFO-capped at entry qty. legs = [{hm, price, qty}] (for the
    chart's multiple manual-cut markers)."""
    e = pair["entry"]
    eq = int(e.get("qty") or 0)
    legs = pair.get("exits") or [pair["exit"]]
    tot_q, wsum, out = 0, 0.0, []
    for x in legs:
        lq = int(x.get("qty") or 0)
        if eq:
            lq = min(lq, eq - tot_q)
            if lq <= 0:
                break
        px = _f(x["price"]) or 0.0
        wsum += px * lq
        tot_q += lq
        out.append({"hm": (x.get("ts") or "")[11:16], "price": round(px, 2), "qty": lq})
    avg = round(wsum / tot_q, 2) if tot_q else None
    first_hm = out[0]["hm"] if out else ((pair["exit"].get("ts") or "")[11:16])
    return avg, tot_q, first_hm, out


# All epochs are normalised to IST-as-UTC (utcfromtimestamp → IST wall-clock), the
# same convention the OptionChain lake / entry timestamps use. trade_ohlc is stored
# in REAL UTC (Dhan intraday), so add the IST offset; the lake is already IST-as-UTC.
_IST = 19800  # 5h30m


# ── premium bars + lot ───────────────────────────────────────────────────────
def _bars(sec_id, date):
    p = os.path.join(PROJECT, "data", "trade_ohlc", f"{sec_id}_{date}.json")
    if not os.path.exists(p):
        return []
    try:
        d = json.load(open(p))
    except Exception:
        return []
    out = []
    for k in sorted(d.keys(), key=lambda x: int(x)):
        v = d[k]
        vals = v if isinstance(v, list) else [v.get("open"), v.get("high"), v.get("low"), v.get("close")]
        try:
            out.append((int(k) + _IST, float(vals[1]), float(vals[2]), float(vals[3])))  # real-UTC → IST-as-UTC
        except (TypeError, ValueError):
            continue
    return out


def _lake_bars(symbol, trad_sym, date):
    """Premium bars for a NIFTY/BANKNIFTY option from the OptionChain lake
    (option_curves) when data/trade_ohlc/ didn't capture it (index options live in
    the chain lake, not always in trade_ohlc). Parses strike+CE/PE from trad_sym."""
    su = str(symbol or "").upper()
    if su not in ("NIFTY", "BANKNIFTY"):
        return []
    ts = str(trad_sym or "")
    if not (ts.endswith("CE") or ts.endswith("PE")):
        return []
    ot = ts[-2:]
    try:
        strike = int(float(ts.split("-")[-2]))
    except Exception:
        return []
    try:
        import option_curves as oc
        r = oc.strike_series(su, date, None, strike, ot)   # expiry=None → day's nearest
        out = []
        for p in (r.get("points") or []):
            v = _f(p.get("ltp"))
            ep = p.get("t")
            if v is not None and ep is not None:
                out.append((int(ep), v, v, v))   # per-minute ltp as high/low/close
        return out
    except Exception:
        return []


_LOT_CACHE = {}


def chart_bars(sec_id, date, symbol=None, trad_sym=None):
    """Full-OHLC premium bars for the intervention chart popup: [{t,o,h,l,c}] with
    t = IST-as-UTC epoch (same convention as entry/cut/cf marker times). trade_ohlc
    (captured 1-min) first, then the OptionChain lake (index options, ltp-as-flat)."""
    p = os.path.join(PROJECT, "data", "trade_ohlc", f"{sec_id}_{date}.json")
    out = []
    if os.path.exists(p):
        try:
            d = json.load(open(p))
            for k in sorted(d.keys(), key=lambda x: int(x)):
                v = d[k]
                if isinstance(v, list):
                    vals = (list(v) + [None, None, None, None])[:4]
                    o, h, l, cl = vals
                else:
                    o, h, l, cl = v.get("open"), v.get("high"), v.get("low"), v.get("close")
                try:
                    out.append({"t": int(k) + _IST, "o": float(o), "h": float(h),
                                "l": float(l), "c": float(cl)})
                except (TypeError, ValueError):
                    continue
        except Exception:
            pass
    if out:
        return out
    for (ep, h, l, cl) in _lake_bars(symbol, trad_sym, date):   # index-option ltp fallback
        out.append({"t": ep, "o": cl, "h": h, "l": l, "c": cl})
    return out


def _lot(sec_id):
    """Lot size for a sec_id (memoised). CRITICAL: in a standalone/timer run (the
    EOD `--all`) dhan_master's scrip cache isn't warm → get_lot_size_by_sec_id
    returns 0 → _sl_tp_from_tags yields no SL/TP → the counterfactual silently
    skips the strategy's SL (INFY 2026-08-03: with a warm cache lot=400 → cf RMS-SL
    @ -7500; without it, cf wrongly ran to 3:15). So warm the cache and retry once
    on a 0 before giving up."""
    sid = str(sec_id)
    if sid in _LOT_CACHE:
        return _LOT_CACHE[sid]
    v = 0
    try:
        import dhan_master as dm
        v = int(float(dm.get_lot_size_by_sec_id(sid) or 0))
        if not v:
            try:
                dm.build_cache()
                v = int(float(dm.get_lot_size_by_sec_id(sid) or 0))
            except Exception:
                pass
    except Exception:
        v = 0
    _LOT_CACHE[sid] = v
    return v


# TF_MAP mirrors strategies/live/01_rsi_v1.py — the live RSI trader resolves its
# candle interval through EXACTLY this map. config "2m" has NO entry here, so it
# FALLS BACK to 5 (runs 5-min), a known mislabel. The intervention report used to
# hardcode a 2-min resample → it replayed a timeframe the live 5m strategy never
# computes (INFY 2026-08-03: 2m RSI dipped to 49 for one candle at 11:50 = noise
# → fake "+460 exit"; 5m RSI stayed 64-79 all day → the strategy's RSI-exit never
# fired). Replay MUST use the strategy's real runtime interval.
_TF_MAP = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}


def _strat_runtime(strategy):
    """(interval_min, rsi_period, rsi_exit) — the strategy's ACTUAL runtime RSI
    params (same TF_MAP fallback the live trader uses). Default 5m/14/50."""
    try:
        cfg = json.load(open(os.path.join(PROJECT, "nifty_config.json")))
        s = cfg.get(str(strategy)) or {}
        return (_TF_MAP.get(s.get("timeframe", "5m"), 5),
                int(s.get("rsi_period", 14)), float(s.get("rsi_exit", 50)))
    except Exception:
        return 5, 14, 50


def _hm(ep):
    return datetime.datetime.utcfromtimestamp(ep).strftime("%H:%M")


# ── RSI-midline exit (underlying) ────────────────────────────────────────────
def _equity_candles(symbol, date, interval_min=5):
    """Underlying close series resampled to the strategy's runtime interval (list of
    (dt_naive_IST, close)). Lake first (post-close, no Dhan call); falls back to a
    single Dhan intraday fetch (display engine, rate-limited, best-effort) so the
    RSI-midline replay works for an on-demand run before the lake is filled. None
    if both fail."""
    import pandas as pd
    rule = f"{int(interval_min)}min"
    for base in (os.path.join(os.path.dirname(PROJECT), "._TRADING DATA", "Equity", symbol),
                 os.path.join(PROJECT, "_TRADING_DATA", "Equity", symbol)):
        p = os.path.join(base, f"{symbol}_{date}.csv")
        if os.path.exists(p):
            try:
                df = pd.read_csv(p)
                tcol = next((c for c in df.columns if c.lower() in ("datetime", "date", "timestamp", "time")), None)
                ccol = next((c for c in df.columns if c.lower() == "close"), None)
                if tcol and ccol:
                    df["dt"] = pd.to_datetime(df[tcol])
                    s = df.set_index("dt")[ccol].resample(rule).last().dropna()
                    if len(s):
                        return list(s.items())
            except Exception:
                pass
    return _fetch_equity(symbol, date, interval_min)


def _fetch_equity(symbol, date, interval_min=5):
    """One Dhan intraday call → close series resampled to interval_min (IST index).
    Best-effort fallback for _equity_candles. Display-only, rate-limited. None on
    any failure."""
    try:
        import pandas as pd, requests, dhan_master as dm
        cfgp = os.path.join(PROJECT, "data", "config.json")
        cfg = json.load(open(cfgp))
        token = cfg.get("jwt_token") or cfg.get("access_token")
        cid = str(cfg.get("client_id") or cfg.get("dhanClientId") or "")
        if not token:
            return None
        sid = dm.get_equity_info(symbol)[0]
        try:
            import dhan_rate_limiter as _drl
            _drl.set_context("Intervention:Candles")
            _drl.acquire("candle", timeout=3.0)
        except Exception:
            pass
        b = {"securityId": str(sid), "exchangeSegment": "NSE_EQ", "instrument": "EQUITY",
             "interval": "1", "fromDate": date, "toDate": date}
        r = requests.post("https://api.dhan.co/v2/charts/intraday", json=b,
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"}, timeout=8)
        d = r.json()
        if "timestamp" not in d:
            return None
        df = pd.DataFrame({"t": d["timestamp"], "c": d["close"]})
        df["dt"] = pd.to_datetime(df["t"], unit="s") + pd.Timedelta(hours=5, minutes=30)
        s = df.set_index("dt")["c"].resample(f"{int(interval_min)}min").last().dropna()
        return list(s.items()) if len(s) else None
    except Exception:
        return None


def _midline_exit_time(symbol, entry_ts, pos, date, interval_min=5, exit_level=50, period=14):
    """First candle (at the strategy's runtime interval) AFTER entry where RSI
    crosses the exit level (pos +1 long CE: RSI>=exit; pos -1 short PE: RSI<=exit).
    Returns (exit_dt_epoch, 'RSI50') or None if candles unavailable / no cross."""
    ser = _equity_candles(symbol, date, interval_min)
    if not ser:
        return None
    try:
        import pandas as pd
        from _CHARTING.indicators import wilder_rsi
        idx = [t for t, _ in ser]
        close = pd.Series([c for _, c in ser], index=idx)
        rsi = wilder_rsi(close, period)
        ent = pd.to_datetime(entry_ts[:19])
        for t, rv in rsi.items():
            if t < ent or pd.isna(rv):
                continue
            if (pos == 1 and rv >= exit_level) or (pos == -1 and rv <= exit_level):
                ep = int(t.replace(tzinfo=datetime.timezone.utc).timestamp())
                return ep, "RSI50"
    except Exception:
        return None
    return None


def _price_at(bars, target_ep):
    if not bars:
        return None
    best, bd = None, 1e18
    for ep, h, l, cl in bars:
        if abs(ep - target_ep) < bd:
            bd, best = abs(ep - target_ep), cl
    return best


# ── counterfactual for one cut ───────────────────────────────────────────────
def _sl_tp_from_tags(entry_row, lot):
    """(sl_pts, tp_pts) premium-point bounds from the entry's RMS tags (per-lot ₹).
    None if not set."""
    t = _tags(entry_row)
    sl_val = tp_val = None
    for x in t:
        if x.startswith("SL_VAL:"):
            sl_val = _f(x.split(":", 1)[1])
        elif x.startswith("TP_VAL:"):
            tp_val = _f(x.split(":", 1)[1])
    sl_pts = (sl_val / lot) if (sl_val and lot) else None
    tp_pts = (tp_val / lot) if (tp_val and lot) else None
    return sl_pts, tp_pts


def _counterfactual(pair, date):
    """Return {cf_price, cf_pnl, method, exit_hm} or method='no-data'."""
    e, x = pair["entry"], pair["exit"]
    side = e["side"]                          # BUY-open (long) or SELL-open (short)
    entry = _f(e["price"]) or 0.0
    # counterfactual on the CLOSED qty (so actual vs cf are apples-to-apples for a
    # partially-cut position); falls back to entry qty if summary is empty.
    _, _cq, _, _ = _exit_summary(pair)
    qty = _cq or int(e["qty"] or 0)
    sid = pair["sec_id"]
    strat = str(pair["strategy"] or "")
    bars = _bars(sid, date) or _lake_bars(pair.get("symbol"), e.get("trad_sym"), date)
    if not bars:
        return {"method": "no-data"}
    lot = _lot(sid)
    ent_ep = int(datetime.datetime.strptime(e["ts"][:19], "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=datetime.timezone.utc).timestamp())
    after = [b for b in bars if b[0] >= ent_ep - 60]
    if not after:
        return {"method": "no-data"}
    sl_pts, tp_pts = _sl_tp_from_tags(e, lot)

    # RSI-midline (rsi strategies) — the strategy's PRIMARY exit, replayed on the
    # strategy's ACTUAL runtime interval (NOT hardcoded 2m — see _strat_runtime).
    rsi_exit = None
    if strat.lower().startswith("rsi") and pair.get("symbol"):
        pos = 1 if str(e["trad_sym"]).endswith("CE") else -1
        _iv, _per, _lvl = _strat_runtime(strat)
        rsi_exit = _midline_exit_time(pair["symbol"], e["ts"], pos, date,
                                      interval_min=_iv, exit_level=_lvl, period=_per)

    exit_ep = exit_px = None
    method = ""
    for (ep, h, l, cl) in after:
        # SL/TP bounds (from entry tags). long: SL=price falls; short: SL=price rises.
        if side == "BUY":
            if sl_pts and l <= entry - sl_pts:
                exit_ep, exit_px, method = ep, entry - sl_pts, "RMS SL"; break
            if tp_pts and h >= entry + tp_pts:
                exit_ep, exit_px, method = ep, entry + tp_pts, "RMS target"; break
        else:
            if sl_pts and h >= entry + sl_pts:
                exit_ep, exit_px, method = ep, entry + sl_pts, "RMS SL"; break
            if tp_pts and l <= entry - tp_pts:
                exit_ep, exit_px, method = ep, entry - tp_pts, "RMS target"; break
        # RSI-midline (if it fires at/after this bar and before SL/TP)
        if rsi_exit and ep >= rsi_exit[0]:
            exit_ep, exit_px, method = ep, cl, "RSI-50 midline"; break
        if _hm(ep) >= "15:15":
            exit_ep, exit_px, method = ep, cl, "3:15 EOD"; break
    if exit_px is None:
        exit_ep, exit_px, method = after[-1][0], after[-1][3], "EOD / last"
        if rsi_exit is None and strat.lower().startswith("rsi"):
            method = "EOD (RSI candles pending)"
    pnl = (exit_px - entry) * qty if side == "BUY" else (entry - exit_px) * qty
    return {"cf_price": round(exit_px, 2), "cf_pnl": round(pnl),
            "method": method, "exit_hm": _hm(exit_ep), "exit_ep": exit_ep}


def _actual_pnl(pair):
    e = pair["entry"]
    entry = _f(e["price"]) or 0.0
    _, _, _, legs = _exit_summary(pair)   # qty-weighted, all exit legs
    pnl = 0.0
    for lg in legs:
        pnl += (lg["price"] - entry) * lg["qty"] if e["side"] == "BUY" else (entry - lg["price"]) * lg["qty"]
    return round(pnl)


# ── main analysis ────────────────────────────────────────────────────────────
def analyze(date=None, mode=None):
    """{ok, date, mode, cuts:[...], strategy_exits:[...], net_impact, helped, hurt,
        day_actual, if_never_cut, n_cut}."""
    date = date or _ist_today()
    rows = _rows_for(date, mode)
    pairs = _pairs(rows)
    cuts, strat_exits, day_actual = [], [], 0
    for p in pairs:
        act = _actual_pnl(p)
        day_actual += act
        kind, reason = _classify_exit(p["exit"], p["entry"])
        avg_ex, ex_q, ex_hm, ex_legs = _exit_summary(p)
        base = {
            "strategy": p["strategy"], "strategy_label": _slabel(p["strategy"]),
            "symbol": p["symbol"],
            "instrument": str(p["entry"].get("trad_sym") or ""),
            "sec_id": p.get("sec_id"),
            "mode": p["entry"].get("mode"),
            "entry_price": _f(p["entry"]["price"]), "entry_hm": (p["entry"]["ts"] or "")[11:16],
            "exit_price": avg_ex, "exit_hm": ex_hm,
            "exit_qty": ex_q, "exit_legs": ex_legs,   # all cut legs (chart markers)
            "qty": int(p["entry"].get("qty") or 0), "side": p["entry"].get("side"),
            "actual_pnl": act, "exit_reason": reason,
        }
        if kind == "cut":
            cf = _counterfactual(p, date)
            base.update({"cf_pnl": cf.get("cf_pnl"), "cf_price": cf.get("cf_price"),
                         "cf_method": cf.get("method"), "cf_exit_hm": cf.get("exit_hm"),
                         "cf_exit_ep": cf.get("exit_ep")})
            base["impact"] = (act - cf["cf_pnl"]) if cf.get("cf_pnl") is not None else None
            cuts.append(base)
        elif kind == "strategy":
            strat_exits.append(base)
        # 'unknown' → skip (neither list) to avoid guessing
    scored = [c for c in cuts if c.get("impact") is not None]
    net = sum(c["impact"] for c in scored)
    helped = [c for c in scored if c["impact"] > 0]
    hurt = [c for c in scored if c["impact"] < 0]
    return {
        "ok": True, "date": date, "mode": mode or "all",
        "cuts": cuts, "strategy_exits": strat_exits,
        "n_cut": len(cuts), "n_scored": len(scored),
        "net_impact": round(net),
        "helped_n": len(helped), "helped_sum": round(sum(c["impact"] for c in helped)),
        "hurt_n": len(hurt), "hurt_sum": round(sum(c["impact"] for c in hurt)),
        "day_actual": round(day_actual),
        "if_never_cut": round(day_actual - net),
    }


def build_and_store(date=None, mode=None):
    """Compute + persist data/intervention/<date>.json (for the EOD timer + trend)."""
    date = date or _ist_today()
    res = analyze(date, mode)
    os.makedirs(STORE_DIR, exist_ok=True)
    with open(os.path.join(STORE_DIR, f"{date}.json"), "w") as f:
        json.dump(res, f, indent=1)
    return res


def trend(n=8, mode=None):
    """Last n stored days' net_impact (for the report's trend strip)."""
    if not os.path.isdir(STORE_DIR):
        return []
    files = sorted(f for f in os.listdir(STORE_DIR) if f.endswith(".json"))[-int(n):]
    out = []
    for f in files:
        try:
            d = json.load(open(os.path.join(STORE_DIR, f)))
            out.append({"date": f[:-5], "net_impact": d.get("net_impact", 0),
                        "n_cut": d.get("n_cut", 0)})
        except Exception:
            continue
    return out


# ── multi-date overview (all dates in one view, live/paper/both, day/week/month) ──
def available_trade_dates():
    """Distinct dates that have any order_store rows (candidate report dates)."""
    import sqlite3
    db = os.path.join(PROJECT, "data", "trades.db")
    if not os.path.exists(db):
        return []
    try:
        c = sqlite3.connect(db)
        rows = c.execute("SELECT DISTINCT date FROM orders WHERE side IN ('BUY','SELL') ORDER BY date").fetchall()
        c.close()
        return [r[0] for r in rows if r[0]]
    except Exception:
        return []


def _stored_path(date):
    return os.path.join(STORE_DIR, f"{date}.json")


def _load_or_build(date, force=False):
    """Stored report json for a date (compute+store if missing/forced). Stores the
    ALL-mode analyse so the overview can filter live/paper/both from the cuts."""
    p = _stored_path(date)
    if not force and os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            pass
    return build_and_store(date, mode=None)


def _period_key(date, group):
    """(sort_key, label) for grouping a YYYY-MM-DD into day / week / month."""
    if group == "month":
        return date[:7], date[:7]                       # 2026-07
    if group == "week":
        try:
            d = datetime.datetime.strptime(date, "%Y-%m-%d").date()
            monday = d - datetime.timedelta(days=d.weekday())
            iso = d.isocalendar()
            return monday.strftime("%Y-%m-%d"), f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            return date, date
    return date, date                                    # day


def overview(mode=None, group="day", warm=True):
    """Per-period intervention aggregate across ALL available dates, filtered by
    mode (None=both). Reads stored jsons (builds missing when warm=True — the
    pre-warm). Returns {ok, mode, group, series:[{period,label,net_impact,day_actual,
    if_never_cut,n_cut,helped,hurt,dates}], totals}."""
    dates = available_trade_dates()
    buckets = {}
    order = []
    for d in dates:
        data = _load_or_build(d) if warm else (
            json.load(open(_stored_path(d))) if os.path.exists(_stored_path(d)) else None)
        if not data:
            continue
        cuts = [c for c in (data.get("cuts") or []) if mode is None or c.get("mode") == mode]
        strat = [s for s in (data.get("strategy_exits") or []) if mode is None or s.get("mode") == mode]
        scored = [c for c in cuts if c.get("impact") is not None]
        net = sum(c["impact"] for c in scored)
        day_actual = sum((c.get("actual_pnl") or 0) for c in cuts) + \
                     sum((s.get("actual_pnl") or 0) for s in strat)
        k, label = _period_key(d, group)
        b = buckets.get(k)
        if b is None:
            b = {"period": k, "label": label, "net_impact": 0, "day_actual": 0,
                 "n_cut": 0, "helped": 0, "hurt": 0, "dates": []}
            buckets[k] = b
            order.append(k)
        b["net_impact"] += net
        b["day_actual"] += day_actual
        b["n_cut"] += len(cuts)
        b["helped"] += sum(1 for c in scored if c["impact"] > 0)
        b["hurt"] += sum(1 for c in scored if c["impact"] < 0)
        b["dates"].append(d)
    series = []
    for k in sorted(order):
        b = buckets[k]
        b["net_impact"] = round(b["net_impact"])
        b["day_actual"] = round(b["day_actual"])
        b["if_never_cut"] = b["day_actual"] - b["net_impact"]
        series.append(b)
    totals = {
        "net_impact": round(sum(b["net_impact"] for b in series)),
        "day_actual": round(sum(b["day_actual"] for b in series)),
        "n_cut": sum(b["n_cut"] for b in series),
        "helped": sum(b["helped"] for b in series),
        "hurt": sum(b["hurt"] for b in series),
    }
    totals["if_never_cut"] = totals["day_actual"] - totals["net_impact"]
    return {"ok": True, "mode": mode or "both", "group": group,
            "series": series, "totals": totals}


def build_all(force=False):
    """Pre-warm: compute + store every available date (idempotent). The LATEST date
    (today) is ALWAYS rebuilt fresh so the daily EOD timer captures the full day's
    cuts even if an on-demand run stored a partial one earlier. Past dates only
    (re)build when missing (or force)."""
    dates = available_trade_dates()
    done = 0
    for i, d in enumerate(dates):
        try:
            _load_or_build(d, force=force or (i == len(dates) - 1))
            done += 1
        except Exception as e:
            print(f"[intervention] build_all {d} fail: {e}")
    return done


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--mode", choices=["live", "paper"])
    ap.add_argument("--store", action="store_true")
    ap.add_argument("--all", action="store_true", help="pre-warm+store every available date")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--overview", action="store_true")
    a = ap.parse_args()
    if a.all:
        n = build_all(force=a.force)
        print(f"pre-warmed {n} dates -> {STORE_DIR}")
    elif a.overview:
        print(json.dumps(overview(a.mode, "day"), indent=1, default=str))
    else:
        r = (build_and_store if a.store else analyze)(a.date, a.mode)
        print(json.dumps(r, indent=1, default=str))
