---
name: unit-converter
description: Convert between common units (meters/km, celsius/fahrenheit). Use when the user asks for unit conversion.
version: 1.0.0
nemesis_builtin: true
source: https://github.com/teteekoue/NEMESIS-AGENT/tree/main/tools_library/unit-converter
source_license: MIT
---

# Unit Converter Skill

When the user asks to convert units:

1. Prefer the MCP calculator server if available for arithmetic.
2. Formulas:
   - km to m: multiply by 1000
   - m to km: divide by 1000
   - C to F: F = C * 9/5 + 32
   - F to C: C = (F - 32) * 5/9

3. Always show the formula used and the numeric result.
4. For multi-step math, call MCP tool `calculate` on server `calculator`.
