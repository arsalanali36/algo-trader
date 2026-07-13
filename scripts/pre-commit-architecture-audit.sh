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

echo "🔍 Running architecture audit before commit..."
PY="$(command -v python3 || command -v python)"
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
exit 0
