"""Inline results.js into dashboard.html -> one portable self-contained file.
Open it by double-click anywhere; no server, no sidecar file needed.
"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
html = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()
data = open(os.path.join(HERE, "results.js"), encoding="utf-8").read()
html = html.replace('<script src="results.js"></script>', "<script>\n" + data + "\n</script>")
out = os.path.join(HERE, "nifty_dashboard_standalone.html")
open(out, "w", encoding="utf-8").write(html)
print(f"wrote {out}  ({os.path.getsize(out)//1024} KB)")
