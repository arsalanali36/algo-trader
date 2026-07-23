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
from flask import Flask, jsonify, render_template, request, Response, send_from_directory, session, redirect
import time as _time
import threading as _threading
import _paths  # sys.path bootstrap — MUST precede flat imports of moved modules (_core/_data/_ops)
import dhan_rate_limiter as _rl
import notify  # notification centre — errors ki permanent history (dismiss != delete)

# IPv4 force — Dhan rejects IPv6 (DH-905). Must be here, not just in range_trader.
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR      = Path(__file__).resolve().parent
TRADERS_DIR   = BASE_DIR / "strategies" / "live"   # live trader runner scripts (was _TRADERS/ pre 2026-07-09)
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

# ── Static asset caching — versioned + immutable ────────────────────────────────
# Flask serves /static/* with `Cache-Control: no-cache`, so the browser revalidates
# EVERY js/css file on EVERY page load (conditional GET → 304). On a heavy page like
# /stats2 (~13 JS files incl. chart.umd 208KB) over real home→VPS latency that's a
# dozen serialized round-trips before the page even runs — the "slow open" the user
# feels. Backend compute is already <35ms, so this — not a missing data cache — is
# the bottleneck. Fix: stamp every url_for('static') URL with the file's mtime
# (?v=<mtime>) and serve versioned assets `immutable` so the browser uses its DISK
# copy with ZERO network round-trips. A deploy changes the mtime → new URL → fresh
# fetch, so there's no stale-JS risk. Un-versioned raw /static paths (e.g. inline
# <script src> in index.html) keep the old no-cache behaviour — no regression.
@app.url_defaults
def _static_cache_bust(endpoint, values):
    if endpoint == 'static' and values.get('filename'):
        try:
            fp = os.path.join(app.static_folder, values['filename'])
            values['v'] = int(os.path.getmtime(fp))
        except OSError:
            pass

@app.after_request
def _static_immutable(resp):
    try:
        if request.path.startswith('/static/') and request.args.get('v'):
            resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    except Exception:
        pass
    return resp

# ── Login gate ────────────────────────────────────────────────────────────────
# Whole dashboard is behind a single password (public internet + live trading).
# Only /login, static assets, and the TradingView webhook (own token auth) are
# open. Credentials + secret_key live in data/auth.json — set via set_password.py.
import dashboard_auth as _auth
from datetime import timedelta as _timedelta

app.secret_key = _auth.get_secret_key()
app.permanent_session_lifetime = _timedelta(days=30)

# Paths that never require a browser login:
#  - /login (the login page itself)
#  - /static/ (CSS/JS — frontend code, no secrets)
#  - /api/webhook/tv (TradingView POST receiver — authed by its own ?token=)
_AUTH_OPEN_EXACT = {"/login", "/logout"}


def _login_open(path: str) -> bool:
    if path in _AUTH_OPEN_EXACT:
        return True
    if path.startswith("/static/"):
        return True
    if path.startswith("/api/webhook/tv"):
        return True
    return False


# simple brute-force throttle: per-IP failed-attempt tracking (in-memory)
_login_fails = {}   # ip -> [count, first_ts]


# Paths this app's own scheduler thread must reach from the algo-monitor
# process (auto_scheduler's 9:10 start / 15:30 stop) — see _is_internal_call.
_INTERNAL_PATHS = ("/api/start", "/api/stop")


def _is_internal_call() -> bool:
    """True only for auto_scheduler's own loopback call to /api/start|/api/stop,
    proving itself with the shared internal token (dashboard_auth).

    Why this exists: auto_scheduler runs in algo-monitor and drives the bots via
    http://127.0.0.1:5099 — the login gate 401'd every one of those calls and
    the caller's `except: pass` hid it, so the 9:10 auto-start was silently dead.
    The TOKEN is the actual gate (loopback alone is not enough — Caddy proxies
    external traffic from 127.0.0.1 too); the peer check is defence in depth."""
    if request.path not in _INTERNAL_PATHS:
        return False
    if (request.remote_addr or "") not in ("127.0.0.1", "::1"):
        return False
    try:
        import secrets as _s
        sent = request.headers.get("X-Internal-Token", "")
        return bool(sent) and _s.compare_digest(sent, _auth.get_internal_token())
    except Exception:
        return False


@app.before_request
def _require_login():
    p = request.path
    if _login_open(p):
        return None
    if session.get("auth_user"):
        return None
    if _is_internal_call():
        return None
    # not logged in
    if p.startswith("/api/"):
        return jsonify({"error": "auth required", "login": "/login"}), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    configured = _auth.is_configured()
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        rec = _login_fails.get(ip, [0, _time.time()])
        # reset the window after 15 min
        if _time.time() - rec[1] > 900:
            rec = [0, _time.time()]
        if rec[0] >= 8:
            return render_template("login.html", configured=configured,
                                   error="Too many attempts — try again in a few minutes."), 429
        u = request.form.get("username", "")
        pw = request.form.get("password", "")
        if _auth.verify(u, pw):
            session.permanent = True
            session["auth_user"] = _auth.username()
            _login_fails.pop(ip, None)
            return redirect("/")
        rec[0] += 1
        _login_fails[ip] = rec
        _time.sleep(0.6)   # slow down guessing
        err = "Invalid username or password." if configured else "No password set yet."
        return render_template("login.html", configured=configured, error=err), 401
    if session.get("auth_user"):
        return redirect("/")
    return render_template("login.html", configured=configured, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/change-password", methods=["POST"])
def change_password():
    # behind the before_request gate → caller is already logged in
    data = request.get_json(silent=True) or {}
    cur = data.get("current_password", "")
    new = data.get("new_password", "")
    user = session.get("auth_user") or _auth.username()
    if not _auth.verify(user, cur):
        return jsonify(ok=False, error="Current password galat hai"), 403
    if not new or len(new) < 4:
        return jsonify(ok=False, error="Naya password kam se kam 4 characters"), 400
    try:
        _auth.set_credentials(user, new)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    # keep the current session valid (secret_key unchanged) — no re-login needed
    session["auth_user"] = user
    return jsonify(ok=True, msg="Password badal gaya")


# ── Dhan Feed (WebSocket real-time LTP) ───────────────────────────────────────
_feed_started = False
_feed_lock = _threading.Lock()
_sec_to_sym = {}   # sec_id(str) -> sym — populated by api_positions_ltp, used by SSE

def _ensure_feed_started():
    """Start dhan_feed's background thread once credentials are available.

    RE-ENABLED 2026-07-03 (TRAP #89) — was disabled entirely (TRAP #88) after
    algo-dashboard's own connection collided with range_trader.py's for the
    same account's limited WebSocket slots (persistent HTTP 429). dhan_feed.py
    now elects a single cross-process owner (sqlite-based, same pattern as
    dhan_rate_limiter.py) before any process actually opens a connection —
    safe to call from every process again, including when MULTIPLE live
    strategies run simultaneously: whichever one wins the race connects,
    everyone else's LIVE dict just stays empty and falls back to REST
    (`_rest_ltp_fallback()`), exactly as already verified safe in TRAP #88."""
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
RMS_AUDIT_FILE = BASE_DIR / "data" / "rms_audit_log.json"   # Task 13 — RMS value-change history
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
    "rsi":      {"script": RSI_SCRIPT,    "log": RSI_LOG,   "cfg": RSI_CFG,   "grep": "01_rsi_v1"},
    "rsi_v1":   {"script": RSI_SCRIPT,    "log": BASE_DIR / "logs/rsi_v1.log", "cfg": TC_FILE, "grep": "01_rsi_v1"},
    "range":    {"script": RANGE_SCRIPT,  "log": RANGE_LOG, "cfg": RANGE_CFG, "grep": "range_trader"},
    "universe": {"script": UNIV_SCRIPT,   "log": UNIV_LOG,  "cfg": TC_FILE,   "grep": "universe_trader"},
    "orb":      {"script": str(TRADERS_DIR / "orb_trader.py"), "log": BASE_DIR / "logs/orb_v1.log", "cfg": TC_FILE, "grep": "orb_trader"},
    # 00.08 = Overnight ORB — same Mid-Day ORB entry, hold overnight, exit next-day 09:20 (POSITIONAL, allow_overnight)
    "orb_overnight": {"script": str(TRADERS_DIR / "orb_overnight_trader.py"), "log": BASE_DIR / "logs/orb_overnight_v1.log", "cfg": TC_FILE, "grep": "orb_overnight_trader"},
    "straddle": {"script": str(TRADERS_DIR / "straddle_trader.py"), "log": BASE_DIR / "logs/straddle_v1.log", "cfg": TC_FILE, "grep": "straddle_trader"},
    # 00.07 = Long Strangle @ ORB (OTM CE+PE buy, off=2, win 11-13). FORWARD-PAPER only —
    # failed sig p=0.072 + loses on real premium (bs_vs_reallake). Watch-only.
    "strangle": {"script": str(TRADERS_DIR / "strangle_trader.py"), "log": BASE_DIR / "logs/strangle_v1.log", "cfg": TC_FILE, "grep": "strangle_trader"},
    # mission numbering (user 2026-07-10): 01=straddle (above), 02=debit vertical, 03=ORB+Supertrend
    "dvert":    {"script": str(TRADERS_DIR / "02_debit_vertical_trader.py"), "log": BASE_DIR / "logs/dvert_v1.log", "cfg": TC_FILE, "grep": "02_debit_vertical_trader"},
    "orbst":    {"script": str(TRADERS_DIR / "03_orbst_trader.py"), "log": BASE_DIR / "logs/orbst_v1.log", "cfg": TC_FILE, "grep": "03_orbst_trader"},
    # 04 = Auto Rev-Chain Zone Breakout (user's Pine, NIFTY 5m ATM-option BUY, p=0.000)
    "chainzone": {"script": str(TRADERS_DIR / "04_chainzone_trader.py"), "log": BASE_DIR / "logs/chainzone_v1.log", "cfg": TC_FILE, "grep": "04_chainzone_trader"},
    # 05 = Ratio Backspread @ Mid-day ORB (sell 1 ATM + buy 2 OTM, p=0.002)
    "backspread": {"script": str(TRADERS_DIR / "05_backspread_trader.py"), "log": BASE_DIR / "logs/backspread_v1.log", "cfg": TC_FILE, "grep": "05_backspread_trader"},
    # 06 = Short-Vol Iron-Fly (REAL premium/IV, Sharpe 8.9, inverse leg) — theta harvest
    "shortvol": {"script": str(TRADERS_DIR / "06_shortvol_trader.py"), "log": BASE_DIR / "logs/shortvol_v1.log", "cfg": TC_FILE, "grep": "06_shortvol_trader"},
    # 07 = Mid-Day ORB on BANKNIFTY (new underlying, p=0.011, Sharpe 1.46)
    "banknifty": {"script": str(TRADERS_DIR / "07_banknifty_trader.py"), "log": BASE_DIR / "logs/banknifty_v1.log", "cfg": TC_FILE, "grep": "07_banknifty_trader"},
    # VRP panic-fade — POSITIONAL (holds to expiry via allow_overnight, ADR-006)
    "vrp":       {"script": str(TRADERS_DIR / "vrp_straddle_trader.py"), "log": BASE_DIR / "logs/vrp_v1.log", "cfg": TC_FILE, "grep": "vrp_straddle_trader"},
    # 10 = VRP Overnight Condor — daily defined-risk short-vol, ONE-night hold (allow_overnight, ADR-006)
    "vrp_condor": {"script": str(TRADERS_DIR / "vrp_condor_trader.py"), "log": BASE_DIR / "logs/vrp_condor_v1.log", "cfg": TC_FILE, "grep": "vrp_condor_trader"},
    # VRP Weekly Condor (mild-IV gate >=0.5, T-4 entry, pre-expiry exit) — POSITIONAL, FORWARD-PAPER
    "vrpw":      {"script": str(TRADERS_DIR / "vrp_condor_weekly_trader.py"), "log": BASE_DIR / "logs/vrpw_v1.log", "cfg": TC_FILE, "grep": "vrp_condor_weekly_trader"},
    # Distance-from-20EMA extreme-oversold BUY — POSITIONAL EQUITY (delivery/CNC), holds weeks (allow_overnight)
    "distma":    {"script": str(TRADERS_DIR / "dist_ma_trader.py"), "log": BASE_DIR / "logs/distma_v1.log", "cfg": TC_FILE, "grep": "dist_ma_trader"},
}
# Aliases — custom variation names map to base strategy
STRATEGY_ALIASES = {"ARS": "range", "rsi": "rsi"}

def _base(strategy):
    # TRAP #116's rule now lives in ONE place (strategy_registry.resolve_base) —
    # health_check.py had its own split('_')[0] copy that never got this fix, so
    # it resolved vrp_condor_v1 → "vrp" (straddle) and preflight-checked the wrong
    # script. Behaviour here is unchanged; only the home moved.
    import strategy_registry
    return strategy_registry.resolve_base(
        strategy, lambda b: (STRATEGIES.get(b) or {}).get("script"), STRATEGY_ALIASES)

def strat_label(strategy):
    """Strategy ka DISPLAY naam — registry se. Unknown/registry-down → raw id.

    Har wo backend string jo user ki aankh tak jaati hai (toast `msg`, report
    text, banner) isse guzre. Raw config-key SIRF plumbing me rehta hai —
    order_store rows, API query params, config keys, `?s=` — unhe kabhi mat
    badlo (poori history unhi strings pe keyed hai).

    Registry ka `resolve()` id/config_key/slug/aliases sab pe match karta hai,
    isliye purane naam se aaya id bhi sahi label pe pahunchta hai.
    """
    try:
        import strategy_registry as _sr
        return _sr.label(strategy) if _sr.resolve(strategy) else str(strategy)
    except Exception:
        return str(strategy)


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
    (cmdline me exact token — grep sirf fallback hai jab --id na ho; fixed 2026-07-03,
    ab dono "rsi"/"rsi_v1" STRATEGIES entries grep="01_rsi_v1" bolte hain, sahi script se match).
    psutil cross-platform — Windows pe pgrep NAHI hota (us bug se get_pid hamesha None
    deta tha → restart pe DUPLICATE traders spawn). pgrep fallback Linux/VPS ke liye."""
    want_id = strategy if (strategy and '_' in strategy) else None
    _psutil_scanned = False
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
            if want_id:   # exact token match — substring se 'rsi_v1' != 'rsi_v1_PAPER'
                # NOTE: split on the JOINED string, not `parts` — supervisor-forked
                # children ka setproctitle title /proc/cmdline me EK single element
                # hota hai ('code3b-strategy --paper --id X'), jisse parts-wise
                # token match hamesha miss hota tha.
                toks = cl.split()
                hit = any(t == f"--id={want_id}" or
                          (t == "--id" and i + 1 < len(toks) and toks[i + 1] == want_id)
                          for i, t in enumerate(toks))
                if hit:
                    return p.info['pid'], cl
            elif grep and grep in cl:
                return p.info['pid'], cl
        _psutil_scanned = True   # loop completed cleanly → result is AUTHORITATIVE
    except Exception:
        _psutil_scanned = False
    # psutil ne saare processes scan kar liye aur exact-token match nahi mila →
    # AUTHORITATIVE "not running". pgrep substring-fallback pe MAT giro — wo
    # '--id rsi_v1' ko '--id rsi_v1_PAPER' ke ANDAR match kar ke galat PID de deta
    # (real-money footgun: idle rsi_v1 (05.01) ka Stop LIVE rsi_v1_PAPER (05.02)
    # ko maar deta). LESSONS.md TRAP — rsi_v1 vs rsi_v1_PAPER.
    if _psutil_scanned:
        return None, None
    try:  # pgrep fallback SIRF jab psutil available na ho (bare env)
        # end-of-token anchored — '--id rsi_v1' ke baad space ya line-end hona zaroori,
        # to '--id rsi_v1_PAPER' se kabhi match nahi hoga.
        pat = f"--id {want_id}( |$)" if want_id else grep
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


@app.route('/stats2')
def stats2():
    """Compact V2 redesign of the Stats page — reuses the same calendar/stats
    JS logic (app-12/13 + deps) in a new 2-column tabbed layout. Original Stats
    tab in index.html is untouched. Display-only."""
    return render_template("stats2.html")

@app.route('/curves')
def option_curves_page():
    """Sensibull-style intraday option curves — ATM straddle premium + ATM gamma
    (real Dhan greeks) + spot/VIX/PCR, from the option-chain collector's per-minute
    snapshots. Display-only."""
    return render_template("option_curves.html")

@app.route('/api/option-curves')
def api_option_curves():
    import option_curves as oc
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date')
    if not date:
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    try:
        return jsonify(oc.curves(u, date, expiry))
    except Exception as e:
        print("[option-curves] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e), "expiries": [], "points": []})

@app.route('/api/option-alerts')
def api_option_alerts():
    """Fired option-chain alerts for a day → chart markers on /curves. Display-only."""
    import option_alerts as oa
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date')
    if not date:
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    try:
        return jsonify({"ok": True, "alerts": oa.read_log(u, date)})
    except Exception as e:
        return jsonify({"ok": False, "alerts": [], "error": str(e)})

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

@app.route('/mockup')
def serve_mockup():
    from flask import send_from_directory
    return send_from_directory(BASE_DIR, 'ui_mockup_redesign.html')

@app.route('/spec-builder')
def serve_spec_builder():
    """Strategy Spec Builder — master-prompt generator (static tool page, no wiring).
    Opened in a new tab from the 'More ▾' nav dropdown."""
    from flask import send_from_directory
    return send_from_directory(BASE_DIR, 'strategy_spec_builder.html')


# ---- 📋 EOD Reports (data/reports/, _ops/eod_report.py se generate) ----------
_REPORTS_DIR = BASE_DIR / "data" / "reports"
_REPORT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

@app.route('/registry')
def strategy_registry_page():
    """Unified Strategy Registry tree view — every strategy in one place (login-gated)."""
    from flask import make_response
    resp = make_response(render_template("strategy_registry.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'  # always fresh (no stale-cache UI)
    return resp

@app.route('/registry2')
def strategy_registry_page_v2():
    """Strategy Registry v2 — one sortable/filterable table (same live data as /registry:
    /api/strategy-registry + /api/config + /lab/runs/index.json + /api/timer-status).
    instrument/hold/structure come from strategy_registry.json static fields. Login-gated."""
    from flask import make_response
    resp = make_response(render_template("strategy_registry2.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp

@app.route('/sl-map')
def sl_map_page():
    """SL Map — every RMS stop-loss mechanism + which strategy has what, LIVE from
    config (login-gated). The reference the user asked for so 'kaun sa SL kahan'
    is never a mystery again."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    try:
        cfg = json.loads(TC_FILE.read_text())
    except Exception:
        cfg = {}
    per = (cfg.get("_risk", {}) or {}).get("per_strategy", {}) or {}
    glob = (cfg.get("_risk", {}) or {}).get("global", {}) or {}
    gcap = glob.get("max_loss_rs") or 5500
    rows, disc, bt = [], [], []
    for sid, v in sorted(per.items()):
        v = v or {}
        sl_on = v.get("default_sl_enabled") is True
        initsl = int(v.get("default_tsl_initial_sl_per_lot") or 2500)
        mlr = v.get("max_loss_rs")
        daycap = f"₹{int(mlr):,}" if mlr else f"₹{int(gcap):,}"
        if mlr and int(mlr) >= 1000000:
            daycap = "₹10L (off)"
        rows.append({"name": sid, "type": "Discretionary" if sl_on else "Backtested",
                     "sl_on": sl_on, "initsl": initsl, "daycap": daycap})
        (disc if sl_on else bt).append(sid)
    ist = _dt.now(_tz.utc) + _td(hours=5, minutes=30)
    # discretionary first in the table so the ON rows lead
    rows.sort(key=lambda r: (not r["sl_on"], r["name"]))
    return render_template("sl_map.html", rows=rows, disc=disc, bt=bt,
                           updated=ist.strftime("%Y-%m-%d %H:%M"))


@app.route('/reports')
def reports_list():
    """Date-wise EOD report list — login-gated (before_request), self-contained page."""
    try:
        dates = json.loads((_REPORTS_DIR / "index.json").read_text(encoding="utf-8"))
    except Exception:
        dates = []
    items = "".join(
        f"<li><a href='/reports/{d}'>📋 {d}</a></li>" for d in dates) or \
        "<li class='dim'>abhi koi report nahi — pehla report close ke baad banega</li>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>EOD Reports</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;
