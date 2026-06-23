#!/usr/bin/env python3
"""
health_check.py — Strategy "order bhej payegi ya nahi?" preflight.

Roz subah ki firefight band karne ke liye. Har strategy ke liye us poore
chain ko verify karta hai jo order lagne ke liye zaroori hai — BINA koi
real order bheje (100% read-only, paper/live dono safe; market open ho ya band).

Check karta hai (jis order me ye subah fail hote hain):
  1. CONFIG    — nifty_config.json me key hai? active:true hai?
  2. SCRIPT    — is type ka live trader script hai? bina syntax-error compile hota?
  3. HEARTBEAT — logs/<id>.log recent hai? (process zinda + loop chal raha — pid se
                 zyada reliable, cross-platform; market band ho to skip)
  4. TOKEN     — Dhan JWT valid? KAB expire hoga? (sabse bada subah-killer)
  5. DATA      — strategy ke pehle symbol ka live LTP aata? (data path working)
  6. CONTRACT  — options strategy ho to ATM option contract resolve hota?
                 (order banane ki capability)

Sab green = strategy poori tarah order-ready hai; bas signal aane ki der hai.
Koi red = wahi aapki "order nahi laga" ki asli wajah — saaf dikh jaayega.

Usage:
  python health_check.py                 # saari ACTIVE strategies
  python health_check.py --id rsi_v1     # ek strategy
  python health_check.py --all           # config ki saari (inactive bhi)
  python health_check.py --all-symbols   # har symbol check karo (default: pehla)
  python health_check.py --loop 60       # har 60s repeat (live watch)

Exit code: koi active strategy RED ho to 1 (cron/loop me use karne ke liye).
"""

import sys, os, json, re, time, base64, argparse, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

# --- IPv4 force (project rule — VPS pe IPv6 se Dhan DH-905 deta) -------------
import socket
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

import requests

BASE_DIR    = Path(__file__).resolve().parent
TC_FILE     = BASE_DIR / "nifty_config.json"
CONFIG_FILE = BASE_DIR / "data" / "config.json"
LOGS_DIR    = BASE_DIR / "logs"
TRADERS_DIR = BASE_DIR / "_TRADERS"
IST         = timezone(timedelta(hours=5, minutes=30))

# strategy base-type -> live trader script (trader_dashboard.STRATEGIES ka mirror).
# Yahan na ho to "live trader script nahi" = order kabhi nahi lagega.
TRADER_SCRIPTS = {
    "ema":      TRADERS_DIR / "nifty_ema_trader.py",
    "rsi":      TRADERS_DIR / "rsi_trader.py",
    "rsi_v1":   TRADERS_DIR / "01_rsi_v1.py",
    "range":    TRADERS_DIR / "range_trader.py",
    "ARS_CHAIN": TRADERS_DIR / "range_trader.py",   # ARS_CHAIN = range engine
    "universe": TRADERS_DIR / "universe_trader.py",
}
# config-key prefix -> base type (trader_dashboard._base / STRATEGY_ALIASES jaisa)
def _base(strategy):
    if strategy.startswith("ARS_CHAIN"):
        return "ARS_CHAIN"
    if strategy.startswith("rsi_v1"):
        return "rsi_v1"
    return strategy.split("_")[0] if "_" in strategy else strategy

# index symbols -> (sec_id, segment); equity dhan_master.get_equity_info se
_IDX = {"NIFTY": ("13", "IDX_I"), "BANKNIFTY": ("25", "IDX_I")}

GREEN, RED, YEL, DIM, RST = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
OK, NO, WARN = f"{GREEN}OK{RST}", f"{RED}FAIL{RST}", f"{YEL}WARN{RST}"


def _load_creds():
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return cfg["jwt_token"], cfg["client_id"]


