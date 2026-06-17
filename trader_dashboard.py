#!/usr/bin/env python3
"""
trader_dashboard.py — Web UI for Algo Trader
Run: python trader_dashboard.py
Open: http://72.61.173.32:5099
"""

import json
import os
import re
import subprocess
import signal
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template, request

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

def get_pid(strategy="ema"):
    grep = STRATEGIES[strategy]["grep"]
    try:
        out = subprocess.check_output(['pgrep', '-f', grep], text=True).strip()
        return int(out.split('\n')[0]) if out else None
    except Exception:
        return None

def get_mode(strategy="ema"):
    grep = STRATEGIES[strategy]["grep"]
    try:
        out = subprocess.check_output(['ps', 'aux'], text=True)
        for line in out.splitlines():
            if grep in line:
                return 'live' if '--live' in line else 'paper'
    except Exception:
        pass
    return 'paper'

def parse_pnl(log_path, today, qty=1):
    try:
        lines = [l for l in Path(log_path).read_text().splitlines() if l.startswith(today)]
    except Exception:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_pnl": 0, "details": []}

    signals = {}
    for line in lines:
        m = re.search(r'\[RSI\]\s+(\w+)\s+close=([\d.]+)\s+RSI=[\d.]+\s+signal=(BUY|SELL)', line)
        if not m:
            m = re.search(r'  (\w+)\s+close=([\d.]+)\s+signal=(BUY|SELL)', line)
        if m:
            sym, price, sig = m.group(1), float(m.group(2)), m.group(3)
            signals.setdefault(sym, []).append((sig, price))

    details, total_pnl, wins, losses = [], 0, 0, 0
    for sym, entries in sorted(signals.items()):
        for i in range(len(entries) - 1):
            a, b = entries[i], entries[i+1]
            if   a[0]=='BUY'  and b[0]=='SELL': p = (b[1]-a[1])*qty
            elif a[0]=='SELL' and b[0]=='BUY':  p = (a[1]-b[1])*qty
            else: continue
            total_pnl += p
            wins   += 1 if p > 0 else 0
            losses += 0 if p > 0 else 1
            details.append({"sym": sym, "entry": a[0], "entry_price": a[1],
                            "exit": b[0], "exit_price": b[1], "pnl": round(p, 2)})
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
    lf = STRATEGIES.get(s, STRATEGIES['ema'])['log']
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
                        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
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
                        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
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