max-width:640px;margin:40px auto;padding:0 16px}}h1{{font-size:20px}}
a{{color:#58a6ff;text-decoration:none;font-size:16px}}a:hover{{text-decoration:underline}}
li{{margin:10px 0;list-style:none}}.dim{{color:#8b949e}}
button{{background:#238636;color:#fff;border:0;border-radius:8px;padding:8px 16px;
cursor:pointer;font-size:14px}}#st{{color:#8b949e;font-size:13px;margin-left:10px}}</style>
</head><body><h1>📋 EOD Reports</h1>
<p><button onclick="gen()">⚡ Aaj ka report abhi banao</button><span id="st"></span></p>
<ul>{items}</ul>
<p><a href='/'>← Dashboard</a></p>
<script>
async function gen(){{
  document.getElementById('st').textContent='ban raha hai... (replay ke saath ~1-2 min)';
  const r=await fetch('/api/reports/generate',{{method:'POST'}});
  const j=await r.json();
  document.getElementById('st').textContent=j.msg||'done';
  if(j.ok) setTimeout(()=>location.reload(), 90000);
}}
</script></body></html>"""

@app.route('/reports/<date>')
def reports_view(date):
    from flask import send_file
    if not _REPORT_DATE_RE.match(date):
        return "invalid date", 400
    f = _REPORTS_DIR / f"eod_{date}.html"
    if not f.exists():
        return f"<body style='background:#0d1117;color:#e6edf3;font-family:sans-serif'>" \
               f"<p>{date} ka report nahi mila — /reports se generate karo.</p></body>", 404
    return send_file(f)


# ---- 🎬 YT Presentations (data/presentations/<date>.html — task 76) ----------
# Daily workflow: user Claude session me din ke points deta hai → Claude poora
# presentation HTML banata hai → yahan date-file me save + VPS deploy → is page
# se date-wise khulta hai. App khud generate NAHI karta — sirf archive/viewer.
_PRESENT_DIR = BASE_DIR / "data" / "presentations"
# Presentations ka apna naam-pattern: date + optional suffix (2026-07-15b) — ek din
# ka kaam do alag decks me bat sakta hai. Reports ka _REPORT_DATE_RE strict date hi
# rehta hai (EOD report per-date generate hoti hai, suffix ka matlab nahi).
_PRESENT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[a-z]?$")

@app.route('/presentations')
def presentations_list():
    """Date-wise YT presentation list — login-gated (before_request)."""
    try:
        dates = sorted((f.stem for f in _PRESENT_DIR.glob("*.html")
                        if _PRESENT_NAME_RE.match(f.stem)), reverse=True)
    except Exception:
        dates = []
    items = "".join(
        f"<li><a href='/presentations/{d}'>🎬 {d}</a></li>" for d in dates) or \
        "<li class='dim'>abhi koi presentation nahi — Claude session me din ke points do, wahi banake yahan save karega</li>"
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>YT Presentations</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;
max-width:640px;margin:40px auto;padding:0 16px}}h1{{font-size:20px}}
a{{color:#58a6ff;text-decoration:none;font-size:16px}}a:hover{{text-decoration:underline}}
li{{margin:10px 0;list-style:none}}.dim{{color:#8b949e}}p.hint{{color:#8b949e;font-size:13px}}</style>
</head><body><h1>🎬 YT Presentations</h1>
<p class="hint">Roz ka flow: Claude ko din ke points do → wo presentation banake yahan date-wise save karta hai.</p>
<ul>{items}</ul>
<p><a href='/'>← Dashboard</a> &nbsp;|&nbsp; <a href='/reports'>📋 EOD Reports</a></p>
</body></html>"""

@app.route('/presentations/<date>')
def presentations_view(date):
    from flask import send_file
    if not _PRESENT_NAME_RE.match(date):
        return "invalid date", 400
    f = _PRESENT_DIR / f"{date}.html"
    if not f.exists():
        return f"<body style='background:#0d1117;color:#e6edf3;font-family:sans-serif'>" \
               f"<p>{date} ka presentation nahi mila.</p></body>", 404
    return send_file(f)

@app.route('/api/reports/generate', methods=['POST'])
def api_reports_generate():
    """Background me eod_report.py chalao (non-blocking) — page 90s me khud reload hota."""
    import subprocess
    date = (request.get_json(silent=True) or {}).get("date") or \
        (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    if not _REPORT_DATE_RE.match(date):
        return jsonify(ok=False, msg="invalid date"), 400
    try:
        subprocess.Popen([_sys.executable, "-X", "utf8",
                          str(BASE_DIR / "_ops" / "eod_report.py"), "--date", date],
                         cwd=str(BASE_DIR), start_new_session=True)
        return jsonify(ok=True, msg=f"{date} ka report background me ban raha hai — "
                                    f"1-2 min me /reports/{date} pe milega")
    except Exception as e:
        return jsonify(ok=False, msg=str(e)), 500

@app.route('/lab')
@app.route('/lab/')
def serve_lab_hub():
    from flask import send_from_directory
    return send_from_directory(BASE_DIR / 'scratch' / 'nifty_trend', 'hub.html')

_LAB_GZIP_EXT = ('.js', '.html', '.htm', '.json', '.css', '.svg', '.csv')

def _lab_gzip_response(full: Path):
    """Serve a Lab text asset gzip-compressed (results.js is 3-13 MB uncompressed —
    the whole reason the Lab page felt slow to load). A sidecar `<file>.gz` is cached
    next to the source and only regenerated when the source's mtime changes, so repeat
    requests cost ~0 CPU. Browser still revalidates via ETag/Last-Modified → 304 when
    unchanged. Falls back to a plain stream if anything goes wrong. Read-only path —
    no bearing on the live order flow."""
    import gzip, hashlib
    try:
        src_mtime = full.stat().st_mtime
        gz = full.with_name(full.name + '.gz')
        if (not gz.exists()) or gz.stat().st_mtime < src_mtime:
            raw = full.read_bytes()
            with gzip.open(gz, 'wb', compresslevel=6) as f:
                f.write(raw)
            os.utime(gz, (src_mtime, src_mtime))   # tie the cache to the source's mtime
        data = gz.read_bytes()
        etag = hashlib.md5(f"{full.name}:{src_mtime}:{len(data)}".encode()).hexdigest()
        if request.headers.get('If-None-Match') == etag:
            return Response(status=304)
        import mimetypes
        ctype = mimetypes.guess_type(full.name)[0] or 'application/octet-stream'
        resp = Response(data, mimetype=ctype)
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Vary'] = 'Accept-Encoding'
        resp.headers['Cache-Control'] = 'no-cache'   # revalidate → 304, never blindly stale
        resp.headers['ETag'] = etag
        return resp
    except Exception:
        return send_from_directory(full.parent, full.name)

@app.route('/lab/<path:fn>')
def serve_lab_file(fn):
    """Strategy Lab — serves the NIFTY research hub + its dashboards / results.js
    (self-contained HTML in scratch/nifty_trend). Relative links resolve under /lab/."""
    root = (BASE_DIR / 'scratch' / 'nifty_trend').resolve()
    full = (root / fn).resolve()
    # path-traversal guard (fn comes from the URL)
    if root not in full.parents and full != root:
        return Response('not found', status=404)
    if (full.is_file() and full.suffix.lower() in _LAB_GZIP_EXT
            and 'gzip' in request.headers.get('Accept-Encoding', '')):
        return _lab_gzip_response(full)
    return send_from_directory(root, fn)

# ---- gzip the heavy static assets (vendor libs + app JS) ----------------------
# A hard-refresh / first-load re-downloads ~2 MB of UNCOMPRESSED js (vendor ~1.4 MB +
# app ~0.7 MB) because Flask's built-in /static/ handler doesn't gzip. These two
# more-specific routes win over the built-in for /static/js and /static/vendor and
# reuse the SAME sidecar-.gz cache (+ ETag/304) as the Lab pages → ~4× smaller, so
# the "Loading…" on Ctrl+Shift+R is much shorter. Read-only, no order/live-path.
def _serve_static_gz(sub, fn):
    root = (BASE_DIR / 'static' / sub).resolve()
    try:
        full = (root / fn).resolve()
    except Exception:
        return Response('not found', status=404)
    if (root not in full.parents and full != root) or not full.is_file():
        return Response('not found', status=404)
    if (full.suffix.lower() in _LAB_GZIP_EXT
            and 'gzip' in request.headers.get('Accept-Encoding', '')):
        return _lab_gzip_response(full)   # falls back to a plain stream on any error
    return send_from_directory(full.parent, full.name)

@app.route('/static/js/<path:fn>')
def _gz_static_js(fn):
    return _serve_static_gz('js', fn)

@app.route('/static/vendor/<path:fn>')
def _gz_static_vendor(fn):
    return _serve_static_gz('vendor', fn)

# ---- Excel Cross-Check: upload a backtest workbook, recompute + render ----
@app.route('/lab/upload')
def serve_lab_upload():
    """Upload page for a backtest cross-check workbook (built by xlsx_export.py)."""
    from flask import send_from_directory
    return send_from_directory(BASE_DIR / 'scratch' / 'nifty_trend', 'lab_upload.html')

@app.route('/api/lab/upload-xlsx', methods=['POST'])
def api_lab_upload_xlsx():
    """Parse an uploaded cross-check .xlsx -> recompute metrics with the canonical
    engine (independent 2nd eye) -> mint a lab run (results.js + dashboard) so the
    SAME lab dashboard renders it, and return the Claude-vs-lab cross-check.
    Read-only research path — no order/risk/live code involved."""
    import sys as _sys, json as _json, time as _time
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(ok=False, msg='koi file nahi mili'), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify(ok=False, msg='.xlsx file chahiye'), 400
    nt = (BASE_DIR / 'scratch' / 'nifty_trend').resolve()
    if str(nt) not in _sys.path:
        _sys.path.insert(0, str(nt))
    try:
        import xlsx_import as XI
    except Exception as e:
        return jsonify(ok=False, msg='lab import fail: ' + str(e)), 500
    uid = _time.strftime('%Y%m%d_%H%M%S')
    updir = nt / 'runs' / '_uploads' / uid
    updir.mkdir(parents=True, exist_ok=True)
    xlpath = updir / 'source.xlsx'
    f.save(str(xlpath))
    try:
        parsed = XI.parse(str(xlpath))
        rc = XI.recompute(parsed['trades'])
        cc = XI.jsonable(XI.crosscheck(parsed['claude_summary'], rc))
        payload = XI.jsonable(XI.to_results_payload(parsed, rc))
    except Exception as e:
        return jsonify(ok=False, msg='parse/recompute fail: ' + str(e)), 400
    try:
        (updir / 'results.js').write_text('window.RESULTS = ' + _json.dumps(payload) + ';',
                                          encoding='utf-8')
        # mint index.html the same way run_hunt does: point the dashboard at ./results.js
        _dash = (nt / 'dashboard_intraday.html').read_text(encoding='utf-8').replace(
            'src="results_intraday.js"', 'src="results.js"')
        (updir / 'index.html').write_text(_dash, encoding='utf-8')
        (updir / 'crosscheck.json').write_text(_json.dumps(cc), encoding='utf-8')
    except Exception as e:
        return jsonify(ok=False, msg='mint run fail: ' + str(e)), 500
    # headline cards use the CLAIMED (validated) metrics so they match the run's own
    # dashboard — e.g. calendar-day Sharpe 2.37, not the trade-day recompute 3.44.
    # The independent recompute stays visible in the cross-check table below.
    disp = payload.get('combos', {}).get('bs|full', {}).get('metrics', {}) or rc
    n_mismatch = sum(1 for r in cc if r.get('status') == 'MISMATCH')
    return jsonify(ok=True, uid=uid,
                   view_url='/lab/runs/_uploads/%s/index.html' % uid,
                   crosscheck=cc, mismatches=n_mismatch,
                   summary={k: XI.jsonable(disp.get(k)) for k in
                            ('trades', 'net_abs', 'net_pct', 'sharpe',
                             'profit_factor', 'win_rate', 'maxdd', 'expectancy', 'fees')})

@app.route('/script3')
def serve_script3():
    """Script 3 redesign mockup (static, no wiring yet) — iframed body-only into
    the main dashboard's 'Script 3' tab. Deliberately contains NO AlgoTrader nav/
    header (real nav lives in index.html, this renders under it)."""
    return render_template('script3.html')

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

@app.route('/api/strategy-registry', methods=['GET'])
def api_strategy_registry():
    """Canonical Strategy ID registry (family.member IDs). Read-only; the frontend
    uses it so every surface shows the same 'NN.MM - Name' label."""
    try:
        import strategy_registry as _sr
        return jsonify(_sr.load(force=True))
    except Exception as e:
        return jsonify({"error": str(e), "families": {}, "strategies": {}})


@app.route('/api/param-stability', methods=['GET'])
def api_param_stability():
    """Days since each strategy's CORE params (entry/exit/SL/target) were last changed,
    from the config audit log (data/rms_audit_log.json). Powers the registry 'Untouched'
    column — how many days of data were collected on the SAME params (= trust). Read-only."""
    try:
        import param_stability as ps
        try:
            cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        except Exception:
            cfg = {}
        cks = set()
        for k, v in (cfg or {}).items():
            if k in _AUDIT_SKIP_TOP or k in ("_risk", "webhooks"):
                continue
            if isinstance(v, dict):
                cks.add(k)                       # strategy config blocks
        for sid in ((cfg.get("_risk") or {}).get("per_strategy") or {}):
            cks.add(sid)                          # RMS per-strategy overrides
        ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
        return jsonify(ps.compute(str(RMS_AUDIT_FILE), cks, today=ist.date()))
    except Exception as e:
        return jsonify({"error": str(e)})

# --- Family-10 (Factor/Equity) PAPER-deploy toggle: these are standalone systemd-timer bots
#     (monthly rebalance), NOT dashboard-Popen intraday loops. The registry "▶ Paper" button
#     enables/disables the timer. PAPER-ONLY + whitelisted units (no arbitrary systemctl, no
#     Live path — equity go-live is gated on GO_LIVE_CHECKLIST). No orders/broker here. ---
_TIMER_STRATEGIES = {"momentum-paper.timer"}

def _timer_state(unit):
    import subprocess
    try:
        en = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True, timeout=6).stdout.strip()
        ac = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=6).stdout.strip()
        return {"unit": unit, "enabled": en == "enabled", "active": ac == "active", "state": en or "unknown"}
    except Exception as e:
        return {"unit": unit, "enabled": False, "active": False, "state": "err", "err": str(e)}

@app.route('/api/timer-status', methods=['GET'])
def api_timer_status():
    return jsonify({u: _timer_state(u) for u in _TIMER_STRATEGIES})

@app.route('/api/timer-deploy', methods=['POST'])
def api_timer_deploy():
    import subprocess
    d = request.get_json(silent=True) or {}
    unit, action = d.get("unit"), d.get("action")
    if unit not in _TIMER_STRATEGIES:
        return jsonify({"ok": False, "err": "unknown/again-whitelisted timer"}), 400
    if action == "paper":
        cmd = ["systemctl", "enable", "--now", unit]
    elif action == "stop":
        cmd = ["systemctl", "disable", "--now", unit]
    else:
        return jsonify({"ok": False, "err": "action must be paper|stop"}), 400
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0:
        return jsonify({"ok": False, "err": (r.stderr or r.stdout).strip()}), 500
    return jsonify({"ok": True, "state": _timer_state(unit)})

@app.route('/api/config', methods=['POST'])
def api_set_config():
    data = request.get_json()
    try:
        old = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        old = {}
    _config_audit_record(old, data)   # log BEFORE overwrite — every field, not just _risk
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

def resolve_trailing_step(entry_px, g_cfg=None):
    """Helper to determine the step size for trailing Stop-Loss/Take-Profit.
    Uses Zerodha-style guidelines (₹1.0/₹2.5/₹5.0/₹10.0 ranges) with custom overrides
    configured in global risk settings. Done separately to keep logic clean and modular.
    """
    if g_cfg is None:
        g_cfg = _risk_config().get("global") or {}
    
    if entry_px <= 50:
        custom = g_cfg.get("trailing_step_band_1")
        try:
            if custom is not None and str(custom).strip() != "":
                return float(custom)
        except: pass
        return 1.0
    elif entry_px <= 100:
        custom = g_cfg.get("trailing_step_band_2")
        try:
            if custom is not None and str(custom).strip() != "":
                return float(custom)
        except: pass
        return 2.5
    elif entry_px <= 500:
        custom = g_cfg.get("trailing_step_band_3")
        try:
            if custom is not None and str(custom).strip() != "":
                return float(custom)
        except: pass
        return 5.0
    else:
        custom = g_cfg.get("trailing_step_band_4")
        try:
            if custom is not None and str(custom).strip() != "":
                return float(custom)
        except: pass
        return 10.0



@app.route('/api/risk-config', methods=['GET'])
def api_get_risk_config():
    return jsonify(_risk_config())

# ── Task 13 — config value-change audit log ─────────────────────────────────
# Every Save (⚠️ Risk tab AND the Strategies config grid) diffs the OLD config against
# the NEW one and appends only the fields that actually changed (IST timestamp + section
# + scope) to data/rms_audit_log.json. Answers "kab kaun si value change hui" without
# guessing.
#
# 2026-07-17 — widened from _risk-only to the WHOLE config. The _risk-only version was
# blind to two whole classes of change: (a) every strategy-block field (qty/lot size,
# symbols, timeframe, active, mode) — those aren't under _risk at all; (b) ANY field,
# _risk included, saved via POST /api/config, which rewrites the entire file and never
# called this. So a strategy's lot size could go 1->3 mid-sample and nothing recorded it.
# That is not a cosmetic gap: any analysis that pools trades across an unrecorded change
# is pooling two different strategies and calling the mush a result. It happened —
# rsi_v1_PAPER's qty 1->3 + profit_target + max_trades were all invisible here, and 89
# trades spanning three configs got read as one homogeneous sample.
_AUDIT_SKIP_TOP = {"_ui_config"}   # UI-only prefs (column layout etc) — not a trading decision


def _cfg_flatten(cfg):
    """(whole config) -> {(section, scope, field): value}.

    section = 'risk' | 'strategy' | 'webhook' | 'root'. It's load-bearing, not decoration:
    scope+field alone are ambiguous — max_trades_per_day lives in BOTH _risk.per_strategy[X]
    and the X strategy block, and they're different knobs."""
    out = {}
    for top, v in (cfg or {}).items():
        if top in _AUDIT_SKIP_TOP:
            continue
        if top == "_risk":
            for k, gv in ((v or {}).get("global") or {}).items():
                out[("risk", "global", k)] = gv
            for sid, sc in ((v or {}).get("per_strategy") or {}).items():
                for k, sv in (sc or {}).items():
                    out[("risk", sid, k)] = sv
        elif top == "webhooks":
            for wid, wc in (v or {}).items():
                if isinstance(wc, dict):
                    for k, wv in wc.items():
                        out[("webhook", wid, k)] = wv
                else:
                    out[("webhook", wid, "")] = wc
        elif isinstance(v, dict):
            for k, sv in v.items():
                out[("strategy", top, k)] = sv
        else:
            out[("root", "", top)] = v
    return out


def _config_audit_record(old_cfg, new_cfg):
    """Append the old->new diff of the whole config. Never raises into the save path."""
    def _norm(x):
        if x is None:
            return ""
        if isinstance(x, (dict, list)):
            return json.dumps(x, sort_keys=True)
        return str(x).strip()
    try:
        of, nf = _cfg_flatten(old_cfg), _cfg_flatten(new_cfg)
        ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
        ts = ist.strftime("%Y-%m-%d %H:%M:%S")
        changes = []
        for key in (set(of) | set(nf)):
            section, scope, field = key
            ov, nv = of.get(key), nf.get(key)
            if _norm(ov) == _norm(nv):
                continue   # unchanged (blank/None treated equal)
            changes.append({"ts": ts, "section": section, "scope": scope, "field": field,
                            "old": ("" if ov is None else ov),
                            "new": ("" if nv is None else nv)})
        if not changes:
            return
        log = []
        if RMS_AUDIT_FILE.exists():
            try: log = json.loads(RMS_AUDIT_FILE.read_text())
            except Exception: log = []
        log.extend(changes)
        RMS_AUDIT_FILE.write_text(json.dumps(log[-4000:], indent=2))   # cap history
    except Exception as e:
        print("config audit record fail:", e, flush=True)

@app.route('/api/rms-audit-log')
def api_rms_audit_log():
    """Newest-first list of RMS field changes for the Risk tab's Change History panel."""
    try:
        log = json.loads(RMS_AUDIT_FILE.read_text()) if RMS_AUDIT_FILE.exists() else []
    except Exception:
        log = []
    return jsonify(list(reversed(log)))

@app.route('/api/risk-config', methods=['POST'])
def api_set_risk_config():
    data = request.get_json() or {}
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        cfg = {}
    old_risk = {"global": (cfg.get("_risk") or {}).get("global") or {},
                "per_strategy": (cfg.get("_risk") or {}).get("per_strategy") or {}}
    new_risk = {"global": data.get("global") or {}, "per_strategy": data.get("per_strategy") or {}}
    _config_audit_record({"_risk": old_risk}, {"_risk": new_risk})   # Task 13 — diff before overwrite
    cfg["_risk"] = new_risk
    TC_FILE.write_text(json.dumps(cfg, indent=2))
    return jsonify({"msg": "Risk settings saved!"})

@app.route('/api/sync-from-vps', methods=['POST'])
def api_sync_from_vps():
    """LOCAL-only: pull VPS trades.db + configs + logs to this local machine so
    the local dashboard shows today's LIVE (VPS) data. Blocked on the VPS itself
    (Linux) — it would scp from itself. Runs _ops/sync_vps_to_local.py."""
    import sys as _sys, subprocess as _sp
    if _sys.platform != 'win32':
        return jsonify({"ok": False, "msg": "Sync sirf LOCAL (Windows dev) pe chalta hai — VPS pe nahi."}), 400
    script = str(BASE_DIR / "_ops" / "sync_vps_to_local.py")
    try:
        r = _sp.run([_sys.executable, script], capture_output=True, text=True, timeout=180)
        ok = (r.returncode == 0)
        tail = (r.stdout or "")[-1200:]
        if r.stderr:
            tail += "\n[stderr] " + r.stderr[-400:]
        return jsonify({"ok": ok,
                        "msg": "Sync done — VPS ka aaj ka data local pe aa gaya." if ok else "Sync fail — output dekho.",
                        "output": tail})
    except _sp.TimeoutExpired:
        return jsonify({"ok": False, "msg": "Sync timeout (180s) — VPS reachable hai?"}), 504
    except Exception as e:
        return jsonify({"ok": False, "msg": f"Sync error: {e}"}), 500

@app.route('/api/kill-floor-status')
def api_kill_floor_status():
    """Live kill-floor state for the RMS tab's big display (2026-07-02).
    Reads the disk state pos_monitor_loop (algo-monitor process) writes —
    this dashboard process doesn't own the engine, so the file IS the truth."""
    import risk_gate
    cfg = risk_gate.kill_floor_config()
    st = {}
    try:
        st = json.loads(_KILL_FLOOR_FILE.read_text())
    except Exception:
        pass
    fired_flag = risk_gate.kill_floor_fired_today()
    return jsonify({
        "config": cfg,
        "armed": bool(st.get("armed")),
        "peak": st.get("peak") or 0.0,
        "floor": st.get("floor"),
        "fired": bool(st.get("fired")) or fired_flag,
        "breaching": st.get("breach_since") is not None,
        "day": st.get("day"),
    })


@app.route('/api/per-instrument-lock-status')
def api_per_instrument_lock_status():
    """Live per-instrument trailing-lock state for the RMS tab's display
    (2026-07-02 redesign) — same read-the-disk-state pattern as
    /api/kill-floor-status, since pos_monitor_loop runs in algo-monitor, not
    this dashboard process. One row per position currently tracked."""
    import risk_gate, order_store
    from datetime import timedelta as _td
    cfg = risk_gate.per_instrument_lock_config()
    state = {}
    try:
        _raw = json.loads((BASE_DIR / "data" / "pos_lock_state.json").read_text())
        state = _raw.get("state") or {}
    except Exception:
        pass
    sym_by_id = {}
    try:
        _ist = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
        for _p in order_store.trades_for(_ist.strftime("%Y-%m-%d")).get("open", []):
            sym_by_id[str(_p.get("id"))] = _p.get("sym")
    except Exception:
        pass
    rows = []
    for pid, st in state.items():
        rows.append({
            "id": pid,
            "sym": sym_by_id.get(str(pid), "?"),
            "armed": bool(st.get("armed")),
            "peak": st.get("peak") or 0.0,
            "floor": st.get("floor"),
            "fired": bool(st.get("fired")),
            "breaching": st.get("breach_since") is not None,
        })
    return jsonify({"config": cfg, "positions": rows})


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
    # Exclude CAPITAL_BLOCKED rows before handing to broker_sync — see the
    # matching fix + comment in pos_monitor_loop (TRAP #92).
    open_pos = [p for p in open_pos if "CAPITAL_BLOCKED" not in (p.get("tags") or [])]
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


@app.route('/api/reconcile-manual-trades', methods=['POST'])
def api_reconcile_manual_trades():
    """Button-triggered (Completed Trades card): pulls today's real broker
    fills, matches each against order_store by broker_order_id, corrects
    drifted prices, and inserts any fill the broker has that this app never
    placed at all (a manual trade on the same account) — tagged 'manual' so
    it shows up like any other row and the day's TOTAL reconciles against
    the broker's real account P&L. See broker_sync.reconcile_manual_trades()."""
    import broker_sync
    broker_name = request.args.get('broker', 'kite')
    date = request.args.get('date') or None
    result = broker_sync.reconcile_manual_trades(date=date, broker_name=broker_name, log=print)
    status = 200 if result.get("ok") else 500
    return jsonify(result), status


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

# ── Fork-supervisor mode (2026-07-21) ────────────────────────────────────────
# data/supervisor_mode.flag maujood ho TO start/stop supervisor-daemon ke
# through jaate hain (RAM: 15 interpreters -> 1 warm parent + COW forks,
# measured ~76% kam). Flag hata do -> turant legacy Popen wapas. Daemon dead
# ho to api_start LEGACY pe khud gir jaata hai (fail-safe, loud log) — "kal
# subah strategies start nahi hui" wala failure mode exist nahi karta.
SUPERVISOR_FLAG = BASE_DIR / 'data' / 'supervisor_mode.flag'

def _supervisor_daemon_pid():
    try:
        from _ops.strategy_supervisor import daemon_alive
        return daemon_alive()
    except Exception:
        return None

def _supervisor_desired_set(sid, desired, mode=None, script=None):
    """supervisor_desired.json me ek entry (atomic). Daemon ise ~2s me uthata hai."""
    f = BASE_DIR / 'data' / 'supervisor_desired.json'
    try:
        cur = json.loads(f.read_text()) if f.exists() else {}
        if not isinstance(cur, dict):
            cur = {}
    except Exception:
        cur = {}
    ent = cur.get(sid) if isinstance(cur.get(sid), dict) else {}
    ent['desired'] = desired
    if mode is not None:
        ent['mode'] = mode
    if script is not None:
        ent['script'] = script
    ent['ts'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cur[sid] = ent
    tmp = f.with_suffix('.tmp')
    tmp.write_text(json.dumps(cur, indent=2))
    os.replace(tmp, f)


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
        return jsonify({"msg": f"⚠ Live/paper trading not built yet for '{strat_label(s)}' — backtest only for now."}), 400
    pid  = get_pid(s)
    if pid:
        return jsonify({"msg": f"{strat_label(s)} already running (PID {pid})"})
    launched_via = 'legacy'
    if SUPERVISOR_FLAG.exists() and _supervisor_daemon_pid():
        # Fork-supervisor path: desired-state likho, daemon ~2s me fork karega
        # (RAM COW-shared). Script yahin se jaata hai — dashboard hi STRATEGIES
        # ka single source hai, daemon kabhi dashboard import nahi karta.
        _supervisor_desired_set(s, 'running', mode=mode, script=st['script'])
        launched_via = 'supervisor'
    else:
        if SUPERVISOR_FLAG.exists():
            # Flag on par daemon dead — FAIL-SAFE: legacy Popen se hi chalao,
            # strategy start hona daemon ki sehat se zyada zaroori hai.
            print(f"🔴 [SUPERVISOR] flag ON par daemon DEAD — {s} legacy Popen se "
                  f"start kar raha hoon (systemctl status algo-supervisor dekho)", flush=True)
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
    # Mark active + remember mode, so a later auto-restart (crash recovery,
    # algo-monitor restart mid-day, VPS reboot) brings it back the same way
    # it was actually running — not silently downgraded to paper (TRAP #57).
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        if s not in cfg:
            cfg[s] = {}
        cfg[s]['active'] = True
        cfg[s]['mode'] = mode
        TC_FILE.write_text(json.dumps(cfg, indent=2))
    except Exception:
        pass
    via = " (supervisor)" if launched_via == 'supervisor' else ""
    return jsonify({"msg": f"✅ {strat_label(s)} started — {mode.upper()} mode{via}"})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    s   = request.args.get('s', 'ema')
    # keep_active=1 -> sirf process band karo, 'active' intent RAKHO. 15:30 ka
    # scheduled-stop yeh bhejta hai taaki kal 9:10 auto-start phir chala de.
    # Manual stop (user ne click kiya) = keep_active nahi -> active:false (band hi rahe).
    keep_active = request.args.get('keep_active') == '1'
    if SUPERVISOR_FLAG.exists():
        # Supervisor mode: desired=stopped PEHLE likho (pid check se pehle) —
        # process pehle hi mar chuka ho tab bhi stale desired=running saaf ho
        # jaata hai, warna daemon use dobara fork kar deta. Daemon khud bhi
        # SIGTERM bhejta hai (grace ke baad SIGKILL); neeche wala kill
        # immediacy ke liye hai — dono same SIGTERM, koi conflict nahi.
        try:
            _supervisor_desired_set(s, 'stopped')
        except Exception as _e:
            print(f"🔴 [SUPERVISOR] desired=stopped write FAIL for {s}: {_e}", flush=True)
    pid = get_pid(s)
    if not pid:
        return jsonify({"msg": f"{strat_label(s)} not running"})
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
        return jsonify({"msg": f"⏹ {strat_label(s)} stopped"})
    except Exception as e:
        return jsonify({"msg": f"Error: {e}"})

# /api/pnl DELETED (2026-07-16) — no callers; log-scraped P&L, superseded by
# order_store (the tagged, per-trade source of truth the whole Orders & P&L tab
# reads). parse_pnl() itself STAYS: it is the spec for the log line format that
# webhook_executor still writes (see its :196 "parse_pnl-compatible" writer) and
# that order_store's docstring cites for backward-compat.

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

@app.route('/api/token_check')
def api_token_check():
    """Control tab ka "Check" button — Dhan JWT abhi zinda hai ya nahi.

    Ye route kabhi tha hi nahi. Button 2026-07-16 tak `fetch('/api/token_check')`
    karta tha, Flask 404 ka HTML deta tha, `r.json()` throw karti thi, aur
    `token-msg` kabhi likha hi nahi jaata — button dabao, kuch nahi hota, koi
    error nahi. Aur ye theek us credential pe baitha hai jiski chupchaap expiry
    baar-baar firefight ki jad rahi hai.

    Checks health_check ke apne hain (Rule 6B — teesri copy nahi): JWT ka apna
    exp claim + ek asli LTP call, kyunki decode-hone-wala token bhi broker ke
    reject kiye jaane pe bekaar hai. 09:20 wala timer bhi yehi do use karta hai.
    """
    try:
        token, cid = _creds()
        if not token:
            return jsonify({"ok": False, "msg": "❌ Koi Dhan token saved nahi — neeche paste karke Save karo"})
        import health_check as _hc
        exp_dt, hrs = _hc._jwt_expiry(token)
        exp_txt = (f" · expiry {exp_dt.strftime('%d %b %H:%M')} ({hrs:.1f}h baaki)"
                   if exp_dt is not None else " · expiry claim decode nahi hui")
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        price, err = _hc._dhan_ltp(headers, "IDX_I", "13")
        if price is not None:
            return jsonify({"ok": True, "msg": f"✅ Token zinda — NIFTY {price:,.2f}{exp_txt}"})
        if (err or "").startswith("AUTH:"):
            return jsonify({"ok": False, "msg": f"❌ Token expired/galat — Dhan ne reject kiya{exp_txt}"})
        # rate-limit/network — health_check bhi ise WARN hi maanta, FAIL nahi
        return jsonify({"ok": None, "msg": f"⚠️ Confirm nahi kar paye ({err}) — transient lag raha hai{exp_txt}"})
    except Exception as e:
        return jsonify({"ok": None, "msg": f"⚠️ Check fail: {e}"})


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
        # Token saved → the Dhan-token problem is over. Drop ONLY its alerts, so
        # the next ingest poll marks them "✓ fixed" on the bell. `substr` covers
        # auto_data_downloader's legacy plain-string token alerts; the key covers
        # health_check's. Deliberately NOT touching token:kite — a Dhan token
        # says nothing about Kite (the old inline filter matched 'token expire',
        # which silently cleared "Kite token EXPIRED" too).
        n = _clear_alerts("token:dhan", substr="dhan token")
        n += _clear_alerts(substr="token expire")   # legacy downloader strings
        if n:
            print(f"[alerts] Dhan token saved — {n} alert(s) cleared", flush=True)
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
        # Logged in → the Kite-token problem is over. This route cleared NOTHING
        # before, so the 🔔 kept showing "Kite token EXPIRED" until health_check's
        # next timer run (09:20 weekdays) — i.e. a morning login left a red bell
        # sitting there all day, which is exactly how a bell stops being believed.
        n = _clear_alerts("token:kite")
        if n:
            print(f"[alerts] Kite login OK — {n} alert(s) cleared", flush=True)
        return jsonify({"ok": True, "msg": "Kite access token saved", "alerts_cleared": n})
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


@app.route('/api/kite-key-status')
def api_kite_key_status():
    """Kite api_key/api_secret already saved hain ya nahi (permanent creds).
    Sirf status + last-4 preview — kabhi raw secret return nahi karta.
    UI isse dikhata hai ki roz sirf request_token chahiye, key/secret nahi."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
        api_key = (cfg.get("kite_api_key") or "").strip()
        has_secret = bool((cfg.get("kite_api_secret") or "").strip())
        return jsonify({
            "has_key": bool(api_key) and has_secret,
            "api_key_preview": api_key[-4:] if api_key else "",
        })
    except Exception as e:
        return jsonify({"has_key": False, "error": str(e)})


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

        # Kite format mein convert karo (sirf response message ke liye)
        kite_sym = kite_broker.dhan_sym_to_kite(trad_sym)

        # Rule 6B (2026-07-07): raw kite.place_order() ki jagah smart_order.execute()
        # — test order bhi order_store mein record hota hai, matlab pos_monitor ka
        # SL/EOD use protect karta hai (pehle ek bhoola hua test order raat tak
        # unprotected padha rehta tha), marketable-limit pricing + fill-confirm free.
        import smart_order
        from brokers import get_broker
        _bkr = get_broker("kite")
        res = smart_order.execute(
            "BUY", "NIFTY", sec_id, "NSE_FNO", lot_size, trad_sym, "live", _bkr,
            log=print, tag="KITE_TEST", source="manual", strategy="kite_test",
            instrument="options", broker_name="kite", extra_tags=["KITE_TEST"])
        if not res.get("ok"):
            return jsonify({"ok": False, "error": f"order failed: {res.get('reason','?')}"})
        return jsonify({"ok": True, "order_id": res.get("order_id"), "symbol": kite_sym,
                        "ltp": res.get("price"), "lot_size": lot_size,
                        "msg": f"{kite_sym} {lot_size}qty BUY LIMIT@{res.get('price')} — "
                               f"orderId={res.get('order_id')} (order_store recorded — SL/EOD protected)"})
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
        try:
            import shared_ltp_cache as _slc_pos
        except Exception:
            _slc_pos = None
        for s, sid in missing_sec_id_map.items():
            c = _pos_ltp_cache.get(s)
            if c and (now - c['ts']) < _POS_CACHE_TTL:
                ltp_map[s] = {"ltp": c['ltp'], "qty": None}
                continue
            # ltp_poller keeps every open position warm in shared_ltp_cache
            # (1.5s cycle) — this route previously only checked its own
            # process-local cache and REST-called Dhan on every miss
            shared = _slc_pos.get(str(sid), max_age=6) if (_slc_pos and sid) else None
            if shared:
                ltp_map[s] = {"ltp": shared, "qty": None}
                _pos_ltp_cache[s] = {'ltp': shared, 'ts': now}
            else:
                still_missing[s] = sid
                
        missing_sec_id_map = still_missing

        if missing_sec_id_map:
            # ask the batched poller to keep these warm — after this one REST
            # touch, every subsequent poll is a shared_ltp_cache hit (watchlist
            # symbols and quick-order contracts ride the poller's single call)
            try:
                import ltp_poller as _lp
                _lp.request_watch([(sid, _get_seg(s)) for s, sid in missing_sec_id_map.items()])
            except Exception:
                pass
            _rl.set_context("Dashboard:PosLTP")
            if not _rl.acquire("ltp"):
                # gate busy — poller will have these warm within ~1.5s; frontend
                # re-polls every 3s, so just return what we have this cycle
                return jsonify({"ok": True, "ltp_map": ltp_map, "src": "cache-pending"})
            
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
            # ltp_poller keeps NIFTY/BANKNIFTY spot warm in shared_ltp_cache
            # every 1.5s during market hours — read that before any REST call
            # (this fallback was the #1 'Dashboard:IdxLTP' rate-limit offender)
            try:
                import shared_ltp_cache as _slc_idx
                idx_price = _slc_idx.get(_idx_id, max_age=8) or None
            except Exception:
                pass
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

        # shared_ltp_cache next (ltp_poller keeps open-position contracts warm)
        if sec_ce and not ltp_ce or sec_pe and not ltp_pe:
            try:
                import shared_ltp_cache as _slc_opt
                if sec_ce and not ltp_ce:
                    ltp_ce = _slc_opt.get(str(sec_ce), max_age=8) or None
                if sec_pe and not ltp_pe:
                    ltp_pe = _slc_opt.get(str(sec_pe), max_age=8) or None
            except Exception:
                pass

        missing_ids = [int(s) for s, l in [(sec_ce, ltp_ce), (sec_pe, ltp_pe)] if s and not l]
        if missing_ids:
            try:
                import ltp_poller as _lp
                _lp.request_watch([(str(i), "NSE_FNO") for i in missing_ids])
            except Exception:
                pass
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

@app.route('/mtm-charts')
def mtm_charts_page():
    return render_template('mtm_charts.html')


def _premium_ohlc_path(sec_id, date_str):
    return BASE_DIR / "data" / "trade_ohlc" / f"{sec_id}_{date_str}.json"


def _sec_id_from_order_store(trad_sym, date_str):
    """The REAL historical sec_id this trad_sym was traded with on date_str.
    order_store rows store the actual sec_id used at order time — use it so an
    EXPIRED contract's chart resolves to the right (now-dead) securityId, not
    dhan_master.get_sec_id_for_trad_sym()'s nearest-LIVE-expiry (wrong/None for
    a past contract — the root cause of old premium charts going missing, #5).
    None if not found."""
    try:
        import order_store
        with order_store._lock, order_store._conn() as c:
            row = c.execute(
                "SELECT sec_id FROM orders WHERE trad_sym=? AND substr(ts,1,10)=? "
                "AND sec_id IS NOT NULL AND sec_id!='' ORDER BY id LIMIT 1",
                (trad_sym, date_str)).fetchone()
            if row and row[0]:
                return str(row[0])
    except Exception:
        pass
    return None


def _save_premium_ohlc(sec_id, date_str, bars_by_epoch):
    """Persist option-premium 1-min bars keyed by RAW Dhan epoch (str) → [o,h,l,c],
    so an expired contract's premium chart still renders after Dhan stops serving
    it (#5). Epoch keys are timezone-unambiguous (the +19800 IST-display shift is
    applied only at read time, exactly like the live path) — avoids the double-
    shift class of bug (TRAP #29). Merges with any existing (daemon-written)
    file. Best-effort."""
    try:
        p = _premium_ohlc_path(sec_id, date_str)
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if p.exists():
            try:
                existing = json.loads(p.read_text())
            except Exception:
                existing = {}
        existing.update(bars_by_epoch)
        p.write_text(json.dumps(existing))
    except Exception:
        pass


def _load_premium_ohlc_candles(sec_id, date_str, entry_t="", exit_t=""):
    """Read saved bars → lightweight-charts candles (+entry/exit markers), or None
    if no usable file. Handles BOTH key formats found in data/trade_ohlc/:
      • raw Dhan epoch (UTC) keys — chart write-through format (#5), +19800 transform;
      • HH:MM keys — auto_data_downloader.py's daemon format. These are IST market
        wall-clock and the file is per-date, so the timestamp is NOT ambiguous:
        rebuild the same IST-as-UTC epoch from date_str + HH:MM. (Previously these
        were skipped as 'ambiguous TZ', which silently blanked every daemon-captured
        chart for a contract Dhan later stopped serving — e.g. expired NIFTY weeklies.)"""
    try:
        import datetime as _dt2, calendar as _cal
        p = _premium_ohlc_path(sec_id, date_str)
        if not p.exists():
            return None
        bars = json.loads(p.read_text())
        if not bars:
            return None
        rows = []   # (t_ist, hhmm, [o,h,l,c])
        for k, v in bars.items():
            if not v or len(v) < 4:
                continue
            ks = str(k)
            if ks.lstrip("-").isdigit():
                t_ist = int(ks) + 19800   # raw Dhan epoch (UTC) → IST-as-UTC for the chart
                hhmm = _dt2.datetime.utcfromtimestamp(t_ist).strftime("%H:%M")
            elif ":" in ks:
                try:
                    t_ist = _cal.timegm(_dt2.datetime.strptime(
                        date_str + " " + ks, "%Y-%m-%d %H:%M").timetuple())
                except Exception:
                    continue
                hhmm = ks
            else:
                continue
            rows.append((t_ist, hhmm, v[:4]))
        if not rows:
            return None
        candles, entry_mk, exit_mk = [], None, None
        for t_ist, hhmm, ohlc in sorted(rows, key=lambda r: r[0]):
            o, h, l, c = ohlc
            candles.append({"time": t_ist, "open": round(float(o), 2), "high": round(float(h), 2),
                            "low": round(float(l), 2), "close": round(float(c), 2)})
            if entry_t and hhmm == entry_t and entry_mk is None:
                entry_mk = t_ist
            if exit_t and hhmm == exit_t:
                exit_mk = t_ist
        if not candles:
            return None
        return {"candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk}
    except Exception:
        return None


# ── Task 8 — moving trailing/aggressive SL, faithful to the live monitor ─────
# The live monitor computes an option's SL each tick; nothing stores that SL per
# timestamp. So for the chart we REPLAY the exact same rule over the premium
# candles the chart already loads (reusing risk_gate.target_sl_level for the
# aggressive profile — Rule 6B, no duplicated risk math), and for the live
# Open-Positions row we surface the CURRENT SL the monitor will actually fire on.
def _sl_premium_from_mtm(entry_px, side, qty, sl_mtm):
    """Invert a signed ₹ MTM level (whole position) back to an option premium."""
    if not qty or qty <= 0:
        return None
    return round(entry_px + sl_mtm / qty, 2) if side == "BUY" else round(entry_px - sl_mtm / qty, 2)

def _tsl_peak_from_disk(pid):
    """Confirmed peak MTM the algo-monitor process tracks for an aggressive
    position (data/tsl_state.json) — read the file since this dashboard process
    doesn't own that state (same pattern as api_kill_floor_status)."""
    try:
        raw = json.loads(_TSL_STATE_FILE.read_text())
        st = raw.get("state") or {}
        v = st.get(str(pid)) if str(pid) in st else st.get(pid)
        if v:
            return float(v.get("peak") or 0.0)
    except Exception:
        pass
    return None

def _trade_entry_row(trad_sym, date_str, entry_t=None, strategy=None):
    """Opening leg (id/side/entry_px/qty/tags) for a trad_sym on date_str, skipping
    blocked/rejected rows. When two strategies traded the same contract same-day,
    entry_t (HH:MM) + strategy disambiguate WHICH trade's chart this is — else the
    earliest leg wins (old behaviour). Progressive fallback so a near-miss on the
    exact minute still resolves the right strategy's leg. None if not found."""
    try:
        import order_store
        base = ("SELECT id, side, price, qty, tags FROM orders WHERE trad_sym=? AND substr(ts,1,10)=? "
                "AND (status IS NULL OR status NOT IN ('blocked','rejected'))")
        with order_store._lock, order_store._conn() as c:
            row = None
            # 1) exact: strategy + entry minute
            if strategy and entry_t:
                row = c.execute(base + " AND strategy=? AND substr(ts,12,5)=? ORDER BY id LIMIT 1",
                                (trad_sym, date_str, strategy, entry_t)).fetchone()
            # 2) strategy's earliest leg
            if not row and strategy:
                row = c.execute(base + " AND strategy=? ORDER BY id LIMIT 1",
                                (trad_sym, date_str, strategy)).fetchone()
            # 3) any leg at that minute
            if not row and entry_t:
                row = c.execute(base + " AND substr(ts,12,5)=? ORDER BY id LIMIT 1",
                                (trad_sym, date_str, entry_t)).fetchone()
            # 4) earliest leg (original behaviour)
            if not row:
                row = c.execute(base + " ORDER BY id LIMIT 1", (trad_sym, date_str)).fetchone()
        if not row:
            return None
        return {"id": row[0], "side": row[1], "entry_px": float(row[2] or 0),
                "qty": int(row[3] or 0), "tags": json.loads(row[4] or "[]")}
    except Exception:
        return None

def _reconstruct_sl_series(trad_sym, date_str, sec_id, candles, entry_mk, entry_t=None, strategy=None):
    """Replay the position's SL over the premium candles → stepped line for the
    chart. Handles the two tag/config-driven SL systems the monitor tracks:
    the aggressive Default-TSL profile (AGGR_TSL) and trailing-points (trailing_pt).
    Static SL types keep their existing flat price-line (no series). None on any
    gap so the chart always still renders."""
    try:
        import risk_gate, dhan_master
        er = _trade_entry_row(trad_sym, date_str, entry_t=entry_t, strategy=strategy)
        if not er:
            return None
        side, entry_px, qty, tags = er["side"], er["entry_px"], er["qty"], er["tags"]
        if entry_px <= 0 or qty <= 0 or not candles:
            return None
        bars = [c for c in candles if (entry_mk is None or c["time"] >= entry_mk)]
        if not bars:
            return None
        is_aggr = any(str(t).startswith("AGGR_TSL") for t in tags)
        sl_type = next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("SL_TYPE:")), None)

        def mtm(prem):
            return (prem - entry_px) * qty if side == "BUY" else (entry_px - prem) * qty

        if is_aggr:
            cfg = risk_gate.default_target_sl_config(strategy)   # per-strategy ⚙ values (task 81)
            lotsz = dhan_master.get_lot_size_by_sec_id(sec_id) or qty
            lots = max(1, round(qty / lotsz)) if lotsz else 1
            target_mtm = cfg["target_per_lot"] * lots
            agg_at = target_mtm * cfg["aggressive_pct"] / 100.0
            peak, prev, series = 0.0, None, []
            for c in bars:
                m = mtm(c["close"])
                confirmed = m if prev is None else min(prev, m)   # 2-reading confirmed peak
                if confirmed > peak:
                    peak = confirmed
                prev = m
                sp = _sl_premium_from_mtm(entry_px, side, qty, risk_gate.target_sl_level(peak, cfg, lots))
                if sp is not None:
                    series.append({"time": c["time"], "value": sp, "agg": bool(peak > agg_at)})
            if not series:
                return None
            return {"mode": "aggressive", "series": series,
                    "target": _sl_premium_from_mtm(entry_px, side, qty, target_mtm)}

        if sl_type == "trailing_pt":
            gap = float(next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("SL_VAL:")), 0) or 0)
            step_tag = next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("SL_TRAIL_STEP:")), None)
            step = float(step_tag) if step_tag else resolve_trailing_step(entry_px)
            if step <= 0:
                step = 1.0
            conf, prev, series = None, None, []
            for c in bars:
                px = c["close"]
                if side == "BUY":
                    two = px if prev is None else min(prev, px)   # confirmed high (monitor parity)
                    conf = two if conf is None else max(conf, two)
                    fav = conf - entry_px
                    sl = round((entry_px - gap) + (int(fav / step) * step), 2) if fav > 0 else round(entry_px - gap, 2)
                else:
                    two = px if prev is None else max(prev, px)   # confirmed low
                    conf = two if conf is None else min(conf, two)
                    fav = entry_px - conf
                    sl = round((entry_px + gap) - (int(fav / step) * step), 2) if fav > 0 else round(entry_px + gap, 2)
                prev = px
                series.append({"time": c["time"], "value": sl, "agg": False})
            if not series:
                return None
            return {"mode": "trailing_pt", "series": series, "target": None}
        return None
    except Exception:
        return None

