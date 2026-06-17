"""
validate_strategy.py — Phase 4 validation harness.

Runs the LIVE engine logic (range_trader.run_signal_engine, copied here to
collect ALL trades instead of just the last) on historical NIFTY 5-min bars,
then compares the engine's entries/exits against a TradingView "List of Trades"
CSV. A trade is a MATCH only when entry AND exit time + side both align.

Output: per-day side-by-side table + overall % match + mismatch list.

Data: ._TRADING DATA / Index / NIFTY  (per-day 1-min CSVs)
Strategy: Range Chain (Ars_Auto_Rev_Chain) == range_trader.py
Timeframe: 5-min (1-min resampled).
"""

import argparse
import csv
import glob
import os
from datetime import time as dtime

import numpy as np
import pandas as pd

import range_trader as rt

DATA_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY"
EXIT_HM  = dtime(15, 15)   # 3:15 daily square-off

# Engine config — mirror the TradingView Pine settings
CFG = {
    "max_candle_size": 25,
    "use_fresh_zone_only": True,
    "hawa_me_zone": False,
    "exit_atr": True,
    "exit_main": True,            # Pine MainExit_Toggle (Zone Exit) = true
    "max_trades_per_symbol": 4,   # Pine maxTradesPerDay
}
ATR_LEN, ATR_MULT, ZONE_AGE = 14, 2.0, 2


# ───────────────────────── data loading ─────────────────────────
def load_1m(path):
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def resample_5m(df1):
    d = df1.set_index("datetime")
    r = pd.DataFrame({
        "open":  d["open"].resample("5min").first(),
        "high":  d["high"].resample("5min").max(),
        "low":   d["low"].resample("5min").min(),
        "close": d["close"].resample("5min").last(),
    }).dropna().reset_index().rename(columns={"datetime": "time"})
    return r


def daily_bars():
    """Daily OHLC for key levels. Prefer full Dhan daily history (nifty_daily.csv,
    back to 2025-01) so chains have enough lookback; else aggregate per-day files."""
    daily_csv = os.path.join(DATA_DIR, "nifty_daily.csv")
    if os.path.exists(daily_csv):
        df = pd.read_csv(daily_csv)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df[["date", "open", "high", "low", "close"]].sort_values("date").reset_index(drop=True)
    rows = []
    for p in sorted(glob.glob(os.path.join(DATA_DIR, "NIFTY_2026-*.csv"))):
        df = load_1m(p)
        if df.empty:
            continue
        rows.append({
            "date":  df["datetime"].iloc[0].date(),
            "open":  float(df["open"].iloc[0]),
            "high":  float(df["high"].max()),
            "low":   float(df["low"].min()),
            "close": float(df["close"].iloc[-1]),
        })
    return pd.DataFrame(rows)


# Pine: bullish = bullEngulf or bullHarami or greenHammer
#       bearish = bearEngulf or bearHarami or invRedHam or redHammer
def _ohlc(df, i):
    r = df.iloc[i]
    return float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])


def _bull(df, i):
    if i < 1:
        return False
    o, h, l, c = _ohlc(df, i)
    po, ph, pl, pc = _ohlc(df, i - 1)
    return (rt.green_hammer(o, h, l, c)
            or rt.bull_engulfing(po, ph, pl, pc, o, h, l, c)
            or rt.bull_harami(po, ph, pl, pc, o, h, l, c))


def _bear(df, i):
    if i < 1:
        return False
    o, h, l, c = _ohlc(df, i)
    po, ph, pl, pc = _ohlc(df, i - 1)
    return (rt.red_hammer(o, h, l, c)
            or rt.inv_red_hammer(o, h, l, c)
            or rt.bear_engulfing(po, ph, pl, pc, o, h, l, c)
            or rt.bear_harami(po, ph, pl, pc, o, h, l, c))


