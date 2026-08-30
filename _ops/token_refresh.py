"""Token auto-refresh — roz ka manual token kaam khatam (Dhan), aur Kite ke liye
loud pre-market reminder.

WHY
---
Autonomy audit (2026-08-30) ka blocker #1: Dhan JWT aur Kite access-token dono
ROZ expire hote hain. Asli problem "token daalna" nahi tha — problem ye tha ki
token na hone pe system SAFELY band nahi hota:

    brokers/kite_broker.py  -> RuntimeError("kite_access_token missing")
    trader_dashboard.py     -> "LIVE square-off EXCEPTION ... leaving position
                               open, will retry"   (har 5s, hamesha)

yaani LIVE position bina kaam karne wale SL ke padi rehti hai, aur system chup
rehta hai. Data Dhan se aata hai par order Kite se jaata hai, to aadha system
chalta rehta hai — sabse bura combination.

DHAN — poori tarah automate ho jaata hai (koi secret store nahi karna padta)
--------------------------------------------------------------------------
    GET https://api.dhan.co/v2/RenewToken
    headers: access-token: <abhi ka token>, dhanClientId: <client id>

Ye ABHI KE ZINDA token se naya 24h token deta hai — password/PIN/TOTP kuch nahi
chahiye. Isliye roz renew karte raho to chain kabhi tootegi nahi, aur system me
ek bhi naya credential nahi rakhna padta. (Live-verified 2026-08-30: 200 OK,
exp 30-Aug 10:03 -> 31-Aug 01:40.)

Chain tabhi tootti hai jab token PEHLE HI mar chuka ho ("only active tokens can
be renewed"). Isliye ye har few hours chalta hai, expiry se KAAFI pehle renew
karta hai, aur fail hone pe phone pe alert bhejta hai (notify -> telegram
bridge, TRAP #192) — taaki 24 ghante ka poora buffer mile.

KITE — automate NAHI kiya gaya (jaan-boojh kar)
-----------------------------------------------
Kite ka koi renew endpoint nahi hai; naya access_token sirf login + 2FA se
milta hai. Use programmatically karna Zerodha ki API terms ke against hai
(wo roz manual 2FA maangte hain) aur API key band ho sakti hai. Isliye yahan
sirf **proactive alert** hai: token mara ya marne wala hai -> phone pe login
URL ke saath message, taaki 30 second ka kaam market khulne se pehle ho jaye.

SAFETY
------
* Naya token PEHLE /v2/profile se validate hota hai, TAB config me likha jaata
  hai — kabhi bhi unvalidated token live nahi hota.
* Config atomic write (tmp + os.replace) + rolling backup — mid-write kill se
  corrupt config nahi banti (audit head 11).
* Paper lane bhi isi Dhan token pe zinda hai (candles/LTP + option-chain
  collector) — yaani ye fix data-collection ko bhi bachata hai, todta nahi.

CLI
---
    python -X utf8 _ops/token_refresh.py            # check + zaroorat ho to renew
    python -X utf8 _ops/token_refresh.py --json     # scheduler/health ke liye
    python -X utf8 _ops/token_refresh.py --force    # abhi renew karo (test)
    python -X utf8 _ops/token_refresh.py --status   # kuch mat karo, sirf batao
"""

import sys as _sys
from pathlib import Path as _Path

# Ye file _ops/ me hai aur seedha chalti hai (systemd timer) — us soorat me
# sys.path[0] = _ops/ hota hai, project root nahi. Isliye root pehle daalo,
# TAB _paths import karo (CLAUDE.md: "subfolder me: pehle root ko sys.path pe").
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import _paths  # noqa: E402,F401  (baaki folders sys.path pe)

import base64
import datetime
import json
import os
import socket
import sys
from pathlib import Path

# ── Critical Rule 1: IPv4 force (VPS pe IPv6 default -> Dhan DH-905) ──────────
_orig_gai = socket.getaddrinfo


def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)


socket.getaddrinfo = _v4

try:
    import requests
except Exception:                                    # pragma: no cover
    requests = None

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"

DHAN_RENEW_URL = "https://api.dhan.co/v2/RenewToken"
DHAN_PROFILE_URL = "https://api.dhan.co/v2/profile"

# Expiry se itne ghante pehle renew karo. 24h token pe 12h = poora aadha din
# buffer: ek check fail bhi ho jaye to agle check pe chain bach jaati hai.
RENEW_BELOW_HOURS = 12.0
# Itna kam bacha ho to alert ka lehja badal jaata hai (ab ye urgent hai).
CRITICAL_HOURS = 3.0
_TIMEOUT = 15


