"""
option_curves.py — Sensibull-style intraday option curves from the on-disk
option-chain snapshots.

SOURCE: `_TRADING_DATA/OptionChain/<U>/<U>_<date>.csv`, written every minute by
`_ops/option_chain_collector.py` — per strike: ltp/oi/iv + REAL Dhan greeks
(delta/theta/gamma/vega) + spot + India-VIX.

WHAT IT COMPUTES (per minute, for a chosen expiry):
  - ATM straddle premium = ATM CE ltp + ATM PE ltp  (the credit you'd collect if
    you SELL the ATM call + put — Sensibull's "Auto ATM Straddle"; ATM is re-picked
    each minute as spot moves).
  - ATM straddle gamma = ATM CE gamma + ATM PE gamma  (real greeks — to spot the
    gamma spike Sensibull doesn't show).
  - spot, VIX, PCR (total PE OI / total CE OI for the expiry).

DISPLAY-ONLY: reads CSV, touches NO order/live path. mtime-cached.
"""
import os
import sys
import csv
import json
import math
import calendar
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)  # _ops/ -> project root

_bs_mod = None


def _bs():
    """Lazy-import the project's SINGLE Black-Scholes source (scratch/nifty_trend/
    bs_option.py — same one _core/payoff.py uses; Rule 6B, no second BS here).
    Returns None if unavailable → the theoretical-decay line just stays empty."""
    global _bs_mod
    if _bs_mod is not None:
        return _bs_mod or None
    d = os.path.join(PROJECT, "scratch", "nifty_trend")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import bs_option as _m
        _bs_mod = _m
    except Exception:
        _bs_mod = False
    return _bs_mod or None


def _lake_dirs(u):
    # same resolution as the collector: parent "._TRADING DATA" (local) or the
    # in-project _TRADING_DATA (VPS).
    return [
        os.path.join(os.path.dirname(PROJECT), "._TRADING DATA", "OptionChain", u),
        os.path.join(PROJECT, "_TRADING_DATA", "OptionChain", u),
    ]


def _csv_path(u, date):
    for d in _lake_dirs(u):
        p = os.path.join(d, f"{u}_{date}.csv")
        if os.path.exists(p):
            return p
    return None


_CACHE = {}  # (u, date) -> (mtime, rows, byte_offset, fieldnames)

# the collector's fixed schema (option_chain_collector.CSV_COLS) — applied when a
# day's file is missing its header row (a collector restart occasionally wrote one
# header-less file, e.g. 2026-07-22), so DictReader doesn't mistake row 1 for headers.
_COLS = ["datetime", "underlying", "spot", "vix", "expiry", "strike", "opt_type",
         "ltp", "bid", "ask", "oi", "prev_oi", "chg_oi", "volume", "iv",
         "delta", "theta", "gamma", "vega"]


def _parse_lines(lines, fieldnames):
    """Parse CSV lines → (rows, fieldnames). fieldnames=None → first line IS the header
    (DictReader derives them, returned so the growing-tail read can reuse them)."""
    if fieldnames is None:
        rd = csv.DictReader(lines)
        return list(rd), rd.fieldnames
    rd = csv.DictReader(lines, fieldnames=fieldnames)
    return list(rd), fieldnames


