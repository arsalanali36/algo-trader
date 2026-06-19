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
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template, request, Response
import time as _time
import threading as _threading

# IPv4 force — Dhan rejects IPv6 (DH-905). Must be here, not just in range_trader.
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR      = Path(__file__).resolve().parent
TC_FILE       = BASE_DIR / "nifty_config.json"
LOG_FILE      = BASE_DIR / "nifty_trader.log"
RESULTS_DIR   = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)
PYTHON        = str(BASE_DIR / "venv" / "bin" / "python")
TRADER_SCRIPT = str(BASE_DIR / "nifty_ema_trader.py")

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
            import dhan_feed, range_trader
            token, cid = range_trader.load_creds()
            from dhanhq import DhanContext
            ctx = DhanContext(cid, token)
            dhan_feed.start(ctx, [])   # start with empty list; instruments added dynamically
            _feed_started = True
        except Exception as e:
            pass  # no creds yet or import error — will retry next call

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

RSI_SCRIPT   = str(BASE_DIR / "rsi_trader.py")
RSI_LOG      = BASE_DIR / "rsi_trader.log"
RSI_CFG      = BASE_DIR / "rsi_config.json"
RANGE_SCRIPT = str(BASE_DIR / "range_trader.py")
RANGE_LOG    = BASE_DIR / "range_trader.log"
RANGE_CFG    = BASE_DIR / "range_config.json"
UNIV_SCRIPT  = str(BASE_DIR / "universe_trader.py")
UNIV_LOG     = BASE_DIR / "universe_trader.log"
CONFIG_FILE  = BASE_DIR / "data" / "config.json"

STRATEGIES = {
    "ema":      {"script": TRADER_SCRIPT, "log": LOG_FILE,  "cfg": TC_FILE,   "grep": "nifty_ema_trader"},
    "rsi":      {"script": RSI_SCRIPT,    "log": RSI_LOG,   "cfg": RSI_CFG,   "grep": "rsi_trader"},
    "range":    {"script": RANGE_SCRIPT,  "log": RANGE_LOG, "cfg": RANGE_CFG, "grep": "range_trader"},
    "universe": {"script": UNIV_SCRIPT,   "log": UNIV_LOG,  "cfg": TC_FILE,   "grep": "universe_trader"},
}
# Aliases — custom variation names map to base strategy
STRATEGY_ALIASES = {"ARS": "range"}

def _base(strategy):
    first = strategy.split('_')[0] if '_' in strategy else strategy
    return STRATEGY_ALIASES.get(first, first)

def get_pid(strategy="ema"):
    grep = STRATEGIES[_base(strategy)]["grep"]
    try:
        out = subprocess.check_output(['pgrep', '-f', grep], text=True).strip()
        return int(out.split('\n')[0]) if out else None
    except Exception:
        return None

def get_mode(strategy="ema"):
    grep = STRATEGIES[_base(strategy)]["grep"]
    try:
        out = subprocess.check_output(['ps', 'aux'], text=True)
        for line in out.splitlines():
            if grep in line:
                return 'live' if '--live' in line else 'paper'
    except Exception:
        pass
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
    return send_file(BASE_DIR / 'backtest_dashboard.html')

@app.route('/api/status')
def api_status():
    st = {}
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        for s in cfg.keys():
            pid = get_pid(s)
            if pid:
                st[s] = pid
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

