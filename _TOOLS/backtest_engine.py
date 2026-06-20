"""
backtest_engine.py — generic date-range backtester for ANY strategy type
(range / rsi / ema). Used by the dashboard's "📊 Backtest" button in the
strategy Run modal.

Loads NIFTY 1-min CSVs from ._TRADING DATA, replays each strategy's actual
signal function bar-by-bar (same logic as the live trader), and returns
chart-ready JSON: candles + python trades (+ optional TradingView trades for
side-by-side comparison, parsed from a Pine Logs export via validate_strategy.parse_log).

CLI (dashboard calls run_backtest() directly, but this also works standalone):
  python backtest_engine.py --strategy range --from 2026-05-01 --to 2026-05-19
"""

import argparse
import datetime
import glob
import json
import os
import sys
import time as _time
from datetime import time as dtime

import pandas as pd
import requests

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADERS_DIR = os.path.join(BASE_DIR, "_TRADERS")
TOOLS_DIR   = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TRADERS_DIR)
sys.path.insert(0, TOOLS_DIR)
sys.path.insert(0, BASE_DIR)   # dhan_master.py lives at project root

import range_trader as rt
import rsi_trader as rsit
import nifty_ema_trader as emat
import validate_strategy as vs   # reuse backtest_day (range) + parse_log (TV)

# Dev machine has the big pre-downloaded NIFTY store at this fixed Windows
# path (shared by validate_strategy.py / generate_june_mfe.py too) — use it
# when present. Anywhere else (e.g. the Linux VPS) falls back to a project-
# local folder that auto_download fills in on demand, so this still works
# without that path existing.
_WIN_DATA_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY"
if os.path.isdir(_WIN_DATA_DIR):
    DATA_DIR = _WIN_DATA_DIR
else:
    DATA_DIR = os.path.join(BASE_DIR, "_TRADING_DATA", "Index", "NIFTY")
    os.makedirs(DATA_DIR, exist_ok=True)
TF_MIN   = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}
EXIT_HM  = dtime(15, 15)
CONFIG_FILE = os.path.join(BASE_DIR, "data", "config.json")   # Dhan jwt_token + client_id
NIFTY_SEC_ID = "13"   # IDX_I — same id used everywhere else in this project


# ───────────────────────── auto-download missing days ─────────────────────────
def _decode_client_id(jwt_token):
    """dhanClientId lives inside the JWT payload itself — decode it as a
    fallback for tokens saved before client_id was stored separately."""
    import base64
    try:
        payload = jwt_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("dhanClientId")
    except Exception:
        return None


def _dhan_creds():
    try:
        cfg = json.loads(open(CONFIG_FILE, encoding="utf-8").read())
        token = cfg.get("jwt_token")
        cid = cfg.get("client_id") or _decode_client_id(token)
        if not token or not cid:
            return None, None
        return token, cid
    except Exception:
        return None, None


def _fetch_nifty_day(date_str, token, cid):
    """One day of NIFTY 1-min bars from Dhan /v2/charts/intraday. None on
    failure/holiday (no bars) — caller treats that as 'skip, not fatal'."""
    try:
        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            headers={"access-token": token, "client-id": cid, "Content-Type": "application/json"},
            json={"securityId": NIFTY_SEC_ID, "exchangeSegment": "IDX_I", "instrument": "INDEX",
                  "expiryCode": 0, "fromDate": date_str, "toDate": date_str},
            timeout=15,
        )
        d = r.json()
        if d.get("errorCode") == "DH-904":   # rate limit — caller backs off and retries
            return "RATE_LIMIT"
        ts = d.get("timestamp") or []
        if not ts:
            return None
        rows = []
        for t, o, h, l, c in zip(ts, d["open"], d["high"], d["low"], d["close"]):
            dt = datetime.datetime.fromtimestamp(t)
            rows.append({"Datetime": dt.strftime("%Y-%m-%d %H:%M:%S"), "Open": o, "High": h,
                         "Low": l, "Close": c, "Volume": 0})
        return pd.DataFrame(rows)
    except Exception:
        return None


