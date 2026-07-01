#!/usr/bin/env python3
"""
trader_dashboard.py — Web UI for Algo Trader
Run: python trader_dashboard.py
Open: http://72.61.173.32:5099
"""

import json
import os
import re
import socket
import subprocess
import signal
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request, Response, send_from_directory
import time as _time
import threading as _threading
import dhan_rate_limiter as _rl

# IPv4 force — Dhan rejects IPv6 (DH-905). Must be here, not just in range_trader.
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR      = Path(__file__).resolve().parent
TRADERS_DIR   = BASE_DIR / "_TRADERS"   # actual trading runner scripts live here
TC_FILE       = BASE_DIR / "nifty_config.json"
LOG_FILE      = BASE_DIR / "nifty_trader.log"
RESULTS_DIR   = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# Trade DB (order_store) — every order (webhook/manual/strategy, paper/live) recorded.
try:
    import order_store
    order_store.init_db()
except Exception as _e:
    print("[order_store] init fail:", _e, flush=True)
import sys as _sys_boot
# venv/bin/python is the VPS (Linux) layout. On Windows there's no such
# path — use the interpreter actually running this process either way, so
# Start/Stop works identically on the VPS and on a local Windows dev box.
PYTHON        = _sys_boot.executable
TRADER_SCRIPT = str(TRADERS_DIR / "nifty_ema_trader.py")

# Allow "import range_trader" etc. from _TRADERS/ without moving shared utils
import sys as _sys
_sys.path.insert(0, str(TRADERS_DIR))

app = Flask(__name__)

# ── Dhan Feed (WebSocket real-time LTP) ───────────────────────────────────────
_feed_started = False
_feed_lock = _threading.Lock()
_sec_to_sym = {}   # sec_id(str) -> sym — populated by api_positions_ltp, used by SSE

def _ensure_feed_started():
    """Start dhan_feed background thread once credentials are available."""
    global _feed_started
    if _feed_started:
        return
    with _feed_lock:
        if _feed_started:
            return
        try:
            import dhan_feed
            token, cid = _creds()
            dhan_feed.start({"client_id": cid, "jwt_token": token}, [])   # start with empty list; instruments added dynamically
            _feed_started = True
        except Exception as e:
            print("[_ensure_feed_started] fail:", e, flush=True)  # no creds yet or import error — will retry next call

def _feed_subscribe(sym_sec_pairs):
    """Subscribe (seg, sec_id) pairs to live feed. Safe to call multiple times."""
    try:
        import dhan_feed
        for seg, sec_id in sym_sec_pairs:
            dhan_feed.add((seg, str(sec_id)))
    except Exception:
        pass

# ── HTML ───────────────────────────────────────────────────────────────────────


# ── API Routes ─────────────────────────────────────────────────────────────────

RSI_SCRIPT   = str(TRADERS_DIR / "01_rsi_v1.py")
RSI_LOG      = BASE_DIR / "logs" / "rsi_v1.log"
RSI_CFG      = BASE_DIR / "nifty_config.json"
RANGE_SCRIPT = str(TRADERS_DIR / "range_trader.py")
RANGE_LOG    = BASE_DIR / "logs" / "range_trader.log"
RANGE_CFG    = BASE_DIR / "range_config.json"
UNIV_SCRIPT  = str(TRADERS_DIR / "universe_trader.py")
UNIV_LOG     = BASE_DIR / "logs" / "universe_trader.log"
CONFIG_FILE  = BASE_DIR / "data" / "config.json"
NOTE_IMG_DIR = BASE_DIR / "data" / "note_images"
NOTE_IMG_DIR.mkdir(parents=True, exist_ok=True)

def _creds():
    """JWT token + client_id from the dashboard's OWN config (root data/config.json).
    Don't use range_trader.load_creds(): on the local dev layout range_trader is
    imported from _TRADERS/ so its BASE_DIR is _TRADERS and it reads
    _TRADERS/data/config.json — which doesn't exist (token is saved at root).
    On the VPS (flat layout) it happened to read root, which is why it worked there
    but not locally. Reading CONFIG_FILE directly is correct on both."""
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return cfg["jwt_token"], cfg["client_id"]

STRATEGIES = {
    "ema":      {"script": TRADER_SCRIPT, "log": LOG_FILE,  "cfg": TC_FILE,   "grep": "nifty_ema_trader"},
    "rsi":      {"script": RSI_SCRIPT,    "log": RSI_LOG,   "cfg": RSI_CFG,   "grep": "rsi_trader"},
    "rsi_v1":   {"script": RSI_SCRIPT,    "log": BASE_DIR / "logs/rsi_v1.log", "cfg": TC_FILE, "grep": "01_rsi_v1"},
    "range":    {"script": RANGE_SCRIPT,  "log": RANGE_LOG, "cfg": RANGE_CFG, "grep": "range_trader"},
    "universe": {"script": UNIV_SCRIPT,   "log": UNIV_LOG,  "cfg": TC_FILE,   "grep": "universe_trader"},
}
# Aliases — custom variation names map to base strategy
STRATEGY_ALIASES = {"ARS": "range", "rsi": "rsi"}

def _base(strategy):
    first = strategy.split('_')[0] if '_' in strategy else strategy
    return STRATEGY_ALIASES.get(first, first)

def _detect_lang(code):
    """Best-effort Pine vs Python vs DSL-rule-block detection (the UI also asks
    the user to confirm). Pine: //@version / strategy()/indicator(). Python: a
    def/import/class. Else if it has entry_long/exit_long rule lines → dsl."""
    c = code or ""
    low = c.lower()
    if "//@version" in low or "strategy(" in low or "indicator(" in low or "ta." in low:
        return "pine"
    if "def evaluate" in low or "def backtest" in low or "\nimport " in c or c.startswith("import ") or "\ndef " in c or "class " in low:
        return "python"
    if "entry_long" in low or "exit_long" in low or "entry_short" in low:
        return "dsl"
    return "pine"

def _parse_dsl_block(code):
    """Turn a rule-block (// comments + `key = value` lines) into a cfg dict the
    custom_rule_engine reads. entry_*/exit_* stay as expression strings; numbers
    coerce to int/float; true/false to bool. Mirrors the Edit-modal parser."""
    EXPR_KEYS = {"entry_long", "entry_short", "exit_long", "exit_short"}
    out = {}
    for raw in (code or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        # strip trailing inline comments on non-expression numeric/string lines
        if key in EXPR_KEYS:
            out[key] = val
            continue
        if "//" in val:
            val = val.split("//", 1)[0].strip()
        low = val.lower()
        if low in ("true", "false"):
            out[key] = (low == "true")
        else:
            try:
                out[key] = int(val) if val.lstrip("-").isdigit() else float(val)
            except ValueError:
                out[key] = val
    return out

def _script_header(code):
    """Read optional `# symbol: X` / `# timeframe: 5m` / `# qty: 1` header lines
    from a pasted Python script so it can pre-fill its run config."""
    hdr = {}
    for raw in (code or "").splitlines()[:25]:
        line = raw.strip()
        if not line.startswith("#") or ":" not in line:
            continue
        k, v = line[1:].split(":", 1)
        k, v = k.strip().lower(), v.strip()
        if k in ("symbol", "timeframe", "qty", "max_trades_per_day"):
            hdr[k] = v
    return hdr

def _proc_cmdline(grep, strategy=None):
    """Pehli running trader process ki (pid, cmdline). Reliable key = `--id <strategy>`
    (cmdline me exact token; grep/_base mismatch se bachata — e.g. rsi_v1 chalti hai
    01_rsi_v1.py se par alias grep 'rsi_trader' deta). grep sirf fallback (jab --id na ho).
    psutil cross-platform — Windows pe pgrep NAHI hota (us bug se get_pid hamesha None
    deta tha → restart pe DUPLICATE traders spawn). pgrep fallback Linux/VPS ke liye."""
    want_id = strategy if (strategy and '_' in strategy) else None
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                parts = p.info.get('cmdline') or []
            except Exception:
                continue
            if not parts:
                continue
            cl = ' '.join(parts)
            if want_id:   # exact token match — substring se 'rsi_v1' != 'rsi_v10'
                hit = any(t == f"--id={want_id}" or
                          (t == "--id" and i + 1 < len(parts) and parts[i + 1] == want_id)
                          for i, t in enumerate(parts))
                if hit:
                    return p.info['pid'], cl
            elif grep and grep in cl:
                return p.info['pid'], cl
    except Exception:
        pass
    try:  # pgrep fallback (Linux/VPS jahan psutil na ho)
        pat = f"--id {want_id}" if want_id else grep
        # '--' end-of-options — warna '--id ...' ko pgrep option samajh ke usage print karta
        out = subprocess.check_output(['pgrep', '-a', '-f', '--', pat], text=True).strip()
        if out:
            first_line = out.split('\n')[0]
            parts = first_line.split(maxsplit=1)
            pid = int(parts[0])
            cl = parts[1] if len(parts) > 1 else ""
            return pid, cl
    except Exception:
        pass
    return None, None

def get_pid(strategy="ema"):
    entry = STRATEGIES.get(_base(strategy))
    if not entry:
        return None   # no live trader script for this type (e.g. vwap — backtest-only so far)
    pid, _ = _proc_cmdline(entry["grep"], strategy)
    return pid

def get_mode(strategy="ema"):
    entry = STRATEGIES.get(_base(strategy))
    if not entry:
        return 'paper'
    _, cl = _proc_cmdline(entry["grep"], strategy)
    if cl:
        return 'live' if '--live' in cl else 'paper'
    return 'paper'

def _ts(line):
    """Extract HH:MM time from log line."""
    m = re.match(r'\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2})', line)
    return m.group(1) if m else ''

def parse_pnl(log_path, today, qty=1):
    try:
        lines = [l for l in Path(log_path).read_text().splitlines() if today in l]
    except Exception:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "details": []}

    # open_positions[sym] = {side, price, time} — set only on [PAPER] entry
    open_pos = {}
    details, total_pnl, wins, losses = [], 0, 0, 0

    last_signal_price = {}  # sym -> price from SIGNAL line

    for line in lines:
        # SIGNAL line has the price: SIGNAL BUY BAJFINANCE @ 956.75
        ms = re.search(r'SIGNAL\s+(BUY|SELL)\s+(\w+)\s+@\s+([\d.]+)', line)
        if ms:
            last_signal_price[ms.group(2)] = float(ms.group(3))

        # Entry: [PAPER] or [LIVE] BUY/SELL QTY SYM @ price  (SYM can be NIFTY-Jun2026-24100-CE)
        m = re.search(r'\[(?:PAPER|LIVE)\]\s+(BUY|SELL)\s+(\d+)\s+([\w\-]+)\s+@\s+([\d.]+)', line)
        if m:
            side, q_log, sym, price = m.group(1), int(m.group(2)), m.group(3), float(m.group(4))
            # Position netting — opposite side on same symbol = close existing position
            if sym in open_pos and open_pos[sym]["side"] != side:
                entry = open_pos.pop(sym)
                exit_price = price
                q_use = entry.get("qty", qty)
                pnl = (exit_price - entry["price"]) * q_use if entry["side"] == "BUY" else (entry["price"] - exit_price) * q_use
                total_pnl += pnl
                wins   += 1 if pnl > 0 else 0
                losses += 0 if pnl > 0 else 1
                details.append({
                    "sym": sym, "entry": entry["side"], "qty": q_use,
                    "entry_price": entry["price"], "entry_time": entry["time"],
                    "exit_price": exit_price, "exit_time": _ts(line),
                    "pnl": round(pnl, 2)
                })
            else:
                open_pos[sym] = {"side": side, "price": price, "time": _ts(line), "qty": q_log}
            continue

        # Entry: [PAPER] BUY/SELL QTY SYM  correlationId (old format — use last SIGNAL price)
        m = re.search(r'\[PAPER\]\s+(BUY|SELL)\s+\d+\s+([\w\-]+)\s+correlationId', line)
        if m:
            side, sym = m.group(1), m.group(2)
            price = last_signal_price.get(sym, 0.0)
            open_pos[sym] = {"side": side, "price": price, "time": _ts(line)}
            continue

        # Exit: EXIT SYM via REASON @ price  (SYM can have hyphens)
        m = re.search(r'EXIT\s+([\w\-]+)\s+via\s+\S+\s+@\s+([\d.]+)', line)
        if m:
            sym, exit_price = m.group(1), float(m.group(2))
            if sym not in open_pos:
                continue  # stale state exit — no entry this session, skip
            entry = open_pos.pop(sym)
            q_use = entry.get("qty", qty)
            if entry["side"] == "BUY":
                pnl = (exit_price - entry["price"]) * q_use
            else:
                pnl = (entry["price"] - exit_price) * q_use
            total_pnl += pnl
            wins   += 1 if pnl > 0 else 0
            losses += 0 if pnl > 0 else 1
            details.append({
                "sym": sym, "entry": entry["side"], "qty": q_use,
                "entry_price": entry["price"], "entry_time": entry["time"],
                "exit_price": exit_price, "exit_time": _ts(line),
                "pnl": round(pnl, 2)
            })
            continue

    # Open positions (entry without exit yet)
    open_list = [{"sym": sym, "entry": v["side"], "entry_price": v["price"],
                  "entry_time": v["time"], "qty": v.get("qty", qty),
                  "exit_price": None, "exit_time": "—", "pnl": None}
                 for sym, v in open_pos.items()]

    n = len(details)
    return {"trades": n, "wins": wins, "losses": losses,
            "win_rate": round(wins/n*100, 1) if n else 0,
            "total_pnl": round(total_pnl, 2), "details": details,
            "open": open_list}

@app.route('/')
def index():
    return render_template("index.html")

@app.route('/backtest')
def backtest():
    from flask import send_file
    return send_file(BASE_DIR / '_TOOLS' / 'backtest_dashboard.html')

@app.route('/backtest-chart')
def backtest_chart():
    """Full-page chart view — opened in a new tab via the Run modal's
    '🔍 Full View' button. Reads the chart JSON from localStorage client-side
    (same origin, so it's already there from the modal that opened this tab)."""
    return render_template("backtest_chart.html")

@app.route('/api/status')
def api_status():
    st = {}
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        for s in cfg.keys():
            pid = get_pid(s)
            if pid:
                st[s] = pid
                st[f"{s}_mode"] = get_mode(s)
    except:
        pass
    return jsonify(st)

@app.route('/api/log')
def api_log():
    s  = request.args.get('s', 'ema')
    lf = BASE_DIR / 'logs' / f"{s}.log"
    try:
        lines = Path(lf).read_text().splitlines()[-80:]
        return jsonify({"lines": lines})
    except Exception:
        return jsonify({"lines": ["Log not found"]})

@app.route('/api/watch/<strategy_id>')
def api_watch_strategy(strategy_id):
    wf = BASE_DIR / 'data' / f"watch_{strategy_id}.json"
    try:
        if wf.exists():
            age = _time.time() - wf.stat().st_mtime
            if age < 120:
                return jsonify(json.loads(wf.read_text()))
            else:
                return jsonify({"error": f"Watchlist stale (age: {age:.1f}s). Ensure strategy is running."})
        else:
            return jsonify({"error": f"Watchlist file not found: {wf.name}"})
    except Exception as e:
        return jsonify({"error": f"Error reading watchlist: {e}"})

@app.route('/watch-chart')
def watch_chart_page():
    return render_template('watch_chart.html')

@app.route('/api/watch-chart-data')
def api_watch_chart_data():
    """Today's 1-min candles for a watchlist symbol + its current zone
    (from data/watch_<strategy>.json) — lets the Watchlist modal's row-click
    show what the strategy is actually seeing, not just the numbers."""
    import range_trader, datetime as _dt, pandas as pd
    symbol = request.args.get('symbol', '').strip().upper()
    strategy_id = request.args.get('strategy', '').strip()
    if not symbol:
        return jsonify({"ok": False, "msg": "symbol required"})
    try:
        df = range_trader.fetch_1m(symbol, "1m")
        if df is None or df.empty:
            return jsonify({"ok": False, "msg": f"No candle data for {symbol} (market closed / no Dhan info?)"})
        candles = []
        for _, row in df.iterrows():
            # range_trader.fetch_1m() already shifts df["time"] by +5:30 (IST
            # wall-clock stored as a naive timestamp) — do NOT add 19800 again
            # here, that double-shifts it (was showing candles ~5.5h ahead).
            t_ist = int(pd.Timestamp(row["time"]).timestamp())
            candles.append({"time": t_ist, "open": round(float(row["open"]), 2),
                            "high": round(float(row["high"]), 2), "low": round(float(row["low"]), 2),
                            "close": round(float(row["close"]), 2)})
        zone = {}
        if strategy_id:
            wf = BASE_DIR / 'data' / f"watch_{strategy_id}.json"
            if wf.exists():
                d = json.loads(wf.read_text())
                for s in d.get("symbols", []):
                    if s.get("symbol") == symbol:
                        zone = s
                        break
        return jsonify({"ok": True, "candles": candles, "symbol": symbol, "zone": zone})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/config', methods=['GET'])
def api_config():
    try:
        return jsonify(json.loads(TC_FILE.read_text()))
    except Exception:
        return jsonify({})

@app.route('/api/config', methods=['POST'])
def api_set_config():
    data = request.get_json()
    TC_FILE.write_text(json.dumps(data, indent=2))
    return jsonify({"msg": "Config saved successfully!"})

def _risk_config():
    """_risk block from nifty_config.json: {global:{max_loss_pct,max_loss_rs,capital_rs},
    per_strategy:{<strategy_id>:{max_loss_pct,max_loss_rs,capital_rs}}}. Strategy-specific
    overrides the global default; absent = no auto cap (manual SL_PCT tag still works).
    capital_rs = ₹ allowed to be deployed (notional, qty*price) — see risk_gate.py."""
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        cfg = {}
    rc = cfg.get("_risk") or {}
    return {"global": rc.get("global") or {}, "per_strategy": rc.get("per_strategy") or {}}

@app.route('/api/risk-config', methods=['GET'])
def api_get_risk_config():
    return jsonify(_risk_config())

@app.route('/api/risk-config', methods=['POST'])
def api_set_risk_config():
    data = request.get_json() or {}
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        cfg = {}
    cfg["_risk"] = {"global": data.get("global") or {}, "per_strategy": data.get("per_strategy") or {}}
    TC_FILE.write_text(json.dumps(cfg, indent=2))
    return jsonify({"msg": "Risk settings saved!"})

