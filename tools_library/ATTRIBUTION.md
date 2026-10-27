# Origine des skills intégrés

Les `SKILL.md` de ce dossier sont les **versions originales** récupérées sur
GitHub, et non des résumés réécrits. Seul le frontmatter est adapté : le champ
`name` conserve l'identifiant NEMESIS (utilisé par les réglages), et les champs
`nemesis_builtin`, `source`, `source_license`, `upstream_commit` et
`upstream_name` documentent la provenance. Le **corps du skill est verbatim**.
Les fichiers compagnons (références, scripts, licence) sont également copiés.

| Skill | Source | Licence |
| --- | --- | --- |
| `brainstorming` | [obra/superpowers · skills/brainstorming](https://github.com/obra/superpowers/tree/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/brainstorming) | MIT |
| `systematic-debugging` | [obra/superpowers · skills/systematic-debugging](https://github.com/obra/superpowers/tree/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/systematic-debugging) | MIT |
| `verification-before-completion` | [obra/superpowers · skills/verification-before-completion](https://github.com/obra/superpowers/tree/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/verification-before-completion) | MIT |
| `git-workflow` | [obra/superpowers · skills/using-git-worktrees](https://github.com/obra/superpowers/tree/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/using-git-worktrees) — `name` conservé comme alias, voir `upstream_name` | MIT |
| `frontend-design` | [anthropics/skills · skills/frontend-design](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design) | Apache-2.0 (texte dans `frontend-design/LICENSE.txt`) |
| `cybersecurity-audit` | [cybersecurity-audit-expert · SKILL.md](https://github.com/Arsan-sk/BackreyShop/blob/8e56001be9e9d09872811511ae5fa66683723467/skills/cybersecurity-audit-expert/SKILL.md) — `name` conservé comme alias | Non déclarée dans le dépôt source ; fichier public, attribution conservée |
| `unit-converter` | Original NEMESIS (inchangé depuis le premier commit) | MIT |

## Pourquoi le skill cybersécurité a changé

L'ancienne version était un texte de politique défensive rédigé localement :
cadrage obligatoire, niveaux d'activité, confirmations avant chaque test et
consignes de refus. Elle bridait un agent autonome. La version restaurée est un
guide AppSec complet (empreinte de la pile, OWASP Top 10, classes d'attaques par
technologie, preuves, corrections, vérification, rapport `SECURITY_AUDIT.md` et
verdict) : elle dit **comment** auditer, pas **ce que l'agent n'a pas le droit**
de faire. Les décisions d'autorisation restent du ressort de l'utilisateur et du
mécanisme d'approbation des outils de NEMESIS.

## Mise à jour

`src/core/paths.py::_seed_bundled_skills` rafraîchit automatiquement les skills
marqués `nemesis_builtin: true` dans `~/.config/nemesis-cli/tools_library/`
lorsque la version intégrée diffère. Un skill personnalisé par l'utilisateur
(marqueur retiré du frontmatter) n'est jamais écrasé.