def ensure_nifty_data(date_from, date_to):
    """Download any missing trading-day NIFTY 1-min CSVs in [date_from, date_to]
    from Dhan before the backtest runs. Silently does nothing if creds are
    missing or every day is already on disk — never raises."""
    if not date_from or not date_to:
        return
    token, cid = _dhan_creds()
    if not token:
        return

    d = pd.to_datetime(date_from).date()
    end = pd.to_datetime(date_to).date()
    missing = []
    while d <= end:
        if d.weekday() < 5:   # Mon-Fri only
            fpath = os.path.join(DATA_DIR, f"NIFTY_{d.isoformat()}.csv")
            if not os.path.exists(fpath):
                missing.append(d)
        d += datetime.timedelta(days=1)
    if not missing:
        return

    print(f"[backtest_engine] downloading {len(missing)} missing NIFTY day(s) from Dhan...")
    for day in missing:
        date_str = day.isoformat()
        for attempt in range(3):
            df = _fetch_nifty_day(date_str, token, cid)
            if isinstance(df, str) and df == "RATE_LIMIT":
                _time.sleep((attempt + 1) * 5)
                continue
            break
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue   # holiday, no data, or rate-limited out — not fatal, just skip this day
        fpath = os.path.join(DATA_DIR, f"NIFTY_{date_str}.csv")
        df.to_csv(fpath, index=False)
        print(f"  + {date_str} ({len(df)} bars)")
        _time.sleep(1)   # be polite to the rate limit


# ───────────────────────── data loading ─────────────────────────
def load_1m_range(date_from=None, date_to=None):
    paths = sorted(glob.glob(os.path.join(DATA_DIR, "NIFTY_*.csv")))
    frames = []
    for p in paths:
        if "daily" in os.path.basename(p).lower():
            continue
        d = vs.load_1m(p)
        if d.empty:
            continue
        day = d["datetime"].iloc[0].date()
        if date_from and day < pd.to_datetime(date_from).date():
            continue
        if date_to and day > pd.to_datetime(date_to).date():
            continue
        frames.append(d)
    if not frames:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close"])
    return pd.concat(frames, ignore_index=True).sort_values("datetime").reset_index(drop=True)


def resample(df1, tf_min):
    if tf_min <= 1:
        return df1.rename(columns={"datetime": "time"})[["time", "open", "high", "low", "close"]]
    d = df1.set_index("datetime")
    r = pd.DataFrame({
        "open":  d["open"].resample(f"{tf_min}min").first(),
        "high":  d["high"].resample(f"{tf_min}min").max(),
        "low":   d["low"].resample(f"{tf_min}min").min(),
        "close": d["close"].resample(f"{tf_min}min").last(),
    }).dropna().reset_index().rename(columns={"datetime": "time"})
    return r


def _fill(df, i):
    n = len(df)
    j = i + 1 if i + 1 < n else i
    row = df.iloc[j]
    return row["time"], float(row["open"])


# ───────────────────────── RANGE (reuses validate_strategy.backtest_day) ─────────────────────────
def _run_range(date_from, date_to, cfg):
    cont1 = load_1m_range(date_from, date_to)
    if cont1.empty:
        return [], pd.DataFrame()
    daily = vs.daily_bars()
    days = sorted(cont1["datetime"].dt.date.unique())
    frames = []
    for d in days:
        d1 = cont1[cont1["datetime"].dt.date == d]
        d5 = vs.resample_5m(d1)
        d5["date"] = d5["time"].dt.date
        frames.append(d5)
    cont5 = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    atr_all = rt.compute_atr(cont5, vs.ATR_LEN)

    rcfg = {
        "max_candle_size":        cfg.get("max_candle_size", 25),
        "use_fresh_zone_only":    cfg.get("use_fresh_zone_only", True),
        "hawa_me_zone":           cfg.get("hawa_me_zone", False),
        "exit_atr":               cfg.get("exit_atr", True),
        "exit_main":              cfg.get("exit_zone", False) or True,
        "max_trades_per_symbol":  cfg.get("max_trades_per_symbol", 4),
    }
    eng = []
    for d in days:
        mask = cont5["date"] == d
        if not mask.any():
            continue
        df5 = cont5[mask].reset_index(drop=True)
        atr_slice = atr_all[mask].reset_index(drop=True)
        sub = daily[daily["date"] <= d].reset_index(drop=True)
        if len(sub) < 2:
            continue
        levels = rt.build_key_levels(sub, is_index=True)
        eng += vs.backtest_day(df5, levels, rcfg, atr_slice)
    return eng, cont5