@app.route('/api/rms-summary')
def api_rms_summary():
    """Combined RMS view (Stage 2): per-strategy + global capital used/available,
    open unrealized P&L, and proximity to the max-loss cap — one read for the
    Risk tab's summary panel. Best-effort live LTP (dhan_feed); positions whose
    quote isn't available yet just show '—' for unrealized P&L, not an error."""
    import risk_gate, order_store, dhan_feed
    from datetime import timedelta

    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        cfg = {}
    reserved = {"_risk", "webhooks"}
    strat_ids = [k for k in cfg.keys() if k not in reserved]

    ist_now_ = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    data = order_store.trades_for(ist_now_.strftime("%Y-%m-%d"))
    open_pos = data.get("open", [])

    rc = risk_gate._risk_cfg()
    glob = rc.get("global", {})

    def _eff(strat, key):
        sv = (rc.get("per_strategy", {}).get(strat, {}) or {}).get(key)
        return sv if sv is not None else glob.get(key)

    def _get_ltp(sec_id, segment):
        # 1. Live WebSocket feed
        try:
            _feed_subscribe([(segment or "NSE_FNO", sec_id)])
            q = dhan_feed.get_quote(sec_id)
            ltp = float(q.get("ltp") or 0) if q else 0.0
            if ltp > 0:
                return ltp
        except Exception:
            pass
        # 2. shared_ltp_cache (cross-process, file-backed)
        try:
            import shared_ltp_cache
            v = shared_ltp_cache.get(str(sec_id))
            if v and float(v) > 0:
                return float(v)
        except Exception:
            pass
        # 3. Dhan REST (rate-limited)
        try:
            import dhan_rate_limiter as _rl
            from brokers.dhan_broker import DhanBroker
            _rl.acquire("ltp")
            db = DhanBroker()
            q2 = db.quote(sec_id, segment or "NSE_FNO")
            ltp2 = float((q2 or {}).get("ltp") or 0)
            if ltp2 > 0:
                return ltp2
        except Exception:
            pass
        return 0.0

    def _unrealized(positions):
        total, n_priced = 0.0, 0
        for p in positions:
            sec_id = p.get("sec_id")
            if not sec_id:
                continue
            ltp = _get_ltp(sec_id, p.get("segment"))
            if ltp <= 0:
                continue
            entry = float(p.get("entry_price") or 0)
            qty = float(p.get("qty") or 0)
            pnl = (ltp - entry) * qty if p.get("entry") == "BUY" else (entry - ltp) * qty
            total += pnl
            n_priced += 1
        return total, n_priced, len(positions)

    rows = []
    for sid_ in strat_ids:
        s_open = [p for p in open_pos if p.get("strategy") == sid_ and not any(
            t == "CAPITAL_BLOCKED" for t in (p.get("tags") or []))]
        cap_used = risk_gate.capital_in_use(sid_)
        cap_cap = _eff(sid_, "capital_rs")
        unreal, priced, total_n = _unrealized(s_open)
        max_loss_rs = _eff(sid_, "max_loss_rs")
        # Can this strategy take a NEW entry right now? (daily-loss / drawdown /
        # capital-exhausted) — so the panel can flag "no further entries today".
        try:
            g_blocked, g_reason, g_hard = risk_gate.gating_status(
                sid_, unrealized=(unreal if priced else 0.0))
        except Exception:
            g_blocked, g_reason, g_hard = False, "", False
        rows.append({
            "strategy": sid_, "capital_used": round(cap_used, 2),
            "capital_cap": cap_cap, "open_positions": total_n, "priced": priced,
            "unrealized_pnl": round(unreal, 2) if priced else None,
            "max_loss_rs": max_loss_rs,
            "max_loss_pct_used": round(abs(unreal) / max_loss_rs * 100, 1)
                if (max_loss_rs and unreal < 0) else None,
            "blocked": g_blocked, "block_reason": g_reason, "block_hard": g_hard,
            "run_mode": get_mode(sid_),   # 'live' / 'paper' / None (stopped)
        })

    glob_open = [p for p in open_pos if not any(
        t == "CAPITAL_BLOCKED" for t in (p.get("tags") or []))]
    glob_unreal, glob_priced, glob_n = _unrealized(glob_open)

    # Actual broker margin (cash available + used) — for display vs Dhan estimate
    _def_broker = risk_gate.default_broker()
    _bal = risk_gate.get_broker_balance(_def_broker)
    totals = {
        "capital_used": round(risk_gate.capital_in_use(None), 2),
        "capital_cap": glob.get("capital_rs"),
        "open_positions": glob_n, "priced": glob_priced,
        "unrealized_pnl": round(glob_unreal, 2) if glob_priced else None,
        "broker_name": _def_broker,
        "broker_available": round(_bal["available"], 2) if _bal.get("available") is not None else None,
        "broker_used_margin": round(_bal["used_margin"], 2) if _bal.get("used_margin") is not None else None,
        "broker_total": round(_bal["total_margin"], 2) if _bal.get("total_margin") is not None else None,
        "broker_cash": round(_bal["cash"], 2) if _bal.get("cash") is not None else None,
        "broker_collateral": round(_bal["collateral"], 2) if _bal.get("collateral") is not None else None,
        "broker_ok": _bal.get("ok", False),
    }

    # ── Webhook max-trades-per-day status ──
    # Webhook strategies run inside THIS dashboard process, so we can read their
    # live per-(strategy,symbol) trade counters directly. Surfaces "max trades
    # reached → no further entries today" right in the RMS panel.
    webhook = []
    try:
        import webhook_executor
        whs = webhook_executor._all_webhooks()
        tdy = dict(webhook_executor._trades_today)   # "strat|symbol" -> count
        gmax = int((whs.get("global", {}) or {}).get("global_max_trades", 0) or 0)
        gsum = sum(tdy.values())
        for k, c in sorted(tdy.items()):
            strat_k, _, sym_k = k.partition("|")
            scfg = whs.get(strat_k, {}) or {}
            mx = int(scfg.get("max_trades_per_day", 2) or 0)
            try:
                wb, wr, wh = risk_gate.gating_status(strat_k)
            except Exception:
                wb, wr, wh = False, "", False
            webhook.append({
                "strategy": strat_k, "symbol": sym_k,
                "trades_today": c, "max_trades": mx,
                "maxed": bool(mx and c >= mx),
                "blocked": wb, "block_reason": wr, "block_hard": wh,
            })
        wh_global = {"global_max_trades": gmax, "total_trades_today": gsum,
                     "maxed": bool(gmax and gsum >= gmax)}
    except Exception as e:
        wh_global = {"error": str(e)}

    return jsonify({"strategies": rows, "totals": totals,
                    "webhook": webhook, "webhook_global": wh_global})

