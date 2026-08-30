#!/usr/bin/env python3
"""heartbeat.py — DEAD-MAN SWITCH: "koi khabar na aana" bhi ek khabar hai.

WHY
---
Autonomy audit (2026-08-30) ka blocker #5 ka doosra aadha. App ke andar
detection achhi hai — `_ops/error_watch.py` dead strategy-process, dead service
aur log-traceback teeno pakadta hai. **Par wo dashboard ke ANDAR chalta hai.**
Yaani:

    dashboard mar gaya  ->  error_watch bhi mar gaya  ->  poori CHUP.

Aur chup "sab theek hai" jaisi dikhti hai. Ek mahina akele chhodne ke liye ye
sabse kharab failure mode hai: kuch toota, aur aapko pata bhi nahi chala.

Ye file us chup ko todti hai — **alag one-shot process** (systemd timer), app ke
andar nahi. Isliye app poora gir jaye tab bhi ye bolti hai.

KYA DEKHTA HAI
--------------
  1. systemd units zinda? (dashboard/monitor/supervisor/option-chain)
  2. dashboard HTTP jawab de raha? (login page — auth ke bina)
  3. supervisor ne kuch respawn / give-up kiya? (`supervisor_events.json`)
  4. safe mode chalu to nahi? (LIVE entry band hai — chup-chaap nahi rehna)
  5. Roz ek "zinda hoon" digest — taaki us message ka NA aana khud alarm ho.

  Har check apne try/except me: ek fail poore watchdog ko na maare.
  READ-ONLY — koi order, koi config write nahi.

ISKI APNI HAD (imaandaari se)
-----------------------------
Poora box mar jaye (VPS down, network gaya) to ye bhi nahi chalega — koi
in-box watchdog nahi chal sakta. Us case ka jawab = **roz ka digest**: wo na
aaye to samajh jaiye box hi chup hai. Sacchi external monitoring (bahar se
ping) is se aage ki cheez hai.

RUN
---
    python -X utf8 _ops/heartbeat.py            # ek baar (systemd timer isse)
    python -X utf8 _ops/heartbeat.py --digest   # digest force bhejo
    python -X utf8 _ops/heartbeat.py --dry      # sirf dikhao, alert mat bhejo
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _paths  # noqa: F401  (project dirs -> sys.path)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "heartbeat_state.json"
EVENTS_FILE = DATA_DIR / "supervisor_events.json"

# Jo units zinda honi chahiye. (algo-dashboard ke andar hi error_watch chalta
# hai — isliye wo mare to sirf YE file bata sakti hai.)
UNITS = ("algo-dashboard", "algo-monitor", "algo-supervisor", "algo-optionchain")
DASHBOARD_URL = "http://127.0.0.1:5099/login"
DIGEST_HOUR = 9          # IST — roz ka "zinda hoon" (market khulne ke aas-paas)
IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now():
    return datetime.now(IST)


def _read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def _write_state(st):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print("[heartbeat] state write fail: " + str(e), flush=True)


# ─────────────────────────────────────────────────────────────── checks ──
def check_units():
    """[(unit, active, detail)] — systemd se. Linux ke bahar khaali list."""
    out = []
    for u in UNITS:
        try:
            r = subprocess.run(["systemctl", "is-active", u],
                               capture_output=True, text=True, timeout=10)
            state = (r.stdout or r.stderr or "").strip()
            out.append((u, state == "active", state or "unknown"))
        except FileNotFoundError:
            return []                       # systemd nahi (local dev) — skip
        except Exception as e:
            out.append((u, None, str(e)[:80]))    # None = pata nahi chala
    return out


def check_dashboard():
    """(alive, detail). None = pata nahi chala (jhootha alarm nahi bajana)."""
    try:
        import urllib.request
        with urllib.request.urlopen(DASHBOARD_URL, timeout=8) as r:
            code = r.getcode()
        return (200 <= code < 400), "HTTP " + str(code)
    except Exception as e:
        s = str(e)
        low = s.lower()
        dead = ("refused" in low or "connection" in low or "timed out" in low)
        if "refused" in low:
            s = "connection refused (process nahi chal raha)"
        elif "timed out" in low:
            s = "timeout (chal raha hai par jawab nahi de raha)"
        return (False if dead else None), s[:100]


def new_supervisor_events(state):
    """Pichli baar ke baad ke naye supervisor events (respawn / give-up)."""
    try:
        with open(EVENTS_FILE, encoding="utf-8") as f:
            evs = json.load(f)
        if not isinstance(evs, list):
            return []
        seen = float(state.get("events_seen_ts") or 0)
        fresh = [e for e in evs if float(e.get("ts") or 0) > seen]
        if evs:
            state["events_seen_ts"] = max(float(e.get("ts") or 0) for e in evs)
        return fresh
    except FileNotFoundError:
        return []
    except Exception as e:
        print("[heartbeat] events read fail: " + str(e), flush=True)
        return []


DISK_WARN_PCT = 85      # is se upar -> warn
DISK_CRIT_PCT = 92      # is se upar -> error (phone)


def check_disk(path="/"):
    """(pct_used, free_gb) — disk bharna ek chup-chaap killer hai: logs/lake
    badhte rehte hain aur ek din write fail hone lagti hai. None = pata nahi chala."""
    try:
        import shutil
        t, u, f = shutil.disk_usage(path)
        return (100.0 * u / t), (f / (1024 ** 3))
    except Exception:
        return None, None


def holiday_coverage_warning():
    """NSE holiday list agle saal ke liye add hui ya nahi (market_calendar se —
    wahi single source, yahan doosri copy nahi banayi)."""
    try:
        import market_calendar as mc
        return mc.coverage_warning()
    except Exception:
        return None


# Registry ke `status` ke wo values jinme LIVE trading ka iraada NAHI hai.
# In pe config `mode=live + active` mila = strategy apni hi declared status ke
# khilaf asli paisa laga rahi hai.
_NON_LIVE_STATUS = ("paper", "research", "retired", "rejected", "test", "watch")


def registry_vs_config():
    """Registry ka declared `status` vs live config ka `mode`/`active` — mismatch list.

    KYUN (2026-08-30): `range_hedged` (Ars chain hedged vertical) ki registry status
    **paper** thi, par config me `active:true, mode:live` tha — aur usne 19-Aug ko
    asli LIVE trade kar diya, jabki uska backtest Sharpe **negative** hai (~−1.0).
    Kisi ko pata nahi chala; user ne haath se pakda.

    Jad: registry ko hum status ka source of truth maante hain, par **registry aur
    live config kabhi cross-check hote hi nahi the**. Do jagah "sach" likha tha aur
    koi milata nahi tha — is repo ka sabse purana bug-shape (Rule 6B ka ulta).

    Do tarah ke mismatch, alag severity (jaan-boojh kar):
      * registry NON-LIVE  + config live+active → **error**: declared iraade ke
        khilaf asli paisa lag raha hai
      * registry `live`    + config band/paper  → **warn**: jo live samjhi ja rahi
        hai wo chup-chaap trade hi nahi kar rahi (kam khatarnak, par jhoothi tasalli)

    READ-ONLY — sirf padhta hai, kuch band/chalu nahi karta.
    """
    out = []
    try:
        import json as _js
        import strategy_registry as _sr
        cfg_path = BASE_DIR / "nifty_config.json"
        cfg = _js.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        regs = _sr.strategies() or {}
    except Exception as e:
        print("[heartbeat] registry check skip: " + str(e), flush=True)
        return out

    items = regs.values() if isinstance(regs, dict) else regs
    for st in items:
        if not isinstance(st, dict):
            continue
        ck = st.get("config_key")
        if not ck:
            continue
        # Kuch strategies ka nifty_config BLOCK ka naam unke order_store id se alag
        # hota hai (02.17: id `weekly_ironfly_v1`, block `_weekly_ironfly`). Aisi
        # entries `settings_key` explicit deti hain — warna guard unhe dhundh hi
        # nahi paata aur wo CHUP-CHAAP guard ke bahar reh jaati hain (blind spot).
        node = cfg.get(st.get("settings_key") or ck)
        if not isinstance(node, dict):
            continue
        status = str(st.get("status") or "").strip().lower()
        mode = str(node.get("mode") or "").strip().lower()
        active = node.get("active")
        # `enabled` un strategies ke liye jo top-level `active` nahi rakhtin
        # (jaise _weekly_ironfly) — dono me se jo maujood ho.
        on = bool(active) if active is not None else bool(node.get("enabled"))
        name = st.get("name") or ck

        if status in _NON_LIVE_STATUS and mode == "live" and on:
            out.append({"level": "error", "config_key": ck, "name": name,
                        "status": status, "mode": mode,
                        "msg": ("🔴 %s (%s) — registry me status '%s' hai par config "
                                "LIVE + chalu hai. Yaani apni declared status ke khilaf "
                                "asli paisa laga rahi hai." % (name, ck, status))})
        elif status == "live" and (mode != "live" or not on):
            out.append({"level": "warn", "config_key": ck, "name": name,
                        "status": status, "mode": mode,
                        "msg": ("⚠️ %s (%s) — registry 'live' kehti hai par config "
                                "mode=%s active=%s. Jo live samjhi ja rahi hai wo trade "
                                "nahi kar rahi." % (name, ck, mode or "?", on))})
    return out


def safe_mode_status():
    try:
        import safe_mode
        return safe_mode.active()
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────── report ──
def _notify(level, msg, key, dry=False):
    print("[heartbeat] " + level.upper() + ": " + msg, flush=True)
    if dry:
        return
    try:
        import notify
        getattr(notify, level)(msg, key=key, source="heartbeat")
    except Exception as e:
        print("[heartbeat] notify fail: " + str(e), flush=True)


def _resolve(key, dry=False):
    if dry:
        return
    try:
        import notify
        notify.resolve(key)
    except Exception:
        pass


def run(dry=False, force_digest=False):
    st = _read_state()
    problems = []

    # 1) systemd units
    units = check_units()
    for u, ok, detail in units:
        key = "hb:unit:" + u
        if ok is False:
            problems.append(u + " = " + detail)
            _notify("error", "🔴 SERVICE DOWN: " + u + " (" + detail + ") — "
                    "is service ka kaam abhi ho hi nahi raha", key, dry)
        elif ok is True:
            _resolve(key, dry)
        # ok is None -> pata nahi chala, jhootha alarm nahi

    # 2) dashboard HTTP (yahi wo process hai jisme error_watch rehta hai)
    alive, detail = check_dashboard()
    if alive is False:
        problems.append("dashboard HTTP (" + detail + ")")
        _notify("error", "🔴 DASHBOARD jawab nahi de raha (" + detail + ") — "
                "iske andar ka error-watch bhi band hai", "hb:dashboard", dry)
    elif alive is True:
        _resolve("hb:dashboard", dry)

    # 3) supervisor events (respawn / give-up)
    for e in new_supervisor_events(st):
        kind = e.get("kind", "")
        sid = e.get("sid", "")
        det = e.get("detail", "")
        if kind == "respawn_gaveup":
            problems.append(sid + " respawn give-up")
            _notify("error", "🔴 " + sid + " baar-baar mar rahi hai — supervisor ne "
                    "haath khade kar diye (" + det + "). Haath se dekhna padega.",
                    "hb:gaveup:" + sid, dry)
        elif kind == "respawned":
            _notify("warn", "♻️ " + sid + " crash ke baad khud wapas chalu ki gayi ("
                    + det + ")", "hb:respawn:" + sid, dry)
        elif kind == "respawn_failed":
            problems.append(sid + " respawn fail")
            _notify("error", "🔴 " + sid + " ko wapas chalu nahi kar paya (" + det + ")",
                    "hb:respawnfail:" + sid, dry)

    # 3b) disk (bharne se pehle bolo, bharne ke baad nahi)
    pct, free = check_disk()
    if pct is not None:
        if pct >= DISK_CRIT_PCT:
            problems.append("disk %.0f%% full" % pct)
            _notify("error", "🔴 DISK %.0f%% bhar chuki (%.1f GB bachi) — "
                    "write fail hone lagengi" % (pct, free), "hb:disk", dry)
        elif pct >= DISK_WARN_PCT:
            problems.append("disk %.0f%% full" % pct)
            _notify("warn", "⚠️ Disk %.0f%% full (%.1f GB bachi)" % (pct, free),
                    "hb:disk", dry)
        else:
            _resolve("hb:disk", dry)

    # 3c) NSE holiday list agle saal ki add hui?
    hw = holiday_coverage_warning()
    if hw:
        problems.append("holiday list")
        _notify("warn", "📅 " + hw, "hb:holidays", dry)
    else:
        _resolve("hb:holidays", dry)

    # 3d) registry status vs live config (declared iraada vs asli behaviour)
    for m in registry_vs_config():
        problems.append("%s: registry=%s config=%s" % (m["config_key"], m["status"], m["mode"]))
        _notify(m["level"], m["msg"], "hb:regmode:" + m["config_key"], dry)

    # 4) safe mode (LIVE entry band — chup mat raho)
    sm = safe_mode_status()
    if sm:
        problems.append("safe mode: " + ",".join(sorted(sm)))

    # 5) roz ka "zinda hoon" — iska NA aana khud alarm hai
    now = _ist_now()
    today = now.date().isoformat()
    if force_digest or (now.hour >= DIGEST_HOUR and st.get("digest_day") != today):
        st["digest_day"] = today
        up = sum(1 for _, ok, _ in units if ok is True)
        line = ("✅ System zinda (" + str(up) + "/" + str(len(units)) + " services, "
                "dashboard " + ("OK" if alive else "DOWN") + ")")
        if problems:
            line = "⚠️ System chal raha hai par dikkat hai: " + "; ".join(problems)
        _notify("warn" if problems else "info", line, "hb:digest", dry)
        # Bridge sirf `error` phone tak le jaata hai — par is digest ka POORA
        # matlab hi ye hai ki wo phone pe roz aaye (na aana = alarm). Isliye
        # seedha bhejte hain. Problems wale case me alerts upar already gaye,
        # to dobara nahi (shor nahi machana).
        if not dry and not problems:
            try:
                import telegram_notify
                telegram_notify.send_raw(line)
            except Exception as e:
                print("[heartbeat] digest push fail: " + str(e), flush=True)

    _write_state(st)
    return problems


def main():
    dry = "--dry" in sys.argv
    problems = run(dry=dry, force_digest="--digest" in sys.argv)
    try:
        import telegram_notify
        telegram_notify.flush()     # one-shot process — TRAP #192
    except Exception:
        pass
    # 0 = sab theek | 3 = "chala, par dhyan do" (crash 1 se alag, TRAP #193)
    return 3 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
