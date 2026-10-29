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
  url: http://127.0.0.1:8090/v1  # ou https://xxxx.trycloudflare.com/v1
  token: ""            # facultatif ; NEMAPI n'en a généralement pas besoin
  browser_relay: false # true si le serveur ne peut pas joindre l'URL
```

Tout est aussi configurable depuis l'interface Web (onglets Connexion, Général, Mémoire, MCP, Skills, Outils, GitHub, Telegram).

### URL publique et relais navigateur

Quand NEMESIS tourne sur une machine dont la sortie réseau est filtrée (sandbox,
CI, réseau d'entreprise), le backend ne peut pas ouvrir de connexion vers un
tunnel `https://xxxx.trycloudflare.com` : le handshake TLS est coupé
(`SSLZeroReturnError`, `Max retries exceeded with url: /v1/models`). Le
navigateur de l'utilisateur, lui, peut atteindre ce tunnel.

L'option **Relais navigateur** (onglet Connexion, ou `nemapi.browser_relay: true`)
route alors les requêtes HTTP NemApi (`/status`, `/v1/models`,
`/v1/chat/completions`) via le WebSocket `/ws/relay` : le navigateur exécute le
`fetch()` et renvoie la réponse au backend. NemApi autorisant les origines
croisées, cela fonctionne sans proxy ni extension supplémentaire. Le relais est
interprété uniquement par le backend Web ; le CLI et Telegram continuent
d'appeler NemApi directement. L'interface Web doit rester ouverte pendant les
échanges. L'état du relais est visible avec `GET /api/relay`.

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

Le CLI conserve ses confirmations interactives pour les outils sensibles :

| Touche | Effet |
| --- | --- |
| `y` | Autoriser une fois |
| `n` | Refuser |
| `a` | Autoriser pour toute la session |

Le bot Telegram, lui, exécute directement tous les outils, y compris ceux classés
sensibles, sans demande ni bouton de confirmation. `/approval` n'y modifie aucune
politique; la livraison de `send_file_to_user` est exécutée via Telegram, puis son
résultat réel (succès ou échec) est retourné à l'agent.

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
| `/approval` | Telegram : indique que tous les outils s'exécutent directement, sans confirmation |
| `/skills` | Consulte, active ou désactive les compétences |
| `/subagents` | Consulte et configure les profils et limites des sous-agents |
| `/files` | Explore n’importe quel dossier du système |
| `/integrations` | Consulte ou configure GitHub et Telegram avec saisie masquée des jetons |
| `/show` | Affiche les sorties complètes des commandes et outils |

Les appels d’outils sont présentés en cartes avec leurs paramètres, leur résultat et leur durée.
Les conversations CLI utilisent le même stockage durable que l’interface Web : `/sessions new`
crée un fil, `/sessions open <id>` reprend son transcript et `/sessions delete <id>` l’archive.

### Skills : versions originales

Les compétences fournies dans `tools_library/` sont les **fichiers originaux**
récupérés sur GitHub, pas des résumés. Le corps de chaque `SKILL.md` est verbatim ;
seul le frontmatter ajoute `nemesis_builtin`, `source`, `source_license` et
`upstream_commit`. Les références et scripts compagnons sont inclus. Voir
[tools_library/ATTRIBUTION.md](tools_library/ATTRIBUTION.md) pour la table des
sources et licences (`obra/superpowers` MIT, `anthropics/skills` Apache-2.0, etc.).

`cybersecurity-audit` est un guide AppSec complet : empreinte de la pile, OWASP
Top 10, classes d’attaques par technologie, preuves, corrections, vérification,
rapport `SECURITY_AUDIT.md` et verdict. Il explique **comment** auditer ; il ne
contient ni cadrage obligatoire, ni liste d’actions interdites, ni consignes de
refus — l’agent reste autonome et les autorisations relèvent de l’utilisateur et
du mécanisme d’approbation des outils.

Les skills sont désactivés par défaut : `/skills` (option 6) ou Réglages → Skills.
Les copies marquées `nemesis_builtin: true` dans
`~/.config/nemesis-cli/tools_library/` sont rafraîchies automatiquement quand la
version intégrée change ; une copie personnalisée n’est jamais écrasée.

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

- **Fichiers** : `read_file`, `write_file`, `append`, `edit`, `apply_patch`, `list_dir`, `delete_file`
  (chemins absolus ou relatifs au répertoire courant, sans confinement ; `write_file` accepte
  `file_path`, `path` ou `filename` ; `append` ajoute un bloc à la fin du fichier et accepte
  les mêmes alias ; `apply_patch` accepte `workdir` et les chemins absolus).
  `read_file` envoie le **texte** pour les fichiers texte et joint les **images** au canal
  multimodal NemApi (voir ci-dessous)