def _live_sl_for_open(p):
    """Current SL/target premium the monitor will fire on for an open position
    (Task 8 live row). Aggressive → tracked peak from tsl_state.json; trailing_pt
    → CONF_MAX/MIN_LTP tags. None when the position uses neither trailing system."""
    try:
        import risk_gate, dhan_master
        tags = p.get("tags") or []
        side = p.get("entry")
        entry_px = float(p.get("entry_price") or 0)
        qty = int(p.get("qty") or 0)
        sec_id = p.get("sec_id")
        if entry_px <= 0 or qty <= 0:
            return None
        if any(str(t).startswith("AGGR_TSL") for t in tags):
            cfg = risk_gate.default_target_sl_config(p.get("strategy"))   # per-strategy ⚙ values (task 81)
            if not cfg.get("enabled"):
                return None   # profile off → monitor won't fire it; don't show a misleading live SL
            lotsz = dhan_master.get_lot_size_by_sec_id(sec_id) or qty
            lots = max(1, round(qty / lotsz)) if lotsz else 1
            peak = _tsl_peak_from_disk(p.get("id")) or 0.0
            agg_at = cfg["target_per_lot"] * lots * cfg["aggressive_pct"] / 100.0
            sl_mtm = risk_gate.target_sl_level(peak, cfg, lots)   # signed ₹ (whole position) if SL hits now
            return {"sl": _sl_premium_from_mtm(entry_px, side, qty, sl_mtm),
                    "sl_rs": round(sl_mtm),   # <0 = max loss, >=0 = locked-in profit
                    "target": _sl_premium_from_mtm(entry_px, side, qty, cfg["target_per_lot"] * lots),
                    "mode": "aggressive", "trailing": True, "aggressive": bool(peak > agg_at)}
        sl_type = next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("SL_TYPE:")), None)
        if sl_type == "trailing_pt":
            gap = float(next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("SL_VAL:")), 0) or 0)
            step_tag = next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("SL_TRAIL_STEP:")), None)
            step = float(step_tag) if step_tag else resolve_trailing_step(entry_px)
            if step <= 0:
                step = 1.0
            if side == "BUY":
                conf = float(next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("CONF_MAX_LTP:")), entry_px) or entry_px)
                fav = conf - entry_px
                sl = round((entry_px - gap) + (int(fav / step) * step), 2) if fav > 0 else round(entry_px - gap, 2)
            else:
                conf = float(next((str(t).split(":", 1)[1] for t in tags if str(t).startswith("CONF_MIN_LTP:")), entry_px) or entry_px)
                fav = entry_px - conf
                sl = round((entry_px + gap) - (int(fav / step) * step), 2) if fav > 0 else round(entry_px + gap, 2)
            sl_rs = round((sl - entry_px) * qty) if side == "BUY" else round((entry_px - sl) * qty)
            return {"sl": sl, "sl_rs": sl_rs, "target": None, "mode": "trailing_pt", "trailing": True, "aggressive": False}
        return None
    except Exception:
        return None

@app.route('/api/trade-chart-data')
def api_trade_chart_data():
    """Option premium 1-min candles for one completed trade + entry/exit marker times.
    Data: Dhan /v2/charts/intraday (raw REST) live, with a disk fallback
    (data/trade_ohlc/) for expired contracts Dhan no longer serves (#5). sec_id
    resolved from order_store's historical row first (correct for expired
    contracts), falling back to dhan_master's nearest-live-expiry."""
    import dhan_master, requests as _req, datetime as _dt
    trad_sym = request.args.get('trad_sym', '').strip()
    date_str = request.args.get('date', '').strip() or \
        (_dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    entry_t  = request.args.get('et', '').strip()   # HH:MM IST
    exit_t   = request.args.get('xt', '').strip()
    tf       = request.args.get('tf', '').strip()
    strategy = request.args.get('strategy', '').strip()   # Task 8 — disambiguate opening leg

    INDEX_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}

    try:
        import universe
        seg = "NSE_FNO"
        inst = "OPTIDX" if trad_sym.split('-')[0] in INDEX_UNDERLYINGS else "OPTSTK"
        # order_store's historical sec_id first (correct for EXPIRED contracts —
        # #5), then dhan_master's nearest-live-expiry as fallback.
        sec_id = _sec_id_from_order_store(trad_sym, date_str) or dhan_master.get_sec_id_for_trad_sym(trad_sym)

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
            
        # Positional / carried-over open trade: entry was on a past day and the
        # position is still open (no exit time) → span entry-date → today so the
        # multi-day premium chart shows, not just the entry day. Same-day / closed
        # trades stay single-day (unchanged). Explicit `to` param overrides.
        _today_str = (_dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        to_date = request.args.get('to', '').strip() or date_str
        if not request.args.get('to') and not exit_t and date_str and date_str < _today_str:
            to_date = _today_str

        token, cid = _creds()
        hdrs = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": date_str, "toDate": to_date}, timeout=12)
        d = r.json()
        if not d.get("open"):
            # Dhan won't serve this contract (expired / non-trading day) — fall
            # back to the on-disk copy (daemon-captured or a prior write-through)
            # so old/expired premium charts still render (#5).
            disk = _load_premium_ohlc_candles(sec_id, date_str, entry_t, exit_t)
            if disk:
                return jsonify({"ok": True, "candles": disk["candles"],
                                "entry_mk": disk["entry_mk"], "exit_mk": disk["exit_mk"],
                                "sl_series": _reconstruct_sl_series(trad_sym, date_str, sec_id, disk["candles"], disk["entry_mk"], entry_t=entry_t, strategy=strategy),
                                "trad_sym": trad_sym, "date": date_str, "source": "disk"})
            return jsonify({"ok": False, "msg": f"{date_str} ka intraday data nahi (non-trading day / expired contract)"})
        candles, entry_mk, exit_mk = [], None, None
        _bars_by_epoch = {}
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
            t_ist = int(ts) + 19800   # +5:30 → chart shows IST (treated as UTC by lightweight-charts)
            hhmm  = _dt.datetime.utcfromtimestamp(int(ts) + 19800).strftime("%H:%M")
            candles.append({"time": t_ist, "open": round(float(o), 2), "high": round(float(h), 2),
                            "low": round(float(l), 2), "close": round(float(c), 2)})
            _bars_by_epoch[str(int(ts))] = [round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2)]
            if entry_t and hhmm == entry_t and entry_mk is None: entry_mk = t_ist
            if exit_t  and hhmm == exit_t:  exit_mk = t_ist
        # Write-through: persist so this contract's chart survives its expiry (#5).
        # Only for a single-day fetch — a multi-day (positional) span would write
        # today's bars into date_str's file (keyed per date) and corrupt it.
        if seg == "NSE_FNO" and _bars_by_epoch and to_date == date_str:
            _save_premium_ohlc(sec_id, date_str, _bars_by_epoch)
        return jsonify({"ok": True, "candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk,
                        "sl_series": _reconstruct_sl_series(trad_sym, date_str, sec_id, candles, entry_mk, entry_t=entry_t, strategy=strategy),
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

        # Match the premium pane: span entry-date → today for an open positional
        # (carried-over) trade so both panes show the same multi-day window.
        _today_str = (_dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        to_date = request.args.get('to', '').strip() or date_str
        if not request.args.get('to') and not exit_t and date_str and date_str < _today_str:
            to_date = _today_str

        token, cid = _creds()
        hdrs = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": date_str, "toDate": to_date}, timeout=12)
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


def _payoff_rows_for_ids(ids):
    """Open order_store rows for the given ids, across a 7-day lookback so
    carried-over positional legs (entered on a prior day) resolve too — same
    lookback /api/orders' carry-over union uses."""
    import order_store
    from datetime import datetime as _d, timedelta as _td, timezone as _tz
    ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
    lb = (ist - _td(days=7)).strftime('%Y-%m-%d')
    rows = order_store.trades_for_range(lb, ist.strftime('%Y-%m-%d')).get('open', [])
    want = {str(i) for i in ids}
    return [r for r in rows if str(r.get('id')) in want]


def _payoff_attach_ltp(rows):
    """Live premium per leg onto the order_store rows. order_store carries no
    price beyond the ENTRY fill — but implied vol (and hence the today-curve /
    POP / margin price) must come from the CURRENT premium, not the entry one.
    Same 3-tier source pos_monitor uses: live feed → shared cache → REST."""
    try:
        import ltp_poller
        ltp_poller.request_watch([(r.get('segment') or 'NSE_FNO', str(r.get('sec_id')))
                                  for r in rows if r.get('sec_id')])
    except Exception:
        pass
    for r in rows:
        sid = r.get('sec_id')
        if not sid:
            continue
        ltp = None
        try:
            import dhan_feed
            q = dhan_feed.get_quote(str(sid), max_age=_FEED_MAX_AGE)
            ltp = float(q.get('ltp') or 0) if q else 0
        except Exception:
            ltp = None
        if not ltp:
            try:
                import shared_ltp_cache
                ltp = float(shared_ltp_cache.get_stale(str(sid), max_age=180) or 0)
            except Exception:
                ltp = 0
        if not ltp:
            try:
                ltp = float(_rest_ltp_fallback(sid, r.get('segment') or 'NSE_FNO') or 0)
            except Exception:
                ltp = 0
        if ltp:
            r['ltp'] = ltp
    return rows


def _payoff_spot(legs):
    """Live underlying spot for the legs' root symbol — shared cache first
    (ltp_poller keeps index spot warm; zero extra Dhan calls), REST fallback."""
    if not legs:
        return None
    root = str(legs[0].get('trad_sym') or '').split('-')[0].strip().upper()
    if not root:
        return None
    try:
        import shared_ltp_cache
        v = shared_ltp_cache.get_index(root, max_age=60)
        if v:
            return float(v)
    except Exception:
        pass
    try:
        if root in _EQ_IDX_SEC:
            sid, seg = _EQ_IDX_SEC[root]
        else:
            import universe
            sid, seg = universe.equity_secid(root), "NSE_EQ"
        if sid:
            return float(_rest_ltp_fallback(sid, seg) or 0) or None
    except Exception:
        pass
    return None


@app.route('/api/position-payoff')
def api_position_payoff():
    """Payoff / zone analytics for one open position GROUP (DISPLAY-ONLY —
    describes an existing position, places nothing, gates nothing).
    Query: ids=<comma-separated order_store ids>. Margin is a separate route
    (5 Kite calls — slow), so the panel renders instantly."""
    try:
        import payoff
        ids = [i for i in (request.args.get('ids') or '').split(',') if i.strip()]
        if not ids:
            return jsonify({"ok": False, "msg": "no ids"})
        rows = _payoff_attach_ltp(_payoff_rows_for_ids(ids))
        if not rows:
            return jsonify({"ok": False, "msg": "no open rows for those ids"})
        spot = _payoff_spot(payoff.build_legs(rows))
        try:
            td = float(request.args.get('target_days')) if request.args.get('target_days') else None
        except Exception:
            td = None
        return jsonify(payoff.analyse(rows, spot, target_days=td))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/position-payoff-margin')
def api_position_payoff_margin():
    """Real HEDGED margin (Kite basket_order_margins, read-only) vs the
    standalone per-leg sum the Margin column shows. Separate route because it
    costs ~5 rate-limited Kite calls."""
    try:
        import payoff
        ids = [i for i in (request.args.get('ids') or '').split(',') if i.strip()]
        rows = _payoff_attach_ltp(_payoff_rows_for_ids(ids))
        if not rows:
            return jsonify({"ok": False, "msg": "no open rows for those ids"})
        return jsonify(payoff.basket_margin(payoff.build_legs(rows)))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


def _leg_closes(sec_id, from_date, to_date):
    """1-min closes {epoch: close} for one option leg over a date range."""
    import requests as _req
    token, cid = _creds()
    _rl.acquire("candle")
    r = _req.post("https://api.dhan.co/v2/charts/intraday",
                  headers={"access-token": token, "client-id": cid,
                           "Content-Type": "application/json"},
                  json={"securityId": str(sec_id), "exchangeSegment": "NSE_FNO",
                        "instrument": "OPTIDX", "expiryCode": 0,
                        "fromDate": from_date, "toDate": to_date}, timeout=15)
    d = r.json()
    if not d.get("close"):
        return {}
    return {int(ts): round(float(c), 2) for ts, c in zip(d["timestamp"], d["close"])}


@app.route('/api/position-legs-series')
def api_position_legs_series():
    """Per-leg premium series + the COMBINED (net-structure) P&L series for a
    position group. Feeds both the 4-up per-leg grid and the combined-premium
    chart. Spans entry-date -> today (positional legs are multi-day).
    Query: ids=<comma ids>."""
    try:
        import payoff
        from datetime import datetime as _d, timedelta as _td, timezone as _tz
        ids = [i for i in (request.args.get('ids') or '').split(',') if i.strip()]
        rows = _payoff_rows_for_ids(ids)
        if not rows:
            return jsonify({"ok": False, "msg": "no open rows for those ids"})
        legs = payoff.build_legs(rows)
        legs = [L for L in legs if L.get('sec_id')]
        if not legs:
            return jsonify({"ok": False, "msg": "legs have no sec_id"})

        ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
        today = ist.strftime('%Y-%m-%d')
        entry_dates = [r.get('entry_date') for r in rows if r.get('entry_date')]
        frm = min(entry_dates) if entry_dates else today

        # Clip to the actual ENTRY moment — Dhan serves the whole entry day from
        # 09:15, but bars before the position existed carry no P&L (a 15:10 entry
        # would otherwise show 6 hours of meaningless "P&L" ahead of itself).
        entry_epoch = 0
        try:
            _ed = min(entry_dates)
            _et = min((r.get('entry_time') or '23:59') for r in rows
                      if (r.get('entry_date') == _ed))
            _dtm = _d.strptime(f"{_ed} {_et}", "%Y-%m-%d %H:%M")
            entry_epoch = int((_dtm - _d(1970, 1, 1)).total_seconds()) - 19800  # IST -> Dhan epoch
        except Exception:
            entry_epoch = 0

        out_legs, bars = [], {}
        for L in legs:
            b = _leg_closes(L['sec_id'], frm, today)
            b = {t: c for t, c in b.items() if t >= entry_epoch}
            bars[L['trad_sym']] = (b, L)
            out_legs.append({
                "trad_sym": L['trad_sym'], "side": L['side'], "opt": L['opt'],
                "strike": L['strike'], "qty": L['qty'], "entry": L['entry'],
                "series": sorted([[t, c] for t, c in b.items()]),
            })

        # entry cash: SELL = credit (+), BUY = debit (-)
        entry_net = sum((L['entry'] if L['side'] == 'SELL' else -L['entry']) for L in legs)
        # combined P&L/unit at each bar both legs have a price for
        common = None
        for b, _L in bars.values():
            ks = set(b.keys())
            common = ks if common is None else (common & ks)
        combined = []
        for ts in sorted(common or []):
            # value if closed now: short leg costs -ltp, long leg returns +ltp
            net = sum((-b[ts] if L['side'] == 'SELL' else b[ts]) for b, L in bars.values())
            combined.append([ts, round(net + entry_net, 2)])

        return jsonify({"ok": True, "legs": out_legs, "combined": combined,
                        "entry_net": round(entry_net, 2), "from": frm, "to": today})
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


# ─────────────────────────────────────────────────────────────────────────────
# PRICE-TRIGGER conditional orders (NIFTY spot level → option order)
# ─────────────────────────────────────────────────────────────────────────────
# Koi broker index (NIFTY spot) pe order nahi deta. Par ltp_poller NIFTY/BANKNIFTY
# spot har ~1.5s se shared_ltp_cache me warm rakhta hai, to ham khud watch karke
# level-cross pe us waqt ki ATM ±offset CE/PE pe order fire kar dete hain —
# execution_gateway (poora RMS) ke through. State: _ops/price_triggers.py
# (day-scoped, EOD auto-cancel). Watch loop = trigger_watch_loop() (monitor_daemon).

def _trigger_spot_now(symbol):
    """Live index spot for arm-time validation: cache-first (zero Dhan call),
    REST fallback (same path as api_manual_order). None if both fail."""
    try:
        import shared_ltp_cache
        s = shared_ltp_cache.get_index(symbol)
        if s:
            return float(s)
    except Exception:
        pass
    try:
        token, cid = _creds()
        _hdrs = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        _idx = {"NIFTY": "13", "BANKNIFTY": "25"}.get(str(symbol).upper(), "13")
        _rl.acquire("ltp")
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                          json={"IDX_I": [int(_idx)]}, headers=_hdrs, timeout=5)
        if r.status_code == 429:
            _rl.note_429()
        return float(r.json()["data"]["IDX_I"][_idx]["last_price"])
    except Exception:
        return None


def _fire_price_trigger(t, spot, log=print):
    """Fire ONE due price-trigger as an option order via execution_gateway
    (full RMS — user's choice). ATM is resolved against the LIVE spot at fire
    time (not arm time). Caller has already claim()'d it (one-shot). Fill uses
    the gateway's own marketable-limit pricing. Returns (ok, msg)."""
    import dhan_master
    import execution_gateway as gw
    symbol = t["symbol"]; opt_type = t["opt_type"]; offset = int(t.get("offset", 0))
    side = t["side"]; lots = int(t["lots"]); mode = t["mode"]; broker = t["broker"]
    sec_id, trad_sym, lot_size = dhan_master.get_option_contract(symbol, spot, opt_type, offset)
    if not sec_id:
        return False, f"Contract not mila ({symbol} {opt_type} off={offset})"
    if not lot_size:
        # never guess a lot size (feedback_no_assumptions) — abort loudly
        return False, f"Lot size resolve nahi hua ({trad_sym}) — order NAHI bheja"
    # Auto per-position SL/target in premium POINTS (default 20pt SL / 30pt target,
    # overridable per-trigger). pos_monitor reads these SL_TYPE:pt / TP_TYPE:pt tags.
    xtags = []
    try:
        _slp = float(t.get("sl_pt", 20) or 0); _tpp = float(t.get("tp_pt", 30) or 0)
        if _slp > 0:
            xtags += ["SL_TYPE:pt", "SL_VAL:%s" % _slp]
        if _tpp > 0:
            xtags += ["TP_TYPE:pt", "TP_VAL:%s" % _tpp]
    except Exception:
        pass
    res = gw.execute_signal(
        "manual_trigger", symbol, side, lots, int(lot_size), sec_id, trad_sym,
        seg="NSE_FNO", mode=mode, broker_name=broker, tag="TRIGGER",
        source="trigger", gate=True, extra_tags=xtags, log=log)
    if res.get("ok"):
        px = res.get("price")
        pxs = f" @ {px:.2f}" if px else ""
        return True, f"[{mode.upper()}/{broker.upper()}] {side} {lots}L {trad_sym}{pxs} ({res.get('status')})"
    return False, f"blocked/failed — {res.get('reason') or res.get('status')}"


# In-process double-fire guard — survives a cross-process file-clobber that
# (very rarely) reverts a persisted fired flag. Lives in whichever process runs
# the watch loop (monitor_daemon). Belt-and-suspenders with claim()'s persist.
_trigger_fired_session = set()


def trigger_watch_loop():
    """Dedicated ~1s loop (started by monitor_daemon next to the poller). Reads
    the already-warm index spot from shared_ltp_cache — ZERO extra Dhan calls —
    and fires due price-triggers. ~1s latency (pos_monitor's 5s is too slow for a
    price trigger). Respects market hours + no-entry time (single source:
    risk_gate.exit_time_config); auto-cancels all pending at market close (EOD)."""
    import time as _t
    try:
        import price_triggers as pt
        import shared_ltp_cache as slc
        import risk_gate as rg
    except Exception as e:
        print(f"[trigger] watch loop imports failed: {e}", flush=True)
        return
    print("[trigger] price-trigger watch loop started (~1s, cache-only spot)", flush=True)
    _eod_cancelled_day = None
    while True:
        try:
            ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            hm = ist.hour * 60 + ist.minute
            today = ist.strftime("%Y-%m-%d")
            market_open = ist.weekday() < 5 and (9 * 60 + 8) <= hm <= (15 * 60 + 35)
            if not market_open:
                if _eod_cancelled_day != today:
                    try:
                        n = pt.cancel_all("eod")
                        if n:
                            print(f"[trigger] market closed — {n} pending trigger(s) auto-cancelled (EOD).", flush=True)
                    except Exception:
                        pass
                    _eod_cancelled_day = today
                _t.sleep(20)
                continue
            _eod_cancelled_day = None

            # no-entry gate — after 3:15 (RMS-configurable) don't fire (leave pending;
            # EOD-cancel at close). Same single source strategies use.
            try:
                _sq, (ne_h, ne_m) = rg.exit_time_config()
            except Exception:
                ne_h, ne_m = 15, 15
            no_entry = hm >= (ne_h * 60 + ne_m)

            spot_by = {}
            for sym in ("NIFTY", "BANKNIFTY"):
                s = slc.get_index(sym)
                if s:
                    spot_by[sym] = float(s)

            if spot_by:
                for t in pt.due_triggers(spot_by):
                    tid = t["id"]
                    if tid in _trigger_fired_session:
                        continue
                    if no_entry:
                        continue  # past no-entry time — skip firing
                    # ONE-SHOT: claim (persist fired) BEFORE placing the order.
                    if not pt.claim(tid):
                        continue  # already claimed / not armed
                    _trigger_fired_session.add(tid)
                    spot = spot_by[t["symbol"]]
                    print(f"[trigger] FIRE {tid}: {t['symbol']} {t['direction']} {t['level']} "
                          f"(spot {spot:.1f}) -> {t['side']} {t['lots']}L {t['opt_type']} "
                          f"off={t['offset']} [{t['mode']}/{t['broker']}]", flush=True)
                    try:
                        ok, msg = _fire_price_trigger(t, spot, log=lambda m: print(m, flush=True))
                    except Exception as fe:
                        ok, msg = False, f"exception: {fe}"
                    pt.set_result(tid, "fired" if ok else "fire_failed", msg)
                    print(f"[trigger] {tid} result: {'OK' if ok else 'FAIL'} — {msg}", flush=True)
                    try:
                        import notify
                        (notify.info if ok else notify.error)(
                            f"Price trigger {'fired' if ok else 'FAILED'}: "
                            f"{t['symbol']} {t['direction']} {t['level']:.0f} → {msg}",
                            source="trigger")
                    except Exception:
                        pass
            _t.sleep(1.0)
        except Exception as e:
            print(f"[trigger] watch loop error: {e}", flush=True)
            _t.sleep(3)


@app.route('/api/triggers', methods=['GET'])
def api_triggers_list():
    """Armed/fired price-triggers + live spot + distance-to-level for the UI."""
    try:
        import price_triggers as pt
        triggers = pt.list_triggers()
        want_sym = (request.args.get("symbol") or "NIFTY").upper()
        spot_by = {}
        for sym in set([t.get("symbol") for t in triggers] + ["NIFTY", want_sym]):
            if sym:
                spot_by[sym] = _trigger_spot_now(sym)
        out = []
        for t in triggers:
            spot = spot_by.get(t.get("symbol"))
            dist = None
            if spot is not None:
                dist = round(float(t["level"]) - spot, 1)  # +ve = above spot
            out.append({**t, "dist": dist})
        return jsonify({"ok": True, "triggers": out, "spot": spot_by})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "triggers": []})


