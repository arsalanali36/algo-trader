# 🔀 MULTI-SESSION RULES — 2-3 hunts / Claude sessions ek saath (2026-07-12)

**Kyun:** 2026-07-10 pe do Claude sessions ne same files edit ki + ek dusre ke builds
kill kar diye. Ab parallel hunting architecture ka part hai — ye rules HAR session
(insaan ya Claude) follow kare.

## Launch — ek command, background, log ke saath

```bash
python hunt.py build_pivot.py              # detached launch, log runs/_logs/<script>_<ts>.log
python hunt.py build_bnf.py --trials 300   # args pass-through
python hunt.py status                      # kaun kya chala raha hai + last log lines
```

2-3 hunts side-by-side theek hai (CPU share hoga, har hunt thoda slow — 27min wala
~40-50min ho sakta hai 3 parallel pe). Har `run_hunt.main()` khud apna slug
`hunt_guard.register()` se claim karta hai.

## Guarantees (hunt_guard.py — run_hunt me built-in)

1. **Slug claim:** same `--name` do jagah launch → doosra turant REFUSE hota hai
   (clear message ke saath), chupchaap `runs/<slug>/` clobber nahi hota.
2. **Locked shared writes:** `runs/index.json` + `runs/compare.json` writes
   `flock()` ke andar — do hunts saath finish hon to bhi corruption nahi.
   (`apply_numbering.py` bhi isi lock se index likhta hai.)
3. **Registry:** `runs/_active_hunts.json` — live hunts {slug, pid, script, started, log}.
   Dead-pid entries auto-prune. Stale locks auto-break (owner-pid dead check).

## HARD RULES — process kill / edit / git

- **KILL:** kabhi `pkill python` / `taskkill python.exe` blanket mat karo. Pehle
  `python hunt.py status` → sirf USI pid ko maro jo TUMHARA slug hai. Doosre slug
  ka pid = doosre session ka 1-2hr build.
- **SOURCE EDIT:** chalta hua hunt apne modules START pe load kar chuka hai —
  `intraday_engine.py` edit karne se running hunt NAHI badalta (na crash hota).
  Par do sessions ek hi file ek saath edit na karein — jo session jis strategy pe
  kaam kar raha hai, wahi us build script ka owner; shared engine me naya design
  ADD karna additive rakho (existing designs ke keys/behaviour mat chhedo).
- **GIT:** commit se pehle `git pull` (cross-machine + cross-session dono);
  apne slug ki files hi stage karo (`git add <specific paths>`, kabhi `git add -A`
  parallel-session ke dauran nahi).
- **VPS push:** index.json kabhi seedha overwrite nahi — merge-by-slug (established
  playbook), kyunki doosra session bhi apna run push kar chuka ho sakta hai.

## Ek session ke liye checklist (Claude bhi yahi kare)

1. `python hunt.py status` — kya already chal raha hai?
2. Apna kaam alag `--name <slug>` se launch karo (`python hunt.py build_X.py`).
3. Progress: log file tail karo (status me path hai) — doosre ke logs padho, chhedo mat.
4. Khatam pe: registry khud saaf ho jaata hai (atexit + dead-pid prune).
5. Kuch phasa lage: `python hunt_guard.py clean` (sirf dead entries/stale locks todta hai).
