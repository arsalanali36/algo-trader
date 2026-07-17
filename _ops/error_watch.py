#!/usr/bin/env python3
"""error_watch.py — poori app ka har error ek hi jagah (🔔) pe le aata hai.

KYUN (2026-07-16, user ki demand):
  "puri app me kuch bhi error aaye, bas isko hi dekhna pade, idhar udhar hunt na
  karna pade." Notification centre (`_core/notify.py`) sirf wahi dikhata tha jo
  koi JAAN-BOOJH KAR report karta hai — downloader alerts, Flask route crashes,
  browser crashes. Par app ke asli errors teen aur jagah chup-chaap marte the:

    1. `logs/<strategy>.log` — har trader `log.error(...)` / traceback yahin
       likhta hai aur koi nahi padhta. Aaj hi: ema_v1 ka "single positional
       indexer is out-of-bounds" 4 baar (wahi crash jo 15-Jul ko fix hua maana
       gaya tha), aur "no Dhan info for <symbol>" (jo symbol list se hata
       diya gaya tha). Dono kisi ko nahi dikhe.
    2. Strategy PROCESS ka chup-chaap mar jaana — koi error report hi nahi hota,
       isliye bell khaali rehti hai (aaj ema_v1 ke saath exactly ye hua).
    3. Koi systemd service (monitor/downloader) gir jaana.

  Teeno ab yahan se notify me jaate hain.

DESIGN:
  - Log tail OFFSET-based hai (`data/error_watch_offsets.json`). Nayi file pe
    offset = EOF se shuru — warna June ke purane traceback (logs kabhi rotate
    nahi hote) ek saath bell me phat jaate. File chhoti ho gayi (rotate/truncate)
    → offset 0.
  - Dedup key = NORMALIZED signature (numbers/timestamps hata ke), raw line nahi
    — warna har loop ka thoda-alag error naya row banata aur wahi spam ban jaata
    jise rokna tha. notify.push ka count usi ek row pe badhta hai.
  - Watchdog sirf market hours me (bahar strategies ka band hona NORMAL hai —
    15:30 pe scheduler khud rokta hai), aur wapas aate hi khud resolve.
  - Har check apne try/except me: ek check ka fail poore watcher ko na maare.

REUSE: process liveness ka faisla `get_pid` INJECT hota hai (dashboard apna
  canonical `get_pid()` deta hai) — yahan doosri PID-detect copy nahi banai
  (Rule 6B; `_base`/`_token` wali "do jagah alag sach" wali galti dobara nahi).
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
OFFSETS = DATA_DIR / "error_watch_offsets.json"

sys.path.insert(0, str(BASE_DIR / "_core"))
import notify  # noqa: E402

# Line ERROR hai ya nahi. Dono log formats cover hote hain:
#   "2026-07-16 09:15:13  ERROR     Loop error: ..."   (dashboard-spawned traders)
#   "2026-07-16 09:16:45,052 ERROR  fetch_daily: ..."  (logging default)
_ERR_RE = re.compile(r"\b(ERROR|CRITICAL)\b")
_TB_START = "Traceback (most recent call last):"
# Traceback ka AAKHRI kaam ka line: "ModuleNotFoundError: No module named 'x'"
_EXC_RE = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception|Interrupt))\b\s*:?\s*(.*)$")
# Timestamp/level prefix hatao taaki message saaf dikhe
_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}[.,]?\d*\s+\w*\s*")

MARKET_OPEN = (9, 15)
MARKET_CLOSE = (15, 30)


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _market_hours():
    n = _ist_now()
    if n.weekday() >= 5:
        return False
    hm = (n.hour, n.minute)
    return MARKET_OPEN <= hm <= MARKET_CLOSE


# `trader_dashboard.auto_scheduler` isi waqt saare bots band karta hai
# (`t >= (15,30)` → `/api/stop?keep_active=1`). Wo `active` flag JAAN-BOOJH KE
# true chhodta hai taaki kal 9:10 pe auto-start chale — matlab 15:30 ke baad
# "ACTIVE hai par process nahi" bilkul NORMAL haalat hai, error nahi.
#
# ⚠️ Ye number `trader_dashboard.py` ke scheduler me bhi hardcoded hai. Wahan
# badlo to yahan bhi badalna padega — warna ye jhootha alarm wapas aa jayega.
SCHED_STOP = (15, 30)


def _proc_check_window():
    """Process-zinda-hai check ka apna window — market hours se ek minute chhota.

    2026-07-17: `check_strategies` `_market_hours()` pe chal raha tha, jiska
    close **inclusive** hai (`hm <= (15,30)`). Scheduler bots 15:30 pe hi band
    karta hai → us poore minute error_watch ke liye market "khula" tha aur bots
    ja chuke the → har trading din ek jhootha 🔴 "koi order nahi lagega"
    (17 July: 33 hits). Function ka apna docstring pehle se kehta tha "bahar
    band hona normal hai (15:30 scheduled stop)" — iraada theek tha, boundary
    galat thi.

    `_market_hours()` ko waisa hi chhoda — market 15:30 pe sach me khula hai;
    badla sirf ye ki HAMARA scheduler tab tak bots band kar chuka hota hai.
    """
    n = _ist_now()
    if n.weekday() >= 5:
        return False
    hm = (n.hour, n.minute)
    return MARKET_OPEN <= hm < SCHED_STOP


def _signature(msg):
    """Dedup identity: numbers/hex/quotes hata ke ek stable shape.

    "INFY candle error: ... (Errno -3)" aur "INFY candle error: ... (Errno -5)"
    ek hi problem hai — alag rows nahi banni chahiye. Symbol/word farq rehta hai
    (INFY vs KOTAKBANK genuinely alag cheez hai), sirf badalte numbers gayab.
    """
    s = re.sub(r"0x[0-9a-fA-F]+", "#", msg)
    s = re.sub(r"\d+", "#", s)
    return re.sub(r"\s+", " ", s).strip()[:120]


def _load_offsets():
    try:
        return json.loads(OFFSETS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_offsets(off):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = OFFSETS.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(off), encoding="utf-8")
        os.replace(tmp, OFFSETS)
    except Exception as e:
        print(f"[error_watch] offsets write fail: {e}", flush=True)


def _new_lines(path, off):
    """(lines, new_offset). Pehli baar dekhi file → EOF se shuru (history mat
    bhejo). File chhoti ho gayi → rotate/truncate hua → 0 se padho."""
    size = path.stat().st_size
    start = off.get(str(path.name))
    if start is None:
        return [], size          # first sight — sirf aage se dekho
    if start > size:
        start = 0                # rotated/truncated
    if start == size:
        return [], size
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        f.seek(start)
        data = f.read()
    return data.splitlines(), size


def scan_logs():
    """logs/*.log ke naye ERROR/CRITICAL/Traceback → notify.error. Wapas: count."""
    if not LOGS_DIR.exists():
        return 0
    off = _load_offsets()
    pushed = 0
    for path in sorted(LOGS_DIR.glob("*.log")):
        try:
            lines, new_off = _new_lines(path, off)
            off[path.name] = new_off
            src = path.stem
            in_tb = False
            for raw in lines:
                ln = raw.rstrip()
                if not ln:
                    continue
                if _TB_START in ln:
                    in_tb = True            # asli wajah aage aayegi
                    continue
                if in_tb:
                    m = _EXC_RE.match(ln.strip())
                    if m:
                        in_tb = False
                        msg = f"{m.group(1)}: {m.group(2)}".strip()[:300]
                        notify.error(msg,
                                     key=f"log:{src}:{_signature(msg)}", source=src)
                        pushed += 1
                    continue                # traceback ki beech wali lines skip
                if _ERR_RE.search(ln):
                    msg = _PREFIX_RE.sub("", ln).strip()[:300]
                    if not msg:
                        continue
                    notify.error(msg,
                                 key=f"log:{src}:{_signature(msg)}", source=src)
                    pushed += 1
        except Exception as e:
            print(f"[error_watch] {path.name} scan fail: {e}", flush=True)
    _save_offsets(off)
    return pushed


def check_strategies(get_pid, actives):
    """Config me active par process gayab = chup-chaap mari hui strategy.

    Ye wahi khaali khana hai jo aaj dikha: ema_v1 mara aur bell khaali rahi,
    kyunki marne pe koi error report hi nahi hota. Sirf market hours me — bahar
    band hona normal hai (15:30 scheduled stop). Wapas chalne pe khud resolve.
    """
    if not _proc_check_window():   # 15:30 = scheduler ka apna stop-minute, error nahi
        return 0
    n = 0
    for sid in actives:
        try:
            key = f"proc:{sid}"
            if get_pid(sid):
                notify.resolve(key)         # wapas zinda → chup ho jao
            else:
                notify.error(
                    "strategy config me ACTIVE hai par process nahi chal raha "
                    "— koi order nahi lagega",
                    key=key, source=sid)
                n += 1
        except Exception as e:
            print(f"[error_watch] {sid} proc check fail: {e}", flush=True)
    return n


SERVICES = ("algo-monitor", "data-downloader")   # algo-dashboard khud ye chala raha
                                                 # hai — wo gira to bell waise hi gayab


def check_services():
    """Koi zaroori systemd service gir gaya? (Linux/VPS only.)"""
    if not sys.platform.startswith("linux"):
        return 0
    n = 0
    for svc in SERVICES:
        try:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True, timeout=10)
            state = (r.stdout or "").strip()
            key = f"svc:{svc}"
            if state == "active":
                notify.resolve(key)
            else:
                notify.error(f"service {svc} chal nahi raha (state={state or 'unknown'})",
                             key=key, source="systemd")
                n += 1
        except Exception as e:
            print(f"[error_watch] {svc} check fail: {e}", flush=True)
    return n


def scan_once(get_pid=None, actives=None):
    """Ek poora cycle. Har hissa apne guard me — ek fail doosre ko na roke."""
    total = 0
    try:
        total += scan_logs()
    except Exception as e:
        print(f"[error_watch] scan_logs fail: {e}", flush=True)
    if get_pid and actives:
        try:
            total += check_strategies(get_pid, actives)
        except Exception as e:
            print(f"[error_watch] check_strategies fail: {e}", flush=True)
    try:
        total += check_services()
    except Exception as e:
        print(f"[error_watch] check_services fail: {e}", flush=True)
    return total


if __name__ == "__main__":
    # Standalone: logs + services (process-check ko dashboard ka get_pid chahiye)
    print("scan_once →", scan_once(), "new notification(s)")
    l = notify.listing()
    print("unread:", l["unread"], "| rows:", len(l["items"]))