def _load_rows(u, date):
    """Collector rows for (u, date). mtime-cached; for the LIVE (still-growing) day-file it
    parses only the newly-APPENDED tail (tracked by byte offset) instead of re-reading the
    whole 15-20 MB file every minute. That full re-parse was ~2-3 s and GIL-bound, so it
    periodically FROZE the entire single-process dashboard (every consumer's request queued
    behind it). A trailing partial line (collector mid-write) is left for the next call."""
    p = _csv_path(u, date)
    if not p:
        return None, []
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return p, []
    key = (u, date)
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return p, hit[1]
    # incremental: same file grew → read ONLY the bytes appended past the cached offset
    if hit and len(hit) == 4 and hit[3] is not None:
        try:
            rows, off, fns = hit[1], hit[2], hit[3]
            with open(p, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size >= off:
                    f.seek(off)
                    data = f.read()
                    nl = data.rfind(b"\n")
                    if nl >= 0:
                        lines = data[:nl + 1].decode("utf-8", "replace").splitlines()
                        new_rows, _ = _parse_lines(lines, fns)
                        rows.extend(new_rows)
                        off += nl + 1
                    _CACHE[key] = (mt, rows, off, fns)
                    return p, rows
        except Exception:
            pass   # fall through to a clean full parse
    # full parse (new file / shrank / no prior offset)
    try:
        with open(p, "rb") as f:
            data = f.read()
    except Exception:
        return p, []
    nl = data.rfind(b"\n")
    off = (nl + 1) if nl >= 0 else len(data)
    lines = data[:off].decode("utf-8", "replace").splitlines()
    if not lines:
        _CACHE[key] = (mt, [], off, None)
        return p, []
    has_header = lines[0].startswith("datetime,")
    rows, fns = _parse_lines(lines, None if has_header else _COLS)
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = (mt, rows, off, fns)
    return p, rows


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _epoch_ist(dtstr):
    # "2026-07-23 10:29:54" is IST wall-clock. Encode it as an as-if-UTC epoch so a
    # lightweight-charts UTC axis prints the IST time verbatim (same convention as
    # trade_chart.html). No timezone shift is applied on purpose.
    try:
        t = datetime.strptime(dtstr, "%Y-%m-%d %H:%M:%S")
        return calendar.timegm(t.timetuple())
    except Exception:
        return None


_EXTBA_CACHE = {}   # (u,date) -> (mtime, {HH:MM: (ce_bid,ce_ask,pe_bid,pe_ask,ce_ltp,pe_ltp)})


def _ext_bidask(u, date):
    """External per-minute ATM CE/PE bid/ask for a day the collector didn't capture
    bid/ask (e.g. sourced once from the DOM/orderbook feed into
    data/opt_bidask/<U>_<date>.csv). Keyed by HH:MM. Decoupled — curves() reads only
    this clean file, never a foreign raw feed."""
    p = os.path.join(PROJECT, "data", "opt_bidask", f"{u}_{date}.csv")
    if not os.path.exists(p):
        return {}
    mt = os.path.getmtime(p)
    key = (u, date)
    hit = _EXTBA_CACHE.get(key)
    if hit and hit[0] == mt:
        return hit[1]
    out = {}
    try:
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                hm = (r.get("datetime") or "")[11:16]
                vals = tuple(_f(r.get(k)) for k in ("ce_bid", "ce_ask", "pe_bid", "pe_ask", "ce_ltp", "pe_ltp"))
                if hm and None not in vals[:4]:
                    out[hm] = vals
    except Exception:
        return {}
    if len(_EXTBA_CACHE) > 8:
        _EXTBA_CACHE.clear()
    _EXTBA_CACHE[key] = (mt, out)
    return out


_CURVES_CACHE = {}   # (u,date,expiry,mtime,ivr) -> result dict (skip the ~1s recompute)


def curves(u, date, expiry=None):
    """Return {ok, underlying, expiry, expiries[], points[]} for one expiry's day.
    mtime-cached: on re-render / 30 s auto-refresh where the day-file hasn't grown, the
    whole per-minute computation is skipped and the prior result is returned."""
    p, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "underlying": u, "expiry": expiry, "expiries": [], "points": []}
    try:
        _mt = os.path.getmtime(p) if p else 0
    except OSError:
        _mt = 0
    # IV-Rank window (disk-cached, cheap) — computed up-front so it can key the result cache
    lo_iv, hi_iv, ndays = _iv_hist_range(u, date)
    ck = (u, date, expiry, _mt, lo_iv, hi_iv, ndays)
    chit = _CURVES_CACHE.get(ck)
    if chit is not None:
        return chit

    exps = []
    for r in rows:
        e = r.get("expiry")
        if e and e not in exps:
            exps.append(e)
    exps = sorted(exps)
    if expiry not in exps:
        expiry = exps[0] if exps else None

    # next expiry (term-structure panel): near-week vs next-week ATM IV. Populated only
    # once the collector captures >1 expiry; single-expiry data → iv_next stays None.
    _ni = (exps.index(expiry) + 1) if (expiry in exps) else len(exps)
    next_expiry = exps[_ni] if _ni < len(exps) else None
    nmap = _atm_iv_map(rows, next_expiry) if next_expiry else {}
    extmap = _ext_bidask(u, date)   # external bid/ask overlay (days collector didn't capture it)

    # group the chosen expiry's rows by timestamp
    bydt = {}
    for r in rows:
        if r.get("expiry") != expiry:
            continue
        bydt.setdefault(r.get("datetime"), []).append(r)

    points = []
    for dt in sorted(k for k in bydt.keys() if k):
        legs = bydt[dt]
        spot = _f(legs[0].get("spot"))
        vix = _f(legs[0].get("vix"))
        if spot is None:
            continue
        strikes = sorted({_f(l.get("strike")) for l in legs if _f(l.get("strike")) is not None})
        if not strikes:
            continue
        atm = min(strikes, key=lambda k: abs(k - spot))
        ce = next((l for l in legs if _f(l.get("strike")) == atm and l.get("opt_type") == "CE"), None)
        pe = next((l for l in legs if _f(l.get("strike")) == atm and l.get("opt_type") == "PE"), None)
        if not ce or not pe:
            continue
        ce_ltp, pe_ltp = _f(ce.get("ltp")), _f(pe.get("ltp"))
        if ce_ltp is None or pe_ltp is None:
            continue
        ce_g, pe_g = _f(ce.get("gamma")) or 0.0, _f(pe.get("gamma")) or 0.0
        ce_oi = sum(_f(l.get("oi")) or 0.0 for l in legs if l.get("opt_type") == "CE")
        pe_oi = sum(_f(l.get("oi")) or 0.0 for l in legs if l.get("opt_type") == "PE")
        # ATM straddle IV (avg CE/PE — percent, so it plots directly against VIX) and
        # net straddle delta (CE delta + PE delta; ~0 at ATM, drifts as spot moves).
        ce_iv, pe_iv = _f(ce.get("iv")), _f(pe.get("iv"))
        _ivs = [x for x in (ce_iv, pe_iv) if x is not None]
        atm_iv = round(sum(_ivs) / len(_ivs), 2) if _ivs else None
        ce_d, pe_d = _f(ce.get("delta")), _f(pe.get("delta"))
        net_delta = round(ce_d + pe_d, 4) if (ce_d is not None and pe_d is not None) else None
        # Max-OI strike (full captured chain, not just ATM): call wall vs put wall.
        ce_oi_by, pe_oi_by = {}, {}
        for l in legs:
            k = _f(l.get("strike"))
            if k is None:
                continue
            o = _f(l.get("oi")) or 0.0
            if l.get("opt_type") == "CE":
                ce_oi_by[k] = o
            elif l.get("opt_type") == "PE":
                pe_oi_by[k] = o
        call_wall = max(ce_oi_by, key=ce_oi_by.get) if ce_oi_by else None
        put_wall = max(pe_oi_by, key=pe_oi_by.get) if pe_oi_by else None
        # Put skew: avg OTM-put IV − avg OTM-call IV (positive = puts richer = fear/hedging).
        put_ivs = [_f(l.get("iv")) for l in legs
                   if l.get("opt_type") == "PE" and _f(l.get("strike")) is not None and _f(l.get("strike")) < atm]
        call_ivs = [_f(l.get("iv")) for l in legs
                    if l.get("opt_type") == "CE" and _f(l.get("strike")) is not None and _f(l.get("strike")) > atm]
        put_ivs = [v for v in put_ivs if v is not None]
        call_ivs = [v for v in call_ivs if v is not None]
        put_skew = round(sum(put_ivs) / len(put_ivs) - sum(call_ivs) / len(call_ivs), 2) \
            if (put_ivs and call_ivs) else None
        # Biggest single-strike OI add / unwind this minute (heatmap "bomb" alert).
        oi_add_max, oi_add_strike, oi_cut_max, oi_cut_strike = 0.0, None, 0.0, None
        for l in legs:
            c = _f(l.get("chg_oi"))
            if c is None:
                continue
            if c > oi_add_max:
                oi_add_max, oi_add_strike = c, _f(l.get("strike"))
            if c < -oi_cut_max:
                oi_cut_max, oi_cut_strike = -c, _f(l.get("strike"))
        # Bid-ask spread (ATM straddle) — execution/opportunity signal (tight = good fill)
        ce_bid, ce_ask = _f(ce.get("bid")), _f(ce.get("ask"))
        pe_bid, pe_ask = _f(pe.get("bid")), _f(pe.get("ask"))
        _sp_str = ce_ltp + pe_ltp   # straddle premium the spread% is relative to
        if (ce_bid is None or pe_bid is None) and extmap:   # collector had no bid/ask → external overlay
            ext = extmap.get(dt[11:16])
            if ext:
                ce_bid, ce_ask, pe_bid, pe_ask = ext[0], ext[1], ext[2], ext[3]
                if ext[4] and ext[5]:
                    _sp_str = ext[4] + ext[5]   # use the external source's own straddle for %
        ce_sp = (ce_ask - ce_bid) if (ce_bid is not None and ce_ask is not None and ce_ask >= ce_bid) else None
        pe_sp = (pe_ask - pe_bid) if (pe_bid is not None and pe_ask is not None and pe_ask >= pe_bid) else None
        if ce_sp is not None and pe_sp is not None:
            spread_abs = round(ce_sp + pe_sp, 2)
            spread_pct = round(spread_abs / _sp_str * 100, 3) if _sp_str else None
        else:
            spread_abs, spread_pct = None, None
        ep = _epoch_ist(dt)
        if ep is None:
            continue
        points.append({
            "t": ep,
            "spot": round(spot, 2),
            "atm": atm,
            "ce": round(ce_ltp, 2),
            "pe": round(pe_ltp, 2),
            "straddle": round(ce_ltp + pe_ltp, 2),
            "gamma": round(ce_g + pe_g, 6),
            "vix": round(vix, 2) if vix is not None else None,
            "pcr": round(pe_oi / ce_oi, 3) if ce_oi else None,
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "atm_iv": atm_iv,
            "net_delta": net_delta,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "straddle_theo": None,   # filled in the theoretical-decay pass below
            "iv_near": atm_iv,       # this (near) expiry's ATM IV — term-structure panel
            "iv_next": round(nmap[dt], 2) if nmap.get(dt) is not None else None,
            "realized_vol": None,    # filled in the realized-vol pass below
            "iv_rank": None,         # filled in the IV-rank pass below
            "put_skew": put_skew,
            "oi_add_max": oi_add_max, "oi_add_strike": oi_add_strike,
            "oi_cut_max": oi_cut_max, "oi_cut_strike": oi_cut_strike,
            "spread_abs": spread_abs, "spread_pct": spread_pct,
            "ce_bid": ce_bid, "ce_ask": ce_ask, "pe_bid": pe_bid, "pe_ask": pe_ask,
        })

    # Theoretical decay reference: freeze the ATM IV at the day's FIRST reading and let
    # only time-to-expiry shrink (pure theta @ open-IV). actual straddle < theo = IV has
    # crushed / decay ahead of schedule = edge for the option seller. Reuses bs_option.
    bs = _bs()
    iv0 = next((p["atm_iv"] for p in points if p.get("atm_iv")), None)
    exp_ep = _epoch_ist(expiry + " 15:30:00") if expiry else None
    if bs and iv0 and exp_ep:
        sig = iv0 if iv0 < 1 else iv0 / 100.0   # accept fraction or percent IV
        yr = 365.25 * 24 * 3600.0
        for p in points:
            T = max(exp_ep - p["t"], 0) / yr
            try:
                theo = (bs.bs_price(p["spot"], p["atm"], T, sig, opt="CE")
                        + bs.bs_price(p["spot"], p["atm"], T, sig, opt="PE"))
                p["straddle_theo"] = round(theo, 2)
            except Exception:
                pass

    # Realized vol (rolling, %): annualised std of 1-min spot log-returns over RV_WIN
    # minutes. Pair vs atm_iv on the /curves RV-vs-IV panel → IV above RV = VRP edge live.
    RV_WIN = 30
    ANN = (252 * 375) ** 0.5   # ~375 trading minutes/day
    for i, p in enumerate(points):
        seg = points[max(0, i - RV_WIN):i + 1]
        rets = []
        for j in range(1, len(seg)):
            s0, s1 = seg[j - 1]["spot"], seg[j]["spot"]
            if s0 and s1 and s0 > 0:
                rets.append(math.log(s1 / s0))
        if len(rets) >= 5:
            m = sum(rets) / len(rets)
            sd = (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5
            p["realized_vol"] = round(sd * ANN * 100, 2)

    # IV Rank (0-100): where today's ATM IV sits in the range of prior stored days'
    # ATM IV. Uses however many days are on disk (labelled), improves as more accrue.
    # (lo_iv/hi_iv/ndays computed up-front for the cache key.)
    if hi_iv is not None and hi_iv > lo_iv:
        for pt in points:
            iv = pt.get("atm_iv")
            if iv is not None:
                pt["iv_rank"] = round(max(0.0, min(100.0, (iv - lo_iv) / (hi_iv - lo_iv) * 100.0)), 1)

    out = {"ok": True, "underlying": u, "expiry": expiry, "expiries": exps, "points": points,
           "iv_rank_days": ndays, "next_expiry": next_expiry}
    if len(_CURVES_CACHE) > 12:
        _CURVES_CACHE.clear()
    _CURVES_CACHE[ck] = out
    return out


def chain_snapshot(u, date, expiry=None):
    """LATEST per-minute snapshot as a per-strike CE/PE map — the Quick Order
    chain's OI/IV/greeks source (real Dhan greeks from the collector lake, ~1min
    fresh; the route overlays live batched LTP on top). Display-only, mtime-cached
    via _load_rows.

    Returns {ok, underlying, expiry, expiries[], datetime, spot, atm, step,
             strikes: {K(int): {ce:{ltp,oi,iv,delta}, pe:{...}}}}."""
    _, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "underlying": u, "expiry": expiry, "expiries": [], "strikes": {}}
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if expiry not in exps:
        expiry = exps[0] if exps else None
    # latest timestamp that has this expiry's legs
    dts = sorted({r.get("datetime") for r in rows
                  if r.get("expiry") == expiry and r.get("datetime")})
    if not dts:
        return {"ok": False, "underlying": u, "expiry": expiry, "expiries": exps, "strikes": {}}
    last = dts[-1]
    legs = [r for r in rows if r.get("expiry") == expiry and r.get("datetime") == last]
    spot = _f(legs[0].get("spot")) if legs else None
    out = {}
    for l in legs:
        k = _f(l.get("strike"))
        ot = (l.get("opt_type") or "").upper()
        if k is None or ot not in ("CE", "PE"):
            continue
        d = out.setdefault(int(k), {})
        d[ot.lower()] = {"ltp": _f(l.get("ltp")), "oi": _f(l.get("oi")),
                         "iv": _f(l.get("iv")), "delta": _f(l.get("delta"))}
    strikes = sorted(out.keys())
    step = None
    if len(strikes) >= 2:
        diffs = sorted(strikes[i + 1] - strikes[i] for i in range(len(strikes) - 1))
        step = diffs[len(diffs) // 2] or None   # median gap = strike step
    atm = min(strikes, key=lambda x: abs(x - spot)) if (strikes and spot) else None
    return {"ok": True, "underlying": u, "expiry": expiry, "expiries": exps,
            "datetime": last, "spot": round(spot, 2) if spot else None,
            "atm": atm, "step": step, "strikes": out}


def available_dates(u):
    """All dates with a stored option-chain CSV for this underlying (sorted)."""
    out = set()
    for d in _lake_dirs(u):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith(u + "_") and f.endswith(".csv"):
                    out.add(f[len(u) + 1:-4])
    return sorted(out)


def curves_multi(u, end_date, days):
    """Concatenate the last `days` available option-chain days (<= end_date) into one
    continuous series — the SAME per-day points as curves(), just across days (each day
    auto-picks its own nearest expiry). Overnight gaps are natural on the time axis."""
    ds = [d for d in available_dates(u) if d <= end_date][-int(days):]
    pts, used, exps = [], [], []
    for d in ds:
        r = curves(u, d)
        if r.get("points"):
            pts.extend(r["points"])
            used.append(d)
            if r.get("expiry") and r["expiry"] not in exps:
                exps.append(r["expiry"])
    return {"ok": bool(pts), "underlying": u, "points": pts, "days_used": used,
            "multi": True, "expiries": sorted(exps), "expiry": None}


def strike_series(u, date, expiry, strike=None, opt_type=None):
    """Per-minute premium series for ONE strike+type (for the /curves right-click
    'Load strike chart'). Also returns the list of strikes available that day so the
    picker can offer them. Display-only."""
    _, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "expiry": expiry, "strikes": [], "points": []}

    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if expiry not in exps:
        expiry = exps[0] if exps else None

    strikes = sorted({_f(r.get("strike")) for r in rows
                      if r.get("expiry") == expiry and _f(r.get("strike")) is not None})

    pts = []
    want = _f(strike)
    ot = (opt_type or "").upper()
    if want is not None and ot in ("CE", "PE"):
        for r in rows:
            if r.get("expiry") != expiry or r.get("opt_type") != ot:
                continue
            if _f(r.get("strike")) != want:
                continue
            ep = _epoch_ist(r.get("datetime"))
            ltp = _f(r.get("ltp"))
            if ep is None or ltp is None:
                continue
            pts.append({
                "t": ep,
                "ltp": round(ltp, 2),
                "oi": _f(r.get("oi")) or 0.0,
                "iv": _f(r.get("iv")),
                "spot": _f(r.get("spot")),
            })
        pts.sort(key=lambda x: x["t"])

    return {
        "ok": True, "underlying": u, "expiry": expiry, "strikes": strikes,
        "strike": want, "opt_type": ot, "points": pts,
    }


