---
name: git-workflow
description: Inspect repository state and perform safe, scoped Git operations.
version: 1.0.0
nemesis_builtin: true
source: https://github.com/obra/superpowers/tree/main/skills/using-git-worktrees
source_license: MIT
---

# Git workflow

Use this guide for repository changes and Git operations.

1. Inspect `git status`, the relevant diff, and repository guidance before
   editing. Preserve unrelated user changes.
2. Keep changes scoped to the request; do not reset, clean, overwrite, or amend
   unrelated work. Ask before destructive or externally visible operations.
3. Prefer focused edits and run the narrowest relevant tests before broader
   checks. Report the exact checks and their outcome.
4. Do not commit or push unless the user explicitly asks. If asked to commit,
   inspect the staged diff and use a concise message that reflects the change.
5. For parallel work, isolate changes when practical and reconcile/test the
   combined result before reporting.
