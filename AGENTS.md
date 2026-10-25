# AGENTS.md

Contexte projet pour les agents IA et contributeurs. À lire avant toute modification.

---

## Contexte

**NEMESIS-CLI** est un agent IA autonome pour Linux, sous contrôle de l'utilisateur.
Trois surfaces partagent le **même moteur**, les **mêmes outils** et la **même mémoire** :

- **CLI** — `agent.py` (interface Rich/prompt_toolkit)
- **Web** — `web/` (backend FastAPI + frontend React/Vite)
- **Telegram** — `telegram_bot.py` (bot, mémoire partagée)

Développé par **Ekoue TETE** (TEJF). Licence : voir `LICENSE`. Version : `2.2.2` (`pyproject.toml`).

---

## Stack & prérequis

- **Python** >= 3.10 (`requires-python` dans `pyproject.toml`)
- **Node.js** 18+ / npm (Web + serveur MCP GitHub via `npx`)
- **curl** (vérification de démarrage backend Web)
- Frontend : React + Vite + TypeScript + Tailwind + shadcn/ui (Radix)
- Backend Web : FastAPI + Uvicorn + WebSockets

---

## Lancer

```bash
# CLI
./venv/bin/python3 agent.py

# Web (backend + frontend, auto-detection venv + installe deps + attend /api/health)
./web/start.sh

# Telegram
./run_telegram_bot.sh

# Installation complète (venv + deps + deps Web optionnelles)
./install.sh
```

Ports Web : backend `8000` (`NEMESIS_WEB_PORT`), frontend `5173` (`NEMESIS_FRONTEND_PORT`).
Le backend FastAPI sert la SPA compilée si `web/frontend/dist` existe (SPA fallback).

---

## Vérifications avant commit

```bash
# Python : compilation rapide
python3 -m compileall -q src web/backend providers agent.py telegram_bot.py

# Tests unitaires
./venv/bin/pytest -q

# Frontend : typecheck + build
cd web/frontend && npm run lint && npm run build
```

Toujours lancer au minimum le typecheck frontend (`npm run lint`) et la compilation Python
après une modification. Ne pas committer `node_modules/`, `dist/`, `__pycache__/`, ni de secrets.

---

## Architecture

```text
agent.py                     CLI principale
telegram_bot.py              Bot Telegram
action_parser.py             Parseur multi-niveaux des réponses IA
tools.py                     Exécuteur d'outils principal
tools_schema.py              Schéma des outils (42)
src/core/
  agent_tools.py             Registre unifié + prompt système + outils mémoire
  subagents.py               Flotte temporaire parallèle (API OpenAI-compatible)
  memory_store.py            Mémoire persistante (prefs + blocs)
  github_settings.py         Connexion GitHub + sync serveur MCP
  mcp_manager.py             Gestionnaire MCP (pool de clients)
  tool_registry.py           Registre des outils
  system_prompt.py           Construction du prompt système
  paths.py                   Résolution des chemins (install vs user config)
  tools/                     Handlers d'outils (read_file, bash, glob, ...)
providers/
  nemapi_v3.py               Provider NEMAPI (OpenAI-compatible)
web/
  backend/                   API REST + WS (/ws/chat, /ws/terminal), sessions, PTY
  frontend/                  React + Vite (src/components, src/store, src/lib)
  start.sh                   Lancement dev backend + frontend
docs/                        WEB_UI, MEMORY, GITHUB, CHANGELOG
docs/SUBAGENTS.md            Configuration et coordination des sous-agents
```

**Données utilisateur** (partagées par toutes les surfaces) :

- `~/.config/nemesis-cli/config.yaml` — configuration principale
- `~/.config/nemesis-cli/memory/` — mémoire (`prefs.json` + `blocks.json`)
- `~/.config/nemesis-cli/github.yaml` — identifiants GitHub (jamais commité)
- `~/.config/nemesis-cli/telegram_bot.yaml` — overlay config bot
- `~/.config/nemesis-cli/mcp_config.yaml` — serveurs MCP
- `~/.config/nemesis-cli/.nemesis_todos.json` — plan de travail partagé

Répertoire de config surchargeable par `NEMESIS_CONFIG_DIR`.

---

## Décisions d'architecture (importantes)