@app.route('/api/start', methods=['POST'])
def api_start():
    s    = request.args.get('s', 'ema_v1')
    mode = request.args.get('mode', 'paper')
    base_s = _base(s)
    st   = STRATEGIES.get(base_s, STRATEGIES['ema'])
    pid  = get_pid(s)
    if pid:
        return jsonify({"msg": f"{s.upper()} already running (PID {pid})"})
    flag = '--live' if mode == 'live' else '--paper'
    log_file = BASE_DIR / 'logs' / f"{s}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lf   = open(log_file, 'a')
    subprocess.Popen([PYTHON, st['script'], flag, '--id', s],
                     stdout=lf, stderr=lf,
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
    pid = get_pid(s)
    if not pid:
        return jsonify({"msg": f"{s.upper()} not running"})
    try:
        os.kill(pid, signal.SIGTERM)
        # Mark inactive so auto-scheduler won't restart it tomorrow
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
    """Returns {sym: sec_id}. Handles options (via dhan_master) + equity/index (via _EQ_IDX_SEC)."""
    import dhan_master
    out = {}
    for s in syms:
        if s in _sec_id_cache:
            out[s] = _sec_id_cache[s]
            continue
        # Equity/index lookup first
        if s in _EQ_IDX_SEC:
            sid = _EQ_IDX_SEC[s][0]
            _sec_id_cache[s] = sid
            out[s] = sid
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
        if ltp_map:
            return jsonify({"ok": True, "ltp_map": ltp_map, "src": "ws"})
    except Exception:
        pass

    # Fallback: Dhan REST API
    try:
        import range_trader, requests as _req
        token, cid = range_trader.load_creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        sec_id_map = _get_sec_ids(syms)
        if sec_id_map:
            # Group by segment for REST call
            seg_groups = {}
            for s, sid in sec_id_map.items():
                seg = _get_seg(s)
                seg_groups.setdefault(seg, []).append((s, sid))
            body = {}
            for seg, pairs in seg_groups.items():
                dhan_seg = {"NSE_EQ": "NSE_EQ", "IDX_I": "IDX_I", "NSE_FNO": "NSE_FNO"}.get(seg, "NSE_FNO")
                body[dhan_seg] = [int(sid) for _, sid in pairs]
            r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
            if r.status_code == 200:
                id_to_sym = {v: k for k, v in sec_id_map.items()}
                for seg_key, quotes in (r.json().get("data", {}) or {}).items():
                    if not isinstance(quotes, dict): continue
                    for sec_id_str, q in quotes.items():
                        sym = id_to_sym.get(str(sec_id_str)) or id_to_sym.get(str(sec_id_str).lstrip('0'))
                        if not sym: continue
                        ltp = float(q.get("last_price") or q.get("ltp") or 0)
                        if ltp: ltp_map[sym] = {"ltp": ltp, "qty": None}
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
_LTP_CACHE_TTL = 3  # seconds — match widget refresh interval

@app.route('/api/option-ltp')
def api_option_ltp():
    """CE/PE LTP for Quick Order widget. Cached 15s to avoid Dhan 429."""
    import time as _t
    symbol = request.args.get('symbol', 'NIFTY')
    offset = int(request.args.get('offset', 0))
    cache_key = (symbol, offset)

    # Return cached if fresh
    cached = _ltp_cache.get(cache_key)
    if cached and (_t.time() - cached['ts']) < _LTP_CACHE_TTL:
        return jsonify(cached['data'])

    try:
        import dhan_master, range_trader, requests as _req, yfinance as yf
        token, cid = range_trader.load_creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}

        ticker_map = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}
        idx_price = float(yf.Ticker(ticker_map.get(symbol, "^NSEI")).fast_info["last_price"])

        sec_ce, t_ce, _ = dhan_master.get_option_contract(symbol, idx_price, "CE", offset)
        sec_pe, t_pe, _ = dhan_master.get_option_contract(symbol, idx_price, "PE", offset)

        ltp_ce = ltp_pe = None
        sec_ids = [int(s) for s in [sec_ce, sec_pe] if s]
        if sec_ids:
            qr = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                           json={"NSE_FNO": sec_ids}, headers=headers, timeout=5)
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
                return jsonify({"ok": False, "msg": "Rate limit (429) — retry in 15s"})

        result = {"ok": True, "ce_sym": t_ce, "ce_ltp": ltp_ce, "pe_sym": t_pe, "pe_ltp": ltp_pe}
        _ltp_cache[cache_key] = {"data": result, "ts": _t.time()}
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/manual-order', methods=['POST'])
def api_manual_order():
    data   = request.get_json()
    symbol = data.get('symbol', 'NIFTY')
    side   = data.get('side', 'BUY')
    lots   = int(data.get('lots', 1))
    offset = int(data.get('strike_offset', 0))
    mode   = data.get('mode', 'paper')
    order_type = (data.get('order_type', 'MARKET') or 'MARKET').upper()
    limit_price = data.get('price')   # user-entered LIMIT price (₹), may be None
    try:
        import dhan_master
        import range_trader
        import yfinance as yf

        token, cid = range_trader.load_creds()
        opt_type = 'PE' if side == 'BUY' else 'CE'

        # ATM price from yfinance
        ticker_map = {'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK'}
        tk = yf.Ticker(ticker_map.get(symbol, '^NSEI'))
        price = float(tk.fast_info['last_price'])

        # Option contract lookup
        sec_id, t_sym, lot_sz_master = dhan_master.get_option_contract(symbol, price, opt_type, offset)
        if not sec_id:
            return jsonify({'ok': False, 'msg': f'Contract not found: {symbol} {opt_type} offset={offset}'})

        # Lot size — from dhan_master cache (already parsed correctly)
        lot_size = lot_sz_master if lot_sz_master else 65

        qty_shares = lots * lot_size   # e.g. 1 lot × 65 = 65 shares

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
            'price':           order_price,
            'triggerPrice':    0,
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

        if mode == 'paper':
            _write_to_log('PAPER')
            return jsonify({'ok': True, 'msg': f'[PAPER] {side} {lots}L ({qty_shares} qty) {t_sym} @ {option_ltp:.2f}'})

        hdrs_dict = range_trader.hdrs(token, cid)
        r = _req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs_dict, timeout=10)
        print(f"[MANUAL ORDER] status={r.status_code} resp={r.text}", flush=True)
        if r.status_code == 200:
            _write_to_log('LIVE')
            return jsonify({'ok': True, 'msg': f'[LIVE] {order_type} {side} {lots}L ({qty_shares} qty) {t_sym} @ {option_ltp:.2f}'})
        else:
            return jsonify({'ok': False, 'msg': f'Dhan {r.status_code}: {r.text[:300]}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})

@app.route('/api/close-position', methods=['POST'])
def api_close_position():
    """Close an open position — place opposite order using exact trading symbol."""
    data     = request.get_json()
    t_sym    = data.get('t_sym', '')        # e.g. NIFTY-Jun2026-24100-CE
    entry_side = data.get('entry_side', '') # BUY or SELL
    qty_shares = int(data.get('qty', 65))
    mode     = data.get('mode', 'paper')

    close_side = 'SELL' if entry_side == 'BUY' else 'BUY'

    try:
        import range_trader, requests as _req, time as _time
        token, cid = range_trader.load_creds()

        # Security ID from scrip master
        sec_id = _get_sec_ids([t_sym]).get(t_sym, '')

        # Get current LTP for log
        option_ltp = 0.0
        if sec_id:
            try:
                qh = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
                qr = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                               json={"NSE_FNO": [int(sec_id)]}, headers=qh, timeout=4)
                if qr.status_code == 200:
                    qdata = qr.json().get("data", {}).get("NSE_FNO", {})
                    for v in (qdata.values() if isinstance(qdata, dict) else []):
                        ltp_v = float(v.get("last_price") or v.get("ltp") or 0)
                        if ltp_v:
                            option_ltp = ltp_v
                            break
            except Exception:
                pass

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

        if mode == 'paper':
            _write_log('PAPER')
            return jsonify({'ok': True, 'msg': f'[PAPER] CLOSE {close_side} {qty_shares} {t_sym} @ {option_ltp:.2f}'})

        if not sec_id:
            return jsonify({'ok': False, 'msg': f'Security ID not found for {t_sym}'})

        body = {
            'dhanClientId': cid, 'correlationId': f'CLOSE_{t_sym}_{ts}',
            'transactionType': close_side, 'exchangeSegment': 'NSE_FNO',
            'productType': 'INTRADAY', 'orderType': 'MARKET', 'validity': 'DAY',
            'securityId': sec_id, 'tradingSymbol': t_sym,
            'quantity': qty_shares, 'price': 0, 'triggerPrice': 0,
        }
        hdrs = range_trader.hdrs(token, cid)
        r = _req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs, timeout=10)
        if r.status_code == 200:
            _write_log('LIVE')
            return jsonify({'ok': True, 'msg': f'[LIVE] CLOSE {close_side} {qty_shares} {t_sym}'})
        else:
            return jsonify({'ok': False, 'msg': f'Dhan {r.status_code}: {r.text[:200]}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/api/debug-order')
def api_debug_order():
    """Test Dhan API call directly from Flask process — diagnose DH-905"""
    try:
        import range_trader, requests as req, yfinance as yf, socket as sk
        # confirm IPv4 patch is active
        ipv4_active = sk.getaddrinfo.__name__ == '_v4'
        token, cid = range_trader.load_creds()
        tk = yf.Ticker('^NSEI')
        price = float(tk.fast_info['last_price'])
        body = {
            'dhanClientId': cid, 'correlationId': 'DEBUG_001',
            'transactionType': 'SELL', 'exchangeSegment': 'NSE_FNO',
            'productType': 'INTRADAY', 'orderType': 'MARKET', 'validity': 'DAY',
            'securityId': '56376', 'tradingSymbol': 'NIFTY-Jun2026-24100-CE',
            'quantity': 65, 'price': 0, 'triggerPrice': 0,
        }
        hdrs = range_trader.hdrs(token, cid)
        r = req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs, timeout=10)
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
    m = re.search(r'strategy\s*\(\s*"([^"]+)"', code)
    strat_name = m.group(1) if m else "unknown"
    pine_dir = BASE_DIR / '_PINE'
    pine_dir.mkdir(exist_ok=True)
    ver_file = pine_dir / 'versions.json'
    versions = _json.loads(ver_file.read_text()) if ver_file.exists() else []
    version = len(versions) + 1
    from datetime import datetime, timezone, timedelta
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ts = ist.strftime('%Y-%m-%d %H:%M IST')
    strat_version = sum(1 for v in versions if v.get('name') == strat_name) + 1
    entry = {"version": version, "name": strat_name, "strat_version": strat_version, "timestamp": ts, "desc": desc}
    versions.append(entry)
    ver_file.write_text(_json.dumps(versions, indent=2, ensure_ascii=False))
    (pine_dir / 'range_chain.pine').write_text(code, encoding='utf-8')
    (pine_dir / f'v{version}.pine').write_text(code, encoding='utf-8')
    return jsonify(entry)

