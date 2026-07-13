#!/usr/bin/env bash
# scripts/setup_hooks.sh — install this repo's git hooks into .git/hooks/.
#
# WHY: .git/hooks/ is per-clone and NOT tracked by git, so a fresh clone (or a
# second machine, or the VPS) has NO hooks until this runs. The pre-commit
# architecture audit (Rule 6B enforcement) only protects the repo if it's
# actually installed — this script closes that gap. Idempotent: safe to re-run.
#
# USAGE (one-time per clone/machine):
#   bash scripts/setup_hooks.sh
#
set -e

# repo root = parent of this script's dir (works regardless of cwd)
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SRC="$ROOT/scripts/pre-commit-architecture-audit.sh"
HOOKS_DIR="$(git -C "$ROOT" rev-parse --git-path hooks 2>/dev/null || echo "$ROOT/.git/hooks")"
DST="$HOOKS_DIR/pre-commit"

if [ ! -f "$SRC" ]; then
  echo "❌ missing $SRC — cannot install pre-commit hook"; exit 1
fi

mkdir -p "$HOOKS_DIR"
cp "$SRC" "$DST"
chmod +x "$DST" 2>/dev/null || true

echo "✅ installed pre-commit hook -> $DST"
echo "   (runs _TOOLS/architecture_audit.py on every commit; blocks on FAIL)"
echo "   skip in a genuine emergency with: git commit --no-verify"