# rsi_trader.compute_signal() is written for the LIVE feed, where the last
# candle row is still forming — it deliberately reads iloc[-2]/iloc[-3] to
# get the last *closed* bar. In the backtest, df.iloc[:i+1] already ends
# exactly at the closed bar i (no forming bar), so reusing compute_signal()
# as-is reads bar i-1 instead of bar i — every signal lands one full bar
# late vs TV (TV's bar_index is i, ours was i-1, then _fill() next-bar-fills
# from the wrong bar). This local copy uses iloc[-1]/iloc[-2] to match the
# backtest's own "last row = closed bar i" convention instead.
def _rsi_signal_backtest(df, period, oversold, overbought, rsi_exit, pos):
    if len(df) < period + 5:
        return None, None
    rsi = rsit.compute_rsi(df["close"], period)
    cur = float(rsi.iloc[-1])
    prv = float(rsi.iloc[-2])

    if pos == 1 and cur >= rsi_exit:
        return "EXIT", round(cur, 1)
    if pos == -1 and cur <= rsi_exit:
        return "EXIT", round(cur, 1)
    if pos != 0:
        return None, round(cur, 1)

    if prv <= oversold and cur > oversold:
        return "BUY", round(cur, 1)
    if prv >= overbought and cur < overbought:
        return "SELL", round(cur, 1)
    return None, round(cur, 1)


# Same live-vs-backtest bar-convention mismatch as RSI above: emat.compute_signal()
# is written for the live feed (iloc[-2]/iloc[-3] = last closed bar, because the
# live candle list always has one still-forming bar at the end). In the backtest
# df.iloc[:i+1] already ends at the closed bar i, so calling the live function
# as-is reads bar i-1 — one bar late vs TV. Local copy uses iloc[-1]/iloc[-2].
def _ema_signal_backtest(df, fast, slow):
    if len(df) < slow + 5:
        return None
    close = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    cf, cs = ema_fast.iloc[-1], ema_slow.iloc[-1]
    pf, ps = ema_fast.iloc[-2], ema_slow.iloc[-2]
    if pf <= ps and cf > cs:
        return "BUY"
    if pf >= ps and cf < cs:
        return "SELL"
    return None


# TradingView's ta.rsi()/ta.ema() run continuously over the chart's full
# history, so by the time the requested backtest window starts they're
# already converged. Our backtest only loads data starting at date_from —
# with no prior bars, the Wilder EMA (RSI) / EMA seed cold-starts and won't
# match TV until enough bars accumulate (visibly: early-window trades show
# "No match", later ones converge to "Exact"). Pull this many EXTRA calendar
# days before date_from purely as RSI/EMA warm-up, then drop any trade that
# entered before the actually-requested date_from.
_WARMUP_CALENDAR_DAYS = 45


def _buffered_from(date_from):
    if not date_from:
        return date_from
    return (pd.to_datetime(date_from) - pd.Timedelta(days=_WARMUP_CALENDAR_DAYS)).strftime("%Y-%m-%d")