@app.route('/api/pine/code/<int:version>')
def api_pine_code(version):
    pine_dir = BASE_DIR / '_PINE'
    # Try version-specific file first, fallback to latest
    vfile = pine_dir / f'v{version}.pine'
    if vfile.exists():
        return vfile.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}
    latest = pine_dir / 'range_chain.pine'
    if latest.exists():
        return latest.read_text(encoding='utf-8'), 200, {'Content-Type': 'text/plain; charset=utf-8'}
    return 'Code not found', 404

@app.route('/api/pine/delete/<int:version>', methods=['DELETE'])
def api_pine_delete(version):
    import json as _json
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify({"ok": False, "error": "No versions file"}), 404
    versions = _json.loads(ver_file.read_text())
    versions = [v for v in versions if v['version'] != version]
    ver_file.write_text(_json.dumps(versions, indent=2, ensure_ascii=False))
    vfile = BASE_DIR / '_PINE' / f'v{version}.pine'
    if vfile.exists():
        vfile.unlink()
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

@app.route('/api/downloader-alerts')
def api_downloader_alerts():
    alert_file = BASE_DIR / "data" / "downloader_alert.json"
    if not alert_file.exists():
        return jsonify([])
    try:
        return jsonify(json.loads(alert_file.read_text()))
    except Exception:
        return jsonify([])


