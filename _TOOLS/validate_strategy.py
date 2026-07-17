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
import re
from datetime import time as dtime

import sys

import numpy as np
import pandas as pd

# _paths bootstrap (CLAUDE.md, "Directly-run naya script"): this file lives in
# _TOOLS/, so put the project root on sys.path FIRST, then import _paths, which
# puts strategies/live, _core, _data etc. on it too. Without this the very next
# line dies with ModuleNotFoundError: range_trader — which is exactly what has
# happened since the 2026-07-09 folder refactor. This harness produced the 90.2%
# number everyone still quotes, and it has not been runnable since.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401,E402

import range_trader as rt  # noqa: E402

_WIN_DATA_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY"
if os.path.isdir(_WIN_DATA_DIR):
    DATA_DIR = _WIN_DATA_DIR
else:
    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "_TRADING_DATA", "Index", "NIFTY")
EXIT_HM  = dtime(15, 15)   # 3:15 daily square-off

# Engine config — mirror the TradingView Pine settings
CFG = {
    # 40, not 25. Read off the user's ACTUAL script (Ars_Auto_Rev_Chain_common,
    # 2026-07-17), where these are hardcoded rather than inputs:
    #   maxCandleSize=40  maxTradesPerDay=2  MainExit_Toggle=true
    #   AtrExit_Toggle=true  SL_Blw_Fib_Exit_Tog=false  HawaME_toggle=false
    #   useFreshZoneOnly=true  max_jump_pct_input=50
    # _PINE/range_chain.pine is NOT that script — it's an older variant whose
    # input DEFAULTS were being read as if they were the live settings. Don't
    # take Pine values from that file; take them from the running script.
    "max_candle_size": 40,
    "use_fresh_zone_only": True,
    "hawa_me_zone": False,
    "exit_atr": True,
    "exit_main": True,            # Pine MainExit_Toggle (Zone Exit) = true
    # Pine maxTradesPerDay. This CFG's entire job is to mirror the Pine, so it
    # tracks it: the Pine moved 4 -> 2 on 2026-07-17 (house rule is 2 entries
    # per day; Pine counts entries only, so entry+exit = 1 trade on both sides).
    # NOTE: the recorded 90.2% exact / 93% entry score was measured at 4,
    # against a TV export from a 4-trade run — it is NOT reproducible at 2.
    # Re-score from a fresh TV export before quoting a fidelity number again.
    "max_trades_per_symbol": 2,
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
    """Collect every trade for ONE day, with TradingView's fill convention.

    This used to BE the engine — a hand-copy of run_signal_engine that had
    silently become the better one: every Pine-fidelity fix from the 90.2% work
    landed here and never in the file that places orders. Measured 2026-07-17
    against the user's own TV export: this logic 75.3% of TV, the live engine
    49.5%. Six drifts, all in this copy's favour (full list in
    range_trader.run_signal_engine's docstring).

    So the logic moved INTO the live engine and this is now a wrapper. What
    stays here is the only part that is genuinely a backtest concern:
    TradingView fills a signal at the NEXT bar's open, while a live strategy
    acts on the bar it signalled. Same decisions, different fill.

    `atr_series` is passed straight through: callers hand this one day at a
    time, so ATR(14) has to arrive pre-warmed and continuous or it restarts cold
    every morning. (Live warms it from its own multi-day window instead.)
    """
    trades = []
    if df5 is None or len(df5) < 20 or not key_levels:
        return trades

    ecfg = dict(cfg)
    # This file's config calls the zone-exit knob `exit_main`; the engine (and
    # the live config, and the RMS UI) call it `exit_zone`. Same knob, two names
    # — one of the six drifts. Translate rather than rename a public field.
    if "exit_main" in ecfg and "exit_zone" not in ecfg:
        ecfg["exit_zone"] = ecfg["exit_main"]

    raw = []
    rt.run_signal_engine(df5, key_levels, ecfg, trades_out=raw, atr_series=atr_series)

    n = len(df5)

    def _fill(i):
        """TV convention: a signal on bar i fills at the NEXT bar's open."""
        j = i + 1 if i + 1 < n else i
        b = df5.iloc[j]
        return b["time"], float(b["open"])

    cur = None
    for r in raw:
        i = r["bar"]
        if r["kind"].startswith("ENTRY"):
            side = "Long" if r["kind"] == "ENTRY_LONG" else "Short"
            if cur is not None:          # reversal — TV labels the exit by the new signal
                t, price = _fill(i)
                cur.update(exit_time=t, exit_price=price, exit_reason=side)
                trades.append(cur)
            t, price = _fill(i)
            cur = dict(entry_time=t, entry_price=price, side=side,
                       exit_time=None, exit_price=None, exit_reason=None,
                       entry_reason="ZONE")
            if dbg:
                print(f"  {df5.iloc[i]['time']:%H:%M} >>> {side.upper()} entry close={r['price']:.1f}")
        elif cur is not None:
            t, price = _fill(i)
            reason = "ATR_LONG" if r["kind"] == "EXIT_LONG" and r["reason"] == "ATR_TRAILING" else                      "ATR_SHORT" if r["kind"] == "EXIT_SHORT" and r["reason"] == "ATR_TRAILING" else                      r["reason"]
            cur.update(exit_time=t, exit_price=price, exit_reason=reason)
            trades.append(cur)
            cur = None

    # The engine squares off at 15:15 itself, so this is only a safety net for a
    # day whose data ends before then.
    if cur is not None:
        cur.update(exit_time=df5.iloc[-1]["time"], exit_price=float(df5.iloc[-1]["close"]),
                   exit_reason="EOD")
        trades.append(cur)
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


def parse_log(path):
    """Parse the consistent Pine Logs export (ZONE/SIGNAL/EXIT from ONE run) into
    trades. Times are the signal/exit bar; +1 bar (5min) = TradingView fill, to
    match the engine's next-bar-open convention. Handles reversals."""
    BAR = pd.Timedelta(minutes=5)
    rows = []
    for line in open(path, encoding="utf-8"):
        msg = line.split(",", 1)[-1] if "," in line else line
        rows.append(msg.strip())
    events = []
    for msg in rows:
        m = re.search(r'SIGNAL (LONG|SHORT) (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) close=([\d.]+)', msg)
        if m:
            events.append(("SIG", "Long" if m.group(1) == "LONG" else "Short",
                           pd.to_datetime(m.group(2)) + BAR, float(m.group(3)), None))
            continue
        m = re.search(r'EXIT (LONG|SHORT) (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) reason=(\S+) close=([\d.]+)', msg)
        if m:
            events.append(("EXIT", "Long" if m.group(1) == "LONG" else "Short",
                           pd.to_datetime(m.group(2)) + BAR, float(m.group(4)), m.group(3)))
    events.sort(key=lambda e: e[2])
    trades, cur = [], None
    for typ, side, t, px, reason in events:
        if typ == "SIG":
            if cur is None:                  # flat -> open
                cur = {"entry_time": t, "entry_price": px, "side": side,
                       "exit_time": None, "exit_price": None, "exit_reason": None}
            elif cur["side"] != side:        # reversal -> close prior, open new
                cur["exit_time"], cur["exit_price"], cur["exit_reason"] = t, px, "Reversal"
                trades.append(cur)
                cur = {"entry_time": t, "entry_price": px, "side": side,
                       "exit_time": None, "exit_price": None, "exit_reason": None}
            # else: already in this direction & pyramiding=0 -> SIGNAL ignored (no trade)
        elif typ == "EXIT" and cur:
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = t, px, reason
            trades.append(cur)
            cur = None
    # drop entries that fill at/after 15:15 (not holdable — same rule as engine)
    trades = [t for t in trades if t["entry_time"].time() < pd.Timestamp("15:15").time()]
    return trades


# ───────────────────────── run + compare ─────────────────────────
def run(tv_csv, date_from=None, date_to=None, tv_trades=None):
    tv = tv_trades if tv_trades is not None else parse_tv(tv_csv)
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

    # Skip TV trades on days we have NO 5-min data for (data gap != engine miss)
    have_days = set(cont["date"].unique())
    skipped = sorted({t["entry_time"].date() for t in tv if t["entry_time"].date() not in have_days})
    tv = [t for t in tv if t["entry_time"].date() in have_days]
    days = sorted({t["entry_time"].date() for t in tv})
    if skipped:
        print(f"[skipped {len(skipped)} TV days with no 5-min data: "
              f"{', '.join(str(d) for d in skipped)}]")

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
    # Project ROOT, not this file's dir. "ACCURACY SCORE CLAUD/" sits at the
    # root; the 2026-07-09 refactor moved this file into _TOOLS/ and the path
    # followed it, so every run died here after doing all the work. Same shape
    # as the import break above (CLAUDE.md: "Moved module me path banate waqt
    # `Path(__file__).parent...` mat maano ki root hai").
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "ACCURACY SCORE CLAUD")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "validation_report.html")
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
    ap.add_argument("--signals", default=None, help="Pine Logs export (consistent ZONE/SIGNAL/EXIT) to score against")
    args = ap.parse_args()
    if args.debug:
        debug_day(args.csv, args.debug)
    elif args.signals:
        run(None, args.dfrom, args.dto, tv_trades=parse_log(args.signals))
    else:
        run(args.csv, args.dfrom, args.dto)