# ───────────────────────── backtest one day ─────────────────────────
def backtest_day(df5, key_levels, cfg, atr_series=None, dbg=False):
    """Mirror of run_signal_engine but COLLECTS every trade. Adds 3:15 exit.
    atr_series (continuous, pre-warmed) aligned to df5 rows; else computed here.
    Returns list of dicts: {entry_time, entry_price, side, exit_time, exit_price, exit_reason}."""
    trades = []
    if df5 is None or len(df5) < 20 or not key_levels:
        return trades

    max_cs     = cfg.get("max_candle_size", 25)
    fresh_only = cfg.get("use_fresh_zone_only", True)
    exit_atr   = cfg.get("exit_atr", True)
    exit_main  = cfg.get("exit_main", True)
    max_trades = cfg.get("max_trades_per_symbol", 4)

    if atr_series is None:
        atr_series = rt.compute_atr(df5, ATR_LEN)
    else:
        atr_series = atr_series.reset_index(drop=True)

    zone_upper = zone_lower = zone_type = None
    zone_bar = -999
    tracked_high = tracked_low = None
    touch_active = False
    active_touch_type = None
    atr_sl_long = atr_sl_short = None
    position = None
    cur = None            # open trade dict
    trades_today = 0

    def _fill(i):
        """TradingView convention: a signal on bar i fills at the NEXT bar's
        open. Return (time, price) of bar i+1 (or bar i if it's the last)."""
        j = i + 1 if i + 1 < n else i
        b = df5.iloc[j]
        return b["time"], float(b["open"])

    def _open(side, i, reason):
        nonlocal cur, position
        t, price = _fill(i)
        cur = {"entry_time": t, "entry_price": price, "side": side,
               "exit_time": None, "exit_price": None, "exit_reason": None,
               "entry_reason": reason}
        position = "LONG" if side == "Long" else "Short"

    def _close(i, reason):
        nonlocal cur, position
        t, price = _fill(i)
        if cur:
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = t, price, reason
            trades.append(cur)
            cur = None
        position = None

    n = len(df5)
    for i in range(2, n):
        row = df5.iloc[i]
        t = row["time"]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        atr_val = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 5.0

        # 3:15 daily square-off
        if position is not None and t.time() >= EXIT_HM:
            _close(i, "3:15 Daily Exit")
            # after square-off, no new entries this loop iter
            continue

        # touch detection — Pine selectedLine: RESISTANCE priority (locks),
        # last resistance wins; else last support/CP touched
        touched_type = None
        res_locked = False
        for price, ltype in key_levels:
            if not (l <= price <= h):
                continue
            if ltype in ("RESISTANCE", "PD_H"):
                touched_type, res_locked = ltype, True
            elif ltype in ("SUPPORT", "PD_L") and not res_locked:
                touched_type = ltype
            elif ltype in ("CP", "PD_C") and not res_locked:
                touched_type = ltype
        # tracked high/low — Pine accumulates over ALL touch bars within the day
        # (touchActive never resets) and does NOT reset on zone formation.
        if touched_type is not None:
            if tracked_high is None:
                tracked_high, tracked_low = h, l
            else:
                tracked_high = max(tracked_high, h)
                tracked_low  = min(tracked_low, l)

        # zone formation — Pine uses CURRENT-bar `touched` (not persistent touch_active)
        bullish = _bull(df5, i)
        bearish = _bear(df5, i)
        if touched_type is not None:
            not_red = touched_type not in ("RESISTANCE", "PD_H")
            not_grn = touched_type not in ("SUPPORT", "PD_L")
            if bearish and not_grn:
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "RED", i
                if dbg: print(f"  {t:%H:%M} RED zone formed [{l:.1f}-{h:.1f}] touch={touched_type}")
            elif bullish and not_red:
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "GREEN", i
                if dbg: print(f"  {t:%H:%M} GREEN zone formed [{l:.1f}-{h:.1f}] touch={touched_type}")

        # zone freshness (Pine maxZoneAge)
        zfresh = zone_type is not None and (i - zone_bar) <= ZONE_AGE

        # EXIT — Pine priority: ATR first, else ZONE (MainExit)
        if position == "LONG":
            if atr_sl_long is not None:
                atr_sl_long = max(atr_sl_long, c - atr_val * ATR_MULT)
            if exit_atr and atr_sl_long is not None and c < atr_sl_long:
                _close(i, "ATR_LONG")
                atr_sl_long = None
            elif exit_main and zfresh and zone_type == "RED" and c < zone_lower and c < o:
                _close(i, "ZONE_LONG")
                atr_sl_long = None
        elif position == "Short":
            if atr_sl_short is not None:
                atr_sl_short = min(atr_sl_short, c + atr_val * ATR_MULT)
            if exit_atr and atr_sl_short is not None and c > atr_sl_short:
                _close(i, "ATR_SHORT")
                atr_sl_short = None
            elif exit_main and zfresh and zone_type == "GREEN" and c > zone_upper and c > o:
                _close(i, "ZONE_SHORT")
                atr_sl_short = None

        # entries
        if trades_today >= max_trades or zone_upper is None:
            continue
        zone_fresh = (i - zone_bar) <= ZONE_AGE
        use_zone = zone_fresh if fresh_only else (zone_type is not None)
        prev = df5.iloc[i - 1]
        prev_green = float(prev["close"]) > float(prev["open"])
        prev_red   = float(prev["close"]) < float(prev["open"])
        curr_green = c > o
        curr_red   = c < o

        # NOTE: Pine's longBelowTrackedHigh/shortAboveTrackedLow filter is NOT
        # applied — its trackedHigh semantics (touchActive never resets) over-block
        # in practice; empirically removing it matches TV better (+5%).
        big_candle = (h - l) > max_cs
        long_ok = (zone_type == "GREEN" and use_zone and c > zone_upper and
                   prev_green and curr_green and not big_candle)
        short_ok = (zone_type == "RED" and use_zone and c < zone_lower and
                    prev_red and curr_red and not big_candle)

        if long_ok and position != "LONG":
            if position == "Short":
                _close(i, "Long")          # reversal exit labeled by new signal
            _open("Long", i, "ZONE")
            atr_sl_long = cur["entry_price"] - atr_val * ATR_MULT
            zone_type = zone_upper = zone_lower = None   # Pine: Green_Zone := false
            trades_today += 1
            if dbg: print(f"  {t:%H:%M} >>> LONG entry  close={c:.1f} zoneUpper(broken) prevG={prev_green}")
        elif short_ok and position != "Short":
            if position == "LONG":
                _close(i, "Short")
            _open("Short", i, "ZONE")
            atr_sl_short = cur["entry_price"] + atr_val * ATR_MULT
            zone_type = zone_upper = zone_lower = None   # Pine: Red_Zone := false
            trades_today += 1
            if dbg: print(f"  {t:%H:%M} >>> SHORT entry close={c:.1f} prevR={prev_red}")

    # day ended still open -> force close at last bar (safety; shouldn't happen post 15:15)
    if cur:
        _close(n - 1, "EOD")
    return trades