def legs_series(u, date, expiry, legs):
    """Combined per-minute premium for a FIXED-STRIKE straddle/strangle held all day
    (the /curves 'Fixed strike' mode — Sensibull-style multi straddle-strangle).
    `legs` = list of {strike, opt_type, sign?} (a straddle = same-K CE+PE; strangle =
    two Ks). `sign` (default +1) lets a SELL+HEDGE structure chart its NET premium:
    SELL legs +1, BUY hedge legs -1 → line = net credit (sold − bought). Plain long
    straddle/strangle omit sign → +1 → sum (unchanged). Reuses strike_series() per leg
    (Rule 6B) on the epochs present in ALL legs. Held strike → no ATM rolling. Display-only."""
    legs = [l for l in (legs or []) if _f(l.get("strike")) is not None
            and (l.get("opt_type") or "").upper() in ("CE", "PE")]
    if not legs:
        return {"ok": False, "expiry": expiry, "strikes": [], "points": []}
    per, strikes, meta = [], [], []
    exp_used = expiry
    for l in legs:
        K = _f(l.get("strike")); ot = (l.get("opt_type") or "").upper()
        sign = 1 if int(l.get("sign", 1) or 1) >= 0 else -1
        s = strike_series(u, date, expiry, K, ot)
        exp_used = s.get("expiry") or exp_used
        strikes = s.get("strikes") or strikes
        per.append({p["t"]: p for p in s.get("points", [])})
        meta.append((K, ot, sign))
    common = set(per[0])
    for m in per[1:]:
        common &= set(m)
    # Theoretical decay: freeze each leg's ENTRY IV, keep the real spot + fixed strike,
    # let only time-to-expiry shrink (same pure-theta+moneyness reference as the ATM
    # "Theo vs Actual decay" panel). actual < theo = IV crushed = seller edge. Reuses bs.
    bs = _bs()
    exp_ep = _epoch_ist((exp_used + " 15:30:00")) if exp_used else None
    sigs = []
    for m in per:
        iv0 = next((m[t].get("iv") for t in sorted(m) if m[t].get("iv")), None)
        sigs.append((iv0 if iv0 < 1 else iv0 / 100.0) if iv0 else None)
    theo_ok = bool(bs and exp_ep and all(s is not None for s in sigs))
    yr = 365.25 * 24 * 3600.0
    pts = []
    for t in sorted(common):
        prem = sum(meta[i][2] * per[i][t]["ltp"] for i in range(len(per)))
        spot = per[0][t].get("spot")
        rec = {"t": t, "prem": round(prem, 2), "spot": spot}
        if theo_ok and spot:
            try:
                T = max(exp_ep - t, 0) / yr
                rec["theo"] = round(sum(meta[i][2] * bs.bs_price(spot, meta[i][0], T, sigs[i], opt=meta[i][1])
                                        for i in range(len(per))), 2)
            except Exception:
                pass
        pts.append(rec)
    return {"ok": True, "underlying": u, "expiry": exp_used, "strikes": strikes,
            "legs": [{"strike": _f(l.get("strike")), "opt_type": (l.get("opt_type") or "").upper(),
                      "sign": (1 if int(l.get("sign", 1) or 1) >= 0 else -1)} for l in legs],
            "points": pts}


