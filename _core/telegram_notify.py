"""telegram_notify.py — trade ENTRY/EXIT ka Telegram alert (screen ghoorne se aazadi).

KYUN (2026-08-11):
  User ko din bhar dashboard dekhna padta tha ki kab entry aayi — us chakkar me
  overtrade ho jaata tha. Ab har asli entry/exit ka message seedha Telegram pe.
  Ek hi jagah hook hai: `execution_gateway.execute_signal` / `execute_exit` —
  jahaan se HAR strategy (Ars chain, RSI, straddle, webhook, universe) enter/exit
  karti hai. Isliye abhi + future ki har strategy apne-aap covered, koi duplicate
  nahi.

DESIGN (money-path safe, [[feedback_proactive_safety_philosophy]]):
  - FAIL-SAFE: koi bhi dikkat (config toota, network down, token galat) → chup-chaap
    swallow, kabhi raise nahi. Ek notification jo order-flow tode wo us problem se
    bada masla hai jo ye solve karti hai.
  - NON-BLOCKING: message ek daemon thread pe jaata hai (short timeout). Strategy
    loop kabhi Telegram ke network pe wait nahi karti. Volume kam hai → per-message
    thread theek hai.
  - CONFIG-DRIVEN FILTER: `data/telegram_config.json`. Default = sirf LIVE entries
    (paper ki spam nahi — 13 paper strategies chal rahi hain). mtime-cache: file
    edit karo, restart nahi chahiye.
  - DEDUP: same message _DEDUP_SECS ke andar dobara na jaaye (order-chase/retry se
    do baar fire ho sakta hai).

CONFIG (data/telegram_config.json):
  {
    "enabled": true,
    "bot_token": "123456:ABC...",     # @BotFather se
    "chat_id": "12345678",            # apna chat id (getchat se nikaalo)
    "notify_modes": ["live"],         # kaunse mode: "live" aur/ya "paper"
    "notify_strategies": [],          # khaali = allowed-mode ki SAARI; warna sirf ye ids/config-keys
    "notify_entries": true,
    "notify_exits": true,
    "notify_blocked": false           # RMS-blocked entry ka bhi alert chahiye?
  }

CLI (setup/test):
    python _core/telegram_notify.py getchat   # bot ko message bhejo, phir ye chat_id dhoondh dega
    python _core/telegram_notify.py test       # ek test message bhejo
    python _core/telegram_notify.py status      # config kya hai
"""
import json
import os
import threading
import time
from pathlib import Path

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_FILE = DATA_DIR / "telegram_config.json"

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 6                 # network — chhota, loop kabhi lambा wait na kare (background thread me bhi)
_DEDUP_SECS = 20             # same text ka repeat is window me suppress

_DEFAULT = {
    "enabled": False,
    "bot_token": "",
    "chat_id": "",
    "notify_modes": ["live"],
    "notify_strategies": [],
    "notify_entries": True,
    "notify_exits": True,
    "notify_blocked": False,
}

# mtime-cache: config file edit → agla read naya config le lega, restart nahi chahiye
_cfg_cache = {"mtime": None, "data": None}
_recent = {}          # text -> last_sent_epoch (dedup)
_recent_lock = threading.Lock()


def _load_config():
    """Config padho (mtime-cache). File na ho/toota ho → defaults (disabled)."""
    try:
        if not CONFIG_FILE.exists():
            return dict(_DEFAULT)
        mt = CONFIG_FILE.stat().st_mtime
        if _cfg_cache["mtime"] == mt and _cfg_cache["data"] is not None:
            return _cfg_cache["data"]
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        cfg = dict(_DEFAULT)
        cfg.update({k: raw[k] for k in raw if k in _DEFAULT})
        _cfg_cache["mtime"] = mt
        _cfg_cache["data"] = cfg
        return cfg
    except Exception:
        return dict(_DEFAULT)


def is_enabled():
    c = _load_config()
    return bool(c.get("enabled") and c.get("bot_token") and c.get("chat_id"))


def _strat_label(strategy_id):
    try:
        import strategy_registry as sr
        return sr.label(strategy_id, with_name=True)
    except Exception:
        return str(strategy_id or "")