@app.route('/api/sync-positions', methods=['POST'])
def api_sync_positions():
    """Force-reconcile DB open positions against actual broker positions.
    Marks ghost positions (flat at broker, OPEN in DB) as externally_closed.
    Use this after manually closing positions at the broker directly (TRAP #44)."""
    from datetime import timedelta as _td2
    import order_store as _os3
    import broker_sync as _bsync2
    today = (datetime.now(timezone.utc) + _td2(hours=5, minutes=30)).strftime('%Y-%m-%d')
    open_pos = _os3.trades_for(today).get('open', [])
    try:
        closed_ids = _bsync2.force_sync(open_pos, log=print)
        return jsonify({
            "ok": True,
            "ghosts_cleared": len(closed_ids),
            "msg": f"✅ {len(closed_ids)} ghost position(s) cleared" if closed_ids
                   else "✅ No ghost positions found — all DB positions match broker"
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route('/api/rms-reconcile')
def api_rms_reconcile():
    """RMS Stage 3 — read-only drift check: our own capital_in_use(None) vs the
    broker's real available funds (health_check.py-style; doesn't block or
    change anything, just surfaces drift for manual investigation)."""
    import risk_gate
    broker_name = request.args.get('broker', 'dhan')
    try:
        from brokers import get_broker
        broker = get_broker(broker_name)
    except Exception as e:
        return jsonify({"ok": True, "our_capital_in_use": risk_gate.capital_in_use(None),
                        "broker_available": None, "note": f"broker init failed: {e}"})
    return jsonify(risk_gate.reconcile_funds(broker))

@app.route('/api/start', methods=['POST'])
def api_start():
    s    = request.args.get('s', 'ema_v1')
    mode = request.args.get('mode', 'paper')
    base_s = _base(s)
    st   = STRATEGIES.get(base_s)
    if st is None:
        # No live trader script for this type yet (e.g. vwap — backtest-only
        # so far). Falling back to a different strategy's script here would
        # silently run the WRONG strategy under this config — refuse instead.
        return jsonify({"msg": f"⚠ Live/paper trading not built yet for '{base_s}' — backtest only for now."}), 400
    pid  = get_pid(s)
    if pid:
        return jsonify({"msg": f"{s.upper()} already running (PID {pid})"})
    flag = '--live' if mode == 'live' else '--paper'
    log_file = BASE_DIR / 'logs' / f"{s}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lf   = open(log_file, 'a', encoding='utf-8')
    # PYTHONUTF8=1 — traders ke log me unicode (→ ─ emoji) Windows cp1252 pe
    # crash karta tha ("UnicodeEncodeError ... charmap"). UTF-8 force se khatam.
    _env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    subprocess.Popen([PYTHON, st['script'], flag, '--id', s],
                     stdout=lf, stderr=lf, env=_env,
                     cwd=str(BASE_DIR),
                     start_new_session=True)
    # Mark active so auto-scheduler knows user wants this running
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        if s not in cfg:
            cfg[s] = {}
        cfg[s]['active'] = True
        TC_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass
    return jsonify({"msg": f"✅ {s.upper()} started — {mode.upper()} mode"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    s   = request.args.get('s', 'ema')
    # keep_active=1 -> sirf process band karo, 'active' intent RAKHO. 15:30 ka
    # scheduled-stop yeh bhejta hai taaki kal 9:10 auto-start phir chala de.
    # Manual stop (user ne click kiya) = keep_active nahi -> active:false (band hi rahe).
    keep_active = request.args.get('keep_active') == '1'
    pid = get_pid(s)
    if not pid:
        return jsonify({"msg": f"{s.upper()} not running"})
    try:
        os.kill(pid, signal.SIGTERM)
        if not keep_active:
            try:
                cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                if s not in cfg:
                    cfg[s] = {}
                cfg[s]['active'] = False
                TC_FILE.write_text(json.dumps(cfg, indent=2))
            except Exception:
                pass
        return jsonify({"msg": f"⏹ {s.upper()} stopped"})
    except Exception as e:
        return jsonify({"msg": f"Error: {e}"})

@app.route('/api/pnl')
def api_pnl():
    s     = request.args.get('s', 'ema_v1')
    today = datetime.now().strftime("%Y-%m-%d")
    lf    = BASE_DIR / 'logs' / f"{s}.log"
    return jsonify(parse_pnl(lf, today))

@app.route('/api/token', methods=['GET'])
def api_get_token():
    try:
        cfg = json.loads(CONFIG_FILE.read_text())
        tok = cfg.get('jwt_token', '')
        if not tok:
            return jsonify({"has_token": False})
        return jsonify({"has_token": True, "preview": tok[-12:], "saved_at": cfg.get('token_saved_at', '?')})
    except Exception:
        return jsonify({"has_token": False})

@app.route('/api/token', methods=['POST'])
def api_set_token():
    token = (request.get_json().get('token') or '').strip()
    if len(token) < 20:
        return jsonify({"ok": False, "msg": "⚠️ Invalid token"})
    try:
        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        cfg['jwt_token']     = token
        cfg['token_saved_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
        # dhanClientId is also embedded in the JWT payload — decode it so
        # cfg['client_id'] is always populated even if never set explicitly.
        try:
            import base64
            payload = token.split('.')[1]
            payload += '=' * (-len(payload) % 4)
            cfg['client_id'] = json.loads(base64.urlsafe_b64decode(payload)).get('dhanClientId') or cfg.get('client_id')
        except Exception:
            pass
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        # Clear any token-expiry alerts from downloader_alert.json
        alert_file = BASE_DIR / "data" / "downloader_alert.json"
        if alert_file.exists():
            try:
                alerts = json.loads(alert_file.read_text())
                alerts = [a for a in alerts if 'token expire' not in a.lower()]
                alert_file.write_text(json.dumps(alerts))
            except Exception:
                pass
        return jsonify({"ok": True, "msg": "✅ Token saved!"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/broker-balances')
def api_broker_balances():
    """Dhan + Kite cash/collateral/total_margin, for the header widget + RMS
    Risk tab. Delegates to risk_gate.get_broker_balance() — same cached (20s)
    source the live daily-loss-cap calculation uses, so the dashboard always
    shows the exact number RMS is actually computing against, not a second
    independent fetch that could drift/disagree."""
    import risk_gate
    out = {}
    for name in ("dhan", "kite"):
        out[name] = risk_gate.get_broker_balance(name)
    return jsonify(out)

@app.route('/api/rate-limit-events')
def api_rate_limit_events():
    """Visibility into Dhan rate-limit throttling/429s — RMS Risk tab '🚦
    Rate Limit Monitor' card. Every acquire()/note_429() call across every
    process (range_trader, rsi_v1, webhook, dashboard, ...) gets tagged with
    an ambient 'strategy:symbol' context (dhan_rate_limiter.set_context) so
    the user can see exactly WHICH strategy+symbol is causing 429s/throttle,
    not just that it happened somewhere."""
    import dhan_rate_limiter as _rl
    events = _rl.get_events(limit=100, since_seconds=900)  # last 15 min
    counts = {}
    for e in events:
        ctx = e.get("context") or "unknown"
        counts[ctx] = counts.get(ctx, 0) + 1
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:10]
    return jsonify({"events": events, "top_offenders": top})

@app.route('/api/kite-login-url')
def api_kite_login_url():
    """Zerodha login URL return karo — user browser mein kholta hai."""
    try:
        cfg     = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        api_key = cfg.get("kite_api_key", "")
        if not api_key:
            return jsonify({"url": None, "error": "kite_api_key not set in config.json"})
        url = f"https://kite.trade/connect/login?api_key={api_key}&v=3"
        return jsonify({"url": url})
    except Exception as e:
        return jsonify({"url": None, "error": str(e)})


@app.route('/api/kite-exchange-token', methods=['POST'])
def api_kite_exchange_token():
    """
    request_token → access_token exchange karo via Kite API.
    access_token config.json mein save hota hai.
    """
    req_token = (request.get_json() or {}).get("request_token", "").strip()
    if not req_token:
        return jsonify({"ok": False, "error": "request_token missing"})
    try:
        _sys.path.insert(0, str(TRADERS_DIR))
        _sys.path.insert(0, str(BASE_DIR))
        from brokers import kite_broker
        access_token, err = kite_broker.exchange_request_token(req_token)
        if err:
            return jsonify({"ok": False, "error": err})
        return jsonify({"ok": True, "msg": "Kite access token saved"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/kite-save-key', methods=['POST'])
def api_kite_save_key():
    """API key + secret config.json mein save karo (one-time setup)."""
    data   = request.get_json() or {}
    api_key    = data.get("api_key", "").strip()
    api_secret = data.get("api_secret", "").strip()
    if not api_key or not api_secret:
        return jsonify({"ok": False, "error": "api_key and api_secret required"})
    try:
        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        cfg["kite_api_key"]    = api_key
        cfg["kite_api_secret"] = api_secret
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/kite-test-order', methods=['POST'])
def api_kite_test_order():
    """NIFTY ATM CE test order (1 lot) — Kite F&O permission verify karne ke liye."""
    try:
        _sys.path.insert(0, str(BASE_DIR))
        from brokers import kite_broker
        import importlib; importlib.reload(kite_broker)
        import dhan_master
        kite = kite_broker._load_kite()

        # NIFTY spot price se ATM strike nikalo
        import json, requests
        cfg = json.loads((BASE_DIR / "data" / "config.json").read_text())
        headers = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"], "Content-Type": "application/json"}
        _rl.acquire("ltp")
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                          json={"IDX_I": [13]}, headers=headers, timeout=5)
        nifty_price = float(r.json()["data"]["IDX_I"]["13"]["last_price"])
        atm = round(nifty_price / 50) * 50

        # Dhan master se ATM CE — returns (sec_id, trad_sym, lot_size)
        sec_id, trad_sym, lot_size = dhan_master.get_option_contract("NIFTY", atm, "CE")
        if not trad_sym:
            return jsonify({"ok": False, "error": f"ATM CE contract nahi mila (NIFTY {atm} CE)"})

        # Kite format mein convert karo
        kite_sym = kite_broker.dhan_sym_to_kite(trad_sym)

        # LTP Dhan se lo (Personal app mein Kite quotes nahi)
        _rl.acquire("ltp")
        r2 = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                           json={"NSE_FNO": [int(sec_id)]}, headers=headers, timeout=5)
        ltp = float(r2.json()["data"]["NSE_FNO"][str(sec_id)]["last_price"])

        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=kite_sym,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=lot_size,  # 1 lot
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=ltp,
            tag="KITE_TEST",
        )
        return jsonify({"ok": True, "order_id": order_id, "symbol": kite_sym, "ltp": ltp, "lot_size": lot_size, "msg": f"{kite_sym} {lot_size}qty BUY LIMIT@{ltp} — orderId={order_id}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


SCRIP_MASTER = BASE_DIR / "data" / "api-scrip-master.csv"

@app.route('/api/lot-sizes')
def api_lot_sizes():
    result = {"NIFTY": 65, "BANKNIFTY": 30}  # fallback defaults
    try:
        import csv
        with open(SCRIP_MASTER, newline='') as f:
            reader = csv.DictReader(f)
            found = set()
            for row in reader:
                ts  = row.get('SEM_TRADING_SYMBOL', '')
                lot = row.get('SEM_LOT_UNITS', '')
                if ts.startswith('NIFTY') and 'CE' in ts and 'NIFTY' not in found:
                    result['NIFTY'] = int(float(lot))
                    found.add('NIFTY')
                elif ts.startswith('BANKNIFTY') and 'CE' in ts and 'BANKNIFTY' not in found:
                    result['BANKNIFTY'] = int(float(lot))
                    found.add('BANKNIFTY')
                if len(found) == 2:
                    break
    except Exception as e:
        pass  # return fallback defaults
    return jsonify(result)

_sec_id_cache = {}  # trading_symbol -> security_id

# sec_id + segment for equity/index symbols (from range_trader._DHAN_DATA)
_EQ_IDX_SEC = {
    "NIFTY":      ("13",    "IDX_I"),
    "BANKNIFTY":  ("25",    "IDX_I"),
    "RELIANCE":   ("2885",  "NSE_EQ"),
    "TCS":        ("11536", "NSE_EQ"),
    "INFY":       ("1594",  "NSE_EQ"),
    "HDFCBANK":   ("1333",  "NSE_EQ"),
    "ICICIBANK":  ("4963",  "NSE_EQ"),
    "SBIN":       ("3045",  "NSE_EQ"),
    "AXISBANK":   ("5900",  "NSE_EQ"),
    "BAJFINANCE": ("317",   "NSE_EQ"),
    "WIPRO":      ("3787",  "NSE_EQ"),
    "KOTAKBANK":  ("1922",  "NSE_EQ"),
    "LT":         ("11483", "NSE_EQ"),
    "MARUTI":     ("10999", "NSE_EQ"),
    "HINDUNILVR": ("1394",  "NSE_EQ"),
    "ITC":        ("1660",  "NSE_EQ"),
    "SUNPHARMA":  ("3351",  "NSE_EQ"),
    "TITAN":      ("3506",  "NSE_EQ"),
    "ULTRACEMCO": ("11532", "NSE_EQ"),
    "NESTLEIND":  ("17963", "NSE_EQ"),
    "POWERGRID":  ("14977", "NSE_EQ"),
    "NTPC":       ("11630", "NSE_EQ"),
    "ONGC":       ("2475",  "NSE_EQ"),
    "ADANIENT":   ("25",    "NSE_EQ"),
    "ASIANPAINT": ("236",   "NSE_EQ"),
    "BHARTIARTL": ("10604", "NSE_EQ"),
    "HCLTECH":    ("1698",  "NSE_EQ"),
    "BAJAJFINSV": ("16675", "NSE_EQ"),
    "TATACONSUM": ("3432",  "NSE_EQ"),
    "COALINDIA":  ("1679",  "NSE_EQ"),
    "DIVISLAB":   ("10720", "NSE_EQ"),
    "DRREDDY":    ("881",   "NSE_EQ"),
    "EICHERMOT":  ("910",   "NSE_EQ"),
    "GRASIM":     ("1232",  "NSE_EQ"),
    "HEROMOTOCO": ("1348",  "NSE_EQ"),
    "HINDALCO":   ("1351",  "NSE_EQ"),
    "JSWSTEEL":   ("11723", "NSE_EQ"),
    "SBILIFE":    ("21808", "NSE_EQ"),
    "SHRIRAMFIN": ("4306",  "NSE_EQ"),
    "TATASTEEL":  ("3499",  "NSE_EQ"),
    "TECHM":      ("13538", "NSE_EQ"),
    "TRENT":      ("3537",  "NSE_EQ"),
}

def _get_sec_ids(syms: list) -> dict:
    """Returns {sym: sec_id}. Handles options (via dhan_master) + equity/index (via _EQ_IDX_SEC and universe)."""
    import dhan_master
    import universe
    out = {}
    for s in syms:
        if s in _sec_id_cache:
            out[s] = _sec_id_cache[s]
            continue
        # Hardcoded Equity/index lookup first
        if s in _EQ_IDX_SEC:
            sid = _EQ_IDX_SEC[s][0]
            _sec_id_cache[s] = sid
            out[s] = sid
            continue
        # Try full universe for all other equities
        uni_sid = universe.equity_secid(s)
        if uni_sid:
            _EQ_IDX_SEC[s] = (uni_sid, "NSE_EQ")
            _sec_id_cache[s] = uni_sid
            out[s] = uni_sid
            continue
        # Options — dhan_master nearest-expiry resolver
        sid = dhan_master.get_sec_id_for_trad_sym(s)
        if sid:
            _sec_id_cache[s] = sid
            out[s] = sid
    return out

def _get_seg(sym: str) -> str:
    """Return Dhan segment string for a symbol."""
    if sym in _EQ_IDX_SEC:
        return _EQ_IDX_SEC[sym][1]
    return "NSE_FNO"   # options default


_pos_ltp_cache = {}
_POS_CACHE_TTL = 15

@app.route('/api/positions-ltp')
def api_positions_ltp():
    """Fetch live LTP for open positions — uses dhan_feed WebSocket if running, else REST fallback."""
    syms_raw = request.args.get('syms', '')
    syms = [s.strip() for s in syms_raw.split(',') if s.strip()]
    if not syms:
        return jsonify({"ok": True, "ltp_map": {}})

    _ensure_feed_started()
    ltp_map = {}

    # Try WebSocket feed first (instant, no REST call)
    missing_syms = []
    try:
        import dhan_feed
        sec_id_map = _get_sec_ids(syms)
        pairs = [(_get_seg(s), v) for s, v in sec_id_map.items() if v]
        _feed_subscribe(pairs)
        id_to_sym = {v: k for k, v in sec_id_map.items()}
        _sec_to_sym.update(id_to_sym)   # keep global map for SSE
        for sec_id, sym in id_to_sym.items():
            q = dhan_feed.get_quote(sec_id)
            if q and q.get("ltp"):
                ltp_map[sym] = {"ltp": q["ltp"], "qty": None}
            else:
                missing_syms.append(sym)
    except Exception:
        missing_syms = syms

    # If all found in WS, return early
    if not missing_syms:
        return jsonify({"ok": True, "ltp_map": ltp_map, "src": "ws"})

    # Fallback: Dhan REST API only for missing symbols
    try:
        import range_trader, requests as _req
        import time as _t
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        missing_sec_id_map = _get_sec_ids(missing_syms)
        
        # Check pos cache to avoid hitting Dhan REST too frequently
        now = _t.time()
        still_missing = {}
        for s, sid in missing_sec_id_map.items():
            c = _pos_ltp_cache.get(s)
            if c and (now - c['ts']) < _POS_CACHE_TTL:
                ltp_map[s] = {"ltp": c['ltp'], "qty": None}
            else:
                still_missing[s] = sid
                
        missing_sec_id_map = still_missing
        
        if missing_sec_id_map:
            _rl.set_context("Dashboard:PosLTP")
            _rl.acquire("ltp")
            
            # Group by segment for REST call
            seg_groups = {}
            for s, sid in missing_sec_id_map.items():
                seg = _get_seg(s)
                seg_groups.setdefault(seg, []).append((s, sid))
            body = {}
            for seg, pairs in seg_groups.items():
                dhan_seg = {"NSE_EQ": "NSE_EQ", "IDX_I": "IDX_I", "NSE_FNO": "NSE_FNO"}.get(seg, "NSE_FNO")
                body[dhan_seg] = [int(sid) for _, sid in pairs]
            
            r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
            if r.status_code == 429:
                _rl.note_429()
                
            if r.status_code == 200:
                id_to_sym = {v: k for k, v in missing_sec_id_map.items()}
                for seg_key, quotes in (r.json().get("data", {}) or {}).items():
                    if not isinstance(quotes, dict): continue
                    for sec_id_str, q in quotes.items():
                        sym = id_to_sym.get(str(sec_id_str)) or id_to_sym.get(str(sec_id_str).lstrip('0'))
                        if not sym: continue
                        ltp = float(q.get("last_price") or q.get("ltp") or 0)
                        if ltp: 
                            ltp_map[sym] = {"ltp": ltp, "qty": None}
                            _pos_ltp_cache[sym] = {'ltp': ltp, 'ts': now}
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "ltp_map": ltp_map})

    return jsonify({"ok": True, "ltp_map": ltp_map, "src": "rest"})


@app.route('/api/ltp-stream')
def api_ltp_stream():
    """SSE endpoint — streams live LTP from dhan_feed WebSocket every 500ms."""
    _ensure_feed_started()

    def generate():
        import dhan_feed
        while True:
            try:
                # Send sym->ltp map so frontend can update cells directly by symbol name
                sym_ltp = {}
                for sec_id, q in dhan_feed.LIVE.items():
                    sym = _sec_to_sym.get(str(sec_id))
                    if sym and q.get("ltp"):
                        sym_ltp[sym] = round(q["ltp"], 2)
                yield f"data: {json.dumps(sym_ltp)}\n\n"
            except Exception:
                yield "data: {}\n\n"
            _time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream',
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

# Cache: (symbol, offset) -> {result, ts}
_ltp_cache = {}
# 2026-06-27: dropped 30s -> 2s. The 30s TTL was compensating for every call
# hitting Dhan REST directly; now api_option_ltp() reads dhan_feed's live
# WebSocket feed first (free, no rate-limit cost) and only falls back to
# REST for whatever the feed doesn't have yet — so a short TTL no longer
# means "more Dhan calls", just "fresher Quick Order price".
_LTP_CACHE_TTL = 15

@app.route('/api/option-ltp')
def api_option_ltp():
    """CE/PE LTP for Quick Order widget. Prefers the live dhan_feed WebSocket
    (free, no rate-limit cost, sub-second) — REST is only the fallback for
    whatever the feed doesn't have yet (e.g. just-resolved strike, not
    subscribed long enough). 30s REST cache stays as a safety net for when
    the feed genuinely has nothing (market closed, feed reconnecting)."""
    import time as _t
    symbol = request.args.get('symbol', 'NIFTY')
    offset = int(request.args.get('offset', 0))
    cache_key = (symbol, offset)

    # Return cached if fresh
    cached = _ltp_cache.get(cache_key)
    if cached and (_t.time() - cached['ts']) < _LTP_CACHE_TTL:
        return jsonify(cached['data'])

    try:
        import dhan_master, range_trader, requests as _req, dhan_feed
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}

        _idx_sec = {"NIFTY": "13", "BANKNIFTY": "25"}
        _idx_id  = _idx_sec.get(symbol, "13")
        _feed_subscribe([("IDX_I", _idx_id)])

        idx_price = float(dhan_feed.get_quote(_idx_id).get("ltp") or 0) or None
        if not idx_price:
            _rl.set_context("Dashboard:IdxLTP")
            _rl.acquire("ltp")
            _qr_idx  = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                                 json={"IDX_I": [int(_idx_id)]}, headers=headers, timeout=5)
            if _qr_idx.status_code == 429:
                _rl.note_429()
            if _qr_idx.status_code != 200:
                # index call rate-limited/failed — show last good value instead of erroring
                if cached:
                    return jsonify({**cached['data'], '_stale': True})
                return jsonify({"ok": False, "msg": "LTP busy (Dhan rate limit) — thodi der me"})
            idx_price = float(_qr_idx.json()["data"]["IDX_I"][_idx_id]["last_price"])

        sec_ce, t_ce, _ = dhan_master.get_option_contract(symbol, idx_price, "CE", offset)
        sec_pe, t_pe, _ = dhan_master.get_option_contract(symbol, idx_price, "PE", offset)

        if sec_ce:
            _feed_subscribe([("NSE_FNO", sec_ce)])
        if sec_pe:
            _feed_subscribe([("NSE_FNO", sec_pe)])

        ltp_ce = float(dhan_feed.get_quote(sec_ce).get("ltp") or 0) or None if sec_ce else None
        ltp_pe = float(dhan_feed.get_quote(sec_pe).get("ltp") or 0) or None if sec_pe else None

        missing_ids = [int(s) for s, l in [(sec_ce, ltp_ce), (sec_pe, ltp_pe)] if s and not l]
        if missing_ids:
            _rl.set_context("Dashboard:OptionLTP")
            _rl.acquire("ltp")
            qr = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                           json={"NSE_FNO": missing_ids}, headers=headers, timeout=5)
            if qr.status_code == 429:
                _rl.note_429()
            if qr.status_code == 200:
                fno = qr.json().get("data", {}).get("NSE_FNO", {})
                for sid_str, v in (fno.items() if isinstance(fno, dict) else []):
                    ltp_v = float(v.get("last_price") or v.get("ltp") or 0) or None
                    if str(sec_ce) == sid_str: ltp_ce = ltp_v
                    if str(sec_pe) == sid_str: ltp_pe = ltp_v
            elif qr.status_code == 429:
                # Rate limited — return stale cache if available
                if cached:
                    return jsonify({**cached['data'], '_stale': True})
                if not (ltp_ce or ltp_pe):
                    return jsonify({"ok": False, "msg": "Rate limit (429) — retry in 15s"})

        result = {"ok": True, "ce_sym": t_ce, "ce_ltp": ltp_ce, "pe_sym": t_pe, "pe_ltp": ltp_pe}
        _ltp_cache[cache_key] = {"data": result, "ts": _t.time()}
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/trade-chart')
def trade_chart_page():
    return render_template('trade_chart.html')

@app.route('/api/trade-chart-data')
def api_trade_chart_data():
    """Option premium 1-min candles for one completed trade + entry/exit marker times.
    Data: Dhan /v2/charts/intraday (raw REST). sec_id from trad_sym (nearest live expiry)."""
    import dhan_master, requests as _req, datetime as _dt
    trad_sym = request.args.get('trad_sym', '').strip()
    date_str = request.args.get('date', '').strip() or \
        (_dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    entry_t  = request.args.get('et', '').strip()   # HH:MM IST
    exit_t   = request.args.get('xt', '').strip()
    tf       = request.args.get('tf', '').strip()

    INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}

    try:
        import universe
        seg = "NSE_FNO"
        inst = "OPTIDX" if trad_sym.split('-')[0] in INDEX_UNDERLYINGS else "OPTSTK"
        sec_id = dhan_master.get_sec_id_for_trad_sym(trad_sym)
        
        # If not found in FNO, check Equity universe
        if not sec_id:
            sec_id = universe.equity_secid(trad_sym)
            if sec_id:
                seg = "NSE_EQ"
                inst = "EQUITY"
                
        if not sec_id:
            return jsonify({"ok": False, "msg": f"sec_id not found: {trad_sym}"})
            
        if tf == '1D':
            import sys
            tools_path = str(BASE_DIR / "_TOOLS")
            if tools_path not in sys.path:
                sys.path.insert(0, tools_path)
            import backtest_engine
            
            end_dt = _dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)
            start_dt = end_dt - _dt.timedelta(days=400) # Give enough buffer for indicators like 200 EMA
            df = backtest_engine.ensure_and_load_symbol(trad_sym, start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"), 1440)
            
            if df is None or df.empty:
                return jsonify({"ok": False, "msg": f"Daily data fetch failed for {trad_sym}"})
                
            candles = []
            entry_mk, exit_mk = None, None
            for i, row in df.iterrows():
                dt_obj = _dt.datetime.strptime(str(row['Datetime']), "%Y-%m-%d %H:%M:%S")
                # Add 5:30 to treat as IST (lightweight charts expects UTC timestamp, but we offset it so it displays local)
                t_ist = int(dt_obj.timestamp()) + 19800 
                candles.append({
                    "time": t_ist,
                    "open": round(float(row['Open']), 2),
                    "high": round(float(row['High']), 2),
                    "low": round(float(row['Low']), 2),
                    "close": round(float(row['Close']), 2)
                })
                # if the entry was on this date, set marker
                if dt_obj.strftime("%Y-%m-%d") == date_str:
                    entry_mk = t_ist
                    
            return jsonify({"ok": True, "candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk, "date": date_str})
            
        token, cid = _creds()
        hdrs = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": date_str, "toDate": date_str}, timeout=12)
        d = r.json()
        if not d.get("open"):
            return jsonify({"ok": False, "msg": f"{date_str} ka intraday data nahi (non-trading day?)"})
        candles, entry_mk, exit_mk = [], None, None
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
            t_ist = int(ts) + 19800   # +5:30 → chart shows IST (treated as UTC by lightweight-charts)
            hhmm  = _dt.datetime.utcfromtimestamp(int(ts) + 19800).strftime("%H:%M")
            candles.append({"time": t_ist, "open": round(float(o), 2), "high": round(float(h), 2),
                            "low": round(float(l), 2), "close": round(float(c), 2)})
            if entry_t and hhmm == entry_t and entry_mk is None: entry_mk = t_ist
            if exit_t  and hhmm == exit_t:  exit_mk = t_ist
        return jsonify({"ok": True, "candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk,
                        "trad_sym": trad_sym, "date": date_str})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/trade-chart-underlying-data')
def api_trade_chart_underlying_data():
    """Underlying instrument/index 1-min candles for the chart split-view's left
    pane — mirrors /api/trade-chart-data's signature (trad_sym/date/et/xt) but
    resolves the UNDERLYING root symbol (e.g. NIFTY from NIFTY-Jun2026-24050-CE)
    instead of the option contract itself."""
    import requests as _req, datetime as _dt
    trad_sym = request.args.get('trad_sym', '').strip()
    date_str = request.args.get('date', '').strip() or \
        (_dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    entry_t = request.args.get('et', '').strip()
    exit_t = request.args.get('xt', '').strip()
    strategy_id = request.args.get('strategy', '').strip()
    root = trad_sym.split('-')[0].strip().upper()
    if not root:
        return jsonify({"ok": False, "msg": "no underlying symbol"})

    # Same key_levels/zones_history/touch-high-low the Watchlist chart shows —
    # written by range_trader.py's main loop to data/watch_<strategy>.json,
    # keyed by the underlying root symbol (NIFTY/BANKNIFTY/...), not the
    # option trad_sym. Best-effort: trade chart still works without it.
    zone = {}
    if strategy_id:
        try:
            wf = BASE_DIR / 'data' / f"watch_{strategy_id}.json"
            if wf.exists():
                wd = json.loads(wf.read_text())
                for s in wd.get("symbols", []):
                    if s.get("symbol") == root:
                        zone = s
                        break
        except Exception:
            pass

    try:
        import universe
        if root in _EQ_IDX_SEC:
            sec_id, seg = _EQ_IDX_SEC[root]
            inst = "INDEX" if seg == "IDX_I" else "EQUITY"
        else:
            sec_id = universe.equity_secid(root)
            seg, inst = "NSE_EQ", "EQUITY"
        if not sec_id:
            return jsonify({"ok": False, "msg": f"underlying sec_id not found: {root}"})

        token, cid = _creds()
        hdrs = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": date_str, "toDate": date_str}, timeout=12)
        d = r.json()
        if not d.get("open"):
            return jsonify({"ok": False, "msg": f"{date_str} ka underlying intraday data nahi"})
        candles, entry_mk, exit_mk = [], None, None
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
            t_ist = int(ts) + 19800
            hhmm = _dt.datetime.utcfromtimestamp(int(ts) + 19800).strftime("%H:%M")
            candles.append({"time": t_ist, "open": round(float(o), 2), "high": round(float(h), 2),
                            "low": round(float(l), 2), "close": round(float(c), 2)})
            if entry_t and hhmm == entry_t and entry_mk is None: entry_mk = t_ist
            if exit_t and hhmm == exit_t: exit_mk = t_ist
        return jsonify({"ok": True, "candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk,
                        "symbol": root, "date": date_str, "zone": zone})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


def _dhan_live_fate(resp, token, cid):
    """Live order ka asli anjaam pata karo. Dhan ka 200 = 'accepted', 'filled' NAHI.
    Price-band/freeze pe order accept hoke turant REJECT ho jaata (async). Return
    (ok, status_upper): ok=False matlab koi real position nahi bani."""
    import time as _t, requests as _rq
    try:
        jr = resp.json() or {}
    except Exception:
        jr = {}
    if not isinstance(jr, dict):
        jr = {}
    oid = str(jr.get('orderId') or jr.get('data', {}).get('orderId') or '')
    status = str(jr.get('orderStatus') or jr.get('data', {}).get('orderStatus') or '').upper()
    # accept hua par abhi final nahi → ek baar confirm karo (reject async aata hai)
    if oid and status not in ('TRADED', 'REJECTED', 'CANCELLED', 'EXPIRED'):
        _t.sleep(1.2)
        try:
            h = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
            _rl.acquire("order")
            rr = _rq.get(f"https://api.dhan.co/v2/orders/{oid}", headers=h, timeout=6)
            if rr.status_code == 200:
                d = rr.json()
                if isinstance(d, list) and d:
                    d = d[0]
                if isinstance(d, dict):
                    status = str(d.get('orderStatus') or status).upper()
        except Exception:
            pass
    dead = status in ('REJECTED', 'CANCELLED', 'EXPIRED')
    return (not dead, status or 'SUBMITTED', oid)

@app.route('/api/bulk-preview', methods=['POST'])
def api_bulk_preview():
    data = request.get_json()
    symbols_raw = data.get('symbols', '')
    
    import re
    raw_list = re.split(r'[,\n\t]+', symbols_raw)
    symbols = [s.strip().upper() for s in raw_list if s.strip()]
    if not symbols:
        return jsonify({"status": "error", "message": "No valid symbols provided."})
        
    try:
        import universe
        import requests as _req
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        
        # Lookup sec_id for all
        sec_ids = {}
        for sym in symbols:
            sid = universe.equity_secid(sym)
            if sid:
                sec_ids[sym] = sid
                
        if not sec_ids:
            return jsonify({"status": "error", "message": "Could not resolve any of the symbols to Dhan NSE_EQ security IDs."})
            
        import time
        ltp_map = {}
        
        # Try to get from live feed first if available
        try:
            import dhan_feed
            for sym, mapped_sid in sec_ids.items():
                if str(mapped_sid) in dhan_feed.LIVE:
                    feed_ltp = float(dhan_feed.LIVE[str(mapped_sid)].get("ltp", 0))
                    if feed_ltp > 0:
                        ltp_map[sym] = feed_ltp
        except Exception:
            pass

        # For remaining, fetch from REST API with retries
        remaining_sids = [int(sid) for sym, sid in sec_ids.items() if sym not in ltp_map]
        if remaining_sids:
            body = {"NSE_EQ": remaining_sids}
            for attempt in range(3):
                _rl.set_context("Dashboard:BulkLTP")
                _rl.acquire("ltp")
                r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
                if r.status_code == 429:
                    _rl.note_429()
                if r.status_code == 200:
                    qdata = r.json().get("data", {}).get("NSE_EQ", {})
                    for sid_str, q in qdata.items():
                        ltp_v = float(q.get("last_price") or q.get("ltp") or 0)
                        if ltp_v:
                            for sym, mapped_sid in sec_ids.items():
                                if str(mapped_sid) == str(sid_str):
                                    ltp_map[sym] = ltp_v
                                    break
                    break # Success
                time.sleep(1.2)
                            
        results = []
        for sym in symbols:
            if sym in sec_ids:
                results.append({"sym": sym, "ltp": ltp_map.get(sym, 0.0), "sec_id": sec_ids[sym]})
        
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()})


@app.route('/api/bulk-order', methods=['POST'])
def api_bulk_order():
    data = request.get_json()
    filter_name = data.get('filter_name', 'Bulk_Test')
    tf = data.get('tf', '')
    ind = data.get('ind', '')
    trades = data.get('trades', []) # list of {"sym": "A", "qty": 1, "sl_pct": 2.0, "sec_id": "123", "ltp": 150.0}
    
    if not trades:
        return jsonify({"status": "error", "message": "No trades provided."})
        
    try:
        import order_store
        placed = 0
        order_store.init_db()
        for t in trades:
            sym = t.get('sym')
            qty = int(t.get('qty', 1))
            sl_pct = float(t.get('sl_pct', 0.0))
            price = float(t.get('ltp', 0.0))
            sid = t.get('sec_id', '')
            
            tags_list = ["bulk"]
            if tf or ind:
                tags_list.append(f"CHART:{tf}:{ind}")
            if sl_pct > 0:
                tags_list.append(f"SL_PCT:{sl_pct}")

            # CNC only makes sense for EQUITY (carry-forward) — instrument here
            # is always EQUITY, so the client's choice is honored as-is; non-equity
            # order paths elsewhere force NRML server-side regardless of client input.
            product_type = (t.get('product_type') or 'NRML').upper()
            if product_type not in ('NRML', 'CNC'):
                product_type = 'NRML'

            order_store.record(
                side="BUY",
                qty=qty,
                price=price,
                source="manual",
                strategy=filter_name,
                mode="paper",
                broker="dhan",
                symbol=sym,
                instrument="EQUITY",
                trad_sym=sym,
                sec_id=str(sid),
                segment="NSE_EQ",
                status="paper",
                tags=tags_list,
                product_type=product_type,
            )
            placed += 1
            
        return jsonify({
            "status": "success", 
            "message": f"Successfully placed paper trades for {placed} out of {len(trades)} symbols.",
            "placed_count": placed
        })
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()})


@app.route('/api/manual-order', methods=['POST'])
def api_manual_order():
    data   = request.get_json()
    symbol = data.get('symbol', 'NIFTY')
    side   = data.get('side', 'BUY')
    lots   = int(data.get('lots', 1))
    offset = int(data.get('strike_offset', 0))
    mode   = data.get('mode', 'paper')
    broker_choice = (data.get('broker') or 'dhan').lower()  # 'dhan' (default) or 'kite'
    order_type = (data.get('order_type', 'MARKET') or 'MARKET').upper()
    limit_price = data.get('price')   # user-entered LIMIT price (₹), may be None
    try:
        import dhan_master
        import range_trader

        token, cid = _creds()
        # opt_type explicitly from request (Quick Order CE/PE selector). Legacy
        # fallback (BUY→PE, SELL→CE) only if client didn't send a valid leg.
        opt_type = str(data.get('opt_type') or '').upper()
        if opt_type not in ('CE', 'PE'):
            opt_type = 'PE' if side == 'BUY' else 'CE'

        _hdrs    = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        _idx_sec = {"NIFTY": "13", "BANKNIFTY": "25"}
        _idx_id  = _idx_sec.get(symbol, "13")
        _rl.acquire("ltp")
        _qr_idx  = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                                 json={"IDX_I": [int(_idx_id)]}, headers=_hdrs, timeout=5)
        price = float(_qr_idx.json()["data"]["IDX_I"][_idx_id]["last_price"])

        # Option contract lookup
        sec_id, t_sym, lot_sz_master = dhan_master.get_option_contract(symbol, price, opt_type, offset)
        if not sec_id:
            return jsonify({'ok': False, 'msg': f'Contract not found: {symbol} {opt_type} offset={offset}'})

        # Lot size — from dhan_master cache (already parsed correctly)
        lot_size = lot_sz_master if lot_sz_master else 65

        qty_shares = lots * lot_size   # e.g. 1 lot × 65 = 65 shares

        # Zerodha test path — contract resolution (ATM strike etc) always uses
        # Dhan per project convention ("data always Dhan, orders via Kite");
        # only the actual order placement diverges. Reuses smart_order.execute()
        # (broker-agnostic, marketable-limit pricing, async order-confirm,
        # order_store recording) instead of hand-rolling a second Dhan-style
        # REST flow for Kite — this is purely to verify "does an order from
        # this dashboard actually reach Zerodha" without duplicating the
        # whole live-order/paper-log/record logic a second time.
        if broker_choice == 'kite':
            try:
                import smart_order
                from brokers import get_broker
                kite_broker_obj = get_broker('kite')
                res = smart_order.execute(
                    side, symbol, sec_id, 'NSE_FNO', qty_shares, t_sym, mode,
                    kite_broker_obj, log=lambda m: print(m, flush=True),
                    tag='MANUAL', source='manual', strategy='manual',
                    instrument='options', broker_name='kite')
                if not res.get('ok'):
                    return jsonify({'ok': False, 'msg': f"Kite order failed — {res.get('reason')}"})
                mtag = 'LIVE' if mode == 'live' else 'PAPER'
                return jsonify({'ok': True,
                    'msg': f"[{mtag}/KITE] {side} {lots}L ({qty_shares} qty) {t_sym} "
                           f"@ {res['price']:.2f} ({res.get('status')})"})
            except Exception as e:
                return jsonify({'ok': False, 'msg': f'Kite order error: {e}'})

        # Get actual option LTP from Dhan quotes (not index price)
        import requests as _req
        import time as _time
        option_ltp = price  # fallback: index price
        try:
            q_headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
            q_resp = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                               json={"NSE_FNO": [int(sec_id)]},
                               headers=q_headers, timeout=4)
            if q_resp.status_code == 200:
                qdata = q_resp.json().get("data", {}).get("NSE_FNO", {})
                for v in (qdata.values() if isinstance(qdata, dict) else qdata):
                    ltp_v = float(v.get("last_price") or v.get("ltp") or 0)
                    if ltp_v:
                        option_ltp = ltp_v
                        break
        except Exception:
            pass

        # LIMIT order: use user-entered price (fallback to live LTP); MARKET: price 0
        if order_type == 'LIMIT':
            try:
                limit_price = float(limit_price)
            except (TypeError, ValueError):
                limit_price = option_ltp
            if not limit_price or limit_price <= 0:
                limit_price = option_ltp
            order_price = round(float(limit_price), 2)
            option_ltp = order_price   # log the exact LIMIT price
        else:
            order_price = 0

        ts = int(_time.time())
        body = {
            'dhanClientId':    cid,
            'correlationId':   f'MANUAL_{symbol}_{ts}',
            'transactionType': side,
            'exchangeSegment': 'NSE_FNO',
            'productType':     'INTRADAY',
            'orderType':       order_type,
            'validity':        'DAY',
            'securityId':      sec_id,
            'tradingSymbol':   t_sym,
            'quantity':        qty_shares,
            'disclosedQuantity': 0,
            'price':           order_price,
            'triggerPrice':    0,
            'afterMarketOrder': False,
        }
        print(f"[MANUAL ORDER] body={body}", flush=True)

        def _write_to_log(tag):
            # Append to active strategy log so P&L parser picks it up
            try:
                cfg_data = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                active = next(iter(cfg_data.keys()), 'range_v1')
                log_path = BASE_DIR / 'logs' / f'{active}.log'
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(log_path, 'a') as lf:
                    lf.write(f"{now},000  INFO      [{tag}] {side} {qty_shares} {t_sym} @ {option_ltp:.2f}  correlationId=MANUAL_{symbol}_{ts}\n")
            except Exception:
                pass

        def _record(status_, m, oid=''):
            try:
                import order_store
                order_store.record(side, qty_shares, option_ltp, source='manual', mode=m,
                    broker='dhan', symbol=symbol, instrument='options', trad_sym=t_sym,
                    sec_id=sec_id, segment='NSE_FNO', broker_order_id=oid,
                    correlation_id=f'MANUAL_{symbol}_{ts}', status=status_,
                    product_type='NRML')  # options always NRML — CNC only applies to EQUITY
            except Exception:
                pass

        if mode == 'paper':
            _write_to_log('PAPER')
            _record('paper', 'paper')
            return jsonify({'ok': True, 'msg': f'[PAPER] {side} {lots}L ({qty_shares} qty) {t_sym} @ {option_ltp:.2f}'})

        hdrs_dict = range_trader.hdrs(token, cid)
        _rl.acquire("order")
        r = _req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs_dict, timeout=10)
        if r.status_code == 429:
            _rl.note_429()
        print(f"[MANUAL ORDER] status={r.status_code} resp={r.text}", flush=True)
        if r.status_code == 200:
            ok_fill, ostatus, _oid = _dhan_live_fate(r, token, cid)
            if not ok_fill:
                # REJECTED/CANCELLED → koi real position nahi. Record MAT karo (phantom se bacho).
                return jsonify({'ok': False, 'msg': f'Dhan ne order {ostatus} kiya (price-band/margin?) — koi position nahi bani'})
            _write_to_log('LIVE')
            _record('filled' if ostatus == 'TRADED' else 'pending', 'live', _oid)
            return jsonify({'ok': True, 'msg': f'[LIVE] {order_type} {side} {lots}L ({qty_shares} qty) {t_sym} @ {option_ltp:.2f} ({ostatus})'})
        else:
            return jsonify({'ok': False, 'msg': f'Dhan {r.status_code}: {r.text[:300]}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/counterfactual')
def api_counterfactual():
    """Alternate history: what would have happened if user hadn't intervened manually.
    ?date=YYYY-MM-DD (defaults to today)"""
    from datetime import timedelta as _td
    _ist = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
    date = request.args.get("date") or _ist.strftime("%Y-%m-%d")
    try:
        import counterfactual as cf
        result = cf.analyze(date)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/api/kite-csv-upload', methods=['POST'])
def api_kite_csv_upload():
    """Upload a Zerodha tradebook CSV to cache as kite_trades_YYYY-MM-DD.json.
    Form field: file (CSV), date (YYYY-MM-DD optional, defaults to today)."""
    import tempfile, os
    from datetime import timedelta as _td
    _ist = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
    date = request.form.get('date') or _ist.strftime('%Y-%m-%d')
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp:
        f.save(tmp.name)
        try:
            import counterfactual as cf
            cf.load_kite_csv(tmp.name, date)
            return jsonify({'ok': True, 'date': date})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            os.unlink(tmp.name)

@app.route('/api/peak-pnl-history')
def api_peak_pnl_history():
    """Returns P&L history for any date. Accepts ?date=YYYY-MM-DD (defaults to today).
    Strategy: daemon file PRIMARY (captures unrealized peaks every minute) with 09:15
    anchor prepended if daemon started late. order_store used only for entry markers
    and as full fallback when daemon file has no data for that date.
    Response: {data: [[time, cum_pnl, trail_peak, peak_ever], ...],
               entries: [[entry_time, cum_pnl_at_entry, sym], ...],
               profit_target_rs, lock_pct, lock_rs}"""
    from datetime import timedelta as _td
    _ist_now = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
    req_date = request.args.get("date") or _ist_now.strftime("%Y-%m-%d")
    is_today = (req_date == _ist_now.strftime("%Y-%m-%d"))

    try:
        import risk_gate as _rg
        gcfg = (_rg._risk_cfg().get("global") or {})
        lock_rs          = gcfg.get("trailing_profit_lock_rs")
        lock_pct         = gcfg.get("trailing_profit_lock_pct")
        profit_target_rs = gcfg.get("profit_target_rs")
    except Exception:
        lock_rs = lock_pct = profit_target_rs = None

    def _to_min(hm):
        try:
            h, m = hm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    # ── Build entry markers from order_store (independent of P&L source) ──
    entries = []
    try:
        import order_store as _os
        details = (_os.trades_for(req_date).get("details") or [])
        completed = [d for d in details if d.get("exit_time") and d.get("pnl") is not None]
        completed.sort(key=lambda d: d["exit_time"])

        if completed:
            # Build cumulative P&L timeline to place entry markers at correct Y
            _cum = 0.0
            _exits = {d["exit_time"]: d for d in completed}
            _entry_events = sorted(
                {(d["entry_time"], d["sym"]) for d in completed if d.get("entry_time")},
                key=lambda x: x[0])
            # We'll place entry markers at the cum P&L value just BEFORE the exit
            # that follows them; simpler: track cum at each entry using sorted exits
            _eidx = 0
            _cum2 = 0.0
            for d in completed:
                while _eidx < len(_entry_events) and _entry_events[_eidx][0] <= d["exit_time"]:
                    et, esym = _entry_events[_eidx]
                    entries.append([et, round(_cum2, 2), esym])
                    _eidx += 1
                _cum2 += float(d["pnl"] or 0)
            while _eidx < len(_entry_events):
                et, esym = _entry_events[_eidx]
                entries.append([et, round(_cum2, 2), esym])
                _eidx += 1
    except Exception:
        pass

    # ── PRIMARY: per-date daemon file (captures unrealized peaks every minute) ──
    # Each entry: [time_str, cum_pnl, trail_peak, peak_ever]
    # Daemon writes to peak_pnl_history.json for today; past dates stored in
    # peak_pnl_history_YYYY-MM-DD.json (if archiving enabled) — try both.
    daemon_pts = []
    try:
        candidates = []
        if is_today:
            candidates.append(BASE_DIR / "data" / "peak_pnl_history.json")
        candidates.append(BASE_DIR / "data" / f"peak_pnl_history_{req_date}.json")
        for f in candidates:
            if f.exists():
                raw = json.loads(f.read_text())
                if raw:
                    daemon_pts = raw
                    break
    except Exception:
        pass

    if daemon_pts:
        # Daemon format: [time, trail_peak, total_mtm, peak_ever]
        # Normalize to: [time, total_mtm, trail_peak, peak_ever]
        def _norm(p):
            if len(p) >= 4:
                return [p[0], p[2], p[1], p[3]]
            elif len(p) == 3:
                return [p[0], p[2], p[1], p[1]]
            return p

        # Clip to market hours only (09:15–15:30) — daemon may run after hours
        MARKET_OPEN  = _to_min("09:15")
        MARKET_CLOSE = _to_min("15:30")
        def _safe_norm(p):
            try:
                n = _norm(p)
                t = _to_min(str(n[0]))
                v = float(n[1])
                if MARKET_OPEN <= t <= MARKET_CLOSE and v == v:  # NaN guard
                    return n
            except Exception:
                pass
            return None
        mkt_pts = [e for p in daemon_pts for e in [_safe_norm(p)] if e is not None]

        if mkt_pts:
            pts = mkt_pts
            # Prepend 09:15 anchor only if daemon started DURING market hours (not long after open)
            # If daemon started within 30 min of open, anchor at 09:15; otherwise skip anchor
            # (avoids fake flat-line when daemon started mid-day or later)
            first_min = _to_min(pts[0][0])
            if first_min > MARKET_OPEN and first_min <= MARKET_OPEN + 30:
                pts = [["09:15", 0.0, 0.0, 0.0]] + pts
            elif first_min == MARKET_OPEN:
                pass  # already starts at open
            # else: daemon started mid-day — don't anchor at 09:15, show from where data begins

            # Extend to 15:30 for past dates / closed market
            now_hm = _ist_now.strftime("%H:%M")
            end_hm = "15:30" if (not is_today or now_hm >= "15:30") else now_hm
            if pts[-1][0] < end_hm:
                last = pts[-1]
                pts = pts + [[end_hm, last[1], last[2], last[3]]]
            return jsonify({"data": pts, "entries": entries,
                            "lock_pct": lock_pct, "lock_rs": lock_rs,
                            "profit_target_rs": profit_target_rs})
        # Daemon file existed but had no market-hours data → fall through to order_store

    # ── FALLBACK: reconstruct from order_store exits (no unrealized peaks) ──
    try:
        import order_store as _os
        details = (_os.trades_for(req_date).get("details") or [])
        completed = [d for d in details if d.get("exit_time") and d.get("pnl") is not None]
        completed.sort(key=lambda d: d["exit_time"])

        if completed:
            pts = [["09:15", 0.0, 0.0, 0.0]]
            cum = trail_peak = peak_ever = 0.0
            for d in completed:
                cum += float(d["pnl"] or 0)
                trail_peak = max(trail_peak, cum)
                peak_ever  = max(peak_ever, cum)
                pts.append([d["exit_time"], round(cum, 2), round(trail_peak, 2), round(peak_ever, 2)])
            now_hm = _ist_now.strftime("%H:%M")
            end_hm = "15:30" if (not is_today or now_hm >= "15:30") else now_hm
            if pts[-1][0] < end_hm:
                pts.append([end_hm, round(cum, 2), round(trail_peak, 2), round(peak_ever, 2)])
            return jsonify({"data": pts, "entries": entries,
                            "lock_pct": lock_pct, "lock_rs": lock_rs,
                            "profit_target_rs": profit_target_rs})
    except Exception:
        pass

    return jsonify({"data": [], "entries": [], "lock_pct": lock_pct,
                    "lock_rs": lock_rs, "profit_target_rs": profit_target_rs})


@app.route('/api/close-position', methods=['POST'])
def api_close_position():
    """Close an open position — place opposite order using exact trading symbol.

    Group-safety (2026-06-29): a sold option + its auto-hedge BUY share a
    group_id. Closing only one leg of that pair through this single-leg
    route used to leave the other naked with no automatic protection — the
    margin requirement for a naked option SELL is dramatically higher than
    for the hedged spread, so an unnoticed unhedged leg risks a margin call
    that force-squares-off unrelated positions or blocks new orders entirely
    (the scenario that prompted this fix). Now: look up the leg's group_id
    first: if it has one, close every leg in that group together (same
    logic as /api/close-position-group) regardless of which UI button was
    clicked — there is no longer a single-leg-only path for a hedged pair.
    """
    data     = request.get_json()
    t_sym    = data.get('t_sym', '')        # e.g. NIFTY-Jun2026-24100-CE
    entry_side = data.get('entry_side', '') # BUY or SELL
    qty_shares = int(data.get('qty', 65))
    mode     = data.get('mode', 'paper')
    # source/strategy of the OPEN leg — close ko isi (source,strategy,trad_sym) se
    # record karo taaki order_store.trades_for me net hoke completed ban jaaye.
    src_in   = data.get('source', '') or 'manual'
    strat_in = data.get('strategy', '') or ''

    try:
        import order_store
        from datetime import timedelta as _td
        today = (datetime.now(timezone.utc) + _td(hours=5, minutes=30)).strftime('%Y-%m-%d')
        open_pos = order_store.trades_for(today).get('open', [])
        this_leg = next((p for p in open_pos if p.get('sym') == t_sym and p.get('entry') == entry_side), None)
        gid = (this_leg or {}).get('group_id')
        if gid:
            siblings = [p for p in open_pos if p.get('group_id') == gid]
            if len(siblings) > 1:
                results = []
                for leg in siblings:
                    r = _close_position_impl(leg['sym'], leg['entry'], leg['qty'], mode,
                                              leg.get('source', 'manual'), leg.get('strategy', ''))
                    r['sym'] = leg['sym']
                    results.append(r)
                all_ok = all(r.get('ok') for r in results)
                return jsonify({'ok': all_ok,
                    'msg': '[GROUP-CLOSE — hedge pair] ' + '; '.join(r.get('msg', '') for r in results),
                    'legs': results})
    except Exception:
        pass  # best-effort group lookup — fall through to single-leg close on any failure

    return jsonify(_close_position_impl(t_sym, entry_side, qty_shares, mode, src_in, strat_in))


def _close_position_impl(t_sym, entry_side, qty_shares, mode, src_in, strat_in):
    """Shared close-one-leg logic — used by /api/close-position and (looped per
    leg) by /api/close-position-group. Returns the same {ok, msg} dict shape
    the route used to jsonify directly."""
    close_side = 'SELL' if entry_side == 'BUY' else 'BUY'

    try:
        import range_trader, requests as _req, time as _time
        token, cid = _creds()

        # Security ID from scrip master
        sec_id = _get_sec_ids([t_sym]).get(t_sym, '')

        # Get current LTP — retry 3x (rapid closes Dhan marketfeed ~1req/sec ko 429 dete).
        option_ltp = 0.0
        if sec_id:
            for _attempt in range(3):
                try:
                    qh = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
                    qr = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                                   json={"NSE_FNO": [int(sec_id)]}, headers=qh, timeout=5)
                    if qr.status_code == 200:
                        qdata = qr.json().get("data", {}).get("NSE_FNO", {})
                        for v in (qdata.values() if isinstance(qdata, dict) else []):
                            ltp_v = float(v.get("last_price") or v.get("ltp") or 0)
                            if ltp_v:
                                option_ltp = ltp_v
                                break
                    if option_ltp:
                        break
                except Exception:
                    pass
                _time.sleep(1.2)

        # CRITICAL: LTP na mile to close ko 0.00 par record MAT karo — wo P&L
        # corrupt karta (SELL @71 → exit @0 = jhootha bada profit). Refuse + bolo.
        # Phantom/expired position clear karni ho to 🗑 book-close use karo.
        if not option_ltp:
            return {'ok': False, 'msg': f'{t_sym} ka LTP nahi mila (Dhan rate-limit/expired) — close record NAHI kiya. Dobara try karo, ya phantom ho to 🗑 book-close use karo.'}

        ts = int(_time.time())

        def _write_log(tag):
            try:
                cfg_data = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                active = next(iter(cfg_data.keys()), 'range_v1')
                log_path = BASE_DIR / 'logs' / f'{active}.log'
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with open(log_path, 'a') as lf:
                    lf.write(f"{now},000  INFO      [{tag}] {close_side} {qty_shares} {t_sym} @ {option_ltp:.2f}  correlationId=CLOSE_{t_sym}_{ts}\n")
            except Exception:
                pass

        def _record_close(status_, m, oid=''):
            try:
                import order_store
                order_store.record(close_side, qty_shares, option_ltp, source=src_in, strategy=strat_in,
                    mode=m, broker='dhan', symbol=t_sym.split('-')[0], instrument='options', trad_sym=t_sym,
                    sec_id=sec_id, segment='NSE_FNO', broker_order_id=oid,
                    correlation_id=f'CLOSE_{t_sym}_{ts}', status=status_,
                    tags=['MANUAL_CLOSE'])
            except Exception:
                pass

        if mode == 'paper':
            _write_log('PAPER')
            _record_close('paper', 'paper')
            return {'ok': True, 'msg': f'[PAPER] CLOSE {close_side} {qty_shares} {t_sym} @ {option_ltp:.2f}'}

        if not sec_id:
            return {'ok': False, 'msg': f'Security ID not found for {t_sym}'}

        body = {
            'dhanClientId': cid, 'correlationId': f'CLOSE_{t_sym}_{ts}',
            'transactionType': close_side, 'exchangeSegment': 'NSE_FNO',
            'productType': 'INTRADAY', 'orderType': 'MARKET', 'validity': 'DAY',
            'securityId': sec_id, 'tradingSymbol': t_sym,
            'quantity': qty_shares, 'disclosedQuantity': 0,
            'price': 0, 'triggerPrice': 0, 'afterMarketOrder': False,
        }
        hdrs = range_trader.hdrs(token, cid)
        _rl.acquire("order")
        r = _req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs, timeout=10)
        if r.status_code == 429:
            _rl.note_429()
        if r.status_code == 200:
            ok_fill, ostatus, _oid = _dhan_live_fate(r, token, cid)
            if not ok_fill:
                return {'ok': False, 'msg': f'Dhan ne close order {ostatus} kiya — position band nahi hui (Dhan pe verify karo)'}
            _write_log('LIVE')
            _record_close('filled' if ostatus == 'TRADED' else 'pending', 'live', _oid)
            return {'ok': True, 'msg': f'[LIVE] CLOSE {close_side} {qty_shares} {t_sym} ({ostatus})'}
        else:
            return {'ok': False, 'msg': f'Dhan {r.status_code}: {r.text[:200]}'}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}


