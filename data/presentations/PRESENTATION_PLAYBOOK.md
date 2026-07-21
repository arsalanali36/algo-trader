# 🎬 YT Daily Presentation — PLAYBOOK

> **Iski zaroorat kyun:** roz ka kaam hai — user din ke points deta hai, Claude ek
> slide-style HTML dev-log banata hai. Ye file poora system capture karti hai taaki
> **har baar samjhana na pade**. Naya deck banana ho → ye padho, neeche wala template
> copy karo, content bharo, deploy karo. Bas.
>
> Reference decks: `data/presentations/2026-07-21.html` (latest — animation engine ke
> saath), `2026-07-20.html`, `2026-07-16.html`. Latest ko hamesha template maano.

---

## 0. TL;DR — 6 steps

1. **Ground** karo: aaj ka `git log --all --since="<date> 00:00" --pretty=format:"%h %ci %s"` + us din ki `project_code3b_*` memory files + `CLAUDE.md` Update Log. **Invent mat karo** — jo sach me hua wahi.
2. **Worktree** banao (parallel sessions safe): `bash scripts/worktree.sh new pres-<mmdd>` → naye Claude session ki zaroorat nahi, isi se kaam.
3. **Build**: template copy → `data/presentations/<YYYY-MM-DD>.html` → content bharo (accent color + slides + rail anchors).
4. **Screenshot** (optional): PIL se resize+base64 → `__IMG_1__` placeholder replace.
5. **Verify**: sections == rail anchors, `__IMG` leftover 0, browser me 0 console error.
6. **Deploy**: commit → `git push origin HEAD:master` (FF) → VPS **surgical** `git checkout origin/master -- <file>` (koi restart nahi — `send_file` disk se serve).

---

## 1. Kahan rehta / kaise serve hota

| Cheez | Detail |
|-------|--------|
| **File** | `data/presentations/<YYYY-MM-DD>.html` (git-tracked) |
| **Route** | `/presentations` (list) + `/presentations/<date>` — `trader_dashboard.py`, login-gated |
| **Nav** | Dashboard → **📋 Reports ▾ → 🎬 YT Presentations** |
| **Deploy** | `git pull` / surgical checkout — **dashboard restart NAHI chahiye** (`send_file` disk se padhta hai) |
| **Route regex** | `_PRESENT_NAME_RE = ^\d{4}-\d{2}-\d{2}[a-z]?$` (letter-suffix allowed). Reports ka `_REPORT_DATE_RE` strict-date hai — **usko mat chhedo** |
| **Verify serving** | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5099/presentations/<date>` → **302** = registered + serving (login gate) |

---

## 2. Naming rules

- File = `<YYYY-MM-DD>.html`. **Date = content ki date, banane ki raat ki nahi** (raat 11 baje 21st ka kaam likh rahe ho to `2026-07-21.html`, `-22` nahi).
- Ek din do decks me bat sakta hai → **letter suffix**: `2026-07-15b.html`.
- **Slot pehle se bhara ho to OVERWRITE mat karo** — user se poochho ya `b`/`c` suffix do. (Ek baar research deck galti se aaj ki date pe publish ho gaya tha, `15b` pe move karna pada.)

---

## 3. Build workflow (detail)

### 3a. Grounding (sabse zaroori)
```bash
cd "D:/KHAZANA/KHAZANA/PYTHON/CODE3B- TV BACKTEST ENGINE"
git log --all --since="2026-07-21 00:00" --pretty=format:"%h %ci %s"   # aaj ke commits
```
+ padho: us din likhi `~/.claude/.../memory/project_code3b_*.md` files, `CLAUDE.md` Update Log ki us din ki rows, aur user ne chat me jo points diye. **Honest raho** — gated/rejected/designed-not-built outcomes ko waise hi dikhao (user ka science-honesty tone). Jo user ke sawaal the (e.g. "return accha to fail kyun", "haath se kisne bhara") unhe slide ka hook banao — feature ka jawab us sawaal se jodo.

### 3b. Worktree (parallel-safe)
```bash
bash scripts/worktree.sh new pres-0721      # origin/master se fresh worktree + apna lock
# kaam .claude/worktrees/pres-0721/ me karo
```
Kyun: main folder pe ek write-lock hai (do sessions clobber na karein). Worktree ki apni `.claude/` + apna lock → parallel session ko block nahi karta. (Agar lock free hai to main folder me bhi seedha edit chal jaata hai — guard fail-open hai; par worktree = clean default.)

### 3c. Screenshot embed
Aaj ke screenshots: `C:\Users\arsal\Pictures\Screenshots\Screenshot <date> *.png`.
HTML me `__IMG_1__` placeholder rakho, phir:
```python
import base64, io
from PIL import Image
im = Image.open(r'C:\Users\arsal\Pictures\Screenshots\Screenshot 2026-07-21 155337.png').convert('RGB')
w = 1280; h = int(im.height*w/im.width); im = im.resize((w,h), Image.LANCZOS)
buf = io.BytesIO(); im.save(buf, 'JPEG', quality=82, optimize=True)
uri = 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
p = r'data/presentations/2026-07-21.html'
html = open(p, encoding='utf-8').read().replace('__IMG_1__', uri)
open(p, 'w', encoding='utf-8').write(html)
```
- ~120-160 KB/image theek hai. **1-2 max.** Screenshots pe "Activate Windows" watermark + user ke red-pen circle rehte hain — authentic hai, feature illustrate karne ke liye thode use karo (`.shot` frame + `.split` se image+cards side-by-side).

### 3d. Verify → Deploy
```bash
# structure sanity
grep -c 'section class="slide' <file>     # == rail anchors hona chahiye
grep -c '__IMG' <file>                    # 0 hona chahiye
# browser pane me kholo → read_console_messages(onlyErrors) → 0 errors