@app.route('/api/save-summary', methods=['POST'])
def api_save_summary():
    try:
        subprocess.run([PYTHON, str(BASE_DIR / 'save_daily_summary.py')], cwd=str(BASE_DIR))
        return jsonify({"msg": "✅ Summary saved to results/"})
    except Exception as e:
        return jsonify({"msg": f"Error: {e}"})


import threading
import requests

def auto_scheduler():
    from datetime import datetime, timezone, timedelta
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
                            if isinstance(cfg[key], dict) and cfg[key].get("active", True):
                                requests.post(f"http://127.0.0.1:5099/api/start?s={key}&mode=paper", timeout=5)
                    except Exception as e:
                        pass
                    has_started_today = True

            if t >= (15, 30):
                if not has_stopped_today:
                    print(f"[{now.strftime('%H:%M:%S')}] Auto-stopping bots...")
                    try:
                        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                        for key in cfg.keys():
                            if isinstance(cfg[key], dict):
                                requests.post(f"http://127.0.0.1:5099/api/stop?s={key}", timeout=5)
                    except Exception as e:
                        pass
                    has_stopped_today = True

        except Exception as e:
            print("Auto Scheduler Error:", e)
        
        time.sleep(30)

if __name__ == '__main__':
    threading.Thread(target=auto_scheduler, daemon=True).start()

    print("\n🤖 Algo Trader Dashboard")
    print("   Open: http://72.61.173.32:5099\n")
    app.run(host='0.0.0.0', port=5099, debug=False)