@app.route('/api/close-position-group', methods=['POST'])
def api_close_position_group():
    """Square off ALL open legs sharing a group_id together (e.g. a sold option
    + its auto-placed hedge) — one button, one combined result, instead of
    closing each leg independently and risking a half-closed hedge."""
    import order_store
    from datetime import timedelta as _td
    data = request.get_json() or {}
    group_id = (data.get('group_id') or '').strip()
    mode = data.get('mode', 'paper')
    if not group_id:
        return jsonify({'ok': False, 'msg': 'group_id required'})

    today = (datetime.now(timezone.utc) + _td(hours=5, minutes=30)).strftime('%Y-%m-%d')
    open_pos = order_store.trades_for(today).get('open', [])
    legs = [p for p in open_pos if p.get('group_id') == group_id]
    if not legs:
        return jsonify({'ok': False, 'msg': f'No open legs found for group {group_id}'})

    results = []
    for leg in legs:
        r = _close_position_impl(leg['sym'], leg['entry'], leg['qty'], mode,
                                  leg.get('source', 'manual'), leg.get('strategy', ''))
        r['sym'] = leg['sym']
        results.append(r)

    all_ok = all(r.get('ok') for r in results)
    return jsonify({'ok': all_ok, 'msg': '; '.join(r.get('msg', '') for r in results), 'legs': results})


