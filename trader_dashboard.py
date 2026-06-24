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
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, render_template, request, Response, send_from_directory
import time as _time
import threading as _threading

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
        out = subprocess.check_output(['pgrep', '-f', '--', pat], text=True).strip()
        if out:
            return int(out.split('\n')[0]), ""
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

    ist_now_ = datetime.utcnow() + timedelta(hours=5, minutes=30)
    data = order_store.trades_for(ist_now_.strftime("%Y-%m-%d"))
    open_pos = data.get("open", [])

    rc = risk_gate._risk_cfg()
    glob = rc.get("global", {})

    def _eff(strat, key):
        sv = (rc.get("per_strategy", {}).get(strat, {}) or {}).get(key)
        return sv if sv is not None else glob.get(key)

    def _unrealized(positions):
        total, n_priced = 0.0, 0
        for p in positions:
            sec_id = p.get("sec_id")
            if not sec_id:
                continue
            try:
                _feed_subscribe([(p.get("segment") or "NSE_FNO", sec_id)])
                q = dhan_feed.get_quote(sec_id)
                ltp = float(q.get("ltp") or 0) if q else 0.0
            except Exception:
                ltp = 0.0
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
        rows.append({
            "strategy": sid_, "capital_used": round(cap_used, 2),
            "capital_cap": cap_cap, "open_positions": total_n, "priced": priced,
            "unrealized_pnl": round(unreal, 2) if priced else None,
            "max_loss_rs": max_loss_rs,
            "max_loss_pct_used": round(abs(unreal) / max_loss_rs * 100, 1)
                if (max_loss_rs and unreal < 0) else None,
        })

    glob_open = [p for p in open_pos if not any(
        t == "CAPITAL_BLOCKED" for t in (p.get("tags") or []))]
    glob_unreal, glob_priced, glob_n = _unrealized(glob_open)
    totals = {
        "capital_used": round(risk_gate.capital_in_use(None), 2),
        "capital_cap": glob.get("capital_rs"),
        "open_positions": glob_n, "priced": glob_priced,
        "unrealized_pnl": round(glob_unreal, 2) if glob_priced else None,
    }
    return jsonify({"strategies": rows, "totals": totals})

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
        _sys.path.insert(0, str(BASE_DIR / "brokers"))
        import kite_broker
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
        _sys.path.insert(0, str(BASE_DIR / "brokers"))
        _sys.path.insert(0, str(BASE_DIR))
        import kite_broker, importlib; importlib.reload(kite_broker)
        import dhan_master
        kite = kite_broker._load_kite()

        # NIFTY spot price se ATM strike nikalo
        import json, requests
        cfg = json.loads((BASE_DIR / "data" / "config.json").read_text())
        headers = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"], "Content-Type": "application/json"}
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
        token, cid = _creds()
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
_LTP_CACHE_TTL = 30  # seconds — was 3 (caused Dhan 429: 2 dashboards + live
                     # strategies share one token). 30s = far fewer LTP calls,
                     # fine for a preview (esp. market-closed last price).

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
        import dhan_master, range_trader, requests as _req
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}

        _idx_sec = {"NIFTY": "13", "BANKNIFTY": "25"}
        _idx_id  = _idx_sec.get(symbol, "13")
        _qr_idx  = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                             json={"IDX_I": [int(_idx_id)]}, headers=headers, timeout=5)
        if _qr_idx.status_code != 200:
            # index call rate-limited/failed — show last good value instead of erroring
            if cached:
                return jsonify({**cached['data'], '_stale': True})
            return jsonify({"ok": False, "msg": "LTP busy (Dhan rate limit) — thodi der me"})
        idx_price = float(_qr_idx.json()["data"]["IDX_I"][_idx_id]["last_price"])

        sec_ce, t_ce, _ = dhan_master.get_option_contract(symbol, idx_price, "CE", offset)
        sec_pe, t_pe, _ = dhan_master.get_option_contract(symbol, idx_price, "PE", offset)

        ltp_ce = ltp_pe = None
        sec_ids = [int(s) for s in [sec_ce, sec_pe] if s]
        if sec_ids:
            _t.sleep(1.1)   # Dhan marketfeed ~1 req/sec — space the 2nd call from the index call
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
        (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
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
            
            end_dt = _dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)
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
                r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
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
                tags=tags_list
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

        def _record(status_, m, oid=''):
            try:
                import order_store
                order_store.record(side, qty_shares, option_ltp, source='manual', mode=m,
                    broker='dhan', symbol=symbol, instrument='options', trad_sym=t_sym,
                    sec_id=sec_id, segment='NSE_FNO', broker_order_id=oid,
                    correlation_id=f'MANUAL_{symbol}_{ts}', status=status_)
            except Exception:
                pass

        if mode == 'paper':
            _write_to_log('PAPER')
            _record('paper', 'paper')
            return jsonify({'ok': True, 'msg': f'[PAPER] {side} {lots}L ({qty_shares} qty) {t_sym} @ {option_ltp:.2f}'})

        hdrs_dict = range_trader.hdrs(token, cid)
        r = _req.post('https://api.dhan.co/v2/orders', json=body, headers=hdrs_dict, timeout=10)
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