def legs_series_multi(u, end_date, days, legs):
    """Multi-day legs_series — concatenate the last `days` stored days' held straddle/
    strangle premium (each day auto-picks its own nearest expiry, theo resets per day),
    so the /curves Fixed pane spans the SAME window as the multi-day main charts. Uses the
    same day-list as curves_multi(). Display-only."""
    ds = [d for d in available_dates(u) if d <= end_date][-int(days):]
    pts, strikes = [], []
    for d in ds:
        r = legs_series(u, d, None, legs)
        if r.get("points"):
            pts.extend(r["points"])
            strikes = r.get("strikes") or strikes
    return {"ok": bool(pts), "underlying": u, "multi": True, "strikes": strikes, "points": pts}


# ── term-structure + IV-rank helpers ────────────────────────────────────────
def _atm_iv_map(rows, expiry):
    """{datetime -> ATM (avg CE/PE) IV} for one expiry — for the next-week term line."""
    bydt = {}
    for r in rows:
        if r.get("expiry") != expiry:
            continue
        bydt.setdefault(r.get("datetime"), []).append(r)
    out = {}
    for dt, legs in bydt.items():
        spot = _f(legs[0].get("spot"))
        if spot is None:
            continue
        strikes = sorted({_f(l.get("strike")) for l in legs if _f(l.get("strike")) is not None})
        if not strikes:
            continue
        atm = min(strikes, key=lambda k: abs(k - spot))
        ivs = [_f(l.get("iv")) for l in legs if _f(l.get("strike")) == atm]
        ivs = [v for v in ivs if v is not None]
        if ivs:
            out[dt] = sum(ivs) / len(ivs)
    return out