def _should_send(strategy_id, mode, kind):
    """kind: 'entry' | 'exit' | 'blocked'. Config filter."""
    c = _load_config()
    if not (c.get("enabled") and c.get("bot_token") and c.get("chat_id")):
        return False
    if kind == "entry" and not c.get("notify_entries", True):
        return False
    if kind == "exit" and not c.get("notify_exits", True):
        return False
    if kind == "blocked" and not c.get("notify_blocked", False):
        return False
    modes = [str(m).lower() for m in (c.get("notify_modes") or ["live"])]
    if str(mode or "").lower() not in modes:
        return False
    only = c.get("notify_strategies") or []
    if only:
        # strategy_id ya uska resolved id — dono se match dekho
        sid = str(strategy_id or "")
        rid = sid
        try:
            import strategy_registry as sr
            rid = sr.resolve(sid) or sid
        except Exception:
            pass
        allow = {str(x) for x in only}
        if sid not in allow and rid not in allow:
            return False
    return True


def _post(text):
    """Actual Telegram HTTP call. Background thread me chalta hai. Kabhi raise nahi."""
    try:
        if requests is None:
            return
        c = _load_config()
        token, chat = c.get("bot_token"), c.get("chat_id")
        if not (token and chat):
            return
        url = _API.format(token=token, method="sendMessage")
        requests.post(url, json={
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=_TIMEOUT)
    except Exception:
        pass   # money-path safe — network fail order-flow ko kabhi na tode


def _dispatch(text):
    """Dedup + fire-and-forget daemon thread. Trading loop kabhi wait nahi karti."""
    try:
        now = time.time()
        with _recent_lock:
            last = _recent.get(text, 0)
            if now - last < _DEDUP_SECS:
                return
            _recent[text] = now
            # halka housekeeping — map ko bounded rakho
            if len(_recent) > 200:
                for k in [k for k, v in _recent.items() if now - v > _DEDUP_SECS * 3]:
                    _recent.pop(k, None)
        t = threading.Thread(target=_post, args=(text,), daemon=True)
        t.start()
    except Exception:
        pass


def _fmt(side, trad_sym, qty, price):
    px = f"₹{price:.2f}" if isinstance(price, (int, float)) and price else "—"
    return f"{side} <b>{trad_sym}</b> ×{qty} @ {px}"


def notify_entry(strategy_id, trad_sym, side, qty, price=None, mode="paper",
                 status="", tag=""):
    """Ek entry ka Telegram alert. execution_gateway se call hota hai. Fail-safe."""
    try:
        if not _should_send(strategy_id, mode, "entry"):
            return
        tag_txt = f" · {mode.upper()}" if mode else ""
        text = (f"🟢 <b>ENTRY</b>{tag_txt}\n"
                f"{_strat_label(strategy_id)}\n"
                f"{_fmt(side, trad_sym, qty, price)}")
        _dispatch(text)
    except Exception:
        pass


def notify_exit(strategy_id, trad_sym, exit_side, qty, price=None, mode="paper",
                reason="", tag=""):
    """Ek exit ka Telegram alert. Fail-safe."""
    try:
        if not _should_send(strategy_id, mode, "exit"):
            return
        tag_txt = f" · {mode.upper()}" if mode else ""
        rsn = f"\n<i>{reason}</i>" if reason else ""
        text = (f"🔴 <b>EXIT</b>{tag_txt}\n"
                f"{_strat_label(strategy_id)}\n"
                f"{_fmt(exit_side, trad_sym, qty, price)}{rsn}")
        _dispatch(text)
    except Exception:
        pass


def notify_blocked(strategy_id, trad_sym, side, reason="", mode="live"):
    """RMS ne entry block ki — optional alert (default off). Fail-safe."""
    try:
        if not _should_send(strategy_id, mode, "blocked"):
            return
        text = (f"⛔ <b>BLOCKED</b>\n"
                f"{_strat_label(strategy_id)}\n"
                f"{side} {trad_sym} — {reason}")
        _dispatch(text)
    except Exception:
        pass


def send_raw(text):
    """Koi bhi custom text (health-check summary etc.). Dedup lagta hai."""
    try:
        if not is_enabled():
            return
        _dispatch(str(text))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Config CRUD — dashboard settings panel ke liye
# ─────────────────────────────────────────────────────────────────────────────

def _write_config(cfg):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)
        _cfg_cache["mtime"] = None   # force reload next read
        return True
    except Exception as e:
        print(f"[telegram] config write fail: {e}", flush=True)
        return False


def get_config_masked():
    """UI ke liye config — token masked (kabhi poora token wire pe nahi bhejenge)."""
    c = _load_config()
    out = dict(c)
    tok = c.get("bot_token") or ""
    out["has_token"] = bool(tok)
    out["bot_token"] = (tok[:10] + "…") if tok else ""
    out["ready"] = is_enabled()
    return out