# ───────────────────────── RSI (generic growing-window replay) ─────────────────────────
def _run_rsi(date_from, date_to, cfg):
    tf_min = TF_MIN.get(cfg.get("timeframe", "5m"), 5)
    buffered_from = _buffered_from(date_from)
    ensure_nifty_data(buffered_from, date_to)
    cont1 = load_1m_range(buffered_from, date_to)
    if cont1.empty:
        return [], pd.DataFrame()
    df = resample(cont1, tf_min)   # includes the warm-up buffer — RSI computed over this
    cutoff_ts = pd.to_datetime(date_from) if date_from else None

    period     = cfg.get("rsi_period", 14)
    oversold   = cfg.get("oversold", 30)
    overbought = cfg.get("overbought", 70)
    rsi_exit   = cfg.get("rsi_exit", 50)
    max_trades = cfg.get("max_trades_per_symbol", 1)

    trades, pos, cur = [], 0, None
    cur_day, trades_today = None, 0
    n = len(df)
    for i in range(period + 5, n):
        row = df.iloc[i]
        t = row["time"]
        if cur_day != t.date():
            cur_day, trades_today = t.date(), 0

        # Forced 3:15 cutoff must execute WITHIN this bar (its own close) —
        # NOT via _fill()'s next-bar-open convention, which is for signal
        # exits where TV's strategy.close() genuinely fills next bar. Using
        # next-bar fill here pushed the exit to 15:20+ (past the cutoff),
        # which TV (no such bug) never matched — TV just held into next day.
        if pos != 0 and t.time() >= EXIT_HM:
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = t, float(row["close"]), "3:15 Daily Exit"
            trades.append(cur); cur, pos = None, 0
            continue

        sig, _rsi_val = _rsi_signal_backtest(df.iloc[:i + 1], period, oversold, overbought, rsi_exit, pos)

        if sig == "EXIT" and pos != 0:
            ft, fp = _fill(df, i)
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = ft, fp, "RSI_EXIT"
            trades.append(cur); cur, pos = None, 0
        elif sig in ("BUY", "SELL") and pos == 0 and trades_today < max_trades:
            fill_t, _ = _fill(df, i)
            if fill_t.time() >= EXIT_HM:
                continue
            ft, fp = _fill(df, i)
            side = "Long" if sig == "BUY" else "Short"
            cur = {"entry_time": ft, "entry_price": fp, "side": side,
                   "exit_time": None, "exit_price": None, "exit_reason": None}
            pos = 1 if sig == "BUY" else -1
            trades_today += 1

    if cur:
        last = df.iloc[-1]
        cur["exit_time"], cur["exit_price"], cur["exit_reason"] = last["time"], float(last["close"]), "EOD"
        trades.append(cur)

    # Drop the warm-up buffer now that RSI has had a chance to converge on
    # it — only trades/candles inside the actually-requested window matter.
    if cutoff_ts is not None:
        trades = [tr for tr in trades if tr["entry_time"] >= cutoff_ts]
        df = df[df["time"] >= cutoff_ts].reset_index(drop=True)
    return trades, df