- **Première requête unifiée** : le prompt système (outils + mémoire) est fusionné avec le
  premier message utilisateur → 1 aller-retour provider au démarrage au lieu de 2.
  Implémenté via `send_message(system_prefix=...)` dans `providers/nemapi_v3.py`, appliqué dans
  `web/backend/agent_runner.py`, `telegram_bot.py` et `agent.py`.
- **Mémoire globale unique** injectée uniquement dans `build_system_prompt()` → toutes surfaces.
- **GitHub via MCP** (`src/core/github_settings.py`) : génère le serveur officiel
  `@modelcontextprotocol/server-github` avec le token en env. Aucun outil ajouté au registre principal.
- **Pool de clients MCP** (`_ClientPool` dans `src/core/mcp_manager.py`) : évite le coût de
  démarrage `npx`/`uvx` à chaque appel. Utiliser `get_client`/`release_client`, ne pas `close()`.
- **Token GitHub jamais renvoyé au frontend** (aperçu masqué).
- **Sous-agents éphémères** : `delegate_tasks` crée des workers concurrents via API OpenAI-compatible ;
  `subagent_status` suit leurs rapports en mémoire. Ils partagent les outils NEMESIS, sans identité
  persistante ni délégation récursive. Le provider et sa clé sont configurés sous `subagents` dans
  `config.yaml` ; les routes Web ne renvoient jamais la clé.
- **Pas de sandbox de fichiers** : `security.workspace` n'est qu'un répertoire de départ
  (vide = répertoire courant). `src/core/paths.py` expose `session_cwd()`, `set_session_cwd()`
  et `resolve_path()` ; les chemins relatifs suivent le `cd` persistant de l'outil `bash`.
- **Aucune limite d'itérations** : `while True` dans `agent.py`, `web/backend/agent_runner.py`,
  `telegram_bot.py` et `src/core/agent_manager.py`. Seules sorties : plus d'appel d'outil,
  interruption utilisateur, doublon de réponse, erreur provider. Les sous-agents utilisent
  `max_iterations: 0` (= illimité) et retombent sur le provider principal sans clé dédiée.
- **Feedback d'outil uniforme** : `src/core/agent_tools.py::format_tool_feedback` expose
  toujours stdout+stderr, le code de sortie et le signal.
- **Pas de conversation fictive par défaut** : le mock n'est chargé qu'avec `?demo=1`.
  Backend injoignable → bandeau « Backend injoignable » + bouton Réessayer.
- **Thème** : clair (blanc-bleu) par défaut, sombre (bleu nuit) ; persisté dans `localStorage("nemesis-theme")`.

---

## Conventions de code

- **Pas de commentaires** dans le code sauf nécessité réelle (expliquer le « pourquoi »).
- Respecter le style existant ; réutiliser les patterns en place plutôt que d'en introduire.
- Chaînes/UI en **français**. Réponses de l'assistant en français.
- Python : type hints, `from __future__ import annotations` en tête des modules récents.
- Frontend : TypeScript strict, composants dans `src/components`, état global Zustand (`src/store`).
- Ne jamais exposer/logguer de secrets. Ne pas écrire de clés LLM/clés d'environnement dans le projet.

---

## WebSockets (frontend)

- `/ws/chat` — query `mode`, `session`, `model`. Événements serveur : `ready`, `status`,
  `assistant_text`, `tool_start`, `tool_result`, `tool_denied`, `authorize_request`, `notice`,
  `error`, `turn_end`, `mode_changed`.
- `/ws/terminal` — PTY réel, query `cols`/`rows`.
- Les noms d'événements côté store (`web/frontend/src/store/useAppStore.ts`) **doivent** rester
  alignés avec les émissions serveur (`web/backend/agent_runner.py`, `app.py`).

---

## Historique récent

### Non publié — Autonomie totale, chemins libres, gros travaux shell
- Suppression de toutes les limites d'itérations (CLI, Web, Telegram, A2A, sous-agents).
- Plus de confinement à un workspace : chemins absolus partout, chemins relatifs ancrés sur un
  `cd` persistant partagé entre appels `bash`.
- `write_file`/`edit`/`delete_file` respectent strictement `path`/`file_path`/`filename`, créent
  les dossiers parents et renvoient un chemin absolu ; dossier cible = erreur explicite.
