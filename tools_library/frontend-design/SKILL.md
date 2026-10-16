---
name: frontend-design
description: Design and refine useful, accessible, responsive web interfaces.
version: 1.0.0
nemesis_builtin: true
source: https://github.com/anthropics/skills/tree/main/skills/frontend-design
source_license: Apache-2.0
---

# Frontend design

Use this guide for UI implementation or visual refinement.

1. Inspect the existing component system, tokens, responsive behavior, and
   nearby screens before changing styles. Reuse established patterns.
2. Identify the user's primary task and make its next action obvious. Keep
   hierarchy clear with purposeful spacing, readable type, and restrained color.
3. Support narrow and wide viewports, keyboard navigation, visible focus, and
   accessible labels. Do not rely on color alone to communicate state.
4. Include loading, empty, error, and success states where the feature needs
   them; preserve entered data when an operation fails.
5. Prefer semantic HTML and existing UI primitives. Avoid decorative
   complexity, arbitrary animations, and one-off styling that conflicts with
   the product.