@app.route('/api/triggers', methods=['POST'])
def api_triggers_add():
    """Arm a new price-trigger. Validates + rejects an instantly-true condition."""
    try:
        import price_triggers as pt
        data = request.get_json() or {}
        symbol = str(data.get("symbol", "NIFTY")).upper()
        arm_spot = _trigger_spot_now(symbol)
        if arm_spot is None:
            return jsonify({"ok": False, "msg": f"{symbol} spot abhi nahi mila — thodi der baad try karo"})
        ok, res = pt.add_trigger(data, arm_spot)
        if not ok:
            return jsonify({"ok": False, "msg": res})
        d = res["direction"]
        return jsonify({"ok": True, "trigger": res,
                        "msg": f"Armed: {symbol} {d} {res['level']:.0f} → "
                               f"{res['side']} {res['lots']}L {res['opt_type']} "
                               f"(spot {arm_spot:.0f})"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/triggers/<tid>', methods=['DELETE'])
def api_triggers_delete(tid):
    """Cancel/remove one trigger (armed or a fired row from the list)."""
    try:
        import price_triggers as pt
        found = pt.remove_trigger(tid)
        return jsonify({"ok": found, "msg": "removed" if found else "not found"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


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
    want_strat = request.args.get("strat") or ""   # task 73: per-strategy line ("__all" default)

    def _build_strat_series(sd_list):
        """From per-point {key:[real,unreal]} dicts (snapshot v[4]) → aligned
        {key:{r:[...],u:[...]}} for '__all' + the requested strategy only (keeps
        the payload small — one strategy at a time)."""
        if not any(isinstance(sd, dict) and sd for sd in sd_list):
            return {}   # pre-v4 date (no per-strategy snapshot) → frontend reconstructs
        keys = {"__all"}
        if want_strat and want_strat != "__all":
            keys.add(want_strat)
        out = {}
        for k in keys:
            r, u = [], []
            for sd in sd_list:
                v = (sd.get(k) if isinstance(sd, dict) else None) or [0, 0]
                r.append(v[0]); u.append(v[1])
            out[k] = {"r": r, "u": u}
        return out

    try:
        # Graph's dashed floor line now mirrors the account-level KILL-FLOOR's
        # own gap_rs (2026-07-02 redesign replaced the old aggregate trailing
        # lock these two keys used to read) — same "peak − gap" shape, just
        # sourced from the system that's actually live.
        import risk_gate as _rg
        _kfc = _rg.kill_floor_config()
        lock_rs          = _kfc["gap_rs"] if _kfc["enabled"] else None
        lock_pct         = None
        profit_target_rs = (_rg._risk_cfg().get("global") or {}).get("profit_target_rs")
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
        # Keep the raw daemon point aligned with its normalized form so v[4]
        # (per-strategy dict) can be extracted for strat_series (task 73).
        _kept = [(p, _safe_norm(p)) for p in daemon_pts]
        _kept = [(rp, n) for (rp, n) in _kept if n is not None]
        mkt_pts = [n for (_, n) in _kept]

        if mkt_pts:
            pts = mkt_pts
            _sd = [(rp[4] if len(rp) > 4 and isinstance(rp[4], dict) else {}) for (rp, _) in _kept]
            # Prepend 09:15 anchor only if daemon started DURING market hours (not long after open)
            # If daemon started within 30 min of open, anchor at 09:15; otherwise skip anchor
            # (avoids fake flat-line when daemon started mid-day or later)
            first_min = _to_min(pts[0][0])
            if first_min > MARKET_OPEN and first_min <= MARKET_OPEN + 30:
                pts = [["09:15", 0.0, 0.0, 0.0]] + pts
                _sd = [{}] + _sd
            elif first_min == MARKET_OPEN:
                pass  # already starts at open
            # else: daemon started mid-day — don't anchor at 09:15, show from where data begins

            # Extend to 15:30 for past dates / closed market
            now_hm = _ist_now.strftime("%H:%M")
            end_hm = "15:30" if (not is_today or now_hm >= "15:30") else now_hm
            if pts[-1][0] < end_hm:
                last = pts[-1]
                pts = pts + [[end_hm, last[1], last[2], last[3]]]
                _sd = _sd + [_sd[-1] if _sd else {}]   # carry final per-strategy values
            return jsonify({"data": pts, "entries": entries,
                            "lock_pct": lock_pct, "lock_rs": lock_rs,
                            "profit_target_rs": profit_target_rs,
                            "strat_series": _build_strat_series(_sd)})
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
                            "profit_target_rs": profit_target_rs,
                            "strat_series": {}})  # no snapshot → frontend reconstructs per-strategy
    except Exception:
        pass

    return jsonify({"data": [], "entries": [], "lock_pct": lock_pct,
                    "lock_rs": lock_rs, "profit_target_rs": profit_target_rs,
                    "strat_series": {}})


@app.route('/api/margin-history')
def api_margin_history():
    """Day margin-utilization timeline (task 74) — reconstructed from order_store
    entry/exit times, so it works for any date without touching the money loop.
    Each position holds ₹ margin from its entry_time until its exit_time (open
    positions → until 'now'): BUY leg = premium notional (qty×entry_price), SELL
    leg = executing-broker real margin (risk_gate._leg_capital, cached; falls back
    to the multiplier estimate for expired/failed lookups). Split buy vs sell,
    filtered by ?mode=all|paper|live (position's own mode).
    Response: {times:[HH:MM...], buy:[₹...], sell:[₹...], peak:₹}."""
    from datetime import timedelta as _td
    _now = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
    req_date = request.args.get("date") or _now.strftime("%Y-%m-%d")
    mode = (request.args.get("mode") or "all").lower()   # all | paper | live
    is_today = (req_date == _now.strftime("%Y-%m-%d"))

    def _to_min(hm):
        try:
            h, m = str(hm).split(":")[:2]
            return int(h) * 60 + int(m)
        except Exception:
            return None

    OPEN_M, CLOSE_M = 9 * 60 + 15, 15 * 60 + 30
    now_m = _to_min(_now.strftime("%H:%M")) or CLOSE_M
    end_m = min(CLOSE_M, now_m) if is_today else CLOSE_M

    positions = []   # (start_min, end_min, side, margin_rs)
    try:
        import order_store
        import risk_gate as _rg
        data = order_store.trades_for(req_date)
        rows = list(data.get("details", [])) + list(data.get("open", []))
        for r in rows:
            if "CAPITAL_BLOCKED" in (r.get("tags") or []):
                continue
            pmode = str(r.get("mode") or "paper").lower()
            if mode != "all" and pmode != mode:
                continue
            s_m = _to_min(r.get("entry_time"))
            if s_m is None:
                continue
            e_raw = _to_min(r.get("exit_time"))
            is_open = e_raw is None    # still open ("—") → held to end of window
            e_m = end_m if is_open else e_raw
            e_m = min(max(e_m, s_m), CLOSE_M)
            side = str(r.get("entry") or "").upper()
            try:
                mgn = _rg._leg_capital(r) if side == "SELL" \
                    else float(r.get("qty") or 0) * float(r.get("entry_price") or 0)
            except Exception:
                mgn = float(r.get("qty") or 0) * float(r.get("entry_price") or 0)
            positions.append((s_m, e_m, side, float(mgn or 0), is_open))
    except Exception:
        positions = []

    # Event-driven step timeline: recompute the in-use sum at every entry/exit
    # boundary (exact step function, no minute-grid rounding).
    tset = {OPEN_M, end_m}
    for (s_m, e_m, _s, _mg, _op) in positions:
        tset.add(s_m); tset.add(e_m)
    tpoints = sorted(t for t in tset if OPEN_M <= t <= CLOSE_M)
    # A closed leg drops OUT at its exit (exclusive: s_m<=t<e_m); a still-open leg
    # is held THROUGH the terminal 'now' point (inclusive: s_m<=t<=e_m) so the line
    # continues at the current margin-in-use instead of collapsing to 0 at 'now'.
    def _active(s_m, e_m, op, t):
        return (s_m <= t <= e_m) if op else (s_m <= t < e_m)
    times, buy, sell = [], [], []
    peak = 0.0
    for t in tpoints:
        b = sum(mg for (s_m, e_m, sd, mg, op) in positions if sd == "BUY" and _active(s_m, e_m, op, t))
        s = sum(mg for (s_m, e_m, sd, mg, op) in positions if sd == "SELL" and _active(s_m, e_m, op, t))
        times.append(f"{t // 60:02d}:{t % 60:02d}")
        buy.append(round(b, 2)); sell.append(round(s, 2))
        peak = max(peak, b + s)
    return jsonify({"times": times, "buy": buy, "sell": sell, "peak": round(peak, 2)})


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

    # Pre-seed: the lookup below sits in a try/except-pass, and this_leg is read
    # again AFTER it for the single-leg close — an exception before the assignment
    # would leave the name unbound and NameError the whole route (TRAP #56 shape).
    this_leg = None
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
                                              leg.get('source', 'manual'), leg.get('strategy', ''),
                                              leg.get('broker', ''))
                    r['sym'] = leg['sym']
                    results.append(r)
                all_ok = all(r.get('ok') for r in results)
                return jsonify({'ok': all_ok,
                    'msg': '[GROUP-CLOSE — hedge pair] ' + '; '.join(r.get('msg', '') for r in results),
                    'legs': results})
    except Exception:
        pass  # best-effort group lookup — fall through to single-leg close on any failure

    # broker of the OPEN leg — the row is already in hand above; without it this
    # falls back to 'dhan' and can phantom-close a Kite leg (see _close_position_impl).
    return jsonify(_close_position_impl(t_sym, entry_side, qty_shares, mode, src_in, strat_in,
                                        (this_leg or {}).get('broker', '')))


def _close_position_impl(t_sym, entry_side, qty_shares, mode, src_in, strat_in,
                         broker_in=''):
    """Shared close-one-leg logic — used by /api/close-position and (looped per
    leg) by /api/close-position-group. Returns the same {ok, msg} dict shape
    the route used to jsonify directly.

    broker_in — the OPEN leg's own broker, straight off its order_store row.
    Until 2026-07-16 this whole function was hardcoded to 'dhan' while the algo
    had moved to Kite (_risk.global.default_broker='kite', TRAP #90), and the
    caller was already holding the leg's real broker without passing it. Both
    branches were wrong for a Kite-held leg:

      - Dhan answers with a position book that has no such symbol -> is_flat_fresh
        says "flat" (a confident answer, not an error, so its fail-open guard
        never engages) -> we mark_externally_closed() and send NO order. The real
        Zerodha position stays open, and being externally_closed it also drops out
        of pos_monitor's 3:15 squareoff — unprotected overnight.
      - Dhan errors -> "not flat" -> we POST a real order to api.dhan.co for a leg
        that lives on Kite: a NEW naked Dhan position, Kite leg still open. That
        also breaks the standing Dhan-hands-off rule (TRAP #97 closed the
        auto-adopt path; this manual one was missed).

    The live order now goes through smart_order.execute(is_exit=True) exactly as
    pos_monitor's own _do_squareoff does — which is the only broker-agnostic way
    to place it anyway (the old body was a Dhan REST payload, and MARKET at that,
    which Zerodha rejects outright on stock options). Also buys rate-limiting,
    async fill-confirm, exit order-chasing and order_store recording.
    """
    close_side = 'SELL' if entry_side == 'BUY' else 'BUY'
    broker_name = (broker_in or 'dhan').lower()

    def _veto_mark():
        # User closed this via the app → respect the intent: strategy/webhook must
        # not re-open (strategy, symbol) today (Fix B, 2026-07-20). Only called on
        # a CONFIRMED close below — never on a failed/rate-limited attempt.
        try:
            import risk_gate as _rg_v
            _rg_v.mark_manual_closed(strat_in, t_sym.split('-')[0])
        except Exception:
            pass

    try:
        # range_trader was imported here only for hdrs() on the raw Dhan order body
        # — that body is gone (smart_order places the order now).
        import requests as _req, time as _time
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
                    mode=m, broker=broker_name, symbol=t_sym.split('-')[0], instrument='options', trad_sym=t_sym,
                    sec_id=sec_id, segment='NSE_FNO', broker_order_id=oid,
                    correlation_id=f'CLOSE_{t_sym}_{ts}', status=status_,
                    tags=['MANUAL_CLOSE'])
            except Exception:
                pass

        if mode == 'paper':
            _write_log('PAPER')
            _record_close('paper', 'paper')
            _veto_mark()
            return {'ok': True, 'msg': f'[PAPER] CLOSE {close_side} {qty_shares} {t_sym} @ {option_ltp:.2f}'}

        if not sec_id:
            return {'ok': False, 'msg': f'Security ID not found for {t_sym}'}

        # P6 audit fix (2026-07-02): this manual-close button placed a live
        # order straight from the clicked row's data — no fresh broker
        # flat-check. A double-click, a stale/duplicate-tab UI, or this click
        # racing pos_monitor_loop's own SL/TP/EOD squareoff on the same leg
        # could all fire a closing order on an already-flat position, opening
        # a phantom opposite one instead of doing nothing.
        try:
            import broker_sync as _bs_close
            # broker_name, NOT 'dhan': asking the wrong broker returns a confident
            # "flat" for a leg it simply never held — see this function's docstring.
            if _bs_close.is_flat_fresh(broker_name, t_sym, str(sec_id)):
                try:
                    import order_store as _os_close
                    _today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
                    _leg = next((p for p in _os_close.trades_for(_today).get('open', [])
                                 if p.get('sym') == t_sym and p.get('entry') == entry_side), None)
                    if _leg:
                        _os_close.mark_externally_closed(_leg.get('id'))
                except Exception:
                    pass
                _veto_mark()
                return {'ok': True, 'msg': f'{t_sym} already FLAT at broker (manually closed / SL hit elsewhere) — no order sent, marked closed'}
        except Exception as _fce:
            print(f"[close-position] pre-close flat-check failed ({_fce}) — proceeding (fail-open)", flush=True)

        # Same path pos_monitor's _do_squareoff takes — broker resolved from the
        # leg, LIMIT + exit chase, order_store written by smart_order itself.
        import smart_order
        from brokers import get_broker
        broker = get_broker(broker_name)
        res = smart_order.execute(
            close_side, t_sym, sec_id, 'NSE_FNO', qty_shares, t_sym,
            'live', broker, log=print, tag='MANUAL-CLOSE',
            source=src_in, strategy=strat_in, instrument='options',
            broker_name=broker_name, extra_tags=['MANUAL_CLOSE'],
            is_exit=True,
        )
        if not res.get('ok'):
            return {'ok': False, 'msg':
                    f"{broker_name} ne close order pura nahi kiya ({res.get('reason')}) — "
                    f"position band NAHI hui, {broker_name} pe verify karo"}
        # smart_order already recorded the fill — double-record mat karo.
        _veto_mark()
        return {'ok': True, 'msg': f"[LIVE] CLOSE {close_side} {qty_shares} {t_sym} "
                                   f"@ {res.get('price')} ({res.get('status')}) via {broker_name}"}
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
                                  leg.get('source', 'manual'), leg.get('strategy', ''),
                                  leg.get('broker', ''))
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


# /api/debug-order DELETED (2026-07-16 audit). It was a one-off DH-905 probe from
# June that outlived its investigation: zero callers anywhere, but it had no
# methods= (so GET), no risk gate, and fired a real Dhan MARKET SELL of a
# hardcoded, long-since-expired contract (NIFTY-Jun2026-24100-CE, secId 56376,
# qty 65) — any authenticated browser navigation, prefetch or history-restore of
# that URL was one real order attempt. It also returned the last 10 chars of the
# JWT. Nothing to keep: health_check.py covers the IPv4/token/LTP diagnostics it
# was written for, without placing an order.

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

def _parse_cfg_text(text):
    """'key = value' lines → dict (same convention as the Parameter Modal's
    config textarea: # / // comments skipped, true/false/number coerced,
    comma-separated values stay a plain string — the frontend decides whether
    a comma list means an optimizer sweep)."""
    out = {}
    for line in (text or '').splitlines():
        l = line.strip()
        if not l or l.startswith('#') or l.startswith('//'):
            continue
        if '=' not in l:
            continue
        key, val = l.split('=', 1)
        key, val = key.strip(), val.strip()
        if not key:
            continue
        if val.lower() == 'true':
            out[key] = True
        elif val.lower() == 'false':
            out[key] = False
        else:
            try:
                out[key] = float(val) if '.' in val else int(val)
            except ValueError:
                out[key] = val
    return out

@app.route('/api/pine/attach-config/<int:version>', methods=['POST'])
def api_pine_attach_config(version):
    """Attach/update the config text stored WITH a saved script version, and
    (for runnable user scripts) refresh that script's nifty_config defaults."""
    import json as _json
    cfg_text = (request.json.get('config') or '').strip()
    ver_file = BASE_DIR / '_PINE' / 'versions.json'
    if not ver_file.exists():
        return jsonify({"error": "No versions"}), 404
    versions = _json.loads(ver_file.read_text())
    v = next((x for x in versions if x['version'] == version), None)
    if not v:
        return jsonify({"error": "Version not found"}), 404
    v['attached_cfg'] = cfg_text
    ver_file.write_text(_json.dumps(versions, indent=2, ensure_ascii=False))
    sid = v.get('script_id')
    if sid and cfg_text:
        try:
            all_cfg = _json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
            if sid in all_cfg:
                parsed = _parse_cfg_text(cfg_text)
                for k in ("_module", "_lang", "active"):
                    parsed.pop(k, None)
                all_cfg[sid].update(parsed)
                TC_FILE.write_text(_json.dumps(all_cfg, indent=2, ensure_ascii=False))
        except Exception:
            pass
    return jsonify({"ok": True})

# ── Backtest Parameter-Modal presets (per-strategy, full modal state) ──
BT_PRESETS_FILE = BASE_DIR / 'data' / 'bt_presets.json'

def _load_bt_presets():
    try:
        return json.loads(BT_PRESETS_FILE.read_text()) if BT_PRESETS_FILE.exists() else []
    except Exception:
        return []

@app.route('/api/backtest/presets', methods=['GET'])
def api_bt_presets_list():
    strat = request.args.get('strategy', '')
    presets = _load_bt_presets()
    if strat:
        presets = [p for p in presets if p.get('strategy') == strat]
    return jsonify(presets)

@app.route('/api/backtest/presets', methods=['POST'])
def api_bt_presets_save():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    strat = (body.get('strategy') or '').strip()
    if not name or not strat:
        return jsonify({"error": "name + strategy required"}), 400
    presets = _load_bt_presets()
    entry = {"id": uuid.uuid4().hex[:10], "strategy": strat, "name": name,
             "state": body.get('state') or {},
             "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M")}
    presets.append(entry)
    BT_PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BT_PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False))
    return jsonify(entry)

@app.route('/api/backtest/presets/<pid>', methods=['DELETE'])
def api_bt_presets_delete(pid):
    presets = [p for p in _load_bt_presets() if p.get('id') != pid]
    BT_PRESETS_FILE.write_text(json.dumps(presets, indent=2, ensure_ascii=False))
    return jsonify({"ok": True})

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
    # AI-workflow: a strategy arrives as TWO pastes (code + config). The raw
    # config text stays attached to this exact version (versions.json) so the
    # Parameter Modal can always show/load the version's own defaults.
    attached_cfg = (request.json.get('config') or '').strip()
    if attached_cfg:
        entry["attached_cfg"] = attached_cfg

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
            py_rel = f"strategies/backtest/{script_id}.py"
            (BASE_DIR / py_rel).write_text(code, encoding='utf-8')
            entry["py_file"] = py_rel
            hdr = _script_header(code)
            cfg_entry = {"_module": f"strategies.backtest.{script_id}", "_lang": "python",
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
        # Attached config's key=value lines become this script's saved defaults
        # (what the Parameter Modal / backtest run read from nifty_config) —
        # engine-routing markers stay protected, and a pasted config can never
        # silently activate a strategy live.
        if attached_cfg:
            parsed_cfg = _parse_cfg_text(attached_cfg)
            for k in ("_module", "_lang", "active"):
                parsed_cfg.pop(k, None)
            cfg_entry.update(parsed_cfg)
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
        stem = py.rsplit('/', 1)[-1].replace('.py', '')  # basename (handles strategies/ and strategies/backtest/)
        if stem and (stem == sid or strat_type in stem or stem in strat_type):
            match = v
            break
    if not match:
        return f'No Pine version mapped to strategy "{strat_label(sid)}"', 404

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
        for pyf in (BASE_DIR / 'strategies' / 'backtest' / f'{sid}.py',
                    BASE_DIR / 'strategies' / f'{sid}.py'):   # backtest/ (new) + legacy flat
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
        # derive strategy id: "strategies/backtest/rsi_v1.py" → "rsi_v1"
        sid = py.rsplit('/', 1)[-1].replace('.py', '')
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
        sid = p["strat_type"]              # full variant id e.g. "ARS_CHAIN_V1"
        grid = p["grid"]
        date_from = p.get("date_from", "")
        date_to = p.get("date_to", "")
        symbols = p.get("symbols", "NIFTY")
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    # The optimizer's `run_backtest(strat_type, ...)` dispatches off `_RUNNERS`,
    # which is keyed by BASE type (range/rsi/ema/vwap/bb) — not the full variant
    # id. Single-Backtest (`api_backtest_run`) already normalizes via `_base()`;
    # the optimizer path never did, so ARS_CHAIN_V1/ema_v1 (bases not present as
    # explicit _RUNNERS keys) errored "unsupported strategy type" on EVERY combo
    # → 0 results. Normalize the same way here, and thread a custom user-script's
    # `_module`/`_lang` markers into the grid so each combo's cfg can still
    # dispatch to `_run_custom` (grid values must be lists — hence [value]).
    strat_type = _base(sid)
    try:
        cfg_file = STRATEGIES.get(strat_type, {}).get("cfg", TC_FILE)
        all_cfg = json.loads(Path(cfg_file).read_text()) if Path(cfg_file).exists() else {}
        disk_cfg = all_cfg.get(sid) or {}
        if not disk_cfg and Path(cfg_file) != TC_FILE:
            disk_cfg = (json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}).get(sid, {})
    except Exception:
        disk_cfg = {}
    for _mk in ("_module", "_lang"):
        if disk_cfg.get(_mk) is not None and _mk not in grid:
            grid[_mk] = [disk_cfg[_mk]]

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
        return jsonify({"error": f"backtest not supported for strategy '{strat_label(sid)}'"}), 400

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
            # per-symbol summary breakdown (multi-symbol runs) — combined stats
            # stay in `summary`, this powers the expandable per-symbol sub-rows
            "per_symbol": body.get("per_symbol"),
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

@app.route('/api/deploy-variation', methods=['POST'])
def api_deploy_variation():
    """Saved Result "🚀 Deploy" → create a NEW named strategy variation the
    user's chosen name se, so it shows up in the dashboard grid + Logs tab under
    that name (not the generic base id). The new config key carries the base
    prefix (rsi_/ema_/range_/ARS_/universe_) so `_base()` routes it to the right
    live script; the rest of the key is a slug of the user's name.
    Always creates it `active:false, mode:paper` — deploy never auto-runs and
    never goes live; the user starts it (and picks Paper/Live) themselves.
    """
    import re as _re
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    strategy = (body.get('strategy') or '').strip()
    cfg = body.get('cfg') or {}
    symbols = body.get('symbols') or []
    if not name or not strategy:
        return jsonify({"error": "name + strategy required"}), 400
    base = _base(strategy)
    if STRATEGIES.get(base) is None:
        return jsonify({"error": f"'{strat_label(strategy)}' live deploy nahi hoti — backtest-only strategy (koi live trader script nahi)."}), 400
    prefix = strategy.split('_')[0]   # rsi / ema / range / ARS / universe — casing preserve (_base ALIAS match)
    slug = _re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_') or 'run'
    newkey = f"{prefix}_{slug}"
    try:
        all_cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        all_cfg = {}
    entry = {k: v for k, v in cfg.items() if k not in ('_module', '_lang')}
    if symbols:
        entry['symbols'] = ','.join(str(s) for s in symbols)
        entry.pop('symbol', None)
    entry['active'] = False   # deploy never auto-runs
    entry['mode'] = 'paper'   # and never lands live on its own
    # merge over any existing same-name variation (re-deploy = same intent)
    all_cfg[newkey] = {**all_cfg.get(newkey, {}), **entry}
    TC_FILE.write_text(json.dumps(all_cfg, indent=2, ensure_ascii=False))
    return jsonify({"ok": True, "key": newkey})

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


# ── Notification centre (2026-07-16) ──────────────────────────────────────────
# downloader_alert.json = CURRENT state (alerts aate-jaate rehte hain, aur ek ✕
# unhe hamesha ke liye dismiss kar deta tha). notifications.jsonl = HISTORY (kabhi
# delete nahi hoti). Ye ingest dono ko jodta hai: har naya alert ek permanent
# notification ban jaata hai, chahe banner ko baad me dismiss kar diya jaaye.
_ALERT_LEVEL_KEYS = {
    "untracked_position": "error",
    "naked_leg": "error",
    "kill_floor": "error",
    "stale_feed": "warn",
    # health_check --report writes these as dict alerts with stable keys, so a
    # login can clear exactly ONE of them (see _clear_alerts). Both tokens are
    # 'error': a dead Dhan token stops data AND orders, a dead Kite token stops
    # every LIVE order (orders route through Kite).
    "token:dhan": "error",
    "token:kite": "error",
    "health:strategies": "warn",
}