def _jwt_expiry(token):
    """JWT payload se exp nikaalo -> (expiry_datetime_IST, hours_left). Decode
    na ho to (None, None)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)          # base64 padding
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        if not exp:
            return None, None
        dt = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone(IST)
        hrs = (dt - datetime.now(IST)).total_seconds() / 3600
        return dt, hrs
    except Exception:
        return None, None


def _dhan_ltp(headers, seg, sec_id):
    """Ek LTP call. Price float ya None (+ error string)."""
    try:
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                          json={seg: [int(sec_id)]}, headers=headers, timeout=6)
        if r.status_code == 429:
            return None, "rate-limit (429) — thoda ruk ke retry"
        j = r.json()
        if j.get("status") == "failure" or "data" not in j:
            return None, str(j.get("remarks") or j)[:60]
        return float(j["data"][seg][str(sec_id)]["last_price"]), None
    except Exception as e:
        return None, str(e)[:60]


def _tf_seconds(tf):
    """'1m'/'5m'/'15m'/'1D' -> seconds. Heartbeat threshold isi se scale hota
    (5m strategy har 5 min log karti — use 3-min se RED mat karo)."""
    tf = str(tf or "1m").strip().lower()
    m = re.match(r"(\d+)\s*([mhd]?)", tf)
    if not m:
        return 60
    n = int(m.group(1)); unit = m.group(2) or "m"
    return n * {"m": 60, "h": 3600, "d": 86400}.get(unit, 60)


def _market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:                       # Sat/Sun
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 30


def _log_heartbeat(strategy_id):
    """(age_seconds, last_line) of logs/<id>.log ki last timestamped line.
    File na ho ya ts na mile to (None, msg)."""
    lf = LOGS_DIR / f"{strategy_id}.log"
    if not lf.exists():
        return None, "log file nahi (kabhi chali hi nahi?)"
    try:
        lines = lf.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        return None, str(e)[:50]
    for line in reversed(lines[-400:]):
        m = re.match(r"(\d{4}-\d{2}-\d{2})[ ,T](\d{2}:\d{2}:\d{2})", line)
        if m:
            ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
            age = (datetime.now(IST) - ts).total_seconds()
            return age, line.strip()[:90]
    return None, "log me koi timestamp line nahi"


def _compiles(script_path):
    """Script bina syntax-error compile hoti? (crash-on-start catch). Import nahi
    karte — woh Dhan/threads chalu kar dega; py_compile safe hai."""
    try:
        import py_compile
        py_compile.compile(str(script_path), doraise=True)
        return True, None
    except Exception as e:
        return False, str(e).splitlines()[0][:80]


def _check_webhook(sid, sc, active, rows):
    """Webhook strategy = TradingView-driven, koi subprocess/trader script nahi.
    Dashboard in-process /api/webhook/tv pe receive karta. Isliye 'script nahi'
    RED galat hai — iske liye: dashboard up hai? + last received alert kab aaya."""
    red = False
    # dashboard (jo webhook host karta) up hai? port 5099 listening check.
    up = False
    try:
        s = socket.create_connection(("127.0.0.1", 5099), timeout=2); s.close(); up = True
    except Exception:
        up = False
    if up:
        rows.append(("dashboard", OK, "port 5099 up — /api/webhook/tv receive kar sakta"))
    else:
        rows.append(("dashboard", NO, "port 5099 DOWN — webhook receive hi nahi hoga")); red = True
    if not sc.get("secret_token"):
        rows.append(("token", WARN, "secret_token set nahi — TV alert reject ho sakta"))
    mode = sc.get("mode", "paper")
    rows.append(("mode", OK, f"{mode} | TV-driven (alert aaye tabhi order — heartbeat N/A)"))
    age, line = _log_heartbeat(sid)
    if age is not None:
        rows.append(("last-alert", DIM + "i" + RST, f"aakhri webhook log {age/60:.0f} min pehle"))
    return rows, red


def check_strategy(sid, cfg, headers, spot_cache, args):
    """Ek strategy ka poora preflight. Returns (rows list of (name,status,detail), is_red)."""
    rows, red = [], False
    sc = cfg.get(sid, {})

    # 1. CONFIG / active
    active = bool(sc.get("active"))
    if not active:
        rows.append(("active", WARN, "active:false — scheduler isse launch NAHI karega"))
    else:
        rows.append(("active", OK, "active:true"))

    # webhook = TV-driven, subprocess nahi — alag raasta
    if _base(sid) == "webhook":
        return _check_webhook(sid, sc, active, rows)

    # 2. SCRIPT
    base = _base(sid)
    script = TRADER_SCRIPTS.get(base)
    if not script:
        rows.append(("script", NO, f"'{base}' ka live trader script nahi (backtest-only) — order kabhi nahi lagega"))
        return rows, True
    if not script.exists():
        rows.append(("script", NO, f"missing: {script.name}")); return rows, True
    ok, err = _compiles(script)
    if ok:
        rows.append(("script", OK, script.name))
    else:
        rows.append(("script", NO, f"compile error: {err}")); red = True

    # 3. HEARTBEAT — threshold timeframe ke hisaab se (5m strategy har 5 min log
    #    karti, use 3-min se RED nahi karna). 1 loop + thoda buffer.
    hb_limit = max(180, _tf_seconds(sc.get("timeframe")) * 1.5 + 90)
    age, line = _log_heartbeat(sid)
    if age is None:
        rows.append(("heartbeat", WARN if not active else NO, line)); red = red or active
    else:
        mins = age / 60
        if not _market_open():
            rows.append(("heartbeat", DIM + "—" + RST, f"market band; last log {mins:.0f} min pehle"))
        elif age <= hb_limit:
            rows.append(("heartbeat", OK, f"{age:.0f}s pehle loop chala"))
        elif active:
            rows.append(("heartbeat", NO, f"{mins:.0f} min se log nahi (limit {hb_limit/60:.0f}m) — process ATKA/MARA?")); red = True
        else:
            rows.append(("heartbeat", WARN, f"{mins:.0f} min se chup (inactive)"))

    # 4/5/6 ke liye underlying symbol(s)
    syms = sc.get("symbols") or ([sc["symbol"]] if sc.get("symbol") else [])
    syms = [s.upper() for s in syms]
    if not syms:
        rows.append(("symbols", NO, "config me koi symbol nahi")); return rows, True
    check_syms = syms if args.all_symbols else syms[:1]

    is_opt = (sc.get("instrument", "").lower() == "options")
    for sym in check_syms:
        # data sec_id resolve (local — koi API nahi)
        if sym in _IDX:
            sec_id, seg = _IDX[sym]
        else:
            try:
                import dhan_master
                info = dhan_master.get_equity_info(sym)
            except Exception as e:
                rows.append((f"data:{sym}", NO, f"dhan_master load fail: {str(e)[:40]}")); red = True; continue
            if not info:
                rows.append((f"data:{sym}", NO, "equity master me symbol nahi (scrip CSV refresh?)")); red = True; continue
            sec_id, seg = str(info[0]), info[1]

        # 5. DATA — ek LTP call (per-symbol dedupe via spot_cache)
        if sym in spot_cache:
            price, derr = spot_cache[sym], None
        else:
            price, derr = _dhan_ltp(headers, seg, sec_id)
            if price is not None:
                spot_cache[sym] = price
            time.sleep(1.1)   # Dhan marketfeed ~1 req/sec — 429 se bacho
        if price is None:
            rows.append((f"data:{sym}", NO, f"LTP nahi aaya: {derr}")); red = True; continue
        rows.append((f"data:{sym}", OK, f"LTP={price}"))

        # 6. CONTRACT — options ho to ATM resolve (local CSV; spot chahiye)
        if is_opt:
            try:
                import dhan_master
                off = int(sc.get("strike_offset", 0) or 0)
                atm = round(price / 50) * 50
                csec, ctrad, clot = dhan_master.get_option_contract(sym, atm, "CE", off)
                if ctrad:
                    rows.append((f"contract:{sym}", OK, f"{ctrad} (lot={clot})"))
                else:
                    rows.append((f"contract:{sym}", NO, f"ATM CE resolve nahi hua (ATM {atm})")); red = True
            except Exception as e:
                rows.append((f"contract:{sym}", NO, f"contract err: {str(e)[:45]}")); red = True

    return rows, red


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="ek strategy id (e.g. rsi_v1)")
    ap.add_argument("--all", action="store_true", help="config ki saari (inactive bhi)")
    ap.add_argument("--all-symbols", action="store_true", help="har symbol (default: pehla)")
    ap.add_argument("--loop", type=int, metavar="SEC", help="har SEC second repeat")
    args = ap.parse_args()

    def run_once():
        try:
            cfg = json.loads(TC_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{RED}nifty_config.json read fail: {e}{RST}"); return 1

        # strategy keys (webhooks/global jaise non-strategy keys skip)
        SKIP = {"webhooks"}
        keys = [k for k in cfg if k not in SKIP and isinstance(cfg[k], dict)]
        if args.id:
            keys = [args.id]
        elif not args.all:
            keys = [k for k in keys if cfg[k].get("active")]
        if not keys:
            print(f"{YEL}Koi active strategy nahi (--all se sab dekho).{RST}"); return 0

        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        mkt = f"{GREEN}OPEN{RST}" if _market_open() else f"{DIM}CLOSED{RST}"
        print(f"\n{'='*64}\n  CODE3B STRATEGY HEALTH CHECK   {now}   market={mkt}\n{'='*64}")

        # --- Shared: TOKEN validity + expiry (sabse bada subah-killer) -------
        try:
            token, client_id = _load_creds()
        except Exception as e:
            print(f"{RED}✗ creds load fail (data/config.json): {e}{RST}"); return 1
        headers = {"access-token": token, "client-id": client_id, "Content-Type": "application/json"}
        exp_dt, hrs = _jwt_expiry(token)
        nifty, terr = _dhan_ltp(headers, "IDX_I", "13"); time.sleep(1.1)
        if nifty is None:
            print(f"  TOKEN     {NO}  Dhan reject (token EXPIRED/galat?) — {terr}")
            print(f"  {RED}>> Dashboard Control tab me naya JWT paste karo. Bina iske KOI order nahi lagega.{RST}")
            token_red = True
        else:
            exp_s = exp_dt.strftime("%d-%b %H:%M") if exp_dt else "?"
            if hrs is not None and hrs < 2:
                print(f"  TOKEN     {WARN}  valid par {hrs:.1f}h me expire ({exp_s}) — NIFTY={nifty}")
            else:
                print(f"  TOKEN     {OK}  valid, expiry {exp_s} ({hrs:.0f}h baaki) — NIFTY={nifty}")
            token_red = False

        spot_cache = {"NIFTY": nifty} if nifty else {}
        any_red = token_red
        for sid in keys:
            rows, red = check_strategy(sid, cfg, headers, spot_cache, args)
            if token_red:
                red = True
            any_red = any_red or red
            tag = f"{RED}● RED{RST}" if red else f"{GREEN}● READY{RST}"
            print(f"\n  ── {sid}  {tag}")
            for name, status, detail in rows:
                print(f"       {name:<14} {status:<14} {DIM}{detail}{RST}")

        print(f"\n{'='*64}")
        if any_red:
            print(f"  {RED}NATIJA: kuch RED hai — upar dekho, wahi order na lagne ki wajah.{RST}")
        else:
            print(f"  {GREEN}NATIJA: sab READY — strategies order bhej sakti hain, bas signal ki der.{RST}")
        print(f"{'='*64}\n")
        return 1 if any_red else 0

    if args.loop:
        try:
            while True:
                run_once()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\nbye.")
            return 0
    else:
        return run_once()


if __name__ == "__main__":
    sys.exit(main())