git add data/presentations/<file> && git commit -m "presentation: <date> ..."
git push origin HEAD:master               # FF (worktree branch → master)

# VPS surgical (tree dirty ho sakta hai — parallel sessions):
ssh -i "C:/Users/arsal/.ssh/khazana_ed25519" root@72.61.173.32 \
  "cd '/root/ARSALAN/CODE3B- TV BACKTEST ENGINE' && git fetch -q origin && \
   git checkout origin/master -- data/presentations/<file>"
# koi restart nahi. curl se 302 verify karo.

bash scripts/worktree.sh done pres-0721    # cleanup (Windows file-lock se dir linger kare to harmless)
```

---

## 4. Design system

### Accent color — har din alag
Ek per-day accent chuno (theme ke hisaab se). Abhi tak use hue: green (13), purple (14),
amber/gold (15), cyan (16), teal (17), emerald (20), **ember-orange (21)**. Naya din = naya
distinct rang. Sirf `--acc` / `--acc2` (light, gradient-text ke liye) / `--acc-soft` badlo —
baaki scaffold same.

### Scaffold ka dhaancha (fixed — mat todo)
- **Dark terminal-grid bg** (`body::before` grid + radial mask).
- **`.scroller`** → `scroll-snap-type: y proximity`; har `section.slide` = `min-height:100vh`, centered.
- **Rail nav** (`.rail`) — left side dots, **ek `<a href="#sN">` per slide, ORDER me, count EXACT match** slides se. Active dot IntersectionObserver se.
- **Hero** (`#s0`) — brand + datechip + `<h1>` (accent gradient span) + lede + 4 `.stat` tiles (ek count-up `data-to`) + scrollhint.
- **Content slides** — `.mhead` (eyebrow `.ey` + `.tickets` commit-hashes + `<h2>` + `.lede`) + phir `.cards` / `.bignums` / `.callout` / `.shot`.
- **Close** (`.close`) — recap chips + signoff.

