# NEMESIS - Documentation du Projet

## Vue d'ensemble

NEMESIS est un agent autonome de codage et d'administration Linux. Il utilise un provider NEMAPI pour l'agent principal et un provider OpenAI-compatible distinct pour créer des sous-agents éphémères en parallèle.

## Architecture

### Composants principaux

- **`agent.py`** : Point d'entrée CLI principal, classe `NemesisApp`
- **`cli.py`** : Interface coding-agent alternative (même moteur)
- **`telegram_bot.py`** : Bot Telegram (même moteur, mémoire partagée)
- **`web/`** : Interface Web — backend FastAPI (`web/backend/`) + frontend React (`web/frontend/`)
- **`providers/`** : Provider NEMAPI pour l'agent principal
- **`src/core/`** : Logique métier (outils, flotte éphémère, MCP, mémoire, GitHub)
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
| `nemapi` | Provider principal NEMAPI, contexte serveur et modèles via `/v1/models` |

### Sous-agents éphémères

Les outils `delegate_tasks` et `subagent_status` coordonnent des tâches temporaires indépendantes, exécutées simultanément via une API OpenAI-compatible. Chaque worker a un historique de tâche complet envoyé à chaque requête et le même registre d'outils NEMESIS, sans possibilité de délégation récursive. Les workers disparaissent après leur tâche ; leurs rapports restent temporairement en mémoire.

**Fichiers clés :**
- `src/core/subagents.py` : flotte, exécution concurrente, historique et statuts
- `src/core/tool_bridge.py` et `src/core/agent_tools.py` : outils locaux partagés et coordination
- `~/.config/nemesis-cli/config.yaml` : provider et paramètres sous la clé `subagents`
- `docs/SUBAGENTS.md` : configuration et fonctionnement

## État technique

- Les réponses JSON et SSE de NEMAPI sont normalisées par le provider.
- **Première requête unifiée** : le prompt système (outils + mémoire) est fusionné
  avec le premier message utilisateur (`system_prefix`) pour ne consommer qu'un
  seul aller-retour provider au démarrage d'une session. Ce comportement est
  appliqué au CLI, au bot Telegram et au Web.
- Les sous-agents exécutent localement les mêmes outils que le bridge principal ; en Web, le mode Confirmation s'applique aussi à leurs actions à risque.
- Le contexte conversationnel reste côté serveur NEMAPI ; aucun historique
  complet n'est reconstruit côté client.
- Les requêtes de sous-agents, contrairement à NEMAPI, incluent l'historique complet OpenAI-compatible à chaque appel.
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
  workspace: ""   # répertoire de départ facultatif (vide = répertoire courant)
```

NEMESIS n'est pas confiné à ce répertoire : tous les chemins du système sont accessibles,
les chemins relatifs suivent le répertoire courant persistant du shell, et la boucle d'outils
n'a aucune limite d'itérations.

### Sous-agents

Configurer le provider, l'URL, le modèle, la clé API et la limite de concurrence dans Réglages Web → Sous-agents. La clé Groq peut aussi être fournie par `GROQ_API_KEY`. Voir [docs/SUBAGENTS.md](docs/SUBAGENTS.md).

## Commandes disponibles

| Commande | Description |
|----------|-------------|
| `/help` | Affiche les commandes disponibles |
| `/clear` | Efface l'écran |
| `/config` | Configure l'URL et le port du serveur NEMAPI |
| `/models` | Liste et sélectionne un modèle officiel, ou saisie manuelle |
| `/stats` | Statistiques de session |
| `/agents` | Rappel du fonctionnement éphémère |
| `/delegate <instruction>` | Crée une tâche éphémère |
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
- Les sous-agents utilisent une API OpenAI-compatible et sont configurés dans `config.yaml`
- Node.js 18+ est requis pour l'interface Web et le serveur MCP GitHub

---

*Document mis à jour le 15 octobre 2026*
