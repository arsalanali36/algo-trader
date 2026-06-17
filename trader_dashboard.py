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
from flask import Flask, jsonify, render_template, request

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

# ── HTML ───────────────────────────────────────────────────────────────────────


# ── API Routes ─────────────────────────────────────────────────────────────────

RSI_SCRIPT   = str(BASE_DIR / "rsi_trader.py")
RSI_LOG      = BASE_DIR / "rsi_trader.log"
RSI_CFG      = BASE_DIR / "rsi_config.json"
RANGE_SCRIPT = str(BASE_DIR / "range_trader.py")
RANGE_LOG    = BASE_DIR / "range_trader.log"
RANGE_CFG    = BASE_DIR / "range_config.json"
CONFIG_FILE  = BASE_DIR / "data" / "config.json"

STRATEGIES = {
    "ema":   {"script": TRADER_SCRIPT, "log": LOG_FILE,  "cfg": TC_FILE,   "grep": "nifty_ema_trader"},
    "rsi":   {"script": RSI_SCRIPT,    "log": RSI_LOG,   "cfg": RSI_CFG,   "grep": "rsi_trader"},
    "range": {"script": RANGE_SCRIPT,  "log": RANGE_LOG, "cfg": RANGE_CFG, "grep": "range_trader"},
}

def _base(strategy):
    return strategy.split('_')[0] if '_' in strategy else strategy

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

        # Entry: [PAPER] BUY/SELL 1 SYM @ price  (new format)
        m = re.search(r'\[PAPER\]\s+(BUY|SELL)\s+\d+\s+(\w+)\s+@\s+([\d.]+)', line)
        if m:
            side, sym, price = m.group(1), m.group(2), float(m.group(3))
            open_pos[sym] = {"side": side, "price": price, "time": _ts(line)}
            continue

        # Entry: [PAPER] BUY/SELL 1 SYM  (old format — use last SIGNAL price)
        m = re.search(r'\[PAPER\]\s+(BUY|SELL)\s+\d+\s+(\w+)\s+correlationId', line)
        if m:
            side, sym = m.group(1), m.group(2)
            price = last_signal_price.get(sym, 0.0)
            open_pos[sym] = {"side": side, "price": price, "time": _ts(line)}
            continue

        # Exit: EXIT SYM via REASON @ price
        m = re.search(r'EXIT\s+(\w+)\s+via\s+\S+\s+@\s+([\d.]+)', line)
        if m:
            sym, exit_price = m.group(1), float(m.group(2))
            if sym not in open_pos:
                continue  # stale state exit — no entry this session, skip
            entry = open_pos.pop(sym)
            if entry["side"] == "BUY":
                pnl = (exit_price - entry["price"]) * qty
            else:
                pnl = (entry["price"] - exit_price) * qty
            total_pnl += pnl
            wins   += 1 if pnl > 0 else 0
            losses += 0 if pnl > 0 else 1
            details.append({
                "sym": sym, "entry": entry["side"],
                "entry_price": entry["price"], "entry_time": entry["time"],
                "exit_price": exit_price, "exit_time": _ts(line),
                "pnl": round(pnl, 2)
            })
            continue

        # EMA/RSI fallback
        m = re.search(r'(\w+)\s+close=([\d.]+)\s+signal=(BUY|SELL)', line)
        if m:
            sym, price, side = m.group(1), float(m.group(2)), m.group(3)
            open_pos[sym] = {"side": side, "price": price, "time": _ts(line)}

    n = len(details)
    return {"trades": n, "wins": wins, "losses": losses,
            "win_rate": round(wins/n*100, 1) if n else 0,
            "total_pnl": round(total_pnl, 2), "details": details}

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
    base_s = s.split('_')[0] if '_' in s else s
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
    return jsonify({"msg": f"✅ {s.upper()} started — {mode.upper()} mode"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    s   = request.args.get('s', 'ema')
    pid = get_pid(s)
    if not pid:
        return jsonify({"msg": f"{s.upper()} not running"})
    try:
        os.kill(pid, signal.SIGTERM)
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

@app.route('/api/manual-order', methods=['POST'])
def api_manual_order():
    data   = request.get_json()
    symbol = data.get('symbol', 'NIFTY')
    side   = data.get('side', 'BUY')
    lots   = int(data.get('lots', 1))
    offset = int(data.get('strike_offset', 0))
    mode   = data.get('mode', 'paper')
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
        sec_id, t_sym = dhan_master.get_option_contract(symbol, price, opt_type, offset)
        if not sec_id:
            return jsonify({'ok': False, 'msg': f'Contract not found: {symbol} {opt_type} offset={offset}'})

        # Lot size from scrip master — Dhan needs actual shares, not lots
        lot_size = 65  # fallback
        try:
            import csv
            with open(SCRIP_MASTER, newline='') as f:
                for row in csv.DictReader(f):
                    ts_col = row.get('SEM_TRADING_SYMBOL', '')
                    if ts_col.startswith(symbol) and 'CE' in ts_col:
                        lot_size = int(float(row.get('SEM_LOT_UNITS', 65)))
                        break
        except Exception:
            pass

        qty_shares = lots * lot_size   # e.g. 1 lot × 65 = 65 shares

        # Build body explicitly so we can log it
        import requests as _req
        import time as _time
        ts = int(_time.time())
        body = {
            'dhanClientId':    cid,
            'correlationId':   f'MANUAL_{symbol}_{ts}',
            'transactionType': side,
            'exchangeSegment': 'NSE_FNO',
            'productType':     'INTRADAY',
            'orderType':       'MARKET',
            'validity':        'DAY',
            'securityId':      sec_id,
            'tradingSymbol':   t_sym,
            'quantity':        qty_shares,
            'price':           0,
            'triggerPrice':    0,
        }
        print(f"[MANUAL ORDER] body={body}", flush=True)

        if mode == 'paper':
            print(f"[PAPER] {side} {qty_shares} {t_sym} @ {price:.2f}", flush=True)
            return jsonify({'ok': True, 'msg': f'[PAPER] {side} {lots}L ({qty_shares} qty) {t_sym} @ {price:.0f}'})

        hdrs_dict = range_trader.hdrs(token, cid)
        r = _req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs_dict, timeout=10)
        print(f"[MANUAL ORDER] status={r.status_code} resp={r.text}", flush=True)
        if r.status_code == 200:
            return jsonify({'ok': True, 'msg': f'[LIVE] {side} {lots}L ({qty_shares} qty) {t_sym} @ {price:.0f}'})
        else:
            return jsonify({'ok': False, 'msg': f'Dhan {r.status_code}: {r.text[:300]}'})
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