_IVRANK_CACHE = {}   # (u, date) -> (lo, hi, ndays)


def _day_rep_atm_iv(u, d):
    """One representative ATM IV for a stored day (median of per-minute ATM IV)."""
    _, rows = _load_rows(u, d)
    if not rows:
        return None
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    m = _atm_iv_map(rows, exps[0] if exps else None)
    vals = sorted(v for v in m.values() if v is not None)
    if not vals:
        return None
    return vals[len(vals) // 2]   # median


# Persisted per-day representative ATM IV. A completed day's median ATM IV never changes,
# so parsing its 14 MB CSV even once (let alone on every page load) is pure waste. This
# sidecar makes each prior day parsed AT MOST ONCE, ever — the fix for the ~20 s+ cold
# /curves load (was: _iv_hist_range full-parsed up to 60 prior day-files per request AND
# thrashed the 8-entry _CACHE, so the active day got evicted and re-parsed every refresh).
_IVREP_PATH = os.path.join(PROJECT, "data", "iv_rank_days.json")
_IVREP = None            # {u: {date: rep_atm_iv}}  (loaded lazily)
_IVREP_DIRTY = False
_IV_NEW_PER_CALL = 8     # cap fresh (uncached) day-parses per request → a page never blocks


def _ivrep_load():
    global _IVREP
    if _IVREP is not None:
        return _IVREP
    try:
        with open(_IVREP_PATH, "r") as f:
            _IVREP = json.load(f) or {}
    except Exception:
        _IVREP = {}
    return _IVREP


def _ivrep_save():
    global _IVREP_DIRTY
    if not _IVREP_DIRTY:
        return
    try:
        os.makedirs(os.path.dirname(_IVREP_PATH), exist_ok=True)
        tmp = _IVREP_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_IVREP, f)
        os.replace(tmp, _IVREP_PATH)
        _IVREP_DIRTY = False
    except Exception:
        pass


