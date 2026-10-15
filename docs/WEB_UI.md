# NEMESIS — Interface Web

Interface Web moderne de l'agent NEMESIS : un backend **FastAPI** (Python) qui réutilise le moteur de l'agent, et une SPA **React** (Vite, TypeScript, Tailwind, shadcn/ui).

---

## Sommaire

1. [Aperçu](#aperçu)
2. [Prérequis](#prérequis)
3. [Installation](#installation)
4. [Lancement](#lancement)
5. [Déploiement « tout-en-un »](#déploiement-tout-en-un)
6. [Fonctionnalités](#fonctionnalités)
7. [Architecture](#architecture)
8. [API REST](#api-rest)
9. [WebSockets](#websockets)
10. [Réglages](#réglages)
11. [Thème et responsive](#thème-et-responsive)
12. [Variables d'environnement](#variables-denvironnement)
13. [Dépannage](#dépannage)

---

## Aperçu

L'interface Web expose **exactement le même moteur** que le CLI (`ToolBridge`, `ToolRegistry`, `build_system_prompt`, provider NEMAPI). Elle ajoute :

- le streaming des événements de l'agent via WebSocket ;
- des autorisations asynchrones (boîte de dialogue) au lieu d'un prompt terminal ;
- un terminal PTY réel, un explorateur de fichiers et des réglages complets ;
- la mémoire persistante et les intégrations (GitHub, Telegram) partagées avec les autres surfaces.

---

## Prérequis

- Python 3.10+
- Node.js 18+ et npm

---

## Installation

### Dépendances backend

```bash
pip install -r web/requirements.txt
```

Contenu de `web/requirements.txt` :

```text
fastapi>=0.110
uvicorn[standard]>=0.29
websockets>=12
```

### Dépendances frontend

```bash
cd web/frontend
npm install
```

---

## Lancement

### Mode développement (backend + frontend, hot-reload)

```bash
./web/start.sh
```

Le script :
- démarre le backend FastAPI sur le port `8000` (surchargeable par `NEMESIS_WEB_PORT`) ;
- installe les dépendances npm au premier lancement ;
- démarre Vite sur le port `5173` (surchargeable par `NEMESIS_FRONTEND_PORT`), avec proxy `/api` et `/ws` vers le backend.

Accès : `http://localhost:5173`.

### Lancement manuel

```bash
# Terminal 1 — backend
python -m web.backend

# Terminal 2 — frontend
cd web/frontend && npm run dev
```

---

## Déploiement « tout-en-un »

Le backend FastAPI sert automatiquement la SPA compilée si `web/frontend/dist` existe :

```bash
cd web/frontend
npm run build          # produit web/frontend/dist
cd ../..
python -m web.backend  # sert l'API + l'UI sur http://localhost:8000
```

Le routage SPA est géré : toute route inconnue (hors `/api` et `/ws`) renvoie `index.html`.

---

## Fonctionnalités

- **Chat en streaming** avec timings : réflexion, durée de chaque outil, durée du tour.
- **Mode d'autorisation** par session :
  - `Confirmation` : les outils à risque demandent votre accord (`y` / `n` / `a`).
  - `Toutes autorisations` : exécution sans confirmation (agent autonome).
- **Terminal PTY réel** intégré (xterm.js).
- **Explorateur de fichiers** du workspace (lecture seule dans l'UI, écriture via l'agent).
- **Sessions** persistantes, reprises après rechargement, renommage automatique.
- **Réglages complets** répartis en onglets.
- **Bascule de thème** clair/sombre persistée.

---

## Architecture

```text
web/
├── backend/
│   ├── app.py               # FastAPI : routes REST + WebSockets
│   ├── agent_runner.py      # Boucle agent (thread worker → events asyncio)
│   ├── config_store.py      # Pont vers la config CLI partagée (~/.config/nemesis-cli)
│   ├── settings.py          # MCP, skills, mémoire
│   ├── github_settings.py   # Wrapper GitHub (web)
│   ├── telegram_settings.py # Overlay config bot Telegram
│   ├── sessions.py          # Persistance des sessions (JSON)
│   └── terminal.py          # Session PTY
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/       # UI (Sidebar, ChatHeader, MessageList, SettingsDialog, ...)
│   │   ├── store/useAppStore.ts  # État global (Zustand) + WebSocket
│   │   ├── lib/api.ts        # Client REST typé
│   │   └── index.css         # Thème (tokens HSL clair/sombre)
│   └── vite.config.ts        # Proxy /api et /ws vers le backend
├── requirements.txt
└── start.sh
```

Le `AgentRunner` exécute le travail bloquant (HTTP, sous-processus) dans un thread et remonte les événements sur la boucle asyncio via une file thread-safe.

---

## API REST

Base : `/api`. Toutes les réponses sont en JSON.

### Santé et configuration

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET  | `/api/health` | État du service |
| GET  | `/api/config` | Configuration publique (sans secrets) |
| PATCH| `/api/config` | Mise à jour (provider / nemapi / security) |
| GET  | `/api/models` | Modèles disponibles + état de connexion |
| POST | `/api/models` | Choix du modèle |
| GET  | `/api/tools` | Catalogue des outils (nom, description, risque) |

### MCP et skills

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET    | `/api/mcp` | Liste des serveurs MCP |
| POST   | `/api/mcp` | Ajoute un serveur MCP |
| DELETE | `/api/mcp/{name}` | Supprime un serveur MCP |
| GET    | `/api/skills` | Liste les skills installées |

### Mémoire

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET    | `/api/memory` | État (activée + blocs) |
| POST   | `/api/memory` | Ajoute / met à jour un bloc |
| DELETE | `/api/memory/{id}` | Supprime un bloc |
| POST   | `/api/memory/clear` | Vide la mémoire |
| POST   | `/api/memory/toggle` | Active/désactive la mémoire |

### GitHub et Telegram

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET  | `/api/github` | État de connexion GitHub (token masqué) |
| POST | `/api/github` | Enregistre token + identité ; synchronise le MCP GitHub |
| GET  | `/api/telegram` | Configuration du bot Telegram |
| POST | `/api/telegram` | Enregistre token/nom du bot |

### Fichiers et sessions

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET    | `/api/files?path=` | Liste un dossier du workspace |
| GET    | `/api/files/content?path=` | Lit un fichier (tronqué si volumineux) |
| GET    | `/api/sessions` | Liste des sessions |
| POST   | `/api/sessions` | Crée une session |
| GET    | `/api/sessions/{id}` | Détail d'une session |
| PATCH  | `/api/sessions/{id}` | Renomme une session |
| POST   | `/api/sessions/{id}/messages` | Ajoute un message |
| DELETE | `/api/sessions/{id}` | Archive une session |

Exemple :

```bash
curl http://localhost:8000/api/github
curl -X POST http://localhost:8000/api/github \
  -H "Content-Type: application/json" \
  -d '{"token":"ghp_xxx","name":"Octocat","email":"octo@example.com"}'
```

---

## WebSockets

### `/ws/chat`

Paramètres de requête : `mode` (`all` | `confirm`), `session`, `model`.

Messages **client → serveur** :

```json
{ "type": "user_message", "content": "Liste les fichiers du workspace" }
{ "type": "authorize", "id": "<req>", "choice": "y" }
{ "type": "interrupt" }
{ "type": "set_model", "model": "qwen-chat" }
{ "type": "set_mode", "mode": "all" }
{ "type": "ping" }
```

Messages **serveur → client** (extraits) :

```json
{ "type": "ready", "provider": { "model": "...", "approval_mode": "confirm" } }
{ "type": "status", "state": "thinking" }
{ "type": "assistant_text", "text": "..." }
{ "type": "tool_start", "name": "bash", "params": {}, "risk": "medium" }
{ "type": "tool_result", "name": "bash", "success": true, "output": "...", "duration": 0.4 }
{ "type": "authorize_request", "id": "...", "name": "...", "params": {}, "risk": "high" }
{ "type": "turn_end", "elapsed": 2.1, "tool_count": 1 }
{ "type": "mode_changed", "mode": "confirm" }
```

### `/ws/terminal`

Terminal PTY (shell du backend). Paramètres : `cols`, `rows`.

Messages client → serveur : `{ "type": "input", "data": "..." }`, `{ "type": "resize", "cols": 120, "rows": 30 }`.
Messages serveur → client : `{ "type": "output", "data": "..." }`, `{ "type": "exit" }`.

---

## Réglages

La boîte de dialogue **Réglages** comporte 8 onglets :

1. **Connexion** — serveur NEMAPI local (hôte/port) ou URL publique, sélection du modèle, test de connexion.
2. **Général** — workspace, auto-approbation des lectures, confirmation des actions à risque, affichage des sorties.
3. **Mémoire** — activation, ajout/édition/suppression de blocs (voir [MEMORY.md](MEMORY.md)).
4. **MCP** — ajout/suppression de serveurs MCP (commande + args + env).
5. **Skills** — skills installées dans `tools_library/`.
6. **Outils** — catalogue complet avec niveau de risque.
7. **GitHub** — connexion (voir [GITHUB.md](GITHUB.md)).
8. **Telegram** — token du bot (partagé avec `telegram_bot.py`).

---

## Thème et responsive

- **Thème** : palette blanc-bleu en clair (par défaut) et bleu nuit en sombre. Les couleurs sont des **tokens sémantiques HSL** définis dans `src/index.css` (`:root` et `.dark`).
- **Persistance** : le choix de thème est stocké dans `localStorage` (`nemesis-theme`) et appliqué avant le premier rendu.
- **Responsive** :
  - `< 768px` : barre latérale en tiroir (bouton menu dans l'en-tête), panneau latéral masqué.
  - `≥ 768px` : barre latérale fixe.
  - `≥ 1024px` : panneau latéral (fichiers/terminal) disponible.
  - Grilles et dialogues s'adaptent de 375 px à 1440 px+.

---

## Variables d'environnement

| Variable | Défaut | Rôle |
| -------- | ------ | ---- |
| `NEMESIS_WEB_HOST` | `0.0.0.0` | Hôte d'écoute du backend |
| `NEMESIS_WEB_PORT` | `8000` | Port du backend |
| `NEMESIS_FRONTEND_PORT` | `5173` | Port du dev server Vite |
| `NEMESIS_BACKEND` | `http://localhost:8000` | Cible du proxy Vite |
| `NEMESIS_CONFIG_DIR` | `~/.config/nemesis-cli` | Répertoire de configuration partagé |

---

## Dépannage

| Problème | Solution |
| -------- | -------- |
| `ModuleNotFoundError: fastapi` | `pip install -r web/requirements.txt` |
| Le frontend n'atteint pas l'API | Vérifier le proxy Vite et que le backend écoute sur `8000` |
| Page blanche après build | Refaire `npm run build` puis relancer `python -m web.backend` |
| Terminal non fonctionnel | Vérifier que le shell (`$SHELL`, sinon `/bin/bash`) existe côté backend |
| Icônes/logo manquants | Vérifier la présence de `web/frontend/public/logo.jpg` (recopié dans `dist/`) |