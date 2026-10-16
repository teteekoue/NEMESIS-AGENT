---
name: systematic-debugging
description: Diagnose bugs from reproducible evidence before changing code.
version: 1.0.0
nemesis_builtin: true
source: https://github.com/obra/superpowers/tree/main/skills/systematic-debugging
source_license: MIT
---

# Systematic debugging

Use this guide when behavior is broken, inconsistent, or newly regressed.

1. Reproduce the failure and capture the exact input, output, logs, and
   environment relevant to it.
2. Trace the failing path from its entry point to the observed result. Compare
   working and failing cases and identify the earliest divergence.
3. Form a testable root-cause hypothesis. Gather evidence that could disprove
   it before editing.
4. Make the smallest change that fixes the cause, then add or update a
   regression test at the narrowest useful layer.
5. Rerun the reproducer and related tests. Report remaining uncertainty rather
   than claiming a broader fix than the evidence supports.