- `bash` : bash comme shell, heredocs/multi-lignes, timeout 10 s–7 jours (défaut 600 s), sortie
  jamais vide (code de sortie explicite), stderr et signal toujours remontés.
- Sous-agents : repli automatique sur le provider principal quand aucune clé dédiée n'existe ;
  statut de délégation affiché au démarrage, dans `/status` et dans `GET /api/subagents`.
- `apply_patch` : moteur d'application intégré (aucune dépendance au cwd), `workdir`, chemins
  absolus, multi-hunks/multi-fichiers, création/suppression, `dry_run`, erreurs explicites.
- Exécuteur historique (`tools.py`) aligné : alias `path`/`file_path`, `cd` persistant, exit code.
- Résolveur de chemin unique `resolve_target_path()` partagé par `write_file`, `edit`,
  `read_file`, `delete_file` et l'exécuteur historique : `path`, `file_path`, `filename`
  sont interchangeables, dossier + nom de fichier sont joints, dossier seul = erreur.
- PTY réservé aux programmes interactifs (`_looks_interactive`) : plus de `cd` perdu ni de
  sortie vide quand une commande contient `node` ou `read`.
- `apply_patch` : `workdir` → shell → ancêtres → racine du projet → `$HOME`.
- `nemesis --check` : auto-vérification des correctifs + chemin des modules chargés
  (distingue un correctif absent d'un paquet installé obsolète ou d'un serveur non redémarré).
- Docs `Non publié` : voir `docs/CHANGELOG.md`.

### Interface Web, Mémoire, GitHub, Performance
- Interface Web complète (backend FastAPI + SPA React) : chat streaming, terminal PTY,
  explorateur de fichiers, 8 onglets de réglages, thème responsive.
- Mémoire persistante (`src/core/memory_store.py`) partagée CLI/Web/Telegram + outils `memory_*`.
- Intégration GitHub par PAT + serveur MCP officiel.
- Optimisations : requête unifiée, sessions HTTP keep-alive, caches (TTL), pool MCP, frontend mémoïsé.

### Corrections
- `web/start.sh` : détection venv, installation auto des deps backend, attente de `/api/health`,
  erreurs explicites (corrige `ModuleNotFoundError: uvicorn` et la boucle `ECONNREFUSED`).
- Plus de session fictive par défaut ; bandeau hors-ligne + Réessayer.
- `install.sh` : deps backend Web installées dans le venv.
- README réécrit en version concise orientée commandes.

Journal complet : `docs/CHANGELOG.md`.

---

## Interface CLI

La couche `src/ui/` a été refondue (branche `ui/v2`, octobre 2026) : messages en gouttière
sans cadre, cartes d'outils sobres, streaming bash en temps réel, bannière ASCII au démarrage,
résumé de tour, commande `/continue`, double Ctrl+C pour fermer.

**Documentation complète : [`docs/UI.md`](docs/UI.md)** — principes de design, API de `ChatUI`,
règles de rendu des outils, points fragiles, commandes de vérification.

À retenir avant de toucher à `src/ui/` :

- `ChatUI.thinking()` doit rester un **générateur** (`next(anim)` / `anim.close()` dans `agent.py`).
- `TextArea` de prompt_toolkit n'expose que `text`, `document`, `accept_handler` — pas de `.title` ni `.prompt`.
- `Rich Markdown` n'accepte pas `inline_code_style` (utiliser `inline_code_theme`).
- Les sorties d'outils sont tronquées à 8 lignes (`TOOL_OUTPUT_PREVIEW_LINES`) et la largeur est capée à 110 colonnes (`MAX_CONTENT_WIDTH`).

---

## Dépannage courant

| Problème | Solution |
| --- | --- |
| `ModuleNotFoundError: uvicorn` / `ECONNREFUSED` en boucle | `./web/start.sh` |
| Bandeau « Backend injoignable » | Lancer `python -m web.backend` puis Réessayer |
| NEMAPI injoignable en local | Onglet Connexion → « Adresse IP et port » (pas d'URL) → Tester |
| Provider injoignable | Vérifier `config.yaml` / onglet Connexion |

Docs : `docs/WEB_UI.md`, `docs/UI.md`, `docs/MEMORY.md`, `docs/GITHUB.md`, `docs/CHANGELOG.md`,
`NEMESIS.md`, `TOOL_CALL_FORMATS.md`.