@app.route('/api/orders/book-close', methods=['POST'])
def api_orders_book_close():
    """Open position ko BOOK se hatao — koi real Dhan order NAHI jaata. Sirf ek
    offsetting leg (same price → pnl 0) record hota hai jo position ko net karke
    Completed me bhej deta hai. Use: rejected/phantom live positions (jo Dhan pe
    asal me the hi nahi) ya kisi bhi stuck entry ko ledger se saaf karne ke liye."""
    import order_store, time as _t
    d = request.get_json() or {}
    t_sym      = (d.get('t_sym') or '').strip()
    entry_side = (d.get('entry_side') or '').upper()
    qty        = int(d.get('qty', 0) or 0)
    price      = float(d.get('entry_price', 0) or 0)
    mode       = d.get('mode', 'paper')
    source     = d.get('source', '') or 'manual'
    strategy   = d.get('strategy', '') or ''
    if not t_sym or entry_side not in ('BUY', 'SELL'):
        return jsonify({'ok': False, 'msg': 'bad request (t_sym/entry_side)'})
    close_side = 'SELL' if entry_side == 'BUY' else 'BUY'
    try:
        order_store.record(close_side, qty, price, source=source, strategy=strategy,
            mode=mode, broker='dhan', symbol=t_sym.split('-')[0], instrument='options',
            trad_sym=t_sym, sec_id='', segment='NSE_FNO',
            correlation_id=f'BOOKCLOSE_{t_sym}_{int(_t.time())}',
            status='bookclose', tags=['bookclose'])
        return jsonify({'ok': True, 'msg': f'Book-closed {t_sym} (no real order, pnl 0)'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/debug-order')
def api_debug_order():
    """Test Dhan API call directly from Flask process — diagnose DH-905"""
    try:
        import range_trader, requests as req, socket as sk
        # confirm IPv4 patch is active
        ipv4_active = sk.getaddrinfo.__name__ == '_v4'
        token, cid = _creds()
        _hdrs_dbg = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        _rl.acquire("ltp")
        _qr_dbg   = req.post("https://api.dhan.co/v2/marketfeed/ltp",
                             json={"IDX_I": [13]}, headers=_hdrs_dbg, timeout=5)
        price = float(_qr_dbg.json()["data"]["IDX_I"]["13"]["last_price"])
        body = {
            'dhanClientId': cid, 'correlationId': 'DEBUG_001',
            'transactionType': 'SELL', 'exchangeSegment': 'NSE_FNO',
            'productType': 'INTRADAY', 'orderType': 'MARKET', 'validity': 'DAY',
            'securityId': '56376', 'tradingSymbol': 'NIFTY-Jun2026-24100-CE',
            'quantity': 65, 'disclosedQuantity': 0, 'price': 0, 'triggerPrice': 0,
            'afterMarketOrder': False,
        }
        hdrs = range_trader.hdrs(token, cid)
        _rl.acquire("order")
        r = req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs, timeout=10)
        if r.status_code == 429:
            _rl.note_429()
        return jsonify({'ipv4_patch': ipv4_active, 'status': r.status_code,
                        'dhan_response': r.text, 'body_sent': body,
                        'token_preview': token[-10:] if token else 'NONE'})
    except Exception as e:
        return jsonify({'error': str(e)})

BACKTEST_DB_FILE = BASE_DIR / "backtest_db.json"

@app.route('/api/backtest-db', methods=['GET'])
def backtest_db_get():
    try:
        return jsonify(json.loads(BACKTEST_DB_FILE.read_text()))
    except Exception:
        return jsonify({})

@app.route('/api/backtest-db', methods=['POST'])
def backtest_db_set():
    try:
        BACKTEST_DB_FILE.write_text(json.dumps(request.get_json(), ensure_ascii=False))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/pine/save', methods=['POST'])
def api_pine_save():
    import re, json as _json
    code = request.json.get('code', '').strip()
    if not code:
        return jsonify({"error": "Empty code"}), 400
    desc = request.json.get('desc', '').strip()
    lang = (request.json.get('lang') or _detect_lang(code)).strip().lower()
    if lang not in ("pine", "python", "dsl"):
        lang = "pine"
    # Name: Pine pulls it from strategy("..."); python/dsl take the user-given
    # name (Script editor field) and fall back to a strategy()/header hint.
    m = re.search(r'strategy\s*\(\s*"([^"]+)"', code)
    req_name = (request.json.get('name') or '').strip()
    strat_name = req_name or (m.group(1) if m else None) or "script"
    pine_dir = BASE_DIR / '_PINE'
    pine_dir.mkdir(exist_ok=True)
    ver_file = pine_dir / 'versions.json'
    versions = _json.loads(ver_file.read_text()) if ver_file.exists() else []
    # NOT len(versions)+1 — any hand-edited/out-of-order entry (e.g. a manually
    # registered version) makes the array length diverge from the highest
    # version id actually in use, and the next save then collides with an
    # existing version, silently overwriting that version's snapshot and image
    # folder. Happened once already (rsi_v1's v6.pine got clobbered by a later
    # vwap save that also landed on id 6) — use the real max instead.
    version = max((v.get("version", 0) for v in versions), default=0) + 1
    from datetime import datetime, timedelta, timezone, timezone, timedelta
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ts = ist.strftime('%Y-%m-%d %H:%M IST')
    strat_version = sum(1 for v in versions if v.get('name') == strat_name) + 1
    author  = request.json.get('author', 'Arsalan').strip()
    slug = re.sub(r'[^a-z0-9]+', '_', strat_name.lower()).strip('_') or 'script'

    entry = {"version": version, "name": strat_name, "strat_version": strat_version,
             "timestamp": ts, "desc": desc, "author": author, "lang": lang}

    # snapshot extension by language
    ext = {"pine": "pine", "python": "py", "dsl": "rules"}[lang]
    (pine_dir / f'{slug}_latest.{ext}').write_text(code, encoding='utf-8')
    (pine_dir / f'v{version}.{ext}').write_text(code, encoding='utf-8')

    # ── Make python/dsl scripts RUNNABLE: register a config entry (keyed by a
    #    unique script id) in nifty_config.json — the backtest dropdown lists
    #    every config key automatically, and api_backtest_run dispatches by the
    #    `_module`/`_lang` markers we write here. `user_` prefix avoids ever
    #    colliding with a built-in strategy file/config key. ──
    if lang in ("python", "dsl"):
        script_id = f"user_{slug}_v{strat_version}"
        try:
            all_cfg = _json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        except Exception:
            all_cfg = {}
        if lang == "python":
            py_rel = f"strategies/{script_id}.py"
            (BASE_DIR / py_rel).write_text(code, encoding='utf-8')
            entry["py_file"] = py_rel
            hdr = _script_header(code)
            cfg_entry = {"_module": f"strategies.{script_id}", "_lang": "python",
                         "symbol": hdr.get("symbol", "NIFTY"),
                         "timeframe": hdr.get("timeframe", "5m"),
                         "qty": int(hdr.get("qty", 1)) if str(hdr.get("qty", "1")).isdigit() else 1,
                         "active": False}
        else:  # dsl
            parsed = _parse_dsl_block(code)
            cfg_entry = {**parsed, "_lang": "dsl",
                         "symbol": parsed.get("symbol", "NIFTY"),
                         "timeframe": parsed.get("timeframe", "5m"),
                         "active": False}
        all_cfg[script_id] = cfg_entry
        TC_FILE.write_text(_json.dumps(all_cfg, indent=2, ensure_ascii=False))
        entry["script_id"] = script_id

    versions.append(entry)
    ver_file.write_text(_json.dumps(versions, indent=2, ensure_ascii=False))
    return jsonify(entry)

@app.route('/api/pine/code/<int:version>')
def api_pine_code(version):
    import json as _json, re
    pine_dir = BASE_DIR / '_PINE'
    EXTS = ('pine', 'py', 'rules')   # pine / python / dsl snapshots
    # Try version-specific snapshot first (any language), fallback to that
    # version's strategy "latest" file.
    for e in EXTS:
        vfile = pine_dir / f'v{version}.{e}'
        if vfile.exists():
            return vfile.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}
    ver_file = pine_dir / 'versions.json'
    if ver_file.exists():
        versions = _json.loads(ver_file.read_text())
        v = next((x for x in versions if x['version'] == version), None)
        if v:
            # Legacy/hand-registered entries (never went through /api/pine/save)
            # carry an explicit "pine_file" pointer — try that first.
            if v.get('pine_file'):
                explicit = pine_dir / v['pine_file']
                if explicit.exists():
                    return explicit.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}
            slug = re.sub(r'[^a-z0-9]+', '_', v['name'].lower()).strip('_') or 'unknown'
            for e in EXTS:
                latest = pine_dir / f'{slug}_latest.{e}'
                if latest.exists():
                    return latest.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}
    return 'Code not found', 404

@app.route('/api/backtest/pine-code')
def api_backtest_pine_code():
    """Pine source for the strategy actually run in a backtest — used by the
    Results page's 'Copy Code' button so the user can paste the exact same
    Pine code into TradingView for an apples-to-apples comparison (instead of
    guessing which Pine version matches the Python run)."""
    import json as _json
    sid = request.args.get('strategy', '')
    strat_type = _base(sid)   # e.g. "range_v1" -> "range", "rsi_v1" -> "rsi"
    pine_dir = BASE_DIR / '_PINE'

    # Fast path: a Pine snapshot file literally named after the strategy id
    # (e.g. "rsi_v1.pine") — several hand-registered versions never got linked
    # into versions.json's pine_file/py_file fields, so check disk directly
    # before falling back to the version-history lookup.
    direct = pine_dir / f'{sid}.pine'
    if direct.exists():
        return direct.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}

    ver_file = pine_dir / 'versions.json'
    if not ver_file.exists():
        return 'No Pine versions found', 404
    try:
        versions = _json.loads(ver_file.read_text())
    except Exception:
        return 'versions.json unreadable', 500

    # Latest version whose py_file's basename relates to this strategy's type
    # (py_file isn't always literally "<sid>.py" — e.g. range_v1's py_file is
    # "range_trader.py" — so match on the file stem containing the base type),
    # newest entries are at the end.
    match = None
    for v in reversed(versions):
        py = v.get('py_file', '') or ''
        stem = py.replace('strategies/', '').replace('.py', '')
        if stem and (stem == sid or strat_type in stem or stem in strat_type):
            match = v
            break
    if not match:
        return f'No Pine version mapped to strategy "{sid}"', 404

    return api_pine_code(match['version'])

@app.route('/api/pine/delete/<int:version>', methods=['DELETE'])
def api_pine_delete(version):
    import json as _json
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify({"ok": False, "error": "No versions file"}), 404
    versions = _json.loads(ver_file.read_text())
    gone = next((v for v in versions if v['version'] == version), None)
    versions = [v for v in versions if v['version'] != version]
    ver_file.write_text(_json.dumps(versions, indent=2, ensure_ascii=False))
    # snapshot (any language extension)
    for e in ('pine', 'py', 'rules'):
        f = BASE_DIR / '_PINE' / f'v{version}.{e}'
        if f.exists():
            f.unlink()
    # If this was a runnable user script, also drop its nifty_config entry + the
    # generated strategies/ file so it disappears from the backtest dropdown.
    sid = (gone or {}).get('script_id')
    if sid:
        try:
            all_cfg = _json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
            if sid in all_cfg:
                all_cfg.pop(sid, None)
                TC_FILE.write_text(_json.dumps(all_cfg, indent=2, ensure_ascii=False))
        except Exception:
            pass
        pyf = BASE_DIR / 'strategies' / f'{sid}.py'
        if pyf.exists():
            pyf.unlink()
    return jsonify({"ok": True})

@app.route('/pine/report/<int:version>')
def pine_report(version):
    import json as _json
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return "No versions", 404
    versions = _json.loads(ver_file.read_text())
    v = next((x for x in versions if x['version'] == version), None)
    if not v or not v.get('report_file'):
        return "No report attached", 404
    rpath = BASE_DIR / v['report_file']
    if not rpath.exists():
        return "Report file missing", 404
    return rpath.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/api/pine/latest')
def api_pine_latest():
    import json as _json
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify({"version": 0, "name": "—", "timestamp": "—"})
    versions = _json.loads(ver_file.read_text())
    return jsonify(versions[-1] if versions else {"version": 0, "name": "—", "timestamp": "—"})

@app.route('/api/pine/desc', methods=['POST'])
def api_pine_desc():
    import json as _json
    data = request.json
    version = data.get('version')
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify({"error": "No versions"}), 404
    versions = _json.loads(ver_file.read_text())
    for v in versions:
        if v['version'] == version:
            if 'desc'         in data: v['desc']         = data['desc'].strip()
            if 'py_file'      in data: v['py_file']      = data['py_file'].strip()
            if 'accuracy'     in data: v['accuracy']     = data['accuracy']
            if 'report_file'   in data: v['report_file']   = data['report_file']
            if 'report_stats'  in data: v['report_stats']  = data['report_stats']
            if 'strat_version' in data: v['strat_version'] = data['strat_version']
            break
    ver_file.write_text(_json.dumps(versions, indent=2, ensure_ascii=False))
    return jsonify({"ok": True})

@app.route('/api/pine/history')
def api_pine_history():
    import json as _json
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify([])
    return jsonify(list(reversed(_json.loads(ver_file.read_text()))))

@app.route('/api/pine/images/<int:version>', methods=['GET'])
def api_pine_images_get(version):
    img_dir = BASE_DIR / '_PINE' / f'v{version}_imgs'
    if not img_dir.exists():
        return jsonify([])
    files = sorted(img_dir.glob('*'), key=lambda f: f.stat().st_mtime)
    return jsonify([f'/pine/img/{version}/{f.name}' for f in files if f.is_file()])

@app.route('/api/pine/images/<int:version>', methods=['POST'])
def api_pine_images_upload(version):
    # NOT "import imghdr" — removed in Python 3.13+ (this server runs 3.14),
    # so every image upload 500'd before even reaching mkdir(). It was never
    # actually used below (extension comes from f.filename instead), so the
    # import alone was the entire bug — nothing else needed it.
    import uuid
    img_dir = BASE_DIR / '_PINE' / f'v{version}_imgs'
    img_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for f in request.files.getlist('images'):
        ext = Path(f.filename).suffix.lower() or '.png'
        fname = f'{uuid.uuid4().hex}{ext}'
        dest = img_dir / fname
        f.save(str(dest))
        saved.append(f'/pine/img/{version}/{fname}')
    return jsonify({'ok': True, 'urls': saved})

@app.route('/api/pine/images/<int:version>/<fname>', methods=['DELETE'])
def api_pine_images_delete(version, fname):
    import re
    if re.search(r'[/\\]', fname):
        return jsonify({'ok': False}), 400
    fpath = BASE_DIR / '_PINE' / f'v{version}_imgs' / fname
    if fpath.exists():
        fpath.unlink()
    return jsonify({'ok': True})

@app.route('/pine/img/<int:version>/<fname>')
def pine_img_serve(version, fname):
    import re, mimetypes
    if re.search(r'[/\\]', fname):
        return 'invalid', 400
    img_dir = BASE_DIR / '_PINE' / f'v{version}_imgs'
    fpath = img_dir / fname
    if not fpath.exists():
        return 'not found', 404
    mime = mimetypes.guess_type(str(fpath))[0] or 'image/png'
    return fpath.read_bytes(), 200, {'Content-Type': mime}

@app.route('/api/pine/strategies')
def api_pine_strategies():
    """Return unique strategies that have a py_file, for the Run tab dropdown."""
    import json as _json
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify([])
    versions = _json.loads(ver_file.read_text())
    seen, result = set(), []
    for v in reversed(versions):
        py = v.get('py_file', '')
        if not py:
            continue
        # derive strategy id: "strategies/rsi_v1.py" → "rsi_v1"
        sid = py.replace('strategies/', '').replace('.py', '')
        if sid in seen:
            continue
        seen.add(sid)
        result.append({"id": sid, "py_file": py, "name": v.get('name', sid),
                        "version": v.get('version'), "timestamp": v.get('timestamp', '')})
    return jsonify(result)

@app.route('/api/run-status')
def api_run_status():
    """Return running status of all known strategy ids."""
    status = {}
    for sid in list(STRATEGIES.keys()):
        status[sid] = bool(get_pid(sid))
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        for sid in cfg:
            if sid not in status:
                status[sid] = bool(get_pid(sid))
    except Exception:
        pass
    return jsonify(status)

@app.route('/api/backtest/progress')
def api_backtest_progress():
    """Polled by the Results page while /api/backtest/run is in flight, so a
    multi-day Dhan download (which blocks that request for a while) shows a
    live 'downloading TCS 3/12' instead of a frozen spinner."""
    import sys as _s
    _s.path.insert(0, str(BASE_DIR / "_TOOLS"))
    import backtest_engine as be
    return jsonify(be.progress)

@app.route('/api/backtest/optimize')
def api_backtest_optimize():
    params_str = request.args.get("params")
    if not params_str:
        return jsonify({"error": "No params provided"}), 400
    try:
        p = json.loads(params_str)
        strat_type = p["strat_type"]
        grid = p["grid"]
        date_from = p.get("date_from", "")
        date_to = p.get("date_to", "")
        symbols = p.get("symbols", "NIFTY")
    except Exception as e:
        return jsonify({"error": str(e)}), 400
        
    import sys as _s
    if str(BASE_DIR / "_TOOLS") not in _s.path:
        _s.path.insert(0, str(BASE_DIR / "_TOOLS"))
    import optimizer
    
    def generate():
        try:
            for update in optimizer.run_optimization_stream(strat_type, grid, date_from, date_to, symbols):
                yield f"data: {json.dumps(update)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
    return Response(generate(), mimetype='text/event-stream')


@app.route('/api/backtest/optimizations', methods=['GET'])
def api_get_optimizations():
    try:
        hist_file = BASE_DIR / "data" / "saved_optimizations.json"
        if not hist_file.exists():
            return jsonify([])
        return jsonify(json.loads(hist_file.read_text()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/backtest/optimizations/<int:run_id>', methods=['DELETE'])
def api_delete_optimization(run_id):
    try:
        hist_file = BASE_DIR / "data" / "saved_optimizations.json"
        if not hist_file.exists():
            return jsonify({"success": True})
        hist = json.loads(hist_file.read_text())
        hist = [h for h in hist if h["id"] != run_id]
        hist_file.write_text(json.dumps(hist, indent=2))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/backtest/run', methods=['POST'])
def api_backtest_run():
    """Generic date-range backtest for any strategy type (range/rsi/ema).
    Accepts multipart form (date_from, date_to, strategy=<id>, optional tv_files —
    one or more: Pine Logs .log/.txt AND/OR List-of-Trades .csv, upload both
    together and this picks the more reliable one) or plain JSON body. Returns
    candles + python trades (+ TV trades/accuracy if a TV file was attached)."""
    import sys as _s, tempfile
    _s.path.insert(0, str(BASE_DIR / "_TOOLS"))
    import backtest_engine as be

    cfg_override = None
    if request.content_type and "multipart" in request.content_type:
        sid       = request.form.get("strategy", "range")
        date_from = request.form.get("date_from") or None
        date_to   = request.form.get("date_to") or None
        tv_files  = [f for f in request.files.getlist("tv_files") if f and f.filename]
        cfg_raw   = request.form.get("cfg_override")
        if cfg_raw:
            try:
                cfg_override = json.loads(cfg_raw)
            except Exception:
                cfg_override = None
    else:
        body      = request.get_json(silent=True) or {}
        sid       = body.get("strategy", "range")
        date_from = body.get("date_from")
        date_to   = body.get("date_to")
        tv_files  = []
        cfg_override = body.get("cfg_override")

    strat_type = _base(sid)
    BUILTIN = ("range", "rsi", "rsi_v1", "ema", "vwap", "bb")

    cfg_file = STRATEGIES.get(strat_type, {}).get("cfg", TC_FILE)
    try:
        all_cfg = json.loads(Path(cfg_file).read_text()) if Path(cfg_file).exists() else {}
        disk_cfg = all_cfg.get(sid, {})
    except Exception:
        disk_cfg = {}
    # Custom user scripts (Script library) live in nifty_config.json keyed by
    # their full id and carry a `_module` (python) or `_lang=dsl` marker. They
    # aren't BUILTIN, so if the per-type cfg file didn't have them, fall back to
    # the shared nifty_config.json.
    if not disk_cfg and Path(cfg_file) != TC_FILE:
        try:
            disk_cfg = (json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}).get(sid, {})
        except Exception:
            disk_cfg = {}

    # Edit & Re-run modal can pass a temporary param override without touching
    # the saved config on disk (saving is a separate explicit step).
    cfg = dict(disk_cfg)
    if isinstance(cfg_override, dict):
        cfg.update(cfg_override)
    # `_module` / `_lang` are engine-internal routing markers — always trust the
    # saved values, never the editable Re-run text (so an accidental edit/delete
    # in the cfg textarea can't break dispatch).
    for k in ("_module", "_lang"):
        if disk_cfg.get(k) is not None:
            cfg[k] = disk_cfg[k]

    # Pick the engine runner: custom python → _custom; DSL rule-block → bb
    # (custom_rule_engine); otherwise the built-in type.
    if cfg.get("_module"):
        engine_strat = "_custom"
    elif cfg.get("_lang") == "dsl" or strat_type == "bb":
        engine_strat = "bb"
    elif strat_type in BUILTIN:
        engine_strat = strat_type
    else:
        return jsonify({"error": f"backtest not supported for strategy '{sid}'"}), 400

    # Save every uploaded TV file to temp, then prefer a Pine Logs export
    # (.log/.txt) over a List-of-Trades CSV — per VALIDATION_PLAYBOOK.md the
    # log export is the more reliable single-run ground truth.
    saved_paths = []
    for f in tv_files:
        suffix = os.path.splitext(f.filename)[1].lower() or ".log"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.close()   # release Windows file lock before save/remove
        f.save(tmp.name)
        saved_paths.append(tmp.name)

    tv_log_path = next((p for p in saved_paths if p.lower().endswith((".log", ".txt"))), None) \
                  or (saved_paths[0] if saved_paths else None)

    try:
        result = be.run_backtest(engine_strat, cfg, date_from, date_to, tv_log_path=tv_log_path)
    except Exception as e:
        return jsonify({"error": f"Backtest failed: {e}"}), 200
    finally:
        for p in saved_paths:
            if os.path.exists(p):
                os.remove(p)

    return jsonify(result)

@app.route('/api/scanner/run', methods=['POST'])
def api_scanner_run():
    import sys as _s
    tools_path = str(BASE_DIR / "_TOOLS")
    if tools_path not in _s.path:
        _s.path.insert(0, tools_path)
    try:
        import scanner_ema_52
        results = scanner_ema_52.run_scanner()
        return jsonify({"status": "success", "results": results})
    except Exception as e:
        import traceback
        return jsonify({"status": "error", "message": str(e), "trace": traceback.format_exc()})

@app.route('/api/indicators/list')
def api_indicators_list():
    """Backs the chart's 'Add Indicator' dropdown — name + param schema for
    every standard indicator in _CHARTING/indicators.py's registry."""
    import sys as _s
    _s.path.insert(0, str(BASE_DIR / "_TOOLS"))
    import backtest_engine as be
    return jsonify(be.chind.list_available_indicators())

@app.route('/api/indicators/compute', methods=['POST'])
def api_indicators_compute():
    """Compute one indicator on demand for the chart's 'Add Indicator' picker.
    Body: {symbol, date_from, date_to, name, params, timeframe}. Returns just
    that indicator's plot_spec fragment — the chart appends it without a
    full backtest re-run."""
    import sys as _s
    _s.path.insert(0, str(BASE_DIR / "_TOOLS"))
    import backtest_engine as be

    body = request.get_json(silent=True) or {}
    result = be.compute_indicator_for_chart(
        symbol=body.get("symbol", "NIFTY"),
        date_from=body.get("date_from"),
        date_to=body.get("date_to"),
        name=body.get("name"),
        params=body.get("params") or {},
        timeframe=body.get("timeframe", "5m"),
    )
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)