def _clear_alerts(*keys, substr=None):
    """Drop matching alerts from downloader_alert.json.

    That file is current STATE — an alert lives in it while the problem lives.
    Removing an entry is therefore how a problem gets marked fixed: the next
    _ingest_downloader_alerts() poll sees the key gone and calls notify.resolve(),
    which turns the bell row into "✓ fixed" without deleting any history.

    keys   — exact dict-alert keys ("token:kite"). The robust path: a key can
             only ever clear its own alert.
    substr — case-insensitive substring, ONLY for legacy plain-string alerts
             (auto_data_downloader still writes those). Kept narrow on purpose:
             the old inline filter here matched 'token expire', which also
             matches "Kite token EXPIRED" — so saving the DHAN token silently
             marked the KITE problem fixed. It also called a.lower() on entries
             that can be dicts, throwing straight into an `except: pass` so it
             quietly cleared nothing at all.

    Returns how many alerts were dropped.
    """
    f = BASE_DIR / "data" / "downloader_alert.json"
    if not f.exists():
        return 0
    try:
        alerts = json.loads(f.read_text())
        if not isinstance(alerts, list):
            return 0
        kset = {str(k) for k in keys}

        def _drop(a):
            if isinstance(a, dict):
                return str(a.get("key") or "") in kset
            if isinstance(a, str):
                return bool(substr) and substr.lower() in a.lower()
            return False

        kept = [a for a in alerts if not _drop(a)]
        n = len(alerts) - len(kept)
        if n:
            f.write_text(json.dumps(kept, ensure_ascii=False))
        return n
    except Exception as e:
        print(f"[alerts] clear fail ({keys}): {e}", flush=True)
        return 0


_ALERT_SEEN_FILE = BASE_DIR / "data" / "alert_ingest_seen.json"


def _ingest_downloader_alerts():
    """downloader_alert.json ko notification history me mirror karo — har alert ke
    har APPEARANCE pe THEEK EK BAAR.

    Ye file current STATE hai: ek alert jab tak zinda hai file me pada rehta hai.
    Isliye "har poll pe push kar do, notify.push ka dedup sambhal lega" GALAT hai —
    notify ka dedup time-window (5 min) wala hai, to har 5 min baad wahi purana
    alert nayi unread row banata → toast + beep har 5 min, wahi spam jise rokna tha.

    Sahi model: ek `seen` set disk pe rakho. File me naya key aaya → tabhi push.
    Key file se HAT gayi (alert resolve ho gaya) → seen se bhi hatao, taaki agar
    wo problem dobara ho to naya notification phir se aaye (chup na rahe).
    """
    try:
        alert_file = BASE_DIR / "data" / "downloader_alert.json"
        try:
            seen = set(json.loads(_ALERT_SEEN_FILE.read_text()))
        except Exception:
            seen = set()

        if not alert_file.exists():
            for k in seen:
                notify.resolve(k)          # saare alerts gaye = sab theek ho gaya
            _ALERT_SEEN_FILE.write_text("[]")
            return
        alerts = json.loads(alert_file.read_text())
        if not isinstance(alerts, list):
            return

        current = set()
        for a in alerts:
            if isinstance(a, dict):
                key = a.get("key") or a.get("msg") or json.dumps(a, ensure_ascii=False)
                msg = a.get("msg") or json.dumps(a, ensure_ascii=False)
                lvl = _ALERT_LEVEL_KEYS.get(a.get("key") or "", "error")
            elif isinstance(a, str):
                # Plain-string alerts (downloader / health_check / rate-limit-verify).
                # Inke writers pehle se emoji me severity bata dete hain — 🔴 = serious
                # (jaise "Dhan token expire ho gaya", jispe data aana hi band ho jaata
                # hai), ⚠️ = warning. Pehle sab ko "warn" maan liya jaata tha, jisse
                # token-expiry ek data-gap notice jitna hi halka dikhta tha.
                key, msg = a, a
                lvl = "error" if a.lstrip().startswith("🔴") else "warn"
            else:
                continue
            key = str(key)[:200]
            current.add(key)
            if key not in seen:
                notify.push(msg, lvl, key=key, source="alert")

        # File se gaayab = problem khatam. Uska notification apne aap read +
        # "fixed" ho jaata hai — ek theek ho chuke error ka laal badge bethe
        # rehna bell pe bharosa utna hi todta hai jitna error ka chhup jaana.
        # Record delete nahi hota; dobara hua to push() usay phir se jagayega.
        for gone in (seen - current):
            notify.resolve(gone)

        if current != seen:
            _ALERT_SEEN_FILE.write_text(json.dumps(sorted(current)))
    except Exception as e:
        print(f"[notify] alert ingest fail: {e}", flush=True)


def _error_watch_loop():
    """App ke har error ko 🔔 tak laane wala loop — strategy logs, mari hui
    strategies, aur gire hue services.

    Dashboard ke andar (monitor_daemon me nahi) JAAN-BOOJH KAR: (a) bell isi
    process se serve hoti hai, to ye gira to bell waise bhi nahi dikhegi — koi
    naya blind spot nahi banta; (b) monitor = SL/TP/squareoff ka safety process,
    usme log-scanning ka load/risk daalna galat hai. Restart-safe: sab state
    (offsets) disk pe hai, to deploy ke 3 sec me kuch chhootta nahi.
    """
    import error_watch
    while True:
        try:
            cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
            # Sirf wahi strategies jinka sach me process hota hai — webhook_v1
            # dashboard ke andar chalta hai, uska PID kabhi nahi milega (wahi
            # filter jo scheduler khud use karta hai).
            actives = [k for k, v in cfg.items()
                       if isinstance(v, dict) and v.get("active") and _base(k) in STRATEGIES]
            error_watch.scan_once(get_pid=get_pid, actives=actives)
        except Exception as e:
            print(f"[error_watch] loop error: {e}", flush=True)
        _time.sleep(20)


@app.route('/api/notifications')
def api_notifications():
    """History + unread count. ?after=<id> = sirf naye (frontend ka incremental poll)."""
    try:
        after = int(request.args.get('after') or 0)
    except Exception:
        after = 0
    _ingest_downloader_alerts()
    return jsonify(notify.listing(after=after))


@app.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    """Mark read. {"ids": [...]} ya {} = sab. Record DELETE nahi hota — sirf
    read flag lagta hai, taaki history hamesha bani rahe."""
    body = request.get_json(silent=True) or {}
    ids = body.get('ids')
    notify.mark_read(ids if ids else None)
    return jsonify({"ok": True})


@app.route('/api/notifications/clear', methods=['POST'])
def api_notifications_clear():
    """Poori history wipe — sirf explicit user action se (banner ka ✕ nahi)."""
    notify.clear()
    return jsonify({"ok": True})


@app.route('/api/notify', methods=['POST'])
def api_notify():
    """Frontend se error push karne ka raasta (window.onerror, failed apiFetch).
    Browser-side crash bhi ab silently nahi marta."""
    body = request.get_json(silent=True) or {}
    msg = str(body.get('msg') or '').strip()
    if not msg:
        return jsonify({"ok": False, "msg": "msg required"}), 400
    nid = notify.push(msg, body.get('level') or 'error',
                      key=body.get('key'), source=body.get('source') or 'ui')
    return jsonify({"ok": True, "id": nid})


