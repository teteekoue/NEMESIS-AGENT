# NEMESIS-CLI

<p align="center">
  <strong>Agent IA autonome pour l'ingénierie logicielle, la gestion système et l'automatisation sous Linux.</strong>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue.svg" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/Version-2.1.0-green.svg" alt="Version 2.1.0">
  <img src="https://img.shields.io/badge/Platform-Linux%20%7C%20POSIX%20%7C%20Termux-orange.svg" alt="Platform">
</p>

---

## 📌 Présentation

**NEMESIS-CLI** est un assistant et agent IA autonome conçu pour automatiser les tâches d'ingénierie, d'administration système et d'analyse sous Linux. Il propose un moteur d'exécution unifié et une mémoire partagée à travers **trois interfaces distinctes** :

1. **CLI (Ligne de commande)** — Interface riche et interactive développée avec `rich` et `prompt_toolkit`.
2. **Interface Web (UI)** — Application Web réactive intégrant un chat en streaming, un terminal PTY complet et un explorateur de fichiers (FastAPI + React/Vite).
3. **Bot Telegram** — Contrôle à distance autonome partageant la même mémoire et le même registre d'outils.

Développé par **Ekoue TETE (TEJF)** et distribué sous **licence MIT**.

---

## ✨ Fonctionnalités clés

- 🛠️ **Registre d'outils dynamique** : Plus de 40 outils intégrés pour la manipulation de fichiers (`read`, `write`, `edit`, `apply_patch`), l'exécution de commandes shell (`bash`), la recherche (`glob`, `grep`, `web_search`, `web_fetch`), Git et les requêtes système.
- 🧠 **Mémoire persistante globale** : Conservation des préférences et contextes clés entre les sessions et partagée instantanément entre CLI, Web et Telegram.
- 🔌 **Protocole MCP & Intégration GitHub** : Support du *Model Context Protocol* (MCP) et intégration GitHub via le serveur officiel (`@modelcontextprotocol/server-github`).
- 🤖 **Flotte de sous-agents éphémères** : Délégation de tâches complexes à des sous-agents exécutés en parallèle via toute API OpenAI-compatible.
- 🔐 **Sécurité & Contrôle granulaire** : Validation interactive des actions sensibles (`y` pour autoriser une fois, `n` pour refuser, `a` pour autoriser toute la session) ou mode autonome complet.
- 🛡️ **Compétences spécialisées (Skills)** : Intégration de compétences à la demande, telles que l'audit de cybersécurité défensive (`cybersecurity-audit`), le débogage systématique ou le workflow Git.

---

## 🚀 Installation

### Prérequis

- **Python** 3.10 ou supérieur
- **Node.js** 18+ & **npm** (pour l'interface Web et l'intégration GitHub MCP)
- **Git** et **curl**

### Installation rapide

```bash
# Cloner le dépôt
git clone https://github.com/teteekoue/NEMESIS-AGENT.git
cd NEMESIS-AGENT

# Rendre le script d'installation exécutable et lancer
chmod +x install.sh
./install.sh
```

### Installation manuelle

```bash
# Création et activation de l'environnement virtuel
python3 -m venv venv
source ./venv/bin/activate

# Installation des dépendances Python
pip install -r requirements.txt
```

> **Android / Termux** : Consultez le guide dédié [TERMUX_INSTALL.md](TERMUX_INSTALL.md).

---

## 💻 Utilisation

### 1. Interface en ligne de commande (CLI)

```bash
# Lancer la CLI NEMESIS
./venv/bin/python3 cli.py

# Mode aperçu/démonstration local (sans appel réseau ni exécution d'outils)
./venv/bin/python3 cli.py --demo

# Mode débogage
./venv/bin/python3 agent.py --debug
```

### 2. Interface Web (UI)

Pour démarrer le backend FastAPI et le frontend React :

```bash
./web/start.sh
```

Par défaut, l'interface Web est accessible aux adresses suivantes :
- **Backend API** : `http://localhost:8000`
- **Frontend Vite** : `http://localhost:5173`

Pour un déploiement « tout-en-un » servi par FastAPI sur un port unique :

```bash
cd web/frontend && npm ci && npm run build
cd ../.. && ./venv/bin/python3 -m web.backend
```

### 3. Bot Telegram

```bash
./run_telegram_bot.sh
```

---

## ⚙️ Configuration

Le fichier de configuration principal est situé dans `config.yaml` (ou dans `~/.config/nemesis-cli/config.yaml`) :