@app.route('/api/backtest/save-config', methods=['POST'])
def api_backtest_save_config():
    """Edit & Re-run modal's 'Save & Run' — merge edited fields into
    nifty_config.json[sid], same target file the Config tab writes to."""
    body = request.get_json(silent=True) or {}
    sid = body.get("strategy")
    fields = body.get("cfg") or {}
    if not sid:
        return jsonify({"error": "missing strategy id"}), 400
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        cfg.setdefault(sid, {}).update(fields)
        TC_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"msg": f"✅ {sid} config saved"})

@app.route('/api/symbols/search', methods=['GET'])
def api_symbols_search():
    """Backtest Results symbol picker — search Dhan's NSE equity scrip master
    (already cached for live option-chain lookups) instead of the old
    hardcoded NIFTY-50 list, so any listed stock (e.g. TECHM) is findable."""
    q = (request.args.get('q') or '').strip().upper()
    try:
        import dhan_master
        cache = dhan_master.build_equity_cache()
        symbols = sorted(cache.keys())
        if q:
            symbols = [s for s in symbols if q in s]
        return jsonify(symbols[:50])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

SAVED_BACKTESTS_FILE = BASE_DIR / "data" / "saved_backtests.json"

def _load_saved_backtests():
    if not SAVED_BACKTESTS_FILE.exists():
        return []
    try:
        return json.loads(SAVED_BACKTESTS_FILE.read_text())
    except Exception:
        return []

@app.route('/api/backtest/saved', methods=['GET'])
def api_backtest_saved_list():
    """Saved Results table on the Results page — only key stats + the run's
    own strategy/cfg/date-range are stored (not candles/trades), so this is
    light enough to list in full every time without a separate paging API."""
    return jsonify(_load_saved_backtests())

@app.route('/api/backtest/saved', methods=['POST'])
def api_backtest_saved_save():
    # Wrapped in try/except so any unexpected failure here returns JSON —
    # otherwise Flask's default error page is HTML, and the frontend's
    # `await r.json()` throws a confusing "Unexpected token '<'" instead of
    # whatever the real problem was.
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        entries = _load_saved_backtests()
        entry = {
            "id": uuid.uuid4().hex[:10],
            "name": name,
            "strategy": body.get("strategy"),
            "cfg": body.get("cfg") or {},
            "date_from": body.get("date_from"),
            "date_to": body.get("date_to"),
            "summary": body.get("summary") or {},
            "symbols": body.get("symbols"),   # present only for multi-symbol saves
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        entries.append(entry)
        SAVED_BACKTESTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SAVED_BACKTESTS_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
        return jsonify(entry)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/backtest/saved/<sid>', methods=['DELETE'])
def api_backtest_saved_delete(sid):
    try:
        entries = _load_saved_backtests()
        entries = [e for e in entries if e.get("id") != sid]
        SAVED_BACKTESTS_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/watch')
def api_watch():
    """Merge all *_watch.json files — one entry per running strategy."""
    data_dir = BASE_DIR / "data"
    all_rows  = []
    latest_ts = None
    for f in sorted(data_dir.glob("*_watch.json")):
        try:
            d   = json.loads(f.read_text())
            sid = d.get("strategy", f.stem.replace("_watch", ""))
            ts  = d.get("updated")
            if ts and (latest_ts is None or ts > latest_ts):
                latest_ts = ts
            for row in d.get("symbols", []):
                row["strategy"] = sid   # tag each row with its strategy
                all_rows.append(row)
        except Exception:
            continue
    # sort: interesting zones first, then by RSI distance from zone
    zone_order = {"OVERSOLD": 0, "OVERBOUGHT": 1, "NEAR_OS": 2, "NEAR_OB": 3, "NEUTRAL": 4}
    all_rows.sort(key=lambda r: (zone_order.get(r.get("zone","NEUTRAL"), 9), r.get("rsi", 50)))
    return jsonify({"updated": latest_ts, "symbols": all_rows})


@app.route('/api/downloader-alerts')
def api_downloader_alerts():
    alert_file = BASE_DIR / "data" / "downloader_alert.json"
    if not alert_file.exists():
        return jsonify([])
    try:
        return jsonify(json.loads(alert_file.read_text()))
    except Exception:
        return jsonify([])


@app.route('/api/health-report')
def api_health_report():
    """Last startup health-check ka structured report (health_check.py --json ne
    likha). on_demand=1 ho to abhi taaza chala ke do (manual refresh)."""
    if request.args.get('on_demand') == '1':
        try:
            _env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            r = subprocess.run([PYTHON, "-X", "utf8", str(BASE_DIR / "health_check.py"), "--json"],
                               capture_output=True, text=True, cwd=str(BASE_DIR), timeout=150, env=_env)
            if r.stdout.strip().startswith("{"):
                rep = json.loads(r.stdout)
                HEALTH_REPORT.write_text(json.dumps(rep, indent=2))
                return jsonify(rep)
            return jsonify({"error": r.stdout[-200:] or r.stderr[-200:]})
        except Exception as e:
            return jsonify({"error": str(e)})
    if not HEALTH_REPORT.exists():
        return jsonify({"error": "abhi tak koi health report nahi (9:10 auto-check ya on_demand=1)"})
    try:
        return jsonify(json.loads(HEALTH_REPORT.read_text()))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route('/api/save-summary', methods=['POST'])
def api_save_summary():
    try:
        subprocess.run([PYTHON, str(BASE_DIR / 'save_daily_summary.py')], cwd=str(BASE_DIR))
        return jsonify({"msg": "✅ Summary saved to results/"})
    except Exception as e:
        return jsonify({"msg": f"Error: {e}"})


# ── TradingView Webhook → auto order ──────────────────────────────────────────
@app.route('/api/webhook/tv', methods=['POST'])
def api_webhook_tv():
    """Receive a TradingView Pine alert (JSON) and execute via webhook_executor.

    Auth: token via ?token= query OR X-WH-Token header, matched against
    nifty_config.json["webhooks"]["global"]["secret_token"]. Mismatch → 403.
    Body: {"id","strategy","symbol","signal":"ENTRY|EXIT","action":"buy|sell"}
    """
    import webhook_executor as wh
    secret = wh.webhook_secret()
    given  = request.args.get("token") or request.headers.get("X-WH-Token", "")
    if not secret or given != secret:
        return jsonify({"ok": False, "msg": "forbidden"}), 403

    # TradingView posts JSON; tolerate text/plain bodies too.
    payload = request.get_json(silent=True)
    if payload is None:
        try:
            payload = json.loads((request.get_data(as_text=True) or "").strip() or "{}")
        except Exception:
            return jsonify({"ok": False, "msg": "bad payload"}), 400

    _ensure_feed_started()
    try:
        res = wh.handle_signal(payload)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)}), 500


@app.route('/api/webhook/status')
def api_webhook_status():
    import webhook_executor as wh
    return jsonify(wh.status())


@app.route('/api/orders')
def api_orders():
    """Trade DB (order_store) — completed trades + open positions for a date,
    with source/mode/strategy/broker tags. Query: date, source, mode, broker,
    strategy, instrument. Plus distinct filter values for the UI dropdowns."""
    import order_store
    from datetime import datetime, timedelta, timezone, timezone, timedelta
    ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    date = request.args.get('date') or ist.strftime('%Y-%m-%d')
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    data = order_store.trades_for(date, **filt)
    data['date'] = date
    data['filters'] = {f: order_store.distinct(f, date)
                       for f in ('source', 'mode', 'strategy', 'broker')}
    try:
        import risk_gate as _rg
        _rc   = _rg._risk_cfg()
        _mult = float((_rc.get("global") or {}).get("margin_multiplier") or 5.0)
        for p in data.get('open', []):
            if (p.get('tags') or []) and 'CAPITAL_BLOCKED' in p['tags']:
                continue
            try:
                qty      = float(p.get("qty") or 0)
                price    = float(p.get("entry_price") or 0)
                notional = qty * price
                if str(p.get("entry") or "").upper() == "SELL":
                    notional *= _mult
                p['margin_used'] = round(notional, 2)
            except Exception:
                p['margin_used'] = 0
    except Exception as _e:
        pass
    return jsonify(data)


@app.route('/api/orders/calendar-summary')
def api_orders_calendar_summary():
    """Returns daily P&L and trade count summary for a given year and month."""
    import order_store
    year = request.args.get('year')
    month = request.args.get('month')
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    
    prefix = ""
    if year and month:
        prefix = f"{year}-{month.zfill(2)}-%"
    elif year:
        prefix = f"{year}-%"
        
    import sqlite3
    db_path = order_store.DB_PATH
    dates = []
    try:
        with order_store._lock, sqlite3.connect(str(db_path), timeout=10) as c:
            sql = "SELECT DISTINCT date FROM orders"
            args = []
            if prefix:
                sql += " WHERE date LIKE ?"
                args.append(prefix)
            sql += " ORDER BY date ASC"
            dates = [r[0] for r in c.execute(sql, args).fetchall() if r[0]]
    except Exception as e:
        print("[calendar_summary] distinct dates fail:", e, flush=True)
        
    summary = {}
    all_trades = []
    for d in dates:
        data = order_store.trades_for(d, **filt)
        det = data.get('details', [])
        if det:
            pnl_sum = sum(t.get('pnl') or 0 for t in det)
            summary[d] = {
                'pnl': round(pnl_sum, 2),
                'count': len(det)
            }
            for t in det:
                all_trades.append(t)
            
    # Also include distinct filter options for the UI
    try:
        distinct_filters = {
            'strategy': order_store.distinct('strategy'),
            'broker': order_store.distinct('broker')
        }
    except Exception:
        distinct_filters = {'strategy': [], 'broker': []}
        
    return jsonify({
        'summary': summary,
        'trades': all_trades,
        'filters': distinct_filters
    })


@app.route('/api/orders/stats-summary')
def api_orders_stats_summary():
    """Profit Factor / Expectancy / Sharpe over a date range (live/paper data),
    plus the closed-trades list for the Stats tab's grouped/toggleable table.
    Separate from /api/orders/calendar-summary to avoid changing that route's
    existing response shape (calendar view is already in production use)."""
    import order_store
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    metrics = order_store.stats_summary(date_from=date_from, date_to=date_to, **filt)
    details = order_store.trades_for_range(date_from or "0000-00-00", date_to or "9999-12-31", **filt)['details']
    return jsonify({'ok': True, 'metrics': metrics, 'trades': details})


