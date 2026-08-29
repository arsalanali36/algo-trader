"""Sync the Lot-economics strip (HTML + JS) from dashboard_intraday.html into every
runs/*/index.html. REMOVES any previous strip first, then re-inserts the current one, so
re-running UPDATES existing pages (not just adds). Run pages are divergent template
snapshots, so we only touch the two additive snippets at stable anchors. 6 sm_nifty_*
use a different minimal page (no anchors) and are skipped.
Run: python scratch/nifty_trend/inject_lab_lotstrip.py"""
import re, glob, os

HERE = os.path.dirname(os.path.abspath(__file__))
tpl = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read()

m = re.search(r'(<div id="lotStrip".*?</div>)\s*\n\s*<div class="controls">', tpl, re.S)
assert m, "lotStrip HTML block not found in template"
HTML_SNIP = m.group(1)

m2 = re.search(r'renderAll\(\);\n(// ---- Lot-economics strip.*?\n\}\)\(\);)\n</script>', tpl, re.S)
assert m2, "lotStrip JS block not found in template"
JS_SNIP = m2.group(1)

def eol(t): return "\r\n" if "\r\n" in t else "\n"

n_done = n_skip = 0
for f in glob.glob(os.path.join(HERE, "runs", "*", "index.html")):
    s = open(f, encoding="utf-8").read()
    # 1) strip any previous injection (HTML strip div + JS block)
    s = re.sub(r'[ \t]*<div id="lotStrip".*?</div>\r?\n', '', s, flags=re.S)
    s = re.sub(r'[ \t]*// ---- Lot-economics strip.*?\r?\n\}\)\(\);\r?\n', '', s, flags=re.S)
    # 2) must have both anchors
    if 'class="controls"' not in s or 'renderAll();' not in s:
        n_skip += 1
        continue
    e = eol(s)
    html = HTML_SNIP.replace("\n", e)
    js = JS_SNIP.replace("\n", e)
    s2, c1 = re.subn(r'([ \t]*<div class="controls">)', e + '    ' + html + r'\1', s, count=1)
    s2, c2 = re.subn(r'(renderAll\(\);)', r'\1' + e + js, s2, count=1)
    if c1 == 1 and c2 == 1:
        open(f, "w", encoding="utf-8", newline="").write(s2)
        n_done += 1
    else:
        print("  SKIP (anchor miss):", os.path.basename(os.path.dirname(f)))
print(f"synced: {n_done}  skipped(diff template): {n_skip}")