# ───────────────────────── EMA (generic growing-window replay) ─────────────────────────
def _run_ema(date_from, date_to, cfg):
    tf_min = TF_MIN.get(cfg.get("timeframe", "1m"), 1)
    buffered_from = _buffered_from(date_from)
    ensure_nifty_data(buffered_from, date_to)
    cont1 = load_1m_range(buffered_from, date_to)
    if cont1.empty:
        return [], pd.DataFrame()
    df = resample(cont1, tf_min)   # includes the warm-up buffer — see _run_rsi's note
    cutoff_ts = pd.to_datetime(date_from) if date_from else None

    fast, slow = cfg.get("fast_ema", 9), cfg.get("slow_ema", 20)
    max_trades = cfg.get("max_trades_per_symbol", 2)

    trades, pos, cur = [], 0, None
    cur_day, trades_today = None, 0
    n = len(df)
    for i in range(slow + 5, n):
        row = df.iloc[i]
        t = row["time"]
        if cur_day != t.date():
            cur_day, trades_today = t.date(), 0

        # Same fix as RSI's forced-cutoff above — exit within bar i's own
        # close, not via next-bar-open fill (which pushed it past 15:15).
        if pos != 0 and t.time() >= EXIT_HM:
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = t, float(row["close"]), "3:15 Daily Exit"
            trades.append(cur); cur, pos = None, 0
            continue

        sig = _ema_signal_backtest(df.iloc[:i + 1], fast, slow)
        if not sig or trades_today >= max_trades:
            continue

        fill_t, _ = _fill(df, i)
        if fill_t.time() >= EXIT_HM:
            continue

        if pos != 0:
            is_reversal = (pos == 1 and sig == "SELL") or (pos == -1 and sig == "BUY")
            if not is_reversal:
                continue
            ft, fp = _fill(df, i)
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = ft, fp, "Reversal"
            trades.append(cur); cur, pos = None, 0

        ft, fp = _fill(df, i)
        side = "Long" if sig == "BUY" else "Short"
        cur = {"entry_time": ft, "entry_price": fp, "side": side,
               "exit_time": None, "exit_price": None, "exit_reason": None}
        pos = 1 if sig == "BUY" else -1
        trades_today += 1

    if cur:
        last = df.iloc[-1]
        cur["exit_time"], cur["exit_price"], cur["exit_reason"] = last["time"], float(last["close"]), "EOD"
        trades.append(cur)

    if cutoff_ts is not None:
        trades = [tr for tr in trades if tr["entry_time"] >= cutoff_ts]
        df = df[df["time"] >= cutoff_ts].reset_index(drop=True)
    return trades, df


_RUNNERS = {"range": _run_range, "rsi": _run_rsi, "rsi_v1": _run_rsi, "ema": _run_ema}


# ───────────────────────── TV comparison (mirrors validate_strategy.run matching) ─────────────────────────
def _match(eng, tv):
    # eng_by_day keeps (global_index_into_eng, trade) so match status can be
    # written back onto the original python trade — the Results page uses
    # this to flag exactly which rows didn't match TV, instead of only
    # showing an aggregate percentage.
    eng_by_day, tv_by_day = {}, {}
    for idx, e in enumerate(eng):
        eng_by_day.setdefault(e["entry_time"].date(), []).append((idx, e))
    for t in tv:
        tv_by_day.setdefault(t["entry_time"].date(), []).append(t)

    # Precedence so a trade already marked "exact" by one TV comparison isn't
    # downgraded by a later, weaker "near" comparison against another TV row.
    RANK = {"exact": 3, "near": 2, "entry": 1, "unmatched": 0}
    eng_status = ["unmatched"] * len(eng)

    ONEBAR = pd.Timedelta(minutes=5)
    matched, report = 0, []
    for d in sorted(tv_by_day):
        tlist, elist, used = tv_by_day[d], eng_by_day.get(d, []), set()
        for tv_t in tlist:
            hit = None
            for j, (gidx, e) in enumerate(elist):
                if j in used:
                    continue
                if (e["entry_time"] == tv_t["entry_time"] and e["side"] == tv_t["side"]
                        and e["exit_time"] == tv_t["exit_time"]):
                    hit = j
                    break
            near = None
            for gidx, e in elist:
                if e["side"] != tv_t["side"]:
                    continue
                if near is None or abs(e["entry_time"] - tv_t["entry_time"]) < abs(near[1]["entry_time"] - tv_t["entry_time"]):
                    near = (gidx, e)
            de = (near[1]["entry_time"] - tv_t["entry_time"]) / ONEBAR if near is not None else None
            dx = (near[1]["exit_time"] - tv_t["exit_time"]) / ONEBAR if near is not None else None
            if hit is not None:
                used.add(hit); matched += 1; status = "exact"
                hit_gidx = elist[hit][0]
            elif near is not None and de is not None and abs(de) <= 1 and dx is not None and abs(dx) <= 1:
                status = "near"
            elif near is not None and de is not None and abs(de) <= 1:
                status = "entry"
            else:
                status = "miss"
            mark_gidx = hit_gidx if hit is not None else (near[0] if near is not None else None)
            if mark_gidx is not None and RANK[status if status != "miss" else "unmatched"] > RANK[eng_status[mark_gidx]]:
                eng_status[mark_gidx] = status if status != "miss" else "unmatched"
            report.append({"status": status, "tv_entry": tv_t["entry_time"], "side": tv_t["side"]})
    total = len(tv)
    pct = 100.0 * matched / total if total else 0.0
    return {"total_tv": total, "total_eng": len(eng), "matched": matched, "pct": round(pct, 1),
            "report": report, "eng_status": eng_status}


