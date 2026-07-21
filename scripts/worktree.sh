#!/usr/bin/env bash
# scripts/worktree.sh — parallel research worktrees spin up / list / remove.
# Poora workflow: WORKFLOW.md padho.
#
#   bash scripts/worktree.sh new  <name>   # origin/master se fresh branch + worktree
#   bash scripts/worktree.sh list          # kaunse worktrees chal rahe hain
#   bash scripts/worktree.sh done <name>   # worktree hatao (branch rehta hai)
#
# Har worktree ki apni .claude/ + apna session-lock hota hai (gitignored) — isliye
# har worktree me ek alag Claude session bina collide kiye likh sakti hai.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WT_DIR="$ROOT/.claude/worktrees"

cmd="${1:-}"
case "$cmd" in
  new)
    name="${2:?usage: worktree.sh new <name>}"
    branch="wt/$name"
    path="$WT_DIR/$name"
    [ -e "$path" ] && { echo "❌ pehle se hai: $path"; exit 1; }
    mkdir -p "$WT_DIR"
    git -C "$ROOT" fetch origin master --quiet || true
    git -C "$ROOT" worktree add -b "$branch" "$path" origin/master
    echo ""
    echo "✅ worktree ready: $path"
    echo "   branch: $branch (origin/master se fresh)"
    echo ""
    echo "   → ab ek NAYI Claude session ISI folder me kholo."
    echo "     Ye apne lock pe chalegi — main folder ki session ko block nahi karegi."
    ;;
  list)
    git -C "$ROOT" worktree list
    ;;
  done)
    name="${2:?usage: worktree.sh done <name>}"
    path="$WT_DIR/$name"
    git -C "$ROOT" worktree remove "$path"
    echo "🧹 hata diya: $path"
    echo "   branch 'wt/$name' abhi bhi hai — merge/push kar chuke ho to:"
    echo "     git branch -D wt/$name"
    ;;
  *)
    echo "usage: worktree.sh {new <name> | list | done <name>}"
    echo "  new  <name>  → origin/master se fresh worktree+branch"
    echo "  list         → sab worktrees"
    echo "  done <name>  → worktree hatao"
    exit 1
    ;;
esac
