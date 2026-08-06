#!/usr/bin/env python3
"""gen_module_docs.py — auto-generate _DOCS/MODULES.md from module docstrings.

Har tracked module ka **module-level docstring** (pehla paragraph) + uske public
top-level functions/classes (naam + 1-line docstring) nikaal ke ek folder-wise
index (`_DOCS/MODULES.md`) banata hai. Source of truth = code ke docstrings, isliye
refactor/rename/move ke baad sirf ye dobara chalao aur doc current ho jaayega.

Pure stdlib (ast) — koi extra dependency nahi. Deterministic output (sorted) taaki
git-diff saaf rahe. Pre-commit hook har commit pe chalata hai.

Usage:
    python _TOOLS/gen_module_docs.py           # regenerate _DOCS/MODULES.md
    python _TOOLS/gen_module_docs.py --check    # exit 1 if MODULES.md is STALE
                                                # (regenerate se diff aaye to) — CI/hook use

Naya module doc me aane ke liye: uska module-docstring likho (pehli line = 1-line
role), public functions/classes pe 1-line docstring do. Baaki generator sambhaal lega.
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "_DOCS", "MODULES.md")

# App ke module folders (scratch/nifty_trend research engine ALAG — wo _DOCS/BACKTEST.md
# me documented hai, isliye yahan skip). Order = display order in MODULES.md.
SCAN_DIRS = [
    ("_core", "RMS + order/execution money-path (sabse critical)"),
    ("_data", "Broker/data plumbing (Dhan/Kite, feed, cache, rate-limit)"),
    ("brokers", "Broker abstraction (Dhan/Kite place-order/quote/funds)"),
    ("_CHARTING", "Reusable indicators / zones / pattern detection"),
    ("strategies/signals", "Single-source entry signals (backtest+live dono call karte)"),
    ("strategies/live", "LIVE trader loops (har strategy ka apna process)"),
    ("strategies/backtest", "Pluggable backtest strategies (evaluate/backtest contract)"),
    ("_ops", "Standalone ops/reporting/display-page builders (display-only, no order path)"),
    ("_TOOLS", "Dev tools (audit, backtest engine, doc-gen, validation)"),
    (".", "Entrypoints (systemd yahin se: dashboard/monitor/health)"),
]

# `.` (root) scan pe sirf ye entrypoints/infra .py (baaki root pe kam hi hai)
ROOT_INCLUDE = {
    "trader_dashboard.py", "monitor_daemon.py", "health_check.py", "_paths.py",
    "range_trader.py", "nifty_ema_trader.py", "validate_strategy.py",
    "generate_june_mfe.py", "auto_data_downloader.py", "save_daily_summary.py",
    "sync_pine.py", "set_password.py", "deploy_vps.py",
}


def _first_para(doc):
    """Docstring ka pehla paragraph (blank line tak), whitespace collapse."""
    if not doc:
        return ""
    out = []
    for line in doc.strip().splitlines():
        if not line.strip():
            break
        out.append(line.strip())
    return " ".join(out).strip()


def _one_line(doc, limit=140):
    """Docstring ki pehli non-blank line (public API summary), trimmed."""
    if not doc:
        return ""
    for line in doc.strip().splitlines():
        if line.strip():
            s = line.strip()
            return (s[: limit - 1] + "…") if len(s) > limit else s
    return ""


def _publics(tree):
    """Module-level public functions/classes: [(kind, name, 1-line-doc), …]."""
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "fn"
        elif isinstance(node, ast.ClassDef):
            kind = "class"
        else:
            continue
        if node.name.startswith("_"):
            continue
        out.append((kind, node.name, _one_line(ast.get_docstring(node))))
    out.sort(key=lambda x: x[1].lower())
    return out


def _scan_file(path):
    """→ (module_summary, [(kind,name,doc)…]) or (None, None) if unparseable."""
    try:
        # utf-8-sig → BOM-tolerant (kuch files me U+FEFF hota hai, warna parse error)
        src = open(path, "r", encoding="utf-8-sig").read()
        tree = ast.parse(src)
    except Exception as e:
        return (f"⚠️ parse error: {e}", [])
    return (_first_para(ast.get_docstring(tree)), _publics(tree))


def _files_in(d):
    full = os.path.join(ROOT, d)
    if not os.path.isdir(full):
        return []
    names = []
    for fn in sorted(os.listdir(full)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        if d == "." and fn not in ROOT_INCLUDE:
            continue
        names.append(fn)
    return names


def build():
    lines = []
    lines.append("# 📦 MODULES — auto-generated module index")
    lines.append("")
    lines.append("> **AUTO-GENERATED — is file ko haath se mat likho.** Source of truth = code ke")
    lines.append("> module-docstrings. Regenerate: `python _TOOLS/gen_module_docs.py` (pre-commit hook")
    lines.append("> har commit pe chalta hai). Wiring/flow (pieces kaise judte) = `_DOCS/ARCHITECTURE.md`.")
    lines.append("> Research/backtest engine (`scratch/nifty_trend`) = `_DOCS/BACKTEST.md` (yahan nahi).")
    lines.append("")
    total = 0
    # table of contents
    lines.append("## Folders")
    for d, desc in SCAN_DIRS:
        anchor = d.replace("/", "").replace("_", "").replace(".", "root")
        label = d if d != "." else "(root entrypoints)"
        lines.append(f"- [`{label}`](#{anchor}) — {desc}")
    lines.append("")
    for d, desc in SCAN_DIRS:
        files = _files_in(d)
        if not files:
            continue
        anchor = d.replace("/", "").replace("_", "").replace(".", "root")
        label = d if d != "." else "(root entrypoints)"
        lines.append(f'<a id="{anchor}"></a>')
        lines.append(f"## `{label}` — {desc}")
        lines.append("")
        for fn in files:
            total += 1
            summary, pubs = _scan_file(os.path.join(ROOT, d if d != "." else "", fn))
            rel = fn if d == "." else f"{d}/{fn}"
            lines.append(f"### `{rel}`")
            lines.append(summary or "_(no module docstring — add ek 1-line role add karo)_")
            if pubs:
                lines.append("")
                for kind, name, pdoc in pubs:
                    tag = "🔧" if kind == "fn" else "📦"
                    lines.append(f"- {tag} `{name}` — {pdoc or '…'}")
            lines.append("")
    lines.insert(6, f"**{total} modules documented** across {len([d for d,_ in SCAN_DIRS if _files_in(d)])} folders.")
    lines.insert(7, "")
    return "\n".join(lines).rstrip() + "\n"


def main():
    content = build()
    check = "--check" in sys.argv
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    old = ""
    if os.path.exists(OUT):
        old = open(OUT, "r", encoding="utf-8").read()
    if check:
        if old != content:
            print("STALE: _DOCS/MODULES.md out of date — run: python _TOOLS/gen_module_docs.py")
            return 1
        print("OK: _DOCS/MODULES.md current")
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(content)
    n = content.count("\n### `")
    print(f"wrote {OUT} — {n} modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