@app.route('/api/orders/rename-strategy', methods=['POST'])
def api_rename_strategy():
    data = request.get_json()
    old_strat = data.get('old_strategy', '')
    new_strat = data.get('new_strategy', '')
    if not old_strat or not new_strat:
        return jsonify({"status": "error", "message": "Missing parameters"})
    try:
        import order_store
        with order_store._lock, order_store._conn() as c:
            c.execute("UPDATE orders SET strategy = ? WHERE strategy = ?", (new_strat, old_strat))
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/orders/update-sl-tp', methods=['POST'])
def api_update_sl_tp():
    """Per-position SL/Target. Generic form (sl_type/sl_val, tp_type/tp_val —
    type one of pct/pt/rs/premium/index) takes priority; legacy sl_pct/tp_pct
    kept for older callers."""
    data = request.get_json()
    order_id = data.get('id')
    sl_type = data.get('sl_type'); sl_val = data.get('sl_val')
    tp_type = data.get('tp_type'); tp_val = data.get('tp_val')
    sl_pct = data.get('sl_pct')
    tp_pct = data.get('tp_pct')
    if not order_id:
        return jsonify({"status": "error", "message": "Missing order ID"})
    try:
        import order_store
        with order_store._lock, order_store._conn() as c:
            row = c.execute("SELECT tags FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not row: return jsonify({"status": "error", "message": "Order not found"})

            tags = []
            try: tags = json.loads(row[0] or "[]")
            except: pass

            tags = [t for t in tags if not t.startswith(("SL_PCT:", "TP_PCT:", "SL_TYPE:", "SL_VAL:", "TP_TYPE:", "TP_VAL:"))]

            if sl_type and sl_val is not None and str(sl_val).strip() != "":
                tags.append(f"SL_TYPE:{sl_type}")
                tags.append(f"SL_VAL:{float(sl_val)}")
            elif sl_pct is not None and str(sl_pct).strip() != "":
                tags.append(f"SL_PCT:{float(sl_pct)}")

            if tp_type and tp_val is not None and str(tp_val).strip() != "":
                tags.append(f"TP_TYPE:{tp_type}")
                tags.append(f"TP_VAL:{float(tp_val)}")
            elif tp_pct is not None and str(tp_pct).strip() != "":
                tags.append(f"TP_PCT:{float(tp_pct)}")

            c.execute("UPDATE orders SET tags = ? WHERE id = ?", (json.dumps(tags), order_id))
            c.commit()
        return jsonify({"status": "success", "tags": tags})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/orders/update-note', methods=['POST'])
def api_update_note():
    data = request.get_json()
    order_id = data.get('id')
    note = data.get('note', '')
    if not order_id:
        return jsonify({"status": "error", "message": "Missing order ID"})
    try:
        import order_store
        with order_store._lock, order_store._conn() as c:
            row = c.execute("SELECT tags FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not row: return jsonify({"status": "error", "message": "Order not found"})
            
            tags = []
            try: tags = json.loads(row[0] or "[]")
            except: pass
            
            tags = [t for t in tags if not t.startswith("NOTE:")]
            
            if note.strip():
                # Replace newlines with a special sequence or just encode it. JSON handles newlines.
                # But to be safe with our split logic elsewhere, let's just save it.
                tags.append(f"NOTE:{note.strip()}")
                
            c.execute("UPDATE orders SET tags = ? WHERE id = ?", (json.dumps(tags), order_id))
            c.commit()
        return jsonify({"status": "success", "tags": tags})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/orders/upload-image', methods=['POST'])
def api_orders_upload_image():
    """Attach one or more images to a trade's note — saved to disk under
    data/note_images/<order_id>/, persisted as IMG:<filename> tags so they
    survive in history (not just the day they were taken)."""
    order_id = request.form.get('id')
    files = request.files.getlist('images')
    if not order_id or not files:
        return jsonify({"status": "error", "message": "Missing order id or images"})
    try:
        import order_store, time as _time
        order_dir = NOTE_IMG_DIR / str(order_id)
        order_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            if not f.filename:
                continue
            ext = os.path.splitext(f.filename)[1][:10] or '.jpg'
            fname = f"{int(_time.time()*1000)}_{len(saved)}{ext}"
            f.save(str(order_dir / fname))
            saved.append(fname)
        with order_store._lock, order_store._conn() as c:
            row = c.execute("SELECT tags FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not row: return jsonify({"status": "error", "message": "Order not found"})
            tags = []
            try: tags = json.loads(row[0] or "[]")
            except: pass
            for fname in saved:
                tags.append(f"IMG:{fname}")
            c.execute("UPDATE orders SET tags = ? WHERE id = ?", (json.dumps(tags), order_id))
            c.commit()
        return jsonify({"status": "success", "tags": tags, "saved": saved})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/orders/note-image/<order_id>/<filename>')
def api_orders_note_image(order_id, filename):
    return send_from_directory(str(NOTE_IMG_DIR / order_id), filename)


@app.route('/api/orders/delete-image', methods=['POST'])
def api_orders_delete_image():
    data = request.get_json()
    order_id = data.get('id'); filename = data.get('filename')
    if not order_id or not filename:
        return jsonify({"status": "error", "message": "Missing id or filename"})
    try:
        import order_store
        with order_store._lock, order_store._conn() as c:
            row = c.execute("SELECT tags FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not row: return jsonify({"status": "error", "message": "Order not found"})
            tags = []
            try: tags = json.loads(row[0] or "[]")
            except: pass
            tags = [t for t in tags if t != f"IMG:{filename}"]
            c.execute("UPDATE orders SET tags = ? WHERE id = ?", (json.dumps(tags), order_id))
            c.commit()
        img_path = NOTE_IMG_DIR / str(order_id) / filename
        if img_path.exists():
            try: img_path.unlink()
            except Exception: pass
        return jsonify({"status": "success", "tags": tags})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

import threading
import requests


HEALTH_REPORT = BASE_DIR / "data" / "health_report.json"

def _startup_healthcheck():
    """9:10 auto-start ke baad bots ko boot hone do, phir health_check.py --json
    chala ke data/health_report.json likho. Koi ACTIVE strategy order-ready na ho
    to dashboard ke red banner (downloader_alert.json) me alert push karo —
    taaki subah firefight ki jagah ek nazar me dikh jaaye kya nahi laga."""
    import time as _t, json as _j
    _t.sleep(90)
    try:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        r = subprocess.run([PYTHON, "-X", "utf8", str(BASE_DIR / "health_check.py"), "--json"],
                           capture_output=True, text=True, cwd=str(BASE_DIR), timeout=150, env=env)
        rep = _j.loads(r.stdout) if r.stdout.strip().startswith("{") else {"error": r.stdout[-200:]}
        HEALTH_REPORT.write_text(_j.dumps(rep, indent=2))
        reds = [s["id"] for s in rep.get("strategies", []) if s.get("red")]
        alert_file = BASE_DIR / "data" / "downloader_alert.json"
        alerts = _j.loads(alert_file.read_text()) if alert_file.exists() else []
        alerts = [a for a in alerts if "Health" not in a]   # purana health alert hatao
        if reds:
            alerts.append(f"⚠️ Health: {', '.join(reds)} order-ready NAHI — health_check report dekho")
        alert_file.write_text(_j.dumps(alerts))
        print(f"[health] startup check done — RED: {reds or 'none'}")
    except Exception as e:
        print("startup healthcheck error:", e)

def auto_scheduler():
    from datetime import datetime, timedelta, timezone, timezone, timedelta
    import time
    def ist_now():
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    
    last_date = None
    has_started_today = False
    has_stopped_today = False

    while True:
        try:
            now = ist_now()
            if last_date != now.date():
                last_date = now.date()
                has_started_today = False
                has_stopped_today = False
            
            t = (now.hour, now.minute)

            if (9, 10) <= t < (15, 30):
                if not has_started_today:
                    print(f"[{now.strftime('%H:%M:%S')}] Auto-starting bots in PAPER mode...")
                    try:
                        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                        for key in cfg.keys():
                            if _base(key) not in STRATEGIES:
                                continue  # not a process strategy (e.g. webhook_v1, vwap)
                            if isinstance(cfg[key], dict) and cfg[key].get("active", True):
                                requests.post(f"http://127.0.0.1:5099/api/start?s={key}&mode=paper", timeout=5)
                    except Exception as e:
                        pass
                    has_started_today = True
                    # bots start hone ke baad auto health-check (90s baad, alag thread)
                    threading.Thread(target=_startup_healthcheck, daemon=True).start()

            if t >= (15, 30):
                if not has_stopped_today:
                    print(f"[{now.strftime('%H:%M:%S')}] Auto-stopping bots...")
                    try:
                        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                        for key in cfg.keys():
                            if _base(key) not in STRATEGIES:
                                continue  # not a process strategy (e.g. webhook_v1, vwap)
                            if isinstance(cfg[key], dict):
                                # keep_active=1 -> intent rakho, kal auto-start phir chale
                                requests.post(f"http://127.0.0.1:5099/api/stop?s={key}&keep_active=1", timeout=5)
                    except Exception as e:
                        pass
                    has_stopped_today = True

        except Exception as e:
            print("Auto Scheduler Error:", e)
        
        time.sleep(30)

def webhook_monitor_loop():
    """Trails SL / target / 3:15 squareoff for open TradingView-webhook positions."""
    import webhook_executor as wh
    import time
    while True:
        try:
            _ensure_feed_started()
            wh.monitor_tick()
        except Exception as e:
            print("Webhook monitor error:", e)
        time.sleep(3)

_rest_ltp_cache = {}   # sec_id -> (ltp, ts) — 3s TTL, avoids DH-904 across many open positions
_REST_LTP_TTL = 3

def _rest_ltp_fallback(sec_id, seg):
    """Direct Dhan REST LTP call — used when the dhan_feed WebSocket isn't
    delivering quotes (e.g. dhanhq version mismatch). Same endpoint/shape as
    /api/positions-ltp's own REST fallback."""
    import time as _t
    cached = _rest_ltp_cache.get(sec_id)
    if cached and (_t.time() - cached[1]) < _REST_LTP_TTL:
        return cached[0]
    try:
        import requests as _req
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        dhan_seg = {"NSE_EQ": "NSE_EQ", "IDX_I": "IDX_I", "NSE_FNO": "NSE_FNO"}.get(seg, "NSE_FNO")
        body = {dhan_seg: [int(sec_id)]}
        r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
        if r.status_code == 200:
            quotes = (r.json().get("data", {}) or {}).get(dhan_seg, {})
            q = quotes.get(str(sec_id)) or quotes.get(str(int(sec_id)))
            if q:
                ltp = float(q.get("last_price") or q.get("ltp") or 0)
                if ltp > 0:
                    _rest_ltp_cache[sec_id] = (ltp, _t.time())
                    return ltp
    except Exception as e:
        print("[_rest_ltp_fallback] fail:", e, flush=True)
    return None


_candle_close_cache = {}   # sec_id -> (close_price, fetched_at) — throttles Dhan intraday calls
_CANDLE_CLOSE_TTL = 30      # seconds; a 1-min candle only closes once a minute anyway

def _last_closed_candle_close(sec_id, seg):
    """Close price of the most recently CLOSED 1-min candle (not the still-forming
    one) — used by the CANDLE_CLOSE SL/TP trigger type. Cached (30s TTL) to avoid
    hammering Dhan's intraday-candle endpoint every pos_monitor_loop tick (same
    DH-904 rate-limit concern already documented elsewhere in this codebase)."""
    import time as _t
    cached = _candle_close_cache.get(sec_id)
    if cached and (_t.time() - cached[1]) < _CANDLE_CLOSE_TTL:
        return cached[0]
    try:
        import requests as _req, datetime as _dt
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        date_str = now_ist.strftime("%Y-%m-%d")
        inst = "EQUITY" if seg == "NSE_EQ" else ("INDEX" if seg == "IDX_I" else "OPTIDX")
        r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=headers, json={
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": date_str, "toDate": date_str}, timeout=8)
        d = r.json()
        if not d.get("close"):
            return None
        closes = d["close"]
        timestamps = d["timestamp"]
        now_epoch = int((now_ist - timedelta(hours=5, minutes=30) - datetime(1970, 1, 1)).total_seconds())
        # drop the still-forming bar (its timestamp + 60s hasn't elapsed yet)
        closed_idx = [i for i, ts in enumerate(timestamps) if int(ts) + 60 <= now_epoch]
        if not closed_idx:
            return None
        last_close = float(closes[closed_idx[-1]])
        _candle_close_cache[sec_id] = (last_close, _t.time())
        return last_close
    except Exception as e:
        print("[_last_closed_candle_close] fail:", e, flush=True)
        return None


_peak_ltp_cache    = {}    # {sec_id: last_known_ltp} — prevents fake dips when feed fails

# Restore peak + history from file on startup — so restart mid-day doesn't reset the floor.
# Only restore if history entries exist and were written TODAY (check file mtime).
_trailing_peak_pnl = 0.0
_daily_peak_ever   = 0.0   # monotonic daily max — NEVER resets, used for graph floor line
_peak_pnl_history  = []
try:
    import datetime as _dt_mod
    _phf_init = BASE_DIR / "data" / "peak_pnl_history.json"
    if _phf_init.exists():
        _fmtime = _dt_mod.datetime.fromtimestamp(_phf_init.stat().st_mtime)
        _today  = _dt_mod.datetime.now().date()
        if _fmtime.date() == _today:           # file written today → safe to restore
            _hist_init = json.loads(_phf_init.read_text())
            if _hist_init:
                # v[1] = peak at that tick (resets after squareoff)
                # v[3] = daily_peak_ever (if present, never resets)
                _trailing_peak_pnl = max(v[1] for v in _hist_init)
                _daily_peak_ever   = max(v[3] if len(v) > 3 else v[1] for v in _hist_init)
                _peak_pnl_history  = _hist_init
                print(f"[TRAILING-LOCK] Restored peak ₹{_trailing_peak_pnl:.0f} "
                      f"(daily max ₹{_daily_peak_ever:.0f}) "
                      f"from {len(_hist_init)} history entries after restart.", flush=True)
except Exception as _e_init:
    print(f"[TRAILING-LOCK] Peak restore failed (ok, starting fresh): {_e_init}", flush=True)


def _trailing_lock_fired_today() -> bool:
    """Returns True if trailing squareoff already fired today — blocks new entries."""
    try:
        from datetime import datetime, timedelta, timezone as _dtc
        _flag = BASE_DIR / "data" / f"trailing_lock_fired_{_dtc.now().strftime('%Y-%m-%d')}.txt"
        return _flag.exists()
    except Exception:
        return False


def pos_monitor_loop():
    """Monitors open positions for SL_PCT, TP_PCT hits and tracks MAX/MIN LTP."""
    import time
    import order_store
    import dhan_feed
    from datetime import timedelta
    global _trailing_peak_pnl, _daily_peak_ever, _peak_pnl_history, _peak_ltp_cache

    while True:
        try:
            _ensure_feed_started()
            # 'datetime' is the CLASS (from datetime import datetime, timedelta, timezone) — datetime.datetime
            # galat tha, har loop crash karta tha (SL/TP monitor band pada tha).
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

            data = order_store.trades_for(ist_now.strftime('%Y-%m-%d'))
            open_pos = data.get("open", [])

            # ── Ghost position sync (TRAP #44) ───────────────────────────────
            # Every 2 min: reconcile DB open positions against actual broker
            # positions. Manually-closed or reject-orphaned positions that are
            # flat at the broker get marked externally_closed so the monitor
            # stops watching them (and trailing lock can't accidentally open
            # new longs on them).
            try:
                import broker_sync as _bsync
                _ghost_ids = _bsync.sync_if_due(open_pos, log=print)
                if _ghost_ids:
                    # Re-fetch so this cycle runs on clean data
                    data = order_store.trades_for(ist_now.strftime('%Y-%m-%d'))
                    open_pos = data.get("open", [])
            except Exception as _bse:
                print(f"[broker_sync] skipped (error): {_bse}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            # Tracks ids already squared-off THIS pass (e.g. as a hedge group
            # sibling) — without this, the for-loop below would re-process a
            # sibling leg that _do_squareoff already closed earlier this pass.
            _closed_ids = set()

            # ── Account-level trailing profit lock ───────────────────────────
            # Config: _risk.global.trailing_profit_lock_rs in nifty_config.json
            # If account P&L drops more than this ₹ from its peak today →
            # squareoff EVERYTHING. Only activates once peak > 0 (i.e. you're
            # in profit — not a stop-loss, it's a profit-protector).
            try:
                import risk_gate as _rg
                _gcfg = (_rg._risk_cfg().get("global") or {})
                _trail_rs  = _gcfg.get("trailing_profit_lock_rs")
                _trail_pct = _gcfg.get("trailing_profit_lock_pct")
                _either_set = (_trail_rs and float(_trail_rs) > 0) or \
                              (_trail_pct and float(_trail_pct) > 0)

                # Always compute realized+unrealized for Stats graph — regardless of lock config
                _realized = _rg._today_realized_pnl()
                _unrealized = 0.0
                _active_pos = [_p for _p in open_pos
                               if _p.get("status") != "blocked"
                               and "CAPITAL_BLOCKED" not in (_p.get("tags") or [])]
                for _p in _active_pos:
                    _sid = _p.get("sec_id")
                    _seg = "NSE_EQ" if _p.get("instrument") == "EQUITY" else "NSE_FNO"
                    _ltp = float((dhan_feed.get_quote(_sid) or {}).get("ltp") or 0) or \
                           _rest_ltp_fallback(_sid, _seg) or 0.0
                    if _ltp > 0:
                        _peak_ltp_cache[_sid] = _ltp        # fresh — update cache
                    else:
                        _ltp = _peak_ltp_cache.get(_sid, 0.0)  # stale feed — use last known
                    _epx = float(_p.get("entry_price") or _p.get("price") or 0)
                    _qty = int(_p.get("qty") or 0)
                    if _ltp > 0 and _epx > 0 and _qty:
                        _unrl = (_ltp - _epx) * _qty if _p.get("entry") == "BUY" \
                                else (_epx - _ltp) * _qty
                        _unrealized += _unrl
                _total_pnl = _realized + _unrealized

                # Update high watermark + record history for Stats graph (always)
                if _total_pnl > _trailing_peak_pnl:
                    _trailing_peak_pnl = _total_pnl
                if _total_pnl > _daily_peak_ever:
                    _daily_peak_ever = _total_pnl   # monotonic — never resets
                _peak_pnl_history.append((
                    (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%H:%M"),
                    round(_trailing_peak_pnl, 2),
                    round(_total_pnl, 2),
                    round(_daily_peak_ever, 2),      # v[3]: floor line base (monotonic, never drops)
                ))
                if len(_peak_pnl_history) > 500:
                    _peak_pnl_history = _peak_pnl_history[-500:]
                # Write to file so dashboard process can read it via API
                try:
                    _phf = BASE_DIR / "data" / "peak_pnl_history.json"
                    _phf.write_text(json.dumps(_peak_pnl_history))
                except Exception:
                    pass
                # Archive previous day's file at midnight rollover
                try:
                    _today_str = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                    _arch_f = BASE_DIR / "data" / f"peak_pnl_history_{_today_str}.json"
                    # If archive for today doesn't exist yet and current file has data
                    # from a PREVIOUS date, archive it first then reset
                    if not _arch_f.exists() and _peak_pnl_history:
                        _first_hm = _peak_pnl_history[0][0]  # "HH:MM"
                        # Check mtime of peak_pnl_history.json — if written on a previous date, archive it
                        import os as _os_mod
                        _mdate = _dt_mod.datetime.fromtimestamp(_phf.stat().st_mtime).strftime("%Y-%m-%d") if _phf.exists() else _today_str
                        if _mdate < _today_str:
                            # Archive the previous day's data
                            _prev_arch = BASE_DIR / "data" / f"peak_pnl_history_{_mdate}.json"
                            _prev_arch.write_text(json.dumps(_peak_pnl_history))
                            # Reset for new day
                            _peak_pnl_history.clear()
                            _trailing_peak_pnl = 0.0
                except Exception:
                    pass

                if _either_set:
                    # Pick threshold: 1 active position → fixed ₹, 2+ → % of peak
                    _n_pos = len(_active_pos)
                    if _n_pos <= 1 and _trail_rs and float(_trail_rs) > 0:
                        _effective_lock = float(_trail_rs)
                        _lock_desc = f"₹{_effective_lock:.0f} (single-pos fixed)"
                    elif _n_pos > 1 and _trail_pct and float(_trail_pct) > 0:
                        _effective_lock = _trailing_peak_pnl * float(_trail_pct) / 100.0
                        _lock_desc = f"₹{_effective_lock:.0f} ({_trail_pct}% of peak)"
                    elif _trail_rs and float(_trail_rs) > 0:
                        _effective_lock = float(_trail_rs)   # fallback: use ₹ if pct not set
                        _lock_desc = f"₹{_effective_lock:.0f} (fixed fallback)"
                    else:
                        _effective_lock = 0.0
                        _lock_desc = "none"
                    # Fire if peak was positive AND drawdown from peak exceeds lock
                    _drawdown = _trailing_peak_pnl - _total_pnl
                    if _trailing_peak_pnl > 0 and _effective_lock > 0 and _drawdown >= _effective_lock:
                        print(f"[TRAILING-LOCK] Peak ₹{_trailing_peak_pnl:.0f} → now ₹{_total_pnl:.0f} "
                              f"(drawdown ₹{_drawdown:.0f} ≥ lock {_lock_desc}, pos={_n_pos}) — squaring off ALL",
                              flush=True)
                        for _p in list(open_pos):
                            _sid = _p.get("sec_id")
                            if not _sid or _p.get("status") == "blocked": continue
                            if "CAPITAL_BLOCKED" in (_p.get("tags") or []): continue
                            if _p.get("id") in _closed_ids: continue
                            _seg = "NSE_EQ" if _p.get("instrument") == "EQUITY" else "NSE_FNO"
                            _ltp2 = float((dhan_feed.get_quote(_sid) or {}).get("ltp") or 0) or \
                                    _rest_ltp_fallback(_sid, _seg) or 0.0
                            if _ltp2 > 0:
                                try:
                                    import smart_order
                                    from brokers import get_broker
                                    _exit_side = "SELL" if _p.get("entry") == "BUY" else "BUY"
                                    _bname = _p.get("broker") or "dhan"
                                    if _p.get("mode") == "live":
                                        _br = get_broker(_bname)
                                        smart_order.execute(
                                            _exit_side, _p["sym"], _sid, _seg, _p["qty"], _p["sym"],
                                            _p["mode"], _br, log=print, tag="TRAILING",
                                            source=_p.get("source",""), strategy=_p.get("strategy",""),
                                            instrument=_p.get("instrument",""), broker_name=_bname,
                                            extra_tags=["TRAILING_PROFIT_LOCK"],
                                        )
                                    else:
                                        import order_store as _os
                                        _os.record(
                                            side=_exit_side, qty=_p["qty"], price=_ltp2,
                                            source=_p.get("source",""), strategy=_p.get("strategy",""),
                                            mode=_p.get("mode","paper"), broker=_bname,
                                            symbol=_p["sym"], instrument=_p.get("instrument",""),
                                            trad_sym=_p["sym"], sec_id=_sid, segment=_seg,
                                            status="paper", tags=["TRAILING_PROFIT_LOCK"],
                                        )
                                    _closed_ids.add(_p.get("id"))
                                except Exception as _te:
                                    print(f"[TRAILING-LOCK] squareoff failed for {_p.get('sym')}: {_te}", flush=True)
                        _trailing_peak_pnl = 0.0   # reset so it doesn't re-fire next cycle
                        # Write day-level flag so webhook/strategy blocks new entries
                        try:
                            from datetime import datetime, timedelta, timezone as _dtc
                            _flag = BASE_DIR / "data" / f"trailing_lock_fired_{_dtc.now().strftime('%Y-%m-%d')}.txt"
                            _flag.write_text(f"fired at {_dtc.now().strftime('%H:%M:%S')}, peak was ₹{_daily_peak_ever:.0f}")
                            print(f"[TRAILING-LOCK] Flag written: {_flag.name} — new entries blocked for today.", flush=True)
                        except Exception as _fe:
                            print(f"[TRAILING-LOCK] Flag write failed: {_fe}", flush=True)
                        time.sleep(5)
                        continue                    # skip per-position checks this cycle
            except Exception as _trail_e:
                print(f"[TRAILING-LOCK] check error (skipped): {_trail_e}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            for p in open_pos:
                if p.get("id") in _closed_ids: continue
                if not p.get("tags") and not p.get("sec_id"): continue
                sec_id = p.get("sec_id")
                if not sec_id: continue

                tags = p.get("tags") or []
                # CAPITAL_BLOCKED legs are not real holdings — they're rejected
                # entries recorded for visibility (status='blocked'). Never
                # square them off (would create a phantom opposite trade); they
                # carry no live exposure for SL/TP/RMS to act on.
                if p.get("status") == "blocked" or "CAPITAL_BLOCKED" in tags:
                    continue

                # Isolate each position's check — a feed hiccup, a malformed
                # tag, or any other exception on ONE position must never skip
                # SL/TP/RMS enforcement for every OTHER open position this
                # cycle (previously an uncaught exception here propagated to
                # the loop's single top-level try/except, which silently
                # skipped the entire `for p in open_pos` pass).
                try:
                    _pos_monitor_check_one(p, sec_id, tags, ist_now, open_pos, _closed_ids)
                except Exception as _pe:
                    print(f"[pos_monitor] check failed for {p.get('sym')} (id={p.get('id')}) "
                          f"— leaving position open, will retry next cycle: {_pe}", flush=True)
        except Exception as e:
            print("Pos monitor error:", e)

        time.sleep(5)


# Consecutive LTP-miss counter per sec_id — once a position has gone this many
# cycles (≈30s at 5s/cycle) with NO price from feed, REST, or even the stale
# cross-process cache, SL/TP/RMS literally cannot be evaluated for it (there's
# no safe action without a price). Past this threshold we escalate loudly in
# the log instead of silently retrying forever — visibility is the realistic
# safety net here, since a stale/guessed price could itself trigger a wrong
# exit.
_ltp_miss_streak = {}
_rms_fail_streak = {}
_LTP_MISS_ALERT_AFTER = 6
# After this many no-price cycles (~5 min), attempt blind emergency exit for
# LIVE positions (smart_order fetches its own price via REST/feed — best effort).
# Paper positions can't be exited at ₹0 (TRAP #1), so only CRITICAL log fires.
_NO_PRICE_EMERGENCY_EXIT_AFTER = 60

# sec_id -> exit_reason — a leg whose group-sibling already closed but whose
# OWN price couldn't be fetched at that exact moment (feed+REST+stale-cache
# all empty for that instant). Without this, a transient price miss during
# group-close would silently leave that leg open and unprotected (e.g. a
# naked option SELL after its hedge BUY leg closed) until 3:15 EOD — the
# user-flagged risk this guards against. Checked first thing every cycle for
# every open position; forces the close through using the SAME price the
# normal per-position check just successfully resolved, bypassing all other
# SL/TP/EOD logic (this leg is leaving regardless of its own trigger state —
# its sibling is already gone).
_pending_group_close = {}

def _pos_monitor_check_one(p, sec_id, tags, ist_now, open_pos, _closed_ids):
    import dhan_feed
    import order_store
    seg = "NSE_EQ" if p.get("instrument") == "EQUITY" else "NSE_FNO"
    _feed_subscribe([(seg, sec_id)])

    q = dhan_feed.get_quote(sec_id)
    ltp = float(q.get("ltp") or 0) if q else 0.0
    if ltp <= 0:
        # WebSocket feed (dhan_feed) needs dhanhq's DhanContext/MarketFeed,
        # which some installed dhanhq versions don't export — when that's
        # the case the feed silently never starts and ltp stays 0 forever,
        # so SL/TP/EOD-squareoff would never fire for ANY position. REST
        # fallback here mirrors /api/positions-ltp's same fallback path.
        ltp = _rest_ltp_fallback(sec_id, seg) or 0.0
    if ltp <= 0:
        # Last-resort tier: the cross-process shared_ltp_cache other
        # strategies/processes populate (range_trader's place_order already
        # uses this same cache → direct → stale pattern) — a few-minutes-old
        # price beats no check at all when both live sources are down.
        try:
            import shared_ltp_cache
            ltp = shared_ltp_cache.get_stale(sec_id, max_age=120) or 0.0
        except Exception:
            ltp = 0.0
    if ltp <= 0:
        streak = _ltp_miss_streak.get(sec_id, 0) + 1
        _ltp_miss_streak[sec_id] = streak
        if streak >= _LTP_MISS_ALERT_AFTER and streak % _LTP_MISS_ALERT_AFTER == 0:
            print(f"[pos_monitor] ⚠️ CRITICAL: {p.get('sym')} (strategy={p.get('strategy')}) "
                  f"has had NO price for {streak} consecutive cycles (~{streak*5}s) — "
                  f"SL/TP/RMS cannot be checked for this position. Feed + REST + stale "
                  f"cache all empty. Check Dhan token/rate-limit.", flush=True)
        # TRAP #43: after 5 min of no price, LIVE positions get a blind emergency
        # exit (smart_order fetches its own price via REST — best effort, better
        # than holding a position with a blown SL indefinitely). Paper positions
        # cannot record ₹0 exit (TRAP #1), so only a loud warning fires there.
        if streak >= _NO_PRICE_EMERGENCY_EXIT_AFTER:
            _ltp_miss_streak.pop(sec_id, None)  # reset streak — one attempt per cycle
            if p.get("mode") == "live":
                print(f"[pos_monitor] 🚨 NO-PRICE EMERGENCY EXIT: {p.get('sym')} — "
                      f"{streak} cycles ({streak*5}s) with zero LTP. Attempting blind "
                      f"exit via smart_order (will use its own REST fallback).", flush=True)
                _do_squareoff(p, 0.0, "NO_PRICE_EMERGENCY_EXIT", sec_id, seg)
            else:
                print(f"[pos_monitor] 🚨 NO-PRICE {p.get('mode','paper').upper()} POSITION: "
                      f"{p.get('sym')} — {streak} cycles with zero LTP. Cannot exit paper "
                      f"at ₹0 (TRAP #1). MANUAL EXIT REQUIRED immediately.", flush=True)
            _ltp_miss_streak[sec_id] = 0  # restart count so next 5 min it fires again
        return
    _ltp_miss_streak.pop(sec_id, None)

    entry_px = float(p.get("entry_price") or 0)
    if entry_px > 0:
        max_ltp = ltp
        min_ltp = ltp
        max_tag_idx = -1
        min_tag_idx = -1
        for i, t in enumerate(tags):
            if t.startswith("MAX_LTP:"):
                max_tag_idx = i
                try: max_ltp = max(ltp, float(t.split(":")[1]))
                except: pass
            elif t.startswith("MIN_LTP:"):
                min_tag_idx = i
                try: min_ltp = min(ltp, float(t.split(":")[1]))
                except: pass

        changed = False
        if max_tag_idx != -1:
            if tags[max_tag_idx] != f"MAX_LTP:{max_ltp}":
                tags[max_tag_idx] = f"MAX_LTP:{max_ltp}"
                changed = True
        else:
            tags.append(f"MAX_LTP:{max_ltp}")
            changed = True

        if min_tag_idx != -1:
            if tags[min_tag_idx] != f"MIN_LTP:{min_ltp}":
                tags[min_tag_idx] = f"MIN_LTP:{min_ltp}"
                changed = True
        else:
            tags.append(f"MIN_LTP:{min_ltp}")
            changed = True

        if changed and p.get("id"):
            order_store.update_tags(p["id"], tags)

    if p["qty"] <= 0 or entry_px <= 0: return

    # Generic per-position SL/TP — set via the ⚙️ modal, type can be
    # %, points (premium), ₹ (amount), absolute premium level, or
    # underlying index/equity level. SL_TYPE/SL_VAL + TP_TYPE/TP_VAL
    # tags take priority over the legacy SL_PCT/TP_PCT tags below.
    sl_type = next((t.split(":", 1)[1] for t in tags if t.startswith("SL_TYPE:")), None)
    sl_val  = next((t.split(":", 1)[1] for t in tags if t.startswith("SL_VAL:")), None)
    tp_type = next((t.split(":", 1)[1] for t in tags if t.startswith("TP_TYPE:")), None)
    tp_val  = next((t.split(":", 1)[1] for t in tags if t.startswith("TP_VAL:")), None)

    def _underlying_ltp(p):
        """Best-effort spot LTP for the option's underlying (index level SL/TP)."""
        root = p.get("symbol") or p["sym"].split("-")[0]
        info = _EQ_IDX_SEC.get(root)
        if not info:
            return None
        u_sec, u_seg = info
        _feed_subscribe([(u_seg, u_sec)])
        q = dhan_feed.get_quote(u_sec)
        u_ltp = float(q.get("ltp") or 0) if q else 0.0
        if u_ltp <= 0:
            u_ltp = _rest_ltp_fallback(u_sec, u_seg) or 0.0
        return u_ltp if u_ltp > 0 else None

    def _generic_px(typ, val, is_sl):
        """Convert a (type, val) SL/TP spec to a premium trigger price, or None."""
        if typ is None or val is None:
            return None
        try: val = float(val)
        except Exception: return None
        side = p["entry"]  # BUY or SELL
        opt_ce = p["sym"].upper().endswith("-CE") or p["sym"].upper().endswith("CE")
        bullish = (side == "BUY" and opt_ce) or (side == "SELL" and not opt_ce)
        if typ == "pct":
            return entry_px * (1 - val/100.0) if (is_sl) == (side == "BUY") else entry_px * (1 + val/100.0)
        if typ == "pt":
            if side == "BUY":
                return entry_px - val if is_sl else entry_px + val
            else:
                return entry_px + val if is_sl else entry_px - val
        if typ == "rs":
            per_unit = val / p["qty"]
            if side == "BUY":
                return entry_px - per_unit if is_sl else entry_px + per_unit
            else:
                return entry_px + per_unit if is_sl else entry_px - per_unit
        if typ == "premium":
            return val  # absolute premium level, taken as-is
        if typ == "index":
            u_ltp = _underlying_ltp(p)
            if u_ltp is None: return None
            # is the index level breach adverse (SL) or favourable (TP) given direction?
            trigger = (u_ltp <= val) if (bullish == is_sl) else (u_ltp >= val)
            return "INDEX_HIT" if trigger else None
        return None

    def _candle_close_px(val):
        """val is the raw SL_VAL/TP_VAL string '<above|below>:<price>'
        (set via the new Candle Close trigger type). Returns a
        sentinel once the last CLOSED 1-min candle's close has
        crossed the specified level in the specified direction."""
        try:
            direction, price_s = str(val).split(":", 1)
            level = float(price_s)
        except Exception:
            return None
        last_close = _last_closed_candle_close(sec_id, seg)
        if last_close is None:
            return None
        if direction == "above" and last_close > level: return "CANDLE_HIT"
        if direction == "below" and last_close < level: return "CANDLE_HIT"
        return None

    def _do_squareoff(p, ltp, exit_reason, sec_id, seg):
        """Exit a position now — live round-trips a real broker order
        first (never marks closed unless the broker confirms), paper
        just records the fill. Returns True once handled."""
        # Webhook positions are co-owned by webhook_executor's own
        # monitor/TV-EXIT. Atomically CLAIM the leg first: release_position
        # flips its in-memory state open->closed and returns True only if
        # WE won the race. If it returns False the webhook side already
        # closed it this instant — skip, so we don't fire a duplicate
        # closing order (the orphan-BUY double-close bug).
        if (p.get("source") or "") == "webhook":
            try:
                import webhook_executor as _wh
                claimed = _wh.release_position(sec_id=sec_id,
                                               trad_sym=p.get("sym"),
                                               reason=exit_reason)
                if not claimed:
                    _closed_ids.add(p.get("id"))
                    return True
            except Exception as _e:
                print(f"[{exit_reason}] webhook claim failed: {_e}")
        print(f"[{exit_reason}] {p['sym']} LTP {ltp}. Squaring off...")
        exit_side = "SELL" if p["entry"] == "BUY" else "BUY"

        # ── Pre-exit broker check (TRAP #44 guard) ──────────────────────────
        # Before placing any exit order, verify the position actually exists at
        # the broker. If it's already flat (manually closed / earlier reject
        # orphan), placing a closing order would open a NEW opposite position.
        # Uses broker_sync's cached data (fresh API call only if cache stale).
        if p.get("mode") == "live":
            try:
                import broker_sync as _bsync
                _br_name = (p.get("broker") or "dhan").lower()
                _sym_chk = p.get("sym") or ""
                _sec_chk = str(p.get("sec_id") or "")
                if _bsync.is_flat(_br_name, _sym_chk, _sec_chk):
                    import order_store as _os2
                    _os2.mark_externally_closed(p.get("id"))
                    _closed_ids.add(p.get("id"))
                    print(f"[{exit_reason}] PRE-EXIT CHECK: {p['sym']} already FLAT at "
                          f"{_br_name} — marked externally_closed, skipping exit order. "
                          f"(TRAP #44: would have opened accidental new position)", flush=True)
                    return True
            except Exception as _pe:
                # Fail open — never block a real exit due to sync check error
                print(f"[{exit_reason}] pre-exit broker check failed ({_pe}) — proceeding", flush=True)
        # ────────────────────────────────────────────────────────────────────

        if p.get("mode") == "live":
            import smart_order
            from brokers import get_broker
            broker = get_broker(p.get("broker") or "dhan")
            try:
                res = smart_order.execute(
                    exit_side, p["sym"], sec_id, seg, p["qty"], p["sym"],
                    p["mode"], broker, log=print, tag="POSMON",
                    source=p["source"], strategy=p["strategy"],
                    instrument=p["instrument"], broker_name=p.get("broker") or "dhan",
                    extra_tags=["pos_monitor_exit", exit_reason],
                )
            except Exception as _ex:
                # A network/API exception here must NOT propagate up and abort
                # this position's check (the per-position try/except in the
                # caller would catch it too, but explicit here documents the
                # intent: never mark closed unless we know the broker round-
                # tripped — leave it open, it retries next 5s cycle.
                print(f"[{exit_reason}] LIVE square-off EXCEPTION for {p['sym']} — {_ex}; leaving position open, will retry")
                return True
            if not res.get("ok"):
                print(f"[{exit_reason}] LIVE square-off FAILED for {p['sym']} — {res.get('reason')}; leaving position open, will retry")
                return True
            # smart_order.execute already persisted the trade — don't double-record.
        else:
            order_store.record(
                side=exit_side, qty=p["qty"], price=ltp, source=p["source"],
                strategy=p["strategy"], mode=p["mode"], broker=p["broker"],
                symbol=p["sym"], instrument=p["instrument"], trad_sym=p["sym"],
                sec_id=sec_id, segment=seg, status=p.get("status", "paper"),
                tags=["pos_monitor_exit", exit_reason]
            )

        # ── Group-aware: a hedge SELL+BUY pair (or any group_id'd
        # legs) must close together — close any open sibling now,
        # tagged so it's clear it followed automatically, not its
        # own independent SL/TP/EOD hit.
        _closed_ids.add(p.get("id"))
        gid = p.get("group_id")
        if gid:
            for sib in open_pos:
                sib_id = sib.get("id")
                if sib_id in _closed_ids or sib is p: continue
                if sib.get("group_id") != gid: continue
                sib_sec = sib.get("sec_id")
                if not sib_sec: continue
                sib_seg = "NSE_EQ" if (sib.get("instrument") or "").upper() == "EQUITY" else "NSE_FNO"
                _feed_subscribe([(sib_seg, sib_sec)])
                qsib = dhan_feed.get_quote(sib_sec)
                sib_ltp = float(qsib.get("ltp") or 0) if qsib else 0.0
                if sib_ltp <= 0:
                    sib_ltp = _rest_ltp_fallback(sib_sec, sib_seg) or 0.0
                if sib_ltp <= 0:
                    try:
                        import shared_ltp_cache
                        sib_ltp = shared_ltp_cache.get_stale(sib_sec, max_age=120) or 0.0
                    except Exception:
                        sib_ltp = 0.0
                if sib_ltp > 0:
                    _do_squareoff(sib, sib_ltp, exit_reason + "_GROUP", sib_sec, sib_seg)
                else:
                    # Every price source failed at this exact instant — leaving
                    # the sibling open here (the old behavior) would mean a
                    # naked option SELL silently outlives its hedge BUY (or
                    # vice versa) until 3:15 EOD catches it, hours later. Queue
                    # a forced retry instead — checked first thing every cycle
                    # for every open position (see _pending_group_close), so
                    # the very next time THIS leg's own price resolves (it's
                    # still being polled normally), the close goes through
                    # immediately instead of waiting for EOD.
                    _pending_group_close[sib_sec] = exit_reason + "_GROUP"
                    print(f"[{exit_reason}_GROUP] ⚠️ sibling {sib.get('sym')} has NO price "
                          f"right now (feed+REST+stale-cache all empty) — queued for forced "
                          f"retry next cycle instead of being left open unprotected", flush=True)
        return True

    # A previous cycle's group-close couldn't get a price for THIS exact leg
    # (see _pending_group_close above) — now that the normal LTP resolution
    # above succeeded for it, force the close through immediately, ahead of
    # any other check. This leg is leaving regardless of its own SL/TP/EOD
    # state; its sibling is already gone.
    if sec_id in _pending_group_close:
        reason = _pending_group_close.pop(sec_id)
        print(f"[{reason}] retry succeeded — {p.get('sym')} now has a price, forcing the "
              f"delayed group-close through", flush=True)
        _do_squareoff(p, ltp, reason, sec_id, seg)
        return

    # ── Expiry-day guards (run before general EOD so they fire earlier) ──────
    # On expiry day: (a) close 20 min earlier to avoid last-hour chaos and
    # physical-delivery margin blocks; (b) if a short option goes ITM, exit
    # immediately — don't wait for EOD, the loss only grows from here.
    _trad_sym = p.get("trad_sym") or p.get("sym") or ""
    _is_option = (p.get("instrument") or "").upper() != "EQUITY"
    if _is_option:
        try:
            import risk_gate as _rg
            _exp_day = _rg.is_expiry_day(trad_sym=_trad_sym, sec_id=sec_id)
        except Exception:
            _exp_day = False

        if _exp_day:
            # (a) Earlier EOD squareoff on expiry day
            _eod_h, _eod_m = _rg.EXPIRY_EOD_HM
            if ist_now.hour > _eod_h or (ist_now.hour == _eod_h and ist_now.minute >= _eod_m):
                _do_squareoff(p, ltp, "EXPIRY_EOD_SQUAREOFF", sec_id, seg)
                return

            # (b) ITM guard — short option went ITM on expiry day → exit now
            # Get underlying spot: use shared_ltp_cache for NIFTY/BANKNIFTY,
            # REST quote for stock options.
            if p.get("entry") == "SELL" and _trad_sym:
                _spot = 0.0
                try:
                    import shared_ltp_cache as _slc
                    _root_sym = _trad_sym.split("-")[0]
                    _idx_id = {"NIFTY": 13, "BANKNIFTY": 25, "FINNIFTY": 27}.get(_root_sym)
                    if _idx_id:
                        _spot = _slc.get(_idx_id) or 0.0
                    if _spot <= 0:
                        # Stock option — use equity LTP from dhan_master sec_id
                        import dhan_master as _dm
                        _eq_info = _dm.get_equity_info(_root_sym) or {}
                        _eq_sid = _eq_info.get("SEM_SMST_SECURITY_ID")
                        if _eq_sid:
                            _spot = _slc.get(str(_eq_sid)) or _rest_ltp_fallback(str(_eq_sid), "NSE_EQ") or 0.0
                except Exception:
                    _spot = 0.0

                if _spot > 0 and _rg.option_is_itm(_trad_sym, _spot):
                    print(f"[EXPIRY-ITM] {_trad_sym} is ITM on expiry day "
                          f"(spot={_spot:.2f}) — squaring off immediately", flush=True)
                    _do_squareoff(p, ltp, "EXPIRY_ITM_SQUAREOFF", sec_id, seg)
                    return

    # ── Blanket 3:15 PM EOD squareoff — this is a positional/intraday
    # system, no option position should carry overnight regardless of
    # which strategy/source opened it. Takes priority over SL/TP so a
    # position with no SL/TP tags set still gets closed at EOD.
    if (ist_now.hour > 15 or (ist_now.hour == 15 and ist_now.minute >= 15)) \
       and _is_option:
        _do_squareoff(p, ltp, "EOD_315_SQUAREOFF", sec_id, seg)
        return

    # ── SUPREME RMS daily-loss breaker — the one guardrail no strategy
    # can bypass. Once a strategy's cumulative P&L today (realized +
    # this leg's unrealized) breaches its unified ₹ cap
    # (risk_gate.effective_daily_loss_cap → per-strategy/global
    # max_loss_rs, always-on default ₹5000), force-close THIS leg.
    # Per-position SL/target below stay independent (they can exit
    # earlier, never later than this). Replaces the old footgun-prone
    # total_capital_rs 1% block.
    #
    # FAIL-SAFE on exception: a transient risk_gate failure here used to
    # just log and silently leave the position open forever with NO
    # retry-aware visibility. Track consecutive failures per sec_id and
    # escalate loudly — we still can't force a blind exit (no way to know
    # if the cap is actually breached without the check succeeding), but
    # the operator now finds out the breaker is blind instead of trusting
    # a guardrail that's quietly stopped working.
    try:
        import risk_gate
        _unrl = (ltp - entry_px) * p["qty"] if p["entry"] == "BUY" else (entry_px - ltp) * p["qty"]
        _breached, _why = risk_gate.daily_loss_breached(
            p.get("strategy") or "", unrealized=_unrl,
            mode=p.get("mode"), broker=p.get("broker"))
        _rms_fail_streak.pop(sec_id, None)
        if _breached:
            _do_squareoff(p, ltp, f"RMS_MAXLOSS:{_why}", sec_id, seg)
            return
        # ── Daily profit target hit → squareoff + block further entries ──
        _pt_hit, _pt_why = risk_gate.daily_profit_target_hit(
            p.get("strategy") or "", unrealized=_unrl)
        if _pt_hit:
            _do_squareoff(p, ltp, f"RMS_PROFIT_TARGET:{_pt_why}", sec_id, seg)
            return
        _rms_fail_streak.pop(sec_id, None)
    except Exception as _e:
        streak = _rms_fail_streak.get(sec_id, 0) + 1
        _rms_fail_streak[sec_id] = streak
        level = "⚠️ CRITICAL —" if streak >= _LTP_MISS_ALERT_AFTER else ""
        print(f"[pos_monitor] {level} RMS daily-loss check failed for {p.get('sym')} "
              f"(strategy={p.get('strategy')}, {streak}x consecutive) — leaving position "
              f"open, will retry: {_e}", flush=True)

    sl_px_generic = _generic_px(sl_type, sl_val, True) if sl_type else None
    tp_px_generic = _generic_px(tp_type, tp_val, False) if tp_type else None
    if sl_type == "candle_close":
        sl_px_generic = _candle_close_px(sl_val)
    if tp_type == "candle_close":
        tp_px_generic = _candle_close_px(tp_val)
    # "INDEX_HIT"/"CANDLE_HIT" are sentinels meaning the trigger already
    # fired — short-circuit straight to exit below.
    if sl_px_generic in ("INDEX_HIT", "CANDLE_HIT") or tp_px_generic in ("INDEX_HIT", "CANDLE_HIT"):
        hit_sl = sl_px_generic in ("INDEX_HIT", "CANDLE_HIT")
        kind = "index_level" if (sl_px_generic == "INDEX_HIT" or tp_px_generic == "INDEX_HIT") else "candle_close"
        reason = f"SL_HIT:{kind}" if hit_sl else f"TP_HIT:{kind}"
        _do_squareoff(p, ltp, reason, sec_id, seg)
        return
    # numeric generic SL/TP trigger price (None if no generic tag set)
    sl_px_num = sl_px_generic if isinstance(sl_px_generic, float) else None
    tp_px_num = tp_px_generic if isinstance(tp_px_generic, float) else None
    if sl_px_num is not None:
        hit = ltp <= sl_px_num if p["entry"] == "BUY" else ltp >= sl_px_num
        if hit:
            _do_squareoff(p, ltp, f"SL_HIT:{sl_type}:{sl_val}", sec_id, seg)
            return
    if tp_px_num is not None:
        hit = ltp >= tp_px_num if p["entry"] == "BUY" else ltp <= tp_px_num
        if hit:
            _do_squareoff(p, ltp, f"TP_HIT:{tp_type}:{tp_val}", sec_id, seg)
            return
    if sl_px_num is not None and tp_px_num is not None:
        return  # generic SL+TP both set and neither hit — skip legacy fallback entirely

    # Legacy SL_PCT/TP_PCT — ONLY from tags explicitly set on THIS position
    # (e.g. an older position created before the SL_TYPE/SL_VAL modal existed).
    # Do NOT fall back to RMS's global/per-strategy max_loss_pct/max_loss_rs
    # here — those are CUMULATIVE/total-capital daily-loss-cap fields (the
    # "Global Max Loss %" RMS Risk-tab field, e.g. "1" meaning 1% of capital),
    # already correctly enforced a few lines above via risk_gate.daily_loss_
    # breached(). Reusing the same number as a PER-POSITION % of the OPTION
    # PREMIUM here was a unit mismatch — 1% of an ~₹80 premium is ~₹0.80,
    # so any untagged position (e.g. a manual/Quick-Order test trade) got
    # force-closed within seconds of entry on pure noise, with no per-position
    # SL ever actually configured. Found 2026-06-29 (first live Kite test
    # order squared off in ~20s). A position with no explicit SL tag and no
    # entry-time default_sl_rs stamp simply gets no automatic SL here now —
    # exactly matching "max loss % is a total-capital cap, not a premium SL".
    sl_pct = next((float(t.split(":")[1]) for t in tags if t.startswith("SL_PCT:")), None) if sl_px_num is None else None
    tp_pct = next((float(t.split(":")[1]) for t in tags if t.startswith("TP_PCT:")), None) if tp_px_num is None else None
    sl_rs  = None  # ₹ max-loss for this position (qty already applied) — explicit SL_RS tag only

    sl_px_pct = None
    sl_px_rs  = None
    tp_px = None

    if p["entry"] == "BUY":
        if sl_pct is not None: sl_px_pct = entry_px * (1 - (sl_pct / 100.0))
        if sl_rs  is not None: sl_px_rs  = entry_px - (sl_rs / p["qty"])
        if tp_pct is not None: tp_px = entry_px * (1 + (tp_pct / 100.0))
    else: # SELL
        if sl_pct is not None: sl_px_pct = entry_px * (1 + (sl_pct / 100.0))
        if sl_rs  is not None: sl_px_rs  = entry_px + (sl_rs / p["qty"])
        if tp_pct is not None: tp_px = entry_px * (1 - (tp_pct / 100.0))

    # Tighter of % / ₹ wins (whichever is hit first / closer to entry).
    if p["entry"] == "BUY":
        sl_px = max([v for v in (sl_px_pct, sl_px_rs) if v is not None], default=None)
    else:
        sl_px = min([v for v in (sl_px_pct, sl_px_rs) if v is not None], default=None)

    exit_reason = None
    if p["entry"] == "BUY":
        if sl_px and ltp <= sl_px: exit_reason = f"SL_HIT:{sl_pct if sl_px==sl_px_pct else None}%/₹{sl_rs if sl_px==sl_px_rs else ''}"
        elif tp_px and ltp >= tp_px: exit_reason = f"TP_HIT:{tp_pct}%"
    else: # SELL
        if sl_px and ltp >= sl_px: exit_reason = f"SL_HIT:{sl_pct if sl_px==sl_px_pct else None}%/₹{sl_rs if sl_px==sl_px_rs else ''}"
        elif tp_px and ltp <= tp_px: exit_reason = f"TP_HIT:{tp_pct}%"

    if exit_reason:
        _do_squareoff(p, ltp, exit_reason, sec_id, seg)


if __name__ == '__main__':
    # auto_scheduler / webhook_monitor_loop / pos_monitor_loop ab is process ke
    # andar NAHI chalte — woh `monitor_daemon.py` mein, apni alag systemd service
    # (algo-monitor) ke roop mein chalte hain. Wajah: pehle yeh dashboard ke hi
    # background threads the, isliye `systemctl restart algo-dashboard` (UI fix
    # deploy karte waqt) unhe bhi 2-3 sec pause kar deta tha — SL/TP/EOD-squareoff
    # aur webhook trailing-SL us window mein miss ho sakte the. Ab dashboard
    # restart in loops ko bilkul touch nahi karta.
    print("\n🤖 Algo Trader Dashboard")
    print("   Open: http://72.61.173.32:5099\n")
    print("   (SL/TP/webhook-monitor/scheduler ab monitor_daemon.py mein — alag se chal rahe honge)\n")
    app.run(host='0.0.0.0', port=5099, debug=False)
