# NEMESIS-CLI

Agent IA autonome pour Linux : lit, écrit, exécute et automatise, sous votre contrôle.
Trois surfaces partagent le même moteur et la même mémoire : **CLI**, **Web**, **Telegram**.

Développé par **Ekoue TETE** (TEJF).

---

## Installation

```bash
git clone https://github.com/teteekoue/NEMESIS-CLI.git
cd NEMESIS-CLI
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Installation automatique (venv + dépendances + dépendances Web optionnelles) :

```bash
chmod +x install.sh && ./install.sh
```

Termux / Android (ARMv7) : voir [TERMUX_INSTALL.md](TERMUX_INSTALL.md).

---

## Lancer

```bash
# CLI
./venv/bin/python3 agent.py

# CLI (debug)
./venv/bin/python3 agent.py --debug

# Interface Web (backend + frontend)
./web/start.sh
```

Ports Web : backend `8000` (`NEMESIS_WEB_PORT`), frontend `5173` (`NEMESIS_FRONTEND_PORT`).
Le backend FastAPI sert aussi le build React s'il existe (`web/frontend/dist`).

```bash
# Web « tout-en-un » (un seul port)
cd web/frontend && npm install && npm run build
cd ../.. && ./venv/bin/python3 -m web.backend   # API + UI sur http://localhost:8000
```

Bot Telegram :

```bash
./run_telegram_bot.sh
```

---

## Configuration

Fichier principal `config.yaml` (ou `~/.config/nemesis-cli/config.yaml`) :

```yaml
provider:
  type: nemapi
  model: qwen-chat

nemapi:
  host: 127.0.0.1
  port: 8090
```

Tout est aussi configurable depuis l'interface Web (onglets Connexion, Général, Mémoire, MCP, Skills, Outils, GitHub, Telegram).

- Mémoire persistante : `~/.config/nemesis-cli/memory/` — partagée CLI/Web/Telegram
- GitHub : `~/.config/nemesis-cli/github.yaml` — génère un serveur **MCP** officiel
- Telegram : `~/.config/nemesis-cli/telegram_bot.yaml`

---

## Autorisation

Avant chaque outil sensible :

| Touche | Effet |
| --- | --- |
| `y` | Autoriser une fois |
| `n` | Refuser |
| `a` | Autoriser pour toute la session |

Mode « toutes autorisations » : agent autonome (défaut du bot Telegram).

---

## Commandes slash

| Commande | Description |
| --- | --- |
| `/help` | Aide |
| `/status` | Connexion, modèle, endpoint, workspace |
| `/models` | Lister / choisir un modèle NEMAPI |
| `/tools` | Lister les outils |
| `/config` | Configurer hôte/port NEMAPI |
| `/todo` | Plan de travail courant |
| `/agents` `/skills` | Sous-agents / compétences |
| `/history` `/stats` `/clear` `/exit` | Historique, stats, écran, quitter |

---

## Exemples

```text
Lis les fichiers Python dans /home/user/projet
Ajoute une fonction de tri dans utils.py
Lance les tests et corrige les erreurs
Recherche les bonnes pratiques Python asyncio
```

Exemple de déclenchement d'outil (le modèle émet du JSON, l'utilisateur valide) :

```json
{ "tool": "bash", "parameters": { "command": "ls -la" } }
```

---

## Outils (42)

Registre dynamique partagé par CLI, Web, Telegram et sous-agents.

- **Fichiers** : `read_file`, `write_file`, `edit`, `apply_patch`, `list_dir`, `delete_file`
- **Shell / processus** : `bash` (synchrone/arrière-plan), `kill_task`, `cleanup_logs`
- **Recherche** : `glob`, `grep`, `web_search`, `web_fetch`
- **Mémoire** : `memory_add`, `memory_update`, `memory_search`, `memory_get`, `memory_delete`, `memory_clear`
- **Planification / sous-agents** : `todo`, `list_agents`, `delegate_task`, `check_reports`, `agent_status`, `get_task_output`, `skills_list`
- **MCP** : `mcp_list`, `mcp_tools_list`, `mcp_call`
- **Git / divers** : `git`

Liste live : `/tools` (CLI) ou onglet **Outils** (Web).

---

## Mémoire, GitHub, Telegram

- **Mémoire** — blocs durables réinjectés dans le prompt système, partagés entre les trois surfaces. Détails : [docs/MEMORY.md](docs/MEMORY.md).
- **GitHub** — collez un PAT dans l'onglet GitHub : NEMESIS crée un serveur MCP (`npx -y @modelcontextprotocol/server-github`) et expose les outils GitHub sans alourdir le registre principal. Détails : [docs/GITHUB.md](docs/GITHUB.md).
- **Telegram** — token configurable depuis le Web ; même moteur, même mémoire, toutes autorisations. Commandes : `/start`, `/help`, `/tools`, `/new`, `/clear`. Raccourcis : `web_search:requête`, `web_fetch:url`, `bash:commande`, `read:fichier`.

---

## Interface Web

- Chat en streaming (WebSocket `/ws/chat`), timings par outil et par tour
- Terminal PTY réel (`/ws/terminal`), explorateur de fichiers du workspace
- Sessions persistantes, thème clair/sombre, responsive mobile → desktop
- Réglages : Connexion, Général, Mémoire, MCP, Skills, Outils, GitHub, Telegram

Aperçu fictif sans backend : ouvrir `http://localhost:5173/?demo=1`.
Si le backend est absent, l'UI affiche un bandeau « Backend injoignable » + **Réessayer**.

Détails : [docs/WEB_UI.md](docs/WEB_UI.md).

---

## Dépannage

| Problème | Solution |
| --- | --- |
| `ModuleNotFoundError: uvicorn` / proxy `ECONNREFUSED` en boucle | `./web/start.sh` (installe les deps et attend l'API) |
| Bandeau « Backend injoignable » | Lancer `python -m web.backend` puis **Réessayer** |
| NEMAPI injoignable en local | Onglet Connexion → « Adresse IP et port » (pas d'URL) → **Tester** |
| `ModuleNotFoundError: yaml` / `rich` | `./venv/bin/pip install -r requirements.txt` |
| Provider injoignable | Vérifier `config.yaml` / onglet Connexion |

---

## Documentation

- [docs/WEB_UI.md](docs/WEB_UI.md) — Interface Web, API REST/WS, déploiement
- [docs/MEMORY.md](docs/MEMORY.md) — Mémoire persistante
- [docs/GITHUB.md](docs/GITHUB.md) — Intégration GitHub (MCP)
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — Journal des modifications
- [NEMESIS.md](NEMESIS.md) — Architecture détaillée
- [TOOL_CALL_FORMATS.md](TOOL_CALL_FORMATS.md) — Formats d'appels d'outils

---

## Licence

Open-source — **TEJF / Ekoue TETE**. Voir [LICENSE](LICENSE).

## Contact

- GitHub : [github.com/teteekoue](https://github.com/teteekoue)
- WhatsApp : +228 98 08 44 42
- Issues : [github.com/teteekoue/NEMESIS-CLI/issues](https://github.com/teteekoue/NEMESIS-CLI/issues)