@app.errorhandler(Exception)
def _notify_unhandled(e):
    """Koi bhi unhandled route exception ab notification banti hai — pehle ye sirf
    Flask ke stderr me jaati thi aur 500 chup-chaap frontend pe dikh jaata tha."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e   # 404/401/403 = normal traffic, notification nahi
    try:
        notify.error(f"{request.path} → {type(e).__name__}: {e}",
                     key=f"route:{request.path}", source="dashboard")
    except Exception:
        pass
    print(f"[unhandled] {request.path}: {e}", flush=True)
    return jsonify({"ok": False, "msg": str(e)}), 500


@app.route('/api/fill-delays')
def api_fill_delays():
    """TRAP #63 monitoring data — every live order whose fill-confirm poll
    took more than one attempt (i.e. wasn't instant), across all strategies.
    Most-recent-first. ?symbol=RELIANCE filters (substring match on trad_sym).
    Not real-time-critical — this is history for later review (which
    instruments run close to the 8s poll cliff, how often, does it correlate
    with time of day/liquidity), not something any code reads back."""
    delay_file = BASE_DIR / "data" / "fill_confirm_delays.json"
    if not delay_file.exists():
        return jsonify([])
    try:
        rows = json.loads(delay_file.read_text())
    except Exception:
        return jsonify([])
    sym_filter = request.args.get('symbol', '').strip().upper()
    if sym_filter:
        rows = [r for r in rows if sym_filter in (r.get('trad_sym') or '').upper()]
    return jsonify(list(reversed(rows)))


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


# /api/save-summary DELETED (2026-07-16) — no callers anywhere, and broken since
# the 2026-07-09 refactor moved the script: it ran BASE_DIR/'save_daily_summary.py'
# while the file is now _TOOLS/save_daily_summary.py. subprocess.run() without
# check= swallows the non-zero exit, so it returned "✅ Summary saved to results/"
# on a guaranteed failure. (_paths.py puts _TOOLS on sys.path — that fixes imports,
# not a filesystem path.) The script still works when run directly.


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

    # ── ASYNC: TradingView ka ~3s webhook timeout trip na ho. LIVE order
    # placement (fill-poll 8s + chase rounds = 8-40s) sync request ke andar
    # chalti thi → TV "delivery failed — request took too long and timed out"
    # dikhata tha CHAHE order actually lag raha ho → TV/server/broker desync.
    # Ab signal background thread me; TV ko turant 200. SAFE: handle_signal
    # khud _lock leta hai (fully serialized) aur dedup (strat:id) TV ke retry/
    # double-fire ko already sambhalta hai — thread-per-alert double-order nahi karega.
    def _run_signal(p):
        try:
            _ensure_feed_started()
            wh.handle_signal(p)
        except Exception as e:
            print("[WEBHOOK-ASYNC] handle_signal error:", e, flush=True)
    _threading.Thread(target=_run_signal, args=(payload,), daemon=True).start()
    return jsonify({"ok": True, "msg": "accepted"}), 200


@app.route('/api/webhook/status')
def api_webhook_status():
    import webhook_executor as wh
    return jsonify(wh.status())


# ── SELL-margin cache (real Dhan SPAN, background-warmed so renders never block) ──
# A BUY option's margin is the premium debit (exact, computed inline). A SELL
# option's real SPAN+exposure margin needs a live Dhan margin-calculator call
# (~0.1-0.3s, rate-limited) — too slow to run per-row inside a synchronous render.
# So: the render reads a disk cache (data/margin_cache.json, keyed sec_id|qty) and
# queues any cache-miss for a low-priority background thread to fill. First view of
# a new contract shows "—", then real ₹ on the next refresh; cached forever after.
_MARGIN_CACHE_FILE = os.path.join(BASE_DIR, 'data', 'margin_cache.json')
_margin_cache = None
_margin_lock = _threading.Lock()
_margin_warm_q = []          # [(sec_id, seg, qty, price), ...]
_margin_tried = set()        # keys attempted this process (avoid re-queuing failures/expired)
_margin_warm_started = False

def _margin_cache_load():
    global _margin_cache
    if _margin_cache is None:
        try:
            with open(_MARGIN_CACHE_FILE) as _f:
                _margin_cache = json.load(_f)
        except Exception:
            _margin_cache = {}
    return _margin_cache

def _margin_warm_loop():
    import risk_gate as _rg, time as _t
    while True:
        item = None
        with _margin_lock:
            if _margin_warm_q:
                item = _margin_warm_q.pop(0)
        if not item:
            _t.sleep(6); continue
        sid, seg, qty, price = item
        key = "%s|%s" % (sid, qty)
        try:
            mv = _rg.dhan_real_margin(sid, seg or 'NSE_FNO', int(qty), float(price), 'SELL')
        except Exception:
            mv = None
        if mv and mv > 0:
            with _margin_lock:
                _margin_cache[key] = round(float(mv), 2)
                try:
                    with open(_MARGIN_CACHE_FILE, 'w') as _f:
                        json.dump(_margin_cache, _f)
                except Exception:
                    pass
        _t.sleep(1.5)   # gentle: dhan_real_margin is low-priority on the rate-limiter; never starve trading

def _margin_warm_start():
    global _margin_warm_started
    if not _margin_warm_started:
        _margin_warm_started = True
        _threading.Thread(target=_margin_warm_loop, daemon=True).start()

def _enrich_trade_display(trades, lot_only=False):
    """Add DISPLAY-ONLY `lot_size` (+ `margin` for completed trades) to each row —
    consumed by the trade table's Margin column, the Open Positions Lots column and
    the Gain/Loss hover's lot-size line. Never changes any order. `lot_only=True`
    (open positions) adds only lot_size — opens already carry `margin_used`.
    BUY margin = premium × qty (exact); SELL margin = real Dhan SPAN from the
    background-warmed cache (est ~), "—" until warmed."""
    try:
        import dhan_master as _dm
    except Exception:
        _dm = None
    cache = _margin_cache_load()
    if not lot_only:
        _margin_warm_start()
    for t in (trades or []):
        try:
            lot = _dm.get_lot_size_by_sec_id(t.get('sec_id')) if _dm else None
            if lot:
                t['lot_size'] = int(lot)
        except Exception:
            pass
        if lot_only:
            continue
        try:
            ep = float(t.get('entry_price') or 0); q = float(t.get('qty') or 0)
            side = str(t.get('entry')).upper()
            if ep <= 0 or q <= 0:
                continue
            if side == 'BUY':
                t['margin'] = round(ep * q, 2)   # premium debit = exact capital
                t['margin_est'] = False
            elif side == 'SELL':
                sid = t.get('sec_id')
                if not sid:
                    continue
                key = "%s|%s" % (sid, int(q))
                m = cache.get(key)
                if m and m > 0:
                    t['margin'] = m
                    t['margin_est'] = True       # Dhan SPAN calc (proxy for Kite)
                elif key not in _margin_tried:   # queue once for the background warmer
                    with _margin_lock:
                        _margin_tried.add(key)
                        if len(_margin_warm_q) < 800:
                            _margin_warm_q.append((sid, t.get('segment') or 'NSE_FNO', int(q), ep))
        except Exception:
            pass
    return trades


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
    # ── Positional/overnight carry-over (DISPLAY-ONLY) ───────────────────────
    # A position entered on a prior day and still open must stay visible in
    # "today's" view — the date-scoped query hides it the moment the day rolls
    # over (the exact reason overnight/positional positions "disappeared" the
    # next day). We union in any leg that is still open across a short recent
    # lookback whose ENTRY date is before the selected date, tagged
    # `carried_over` so the UI can badge it. pos_monitor stays today-scoped —
    # this changes only what the dashboard SHOWS, never what RMS manages.
    # A day-scoped NETTING cannot correctly pair a positional ROLL, so for these
    # strategies the multi-day netting is the only correct one and we take their
    # rows from it wholesale. Proven live 2026-07-16: at 15:10 the VRP condor
    # closed yesterday's 4 legs and opened 4 new ones. The day view paired
    # today's CLOSING legs against today's OPENING legs — 4 phantom completed
    # trades totalling -₹71.50, and today's real 4-leg position invisible. The
    # truth (range-netted): yesterday's condor closed at +₹598, 4 legs still
    # open. Merely carrying over prior-day legs (the earlier fix) could not see
    # this at all: after a roll the live legs' entry_date IS today.
    # Only opt-in allow_overnight strategies are re-netted this way — an
    # intraday strategy is flat by EOD, so its lingering prior-day "open" rows
    # are STALE (this DB has ~38) and range-netting them would resurrect ghosts.
    try:
        import risk_gate as _rg_ov
        _lb_from = (ist - timedelta(days=7)).strftime('%Y-%m-%d')
        _rng = order_store.trades_for_range(_lb_from, date, **filt)
        _pos = set()
        for _r in (_rng.get('open') or []) + (_rng.get('details') or []):
            try:
                if _rg_ov.allow_overnight(_r.get('strategy')):
                    _pos.add(_r.get('strategy'))
            except Exception:
                pass
        if _pos:
            # drop the day-scoped (mis-netted) rows for these strategies …
            data['open'] = [p for p in data.get('open', []) if p.get('strategy') not in _pos]
            data['details'] = [t for t in data.get('details', []) if t.get('strategy') not in _pos]
            # … and replace them with the range-netted truth
            for p in (_rng.get('open') or []):
                if p.get('strategy') not in _pos:
                    continue
                if (p.get('entry_date') or date) < date:
                    p['carried_over'] = True   # entered on a prior day, still live
                data.setdefault('open', []).append(p)
            for t in (_rng.get('details') or []):
                if t.get('strategy') not in _pos:
                    continue
                if (t.get('exit_date') or date) != date:
                    continue                   # only what actually CLOSED on this date
                data.setdefault('details', []).append(t)
    except Exception:
        pass
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
                # Real executing-broker margin: SELL → actual SPAN+exposure via
                # broker_real_margin (Kite order_margins / Dhan calculator); BUY →
                # premium paid. Falls back to the multiplier only if the API fails.
                # (Was crude qty*price*multiplier, which under-showed SELL margin.)
                p['margin_used'] = round(_rg._leg_capital(p, _rc), 2)
            except Exception:
                p['margin_used'] = 0
            # Task 8 — current trailing/aggressive SL the monitor will fire on
            try:
                _sli = _live_sl_for_open(p)
                if _sli:
                    p['sl_now'] = _sli.get('sl')
                    p['sl_rs'] = _sli.get('sl_rs')   # signed ₹ if SL hits now (<0 loss, >=0 locked profit)
                    p['tp_now'] = _sli.get('target')
                    p['sl_mode'] = _sli.get('mode')
                    p['sl_trailing'] = _sli.get('trailing')
                    p['sl_aggressive'] = _sli.get('aggressive')
            except Exception:
                pass

        # ── Real HEDGED margin per strategy group ────────────────────────────
        # `margin_used` above is each leg's STANDALONE margin — right for a leg,
        # badly wrong when SUMMED across a hedged structure: that's not what the
        # broker blocks. Live-measured 2026-07-16: a 2-leg vertical summed to
        # ₹1,49,532 vs ₹37,813 really blocked; a 4-leg condor ₹3,77,337 vs
        # ₹82,334. The UI groups by strategy, so give it that group's real basket
        # margin to show as the group TOTAL instead of the sum.
        # Uses the SAME cached _group_capital() the RMS capital check uses — the
        # table, the payoff panel and the risk engine must never disagree about
        # the same position.
        try:
            _grp = {}
            for p in data.get('open', []):
                if 'CAPITAL_BLOCKED' in (p.get('tags') or []):
                    continue
                _grp.setdefault(p.get('strategy') or '', []).append(p)
            data['group_margin'] = {
                s: {"hedged": round(_rg._group_capital(rows, _rc), 2),
                    "standalone": round(sum(float(r.get('margin_used') or 0) for r in rows), 2)}
                for s, rows in _grp.items()
            }
        except Exception:
            data['group_margin'] = {}
    except Exception as _e:
        pass
    _enrich_trade_display(data.get('details'))              # lot_size + margin for the table
    _enrich_trade_display(data.get('open'), lot_only=True)  # lot_size for Open Positions Lots column
    return jsonify(data)


def _strategy_matcher(want):
    """Resolve-aware strategy filter for the display/stats routes (read-only).

    order_store rows raw strings pe hote hain — case-variants (`rsi_v1_PAPER` vs
    registry ck), purane aliases (`ema920` = 05.03 ema_v1) aur "id | desc"
    pollution (TRAP #128). Total Summary in sabko `strategy_registry.resolve()`
    se EK strategy pe group karta hai, par single-strategy filter ab tak raw SQL
    `=` exact-match tha → dropdown se strategy chunte hi calendar KHAALI (0
    trades) jabki All-strategies summary me uske trades dikh rahe the (2026-07-21
    user report: 05.02 RSI paper / 05.03 EMA / 09.01). Ye matcher filter ko usi
    resolve() identity pe le aata hai — single-strategy view == All-strategies
    grouping ka wahi attribution set. Unregistered buckets (manual/unknown/
    default) sirf exact raw match karte hain (koi collapse nahi).
    """
    try:
        import strategy_registry as _sr
        wid = _sr.resolve(want)
    except Exception:
        _sr, wid = None, None
    wl = str(want or '').lower()
    _cache = {}

    def _match(raw):
        hit = _cache.get(raw)
        if hit is None:
            rs = str(raw or '')
            hit = (rs == want) or (rs.lower() == wl)
            if not hit and wid is not None and _sr is not None:
                try:
                    hit = _sr.resolve(rs) == wid
                except Exception:
                    hit = False
            _cache[raw] = hit
        return hit
    return _match


def _pop_strategy_matcher(filt):
    """filt dict se 'strategy' nikaal kar resolve-aware matcher do (ya None).
    SQL exact-match ki jagah netted trades ki attribution post-filter hoti hai —
    netting All-strategies jaisi hi chalti hai, isliye single-strategy ke totals
    All-view ke summary row se EXACT match karte hain."""
    strat = filt.pop('strategy', None)
    return _strategy_matcher(strat) if strat else None


@app.route('/api/orders/calendar-summary')
def api_orders_calendar_summary():
    """Returns daily P&L and trade count summary for a given year/month or from_date/to_date range."""
    import order_store
    year = request.args.get('year')
    month = request.args.get('month')
    from_date = request.args.get('from_date')  # YYYY-MM-DD
    to_date = request.args.get('to_date')      # YYYY-MM-DD
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    _sm = _pop_strategy_matcher(filt)

    # Determine the requested [lo, hi] window = which EXIT dates to show.
    lo = hi = None
    if from_date and to_date:
        lo, hi = from_date, to_date
    elif year and month:
        _mm = str(month).zfill(2)
        lo, hi = f"{year}-{_mm}-01", f"{year}-{_mm}-31"
    elif year:
        lo, hi = f"{year}-01-01", f"{year}-12-31"

    # Net across a LOOKBACK range (not per-day) so a positional trade whose ENTRY
    # leg is on an earlier day still pairs correctly with its in-window EXIT, then
    # attribute each completed round-trip to its EXIT date (the day the P&L is
    # realized — matches the live Orders "Today's Peak" + reality). Per-day
    # trades_for(d) filters WHERE date=d, so it can't pair an overnight position's
    # entry (earlier day) with its exit — it mis-nets the close leg against a
    # same-day re-open on the same strike → phantom P&L (VRP condor: -6155 phantom
    # vs +4934 real). trades_for_range nets across dates correctly (its docstring).
    def _minus_days(dstr, n):
        try:
            return (datetime.strptime(dstr, '%Y-%m-%d') - timedelta(days=n)).strftime('%Y-%m-%d')
        except Exception:
            return dstr
    net_lo = _minus_days(lo, 400) if lo else '2000-01-01'   # 400d covers any realistic positional hold
    net_hi = hi or '2099-12-31'

    summary = {}
    all_trades = []
    try:
        rng = order_store.trades_for_range(net_lo, net_hi, **filt)
        for t in (rng.get('details') or []):
            if _sm and not _sm(t.get('strategy')):
                continue   # resolve-aware single-strategy narrow (see _strategy_matcher)
            xd = t.get('exit_date')
            if not xd:
                continue
            if lo and not (lo <= xd <= hi):
                continue   # only round-trips REALIZED in the requested window
            s = summary.setdefault(xd, {'pnl': 0.0, 'count': 0})
            s['pnl'] += t.get('pnl') or 0
            s['count'] += 1
            all_trades.append(t)
        for _d in summary:
            summary[_d]['pnl'] = round(summary[_d]['pnl'], 2)
    except Exception as e:
        print("[calendar_summary] range-net fail:", e, flush=True)
            
    # Also include distinct filter options for the UI
    try:
        distinct_filters = {
            'strategy': order_store.distinct('strategy'),
            'broker': order_store.distinct('broker')
        }
    except Exception:
        distinct_filters = {'strategy': [], 'broker': []}
        
    _enrich_trade_display(all_trades)   # lot_size (Gain/Loss hover) + margin
    return jsonify({
        'summary': summary,
        'trades': all_trades,
        'filters': distinct_filters
    })


@app.route('/api/bs-shadow')
def api_bs_shadow():
    """Black-Scholes shadow of the REAL trades, per day + per strategy, from the
    cached data/bs_shadow/<date>.json files written daily by _ops/bs_shadow.py.
    DISPLAY-ONLY. Real numbers reproduce the dashboard exactly; only BS is modelled.

    Query: from_date/to_date OR year[/month]. Returns:
      { days: {date: {real_net, bs_net, n, bs_n, by_strategy:{sid:{real_net,bs_net,n,bs_n}}}},
        available: [...], missing: [...] }  (missing = date has trades but no shadow yet)
    """
    import os as _o, json as _j, glob as _g
    year = request.args.get('year'); month = request.args.get('month')
    from_date = request.args.get('from_date'); to_date = request.args.get('to_date')
    base = _o.path.join(_o.path.dirname(_o.path.abspath(__file__)), 'data', 'bs_shadow')

    def _in_range(d):
        if from_date and to_date:
            return from_date <= d <= to_date
        if year and month:
            return d.startswith(f"{year}-{str(month).zfill(2)}-")
        if year:
            return d.startswith(f"{year}-")
        return True

    days = {}
    bykey = {}      # join map: "<entry_date>|<sym>|<etime>|<xtime>|<side>" -> {bs_net,bs_gross,bs_ok}
                    # lets the frontend attach BS to the calendar's already-filtered trades,
                    # so ALL calendar filters (source/mode/broker/strategy/view/date) apply for free.
    try:
        for fp in _g.glob(_o.path.join(base, '*.json')):
            d = _o.path.basename(fp)[:-5]
            if len(d) != 10 or not _in_range(d):
                continue
            try:
                p = _j.load(open(fp))
            except Exception:
                continue
            for t in p.get('trades', []):
                if not t.get('bs_ok'):
                    continue
                k = "|".join([str(t.get('entry_date', d)), str(t.get('sym', '')),
                              str(t.get('entry_time', '')), str(t.get('exit_time', '')),
                              str(t.get('side', ''))])
                bykey[k] = {'bs_net': t.get('bs_net', 0), 'bs_gross': t.get('bs_gross', 0), 'bs_ok': True}
            bs_n = sum(1 for t in p.get('trades', []) if t.get('bs_ok'))
            bystrat = {}
            for sid, v in (p.get('by_strategy') or {}).items():
                sbn = sum(1 for t in p.get('trades', [])
                          if t.get('strategy') == sid and t.get('bs_ok'))
                bystrat[sid] = {'real_gross': v.get('real_gross', 0), 'real_net': v.get('real_net', 0),
                                'bs_gross': v.get('bs_gross', 0), 'bs_net': v.get('bs_net', 0),
                                'n': v.get('n', 0), 'bs_n': sbn}
            tot = p.get('totals') or {}
            days[d] = {'real_gross': tot.get('real_gross', 0), 'real_net': tot.get('real_net', 0),
                       'bs_gross': tot.get('bs_gross', 0), 'bs_net': tot.get('bs_net', 0),
                       'n': tot.get('n', 0), 'bs_n': bs_n, 'by_strategy': bystrat}
    except Exception as e:
        print("[bs-shadow] fail:", e, flush=True)

    missing = []
    try:
        import sqlite3, order_store
        with order_store._lock, sqlite3.connect(str(order_store.DB_PATH), timeout=10) as c:
            if from_date and to_date:
                rows = c.execute("SELECT DISTINCT date FROM orders WHERE date>=? AND date<=?",
                                 [from_date, to_date]).fetchall()
            else:
                pre = (f"{year}-{str(month).zfill(2)}-%" if year and month
                       else f"{year}-%" if year else "%")
                rows = c.execute("SELECT DISTINCT date FROM orders WHERE date LIKE ?", [pre]).fetchall()
        missing = sorted(r[0] for r in rows if r[0] and r[0] not in days)
    except Exception:
        pass

    return jsonify({'days': days, 'bykey': bykey, 'available': sorted(days.keys()), 'missing': missing})


@app.route('/api/backtest/runs')
def api_backtest_runs():
    """List available backtest runs (from runs/index.json) for the Stats-tab
    backtest-mode strategy dropdown. Display-only."""
    import backtest_calendar
    try:
        return jsonify({'ok': True, 'runs': backtest_calendar.list_runs()})
    except Exception as e:
        print("[backtest/runs] fail:", e, flush=True)
        return jsonify({'ok': False, 'runs': [], 'error': str(e)})


@app.route('/api/backtest/calendar-summary')
def api_backtest_calendar_summary():
    """Same {summary, trades, filters} shape as /api/orders/calendar-summary,
    but built from ONE backtest run's all_trades (bucketed by ENTRY date) so the
    Stats calendar/table/charts render backtest results day-by-day. Also returns
    `metrics` (the run's own report card) and `meta` (label/window/combo used).
    Params: slug (required), pass=instrument|rms|bs, period=full|train|oos,
    from_date/to_date (YYYY-MM-DD, optional)."""
    import backtest_calendar
    slug = request.args.get('slug')
    if not slug:
        return jsonify({'summary': {}, 'trades': [], 'filters': {},
                        'metrics': {}, 'meta': {}, 'error': 'slug required'})
    pass_ = request.args.get('pass') or 'bs'
    period = request.args.get('period') or 'full'
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    # Month-mode (no explicit range): the frontend sends year/month — derive the
    # date window so the calendar's top summary reflects THAT month only, exactly
    # like the live calendar-summary route.
    if not (from_date and to_date):
        year = request.args.get('year')
        month = request.args.get('month')
        if year and month:
            mm = month.zfill(2)
            from_date, to_date = f"{year}-{mm}-01", f"{year}-{mm}-31"
        elif year:
            from_date, to_date = f"{year}-01-01", f"{year}-12-31"
    try:
        if ',' in slug:
            # portfolio-style: combine multiple runs (comma-separated slugs)
            slugs = [s for s in slug.split(',') if s]
            data = backtest_calendar.combined_summary(
                slugs, pass_=pass_, period=period,
                from_date=from_date, to_date=to_date)
        else:
            data = backtest_calendar.calendar_summary(
                slug, pass_=pass_, period=period,
                from_date=from_date, to_date=to_date)
        return jsonify(data)
    except Exception as e:
        print("[backtest/calendar-summary] fail:", e, flush=True)
        return jsonify({'summary': {}, 'trades': [], 'filters': {},
                        'metrics': {}, 'meta': {}, 'error': str(e)})


@app.route('/api/orders/monthly-returns')
def api_orders_monthly_returns():
    """All-history month-wise NET ₹ (for the Stats V2 heatmap). Grouped by trade
    entry month. Same source/mode/broker/strategy filters as calendar-summary.
    Display-only — reuses order_store."""
    import order_store
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    _sm = _pop_strategy_matcher(filt)
    out = {}
    try:
        details = order_store.trades_for_range('2015-01-01', '9999-12-31', **filt).get('details', [])
        for t in details:
            if _sm and not _sm(t.get('strategy')):
                continue
            d = (t.get('entry_date') or t.get('exit_date') or '')
            if len(d) < 7:
                continue
            y, m = d[:4], int(d[5:7])
            out.setdefault(y, {})
            out[y][m] = round(out[y].get(m, 0) + (t.get('pnl') or 0), 2)
    except Exception as e:
        print("[monthly-returns] fail:", e, flush=True)
    return jsonify({'ok': True, 'months': out})


@app.route('/api/stat-views', methods=['GET'])
def api_stat_views_list():
    """Saved strategy-group Views for the Stats tab. Display/config only."""
    import stat_views
    try:
        return jsonify({'ok': True, 'views': stat_views.list_views()})
    except Exception as e:
        print("[stat-views list] fail:", e, flush=True)
        return jsonify({'ok': False, 'views': [], 'error': str(e)})


@app.route('/api/stat-views', methods=['POST'])
def api_stat_views_create():
    import stat_views
    body = request.get_json(silent=True) or {}
    try:
        v = stat_views.create_view(body.get('name'), body.get('strategies'),
                                   kind=body.get('kind') or 'live')
        return jsonify({'ok': True, 'view': v})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/stat-views/<int:view_id>', methods=['PUT'])
def api_stat_views_update(view_id):
    import stat_views
    body = request.get_json(silent=True) or {}
    try:
        v = stat_views.update_view(view_id, name=body.get('name'),
                                   strategies=body.get('strategies'))
        return jsonify({'ok': True, 'view': v})
    except KeyError as e:
        return jsonify({'ok': False, 'error': str(e)}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/stat-views/<int:view_id>', methods=['DELETE'])
def api_stat_views_delete(view_id):
    import stat_views
    try:
        stat_views.delete_view(view_id)
        return jsonify({'ok': True})
    except KeyError as e:
        return jsonify({'ok': False, 'error': str(e)}), 404
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/orders/optimized-pnl')
def api_orders_optimized_pnl():
    """Per-trade "what-if" P&L under the two OPTIMISED SL/Target profiles
    (best-fixed + best-aggressive from the grid-search). Display-only for the
    Stats page — reuses the path-aware replay engine, no live-path change.
    Same query params as calendar-summary so the trade id set matches 1:1.
    Disk bars only by default (no Dhan hit on a dashboard load); ?fetch=1
    backfills missing bars from Dhan (rate-limited)."""
    import order_store
    import opt_pnl
    year = request.args.get('year')
    month = request.args.get('month')
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    fetch = str(request.args.get('fetch') or '') in ('1', 'true', 'yes')
    filt = {k: request.args.get(k) for k in
            ('source', 'mode', 'broker', 'strategy', 'instrument') if request.args.get(k)}
    _sm = _pop_strategy_matcher(filt)   # calendar-summary ke saath 1:1 same filtering

    # Resolve the same date window calendar-summary uses.
    if from_date and to_date:
        d_from, d_to = from_date, to_date
    elif year and month:
        mm = month.zfill(2)
        d_from, d_to = f"{year}-{mm}-01", f"{year}-{mm}-31"
    elif year:
        d_from, d_to = f"{year}-01-01", f"{year}-12-31"
    else:
        return jsonify({'map': {}, 'params': {}})

    m = {}
    try:
        details = order_store.trades_for_range(d_from, d_to, **filt)['details']
        if _sm:
            details = [t for t in details if _sm(t.get('strategy'))]
        m = opt_pnl.compute_for_trades(details, allow_fetch=fetch)
    except Exception as e:
        print("[optimized-pnl] fail:", e, flush=True)

    covered = sum(1 for v in m.values() if v.get('covered'))
    return jsonify({
        'map': m,
        'covered': covered,
        'total': len(m),
        'params': {'fixed': opt_pnl.FIX, 'aggr': opt_pnl.AGG_PARAMS,
                   'aggr_eod': opt_pnl.AGG_EOD_PARAMS}
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
    _sm = _pop_strategy_matcher(filt)
    details = order_store.trades_for_range(date_from or "0000-00-00", date_to or "9999-12-31", **filt)['details']
    if _sm:
        details = [t for t in details if _sm(t.get('strategy'))]
    # metrics usi (resolve-aware filtered) trade set se — pills == table, exact
    metrics = order_store.stats_summary(trades=details)
    return jsonify({'ok': True, 'metrics': metrics, 'trades': details})


_charges_cache = None


def _charges_mod():
    """Lazy-import scratch/nifty_trend/charges.py — the declared single source of
    truth for Zerodha charges (date-aware STT/txn regime table). Same local
    sys.path-insert idiom _core/payoff.py uses for bs_option."""
    global _charges_cache
    if _charges_cache is not None:
        return _charges_cache or None
    d = str(BASE_DIR / "scratch" / "nifty_trend")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import charges as _m
        _charges_cache = _m
    except Exception as e:
        print(f"[charges] canonical charges.py unavailable ({e}) — "
              f"falling back to hardcoded CURRENT-regime rates; tax on trades "
              f"entered before 2026-04-01 will be overstated.", flush=True)
        _charges_cache = False
    return _charges_cache or None


def _zerodha_charges(entry_px, exit_px, qty, entry_side, when=None):
    """₹ round-trip charges for one option leg, at the rates in force on `when`
    (the trade's ENTRY date).

    Was a hand-copied mirror of scratch/nifty_trend/charges.py with the
    Budget-2026 rates frozen in as literals, and a "keep in sync" comment — i.e.
    the codebase knew it was a duplicate. Both agreed on today's numbers (that's
    why TRAP #118's reconciliation matched to the rupee), but charges.py is
    date-aware and this wasn't: STT on options was 0.0625% -> 0.10% (2024-10-01)
    -> 0.15% (2026-04-01). Its docstring claimed "TODAY's trades only", which is
    not true of its caller — /api/strategy-equity takes an arbitrary date_from.
    The moment a range reached back past 2026-04-01, the dashboard and the EOD
    report (which uses charges.py) would disagree on tax for the same trade —
    exactly the shape TRAP #118 already cost a debugging session.

    when=None -> today's rates, same as before.
    """
    if not entry_px or not exit_px or not qty:
        return 0.0
    _ch = _charges_mod()
    if _ch is not None:
        return _ch.option_charges(entry_px, exit_px, qty, entry_side, when=when)
    # Degraded path only (charges.py unreachable) — current-regime literals.
    buy_side  = entry_px if entry_side == "BUY" else exit_px
    sell_side = entry_px if entry_side == "SELL" else exit_px
    buy_turn, sell_turn = buy_side * qty, sell_side * qty
    total_turn = buy_turn + sell_turn
    brokerage    = 40.0
    stt          = 0.0015 * sell_turn
    exch         = 0.0003553 * total_turn
    sebi         = 0.0000001 * total_turn
    stamp        = 0.00003 * buy_turn
    gst          = 0.18 * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst


@app.route('/api/strategy-equity')
def api_strategy_equity():
    """Per-strategy equity curves + summary table over a date range (Task 84).
    For every strategy that has completed trades in the range, returns a
    time-ordered cumulative-P&L series (gross + net) and headline stats. Also a
    combined 'All strategies' series. Marks which strategies are live right now
    (running PID) and their paper/live mode. Query: date_from, date_to, mode."""
    import order_store
    from datetime import datetime, timedelta, timezone
    try:
        import strategy_registry as _sr
    except Exception:
        _sr = None

    ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    date_to   = request.args.get('date_to')   or ist.strftime('%Y-%m-%d')
    date_from = request.args.get('date_from') or (ist - timedelta(days=30)).strftime('%Y-%m-%d')
    mode = request.args.get('mode')  # 'paper' | 'live' | None(all)
    filt = {'mode': mode} if mode in ('paper', 'live') else {}

    # Which strategies are running right now + their mode
    running = {}
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        for s in cfg.keys():
            pid = get_pid(s)
            if pid:
                running[s] = get_mode(s)
    except Exception:
        pass

    details = order_store.trades_for_range(date_from, date_to, **filt)['details']

    def _label(sid):
        if _sr:
            try:
                lbl = _sr.label(sid)
                if lbl and lbl != sid:
                    return lbl
            except Exception:
                pass
        return sid

    # Bucket completed trades by strategy, ordered by exit datetime
    buckets = {}
    combined = []
    for t in details:
        if t.get('pnl') is None:
            continue
        sid = t.get('strategy') or 'unknown'
        exit_dt = f"{t.get('exit_date','')} {t.get('exit_time','')}".strip()
        gross = float(t.get('pnl') or 0)
        # when = ENTRY date: this route accepts an arbitrary date_from, so a
        # range reaching before 2026-04-01 must use that era's STT, not today's.
        tax = _zerodha_charges(t.get('entry_price'), t.get('exit_price'),
                               t.get('qty'), t.get('entry'),
                               when=t.get('entry_date'))
        rec = {'t': exit_dt, 'gross': round(gross, 2), 'tax': round(tax, 2),
               'net': round(gross - tax, 2), 'sym': t.get('sym', ''),
               'reason': t.get('exit_reason', '')}
        buckets.setdefault(sid, []).append(rec)
        combined.append(rec)

    def _series(trades):
        trades = sorted(trades, key=lambda r: r['t'])
        cg = cn = 0.0
        peak = 0.0
        maxdd = 0.0
        pts = []
        wins = 0
        for r in trades:
            cg += r['gross']; cn += r['net']
            peak = max(peak, cn)
            maxdd = max(maxdd, peak - cn)
            if r['net'] > 0:
                wins += 1
            pts.append({'t': r['t'], 'cum_gross': round(cg, 2),
                        'cum_net': round(cn, 2), 'pnl': r['net'], 'sym': r['sym']})
        n = len(trades)
        nets = [r['net'] for r in trades]
        mean = (sum(nets) / n) if n else 0.0
        var = (sum((x - mean) ** 2 for x in nets) / n) if n else 0.0
        sd = var ** 0.5
        sharpe = round((mean / sd) * (n ** 0.5), 2) if sd > 0 else 0.0
        gross_win = sum(x for x in nets if x > 0)
        gross_loss = abs(sum(x for x in nets if x <= 0))
        pf = round(gross_win / gross_loss, 2) if gross_loss > 0 else (round(gross_win, 2) if gross_win else 0.0)
        return {
            'points': pts,
            'n_trades': n, 'wins': wins, 'losses': n - wins,
            'win_rate': round(wins / n * 100, 1) if n else 0.0,
            'gross': round(cg, 2), 'tax': round(cg - cn, 2), 'net': round(cn, 2),
            'max_dd': round(maxdd, 2), 'sharpe': sharpe, 'profit_factor': pf,
        }

    strategies = []
    for sid, trades in buckets.items():
        s = _series(trades)
        s['id'] = sid
        s['label'] = _label(sid)
        s['running'] = sid in running
        s['mode'] = running.get(sid, '')
        strategies.append(s)
    strategies.sort(key=lambda s: s['net'], reverse=True)

    combined_series = _series(combined) if combined else None

    return jsonify({
        'date_from': date_from, 'date_to': date_to, 'mode': mode or 'all',
        'strategies': strategies,
        'combined': combined_series,
        'running': running,
    })


@app.route('/strategy-equity')
def strategy_equity_page():
    return render_template('strategy_equity.html')


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

@app.route('/api/tags-store', methods=['GET'])
def get_tags_store():
    tags_file = BASE_DIR / "data" / "tags_store.json"
    if not tags_file.exists():
        return jsonify([])
    try:
        with open(tags_file, "r") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify([])

@app.route('/api/tags-store', methods=['POST'])
def save_tags_store():
    tags_file = BASE_DIR / "data" / "tags_store.json"
    tags_file.parent.mkdir(exist_ok=True)
    tags = request.get_json().get('tags', [])
    try:
        with open(tags_file, "w") as f:
            json.dump(tags, f)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/api/orders/update-tags', methods=['POST'])
def update_order_tags():
    data = request.get_json()
    order_id = data.get('id')
    tags = data.get('tags', [])
    if not order_id:
        return jsonify({"status": "error", "message": "Missing order ID"})
    try:
        import order_store
        order_store.update_tags(order_id, tags)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route('/api/orders/update-sl-tp', methods=['POST'])
def api_update_sl_tp():
    data = request.get_json()
    order_id = data.get('id')
    sl_type = data.get('sl_type'); sl_val = data.get('sl_val')
    tp_type = data.get('tp_type'); tp_val = data.get('tp_val')
    sl_pct = data.get('sl_pct')
    tp_pct = data.get('tp_pct')
    sl_candle_close = bool(data.get('sl_candle_close'))
    tp_candle_close = bool(data.get('tp_candle_close'))
    if not order_id:
        return jsonify({"status": "error", "message": "Missing order ID"})
    try:
        import order_store
        with order_store._lock, order_store._conn() as c:
            row = c.execute("SELECT tags, price FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not row: return jsonify({"status": "error", "message": "Order not found"})

            tags_str, entry_px_val = row
            entry_px = float(entry_px_val or 0)
            
            tags = []
            try: tags = json.loads(tags_str or "[]")
            except: pass

            # --- Modified by Antigravity AI: Added Trailing Stop-Loss support & fixed candle_close crash ---
            tags = [t for t in tags if not t.startswith(("SL_PCT:", "TP_PCT:", "SL_TYPE:", "SL_VAL:", "TP_TYPE:", "TP_VAL:", "SL_TRAIL_STEP:", "TP_TRAIL_STEP:")) and t not in ("SL_CANDLE_CLOSE:true", "TP_CANDLE_CLOSE:true")]

            # Load global configuration to check custom trailing step bands
            g_cfg = _risk_config().get("global") or {}
            min_s = resolve_trailing_step(entry_px, g_cfg)

            if sl_type and sl_val is not None and str(sl_val).strip() != "":
                tags.append(f"SL_TYPE:{sl_type}")
                if sl_type == "candle_close":
                    tags.append(f"SL_VAL:{sl_val}")
                elif sl_type == "trailing_pt":
                    if ":" in str(sl_val):
                        gap_part, step_part = str(sl_val).split(":", 1)
                        gap_val = float(gap_part)
                        step_val = max(float(step_part), min_s)
                    else:
                        gap_val = float(sl_val)
                        step_val = min_s
                    tags.append(f"SL_VAL:{gap_val}")
                    tags.append(f"SL_TRAIL_STEP:{step_val}")
                else:
                    tags.append(f"SL_VAL:{float(sl_val)}")
                if sl_candle_close:
                    tags.append("SL_CANDLE_CLOSE:true")
            elif sl_pct is not None and str(sl_pct).strip() != "":
                tags.append(f"SL_PCT:{float(sl_pct)}")
                if sl_candle_close:
                    tags.append("SL_CANDLE_CLOSE:true")

            if tp_type and tp_val is not None and str(tp_val).strip() != "":
                tags.append(f"TP_TYPE:{tp_type}")
                if tp_type == "candle_close":
                    tags.append(f"TP_VAL:{tp_val}")
                elif tp_type == "trailing_pt":
                    min_s = _get_min_step(entry_px)
                    if ":" in str(tp_val):
                        gap_part, step_part = str(tp_val).split(":", 1)
                        gap_val = float(gap_part)
                        step_val = max(float(step_part), min_s)
                    else:
                        gap_val = float(tp_val)
                        step_val = min_s
                    tags.append(f"TP_VAL:{gap_val}")
                    tags.append(f"TP_TRAIL_STEP:{step_val}")
                else:
                    tags.append(f"TP_VAL:{float(tp_val)}")
                if tp_candle_close:
                    tags.append("TP_CANDLE_CLOSE:true")
            elif tp_pct is not None and str(tp_pct).strip() != "":
                tags.append(f"TP_PCT:{float(tp_pct)}")
                if tp_candle_close:
                    tags.append("TP_CANDLE_CLOSE:true")
            # --- End modification ---

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

def _sched_post(path, key):
    """auto_scheduler's own call into the dashboard's HTTP API, carrying the
    internal token (the login gate 401s otherwise — that's what silently killed
    the 9:10 auto-start for 6 days).

    LOUD on failure, deliberately: the old code did a bare `requests.post(...)`
    inside `except Exception: pass` and never looked at the status code, so a
    401 on every single call looked exactly like success. A scheduler that
    can't start the bots must never fail quietly again."""
    try:
        r = requests.post("http://127.0.0.1:5099" + path, timeout=5,
                          headers={"X-Internal-Token": _auth.get_internal_token()})
        if r.status_code != 200:
            print(f"[SCHEDULER] 🔴 {key}: {path} -> HTTP {r.status_code} "
                  f"{(r.text or '')[:120]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[SCHEDULER] 🔴 {key}: {path} -> {e}", flush=True)
        return False


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
                    print(f"[{now.strftime('%H:%M:%S')}] Auto-starting bots (last-known mode each)...")
                    try:
                        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                        for key in cfg.keys():
                            if _base(key) not in STRATEGIES:
                                continue  # not a process strategy (e.g. webhook_v1, vwap)
                            if isinstance(cfg[key], dict) and cfg[key].get("active", True):
                                # Restore the mode it was last explicitly started in — NOT
                                # always paper. This fires on every algo-monitor restart
                                # during trading hours (has_started_today is per-process,
                                # not per-day-only), and on VPS reboot recovery — a strategy
                                # that was LIVE must come back LIVE, or it silently stops
                                # placing real orders with zero alert. See LESSONS.md TRAP #57.
                                saved_mode = cfg[key].get("mode", "paper")
                                _sched_post(f"/api/start?s={key}&mode={saved_mode}", key)
                    except Exception as e:
                        print(f"[SCHEDULER] auto-start pass FAILED: {e}", flush=True)
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
                                _sched_post(f"/api/stop?s={key}&keep_active=1", key)
                    except Exception as e:
                        print(f"[SCHEDULER] auto-stop pass FAILED: {e}", flush=True)
                    has_stopped_today = True

        except Exception as e:
            print("Auto Scheduler Error:", e)
        
        time.sleep(30)

def webhook_monitor_loop():
    """Trails SL / target / 3:15 squareoff for open TradingView-webhook positions."""
    import webhook_executor as wh
    import time
    _cyc = 0
    while True:
        try:
            _ensure_feed_started()
            # Periodically re-adopt webhook positions opened AFTER this process
            # booted. _recover_wh_state() otherwise runs only once at import, so a
            # webhook entry handled by the algo-dashboard process never reaches
            # THIS (algo-monitor) process's _wh_state → its trailing SL never
            # manages it AND release_position() falsely reports "not tracked",
            # which made _pre_exit_guard suppress a real SL fire (root cause of the
            # 2026-07-09 unenforced-SL incident). Non-clobbering — live trails are
            # never reset. Fires immediately on start, then every ~30s (sleep 3s).
            _cyc += 1
            if _cyc % 10 == 1:
                wh._recover_wh_state()
            wh.monitor_tick()
        except Exception as e:
            print("Webhook monitor error:", e)
        time.sleep(3)

_rest_ltp_cache = {}   # sec_id -> (ltp, ts) — 3s TTL, avoids DH-904 across many open positions
_REST_LTP_TTL = 5   # poller cycle is 1.5s — 5s tolerates one missed/429'd poll without a REST stampede

def _rest_ltp_fallback(sec_id, seg):
    """LTP when the dhan_feed WebSocket isn't delivering quotes.
    P7 rewrite (2026-07-02): shared_ltp_cache FIRST — ltp_poller keeps every
    open position + index spot warm there with ONE batched Dhan call per cycle,
    so this normally never hits Dhan at all. The direct REST call survives only
    as a cache-miss last resort (e.g. a sec_id the poller doesn't watch), and
    is now routed through dhan_rate_limiter (acquire + note_429) — previously
    this path was completely invisible to the cross-process throttle."""
    import time as _t
    try:
        import shared_ltp_cache as _slc
        cached = _slc.get(sec_id, max_age=_REST_LTP_TTL)
        if cached:
            return cached
    except Exception:
        pass
    try:
        import requests as _req
        import dhan_rate_limiter as _drl
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        dhan_seg = {"NSE_EQ": "NSE_EQ", "IDX_I": "IDX_I", "NSE_FNO": "NSE_FNO"}.get(seg, "NSE_FNO")
        body = {dhan_seg: [int(sec_id)]}
        _drl.set_context("Monitor:PosLTP")
        if not _drl.acquire("ltp"):
            r = None   # gate saturated — fall through to the stale-cache last resort below
        else:
            r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
        if r is not None and r.status_code == 429:
            _drl.note_429()
        if r is not None and r.status_code == 200:
            quotes = (r.json().get("data", {}) or {}).get(dhan_seg, {})
            q = quotes.get(str(sec_id)) or quotes.get(str(int(sec_id)))
            if q:
                ltp = float(q.get("last_price") or q.get("ltp") or 0)
                if ltp > 0:
                    try:
                        import shared_ltp_cache as _slc2
                        _slc2.put(sec_id, ltp)
                    except Exception:
                        pass
                    return ltp
    except Exception as e:
        print("[_rest_ltp_fallback] fail:", e, flush=True)
    # slightly-stale shared value beats returning nothing (same convention as
    # dhan_broker.quote's last resort)
    try:
        import shared_ltp_cache as _slc3
        return _slc3.get_stale(sec_id, max_age=15.0)
    except Exception:
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
        import requests as _req
        import dhan_rate_limiter as _drl
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        date_str = now_ist.strftime("%Y-%m-%d")
        inst = "EQUITY" if seg == "NSE_EQ" else ("INDEX" if seg == "IDX_I" else "OPTIDX")
        # TRAP #95: this call was previously UNTHROTTLED (no acquire) and the
        # tz-mixed epoch math below threw on every call AFTER the request —
        # so the 30s cache never populated and every pos_monitor tick burned a
        # raw Dhan candle call invisible to the cross-process limiter.
        _drl.set_context("Monitor:CandleClose")
        if not _drl.acquire("candle"):
            return None   # gate saturated/cooldown — skip this tick, cache/next tick covers it
        r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=headers, json={
            "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
            "expiryCode": 0, "fromDate": date_str, "toDate": date_str}, timeout=8)
        if r.status_code == 429:
            _drl.note_429()
            return None
        d = r.json()
        if not d.get("close"):
            return None
        closes = d["close"]
        timestamps = d["timestamp"]
        now_epoch = int(_t.time())   # Dhan candle timestamps are plain epoch seconds
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
# Max age (sec) a dhan_feed WS tick may have before the monitor treats it as
# stale and falls through to the fresh REST/shared_ltp_cache path. Guards the
# "dead-subscription frozen tick pins SL to a stale price" bug (a contract whose
# WS died on a 429 kept its last tick forever, non-zero, so get_quote()'s value
# short-circuited the fallback → SL never saw the real loss). Falling through
# costs nothing: ltp_poller keeps shared_ltp_cache warm every ~1.5s.
# Value lives in dhan_feed now — smart_order (order pricing) and strategy_safety
# (liquidity gate) need the same number, and three copies of it is how the two
# of them ended up with no guard at all.
# The import is local because this file only ever imports dhan_feed lazily inside
# functions; at module scope the name doesn't exist. dhan_feed itself pulls in
# stdlib only (dhanhq is lazy inside its own thread), so importing it here is free.
import dhan_feed as _dhan_feed_const
_FEED_MAX_AGE      = _dhan_feed_const.FEED_MAX_AGE
_STALE_FEED_ALERTS = {}    # {sec_id: (first_unreliable_ts, sym)} — track how long a leg has had NO usable price
_STALE_FEED_FIRED  = set() # {sec_id} — sids currently surfaced on the alert banner (write file only on change)
_pos_lock_state    = {}    # {pos_id: {armed,peak,floor,breach_since,fired,prev_mtm}} — per-instrument trailing-lock state machine (2026-07-02 redesign)

# Restore peak + history from file on startup — so restart mid-day doesn't reset the floor.
# Only restore if history entries exist and were written TODAY (check file mtime).
_trailing_peak_pnl = 0.0
_daily_peak_ever   = 0.0   # monotonic daily max — NEVER resets, used for graph floor line
_peak_pnl_history  = []
# IST "today" this peak state belongs to — used by pos_monitor_loop to detect a real
# midnight rollover on a long-running process (see TRAP: mtime-after-write self-defeat below).
_peak_day_str = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
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

# Per-position trailing-lock state persists to disk (same pattern as
# peak_pnl_history.json / kill_floor_state.json) — a mid-day dashboard restart
# must not wipe a position's trailing-lock memory (TRAP #38's failure shape,
# per-instrument equivalent). Keyed by order_store row id, so entries survive
# restarts and never cross-contaminate.
_POS_LOCK_STATE_FILE = BASE_DIR / "data" / "pos_lock_state.json"
try:
    if _POS_LOCK_STATE_FILE.exists():
        _pls_init = json.loads(_POS_LOCK_STATE_FILE.read_text())
        if _pls_init.get("day") == _peak_day_str and isinstance(_pls_init.get("state"), dict):
            for _pk, _pv in _pls_init["state"].items():
                try:
                    _pos_lock_state[int(_pk)] = _pv
                except (ValueError, TypeError):
                    _pos_lock_state[_pk] = _pv
            if _pos_lock_state:
                print(f"[TRAILING-LOCK] Restored {len(_pos_lock_state)} per-instrument lock state(s) "
                      f"after restart.", flush=True)
except Exception as _e_pls:
    print(f"[TRAILING-LOCK] pos_lock_state restore failed (ok, starting fresh): {_e_pls}", flush=True)


def _save_pos_lock_state():
    try:
        _POS_LOCK_STATE_FILE.write_text(json.dumps({"day": _peak_day_str, "state": _pos_lock_state}))
    except Exception:
        pass


# Default Target/SL exit profile per-position state (2026-07-04) — {pos_id:
# {peak, prev_mtm, fired}}. Persisted like pos_lock_state so a mid-day restart
# doesn't wipe a position's trailing peak (TRAP #38 shape).
_tsl_state = {}
_TSL_STATE_FILE = BASE_DIR / "data" / "tsl_state.json"
try:
    if _TSL_STATE_FILE.exists():
        _tsl_init = json.loads(_TSL_STATE_FILE.read_text())
        if _tsl_init.get("day") == _peak_day_str and isinstance(_tsl_init.get("state"), dict):
            for _tk, _tv in _tsl_init["state"].items():
                try:
                    _tsl_state[int(_tk)] = _tv
                except (ValueError, TypeError):
                    _tsl_state[_tk] = _tv
            if _tsl_state:
                print(f"[DEFAULT-TSL] Restored {len(_tsl_state)} position state(s) after restart.", flush=True)
except Exception as _e_tsl:
    print(f"[DEFAULT-TSL] tsl_state restore failed (ok, starting fresh): {_e_tsl}", flush=True)


def _save_tsl_state():
    try:
        _TSL_STATE_FILE.write_text(json.dumps({"day": _peak_day_str, "state": _tsl_state}))
    except Exception:
        pass


# _trailing_lock_fired_today() DELETED (2026-07-16) — it was risk_gate's
# kill_floor_fired_today() with different date math (naive server clock vs IST),
# and its only caller was _core/webhook_executor importing it back out of this UI
# module inside a try/except: pass. LESSONS TRAP #56 already recorded that this
# copy "fails PERMISSIVE, not safe". Use risk_gate.kill_floor_fired_today().


_last_invariant_check = 0.0   # invariant_guard cooldown (read-only sentinel)


def pos_monitor_loop():
    """Monitors open positions for SL_PCT, TP_PCT hits and tracks MAX/MIN LTP."""
    import time
    import order_store
    import dhan_feed
    from datetime import timedelta
    global _trailing_peak_pnl, _daily_peak_ever, _peak_pnl_history, _peak_ltp_cache, _peak_day_str, _pos_lock_state, _kf_state, _tsl_state

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
                # Exclude CAPITAL_BLOCKED rows (TRAP #92) — a blocked entry was
                # never actually placed at the broker, so it always looks
                # "flat" to _check_flat(), and broker_sync would then record a
                # bogus "exit" for it using whatever real fill happens to
                # exist for that contract that day (from a totally unrelated
                # trade) — netting then pairs the blocked entry's placeholder
                # price against that unrelated exit, producing an impossible
                # P&L (found live: a NIFTY entry recorded at the INDEX level
                # during a capital-block, later "closed" at a real option
                # premium — a ₹15L+ phantom profit). Same root gap as TRAP #86,
                # just a different consumer of the same unfiltered "open" list.
                # SUPERSEDED (2026-07-23, B2): the heuristic ghost-sync recorded
                # exits by fill-SIGNATURE/trade-id and could partial-record or hit
                # the TRAP #60 "fill already used" skip (that's how the arschain
                # phantom slipped through). reconcile_broker.mirror_if_due (below)
                # now mirrors the broker's trade book by ORDER_ID authoritatively —
                # ONE reconciler, no fill-signature guessing. Two reconcilers with
                # different keys would fight (partial-record → residual), so this
                # auto-call is disabled. The manual "🔄 Sync from Broker" button
                # still calls force_sync on demand.
                _ghost_ids = None
                _ = _bsync   # keep import referenced for the on-demand button path
            except Exception as _bse:
                print(f"[broker_sync] skipped (error): {_bse}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            # ── Untracked-position scan (TRAP #58) — mirror image of the ghost
            # sync above: catches a broker position that order_store has NO row
            # for at all (e.g. a restart landed mid-order, killing the process
            # before order_store.record() ran — no SIGTERM handler exists
            # anywhere to prevent that). Independent of open_pos on purpose —
            # must work even if the orphan is the ONLY position that exists.
            # SUPERSEDED (2026-07-23, B2): reconcile_broker.mirror_if_due (below)
            # catches a broker position with no app row too — an external order in
            # the trade book that the app never recorded → it records it. The full
            # trade book is strictly more complete than diffing current positions,
            # so this heuristic auto-scan is disabled to keep ONE authoritative
            # reconciler. (Dhan auto-adopt was irrelevant — algo trades on Kite.)
            # ─────────────────────────────────────────────────────────────────

            # ── Auto manual-trade reconcile (2026-07-02) — same "🧾 Reconcile
            # vs Broker" logic the manual button triggers, now also running on
            # its own cooldown so a Kite entry+exit round-trip placed directly
            # on Zerodha (SL/Target limit, or a fresh manual entry) that both
            # completed inside one untracked-scan gap still lands in
            # order_store automatically. Button stays available for on-demand
            # use — this doesn't replace it, just removes the need to click it
            # every time. ──
            # AUTHORITATIVE reconciler (2026-07-23, B2): replaces the heuristic
            # reconcile_if_due (which kept mis-recording manual closes by fill-
            # signature, causing the recurring phantom/whack-a-mole). This mirrors
            # the broker's trade book by order_id — records each external order once,
            # aggregated (netting-safe), idempotent; ambiguous cases are flagged, not
            # written. invariant_guard (below) is the independent watchdog. See
            # _ops/reconcile_broker.py + ADR.
            try:
                import reconcile_broker as _rb
                _rb.mirror_if_due(broker_name="kite", log=print)
            except Exception as _rce:
                print(f"[reconcile_broker] auto-reconcile skipped (error): {_rce}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            # ── Proactive invariant guard (2026-07-20) — READ-ONLY sentinel:
            # "does the app still match reality + do the always-true rules hold?"
            # Fires a LOUD alert (notify → dashboard banner) on ANY divergence:
            # app-net ≠ broker-net (phantom/ghost/double-count), blank symbol, ₹0
            # fill, one trade-id on two rows, insane MTM. Catches an UNKNOWN bug
            # BEFORE it compounds — no bug-name needed, just detect app ≠ reality.
            # Never places/cancels an order. ~120s cooldown. See _ops/invariant_guard.py.
            try:
                global _last_invariant_check
                if _time.time() - _last_invariant_check >= 120:
                    _last_invariant_check = _time.time()
                    import invariant_guard as _ig
                    _ig.run(alert=True, log=print)
            except Exception as _ige:
                print(f"[invariant_guard] skipped (error): {_ige}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            # Tracks ids already squared-off THIS pass (e.g. as a hedge group
            # sibling) — without this, the for-loop below would re-process a
            # sibling leg that _do_squareoff already closed earlier this pass.
            _closed_ids = set()

            # ── Per-instrument trailing lock (2026-07-02 redesign) ────────────
            # Config: risk_gate.per_instrument_lock_config() — same arm+gap+
            # confirm state machine as the account-level KILL-FLOOR below,
            # scoped to ONE open position instead of the whole account. Old
            # flat-₹/%-of-peak design (no arm threshold, no confirm window)
            # replaced outright — that was the exact misfire shape the
            # account-level lock also had before this same fix (TRAP #77 family).
            try:
                import risk_gate as _rg
                _pi_cfg = _rg.per_instrument_lock_config()

                # Always compute realized+unrealized for Stats graph — regardless of lock config
                _realized = _rg._today_realized_pnl()
                _unrealized = 0.0
                _mtm_unreliable = False   # any open position priced from nothing → kill-floor must not fire this cycle

                # ── Per-strategy MTM snapshot (task 73) ──────────────────────────
                # Feeds the Today's Peak "Graph" view's per-strategy line + the
                # Realised/Unrealised/Current switch. Keyed the SAME way the
                # dashboard's Summary rows are (strategy key, else MANUAL/WEBHOOK by
                # source) so the frontend can look a row up directly. Each value =
                # [realized, unrealized]. "__all" = whole-account display totals.
                # DELIBERATELY display-only + includes PAPER (the account kill-floor's
                # own _realized above stays live-only by design — Critical Rule 6);
                # computed entirely from `data` already fetched + the LTP loop below,
                # so ZERO extra Dhan calls.
                def _mkey(_r):
                    _s = str(_r.get("source") or "STRATEGY").upper()
                    return _s if _s in ("MANUAL", "WEBHOOK") else (_r.get("strategy") or "STRATEGY")
                _mtm_by_strat = {}
                _disp_realized = 0.0
                for _d in data.get("details", []):
                    _pnl = float(_d.get("pnl") or 0)
                    _disp_realized += _pnl
                    _mtm_by_strat.setdefault(_mkey(_d), [0.0, 0.0])[0] += _pnl

                _active_pos = [_p for _p in open_pos
                               if _p.get("status") != "blocked"
                               and "CAPITAL_BLOCKED" not in (_p.get("tags") or [])]
                for _p in _active_pos:
                    _sid = _p.get("sec_id")
                    _seg = "NSE_EQ" if _p.get("instrument") == "EQUITY" else "NSE_FNO"
                    _live_ltp = float((dhan_feed.get_quote(_sid, max_age=_FEED_MAX_AGE) or {}).get("ltp") or 0) or \
                                _rest_ltp_fallback(_sid, _seg) or 0.0
                    if _live_ltp > 0:
                        _ltp = _live_ltp
                        _peak_ltp_cache[_sid] = _ltp        # fresh — update cache
                        _STALE_FEED_ALERTS.pop(_sid, None)  # live price back → clear stale-feed flag
                    else:
                        _ltp = _peak_ltp_cache.get(_sid, 0.0)  # stale feed — use last known
                        # No live price from ANY source (feed stale + REST failed).
                        # Track since-when so a persistently-blind leg (its SL can't
                        # fire on a stale price) gets surfaced on the alert banner.
                        _STALE_FEED_ALERTS.setdefault(_sid, (_time.time(), _p.get("sym") or _sid))
                    _epx = float(_p.get("entry_price") or _p.get("price") or 0)
                    _qty = int(_p.get("qty") or 0)
                    if _ltp > 0 and _epx > 0 and _qty:
                        _unrl = (_ltp - _epx) * _qty if _p.get("entry") == "BUY" \
                                else (_epx - _ltp) * _qty
                        _unrealized += _unrl
                        _mtm_by_strat.setdefault(_mkey(_p), [0.0, 0.0])[1] += _unrl
                    else:
                        # this leg contributed NOTHING to MTM (no price anywhere) —
                        # total is understated; kill-floor treats this cycle as
                        # unreliable (never fire a kill-all on incomplete data)
                        _mtm_unreliable = True
                _total_pnl = _realized + _unrealized

                # ── Stale-feed alert reconcile (banner) ──────────────────────
                # Any open leg that's had NO usable live price for > _STALE_ALERT_SECS
                # is running blind — its per-position SL literally cannot fire on a
                # frozen price. Surface it on the dashboard alert banner (dict entries
                # keyed "stale_feed"), and clear them the moment price returns. Only
                # rewrite the file when the alerted set actually changes.
                try:
                    _STALE_ALERT_SECS = 90
                    _now_sf = _time.time()
                    _cur_stale = {}   # sid -> msg
                    for _sid_sf, _v_sf in list(_STALE_FEED_ALERTS.items()):
                        _since, _sym_sf = _v_sf
                        _age_sf = _now_sf - _since
                        if _age_sf > _STALE_ALERT_SECS:
                            _cur_stale[str(_sid_sf)] = (
                                f"📡 {_sym_sf} — live feed {int(_age_sf)}s stale, "
                                f"koi price source nahi. SL/target enforcement is leg pe PAUSED "
                                f"(kill-floor bhi freeze). Token/feed check karo.")
                    if set(_cur_stale) != _STALE_FEED_FIRED:
                        _af_sf = BASE_DIR / "data" / "downloader_alert.json"
                        try:
                            _al_sf = json.loads(_af_sf.read_text())
                            if not isinstance(_al_sf, list):
                                _al_sf = []
                        except Exception:
                            _al_sf = []
                        # drop our previous stale_feed entries, re-add current ones
                        _al_sf = [a for a in _al_sf
                                  if not (isinstance(a, dict) and a.get("key") == "stale_feed")]
                        for _sid_sf, _msg_sf in _cur_stale.items():
                            _al_sf.append({"key": "stale_feed", "level": "error",
                                           "sec_id": _sid_sf, "msg": _msg_sf})
                        _af_sf.write_text(json.dumps(_al_sf))
                        _STALE_FEED_FIRED.clear()
                        _STALE_FEED_FIRED.update(_cur_stale.keys())
                except Exception as _sfe:
                    print(f"[STALE-FEED] alert reconcile skipped: {_sfe}", flush=True)
                # ─────────────────────────────────────────────────────────────

                # finalize per-strategy dict: round + account rollup
                for _k in list(_mtm_by_strat.keys()):
                    _mtm_by_strat[_k] = [round(_mtm_by_strat[_k][0], 2), round(_mtm_by_strat[_k][1], 2)]
                _mtm_by_strat["__all"] = [round(_disp_realized, 2), round(_unrealized, 2)]

                # Day-rollover check — MUST run before the peak/history update below.
                # trader_dashboard runs as a long-lived systemd service and is NOT
                # restarted every trading day, so the module-load-time restore (above)
                # only resets peak on an actual process restart. Previously this was
                # detected by re-reading peak_pnl_history.json's mtime — but that file
                # was rewritten (mtime bumped to "now") a few lines below in the SAME
                # iteration, so by the time the check ran, mtime was always "today" and
                # the reset branch could never fire. Fixed: track the day explicitly in
                # _peak_day_str instead of relying on the file's own mtime.
                _today_str = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
                if _today_str != _peak_day_str:
                    try:
                        if _peak_pnl_history:
                            _prev_arch = BASE_DIR / "data" / f"peak_pnl_history_{_peak_day_str}.json"
                            if not _prev_arch.exists():
                                _prev_arch.write_text(json.dumps(_peak_pnl_history))
                    except Exception:
                        pass
                    _peak_pnl_history  = []
                    _trailing_peak_pnl = 0.0
                    _daily_peak_ever   = 0.0
                    _pos_lock_state    = {}
                    _tsl_state         = {}
                    _peak_day_str      = _today_str
                    _save_pos_lock_state()
                    _save_tsl_state()
                    _pending_group_close.clear()   # yesterday's queued closes are moot (EOD handled them)
                    _save_pending_group_close()
                    _kf_state.update({"day": _today_str, "armed": False, "peak": 0.0,
                                      "floor": None, "breach_since": None, "fired": False,
                                      "prev_mtm": None})
                    _save_kf_state()
                    print(f"[TRAILING-LOCK] New trading day ({_today_str}) — peak/DD/floor + kill-floor reset.", flush=True)

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
                    _mtm_by_strat,                   # v[4]: {key: [realized, unrealized]}, "__all" = account (task 73)
                ))
                # Cap sized for a FULL trading day, not a rolling window. Loop cycles
                # every ~5s (time.sleep(5) at the bottom, plus whatever the SL/TP/
                # kill-floor checks above it take) — market hours 09:15-15:30 = 375min
                # = 22500s, so even at the fastest possible 5s/cycle that's ≤4500
                # entries/day. The OLD cap of 500 meant the graph only ever showed the
                # most recent ~500×5s ≈ 42 MINUTES — found live 2026-07-02, user
                # reported the Peak P&L graph "looks like only the last 1hr", which is
                # exactly this symptom (worse: less than 1hr, since real cycles run
                # slower than the bare 5s sleep). 6000 gives headroom above the
                # fastest-possible-cycle worst case for the whole session.
                if len(_peak_pnl_history) > 6000:
                    _peak_pnl_history = _peak_pnl_history[-6000:]
                # Write to file so dashboard process can read it via API
                try:
                    _phf = BASE_DIR / "data" / "peak_pnl_history.json"
                    _phf.write_text(json.dumps(_peak_pnl_history))
                except Exception:
                    pass

                if _pi_cfg["enabled"]:
                    _pos_lock_state_dirty = False
                    for _p in _active_pos:
                        _pid = _p.get("id")
                        if not _pid or _pid in _closed_ids:
                            continue
                        _pst = _pos_lock_state.get(_pid) or {
                            "armed": False, "peak": 0.0, "floor": None,
                            "breach_since": None, "fired": False, "prev_mtm": None,
                        }
                        if _pst.get("fired"):
                            continue   # already fired for this position today — resolved, leave it

                        _sid = _p.get("sec_id")
                        _seg = "NSE_EQ" if _p.get("instrument") == "EQUITY" else "NSE_FNO"
                        _ltp = float((dhan_feed.get_quote(_sid, max_age=_FEED_MAX_AGE) or {}).get("ltp") or 0) or \
                               _rest_ltp_fallback(_sid, _seg) or 0.0
                        _pi_unreliable = False
                        if _ltp > 0:
                            _peak_ltp_cache[_sid] = _ltp
                        else:
                            _ltp = _peak_ltp_cache.get(_sid, 0.0)
                            if _ltp <= 0:
                                _pi_unreliable = True

                        _epx = float(_p.get("entry_price") or _p.get("price") or 0)
                        _qty = int(_p.get("qty") or 0)
                        _unrl = 0.0
                        if _ltp > 0 and _epx > 0 and _qty:
                            _unrl = (_ltp - _epx) * _qty if _p.get("entry") == "BUY" \
                                     else (_epx - _ltp) * _qty
                        else:
                            _pi_unreliable = True

                        _was_armed_pi = _pst.get("armed", False)
                        _was_breaching_pi = _pst.get("breach_since") is not None
                        _pst, _pst_changed = _rg.advance_trailing_lock(
                            _pst, _unrl, _pi_cfg["arm_rs"], _pi_cfg["gap_rs"], _pi_cfg["confirm_secs"],
                            time.time(), mtm_unreliable=_pi_unreliable)
                        _pos_lock_state[_pid] = _pst
                        if _pst_changed:
                            _pos_lock_state_dirty = True

                        if _pst["armed"] and not _was_armed_pi:
                            print(f"[TRAILING-LOCK] [PER-INSTRUMENT] {_p.get('sym')} (ID {_pid}) ARMED — "
                                  f"confirmed peak ₹{_pst['peak']:.0f} crossed arm ₹{_pi_cfg['arm_rs']:.0f}; "
                                  f"floor trails at peak − ₹{_pi_cfg['gap_rs']:.0f}", flush=True)
                        if _pst.get("breach_since") is not None and not _was_breaching_pi:
                            print(f"[TRAILING-LOCK] [PER-INSTRUMENT] {_p.get('sym')} P&L ₹{_unrl:.0f} below "
                                  f"floor ₹{_pst['floor']:.0f} — confirm timer started "
                                  f"({_pi_cfg['confirm_secs']:.0f}s)", flush=True)
                        if _pst.get("breach_since") is None and _was_breaching_pi and not _pst["fired"]:
                            print(f"[TRAILING-LOCK] [PER-INSTRUMENT] {_p.get('sym')} P&L ₹{_unrl:.0f} back above "
                                  f"floor ₹{_pst['floor']:.0f} — confirm timer reset", flush=True)

                        if _pst["fired"]:
                            print(f"[TRAILING-LOCK] [PER-INSTRUMENT] 🔒 FIRED — {_p.get('sym')} (ID {_pid}) "
                                  f"P&L ₹{_unrl:.0f} stayed below floor ₹{_pst['floor']:.0f} for "
                                  f"{_pi_cfg['confirm_secs']:.0f}s (peak was ₹{_pst['peak']:.0f}) "
                                  f"— squaring off this position only", flush=True)
                            if _ltp <= 0:
                                print(f"[TRAILING-LOCK] [PER-INSTRUMENT] ⚠️ {_p.get('sym')} — no price this "
                                      f"instant; queued for forced close next cycle", flush=True)
                                _pgc_queue(_p, _sid, "TRAILING_PROFIT_LOCK_PI")
                            elif _pre_exit_guard(_p, _sid, "TRAILING_PROFIT_LOCK_PI", _closed_ids, log=print):
                                pass
                            else:
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
                                            is_exit=True,
                                        )
                                    else:
                                        import order_store as _os
                                        _os.record(
                                            side=_exit_side, qty=_p["qty"], price=_ltp,
                                            source=_p.get("source",""), strategy=_p.get("strategy",""),
                                            mode=_p.get("mode","paper"), broker=_bname,
                                            symbol=_p["sym"], instrument=_p.get("instrument",""),
                                            trad_sym=_p["sym"], sec_id=_sid, segment=_seg,
                                            status="paper", tags=["TRAILING_PROFIT_LOCK"],
                                        )
                                    _closed_ids.add(_pid)
                                except Exception as _te:
                                    print(f"[TRAILING-LOCK] [PER-INSTRUMENT] squareoff failed for "
                                          f"{_p.get('sym')}: {_te}", flush=True)
                            # Multi-leg atomicity: this path places its primary-leg
                            # exit directly (above), NOT through _do_squareoff, so it
                            # would orphan a group'd structure's siblings. Queue them
                            # for a group-aware forced close this same cycle (see
                            # _queue_group_siblings). No-op if group_id unset.
                            _queue_group_siblings(_p, open_pos, _closed_ids, "TRAILING_PROFIT_LOCK_PI")
                            # NOTE (2026-07-02, user decision, TRAP #77): deliberately does NOT
                            # write the day-level trailing_lock_fired flag. That flag blocks
                            # ALL new entries account-wide (webhook _do_entry + strategies via
                            # risk_gate.gating_status check it) — correct for the account-level
                            # KILL-FLOOR, but would defeat the entire point of per-instrument:
                            # one position's floor firing is a closed, resolved event and must
                            # not stop other symbols/strategies from trading.

                    # Prune state for positions no longer open, persist the rest —
                    # restart mid-day must not reset trailing-lock memory to zero.
                    _active_pi_ids = {_p.get("id") for _p in _active_pos}
                    for _k in list(_pos_lock_state.keys()):
                        if _k not in _active_pi_ids:
                            del _pos_lock_state[_k]
                            _pos_lock_state_dirty = True
                    if _pos_lock_state_dirty:
                        _save_pos_lock_state()
            except Exception as _trail_e:
                print(f"[TRAILING-LOCK] check error (skipped): {_trail_e}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            # ── Default Target/SL exit profile (2026-07-04, user-designed) ─────
            # Per-position rupee-based fixed-target + stepped-trailing SL +
            # aggressive phase after X% of target + min_cushion whipsaw guard.
            # Global default (RMS tab) on every active leg when enabled. Config ₹
            # are PER-LOT → scaled by lots (=qty/lot_size, lot_size from scrip
            # master, never guessed — unknown lot_size = skip, never assume).
            # Fires squareoff of THAT position only (does NOT set the account-wide
            # entry-block flag — per-instrument scope, TRAP #77). Reuses
            # risk_gate.advance_target_sl (confirmed-peak spike guard); fire routes
            # through _pre_exit_guard (fresh flat-check, TRAP #75). LTP reads the
            # cycle-warm _peak_ltp_cache first → zero extra Dhan calls.
            try:
                import risk_gate as _rg_tsl
                import dhan_master as _dm_tsl
                _tslc = _rg_tsl.default_target_sl_config()
                # Per-strategy ⚙ values (task 81) — one config per strategy per
                # cycle (cached, no extra I/O per position beyond first hit).
                _tslc_by_strat = {}
                def _tslc_for(_s):
                    if _s not in _tslc_by_strat:
                        try:
                            _tslc_by_strat[_s] = _rg_tsl.default_target_sl_config(_s)
                        except Exception:
                            _tslc_by_strat[_s] = _tslc
                    return _tslc_by_strat[_s]
                if _tslc.get("feature_on"):
                    _tsl_dirty = False
                    # Only positions stamped AGGR_TSL at entry (opened while
                    # 'aggressive' was the chosen default SL mode) — so switching
                    # TO aggressive mid-day never grabs already-open trades
                    # ("new trades only", 2026-07-07). Older aggressive positions
                    # (pre-marker) also carry it going forward; nothing without
                    # the marker is touched.
                    _active_tsl = [_p for _p in open_pos
                                   if _p.get("status") != "blocked"
                                   and "CAPITAL_BLOCKED" not in (_p.get("tags") or [])
                                   and any(str(_t).startswith("AGGR_TSL") for _t in (_p.get("tags") or []))]
                    for _p in _active_tsl:
                        _pid = _p.get("id")
                        if not _pid or _pid in _closed_ids:
                            continue
                        _tst = _tsl_state.get(_pid) or {"peak": 0.0, "prev_mtm": None, "fired": False}
                        if _tst.get("fired"):
                            continue
                        _sid = _p.get("sec_id")
                        _seg = "NSE_EQ" if _p.get("instrument") == "EQUITY" else "NSE_FNO"
                        _qty = int(_p.get("qty") or 0)
                        _lotsz = _dm_tsl.get_lot_size_by_sec_id(_sid)
                        if not _lotsz or _qty <= 0:
                            continue   # lot_size unknown → don't guess, skip this leg
                        _lots = max(1, round(_qty / _lotsz))
                        _ltp = _peak_ltp_cache.get(_sid, 0.0)
                        if _ltp <= 0:
                            _ltp = float((dhan_feed.get_quote(_sid, max_age=_FEED_MAX_AGE) or {}).get("ltp") or 0) or \
                                   _rest_ltp_fallback(_sid, _seg) or 0.0
                        _epx = float(_p.get("entry_price") or _p.get("price") or 0)
                        _tsl_unreliable = False
                        _unrl = 0.0
                        if _ltp > 0 and _epx > 0 and _qty:
                            _unrl = (_ltp - _epx) * _qty if _p.get("entry") == "BUY" \
                                     else (_epx - _ltp) * _qty
                        else:
                            _tsl_unreliable = True   # no price → freeze (never fire on bad data)
                        _tslc_p = _tslc_for(_p.get("strategy") or "")
                        _tst, _act, _sl_lvl = _rg_tsl.advance_target_sl(
                            _tst, _unrl, _tslc_p, _lots, mtm_unreliable=_tsl_unreliable)
                        # Smart-split candle-close confirm (2026-07-09, user) — in
                        # the PROFIT-lock zone (sl_level >= 0: the SL has ratcheted
                        # into locked profit) don't stop out on an intraday WICK;
                        # require the last CLOSED 1-min candle to confirm the breach
                        # (wick-proof whipsaw guard — "line ke upar close hoga tabhi
                        # exit"). The LOSS zone (sl_level < 0) still fires on the live
                        # tick for immediate capital protection. Candle data
                        # unavailable → fail-safe: fire on the tick (never leave a
                        # real breach unhandled). TARGET is unaffected (locks profit
                        # immediately). Only ~1 extra Dhan candle call at the moment
                        # a profit-zone stop would fire, and it's 30s-cached.
                        if _act == "SL" and _sl_lvl >= 0:
                            _cc = _last_closed_candle_close(_sid, _seg)
                            if _cc and _cc > 0:
                                _cc_mtm = (_cc - _epx) * _qty if _p.get("entry") == "BUY" \
                                          else (_epx - _cc) * _qty
                                if _cc_mtm > _sl_lvl:   # last closed candle did NOT breach → wait
                                    _act = None
                                    _tst["fired"] = False   # re-evaluate next cycle / candle
                                    print(f"[DEFAULT-TSL] profit-lock SL wick ignored — "
                                          f"{_p.get('sym')} (ID {_pid}) candle-close ₹{_cc:.2f} "
                                          f"(mtm ₹{_cc_mtm:.0f}) still above SL ₹{_sl_lvl:.0f}", flush=True)
                        _tsl_state[_pid] = _tst
                        _tsl_dirty = True
                        if _act:
                            # Carry the ₹ level in the tag so the Exit Reason column
                            # can show "kitna SL/target tha" (#6, 2026-07-07). TARGET
                            # → the whole-position target ₹; SL → the signed SL level
                            # (_sl_lvl: negative = loss stop e.g. -2000, positive =
                            # locked-in profit). Prefix still matches _EXIT_REASON_PREFIXES.
                            if _act == "TARGET":
                                _reason = f"DEFAULT_TSL_TARGET:{_tslc_p['target_per_lot']*_lots:.0f}"
                            else:
                                _reason = f"DEFAULT_TSL_SL:{_sl_lvl:.0f}"
                            print(f"[DEFAULT-TSL] 🎯 FIRED ({_act}) — {_p.get('sym')} (ID {_pid}) "
                                  f"P&L ₹{_unrl:.0f} vs target ₹{_tslc_p['target_per_lot']*_lots:.0f} / "
                                  f"SL ₹{_sl_lvl:.0f} (peak ₹{_tst.get('peak',0):.0f}, {_lots} lot) "
                                  f"— squaring off this position", flush=True)
                            if _ltp <= 0:
                                _pgc_queue(_p, _sid, _reason)
                            elif _pre_exit_guard(_p, _sid, _reason, _closed_ids, log=print):
                                pass
                            else:
                                try:
                                    import smart_order
                                    from brokers import get_broker
                                    _exit_side = "SELL" if _p.get("entry") == "BUY" else "BUY"
                                    _bname = _p.get("broker") or "dhan"
                                    if _p.get("mode") == "live":
                                        _br = get_broker(_bname)
                                        smart_order.execute(
                                            _exit_side, _p["sym"], _sid, _seg, _p["qty"], _p["sym"],
                                            _p["mode"], _br, log=print, tag="DEFAULT_TSL",
                                            source=_p.get("source", ""), strategy=_p.get("strategy", ""),
                                            instrument=_p.get("instrument", ""), broker_name=_bname,
                                            extra_tags=[_reason], is_exit=True,
                                        )
                                    else:
                                        import order_store as _os
                                        _os.record(
                                            side=_exit_side, qty=_p["qty"], price=_ltp,
                                            source=_p.get("source", ""), strategy=_p.get("strategy", ""),
                                            mode=_p.get("mode", "paper"), broker=_bname,
                                            symbol=_p["sym"], instrument=_p.get("instrument", ""),
                                            trad_sym=_p["sym"], sec_id=_sid, segment=_seg,
                                            status="paper", tags=[_reason],
                                        )
                                    _closed_ids.add(_pid)
                                except Exception as _tse:
                                    print(f"[DEFAULT-TSL] squareoff failed for {_p.get('sym')}: {_tse}", flush=True)
                            # Multi-leg atomicity: primary-leg exit placed directly
                            # above (not via _do_squareoff) → queue any group'd
                            # siblings for a group-aware forced close this same cycle,
                            # so a DEFAULT_TSL hit on one leg never orphans the
                            # structure. No-op if group_id unset. (See _queue_group_siblings.)
                            _queue_group_siblings(_p, open_pos, _closed_ids, _reason)
                    _active_tsl_ids = {_p.get("id") for _p in _active_tsl}
                    for _k in list(_tsl_state.keys()):
                        if _k not in _active_tsl_ids:
                            del _tsl_state[_k]
                            _tsl_dirty = True
                    if _tsl_dirty:
                        _save_tsl_state()
            except Exception as _tsl_e:
                print(f"[DEFAULT-TSL] check error (skipped): {_tsl_e}", flush=True)
            # ─────────────────────────────────────────────────────────────────

            # ── Account-level trailing KILL-FLOOR (2026-07-02, user-designed) ──
            # Replaces the old aggregate trailing lock. Semantics locked with
            # the user + calibrated on 2026-07-02's real trades (peak ₹5,193,
            # final ₹1,937): arm when confirmed MTM crosses arm_rs; floor =
            # confirmed_peak − gap_rs, ratchets ₹1-fine on every confirmed new
            # high, NEVER moves down; MTM must stay below the floor for
            # confirm_secs CONSECUTIVE seconds to fire (one spike/whipsaw never
            # fires — the old floor's misfire root cause). "Confirmed" = min of
            # two consecutive readings, so a single bad tick can never inflate
            # the peak either. Runs independent of trailing_lock_mode. Fire →
            # every open position squared off through _pre_exit_guard (webhook
            # claim + fresh flat-check) + day-level entry-block flag (webhook
            # honors it directly; strategies via risk_gate.gating_status).
            try:
                import risk_gate as _rg_kf   # own import — never depend on the previous block's local
                _kfc = _rg_kf.kill_floor_config()
                if _kfc["enabled"] and not _kf_state["fired"]:
                    _was_armed_kf = _kf_state.get("armed", False)
                    _was_breaching_kf = _kf_state.get("breach_since") is not None
                    _kf_state, _kf_changed = _rg_kf.advance_trailing_lock(
                        _kf_state, _total_pnl, _kfc["arm_rs"], _kfc["gap_rs"], _kfc["confirm_secs"],
                        time.time(), mtm_unreliable=_mtm_unreliable)
                    if _kf_state["armed"] and not _was_armed_kf:
                        print(f"[KILL-FLOOR] ARMED — confirmed peak ₹{_kf_state['peak']:.0f} crossed "
                              f"arm threshold ₹{_kfc['arm_rs']:.0f}; floor trails at peak − ₹{_kfc['gap_rs']:.0f}", flush=True)
                    if _kf_state.get("breach_since") is not None and not _was_breaching_kf:
                        print(f"[KILL-FLOOR] MTM ₹{_total_pnl:.0f} below floor ₹{_kf_state['floor']:.0f} "
                              f"— confirm timer started ({_kfc['confirm_secs']:.0f}s)", flush=True)
                    if _kf_state.get("breach_since") is None and _was_breaching_kf and not _kf_state["fired"]:
                        print(f"[KILL-FLOOR] MTM ₹{_total_pnl:.0f} back above floor "
                              f"₹{_kf_state['floor']:.0f} — confirm timer reset", flush=True)
                    if _kf_state["fired"]:
                        print(f"[KILL-FLOOR] 🔒 FIRED — MTM ₹{_total_pnl:.0f} stayed below floor "
                              f"₹{_kf_state['floor']:.0f} for {_kfc['confirm_secs']:.0f}s "
                              f"(peak was ₹{_kf_state['peak']:.0f}). KILLING ALL POSITIONS.", flush=True)
                        for _p in list(open_pos):
                            _sid = _p.get("sec_id")
                            if not _sid or _p.get("status") == "blocked": continue
                            if "CAPITAL_BLOCKED" in (_p.get("tags") or []): continue
                            if _p.get("id") in _closed_ids: continue
                            _seg = "NSE_EQ" if _p.get("instrument") == "EQUITY" else "NSE_FNO"
                            _ltp2 = float((dhan_feed.get_quote(_sid, max_age=_FEED_MAX_AGE) or {}).get("ltp") or 0) or \
                                    _rest_ltp_fallback(_sid, _seg) or 0.0
                            if _ltp2 <= 0:
                                try:
                                    import shared_ltp_cache as _slc_kf
                                    _ltp2 = _slc_kf.get_stale(_sid, max_age=120) or 0.0
                                except Exception:
                                    _ltp2 = 0.0
                            if _ltp2 <= 0:
                                print(f"[KILL-FLOOR] ⚠️ {_p.get('sym')} — no price this instant; "
                                      f"queued for forced close next cycle", flush=True)
                                _pgc_queue(_p, _sid, "KILL_FLOOR")
                                continue
                            if _pre_exit_guard(_p, _sid, "KILL_FLOOR", _closed_ids, log=print):
                                continue
                            try:
                                import smart_order
                                from brokers import get_broker
                                _exit_side = "SELL" if _p.get("entry") == "BUY" else "BUY"
                                _bname = _p.get("broker") or "dhan"
                                if _p.get("mode") == "live":
                                    _br = get_broker(_bname)
                                    smart_order.execute(
                                        _exit_side, _p["sym"], _sid, _seg, _p["qty"], _p["sym"],
                                        _p["mode"], _br, log=print, tag="KILLFLOOR",
                                        source=_p.get("source",""), strategy=_p.get("strategy",""),
                                        instrument=_p.get("instrument",""), broker_name=_bname,
                                        extra_tags=["KILL_FLOOR"],
                                        is_exit=True,
                                    )
                                else:
                                    import order_store as _os
                                    _os.record(
                                        side=_exit_side, qty=_p["qty"], price=_ltp2,
                                        source=_p.get("source",""), strategy=_p.get("strategy",""),
                                        mode=_p.get("mode","paper"), broker=_bname,
                                        symbol=_p["sym"], instrument=_p.get("instrument",""),
                                        trad_sym=_p["sym"], sec_id=_sid, segment=_seg,
                                        status="paper", tags=["KILL_FLOOR"],
                                    )
                                _closed_ids.add(_p.get("id"))
                            except Exception as _ke:
                                print(f"[KILL-FLOOR] squareoff failed for {_p.get('sym')}: {_ke}", flush=True)
                        # Day-level entry block: webhook checks this flag directly,
                        # every strategy via risk_gate.gating_status (added 2026-07-02)
                        try:
                            from datetime import datetime as _dtc
                            import risk_gate as _rg_kf
                            # risk_gate owns this flag's path now — the writer used
                            # naive datetime.now() while risk_gate's reader (the one
                            # that blocks every strategy) used IST. Same file, two
                            # date maths. See risk_gate.kill_floor_flag_path().
                            if _rg_kf.mark_kill_floor_fired(
                                    f"KILL_FLOOR fired at {_dtc.now().strftime('%H:%M:%S')} — "
                                    f"MTM ₹{_total_pnl:.0f} below floor ₹{_kf_state['floor']:.0f} "
                                    f"for {_kfc['confirm_secs']:.0f}s (peak ₹{_kf_state['peak']:.0f})"):
                                print(f"[KILL-FLOOR] Flag written: {_rg_kf.kill_floor_flag_path().name} — "
                                      f"ALL new entries blocked for today.", flush=True)
                            else:
                                print("[KILL-FLOOR] Flag write FAILED — new entries are NOT blocked!", flush=True)
                        except Exception as _fe:
                            print(f"[KILL-FLOOR] Flag write failed: {_fe}", flush=True)
                        try:
                            _af = BASE_DIR / "data" / "downloader_alert.json"
                            _alerts = []
                            try:
                                _alerts = json.loads(_af.read_text())
                            except Exception:
                                pass
                            _alerts.append({"key": "kill_floor", "level": "error",
                                            "msg": (f"🔒 KILL-FLOOR FIRED — profit locked at "
                                                    f"~₹{_kf_state['floor']:.0f} (peak was ₹{_kf_state['peak']:.0f}). "
                                                    f"All positions squared off, entries blocked for today.")})
                            _af.write_text(json.dumps(_alerts))
                        except Exception:
                            pass
                    if not _mtm_unreliable:
                        _kf_state["prev_mtm"] = _total_pnl
                    if _kf_changed:
                        _save_kf_state()
            except Exception as _kfe:
                print(f"[KILL-FLOOR] check error (skipped this cycle): {_kfe}", flush=True)
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
            # flush=True is load-bearing: without it, on a systemd service with
            # block-buffered stdout this error sits invisible for minutes while
            # SL/TP enforcement is down (TRAP #56's exact silent-failure mode).
            print("Pos monitor error:", e, flush=True)

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

# Disk-persisted (TRAP #74 worklist P4): a dashboard/monitor restart while a
# leg sat in this queue used to silently drop its scheduled forced-close —
# the naked leg stayed open, unprotected, with no retry and no alert. Same
# same-day-restore pattern as pos_peaks.json. Keys normalized to str
# (sec_id type varies by caller; JSON round-trip is str anyway).
_PENDING_GROUP_CLOSE_FILE = BASE_DIR / "data" / "pending_group_close.json"
try:
    if _PENDING_GROUP_CLOSE_FILE.exists():
        _pgc_init = json.loads(_PENDING_GROUP_CLOSE_FILE.read_text())
        if _pgc_init.get("day") == _peak_day_str and isinstance(_pgc_init.get("pending"), dict):
            _pending_group_close.update({str(k): v for k, v in _pgc_init["pending"].items()})
            if _pending_group_close:
                print(f"[GROUP-CLOSE] ⚠️ Recovered {len(_pending_group_close)} pending forced "
                      f"group-close leg(s) after restart: {list(_pending_group_close.keys())} — "
                      f"will force-close as soon as each resolves a price.", flush=True)
except Exception as _e_pgc:
    print(f"[GROUP-CLOSE] pending-queue restore failed (ok, starting empty): {_e_pgc}", flush=True)


def _save_pending_group_close():
    try:
        _PENDING_GROUP_CLOSE_FILE.write_text(json.dumps(
            {"day": _peak_day_str, "pending": _pending_group_close}))
    except Exception:
        pass


# ── Task 5 (2026-07-07): per-strategy keying — "strategy:sec_id", sirf sec_id
# nahi. 2 strategies same contract hold karein to bare-sec_id key ek doosre ka
# queued close overwrite/steal kar leti thi (galat strategy ki position force-
# close ho sakti thi). Pop backward-compat hai: restart-recovered purani bare
# keys bhi match hoti hain (persisted file mein old-format entries ho sakti hain).

def _pgc_key(p, sec_id):
    return f"{(p.get('strategy') or '')}:{sec_id}"


def _pgc_queue(p, sec_id, reason):
    _pending_group_close[_pgc_key(p, sec_id)] = reason
    _save_pending_group_close()


def _pgc_pop(p, sec_id):
    """Is position ka queued forced-close reason nikaalo (None = queued nahi).
    New-format key pehle; old bare-sec_id key fallback (pre-Task-5 persisted)."""
    for k in (_pgc_key(p, sec_id), str(sec_id)):
        if k in _pending_group_close:
            reason = _pending_group_close.pop(k)
            _save_pending_group_close()
            return reason
    return None


def _queue_group_siblings(p, open_pos, closed_ids, reason):
    """Multi-leg atomicity: when ONE leg of a group_id'd structure (straddle /
    strangle / condor / backspread / a sold option + its hedge) is squared off
    by a PER-LEG exit path that places its own primary-leg order DIRECTLY
    instead of through _do_squareoff (the per-instrument trailing lock and the
    DEFAULT_TSL aggressive profile), queue every still-open sibling sharing its
    group_id for a forced close.

    Without this, enabling a per-leg profit-lock on a multi-leg strategy would
    orphan the structure — one leg locked out while its siblings stay open
    (naked option SELL / broken hedge). The queued siblings are force-closed on
    THIS same cycle's `for p in open_pos` pass (via _pgc_pop -> _do_squareoff,
    which is the single group-aware exit path). This deliberately reuses the
    EXACT no-price sibling pattern _do_squareoff itself uses (see its group
    loop) rather than duplicating any order-placement logic (Rule 6B).

    No-op unless group_id is actually set — so it's inert for single-leg
    strategies and, when these profit-lock features are OFF (current default),
    it never runs at all."""
    gid = p.get("group_id")
    if not gid:
        return
    for sib in open_pos:
        if sib is p or sib.get("id") in closed_ids:
            continue
        if sib.get("group_id") != gid:
            continue
        sib_sec = sib.get("sec_id")
        if not sib_sec:
            continue
        _pgc_queue(sib, sib_sec, reason + "_GROUP")
        print(f"[GROUP-CLOSE] {sib.get('sym')} (group {gid}) queued for forced close — "
              f"sibling {p.get('sym')} was profit-locked out; structure must exit together",
              flush=True)


# ── Account-level KILL-FLOOR state (2026-07-02) — disk-persisted so a mid-day
# restart never resets the armed floor to zero (failure shape #3: RAM-only
# state; same pattern as pos_peaks.json / pending_group_close.json).
# peak       — highest CONFIRMED MTM today (min of 2 consecutive readings)
# floor      — peak − gap once armed; ratchets UP only, never down
# breach_since — epoch when MTM first went below floor (None = not breaching)
# fired      — kill-all executed today (also mirrored by the day flag file)
# prev_mtm   — last cycle's MTM (for the 2-reading confirmation)
_KILL_FLOOR_FILE = BASE_DIR / "data" / "kill_floor_state.json"
_kf_state = {"day": _peak_day_str, "armed": False, "peak": 0.0,
             "floor": None, "breach_since": None, "fired": False, "prev_mtm": None}
try:
    if _KILL_FLOOR_FILE.exists():
        _kf_init = json.loads(_KILL_FLOOR_FILE.read_text())
        if _kf_init.get("day") == _peak_day_str and isinstance(_kf_init, dict):
            for _kk in ("armed", "peak", "floor", "fired", "prev_mtm"):
                if _kk in _kf_init:
                    _kf_state[_kk] = _kf_init[_kk]
            # breach_since deliberately NOT restored — a restart mid-breach
            # restarts the confirm timer (conservative: never fire off a stale
            # timer from before the restart; worst case fire is confirm_secs late)
            if _kf_state["armed"]:
                print(f"[KILL-FLOOR] Restored after restart — armed, peak ₹{_kf_state['peak']:.0f}, "
                      f"floor ₹{(_kf_state['floor'] or 0):.0f}, fired={_kf_state['fired']}", flush=True)
except Exception as _e_kf:
    print(f"[KILL-FLOOR] state restore failed (ok, starting fresh): {_e_kf}", flush=True)


def _save_kf_state():
    try:
        _kf_state["day"] = _peak_day_str
        _KILL_FLOOR_FILE.write_text(json.dumps(_kf_state))
    except Exception:
        pass


def _pre_exit_guard(p, sec_id, exit_reason, _closed_ids, log=print):
    """Shared live-exit safety gate (P6 audit, 2026-07-02) — two checks that
    used to live ONLY inside _do_squareoff, so any exit path that placed an
    order a different way (trailing-lock squareoff) skipped both:
      1. webhook co-ownership claim — a webhook position is also watched by
         webhook_executor's own monitor/TV-EXIT; atomically claim it first so
         two processes never both fire a closing order on the same leg.
      2. fresh broker flat-check (TRAP #44/#73) — is_flat_fresh() never trusts
         data older than 5s. If the position is already flat at the broker
         (manual close, earlier reject-orphan), placing a closing order would
         OPEN a new opposite position instead of closing anything.
    Returns True if the exit should be SKIPPED (already handled elsewhere or
    already flat) — caller must not place an order in that case."""
    if (p.get("source") or "") == "webhook":
        try:
            import webhook_executor as _wh
            # Tell the webhook monitor to back off (clears its in-memory state so
            # it won't ALSO fire a closing order on this leg). release_position()
            # returns True only if it WAS tracking this position; False means it
            # simply doesn't know about it — most often because the position was
            # opened AFTER this (algo-monitor) process booted, and _wh_state only
            # recovered at import (cross-process gap → SL-suppressed-by-false-
            # webhook-claim, root-fixed 2026-07-09). False is NOT evidence the leg
            # is closed, so we must NOT skip on it — fall through to the
            # authoritative fresh broker flat-check below (the real double-close
            # guard). Previously this `return True` left a webhook position with a
            # genuinely-fired SL completely unenforced.
            _wh.release_position(sec_id=sec_id, trad_sym=p.get("sym"), reason=exit_reason)
        except Exception as _e:
            log(f"[{exit_reason}] webhook release failed: {_e}", flush=True)
    if p.get("mode") != "live":
        return False
    try:
        import broker_sync as _bsync
        _br_name = (p.get("broker") or "dhan").lower()
        if _bsync.is_flat_fresh(_br_name, p.get("sym") or "", str(sec_id or "")):
            import order_store as _os2
            _os2.mark_externally_closed(p.get("id"))
            _closed_ids.add(p.get("id"))
            log(f"[{exit_reason}] PRE-EXIT CHECK: {p.get('sym')} already FLAT at "
                f"{_br_name} — marked externally_closed, skipping exit order.", flush=True)
            return True
    except Exception as _pe:
        log(f"[{exit_reason}] pre-exit broker check failed ({_pe}) — proceeding", flush=True)
    return False


def _pos_monitor_check_one(p, sec_id, tags, ist_now, open_pos, _closed_ids):
    import dhan_feed
    import order_store
    seg = "NSE_EQ" if p.get("instrument") == "EQUITY" else "NSE_FNO"
    _feed_subscribe([(seg, sec_id)])

    q = dhan_feed.get_quote(sec_id, max_age=_FEED_MAX_AGE)  # reject a stale WS tick → fresh fallback
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
    conf_max_ltp = ltp
    conf_min_ltp = ltp
    if entry_px > 0:
        max_ltp = ltp
        min_ltp = ltp
        max_tag_idx = -1
        min_tag_idx = -1
        conf_max_tag_idx = -1
        conf_min_tag_idx = -1
        prev_tag_idx = -1
        prev_ltp = None
        for i, t in enumerate(tags):
            if t.startswith("MAX_LTP:"):
                max_tag_idx = i
                try: max_ltp = max(ltp, float(t.split(":")[1]))
                except: pass
            elif t.startswith("MIN_LTP:"):
                min_tag_idx = i
                try: min_ltp = min(ltp, float(t.split(":")[1]))
                except: pass
            elif t.startswith("CONF_MAX_LTP:"):
                conf_max_tag_idx = i
                try: conf_max_ltp = float(t.split(":")[1])
                except: pass
            elif t.startswith("CONF_MIN_LTP:"):
                conf_min_tag_idx = i
                try: conf_min_ltp = float(t.split(":")[1])
                except: pass
            elif t.startswith("PREV_LTP:"):
                prev_tag_idx = i
                try: prev_ltp = float(t.split(":")[1])
                except: pass

        # Confirmed peak/trough — a SEPARATE track from MAX_LTP/MIN_LTP (which
        # stay raw, for the Run-Up/Run-Down display — that's supposed to show
        # the actual best/worst tick seen, glitch or not). CONF_MAX_LTP/
        # CONF_MIN_LTP only advance when the current tick's LTP is confirmed
        # by the NEXT tick not reverting below it (same "confirmed = min/max
        # of this-and-previous reading" technique as risk_gate.
        # advance_trailing_lock's 2-reading confirmed peak, built for the
        # KILL-ALL floor for exactly this reason). Feeds ONLY the trailing_pt
        # SL/TP ratchet below — a single spike/stale tick can no longer
        # permanently ratchet a position's stop-loss to a level real price
        # never actually held.
        confirmed_high = ltp if prev_ltp is None else min(prev_ltp, ltp)
        confirmed_low  = ltp if prev_ltp is None else max(prev_ltp, ltp)
        conf_max_ltp = max(conf_max_ltp, confirmed_high)
        conf_min_ltp = min(conf_min_ltp, confirmed_low)

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

        if conf_max_tag_idx != -1:
            if tags[conf_max_tag_idx] != f"CONF_MAX_LTP:{conf_max_ltp}":
                tags[conf_max_tag_idx] = f"CONF_MAX_LTP:{conf_max_ltp}"
                changed = True
        else:
            tags.append(f"CONF_MAX_LTP:{conf_max_ltp}")
            changed = True

        if conf_min_tag_idx != -1:
            if tags[conf_min_tag_idx] != f"CONF_MIN_LTP:{conf_min_ltp}":
                tags[conf_min_tag_idx] = f"CONF_MIN_LTP:{conf_min_ltp}"
                changed = True
        else:
            tags.append(f"CONF_MIN_LTP:{conf_min_ltp}")
            changed = True

        if prev_tag_idx != -1:
            if tags[prev_tag_idx] != f"PREV_LTP:{ltp}":
                tags[prev_tag_idx] = f"PREV_LTP:{ltp}"
                changed = True
        else:
            tags.append(f"PREV_LTP:{ltp}")
            changed = True

        if changed and p.get("id"):
            # Merge ONLY the LTP-tracking fields into the row's CURRENT DB tags
            # (atomic read-modify-write). The old full-list update_tags() rewrote
            # the whole tag array from this cycle's start-of-loop snapshot, which
            # clobbered any SL/TP/NOTE tag a user set via the ⚙ modal mid-cycle —
            # so manual/trigger positions (no entry-time default SL) silently lost
            # their gear-set SL/Target within ~5s (SL not shown + exit reason '-').
            order_store.update_tag_fields(p["id"], {
                "MAX_LTP": max_ltp, "MIN_LTP": min_ltp,
                "CONF_MAX_LTP": conf_max_ltp, "CONF_MIN_LTP": conf_min_ltp,
                "PREV_LTP": ltp,
            })

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
        q = dhan_feed.get_quote(u_sec, max_age=_FEED_MAX_AGE)  # reject a stale WS tick → fresh fallback
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
        # --- Stepped Trailing Stop-Loss/Take-Profit in Points ---
        if typ == "trailing_pt":
            prefix = "SL" if is_sl else "TP"
            step_val_tag = next((t.split(":", 1)[1] for t in tags if t.startswith(f"{prefix}_TRAIL_STEP:")), None)
            if step_val_tag is not None:
                step_val = float(step_val_tag)
            else:
                step_val = resolve_trailing_step(entry_px)
            if side == "BUY":
                ref_ltp = conf_max_ltp if is_sl else conf_min_ltp
                if is_sl:
                    favorable_movement = ref_ltp - entry_px
                    if favorable_movement > 0:
                        num_steps = int(favorable_movement / step_val)
                        return round((entry_px - val) + (num_steps * step_val), 2)
                    else:
                        return round(entry_px - val, 2)
                else:
                    favorable_movement = entry_px - ref_ltp
                    if favorable_movement > 0:
                        num_steps = int(favorable_movement / step_val)
                        return round((entry_px + val) - (num_steps * step_val), 2)
                    else:
                        return round(entry_px + val, 2)
            else:
                ref_ltp = conf_min_ltp if is_sl else conf_max_ltp
                if is_sl:
                    favorable_movement = entry_px - ref_ltp
                    if favorable_movement > 0:
                        num_steps = int(favorable_movement / step_val)
                        return round((entry_px + val) - (num_steps * step_val), 2)
                    else:
                        return round(entry_px + val, 2)
                else:
                    favorable_movement = ref_ltp - entry_px
                    if favorable_movement > 0:
                        num_steps = int(favorable_movement / step_val)
                        return round((entry_px - val) + (num_steps * step_val), 2)
                    else:
                        return round(entry_px - val, 2)
        # --- End Stepped Trailing ---
        if typ == "pct":
            return entry_px * (1 - val/100.0) if (is_sl) == (side == "BUY") else entry_px * (1 + val/100.0)
        if typ == "pt":
            if side == "BUY":
                return entry_px - val if is_sl else entry_px + val
            else:
                return entry_px + val if is_sl else entry_px - val
        if typ == "rs":
            # val is a PER-LOT ₹ amount (consistent with aggressive mode,
            # risk_gate.target_sl_level) — divide by lot_size, NOT total qty,
            # so the price-distance stays constant per lot regardless of how
            # many lots are in the position. Resolve lot_size from the scrip
            # master (same pattern as the aggressive-mode block in
            # pos_monitor_loop); unknown lot_size = skip, never guess.
            import dhan_master as _dm_rs
            lot_size = _dm_rs.get_lot_size_by_sec_id(sec_id)
            if not lot_size:
                return None
            per_unit = val / lot_size
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
        # ── Pre-exit safety gate (TRAP #44/#73 guard) — webhook co-ownership
        # claim + fresh broker flat-check, both explained in _pre_exit_guard's
        # docstring. Shared with the trailing-lock squareoff paths (P6 audit,
        # 2026-07-02) so this logic lives in exactly one place.
        if _pre_exit_guard(p, sec_id, exit_reason, _closed_ids, log=print):
            return True
        print(f"[{exit_reason}] {p['sym']} LTP {ltp}. Squaring off...")
        exit_side = "SELL" if p["entry"] == "BUY" else "BUY"

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
                    is_exit=True,
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
                qsib = dhan_feed.get_quote(sib_sec, max_age=_FEED_MAX_AGE)  # reject a stale WS tick → fresh fallback
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
                    _pgc_queue(sib, sib_sec, exit_reason + "_GROUP")
                    print(f"[{exit_reason}_GROUP] ⚠️ sibling {sib.get('sym')} has NO price "
                          f"right now (feed+REST+stale-cache all empty) — queued for forced "
                          f"retry next cycle instead of being left open unprotected", flush=True)
        return True

    # A previous cycle's group-close couldn't get a price for THIS exact leg
    # (see _pending_group_close above) — now that the normal LTP resolution
    # above succeeded for it, force the close through immediately, ahead of
    # any other check. This leg is leaving regardless of its own SL/TP/EOD
    # state; its sibling is already gone.
    reason = _pgc_pop(p, sec_id)   # Task 5: strategy-aware key (old bare-key fallback inside)
    if reason is not None:
        print(f"[{reason}] retry succeeded — {p.get('sym')} now has a price, forcing the "
              f"delayed group-close through", flush=True)
        _do_squareoff(p, ltp, reason, sec_id, seg)
        return

    # ── Expiry-day guards (run before general EOD so they fire earlier) ──────
    # On expiry day: (a) close 20 min earlier to avoid last-hour chaos and
    # physical-delivery margin blocks; (b) if a short option goes ITM, exit
    # immediately — don't wait for EOD, the loss only grows from here.
    _trad_sym = p.get("sym") or ""   # order_store open row → symbol is "sym", not "trad_sym" (always None)
    _is_option = (p.get("instrument") or "").upper() != "EQUITY"
    if _is_option:
        try:
            import risk_gate as _rg
            _exp_day = _rg.is_expiry_day(trad_sym=_trad_sym, sec_id=sec_id)
        except Exception:
            _exp_day = False

        # EXPIRY auto-squareoffs (EXPIRY_EOD 2:55 + EXPIRY_ITM) — OFF by default
        # (removed 2026-07-21, user: not in any backtest → live/paper diverged from
        # validated numbers, Rule 10). Index options are cash-settled; overnight
        # strategies hold to/through expiry like their backtests. Re-enable via
        # _risk.global.expiry_auto_squareoff_enabled if a naked/stock short is ever
        # held to expiry.
        if _exp_day and _rg.expiry_auto_squareoff_enabled():
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
    _sq_h, _sq_m = _rg.exit_time_config()[0]   # RMS single-source squareoff time (was hardcoded 15:15)
    if (ist_now.hour > _sq_h or (ist_now.hour == _sq_h and ist_now.minute >= _sq_m)) \
       and _is_option:
        # ── Positional/overnight lane (ADR-006) ─────────────────────────────
        # A strategy EXPLICITLY flagged allow_overnight (opt-in, default False)
        # may carry its option position PAST 3:15 (held to/through expiry — e.g. VRP
        # condor settles at expiry, overnight ORB exits next-day 09:20). PURELY
        # ADDITIVE: any strategy NOT flagged (webhook, intraday) is squared off here
        # at 3:15 exactly as before, so it never reaches settlement.
        # NOTE: the old `and not _exp_day` — which force-closed overnight strategies
        # on expiry day — was removed 2026-07-21 with the EXPIRY guards above (Rule 10,
        # these weren't in their backtests). Index options are cash-settled and the
        # overnight fleet is hedged/bounded, so holding to expiry is safe.
        _skip_eod = False
        try:
            _owner = p.get("strategy") or ""
            _skip_eod = bool(_owner) and _rg.allow_overnight(_owner)
        except Exception:
            _skip_eod = False
        if _skip_eod:
            print(f"[pos_monitor] {p.get('sym')} held OVERNIGHT — strategy "
                  f"{p.get('strategy')} allow_overnight=True, non-expiry day "
                  f"(EOD 3:15 squareoff skipped; expiry/ITM/RMS guards still active)",
                  flush=True)
        else:
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
    # --- Modified by Antigravity AI: Support checkbox-based Candle Close exit triggers ---
    sl_candle_close = any(t == "SL_CANDLE_CLOSE:true" for t in tags)
    tp_candle_close = any(t == "TP_CANDLE_CLOSE:true" for t in tags)

    # numeric generic SL/TP trigger price (None if no generic tag set)
    sl_px_num = sl_px_generic if isinstance(sl_px_generic, float) else None
    tp_px_num = tp_px_generic if isinstance(tp_px_generic, float) else None

    if sl_px_num is not None:
        eval_sl_price = ltp
        if sl_candle_close:
            last_close = _last_closed_candle_close(sec_id, seg)
            if last_close and last_close > 0:
                eval_sl_price = last_close
            else:
                eval_sl_price = None  # wait for next valid candle close
        
        if eval_sl_price is not None:
            hit = eval_sl_price <= sl_px_num if p["entry"] == "BUY" else eval_sl_price >= sl_px_num
            if hit:
                _do_squareoff(p, eval_sl_price, f"SL_HIT:{sl_type}:{sl_val}", sec_id, seg)
                return

    if tp_px_num is not None:
        eval_tp_price = ltp
        if tp_candle_close:
            last_close = _last_closed_candle_close(sec_id, seg)
            if last_close and last_close > 0:
                eval_tp_price = last_close
            else:
                eval_tp_price = None  # wait for next valid candle close

        if eval_tp_price is not None:
            hit = eval_tp_price >= tp_px_num if p["entry"] == "BUY" else eval_tp_price <= tp_px_num
            if hit:
                _do_squareoff(p, eval_tp_price, f"TP_HIT:{tp_type}:{tp_val}", sec_id, seg)
                return

    if sl_px_num is not None and tp_px_num is not None:
        return  # generic SL+TP both set and neither hit — skip legacy fallback entirely
    # --- End Antigravity AI modification ---

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
    # Har error 🔔 tak — strategy logs + mari hui strategies + gire services.
    _threading.Thread(target=_error_watch_loop, daemon=True).start()
    print("   🔔 error-watch chalu — logs/processes/services ke errors bell me aayenge\n")
    app.run(host='0.0.0.0', port=5099, debug=False)
