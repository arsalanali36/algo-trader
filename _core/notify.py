"""notify.py — CODE3B ka notification centre (single source for "kuch galat hua").

KYUN BANA (2026-07-16):
  Pehle har error do me se ek jagah jaata tha — ya `print()` ho ke log me dab
  jaata tha, ya `downloader_alert.json` ki red patti me aata tha jise ek ✕ se
  hamesha ke liye dismiss kiya ja sakta tha. Dono me HISTORY nahi thi: patti
  band ki → error gaya. Live trading me ye silent-swallow sabse mehnga bug
  shape hai (LESSONS.md TRAP #56 — SL enforcement poore din band tha aur ek
  bhi signal nahi mila).

  Ye module ek append-only log rakhta hai: har error/warning permanently likha
  jaata hai, read/unread alag track hota hai. ✕ karne se sirf "padh liya"
  mark hota hai — record kabhi delete nahi hota.

DESIGN:
  - Storage = JSONL (`data/notifications.jsonl`), append-only. Multi-process
    safe: dashboard, pos_monitor, traders — sab O_APPEND se ek-ek line likhte
    hain, koi read-modify-write race nahi (jo poore log ko corrupt kar sakti).
  - Read-state alag file me (`data/notifications_read.json`) — ek high-water
    mark. Log immutable rehta hai.
  - Dedup: same (key/msg, level) agar _DEDUP_SECS ke andar dobara aaye to nayi
    line ki jagah purani ka `count` badhta hai. Ek loop jo har 3 sec fail kar
    raha hai use 1200 rows nahi banane chahiye — par uska pata bhi chalna
    chahiye, isliye count + last_ts rakhte hain.

USAGE:
    import notify
    notify.error("Kite order reject: DH-905", key="kite_order", source="range_v1")
    notify.warn("LTP feed stale 40s", key="stale_feed")
    notify.info("Health check done")

RULE: koi bhi naya `except:` block jo trading path pe hai — usme notify.error()
      lagao. `print()` akela kaafi nahi (koi log nahi dekhta jab tak kuch toot
      na jaaye).

RULE 2 (2026-07-17): strategy ka raw id `msg` ke andar mat likho —
      `source=` me do. Kyun: `msg` ek jama hua string hai, usme se baad me
      naam nikaal ke label nahi kiya ja sakta — isi wajah se bell me
      `ARS_CHAIN_V1_PAPER: ...` chhap raha tha jabki registry me uska naam
      "Ars chain - Canary (stocks)" hai. `source` alag field hai, aur
      `listing()` use PADHTE waqt label karta hai (`source_label`) — likhte
      waqt nahi. Isliye kal naam badla to poori purani history bhi naye naam
      pe dikhegi, aur raw id (jo debugging me chahiye) record me bacha rehta
      hai.
"""
import json
import os
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_FILE = DATA_DIR / "notifications.jsonl"
READ_FILE = DATA_DIR / "notifications_read.json"
PUSH_FILE = DATA_DIR / "notifications_pushed.json"   # telegram bridge ka state

# Ek hi baat baar-baar aa rahi ho to is window ke andar count badhao, nayi
# row mat banao.
_DEDUP_SECS = 300
# Frontend / API kitni history wapas de. File isse badi ho sakti hai — ye sirf
# padhne ki limit hai (tail), taaki UI slow na ho.
_TAIL_LIMIT = 400
# Log itni lines se bada hua to purani lines trim kar do (rotate). Bounded
# rakhna zaroori hai — ye file har din badhti rahegi warna.
_MAX_LINES = 5000

LEVELS = ("error", "warn", "info")


def _now_ms():
    return int(time.time() * 1000)