# ───────────────────────── JSON shaping ─────────────────────────
def _candles_json(df):
    return [{"time": int(r["time"].timestamp()), "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"])} for _, r in df.iterrows()]


def _trades_json(trades, statuses=None):
    out = []
    for i, t in enumerate(trades):
        pnl = None
        if t.get("exit_price") is not None:
            pnl = (t["exit_price"] - t["entry_price"]) if t["side"] == "Long" else (t["entry_price"] - t["exit_price"])
        row = {
            "side": t["side"],
            "entry_time": int(t["entry_time"].timestamp()), "entry_price": round(t["entry_price"], 2),
            "exit_time": int(t["exit_time"].timestamp()) if t.get("exit_time") is not None else None,
            "exit_price": round(t["exit_price"], 2) if t.get("exit_price") is not None else None,
            "exit_reason": t.get("exit_reason"),
            "pnl": round(pnl, 2) if pnl is not None else None,
        }
        if statuses is not None:
            row["match_status"] = statuses[i]
        out.append(row)
    return out


def _parse_tv_csv_flexible(csv_path):
    """TradingView's 'List of Trades' export column names vary by TV version/
    locale (e.g. 'Trade #' vs 'Trade number', 'Date/Time' vs 'Date and time').
    validate_strategy.parse_tv() expects one exact set of names and KeyErrors
    on anything else — this matches by keyword instead of exact string."""
    import csv as _csv

    def _find(headers, *keywords_sets):
        for keywords in keywords_sets:
            for h in headers:
                hl = h.lower()
                if all(kw in hl for kw in keywords):
                    return h
        return None

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = _csv.DictReader(f)
        headers = reader.fieldnames or []
        col_id    = _find(headers, ("trade", "#"), ("trade", "number"), ("trade", "no"))
        col_type  = _find(headers, ("type",))
        col_date  = _find(headers, ("date", "time"), ("date",))
        col_price = _find(headers, ("price",))
        if not all([col_id, col_type, col_date, col_price]):
            raise ValueError(f"Unrecognized List-of-Trades CSV columns: {headers}")

        byid = {}
        for r in reader:
            byid.setdefault(r[col_id], []).append(r)

    trades = []
    for _tid, rs in byid.items():
        entry = next((x for x in rs if x[col_type].lower().startswith("entry")), None)
        exit_ = next((x for x in rs if x[col_type].lower().startswith("exit")), None)
        if not entry or not exit_:
            continue
        side = "Long" if "long" in entry[col_type].lower() else "Short"
        trades.append({
            "entry_time": pd.to_datetime(entry[col_date]),
            "entry_price": float(str(entry[col_price]).replace(",", "")),
            "side": side,
            "exit_time": pd.to_datetime(exit_[col_date]),
            "exit_price": float(str(exit_[col_price]).replace(",", "")),
            "exit_reason": exit_.get(_find(headers, ("signal",)) or "", ""),
        })
    trades.sort(key=lambda x: x["entry_time"])
    return trades


def _load_tv_trades(tv_path):
    """TV exports come in two shapes — and the file EXTENSION doesn't reliably
    tell you which: TradingView's Pine Logs panel "Export" button saves a
    Date,Message CSV (not .log!) containing our SIGNAL/EXIT log.info() lines,
    while the Strategy Tester's "List of Trades" export is a real trade-table
    CSV (Trade #/Type/Date/Price columns). Sniff the actual content instead.
    vs.parse_log() already strips up to the first comma per line, so it reads
    a Date,Message CSV directly (no comma-containing text in our log lines)."""
    with open(tv_path, encoding="utf-8-sig", errors="ignore") as f:
        head = f.read(4096)

    if "SIGNAL " in head or "EXIT " in head:
        return vs.parse_log(tv_path)

    try:
        return vs.parse_tv(tv_path)
    except KeyError:
        return _parse_tv_csv_flexible(tv_path)


