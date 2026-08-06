#!/usr/bin/env bash
# .git/hooks/pre-commit
#
# Automatically runs the mechanical architecture audit (_TOOLS/architecture_audit.py)
# on every commit. Blocks the commit if it FAILs. This does NOT depend on any
# AI reading a prompt, remembering a rule, or being in a good mood — it runs
# every single time, for every author (human or AI), no exceptions.
#
# INSTALL (one-time, per machine/clone):
#   cp scripts/pre-commit-architecture-audit.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit
#
# To skip in a genuine emergency (NOT recommended, logs a warning):
#   git commit --no-verify

# NOTE: no `set -e` here — we need to capture the audit's exit code ourselves
# (with set -e a FAIL would abort before the friendly error message prints).

REPO_ROOT="$(git rev-parse --show-toplevel)"
AUDIT_SCRIPT="$REPO_ROOT/_TOOLS/architecture_audit.py"

if [ ! -f "$AUDIT_SCRIPT" ]; then
    echo "⚠️  architecture_audit.py not found yet (Task 1 not built) — skipping audit gate."
    exit 0
fi

# Pick a python that ACTUALLY RUNS — not just one that's on PATH. On Windows the
# `python3` alias is often a Microsoft Store shim that errors out; test each candidate
# with a trivial import so we skip it. Works on Windows dev + Linux VPS.
PY=""
for _c in python python3 py; do
    if command -v "$_c" >/dev/null 2>&1 && "$_c" -c "import sys" >/dev/null 2>&1; then
        PY="$_c"; break
    fi
done
if [ -z "$PY" ]; then
    echo "⚠️  no working python found on PATH — skipping audit gate (fix your PATH)."
    exit 0
fi

echo "🔍 Running architecture audit before commit..."
"$PY" "$AUDIT_SCRIPT" --staged-only
AUDIT_EXIT=$?

if [ $AUDIT_EXIT -ne 0 ]; then
    echo ""
    echo "❌ COMMIT BLOCKED — architecture_audit.py found violations."
    echo "   Fix the FAILs above, or re-run with --report for details."
    echo "   Emergency bypass (logs a warning, use only if truly necessary):"
    echo "     git commit --no-verify"
    exit 1
fi

echo "✅ Architecture audit passed."

# --- auto-refresh module docs (_DOCS/MODULES.md) when any .py is staged ----------
# MODULES.md code ke module-docstrings se generate hoti hai. Agar is commit me koi
# .py badla, doc dobara banao + commit me shaamil karo — refactor pe doc apne-aap
# current. Non-fatal (doc-gen fail se commit block nahi hota — audit gate hi asli gate).
DOCGEN="$REPO_ROOT/_TOOLS/gen_module_docs.py"
if [ -f "$DOCGEN" ] && git diff --cached --name-only --diff-filter=ACMR | grep -q '\.py$'; then
    echo "📚 Refreshing _DOCS/MODULES.md from docstrings..."
    if "$PY" "$DOCGEN" >/dev/null 2>&1; then
        git add "$REPO_ROOT/_DOCS/MODULES.md" 2>/dev/null || true
    else
        echo "   ⚠️  doc-gen failed (skipping — not blocking the commit)"
    fi
fi
exit 0