# ─────────────────────────────────────────────────────────────── config i/o ──
def _read_cfg():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cfg_atomic(cfg):
    """tmp + os.replace + rolling backup.

    Purana `CONFIG_FILE.write_text(json.dumps(...))` non-atomic tha: mid-write
    kill = corrupt config = agli subah kuch bhi start na ho. Token likhne wala
    path roz chalta hai, isliye yahan atomic hona zaroori hai.
    """
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            bak = CONFIG_FILE.with_suffix(".json.bak.token")
            bak.write_text(CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    tmp = str(CONFIG_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _notify(level, msg, key, source="token"):
    """Bell + (RED ho to) phone. Kabhi raise nahi karta."""
    try:
        import notify
        (notify.error if level == "error" else notify.warn)(msg, key=key, source=source)
    except Exception as e:
        print(f"[token_refresh] notify fail: {e}", flush=True)


def _resolve(key):
    try:
        import notify
        notify.resolve(key)
    except Exception:
        pass


# ───────────────────────────────────────────────────────────────── dhan ──────
def jwt_expiry(token):
    """JWT ka exp -> naive local datetime. Parse na ho to None."""
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return datetime.datetime.fromtimestamp(
            json.loads(base64.urlsafe_b64decode(p))["exp"])
    except Exception:
        return None


def dhan_hours_left(cfg=None):
    cfg = cfg if cfg is not None else _read_cfg()
    exp = jwt_expiry(cfg.get("jwt_token") or "")
    if not exp:
        return None
    return (exp - datetime.datetime.now()).total_seconds() / 3600.0


def _dhan_token_alive(token):
    try:
        r = requests.get(DHAN_PROFILE_URL, headers={"access-token": token},
                         timeout=_TIMEOUT)
        return r.status_code == 200
    except Exception:
        return False


def renew_dhan(force=False):
    """Dhan token renew. Wapas: (ok: bool, msg: str, hours_left: float|None).

    Naya token validate hone ke BAAD hi likha jaata hai.
    """
    if requests is None:
        return False, "requests module nahi hai", None

    cfg = _read_cfg()
    old = cfg.get("jwt_token") or ""
    cid = str(cfg.get("client_id") or "")
    if not old or not cid:
        return False, "config.json me jwt_token/client_id nahi hai", None

    left = dhan_hours_left(cfg)
    if left is None:
        return False, "token ka expiry parse nahi hua", None
    if not force and left > RENEW_BELOW_HOURS:
        return True, f"abhi zaroorat nahi ({left:.1f}h bache)", left

    try:
        r = requests.get(DHAN_RENEW_URL,
                         headers={"access-token": old, "dhanClientId": cid},
                         timeout=_TIMEOUT)
    except Exception as e:
        return False, f"renew call fail: {e}", left

    if r.status_code != 200:
        return False, f"renew HTTP {r.status_code}: {str(r.text)[:160]}", left

    new = None
    try:
        j = r.json()
        for k in ("token", "accessToken", "access_token", "jwt"):
            v = j.get(k)
            if isinstance(v, str) and v.count(".") == 2:
                new = v
                break
    except Exception as e:
        return False, f"renew response parse fail: {e}", left
    if not new:
        return False, "renew response me token nahi mila", left

    # ⚠️ Validate BEFORE write — kabhi bhi unvalidated token live na ho.
    if not _dhan_token_alive(new):
        return False, "naya token validate nahi hua — purana hi rakha", left

    cfg["jwt_token"] = new
    cfg["token_saved_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        p = new.split(".")[1]
        p += "=" * (-len(p) % 4)
        cid2 = json.loads(base64.urlsafe_b64decode(p)).get("dhanClientId")
        if cid2:
            cfg["client_id"] = cid2
    except Exception:
        pass
    _write_cfg_atomic(cfg)

    new_left = dhan_hours_left(cfg)
    exp = jwt_expiry(new)
    return True, f"renew ho gaya — ab {exp:%d-%b %H:%M} tak", new_left


# ───────────────────────────────────────────────────────────────── kite ──────
def kite_status():
    """(alive: bool|None, msg). None = check hi nahi ho paya (fail-safe)."""
    cfg = _read_cfg()
    if not cfg.get("kite_api_key") or not cfg.get("kite_access_token"):
        return False, "kite_api_key / kite_access_token config me nahi hai"
    try:
        # `brokers` ek PACKAGE hai (root sys.path pe) — `kite_broker` top-level
        # module ke roop me import NAHI hota. Canonical entry = get_broker()
        # (Rule 6B: broker class se jao, uske internals se nahi).
        from brokers import get_broker
        f = get_broker("kite").funds() or {}
        if not f:
            return None, "funds() khaali laut aaya — token ka pata nahi chala"
        return True, "zinda (funds read OK)"
    except Exception as e:
        s = str(e)
        if "Token" in s or "token" in s or "expired" in s.lower() or "Incorrect" in s:
            return False, f"token expire/invalid: {s[:120]}"
        return None, f"check nahi ho paya: {s[:120]}"


def kite_login_url():
    try:
        cfg = _read_cfg()
        k = cfg.get("kite_api_key") or ""
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={k}" if k else ""
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────── orchestrate ──
def check(do_notify=True, force=False):
    """Ek poora cycle. Wapas: dict (scheduler/health ke liye)."""
    out = {"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # ── Dhan ──────────────────────────────────────────────────────────────
    left = dhan_hours_left()
    out["dhan_hours_left_before"] = round(left, 2) if left is not None else None
    ok, msg, left2 = renew_dhan(force=force)
    out["dhan_ok"] = bool(ok)
    out["dhan_msg"] = msg
    out["dhan_hours_left"] = round(left2, 2) if left2 is not None else None
    print(f"[token_refresh] dhan: {'OK ' if ok else 'FAIL'} — {msg}", flush=True)

    # Safe mode: Dhan token SACH ME mar chuka ho to system data-andha hai — nayi
    # LIVE entry nahi honi chahiye. Sirf expire pe (renew fail ≠ expire; buffer
    # ke andar fail hona normal retry hai).
    try:
        import safe_mode as _sm
        if left2 is not None and left2 <= 0:
            _sm.trip("data_auth", f"Dhan token expire ({left2:.1f}h)")
        elif ok and left2 is not None and left2 > 0:
            _sm.clear("data_auth")
    except Exception:
        pass

    if do_notify:
        if ok:
            _resolve("token:dhan")
        else:
            urgent = (left2 is not None and left2 < CRITICAL_HOURS)
            head = "🔴 DHAN TOKEN" if urgent else "Dhan token"
            tail = (f"sirf {left2:.1f}h bache — market se pehle Control tab me naya "
                    f"token daaliye." if urgent else
                    "auto-renew fail hua, agla attempt agle cycle me.")
            _notify("error", f"{head}: {msg}. {tail}", key="token:dhan", source="dhan")

    # ── Kite (alert-only, by design) ──────────────────────────────────────
    alive, kmsg = kite_status()
    out["kite_alive"] = alive
    out["kite_msg"] = kmsg
    print(f"[token_refresh] kite: {alive} — {kmsg}", flush=True)

    # Safe mode: Kite dead = LIVE order aur EXIT dono fail honge. Nayi live entry
    # band karo (exit attempts jaari rehte hain — wo alag path hai).
    # alive None (network glitch) pe kuch mat karo — jhoothe trip se live trading
    # bina wajah rukegi.
    try:
        import safe_mode as _sm
        if alive is False:
            _sm.trip("broker_auth", kmsg)
        elif alive is True:
            _sm.clear("broker_auth")     # subah login karte hi khud bahaal
    except Exception:
        pass

    if do_notify:
        if alive is True:
            _resolve("token:kite")
        elif alive is False:
            url = kite_login_url()
            _notify("error",
                    "Kite token chahiye — LIVE order/exit iske bina fail honge. "
                    + (f"Login: {url}" if url else "Control tab me request_token daaliye."),
                    key="token:kite", source="kite")
        # alive is None -> check hi nahi ho paya; jhootha alarm nahi bhejte

    return out


def main(argv):
    force = "--force" in argv
    status_only = "--status" in argv
    as_json = "--json" in argv

    if status_only:
        left = dhan_hours_left()
        alive, kmsg = kite_status()
        try:
            import safe_mode as _sm
            sm = _sm.status()
        except Exception:
            sm = None
        res = {"dhan_hours_left": round(left, 2) if left is not None else None,
               "kite_alive": alive, "kite_msg": kmsg, "safe_mode": sm}
        print(json.dumps(res) if as_json else res)
        return 0

    res = check(do_notify=True, force=force)
    if as_json:
        print(json.dumps(res))
    # 0 = sab theek | 3 = chala, par insaan ko dekhna chahiye
    # (3 isliye, 1 nahi: crash bhi 1 deta hai — dono ko alag rakhna zaroori hai,
    #  warna systemd ek CRASHED refresher ko bhi "Finished successfully" dikhata
    #  hai aur ye guard chup-chaap mar jaata hai)
    return 0 if (res.get("dhan_ok") and res.get("kite_alive") is not False) else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
