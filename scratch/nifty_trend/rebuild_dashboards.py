"""Re-copy the current dashboard_intraday.html template into every runs/<slug>/index.html
(same substitution run_hunt.py does: results_intraday.js -> results.js). Use after editing
the shared template so existing runs pick up UI/JS changes WITHOUT re-running the hunt
(results.js data is left untouched). Reusable — Rule 6B (single template, many runs).

Usage: python rebuild_dashboards.py
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

def main():
    src = os.path.join(HERE, "dashboard_intraday.html")
    dash = open(src, encoding="utf-8").read().replace(
        'src="results_intraday.js"', 'src="results.js"')
    n = 0
    for slug in sorted(os.listdir(RUNS)):
        folder = os.path.join(RUNS, slug)
        idx = os.path.join(folder, "index.html")
        if os.path.isdir(folder) and os.path.exists(os.path.join(folder, "results.js")):
            open(idx, "w", encoding="utf-8").write(dash)
            n += 1
            print(f"  rebuilt runs/{slug}/index.html")
    print(f"done — {n} run dashboards rebuilt from template")

if __name__ == "__main__":
    main()