### Content blocks (cheat-sheet)
| Block | Kab |
|-------|-----|
| `.cards` (2-col grid, `.card` me `.badge` + `.ct` + `.cd`) | 3-4 related points ek slide pe |
| `.bignums` (3-col, `.bignum.g/.r/.a/.b/.o` colored) | key metrics (Sharpe, %, ₹) |
| `.callout` (`.green/.blue/.red/.amber`) | ek zaroori takeaway / lesson / caveat |
| `.shot` (`.split` ke andar) | screenshot + side me cards |
| `.ramviz` / animated bars | koi before→after metric (RAM, P&L) — signature animation |

**Badges:** `b-ship` (green, naya feature) · `b-fix` (red, bug) · `b-infra` (accent) · `b-money` (gold) · `b-eng` (blue).

### Narrative
Hook → **kya** → **kyun matter karta** → **result**. Do-act pattern jab din me research + platform dono ho (jaise 14-July: ML + Control Center). Hinglish, user ki honest-science tone. **Devanagari se bacho** agar corruption ka risk ho — latin Hinglish safe (`darasal`, `ooncha`).

---

## 5. Interactivity / animation layer (2026-07-21 se baked-in)

Template me ye sab pehle se hai — content bharo, animation apne aap chalega:

| Effect | Kaise |
|--------|-------|
| **Scroll progress bar** | `<div class="prog" id="prog">` + scroll listener width % set karta hai |
| **Staggered reveals** | JS har `.cards/.bignums/.stats` ke children pe `--d` (i*0.075s) set karta; `.reveal` transition-delay usse leta |
| **Reveal-on-scroll** | IntersectionObserver `.reveal` pe `.in` class lagata (opacity + translateY + scale) |
| **Hero count-up** | `[data-to]` element, IO threshold .6, cubic-ease |
| **Generic count-up** | `[data-count]` + optional `data-suf` (" MB") + `data-delay` |
| **Animated before/after bars** | `.ramviz > .rambar` — `.fill[data-w="24"]` width 0→N% on reveal + `.rv[data-count]` count-up. Koi bhi before→after metric ke liye reuse karo |
| **Hero parallax + fade** | scroll pe `.hero .wrap` translateY + opacity |
| **Hover-lift** | `.card:hover/.bignum:hover/.stat:hover` → translateY + accent glow |
| **Reduced-motion** | `@media(prefers-reduced-motion:reduce)` sab transition/animation off + JS `RM` flag counts/bars ko instant final karta |

**Rule:** koi bhi naya animation `prefers-reduced-motion` respect kare (continuous effects — parallax — JS `RM` guard se skip; transition-based CSS media query se off).

---

## 6. Verification checklist (deploy se pehle)

- [ ] `sections == rail anchors` (dono grep barabar)
- [ ] `grep -c '__IMG' == 0` (koi placeholder na chhoote)
- [ ] Browser pane me kholo → `read_console_messages(onlyErrors)` → **0 errors**
- [ ] Rail dots + count-up + reveals wired (JS `document.querySelectorAll('.reveal').length` > 0)
- [ ] Deploy ke baad `curl … /presentations/<date>` → **302**
- [ ] Preview pane "static snapshot" me IntersectionObserver nahi firing dikhe to **ghabrao mat** — asli logged-in Chrome me standard IO chalta hai (ye preview-quirk hai, code-bug nahi)

---

## 7. Copy-paste TEMPLATE

> Neeche wala poora scaffold copy karo (animation engine included). `<!-- FILL -->` markers
> pe content bharo. Rail anchors slide-count se match karna mat bhoolo. Accent 3 vars badlo.
> **Sabse aasan:** seedha latest deck (`2026-07-21.html`) copy karke content overwrite kar do —
> usme sab kuch already hai; ye template usi ka minified-structure sketch hai.