@app.route('/api/close-position', methods=['POST'])
def api_close_position():
    """Close an open position — place opposite order using exact trading symbol."""
    data     = request.get_json()
    t_sym    = data.get('t_sym', '')        # e.g. NIFTY-Jun2026-24100-CE
    entry_side = data.get('entry_side', '') # BUY or SELL
    qty_shares = int(data.get('qty', 65))
    mode     = data.get('mode', 'paper')
    # source/strategy of the OPEN leg — close ko isi (source,strategy,trad_sym) se
    # record karo taaki order_store.trades_for me net hoke completed ban jaaye.
    src_in   = data.get('source', '') or 'manual'
    strat_in = data.get('strategy', '') or ''

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
            return jsonify({'ok': False, 'msg': f'{t_sym} ka LTP nahi mila (Dhan rate-limit/expired) — close record NAHI kiya. Dobara try karo, ya phantom ho to 🗑 book-close use karo.'})

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
                    correlation_id=f'CLOSE_{t_sym}_{ts}', status=status_)
            except Exception:
                pass

        if mode == 'paper':
            _write_log('PAPER')
            _record_close('paper', 'paper')
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
            ok_fill, ostatus, _oid = _dhan_live_fate(r, token, cid)
            if not ok_fill:
                return jsonify({'ok': False, 'msg': f'Dhan ne close order {ostatus} kiya — position band nahi hui (Dhan pe verify karo)'})
            _write_log('LIVE')
            _record_close('filled' if ostatus == 'TRADED' else 'pending', 'live', _oid)
            return jsonify({'ok': True, 'msg': f'[LIVE] CLOSE {close_side} {qty_shares} {t_sym} ({ostatus})'})
        else:
            return jsonify({'ok': False, 'msg': f'Dhan {r.status_code}: {r.text[:200]}'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


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
        _qr_dbg   = req.post("https://api.dhan.co/v2/marketfeed/ltp",
                             json={"IDX_I": [13]}, headers=_hdrs_dbg, timeout=5)
        price = float(_qr_dbg.json()["data"]["IDX_I"]["13"]["last_price"])
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
    from datetime import datetime, timezone, timedelta
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
    from datetime import datetime, timezone, timedelta
    ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    date = request.args.get('date') or ist.strftime('%Y-%m-%d')
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    data = order_store.trades_for(date, **filt)
    data['date'] = date
    data['filters'] = {f: order_store.distinct(f, date)
                       for f in ('source', 'mode', 'strategy', 'broker')}
    try:
        import risk_gate
        for p in data.get('open', []):
            if (p.get('tags') or []) and 'CAPITAL_BLOCKED' in p['tags']:
                continue
            p['margin_used'] = round(risk_gate._leg_capital(p) or 0, 2)
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

def pos_monitor_loop():
    """Monitors open positions for SL_PCT, TP_PCT hits and tracks MAX/MIN LTP."""
    import time
    import order_store
    import dhan_feed
    from datetime import timedelta

    while True:
        try:
            _ensure_feed_started()
            # 'datetime' is the CLASS (from datetime import datetime) — datetime.datetime
            # galat tha, har loop crash karta tha (SL/TP monitor band pada tha).
            ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
            
            data = order_store.trades_for(ist_now.strftime('%Y-%m-%d'))
            open_pos = data.get("open", [])
            
            for p in open_pos:
                if not p.get("tags") and not p.get("sec_id"): continue
                sec_id = p.get("sec_id")
                if not sec_id: continue
                
                tags = p.get("tags") or []
                
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
                if ltp <= 0: continue
                
                # Update MAX/MIN LTP for Run-up / Run-down tracking
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

                if p["qty"] <= 0 or entry_px <= 0: continue

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

                def _do_squareoff(p, ltp, exit_reason, sec_id, seg):
                    """Exit a position now — live round-trips a real broker order
                    first (never marks closed unless the broker confirms), paper
                    just records the fill. Returns True once handled."""
                    print(f"[{exit_reason}] {p['sym']} LTP {ltp}. Squaring off...")
                    exit_side = "SELL" if p["entry"] == "BUY" else "BUY"
                    if p.get("mode") == "live":
                        import smart_order
                        from brokers import get_broker
                        broker = get_broker(p.get("broker") or "dhan")
                        res = smart_order.execute(
                            exit_side, p["sym"], sec_id, seg, p["qty"], p["sym"],
                            p["mode"], broker, log=print, tag="POSMON",
                            source=p["source"], strategy=p["strategy"],
                            instrument=p["instrument"], broker_name=p.get("broker") or "dhan",
                        )
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
                    return True

                # ── Blanket 3:15 PM EOD squareoff — this is a positional/intraday
                # system, no option position should carry overnight regardless of
                # which strategy/source opened it. Takes priority over SL/TP so a
                # position with no SL/TP tags set still gets closed at EOD.
                if (ist_now.hour > 15 or (ist_now.hour == 15 and ist_now.minute >= 15)) \
                   and (p.get("instrument") or "").upper() != "EQUITY":
                    _do_squareoff(p, ltp, "EOD_315_SQUAREOFF", sec_id, seg)
                    continue

                sl_px_generic = _generic_px(sl_type, sl_val, True) if sl_type else None
                tp_px_generic = _generic_px(tp_type, tp_val, False) if tp_type else None
                # "INDEX_HIT" is a sentinel meaning the underlying index/equity level
                # was already breached — short-circuit straight to exit below.
                if sl_px_generic == "INDEX_HIT" or tp_px_generic == "INDEX_HIT":
                    reason = "SL_HIT:index_level" if sl_px_generic == "INDEX_HIT" else "TP_HIT:index_level"
                    _do_squareoff(p, ltp, reason, sec_id, seg)
                    continue
                # numeric generic SL/TP trigger price (None if no generic tag set)
                sl_px_num = sl_px_generic if isinstance(sl_px_generic, float) else None
                tp_px_num = tp_px_generic if isinstance(tp_px_generic, float) else None
                if sl_px_num is not None:
                    hit = ltp <= sl_px_num if p["entry"] == "BUY" else ltp >= sl_px_num
                    if hit:
                        _do_squareoff(p, ltp, f"SL_HIT:{sl_type}:{sl_val}", sec_id, seg)
                        continue
                if tp_px_num is not None:
                    hit = ltp >= tp_px_num if p["entry"] == "BUY" else ltp <= tp_px_num
                    if hit:
                        _do_squareoff(p, ltp, f"TP_HIT:{tp_type}:{tp_val}", sec_id, seg)
                        continue
                if sl_px_num is not None and tp_px_num is not None:
                    continue  # generic SL+TP both set and neither hit — skip legacy fallback entirely

                # Legacy SL_PCT/TP_PCT (kept for older positions / strategy defaults).
                sl_pct = next((float(t.split(":")[1]) for t in tags if t.startswith("SL_PCT:")), None) if sl_px_num is None else None
                tp_pct = next((float(t.split(":")[1]) for t in tags if t.startswith("TP_PCT:")), None) if tp_px_num is None else None
                sl_rs  = None  # ₹ max-loss for this position (qty already applied)

                if sl_pct is None and sl_px_num is None:
                    rc = _risk_config()
                    strat_risk = rc.get("per_strategy", {}).get(p.get("strategy") or "", {})
                    glob_risk  = rc.get("global", {})
                    eff_pct = strat_risk.get("max_loss_pct") if strat_risk.get("max_loss_pct") is not None else glob_risk.get("max_loss_pct")
                    eff_rs  = strat_risk.get("max_loss_rs")  if strat_risk.get("max_loss_rs")  is not None else glob_risk.get("max_loss_rs")
                    if eff_pct is not None:
                        try: sl_pct = float(eff_pct)
                        except Exception: pass
                    if eff_rs is not None:
                        try: sl_rs = float(eff_rs)
                        except Exception: pass

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
        except Exception as e:
            print("Pos monitor error:", e)
            
        time.sleep(5)



if __name__ == '__main__':
    threading.Thread(target=auto_scheduler, daemon=True).start()
    threading.Thread(target=webhook_monitor_loop, daemon=True).start()
    threading.Thread(target=pos_monitor_loop, daemon=True).start()

    print("\n🤖 Algo Trader Dashboard")
    print("   Open: http://72.61.173.32:5099\n")
    app.run(host='0.0.0.0', port=5099, debug=False)