def _iv_hist_range(u, date, lookback=60):
    """(lo, hi, ndays) of prior stored days' representative ATM IV — the IV-Rank window.
    Uses whatever days are on disk (up to `lookback`). Per-day rep IV is disk-cached
    (each day parsed once, ever); at most `_IV_NEW_PER_CALL` uncached days are computed
    per request so a cold call can never stall the page — the rest fill in on later
    refreshes (IV-Rank is a soft display metric, self-completes)."""
    global _IVREP_DIRTY
    key = (u, date)
    hit = _IVRANK_CACHE.get(key)
    if hit:
        return hit
    store = _ivrep_load().setdefault(u, {})
    prior = [d for d in available_dates(u) if d < date][-int(lookback):]
    reps, budget = [], _IV_NEW_PER_CALL
    for d in prior:
        if d in store:                       # cached (incl. days with no IV → stored null)
            v = store[d]
        elif budget > 0:                     # compute + persist (bounded per call)
            v = _day_rep_atm_iv(u, d)
            store[d] = v
            _IVREP_DIRTY = True
            budget -= 1
        else:
            continue                         # leave for a later refresh
        if v is not None:
            reps.append(v)
    _ivrep_save()
    res = (min(reps), max(reps), len(reps)) if reps else (None, None, 0)
    # only memory-cache a COMPLETE result (all prior days computed) — a partial one must
    # be recomputed next call so the remaining days keep filling in.
    if not any((d not in store) for d in prior):
        if len(_IVRANK_CACHE) > 16:
            _IVRANK_CACHE.clear()
        _IVRANK_CACHE[key] = res
    return res


