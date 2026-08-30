"""SAFE MODE — jab broker/token bharosemand na ho to LIVE entry band, exit jaari.

WHY
---
Autonomy audit (2026-08-30) ka blocker #3. System ke paas sirf DO state the:
"chal raha hai" ya "band hai". Beech ka — *"broker abhi bharosemand nahi hai"* —
kabhi tha hi nahi. Nateeja:

    token mar gaya  ->  signal banta rahega, gate pass karega, naya LIVE order
                        try karega... jabki USI waqt exit order complete nahi ho
                        sakta (_do_squareoff har 5s "leaving position open,
                        will retry" karta rehta hai).

Yaani sabse kharab combination: nayi risk khulti rehti hai jab purani band nahi
ho sakti. Safe mode us beech wale state ko asli banata hai:

    LIVE ENTRY  -> BAND (execution_gateway.execute_signal me refuse)
    EXIT        -> JAARI (kabhi block nahi — position band karna hamesha allowed)
    PAPER       -> JAARI (data collection kabhi nahi rukti — user ki explicit rule)
    ALARM       -> notify.error(key="safe_mode") -> phone (TRAP #192 bridge)

DESIGN
------
* **Sirf `mode == "live"` pe bite karta hai.** Paper lane (14+ strategies ka
  data collection, option-chain lake) ko ye chhuta bhi nahi. Blanket gateway-gate
  lagana sabse aasan galti hoti — wo paper ko bhi maar deta.
* **Exit kabhi block nahi.** Gate `execute_signal` (= ENTRY) me hai;
  `execute_exit`/`execute_basket_exit` alag functions hain, unhe chhua nahi.
* **Fail-OPEN on evaluation error.** Agar is module me hi koi dikkat ho to live
  trading nahi rukni chahiye — wo status-quo se bura hai. (Wahi convention jo
  `check_broker_funds` + trading-day guard follow karte hain.)
* **Disk-persisted** (`data/safe_mode.json`) — restart pe state zinda rehti hai
  (PRE-MORTEM shape #3). Day-scoped NAHI: mara hua token agle din bhi mara hai.
* **Khud saaf hota hai.** Token wapas zinda -> `clear()` -> live entry apne aap
  bahaal. Isi liye ye 1-mahina-akela wale sawaal me kaam ka hai: subah aap Kite
  login karo, 08:20 ka token check dekhta hai, safe mode khud hat jaata hai.

TRIGGERS (kaun trip karta hai)
------------------------------
  broker_auth     `_ops/token_refresh` — Kite token dead (live order fail honge)
  data_auth       `_ops/token_refresh` — Dhan token EXPIRE ho gaya (data andha)
  order_failures  `execution_gateway`  — lagatar N live order fail

CLI
---
    python -X utf8 _core/safe_mode.py            # status
    python -X utf8 _core/safe_mode.py --clear    # sab reasons hatao (manual)
"""

import json
import os
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / "data" / "safe_mode.json"

# Itne LAGATAR live-order failures ke baad maan lo ki broker-side kuch toota hai.
# 3 = ek transient reject/network blip se trip nahi hoga, par asli auth/margin
# problem 3 signals me pakdi jayegi.
ORDER_FAIL_STREAK = 3

_REASON_LABEL = {
    "broker_auth": "Kite token dead — LIVE order/exit fail honge",
    "data_auth": "Dhan token expire — data andha",
    "order_failures": "lagatar live order fail ho rahe hain",
}


# ──────────────────────────────────────────────────────────────── state i/o ──
def _read():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault("reasons", {})
            d.setdefault("order_fail", {"streak": 0, "last": 0})
            return d
    except Exception:
        pass
    return {"reasons": {}, "order_fail": {"streak": 0, "last": 0}}


def _write(st):
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        print(f"[safe_mode] state write fail: {e}", flush=True)