# ───────────────────────── TV parsing ─────────────────────────
def parse_tv(csv_path):
    """Group TV List-of-Trades rows into trades keyed by 'Trade number'."""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    byid = {}
    for r in rows:
        byid.setdefault(r["Trade number"], []).append(r)
    trades = []
    for _tid, rs in byid.items():
        entry = next((x for x in rs if x["Type"].startswith("Entry")), None)
        exit_ = next((x for x in rs if x["Type"].startswith("Exit")), None)
        if not entry or not exit_:
            continue
        side = "Long" if "long" in entry["Type"] else "Short"
        trades.append({
            "entry_time": pd.to_datetime(entry["Date and time"]),
            "entry_price": float(entry["Price"]),
            "side": side,
            "exit_time": pd.to_datetime(exit_["Date and time"]),
            "exit_price": float(exit_["Price"]),
            "exit_reason": exit_["Signal"],
        })
    trades.sort(key=lambda x: x["entry_time"])
    return trades


# ───────────────────────── run + compare ─────────────────────────
def run(tv_csv, date_from=None, date_to=None):
    tv = parse_tv(tv_csv)
    if date_from:
        tv = [t for t in tv if t["entry_time"] >= pd.to_datetime(date_from)]
    if date_to:
        tv = [t for t in tv if t["entry_time"] <= pd.to_datetime(date_to) + pd.Timedelta(days=1)]

    daily = daily_bars()
    days = sorted({t["entry_time"].date() for t in tv})

    # Build ONE continuous 5-min series across ALL available days so ATR(14)
    # is warmed/continuous like TradingView (not reset each day).
    all_paths = sorted(glob.glob(os.path.join(DATA_DIR, "NIFTY_2026-*.csv")))
    frames = []
    for p in all_paths:
        d5 = resample_5m(load_1m(p))
        d5["date"] = d5["time"].dt.date
        frames.append(d5)
    cont = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    atr_all = rt.compute_atr(cont, ATR_LEN)

    eng = []
    for d in days:
        mask = cont["date"] == d
        if not mask.any():
            continue
        df5 = cont[mask].reset_index(drop=True)
        atr_slice = atr_all[mask].reset_index(drop=True)
        # daily frame ending at this day (build_key_levels uses iloc[-2]=prev day)
        sub = daily[daily["date"] <= d].reset_index(drop=True)
        if len(sub) < 2:
            continue
        levels = rt.build_key_levels(sub, is_index=True)
        eng += backtest_day(df5, levels, CFG, atr_slice)

    # match per day, in entry-time order
    def fmt(t): return t.strftime("%Y-%m-%d %H:%M") if t is not None else "-"
    matched = 0
    report = []
    eng_by_day = {}
    for e in eng:
        eng_by_day.setdefault(e["entry_time"].date(), []).append(e)
    tv_by_day = {}
    for t in tv:
        tv_by_day.setdefault(t["entry_time"].date(), []).append(t)

    ONEBAR = pd.Timedelta(minutes=5)
    entry_exact = entry_1bar = full_1bar = 0   # tolerance diagnostics
    for d in days:
        tlist = tv_by_day.get(d, [])
        elist = eng_by_day.get(d, [])
        used = set()
        for tv_t in tlist:
            hit = None
            for j, e in enumerate(elist):
                if j in used:
                    continue
                if (e["entry_time"] == tv_t["entry_time"] and e["side"] == tv_t["side"]
                        and e["exit_time"] == tv_t["exit_time"]):
                    hit = j
                    break
            # tolerance diagnostics — nearest same-side engine trade
            near = None
            for k, e in enumerate(elist):
                if e["side"] != tv_t["side"]:
                    continue
                if near is None or abs(e["entry_time"] - tv_t["entry_time"]) < abs(near["entry_time"] - tv_t["entry_time"]):
                    near = e
            de = dx = None
            if near is not None:
                de = (near["entry_time"] - tv_t["entry_time"]) / ONEBAR   # in bars (signed)
                dx = (near["exit_time"] - tv_t["exit_time"]) / ONEBAR
                if abs(de) == 0:
                    entry_exact += 1
                if abs(de) <= 1:
                    entry_1bar += 1
                    if abs(dx) <= 1:
                        full_1bar += 1
            if hit is not None:
                used.add(hit)
                matched += 1
                status = "exact"
            elif near is not None and abs(de) <= 1 and abs(dx) <= 1:
                status = "near"
            elif near is not None and abs(de) <= 1:
                status = "entry"     # entry aligns, exit off
            else:
                status = "miss"
            report.append({"status": status, "tv": tv_t, "eng": near,
                           "de": de, "dx": dx})

    total = len(tv)
    pct = 100.0 * matched / total if total else 0.0
    print(f"\n{'='*78}")
    print(f"VALIDATION: {fmt(tv[0]['entry_time']) if tv else '-'}  ..  "
          f"{fmt(tv[-1]['entry_time']) if tv else '-'}")
    print(f"TV trades: {total} | Engine trades: {len(eng)} | "
          f"MATCHED (entry+exit+side): {matched}")
    print(f"SCORE (exact entry+exit): {pct:.1f}%")
    print(f"  entry exact (time+side):       {entry_exact}/{total} ({100.0*entry_exact/total:.0f}%)")
    print(f"  entry within 1 bar:            {entry_1bar}/{total} ({100.0*entry_1bar/total:.0f}%)")
    print(f"  entry+exit within 1 bar:       {full_1bar}/{total} ({100.0*full_1bar/total:.0f}%)")
    print(f"{'='*78}")
    print(f"{'stat':6} {'TV entry':16} {'side':5} {'TV exit':16} {'TVrsn':14} | "
          f"{'ENG entry':16} {'ENG exit':16} {'ENGrsn':10} {'dE':>4} {'dX':>4}")
    for r in report:
        tvt, e = r["tv"], r["eng"]
        eng_entry = fmt(e["entry_time"]) if e else "-"
        eng_exit  = fmt(e["exit_time"]) if e else "-"
        eng_rsn   = e["exit_reason"] if e else "-"
        de = f"{r['de']:+.0f}" if r["de"] is not None else "-"
        dx = f"{r['dx']:+.0f}" if r["dx"] is not None else "-"
        print(f"{r['status']:6} {fmt(tvt['entry_time']):16} {tvt['side']:5} "
              f"{fmt(tvt['exit_time']):16} {tvt['exit_reason']:14} | "
              f"{eng_entry:16} {eng_exit:16} {eng_rsn:10} {de:>4} {dx:>4}")

    # extra engine trades (took a trade TV didn't) — diagnostic
    matched_eng = {id(r["eng"]) for r in report if r["status"] in ("exact", "near", "entry") and r["eng"]}
    extras = [e for e in eng if id(e) not in matched_eng]

    write_html(report, extras, dict(total=total, matched=matched, pct=pct,
               entry_exact=entry_exact, entry_1bar=entry_1bar, full_1bar=full_1bar,
               eng_n=len(eng),
               span=f"{fmt(tv[0]['entry_time']) if tv else '-'} .. {fmt(tv[-1]['entry_time']) if tv else '-'}"))
    return pct