def _near_strikes(legs, spot, each_side=7):
    """Strikes within ±each_side steps of ATM (for skew/heatmap), sorted."""
    strikes = sorted({_f(l.get("strike")) for l in legs if _f(l.get("strike")) is not None})
    if not strikes:
        return [], None
    atm = min(strikes, key=lambda k: abs(k - spot))
    i = strikes.index(atm)
    return strikes[max(0, i - each_side):i + each_side + 1], atm


def skew_series(u, date, expiry=None, each_side=7):
    """Per-minute strike-wise IV smile: for each timestamp, CE-IV and PE-IV across ATM±N
    strikes. The /curves skew panel picks the crosshair minute and draws two curves.
    Display-only."""
    _, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "expiry": expiry, "series": []}
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if expiry not in exps:
        expiry = exps[0] if exps else None
    bydt = {}
    for r in rows:
        if r.get("expiry") != expiry:
            continue
        bydt.setdefault(r.get("datetime"), []).append(r)
    series = []
    for dt in sorted(k for k in bydt if k):
        legs = bydt[dt]
        spot = _f(legs[0].get("spot"))
        ep = _epoch_ist(dt)
        if spot is None or ep is None:
            continue
        strikes, atm = _near_strikes(legs, spot, each_side)
        if not strikes:
            continue
        cem = {_f(l.get("strike")): _f(l.get("iv")) for l in legs if l.get("opt_type") == "CE"}
        pem = {_f(l.get("strike")): _f(l.get("iv")) for l in legs if l.get("opt_type") == "PE"}
        series.append({"t": ep, "atm": atm, "spot": round(spot, 2),
                       "strikes": [int(k) for k in strikes],
                       "ce": [cem.get(k) for k in strikes],
                       "pe": [pem.get(k) for k in strikes]})
    return {"ok": True, "underlying": u, "expiry": expiry, "expiries": exps, "series": series}