def _notify_tripped(st):
    """Ek hi alert, saare reasons mila ke — phir bhi bell/phone dono pe."""
    try:
        import notify
        rs = st.get("reasons") or {}
        if not rs:
            notify.resolve("safe_mode")
            return
        bits = "; ".join(f"{k} ({v.get('detail') or _REASON_LABEL.get(k, '')})"
                         for k, v in sorted(rs.items()))
        notify.error(
            "🛑 SAFE MODE — nayi LIVE entry band (exit jaari, paper jaari). " + bits,
            key="safe_mode", source="safe_mode")
    except Exception as e:
        print(f"[safe_mode] notify fail: {e}", flush=True)


# ──────────────────────────────────────────────────────────────────── api ────
def trip(reason, detail=""):
    """Safe mode ON kar do is reason ke liye. Idempotent (since preserve hota)."""
    try:
        st = _read()
        was = reason in st["reasons"]
        if not was:
            st["reasons"][reason] = {"since": int(time.time()), "detail": str(detail)[:200]}
            _write(st)
            print(f"[safe_mode] 🛑 TRIPPED: {reason} — {detail}", flush=True)
            _notify_tripped(st)
        elif detail and st["reasons"][reason].get("detail") != str(detail)[:200]:
            st["reasons"][reason]["detail"] = str(detail)[:200]
            _write(st)
        return True
    except Exception as e:
        print(f"[safe_mode] trip fail: {e}", flush=True)
        return False


def clear(reason=None):
    """Ek reason (ya sab, reason=None) hatao. Aakhri hatte hi 'fixed' jaata hai."""
    try:
        st = _read()
        if reason is None:
            had = bool(st["reasons"])
            st["reasons"] = {}
            st["order_fail"] = {"streak": 0, "last": 0}
        else:
            had = reason in st["reasons"]
            st["reasons"].pop(reason, None)
            if reason == "order_failures":
                st["order_fail"] = {"streak": 0, "last": 0}
        if had:
            _write(st)
            print(f"[safe_mode] ✅ CLEARED: {reason or 'all'}", flush=True)
            if not st["reasons"]:
                _notify_tripped(st)      # koi reason nahi bacha -> resolve
        return True
    except Exception as e:
        print(f"[safe_mode] clear fail: {e}", flush=True)
        return False


def active():
    """{reason: {since, detail}} — khaali dict = system healthy."""
    try:
        return dict(_read().get("reasons") or {})
    except Exception:
        return {}


def is_tripped():
    return bool(active())


def blocks_live_entry():
    """Gateway ke liye: (block: bool, reason_text: str).

    FAIL-OPEN — is module me error aaye to trading nahi rukegi.
    """
    try:
        rs = active()
        if not rs:
            return False, ""
        return True, ",".join(sorted(rs.keys()))
    except Exception:
        return False, ""


def note_order_result(mode, ok, detail=""):
    """LIVE order ka nateeja record karo. Lagatar N fail -> trip.

    PAPER orders counter ko chhute bhi nahi (paper fail broker ke baare me kuch
    nahi kehta).
    """
    try:
        if str(mode) != "live":
            return
        st = _read()
        of = st.get("order_fail") or {"streak": 0, "last": 0}
        if ok:
            if of.get("streak"):
                of["streak"] = 0
                st["order_fail"] = of
                _write(st)
                clear("order_failures")
            return
        of["streak"] = int(of.get("streak") or 0) + 1
        of["last"] = int(time.time())
        st["order_fail"] = of
        _write(st)
        if of["streak"] >= ORDER_FAIL_STREAK:
            trip("order_failures",
                 f"{of['streak']} lagatar live order fail — {str(detail)[:120]}")
    except Exception as e:
        print(f"[safe_mode] note_order_result fail: {e}", flush=True)


def status():
    st = _read()
    return {
        "tripped": bool(st.get("reasons")),
        "reasons": st.get("reasons") or {},
        "order_fail_streak": int((st.get("order_fail") or {}).get("streak") or 0),
        "blocks": "live entries only (exits + paper unaffected)",
    }


if __name__ == "__main__":
    import sys
    if "--clear" in sys.argv:
        clear(None)
    print(json.dumps(status(), indent=2))