```html
<!doctype html>
<html lang="hi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KHAZANA Dev Log — <!-- DATE --> · <!-- THEME --></title>
<style>
  :root{
    --bg:#0a0805; --panel:#171009; --panel2:#120c07;
    --border:#2a1f12; --border-soft:#3a2b18;
    --ink:#f3ece2; --ink-mut:#b39c82; --ink-dim:#7a674f;
    --green:#4ec96a; --green-soft:#2ea04322; --blue:#57a0ff; --blue-soft:#1f6feb22;
    --red:#f26d6d; --red-soft:#f8514922; --gold:#f0c765;
    /* ==== PER-DAY ACCENT — sirf ye 3 badlo ==== */
    --acc:#f4923b; --acc2:#ffc98a; --acc-soft:#f4923b1e;
    --font-sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
    --font-mono:ui-monospace,"Cascadia Code","SF Mono",Consolas,Menlo,monospace;
    --maxw:1080px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{background:var(--bg);color:var(--ink);font-family:var(--font-sans);-webkit-font-smoothing:antialiased;line-height:1.5;overflow-x:hidden}
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
    background-image:linear-gradient(var(--border-soft) 1px,transparent 1px),linear-gradient(90deg,var(--border-soft) 1px,transparent 1px);
    background-size:44px 44px;opacity:.15;mask-image:radial-gradient(ellipse 90% 70% at 50% 30%,#000 40%,transparent 100%)}
  .scroller{position:relative;z-index:1;scroll-snap-type:y proximity}
  section.slide{min-height:100vh;scroll-snap-align:start;display:flex;flex-direction:column;justify-content:center;padding:84px 32px;position:relative}
  .wrap{max-width:var(--maxw);margin:0 auto;width:100%}
  .ey{font-family:var(--font-mono);font-size:12px;letter-spacing:.22em;text-transform:uppercase;color:var(--ink-dim);display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .ey .dot{width:7px;height:7px;border-radius:50%;background:var(--acc);box-shadow:0 0 10px 1px var(--acc)}
  .ey .modno{color:var(--acc);font-weight:600}
  h1{font-size:clamp(35px,6.4vw,78px);font-weight:800;letter-spacing:-.03em;line-height:1;text-wrap:balance}
  h2{font-size:clamp(26px,4.2vw,48px);font-weight:800;letter-spacing:-.025em;line-height:1.03;text-wrap:balance}
  .lede{color:var(--ink-mut);font-size:clamp(15px,1.7vw,19px);max-width:63ch;line-height:1.6}
  .mono{font-family:var(--font-mono)} .tnum{font-variant-numeric:tabular-nums}
  .hero .wrap{display:flex;flex-direction:column;gap:28px}
  .hero-top{display:flex;flex-wrap:wrap;gap:14px 26px;align-items:baseline}
  .brand{font-family:var(--font-mono);font-size:13px;letter-spacing:.16em;color:var(--ink-mut);text-transform:uppercase} .brand b{color:var(--acc)}
  .datechip{font-family:var(--font-mono);font-size:12px;letter-spacing:.1em;padding:5px 11px;border:1px solid var(--border);border-radius:999px;color:var(--ink-mut);background:var(--panel2)}
  .hero h1 .accent{background:linear-gradient(180deg,var(--acc2),var(--acc));-webkit-background-clip:text;background-clip:text;color:transparent}
  .stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:4px}
  .stat{border:1px solid var(--border);background:linear-gradient(180deg,var(--panel),var(--panel2));border-radius:14px;padding:19px 17px;position:relative;overflow:hidden;transition:transform .5s cubic-bezier(.2,.75,.25,1),border-color .3s}
  .stat::after{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--acc);opacity:.9}
  .stat.green::after{background:var(--green)} .stat.red::after{background:var(--red)} .stat.blue::after{background:var(--blue)}
  .stat:hover{transform:translateY(-3px);border-color:var(--border-soft)}
  .stat .n{font-family:var(--font-mono);font-size:clamp(22px,3.5vw,37px);font-weight:700;letter-spacing:-.02em;line-height:1}
  .stat .k{font-size:12.5px;color:var(--ink-mut);margin-top:9px}
  .scrollhint{margin-top:12px;font-family:var(--font-mono);font-size:12px;color:var(--ink-dim);letter-spacing:.14em;display:flex;align-items:center;gap:9px}
  .scrollhint .ar{animation:bob 1.8s ease-in-out infinite}
  @keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(4px)}}
  .mhead{display:flex;flex-direction:column;gap:12px;margin-bottom:24px}
  .mhead .row{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;flex-wrap:wrap}
  .tickets{display:flex;flex-wrap:wrap;gap:6px}
  .tk{font-family:var(--font-mono);font-size:11.5px;color:var(--ink-mut);border:1px solid var(--border);border-radius:6px;padding:3px 7px;background:var(--panel2)}
  .cards{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
  .card{border:1px solid var(--border);border-radius:14px;padding:18px;background:linear-gradient(180deg,var(--panel),var(--panel2));display:flex;flex-direction:column;gap:8px;transition:transform .5s cubic-bezier(.2,.75,.25,1),opacity .5s ease,border-color .3s,box-shadow .3s;will-change:transform}
  .card:hover{transform:translateY(-5px);border-color:var(--acc);box-shadow:0 16px 36px -18px var(--acc)}
  .card .ct{display:flex;align-items:center;gap:10px;font-weight:700;font-size:15.5px}
  .card .cd{color:var(--ink-mut);font-size:13.6px;line-height:1.55} .card .cd b{color:var(--ink);font-weight:600}
  .badge{font-family:var(--font-mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;padding:3px 8px;border-radius:999px;white-space:nowrap}
  .b-ship{color:#9be5a6;background:var(--green-soft);border:1px solid #2ea04340}
  .b-fix{color:#f7a5a5;background:var(--red-soft);border:1px solid #f8514940}
  .b-infra{color:#ffcf9a;background:var(--acc-soft);border:1px solid #f4923b55}
  .b-money{color:#f0c765;background:#d2992222;border:1px solid #d2992255}
  .b-eng{color:#9cc0ff;background:var(--blue-soft);border:1px solid #1f6feb44}
  .bignums{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}
  .bignum{border:1px solid var(--border);border-radius:14px;padding:16px;background:linear-gradient(180deg,var(--panel),var(--panel2));text-align:center;transition:transform .5s cubic-bezier(.2,.75,.25,1),border-color .3s,box-shadow .3s}
  .bignum:hover{transform:translateY(-4px);border-color:var(--acc);box-shadow:0 14px 30px -18px var(--acc)}
  .bignum .bn{font-family:var(--font-mono);font-size:clamp(20px,3vw,31px);font-weight:700;line-height:1.05}
  .bignum .bl{font-size:12px;color:var(--ink-mut);margin-top:8px;line-height:1.4}
  .bignum.g .bn{color:var(--green)} .bignum.r .bn{color:var(--red)} .bignum.a .bn{color:var(--acc)} .bignum.o .bn{color:var(--gold)} .bignum.b .bn{color:var(--blue)}
  .shot{border:1px solid var(--border-soft);border-radius:14px;overflow:hidden;background:var(--panel2);box-shadow:0 20px 50px -20px #000;margin-top:2px}
  .shot img{width:100%;display:block}
  .shot .cap{font-family:var(--font-mono);font-size:11.5px;color:var(--ink-dim);padding:9px 14px;border-top:1px solid var(--border);line-height:1.5} .shot .cap b{color:var(--acc)}
  .split{display:grid;grid-template-columns:1.15fr .85fr;gap:16px;align-items:start}
  .reveal{opacity:0;transform:translateY(24px) scale(.985);transition:opacity .6s ease,transform .66s cubic-bezier(.2,.75,.25,1);transition-delay:var(--d,0s)}
  .reveal.in{opacity:1;transform:none}
  .prog{position:fixed;top:0;left:0;height:3px;width:0;z-index:9;background:linear-gradient(90deg,var(--acc),var(--acc2));box-shadow:0 0 12px var(--acc);transition:width .08s linear}
  .ramviz{display:flex;flex-direction:column;gap:11px;margin-bottom:16px}
  .rambar{display:grid;grid-template-columns:150px 1fr 92px;gap:14px;align-items:center;font-family:var(--font-mono);font-size:12.5px}
  .rambar .rl{color:var(--ink-mut);letter-spacing:.04em}
  .rambar .track{height:24px;background:var(--panel2);border:1px solid var(--border);border-radius:7px;overflow:hidden}
  .rambar .fill{height:100%;width:0;border-radius:6px;transition:width 1.35s cubic-bezier(.2,.75,.25,1)}
  .rambar.before .fill{background:linear-gradient(90deg,#f26d6d40,#f26d6d)}
  .rambar.after .fill{background:linear-gradient(90deg,#4ec96a40,#4ec96a)}
  .rambar .rv{color:var(--ink);font-weight:700;font-size:15px;text-align:right}
  .rambar.before .rv{color:var(--red)} .rambar.after .rv{color:var(--green)}
  .ramnote{font-family:var(--font-mono);font-size:12px;color:var(--ink-dim);margin-top:3px}
  .rail{position:fixed;left:18px;top:50%;transform:translateY(-50%);z-index:5;display:flex;flex-direction:column;gap:10px}
  .rail a{width:9px;height:9px;border-radius:50%;background:var(--border);border:1px solid var(--border-soft);transition:all .3s;display:block}
  .rail a.on{background:var(--acc);box-shadow:0 0 9px 1px var(--acc);transform:scale(1.25)}
  .slide.hi{background:radial-gradient(ellipse 80% 60% at 50% 40%,#2a180633,transparent 70%)}
  .callout{border:1px solid #f4923b44;background:linear-gradient(180deg,#1e1206,#140c05);border-left:3px solid var(--acc);border-radius:12px;padding:16px 18px;margin-bottom:14px;display:flex;gap:14px;align-items:flex-start}
  .callout.blue{border-color:#1f6feb44;background:linear-gradient(180deg,#0a1322,#060d16);border-left-color:var(--blue)}
  .callout.red{border-color:#f8514940;background:linear-gradient(180deg,#1c0f0f,#140a0a);border-left-color:var(--red)}
  .callout.green{border-color:#2ea04344;background:linear-gradient(180deg,#08190f,#04120a);border-left-color:var(--green)}
  .callout .ic{font-size:20px;line-height:1}
  .callout .txt{font-size:14.3px;line-height:1.55;color:#ecd9c2} .callout .txt b{color:#fff}
  .close{text-align:center;background:radial-gradient(ellipse 70% 55% at 50% 45%,#2a1806,transparent 70%)}
  .close .wrap{display:flex;flex-direction:column;align-items:center;gap:20px}
  .recap{display:flex;flex-wrap:wrap;gap:9px;justify-content:center;max-width:920px}
  .recap span{font-family:var(--font-mono);font-size:12px;color:var(--ink-mut);border:1px solid var(--border);border-radius:999px;padding:6px 12px;background:var(--panel2)}
  .signoff{font-family:var(--font-mono);color:var(--ink-dim);font-size:12.5px;letter-spacing:.12em}
  @media(max-width:720px){.stats{grid-template-columns:repeat(2,1fr)}.cards{grid-template-columns:1fr}.bignums{grid-template-columns:1fr}.split{grid-template-columns:1fr}.rail{display:none}section.slide{padding:70px 20px}.rambar{grid-template-columns:96px 1fr 68px;gap:9px;font-size:11px}}
  @media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.reveal{opacity:1;transform:none}.prog{display:none}.rambar .fill{width:auto}html{scroll-behavior:auto}}
</style>
</head>
<body>

<div class="prog" id="prog"></div>

<!-- RAIL: ek <a> per slide, ORDER me, count == slides -->
<nav class="rail" aria-label="slides">
  <a href="#s0" class="on"></a><a href="#s1"></a><a href="#s2"></a><!-- ...aur slides ke hisaab se --></nav>

<div class="scroller">

  <!-- HERO -->
  <section class="slide hero" id="s0">
    <div class="wrap">
      <div class="hero-top">
        <span class="brand"><b>KHAZANA</b> · ALGO TRADER · <!-- DAY TAG --></span>
        <span class="datechip">DEV LOG — <!-- DATE --></span>
      </div>
      <h1><!-- HOOK line 1 --><br><span class="accent"><!-- HOOK line 2 --></span></h1>
      <p class="lede"><!-- 2-3 line intro --></p>
      <div class="stats">
        <div class="stat"><div class="n tnum" data-to="46">0</div><div class="k">Commits</div></div>
        <div class="stat green"><div class="n"><!-- metric --></div><div class="k"><!-- label --></div></div>
        <div class="stat blue"><div class="n"><!-- metric --></div><div class="k"><!-- label --></div></div>
        <div class="stat"><div class="n"><!-- metric --></div><div class="k"><!-- label --></div></div>
      </div>
      <div class="scrollhint"><span class="ar">↓</span> SCROLL / ARROW KEYS</div>
    </div>
  </section>

  <!-- CONTENT SLIDE (cards) — id s1, s2... -->
  <section class="slide hi" id="s1">
    <div class="wrap">
      <div class="mhead">
        <div class="row">
          <div class="ey"><span class="dot"></span><span class="modno"><!-- TAG --></span> · <!-- SUBTITLE --></div>
          <div class="tickets"><span class="tk"><!-- commit --></span></div>
        </div>
        <h2><!-- slide headline --></h2>
        <p class="lede"><!-- context --></p>
      </div>
      <div class="cards">
        <div class="card reveal"><div class="ct"><span class="badge b-ship"><!-- tag --></span> <!-- title --></div><div class="cd"><!-- detail --></div></div>
        <!-- ...2-4 cards -->
      </div>
    </div>
  </section>

  <!-- Optional: animated before/after bars -->
  <!--
      <div class="ramviz reveal">
        <div class="rambar before"><span class="rl">PEHLE</span><div class="track"><div class="fill" data-w="100"></div></div><span class="rv" data-count="521" data-suf=" MB">0 MB</span></div>
        <div class="rambar after"><span class="rl">AB</span><div class="track"><div class="fill" data-w="24" data-delay="420"></div></div><span class="rv" data-count="127" data-suf=" MB" data-delay="420">0 MB</span></div>
        <div class="ramnote">caption</div>
      </div>
  -->

  <!-- Optional: screenshot + cards side-by-side -->
  <!--
      <div class="split">
        <div class="shot reveal"><img src="__IMG_1__" alt=""><div class="cap">caption <b>highlight</b></div></div>
        <div style="display:flex;flex-direction:column;gap:12px"><div class="card reveal">...</div></div>
      </div>
  -->

  <!-- CLOSE -->
  <section class="slide close" id="sN">
    <div class="wrap">
      <div class="ey" style="justify-content:center"><span class="dot"></span><span class="modno"><!-- DATE --></span> · DIN KA NICHOD</div>
      <h2><!-- closing line --></h2>
      <p class="lede" style="text-align:center;margin:0 auto"><!-- summary para --></p>
      <div class="recap">
        <span><!-- chip --></span><!-- ...ek chip per feature -->
      </div>
      <div class="signoff">KHAZANA · ALGO TRADER · DEV LOG — ARSALAN ALI</div>
    </div>
  </section>

</div>

<script>
(function(){
  // reveal-on-scroll
  var io=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting)e.target.classList.add('in')})},{threshold:.14});
  document.querySelectorAll('.reveal').forEach(function(el){io.observe(el)});
  // rail active
  var secs=[].slice.call(document.querySelectorAll('section.slide')),dots=[].slice.call(document.querySelectorAll('.rail a'));
  var sio=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){var i=secs.indexOf(e.target);dots.forEach(function(d,j){d.classList.toggle('on',j===i)})}})},{threshold:.5});
  secs.forEach(function(s){sio.observe(s)});
  // easing + hero count-up
  function ease(t){return 1-Math.pow(1-t,3)}
  function run(el){var to=+el.getAttribute('data-to'),dur=1100,st=null;function step(ts){if(!st)st=ts;var p=Math.min((ts-st)/dur,1);el.textContent=Math.round(ease(p)*to);if(p<1)requestAnimationFrame(step)}requestAnimationFrame(step)}
  var hio=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){run(e.target);hio.unobserve(e.target)}})},{threshold:.6});
  document.querySelectorAll('[data-to]').forEach(function(el){hio.observe(el)});
  // arrow-key nav
  window.addEventListener('keydown',function(e){if(e.key!=='ArrowDown'&&e.key!=='ArrowUp'&&e.key!=='ArrowRight'&&e.key!=='ArrowLeft')return;var y=window.scrollY,cur=0,best=1e9;secs.forEach(function(s,i){var d=Math.abs(s.offsetTop-y);if(d<best){best=d;cur=i}});var nx=(e.key==='ArrowDown'||e.key==='ArrowRight')?cur+1:cur-1;if(nx>=0&&nx<secs.length){e.preventDefault();secs[nx].scrollIntoView({behavior:'smooth'})}});

  var RM=matchMedia('(prefers-reduced-motion: reduce)').matches;
  // scroll progress bar
  var prog=document.getElementById('prog');
  function updProg(){var h=document.documentElement,mx=h.scrollHeight-h.clientHeight;if(prog)prog.style.width=(mx>0?(window.scrollY/mx*100):0)+'%'}
  window.addEventListener('scroll',updProg,{passive:true});updProg();
  // stagger reveals
  document.querySelectorAll('.cards, .bignums, .stats').forEach(function(g){[].slice.call(g.children).forEach(function(c,i){if(c.classList.contains('reveal'))c.style.setProperty('--d',(i*0.075)+'s')})});
  // generic count-up
  function runCount(el){var to=+el.getAttribute('data-count'),suf=el.getAttribute('data-suf')||'',dl=+(el.getAttribute('data-delay')||0),dur=1150,st=null;if(RM){el.textContent=to+suf;return}function step(ts){if(!st)st=ts;var p=Math.min((ts-st)/dur,1);el.textContent=Math.round(ease(p)*to)+suf;if(p<1)requestAnimationFrame(step)}setTimeout(function(){requestAnimationFrame(step)},dl)}
  // before/after bars reveal
  var ramSeen=false;
  var ramObs=new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting&&!ramSeen){ramSeen=true;e.target.querySelectorAll('.fill').forEach(function(f){var dl=+(f.getAttribute('data-delay')||0);setTimeout(function(){f.style.width=f.getAttribute('data-w')+'%'},RM?0:dl)});e.target.querySelectorAll('[data-count]').forEach(runCount);ramObs.disconnect()}})},{threshold:.55});
  document.querySelectorAll('.ramviz').forEach(function(el){ramObs.observe(el)});
  // hero parallax + fade
  var heroWrap=document.querySelector('.hero .wrap');
  if(heroWrap&&!RM){window.addEventListener('scroll',function(){var y=window.scrollY,vh=window.innerHeight;if(y<vh){heroWrap.style.transform='translateY('+(y*0.16)+'px)';heroWrap.style.opacity=String(Math.max(0,1-y/(vh*0.82)))}},{passive:true})}
})();
</script>
</body>
</html>
```

---

## 8. Related

- Memory: `project_code3b_yt_daily_presentation` (short pointer to this file).
- Pairs with `daily-linkedin-post` scheduled task (6pm) — same "aaj kya kiya" spirit, alag medium.
- Deploy standard: CLAUDE.md "Deploy Karna" (git-based, surgical checkout for dirty tree).
