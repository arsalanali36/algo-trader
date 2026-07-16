#!/usr/bin/env python3
r"""session_guard.py — ek waqt me ek hi Claude session is repo ko likh sake.

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

SCOPE (2026-07-16, audit me pakda — ye guard KHUD is bug ka shikaar tha):
  Hook do jagah register hai, aur dono zaroori hain:
    - `CODE3B/.claude/settings.json`  → CODE3B se khuli session
    - `<parent>/.claude/settings.json` → `D:\KHAZANA\KHAZANA\PYTHON` se khuli session
  Kyun: hook ka command `$CLAUDE_PROJECT_DIR/.claude/session_guard.py` resolve hota
  hai. Sirf CODE3B me register tha, to parent se khuli session ye script chalati hi
  nahi thi — na lock leti, na deny hoti. Yaani jis cheez ko rokne ke liye guard bana
  tha (do sessions, ek repo), guard usi ke against bekaar tha. Live pakda gaya: ek
  session CODE3B me commit kar rahi thi jab parent-rooted session usi file pe kaam
  karne ja rahi thi.

  Isi wajah se `_targets_repo()` hai: parent se khuli session CODE7/CODE2 ka bhi kaam
  karti hai, aur wo writes IS repo ka lock nahi maangne chahiye. LOCK/REPO dono
  `__file__` se aate hain, to lock hamesha ISI repo ka lagta hai — chahe hook kahin
  se bhi chale.

FAIL-OPEN: koi bhi gadbad (JSON toota, disk fail, session_id nahi mila) → allow.
  Ek guard jo kaam rok de, us problem se bada masla hai jo ye rokta hai.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, ".session_lock.json")

# Is repo ki jad (.claude/ ka parent). LOCK aur REPO dono __file__ se aate hain,
# isliye ye guard kahin se bhi register ho — lock hamesha ISI repo ka lagta hai.
REPO = os.path.dirname(HERE)

# Itni der koi write na aaye to lock chhod do. (Live-verified 2026-07-16.) Ek session jo 40 min soch raha hai
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


def _norm(p):
    """Windows-safe path compare form: absolute, lowercase, forward slashes."""
    try:
        n = os.path.normcase(os.path.abspath(str(p)))
    except Exception:
        return ""
    n = n.replace("\\", "/")
    while "//" in n:
        n = n.replace("//", "/")
    return n


_REPO_N = _norm(REPO)


def _under_repo(p):
    n = _norm(p)
    return bool(n) and (n == _REPO_N or n.startswith(_REPO_N + "/"))


def _targets_repo(tool, tool_input, cwd):
    """Kya ye write SACH ME is repo ko chhu raha hai?

    Guard ab parent-rooted session me bhi register hota hai (dekho module docstring
    ka SCOPE note), isliye ye check zaroori hai — warna CODE7/CODE2 ka koi edit
    CODE3B ka lock le leta aur CODE3B ki session ko bina wajah block kar deta.
    Shak ho to True (guard lagao) — ek extra lock, chhoote hue lock se behtar hai.
    """
    ti = tool_input or {}
    if tool in _WRITE_TOOLS:
        fp = ti.get("file_path") or ti.get("notebook_path") or ""
        return _under_repo(fp) if fp else False
    if tool in ("Bash", "PowerShell"):
        # `cd "<repo>" && git commit ...` — cwd payload me session ka hota hai,
        # command ka nahi; isliye dono dekho.
        cmd = str(ti.get("command") or "")
        cmd_n = cmd.lower().replace("\\\\", "/").replace("\\", "/")
        while "//" in cmd_n:
            cmd_n = cmd_n.replace("//", "/")
        if _REPO_N and _REPO_N in cmd_n:
            return True
        return _under_repo(cwd) if cwd else False
    return False


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _emit_allow()          # stdin toota — guard ki wajah se kaam na ruke

    sid = str(payload.get("session_id") or "")
    tool = payload.get("tool_name") or ""
    tin = payload.get("tool_input")
    if not sid or not _is_write(tool, tin):
        _emit_allow()

    # Doosre repo ka write → hamara lock isse koi matlab nahi rakhta.
    if not _targets_repo(tool, tin, payload.get("cwd") or ""):
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
