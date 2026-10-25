# NEMESIS-CLI

Agent IA autonome pour Linux : lit, écrit, exécute et automatise, sous votre contrôle.
Trois surfaces partagent le même moteur et la même mémoire : **CLI**, **Web**, **Telegram**.

Développé par **Ekoue TETE** (TEJF).

---

## Installation

```bash
git clone https://github.com/teteekoue/NEMESIS-AGENT.git
cd NEMESIS-AGENT
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
./venv/bin/python3 cli.py

# Aperçu local de l'interface (aucun appel réseau ou outil exécuté)
./venv/bin/python3 cli.py --demo

# Alias historique (même interface et même moteur)
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
cd web/frontend && npm ci && npm run build
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
  url: http://127.0.0.1:8090/v1
  token: "" # facultatif ; NEMAPI n'en a généralement pas besoin
```

Tout est aussi configurable depuis l'interface Web (onglets Connexion, Général, Mémoire, MCP, Skills, Outils, GitHub, Telegram).

### Chemins et autonomie

NEMESIS n'est plus confiné à un « workspace » :

- **Aucun chemin n'est refusé** : `/etc`, `~/projets`, `/tmp`, disques montés… tout est accessible
  par les outils `read_file`, `write_file`, `edit`, `apply_patch`, `delete_file`, `bash`, etc.
- Les chemins **relatifs** partent du **répertoire courant du shell**, et ce répertoire est
  **persistant** : un `cd` effectué dans un appel `bash` reste actif pour les appels suivants.
  Les paramètres `workdir` / `cwd` permettent de cibler explicitement un autre dossier.
- **Aucune limite d'itérations** : l'agent travaille jusqu'à ce que le modèle n'appelle plus
  d'outil, jusqu'à une interruption utilisateur ou une erreur du provider. Les sous-agents
  acceptent aussi `max_iterations: 0` (= illimité).
- Chaque résultat d'outil renvoie **stdout, stderr, code de sortie** (et le signal en cas de
  timeout/interruption), même quand la sortie est vide.
- `bash` accepte les commandes **multi-lignes, heredocs et guillemets** sans échappement
  supplémentaire ; les timeouts vont de 10 s à 7 jours (défaut 600 s).
- Le répertoire courant est partagé par les conversations d'un même processus (CLI, backend Web,
  bot Telegram) : chaque `cd` influence les appels suivants de ce processus.

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
| `/status` | Connexion, modèle, endpoint, répertoire de départ |
| `/models` | Lister / choisir un modèle NEMAPI |
| `/tools` | Lister les outils |
| `/config` | Configurer l'URL complète NEMAPI se terminant par `/v1` et son jeton facultatif |
| `/todo` | Plan de travail courant |
| `/agents` | Sous-agents |
| `/history` `/stats` `/clear` `/exit` | Historique, stats, écran, quitter |
| `/sessions` | Liste, crée, reprend ou supprime les conversations partagées avec le Web |
| `/memory` | Consulte et gère la mémoire partagée avec le Web et Telegram |
| `/approval` | Choisit le mode de confirmation des outils (`confirm` ou `all`) |
| `/skills` | Consulte, active ou désactive les compétences |
| `/subagents` | Consulte et configure les profils et limites des sous-agents |
| `/files` | Explore n’importe quel dossier du système |
| `/integrations` | Consulte ou configure GitHub et Telegram avec saisie masquée des jetons |
| `/show` | Affiche les sorties complètes des commandes et outils |

Les appels d’outils sont présentés en cartes avec leurs paramètres, leur résultat et leur durée.
Les conversations CLI utilisent le même stockage durable que l’interface Web : `/sessions new`
crée un fil, `/sessions open <id>` reprend son transcript et `/sessions delete <id>` l’archive.

La compétence intégrée `cybersecurity-audit` fournit une méthode d’audit défensif et de tests
réseau autorisés. Elle est désactivée par défaut : dans `/skills`, choisissez l’option 6 puis
`cybersecurity-audit` pour l’activer pour les nouvelles conversations. Les audits actifs exigent
un périmètre et une autorisation explicites; les tests destructifs, furtifs ou d’exploitation
ne sont pas couverts.

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

## Outils

Registre dynamique partagé par CLI, Web, Telegram et sous-agents.

- **Fichiers** : `read_file`, `write_file`, `edit`, `apply_patch`, `list_dir`, `delete_file`
  (chemins absolus ou relatifs au répertoire courant, sans confinement)
- **Shell / processus** : `bash` (synchrone/arrière-plan, `cd` persistant, heredocs), `kill_task`, `cleanup_logs`
- **Recherche** : `glob`, `grep`, `web_search`, `web_fetch`
- **Mémoire** : `memory_add`, `memory_update`, `memory_search`, `memory_get`, `memory_delete`, `memory_clear`
- **Planification / sous-agents** : `todo`, `delegate_tasks`, `subagent_status`, `get_task_output`, `skills_list`
- **MCP** : `mcp_list`, `mcp_tools_list`, `mcp_call`
- **Git / divers** : `git`

Liste live : `/tools` (CLI) ou onglet **Outils** (Web).

Les sous-agents sont des instances temporaires exécutées en parallèle via une API OpenAI-compatible (Groq par défaut). Configurez leur provider, clé API, modèle et limite de concurrence dans **Réglages → Sous-agents**. Voir [docs/SUBAGENTS.md](docs/SUBAGENTS.md).

---

## Mémoire, GitHub, Telegram

- **Mémoire** — blocs durables réinjectés dans le prompt système, partagés entre les trois surfaces. Détails : [docs/MEMORY.md](docs/MEMORY.md).
- **GitHub** — collez un PAT dans l'onglet GitHub : NEMESIS crée un serveur MCP (`npx -y @modelcontextprotocol/server-github`) et expose les outils GitHub sans alourdir le registre principal. Détails : [docs/GITHUB.md](docs/GITHUB.md).
- **Telegram** — token configurable depuis le Web ; même moteur, même mémoire, toutes autorisations. Commandes : `/start`, `/help`, `/tools`, `/new`, `/clear`. Raccourcis : `web_search:requête`, `web_fetch:url`, `bash:commande`, `read:fichier`.

---

## Interface Web

- Chat en streaming (WebSocket `/ws/chat`), timings par outil et par tour
- Terminal PTY réel (`/ws/terminal`), explorateur de fichiers de tout le système
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
- Issues : [github.com/teteekoue/NEMESIS-AGENT/issues](https://github.com/teteekoue/NEMESIS-AGENT/issues)