```yaml
provider:
  type: nemapi
  model: qwen-chat

nemapi:
  url: http://127.0.0.1:8090/v1
  token: "" # Optionnel selon votre déploiement NEMAPI
```

### Configuration via l'interface Web

L'ensemble des paramètres (Connexion provider, Mémoire persistante, Serveurs MCP, GitHub PAT, Bot Telegram, Sous-agents et Compétences) est entièrement configurable depuis les onglets de réglages de l'interface Web.

### Fichiers de données et persistance

- **Configuration globale** : `~/.config/nemesis-cli/config.yaml`
- **Mémoire persistante** : `~/.config/nemesis-cli/memory/`
- **Intégration GitHub** : `~/.config/nemesis-cli/github.yaml`
- **Configuration Telegram** : `~/.config/nemesis-cli/telegram_bot.yaml`
- **Configuration MCP** : `~/.config/nemesis-cli/mcp_config.yaml`

---

## 🕹️ Commandes Slash (CLI)

| Commande | Description |
| :--- | :--- |
| `/help` | Affiche l'aide interactive et la liste des commandes. |
| `/status` | Affiche l'état de la connexion, du modèle, de l'endpoint et du workspace. |
| `/models` | Liste et permet de basculer entre les modèles disponibles. |
| `/tools` | Liste les outils enregistrés et leurs paramètres. |
| `/todo` | Affiche et gère le plan de travail (to-do list) courant. |
| `/sessions` | Liste, crée, bascule ou supprime les conversations partagées avec le Web. |
| `/memory` | Consulte et gère la mémoire persistante globale. |
| `/approval` | Configure le mode d'autorisation des outils (`confirm` ou `all`). |
| `/skills` | Active, désactive ou consulte les compétences (ex: `cybersecurity-audit`). |
| `/subagents` | Affiche et configure les sous-agents et leurs limites d'exécution. |
| `/integrations` | Configure en toute sécurité les jetons GitHub et Telegram. |
| `/files` | Explore les fichiers de l'espace de travail. |
| `/config` | Configure l'URL et les identifiants de l'API provider. |
| `/clear` / `/exit` | Efface l'écran ou quitte l'application. |

---

## 📂 Architecture du Projet

```text
NEMESIS-AGENT/
├── agent.py / cli.py       # Points d'entrée de l'interface CLI
├── action_parser.py        # Analyseur multi-niveaux des réponses et tool calls IA
├── tools.py / tools_schema.py # Définition et exécution du registre d'outils
├── telegram_bot.py         # Bot Telegram
├── src/
│   ├── core/               # Moteur principal, mémoire, sous-agents, MCP, handlers d'outils
│   └── ui/                 # Composants d'affichage CLI Rich
├── providers/              # Adapteurs pour API LLM (NEMAPI, OpenAI-compatible)
├── web/
│   ├── backend/            # API REST FastAPI & WebSockets (chat streaming, PTY terminal)
│   └── frontend/           # Application SPA React + Vite + TailwindCSS + shadcn/ui
├── docs/                   # Documentation détaillée (Web UI, Mémoire, GitHub, Subagents, Changelog)
└── tools_library/          # Modules de compétences spécialisées (Skills)
```

---

## 📚 Documentation complémentaire

- [docs/WEB_UI.md](docs/WEB_UI.md) — Documentation complète de l'interface Web et déploiement.
- [docs/MEMORY.md](docs/MEMORY.md) — Fonctionnement du système de mémoire persistante.
- [docs/GITHUB.md](docs/GITHUB.md) — Configuration et utilisation de l'intégration GitHub via MCP.
- [docs/SUBAGENTS.md](docs/SUBAGENTS.md) — Guide d'utilisation et de configuration de la flotte de sous-agents.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — Historique des versions et notes de publication.
- [NEMESIS.md](NEMESIS.md) — Architecture technique détaillée.
- [TOOL_CALL_FORMATS.md](TOOL_CALL_FORMATS.md) — Spécifications des formats d'appel d'outils.

---

## 📄 Licence

Ce projet est distribué sous la licence **MIT**. Voir le fichier [LICENSE](LICENSE) pour plus de détails.

```text
Copyright (c) 2024-2026 TEJF - L'Aigle de la Justice / Ekoue TETE
```

---

## 📬 Contact & Support

- **Auteur** : Ekoue TETE (TEJF)
- **GitHub** : [@teteekoue](https://github.com/teteekoue)
- **Signalement de bugs** : [GitHub Issues](https://github.com/teteekoue/NEMESIS-AGENT/issues)
- **WhatsApp** : +228 98 08 44 42
