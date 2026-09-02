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


def _write_json_atomic(path, obj, ensure_ascii=True):
    """Config ko ATOMICALLY likho — tmp file + os.replace.

    KYUN (2026-08-30, autonomy audit): `path.write_text(json.dumps(...))` file ko
    TRUNCATE karke likhta hai. Us beech me process mara (deploy restart, OOM,
    reboot) to config **aadhi likhi** reh jaati hai = corrupt JSON. Aur ye file
    (`nifty_config.json`) har strategy ka mode/active/lots/RMS rakhti hai — corrupt
    hui to agli subha kuch bhi start nahi hoga, aur wajah bhi nahi dikhegi.
    `os.replace` POSIX pe atomic hai: ya poori nayi file, ya poori purani —
    beech ka aadha-adhoora state kabhi nahi.

    `_ops/token_refresh.py` isi ko pehle se karta hai (wo roz chalta hai);
    ye usi ko baaki har config-writer tak le aata hai.
    """
    import os as _os
    p = str(path)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=ensure_ascii)
        f.flush()
        _os.fsync(f.fileno())
    _os.replace(tmp, p)
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
    "bnf_strangle": {"script": str(TRADERS_DIR / "bnf_strangle_trader.py"), "log": BASE_DIR / "logs/bnf_strangle_v1.log", "cfg": TC_FILE, "grep": "bnf_strangle_trader"},
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

