#!/usr/bin/env python3
"""
health_check.py — Strategy "order bhej payegi ya nahi?" preflight.

Roz subah ki firefight band karne ke liye. Har strategy ke liye us poore chain
ko verify karta hai jo order lagne ke liye zaroori hai — BINA koi real order
bheje (read-only preflight; paper/live dono safe, market open ho ya band).

Preflight checks (jis order me ye subah fail hote hain):
  1. CONFIG    — nifty_config.json me key hai? active:true hai?
  2. SCRIPT    — is type ka live trader script hai? bina syntax-error compile hota?
  3. HEARTBEAT — logs/<id>.log recent hai? (process zinda + loop chal raha;
                 threshold timeframe-aware — 5m strategy ko 3-min se RED nahi)
  4. TOKEN     — Dhan JWT valid? KAB expire hoga? (sabse bada subah-killer)
  5. DATA      — strategy ke pehle symbol ka live LTP aata? (data path working)
  6. CONTRACT  — options strategy ho to ATM option contract resolve hota?
  Webhook strategies = TV-driven (subprocess nahi) → dashboard-up check.

--fire-test (PAPER, market band hone ke baad): har active strategy ka asli order
path chala ke confirm karta hai ki order trade DB me LAND hota hai — phir test
rows delete kar deta (production P&L kabhi pollute nahi hota). "Full proof".

Usage:
  python health_check.py                  # active strategies (pretty)
  python health_check.py --id rsi_v1      # ek strategy
  python health_check.py --all            # config ki saari (inactive bhi)
  python health_check.py --all-symbols    # har symbol (default: pehla)
  python health_check.py --json           # machine-readable (scheduler use karta)
  python health_check.py --fire-test      # paper test-fire (market band pe; --force se kabhi bhi)
  python health_check.py --loop 60        # har 60s repeat (live watch)

Exit code: koi active strategy RED ho to 1 (cron/--loop/scheduler me use karne ke liye).
"""

import sys, os, json, re, time, base64, argparse
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
TEST_SOURCE = "healthtest"     # --fire-test ke orders ka source (cleanup isse hota)

# strategy base-type -> live trader script (trader_dashboard.STRATEGIES ka mirror).
TRADER_SCRIPTS = {
    "ema":       TRADERS_DIR / "nifty_ema_trader.py",
    "rsi":       TRADERS_DIR / "rsi_trader.py",
    "rsi_v1":    TRADERS_DIR / "01_rsi_v1.py",
    "range":     TRADERS_DIR / "range_trader.py",
    "ARS_CHAIN": TRADERS_DIR / "range_trader.py",   # ARS_CHAIN = range engine
    "universe":  TRADERS_DIR / "universe_trader.py",
}
def _base(strategy):
    if strategy.startswith("ARS_CHAIN"):
        return "ARS_CHAIN"
    if strategy.startswith("rsi_v1"):
        return "rsi_v1"
    return strategy.split("_")[0] if "_" in strategy else strategy

_IDX = {"NIFTY": ("13", "IDX_I"), "BANKNIFTY": ("25", "IDX_I")}

# --- status tokens + colorizer (JSON me plain token, terminal me rang) -------
GREEN, RED, YEL, DIM, RST = "\033[92m", "\033[91m", "\033[93m", "\033[90m", "\033[0m"
_COLOR = {"OK": GREEN, "FAIL": RED, "WARN": YEL, "INFO": DIM, "SKIP": DIM}
def _c(status):
    return f"{_COLOR.get(status,'')}{status}{RST}"


def _load_creds():
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return cfg["jwt_token"], cfg["client_id"]