def save_config(patch):
    """UI se aaya patch merge karke likho. bot_token khaali/masked aaye to purana
    rakho (baaki fields save karte waqt token wipe na ho). Wapas: masked config."""
    try:
        cur = dict(_load_config())
        patch = patch or {}
        for k in _DEFAULT:
            if k == "bot_token":
                nt = str(patch.get("bot_token") or "").strip()
                # masked/khaali → chhedo mat; naya poora token (":" wala) → update
                if nt and "…" not in nt and ":" in nt:
                    cur["bot_token"] = nt
                continue
            if k in patch:
                cur[k] = patch[k]
        _write_config(cur)
        return get_config_masked()
    except Exception as e:
        print(f"[telegram] save_config fail: {e}", flush=True)
        return get_config_masked()


def detect_chat_ids():
    """getUpdates se chat_id(s) nikaalo (user ne bot ko message bheja ho to)."""
    try:
        c = _load_config()
        token = c.get("bot_token")
        if not token:
            return {"ok": False, "error": "bot_token nahi hai — pehle token save karo."}
        if requests is None:
            return {"ok": False, "error": "requests missing"}
        r = requests.get(_API.format(token=token, method="getUpdates"), timeout=10)
        data = r.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("description") or "getUpdates fail (token galat?)"}
        seen = {}
        for u in data.get("result", []):
            msg = u.get("message") or u.get("channel_post") or u.get("edited_message") or {}
            chat = msg.get("chat") or {}
            if chat.get("id") is not None:
                nm = chat.get("title") or (str(chat.get("first_name", "")) + " " + str(chat.get("username", ""))).strip()
                seen[str(chat["id"])] = nm or "(no name)"
        chats = [{"chat_id": k, "name": v} for k, v in seen.items()]
        return {"ok": True, "chats": chats,
                "hint": "" if chats else "Koi message nahi mila. Apne bot ko Telegram pe koi message bhejo, phir dobara Detect."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_test():
    """UI ka Test button — abhi seedha bhejo (dedup ke bina, sync so error dikhe)."""
    try:
        c = _load_config()
        token, chat = c.get("bot_token"), c.get("chat_id")
        if not (token and chat):
            return {"ok": False, "error": "token/chat_id missing"}
        if requests is None:
            return {"ok": False, "error": "requests missing"}
        r = requests.post(_API.format(token=token, method="sendMessage"),
                          json={"chat_id": chat,
                                "text": f"✅ CODE3B Telegram alert connected — {time.strftime('%Y-%m-%d %H:%M:%S')} IST",
                                "parse_mode": "HTML"}, timeout=_TIMEOUT)
        j = r.json()
        if j.get("ok"):
            return {"ok": True}
        return {"ok": False, "error": j.get("description") or "sendMessage fail"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI — setup / test
# ─────────────────────────────────────────────────────────────────────────────

def _cli_getchat():
    """Bot ko koi message bhejo, phir ye getUpdates se chat_id nikaal deta hai."""
    c = _load_config()
    token = c.get("bot_token")
    if not token:
        print("bot_token config me daalo pehle (data/telegram_config.json).")
        return
    if requests is None:
        print("requests missing.")
        return
    r = requests.get(_API.format(token=token, method="getUpdates"), timeout=10)
    data = r.json()
    seen = {}
    for u in data.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id") is not None:
            seen[chat["id"]] = chat.get("title") or (chat.get("first_name", "") + " " + chat.get("username", "")).strip()
    if not seen:
        print("Koi message nahi mila. Pehle apne bot ko Telegram pe /start ya koi bhi message bhejo, phir dobara chalao.")
        return
    print("Mile chat_id(s):")
    for cid, name in seen.items():
        print(f"  chat_id = {cid}   ({name})")
    print("\nInme se apna wala config ke chat_id me daal do.")


def _cli_test():
    if not is_enabled():
        print("Telegram enabled nahi / token/chat_id missing hai. data/telegram_config.json check karo.")
        return
    _post(f"✅ CODE3B Telegram alert test — {time.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print("Test message bhej diya (Telegram check karo).")


def _cli_status():
    c = _load_config()
    safe = dict(c)
    if safe.get("bot_token"):
        safe["bot_token"] = safe["bot_token"][:8] + "…"
    print(json.dumps(safe, indent=2, ensure_ascii=False))
    print("enabled+ready:", is_enabled())


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"getchat": _cli_getchat, "test": _cli_test, "status": _cli_status}.get(cmd, _cli_status)()
