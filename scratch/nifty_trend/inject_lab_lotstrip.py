"""Idempotent: inject the Lot-economics strip (HTML + JS) from dashboard_intraday.html
into every runs/*/index.html. Existing run pages are divergent template snapshots, so we
insert ONLY the two additive snippets at stable anchors (before <div class="controls"> and
after renderAll();). Re-runnable (skips pages that already have the strip). Future runs get
it straight from the template. Run: python scratch/nifty_trend/inject_lab_lotstrip.py"""
import re, glob, os

HERE = os.path.dirname(os.path.abspath(__file__))
tpl = open(os.path.join(HERE, "dashboard_intraday.html"), encoding="utf-8").read()

# --- extract the HTML strip (from <div id="lotStrip"...> up to <div class="controls">) ---
m = re.search(r'(\s*<div id="lotStrip".*?</div>\s*)\n(\s*<div class="controls">)', tpl, re.S)
assert m, "lotStrip HTML block not found in template"
HTML_SNIP = m.group(1).rstrip("\n")

# --- extract the JS (from the '// ---- Lot-economics strip' comment to just before </script>) ---
m2 = re.search(r'(renderAll\(\);\n)(// ---- Lot-economics strip.*?\}\)\(\);)\n(</script>)', tpl, re.S)
assert m2, "lotStrip JS block not found in template"
JS_SNIP = m2.group(2)

def eol(txt):
    return "\r\n" if "\r\n" in txt else "\n"

n_done = n_skip = 0
for f in glob.glob(os.path.join(HERE, "runs", "*", "index.html")):
    s = open(f, encoding="utf-8").read()
    if 'id="lotStrip"' in s:
        n_skip += 1
        continue
    e = eol(s)
    html = HTML_SNIP.replace("\n", e)
    js = JS_SNIP.replace("\n", e)
    # insert HTML before <div class="controls">
    s2, c1 = re.subn(r'(\s*<div class="controls">)', e + html + r'\1', s, count=1)
    # insert JS right after renderAll();
    s2, c2 = re.subn(r'(renderAll\(\);)', r'\1' + e + js, s2, count=1)
    if c1 == 1 and c2 == 1:
        open(f, "w", encoding="utf-8", newline="").write(s2)
        n_done += 1
    else:
        print("  SKIP (anchor miss):", os.path.basename(os.path.dirname(f)), "html=", c1, "js=", c2)
print(f"injected: {n_done}  already-had: {n_skip}")