def _jwt_expiry(token):
    """JWT payload se exp -> (expiry_datetime_IST, hours_left). Decode na ho to (None,None)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        if not exp:
            return None, None
        dt = datetime.fromtimestamp(exp, tz=timezone.utc).astimezone(IST)
        hrs = (dt - datetime.now(IST)).total_seconds() / 3600
        return dt, hrs
    except Exception:
        return None, None


def _dhan_ltp(headers, seg, sec_id, retries=2):
    """Ek LTP call. (price_float, err_or_None). Err 'AUTH:' se shuru = token galat/expired
    (real fail); 'TRANSIENT:' = 429/network (retry ke baad bhi na mila — RED mat karo).
    429/network pe retries baar tak retry karta (live dashboard bhi Dhan poll karta —
    ek transient se health-check ko jhootha RED nahi hona chahiye)."""
    last = "TRANSIENT: unknown"
    for attempt in range(retries + 1):
        try:
            r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                              json={seg: [int(sec_id)]}, headers=headers, timeout=6)
            if r.status_code in (401, 403):
                return None, f"AUTH: {r.status_code} token reject"
            if r.status_code == 429:
                last = "TRANSIENT: rate-limit (429)"
                time.sleep(1.3); continue
            j = r.json()
            if j.get("status") == "failure" or "data" not in j:
                rem = str(j.get("remarks") or j).lower()
                if any(k in rem for k in ("token", "unauthor", "invalid", "expire", "dh-901", "dh-808")):
                    return None, f"AUTH: {str(j.get('remarks') or j)[:50]}"
                last = f"TRANSIENT: {str(j.get('remarks') or j)[:50]}"
                time.sleep(1.0); continue
            return float(j["data"][seg][str(sec_id)]["last_price"]), None
        except Exception as e:
            last = f"TRANSIENT: {str(e)[:50]}"
            time.sleep(1.0)
    return None, last


def _tf_seconds(tf):
    """'1m'/'5m'/'15m'/'1D' -> seconds (heartbeat threshold scaling)."""
    tf = str(tf or "1m").strip().lower()
    m = re.match(r"(\d+)\s*([mhd]?)", tf)
    if not m:
        return 60
    n = int(m.group(1)); unit = m.group(2) or "m"
    return n * {"m": 60, "h": 3600, "d": 86400}.get(unit, 60)


def _symbols(sc):
    """Config se symbol list — `symbols` list ya string dono handle (VPS config me
    kabhi-kabhi `symbols` ek string hota hai, list nahi → char-by-char tootne se bacho)."""
    s = sc.get("symbols")
    if not s:
        s = sc.get("symbol")
    if not s:
        return []
    if isinstance(s, str):
        s = [p for p in re.split(r"[,\s]+", s) if p]   # "NIFTY,BANKNIFTY" / "NIFTY BANKNIFTY"
    return [str(x).upper() for x in s]


def _market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 30


def _log_heartbeat(strategy_id):
    """(age_seconds, last_line) of logs/<id>.log last timestamped line, ya (None, msg)."""
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
            return (datetime.now(IST) - ts).total_seconds(), line.strip()[:90]
    return None, "log me koi timestamp line nahi"


def _compiles(script_path):
    """Script bina syntax-error compile hoti? (crash-on-start catch; import nahi karte)."""
    try:
        import py_compile
        py_compile.compile(str(script_path), doraise=True)
        return True, None
    except Exception as e:
        return False, str(e).splitlines()[0][:80]


def _resolve_target(sym, sc, headers, spot_cache):
    """Ek symbol ka order target resolve karo. Returns dict ya {'error':...}.
    {sec_id, seg, trad_sym, qty, price, is_opt}"""
    is_opt = (sc.get("instrument", "").lower() == "options")
    if sym in _IDX:
        sec_id, seg = _IDX[sym]
    else:
        try:
            import dhan_master
            info = dhan_master.get_equity_info(sym)
        except Exception as e:
            return {"error": f"dhan_master load fail: {str(e)[:40]}"}
        if not info:
            return {"error": "equity master me symbol nahi (scrip CSV refresh?)"}
        sec_id, seg = str(info[0]), info[1]

    if sym in spot_cache:
        price, derr = spot_cache[sym], None
    else:
        price, derr = _dhan_ltp(headers, seg, sec_id)
        if price is not None:
            spot_cache[sym] = price
        time.sleep(1.1)   # Dhan marketfeed ~1 req/sec
    if price is None:
        return {"error": derr or "LTP nahi aaya"}

    out = {"sec_id": sec_id, "seg": seg, "trad_sym": sym, "qty": int(sc.get("qty", 1) or 1),
           "price": price, "is_opt": is_opt}
    if is_opt:
        try:
            import dhan_master
            off = int(sc.get("strike_offset", 0) or 0)
            atm = round(price / 50) * 50
            csec, ctrad, clot = dhan_master.get_option_contract(sym, atm, "CE", off)
            if not ctrad:
                return {"error": f"ATM CE resolve nahi hua (ATM {atm})"}
            out.update(sec_id=str(csec), seg="NSE_FNO", trad_sym=ctrad, qty=int(clot or 1))
        except Exception as e:
            return {"error": f"contract err: {str(e)[:45]}"}
    return out


def _check_webhook(sid, sc, active):
    """Webhook = TV-driven, koi subprocess nahi → dashboard-up check."""
    rows, red = [], False
    try:
        s = socket.create_connection(("127.0.0.1", 5099), timeout=2); s.close(); up = True
    except Exception:
        up = False
    if up:
        rows.append(("dashboard", "OK", "port 5099 up — /api/webhook/tv receive kar sakta"))
    else:
        rows.append(("dashboard", "FAIL", "port 5099 DOWN — webhook receive hi nahi hoga")); red = True
    if not sc.get("secret_token"):
        rows.append(("token", "WARN", "secret_token set nahi — TV alert reject ho sakta"))
    rows.append(("mode", "OK", f"{sc.get('mode','paper')} | TV-driven (alert aaye tabhi order — heartbeat N/A)"))
    age, _ = _log_heartbeat(sid)
    if age is not None:
        rows.append(("last-alert", "INFO", f"aakhri webhook log {age/60:.0f} min pehle"))
    return rows, red


def check_strategy(sid, cfg, headers, spot_cache, args):
    """Ek strategy ka poora preflight. Returns (rows[(name,status,detail)], is_red)."""
    rows, red = [], False
    sc = cfg.get(sid, {})

    active = bool(sc.get("active"))
    rows.append(("active", "OK" if active else "WARN",
                 "active:true" if active else "active:false — scheduler isse launch NAHI karega"))

    if _base(sid) == "webhook":
        return _check_webhook(sid, sc, active)

    base = _base(sid)
    script = TRADER_SCRIPTS.get(base)
    if not script:
        rows.append(("script", "FAIL", f"'{base}' ka live trader script nahi (backtest-only) — order kabhi nahi lagega"))
        return rows, True
    if not script.exists():
        rows.append(("script", "FAIL", f"missing: {script.name}")); return rows, True
    ok, err = _compiles(script)
    rows.append(("script", "OK" if ok else "FAIL", script.name if ok else f"compile error: {err}"))
    red = red or (not ok)

    # heartbeat — timeframe-aware threshold
    hb_limit = max(180, _tf_seconds(sc.get("timeframe")) * 1.5 + 90)
    age, line = _log_heartbeat(sid)
    if age is None:
        rows.append(("heartbeat", "FAIL" if active else "WARN", line)); red = red or active
    elif not _market_open():
        rows.append(("heartbeat", "SKIP", f"market band; last log {age/60:.0f} min pehle"))
    elif age <= hb_limit:
        rows.append(("heartbeat", "OK", f"{age:.0f}s pehle loop chala"))
    elif active:
        rows.append(("heartbeat", "FAIL", f"{age/60:.0f} min se log nahi (limit {hb_limit/60:.0f}m) — process ATKA/MARA?")); red = True
    else:
        rows.append(("heartbeat", "WARN", f"{age/60:.0f} min se chup (inactive)"))

    syms = _symbols(sc)
    if not syms:
        rows.append(("symbols", "FAIL", "config me koi symbol nahi")); return rows, True
    for sym in (syms if args.all_symbols else syms[:1]):
        tgt = _resolve_target(sym, sc, headers, spot_cache)
        if tgt.get("error"):
            err = tgt["error"]
            transient = err.startswith("TRANSIENT:")   # 429/network — jhootha RED mat karo
            rows.append((f"data:{sym}", "WARN" if transient else "FAIL", err))
            red = red or (not transient)
            continue
        rows.append((f"data:{sym}", "OK", f"LTP={tgt['price']}"))
        if tgt["is_opt"]:
            rows.append((f"contract:{sym}", "OK", f"{tgt['trad_sym']} (qty={tgt['qty']})"))
    return rows, red


def fire_test(sid, cfg, headers, spot_cache):
    """PAPER test-fire: asli order path chala ke confirm karo order DB me land hota.
    Test rows source='healthtest' se tag hote — caller cleanup karta. Returns (status, detail)."""
    sc = cfg.get(sid, {})
    if _base(sid) == "webhook" or _base(sid) not in TRADER_SCRIPTS:
        return "SKIP", "subprocess/order-path strategy nahi (webhook/backtest-only)"
    syms = _symbols(sc)
    if not syms:
        return "FAIL", "koi symbol nahi"
    sym = syms[0]
    tgt = _resolve_target(sym, sc, headers, spot_cache)
    if tgt.get("error"):
        return "FAIL", f"target resolve fail: {tgt['error']}"

    try:
        sys.path.insert(0, str(BASE_DIR / "brokers"))
        sys.path.insert(0, str(BASE_DIR))
        import smart_order, order_store
        from brokers import get_broker
        broker = get_broker(sc.get("broker", "dhan"))
    except Exception as e:
        return "FAIL", f"order modules load fail: {str(e)[:60]}"

    htlog = (LOGS_DIR / "healthtest.log").open("a", encoding="utf-8")
    _log = lambda m: htlog.write(f"{datetime.now(IST):%H:%M:%S} [{sid}] {m}\n")
    try:
        res = smart_order.execute(
            "BUY", sym, tgt["sec_id"], tgt["seg"], tgt["qty"], tgt["trad_sym"],
            mode="paper", broker=broker, tag="HEALTHTEST",
            source=TEST_SOURCE, strategy=sid,
            instrument=sc.get("instrument", ""), broker_name=sc.get("broker", "dhan"),
            log=_log)
    except Exception as e:
        return "FAIL", f"execute() raised: {str(e)[:60]}"
    finally:
        htlog.close()

    if not res.get("ok"):
        return "FAIL", f"order path ne fill nahi diya: {res.get('reason')}"
    # DB me land hua? (source + trad_sym match)
    rows = [r for r in order_store.query(source=TEST_SOURCE)
            if r.get("trad_sym") == tgt["trad_sym"]]
    if rows:
        return "OK", f"order DB me LAND hua @ {res['price']:.2f} ({tgt['trad_sym']}) — path verified"
    return "FAIL", f"execute OK par DB me row nahi mila (order_store.record fail?)"


def build_report(cfg, args):
    """Poora report dict banao (pretty + json dono isi se)."""
    token, client_id = _load_creds()
    headers = {"access-token": token, "client-id": client_id, "Content-Type": "application/json"}
    exp_dt, hrs = _jwt_expiry(token)
    nifty, terr = _dhan_ltp(headers, "IDX_I", "13"); time.sleep(1.1)
    if nifty is not None:
        tok_status, token_red = "OK", False
    elif (terr or "").startswith("AUTH:"):
        tok_status, token_red = "FAIL", True            # real token reject — sab RED
    else:
        tok_status, token_red = "WARN", False           # transient (429/network) — cascade RED mat karo
    tok = {"ok": nifty is not None, "status": tok_status, "nifty": nifty, "error": terr,
           "expiry": exp_dt.strftime("%d-%b %H:%M") if exp_dt else None,
           "hours_left": round(hrs, 1) if hrs is not None else None}

    SKIP = {"webhooks"}
    keys = [k for k in cfg if k not in SKIP and isinstance(cfg[k], dict)]
    if args.id:
        keys = [args.id]
    elif not args.all:
        keys = [k for k in keys if cfg[k].get("active")]

    spot_cache = {"NIFTY": nifty} if nifty else {}
    strategies, any_red = [], token_red
    for sid in keys:
        rows, red = check_strategy(sid, cfg, headers, spot_cache, args)
        if token_red:
            red = True
        ft = None
        if getattr(args, "fire_test", False) and not red and cfg.get(sid, {}).get("active"):
            st, det = fire_test(sid, cfg, headers, spot_cache)
            ft = {"status": st, "detail": det}
            rows.append(("test-fire", st, det))
            if st == "FAIL":
                red = True
        any_red = any_red or red
        strategies.append({"id": sid, "red": red, "ready": not red,
                           "checks": [{"name": n, "status": s, "detail": d} for n, s, d in rows],
                           "fire_test": ft})

    # --fire-test cleanup — test orders production P&L se hatao
    cleaned = 0
    if getattr(args, "fire_test", False):
        try:
            sys.path.insert(0, str(BASE_DIR))
            import order_store
            cleaned = order_store.delete_by_source(TEST_SOURCE)
        except Exception:
            pass

    return {"time": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            "market_open": _market_open(), "token": tok,
            "strategies": strategies, "any_red": any_red, "test_rows_cleaned": cleaned}


def render_pretty(rep):
    mkt = f"{GREEN}OPEN{RST}" if rep["market_open"] else f"{DIM}CLOSED{RST}"
    print(f"\n{'='*64}\n  CODE3B STRATEGY HEALTH CHECK   {rep['time']}   market={mkt}\n{'='*64}")
    t = rep["token"]; hl = t["hours_left"]
    if t["status"] == "FAIL":
        print(f"  TOKEN     {_c('FAIL')}  Dhan reject (token EXPIRED/galat?) — {t['error']}")
        print(f"  {RED}>> Dashboard Control tab me naya JWT paste karo. Bina iske KOI order nahi lagega.{RST}")
    elif t["status"] == "WARN":
        print(f"  TOKEN     {_c('WARN')}  verify nahi ho paaya (rate-limit/network: {t['error']}) — "
              f"JWT expiry {t['expiry']} ({hl}h), token shayad theek")
    else:
        warn = (hl is not None and hl < 2)
        print(f"  TOKEN     {_c('WARN' if warn else 'OK')}  valid, expiry {t['expiry']} "
              f"({hl}h baaki) — NIFTY={t['nifty']}")
    for s in rep["strategies"]:
        tag = f"{RED}● RED{RST}" if s["red"] else f"{GREEN}● READY{RST}"
        print(f"\n  ── {s['id']}  {tag}")
        for ch in s["checks"]:
            print(f"       {ch['name']:<14} {_c(ch['status']):<22} {DIM}{ch['detail']}{RST}")
    print(f"\n{'='*64}")
    if rep.get("test_rows_cleaned"):
        print(f"  {DIM}(test-fire: {rep['test_rows_cleaned']} test order(s) DB se cleanup ho gaye){RST}")
    if rep["any_red"]:
        print(f"  {RED}NATIJA: kuch RED hai — upar dekho, wahi order na lagne ki wajah.{RST}")
    else:
        print(f"  {GREEN}NATIJA: sab READY — strategies order bhej sakti hain, bas signal ki der.{RST}")
    print(f"{'='*64}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="ek strategy id (e.g. rsi_v1)")
    ap.add_argument("--all", action="store_true", help="config ki saari (inactive bhi)")
    ap.add_argument("--all-symbols", action="store_true", help="har symbol (default: pehla)")
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    ap.add_argument("--fire-test", dest="fire_test", action="store_true",
                    help="PAPER test-fire — order DB me land hota hai confirm karo (market band pe)")
    ap.add_argument("--force", action="store_true", help="--fire-test market khula ho tab bhi chalao")
    ap.add_argument("--loop", type=int, metavar="SEC", help="har SEC second repeat")
    args = ap.parse_args()

    def run_once():
        try:
            cfg = json.loads(TC_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(json.dumps({"error": f"config read fail: {e}"}) if args.json
                  else f"{RED}nifty_config.json read fail: {e}{RST}")
            return 1
        if args.fire_test and _market_open() and not args.force:
            msg = "⛔ --fire-test market khule me block (paper hi sahi, par noise/race se bachne ko). --force se chalao."
            print(json.dumps({"error": msg}) if args.json else f"{RED}{msg}{RST}")
            return 1
        try:
            cred_ok = CONFIG_FILE.exists()
            if not cred_ok:
                raise FileNotFoundError("data/config.json")
            rep = build_report(cfg, args)
        except Exception as e:
            print(json.dumps({"error": str(e)}) if args.json else f"{RED}✗ {e}{RST}")
            return 1
        if args.json:
            print(json.dumps(rep))
        else:
            render_pretty(rep)
        return 1 if rep["any_red"] else 0

    if args.loop:
        try:
            while True:
                run_once(); time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\nbye."); return 0
    return run_once()


if __name__ == "__main__":
    sys.exit(main())
