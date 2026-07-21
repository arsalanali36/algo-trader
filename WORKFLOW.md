# CODE3B — Parallel Work Workflow

> **Problem jo ye solve karta hai:** sab kaam ek session me ghusa hua tha → ek
> research backtest bhi live-fix ka wait karta tha, aur session_guard sab writes
> ko serialize kar deta tha. Ilaaj: kaam ko **do lanes** me baant do.

---

## Golden rule — do lanes

| | 🔴 **LIVE lane** | 🟢 **RESEARCH lane** |
|---|---|---|
| Kya | deploy, ek bug fix, VPS change, `_core/` order-path | backtest, research, naya isolated feature, tests |
| Nature | dependent, money-path, ek shared resource (VPS/Dhan) | independent, reversible, read-heavy |
| Kahan | **CODE3B main folder** (ye repo, master) | **worktree** (`.claude/worktrees/<name>`, apni branch) |
| Sessions | **EK** session — serial + guarded (ye SAFETY hai) | **jitni** chahiye — har worktree apne lock pe |
| Rule | fast karne ki koshish mat karo = zyada risk | jitna parallel kar sako karo |

**Ek line:** serial cheez ko fast mat karo, parallel cheez ko serial mat rakho.

---

## Kyun ye kaam karta hai (session_guard + worktree)

`.claude/session_guard.py` ek waqt me ek hi session ko is repo pe **likhne** deta hai
(padhna hamesha allowed). Ye jaan-boojh ke hai — do sessions ek saath likhein to ek
ka edit doosre ke commit me chala jaata hai + VPS pe do restart ek strategy maar dete
hain (2026-07-16 ka asli incident).

**Trick:** `.claude/` git me tracked hai → har worktree ko banate hi apni copy milti
hai, aur uska lock file (`.claude/.session_lock.json`) gitignored + per-folder hota
hai. Matlab:

- Main folder ki session → main ka lock
- Worktree-A ki session → worktree-A ka apna lock
- Worktree-B ki session → worktree-B ka apna lock

Teeno ek saath likh sakti hain, kabhi collide nahi (alag folder = alag files = alag
lock). `.git` shared hai par git alag branches ke commits khud sambhaal leta hai.

**"🔒 Doosra session..." deny mila?** Guard theek kaam kar raha hai — aap main folder
me ho aur koi aur bhi main folder pe likh raha hai. Options: (a) us session ko band
karo, (b) 15 min ruko (lock khud chhoot jaata hai), ya (c) apna kaam research-lane hai
to worktree me le jao (neeche).

---

## Worktree — ek command me

Helper: `scripts/worktree.sh` (git-bash / Bash tool se chalao).

```bash
# Naya research worktree banao (origin/master se fresh branch)
bash scripts/worktree.sh new range-strangle
#  → .claude/worktrees/range-strangle banega, branch wt/range-strangle
#  → ab ek NAYI Claude session ISI folder me kholo

# Kaunse worktrees chal rahe hain
bash scripts/worktree.sh list

# Kaam khatam (merge/push kar chuke ho) → worktree hatao
bash scripts/worktree.sh done range-strangle
```

**Worktree ke andar ki session normal git use karti hai** — `git add/commit`, phir
`git push origin wt/range-strangle`. Master me merge main-lane se (ya PR se) karo.

---

## Background agents — lambe kaam watch mat karo

Jo kaam ghanta lega (backtest sweep, poore codebase ki search, F&O universe scan) —
usko **background agent** me pheko, wo chalta rahe, aap dusra kaam karo. Done hone pe
notify ho jaata hai — progress bar dekhne ki zaroorat nahi.

Kab use karo:
- Lamba backtest / optimize sweep (`run_hunt.py`, `scripts/*_sweep.py`)
- "Poore repo me X kahan use hua" type deep search
- Ek saath 3-4 independent strategies ka backtest

Kab NA karo: live deploy, ek specific bug ka debug (wo interactive + serial hai).

---

## Decision table — "ye kaam kahan karun?"

| Kaam | Lane | Kaise |
|------|------|-------|
| VPS pe deploy / live bug fix | 🔴 LIVE | main folder, ek session |
| `_core/` order-path edit | 🔴 LIVE | main folder — **kabhi worktree/loop se nahi** |
| Naya backtest / research | 🟢 RESEARCH | `worktree.sh new` → nayi session |
| Naya isolated feature (jo `_core/` na chhue) | 🟢 RESEARCH | worktree |
| Lamba sweep / deep search | 🟢 RESEARCH | background agent |
| "X kahan hai" quick lookup | — | seedha main session me (read, lock nahi lagta) |

---

## Rules jo yahan bhi lagu hain

- **Rule 6D:** koi bhi loop / background automation `_core/` ke order-path files ya
  `nifty_config.json` ke `active`/`mode` ko **kabhi touch nahi** karega — sirf
  research/audit/report. (Detail: CLAUDE.md Rule 6D.)
- **VPS = single writer:** deploy hamesha 🔴 LIVE lane se, ek waqt ek. Do sessions ka
  ek saath `systemctl restart` = strategy silently mar sakti hai.
- **Worktree research** VPS ko nahi chhuti — sirf local branch pe kaam, deploy alag
  step hai (LIVE lane).
