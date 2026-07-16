#!/usr/bin/env python3
"""session_guard.py — ek waqt me ek hi Claude session is repo ko likh sake.

KYUN (2026-07-16):
  Do Claude sessions ek saath is repo + VPS pe kaam kar rahe the. Dono ko pata
  nahi tha. Nateeja: ek session ka uncommitted edit doosre ke `git add` me sweep
  hoke uske commit me chala gaya (galat message, galat authorship); VPS pe dono
  alag-alag `systemctl restart` maar rahe the, jisse ek strategy chup-chaap mar
  gayi aur uski diagnosis me kaafi waqt gaya.

  Ye guard us faisle ko user ke sar se hata deta hai. Pehla session lock le leta
  hai; doosra session ka koi bhi WRITE (Edit/Write ya git commit/push, ya VPS
  deploy) turant block ho jaata hai — saaf message ke saath. Padhna/khojna kabhi
  block nahi hota, to doosra session dekh-samajh sakta hai, bas likh nahi sakta.

KAISE:
  PreToolUse hook (.claude/settings.json). stdin pe Claude ka JSON aata hai
  (session_id + tool_name + tool_input), stdout pe faisla.

  - Lock khaali / purana (TTL beet gaya) / apna hi hai  → le lo, aage badho
  - Kisi aur ka zinda lock                              → deny + batao kaun

  Lock har write pe refresh hota hai (heartbeat). Session crash/band ho jaye to
  lock TTL ke baad khud mar jaata hai — koi manual safai nahi, koi stale-lock
  trap nahi (wahi design jo `_core/singleton_guard.py` strategies ke liye karta
  hai, bas yahan file-mtime ki jagah explicit timestamp).

FAIL-OPEN: koi bhi gadbad (JSON toota, disk fail, session_id nahi mila) → allow.
  Ek guard jo kaam rok de, us problem se bada masla hai jo ye rokta hai.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, ".session_lock.json")

# Itni der koi write na aaye to lock chhod do. Ek session jo 40 min soch raha hai
# wo lock kho dega — theek hai: wo agla write karte hi wapas le lega (agar tab tak
# koi aur na le chuka ho). Isse chhota rakha to sessions aapas me lock cheenenge;
# bada rakha to ek band session doosre ko der tak rokega.
TTL_SECS = 30 * 60

# Sirf ye cheezein lock maangti hain. Baaki sab (Read/Grep/Glob/git status/log/
# diff, koi bhi read-only ssh) hamesha allow — doosra session dekh sakta hai.
_WRITE_TOOLS = {"Edit", "Write", "NotebookEdit"}

# Bash tabhi guard hota hai jab command sach me kuch BADALTI ho — repo me ya VPS pe.
_WRITE_CMDS = (
    "git commit", "git add", "git push", "git merge", "git rebase", "git reset",
    "git checkout", "git restore", "git stash", "git cherry-pick", "git revert",
    "git pull", "git clean", "git apply", "git rm", "git mv",
    "systemctl restart", "systemctl stop", "systemctl start",  # VPS services
    "scp ", "rsync ",                                           # VPS deploy
)


def _emit_allow():
    sys.exit(0)


def _emit_deny(reason, holder_age_min):
    """PreToolUse deny — Claude ko reason milta hai, user ko ek line."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "systemMessage": (
            f"🔒 Doosra Claude session is repo pe kaam kar raha hai "
            f"(~{holder_age_min} min pehle tak active). Ye session sirf padh sakta hai. "
            f"Us session ko band karo, ya {TTL_SECS // 60} min ruko — lock khud chhoot jaayega."
        ),
    }
    print(json.dumps(out))
    sys.exit(0)


def _is_write(tool, tool_input):
    if tool in _WRITE_TOOLS:
        return True
    if tool in ("Bash", "PowerShell"):
        cmd = str((tool_input or {}).get("command") or "").lower()
        return any(w in cmd for w in _WRITE_CMDS)
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _emit_allow()          # stdin toota — guard ki wajah se kaam na ruke

    sid = str(payload.get("session_id") or "")
    tool = payload.get("tool_name") or ""
    if not sid or not _is_write(tool, payload.get("tool_input")):
        _emit_allow()

    now = int(time.time())
    holder = None
    try:
        if os.path.exists(LOCK):
            holder = json.load(open(LOCK, encoding="utf-8"))
    except Exception:
        holder = None          # corrupt lock = koi lock nahi

    if holder:
        age = now - int(holder.get("ts") or 0)
        other = str(holder.get("session_id") or "")
        if other and other != sid and age < TTL_SECS:
            _emit_deny(
                f"Is repo ka write-lock doosre Claude session ke paas hai "
                f"(session {other[:8]}, {age // 60} min pehle tak active). "
                f"Do sessions ek saath likhein to ek ka kaam doosre ke commit me "
                f"chala jaata hai. Ye {tool} call block ki gayi. "
                f"Padhna/khojna abhi bhi chalta hai — user ko batao ki doosra session "
                f"band kare, ya {TTL_SECS // 60} min baad lock apne aap chhoot jaayega.",
                age // 60,
            )

    # Lock apna hai / khaali hai / mar chuka hai → le lo + heartbeat refresh
    try:
        tmp = LOCK + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"session_id": sid, "ts": now, "pid": os.getpid()}, f)
        os.replace(tmp, LOCK)
    except Exception:
        pass                   # lock likh na paye to bhi kaam rokna nahi

    _emit_allow()


if __name__ == "__main__":
    main()