@app.route('/whatif')
def whatif_page():
    """Manual options what-if backtest — pick instrument/date/entry-exit time/legs,
    see real-premium P&L split into price-move / IV-crush / decay. Collector data for
    recent days (real greeks), OptChainLake for historical (BS-derived). Display-only."""
    from flask import make_response
    resp = make_response(render_template("whatif.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'   # inline JS changes often → never serve stale HTML
    return resp

@app.route('/crypto')
def crypto_page():
    """Delta Exchange India crypto (BTC) — live spot + option chain + validated
    daily Iron-Fly setup. Display-only, credential-free (public Delta API).
    Phase-1 of the Delta integration (see project_delta_crypto_options)."""
    from flask import make_response
    resp = make_response(render_template("crypto.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp

@app.route('/api/delta-chain')
def api_delta_chain():
    """Live BTC/ETH option chain (one Delta /v2/tickers call). Display-only."""
    try:
        from _ops import delta_feed
    except Exception:
        import delta_feed
    u = (request.args.get('underlying') or 'BTC').upper()
    exp = request.args.get('expiry') or None
    try:
        n = int(request.args.get('n', 8))
    except (TypeError, ValueError):
        n = 8
    data = delta_feed.chain(u, exp, n)
    return jsonify(data or {"error": "no data"})

@app.route('/api/delta-ironfly')
def api_delta_ironfly():
    """Validated daily Iron-Fly setup with live premiums (display-only context)."""
    try:
        from _ops import delta_feed
    except Exception:
        import delta_feed
    u = (request.args.get('underlying') or 'BTC').upper()
    exp = request.args.get('expiry') or None
    wing = request.args.get('wing')
    wing = int(wing) if (wing and wing.isdigit()) else None
    data = delta_feed.ironfly_setup(u, exp, wing)
    return jsonify(data or {"error": "no data"})

@app.route('/api/delta-paper')
def api_delta_paper():
    """Delta paper Iron-Fly state (open + completed + config). Display-only, PAPER."""
    try:
        from _ops import delta_ironfly_trader as dft
    except Exception:
        import delta_ironfly_trader as dft
    st = dft._load()
    comp = st.get("completed") or []
    tot_pts = sum((c.get("pnl_pts") or 0) for c in comp)
    tot_usd = sum((c.get("pnl_usd") or 0) for c in comp)
    op = st.get("open")
    mtm = None
    if op:
        try:
            mtm = dft.live_mtm(op)
        except Exception:
            mtm = None
    return jsonify({"open": op, "open_mtm": mtm, "completed": comp[-30:],
                    "n": len(comp), "total_pnl_pts": tot_pts,
                    "total_pnl_usd": tot_usd, "config": dft._config()})

@app.route('/whatif2')
def whatif2_page():
    """Sensibull-style Strategy Builder (backtest) — leg builder + Add/Edit chain modal +
    payoff/KPI panel + Run-backtest results. SEPARATE from /whatif (which stays as-is);
    reuses the same backend (chain_at / payoff_at / opt_whatif.run / whatif-margin)."""
    from flask import make_response
    resp = make_response(render_template("whatif2.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp

@app.route('/whatif3')
def whatif3_page():
    """4-in-1 compare — ek base leg (strike + CE/PE + 0.25Δ) se 4 structures ek saath:
    naked sell / naked buy / credit spread / debit spread. Har ek ka poora rich result
    (NET P&L + leg table + MTM journey + profit-split), ek scroll me. Reuses /api/whatif
    (4 calls) — koi naya compute nahi. Display-only."""
    from flask import make_response
    resp = make_response(render_template("whatif3.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp

@app.route('/backtest-lab')
def backtest_lab_page():
    """StockMock-style multi-day options backtest — build a leg strategy (ATM±N, per-leg
    SL/Target/Trail + strategy SL/Target), run it day-by-day over a date range on OUR data
    (lake 2021→ + collector recent), and get all the stats/breakups/charts/trade-log +
    per-day intraday PnL. Display-only research; no order path. Reuses backtest_lab.py."""
    from flask import make_response
    resp = make_response(render_template("backtest_lab.html"))
    resp.headers['Cache-Control'] = 'no-store, must-revalidate'
    return resp


def _btlab_legs(raw):
    """Normalise the frontend legs → engine legs [{side,opt,off,lots,sl_rs?,tp_rs?,trail_*}]."""
    out = []
    for lg in (raw or []):
        try:
            out.append({
                "side": str(lg.get("side", "SELL")).upper(),
                "opt": str(lg.get("opt", "CE")).upper(),
                "off": int(lg.get("off", 0)),
                "lots": max(1, int(lg.get("lots") or 1)),
                "sl_rs": (float(lg["sl_rs"]) if lg.get("sl_rs") not in (None, "", 0, "0") else None),
                "tp_rs": (float(lg["tp_rs"]) if lg.get("tp_rs") not in (None, "", 0, "0") else None),
                "trail_arm": (float(lg["trail_arm"]) if lg.get("trail_arm") not in (None, "", 0, "0") else None),
                "trail_gap": (float(lg["trail_gap"]) if lg.get("trail_gap") not in (None, "", 0, "0") else None),
            })
        except Exception:
            continue
    return out


@app.route('/api/backtest-lab', methods=['POST'])
def api_backtest_lab():
    """Run a multi-day options backtest. Returns summary + monthly/day-wise breakup +
    equity curve + per-day trade log. Display-only (backtest_lab.py, disk data)."""
    try:
        import backtest_lab as bl
        b = request.get_json(force=True) or {}
        legs = _btlab_legs(b.get('legs'))
        if not legs:
            return jsonify({"ok": False, "reason": "koi valid leg nahi"})
        wd = b.get('weekdays')
        weekdays = set(int(x) for x in wd) if wd else None
        out = bl.run(
            str(b.get('underlying', 'BANKNIFTY')).upper(), legs,
            str(b.get('entry') or '09:20')[:5], str(b.get('exit') or '15:15')[:5],
            str(b.get('from')), str(b.get('to')),
            strat_sl=(float(b['strat_sl']) if b.get('strat_sl') not in (None, "", 0, "0") else None),
            strat_tp=(float(b['strat_tp']) if b.get('strat_tp') not in (None, "", 0, "0") else None),
            sqoff=str(b.get('sqoff') or 'all'), weekdays=weekdays)
        return jsonify(out)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"ok": False, "reason": str(e)})


@app.route('/api/backtest-lab/intraday', methods=['POST'])
def api_backtest_lab_intraday():
    """One day's minute-by-minute combined MTM + spot (per-day PnL modal)."""
    try:
        import backtest_lab as bl
        b = request.get_json(force=True) or {}
        legs = _btlab_legs(b.get('legs'))
        if not legs:
            return jsonify({"ok": False, "reason": "koi leg nahi"})
        return jsonify(bl.intraday(
            str(b.get('underlying', 'BANKNIFTY')).upper(), legs,
            str(b.get('entry') or '09:20')[:5], str(b.get('exit') or '15:15')[:5],
            str(b.get('date')),
            strat_sl=(float(b['strat_sl']) if b.get('strat_sl') not in (None, "", 0, "0") else None),
            strat_tp=(float(b['strat_tp']) if b.get('strat_tp') not in (None, "", 0, "0") else None),
            sqoff=str(b.get('sqoff') or 'all')))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)})

@app.route('/api/whatif', methods=['POST'])
def api_whatif():
    import opt_whatif as w
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify(w.run(d.get('underlying', 'NIFTY'), d.get('date'),
                             d.get('entry', '09:20'), d.get('exit', '15:30'),
                             d.get('lots', 1), d.get('legs', []), expiry=d.get('expiry') or None,
                             exit_date=d.get('exit_date') or None))
    except Exception as e:
        print("[whatif] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e), "legs": []})


# ------------------- ☀️ Morning Brief (subha ek-nazar market snapshot) -------
@app.route('/brief')
def morning_brief_page():
    """One-glance morning snapshot — India indices/VIX + FII-DII flows + PCR +
    crypto + top news + upcoming events + Reddit buzz + auto bias line. All FREE
    sources (option-chain lake + NSE FII lake + CoinGecko + RSS). Display-only."""
    return render_template("morning_brief.html")


@app.route('/api/morning-brief')
def api_morning_brief():
    import morning_brief as mb
    try:
        if request.args.get('fresh'):
            mb._CACHE.clear()
        return jsonify(mb.build_brief())
    except Exception as e:
        print("[morning-brief] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


# ------------------- 💡 Idea Vault (quick strategy/idea clip gallery) ---------
@app.route('/ideas')
def idea_vault_page():
    """Quick idea/strategy/bug video capture — drag-drop a clip, tag it, it
    shows up instantly in a gallery. Display-only, zero order/Dhan path."""
    return render_template("idea_vault.html")


@app.route('/api/ideas')
def api_ideas_list():
    import idea_vault as iv
    tag = request.args.get('tag') or None
    q = request.args.get('q') or None
    try:
        return jsonify({"ok": True, "ideas": iv.list_ideas(tag=tag, q=q),
                        "tags": list(iv.VALID_TAGS)})
    except Exception as e:
        print("[ideas] list fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e), "ideas": []})


@app.route('/api/ideas/upload', methods=['POST'])
def api_ideas_upload():
    import idea_vault as iv
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    title = request.form.get('title', '')
    note = request.form.get('note', '')
    tag = request.form.get('tag', 'idea')
    try:
        entry = iv.add(f, title=title, note=note, tag=tag)
        return jsonify({"ok": True, "idea": entry})
    except Exception as e:
        print("[ideas] upload fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/api/ideas/update', methods=['POST'])
def api_ideas_update():
    import idea_vault as iv
    body = request.get_json(force=True) or {}
    vid = body.get('id')
    if not vid:
        return jsonify({"ok": False, "error": "id required"}), 400
    try:
        item = iv.update(vid, body)
        if not item:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "idea": item})
    except Exception as e:
        print("[ideas] update fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/api/ideas/delete', methods=['POST'])
def api_ideas_delete():
    import idea_vault as iv
    body = request.get_json(force=True) or {}
    vid = body.get('id')
    if not vid:
        return jsonify({"ok": False, "error": "id required"}), 400
    try:
        ok = iv.delete(vid)
        return jsonify({"ok": ok})
    except Exception as e:
        print("[ideas] delete fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/idea-video/<vid>')
def idea_video_stream(vid):
    """HTTP Range streaming for an idea clip (ported from CODE7 stream_video)."""
    import idea_vault as iv
    path = iv.clip_path(vid)
    if not path:
        return Response('not found', status=404)

    file_size = os.path.getsize(path)
    mime = iv.video_mime(path)
    range_header = request.headers.get('Range')

    if range_header:
        m = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if not m:
            return Response(status=416)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        def generate():
            with open(path, 'rb') as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(512 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        resp = Response(generate(), status=206, mimetype=mime,
                        direct_passthrough=True)
        resp.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        resp.headers['Accept-Ranges'] = 'bytes'
        resp.headers['Content-Length'] = str(length)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp

    resp = Response(open(path, 'rb').read(), status=200, mimetype=mime)
    resp.headers['Accept-Ranges'] = 'bytes'
    resp.headers['Content-Length'] = str(file_size)
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ------------------- 📓 P&L Journal (monthly grid + per-trade comments/media) --
@app.route('/journal')
def pnl_journal_page():
    """Monthly P&L journal grid (day-rows x strategy-cols), per-trade drill-down
    with comments + screen-capture video notes + images. Display-only, zero
    order/Dhan path. Reuses order_store + charges + registry + idea_vault mime."""
    return render_template("journal.html")


@app.route('/jnl-api/data')
def api_journal_data():
    import pnl_journal as pj
    try:
        import datetime as _d
        today = _d.date.today()
        y = int(request.args.get('y') or today.year)
        m = int(request.args.get('m') or today.month)
        return jsonify({"ok": True, "data": pj.build_month(y, m)})
    except Exception as e:
        print("[journal] data fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/jnl-api/notes')
def api_journal_notes():
    import pnl_journal as pj
    return jsonify({"ok": True, "notes": pj.get_notes()})


@app.route('/jnl-api/notes', methods=['POST'])
def api_journal_set_note():
    import pnl_journal as pj
    b = request.get_json(force=True) or {}
    key = b.get('key')
    if not key:
        return jsonify({"ok": False, "error": "key required"}), 400
    pj.set_note(key, b.get('text') or "")
    return jsonify({"ok": True})


@app.route('/jnl-api/media/list')
def api_journal_media_list():
    import pnl_journal as pj
    tk = request.args.get('tk')
    if not tk:
        return jsonify({"ok": False, "error": "tk required"}), 400
    return jsonify({"ok": True, "media": pj.list_media(tk)})


@app.route('/jnl-api/media/keys')
def api_journal_media_keys():
    import pnl_journal as pj
    return jsonify({"ok": True, "keys": pj.media_keys()})


@app.route('/jnl-api/media/upload', methods=['POST'])
def api_journal_media_upload():
    import pnl_journal as pj
    f = request.files.get('file')
    tk = request.form.get('tk')
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "no file"}), 400
    if not tk:
        return jsonify({"ok": False, "error": "tk required"}), 400
    try:
        return jsonify({"ok": True, "item": pj.add_media(f, tk, note=request.form.get('note', ''))})
    except Exception as e:
        print("[journal] media upload fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route('/jnl-api/media/note', methods=['POST'])
def api_journal_media_note():
    import pnl_journal as pj
    b = request.get_json(force=True) or {}
    if not b.get('id'):
        return jsonify({"ok": False, "error": "id required"}), 400
    return jsonify({"ok": pj.update_media_note(b['id'], b.get('note') or "")})


@app.route('/jnl-api/media/delete', methods=['POST'])
def api_journal_media_delete():
    import pnl_journal as pj
    b = request.get_json(force=True) or {}
    if not b.get('id'):
        return jsonify({"ok": False, "error": "id required"}), 400
    return jsonify({"ok": pj.delete_media(b['id'])})


@app.route('/jnl-media/<mid>')
def journal_media_stream(mid):
    """Serve a journal media file — Range streaming for video, direct for image."""
    import pnl_journal as pj
    path = pj.media_path(mid)
    if not path:
        return Response('not found', status=404)
    file_size = os.path.getsize(path)
    mime = pj.media_mime(path)
    range_header = request.headers.get('Range')
    if range_header and mime.startswith('video'):
        m = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if not m:
            return Response(status=416)
        start = int(m.group(1)); end = int(m.group(2)) if m.group(2) else file_size - 1
        end = min(end, file_size - 1); length = end - start + 1

        def generate():
            with open(path, 'rb') as fh:
                fh.seek(start); remaining = length
                while remaining > 0:
                    chunk = fh.read(min(512 * 1024, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk); yield chunk
        resp = Response(generate(), status=206, mimetype=mime, direct_passthrough=True)
        resp.headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
        resp.headers['Accept-Ranges'] = 'bytes'
        resp.headers['Content-Length'] = str(length)
        resp.headers['Cache-Control'] = 'no-cache'
        return resp
    resp = Response(open(path, 'rb').read(), status=200, mimetype=mime)
    resp.headers['Accept-Ranges'] = 'bytes'
    resp.headers['Content-Length'] = str(file_size)
    return resp


# ------------------- 📋 Daily Report (one-scroll EOD report) -----------------
@app.route('/report')
def daily_report_page():
    """One-scroll EOD Daily Report — KPIs, target/stat tables, per-strategy +
    per-trade breakdowns, distribution charts, per-date observation notes.
    Reuses order_store range-net + charges + registry. Display-only."""
    return render_template("daily_report.html")


@app.route('/roadmap')
def roadmap_page():
    """Live per-strategy growth tracker — actual equity vs Monte-Carlo corridor,
    auto on-track status + next-action (lot/capital). Display-only, no order path."""
    return render_template("roadmap.html")


@app.route('/api/roadmap')
def api_roadmap():
    import roadmap as _rm
    sid = request.args.get('strategy') or None
    try:
        strategies = _rm.list_strategies()
        if not sid and strategies:
            sid = strategies[0]["id"]
        data = _rm.build(sid) if sid else None
        return jsonify({"ok": True, "strategies": strategies, "data": data})
    except Exception as e:
        print("[roadmap] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


# ─────────────────────────────────────────────── Roadmap v2: portfolio + goal planner
# Display/config-only. Sirf APPLY (`/api/roadmap/plan/apply`) config likhta hai — aur wo
# bhi sirf lots + per-strategy capital_rs; mode/active kabhi nahi (goal_planner ke rails).
@app.route('/api/roadmap/portfolio')
def api_roadmap_portfolio():
    try:
        import roadmap_portfolio as _rp
        to = request.args.get('to') or None
        lane = (request.args.get('lane') or 'all').lower()
        lots_mode = (request.args.get('lots_mode') or 'live').lower()
        tgt = request.args.get('target')
        data = _rp.build(to, lane if lane in ('real', 'all') else 'all',
                         lots_mode if lots_mode in ('live', 'plan') else 'live',
                         target=float(tgt) if tgt else None)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        print("[roadmap-portfolio] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/candidates')
def api_roadmap_candidates():
    """Har Lab-run strategy pe deploy-gate → eligible / weak / rejected (read-only)."""
    try:
        import strategy_candidates as _sc
        return jsonify({"ok": True, "summary": _sc.summary(), "candidates": _sc.scan()})
    except Exception as e:
        print("[roadmap-candidates] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/goal', methods=['POST'])
def api_roadmap_goal():
    """Solve only — koi write nahi."""
    try:
        import goal_planner as _gp
        b = request.get_json(force=True, silent=True) or {}
        target = float(b.get('target') or 0)
        to_date = b.get('to_date')
        dd = float(b.get('dd_budget') or 0)
        scope = (b.get('scope') or 'all').lower()
        ids = b.get('ids') or None
        if not to_date:
            return jsonify({"ok": False, "error": "to_date chahiye"})
        if b.get('scenarios'):
            return jsonify({"ok": True, "scenarios": _gp.scenarios(
                target, to_date, dd, scope, ids,
                weights=(b.get('weights') or None),
                max_share=(float(b['max_share']) if b.get('max_share') else None))})
        p_goal = float(b.get('p_goal') or 60.0)
        # weights = user ka per-strategy bharosa (0=Off .. 3=Max), max_share = ek
        # strategy ka max EXPECTED hissa. Dono display/plan-level hain — order path nahi.
        weights = b.get('weights') or None
        ms = b.get('max_share')
        plan = _gp.solve(target, to_date, dd, scope, ids, p_goal=p_goal,
                         weights=weights, max_share=(float(ms) if ms else None))
        if plan.get('ok') and b.get('funding', True):
            try:
                plan['funding'] = _gp.funding_check(plan)
            except Exception as fe:
                plan['funding'] = {"ok": False, "reason": str(fe)}
        return jsonify({"ok": True, "plan": plan})
    except Exception as e:
        print("[roadmap-goal] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/plan', methods=['GET'])
def api_roadmap_plan():
    try:
        import goal_planner as _gp
        act = _gp.active_plan()
        store = _gp._plan_store()
        return jsonify({"ok": True, "active": act, "history": (store.get('history') or [])[:10]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/plan/preview', methods=['POST'])
def api_roadmap_plan_preview():
    """Kya-kya badlega — read-only."""
    try:
        import goal_planner as _gp
        b = request.get_json(force=True, silent=True) or {}
        plan = b.get('plan') or {}
        return jsonify({"ok": True, "preview": _gp.preview_apply(plan)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/plan/apply', methods=['POST'])
def api_roadmap_plan_apply():
    """Config WRITE — sirf lots + capital_rs. Live member ho to typed confirm zaroori."""
    try:
        import goal_planner as _gp
        b = request.get_json(force=True, silent=True) or {}
        plan = b.get('plan') or {}
        if not (plan.get('members') or []):
            return jsonify({"ok": False, "error": "plan khaali hai"})
        res = _gp.apply_plan(plan, confirm=b.get('confirm'),
                             paper_only=bool(b.get('paper_only')),
                             note=b.get('note') or '')
        if res.get('ok'):
            try:
                notify.info("roadmap_plan_applied",
                            f"Plan '{res['plan']['name']}' applied — "
                            f"{len(res['applied'])} strategies configured, "
                            f"{len(res['queued'])} queued (open position)",
                            source="roadmap")
            except Exception:
                pass
        return jsonify(res)
    except Exception as e:
        print("[roadmap-apply] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/plan/rollback', methods=['POST'])
def api_roadmap_plan_rollback():
    try:
        import goal_planner as _gp
        res = _gp.rollback()
        if res.get('ok'):
            try:
                notify.info("roadmap_plan_rollback", "Roadmap plan rolled back — "
                            "config pichle backup se restore", source="roadmap")
            except Exception:
                pass
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/roadmap/daily')
def api_roadmap_daily():
    try:
        import roadmap_daily as _rd
        return jsonify({"ok": True, "data": _rd.build()})
    except Exception as e:
        print("[roadmap-daily] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/daily-report')
def api_daily_report():
    import daily_report as dr
    date_from = request.args.get('from') or request.args.get('date')
    date_to = request.args.get('to') or date_from
    mode = request.args.get('mode') or None
    source = request.args.get('source') or None
    broker = request.args.get('broker') or None
    strategy = request.args.get('strategy') or None
    if not date_from:
        return jsonify({"ok": False, "error": "date required"})
    try:
        return jsonify(dr.build(date_from, date_to, mode=mode, source=source,
                                broker=broker, strategy=strategy))
    except Exception as e:
        print("[daily-report] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/daily-report/dates')
def api_daily_report_dates():
    """Dates that actually have trade data (same mode/source/broker/strategy
    filters + exit-date bucketing as the report) — for the date arrows to skip
    empty days and jump only to days the report can render. Display-only."""
    import daily_report as dr
    mode = request.args.get('mode') or None
    source = request.args.get('source') or None
    broker = request.args.get('broker') or None
    strategy = request.args.get('strategy') or None
    try:
        return jsonify({"ok": True,
                        "dates": dr.available_dates(mode=mode, source=source,
                                                    broker=broker, strategy=strategy)})
    except Exception as e:
        print("[daily-report dates] fail:", e, flush=True)
        return jsonify({"ok": False, "dates": [], "error": str(e)})


@app.route('/api/daily-report/health')
def api_daily_report_health():
    """EOD system-health for the Daily Report page — the same ✅ Positives / ❌
    Negatives + banner as the /reports page, merged in so everything's one place.
    Fast by default (do_replay=False → no per-strategy candle refetch); ?replay=1
    adds the signal-replay-drift (TRAP #108) check. Display-only."""
    try:
        import eod_report as er
        date = request.args.get('date') or er.ist_today()
        replay = request.args.get('replay') == '1'
        data = er.collect(date, do_replay=replay)
        pos, neg = er.pos_neg(data)
        try:
            bt_rows, bt_warn = er.bt_live_match(date)
            bt_reds = sum(1 for r in bt_rows if r.get('verdict') == 'RED')
        except Exception:
            bt_warn, bt_reds = [], 0
        neg = list(neg) + list(bt_warn)
        rows = data['rows']
        reds = sum(1 for r in rows if r['colour'] == 'RED')
        yels = sum(1 for r in rows if r['colour'] == 'YELLOW')
        greys = sum(1 for r in rows if r['colour'] == 'GREY')
        if reds or bt_reds:
            lvl = 'red'
            txt = f"🔴 {reds} strategy RED" + (f" · 🎯 {bt_reds} backtest se DIVERGE" if bt_reds else "") + " — neeche Negatives dekho"
        elif yels:
            lvl, txt = 'yellow', f"🟡 sab critical clear, {yels} yellow (minor) — ek nazar maar lo"
        else:
            lvl, txt = 'green', "🟢 ALL CLEAR — jo strategies chali sab healthy"
        if greys:
            txt += f" · {greys} chali nahi (ignore)"
        return jsonify({"ok": True, "date": date, "banner_level": lvl, "banner_text": txt,
                        "positives": pos, "negatives": neg, "replay": replay})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/report-settings', methods=['GET', 'POST'])
def api_report_settings():
    import daily_report as dr
    if request.method == 'GET':
        try:
            return jsonify({"ok": True, **dr.get_settings()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "capital": None, "targets": {}})
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify({"ok": True, **dr.save_settings(
            capital=d.get('capital'), targets=d.get('targets'))})
    except Exception as e:
        print("[report-settings] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/telegram/config', methods=['GET', 'POST'])
def api_telegram_config():
    """Telegram alert settings — panel se read/save. Token kabhi wire pe poora
    nahi jaata (masked). Fail-safe."""
    import telegram_notify as tg
    if request.method == 'GET':
        try:
            cfg = tg.get_config_masked()
            # picker ke liye: active strategies (id + label + mode)
            strategies = []
            try:
                import strategy_registry as sr
                raw = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                for k, v in raw.items():
                    if isinstance(v, dict) and v.get('active'):
                        try:
                            lbl = sr.label(k, with_name=True)
                        except Exception:
                            lbl = k
                        strategies.append({"id": k, "label": lbl,
                                           "mode": v.get('mode', 'paper')})
                # monitor_daemon-fired families have NO top-level `active` key (config lives
                # under _auto_strangle / _auto_straddle) — inject them like the RMS/Logs virtual
                # rows so they're alertable too. They fire via execution_gateway, so the Telegram
                # hook already covers their entry/exit; this just makes them selectable here.
                for _blk, _ids in (('_auto_strangle', ('strangle_920', 'strangle_manual')),
                                   ('_auto_straddle', ('straddle_920', 'straddle_alert', 'straddle_manual'))):
                    if isinstance(raw.get(_blk), dict):
                        for _sid in _ids:
                            try:
                                _lbl = sr.label(_sid, with_name=True)
                            except Exception:
                                _lbl = _sid
                            strategies.append({"id": _sid, "label": _lbl, "mode": "paper"})
                # Block-config strategies (02.17 weekly iron-fly = REAL MONEY, m-pattern):
                # inke paas top-level `active` key hai hi nahi (config apne `_block` ke andar
                # rehta hai) — isliye upar wala loop inhe chhod deta tha aur ye picker me
                # kabhi dikhti hi nahi thin => UI se inka alert on karna NAMUMKIN tha.
                # `enabled` inka apna on/off hai; mode block se hi lo (live/paper).
                for _blk, _sid in (('_weekly_ironfly', 'weekly_ironfly_v1'),
                                   ('_m_pattern_ironfly', 'm_pattern_ironfly_v1')):
                    _b = raw.get(_blk)
                    if isinstance(_b, dict) and _b.get('enabled'):
                        try:
                            _lbl = sr.label(_sid, with_name=True)
                        except Exception:
                            _lbl = _sid
                        strategies.append({"id": _sid, "label": _lbl,
                                           "mode": _b.get('mode', 'paper')})
                strategies.sort(key=lambda s: (s['mode'] != 'live', s['label']))
            except Exception as e:
                print("[telegram] strategy list fail:", e, flush=True)
            return jsonify({"ok": True, "config": cfg, "strategies": strategies})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
    d = request.get_json(force=True, silent=True) or {}
    try:
        return jsonify({"ok": True, "config": tg.save_config(d)})
    except Exception as e:
        print("[telegram] save fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/telegram/detect-chat', methods=['POST'])
def api_telegram_detect_chat():
    import telegram_notify as tg
    return jsonify(tg.detect_chat_ids())


@app.route('/api/telegram/test', methods=['POST'])
def api_telegram_test():
    import telegram_notify as tg
    return jsonify(tg.send_test())


@app.route('/api/report-notes', methods=['GET', 'POST', 'PUT', 'DELETE'])
def api_report_notes():
    import report_notes as rn
    if request.method == 'GET':
        date = request.args.get('date')
        try:
            return jsonify({"ok": True, "notes": rn.list_notes(date)})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e), "notes": []})
    d = request.get_json(force=True, silent=True) or {}
    date = d.get('date')
    try:
        if request.method == 'POST':
            n = rn.add_note(date, d.get('anchor'), d.get('text'),
                            d.get('color', 'b'), d.get('images'))
            return jsonify({"ok": True, "note": n})
        if request.method == 'PUT':
            n = rn.update_note(date, d.get('id'), text=d.get('text'),
                               color=d.get('color'), images=d.get('images'))
            return jsonify({"ok": bool(n), "note": n})
        if request.method == 'DELETE':
            return jsonify({"ok": rn.delete_note(date, d.get('id'))})
    except Exception as e:
        print("[report-notes] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": False, "error": "bad method"})


@app.route('/api/report-notes/image', methods=['POST'])
def api_report_note_image():
    import report_notes as rn
    date = request.form.get('date')
    f = request.files.get('image')
    if not date or not f:
        return jsonify({"ok": False, "error": "date + image required"})
    try:
        import time as _t
        safe = _t.strftime('%H%M%S') + '_' + os.path.basename(f.filename or 'img')
        f.save(os.path.join(rn.image_dir(date), safe))
        return jsonify({"ok": True, "filename": safe})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/report-note-image/<date>/<path:fname>')
def serve_report_note_image(date, fname):
    import report_notes as rn
    return send_from_directory(rn.image_dir(date), fname)


@app.route('/api/whatif-legprice', methods=['POST'])
def api_whatif_legprice():
    """Per-leg REAL premium AT the entry time on the selected date (backtest price, not
    live LTP) — for the What-If leg rows. Recent/collector dates only; historical → null."""
    try:
        import opt_whatif as w
        d = request.get_json(force=True, silent=True) or {}
        r = w.leg_prices_at(d.get('underlying', 'NIFTY'), d.get('date'), d.get('entry', '09:20'),
                            d.get('legs', []), expiry=d.get('expiry') or None)
        return jsonify({"ok": bool(r), **(r or {"legs": []})})
    except Exception as e:
        return jsonify({"ok": False, "legs": [], "msg": str(e)})


@app.route('/api/whatif-expiries')
def api_whatif_expiries():
    """Stored expiries for a date so the What-If page can simulate on a specific expiry
    (recent/collector dates). Historical lake is weekly-only → empty list."""
    try:
        import opt_whatif as w
        u = str(request.args.get('underlying', 'NIFTY')).upper()
        return jsonify({"ok": True, "expiries": w.list_expiries(u, request.args.get('date'))})
    except Exception as e:
        return jsonify({"ok": False, "expiries": [], "msg": str(e)})

@app.route('/api/whatif-chain')
def api_whatif_chain():
    """Option-chain snapshot AT a backtest date+time for the What-If chain-grid picker —
    real premium + real IV per strike on collector days; historical days fall back to the
    OptChainLake (real premium, IV '—' — no BS-guess in the grid). Display-only."""
    try:
        import opt_whatif as w
        u = str(request.args.get('underlying', 'NIFTY')).upper()
        date = request.args.get('date')
        hm = str(request.args.get('time') or '09:20')[:5]
        expiry = request.args.get('expiry') or None
        sel = [s for s in (request.args.get('legs') or '').split(',') if s.strip()]
        try:
            n = max(6, min(30, int(request.args.get('n') or 10)))   # window ATM±n (builder wants wider)
        except Exception:
            n = 10
        return jsonify(w.chain_at(u, date, hm, expiry, n=n, sel=sel))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e), "strikes": []})

@app.route('/api/whatif2-payoff', methods=['POST'])
def api_whatif2_payoff():
    """Payoff curve (expiry + exit-day) + KPI (max P/L, breakevens, POP, net-credit,
    time-value, intrinsic) for the whatif2 builder legs at a backtest date/time. Reuses
    opt_whatif.payoff_at → payoff.py pure fns. Display-only."""
    try:
        import opt_whatif as w
        b = request.get_json(force=True) or {}
        return jsonify(w.payoff_at(
            str(b.get('underlying', 'NIFTY')).upper(), b.get('date'),
            str(b.get('entry') or '09:20')[:5], b.get('legs') or [],
            expiry=b.get('expiry') or None, exit_date=b.get('exit_date') or None,
            exit_hm=str(b.get('exit') or '')[:5] or None, mult=int(b.get('mult') or 1)))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)})

@app.route('/api/whatif2-intraday', methods=['POST'])
def api_whatif2_intraday():
    """Per-minute combined premium (cost-to-close) + net position delta over the entry
    date, for the Day chart's Smart-SL overlay (delta-flip + premium-stop). Recent =
    collector real greeks; historical = lake premium + BS delta (src flags it).
    Display-only — the 2-condition exit logic + thresholds live client-side (instant tweak)."""
    try:
        import opt_whatif as w
        b = request.get_json(force=True) or {}
        legs = b.get('legs') or []
        date = str(b.get('date') or '').strip()
        if not date or not legs:
            return jsonify({"ok": False, "reason": "date/legs missing"})
        return jsonify(w.intraday_series(
            str(b.get('underlying', 'NIFTY')).upper(), date, legs,
            str(b.get('entry') or '09:20')[:5], str(b.get('exit') or '15:15')[:5],
            expiry=b.get('expiry') or None, mult=int(b.get('mult') or 1)))
    except Exception as e:
        return jsonify({"ok": False, "reason": str(e)})

@app.route('/api/whatif-coverage')
def api_whatif_coverage():
    """Since-when REAL IV is available (broker's own reported IV = live collector window)
    + how far back real premium/P&L goes (lake, where IV is NOT real). Display-only."""
    try:
        import opt_whatif as w
        u = str(request.args.get('underlying', 'NIFTY')).upper()
        return jsonify(w.iv_coverage(u))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/whatif-margin', methods=['POST'])
def api_whatif_margin():
    """LIVE margin + current LTP for the entered legs (current market — resolves
    each strike to its live contract). Shows hedged basket margin vs the naked
    per-leg sum, so a hedge's benefit is visible. Market-hours only."""
    import dhan_master, requests as _req, payoff
    d = request.get_json(force=True, silent=True) or {}
    u = (d.get('underlying') or 'NIFTY').upper()
    lots = max(1, int(d.get('lots') or 1))
    legs = d.get('legs', [])
    step = 100 if u == 'BANKNIFTY' else 50
    try:
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        idx_id = {"NIFTY": "13", "BANKNIFTY": "25"}.get(u, "13")
        # live index spot (cache-first, then REST)
        spot = None
        try:
            import shared_ltp_cache as _slc
            spot = _slc.get(idx_id, max_age=15) or None
        except Exception:
            pass
        if not spot:
            _rl.set_context("WhatifMargin:Idx"); _rl.acquire("ltp")
            r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json={"IDX_I": [int(idx_id)]}, headers=headers, timeout=6)
            if r.status_code == 200:
                spot = float(r.json()["data"]["IDX_I"][idx_id]["last_price"])
        if not spot:
            return jsonify({"ok": False, "msg": "Live spot nahi mila — market band ho to margin/LTP off-hours nahi milte."})
        atm = round(spot / step) * step
        rows, out_legs, sec_ids = [], [], []
        for lg in legs:
            K = float(lg['strike']); typ = lg['type']; side = lg['side'].upper()
            off = round((K - atm) / step) if typ == 'CE' else round((atm - K) / step)
            sec, tsym, lotsz = dhan_master.get_option_contract(u, spot, typ, off)
            if not sec:
                return jsonify({"ok": False, "msg": f"{int(K)} {typ} ka current contract resolve nahi hua (spot {spot:.0f} se door?)."})
            qty = int(lotsz or (65 if u == 'NIFTY' else 30)) * lots
            rows.append({"trad_sym": tsym, "sec_id": sec, "side": side, "qty": qty, "entry": 0, "ltp": 0})
            out_legs.append({"label": f"{side.title()} {int(K)} {typ}", "sec": sec, "trad_sym": tsym})
            sec_ids.append(int(sec))
        # live LTP for the legs (one batched call)
        _rl.set_context("WhatifMargin:Legs"); _rl.acquire("ltp")
        rq = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json={"NSE_FNO": sec_ids}, headers=headers, timeout=6)
        ltpmap = {}
        if rq.status_code == 200:
            ltpmap = {k: (v or {}).get("last_price") for k, v in (rq.json().get("data", {}).get("NSE_FNO", {}) or {}).items()}
        for r, ol in zip(rows, out_legs):
            lp = ltpmap.get(str(r["sec_id"]))
            r["entry"] = r["ltp"] = lp or 0
            ol["ltp"] = round(lp, 1) if lp else None
        if d.get("ltp_only"):   # inline per-leg live LTP only — skip the heavy basket-margin Kite call
            return jsonify({"ok": True, "underlying": u, "spot": round(spot, 2), "legs": out_legs, "ltp_only": True})
        m = payoff.basket_margin(rows)
        bal = None
        try:
            import risk_gate as _rg
            _b = _rg.get_broker_balance(_rg.default_broker())
            if _b and _b.get("ok"):
                bal = _b.get("total_margin")
        except Exception:
            pass
        return jsonify({"ok": True, "underlying": u, "lots": lots, "spot": round(spot, 2),
                        "legs": out_legs, "hedged": m.get("hedged"), "standalone": m.get("standalone"),
                        "benefit": m.get("benefit"), "balance": bal, "msg": m.get("msg") or ""})
    except Exception as e:
        print("[whatif-margin] fail:", e, flush=True)
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/option-curves')
def api_option_curves():
    import option_curves as oc, json as _json, gzip as _gzip
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date')
    if not date:
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    try:
        days = int(request.args.get('days') or 1)
    except Exception:
        days = 1

    def _out(obj):
        # a day's curve payload is ~130 KB raw, ~6x smaller gzipped (was sent uncompressed)
        payload = _json.dumps(obj, separators=(',', ':'))
        if 'gzip' in (request.headers.get('Accept-Encoding') or '') and len(payload) > 2000:
            body = _gzip.compress(payload.encode('utf-8'), 6)
            resp = app.response_class(body, mimetype='application/json')
            resp.headers['Content-Encoding'] = 'gzip'
            resp.headers['Vary'] = 'Accept-Encoding'
            return resp
        return app.response_class(payload, mimetype='application/json')

    try:
        if days > 1:
            return _out(oc.curves_multi(u, date, days))   # multi-day concatenated
        return _out(oc.curves(u, date, expiry))
    except Exception as e:
        print("[option-curves] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e), "expiries": [], "points": []})

@app.route('/gex')
def gex_profile_page():
    """QuantTradingApp-style Gamma-Exposure (GEX) profile — per-strike Net GEX
    (green +/red −), call/put volume walls, spot, max-pain, abs-GEX/flip, from the
    option-chain collector's per-minute snapshots. Scrub/play through the day.
    Display-only (no order/risk path); GEX = context/level map, not a signal."""
    return render_template("gex.html")

@app.route('/api/gex')
def api_gex():
    import gex_profile as gp, json as _json, gzip as _gzip
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date')
    if not date:
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    latest_only = (request.args.get('latest') or '') in ('1', 'true', 'yes')
    want_list = (request.args.get('list') or '') in ('1', 'true', 'yes')
    smooth = (request.args.get('smooth') or 'med').lower()
    if smooth not in ('off', 'low', 'med', 'high'):
        smooth = 'med'

    def _out(obj):
        # compact JSON + gzip (a day's profile is ~1MB raw, ~8x smaller gzipped)
        payload = _json.dumps(obj, separators=(',', ':'))
        if 'gzip' in (request.headers.get('Accept-Encoding') or '') and len(payload) > 2000:
            body = _gzip.compress(payload.encode('utf-8'), 6)
            resp = app.response_class(body, mimetype='application/json')
            resp.headers['Content-Encoding'] = 'gzip'
            resp.headers['Vary'] = 'Accept-Encoding'
            return resp
        return app.response_class(payload, mimetype='application/json')

    try:
        if want_list:
            ds = gp.available_dates(u)
            return _out({"ok": bool(ds), "underlying": u, "dates": ds})   # date picker default
        if latest_only:
            return _out(gp.latest(u, date, expiry, smooth=smooth))   # newest snapshot (live refresh)
        return _out(gp.profile(u, date, expiry, smooth=smooth))      # full day (scrub/play)
    except Exception as e:
        print("[gex] fail:", e, flush=True)
        return _out({"ok": False, "error": str(e), "expiries": [], "snaps": []})

@app.route('/fii-flow')
def fii_flow_page():
    """FII/DII participant-flow dashboard — Sensibull-style but from our own free
    NSE lakes (participant OI 2015→, PCR/spot 2020→, cash from when collection
    started). Display-only context map — NOT a directional signal (next-day
    direction backtest FAILED; data = context/regime/vol only)."""
    return render_template("fii_flow.html")

@app.route('/api/fii-flow')
def api_fii_flow():
    import fii_flow_view as ffv, json as _json, gzip as _gzip
    try:
        obj = ffv.series()
    except Exception as e:
        print("[fii-flow] fail:", e, flush=True)
        obj = {"ok": False, "error": str(e), "cols": [], "rows": [], "meta": {}}
    payload = _json.dumps(obj, separators=(',', ':'))
    if 'gzip' in (request.headers.get('Accept-Encoding') or '') and len(payload) > 2000:
        body = _gzip.compress(payload.encode('utf-8'), 6)
        resp = app.response_class(body, mimetype='application/json')
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Vary'] = 'Accept-Encoding'
        return resp
    return app.response_class(payload, mimetype='application/json')

@app.route('/broker-orders')
def broker_orders_page():
    """Broker Orders page — Zerodha ki tarah aaj ke saare executed/rejected orders
    + trades (fills), app ke strategy/mode se annotated + app-blocked entries +
    CSV match. Display-only — koi order/risk path nahi."""
    return render_template("broker_orders.html")

@app.route('/api/app-orders')
def api_app_orders():
    """App ke apne order records (order_store) — PAPER + REAL, kisi bhi date ke,
    galat orders flagged. Primary source for the Broker Orders page. Display-only."""
    import broker_orders as bo
    date = request.args.get('date') or None
    mode = request.args.get('mode') or None
    try:
        obj = bo.fetch_app(date=date, mode=mode)
    except Exception as e:
        print("[app-orders] fail:", e, flush=True)
        obj = {"ok": False, "error": str(e), "orders": [], "strategies": [], "summary": {}}
    return jsonify(obj)

@app.route('/api/broker-orders')
def api_broker_orders():
    """Live broker order book + trade book + app-blocked entries (display-only, today-only)."""
    import broker_orders as bo
    broker = (request.args.get('broker') or 'kite').lower()
    date = request.args.get('date') or None
    try:
        obj = bo.fetch(broker=broker, date=date)
    except Exception as e:
        print("[broker-orders] fail:", e, flush=True)
        obj = {"ok": False, "error": str(e), "orders": [], "trades": [],
               "blocked": [], "summary": {}}
    return jsonify(obj)

@app.route('/api/broker-orders/csv-match', methods=['POST'])
def api_broker_orders_csv_match():
    """Uploaded Zerodha tradebook CSV vs live broker trades → exact-match report."""
    import broker_orders as bo
    broker = (request.args.get('broker') or 'kite').lower()
    try:
        f = request.files.get('file')
        text = f.read().decode('utf-8', 'replace') if f else request.get_data(as_text=True)
        obj = bo.csv_match(text, broker=broker)
    except Exception as e:
        print("[broker-orders csv-match] fail:", e, flush=True)
        obj = {"ok": False, "error": str(e), "rows": [], "exact_match": False,
               "summary": {}}
    return jsonify(obj)

@app.route('/api/option-strike')
def api_option_strike():
    """Per-strike premium series for /curves right-click 'Load strike chart'.
    No strike given → just the available-strikes list (for the picker). Display-only."""
    import option_curves as oc
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date')
    if not date:
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    strike = request.args.get('strike')
    ot = request.args.get('type') or None
    try:
        return jsonify(oc.strike_series(u, date, expiry, strike, ot))
    except Exception as e:
        print("[option-strike] fail:", e, flush=True)
        return jsonify({"ok": False, "strikes": [], "points": []})


@app.route('/api/option-legs')
def api_option_legs():
    """Combined held-strike premium for a /curves 'Fixed strike' straddle/strangle.
    `legs` = comma list of STRIKE-TYPE (e.g. 23950-CE,23950-PE = straddle;
    23800-PE,24100-CE = strangle). One strategy per call. Display-only."""
    import option_curves as oc
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date')
    if not date:
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    legs = []
    for tok in (request.args.get('legs') or '').split(','):
        p = tok.strip().split('-')          # STRIKE-TYPE (sign +1) | STRIKE-TYPE-B (buy hedge, sign -1)
        if len(p) < 2:
            continue
        legs.append({"strike": p[0], "opt_type": p[1],
                     "sign": (-1 if (len(p) > 2 and p[2].upper() == 'B') else 1)})
    try:
        days = int(request.args.get('days') or 1)
    except Exception:
        days = 1
    try:
        if days > 1:
            return jsonify(oc.legs_series_multi(u, date, days, legs))
        return jsonify(oc.legs_series(u, date, expiry, legs))
    except Exception as e:
        print("[option-legs] fail:", e, flush=True)
        return jsonify({"ok": False, "strikes": [], "points": []})

@app.route('/api/option-skew')
def api_option_skew():
    """Per-minute strike-wise IV smile (CE + PE across ATM±N) → /curves skew panel."""
    import option_curves as oc
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date') or (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    try:
        return jsonify(oc.skew_series(u, date, expiry))
    except Exception as e:
        print("[option-skew] fail:", e, flush=True)
        return jsonify({"ok": False, "series": []})

@app.route('/api/option-oi-heatmap')
def api_option_oi_heatmap():
    """OI-change heatmap grid (strike × time bucket) → /curves heatmap panel."""
    import option_curves as oc
    u = (request.args.get('underlying') or 'NIFTY').upper()
    date = request.args.get('date') or (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
    expiry = request.args.get('expiry') or None
    try:
        return jsonify(oc.oi_heatmap_series(u, date, expiry))
    except Exception as e:
        print("[option-oi-heatmap] fail:", e, flush=True)
        return jsonify({"ok": False, "strikes": [], "times": [], "ce": [], "pe": []})

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
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>EOD Reports</title>
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
</script><script defer src="/static/js/topnav.js"></script></body></html>"""

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
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>YT Presentations</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',sans-serif;
max-width:640px;margin:40px auto;padding:0 16px}}h1{{font-size:20px}}
a{{color:#58a6ff;text-decoration:none;font-size:16px}}a:hover{{text-decoration:underline}}
li{{margin:10px 0;list-style:none}}.dim{{color:#8b949e}}p.hint{{color:#8b949e;font-size:13px}}</style>
</head><body><h1>🎬 YT Presentations</h1>
<p class="hint">Roz ka flow: Claude ko din ke points do → wo presentation banake yahan date-wise save karta hai.</p>
<ul>{items}</ul>
<p><a href='/'>← Dashboard</a> &nbsp;|&nbsp; <a href='/reports'>📋 EOD Reports</a></p>
<script defer src="/static/js/topnav.js"></script></body></html>"""

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
    _write_json_atomic(TC_FILE, data)
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
    _write_json_atomic(TC_FILE, cfg)
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


# rms-summary is display-only but EXPENSIVE: it loops ~30 strategies calling
# capital_in_use + gating_status (DB + cached broker margin) + a live broker-balance
# read — ~8-58 s. The Risk tab re-fetches it every 30 s, so without a cache every poll
# re-paid that cost ("constantly loading"). Cache the whole payload for a hair longer
# than the poll interval → the poll (and any re-open within the window) is instant; only
# the first open per window computes. No background thread = no extra Dhan-rate load.
_RMS_SUM_CACHE = {"ts": 0.0, "payload": None}
_RMS_SUM_TTL = 35.0


@app.route('/api/rms-summary')
def api_rms_summary():
    import time as _t
    now = _t.time()
    c = _RMS_SUM_CACHE
    if c["payload"] is not None and (now - c["ts"]) < _RMS_SUM_TTL:
        return jsonify(c["payload"])
    try:
        p = _rms_summary_compute()
        c["payload"], c["ts"] = p, now
        return jsonify(p)
    except Exception as e:
        if c["payload"] is not None:          # serve last-good rather than error out
            return jsonify(c["payload"])
        return jsonify({"error": str(e), "strategies": [], "totals": {}, "webhook": []})


def _rms_summary_compute():
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

    # Zerodha CASH-margin headroom — how much MORE F&O-writing margin the account
    # can use before the 50%-cash rule rejects a new SELL (see risk_gate.cash_headroom).
    # Reuses the same cached broker balance above (no extra API call). LIVE-broker only.
    try:
        _ch = risk_gate.cash_headroom(_def_broker)
        totals["cash_headroom"] = _ch if _ch.get("ok") else None
        totals["cash_gate_on"] = risk_gate.cash_margin_gate_enabled()
    except Exception:
        totals["cash_headroom"] = None
        totals["cash_gate_on"] = None

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

    return {"strategies": rows, "totals": totals,
            "webhook": webhook, "webhook_global": wh_global}


# Background warmer: keep the rms-summary cache fresh during MARKET HOURS so even the
# FIRST Risk-tab open is instant (not a one-time ~8s compute). Runs only 09:08-15:45 IST
# on weekdays; off-market it does nothing (data doesn't change + avoids flaky off-hours
# broker calls) — the first evening open recomputes once, then the TTL cache serves.
# Cadence (30s) < the cache TTL (35s) → the 30s Risk-tab poll ALWAYS hits a warm cache.
# Marginal Dhan/Kite cost ≈ the same 1 (cached) broker-balance read the Risk poll already
# does — so this is no heavier than a user sitting on the Risk tab, never starves trading.
_rms_warm_started = False


def _rms_warm_loop():
    import time as _t
    from datetime import timedelta as _td
    while True:
        mkt = False
        try:
            ist = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
            mins = ist.hour * 60 + ist.minute
            mkt = (ist.weekday() < 5) and (9 * 60 + 8 <= mins <= 15 * 60 + 45)
            if mkt:
                p = _rms_summary_compute()
                _RMS_SUM_CACHE["payload"], _RMS_SUM_CACHE["ts"] = p, _t.time()
        except Exception:
            pass   # keep last-good; never crash the daemon
        _t.sleep(30 if mkt else 240)


def _rms_warm_start():
    global _rms_warm_started
    if not _rms_warm_started:
        _rms_warm_started = True
        _threading.Thread(target=_rms_warm_loop, daemon=True).start()


@app.route('/api/sync-positions', methods=['POST'])
def api_sync_positions():
    """Force-reconcile the app's LIVE ledger against the broker's trade book,
    AUTHORITATIVELY (ADR-011 mirror). Records any real broker order the app never
    had, keyed by the broker's own order_id (idempotent) — then order_store
    netting pairs it against the strategy leg it closes (broker_reconcile is an
    allowed cross-strategy closer, TRAP #170).

    Replaces the old heuristic broker_sync.force_sync, which guessed exits by
    fill-signature and could wrongly mark a strategy's OWN open leg
    externally_closed while the closing fill was recorded separately by the
    mirror — orphaning the two halves of one real round-trip into a permanent
    phantom short the button then falsely reported as 'cleared' (BAJFINANCE
    2026-08-03, TRAP #170). ONE reconciler now, no guessing. LIVE/kite only —
    paper is a separate simulated ledger, never reconciled against the broker."""
    from datetime import datetime as _dt2, timezone as _tz2, timedelta as _td2
    date = (_dt2.now(_tz2.utc) + _td2(hours=5, minutes=30)).strftime('%Y-%m-%d')
    try:
        import reconcile_broker as _rb
        r = _rb.apply(date, "kite", dry_run=False, log=print)
        recorded = [a for a in (r.get("actions") or []) if a.get("type") == "record"]
        skipped  = [a for a in (r.get("actions") or []) if a.get("type") == "skip"]
        residual = r.get("residual_mismatch") or []
        n = len(recorded)
        parts = []
        if n:
            parts.append(f"✅ {n} broker order(s) mirrored into the app")
        if residual:
            parts.append(f"⚠️ {len(residual)} contract(s) still don't match "
                         "(broker aur app ka net alag) — "
                         + ", ".join(str(m.get("contract")) for m in residual[:4])
                         + " — manual dekho")
        if skipped:
            parts.append(f"⚠️ {len(skipped)} broker order(s) skipped (symbol resolve nahi hua)")
        if not parts:
            parts.append("✅ App already mirrors the broker — nothing to reconcile")
        return jsonify({
            "ok": True,
            "recorded": n,
            "residual": len(residual),
            "ghosts_cleared": n,   # compat: front-end refreshes the P&L table when > 0
            "msg": "  ·  ".join(parts),
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
    # event-driven strategies (e.g. straddle_alert_hedged) fire from a dashboard
    # hook (on_option_alert), NOT a launchable process — and _base() would resolve
    # them to the WRONG trader script. Refuse to Popen them (active flag alone drives
    # the hook). Defense-in-depth alongside auto_scheduler's own event_driven skip.
    try:
        _c = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        if isinstance(_c.get(s), dict) and _c[s].get("event_driven"):
            return jsonify({"msg": f"'{strat_label(s)}' event-driven hai (alert-fire) — "
                                   f"process launch nahi hota; active flag hi kaafi hai."}), 400
    except Exception:
        pass
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
        _write_json_atomic(TC_FILE, cfg)
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
                _write_json_atomic(TC_FILE, cfg)
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
        _write_json_atomic(CONFIG_FILE, cfg)
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
    # opportunistically record today's balance snapshot (once/day) so the ledger
    # graph accumulates even without a dedicated cron — the RMS tab is opened
    # daily. Best-effort, display-only. (broker_ledger, 2026-08-03)
    try:
        import broker_ledger
        broker_ledger.snapshot_if_due()
    except Exception:
        pass
    return jsonify(out)


@app.route('/api/broker-ledger')
def api_broker_ledger():
    """Balance-over-time (ledger) for the RMS Broker Balances panel — per broker:
    balance line + fund add/withdraw markers + table. Display-only; snapshots +
    uploaded CSV ledgers only, no order path. (2026-08-03)"""
    import broker_ledger
    try:
        broker_ledger.snapshot_if_due()   # keep the series fresh on open
    except Exception:
        pass
    try:
        return jsonify(broker_ledger.view())
    except Exception as e:
        print("[broker-ledger] fail:", e, flush=True)
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/broker-ledger/upload', methods=['POST'])
def api_broker_ledger_upload():
    """Upload a broker's own ledger/statement CSV (Zerodha Console → Funds →
    Statement, or Dhan ledger) → real historical closing balance + fund events.
    Tolerant of column naming. Idempotent (re-upload = no-op)."""
    import broker_ledger
    broker = (request.form.get('broker') or '').lower()
    f = request.files.get('file')
    if not f:
        return jsonify({"ok": False, "error": "no file"})
    try:
        name = (f.filename or '').lower()
        raw = f.read()
        if name.endswith('.xlsx') or name.endswith('.xls'):
            return jsonify(broker_ledger.import_ledger_xlsx(broker, raw))
        return jsonify(broker_ledger.import_ledger(broker, raw.decode('utf-8', errors='replace')))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


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
        # Safe mode (blocker #3) bhi yahin hatao. Warna: user subah login karta
        # hai, par safe mode agle token-check (har 6h) tak LIVE entries rokta
        # rehta — ghanton ka blind window bina kisi wajah ke. Token wapas aate hi
        # bahaal hona chahiye, isi jagah pe.
        try:
            import safe_mode
            safe_mode.clear("broker_auth")
        except Exception as _e:
            print(f"[safe_mode] clear-on-login fail: {_e}", flush=True)
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
        _write_json_atomic(CONFIG_FILE, cfg)
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
    """Returns {sym: sec_id}. Handles options (via dhan_master nearest-expiry resolver)
    + equity/index (via _EQ_IDX_SEC and universe). NOTE: for an OPEN position the
    nearest-expiry guess can be the WRONG contract when a month-only trad_sym aliases
    two expiries — so the positions-LTP / P&L path joins on the row's own sec_id
    (see api_positions_ltp `secs`), NOT this. This stays for FRESH-contract callers
    (watchlist / quick-order) where nearest-live IS the right contract (TRAP #166)."""
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


def _seg_for_sec(sid) -> str:
    """Segment for a bare sec_id — reverse of _EQ_IDX_SEC (equity/index), else NSE_FNO
    (options = the overwhelming majority of open positions). Used by the sec_id-keyed
    positions-LTP path so two same-trad_sym expiries each resolve their own contract."""
    s = str(sid)
    for _sym, (_sid, _seg) in _EQ_IDX_SEC.items():
        if str(_sid) == s:
            return _seg
    return "NSE_FNO"


_pos_ltp_cache = {}
_POS_CACHE_TTL = 15

@app.route('/api/positions-ltp')
def api_positions_ltp():
    """Live LTP for open positions — joined on sec_id (the ONLY unique contract key),
    NOT trad_sym. Client sends `secs` (comma sec_ids); ltp_map is keyed by sec_id.
    Legacy `syms` still works (keyed by sym) for any trad_sym caller. dhan_feed WS
    first, then shared cache, then Dhan REST. TRAP #166: month-only NIFTY/BNF trad_syms
    alias two expiries (weekly+monthly) onto one key → a sym-keyed map showed one
    position's LTP on the other → fake P&L. sec_id can't alias."""
    secs = [s.strip() for s in request.args.get('secs', '').split(',') if s.strip()]
    syms = [s.strip() for s in request.args.get('syms', '').split(',') if s.strip()]
    if not secs and not syms:
        return jsonify({"ok": True, "ltp_map": {}})

    _ensure_feed_started()
    # Unified work list of (key, sec_id, seg). key = sec_id for `secs`, sym for `syms`.
    items = []
    for sid in secs:
        items.append((str(sid), str(sid), _seg_for_sec(sid)))
    if syms:
        sym_sec = _get_sec_ids(syms)
        for sym in syms:
            sid = sym_sec.get(sym)
            if sid:
                items.append((sym, str(sid), _get_seg(sym)))
    if not items:
        return jsonify({"ok": True, "ltp_map": {}})

    ltp_map = {}

    # ── Crypto (Delta) legs: sec_id is a Delta symbol (e.g. C-BTC-80800-250826),
    # NOT an NSE sec_id — resolve LTP from delta_feed marks and convert to the
    # SAME INR-per-lot units order_store stored (mark x contract_value x USD/INR),
    # so the Open Positions P&L math (which is INR) stays consistent. Pull these
    # out so the NSE Dhan feed below never sees them. ──
    def _is_delta(sid):
        s = str(sid)
        return ("-BTC-" in s or "-ETH-" in s) and (s[:2] in ("C-", "P-"))
    crypto_items = [it for it in items if _is_delta(it[1])]
    if crypto_items:
        items = [it for it in items if not _is_delta(it[1])]
        try:
            from _ops import delta_feed as _df
        except Exception:
            try:
                import delta_feed as _df
            except Exception:
                _df = None
        if _df is not None:
            try:
                _dcfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                _rate = float((_dcfg.get("_delta_ironfly") or {}).get("usd_inr") or 85.0)
            except Exception:
                _rate = 85.0
            _cv = {"BTC": 0.001, "ETH": 0.01}
            _unds = {("BTC" if "-BTC-" in str(sid) else "ETH")
                     for _k, sid, _s in crypto_items}
            try:
                for _und in _unds:
                    marks = {t.get("symbol"): _df._f(t.get("mark_price"))
                             for t in _df._all_option_tickers(_und)}
                    cvv = _cv.get(_und, 0.001)
                    for key, sid, _seg in crypto_items:
                        m = marks.get(str(sid))
                        if m is not None:
                            ltp_map[key] = {"ltp": round(m * cvv * _rate, 2), "qty": None}
            except Exception:
                pass
    if not items:
        return jsonify({"ok": True, "ltp_map": ltp_map, "src": "delta"})

    try:
        _feed_subscribe([(seg, sid) for _k, sid, seg in items])
    except Exception:
        pass

    # 1) WS feed (instant, free)
    missing = []
    try:
        import dhan_feed
        for key, sid, seg in items:
            q = dhan_feed.get_quote(sid)
            if q and q.get("ltp"):
                ltp_map[key] = {"ltp": q["ltp"], "qty": None}
            else:
                missing.append((key, sid, seg))
    except Exception:
        missing = list(items)

    if not missing:
        return jsonify({"ok": True, "ltp_map": ltp_map, "src": "ws"})

    # 2) shared_ltp_cache (ltp_poller warms open positions every ~1.5s) + process cache
    try:
        import requests as _req, time as _t
        now = _t.time()
        try:
            import shared_ltp_cache as _slc_pos
        except Exception:
            _slc_pos = None
        still = []
        for key, sid, seg in missing:
            c = _pos_ltp_cache.get(sid)
            if c and (now - c['ts']) < _POS_CACHE_TTL:
                ltp_map[key] = {"ltp": c['ltp'], "qty": None}
                continue
            shared = _slc_pos.get(str(sid), max_age=6) if (_slc_pos and sid) else None
            if shared:
                ltp_map[key] = {"ltp": shared, "qty": None}
                _pos_ltp_cache[sid] = {'ltp': shared, 'ts': now}
            else:
                still.append((key, sid, seg))
        missing = still

        # 3) Dhan REST for whatever's still missing
        if missing:
            try:
                import ltp_poller as _lp
                _lp.request_watch([(sid, seg) for _k, sid, seg in missing])
            except Exception:
                pass
            _rl.set_context("Dashboard:PosLTP")
            if not _rl.acquire("ltp"):
                # gate busy — poller warms these within ~1.5s; frontend re-polls 3s
                return jsonify({"ok": True, "ltp_map": ltp_map, "src": "cache-pending"})
            token, cid = _creds()
            headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
            seg_groups = {}
            sid_to_keys = {}
            for key, sid, seg in missing:
                dhan_seg = {"NSE_EQ": "NSE_EQ", "IDX_I": "IDX_I", "NSE_FNO": "NSE_FNO"}.get(seg, "NSE_FNO")
                seg_groups.setdefault(dhan_seg, set()).add(int(sid))
                sid_to_keys.setdefault(str(sid), []).append(key)
            body = {seg: list(sids) for seg, sids in seg_groups.items()}
            r = _req.post("https://api.dhan.co/v2/marketfeed/ltp", json=body, headers=headers, timeout=5)
            if r.status_code == 429:
                _rl.note_429()
            if r.status_code == 200:
                for _seg_key, quotes in (r.json().get("data", {}) or {}).items():
                    if not isinstance(quotes, dict): continue
                    for sec_id_str, q in quotes.items():
                        keys = (sid_to_keys.get(str(sec_id_str))
                                or sid_to_keys.get(str(sec_id_str).lstrip('0')) or [])
                        ltp = float(q.get("last_price") or q.get("ltp") or 0)
                        if ltp:
                            for key in keys:
                                ltp_map[key] = {"ltp": ltp, "qty": None}
                            _pos_ltp_cache[str(sec_id_str)] = {'ltp': ltp, 'ts': now}
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
                # sec_id(str) -> ltp. Keyed by sec_id, NOT trad_sym: two open positions
                # can share a month-only trad_sym on different expiries, so a sym-keyed
                # stream collapsed them onto ONE ltp → fake P&L (TRAP #166). The client
                # joins each cell on its own data-sec (the contract it actually holds).
                sec_ltp = {}
                # snapshot() = own LIVE + the feed owner's shared store (ADR-013) —
                # iterating LIVE directly was {} whenever another process held the socket.
                for sec_id, q in dhan_feed.snapshot(max_age=dhan_feed.FEED_MAX_AGE).items():
                    if q.get("ltp"):
                        sec_ltp[str(sec_id)] = round(q["ltp"], 2)
                yield f"data: {json.dumps(sec_ltp)}\n\n"
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


def _save_premium_ohlc(sec_id, date_str, bars_by_epoch, complete=False):
    """Persist option-premium 1-min bars keyed by RAW Dhan epoch (str) → [o,h,l,c],
    so an expired contract's premium chart still renders after Dhan stops serving
    it (#5). Epoch keys are timezone-unambiguous (the +19800 IST-display shift is
    applied only at read time, exactly like the live path) — avoids the double-
    shift class of bug (TRAP #29). Merges with any existing (daemon-written)
    file. Best-effort.

    complete=True marks the file as a FULL past day's bars (quick-load, feature
    2026-08-03): a fetch for a date strictly before today returns the whole day,
    so it can be served straight from disk on every future open (this session /
    next session / next day) with zero Dhan round-trip. Stored as a non-digit,
    non-colon '_complete' key which the candle reader skips."""
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
        if complete:
            existing["_complete"] = 1
        p.write_text(json.dumps(existing))
    except Exception:
        pass


def _premium_ohlc_is_complete(sec_id, date_str):
    """True if a FULL past-day cache exists for this contract (quick-load) — i.e.
    a prior fetch of a strictly-past date already stored the whole day. Only such
    files are served disk-first (partial/daemon HH:MM captures still go to Dhan)."""
    try:
        p = _premium_ohlc_path(sec_id, date_str)
        if not p.exists():
            return False
        return bool(json.loads(p.read_text()).get("_complete"))
    except Exception:
        return False


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


_straddle_leg_cache = {}     # (sec_id, date) -> (candles_list, fetched_at)
_STRADDLE_LEG_TTL = 45       # seconds — 1-min bars change once a minute; the chart polls every 10s

def _leg_premium_candles(sec_id, date_str):
    """1-min premium candles [{time, close}] for ONE straddle SELL leg.
    Disk-captured bars first (auto_data_downloader / trade-chart write-through,
    instant); live Dhan intraday fallback for fresh/paper legs the daemon never
    captured — PAPER straddle legs don't hit the broker /v2/orders the daemon
    polls, so their disk file is empty and the chart was blank (#6). The live
    fetch is cached _STRADDLE_LEG_TTL s so the chart's 10s poll doesn't hammer
    Dhan (rate-limited 'candle' priority either way). Returns [] on no data.
    NIFTY/BANKNIFTY straddles only → instrument OPTIDX."""
    disk = _load_premium_ohlc_candles(str(sec_id), date_str)
    if disk and disk.get("candles"):
        return [{"time": c["time"], "close": c["close"]} for c in disk["candles"]]
    import time as _t
    key = (str(sec_id), date_str)
    hit = _straddle_leg_cache.get(key)
    if hit and (_t.time() - hit[1]) < _STRADDLE_LEG_TTL:
        return hit[0]
    out = []
    try:
        import requests as _req
        import dhan_rate_limiter as _drl
        token, cid = _creds()
        hdrs = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        _drl.set_context("Straddle:ChartIntraday")
        if _drl.acquire("candle"):
            r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
                "securityId": str(sec_id), "exchangeSegment": "NSE_FNO", "instrument": "OPTIDX",
                "expiryCode": 0, "fromDate": date_str, "toDate": date_str}, timeout=10)
            if r.status_code == 429:
                _drl.note_429()
            elif r.status_code == 200:
                d = r.json()
                if d.get("close") and d.get("timestamp"):
                    for ts, c in zip(d["timestamp"], d["close"]):
                        out.append({"time": int(ts) + 19800, "close": round(float(c), 2)})
    except Exception:
        out = []
    if out:
        _straddle_leg_cache[key] = (out, _t.time())
    return out


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

@app.route('/strategy-study')
def strategy_study_page():
    """Landscape 'study' of every trade a strategy took — one chart per trade
    (index + RSI + premium) with a stats header, so it's clear WHEN the strategy
    works and when it fails. Display-only; login-gated (before_request)."""
    return render_template('strategy_study.html')


@app.route('/api/strategy-study/trades')
def api_strategy_study_trades():
    """Completed trades for a strategy over a date range + aggregate. The study page
    lists these, then fetches each trade's index+premium+RSI via the existing
    /api/trade-chart-* endpoints. Read-only — reuses order_store + _strategy_matcher
    + _enrich_trade_display (no new trade/P&L math path)."""
    import order_store, datetime as _dt
    strat = request.args.get('strategy', '').strip()
    today = (_dt.datetime.now(timezone.utc) + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    to = request.args.get('to', '').strip() or today
    try:
        frm = request.args.get('from', '').strip() or \
            (_dt.datetime.strptime(to, "%Y-%m-%d") - _dt.timedelta(days=120)).strftime("%Y-%m-%d")
    except Exception:
        frm = to
    data = order_store.trades_for_range(frm, to)
    rows = list(data.get('details') or [])
    if strat:
        _m = _strategy_matcher(strat)
        rows = [r for r in rows if _m(r.get('strategy'))]
    try:
        _enrich_trade_display(rows)   # lot_size (for Lots) — best-effort
    except Exception:
        pass
    out, wins, net, gw, gl = [], 0, 0.0, 0.0, 0.0
    for r in rows:
        ep = float(r.get('entry_price') or 0); xp = float(r.get('exit_price') or 0)
        q = float(r.get('qty') or 0); side = r.get('entry')
        pnl = float(r.get('pnl') or 0)
        pts = (xp - ep) if side == 'BUY' else (ep - xp)
        pct = (pts / ep * 100) if ep else 0
        lot = float(r.get('lot_size') or 0)
        tsym = r.get('sym') or ''
        opt = 'CE' if tsym.endswith('-CE') else ('PE' if tsym.endswith('-PE') else '')
        win = pnl > 0
        wins += 1 if win else 0
        net += pnl
        gw += pnl if pnl >= 0 else 0
        gl += (-pnl) if pnl < 0 else 0
        out.append({
            "sym": tsym, "root": tsym.split('-')[0], "opt": opt,
            "date": r.get('entry_date') or '', "exit_date": r.get('exit_date') or r.get('entry_date') or '',
            "side": side, "entry_price": round(ep, 2), "exit_price": round(xp, 2),
            "entry_time": r.get('entry_time') or '', "exit_time": r.get('exit_time') or '',
            "qty": int(q), "lot_size": int(lot) if lot else None,
            "lots": int(round(q / lot)) if lot else None,
            "pnl": round(pnl, 2), "points": round(pts, 2), "pct": round(pct, 2),
            "win": win, "strategy": r.get('strategy'), "exit_reason": r.get('exit_reason') or '',
        })
    out.sort(key=lambda t: (t['date'], t['entry_time']), reverse=True)   # newest first
    n = len(out); losses = n - wins
    agg = {"trades": n, "wins": wins, "losses": losses,
           "win_pct": round(wins / n * 100, 1) if n else 0,
           "net": round(net, 2), "pf": round(gw / gl, 2) if gl else None,
           "avg_win": round(gw / wins) if wins else 0,
           "avg_loss": round(gl / losses) if losses else 0}
    return jsonify({"ok": True, "strategy": strat,
                    "name": request.args.get('name', '').strip() or strat or 'All strategies',
                    "from": frm, "to": to, "trades": out, "agg": agg})


@app.route('/api/reconcile-csv', methods=['POST'])
def api_reconcile_csv():
    """Upload a Zerodha tradebook CSV → reconcile the app's LIVE ledger to it. Default is a
    read-only PREVIEW (per-contract broker-net vs app-net); `?apply=1` writes the missing
    deltas (idempotent). LIVE only. Login-gated (before_request)."""
    try:
        from _ops import reconcile_csv as _rc
        f = request.files.get('file')
        text = f.read().decode('utf-8', 'replace') if f else request.get_data(as_text=True)
        if not (text or '').strip():
            return jsonify({"ok": False, "msg": "empty file — Zerodha tradebook CSV upload karo"})
        if request.args.get('apply') == '1':
            return jsonify(_rc.apply(text, dry_run=False))
        p = _rc.plan(text)
        p.pop('_csv_px', None)          # internal price map — UI doesn't need it
        return jsonify(p)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


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
    entry_t  = request.args.get('et', '').strip()   # HH:MM IST (comma-list when >1 trade on same contract merged into one chart)
    exit_t   = request.args.get('xt', '').strip()
    entry_times = [x.strip() for x in entry_t.split(',') if x.strip()]
    exit_times  = [x.strip() for x in exit_t.split(',') if x.strip()]
    entry_t_first = entry_times[0] if entry_times else ''   # single-consumer helpers (SL series / disk) take one time
    exit_t_first  = exit_times[0] if exit_times else ''
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

        # QUICK-LOAD (2026-08-03): a completed trade on a PAST day has immutable
        # bars — once fetched, serve straight from disk on every future open (this
        # session / next session / next day) with zero Dhan round-trip. Only a
        # single-day request whose full-day cache was already stored (_complete)
        # qualifies; today / positional multi-day spans still go live.
        if (seg == "NSE_FNO" and to_date == date_str and date_str < _today_str
                and _premium_ohlc_is_complete(sec_id, date_str)):
            disk = _load_premium_ohlc_candles(sec_id, date_str, entry_t_first, exit_t_first)
            if disk and disk.get("candles"):
                return jsonify({"ok": True, "candles": disk["candles"],
                                "entry_mk": disk["entry_mk"], "exit_mk": disk["exit_mk"],
                                "entry_mks": [disk["entry_mk"]] if disk["entry_mk"] else [],
                                "exit_mks": [disk["exit_mk"]] if disk["exit_mk"] else [],
                                "sl_series": _reconstruct_sl_series(trad_sym, date_str, sec_id, disk["candles"], disk["entry_mk"], entry_t=entry_t_first, strategy=strategy),
                                "trad_sym": trad_sym, "date": date_str, "source": "cache"})

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
            disk = _load_premium_ohlc_candles(sec_id, date_str, entry_t_first, exit_t_first)
            if disk:
                return jsonify({"ok": True, "candles": disk["candles"],
                                "entry_mk": disk["entry_mk"], "exit_mk": disk["exit_mk"],
                                "entry_mks": [disk["entry_mk"]] if disk["entry_mk"] else [],
                                "exit_mks": [disk["exit_mk"]] if disk["exit_mk"] else [],
                                "sl_series": _reconstruct_sl_series(trad_sym, date_str, sec_id, disk["candles"], disk["entry_mk"], entry_t=entry_t_first, strategy=strategy),
                                "trad_sym": trad_sym, "date": date_str, "source": "disk"})
            return jsonify({"ok": False, "msg": f"{date_str} ka intraday data nahi (non-trading day / expired contract)"})
        candles, entry_mk, exit_mk = [], None, None
        entry_mks, exit_mks = [], []   # ALL entry/exit marker epochs (>1 when same-contract trades merged into one chart)
        _seen_e, _seen_x = set(), set()
        _bars_by_epoch = {}
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
            t_ist = int(ts) + 19800   # +5:30 → chart shows IST (treated as UTC by lightweight-charts)
            hhmm  = _dt.datetime.utcfromtimestamp(int(ts) + 19800).strftime("%H:%M")
            candles.append({"time": t_ist, "open": round(float(o), 2), "high": round(float(h), 2),
                            "low": round(float(l), 2), "close": round(float(c), 2)})
            _bars_by_epoch[str(int(ts))] = [round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2)]
            if hhmm in entry_times and hhmm not in _seen_e:
                _seen_e.add(hhmm); entry_mks.append(t_ist)
                if entry_mk is None: entry_mk = t_ist
            if hhmm in exit_times and hhmm not in _seen_x:
                _seen_x.add(hhmm); exit_mks.append(t_ist)
                if exit_mk is None: exit_mk = t_ist
        # Write-through: persist so this contract's chart survives its expiry (#5).
        # Only for a single-day fetch — a multi-day (positional) span would write
        # today's bars into date_str's file (keyed per date) and corrupt it.
        # complete=True for a strictly-past date (full immutable day) → future
        # opens serve disk-first (quick-load).
        if seg == "NSE_FNO" and _bars_by_epoch and to_date == date_str:
            _save_premium_ohlc(sec_id, date_str, _bars_by_epoch, complete=(date_str < _today_str))
        return jsonify({"ok": True, "candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk,
                        "entry_mks": entry_mks, "exit_mks": exit_mks,
                        "sl_series": _reconstruct_sl_series(trad_sym, date_str, sec_id, candles, entry_mk, entry_t=entry_t_first, strategy=strategy),
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
    entry_t = request.args.get('et', '').strip()      # comma-list when >1 same-contract trade merged
    exit_t = request.args.get('xt', '').strip()
    entry_times = [x.strip() for x in entry_t.split(',') if x.strip()]
    exit_times = [x.strip() for x in exit_t.split(',') if x.strip()]
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

        # QUICK-LOAD (2026-08-03): the underlying/index pane is the DEFAULT view in
        # the Daily Report (Only-Index panes) — its 1-min candles for a completed
        # PAST day are immutable, so serve them disk-first (this session / next
        # session / next day) with zero Dhan round-trip. Today / positional
        # multi-day spans still fetch live. Overlays (markers/RSI/ATR) recompute
        # from whichever candle source. sec_id here is the underlying's — no
        # collision with the option pane's file (different sec_id).
        candles = None
        if (to_date == date_str and date_str < _today_str
                and _premium_ohlc_is_complete(sec_id, date_str)):
            _disk = _load_premium_ohlc_candles(sec_id, date_str)
            if _disk and _disk.get("candles"):
                candles = _disk["candles"]

        if candles is None:
            # Dhan intraday for INDEX/EQUITY underlyings needs "interval" (not
            # "expiryCode" — that's the OPTION shape). Same payload as the proven
            # live strategy fetch (range_trader.fetch_1m: interval "1"). expiryCode:0
            # here returned empty candles for IDX_I → "underlying data nahi" (the
            # premium pane works because options DO take expiryCode). 2026-08-01 fix.
            r = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
                "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
                "interval": "1", "fromDate": date_str, "toDate": to_date}, timeout=12)
            d = r.json()
            if not d.get("open"):
                return jsonify({"ok": False, "msg": f"{date_str} ka underlying intraday data nahi"})
            candles = []
            _u_bars = {}
            for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
                t_ist = int(ts) + 19800
                candles.append({"time": t_ist, "open": round(float(o), 2), "high": round(float(h), 2),
                                "low": round(float(l), 2), "close": round(float(c), 2)})
                _u_bars[str(int(ts))] = [round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2)]
            # write-through: strictly-past single day = full immutable → cache it
            if _u_bars and to_date == date_str and date_str < _today_str:
                _save_premium_ohlc(sec_id, date_str, _u_bars, complete=True)

        # markers from candles (works for disk OR live source). candle 'time' is
        # already IST-shifted (+19800), so utcfromtimestamp(time) == IST wall-clock.
        entry_mk, exit_mk = None, None
        entry_mks, exit_mks = [], []   # ALL markers (>1 when same-contract trades merged into one chart)
        _seen_e, _seen_x = set(), set()
        for _c in candles:
            t_ist = _c["time"]
            hhmm = _dt.datetime.utcfromtimestamp(int(t_ist)).strftime("%H:%M")
            if hhmm in entry_times and hhmm not in _seen_e:
                _seen_e.add(hhmm); entry_mks.append(t_ist)
                if entry_mk is None: entry_mk = t_ist
            if hhmm in exit_times and hhmm not in _seen_x:
                _seen_x.add(hhmm); exit_mks.append(t_ist)
                if exit_mk is None: exit_mk = t_ist
        # RSI overlay — if this trade's strategy trades on RSI, compute the SAME Wilder
        # RSI it uses (its own timeframe from config) so the OB/OS entry levels + the
        # midline (50) EXIT line are visible on the chart. Uses _CHARTING.indicators.
        # wilder_rsi (the exact formula the live strategy runs). Best-effort — a failure
        # just omits the overlay, the candle chart still renders.
        rsi = None
        try:
            scfg = json.loads(TC_FILE.read_text()).get(strategy_id) or {}
            if (scfg.get('rsi_period') or scfg.get('rsi_exit') or 'rsi' in strategy_id.lower()) and len(candles) > 20:
                period = int(scfg.get('rsi_period') or 14)
                # EFFECTIVE timeframe = exactly what 01_rsi_v1.py runs: TF_MAP.get(cfg, 5).
                # A config label not in this map (e.g. "2m") silently falls back to 5m in
                # the live strategy, so the chart MUST mirror that or it draws a different-TF
                # RSI than the strategy acted on (the "entry @ RSI 52 vs strategy's 71" bug —
                # config said 2m, strategy ran 5m). 2026-08-01.
                _RSI_TFMAP = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}
                tf_min = _RSI_TFMAP.get(str(scfg.get('timeframe') or '5m').strip(), 5)
                ob = float(scfg.get('overbought') or 70)
                oss = float(scfg.get('oversold') or 30)
                mid = float(scfg.get('rsi_exit') or 50)
                import pandas as pd
                from _CHARTING import indicators as _ind
                import bisect as _bisect
                # Compute RSI on the strategy's OWN native-TF candles (Dhan interval=tf_min,
                # single day = date_str) EXACTLY as 01_rsi_v1.fetch_candles does — NOT by
                # epoch-floor re-bucketing the 1-min pane. Re-bucketing 1-min bars
                # (time//120*120) lands on different boundaries than Dhan's native 2m
                # aggregation, shifting every 2m close by ~1 min → a recursively-divergent
                # Wilder RSI (chart showed ~52 where the live strategy computed ~71 → looked
                # like the SHORT fired wrongly when it hadn't). 2026-08-01 fix.
                if tf_min > 1:
                    nrr = _req.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
                        "securityId": str(sec_id), "exchangeSegment": seg, "instrument": inst,
                        "interval": str(tf_min), "fromDate": date_str, "toDate": date_str}, timeout=12)
                    nrd = nrr.json()
                    nat = [(int(ts) + 19800, float(cl))
                           for ts, cl in zip(nrd.get("timestamp", []), nrd.get("close", []))]
                else:
                    nat = [(c['time'], c['close']) for c in candles]
                nat.sort()
                if len(nat) > period + 2:
                    rv = _ind.wilder_rsi(pd.Series([x[1] for x in nat]), period)
                    starts = [x[0] for x in nat]
                    nat_rsi = {starts[i]: rv.iloc[i] for i in range(len(starts)) if rv.iloc[i] == rv.iloc[i]}
                    # Forward-fill each native-TF RSI onto every 1-min display candle in its
                    # window (largest native-bar-start <= candle time) so the RSI series
                    # shares the candles' exact times + bar-count → the two panes stay
                    # time-aligned under logical(bar-index) range sync. Step line, TF-accurate.
                    series = []
                    for c in candles:
                        j = _bisect.bisect_right(starts, c['time']) - 1
                        val = nat_rsi.get(starts[j]) if j >= 0 else None
                        series.append({"time": c['time'], "value": round(float(val), 2)}
                                      if val is not None else {"time": c['time']})
                    if any('value' in p for p in series):
                        rsi = {"series": series, "ob": ob, "mid": mid, "os": oss, "period": period, "tf": f"{tf_min}m"}
        except Exception:
            rsi = None
        # ATR trailing-stop overlay — if this trade's strategy exits on ATR (range
        # family, exit_atr on), reconstruct the SAME trailing stop range_trader runs
        # (wilder_atr(14) × 2 on the strategy's own TF, ratcheting from entry) for the
        # position's index direction, so the chart shows WHERE the ATR exit sits — the
        # strategy's MAIN exit, distinct from the premium-pane's ₹ SL floor. Same
        # _CHARTING.indicators the live strategy uses. Best-effort; approx (live ATR is
        # warm across days). Display-only.
        atr_trail = None
        try:
            scfg = json.loads(TC_FILE.read_text()).get(strategy_id) or {}
            _atr_on = str(scfg.get('exit_atr', scfg.get('atr_exit', ''))).lower() in ('true', '1', 'yes')
            er = _trade_entry_row(trad_sym, date_str, entry_t=entry_t, strategy=strategy_id) if _atr_on else None
            _side = str((er or {}).get('side') or '').upper()
            _ot = 'CE' if trad_sym.upper().endswith('-CE') else ('PE' if trad_sym.upper().endswith('-PE') else '')
            if _atr_on and entry_mk is not None and _side and _ot and len(candles) > 16:
                _long = (_side == 'BUY') == (_ot == 'CE')   # SELL PE / BUY CE = index LONG
                tf_min = int(''.join(ch for ch in str(scfg.get('timeframe') or '1m') if ch.isdigit()) or 1) or 1
                buck = {}                                   # strategy-TF resample: bucket OHLC
                for c in candles:
                    b = (c['time'] // (tf_min * 60)) * (tf_min * 60)
                    e = buck.get(b)
                    if e is None:
                        buck[b] = {'time': c['time'], 'high': c['high'], 'low': c['low'], 'close': c['close']}
                    else:
                        e['high'] = max(e['high'], c['high']); e['low'] = min(e['low'], c['low'])
                        e['close'] = c['close']; e['time'] = c['time']
                bts = sorted(buck)
                import pandas as pd
                from _CHARTING import indicators as _ind
                av = _ind.wilder_atr(pd.DataFrame([buck[b] for b in bts]), 14)   # exact ATR range_trader uses
                MULT, sl, series = 2.0, None, []
                for i, b in enumerate(bts):
                    bk = buck[b]
                    if bk['time'] < entry_mk:
                        continue
                    a = float(av.iloc[i]) if (i < len(av) and av.iloc[i] == av.iloc[i]) else None
                    if a is None:
                        continue
                    lvl = (bk['close'] - a * MULT) if _long else (bk['close'] + a * MULT)
                    sl = lvl if sl is None else (max(sl, lvl) if _long else min(sl, lvl))
                    series.append({"time": bk['time'], "value": round(sl, 2)})
                if len(series) >= 2:
                    atr_trail = {"direction": "LONG" if _long else "SHORT",
                                 "series": series, "mult": MULT, "period": 14, "tf": f"{tf_min}m"}
        except Exception:
            atr_trail = None
        return jsonify({"ok": True, "candles": candles, "entry_mk": entry_mk, "exit_mk": exit_mk,
                        "entry_mks": entry_mks, "exit_mks": exit_mks,
                        "symbol": root, "date": date_str, "zone": zone, "rsi": rsi, "atr_trail": atr_trail})
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


def _payoff_resolve(args):
    """Resolve payoff legs from request args — either `ids=` (open legs, the
    original path) OR `group_id=` (a whole hedge/pair GROUP, open OR closed).
    For a CLOSED group the legs are reconstructed from completed round-trips
    (each carries entry side/price/strike/sec_id/group_id) so the payoff still
    renders after the position has flattened (#01 — 'kaun sa leg kis pair ka').
    Returns (rows, is_closed). build_legs reads the same fields off open rows and
    completed-detail rows alike, so both feed payoff.analyse unchanged."""
    import order_store
    from datetime import datetime as _d, timedelta as _td, timezone as _tz
    ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
    lb = (ist - _td(days=7)).strftime('%Y-%m-%d')
    data = order_store.trades_for_range(lb, ist.strftime('%Y-%m-%d'))
    gid = (args.get('group_id') or '').strip()
    if gid:
        opens = [r for r in data.get('open', [])
                 if (r.get('group_id') or '') == gid and 'CAPITAL_BLOCKED' not in (r.get('tags') or [])]
        closed = [dict(d, status='closed') for d in data.get('details', [])
                  if (d.get('group_id') or '') == gid]
        rows = opens + closed
        return rows, (len(opens) == 0 and len(closed) > 0)
    ids = [i for i in (args.get('ids') or '').split(',') if i.strip()]
    want = {str(i) for i in ids}
    opens = [r for r in data.get('open', []) if str(r.get('id')) in want]
    have = {str(r.get('id')) for r in opens}
    closed = [dict(d, status='closed') for d in data.get('details', [])
              if str(d.get('id')) in want and str(d.get('id')) not in have]
    rows = opens + closed
    return rows, (len(opens) == 0 and len(closed) > 0)


def _payoff_attach_ltp(rows):
    """Live premium per leg onto the order_store rows. order_store carries no
    price beyond the ENTRY fill — but implied vol (and hence the today-curve /
    POP / margin price) must come from the CURRENT premium, not the entry one.
    Feed + shared cache first (no network); whatever's still missing gets ONE
    BATCHED /v2/marketfeed/ltp call (via _prewarm_option_ltps) instead of a
    per-leg rate-limited REST fetch. The OPEN-group payoff used to take ~8s
    (N legs each doing a serial ~1/sec REST call, post-market) — the user saw it
    "stuck loading"; batching turns N serial calls into 1."""
    try:
        import ltp_poller
        ltp_poller.request_watch([(r.get('segment') or 'NSE_FNO', str(r.get('sec_id')))
                                  for r in rows if r.get('sec_id')])
    except Exception:
        pass
    # pass 1 — feed + shared cache (no network)
    missing = []
    for r in rows:
        sid = r.get('sec_id')
        if not sid:
            continue
        ltp = 0
        try:
            import dhan_feed
            q = dhan_feed.get_quote(str(sid), max_age=_FEED_MAX_AGE)
            ltp = float(q.get('ltp') or 0) if q else 0
        except Exception:
            ltp = 0
        if not ltp:
            try:
                import shared_ltp_cache
                ltp = float(shared_ltp_cache.get_stale(str(sid), max_age=180) or 0)
            except Exception:
                ltp = 0
        if ltp:
            r['ltp'] = ltp
        else:
            missing.append(r)
    # pass 2 — ONE batched LTP call for the rest, then re-read the warmed cache
    if missing:
        try:
            _prewarm_option_ltps([str(r.get('sec_id')) for r in missing if r.get('sec_id')])
            import shared_ltp_cache
            for r in missing:
                sid = r.get('sec_id')
                if sid:
                    ltp = float(shared_ltp_cache.get_stale(str(sid), max_age=60) or 0)
                    if ltp:
                        r['ltp'] = ltp
        except Exception:
            pass
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
        v = shared_ltp_cache.get_index(root, max_age=60)          # fresh (market hours — poller-warm)
        if v:
            return float(v)
        v = shared_ltp_cache.get_index(root, max_age=86400)       # any today value — INSTANT, covers
        if v:                                                     # post-market (poller stops at EOD);
            return float(v)                                       # a stale spot still positions the curve
    except Exception:
        pass
    # last resort — REST (rate-limited, can be slow post-market): only when the
    # cache never had this index at all (e.g. a fresh restart before any poll).
    try:
        if root in _EQ_IDX_SEC:
            sid, seg = _EQ_IDX_SEC[root]
        else:
            import universe
            sid, seg = universe.equity_secid(root), "NSE_EQ"
        if sid:
            v = float(_rest_ltp_fallback(sid, seg) or 0)
            if v:
                return v
    except Exception:
        pass
    return None


_payoff_cache = {}      # qs -> (res, ts) — LTP-attach + analyse is a few-second op;
_PAYOFF_TTL = 60        # rapid group-switching re-hits instantly. The warm loop below
                        # refreshes open groups every ~20s so the FIRST open is a cache
                        # hit too (user: panel loading slow / "mood kharab").


def _pf_key(prefix, rows, extra=''):
    """CANONICAL cache key from the RESOLVED leg ids — NOT the raw query string. The
    panel opens the same group three ways (group_id=…, ids=<csv> from the 📊 button in
    any leg order, ids=<single>), so a raw-qs key made the warm loop's entry un-hittable
    for the exact form the UI used. Keying on sorted resolved ids makes every path (and
    the warm loop) share ONE entry."""
    return prefix + '|' + ','.join(sorted(str(r.get('id')) for r in rows)) + ('|' + str(extra) if extra else '')


def _payoff_compute_rows(rows, closed, td=None):
    """Payoff/zone analytics from already-resolved rows — shared by the route AND the
    warm loop (Rule 6B), so a warmed entry is byte-identical to a live request."""
    import payoff
    if not closed:
        rows = _payoff_attach_ltp(rows)          # live IV/today-curve only for still-open legs
    spot = _payoff_spot(payoff.build_legs(rows))
    try:
        td = float(td) if td else None
    except Exception:
        td = None
    res = payoff.analyse(rows, spot, target_days=td)
    if isinstance(res, dict):
        res['closed'] = bool(closed)
    return res


def _legs_series_compute(args):
    """Per-leg premium series + combined net-structure P&L for a group — the slow part
    (each leg = one serial rate-limited Dhan candle call). Extracted from the route so
    the warm loop can pre-fetch it for open multi-leg groups (Rule 6B)."""
    import payoff
    from datetime import datetime as _d, timedelta as _td, timezone as _tz
    rows, closed = _payoff_resolve(args)
    if not rows:
        return {"ok": False, "msg": "no legs for this group"}
    legs = [L for L in payoff.build_legs(rows) if L.get('sec_id')]
    if not legs:
        return {"ok": False, "msg": "legs have no sec_id"}
    ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
    today = ist.strftime('%Y-%m-%d')
    entry_dates = [r.get('entry_date') for r in rows if r.get('entry_date')]
    frm = min(entry_dates) if entry_dates else today
    exit_dates = [r.get('exit_date') for r in rows if r.get('exit_date')]
    today = (max(exit_dates) if (closed and exit_dates) else today)
    entry_epoch = 0
    try:
        _ed = min(entry_dates)
        _et = min((r.get('entry_time') or '23:59') for r in rows if (r.get('entry_date') == _ed))
        _dtm = _d.strptime(f"{_ed} {_et}", "%Y-%m-%d %H:%M")
        entry_epoch = int((_dtm - _d(1970, 1, 1)).total_seconds()) - 19800
    except Exception:
        entry_epoch = 0
    out_legs, bars = [], {}
    for L in legs:
        b = _leg_closes(L['sec_id'], frm, today)
        b = {t: c for t, c in b.items() if t >= entry_epoch}
        bars[L['trad_sym']] = (b, L)
        out_legs.append({"trad_sym": L['trad_sym'], "side": L['side'], "opt": L['opt'],
                         "strike": L['strike'], "qty": L['qty'], "entry": L['entry'],
                         "series": sorted([[t, c] for t, c in b.items()])})
    entry_net = sum((L['entry'] if L['side'] == 'SELL' else -L['entry']) for L in legs)
    common = None
    for b, _L in bars.values():
        ks = set(b.keys())
        common = ks if common is None else (common & ks)
    combined = []
    for ts in sorted(common or []):
        net = sum((-b[ts] if L['side'] == 'SELL' else b[ts]) for b, L in bars.values())
        combined.append([ts, round(net + entry_net, 2)])
    return {"ok": True, "legs": out_legs, "combined": combined,
            "entry_net": round(entry_net, 2), "from": frm, "to": today}


_payoff_warm_started = False
_PAYOFF_WARM_INTERVAL = 20     # market-hours cadence; keeps every open group inside _PAYOFF_TTL


def _payoff_warm_loop():
    """Pre-compute payoff/zone (every ~20s) AND the heavy legs-series combined-premium
    chart (every ~80s — it's N serial Dhan candle calls) for every OPEN group, into
    their caches, so the panel opens instant instead of cold-fetching. Keyed canonically
    (_pf_key) so it matches whatever qs the UI sends. Display-only — touches only caches."""
    import order_store, time as _t
    cycle = 0
    while True:
        slept = _PAYOFF_WARM_INTERVAL
        cycle += 1
        try:
            mkt = True
            try:
                from _core import market_calendar as _mc
                mkt = _mc.is_market_open()
            except Exception:
                mkt = True
            data = order_store.trades_for(order_store.ist_now_str()[:10])
            groups = {}          # gid -> [ids]   (multi-leg structures)
            singles = []         # [id]           (single legs)
            for o in (data.get('open') or []):
                if 'CAPITAL_BLOCKED' in (o.get('tags') or []):
                    continue
                oid = o.get('id')
                if oid is None:
                    continue
                gid = o.get('group_id')
                if gid:
                    groups.setdefault(str(gid), []).append(oid)
                singles.append(oid)
            # 1) payoff — cheap (batched LTP + CPU analyse): warm every cycle
            for gid, ids in groups.items():
                _warm_payoff({'group_id': gid})
                _t.sleep(0.3)
            for oid in singles:
                _warm_payoff({'ids': str(oid)})
                _t.sleep(0.2)
            # 2) legs-series — heavy (N Dhan candle calls): only multi-leg groups, ~every 80s
            if mkt and cycle % 4 == 1:
                for gid, ids in groups.items():
                    if len(ids) < 2:
                        continue
                    try:
                        kv = {'group_id': gid}
                        rows, _c = _payoff_resolve(kv)
                        if rows:
                            _legs_series_cache[_pf_key('ls', rows)] = (_legs_series_compute(kv), _t.time())
                    except Exception:
                        pass
                    _t.sleep(1.0)
            if not mkt:
                slept = 90
        except Exception:
            pass
        _t.sleep(slept)


def _warm_payoff(kv):
    import time as _t
    try:
        rows, closed = _payoff_resolve(kv)
        if rows:
            _payoff_cache[_pf_key('pf', rows)] = (_payoff_compute_rows(rows, closed), _t.time())
    except Exception:
        pass


def _payoff_warm_start():
    global _payoff_warm_started
    if not _payoff_warm_started:
        _payoff_warm_started = True
        _threading.Thread(target=_payoff_warm_loop, daemon=True).start()


@app.route('/api/position-payoff')
def api_position_payoff():
    """Payoff / zone analytics for one position GROUP (DISPLAY-ONLY — describes
    an existing position, places nothing, gates nothing). Query: ids=/group_id=.
    CACHED (_PAYOFF_TTL): switching between groups re-hits the same group instantly
    instead of re-attaching LTP + re-running analyse each time. Margin is a
    separate route (5 Kite calls — slow), so the panel renders instantly."""
    try:
        import time as _tm
        rows, closed = _payoff_resolve(request.args)
        if not rows:
            return jsonify({"ok": False, "msg": "no legs for this group (open or recently-closed)"})
        _ck = _pf_key('pf', rows, request.args.get('target_days') or '')
        _hit = _payoff_cache.get(_ck)
        if _hit and (_tm.time() - _hit[1]) < _PAYOFF_TTL:
            return jsonify(_hit[0])
        res = _payoff_compute_rows(rows, closed, request.args.get('target_days'))
        _payoff_cache[_ck] = (res, _tm.time())
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/position-payoff-margin')
def api_position_payoff_margin():
    """Real HEDGED margin (Kite basket_order_margins, read-only) vs the
    standalone per-leg sum the Margin column shows. Separate route because it
    costs ~5 rate-limited Kite calls."""
    try:
        import payoff
        rows, closed = _payoff_resolve(request.args)
        if not rows:
            return jsonify({"ok": False, "msg": "no legs for this group"})
        if not closed:
            rows = _payoff_attach_ltp(rows)
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


def _index_closes(idx_sec, from_date, to_date):
    """1-min closes {epoch: close} for an INDEX (IDX_I) over a date range — same
    shape as _leg_closes but the underlying, for 'spot at entry' lookups."""
    import requests as _req
    token, cid = _creds()
    _rl.acquire("candle")
    r = _req.post("https://api.dhan.co/v2/charts/intraday",
                  headers={"access-token": token, "client-id": cid,
                           "Content-Type": "application/json"},
                  json={"securityId": str(idx_sec), "exchangeSegment": "IDX_I",
                        "instrument": "INDEX", "expiryCode": 0,
                        "fromDate": from_date, "toDate": to_date}, timeout=15)
    d = r.json()
    if not d.get("close"):
        return {}
    return {int(ts): round(float(c), 2) for ts, c in zip(d["timestamp"], d["close"])}


def _group_underlying(rows):
    """NIFTY / BANKNIFTY from a group's legs (BANKNIFTY checked first — it contains
    'NIFTY')."""
    for r in rows:
        s = str(r.get("sym") or "").upper()
        if s.startswith("BANKNIFTY"):
            return "BANKNIFTY"
        if s.startswith("NIFTY"):
            return "NIFTY"
    return None


_ENTRY_SPOT_CACHE = {}       # "SYM|date|time" -> spot (entry spot never changes)


def _entry_spot_cached(rows, sym):
    """Underlying spot at the group's ENTRY minute (index 1-min candle nearest the
    entry timestamp). Cached per (sym, entry date, entry time) — one Dhan call ever."""
    try:
        idx_sec = {"NIFTY": "13", "BANKNIFTY": "25"}.get(str(sym).upper())
        if not idx_sec:
            return None
        eds = [r.get("entry_date") for r in rows if r.get("entry_date")]
        if not eds:
            return None
        ed = min(eds)
        et = min((r.get("entry_time") or "23:59") for r in rows if r.get("entry_date") == ed)
        key = f"{sym}|{ed}|{et}"
        if key in _ENTRY_SPOT_CACHE:
            return _ENTRY_SPOT_CACHE[key]
        from datetime import datetime as _d
        target = int((_d.strptime(f"{ed} {et}", "%Y-%m-%d %H:%M") - _d(1970, 1, 1)).total_seconds()) - 19800
        bars = _index_closes(idx_sec, ed, ed)
        if not bars:
            return None
        best = min(bars.keys(), key=lambda t: abs(t - target))
        val = bars[best]
        _ENTRY_SPOT_CACHE[key] = val
        return val
    except Exception:
        return None


@app.route('/api/position-greeks')
def api_position_greeks():
    """Net + per-leg Delta/Vega for a position GROUP + underlying spot move since
    entry (display-only). Query: ids=/group_id=. Light — no candle SERIES, just
    live LTP → per-leg IV → BS greeks + one cached index-candle for entry spot."""
    try:
        import payoff
        rows, closed = _payoff_resolve(request.args)
        if not rows:
            return jsonify({"ok": False, "msg": "no legs"})
        if not closed:
            rows = _payoff_attach_ltp(rows)
        legs = payoff.build_legs(rows)
        spot = _payoff_spot(legs)
        g = payoff.position_greeks(legs, spot)
        if g.get("ok"):
            sym = _group_underlying(rows)
            se = _entry_spot_cached(rows, sym) if sym else None
            if se and spot:
                g["spot_entry"] = round(se, 1)
                g["spot_move"] = round(spot - se, 1)
            g["symbol"] = sym
        return jsonify(g)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


_legs_series_cache = {}      # qs -> (payload, ts) — one full-day per-leg fetch is N
_LEGS_SERIES_TTL = 120       # rate-limited candle calls; rapid group-switching backlogs them.

@app.route('/api/position-legs-series')
def api_position_legs_series():
    """Per-leg premium series + COMBINED net-structure P&L for a position group
    (4-up grid + combined-premium chart). Spans entry-date -> today. Query:
    ids=/group_id=. CACHED + background-warmed (canonical key) → instant open;
    each cold call is N serial rate-limited Dhan candle fetches, so warming the
    open multi-leg groups is what removes the 'loading' wait."""
    try:
        import time as _tm
        rows, closed = _payoff_resolve(request.args)
        if not rows:
            return jsonify({"ok": False, "msg": "no legs for this group"})
        _ck = _pf_key('ls', rows)
        _hit = _legs_series_cache.get(_ck)
        if _hit and (_tm.time() - _hit[1]) < _LEGS_SERIES_TTL:
            return jsonify(_hit[0])
        _payload = _legs_series_compute(request.args)
        _legs_series_cache[_ck] = (_payload, _tm.time())
        return jsonify(_payload)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.route('/api/position-groups')
def api_position_groups():
    """Open + recently-closed option GROUPS (by group_id) for the payoff panel's
    group selector (#01). DISPLAY-ONLY. Each group carries a leg summary + the ids
    the payoff routes resolve, so a closed hedge/pair is reachable + its legs stay
    grouped (kaun sa leg kis pair ka tha) instead of showing as loose flat rows."""
    try:
        import order_store
        from datetime import datetime as _d, timedelta as _td, timezone as _tz
        ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
        lb = (ist - _td(days=7)).strftime('%Y-%m-%d')
        data = order_store.trades_for_range(lb, ist.strftime('%Y-%m-%d'))

        def _opt(sym):
            p = str(sym or '').split('-')
            if len(p) < 3:
                return None
            opt = (p[-1] or '').upper()
            try:
                strike = float(p[-2])
            except Exception:
                return None
            if opt not in ('CE', 'PE'):
                return None
            return p[0].upper(), strike, opt

        G = {}

        def _g(gid, underlying):
            return G.setdefault(gid, {"group_id": gid, "status": "closed",
                                      "underlying": underlying, "legs": [], "ids": [], "recent": ""})
        for r in data.get('open', []):
            if 'CAPITAL_BLOCKED' in (r.get('tags') or []):
                continue
            gid = r.get('group_id') or ''
            info = _opt(r.get('sym'))
            if not gid or not info:
                continue
            g = _g(gid, info[0]); g["status"] = "open"
            g["legs"].append({"side": r.get('entry'), "opt": info[2], "strike": info[1],
                              "entry": r.get('entry_price'), "flat": False})
            g["ids"].append(str(r.get('id')))
            g["recent"] = max(g["recent"], r.get('entry_date') or '')
        for dtl in data.get('details', []):
            gid = dtl.get('group_id') or ''
            info = _opt(dtl.get('sym'))
            if not gid or not info:
                continue
            g = _g(gid, info[0])
            g["legs"].append({"side": dtl.get('entry'), "opt": info[2], "strike": info[1],
                              "entry": dtl.get('entry_price'), "flat": True})
            g["ids"].append(str(dtl.get('id')))
            g["recent"] = max(g["recent"], dtl.get('exit_date') or dtl.get('entry_date') or '')
        out = []
        for gid, g in G.items():
            if len([l for l in g["legs"] if l["strike"]]) < 2:
                continue      # need a real multi-leg structure
            sells = sum(1 for l in g["legs"] if l["side"] == "SELL")
            buys = len(g["legs"]) - sells
            g["label"] = (f"{g['underlying']} · {len(g['legs'])} legs"
                          + (" · hedged" if buys and sells else "")
                          + (" · OPEN" if g["status"] == "open" else " · closed"))
            out.append(g)
        out.sort(key=lambda x: x.get("recent", ""), reverse=True)     # newest first…
        out.sort(key=lambda x: 0 if x["status"] == "open" else 1)      # …but open groups on top (stable)
        return jsonify({"ok": True, "groups": out})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "groups": []})


def _exit_rule_identity(args):
    """Resolve a payoff request (ids= / group_id=) to (key, group_id, ids, mode,
    rows, closed) so POST-arm and DELETE-clear derive the SAME rule key. mode =
    'live' if ANY leg is live (a live leg means a live square-off), else paper."""
    import position_exit_rules as per
    rows, closed = _payoff_resolve(args)
    gid, ids, modes = "", [], set()
    for r in rows:
        ids.append(str(r.get('id')))
        if r.get('group_id'):
            gid = r.get('group_id')
        modes.add(str(r.get('mode') or 'paper').lower())
    mode = "live" if "live" in modes else "paper"
    return per.rule_key(gid, ids), gid, ids, mode, rows, closed


def _tm_num(v):
    """Blank/None/garbage → None, else float. Keeps '' out of the rule store."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f else None


def _tm_dir_from_legs(rows):
    """Which way this position wants the index to go: +1 up, -1 down.

    Derived from leg structure alone — SELL PE / BUY CE profit when the index
    rises, SELL CE / BUY PE when it falls — weighted by qty. No pricing call, so
    arming stays instant. A balanced straddle nets 0 → +1, under which "target"
    simply means the UPPER level and "sl" the LOWER one; for a non-directional
    structure that is exactly the useful reading (breach either side → get out)."""
    net = 0.0
    for r in rows or []:
        ts = str(r.get("trad_sym") or r.get("sym") or "").upper()
        opt = "CE" if ts.endswith("CE") else ("PE" if ts.endswith("PE") else "")
        if not opt:
            continue
        side = str(r.get("entry") or "").upper()
        qty = abs(float(r.get("qty") or 0))
        bull = (opt == "PE") if side == "SELL" else (opt == "CE")
        net += qty if bull else -qty
    return 1 if net >= 0 else -1


@app.route('/api/position-exit-rule', methods=['POST'])
def api_position_exit_rule_set():
    """Arm a combined-MTM auto-exit rule for a position GROUP (#02). When the
    group's live combined MTM crosses target_rs (>=) or sl_rs (<=), the whole
    group squares off — in the group's OWN mode (paper→paper, live→REAL order).
    Body: {qs:'ids=..'|'group_id=..', target_rs, sl_rs}."""
    try:
        import position_exit_rules as per
        from urllib.parse import parse_qs
        d = request.get_json(force=True) or {}
        pq = parse_qs(str(d.get("qs") or ""))
        args = {"ids": pq.get("ids", [""])[0], "group_id": pq.get("group_id", [""])[0]}
        key, gid, ids, mode, rows, closed = _exit_rule_identity(args)
        if closed or not rows:
            return jsonify({"ok": False, "msg": "group open nahi hai — auto-exit sirf live/open group pe lag sakta"})
        target_rs = float(d.get("target_rs") or 0)
        sl_rs = float(d.get("sl_rs") or 0)
        # Trade Manager index triggers (optional). Omit them all and this route
        # behaves exactly as before.
        idx = {k: d.get(k) for k in ("idx_pt_tg", "idx_pt_sl", "idx_px_tg", "idx_px_sl")}
        has_idx = any(_tm_num(v) for v in idx.values())
        if target_rs <= 0 and sl_rs >= 0 and not has_idx:
            return jsonify({"ok": False, "msg": "Target (>0) ya SL (<0) me se kam se kam ek set karo"})
        extra = {}
        if has_idx:
            en = d.get("enabled") if isinstance(d.get("enabled"), dict) else {}
            extra = {
                "enabled": {"rs": bool(en.get("rs", True)),
                            "ip": bool(en.get("ip", True)),
                            "il": bool(en.get("il", True))},
                "tf": str(d.get("tf") or "5m"),
                "confirm_mode": str(d.get("confirm_mode") or "close"),
                "confirm_min": _tm_num(d.get("confirm_min")) or 2,
                "dir": _tm_dir_from_legs(rows),
            }
            for k, v in idx.items():
                extra[k] = _tm_num(v)
            # entry_spot is captured HERE, server-side, from the live cache — never
            # taken from the client (a stale/edited browser value would silently
            # place every index-point trigger at the wrong level).
            if extra.get("idx_pt_tg") or extra.get("idx_pt_sl"):
                sym = str((rows[0].get("symbol") or "")
                          or str(rows[0].get("trad_sym") or rows[0].get("sym") or "").split("-")[0]).upper()
                spot = None
                if sym in _TM_IDX_SEC:
                    try:
                        import shared_ltp_cache as _slc
                        spot = _slc.get_index(sym, max_age=60.0)
                    except Exception:
                        spot = None
                if not spot or float(spot) <= 0:
                    # refuse loudly instead of arming a trigger that can never fire
                    return jsonify({"ok": False, "msg":
                                    f"{sym or 'is position'} ka live spot nahi mil raha — "
                                    "index-points trigger arm nahi kar sakte. Index PRICE "
                                    "(absolute level) use karo, ya spot aane par dobara try karo."})
                extra["entry_spot"] = float(spot)
        rule = per.set_rule(key, gid, ids, target_rs, sl_rs, mode, **extra)
        return jsonify({"ok": True, "rule": rule, "mode": mode,
                        "levels": per.trigger_levels(rule)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/position-carry', methods=['GET'])
def api_position_carry_get():
    """Currently carried-overnight (NRML) position keys — so Open Positions can show
    each row's MIS/NRML state. Returns {ok, keys:[group_id|id:<id>, ...]}. Day-scoped
    (agle din khaali = sab wapas MIS)."""
    try:
        import position_carry
        return jsonify({"ok": True, "keys": position_carry.list_keys()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/position-carry', methods=['POST'])
def api_position_carry_set():
    """Toggle a position MIS <-> NRML. Body: {group_id, id, on}. on=True → NRML
    (carry past 3:15, GROUP-WIDE via group_id), False → MIS (3:15 auto-square,
    default). Display/EOD-behaviour only — koi order abhi place/convert nahi hota
    (PAPER; live NRML/convert-position baad me). group_id ho to poora group carry
    hota (straddle ke dono leg), warna us akele leg (id) pe."""
    try:
        import position_carry
        d = request.get_json(force=True) or {}
        gid = str(d.get("group_id") or "").strip()
        pid = str(d.get("id") or "").strip()
        on = bool(d.get("on"))
        if not gid and not pid:
            return jsonify({"ok": False, "msg": "group_id ya id chahiye"})
        key, state = position_carry.set_carry(gid, pid, on)
        if not key:
            return jsonify({"ok": False, "msg": "carry key resolve nahi hua"})
        return jsonify({"ok": True, "on": state, "key": key,
                        "msg": ("NRML — carry overnight (3:15 square skip)" if state
                                else "MIS — 3:15 auto square-off")})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/position-exit-rule', methods=['GET'])
def api_position_exit_rule_get():
    """Currently-armed combined-MTM auto-exit rule for a group — so the payoff
    panel shows the SAVED target/SL on reopen (not a fresh default). Query:
    ids= / group_id=. Returns {ok, rule} where rule is null if nothing armed."""
    try:
        import position_exit_rules as per
        key, gid, ids, mode, rows, closed = _exit_rule_identity(request.args)
        return jsonify({"ok": True, "rule": per.get_rule(key)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/position-exit-rule', methods=['DELETE'])
def api_position_exit_rule_clear():
    """Clear a group's armed auto-exit rule. Query: ids=… or group_id=…."""
    try:
        import position_exit_rules as per
        key, gid, ids, mode, rows, closed = _exit_rule_identity(request.args)
        # even if the group is gone, still try clearing by the resolved key
        found = per.clear_rule(key)
        return jsonify({"ok": True, "cleared": found})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


_TM_IDX_SEC = {"NIFTY": "13", "BANKNIFTY": "25"}


def _tm_index_reason(rule, legs, per, slc, log=print):
    """Trade Manager: evaluate a rule's INDEX-based triggers (index points /
    absolute index level) → (reason, src, level) or (None, None, None).

    Runs only for rules that actually carry them, so a plain ₹-MTM rule never
    touches any of this. Everything here freezes rather than fires when data is
    missing: no underlying spot, unknown index, or an unresolvable candle all
    return None. Confirmation (wick / candle-close / close+N-min) is applied per
    the rule's own setting; its progress is persisted so a monitor restart mid-
    confirmation resumes instead of silently restarting the window.

    NOTE: confirmation applies to the INDEX triggers only. The ₹-combined-MTM
    trigger keeps firing immediately, exactly as it does today — unchanged."""
    trigs = per.trigger_levels(rule)
    if not trigs:
        return None, None, None
    sym = str((legs[0].get("symbol") or "")
              or str(legs[0].get("sym") or "").split("-")[0]).upper()
    idx_sec = _TM_IDX_SEC.get(sym)
    if not idx_sec:
        return None, None, None                     # unknown underlying → freeze
    spot = slc.get_index(sym, max_age=20.0)
    if not spot or float(spot) <= 0:
        return None, None, None                     # no spot → freeze (TRAP #1 shape)

    dir_ = 1 if float(rule.get("dir") or 1) >= 0 else -1
    mode = str(rule.get("confirm_mode") or "close")
    tf_min = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}.get(str(rule.get("tf") or "5m"), 5)
    conf = dict(rule.get("conf") or {})
    changed = False
    hit = None

    # nearest-first: whichever armed level price is closest to decides first
    for t in sorted(trigs, key=lambda x: abs(float(x["level"]) - float(spot))):
        slot = "%s_%s" % (t["src"], t["side"])
        beyond = per.is_beyond(spot, t["level"], t["side"], dir_)
        on_close = None
        if mode != "wick" and beyond:
            cc = _last_closed_candle_close(idx_sec, "IDX_I", tf_min)
            # None stays None on purpose: "candle not known" must NOT read as
            # "closed back inside", or an un-fetchable candle would silently
            # cancel a real breach.
            if cc is not None:
                on_close = per.is_beyond(cc, t["level"], t["side"], dir_)
        st, fire = per.advance_confirm(conf.get(slot), beyond, on_close,
                                       _time.time(), mode,
                                       rule.get("confirm_min") or 2)
        if st != conf.get(slot):
            conf[slot] = st
            changed = True
        if fire and hit is None:
            hit = (t["side"], t["src"], float(t["level"]))
    if changed:
        try:
            per.update_conf(rule.get("key"), conf)
        except Exception as e:
            log(f"[exit-rule] conf persist fail ({rule.get('key')}): {e}")
    return hit if hit else (None, None, None)


def _run_position_exit_rules(log=print):
    """Evaluate each armed combined-MTM GROUP rule against LIVE MTM and square
    off the whole group on target/SL (#02). Runs inside auto_straddle_loop
    (monitor_daemon, ~3s) so it shares the ltp_poller-warmed cache. Safety:
      • legs re-resolved FRESH from order_store each cycle (no stale snapshot);
      • FREEZE — never fire — if ANY leg's LTP is missing/stale (TRAP #1 shape);
      • square-off reuses execution_gateway.execute_exit (its own fresh flat-
        check per leg), recorded under each leg's OWN strategy/source/mode —
        so a paper group exits paper, a live group fires a REAL exit;
      • any error → skip (never fire on an exception);
      • a rule whose group is already flat auto-clears."""
    import position_exit_rules as per
    rules = per.list_rules()
    if not rules:
        return
    import execution_gateway as gw
    try:
        import shared_ltp_cache as slc
        import ltp_poller
        import order_store
    except Exception:
        return
    from datetime import datetime as _d, timedelta as _td, timezone as _tz
    ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
    lb = (ist - _td(days=7)).strftime('%Y-%m-%d')
    open_rows = [r for r in order_store.trades_for_range(lb, ist.strftime('%Y-%m-%d')).get('open', [])
                 if 'CAPITAL_BLOCKED' not in (r.get('tags') or [])]
    for rule in rules:
        try:
            key = rule.get("key")
            gid = rule.get("group_id") or ""
            want = set(rule.get("ids") or [])
            if gid:
                # Group-scoped resolution (2026-08-21 naked-leg fix): resolve THIS
                # group's open legs from its OWN ledger, NOT by filtering the global
                # multi-day netting — that cross-nets a re-traded monthly contract
                # across days and DROPS legs (a 4-leg basket resolved as 2 → basket-SL
                # closed 2 → left a naked short). group_id IS the placement identity.
                legs = order_store.open_legs_in_group(gid)
            else:
                legs = [r for r in open_rows if str(r.get('id')) in want]
            if not legs:
                per.clear_rule(key)          # group flat → rule fulfilled/stale
                continue
            # ── ZOMBIE-RULE GUARD (2026-08-28, preemptive defense) ───────────────
            # An intraday group's rule that outlived its own EOD square-off (the
            # root bug this session fixed) must NEVER fire on a LATER trading day —
            # doing so churns a position that no longer exists at the broker (today's
            # incident: a prior-day hedged straddle's ±4k rule re-fired, re-opening
            # closed legs, one rejected → orphan). If EVERY resolved leg's entry is
            # from before today AND the owning strategy is not allow_overnight, the
            # group is a zombie → clear the rule, never fire. Overnight strategies
            # (weekly_ironfly / vrp_* etc.) legitimately hold across days and are
            # exempt — their rules keep monitoring as intended.
            try:
                import risk_gate as _rg_zg
                _today = ist.strftime('%Y-%m-%d')
                _all_prior = all((str(l.get('entry_date') or _today) < _today) for l in legs)
                if _all_prior:
                    _strat0 = str(legs[0].get('strategy') or '')
                    if not _rg_zg.allow_overnight(_strat0):
                        log(f"[exit-rule] {key} STALE — all legs from a prior day & "
                            f"'{_strat0}' is intraday (not overnight); clearing zombie "
                            f"rule without firing (would churn a closed position)")
                        per.clear_rule(key)
                        continue
            except Exception as _zge:
                log(f"[exit-rule] zombie-guard check skipped ({_zge})")
            # Same stale-LTP guard as the straddle auto-exit: a 20s warm-up grace
            # after the rule was ARMED + only act on a tick timestamped AFTER arm
            # (get_after) — a pre-arm/stale cache value can't drive a phantom
            # target/SL square-off. (The group is usually warm, but be safe.)
            armed_ts = rule.get("created_ts", 0)
            if (_time.time() - armed_ts) < 20:
                continue
            try:
                ltp_poller.request_watch([(str(r.get('sec_id')), r.get('segment') or 'NSE_FNO')
                                          for r in legs if r.get('sec_id')])
            except Exception:
                pass
            combined, ok_data = 0.0, True
            for r in legs:
                sid = str(r.get('sec_id') or '')
                ltp = slc.get_after(sid, armed_ts, max_age=20.0) if sid else None
                if not ltp or float(ltp) <= 0:
                    ok_data = False
                    break
                qty = abs(float(r.get('qty') or 0))
                entry = float(r.get('entry_price') or 0)
                side = str(r.get('entry') or '').upper()
                combined += ((entry - float(ltp)) if side == 'SELL' else (float(ltp) - entry)) * qty
            if not ok_data:
                continue                     # FREEZE — incomplete data, never fire
            reason = per.check_exit(combined, rule.get("target_rs"), rule.get("sl_rs"))
            _why = f"combined MTM ₹{combined:,.0f}"
            if reason not in ("target", "sl"):
                # Trade Manager index triggers (index points / index level). OR
                # logic: the ₹ rule above already had its say; this is simply the
                # next armed source. No-op for any rule without them.
                try:
                    reason, _src, _lvl = _tm_index_reason(rule, legs, per, slc, log)
                    if reason:
                        _why = f"{'index level' if _src == 'il' else 'index points'} {_lvl:,.0f}"
                except Exception as _te:
                    log(f"[exit-rule] index-trigger err ({key}): {_te}")
                    reason = None
            if reason not in ("target", "sl"):
                continue
            log(f"[exit-rule] {key} {reason.upper()} @ {_why} — "
                f"squaring off {len(legs)} legs ({rule.get('mode')})")
            # ORDERED square-off: shorts (SELL) buy-to-close FIRST, then wings
            # (BUY) sell-to-close — stripping the hedge first spikes margin and
            # the broker rejects the remaining exits (user-reported). Shared
            # helper so the trader files and this monitor can't drift.
            norm = [{
                "strategy": r.get('strategy') or 'manual',
                "symbol": r.get('symbol') or str(r.get('sym') or '').split('-')[0],
                "sec_id": str(r.get('sec_id')),
                "trad_sym": r.get('sym'),
                "qty": abs(int(float(r.get('qty') or 0))),
                "entry_side": str(r.get('entry') or 'SELL').upper(),
                "mode": str(r.get('mode') or 'paper').lower(),
                "group_id": gid,
                "source": r.get('source') or 'manual',
                "seg": r.get('segment') or 'NSE_FNO',
            } for r in legs]
            try:
                gw.execute_basket_exit(norm, reason="GROUP_%s" % reason.upper(), log=log)
            except Exception as e:
                log(f"[exit-rule] basket exit FAIL {key}: {e}")
            per.clear_rule(key)
            try:
                import notify
                notify.push(f"{'🎯' if reason == 'target' else '🛑'} Group auto-exit "
                            f"{reason.upper()} — {_why} ({len(legs)} legs, {rule.get('mode')})",
                            level="info" if reason == "target" else "warn",
                            key="grpexit_%s" % key, source="chain")
            except Exception:
                pass
        except Exception as e:
            log(f"[exit-rule] rule err ({rule.get('key')}): {e}")


def _sweep_zombie_state(log=print):
    """DAILY zombie guard (2026-08-28) — runs once at pos_monitor startup and on
    every trading-day rollover. Guarantees no intraday leg/rule silently outlives
    its own day:
      • CLEAR any position_exit_rule whose group is FLAT (ledger net-zero) or whose
        legs are ALL from a prior day for a non-allow_overnight (intraday) strategy —
        a zombie left by an EOD square-off that didn't clear it (today's incident).
      • ALERT (bell + log) on any intraday-strategy position still OPEN from a prior
        day (a carried-over leg the EOD 3:15 square-off should have closed). Today's
        3:15 EOD still closes it; this surfaces it immediately so nothing rots silent.
    NEVER fires an order — firing on stale state is exactly what caused the churn.
    Returns (rules_cleared, stale_positions_flagged)."""
    try:
        import position_exit_rules as per
        import order_store
    except Exception as e:
        log(f"[zombie-sweep] import fail: {e}")
        return 0, 0
    from datetime import datetime as _d, timedelta as _td, timezone as _tz
    ist = _d.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)
    today = ist.strftime('%Y-%m-%d')
    try:
        import risk_gate as _rg
    except Exception:
        _rg = None

    cleared = 0
    for rule in per.list_rules():
        try:
            key = rule.get("key")
            gid = rule.get("group_id") or ""
            legs = order_store.open_legs_in_group(gid) if gid else []
            if not legs:
                per.clear_rule(key); cleared += 1
                log(f"[zombie-sweep] cleared FLAT rule {key}")
                continue
            all_prior = all((str(l.get('entry_date') or today) < today) for l in legs)
            strat0 = str(legs[0].get('strategy') or '')
            overnight = bool(_rg and _rg.allow_overnight(strat0))
            if all_prior and not overnight:
                per.clear_rule(key); cleared += 1
                log(f"[zombie-sweep] cleared PRIOR-DAY intraday rule {key} ({strat0})")
        except Exception as e:
            log(f"[zombie-sweep] rule check err: {e}")

    stale = []
    try:
        lb = (ist - _td(days=7)).strftime('%Y-%m-%d')
        for p in order_store.trades_for_range(lb, today).get('open', []):
            if 'CAPITAL_BLOCKED' in (p.get('tags') or []):
                continue
            # LIVE (real money) only — paper strategies leave lots of stale ledger
            # junk that isn't a real broker position; flagging those would be pure
            # noise. A real-money intraday leg carried past its day is the concern.
            if str(p.get('mode') or '').lower() != 'live':
                continue
            ed = str(p.get('entry_date') or today)
            strat = str(p.get('strategy') or '')
            if ed < today and not (_rg and _rg.allow_overnight(strat)):
                stale.append(p)
    except Exception as e:
        log(f"[zombie-sweep] open-scan err: {e}")
    if stale:
        msg = (f"⚠️ {len(stale)} intraday leg(s) from a prior day STILL OPEN "
               f"(EOD squareoff should have closed them) — "
               + ", ".join(f"{p.get('strategy')}·{p.get('sym')}" for p in stale[:6]))
        log("[zombie-sweep] " + msg)
        try:
            import notify
            notify.warn(msg, key="zombie_stale_positions", source="zombie-sweep")
        except Exception:
            pass
    log(f"[zombie-sweep] {today}: {cleared} stale rule(s) cleared, "
        f"{len(stale)} prior-day intraday leg(s) flagged")
    return cleared, len(stale)


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
                _fq = dhan_feed.get_quote(str(mapped_sid), max_age=dhan_feed.FEED_MAX_AGE)  # own LIVE → shared store (ADR-013)
                if _fq:
                    feed_ltp = float(_fq.get("ltp") or 0)
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

        # P1 refined (2026-07-30 user rule): manual orders alag-alag BY DEFAULT, PAR
        # SAME symbol + SAME minute pe pade orders EK hi group me (e.g. ek saath daali
        # 2-leg strangle → ek panel, ek close-all). group_id = symbol + IST-minute
        # bucket → deterministic: same minute ke legs apne-aap same group (koi lookup/
        # race nahi), alag minute = alag group. (Pehle per-order uuid tha → same-time
        # legs bhi alag dikhte the.)
        from datetime import timedelta as _tdm
        _mmin = (datetime.now(timezone.utc) + _tdm(hours=5, minutes=30)).strftime('%Y%m%d%H%M')
        mgid = 'MANUAL_' + symbol + '_' + _mmin

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
                    instrument='options', broker_name='kite', group_id=mgid)
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
        option_ltp = 0  # NEVER fall back to the index/spot price — recording an
                        # option fill at the NIFTY level (e.g. 24236) makes a phantom
                        # ₹-lakh P&L (TRAP #1). If premium can't be fetched → skip below.
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

        # TRAP #1 guard: never record an option fill at ₹0 or at the index/spot level.
        # If the option premium couldn't be fetched (rate-limit / market closed) and no
        # valid LIMIT price was given, SKIP the order — recording at the NIFTY level
        # (24236) creates a phantom ₹-lakh completed trade that corrupts the day's P&L.
        if (not option_ltp) or float(option_ltp) <= 0:
            return jsonify({'ok': False, 'msg': f'{t_sym} ka live premium nahi mila (rate-limit?) — '
                            f'order NAHI bheja (index price pe record karke phantom P&L nahi banate). '
                            f'Dobara try karo, ya LIMIT price daalo.'})

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
                    product_type='NRML', group_id=mgid)  # options always NRML — CNC only applies to EQUITY; mgid = per-order bucket (P1)
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


# ══════════════════════════════════════════════════════════════════════════════
# AUTO ATM STRADDLE (short) — 3 entry sources, COMBINED-premium 30/30 basket exit.
#   A) 9:20 scheduled   B) Quick Order (manual)   C) on an option-alert
# ALL PAPER (mode hard-locked here). Reuses execution_gateway (RMS + order_store),
# dhan_master ATM resolve, _trigger_spot_now for spot, ltp_poller/shared_ltp_cache
# for the live combined premium, _pre_exit machinery via execute_exit. State +
# pure basket-exit decision live in _ops/auto_straddle.py (standalone-tested).
# ══════════════════════════════════════════════════════════════════════════════
def _ast_ist_now():
    from datetime import datetime as _d, timezone as _tz, timedelta as _td
    return _d.now(_tz.utc) + _td(hours=5, minutes=30)


def _parse_hm_pair(s, default=(9, 20)):
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return default


def _auto_straddle_cfg():
    """nifty_config['_auto_straddle'] with defaults. mode is HARD-LOCKED to paper
    (going live needs an explicit code change + user confirmation)."""
    cfg = {
        "enabled_920": False, "enabled_alert": False,
        "symbols_920": ["NIFTY", "BANKNIFTY"],
        "lots": 1, "tp_pt": 30.0, "sl_pt": 30.0,
        "entry_920": "09:20", "entry_920_window_min": 6,
        "alert_triggers": ["straddle_pop", "straddle_crush", "gamma_spike"],
        "max_per_day": 2,
        # HEDGE: buy cheap OTM wings (~max_premium ₹) → hedged iron fly, low margin
        "hedge": {"enabled": True, "max_premium": 2.0, "min_strikes": 3},
    }
    try:
        raw = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        cfg.update(raw.get("_auto_straddle") or {})
    except Exception:
        pass
    hd = cfg.get("hedge") or {}
    cfg["hedge"] = {"enabled": bool(hd.get("enabled", True)),
                    "min_strikes": int(hd.get("min_strikes", 3))}   # max_premium is PER-INDEX (per_symbol)
    # per-index target/SL + hedge max-premium — BANKNIFTY premium moves more per point (wider
    # 30/30→60/60) AND a ₹2 wing sits ~1500pt OTM (too wide) → default ₹5 for a tighter fly.
    ps = cfg.get("per_symbol") or {}
    merged = {"NIFTY": {"tp_pt": 30.0, "sl_pt": 30.0, "hedge_max_premium": 2.0},
              "BANKNIFTY": {"tp_pt": 60.0, "sl_pt": 60.0, "hedge_max_premium": 5.0}}
    for k in merged:
        if isinstance(ps.get(k), dict):
            merged[k].update({kk: ps[k][kk] for kk in ("tp_pt", "sl_pt", "hedge_max_premium") if kk in ps[k]})
    cfg["per_symbol"] = merged
    cfg["mode"] = "paper"   # hard paper-lock
    return cfg


def _straddle_tp_sl(cfg, symbol):
    """Per-index target/SL points (per_symbol override, else global tp_pt/sl_pt)."""
    ps = (cfg.get("per_symbol") or {}).get(str(symbol).upper()) or {}
    return float(ps.get("tp_pt", cfg.get("tp_pt", 30))), float(ps.get("sl_pt", cfg.get("sl_pt", 30)))


def _straddle_hedge_max(cfg, symbol):
    """Per-index hedge wing max-premium ₹ (NIFTY ~2, BANKNIFTY ~5 — BNF ₹2 is too far OTM)."""
    ps = (cfg.get("per_symbol") or {}).get(str(symbol).upper()) or {}
    return float(ps.get("hedge_max_premium", 2.0))


def _straddle_strategy_id(source):
    """A/B/C get DISTINCT strategy ids so Orders/Stats/RMS distinguish them
    (registered 02.06/07/08). Same short-straddle execution, different entry idea."""
    s = str(source or "")
    if s.startswith("schedule"):
        return "straddle_920"     # A — 9:20 auto
    if s.startswith("alert"):
        return "straddle_alert"   # C — option-alert fired
    return "straddle_manual"      # B — Quick Order manual


def _prewarm_option_ltps(sec_ids, seg="NSE_FNO", log=print, acq_timeout=None):
    """One batched Dhan /v2/marketfeed/ltp call for a list of option sec_ids →
    warms shared_ltp_cache so a subsequent per-strike walk (compute_hedge_target)
    hits the cache (max_age) instead of doing N serial, rate-limited quote_fn
    REST calls. This is the fix for the 2026-07-24 auto-straddle 1:30+ min
    "slow fire": hedge-first resolved BOTH wings by walking up to max_search OTM
    strikes, fetching EACH strike's premium one-by-one (~1/sec) → 30-60 serial
    fetches before a single order went out. Best-effort — any failure just
    leaves the cache cold and the walk falls back to its own per-strike REST
    (correctness unchanged, only speed). Returns count warmed."""
    ids = []
    for s in sec_ids:
        try:
            ids.append(int(s))
        except (TypeError, ValueError):
            continue
    ids = sorted(set(ids))
    if not ids:
        return 0
    try:
        import requests as _req
        import dhan_rate_limiter as _drl
        import shared_ltp_cache as _slc
        token, cid = _creds()
        headers = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        _drl.set_context("Straddle:HedgePrewarm")
        # acquire BLOCKS up to its timeout; during a Dhan 429 cooldown the non-order
        # cap is 0 so it blocks the FULL timeout then fails. Callers that must fill
        # before acting (fire path) use the default 8s; the user-facing preview
        # passes a SHORT timeout (best-effort) and relies on ltp_poller for the rest
        # so it never blocks the request. (A retry-loop of blocking acquires = a
        # multi-minute hang — never do that.)
        if not _drl.acquire("ltp", timeout=float(acq_timeout or 8.0)):
            return 0   # gate saturated / 429 cooldown — walk will REST-fallback per strike
        r = _req.post("https://api.dhan.co/v2/marketfeed/ltp",
                      json={seg: ids}, headers=headers, timeout=6)
        if r.status_code == 429:
            _drl.note_429()
            return 0
        if r.status_code != 200:
            return 0
        node = (r.json().get("data", {}) or {}).get(seg, {}) or {}
        out = {}
        for sid_str, q in node.items():
            try:
                ltp = float(q.get("last_price") or q.get("ltp") or 0)
            except (TypeError, AttributeError):
                continue
            if ltp > 0:
                out[str(sid_str)] = ltp
        if out:
            _slc.put_many(out)
        return len(out)
    except Exception as e:
        log(f"[STRADDLE] hedge LTP prewarm fail (walk will REST-fallback): {e}")
        return 0


def _straddle_leg_open(strategy_id, sec_id, trad_sym):
    """order_store openness for one straddle leg, for auto_straddle.reconcile_open's
    stale-record self-heal. 0 = confident-flat (closed round-trip recorded), >0 =
    still open, None = uncertain — exactly broker_sync._my_open_qty's contract."""
    try:
        import broker_sync
        return broker_sync._my_open_qty(strategy_id, sec_id, trad_sym)
    except Exception:
        return None


def _strike_of(tsym):
    """Best-effort strike int from a Dhan trad_sym (e.g. NIFTY-Jul2026-24050-CE)."""
    try:
        return int(float(str(tsym).split("-")[-2]))
    except Exception:
        return None


def _norm_expiry(v):
    """Straddle expiry choice → one of: 'near' | 'nextmonth' | a specific date
    'YYYY-MM-DD'. Anything unrecognised → 'near' (safe default). Used by the
    fire/preview routes so a user-picked specific expiry (weekly/monthly, to match
    Sensibull etc.) passes through instead of being squeezed to near/nextmonth."""
    s = str(v or "").strip()
    if s == "nextmonth":
        return "nextmonth"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    return "near"


def _straddle_resolver(expiry):
    """Contract resolver by expiry choice, all with the same (symbol, spot,
    opt_type, offset) signature + PE-inverted offset:
      'nextmonth'   → next-month monthly (get_next_monthly_option_contract)
      'YYYY-MM-DD'  → that specific listed expiry (get_option_contract_for_expiry)
      else          → nearest listed (weekly/current, get_option_contract)."""
    import dhan_master
    e = str(expiry or "near")
    if e == "nextmonth":
        return dhan_master.get_next_monthly_option_contract
    if re.match(r"^\d{4}-\d{2}-\d{2}$", e):
        return lambda sym, spot, ot, off: dhan_master.get_option_contract_for_expiry(sym, spot, ot, off, e)
    return dhan_master.get_option_contract


def _resolve_straddle_legs(symbol, spot, legs_spec, expiry="near", signed=False):
    """Resolve a UI leg spec → concrete contracts. Each spec leg:
        {side: SELL|BUY, opt_type: CE|PE, offset: int}
    Default (signed=False, the auto/straddle path): `offset` is a non-negative OTM
    magnitude — dhan_master.get_option_contract handles direction per opt_type
    (CE up, PE down); we NEVER pass a negative PE offset (TRAP #140 /
    architecture_audit PE-OFFSET-SIGN, magnitude only).
    signed=True (the Quick Order CHAIN path): `offset` is the SIGNED index offset
    from ATM as the frontend computed it per opt_type (CE leg at strike K →
    (K-atm)/step; PE leg at K → (atm-K)/step). get_option_contract's own index math
    (CE atm_idx+off, PE atm_idx-off) then lands on the exact strike K — reaching ITM
    AND OTM for both types. Offset is a runtime-computed variable, never a literal
    negative, so the PE-OFFSET-SIGN static guard doesn't trip; correctness is the
    strike-lands-on-K property, verified in tests.
    `expiry`='near' (weekly) or 'nextmonth' (next monthly). Returns (legs, err),
    legs = [{opt_type, side, offset, sec_id, trad_sym, lot}]  err=None on success."""
    _resolve = _straddle_resolver(expiry)
    out, lot0 = [], None
    for sp in (legs_spec or []):
        side = str(sp.get("side", "")).upper()
        ot = str(sp.get("opt_type", "")).upper()
        off = int(sp.get("offset", 0)) if signed else abs(int(sp.get("offset", 0)))  # pe-offset-ok: chain=signed index (lands on exact strike), auto=OTM magnitude
        if side not in ("SELL", "BUY") or ot not in ("CE", "PE"):
            return None, f"bad leg {side}/{ot}"
        sec, tsym, lot = _resolve(symbol, spot, ot, off)
        if not sec:
            return None, f"{ot} {'+' if ot == 'CE' else '-'}{off} contract resolve fail"
        if lot:
            lot0 = int(lot)
        out.append({"opt_type": ot, "side": side, "offset": off,
                    "sec_id": str(sec), "trad_sym": tsym, "lot": int(lot or 0)})
    if not out:
        return None, "koi leg select nahi hua"
    if not lot0:
        return None, "lot size resolve nahi hua"
    for lg in out:                     # backfill any leg whose own lot came back 0
        lg["lot"] = lg["lot"] or lot0
    return out, None


def _straddle_lltp(sec):
    """Best-effort live premium for one option (shared cache → REST). None on fail."""
    try:
        import shared_ltp_cache as _s
        v = _s.get(str(sec), max_age=20)
        if v:
            return float(v)
    except Exception:
        pass
    try:
        return float(_rest_ltp_fallback(sec, "NSE_FNO") or 0) or None
    except Exception:
        return None


def _straddle_preview(symbol, lots, spec, quick=False, expiry="near", signed=False):
    """Read-only preview for the leg-window / Quick Order chain: per-leg strike/LTP +
    net credit + real hedged basket margin. NO order placed. Returns a dict for jsonify.
    quick=True skips the slow Kite basket-margin call (net+LTP stay snappy on
    every +/- ; the UI trails a full preview once the user stops adjusting).
    expiry='near' (weekly) or 'nextmonth' (next monthly).
    signed=True → leg offsets are signed index offsets (chain path, ITM+OTM)."""
    import risk_gate as rg
    symbol = str(symbol).upper()
    lots = max(1, int(lots or 1))
    if symbol not in ("NIFTY", "BANKNIFTY"):
        return {"ok": False, "msg": f"{symbol} supported nahi"}
    spot = _trigger_spot_now(symbol)
    if not spot or spot <= 0:
        # PREVIEW is display-only → off-market use last-known spot (wide stale) so
        # strikes resolve + last-close LTPs still show. FIRING stays strict: it uses
        # _trigger_spot_now (fresh only) and blocks off-market — this fallback is
        # never on the order path.
        try:
            import shared_ltp_cache as _slc
            spot = _slc.get_index(symbol, max_age=86400) or None
        except Exception:
            spot = None
    if not spot or spot <= 0:
        return {"ok": False, "msg": "spot abhi nahi mila (rate-limit?)"}
    legs, err = _resolve_straddle_legs(symbol, spot, spec, expiry=expiry, signed=signed)
    if err:
        return {"ok": False, "msg": err}
    # ATM strike + strike-step (from scrip master, NOT hardcoded) so the UI can
    # compute any wing's strike client-side instantly on +/- — even off-market
    # when live LTP won't refresh. Uses the SAME expiry's contracts as the legs.
    atm_strike = step = None
    try:
        _res = _straddle_resolver(expiry)
        atm_strike = _strike_of(_res(symbol, spot, "CE", 0)[1])
        _s1 = _strike_of(_res(symbol, spot, "CE", 1)[1])
        if atm_strike and _s1:
            step = abs(_s1 - atm_strike)
    except Exception:
        pass
    _sids = [lg["sec_id"] for lg in legs]
    # LTP the SAME way /api/option-ltp does — via the ONE batched ltp_poller, NOT a
    # per-request Dhan call. request_watch keeps these strikes warm (90s TTL); the
    # poller fetches all watched sec_ids in one rate-limit-respecting call/cycle
    # (TRAP #2 pattern). A short best-effort prewarm fills instantly when a slot is
    # free; otherwise the poller warms them within a cycle or two (the 3s preview
    # auto-refresh then shows them). Never blocks the request.
    try:
        import ltp_poller
        ltp_poller.request_watch([(s, "NSE_FNO") for s in _sids])
    except Exception:
        pass
    try:
        _prewarm_option_ltps(_sids, acq_timeout=1.5)   # short — best-effort immediate fill
    except Exception:
        pass

    def _cache_ltp(sec):   # cache-only (poller/prewarm warmed) — never the slow REST path
        try:
            import shared_ltp_cache as _s
            v = _s.get(str(sec), max_age=120)
            return float(v) if v else None
        except Exception:
            return None

    lot = int(legs[0]["lot"] or 0)
    q = lots * lot
    out_legs, rows, net, all_ltp = [], [], 0.0, True
    for lg in legs:
        ltp = _cache_ltp(lg["sec_id"])
        out_legs.append({"side": lg["side"], "opt_type": lg["opt_type"], "offset": lg["offset"],
                         "trad_sym": lg["trad_sym"], "strike": _strike_of(lg["trad_sym"]), "ltp": ltp})
        if ltp and ltp > 0:
            net += ltp if lg["side"] == "SELL" else -ltp
        else:
            all_ltp = False
        rows.append({"sec_id": lg["sec_id"], "entry": lg["side"], "qty": q,
                     "entry_price": ltp or 0, "sym": lg["trad_sym"], "segment": "NSE_FNO"})
    net = round(net, 2) if all_ltp else None
    margin = None
    if not quick:   # quick=fast path: skip the slow Kite basket-margin call
        try:
            margin = rg.position_margin(rows)   # single margin gate (basket, per-leg fallback)
        except Exception:
            margin = None
    return {"ok": True, "spot": round(spot, 1), "atm": atm_strike, "step": step,
            "lot": lot, "lots": lots, "legs": out_legs, "net_credit": net, "quick": bool(quick),
            "net_credit_total": (round(net * q) if net is not None else None),
            "margin": (round(margin) if margin else None),
            "margin_lot": (round(margin / lots) if margin else None)}


def _fire_flex_straddle(symbol, spot, lots, tp_pt, sl_pt, source, legs_spec, log=print, expiry="near", signed=False):
    """User-picked flexible multi-leg (the leg-window / Quick Order chain basket).
    Same safety spine as the legacy hedge path: resolve ALL legs up front, gate the
    WHOLE basket ONCE (RMS + hedged basket-margin), place BUY legs FIRST (margin
    already reduced) then SELL legs, and UNWIND everything on any mid-way failure
    (never leave an untracked/naked leg). Exit is monitored on the NET combined
    premium of the whole structure (net_exit=True). PAPER. `expiry`='near'|'nextmonth'.
    signed=True → leg offsets are signed index offsets (chain path). Returns (ok, msg)."""
    import execution_gateway as gw
    import auto_straddle as ast
    import risk_gate as rg
    try:
        import ltp_poller
    except Exception:
        ltp_poller = None
    mode = "paper"
    legs, err = _resolve_straddle_legs(symbol, spot, legs_spec, expiry=expiry, signed=signed)
    if err:
        return False, f"legs resolve fail — {err}"
    lot = int(legs[0]["lot"] or 0)
    if lot < 1:
        return False, f"{symbol} lot size resolve nahi hua — order NAHI bheja"
    gid = "STRAD_" + uuid.uuid4().hex[:8]
    sid = _straddle_strategy_id(source)
    q = lots * lot
    # BUY legs FIRST (reduce margin), then SELL — deterministic, unwind-safe order
    ordered = sorted(legs, key=lambda l: 0 if l["side"] == "BUY" else 1)

    # ── gate the WHOLE structure ONCE (RMS + basket-margin capital) ──
    try:
        blocked, why, _hard = rg.gating_status(sid, mode=mode)
        if blocked:
            return False, f"RMS blocked — {why}"
    except Exception:
        pass
    try:
        _prewarm_option_ltps([lg["sec_id"] for lg in ordered], log=log)
    except Exception:
        pass
    def _basket_at(n):
        _q = int(n) * lot
        return [{"sec_id": lg["sec_id"], "entry": lg["side"], "qty": _q,
                 "entry_price": _straddle_lltp(lg["sec_id"]) or 0,
                 "sym": lg["trad_sym"], "segment": "NSE_FNO"} for lg in ordered]
    _sz, _need, _why = rg.affordable_lots(sid, lots, _basket_at, mode=mode)
    if _sz < 1:
        log(f"[STRADDLE] {symbol} flex skip — basket margin fit nahi even for 1 lot: {_why}")
        return False, f"capital — {_why}"
    if _sz < lots:
        log(f"[STRADDLE] {symbol} flex smart-size {lots}->{_sz} lots — ₹{_need:,.0f} ({_why})")
    lots, q = _sz, _sz * lot

    # ── place, unwind-safe ──
    placed = []          # [{sec, tsym, side}]
    out_legs = []        # stored legs (with fills)

    def _unwind_all(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(sid, symbol, p["sec"], p["tsym"], q, entry_side=p["side"],
                                mode=mode, group_id=gid, reason=reason, tag="STRADDLE",
                                source="straddle", log=log)
            except Exception as ue:
                log(f"[STRADDLE] {symbol} flex unwind FAIL {p['tsym']}: {ue}")
                try:
                    import notify
                    notify.error("straddle_unwind_%s" % gid,
                                 f"⚠️ {symbol} straddle unwind fail ({p['tsym']}) — MANUAL check",
                                 source="chain")
                except Exception:
                    pass

    for lg in ordered:
        tag = "STRADDLE_HEDGE" if lg["side"] == "BUY" else "STRADDLE"
        xtra = ["STRADDLE", "STRAD_SRC:%s" % source] + (["HEDGE"] if lg["side"] == "BUY" else [])
        res = gw.execute_signal(sid, symbol, lg["side"], lots, lot, lg["sec_id"], lg["trad_sym"],
                                seg="NSE_FNO", mode=mode, source="straddle", tag=tag,
                                group_id=gid, gate=False, extra_tags=xtra, log=log)
        if not res.get("ok"):
            log(f"[STRADDLE] {symbol} flex {lg['side']} {lg['opt_type']} fail "
                f"({res.get('reason') or res.get('status')}) — unwinding, abort")
            _unwind_all("STRADDLE_ABORT_FLEX")
            return False, f"{lg['side']} {lg['opt_type']} leg fail — sab unwound (koi leg nahi bacha)"
        placed.append({"sec": lg["sec_id"], "tsym": lg["trad_sym"], "side": lg["side"]})
        out_legs.append({"opt_type": lg["opt_type"], "side": lg["side"], "offset": lg["offset"],
                         "sec_id": lg["sec_id"], "trad_sym": lg["trad_sym"],
                         "entry_price": res.get("price") or 0, "qty": q})

    entry_net = round(sum((l["entry_price"] if l["side"] == "SELL" else -l["entry_price"])
                          for l in out_legs), 2)
    ast.add({"symbol": symbol, "lots": lots, "mode": mode, "source": source, "strategy_id": sid,
             "group_id": gid, "tp_pt": tp_pt, "sl_pt": sl_pt, "entry_credit": entry_net,
             "legs": out_legs, "net_exit": True})
    if ltp_poller:
        try:
            ltp_poller.request_watch([(l["sec_id"], "NSE_FNO") for l in out_legs])
        except Exception:
            pass
    nS = sum(1 for l in out_legs if l["side"] == "SELL")
    nB = len(out_legs) - nS
    try:
        import notify
        # Manual straddle = user ne khud placed (Open Positions me turant dikhta) —
        # uska info-notify faltu spam tha. Sirf AUTO (9:20/alert) ka notify (jo bina
        # user-action fire hua, wo jaanna zaroori). Grouped category "Straddle placed".
        if "manual" not in str(source).lower():
            notify.push(f"🩳 {symbol} {len(out_legs)}-leg ({nS}S/{nB}B) @ net {entry_net:.0f} "
                        f"(tgt −{tp_pt:.0f} / SL +{sl_pt:.0f}) [{source}]",
                        level="info", key="straddle_open_%s" % gid, source="chain")
    except Exception:
        pass
    _kind = "credit" if entry_net >= 0 else "debit"
    return True, f"[PAPER] {symbol} {len(out_legs)}-leg placed @ net {entry_net:.0f} ({_kind})"


# ════════════════════════════════════════════════════════════════════════════════
# StockMock-style config strategies (_sm) — generic scheduled leg-basket runner.
# One nifty_config[<id>]["_sm"] config drives BOTH the backtest (StockMock-parity engine)
# AND this live-paper firer → the two can't silently diverge (Rule 10). Entry fires the
# configured legs with a per-leg SL% tag; ALL exits (per-leg SL + EOD squareoff) are the
# existing pos_monitor's job — square-off-one is natural since each leg is monitored on its
# own SL. PAPER-locked. Loop-driven (not a process) — auto_scheduler skips it (id not in
# STRATEGIES, like webhook/straddle). See _ops/sm_runner.py (pure logic) + memory
# project_code3b_stockmock_parity.
# ════════════════════════════════════════════════════════════════════════════════
def _fire_sm_strategy(strategy_id, cfg, log=print):
    """Fire a StockMock-style leg basket (PAPER). Resolve ALL legs → gate the WHOLE basket
    ONCE (RMS gating + basket-margin capital, so naked sells don't partial/naked-orphan —
    TRAP #156) → place each leg via execution_gateway with a per-leg SL% tag (pos_monitor
    enforces the SELL entry×(1+sl%) stop + EOD). All-or-nothing: any leg fails → unwind the
    placed legs. Per-leg lots (asymmetric OK). cfg = sm_runner.parse_cfg(...). Returns (ok, msg)."""
    import dhan_master
    import execution_gateway as gw
    import risk_gate as rg
    symbol = cfg["instrument"]
    mode = "paper"   # _sm strategies are hard paper-locked
    try:
        _sq, no_entry = rg.exit_time_config()
        now = _ast_ist_now()
        if (now.hour, now.minute) >= (no_entry[0], no_entry[1]):
            return False, f"no-entry window ke baad ({no_entry[0]:02d}:{no_entry[1]:02d}) — nahi khola"
    except Exception:
        pass
    spot = _trigger_spot_now(symbol)
    if not spot or spot <= 0:
        return False, f"{symbol} spot abhi nahi mila (rate-limit?) — order NAHI bheja"
    # resolve ALL legs up front. Config `off` is a LITERAL signed strike offset from ATM
    # (StockMock display: PE "ATM-100" → off=-2, CE "ATM+100" → off=+2). get_option_contract
    # takes an OTM-magnitude offset and INVERTS it for PE internally (positive = OTM = below
    # spot, TRAP #140). So convert: CE passes off as-is; PE passes -off (a literal -2 OTM-below
    # → +2 to get_option_contract → correct OTM put). cp_pct_sp legs resolve by premium instead.
    _SM_STEP = {"NIFTY": 50, "BANKNIFTY": 100}
    step = _SM_STEP.get(symbol, 50)
    atm = round(spot / step) * step
    # sw_mult (Straddle Width) legs need the LIVE ATM straddle premium (ATM CE + ATM PE LTP)
    _sm_straddle = None
    if any(lg.get("strike_mode") == "sw_mult" for lg in cfg["legs"]):
        _ace, _t_ace, _al = dhan_master.get_option_contract(symbol, spot, "CE", 0)
        _ape, _t_ape, _ = dhan_master.get_option_contract(symbol, spot, "PE", 0)
        if _ace and _ape:
            try:
                _prewarm_option_ltps([_ace, _ape], log=log)
            except Exception:
                pass
            _sm_straddle = (_straddle_lltp(_ace) or 0) + (_straddle_lltp(_ape) or 0)
        if not _sm_straddle or _sm_straddle <= 0:
            return False, f"{symbol} ATM straddle LTP nahi mila — sw_mult strike resolve fail"
    resolved = []
    for lg in cfg["legs"]:
        # NOTE: use a loop-local name (smode) — do NOT rebind the outer `mode` ("paper"),
        # else the strike-mode leaks into order_store.record() + RMS gating and every _sm
        # (paper) trade looks LIVE in Stats/reconcile (TRAP: mode-field pollution).
        smode = lg.get("strike_mode")
        # premium-picked strikes (cp_pct_sp/cp_rs) need a live premium-walk resolver — not built,
        # backtest-only for now. off / atm_pct / sw_mult are deterministic → resolved here.
        if smode in ("cp_pct_sp", "cp_rs"):
            return False, f"{symbol} sm live: {smode} (premium-picked) strike abhi live me support nahi — backtest-only"
        if smode == "atm_pct":
            target = round(spot * (1 + lg["atm_pct"] / 100.0) / step) * step
            soff = int(round((target - atm) / step))
        elif smode == "sw_mult":
            target = round((atm + lg["sw_mult"] * _sm_straddle) / step) * step
            soff = int(round((target - atm) / step))
        else:
            soff = lg["off"]
        # get_option_contract takes OTM-magnitude + inverts PE (TRAP #140): CE passes soff, PE -soff
        gc_off = soff if lg["opt"] == "CE" else -soff
        sec, tsym, lot = dhan_master.get_option_contract(symbol, spot, lg["opt"], gc_off)
        if not sec or not lot:
            return False, f"{symbol} {lg['opt']} {smode} soff{soff:+d} contract resolve fail"
        resolved.append({**lg, "sec": sec, "tsym": tsym, "lot": int(lot)})
    gid = "SM_" + uuid.uuid4().hex[:8]
    # gate the WHOLE basket ONCE — RMS gating + basket-margin capital
    try:
        blocked, why, _hard = rg.gating_status(strategy_id, mode=mode)
        if blocked:
            return False, f"RMS blocked — {why}"
    except Exception:
        pass
    try:
        _prewarm_option_ltps([r["sec"] for r in resolved], log=log)
    except Exception:
        pass
    try:
        basket_rows = [{"sec_id": r["sec"], "entry": r["side"], "qty": r["lots"] * r["lot"],
                        "entry_price": _straddle_lltp(r["sec"]) or 0, "sym": r["tsym"],
                        "segment": "NSE_FNO"} for r in resolved]
        basket = rg.position_margin(basket_rows)
        ok_cap, cap_why = rg.check_capital_needed(strategy_id, basket, mode=mode)
        if not ok_cap:
            log(f"[SM] {strategy_id} skip — basket margin ₹{basket:,.0f} fit nahi: {cap_why}")
            return False, f"capital — {cap_why}"
    except Exception as ce:
        log(f"[SM] {strategy_id} basket capital err: {ce}")
    # place (BUY legs first → margin reduced, then SELL), unwind-safe
    ordered = sorted(resolved, key=lambda r: 0 if r["side"] == "BUY" else 1)
    placed = []
    def _unwind_all(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(strategy_id, symbol, p["sec"], p["tsym"], p["qty"],
                                entry_side=p["side"], mode=mode, group_id=gid, reason=reason,
                                tag="SM", source="sm", log=log)
            except Exception as ue:
                log(f"[SM] {strategy_id} unwind FAIL {p['tsym']}: {ue}")
    for r in ordered:
        xtra = ["SM", f"SM_ID:{strategy_id}"]
        if r.get("sl_pct"):
            xtra += ["SL_TYPE:pct", "SL_VAL:%g" % r["sl_pct"]]
        if r.get("tp_pct"):
            xtra += ["TP_TYPE:pct", "TP_VAL:%g" % r["tp_pct"]]
        res = gw.execute_signal(strategy_id, symbol, r["side"], r["lots"], r["lot"],
                                r["sec"], r["tsym"], seg="NSE_FNO", mode=mode, source="sm",
                                tag="SM", group_id=gid, gate=False, extra_tags=xtra, log=log)
        if not res.get("ok"):
            log(f"[SM] {strategy_id} {r['side']} {r['opt']} fail "
                f"({res.get('reason') or res.get('status')}) — unwinding, abort")
            _unwind_all("SM_ABORT")
            return False, f"{r['side']} {r['opt']} leg fail — sab unwound"
        placed.append({"sec": r["sec"], "tsym": r["tsym"], "side": r["side"], "qty": r["lots"] * r["lot"]})
    log(f"[SM] {strategy_id} fired {len(placed)} legs @ spot {spot:.0f} (gid {gid})")
    try:
        import ltp_poller
        ltp_poller.request_watch([(p["sec"], "NSE_FNO") for p in placed])
    except Exception:
        pass
    try:
        import notify
        notify.push(f"📋 {len(placed)}-leg placed @ {symbol} {spot:.0f} (paper)",
                    level="info", key="sm_open_%s" % gid, source=strategy_id)
    except Exception:
        pass
    return True, f"[PAPER] {strategy_id} {len(placed)}-leg placed @ spot {spot:.0f}"


def sm_runner_loop():
    """Daemon (monitor_daemon thread) that drives every active _sm config strategy: on a
    qualifying day (day-filter) at/after entry time, fire the leg basket ONCE — order_store
    'entries today' (durable) is the already-fired guard, so a restart never double-fires.
    Exits are 100% pos_monitor's job (per-leg SL% tag + EOD). PAPER only."""
    import time
    import sm_runner as smr
    import risk_gate as rg
    while True:
        try:
            now = _ast_ist_now()
            if (9, 15) <= (now.hour, now.minute) < (15, 30):
                today = now.date().isoformat()
                now_hm = "%02d:%02d" % (now.hour, now.minute)
                try:
                    cfg_all = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
                except Exception:
                    cfg_all = {}
                for key, raw in cfg_all.items():
                    try:
                        if not smr.is_sm(raw) or not (isinstance(raw, dict) and raw.get("active", False)):
                            continue
                        c = smr.parse_cfg(key, raw)
                        if not c or not c["valid"]:
                            continue
                        if not smr.should_fire_today(today, c):
                            continue
                        if not smr.hm_ge(now_hm, c["entry_hm"]):
                            continue
                        try:
                            if rg.entries_today(key) > 0:
                                continue   # already fired today (durable)
                        except Exception:
                            continue        # can't confirm → don't risk a double-fire
                        ok, msg = _fire_sm_strategy(key, c, log=lambda m: print(f"[SM-LOOP] {m}", flush=True))
                        print(f"[SM-LOOP] {key} fire → {ok}: {msg}", flush=True)
                    except Exception as _e:
                        print(f"[SM-LOOP] {key} error: {_e}", flush=True)
        except Exception as e:
            print("sm_runner_loop error:", e, flush=True)
        time.sleep(20)


def _fire_auto_straddle(symbol, lots, tp_pt, sl_pt, source, log=print, legs_spec=None, expiry="near"):
    """HEDGE-FIRST short straddle. Order of operations (the fix for the 2026-07-24
    naked-orphan storm):
      1. resolve ATM CE/PE (the SELL legs) + BOTH OTM hedge wings up front;
      2. if hedge is enabled and a wing can't resolve → ABORT (no naked straddle);
      3. gate the WHOLE structure ONCE — RMS gating_status + a single basket-margin
         capital check (kite_basket_margin, hedge benefit included). This replaces
         the old per-leg gate where the CE squeezed into the cap and the PE then
         got blocked on a standalone naked-margin estimate → partial/naked orphan;
      4. BUY both hedge wings FIRST (so the margin is already reduced), then SELL
         ATM CE + PE (gate=False — capital already vetted as a basket);
      5. anything fails mid-way → unwind everything placed so far (verified), never
         leave an untracked naked leg.
    All PAPER. Returns (ok, msg)."""
    import dhan_master
    import execution_gateway as gw
    import auto_straddle as ast
    import risk_gate as rg
    try:
        import ltp_poller
    except Exception:
        ltp_poller = None
    symbol = str(symbol).upper()
    if symbol not in ("NIFTY", "BANKNIFTY"):
        return False, f"{symbol} supported nahi"
    mode = "paper"   # hard paper-lock
    lots = int(lots or 1)
    tp_pt = float(tp_pt or 0)
    sl_pt = float(sl_pt or 0)
    if lots < 1:
        return False, "lots >= 1 hona chahiye"
    cfg = _auto_straddle_cfg()
    # reconcile_open (not bare has_open): self-heals a stale-open record whose SELL
    # legs are actually flat in order_store (SL/target/EOD/external close already
    # squared them) before deciding — otherwise a lagging record false-blocks a
    # genuine fresh straddle. Only clears when order_store CONFIRMS all SELL legs
    # flat; uncertain/open stays blocked. (2026-07-24 #4, TRAP #62 class.)
    if ast.reconcile_open(symbol, _straddle_leg_open, log=log):
        return False, f"{symbol} straddle already open — skip (ek waqt ek per index)"
    if source != "manual" and ast.count_today(symbol) >= int(cfg.get("max_per_day", 2)):
        return False, f"{symbol} max/day ({cfg.get('max_per_day')}) reached"
    try:
        _sq, no_entry = rg.exit_time_config()
        now = _ast_ist_now()
        if (now.hour, now.minute) >= (no_entry[0], no_entry[1]):
            return False, f"no-entry window (after {no_entry[0]:02d}:{no_entry[1]:02d}) — straddle nahi khola"
    except Exception:
        pass
    spot = _trigger_spot_now(symbol)
    if not spot or spot <= 0:
        return False, f"{symbol} spot abhi nahi mila (rate-limit?) — order NAHI bheja"
    # ── flexible leg-builder path: user picked exactly which legs (SELL ATM + BUY
    #    wings by offset, any combo). Resolves+places THOSE legs and returns; the
    #    legacy premium-pick hedge path below is only for legs_spec=None. ──
    if legs_spec:
        return _fire_flex_straddle(symbol, spot, lots, tp_pt, sl_pt, source, legs_spec, log=log, expiry=expiry)
    sec_ce, t_ce, lot = dhan_master.get_option_contract(symbol, spot, "CE", 0)
    sec_pe, t_pe, _lot2 = dhan_master.get_option_contract(symbol, spot, "PE", 0)
    if not sec_ce or not sec_pe:
        return False, f"{symbol} ATM contract resolve fail"
    if not lot:
        return False, f"{symbol} lot size resolve nahi hua — order NAHI bheja"
    gid = "STRAD_" + uuid.uuid4().hex[:8]
    sid = _straddle_strategy_id(source)   # straddle_920 / straddle_alert / straddle_manual
    q = lots * int(lot)

    def _lltp(_sec):
        """Best-effort live premium (shared cache → REST). None on failure."""
        try:
            import shared_ltp_cache as _s
            v = _s.get(str(_sec), max_age=20)
            if v:
                return float(v)
        except Exception:
            pass
        try:
            return float(_rest_ltp_fallback(_sec, "NSE_FNO") or 0) or None
        except Exception:
            return None

    # ── 1/2. Resolve BOTH hedge wings up front (hedge enabled = mandatory) ──
    hcfg = cfg.get("hedge") or {}
    hedge_on = bool(hcfg.get("enabled"))
    hedges = []   # [{opt_type, sec, tsym, lot}]
    if hedge_on:
        import strategy_safety as _ss
        _hmax = _straddle_hedge_max(cfg, symbol)
        _hmin = int(hcfg.get("min_strikes", 3))
        _hsearch = 30
        # SLOW-FIRE FIX (2026-07-24): compute_hedge_target below walks up to
        # _hsearch OTM strikes PER wing, fetching each strike's premium serially
        # via _lltp (cache-miss → rate-limited REST ~1/sec) → up to 60 serial
        # fetches → 1:30+ min before the first order. Pre-warm ALL candidate
        # wing-strike premiums (both CE & PE, the offset range the walk visits)
        # PLUS the ATM SELL legs in ONE batched Dhan LTP call, so every _lltp
        # lookup below (and the basket-margin _lltp calls) hits shared_ltp_cache
        # (max_age=20) instead of REST. compute_hedge_target's strike-selection
        # logic is untouched — this only makes its lookups fast.
        try:
            _cand = [sec_ce, sec_pe]   # ATM legs → basket-margin _lltp hits cache too
            _base = max(_hmin, 1)
            for _ot in ("CE", "PE"):
                for _off in range(_base, _base + _hsearch + 1):
                    _cs, _ct, _cl = dhan_master.get_option_contract(symbol, spot, _ot, _off)
                    if _cs:
                        _cand.append(_cs)
            _nw = _prewarm_option_ltps(_cand, log=log)
            if _nw:
                log(f"[STRADDLE] {symbol} pre-warmed {_nw} strike premiums (1 batched call) — fast hedge resolve")
        except Exception as _pwe:
            log(f"[STRADDLE] {symbol} wing pre-warm skipped ({_pwe}) — hedge resolve may be slower")
        for ot in ("CE", "PE"):
            try:
                hsec, htsym, hlot = _ss.compute_hedge_target(
                    sid, symbol, spot, ot, 0, quote_fn=_lltp,
                    min_strikes_override=_hmin,
                    max_premium_override=_hmax, max_search=_hsearch, log=log)
            except Exception as he:
                hsec = None
                log(f"[STRADDLE] {symbol} {ot} hedge resolve err: {he}")
            if not hsec:
                # user rule: hedge fail ho to straddle hi mat lagao (no naked short)
                try:
                    import notify
                    notify.warn("straddle_hedge_resolve_%s" % symbol,
                                f"⚠️ {symbol} straddle skip — {ot} hedge wing resolve nahi hua (naked nahi lagate)",
                                source="chain")
                except Exception:
                    pass
                return False, f"{ot} hedge wing resolve fail — straddle NAHI khola (naked avoid)"
            hedges.append({"opt_type": ot, "sec": str(hsec), "tsym": htsym, "lot": int(hlot or lot)})

    # ── 3. Gate the WHOLE structure ONCE (RMS + basket-margin capital check) ──
    try:
        blocked, why, _hard = rg.gating_status(sid, mode=mode)
        if blocked:
            return False, f"RMS blocked — {why}"
    except Exception:
        pass
    def _basket_at(n):
        _q = int(n) * int(lot)
        rows = [
            {"sec_id": str(sec_ce), "entry": "SELL", "qty": _q, "entry_price": _lltp(sec_ce) or 0, "sym": t_ce, "segment": "NSE_FNO"},
            {"sec_id": str(sec_pe), "entry": "SELL", "qty": _q, "entry_price": _lltp(sec_pe) or 0, "sym": t_pe, "segment": "NSE_FNO"},
        ]
        for h in hedges:
            rows.append({"sec_id": h["sec"], "entry": "BUY", "qty": _q,
                         "entry_price": _lltp(h["sec"]) or 0, "sym": h["tsym"], "segment": "NSE_FNO"})
        return rows
    _sz, _need, _why = rg.affordable_lots(sid, lots, _basket_at, mode=mode)
    if _sz < 1:
        log(f"[STRADDLE] {symbol} skip — basket margin fit nahi even for 1 lot: {_why}")
        return False, f"capital — {_why}"
    if _sz < lots:
        log(f"[STRADDLE] {symbol} smart-size {lots}->{_sz} lots — ₹{_need:,.0f} ({_why})")
    lots, q = _sz, _sz * int(lot)

    # ── 4. Place hedge wings FIRST, then the ATM SELL legs ──
    placed = []   # [{sec, tsym, side}] — for unwinding on any later failure

    def _unwind_all(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(sid, symbol, p["sec"], p["tsym"], q,
                                entry_side=p["side"], mode=mode, group_id=gid,
                                reason=reason, tag="STRADDLE", source="straddle", log=log)
            except Exception as ue:
                log(f"[STRADDLE] {symbol} unwind FAIL {p['tsym']}: {ue}")
                try:
                    import notify
                    notify.error("straddle_unwind_%s" % gid,
                                 f"⚠️ {symbol} straddle unwind fail ({p['tsym']}) — MANUAL check",
                                 source="chain")
                except Exception:
                    pass

    hlegs = []
    for h in hedges:
        hres = gw.execute_signal(sid, symbol, "BUY", lots, h["lot"], h["sec"], h["tsym"],
                                 seg="NSE_FNO", mode=mode, source="straddle", tag="STRADDLE_HEDGE",
                                 group_id=gid, gate=False,
                                 extra_tags=["STRADDLE", "HEDGE", "STRAD_SRC:%s" % source], log=log)
        if not hres.get("ok"):
            log(f"[STRADDLE] {symbol} {h['opt_type']} hedge BUY fail "
                f"({hres.get('reason') or hres.get('status')}) — unwinding, straddle abort")
            _unwind_all("STRADDLE_ABORT_HEDGE")
            return False, f"hedge {h['opt_type']} BUY fail — straddle abort (kuch nahi bacha)"
        placed.append({"sec": h["sec"], "tsym": h["tsym"], "side": "BUY"})
        hlegs.append({"opt_type": h["opt_type"], "side": "BUY", "sec_id": h["sec"],
                      "trad_sym": h["tsym"], "entry_price": hres.get("price") or 0, "qty": q})

    xtags = ["STRADDLE", "STRAD_SRC:%s" % source]
    res_ce = gw.execute_signal(sid, symbol, "SELL", lots, int(lot), sec_ce, t_ce,
                               seg="NSE_FNO", mode=mode, source="straddle", tag="STRADDLE",
                               group_id=gid, gate=False, extra_tags=xtags, log=log)
    if not res_ce.get("ok"):
        _unwind_all("STRADDLE_ABORT_NAKED")
        return False, f"CE leg fail — hedges unwound. {res_ce.get('reason') or res_ce.get('status')}"
    placed.append({"sec": str(sec_ce), "tsym": t_ce, "side": "SELL"})
    ce_fill = res_ce.get("price") or 0

    res_pe = gw.execute_signal(sid, symbol, "SELL", lots, int(lot), sec_pe, t_pe,
                               seg="NSE_FNO", mode=mode, source="straddle", tag="STRADDLE",
                               group_id=gid, gate=False, extra_tags=xtags, log=log)
    if not res_pe.get("ok"):
        log(f"[STRADDLE] {symbol} PE leg fail ({res_pe.get('reason') or res_pe.get('status')}) "
            f"— unwinding CE + hedges (no naked leg)")
        _unwind_all("STRADDLE_ABORT_NAKED")
        return False, f"PE leg fail — sab unwound (koi straddle nahi bana). {res_pe.get('reason') or res_pe.get('status')}"
    pe_fill = res_pe.get("price") or 0

    entry_credit = round((ce_fill or 0) + (pe_fill or 0), 2)
    # SELL legs FIRST (indices 0/1 = the ATM straddle the basket-exit monitors), then hedges
    legs = [
        {"opt_type": "CE", "side": "SELL", "sec_id": str(sec_ce), "trad_sym": t_ce, "entry_price": ce_fill, "qty": q},
        {"opt_type": "PE", "side": "SELL", "sec_id": str(sec_pe), "trad_sym": t_pe, "entry_price": pe_fill, "qty": q},
    ] + hlegs
    ast.add({
        "symbol": symbol, "lots": lots, "mode": mode, "source": source, "strategy_id": sid, "group_id": gid,
        "tp_pt": tp_pt, "sl_pt": sl_pt, "entry_credit": entry_credit, "legs": legs,
    })
    if ltp_poller:
        try:
            ltp_poller.request_watch([(str(sec_ce), "NSE_FNO"), (str(sec_pe), "NSE_FNO")])
        except Exception:
            pass
    try:
        import notify
        _hnote = "" if not hedge_on else f" +{len(hlegs)}-leg hedge"
        if "manual" not in str(source).lower():   # manual = user-placed, faltu notify skip (auto only)
            notify.push(f"🩳 {symbol} ATM straddle SELL @ credit {entry_credit:.0f}{_hnote} "
                        f"(tgt −{tp_pt:.0f} / SL +{sl_pt:.0f}) [{source}]",
                        level="info", key="straddle_open_%s" % gid, source="chain")
    except Exception:
        pass
    return True, f"[PAPER] {symbol} straddle sold @ {entry_credit:.0f} (CE {ce_fill:.1f} + PE {pe_fill:.1f})"


def _close_straddle(strad, status, reason, log=print):
    """Square off BOTH legs (basket exit). status ∈ target/sl/eod/manual.
    execute_exit does the fresh pre-exit flat-check per leg. Records exit_credit."""
    import execution_gateway as gw
    import auto_straddle as ast
    try:
        import shared_ltp_cache as slc
    except Exception:
        slc = None
    symbol = strad.get("symbol")
    gid = strad.get("group_id", "")
    mode = strad.get("mode", "paper")
    sid = strad.get("strategy_id", "auto_straddle")   # exit under the SAME id as entry
    for leg in strad.get("legs", []):
        try:
            gw.execute_exit(sid, symbol, leg["sec_id"], leg["trad_sym"], leg["qty"],
                            entry_side=leg.get("side", "SELL"), mode=mode, group_id=gid, reason=reason,
                            tag="STRADDLE", source="straddle", log=log)
        except Exception as e:
            log(f"[STRADDLE] {symbol} leg close fail {leg.get('trad_sym')}: {e}")
    exit_credit = None
    if slc:
        try:
            import auto_straddle as _ast
            lg = strad.get("legs", [])
            if strad.get("net_exit"):
                net, ok_net = _ast.net_credit(lg, lambda l: slc.get(l["sec_id"], max_age=20.0))
                exit_credit = net if ok_net else None
            else:
                ce = slc.get(lg[0]["sec_id"], max_age=20.0) if len(lg) > 0 else None
                pe = slc.get(lg[1]["sec_id"], max_age=20.0) if len(lg) > 1 else None
                if ce and pe:
                    exit_credit = ce + pe
        except Exception:
            pass
    ast.set_status(strad["id"], status, reason, exit_credit=exit_credit)


def _hedged_alert_cfg():
    """straddle_alert_hedged block (02.07.01) from nifty_config — the LIVE hedged
    twin of the paper alert straddle. {} if absent/inactive."""
    try:
        raw = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return raw.get("straddle_alert_hedged") or {}
    except Exception:
        return {}


def _hedged_alert_open(sid):
    """True if a straddle_alert_hedged group is already open (dedup — no stacking)."""
    try:
        import order_store
        opens = order_store.trades_for(_ast_ist_now().strftime("%Y-%m-%d")).get("open") or []
        return any(str(p.get("strategy")) == sid for p in opens)
    except Exception:
        return False


def _fire_hedged_alert_straddle(symbol, source, log=print):
    """02.07.01 — LIVE hedged ATM straddle fired ALONGSIDE the paper alert straddle
    (A/B). SELL ATM CE+PE + BUY far-OTM wings (config wing_strikes) → defined risk.
    Whole structure gated ONCE (RMS + basket margin); wings placed FIRST then shorts
    (margin-safe, unwind on any fail — NEVER a naked leg). On success arms a
    position_exit_rules ±basket_rs rule on the group, so the central monitor
    (auto_straddle_loop) does the ORDERED square-off (shorts buy-to-close first,
    then wings). Fully separate from the paper _fire_auto_straddle subsystem, which
    stays paper. Returns (ok, msg)."""
    hc = _hedged_alert_cfg()
    if not hc.get("active"):
        return False, "straddle_alert_hedged inactive"
    symbol = str(symbol).upper()
    if symbol not in ("NIFTY", "BANKNIFTY"):
        return False, f"{symbol} unsupported"
    # Per-symbol allow-list for the LIVE hedged twin (config `symbols`). Absent/empty
    # = both (backward-compat). Set ["NIFTY"] to PAUSE BankNifty live entries — the
    # 5yr real-lake backtest (reconstructed greeks) had BNF a net loser every year,
    # NIFTY marginal. Paper twin (_fire_auto_straddle) is unaffected. Reversible.
    _allow = hc.get("symbols")
    if _allow and symbol not in [str(x).upper() for x in _allow]:
        return False, f"{symbol} paused (not in straddle_alert_hedged.symbols allow-list)"
    sid = "straddle_alert_hedged"
    mode = str(hc.get("mode", "live")).lower()
    lots = int(hc.get("lots", 4))
    ws_cfg = hc.get("wing_strikes") or {}
    wing_strikes = int(ws_cfg.get(symbol, 8 if symbol == "NIFTY" else 5))
    if _hedged_alert_open(sid):
        return False, f"{sid} already open — skip (no stacking)"
    import dhan_master
    import execution_gateway as gw
    import strategy_safety as ss
    import risk_gate as rg
    import leg_collision as lc
    try:
        _sq, no_entry = rg.exit_time_config()
        now = _ast_ist_now()
        if (now.hour, now.minute) >= (no_entry[0], no_entry[1]):
            return False, f"no-entry window (after {no_entry[0]:02d}:{no_entry[1]:02d})"
    except Exception:
        pass
    spot = _trigger_spot_now(symbol)
    if not spot or spot <= 0:
        return False, f"{symbol} spot abhi nahi mila — order NAHI bheja"
    gid = f"STRADH_{symbol}_{int(_time.time())}"

    # Contracts another LIVE strategy already holds open — never share a leg (broker
    # fungibility nets the two → broken structure, see _core/leg_collision.py).
    # Paper entries don't reach the broker, so no shift for them (occ stays empty).
    occ = lc.occupied_sec_ids(sid) if str(mode).lower() == "live" else set()

    # ── resolve ATM shorts (off=0) + wings (wing_strikes OTM) ──
    shorts, wings, lot_size = [], [], 0
    for ot in ("CE", "PE"):
        sec, tsym, lot, _uoff = lc.clear_leg(symbol, spot, ot, 0, occ,
                                             dhan_master.get_option_contract, log=log)
        if not sec:
            return False, f"{ot} ATM resolve/collision fail"
        occ.add(str(sec))                       # my leg now occupies this contract
        lot_size = int(lot or lot_size or 1)
        shorts.append({"opt_type": ot, "sec_id": str(sec), "trad_sym": tsym, "lot": lot_size})
        try:
            hsec, htsym, hlot = ss.compute_hedge_target(sid, symbol, spot, ot, 0, quote_fn=None,
                                                        min_strikes_override=wing_strikes,
                                                        max_premium_override=None, max_search=1,
                                                        log=log, avoid=occ)
        except Exception as he:
            hsec = None; log(f"[straddle-hedged] {ot} wing resolve err: {he}")
        if not hsec:
            return False, f"{ot} wing resolve/collision fail — no naked"
        occ.add(str(hsec))
        wings.append({"opt_type": ot, "sec_id": str(hsec), "trad_sym": htsym, "lot": int(hlot or lot_size)})

    q = lots * lot_size

    def _px(sec):
        try:
            import shared_ltp_cache as slc
            return float(slc.get(str(sec), max_age=30) or 0)
        except Exception:
            return 0.0
    try:
        import ltp_poller
        import shared_ltp_cache as slc
        watch = [(l["sec_id"], "NSE_FNO") for l in shorts + wings]
        ltp_poller.request_watch(watch)
        for _ in range(10):
            if all(slc.get(s, max_age=30) for s, _sg in watch):
                break
            _time.sleep(0.5)
    except Exception:
        pass

    # ── single whole-structure gate (RMS + real basket margin) ──
    try:
        blocked, why, _h = rg.gating_status(sid, mode=mode)
        if blocked:
            return False, f"RMS blocked — {why}"
    except Exception:
        pass
    def _basket_at(n):
        _q = int(n) * lot_size
        return [{"sec_id": l["sec_id"], "entry": "SELL", "qty": _q, "sym": l["trad_sym"],
                 "entry_price": _px(l["sec_id"]), "segment": "NSE_FNO"} for l in shorts] \
             + [{"sec_id": l["sec_id"], "entry": "BUY", "qty": _q, "sym": l["trad_sym"],
                 "entry_price": _px(l["sec_id"]), "segment": "NSE_FNO"} for l in wings]

    # Smart size-down (shared): config lots fit na ho to poori entry miss karne ki
    # jagah utne lots fire karo jitne HAR cap (₹4L capital_rs + live cash-headroom +
    # global) me fit ho. `rg.affordable_lots` = single shared home — sab hedged
    # strategies wahi use karti hain (drift nahi). smart OFF → block-or-pass.
    _sz, _need, _why = rg.affordable_lots(sid, lots, _basket_at, mode=mode)
    if _sz < 1:
        return False, f"basket margin fit nahi even for 1 lot — {_why}"
    if _sz < lots:
        log(f"[straddle-hedged] smart-size {lots}→{_sz} lots — basket ₹{_need:,.0f} fits ({_why})")
    lots, q = _sz, _sz * lot_size

    # ── place: BUY wings FIRST (margin drops), then SELL shorts; unwind on fail ──
    placed = []

    def _unwind(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(sid, symbol, p["sec_id"], p["trad_sym"], q, entry_side=p["side"],
                                seg="NSE_FNO", mode=mode, group_id=gid, reason=reason,
                                tag="STRADH", source="strategy", instrument="options", log=log)
            except Exception as ue:
                log(f"[straddle-hedged] unwind FAIL {p['trad_sym']}: {ue}")

    for grp, side, tag in ((wings, "BUY", "STRADH_HEDGE"), (shorts, "SELL", "STRADH")):
        for l in grp:
            try:
                r = gw.execute_signal(sid, symbol, side, lots, l["lot"], l["sec_id"], l["trad_sym"],
                                      seg="NSE_FNO", mode=mode, source="strategy", tag=tag,
                                      group_id=gid, gate=False,
                                      extra_tags=(["STRADH", "HEDGE"] if side == "BUY" else ["STRADH"]),
                                      instrument="options", log=log)
            except Exception as e:
                r = {"ok": False, "reason": str(e)}
            if not r.get("ok"):
                _unwind("STRADH_ABORT")
                return False, f"{l['opt_type']} {side} fail ({r.get('reason') or r.get('status')}) — abort (no naked)"
            placed.append({"sec_id": l["sec_id"], "trad_sym": l["trad_sym"], "side": side})

    # ── arm ±basket_rs rule on the group → central monitor does ORDERED exit ──
    try:
        import position_exit_rules as per
        import basket_risk as _brisk
        # basket cap ab EK resolver se — per-lot ho to size ke saath scale karta hai,
        # aur strategy ke apne exit se tight ho to loud bolta hai (conflict).
        # apna validated exit PARENT (paper twin) ke config me hai — wahi se do,
        # warna coherence-check "unknown" pe reh jaata aur drift chhup jaata.
        _own_pt = None
        try:
            _raw = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
            _acfg = (_raw.get("_auto_straddle") or {})
            _psym = (_acfg.get("per_symbol") or {}).get(symbol) or {}
            _own_pt = _psym.get("sl_pt", _acfg.get("sl_pt"))
        except Exception:
            pass
        _br = _brisk.resolve("straddle_alert_hedged", hc, lots=lots,
                             lot_size=lot_size, own_exit_pt=_own_pt, log=log)
        tgt = float(_br["target_rs"])
        sl = -abs(float(_br["sl_rs"]))
        key = per.rule_key(gid, [])
        per.set_rule(key, gid, [], target_rs=tgt, sl_rs=sl, mode=mode)
        log(f"[straddle-hedged] armed basket rule {key}: +₹{tgt:.0f}/-₹{abs(sl):.0f}")
    except Exception as re:
        log(f"[straddle-hedged] arm basket rule FAIL: {re} — EOD squareoff still protects")
    return True, (f"{symbol} HEDGED straddle LIVE ({lots}L, wings {wing_strikes} strikes OTM, "
                  f"basket ±₹{abs(sl):.0f})")


def on_option_alert(alert):
    """C — option_alerts watcher callback. On a straddle-move / gamma-spike alert,
    auto-sell that index's ATM straddle (if enabled_alert). PAPER twin + optional
    LIVE hedged twin (02.07.01, straddle_alert_hedged)."""
    try:
        cfg = _auto_straddle_cfg()
        if not cfg.get("enabled_alert"):
            return
        key = str(alert.get("key", ""))
        u = str(alert.get("u", "")).upper()
        typ = key[4:].rsplit("_", 1)[0] if key.startswith("opt_") else ""
        if typ not in cfg.get("alert_triggers", []):
            return
        if u not in ("NIFTY", "BANKNIFTY"):
            return
        try:
            import market_calendar as mc
            if not mc.is_trading_day():
                return
        except Exception:
            pass
        # ── One alert entry per symbol per day — NO re-entry (user rule 2026-08-17).
        # The alert straddle is discretionary; a second alert later in the day used
        # to re-enter this symbol after the first had already exited. Block it (the
        # marker persists past the exit; day-reset handled in auto_straddle). Both
        # the paper twin AND the LIVE hedged twin fire from here, so gating here caps
        # both together.
        try:
            import auto_straddle as _ast_guard
            if _ast_guard.fired_alert_today(u):
                print(f"[straddle] alert {u} ({typ}) SKIPPED — already had an alert "
                      f"entry today for {u} (one per symbol/day, no re-entry)", flush=True)
                return
        except Exception as _ge:
            print(f"[straddle] alert once-per-day guard err ({_ge}) — proceeding", flush=True)
        _tp, _sl = _straddle_tp_sl(cfg, u)
        ok, msg = _fire_auto_straddle(u, cfg.get("lots", 1), _tp, _sl, "alert:%s" % typ,
                                      log=lambda m: print(m, flush=True))
        print(f"[straddle] alert-fire {u} ({typ}): {msg}", flush=True)
        # 02.07.01 — LIVE hedged twin fires ALONGSIDE the paper straddle (A/B).
        # No-op unless straddle_alert_hedged.active; fully self-guarded (dedup,
        # RMS, no-naked). Never let its failure affect the paper fire above.
        _hok = False
        try:
            _hok, _hmsg = _fire_hedged_alert_straddle(u, "alert:%s" % typ,
                                                      log=lambda m: print(m, flush=True))
            print(f"[straddle-hedged] alert-fire {u} ({typ}): {_hmsg}", flush=True)
        except Exception as _he:
            print(f"[straddle-hedged] fire err: {_he}", flush=True)
        # Mark the day AFTER a real entry (either twin) → a fire that took NO
        # position can still retry on a later alert; only a real entry blocks re-entry.
        try:
            if ok or _hok:
                import auto_straddle as _ast_mark
                _ast_mark.mark_alert(u)
        except Exception:
            pass
    except Exception as e:
        print(f"[straddle] on_option_alert err: {e}", flush=True)


def auto_straddle_loop():
    """~3s loop (monitor_daemon): (A) 9:20 scheduled fire (one-shot/day, restart-safe,
    short window) + COMBINED-premium 30/30 basket exit for every open auto-straddle.
    Reads both legs' LTP from shared_ltp_cache (kept warm via ltp_poller). PAPER."""
    import time as _t
    import auto_straddle as ast
    try:
        import shared_ltp_cache as slc
        import ltp_poller
    except Exception as e:
        print(f"[straddle] loop deps missing: {e}", flush=True)
        return
    print("[straddle] auto-straddle loop started", flush=True)
    _roller_bucket = None   # Auto-Rolling ATM Straddle (02.09) — fire on_candle_close
                            # once per fresh 5-min candle bucket (loop ticks ~3s)
    while True:
        try:
            cfg = _auto_straddle_cfg()
            now = _ast_ist_now()
            trading = True
            try:
                import market_calendar as mc
                trading = mc.is_trading_day(now.date())
            except Exception:
                pass
            # ── A) 9:20 scheduled fire (only within the entry window; ONE attempt/symbol/day) ──
            if cfg.get("enabled_920") and trading:
                eh, em = _parse_hm_pair(cfg.get("entry_920", "09:20"))
                win = int(cfg.get("entry_920_window_min", 6))
                for sym in cfg.get("symbols_920", []):
                    sym = str(sym).upper()
                    # fresh clock PER symbol — a slow _fire_auto_straddle (multi-minute hedge
                    # resolve) used to leave `now` stale so the next symbol fired outside the
                    # window; recompute the window gate right before each symbol's fire.
                    now2 = _ast_ist_now()
                    delta = (now2.hour * 60 + now2.minute) - (eh * 60 + em)
                    if not (0 <= delta <= win):
                        continue
                    if ast.fired_920_today(sym) or ast.has_open(sym):
                        continue
                    # mark the attempt BEFORE firing → a failed/partial fire (RMS block,
                    # capital cap, naked-abort) can NEVER re-trigger every 3s in the window.
                    ast.mark_920(sym)
                    _tp, _sl = _straddle_tp_sl(cfg, sym)
                    ok, msg = _fire_auto_straddle(sym, cfg.get("lots", 1), _tp, _sl, "schedule_920",
                                                  log=lambda m: print(m, flush=True))
                    print(f"[straddle] 9:20 {sym}: {msg}", flush=True)
                    if not ok:
                        try:
                            import notify
                            notify.warn("straddle_920_fail_%s" % sym,
                                        f"🩳 {sym} 9:20 straddle nahi laga: {msg}", source="chain")
                        except Exception:
                            pass
            # ── COMBINED-premium 30/30 basket exit ──
            opens = ast.list_open()
            if opens:
                watch = []
                for s in opens:
                    for lg in s.get("legs", []):
                        watch.append((str(lg["sec_id"]), "NSE_FNO"))
                try:
                    ltp_poller.request_watch(watch)
                except Exception:
                    pass
                # Warm-up grace: don't let target/SL fire in the first _STRAD_GRACE
                # seconds after entry. Freshly-placed legs aren't poller-warmed yet,
                # so the cache can still hold a pre-entry/preview tick → a phantom
                # target/SL fired within ~10s of entry (e.g. live read 88 vs real 376
                # → +289pt "target"). get_after() below also rejects any non-post-entry
                # tick; the grace is belt-and-suspenders for a transient early misread.
                _STRAD_GRACE = 20
                for s in opens:
                    lg = s.get("legs", [])
                    entry_ts = s.get("created_ts", 0)
                    if (_t.time() - entry_ts) < _STRAD_GRACE:
                        continue   # still warming up — don't fire on not-yet-warm cache
                    # Only act on a tick that is BOTH fresh AND from after this straddle's
                    # entry — a stale pre-entry value can never drive the exit (root of the
                    # instant-hit bug).
                    _pof = lambda l, ets=entry_ts: slc.get_after(str(l["sec_id"]), ets, max_age=8.0)
                    if s.get("net_exit"):
                        # flexible structure — exit on NET premium of ALL legs (SELL−BUY)
                        if not lg:
                            continue
                        live, ok_net = ast.net_credit(lg, _pof)
                        if not ok_net:
                            continue   # a leg's fresh post-entry LTP missing → freeze, never fire
                        reason, profit = ast.check_exit_net(s["entry_credit"], live, s["tp_pt"], s["sl_pt"])
                    else:
                        if len(lg) < 2:
                            continue
                        ce = slc.get_after(str(lg[0]["sec_id"]), entry_ts, max_age=8.0)
                        pe = slc.get_after(str(lg[1]["sec_id"]), entry_ts, max_age=8.0)
                        reason, live, profit = ast.check_exit(s["entry_credit"], ce, pe, s["tp_pt"], s["sl_pt"])
                    if reason in ("target", "sl"):
                        _close_straddle(s, reason, "STRADDLE_%s" % reason.upper(),
                                        log=lambda m: print(m, flush=True))
                        try:
                            import notify
                            notify.push(f"{'🎯' if reason == 'target' else '🛑'} {s['symbol']} straddle "
                                        f"{reason.upper()} — credit {s['entry_credit']:.0f}→{live:.0f} ({profit:+.0f}pt)",
                                        level="info" if reason == "target" else "warn",
                                        key="straddle_exit_%s" % s["id"], source="chain")
                        except Exception:
                            pass
                        print(f"[straddle] {s['symbol']} {reason.upper()} @ {live:.0f} ({profit:+.0f}pt)", flush=True)
            # ── generic per-GROUP combined-MTM auto-exit rules (#02) — runs every
            #    cycle regardless of straddles, shares this loop's warm LTP cache ──
            try:
                _run_position_exit_rules(log=lambda m: print(m, flush=True))
            except Exception as _pe:
                print(f"[exit-rule] loop err: {_pe}", flush=True)
            # ── Auto-Rolling ATM Straddle (02.09) — hook into THIS existing cycle
            #    (no new polling loop, ADR-004). Fire on_candle_close once per fresh
            #    5-min candle bucket. Enabled-gated → a complete no-op when off, so
            #    zero risk to this live loop until explicitly turned on in config. ──
            try:
                import atm_straddle_roller as _roller
                _rcfg = _roller.load_config()
                if _rcfg.get("enabled"):
                    _bucket = f"{now.strftime('%Y-%m-%d %H')}:{now.minute // 5}"
                    if _bucket != _roller_bucket:
                        _roller_bucket = _bucket   # once per 5-min candle
                        for _rsym in _rcfg.get("symbols", ["NIFTY"]):
                            _roller.on_candle_close(str(_rsym).upper(), now=now, cfg=_rcfg,
                                                    log=lambda m: print(m, flush=True))
            except Exception as _re:
                print(f"[roller] loop err: {_re}", flush=True)
        except Exception as e:
            print(f"[straddle] loop error: {e}", flush=True)
        _t.sleep(3)


@app.route('/api/auto-straddle/fire', methods=['POST'])
def api_auto_straddle_fire():
    """B — Quick Order 'Sell ATM Straddle'. PAPER."""
    try:
        d = request.get_json(force=True) or {}
        sym = str(d.get("symbol", "NIFTY")).upper()
        lots = int(d.get("lots", 1) or 1)
        tp = float(d.get("tp_pt", 30) or 30)
        sl = float(d.get("sl_pt", 30) or 30)
        legs = d.get("legs") or None   # flexible leg-window spec (None = legacy ATM+hedge)
        expiry = _norm_expiry(d.get("expiry"))   # near | nextmonth | YYYY-MM-DD
        ok, msg = _fire_auto_straddle(sym, lots, tp, sl, "manual",
                                      log=lambda m: print(m, flush=True), legs_spec=legs, expiry=expiry)
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/auto-strangle/fire', methods=['POST'])
def api_auto_strangle_fire():
    """Manual fire of the positional hedged short-strangle + roll + IV-gate. PAPER
    hard-locked (strangle_live.MODE). Self-contained in _ops/strangle_live.py."""
    try:
        import strangle_live
        d = request.get_json(force=True) or {}
        sym = str(d.get("symbol", "NIFTY")).upper()
        pos = strangle_live.fire_strangle(sym, "strangle_manual")
        if pos:
            return jsonify({"ok": True, "msg": f"Strangle entered ({sym}) credit "
                            f"{pos['entry_net_credit']} target {pos['target_pts']} (paper)",
                            "id": pos["id"]})
        return jsonify({"ok": False, "msg": "not entered — IV-gate / RMS / data-gap / already open (see log)"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/auto-strangle/list', methods=['GET'])
def api_auto_strangle_list():
    """Open strangle positions (display)."""
    try:
        import auto_strangle_roll as sr
        return jsonify({"ok": True, "positions": sr.list_open()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "positions": []})


@app.route('/api/auto-straddle/preview', methods=['POST'])
def api_auto_straddle_preview():
    """Live preview for the Quick Order straddle LEG WINDOW / chain multi-leg:
    per-leg strike/LTP + net credit + real hedged basket margin. Read-only — NO
    order placed. signed=True → chain path (signed index offsets, ITM+OTM)."""
    try:
        d = request.get_json(force=True) or {}
        _exp = _norm_expiry(d.get("expiry"))   # near | nextmonth | YYYY-MM-DD
        return jsonify(_straddle_preview(d.get("symbol", "NIFTY"), d.get("lots", 1),
                                         d.get("legs") or [], quick=bool(d.get("quick")),
                                         expiry=_exp, signed=bool(d.get("signed"))))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/option-chain', methods=['POST'])
def api_option_chain():
    """Quick Order CHAIN — ATM±N strikes for one expiry: per strike CE/PE
    {ltp, oi, iv, delta} + signed index offsets for firing. Fast: OI/IV/greeks
    come INSTANTLY from the collector lake snapshot (one file read, real Dhan
    greeks, ~1min fresh); live LTP is overlaid via ONE batched marketfeed call
    (ltp_poller.request_watch + short best-effort prewarm, TRAP #2 pattern) — never
    per-leg, never blocking. Display-only (no order/risk path). Offsets are relative
    to the resolved ATM, the SAME semantics /api/manual-order + /api/triggers use."""
    try:
        d = request.get_json(force=True) or {}
        sym = str(d.get("symbol", "NIFTY")).upper()
        if sym not in ("NIFTY", "BANKNIFTY"):
            return jsonify({"ok": False, "msg": f"{sym} supported nahi"})
        n = max(1, min(40, int(d.get("n", 12))))   # cap 40 → far-OTM wings (BNF ±35) reachable, matches collector window
        expiry = _norm_expiry(d.get("expiry"))
        import shared_ltp_cache as _slc

        # LAKE-FIRST: the collector snapshot (OI/IV/greeks + ~1min LTP) is the display
        # source — one file read, ZERO Dhan calls, so the chain shows off-market too.
        from datetime import timedelta as _td
        _ist = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
        snap, snap_dt, snap_exp = {}, None, None
        lake_atm = lake_step = lake_spot = None
        try:
            import option_curves as _oc
            _sn = _oc.chain_snapshot(sym, _ist.strftime("%Y-%m-%d"))
            if _sn.get("ok"):
                snap = _sn.get("strikes") or {}
                snap_dt, snap_exp = _sn.get("datetime"), _sn.get("expiry")
                lake_atm, lake_step, lake_spot = _sn.get("atm"), _sn.get("step"), _sn.get("spot")
        except Exception:
            pass

        # spot: FRESH cache = market open (→ live LTP overlay). Fall back to wide-stale
        # cache, then the lake's own captured spot. All cache-only — NEVER the blocking
        # REST fallback (that made the endpoint hang ~15s off-market). Firing uses fresh
        # spot separately; this is display.
        def _idx(age):
            try:
                v = _slc.get_index(sym, max_age=age)
                return float(v) if v else None
            except Exception:
                return None
        spot_live = _idx(45)                       # <45s old = poller active = market open
        spot = spot_live or _idx(86400) or lake_spot
        if not spot or spot <= 0:
            return jsonify({"ok": False, "msg": "spot cache me nahi (market band + koi stored data nahi)"})

        # Grid: prefer the lake's ATM/step (matches the OI/IV data exactly). Only when
        # the lake is empty (collector didn't run today) fall back to live resolution.
        import dhan_master
        _res = _straddle_resolver(expiry)
        rows, sids, lot0 = [], [], None
        if lake_atm and lake_step:
            atm_strike, step = int(lake_atm), int(lake_step)
            for i in range(-n, n + 1):
                K = atm_strike + i * step
                # off-market: only rows the collector actually captured (no blank strikes);
                # market open: full ±n (outer strikes get live LTP, OI/IV may be blank).
                if K not in snap and not spot_live:
                    continue
                lk = snap.get(K, {})
                ce_sec = pe_sec = ce_sym = pe_sym = None
                if spot_live:                       # resolve for live LTP overlay only when open
                    sc, tc, lot = _res(sym, spot, "CE", i)
                    sp, tp, _lp = _res(sym, spot, "PE", -i)
                    ce_sec, ce_sym = (str(sc) if sc else None), tc
                    pe_sec, pe_sym = (str(sp) if sp else None), tp
                    if lot:
                        lot0 = int(lot)
                    if sc:
                        sids.append(str(sc))
                    if sp:
                        sids.append(str(sp))
                rows.append({"strike": K, "off_ce": i, "off_pe": -i,
                             "ce_sec": ce_sec, "ce_sym": ce_sym, "lk_ce": lk.get("ce") or {},
                             "pe_sec": pe_sec, "pe_sym": pe_sym, "lk_pe": lk.get("pe") or {}})
        else:
            # no lake — live resolution (needs a spot; works with stale but LTP only live-open)
            atm_strike = None
            for i in range(-n, n + 1):
                sc, tc, lot = _res(sym, spot, "CE", i)
                sp, tp, _lp = _res(sym, spot, "PE", -i)
                k = _strike_of(tc)
                if k is None:
                    continue
                if i == 0:
                    atm_strike = k
                if lot:
                    lot0 = int(lot)
                if sc:
                    sids.append(str(sc))
                if sp:
                    sids.append(str(sp))
                rows.append({"strike": k, "off_ce": i, "off_pe": -i,
                             "ce_sec": str(sc) if sc else None, "ce_sym": tc, "lk_ce": {},
                             "pe_sec": str(sp) if sp else None, "pe_sym": tp, "lk_pe": {}})
            step = None
            _ss = sorted(r["strike"] for r in rows)
            if len(_ss) >= 2:
                step = _ss[1] - _ss[0]
        if not rows:
            return jsonify({"ok": False, "msg": "chain data nahi (collector aaj chala?)"})

        # live LTP overlay — market open only: warm poller + ONE short best-effort batched
        # call, then read cache-only. Off-market this whole block is skipped (sids empty).
        if spot_live and sids:
            try:
                import ltp_poller
                ltp_poller.request_watch([(s, "NSE_FNO") for s in sids])
            except Exception:
                pass
            try:
                _prewarm_option_ltps(sids, acq_timeout=1.2)
            except Exception:
                pass

        def _cl(sec):
            if not sec:
                return None
            try:
                v = _slc.get(str(sec), max_age=120)
                return round(float(v), 2) if v else None
            except Exception:
                return None

        out_rows = []
        for r in rows:
            ce_l, pe_l = r["lk_ce"], r["lk_pe"]
            ce_live = _cl(r["ce_sec"]) if r["ce_sec"] else None
            pe_live = _cl(r["pe_sec"]) if r["pe_sec"] else None
            out_rows.append({
                "strike": r["strike"], "off_ce": r["off_ce"], "off_pe": r["off_pe"],
                "ce": {"ltp": ce_live if ce_live is not None else ce_l.get("ltp"),
                       "oi": ce_l.get("oi"), "iv": ce_l.get("iv"), "delta": ce_l.get("delta"),
                       "sym": r["ce_sym"]},
                "pe": {"ltp": pe_live if pe_live is not None else pe_l.get("ltp"),
                       "oi": pe_l.get("oi"), "iv": pe_l.get("iv"), "delta": pe_l.get("delta"),
                       "sym": r["pe_sym"]},
            })
        return jsonify({"ok": True, "symbol": sym, "spot": round(spot, 1),
                        "atm": atm_strike, "step": step, "lot": lot0, "expiry": expiry,
                        "live": bool(spot_live), "snap_dt": snap_dt, "snap_expiry": snap_exp,
                        "rows": out_rows})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/chain/fire-basket', methods=['POST'])
def api_chain_fire_basket():
    """Quick Order chain MULTI-LEG basket (PAPER). Legs carry SIGNED index offsets
    (ITM+OTM, per opt_type). Reuses the flex-straddle unwind-safe placement spine
    (_fire_flex_straddle, signed=True) — same RMS gate + basket margin + BUY-first
    + full unwind-on-fail. Single-leg orders go through /api/manual-order instead."""
    try:
        d = request.get_json(force=True) or {}
        sym = str(d.get("symbol", "NIFTY")).upper()
        lots = int(d.get("lots", 1) or 1)
        tp = float(d.get("tp_pt", 30) or 30)
        sl = float(d.get("sl_pt", 30) or 30)
        legs = d.get("legs") or []
        expiry = _norm_expiry(d.get("expiry"))
        if len(legs) < 2:
            return jsonify({"ok": False, "msg": "basket ke liye 2+ legs chahiye"})
        spot = _trigger_spot_now(sym)   # FIRING = fresh spot only (never stale)
        if not spot or spot <= 0:
            return jsonify({"ok": False, "msg": f"{sym} live spot nahi (market band / rate-limit) — order NAHI"})
        ok, msg = _fire_flex_straddle(sym, spot, lots, tp, sl, "chain",
                                      legs, log=lambda m: print(m, flush=True),
                                      expiry=expiry, signed=True)
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/intervention')
def intervention_page():
    """Manual Intervention Report — 'agar haath se cut na karte to kya hota'."""
    return render_template('intervention.html')


@app.route('/api/intervention')
def api_intervention():
    """Counterfactual for the day's manually-cut positions (display-only)."""
    try:
        import intervention_report as ir
        date = request.args.get('date') or None
        mode = request.args.get('mode')
        mode = mode if mode in ('live', 'paper') else None
        res = ir.analyze(date, mode)
        res['trend'] = ir.trend(8, mode)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/intervention/rerun', methods=['POST'])
def api_intervention_rerun():
    """Recompute + persist today's (or a given date's) intervention report."""
    try:
        import intervention_report as ir
        d = request.get_json(silent=True) or {}
        res = ir.build_and_store(d.get('date') or None)
        res['trend'] = ir.trend(8)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/intervention/chart')
def api_intervention_chart():
    """Premium OHLC bars for a cut's option (intervention chart popup). The entry /
    manual-cut / strategy-would-exit markers are drawn client-side from the cut row
    the /api/intervention response already carries. Display-only."""
    try:
        import intervention_report as ir
        bars = ir.chart_bars(request.args.get('sec_id'), request.args.get('date'),
                             request.args.get('symbol'), request.args.get('trad_sym'))
        return jsonify({"ok": True, "bars": bars})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "bars": []})


@app.route('/api/intervention/overview')
def api_intervention_overview():
    """All-dates intervention aggregate (live/paper/both, day/week/month) — one
    graph for the whole history so the user needn't open each date. Pre-warmed
    (builds+stores any missing date on first read)."""
    try:
        import intervention_report as ir
        mode = request.args.get('mode')
        mode = mode if mode in ('live', 'paper') else None
        group = request.args.get('group')
        group = group if group in ('day', 'week', 'month') else 'day'
        return jsonify(ir.overview(mode, group))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/option-expiries')
def api_option_expiries():
    """Listed option expiries (weeklies + monthlies, >= today) for a symbol, so the
    Quick-Order straddle builder can pick a SPECIFIC expiry (e.g. match Sensibull's
    04 Aug) instead of only nearest/next-month. Read-only display — no order path."""
    try:
        import dhan_master
        sym = str(request.args.get("symbol", "NIFTY")).upper()
        return jsonify({"ok": True, "expiries": dhan_master.list_expiries(sym)})
    except Exception as e:
        return jsonify({"ok": False, "expiries": [], "msg": str(e)})


@app.route('/api/auto-straddle/list')
def api_auto_straddle_list():
    """Today's straddles + live combined premium + P&L points for the UI."""
    try:
        import auto_straddle as ast
        import shared_ltp_cache as slc
        out = []
        for s in ast.list_today():
            lg = s.get("legs", [])
            if s.get("net_exit"):
                net, ok_net = ast.net_credit(lg, lambda l: slc.get(l["sec_id"], max_age=15.0))
                live = net if ok_net else None
            else:
                ce = slc.get(lg[0]["sec_id"], max_age=15.0) if len(lg) > 0 else None
                pe = slc.get(lg[1]["sec_id"], max_age=15.0) if len(lg) > 1 else None
                live = round(ce + pe, 2) if (ce and pe) else None
            prof = round(s["entry_credit"] - live, 1) if (live is not None and s.get("entry_credit") is not None) else None
            out.append({**s, "live_credit": live, "profit_pt": prof})
        return jsonify({"ok": True, "straddles": out, "cfg": _auto_straddle_cfg()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "straddles": []})


@app.route('/api/auto-straddle/close', methods=['POST'])
def api_auto_straddle_close():
    try:
        import auto_straddle as ast
        d = request.get_json(force=True) or {}
        s = next((x for x in ast.list_open() if x.get("id") == d.get("id")), None)
        if not s:
            return jsonify({"ok": False, "msg": "straddle not found / already closed"})
        _close_straddle(s, "manual", "STRADDLE_MANUAL_CLOSE", log=lambda m: print(m, flush=True))
        return jsonify({"ok": True, "msg": "closing both legs"})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/auto-straddle/config', methods=['GET', 'POST'])
def api_auto_straddle_config():
    """Get/set nifty_config['_auto_straddle']. Live mode is NOT settable here — paper-locked."""
    try:
        if request.method == 'GET':
            return jsonify({"ok": True, "cfg": _auto_straddle_cfg()})
        d = request.get_json(force=True) or {}
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        cur = cfg.get("_auto_straddle") or {}
        for k in ("enabled_920", "enabled_alert"):
            if k in d:
                cur[k] = bool(d[k])
        for k in ("lots", "max_per_day"):
            if k in d:
                cur[k] = int(d[k])
        # tp/sl → per-index (per_symbol[symbol]) when a symbol is given, else global
        _sym = str(d.get("symbol", "")).upper()
        if _sym in ("NIFTY", "BANKNIFTY") and ("tp_pt" in d or "sl_pt" in d or "hedge_max_premium" in d):
            ps = cur.get("per_symbol") or {}
            node = ps.get(_sym) or {}
            if "tp_pt" in d:
                node["tp_pt"] = float(d["tp_pt"])
            if "sl_pt" in d:
                node["sl_pt"] = float(d["sl_pt"])
            if "hedge_max_premium" in d:
                node["hedge_max_premium"] = float(d["hedge_max_premium"])   # per-index wing ₹
            ps[_sym] = node
            cur["per_symbol"] = ps
        else:
            for k in ("tp_pt", "sl_pt"):
                if k in d:
                    cur[k] = float(d[k])
        if isinstance(d.get("symbols_920"), list):
            cur["symbols_920"] = [str(x).upper() for x in d["symbols_920"] if str(x).upper() in ("NIFTY", "BANKNIFTY")]
        if isinstance(d.get("alert_triggers"), list):
            cur["alert_triggers"] = [str(x) for x in d["alert_triggers"]]
        if "hedge_enabled" in d:
            h = cur.get("hedge") or {}
            h["enabled"] = bool(d["hedge_enabled"])   # global on/off; max_premium is PER-INDEX above
            cur["hedge"] = h
        cur["mode"] = "paper"
        cfg["_auto_straddle"] = cur
        _write_json_atomic(TC_FILE, cfg)
        return jsonify({"ok": True, "cfg": _auto_straddle_cfg()})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/straddle-chart')
def straddle_chart_page():
    from flask import send_file
    return send_file(BASE_DIR / 'templates' / 'straddle_chart.html')


@app.route('/api/straddle-chart-data')
def api_straddle_chart_data():
    """Combined straddle premium (CE close + PE close) intraday + entry marker +
    target/SL lines for the dedicated straddle chart. Sums the per-leg premium
    candles (disk-captured) by timestamp."""
    try:
        import auto_straddle as ast
        s = ast.get(request.args.get("id"))
        if not s:
            return jsonify({"ok": False, "msg": "straddle not found"})
        date = _ast_ist_now().strftime("%Y-%m-%d")
        lg = s.get("legs", [])
        # The chart's "straddle premium" = the SELL CE + SELL PE credit only
        # (exactly what entry_credit / tp_line / sl_line track). The 2026-07-24
        # hedge-first redesign made straddles 4-leg (2 SELL + 2 BUY hedge); the
        # old `len(maps)==2` guard summed ALL legs and then never matched → the
        # chart went permanently blank (#6). Sum ONLY the 2 SELL legs, and use
        # _leg_premium_candles (disk-first, live-intraday fallback) so a fresh
        # PAPER straddle — whose legs the broker-order daemon never captures —
        # still renders instead of showing nothing.
        sell_legs = [x for x in lg if str(x.get("side", "SELL")).upper() == "SELL"] or lg[:2]
        maps = []
        for leg in sell_legs[:2]:
            cands = _leg_premium_candles(str(leg["sec_id"]), date)
            maps.append({row["time"]: row["close"] for row in cands})
        combined = []
        if len(maps) == 2 and maps[0] and maps[1]:
            for t in sorted(set(maps[0]) & set(maps[1])):
                combined.append({"t": t, "v": round(maps[0][t] + maps[1][t], 2)})
        ec = float(s.get("entry_credit", 0) or 0)
        # Payoff diagram (short straddle: P&L = (credit − |S − K|) × qty) — computed from the
        # straddle's OWN stored fields so it renders whether the position is open or closed.
        payoff = None
        try:
            def _lk(leg):
                try:
                    return float(str(leg.get("trad_sym", "")).split("-")[-2])
                except Exception:
                    return None
            plegs = []
            for leg in lg:
                K = _lk(leg)
                if K is None:
                    continue
                plegs.append({"K": K, "opt": leg.get("opt_type"), "side": leg.get("side", "SELL"),
                              "entry": float(leg.get("entry_price") or 0), "qty": float(leg.get("qty") or 0)})
            if plegs:
                atmK = _lk(lg[0]) or plegs[0]["K"]
                Ks = [p["K"] for p in plegs]
                span = max(ec * 2, (max(Ks) - min(Ks)) or ec)
                lo, hi, n = min(Ks) - span, max(Ks) + span, 160
                stepp = (hi - lo) / n

                def _pnl(S):
                    tot = 0.0
                    for p in plegs:
                        intr = max(S - p["K"], 0.0) if p["opt"] == "CE" else max(p["K"] - S, 0.0)
                        tot += ((p["entry"] - intr) if p["side"] == "SELL" else (intr - p["entry"])) * p["qty"]
                    return tot
                curve = [[round(lo + i * stepp, 1), round(_pnl(lo + i * stepp), 0)] for i in range(n + 1)]
                bes = []
                for i in range(1, len(curve)):
                    y0, y1 = curve[i - 1][1], curve[i][1]
                    if (y0 <= 0 < y1) or (y0 >= 0 > y1):
                        x0, x1 = curve[i - 1][0], curve[i][0]
                        bes.append(round(x0 + (x1 - x0) * (0 - y0) / ((y1 - y0) or 1), 1))
                ys = [c[1] for c in curve]
                payoff = {"strike": atmK, "credit": ec, "qty": plegs[0]["qty"],
                          "hedged": any(p["side"] == "BUY" for p in plegs),
                          "max_profit": round(max(ys), 0), "max_loss": round(min(ys), 0),
                          "breakevens": bes[:2], "curve": curve}
        except Exception:
            payoff = None
        try:
            import shared_ltp_cache as _slc
            spot = _slc.get_index(s.get("symbol"), max_age=120)
        except Exception:
            spot = None
        return jsonify({
            "ok": True, "symbol": s.get("symbol"), "combined": combined,
            "entry_credit": ec, "tp_line": round(ec - float(s.get("tp_pt", 30)), 2),
            "sl_line": round(ec + float(s.get("sl_pt", 30)), 2),
            "entry_ts": s.get("created_ts"), "status": s.get("status"),
            "exit_credit": s.get("exit_credit"), "legs": [x.get("trad_sym") for x in lg],
            "payoff": payoff, "spot": spot,
        })
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


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


# ══════════════════════════════════════════════════════════════════════════════
# 🎯 LEVEL SPREAD SLOTS (registry 03.02) — key-level → candle pattern → next-candle
# break → delta-hedged credit spread. State: _ops/level_slots.py (pure), loop + fire:
# _ops/level_slots_live.py (monitor_daemon thread). These routes are THIN (CRUD +
# read-only chart data) — no order path here. PAPER hard-lock lives in the module.
# ══════════════════════════════════════════════════════════════════════════════
@app.route('/level-slots')
def level_slots_page():
    return render_template("level_slots.html")


@app.route('/api/level-slots', methods=['GET'])
def api_level_slots_list():
    try:
        import level_slots as ls
        import level_slots_live as L
        ls.ensure_fixed()
        und = ls.list_underlyings()
        prices = {sym: L.spot_cached(sym) for sym in und}
        return jsonify({"ok": True, "underlyings": und, "slots": ls.list_slots(),
                        "prices": prices, "mode": L.MODE, "fixed": list(ls.FIXED_UNDERLYINGS)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/level-slots/search')
def api_level_slots_search():
    """F&O underlyings (scrip master OPTIDX+OPTSTK) — never a hardcoded list."""
    try:
        import level_slots_live as L
        q = (request.args.get('q') or '').strip().upper()
        syms = [x for x in L.fno_symbols() if (q in x) and x not in ("NIFTY", "BANKNIFTY")]
        return jsonify({"ok": True, "symbols": syms[:60]})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "symbols": []})


@app.route('/api/level-slots/underlying', methods=['POST'])
def api_level_slots_add_underlying():
    try:
        import level_slots as ls
        import level_slots_live as L
        sym = str((request.get_json() or {}).get("sym") or "").upper().strip()
        if not sym:
            return jsonify({"ok": False, "msg": "symbol do"})
        if sym != "BTC" and not L.is_fno_underlying(sym):
            return jsonify({"ok": False, "msg": f"{sym} F&O universe me nahi (options listed nahi)"})
        ok, res = ls.add_underlying(sym, {"source": "delta" if sym == "BTC" else "nse"})
        return jsonify({"ok": ok, "msg": res if not ok else "added", "underlying": res if ok else None})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/level-slots/underlying/<sym>', methods=['DELETE'])
def api_level_slots_del_underlying(sym):
    try:
        import level_slots as ls
        ok, msg = ls.remove_underlying(sym)
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/level-slots/contracts')
def api_level_slots_contracts():
    """Premium-slot strike picker: ATM±n of the nearest expiry (live spot, scrip master)."""
    try:
        import level_slots_live as L
        sym = (request.args.get('sym') or 'NIFTY').upper()
        opt = (request.args.get('opt') or 'CE').upper()
        if opt not in ("CE", "PE"):
            return jsonify({"ok": False, "msg": "opt CE/PE"})
        return jsonify(L.contracts_near_atm(sym, opt, n=int(request.args.get('n') or 6)))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "rows": []})


@app.route('/api/level-slots/<path:slot_id>/chart')
def api_level_slots_chart(slot_id):
    """Closed candles at the slot's TF (or ?tf=) + the slot itself for overlays. Read-only."""
    try:
        import level_slots as ls
        import level_slots_live as L
        s = ls.get_slot(slot_id)
        if not s:
            return jsonify({"ok": False, "msg": "slot nahi mila"})
        tf = request.args.get('tf') or s.get("tf") or "5m"
        days = int(request.args.get('days') or 1)
        bars = L.fetch_bars(s, tf=tf, days=days)
        return jsonify({"ok": True, "bars": bars[-6000:], "slot": s, "tf": tf, "days": days,
                        "price": L.price_now(s, wide=True)})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "bars": []})


@app.route('/api/level-slots/<path:slot_id>/preview', methods=['POST'])
def api_level_slots_preview(slot_id):
    """What-if for the slot as CURRENTLY typed (unsaved form values merged over the saved slot):
    legs (strike/LTP/Δ), net Δ, ₹/index-pt, credit, real hedged margin, ₹ per exit line. Read-only."""
    try:
        import level_slots as ls
        import level_slots_live as L
        s = ls.get_slot(slot_id)
        if not s:
            return jsonify({"ok": False, "msg": "slot nahi mila"})
        body = request.get_json(silent=True) or {}
        for k in ("level", "zone", "zone_unit", "from_dir", "sell_leg", "hedge_delta", "lots", "exit", "contract"):
            if k in body and body[k] is not None:
                s[k] = body[k]
        return jsonify(L.preview_structure(s))
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/level-slots/<path:slot_id>/arm', methods=['POST'])
def api_level_slots_arm(slot_id):
    try:
        import level_slots as ls
        import level_slots_live as L
        s = ls.get_slot(slot_id)
        if not s:
            return jsonify({"ok": False, "msg": "slot nahi mila"})
        px = L.price_now(s)
        if px is None:
            return jsonify({"ok": False, "msg": "price abhi nahi mila — arm REFUSED (galat direction se surprise fill na ho)"})
        ok, res = ls.arm(slot_id, spot_now=px)
        return jsonify({"ok": ok, "msg": res if not ok else f"ARMED (price {px:g})", "slot": res if ok else None})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/level-slots/<path:slot_id>/disarm', methods=['POST'])
def api_level_slots_disarm(slot_id):
    try:
        import level_slots as ls
        ok, res = ls.disarm(slot_id, "disarmed (user)")
        return jsonify({"ok": ok, "msg": res if not ok else "disarmed", "slot": res if ok else None})
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})


@app.route('/api/level-slots/<path:slot_id>', methods=['POST'])
def api_level_slots_save(slot_id):
    try:
        import level_slots as ls
        ok, res = ls.save_slot(slot_id, request.get_json() or {})
        return jsonify({"ok": ok, "msg": res if not ok else "saved", "slot": res if ok else None})
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


# margin-history rebuilds the day's margin timeline by calling position_margin per
# position-group — each a (cached) executing-broker margin lookup — so a busy day is
# ~16 s. It's polled from the Today's Peak "Margin" view. Cache per (date, mode): a PAST
# day is immutable (long TTL); today refreshes on a short TTL just past the poll interval.
_MARGIN_HIST_CACHE = {}   # (date, mode) -> (ts, payload)


@app.route('/api/margin-history')
def api_margin_history():
    import time as _t
    from datetime import timedelta as _td
    _now = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
    req_date = request.args.get("date") or _now.strftime("%Y-%m-%d")
    mode = (request.args.get("mode") or "all").lower()   # all | paper | live
    key = (req_date, mode)
    ttl = 35.0 if req_date == _now.strftime("%Y-%m-%d") else 3600.0
    now = _t.time()
    hit = _MARGIN_HIST_CACHE.get(key)
    if hit and (now - hit[0]) < ttl:
        return jsonify(hit[1])
    try:
        p = _margin_history_compute(req_date, mode)
    except Exception:
        p = {"times": [], "buy": [], "sell": [], "peak": 0.0}
    if len(_MARGIN_HIST_CACHE) > 32:
        _MARGIN_HIST_CACHE.clear()
    _MARGIN_HIST_CACHE[key] = (now, p)
    return jsonify(p)


def _margin_history_compute(req_date, mode):
    """Day margin-utilization timeline (task 74) — reconstructed from order_store
    entry/exit times, so it works for any date without touching the money loop.
    Each position holds ₹ margin from its entry_time until its exit_time (open
    positions → until 'now'): BUY leg = premium notional (qty×entry_price), SELL
    leg = executing-broker real margin (risk_gate._leg_capital, cached; falls back
    to the multiplier estimate for expired/failed lookups). Split buy vs sell,
    filtered by mode=all|paper|live (position's own mode).
    Response: {times:[HH:MM...], buy:[₹...], sell:[₹...], peak:₹}."""
    from datetime import timedelta as _td
    _now = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
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

    positions = []   # (start_min, end_min, side, margin_rs, is_open)
    try:
        import order_store
        import risk_gate as _rg
        data = order_store.trades_for(req_date)
        # current-open: for TODAY use _today_open() — it RANGE-nets positional /
        # overnight legs, so a closed overnight position's exit-leg is NOT counted
        # as a phantom open (same source as capital_in_use → chart 'current' now
        # matches the RMS number). Past dates: the day's own open snapshot.
        _open = _rg._today_open() if is_today else data.get("open", [])
        rows = list(data.get("details", [])) + list(_open)
        # Group legs by (mode, strategy, group_id) so a MULTI-LEG structure gets ONE
        # hedged BASKET margin (kite_basket_margin via _group_capital) held over the
        # group's time span — NOT a per-leg NAKED-SELL sum, which over-counts a
        # hedged condor/straddle 5-10x. Live 2026-07-28: chart peaked ₹21.8L for a
        # ~₹2.79L real basket. Same margin source as capital_in_use now.
        groups = {}
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
            e_m = min(max(end_m if is_open else e_raw, s_m), CLOSE_M)
            gid = r.get("group_id") or ""
            key = (pmode, str(r.get("strategy") or ""), gid or ("solo:%s" % r.get("id")))
            g = groups.get(key)
            if g is None:
                groups[key] = {"legs": [r], "s": s_m, "e": e_m, "op": is_open}
            else:
                g["legs"].append(r); g["s"] = min(g["s"], s_m); g["e"] = max(g["e"], e_m); g["op"] = g["op"] or is_open
        for key, g in groups.items():
            try:
                mgn = _rg.position_margin(g["legs"])          # single margin gate (hedged basket / per-leg)
            except Exception:
                mgn = sum(float(l.get("qty") or 0) * float(l.get("entry_price") or 0) for l in g["legs"])
            # buy-only group (long premium) vs anything with a SELL leg (basket margin)
            side = "BUY" if all(str(l.get("entry") or "").upper() == "BUY" for l in g["legs"]) else "SELL"
            positions.append((g["s"], g["e"], side, float(mgn or 0), g["op"]))
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
    return {"times": times, "buy": buy, "sell": sell, "peak": round(peak, 2)}


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
            # Group-scoped (2026-08-21 naked-leg fix): resolve the hedge group's open
            # legs from its OWN ledger, not by filtering today's global netting — that
            # can drop/mis-net a re-traded monthly contract's legs and orphan a short.
            siblings = order_store.open_legs_in_group(gid)
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


def _broker_position_product(broker, broker_name, t_sym, sec_id, fallback='NRML'):
    """The product (MIS/NRML/CNC) the broker ACTUALLY holds for this contract.

    Zerodha tracks MIS and NRML as SEPARATE positions, so a close/squareoff order in
    the WRONG product does not net the open leg — it opens a NEW opposite position
    (user bug 2026-08-18, TRAP #178: an NRML leg + a default-MIS close → the leg
    stayed open and a fresh MIS leg appeared). Read the real product live from the
    broker's own position book. The app's recorded product_type is NOT a reliable
    fallback — `order_store.record()` defaults it to NRML regardless of the order's
    real product — so on a read failure we return `fallback` (caller decides: the
    manual-close path passes 'NRML' since manual F&O is usually NRML; the auto-
    squareoff path passes None to preserve its prior default-MIS behaviour, no
    regression for the MIS strategy positions it usually closes)."""
    try:
        if broker_name == 'kite' and broker is not None:
            ksym = broker.resolve_symbol(t_sym, sec_id)   # forward-only exact match (TRAP #13)
            if ksym:
                for _p in (broker.positions_detailed() or []):
                    if _p.get('kite_sym') == ksym and _p.get('product'):
                        return str(_p.get('product')).upper()
    except Exception as _pe:
        print(f"[close-product] {t_sym} broker product read failed ({_pe}) — using fallback {fallback}", flush=True)
    return fallback


_nrml_syms_cache = {"ts": 0.0, "syms": set()}

# ── Broker-truth liveness for DISPLAYED open positions ───────────────────────
# WHY (user-reported 2026-08-29): the Orders page carried prior-day legs over ONLY
# for strategies whose risk_gate.allow_overnight() is True. `manual` is False — so
# a position the USER carried overnight (4 real NRML NIFTY legs, live at Zerodha)
# was completely INVISIBLE the next day, while a long-dead leg (an Aug-expired
# BANKNIFTY short whose real close DID exist in the DB but got FIFO-mis-paired
# against an older externally_closed ghost) kept showing as open. The page was
# wrong in BOTH directions at once.
#
# Fix = ask the broker. The account's own position book is the only authority on
# what is open (ADR-011, same principle as reconcile_broker). DISPLAY-ONLY: this
# never places, cancels or suppresses an order, and never touches PAPER legs —
# a paper leg has no broker position by definition, so it is always left alone.
#
# FAIL-SAFE IN BOTH DIRECTIONS: a leg is only DROPPED on a confident "not at the
# broker" (or a contract that no longer exists / has expired), and only ADDED on a
# confident "yes, held". Any read/resolve failure -> verdict None (unknown) -> the
# page behaves exactly as it did before this change.
_broker_open_cache = {"ts": 0.0, "snap": {}, "ok": False}


def _broker_open_snapshot(max_age=30):
    """({kite_tradingsymbol: signed_net_qty}, ok) — what the broker holds NOW.

    Cached ~30s so a page render with N legs costs at most ONE positions call
    (same shape as _broker_nrml_syms). Kite-only: Dhan is data/manual-only here
    (project_code3b_dhan_manual_kite_algo), and the algo's executing broker is
    Kite. ok=False -> caller must treat every verdict as unknown."""
    import time as _t
    now = _t.time()
    if now - _broker_open_cache["ts"] < max_age and _broker_open_cache["ok"]:
        return _broker_open_cache["snap"], True
    try:
        from brokers.kite_broker import KiteBroker
        _kb = KiteBroker()
        # ⚠️ AUTH PEHLE. `positions_detailed()` har exception nigal ke `[]` deta hai
        # (KiteBroker ka documented shape) — yaani **dead token aur sach me flat
        # bilkul ek jaise dikhte hain**. Uske bharose `ok=True` set kar dena is
        # poore fail-safe ko ulta kar deta hai: token marte hi book "khaali" padhi
        # jaati hai aur har asli LIVE leg page se GAYAB ho jaati hai.
        # Live-dekha 2026-08-30: Kite token dead, user ki 8 asli legs (4 weekly
        # iron-fly + 4 manual, Zerodha pe khuli) page pe "koi open position nahi",
        # aur invariant_guard "Zerodha mismatch (8)". Dono ki ek hi jad.
        # Isliye khaali book pe bharosa TABHI jab auth khud verify ho jaye.
        _alive, _why = _kb.auth_ok()
        if _alive is not True:
            print(f"[open-truth] broker auth not confirmed ({_why}) — position book "
                  f"ko authoritative NAHI maana; DB-view hi dikhega", flush=True)
            return _broker_open_cache["snap"], False
        snap = {}
        for _p in (_kb.positions_detailed() or []):
            try:
                q = int(_p.get('qty') or 0)
            except (TypeError, ValueError):
                continue
            ks = str(_p.get('kite_sym') or '')
            if ks and q:
                snap[ks] = snap.get(ks, 0) + q
        _broker_open_cache.update(ts=now, snap=snap, ok=True)
        return snap, True
    except Exception as e:
        print(f"[open-truth] broker position read failed ({e}) — display falls back "
              f"to the DB-only view", flush=True)
        # Keep the last-good snapshot but report NOT ok: a stale book must never
        # be authoritative enough to hide a real leg.
        return _broker_open_cache["snap"], False


def _leg_alive_at_broker(p, snap, ok):
    """Is this displayed open leg REALLY open? True / False / None (unknown).

    Order of authority:
      1. PAPER / CAPITAL_BLOCKED legs      -> None (never judged by a broker book)
      2. the broker's own position book    -> exact answer, when it resolves
      3. contract liveness (scrip master)  -> a contract that is gone/expired
                                              cannot be an open position
    Anything else -> None. Callers KEEP on None."""
    try:
        if str(p.get('mode') or '').lower() != 'live':
            return None                       # paper never reaches the broker
        if 'CAPITAL_BLOCKED' in (p.get('tags') or []):
            return None                       # never executed; has its own panel
        sec_id = str(p.get('sec_id') or '').strip()
        t_sym = str(p.get('sym') or '')
        if ok and sec_id and t_sym:
            try:
                from brokers import kite_broker as _kb
                _k = _kb.KiteBroker()._get_kite()
                ksym = _kb.resolve_kite_symbol(_k, t_sym, sec_id)
                if ksym:
                    return ksym in snap       # confident, both ways
            except Exception:
                pass
        # Broker unreachable, or the contract no longer resolves there (an expired
        # option is dropped from the instrument list). Fall back to the scrip
        # master: no expiry on record, or an expiry already PAST, means the
        # contract cannot be held. Expiry == today is still live (settles at EOD).
        if sec_id:
            try:
                import dhan_master as _dm
                exp = _dm.get_expiry_for_sec_id(sec_id)
            except Exception:
                return None
            _today = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d')
            if not exp:
                return False                  # not in the master at all -> gone
            return None if str(exp) >= _today else False
    except Exception:
        return None
    return None


def _broker_nrml_syms(broker, broker_name):
    """Set of Kite tradingsymbols the broker currently holds as NRML (overnight).
    Lets the EOD loop RESPECT a manual MIS->NRML conversion the user did directly
    on Zerodha — if a leg is NRML at the broker, the user chose to carry it, so the
    blanket 3:15 EOD squareoff must NOT close it (else the app fights the user's
    deliberate carry). Cached ~10s so per-leg checks don't re-hit positions_detailed.
    Kite-only (Dhan positions return nothing here); read failure keeps the last-good
    set (fail-safe: no spurious 'NRML' that would wrongly skip a squareoff)."""
    import time as _t
    if broker_name != 'kite' or broker is None:
        return set()
    now = _t.time()
    if now - _nrml_syms_cache["ts"] < 10:
        return _nrml_syms_cache["syms"]
    try:
        syms = {str(_p.get('kite_sym')) for _p in (broker.positions_detailed() or [])
                if str(_p.get('product') or '').upper() == 'NRML' and _p.get('kite_sym')}
        _nrml_syms_cache.update(ts=now, syms=syms)
        return syms
    except Exception:
        return _nrml_syms_cache["syms"]


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

        # PRODUCT MATCH (user bug 2026-08-18, TRAP #178): Zerodha tracks MIS and
        # NRML as SEPARATE positions. A close order in the WRONG product does NOT
        # net the open leg — it opens a NEW opposite position (the open leg stays,
        # a phantom appears). The close used to pass no product → Kite default MIS →
        # an NRML leg never closed (a fresh MIS leg opened instead). Read the product
        # the broker ACTUALLY holds and close with THAT (manual F&O default NRML).
        close_product = _broker_position_product(broker, broker_name, t_sym, sec_id, fallback='NRML')
        print(f"[close-position] {t_sym} closing with product={close_product} "
              f"(matched to broker's open position)", flush=True)

        res = smart_order.execute(
            close_side, t_sym, sec_id, 'NSE_FNO', qty_shares, t_sym,
            'live', broker, log=print, tag='MANUAL-CLOSE',
            source=src_in, strategy=strat_in, instrument='options',
            broker_name=broker_name, extra_tags=['MANUAL_CLOSE'],
            product=close_product,
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

    _now_ist = datetime.now(timezone.utc) + _td(hours=5, minutes=30)
    today = _now_ist.strftime('%Y-%m-%d')
    # 90-day range, NOT today-scoped: a positional/overnight group entered on a prior
    # day (VRP condor entered 07-29) has legs dated then, so trades_for(today) found
    # NONE → "No open legs" and close-all silently no-op'd (HTTP 200 but ok:False).
    # Same today-scoped family as the /api/orders display fix; trades_for_range nets
    # entry+exit across dates → surfaces the carried-over still-open legs to close.
    # Group-scoped resolution (2026-08-21 naked-leg fix): resolve THIS group's open
    # legs from its OWN ledger, not by filtering the global multi-day netting — that
    # cross-nets a re-traded monthly contract across days and drops legs, so a manual
    # "Close all" could close only a subset and leave a naked short. group_id IS the
    # placement identity. (Overnight/positional groups covered too — no date window.)
    legs = order_store.open_legs_in_group(group_id)
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
                _write_json_atomic(TC_FILE, all_cfg, ensure_ascii=False)
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
        _write_json_atomic(TC_FILE, all_cfg, ensure_ascii=False)
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
                _write_json_atomic(TC_FILE, all_cfg, ensure_ascii=False)
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
        _write_json_atomic(TC_FILE, cfg)
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
    _write_json_atomic(TC_FILE, all_cfg, ensure_ascii=False)
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
            # event_driven (e.g. straddle_alert_hedged) ka bhi apna process NAHI
            # hota — wo on_option_alert hook se fire hoti hai (api_start +
            # auto_scheduler dono ise skip karte hain). Warna error_watch har
            # loop "config ACTIVE par process nahi" ka JHOOTHA alert baja deta.
            actives = [k for k, v in cfg.items()
                       if isinstance(v, dict) and v.get("active")
                       and not v.get("event_driven") and _base(k) in STRATEGIES]
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
    _exp_cache = {}   # sec_id -> expiry date (memoise across rows; get_expiry_for_sec_id scans full cache)
    for t in (trades or []):
        # Crypto (Delta) legs: NOT in the Dhan scrip-master — skip lot_size/expiry
        # lookups (each is a full 26MB-cache O(n) SCAN that finds nothing = ~0.25s
        # per leg → 2s+ page stall) and the NSE SPAN-margin queue. Cheap fields only.
        _sid0 = str(t.get('sec_id') or '')
        if (t.get('broker') == 'delta' or t.get('segment') == 'crypto'
                or (('-BTC-' in _sid0 or '-ETH-' in _sid0) and _sid0[:2] in ('C-', 'P-'))):
            t['lot_size'] = 1   # crypto = per-lot (0.001 BTC); qty already in lots
            if not lot_only:
                try:
                    ep = float(t.get('entry_price') or 0); q = float(t.get('qty') or 0)
                    if ep > 0 and q > 0:
                        # BUY = premium debit; SELL ≈ portfolio-margin (~0 under Portfolio)
                        t['margin'] = round(ep * q, 2) if str(t.get('entry')).upper() == 'BUY' else 0
                        t['margin_est'] = True
                except Exception:
                    pass
            continue
        try:
            lot = _dm.get_lot_size_by_sec_id(t.get('sec_id')) if _dm else None
            if lot:
                t['lot_size'] = int(lot)
        except Exception:
            pass
        # DTE = days-to-expiry AT ENTRY (gamma proxy for options — lower = higher gamma).
        # expiry − entry_date; consistent for live intraday (entry_date=today) and history.
        try:
            sid = t.get('sec_id')
            if _dm and sid:
                if sid not in _exp_cache:
                    _exp_cache[sid] = _dm.get_expiry_for_sec_id(sid)
                exp = _exp_cache[sid]
                if exp:
                    ed = t.get('entry_date') or t.get('date')
                    from datetime import datetime as _dtd
                    base = _dtd.strptime(ed[:10], '%Y-%m-%d').date() if ed else None
                    if base:
                        t['dte'] = (exp - base).days
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
        # 90-day (not 7) lookback: multi-day netting must capture BOTH legs of a
        # round-trip to pair them. A 7-day window SPLIT a closed round-trip whose
        # ENTRY was >7d old but EXIT ≤7d (e.g. VRPC entered 07-22 / exited 07-23,
        # viewed 07-30) → its exit legs showed as phantom "open". Wider window is
        # strictly more correct (genuinely-open legs still remain net-open; the
        # allow_overnight filter keeps stale intraday ghosts out) and also fixes
        # multi-week positional-equity holds (distma) that a 7-day window dropped.
        _lb_from = (ist - timedelta(days=90)).strftime('%Y-%m-%d')
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
    # ── Broker-truth pass over the DISPLAYED open legs (DISPLAY-ONLY) ────────
    # The day-scoped view + the allow_overnight allow-list get this wrong in both
    # directions (see _leg_alive_at_broker). Reconcile what we SHOW against the
    # account's real position book: drop only on a confident "not held", add only
    # on a confident "held". Broker unreachable -> nothing changes.
    try:
        _snap, _ok = _broker_open_snapshot()
        _kept, _dropped = [], []
        for _p in (data.get('open') or []):
            if _leg_alive_at_broker(_p, _snap, _ok) is False:
                _dropped.append(_p.get('sym'))
            else:
                _kept.append(_p)
        data['open'] = _kept
        _key = lambda _q: (str(_q.get('sec_id') or ''), str(_q.get('entry') or ''),
                           str(_q.get('entry_date') or ''), str(_q.get('strategy') or ''))
        _seen = {_key(_p) for _p in data['open']}
        _added = 0
        for _p in (_rng.get('open') or []):
            if _key(_p) in _seen:
                continue
            if _leg_alive_at_broker(_p, _snap, _ok) is not True:
                continue                       # only ADD on a confident yes
            if (_p.get('entry_date') or date) < date:
                _p['carried_over'] = True
            data.setdefault('open', []).append(_p)
            _seen.add(_key(_p))
            _added += 1
        if _dropped or _added:
            print(f"[open-truth] {date}: dropped {len(_dropped)} dead leg(s) {_dropped}, "
                  f"added {_added} broker-held leg(s) the day view missed", flush=True)
    except Exception as _bt:
        print(f"[open-truth] pass skipped ({_bt}) — DB-only view", flush=True)

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
            # Crypto (Delta): NEVER route through position_margin — it calls Kite/Dhan
            # SPAN on a Delta symbol that resolves to nothing and retries/times out
            # (measured ~3.3s PER LEG → 13s+ page stall). Cheap margin instead; the
            # per-group Delta margin (real, ~portfolio) is handled below.
            if p.get('broker') == 'delta' or p.get('segment') == 'crypto':
                ep = float(p.get('entry_price') or 0); q = float(p.get('qty') or 0)
                p['margin_used'] = round(ep * q, 2) if str(p.get('entry')).upper() == 'BUY' else 0
                continue
            try:
                # Real executing-broker margin: SELL → actual SPAN+exposure via
                # broker_real_margin (Kite order_margins / Dhan calculator); BUY →
                # premium paid. Falls back to the multiplier only if the API fails.
                # (Was crude qty*price*multiplier, which under-showed SELL margin.)
                p['margin_used'] = round(_rg.position_margin([p], _rc), 2)   # single margin gate
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
        # Keyed by GROUP_ID (placement batch) — the UI groups Open Positions by
        # group_id now (legs placed together = one group), so the group TOTAL /
        # header 💰 must be that PLACEMENT's real hedged basket margin, not a
        # per-strategy sum. position_margin() gives the SPAN-benefit basket for the
        # actual structure (2-leg vertical / 4-leg condor), which is far below the
        # sum of each leg's standalone margin. Key form matches the frontend's:
        # group_id if present, else 'solo_<id>'.
        try:
            _grp = {}
            for p in data.get('open', []):
                if 'CAPITAL_BLOCKED' in (p.get('tags') or []):
                    continue
                _gk = str(p.get('group_id') or '').strip() or ('solo_' + str(p.get('id')))
                _grp.setdefault(_gk, []).append(p)
            data['group_margin'] = {}
            for gk, rows in _grp.items():
                _stand = round(sum(float(r.get('margin_used') or 0) for r in rows), 2)
                # crypto groups: skip position_margin (Kite/Dhan SPAN on Delta syms =
                # ~13s stall) — use the cheap standalone sum (Portfolio margin ≈ that).
                if any(r.get('broker') == 'delta' or r.get('segment') == 'crypto' for r in rows):
                    data['group_margin'][gk] = {"hedged": _stand, "standalone": _stand}
                else:
                    data['group_margin'][gk] = {
                        "hedged": round(_rg.position_margin(rows, _rc), 2),
                        "standalone": _stand}
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
        # ALL-strategies view (no strategy filter) → CHRONOLOGICAL per-contract netting so
        # the daily total matches the broker (Zerodha) tradebook on multi-day positional
        # positions (a strategy leg + its broker_reconcile mirror pair in time order, not by
        # source-group which dumped a hedged straddle's whole round-trip onto its exit day →
        # phantom +₹12k on one cell). Single-strategy filter → strategy-aware _net_rows
        # (attribution + TRAP #145 cross-strategy protection). _net_rows itself is untouched.
        if _sm is None:
            rng = order_store.trades_for_range_chrono(net_lo, net_hi, **filt)
        else:
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



@app.route('/api/health/app-vs-broker')
def api_health_app_vs_broker():
    """Does the app's picture of open positions match Zerodha's? ONE line, always
    visible in the header (static/js/health-pill.js).

    WHY (TRAP #191): invariant_guard ALREADY caught the 2026-08-29 mismatch — its
    RED sat unread in a bell showing "99+" while the user was told, on screen, a
    position he was really carrying did not exist. A guard nobody can hear is not
    a guard. So its verdict gets one unmissable pill instead.

    Reads ONLY the status file the guard writes each cycle (~120s from
    pos_monitor_loop) — a page render must never cost a broker call. A verdict
    older than 20 min reports `stale`, never a false green: "we have not checked
    recently" and "everything is fine" must never look the same."""
    import json as _json
    from pathlib import Path as _P
    out = {"state": "unknown", "red": 0, "items": [], "ts": None}
    try:
        f = _P(BASE_DIR) / "data" / "invariant_status.json"
        if not f.exists():
            return jsonify(out)
        d = _json.loads(f.read_text() or "{}")
        out["ts"] = d.get("ts")
        out["red"] = int(d.get("red") or 0) + int(d.get("unknown") or 0)
        out["items"] = d.get("items") or []
        age_min = None
        try:
            t = datetime.strptime(str(d.get("ts")), "%Y-%m-%d %H:%M:%S")
            now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
            age_min = (now - t).total_seconds() / 60.0
        except Exception:
            pass
        if age_min is not None and age_min > 20:
            out["state"] = "stale"
            out["age_min"] = round(age_min)
        elif out["red"]:
            out["state"] = "mismatch"
        else:
            out["state"] = "ok"
    except Exception as e:
        out["state"] = "unknown"
        out["err"] = str(e)
    return jsonify(out)

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


@app.route('/api/registry-economics')
def api_registry_economics():
    """Per-run LOT-INDEPENDENT economics (gross_per_lot, flat_charge, per_lot_charge,
    capital_per_lot, ...) so /registry2 can rescale Net/Tax/Capital/ROC to any lot count
    client-side. Display-only; reuses registry_economics (backtest_calendar + charges)."""
    try:
        import registry_economics as re
        return jsonify({'ok': True, 'econ': re.all_economics()})
    except Exception as e:
        print("[registry-economics] fail:", e, flush=True)
        return jsonify({'ok': False, 'econ': {}, 'error': str(e)})


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


@app.route('/api/basket-note', methods=['GET', 'POST'])
def api_basket_note():
    """User's own comment on an option BASKET (pair) in the Completed Trades
    'Pair / Basket' view. Keyed by the basket's stable key (group_id or entry-time
    cluster). Display/notes only — no order/risk path."""
    import basket_notes
    if request.method == 'GET':
        try:
            return jsonify({'ok': True, 'notes': basket_notes.all_notes()})
        except Exception as e:
            print("[basket-note list] fail:", e, flush=True)
            return jsonify({'ok': False, 'notes': {}, 'error': str(e)})
    body = request.get_json(silent=True) or {}
    try:
        from datetime import datetime as _dt, timezone as _tz, timedelta as _tdd
        ts = (_dt.now(_tz.utc) + _tdd(hours=5, minutes=30)).strftime('%Y-%m-%d %H:%M')
        note = basket_notes.set_note(body.get('key'), body.get('text'), ts=ts)
        return jsonify({'ok': True, 'note': note})
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


def _delta_charges(qty, sym):
    """₹ round-trip Delta (crypto) commission for one option leg — taker ≈ 0.03%
    of NOTIONAL per side, in INR (NOT Zerodha STT/brokerage). Notional from the
    strike in the Delta symbol. Mirrors the client-side calcCharges crypto branch."""
    try:
        parts = str(sym).split("-")
        K = float(parts[2])
        cv = 0.001 if "-BTC-" in sym else 0.01
        return 0.0003 * (K * cv) * 85.0 * float(qty) * 2.0
    except Exception:
        return 0.0


def _zerodha_charges(entry_px, exit_px, qty, entry_side, when=None, sym=None):
    """₹ round-trip charges for one option leg, at the rates in force on `when`
    (the trade's ENTRY date). Crypto (Delta) legs → Delta commission model."""
    if sym and ("-BTC-" in str(sym) or "-ETH-" in str(sym)) and str(sym)[:2] in ("C-", "P-"):
        return _delta_charges(qty, sym)
    if not entry_px or not exit_px or not qty:
        return 0.0
    return _zerodha_charges_nse(entry_px, exit_px, qty, entry_side, when=when)


def _zerodha_charges_nse(entry_px, exit_px, qty, entry_side, when=None):
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
                               when=t.get('entry_date'), sym=t.get('sym'))
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
                            if isinstance(cfg[key], dict) and cfg[key].get("event_driven"):
                                continue  # fired from a dashboard hook (e.g. straddle_alert_hedged
                                          # via on_option_alert), NOT a launchable process — its
                                          # _base() would resolve to the WRONG trader script.
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
                            if isinstance(cfg[key], dict) and cfg[key].get("event_driven"):
                                continue  # event-driven (dashboard hook), not a process to stop
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

def _last_closed_candle_close(sec_id, seg, tf_min=1):
    """Close price of the most recently CLOSED candle (not the still-forming one)
    — used by the CANDLE_CLOSE SL/TP trigger type. Cached (30s TTL) to avoid
    hammering Dhan's intraday-candle endpoint every pos_monitor_loop tick (same
    DH-904 rate-limit concern already documented elsewhere in this codebase).

    tf_min>1 (Trade Manager's candle-close exit confirmation) buckets the SAME
    1-min fetch into tf_min groups instead of issuing another Dhan call, so a 5m
    close costs exactly what a 1m close costs. Plain epoch modulo is safe for
    1/3/5/15m because IST is UTC+5:30 and 330 is divisible by each of them, so
    bucket edges land on real IST candle boundaries. tf_min=1 (every pre-existing
    caller) walks the identical path as before."""
    import time as _t
    tf_min = max(1, int(tf_min or 1))
    ck = "%s:%d" % (sec_id, tf_min)
    cached = _candle_close_cache.get(ck)
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
        if tf_min > 1:
            # last FULLY-closed tf_min bucket; its close = close of the last 1-min
            # bar inside it. A bucket still in progress is skipped, exactly like
            # the still-forming 1-min bar above.
            span = tf_min * 60
            done = [i for i in closed_idx if (int(timestamps[i]) - int(timestamps[i]) % span) + span <= now_epoch]
            if not done:
                return None
            b_start = int(timestamps[done[-1]]) - int(timestamps[done[-1]]) % span
            in_bucket = [i for i in done if int(timestamps[i]) - int(timestamps[i]) % span == b_start]
            last_close = float(closes[in_bucket[-1]])
        else:
            last_close = float(closes[closed_idx[-1]])
        _candle_close_cache[ck] = (last_close, _t.time())
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

    # Startup zombie guard — a restart on a new day won't hit the rollover branch
    # (it's seeded to today), so sweep once here too (TRAP #188).
    try:
        _sweep_zombie_state(log=lambda m: print(m, flush=True))
    except Exception as _ze:
        print(f"[zombie-sweep] startup sweep err: {_ze}", flush=True)

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
                    # DAILY zombie guard — clear any stale exit-rule + flag any
                    # intraday leg carried from a prior day (TRAP #188). Runs on the
                    # first cycle of every new trading day.
                    try:
                        _sweep_zombie_state(log=lambda m: print(m, flush=True))
                    except Exception as _ze:
                        print(f"[zombie-sweep] rollover sweep err: {_ze}", flush=True)

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


def _release_exit_claim(p, sec_id, exit_side):
    """Free the cross-engine exit dedup claim (LESSONS #176) when a live close
    order did NOT place, so the next 5s cycle can retry this leg instead of being
    blocked by our own just-taken claim. No-op if exit_claim is unavailable."""
    try:
        import exit_claim
        exit_claim.release(p.get("strategy"), sec_id, exit_side, p.get("mode"))
    except Exception:
        pass


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
    # PAPER positions have no broker book — order_store IS the authoritative
    # flatness signal for them. Treat paper exactly like real: a manual close (or
    # any earlier exit) that already netted a paper leg flat must suppress a
    # redundant squareoff, else the closing order OPENS a phantom opposite
    # position. (2026-07-24: RELIANCE paper was manual-closed at 09:21:35, the RMS
    # daily-profit-target squareoff 20s later STILL fired a redundant BUY → phantom
    # LONG → closed again 09:30, inflating P&L. Root: this guard early-returned
    # False for every non-live position, skipping the flat-check entirely.)
    # _my_open_qty==0 is CONFIDENT-flat (needs positive closed-round-trip evidence,
    # never a bare "order_store doesn't know"), so a genuinely-open paper leg still
    # exits normally. Do NOT mark_externally_closed here — the leg is already
    # properly netted flat; marking the entry would unpair it into a new phantom.
    if p.get("mode") != "live":
        try:
            import broker_sync as _bsync
            if _bsync._my_open_qty(p.get("strategy") or "", str(sec_id or ""),
                                   p.get("sym") or "") == 0:
                _closed_ids.add(p.get("id"))
                log(f"[{exit_reason}] PRE-EXIT CHECK: {p.get('sym')} paper leg already "
                    f"FLAT in order_store (manual close?) — skipping redundant exit.", flush=True)
                return True
        except Exception as _pe:
            log(f"[{exit_reason}] paper pre-exit flat-check failed ({_pe}) — proceeding", flush=True)
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
        exit_side = "SELL" if p["entry"] == "BUY" else "BUY"

        # ── Cross-engine exit dedup (LESSONS #176) — auto_straddle_loop's
        # basket-exit (GROUP_SL/TARGET) and this pos_monitor squareoff both run in
        # this process and can each fire a close on the same (strategy, contract,
        # side) within seconds (broker fill lags → is_flat_fresh can't catch it) →
        # a phantom naked leg + extra flatten order + wasted tax. If another engine
        # already claimed THIS leg, don't re-fire its order — but STILL cascade to
        # the group siblings below (each has its own claim), so the group still
        # closes fully.
        _won_claim = True
        try:
            import exit_claim
            _won_claim = exit_claim.claim(p.get("strategy"), sec_id, exit_side, p.get("mode"))
        except Exception:
            _won_claim = True
        if not _won_claim:
            print(f"[{exit_reason}] {p['sym']} {exit_side} exit already in-flight elsewhere — "
                  f"not re-firing (dup guard), still checking group siblings", flush=True)
            _closed_ids.add(p.get("id"))
            gid = p.get("group_id")
            if gid:
                # shorts-first (see the matching cascade in the normal-close branch)
                for sib in sorted(open_pos, key=lambda s: 0 if str(s.get("entry", "")).upper() == "SELL" else 1):
                    sib_id = sib.get("id")
                    if sib_id in _closed_ids or sib is p:
                        continue
                    if sib.get("group_id") != gid:
                        continue
                    sib_sec = sib.get("sec_id")
                    if not sib_sec:
                        continue
                    sib_seg = "NSE_EQ" if (sib.get("instrument") or "").upper() == "EQUITY" else "NSE_FNO"
                    _feed_subscribe([(sib_seg, sib_sec)])
                    qsib = dhan_feed.get_quote(sib_sec, max_age=_FEED_MAX_AGE)
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
                        _pgc_queue(sib, sib_sec, exit_reason + "_GROUP")
            return True
        print(f"[{exit_reason}] {p['sym']} LTP {ltp}. Squaring off...")

        if p.get("mode") == "live":
            import smart_order
            from brokers import get_broker
            broker = get_broker(p.get("broker") or "dhan")
            # PRODUCT MATCH (TRAP #178) — square off in the SAME product the broker
            # holds, else Kite opens a new opposite position instead of netting.
            # fallback=None → smart_order default MIS (this path usually closes MIS
            # strategy legs; no regression when the live read can't resolve).
            _sq_prod = _broker_position_product(broker, p.get("broker") or "dhan",
                                                p["sym"], sec_id, fallback=None)
            try:
                res = smart_order.execute(
                    exit_side, p["sym"], sec_id, seg, p["qty"], p["sym"],
                    p["mode"], broker, log=print, tag="POSMON",
                    source=p["source"], strategy=p["strategy"],
                    instrument=p["instrument"], broker_name=p.get("broker") or "dhan",
                    # ROOT FIX (2026-08-28, TRAP zombie-rule): stamp the exit with its
                    # OWN group_id. Without this the EOD/SL squareoff recorded exits with
                    # a BLANK group_id → invisible to order_store.open_legs_in_group() →
                    # the group stayed "net-open" in that view even though the broker was
                    # flat → the group's ±basket position_exit_rule never auto-cleared,
                    # survived overnight, and RE-FIRED next day, churning a closed
                    # position with real orders (some rejected → orphan legs).
                    group_id=p.get("group_id") or "",
                    extra_tags=["pos_monitor_exit", exit_reason],
                    product=_sq_prod,
                    is_exit=True,
                )
            except Exception as _ex:
                # A network/API exception here must NOT propagate up and abort
                # this position's check (the per-position try/except in the
                # caller would catch it too, but explicit here documents the
                # intent: never mark closed unless we know the broker round-
                # tripped — leave it open, it retries next 5s cycle.
                print(f"[{exit_reason}] LIVE square-off EXCEPTION for {p['sym']} — {_ex}; leaving position open, will retry")
                _release_exit_claim(p, sec_id, exit_side)   # free claim so next cycle retries
                return True
            if not res.get("ok"):
                print(f"[{exit_reason}] LIVE square-off FAILED for {p['sym']} — {res.get('reason')}; leaving position open, will retry")
                _release_exit_claim(p, sec_id, exit_side)   # free claim so next cycle retries
                return True
            # smart_order.execute already persisted the trade — don't double-record.
        else:
            order_store.record(
                side=exit_side, qty=p["qty"], price=ltp, source=p["source"],
                strategy=p["strategy"], mode=p["mode"], broker=p["broker"],
                symbol=p["sym"], instrument=p["instrument"], trad_sym=p["sym"],
                sec_id=sec_id, segment=seg, status=p.get("status", "paper"),
                group_id=p.get("group_id") or "",   # ROOT FIX (2026-08-28) — see live branch above
                tags=["pos_monitor_exit", exit_reason]
            )

        # ── Group-aware: a hedge SELL+BUY pair (or any group_id'd
        # legs) must close together — close any open sibling now,
        # tagged so it's clear it followed automatically, not its
        # own independent SL/TP/EOD hit.
        _closed_ids.add(p.get("id"))
        gid = p.get("group_id")
        if gid:
            # ORDERED (2026-08-28): close SHORT siblings (SELL-entry → buy-to-close)
            # BEFORE BUY-entry wings. Stripping a hedge wing first spikes the naked
            # margin → the broker rejects the remaining exits (user-reported "pehle
            # sell nikalo, fir buy"). Same shorts-first ordering execute_basket_exit
            # already enforces; this brings the pos_monitor EOD/SL group-close in line.
            for sib in sorted(open_pos, key=lambda s: 0 if str(s.get("entry", "")).upper() == "SELL" else 1):
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
        _skip_why = ""
        try:
            _owner = p.get("strategy") or ""
            if bool(_owner) and _rg.allow_overnight(_owner):
                _skip_eod = True
                _skip_why = f"strategy {_owner} allow_overnight=True"
                # Bounded positional: hold only up to max_hold_days trading days,
                # then let the EOD squareoff fire (backstop to the trader's own
                # deadline exit). Enter day D, elapsed==max_hold -> square off.
                _mh = _rg.max_hold_days(_owner)
                if _mh is not None:
                    try:
                        import market_calendar as _mc
                        _elapsed = _mc.trading_days_between(p.get("entry_date"))
                        if _elapsed >= _mh:
                            _skip_eod = False
                            _skip_why = f"strategy {_owner} max_hold {_mh}d reached (elapsed {_elapsed}) — EOD squareoff"
                        else:
                            _skip_why = f"strategy {_owner} positional (held {_elapsed}/{_mh}d)"
                    except Exception:
                        pass    # on any calendar error, keep skipping (trader owns the deadline)
        except Exception:
            _skip_eod = False
        # Per-position MIS->NRML carry (user toggle) — GROUP-WIDE, day-scoped. Even a
        # 'manual' position (not an allow_overnight strategy) can be held past 3:15 if
        # the user flipped it to NRML in Open Positions. PAPER: just skips the EOD
        # squareoff (expiry/ITM/RMS/SL guards stay active). Default (no flag) = MIS =
        # squared here exactly as before, so any position not toggled is untouched.
        if not _skip_eod:
            try:
                import position_carry
                if position_carry.is_carried(p.get("group_id"), p.get("id")):
                    _skip_eod = True
                    _skip_why = "MIS->NRML carry (user toggle)"
            except Exception:
                pass
        # Respect a manual MIS->NRML conversion done DIRECTLY on Zerodha (no app
        # toggle): if the broker holds this leg as NRML, the user chose to carry it
        # overnight → don't force-square it at 3:15. LIVE only. NOT for allow_overnight
        # strategies — their max_hold logic above is authoritative (a positional leg
        # is NRML by design and must still exit at max_hold, not be held forever here).
        if (not _skip_eod) and p.get("mode") == "live" and (p.get("broker") == "kite") \
                and not (bool(p.get("strategy")) and _rg.allow_overnight(p.get("strategy"))):
            try:
                from brokers import get_broker
                _bk = get_broker("kite")
                _ksym = _bk.resolve_symbol(p["sym"], sec_id)
                if _ksym and _ksym in _broker_nrml_syms(_bk, "kite"):
                    _skip_eod = True
                    _skip_why = "broker product = NRML (manual carry on Zerodha)"
            except Exception:
                pass
        if _skip_eod:
            print(f"[pos_monitor] {p.get('sym')} held OVERNIGHT — {_skip_why}, non-expiry "
                  f"day (EOD 3:15 squareoff skipped; expiry/ITM/RMS guards still active)",
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
    # Daily housekeeping: RMS-rejection LOG rows ('blocked') accumulate forever
    # (a capped signal-heavy strategy logs 6-10/day) → prune old ones. Runs on the
    # daily 07:00 restart. DELETE-based (blocked rows are excluded from netting →
    # zero P&L risk); keeps 7 days for review. Display/data-only.
    try:
        import order_store as _os_purge
        _npb = _os_purge.purge_old_blocked(7)
        if _npb:
            print(f"   🧹 purged {_npb} old blocked-log rows (>7 days)\n")
    except Exception as _pe:
        print("   ⚠️ blocked-log purge skipped:", _pe, "\n")
    # Har error 🔔 tak — strategy logs + mari hui strategies + gire services.
    _threading.Thread(target=_error_watch_loop, daemon=True).start()
    print("   🔔 error-watch chalu — logs/processes/services ke errors bell me aayenge\n")
    _payoff_warm_start()   # open groups ka payoff/zone pehle se cache — panel instant khule
    print("   📊 payoff warm-loop chalu — open positions ka payoff pre-computed\n")
    _rms_warm_start()      # market-hours: rms-summary cache warm rakho — Risk tab instant khule
    print("   ⚡ rms-summary warm-loop chalu — Risk panel pehli baar bhi instant\n")
    # Warm the Dhan scrip-master cache (build_cache) at startup in the background so
    # the FIRST /api/orders doesn't pay ~2s to build the expiry index lazily (the
    # post-restart "pehli load slow" — also helps the daily 07:00 restart). Best-effort.
    def _warm_dhan_cache():
        try:
            import dhan_master as _dm
            _dm.build_cache()
            print("   🗂️ dhan scrip-master cache warmed — first orders load instant", flush=True)
        except Exception as _we:
            print("   ⚠️ dhan cache warm skipped:", _we, flush=True)
    _threading.Thread(target=_warm_dhan_cache, daemon=True).start()
    # threaded=True: one slow request (e.g. a cold option-chain CSV parse) must NOT block
    # the whole dashboard — single-threaded froze every other request/page for 20s+ (2026-08-05).
    app.run(host='0.0.0.0', port=5099, debug=False, threaded=True)