def oi_heatmap_series(u, date, expiry=None, bucket_min=5, each_side=7):
    """OI-change heatmap grid: rows = strikes (ATM±N over the day), cols = `bucket_min`
    buckets, cell = net chg_oi in that bucket (CE and PE separately). Display-only."""
    _, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "expiry": expiry, "strikes": [], "times": [], "ce": [], "pe": []}
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if expiry not in exps:
        expiry = exps[0] if exps else None
    bydt = {}
    for r in rows:
        if r.get("expiry") != expiry:
            continue
        bydt.setdefault(r.get("datetime"), []).append(r)
    # strike axis = union of ATM±N strikes seen across the day
    keep = set()
    for dt, legs in bydt.items():
        spot = _f(legs[0].get("spot"))
        if spot is None:
            continue
        ns, _atm = _near_strikes(legs, spot, each_side)
        keep.update(ns)
    strikes = sorted(keep)
    sidx = {k: i for i, k in enumerate(strikes)}
    bsec = bucket_min * 60
    buckets = {}   # bucket_epoch -> {"ce":[..], "pe":[..]}
    for dt in sorted(k for k in bydt if k):
        ep = _epoch_ist(dt)
        if ep is None:
            continue
        bk = (ep // bsec) * bsec
        b = buckets.setdefault(bk, {"ce": [None] * len(strikes), "pe": [None] * len(strikes)})
        for l in bydt[dt]:
            k = _f(l.get("strike"))
            if k not in sidx:
                continue
            c = _f(l.get("chg_oi"))
            if c is None:
                continue
            side = "ce" if l.get("opt_type") == "CE" else ("pe" if l.get("opt_type") == "PE" else None)
            if side:
                cur = b[side][sidx[k]]
                b[side][sidx[k]] = (cur or 0.0) + c
    times = sorted(buckets)
    return {"ok": True, "underlying": u, "expiry": expiry, "expiries": exps,
            "strikes": [int(k) for k in strikes], "times": times,
            "ce": [buckets[t]["ce"] for t in times], "pe": [buckets[t]["pe"] for t in times]}
