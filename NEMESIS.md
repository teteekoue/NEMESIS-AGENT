# NEMESIS - Documentation du Projet

## Vue d'ensemble

NEMESIS est un agent de codage autonome en CLI, spécialisé dans l'ingénierie logicielle et l'administration système sur Linux. Il utilise une architecture modulaire avec des providers LLM, des outils système et un système de subagents via le protocole A2A.

## Architecture

### Composants principaux

- **`agent.py`** : Point d'entrée CLI principal, classe `NemesisApp`
- **`cli.py`** : Interface coding-agent alternative (même moteur)
- **`telegram_bot.py`** : Bot Telegram (même moteur, mémoire partagée)
- **`web/`** : Interface Web — backend FastAPI (`web/backend/`) + frontend React (`web/frontend/`)
- **`providers/`** : Provider NEMAPI unique, avec contexte conservé côté serveur
- **`src/core/`** : Logique métier (outils, commandes, A2A, MCP, mémoire, GitHub)
- **`src/ui/`** : Interface utilisateur avec Rich
- **`tools/`** : Outils système et exécuteur

### Surfaces et partage

Le CLI, le Web et Telegram utilisent le **même registre d'outils** (`src/core/agent_tools.py`),
le **même prompt système** (`build_system_prompt`) et la **même mémoire persistante**
(`src/core/memory_store.py`, stockée dans `~/.config/nemesis-cli/`).

### Intégrations

- **Mémoire** : mini-blocs durables injectés dans le prompt de chaque session.
- **GitHub** : `src/core/github_settings.py` génère un serveur MCP GitHub à partir d'un PAT.
- **MCP** : `src/core/mcp_manager.py` gère un pool de serveurs (démarrage mis en cache).

### Provider

| Provider | Description |
|----------|-------------|
| `nemapi` | Provider unique NEMAPI, contexte serveur et modèles via `/v1/models` |

### Subagents (A2A)

Le système de subagents utilise le protocole A2A (Agent-to-Agent) pour la délégation de tâches.

**Fichiers clés :**
- `src/core/agent_manager.py` : Gestion des subagents
- `src/core/a2a_protocol.py` : Protocole A2A
- `agents.json` : Configuration des subagents

**Subagent actuel :**
- Nom : Agent1
- Provider : nemapi
- Modèle : sélectionné depuis `/v1/models`
- Statut : outils exécutés par le bridge partagé

## État technique

- Les réponses JSON et SSE de NEMAPI sont normalisées par le provider.
- **Première requête unifiée** : le prompt système (outils + mémoire) est fusionné
  avec le premier message utilisateur (`system_prefix`) pour ne consommer qu'un
  seul aller-retour provider au démarrage d'une session. Ce comportement est
  appliqué au CLI, au bot Telegram et au Web.
- Les appels d'outils sont exécutés par le bridge partagé, pour l'agent
  principal comme pour les sous-agents.
- Le contexte conversationnel reste côté serveur NEMAPI ; aucun historique
  complet n'est reconstruit côté client.
- La mémoire persistante est ajoutée au prompt système lorsque l'option est
  activée (partagée par toutes les surfaces).

## Configuration

### config.yaml

```yaml
provider:
  type: nemapi
  model: qwen-chat

nemapi:
  host: 127.0.0.1
  port: 8090

security:
  workspace: ./workspace
```

### agents.json

Les sous-agents utilisent la même instance NEMAPI et le même contexte serveur
que l'agent principal. Aucun token de fournisseur n'est requis par NEMESIS.

## Commandes disponibles

| Commande | Description |
|----------|-------------|
| `/help` | Affiche les commandes disponibles |
| `/clear` | Efface l'écran |
| `/config` | Configure l'URL et le port du serveur NEMAPI |
| `/models` | Liste et sélectionne un modèle officiel, ou saisie manuelle |
| `/stats` | Statistiques de session |
| `/agents` | Gestion des subagents |
| `/delegate <agent> <instruction>` | Délègue une tâche |
| `/tools` | Liste des outils disponibles |
| `/show` | Affiche les sorties cachées |

## État actuel

- NEMAPI est le seul provider public.
- Les modèles sont récupérés dynamiquement depuis `/v1/models`.
- NEMAPI conserve le contexte ; le client n'envoie pas tout l'historique à
  chaque tour.
- Le parseur accepte les appels JSON, XML/Qwen, groupés et tronqués.

## Interface Web

- **Backend** : `web/backend/app.py` (FastAPI) — REST + WebSockets `/ws/chat` et `/ws/terminal`.
- **Frontend** : `web/frontend/` — React + Vite + TypeScript + Tailwind + shadcn/ui.
- **Lancement dev** : `./web/start.sh` (backend `:8000`, frontend `:5173`).
- **Déploiement** : `python -m web.backend` sert le build React (`web/frontend/dist`).
- Détails : [docs/WEB_UI.md](docs/WEB_UI.md).

## Mémoire et GitHub

- **Mémoire** : `src/core/memory_store.py` — blocs durables dans `~/.config/nemesis-cli/memory/`.
  Détails : [docs/MEMORY.md](docs/MEMORY.md).
- **GitHub** : `src/core/github_settings.py` — connexion par PAT, génération d'un serveur MCP GitHub.
  Détails : [docs/GITHUB.md](docs/GITHUB.md).

## Notes de développement

- Le projet utilise Python 3.10+
- L'interface utilisateur utilise la bibliothèque `rich`
- Le protocole A2A est basé sur JSON
- Les subagents sont configurés via `agents.json`
- Node.js 18+ est requis pour l'interface Web et le serveur MCP GitHub

---

*Document mis à jour le 15 octobre 2026*