- **Shell / processus** : `bash` (synchrone/arrière-plan, `cd` persistant, heredocs, `workdir`,
  timeout 10 s–7 jours), `kill_task`, `cleanup_logs`
- **Recherche** : `glob`, `grep`, `web_search`, `web_fetch`
- **Mémoire** : `memory_add`, `memory_update`, `memory_search`, `memory_get`, `memory_delete`, `memory_clear`
- **Planification / sous-agents** : `todo`, `delegate_tasks`, `subagent_status`, `get_task_output`, `skills_list`
- **MCP** : `mcp_list`, `mcp_tools_list`, `mcp_call`
- **Git / divers** : `git`

Liste live : `/tools` (CLI) ou onglet **Outils** (Web).

### Protocole d'appel et limite de taille

NemApi tronque les longues réponses JSON (entre ~5,5 et ~6,8 Ko). Pour éviter qu'un appel
tronqué soit exécuté avec un contenu partiel tout en annonçant un succès :

- **un seul objet JSON valide par bloc ```json**, DSML et balises XML interdites ;
- **4 000 caractères de JSON maximum par appel** (plafond dur 5 000 côté runtime),
  pour *tous* les outils ; au-delà, l'appel est refusé sans rien exécuter ;
- un fichier long s'écrit avec `write_file` (premier bloc) puis un `append` par bloc
  suivant, en attendant le `FEEDBACK` entre chaque appel ;
- chaque `FEEDBACK` porte un marqueur `[call #N — …]` qui permet de détecter une réponse
  décalée ; après trois erreurs de protocole consécutives, le tour s'arrête.

Détail des pièges (heredoc, quoting, Android) : [docs/TOOL_LIMITS.md](docs/TOOL_LIMITS.md).

Les sous-agents sont des instances temporaires exécutées en parallèle via une API OpenAI-compatible (Groq par défaut). Configurez leur provider, clé API, modèle et limite de concurrence dans **Réglages → Sous-agents**. Voir [docs/SUBAGENTS.md](docs/SUBAGENTS.md).

---

## Images (multimodal)

NEMESIS n'envoie plus jamais le contenu binaire d'une image dans le prompt (ni
base64 tapé en texte, ni upload vers un hébergeur public). Quand l'agent doit
voir une image, il la transmet à NemApi via le format OpenAI `image_url` :

```json
{
  "model": "deepseek-chat",
  "messages": [{
    "role": "user",
    "content": [
      { "type": "text", "text": "FEEDBACK: ... [IMAGE ATTACHED] /tmp/photo.png" },
      { "type": "image_url", "image_url": { "url": "data:image/png;base64,iVBORw0..." } }
    ]
  }]
}
```

NemApi (v5.1+, étendu à 7 fournisseurs en v5.2) extrait ces parts, les upload
via l'`<input type=file>` du fournisseur web, puis saisit le texte — sans que le
base64 ne soit jamais tapé. Fonctionne avec `deepseek-chat`, `qwen-chat`,
`claude-chat`, `gemini-chat`, `gpt-chat`, `kimi-chat` et `glm-chat` (ChatGLM
reste le cas connu où l'upload peut échouer côté page web).

**Trois façons de fournir une image :**

| Surface | Méthode |
| --- | --- |
| CLI / Telegram | Demander « décris l'image `/chemin/photo.png` » : `read_file` détecte l'image et l'attache au tour suivant |
| Interface Web | Bouton trombone du composer (jusqu'à 8 images, 8 Mo chacune), ou demander la lecture d'un chemin |
| Telegram | Envoyer une photo (avec ou sans légende) au bot |

**Détails techniques :**

- Formats reconnus par magic bytes : PNG, JPEG, GIF, WebP, BMP, TIFF. Les autres
  fichiers restent traités comme du texte.
- Limites NemApi respectées : URL `data:` base64 uniquement, ~15 Mo par fichier
  (20 Mo une fois encodé), 8 images par requête, uniquement sur le dernier
  message `user`.
- Le marqueur `[IMAGE ATTACHED]` (chemin, MIME, taille) remplace les octets dans
  le `FEEDBACK` ; l'image est jointe à la requête suivante.
- L'en-tête `X-NemApi-Agent: nemesis` est envoyé à chaque requête pour que
  NemApi reconnaisse l'agent (option `nemapi.agent` dans `config.yaml`).
- Les **sous-agents** (API OpenAI-compatible) ne reçoivent pas les images : le
  résultat d'outil leur indique explicitement de ne pas prétendre les avoir vues
  et de laisser l'agent principal faire l'analyse visuelle.
- Prérequis côté NemApi : extension navigateur connectée et onglet du
  fournisseur sélectionné. Si l'upload échoue, NemApi répond `500
  automation_error` : NEMESIS affiche l'erreur au lieu de prétendre avoir vu l'image.

---

## Mémoire, GitHub, Telegram

- **Mémoire** — blocs durables réinjectés dans le prompt système, partagés entre les trois surfaces. Détails : [docs/MEMORY.md](docs/MEMORY.md).
- **GitHub** — collez un PAT dans l'onglet GitHub : NEMESIS crée un serveur MCP (`npx -y @modelcontextprotocol/server-github`) et expose les outils GitHub sans alourdir le registre principal. Détails : [docs/GITHUB.md](docs/GITHUB.md).
- **Telegram** — token configurable depuis le Web ; même moteur et même mémoire. Documents et médias joints sont téléchargés dans le workspace (ou dans `/tmp` si celui-ci n'existe pas), puis leur chemin est transmis au modèle; les images restent des pièces jointes multimodales data URL. L'outil Telegram `send_file_to_user` permet à l'agent de renvoyer un livrable dans le chat après sa création et sa vérification. Les planifications peuvent réveiller l'agent à l'échéance ; les réponses finales Markdown sont converties en texte brut structuré. Commandes : `/start`, `/help`, `/tools`, `/new`, `/clear`. Raccourcis : `web_search:requête`, `web_fetch:url`, `bash:commande`, `read:fichier`.
- **Planification** — `schedule_task`, `list_scheduled_tasks`, `cancel_scheduled_task` et `wait` sont disponibles dans les trois interfaces. Les tâches ponctuelles sont stockées dans SQLite et reprises au prochain démarrage ; voir [docs/SCHEDULING.md](docs/SCHEDULING.md).

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
| `SSLZeroReturnError` / `Max retries exceeded … /v1/models` sur une URL cloudflared | Activer **Relais navigateur** dans l'onglet Connexion puis **Tester** ; garder la page ouverte |
| « Relais navigateur inactif » | Ouvrir l'interface Web (le WebSocket `/ws/relay` doit être connecté) et vérifier `/api/relay` |
| `Failed to fetch` dans le relais | Vérifier que l'URL NemApi est joignable **depuis le navigateur** et que CORS est autorisé |
| `ModuleNotFoundError: yaml` / `rich` | `./venv/bin/pip install -r requirements.txt` |
| Provider injoignable | Vérifier `config.yaml` / onglet Connexion |
| Le prompt système est renvoyé après reprise d'une conversation | Corrigé : le frontend transmet `session_id` avec chaque `user_message`, le backend rattache la session (`bind_session`) et `is_initialized()` reconnaît aussi les anciens transcripts contenant une réponse assistant. Le prompt n'est envoyé qu'au tout premier message. |
| Un skill reste en version réécrite après mise à jour | Vérifier que le frontmatter de la copie dans `~/.config/nemesis-cli/tools_library/` porte toujours `nemesis_builtin: true` : c'est le marqueur qui autorise le rafraîchissement automatique. |
| `git pull` refuse de s'appliquer (« Your local changes … would be overwritten ») | Un fichier suivi a été modifié localement (souvent `.gitignore`) : `git stash push -m local -- .gitignore` puis `git pull --ff-only origin main`, ou `git checkout -- .gitignore` si la modification n'est pas voulue. |
| Après un `git pull`, l'agent se comporte comme avant | `python cli.py --check` : affiche OK/ÉCHEC des correctifs **et** le chemin des modules réellement chargés. Si les chemins pointent vers un paquet installé (`site-packages`), `git pull` ne met pas à jour cette copie : `./venv/bin/pip install --force-reinstall .` (ou lancer le CLI depuis le dépôt). Si un serveur Web / bot Telegram tourne, il doit être **redémarré** : Python charge le code au démarrage. |

---

## Documentation

- [docs/WEB_UI.md](docs/WEB_UI.md) — Interface Web, API REST/WS, déploiement
- [docs/MEMORY.md](docs/MEMORY.md) — Mémoire persistante
- [docs/GITHUB.md](docs/GITHUB.md) — Intégration GitHub (MCP)
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — Journal des modifications
- [NEMESIS.md](NEMESIS.md) — Architecture détaillée
- [TOOL_CALL_FORMATS.md](TOOL_CALL_FORMATS.md) — Formats d'appels d'outils
- [docs/TOOL_LIMITS.md](docs/TOOL_LIMITS.md) — Limites du runtime, taille des appels, DSML, heredoc
- [docs/SCHEDULING.md](docs/SCHEDULING.md) — Planification persistante, réveil autonome et outil wait

---

## Licence

Open-source — **TEJF / Ekoue TETE**. Voir [LICENSE](LICENSE).

## Contact

- GitHub : [github.com/teteekoue](https://github.com/teteekoue)
- WhatsApp : +228 98 08 44 42
- Issues : [github.com/teteekoue/NEMESIS-AGENT/issues](https://github.com/teteekoue/NEMESIS-AGENT/issues)