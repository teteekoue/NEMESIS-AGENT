---
name: verification-before-completion
description: Verify code changes and observable behavior before claiming completion.
version: 1.0.0
nemesis_builtin: true
source: https://github.com/obra/superpowers/tree/main/skills/verification-before-completion
source_license: MIT
---

# Verification before completion

Use this guide before reporting a code or configuration task as complete.

1. Identify the behavior changed and the smallest test or command that directly
   verifies it.
2. Run checks after the final edit; do not rely on an earlier passing result if
   relevant files changed afterward.
3. Inspect the final diff for unintended files, secrets, generated artifacts,
   and changes outside scope.
4. Distinguish verified facts from assumptions. State any blocked checks and
   their impact explicitly.
5. Never claim a test, build, deployment, or runtime action succeeded unless
   its result was observed.