# ───────────────────────── entry point ─────────────────────────
def run_backtest(strategy_type, cfg, date_from, date_to, tv_log_path=None):
    runner = _RUNNERS.get(strategy_type)
    if runner is None:
        return {"error": f"unsupported strategy type: {strategy_type}"}

    ensure_nifty_data(date_from, date_to)

    trades, df = runner(date_from, date_to, cfg)
    if df.empty:
        return {"error": "No 1-min data found for this date range, and auto-download failed "
                          "(check Dhan token in data/config.json, or it's a market holiday)."}

    wins = sum(1 for t in trades if t.get("exit_price") is not None and
               ((t["exit_price"] - t["entry_price"] > 0) if t["side"] == "Long" else (t["entry_price"] - t["exit_price"] > 0)))
    closed_pnls = [(t["exit_price"] - t["entry_price"]) if t["side"] == "Long" else (t["entry_price"] - t["exit_price"])
                   for t in trades if t.get("exit_price") is not None]
    pnl_pts = sum(closed_pnls)

    n_closed = len(closed_pnls)
    win_rate = round(wins / n_closed * 100, 1) if n_closed else None

    gains = sum(p for p in closed_pnls if p > 0)
    losses_sum = sum(-p for p in closed_pnls if p < 0)
    profit_factor = round(gains / losses_sum, 2) if losses_sum > 0 else None

    if n_closed >= 2:
        mean_pnl = pnl_pts / n_closed
        variance = sum((p - mean_pnl) ** 2 for p in closed_pnls) / (n_closed - 1)
        stdev = variance ** 0.5
        sharpe = round(mean_pnl / stdev * (n_closed ** 0.5), 2) if stdev > 0 else None
    else:
        sharpe = None

    equity_curve = []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for i, p in enumerate(closed_pnls, start=1):
        cum += p
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
        equity_curve.append({"trade_no": i, "cum_pnl": round(cum, 2)})
    max_drawdown = round(max_dd, 2) if n_closed else None

    accuracy = None
    tv_trades_json = None
    if tv_log_path and os.path.exists(tv_log_path):
        tv_trades = _load_tv_trades(tv_log_path)
        if date_from:
            tv_trades = [t for t in tv_trades if t["entry_time"] >= pd.to_datetime(date_from)]
        if date_to:
            tv_trades = [t for t in tv_trades if t["entry_time"] <= pd.to_datetime(date_to) + pd.Timedelta(days=1)]
        tv_trades_json = _trades_json(tv_trades)
        accuracy = _match(trades, tv_trades)

    result = {
        "candles": _candles_json(df),
        "trades": _trades_json(trades, statuses=accuracy["eng_status"] if accuracy else None),
        "summary": {"n_trades": len(trades), "wins": wins,
                    "losses": len(trades) - wins, "pnl_points": round(pnl_pts, 2),
                    "win_rate": win_rate, "profit_factor": profit_factor,
                    "sharpe": sharpe, "max_drawdown": max_drawdown,
                    "equity_curve": equity_curve},
        "tv_trades": tv_trades_json,
        "accuracy": accuracy,
    }
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=list(_RUNNERS.keys()))
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--signals", default=None, help="Pine Logs export for TV comparison")
    args = ap.parse_args()

    res = run_backtest(args.strategy, {}, args.dfrom, args.dto, tv_log_path=args.signals)
    if "error" in res:
        print("ERROR:", res["error"])
    else:
        print(f"Trades: {res['summary']['n_trades']}  Wins: {res['summary']['wins']}  "
              f"PnL pts: {res['summary']['pnl_points']}")
        if res["accuracy"]:
            print(f"TV match: {res['accuracy']['matched']}/{res['accuracy']['total_tv']} "
                  f"({res['accuracy']['pct']}%)")
