# Journal des modifications — NEMESIS

Ce document regroupe l'historique des évolutions, avec un focus sur les récentes sessions de développement.

---

## [Non publié] — Interface Web, Mémoire, GitHub, Performance

### Ajouts

- **Interface Web complète** (`web/`) : backend FastAPI + SPA React/Vite/TypeScript/Tailwind/shadcn-ui.
  - Chat en streaming (WebSocket `/ws/chat`), terminal PTY (`/ws/terminal`), explorateur de fichiers.
  - Réglages en 8 onglets : Connexion, Général, Mémoire, MCP, Skills, Outils, GitHub, Telegram.
  - Thème blanc-bleu (clair) et bleu nuit (sombre), persistant, entièrement responsive.
  - Mode de session `Confirmation` / `Toutes autorisations`.
  - Logo NEMESIS (`web/frontend/public/logo.jpg`).
- **Mémoire persistante** (`src/core/memory_store.py`) partagée entre CLI, Web et Telegram.
  - 6 outils : `memory_add`, `memory_update`, `memory_search`, `memory_get`, `memory_delete`, `memory_clear`.
  - Injection automatique dans le prompt système (`build_system_prompt`).
- **Intégration GitHub** (`src/core/github_settings.py`).
  - Connexion par PAT + identité Git.
  - Génération automatique du serveur **MCP GitHub** (`@modelcontextprotocol/server-github`).
- **Bot Telegram** : partage de la mémoire et du moteur ; token configurable depuis le Web.

### Performances

- **Première requête unifiée** : le prompt système (outils + mémoire) est fusionné avec le premier message
  utilisateur → **1 aller-retour provider au lieu de 2** au démarrage d'une session
  (`providers/nemapi_v3.py::send_message(system_prefix=...)`, appliqué à `agent_runner.py`, `telegram_bot.py`, `agent.py`).
- **Sessions HTTP persistantes** (keep-alive) dans le provider ; timeout de connexion réduit.
- **Mise en cache** : test de connexion (TTL 20 s), catalogue d'outils (TTL 30 s), résolution des chemins (`ensure_user_dirs` une seule fois par process).
- **Pool de serveurs MCP** (`src/core/mcp_manager.py::_ClientPool`) : évite le coût de démarrage `npx`/`uvx` à chaque appel.
- **Frontend** : `MessageBubble` mémoïsé, auto-scroll non intrusif, thème appliqué avant le premier rendu.

### Corrections

- Politique de retry du provider assouplie (délai 3 s → 1 s).
- Polling d'interruption resserré (500 ms → 20 ms).
- Suppression d'un envoi de prompt système devenu inutile côté bot Telegram.
- Containers/dialogues adaptés aux petits écrans.
- **Lancement Web robuste** (`web/start.sh`) : détection automatique du virtualenv (`./venv`, `.venv`),
  installation automatique des dépendances backend manquantes (`uvicorn`/`fastapi`), attente de
  `/api/health` avant de démarrer Vite, messages d'erreur explicites.
- **Plus de conversation fictive par défaut** : le mock n'est chargé qu'avec `?demo=1`. Si le backend
  est injoignable, l'UI affiche un bandeau « Backend injoignable » avec un bouton **Réessayer** au lieu
  de masquer l'erreur (corrige l'atterrissage sur une fausse session).
- Installation Web dans `install.sh` : les dépendances backend sont désormais installées dans le venv.
- **Filet de sécurité timeout bash** : l'outil `bash` expose un paramètre `timeout` **en secondes**
  (min 10, max 600, défaut 120). L'agent l'ajuste selon la commande ; toute commande synchrone est
  désormais bornée et ne peut plus tourner indéfiniment. Les commandes en arrière-plan
  (`is_background=true`) restent non bornées et renvoient immédiatement un `task_id`.

### Fichiers clés

| Fichier | Rôle |
| ------- | ---- |
| `src/core/memory_store.py` | Mémoire persistante (préférences + mini-blocs) |
| `src/core/github_settings.py` | Connexion GitHub + synchronisation MCP |
| `src/core/agent_tools.py` | Registre unifié (29 outils) + prompt + outils mémoire |
| `src/core/mcp_manager.py` | Pool de clients MCP |
| `providers/nemapi_v3.py` | `system_prefix`, session HTTP, cache de connexion |
| `web/backend/*` | API REST + WebSocket + settings |
| `web/frontend/*` | SPA React |

### Dépendances ajoutées

- Backend Web : `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `websockets>=12` (voir `web/requirements.txt`).
- Frontend : voir `web/frontend/package.json` (React, Vite, Tailwind, Radix UI, xterm, zustand, ...).
- MCP GitHub : fourni à l'exécution via `npx` (Node.js requis, non embarqué).

---

## [2.0.0] — Base

- Agent CLI autonome (`agent.py`), interface coding-agent (`cli.py`).
- 20+ outils (fichiers, bash, recherche, processus, web).
- Système d'autorisation `y`/`n`/`a`.
- Support MCP, skills, sous-agents.
- Bot Telegram (`telegram_bot.py`).
- Support Linux et Android/Termux.