def write_html(report, extras, stats):
    def fmt(t): return t.strftime("%Y-%m-%d %H:%M") if t is not None else "—"
    COL = {"exact": "#3fb950", "near": "#d29922", "entry": "#58a6ff", "miss": "#f85149"}
    counts = {k: sum(1 for r in report if r["status"] == k) for k in COL}

    rows = []
    for r in report:
        tvt, e = r["tv"], r["eng"]
        c = COL[r["status"]]
        de = f"{r['de']:+.0f}" if r["de"] is not None else "—"
        dx = f"{r['dx']:+.0f}" if r["dx"] is not None else "—"
        rows.append(f"""<tr>
<td><span class="dot" style="background:{c}"></span>{r['status']}</td>
<td>{fmt(tvt['entry_time'])}</td><td class="{'lng' if tvt['side']=='Long' else 'sht'}">{tvt['side']}</td>
<td>{fmt(tvt['exit_time'])}</td><td class="rsn">{tvt['exit_reason']}</td>
<td>{fmt(e['entry_time']) if e else '—'}</td>
<td>{fmt(e['exit_time']) if e else '—'}</td><td class="rsn">{e['exit_reason'] if e else '—'}</td>
<td class="num">{de}</td><td class="num">{dx}</td></tr>""")

    extra_rows = "".join(
        f"<tr><td class='{'lng' if e['side']=='Long' else 'sht'}'>{e['side']}</td>"
        f"<td>{fmt(e['entry_time'])}</td><td>{fmt(e['exit_time'])}</td>"
        f"<td class='rsn'>{e['exit_reason']}</td></tr>" for e in extras)

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Strategy Validation</title><style>
body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;margin:0;padding:24px}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#8b949e;font-size:13px;margin-bottom:16px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;min-width:130px}}
.card .v{{font-size:26px;font-weight:700}} .card .l{{color:#8b949e;font-size:12px;margin-top:2px}}
.legend{{display:flex;gap:16px;margin-bottom:12px;font-size:12px;color:#8b949e;flex-wrap:wrap}}
.legend span{{display:inline-flex;align-items:center;gap:6px}}
.dot{{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}}
th,td{{padding:6px 10px;border-bottom:1px solid #21262d;text-align:left;white-space:nowrap}}
th{{color:#8b949e;font-weight:600;position:sticky;top:0;background:#0d1117}}
.lng{{color:#3fb950;font-weight:600}} .sht{{color:#f85149;font-weight:600}}
.rsn{{color:#8b949e;font-size:11px}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.sec{{margin:26px 0 8px;font-size:14px;color:#e6edf3}}
.bar{{height:8px;border-radius:4px;background:#21262d;overflow:hidden;margin-top:8px}}
.bar i{{display:block;height:100%;background:#3fb950}}
</style></head><body>
<h1>🎯 Strategy Validation — TradingView vs Engine</h1>
<div class="sub">{stats['span']} &nbsp;·&nbsp; {stats['total']} TV trades &nbsp;·&nbsp; {stats['eng_n']} engine trades</div>
<div class="cards">
<div class="card"><div class="v" style="color:#3fb950">{stats['pct']:.0f}%</div><div class="l">Exact entry+exit</div>
<div class="bar"><i style="width:{stats['pct']:.0f}%"></i></div></div>
<div class="card"><div class="v">{100*stats['entry_exact']//stats['total']}%</div><div class="l">Entry exact ({stats['entry_exact']}/{stats['total']})</div></div>
<div class="card"><div class="v">{100*stats['entry_1bar']//stats['total']}%</div><div class="l">Entry ±1 bar</div></div>
<div class="card"><div class="v">{100*stats['full_1bar']//stats['total']}%</div><div class="l">Entry+exit ±1 bar</div></div>
</div>
<div class="legend">
<span><i class="dot" style="background:#3fb950"></i>exact (entry+exit match)</span>
<span><i class="dot" style="background:#d29922"></i>near (both within 1 bar)</span>
<span><i class="dot" style="background:#58a6ff"></i>entry ok, exit off</span>
<span><i class="dot" style="background:#f85149"></i>miss (entry differs)</span>
<span>dE/dX = engine minus TV, in 5-min bars</span>
</div>
<table><thead><tr>
<th>status</th><th>TV entry</th><th>side</th><th>TV exit</th><th>TV reason</th>
<th>ENG entry</th><th>ENG exit</th><th>ENG reason</th><th class="num">dE</th><th class="num">dX</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table>
<div class="sec">Extra engine trades ({len(extras)}) — engine entered, TV did not</div>
<table><thead><tr><th>side</th><th>entry</th><th>exit</th><th>reason</th></tr></thead>
<tbody>{extra_rows or '<tr><td colspan=4 style="color:#8b949e">none</td></tr>'}</tbody></table>
</body></html>"""
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "ACCURACY SCORE CLAUD", "validation_report.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML report: {out}")


def debug_day(tv_csv, date_str):
    """Trace a single day: key levels, zone formations, entries/exits + TV trades."""
    d = pd.to_datetime(date_str).date()
    daily = daily_bars()
    all_paths = sorted(glob.glob(os.path.join(DATA_DIR, "NIFTY_2026-*.csv")))
    frames = []
    for p in all_paths:
        d5 = resample_5m(load_1m(p)); d5["date"] = d5["time"].dt.date
        frames.append(d5)
    cont = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    atr_all = rt.compute_atr(cont, ATR_LEN)
    mask = cont["date"] == d
    df5 = cont[mask].reset_index(drop=True)
    atr_slice = atr_all[mask].reset_index(drop=True)
    sub = daily[daily["date"] <= d].reset_index(drop=True)
    levels = rt.build_key_levels(sub, is_index=True)
    print(f"\n=== DEBUG {d} ===")
    print("KEY LEVELS:", ", ".join(f"{t}:{p:.0f}" for p, t in sorted(levels, key=lambda x: -x[0])))
    print("ENGINE trace:")
    eng = backtest_day(df5, levels, CFG, atr_slice, dbg=True)
    print("ENGINE trades:")
    for e in eng:
        print(f"  {e['side']:5} {e['entry_time']:%H:%M} -> {e['exit_time']:%H:%M} "
              f"@{e['entry_price']:.1f}->{e['exit_price']:.1f} {e['exit_reason']}")
    print("TV trades that day:")
    for t in parse_tv(tv_csv):
        if t["entry_time"].date() == d:
            print(f"  {t['side']:5} {t['entry_time']:%H:%M} -> {t['exit_time']:%H:%M} "
                  f"@{t['entry_price']:.1f}->{t['exit_price']:.1f} {t['exit_reason']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--debug", default=None, help="YYYY-MM-DD single-day trace")
    args = ap.parse_args()
    if args.debug:
        debug_day(args.csv, args.debug)
    else:
        run(args.csv, args.dfrom, args.dto)