def _read_lines(limit=_TAIL_LIMIT):
    """Log ki aakhri `limit` lines parse karke do (purani → nayi)."""
    if not LOG_FILE.exists():
        return []
    try:
        raw = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    out = []
    for ln in raw[-limit:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:
            # Ek corrupt line (aadha-likha append) poore centre ko na maare.
            continue
    return out


def _append(rec):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        # Notification likhna khud kabhi caller ko na tode — wo trading path pe
        # hai. Yahan print hi last resort hai.
        print(f"[notify] write fail: {e}", flush=True)


def _rewrite(records):
    """Poora log replace karo (sirf rotate/dedup-bump ke liye)."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = LOG_FILE.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, LOG_FILE)
    except Exception as e:
        print(f"[notify] rewrite fail: {e}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM BRIDGE — bell ka last mile
#
# Detection pehle se achhi thi: 28 jagah se notify.error() nikalta hai
# (error_watch dead-process/dead-service/log-traceback, reconcile, order_store,
# range_trader token, config-drift, eod...). Par ye SIRF bell me girta tha, aur
# bell 344 unread pe wallpaper ban chuki thi — yaani alarm bana hua tha par
# user tak pahunchta nahi tha. Phone pe sirf invariant_guard jaata tha: din me
# ek baar, 5 narrow checks.
#
# Ye bridge wahi last mile jodta hai, in rules ke saath (taaki phone bhi
# wallpaper na ban jaaye):
#   * sirf level="error"  — warn/info bell me hi rehte hain
#   * muted sources skip  — default "chain" (option alerts, 284/344 unread wahi the)
#   * ek problem = ek message — dobara tabhi jab renotify window nikal jaaye
#   * resolve() pe "✓ fixed" — laal badge ka band hona bhi utna hi zaroori hai
#   * rate cap/hour       — burst me ek summary line, phir chup
#
# FAIL-SAFE: poora hissa try/except me hai aur telegram_notify lazily import
# hota hai. Ye code order-path ke exception handlers se chalta hai — yahan koi
# bhi error trade ko todna nahi chahiye.
# ─────────────────────────────────────────────────────────────────────────────

def _push_state():
    try:
        with open(PUSH_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("keys", {})
            d.setdefault("hour", [])
            return d
    except Exception:
        pass
    return {"keys": {}, "hour": []}


def _push_state_write(st):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(PUSH_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, PUSH_FILE)
    except Exception:
        pass


def _tg_cfg():
    import telegram_notify as tg
    return tg, (tg._load_config() or {})


def _maybe_push_telegram(level, msg, dedup_key, source):
    """Naya RED bell-alert -> phone. Kabhi raise nahi karta."""
    try:
        if level != "error":
            return
        tg, cfg = _tg_cfg()
        if not cfg.get("alerts_enabled", True) or not tg.is_enabled():
            return
        muted = cfg.get("alert_mute_sources") or []
        if str(source or "") in {str(m) for m in muted}:
            return

        now = _now_ms()
        st = _push_state()
        cool = float(cfg.get("alert_renotify_hours", 6)) * 3600_000
        last = st["keys"].get(dedup_key)
        if last and (now - int(last)) < cool:
            return                      # abhi bataya tha — dobara nahi

        # rate cap: rolling hour
        cap = int(cfg.get("alert_max_per_hour", 10))
        hour = [t for t in st.get("hour", []) if now - int(t) < 3600_000]
        if len(hour) >= cap:
            # cap ke baad ek hi baar batao ki aur bhi hain, phir chup
            if len(hour) == cap:
                hour.append(now)
                st["hour"] = hour
                st["keys"][dedup_key] = now
                _push_state_write(st)
                tg._dispatch("⚠️ Aur alerts aa rahe hain (rate cap %d/hr hit) — "
                             "dashboard ki bell dekhein." % cap)
            return

        hour.append(now)
        st["hour"] = hour
        st["keys"][dedup_key] = now
        _push_state_write(st)

        src = _source_label(source) if source else ""
        head = "🔴 %s" % src if src else "🔴 Alert"
        tg._dispatch(head + chr(10) + str(msg)[:600])
    except Exception as e:
        print(f"[notify] telegram bridge fail: {e}", flush=True)


def _push_telegram_resolved(dedup_key, msg=""):
    """Jo problem phone pe gayi thi, uska theek hona bhi phone pe jaaye."""
    try:
        st = _push_state()
        if dedup_key not in st.get("keys", {}):
            return                      # ye kabhi phone pe gaya hi nahi
        st["keys"].pop(dedup_key, None)
        _push_state_write(st)
        tg, cfg = _tg_cfg()
        if not cfg.get("alerts_enabled", True) or not tg.is_enabled():
            return
        tg._dispatch("✅ Theek ho gaya: %s" % (str(msg or dedup_key)[:300]))
    except Exception as e:
        print(f"[notify] telegram resolve-push fail: {e}", flush=True)


def push(msg, level="error", key=None, source=None):
    """Ek notification record karo. Hamesha safe — kabhi raise nahi karta.

    msg    — jo user ko dikhega (ek line, actionable rakho)
    level  — error | warn | info
    key    — dedup identity (ek hi problem ke repeats ko jodne ke liye).
             None = msg khud key hai.
    source — kis strategy/module se aaya (e.g. "range_v1", "pos_monitor")
    """
    try:
        msg = str(msg).strip()
        if not msg:
            return None
        if level not in LEVELS:
            level = "error"
        dedup_key = str(key or msg)[:200]
        now = _now_ms()

        recs = _read_lines()
        # Dedup: window ke andar same key+level → count bump, nayi row nahi.
        for r in reversed(recs):
            if r.get("dedup") == dedup_key and r.get("level") == level:
                if now - int(r.get("last_ts") or r.get("ts") or 0) <= _DEDUP_SECS * 1000:
                    was_read = bool(r.get("read"))
                    r["count"] = int(r.get("count") or 1) + 1
                    r["last_ts"] = now
                    r["msg"] = msg          # latest wording jeete
                    r["read"] = False       # dobara hua = phir se dikhao
                    r["resolved"] = False   # "fixed" tha aur laut aaya → ab fixed nahi
                    if was_read:
                        # User ne isay padh liya tha aur problem PHIR hui — ye ek
                        # nayi ghatna hai, isliye nayi id: high-water mark se upar
                        # jaaye (warna listing() usay dobara "read" bana deti aur
                        # re-occurrence CHUP ho jaati), panel me top pe aaye, aur
                        # frontend ka fresh-check toast+beep bajaye.
                        r["id"] = now
                        used = {x.get("id") for x in recs if x is not r}
                        while r["id"] in used:
                            r["id"] += 1
                    # Agar abhi tak unread hai to id waisi hi rehti — user ke saamne
                    # pehle se hai, har repeat pe dobara toast karna spam hoga.
                    _rewrite(recs)
                    return r["id"]
                break

        rec = {
            "id": now,
            "ts": now,
            "last_ts": now,
            "level": level,
            "msg": msg[:1000],
            "dedup": dedup_key,
            "source": source or "",
            "count": 1,
            "read": False,
        }
        # id unique rakho — same ms me do push ho sakte hain.
        used = {r.get("id") for r in recs}
        while rec["id"] in used:
            rec["id"] += 1

        if len(recs) >= _MAX_LINES:
            _rewrite(recs[-(_MAX_LINES // 2):] + [rec])
        else:
            _append(rec)
        # Bell me likh diya — ab phone tak bhi pahunchao (RED only, rate-capped).
        _maybe_push_telegram(level, msg, dedup_key, source)
        return rec["id"]
    except Exception as e:
        print(f"[notify] push fail: {e}", flush=True)
        return None


def error(msg, key=None, source=None):
    return push(msg, "error", key, source)


def warn(msg, key=None, source=None):
    return push(msg, "warn", key, source)


def info(msg, key=None, source=None):
    return push(msg, "info", key, source)


def _read_state():
    try:
        if READ_FILE.exists():
            return json.loads(READ_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"seen_id": 0}


def _write_state(st):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = READ_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        os.replace(tmp, READ_FILE)
    except Exception as e:
        print(f"[notify] state write fail: {e}", flush=True)


def _source_label(src):
    """`source` ka display naam — registry se. Resolve na ho to raw hi wapas.

    Label PADHTE waqt lagta hai, likhte waqt nahi: record me raw id rehta hai
    (debugging/grep ke liye), aur naam badalne pe poori purani history apne aap
    naye naam pe dikhne lagti hai. Registry na mile (koi standalone process)
    to raw — kabhi crash nahi.
    """
    if not src:
        return ""
    try:
        import strategy_registry as _sr
        return _sr.label(src, with_name=True).split(" - ", 1)[-1] if _sr.resolve(src) else str(src)
    except Exception:
        return str(src)


def listing(after=0, limit=_TAIL_LIMIT):
    """History do (nayi → purani) + unread count.

    after — sirf isse badi id wale do (frontend ka incremental poll). 0 = sab.
    """
    recs = _read_lines(limit)
    seen = int(_read_state().get("seen_id") or 0)
    for r in recs:
        # Explicit read flag ya high-water mark — dono me se koi bhi read maane.
        if int(r.get("id") or 0) <= seen:
            r["read"] = True
        # UI `source_label` dikhata hai, `source` (raw) title/hover me rehta hai.
        r["source_label"] = _source_label(r.get("source"))
    unread = sum(1 for r in recs if not r.get("read"))
    items = [r for r in recs if int(r.get("id") or 0) > int(after or 0)]
    items.sort(key=lambda r: int(r.get("id") or 0), reverse=True)
    return {"items": items, "unread": unread,
            "max_id": max([int(r.get("id") or 0) for r in recs] or [0])}


def mark_read(ids=None):
    """ids=None → sab read. Warna sirf di gayi ids."""
    recs = _read_lines()
    if ids is None:
        top = max([int(r.get("id") or 0) for r in recs] or [0])
        for r in recs:
            r["read"] = True
        _rewrite(recs)
        _write_state({"seen_id": top})
        return top
    want = {int(i) for i in ids}
    for r in recs:
        if int(r.get("id") or 0) in want:
            r["read"] = True
    _rewrite(recs)
    return len(want)


def resolve(dedup_key):
    """Problem khatam ho gayi → uske notifications ko read + `resolved` mark karo.

    Jo error theek ho chuka hai uska laal badge bethe rehna utna hi bekaar hai
    jitna error ka chhup jaana — dono soorat me bell pe bharosa khatam hota hai.
    Row DELETE nahi hoti (history hamesha rehti hai), bas chup ho jaati hai aur
    UI me "✓ fixed" dikhti hai.

    Wapas: kitni rows resolve hui (0 = pehle se resolved/koi thi hi nahi).
    """
    try:
        recs = _read_lines()
        n = 0
        _msg = ""
        for r in recs:
            if r.get("dedup") == str(dedup_key)[:200] and not r.get("resolved"):
                r["resolved"] = True
                r["read"] = True
                _msg = r.get("msg") or _msg
                n += 1
        if n:
            _rewrite(recs)
            _push_telegram_resolved(str(dedup_key)[:200], _msg)
        return n
    except Exception as e:
        print(f"[notify] resolve fail: {e}", flush=True)
        return 0


def clear():
    """History wipe. Deliberate user action only — ✕/dismiss ISKO NAHI bulata
    (wo sirf mark_read karta hai). Errors accident se gaayab nahi hone chahiye."""
    _rewrite([])
    _write_state({"seen_id": 0})
