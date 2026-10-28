# Journal des modifications — NEMESIS

Ce document regroupe l'historique des évolutions, avec un focus sur les récentes sessions de développement.

---

## [Non publié] — Garde-fous d'appels d'outils (28 octobre 2026)

Retour d'une session NemApi sur Android : `write_file` annonçait un succès alors
que le fichier était absent ou tronqué au-delà de ~5,5–6,8 Ko, le `FEEDBACK`
correspondait parfois à l'appel précédent, et le modèle émettait des blocs
`<|DSML|>` ou des heredocs cassés. Tous les points du rapport sont traités.

### Nouvel outil `append` (Web + CLI + Telegram)

- `append` est enregistré dans le **registre unifié** (`src/core/agent_tools.py`) et
  apparaît donc dans le catalogue du prompt, `/tools`, l'onglet Outils et les
  schémas OpenAI. Il accepte `file_path` / `path` / `filename`, crée le fichier
  et ses dossiers parents, et rapporte `bytes_written` / `total_bytes`.
- Alias `append_file` ajouté au parseur, au schéma legacy et au pont ; l'ancien
  chemin `__APPEND__:` reste compatible. Le bot Telegram ne redirige plus
  `append` vers un outil inexistant.
- Motif imposé pour un long fichier : `write_file` (premier bloc) puis un
  `append` par bloc, un par message, avec vérification finale.

### Taille, JSON strict et vérification

- `src/core/system_prompt.py` enseigne une limite de **4 000 caractères de JSON
  par appel** (tous outils) et interdit DSML, `<tool_call>`, heredocs, les
  contournements `printf`/base64 et les enchaînements sans `FEEDBACK`.
- `ToolBridge` applique un **plafond dur de 5 000 caractères** : l'appel est
  refusé sans rien exécuter, avec un message demandant de le découper.
- `ActionParser(strict=True)` est activé dans les trois boucles. Il rejette le
  DSML/XML, un JSON dont une chaîne/un objet/une clôture de fence manque, et
  les arguments non décodés. Plus de réparation silencieuse d'un appel tronqué.
- `write_file` et `append` vérifient la taille réelle après écriture ; un écart
  devient un échec explicite. Les erreurs de protocole sont renvoyées au modèle
  (trois consécutives au maximum avant l'arrêt du tour).

### Feedback, paramètres, documentation

- `build_feedback_message()` ajoute `[call #N — M tool(s)]` à chaque `FEEDBACK`
  dans CLI, Web et Telegram ; `protocol_error_feedback()` utilise le même
  marqueur. Le prompt explique quoi faire si le marqueur se répète.
- `validate_call()` normalise `file`, `target_file`, `target`, `name` et `files`,
  ce qui élimine les faux « Parametres requis pour 'read_file' ».
- Règles shell/Android (quoting, `realpath`, `screencap`, `KEYCODE_CTRL_A`)
  ajoutées au prompt et détaillées dans **`docs/TOOL_LIMITS.md`**.
- README, AGENTS.md, NEMESIS.md, docs/WEB_UI.md, TOOL_CALL_FORMATS.md,
  prompts de secours et message `/start` Telegram mis à jour. Icône/libellé
  `append` dans l'UI Web.

### Vérification

- 287 tests (`tests/test_tool_call_guards.py`, `tests/test_tool_loop_e2e.py`),
  dont des scénarios complets sur les vraies boucles CLI/Web/Telegram avec un
  provider stub local : écriture par blocs, appel tronqué refusé, DSML refusé,
  corrélation du feedback, `tool_calls` natifs préservés.
- Scénario **live** sur le serveur Web (`/ws/chat` + stub HTTP local sur
  `127.0.0.1:8099`) : 14 vérifications OK — `/api/tools` expose `append`,
  écriture en 3 blocs, prompt unique avec garde-fous, marqueurs `[call #N]`,
  appel tronqué et DSML refusés puis reprise.
- `nemesis --check` passe à **12 vérifications** (append, plafond de taille,
  protocole strict), `compileall`, `tsc --noEmit` et `vite build` OK.
  Aucune requête vers l'URL Cloudflared de l'utilisateur.

---

## [Non publié] — Skills originaux et reprise de session sans prompt (26 octobre 2026)

### Skills : retour aux sources

Les sept `tools_library/*/SKILL.md` livrés étaient des réécritures de 19 à 24
lignes, incomplètes, avec une URL `source:` qui ne correspondait pas au contenu.
Ils sont remplacés par les **originaux GitHub**, corps verbatim et fichiers
compagnons inclus :

- `brainstorming`, `systematic-debugging`, `verification-before-completion` et
  `git-workflow` (alias de `using-git-worktrees`) depuis `obra/superpowers` (MIT) ;
- `frontend-design` depuis `anthropics/skills` (Apache-2.0, `LICENSE.txt` copié) ;
- `cybersecurity-audit` depuis le skill public `cybersecurity-audit-expert`
  (201 lignes : empreinte de pile, OWASP Top 10, preuves, corrections,
  `SECURITY_AUDIT.md`, verdict). L'ancien texte français imposait un cadrage
  obligatoire, des niveaux d'activité, des confirmations avant chaque test et
  des consignes de refus : il bridait un agent autonome. La version restaurée
  décrit une méthode, pas des interdictions ; l'approbation des outils reste le
  seul mécanisme d'autorisation.
- `unit-converter` conservé (original NEMESIS inchangé depuis le premier commit).

Seul le frontmatter est adapté (`name` NEMESIS stable, `nemesis_builtin`,
`source`, `source_license`, `upstream_commit`, `upstream_name`). Table complète
dans `tools_library/ATTRIBUTION.md`. Total des corps : ~53 000 caractères, sous
la limite de 100 000 du prompt. `pyproject.toml` package désormais aussi les
fichiers compagnons, et `_seed_bundled_skills()` rafraîchit les copies
utilisateur marquées `nemesis_builtin: true` sans écraser une personnalisation.

### Reprise de session : plus de prompt système renvoyé

Le frontend créait la session **après** l'ouverture du WebSocket : le backend
n'avait donc aucun `session_id`, n'écrivait jamais `nemapi_initialized` et
renvoyait le prompt + catalogue d'outils à chaque reprise.

- `useAppStore.sendMessage()` ajoute `session_id` au message `user_message`.
- `/ws/chat` appelle `AgentRunner.bind_session()` avant de démarrer le tour.
- `sessions.is_initialized()` retourne vrai pour le drapeau persistant **ou**
  pour un ancien transcript contenant une réponse `assistant` (sessions créées
  avant le correctif).
- `mark_initialized()` ne dépend plus de la présence d'un `system_prefix` : un
  catalogue vide marque quand même la conversation comme démarrée.
- Garde supplémentaire au début de chaque tour pour les sessions initialisées
  par une autre connexion.

Tests : `tests/test_authentic_skills.py` et `tests/test_session_resume.py`
(24 cas), dont un test WebSocket de bout en bout du rattachement de session.

---

## [Non publié] — Relais navigateur pour les URL NemApi distantes (26 octobre 2026)

Une URL publique Cloudflared configurée dans l'interface Web échouait avec
`HTTPSConnectionPool(...): Max retries exceeded with url: /v1/models (Caused by
SSLError(SSLZeroReturnError(6, 'TLS/SSL connection has been closed (EOF)')))`.
Le backend hébergé dans un environnement à sortie réseau restreinte ne peut pas
établir le handshake TLS vers un hôte non autorisé ; le navigateur le peut.

- **Nouveau module `web/backend/browser_relay.py`** : `RelayHub` (requêtes en
  attente, abonnés WebSocket, distribution sans duplication), `RelaySession`
  (faux `requests.Session` avec `get`/`post`), `RelayResponse` compatible
  `raise_for_status()`/`json()`. Messages d'erreur explicites si aucun
  navigateur n'est connecté ou si le `fetch()` échoue.
- **Nouveau WebSocket `/ws/relay`** et `GET /api/relay` (diagnostic). Le
  frontend (`useAppStore.connectRelay`) exécute les `fetch()` NemApi et renvoie
  statut + corps ; reconnexion automatique après 3 s.
- **Option `nemapi.browser_relay`** : interrupteur « Relais navigateur » dans
  Réglages → Connexion, avec état connecté/déconnecté. Interprété seulement par
  le backend Web (`attach_to_provider`) — le CLI et Telegram gardent leurs
  requêtes directes. Désactivé automatiquement en mode local.
- **Normalisation d'URL** : un schéma absent devient `https://`, les `/` finaux
  sont retirés, le suffixe `/v1` reste accepté. `list_models()` ajoute une
  consigne « Active Relais navigateur » quand l'erreur ressemble à un blocage
  TLS/réseau.
- **Sécurité** : le backend construit l'URL à partir de la configuration ; le
  navigateur ne choisit pas la cible. Le jeton `Authorization` (si configuré)
  transite par le navigateur de l'utilisateur qui l'a saisi.
- Vérifié sans contacter l'URL de l'utilisateur : 8 nouveaux tests unitaires,
  et un test de bout en temps réel sur un stub local où `/api/models` et
  `/ws/chat` passent tous deux par le relais (`X-NemApi-Agent: nemesis`).

---

## [Non publié] — Lecture d'images en multimodal NemApi (26 octobre 2026)

NemApi v5.1/v5.2 sait joindre des images à une requête OpenAI (`image_url` en
`data:` URL base64), en les uploadant via l'interface web du fournisseur —
notamment `deepseek-chat`. NEMESIS n'exploite plus les méthodes rudimentaires
(dump binaire avec `errors="replace"`, base64 tapé, upload vers un hébergeur
public) : les fichiers texte restent du texte, les images deviennent de vraies
pièces jointes.

- **Nouveau module `src/core/image_attachments.py`** : détection par magic bytes
  (PNG/JPEG/GIF/WebP/BMP/TIFF), construction des parts OpenAI `image_url`,
  validation des limites NemApi (data: URL uniquement, ~15 Mo par fichier,
  8 images par requête), et helper `send_message_with_images()` qui garde les
  providers non-multimodaux fonctionnels.
- **`read_file`** (`src/core/tools/read_file.py`) : une image n'est plus lue
  comme du texte. Le `FEEDBACK` contient `[IMAGE ATTACHED] <chemin> (MIME, taille)`
  et le résultat porte une liste structurée `images`, propagée par `ToolBridge`.
  Le mode multi-fichiers fusionne texte et images.
- **Provider** (`providers/nemapi_v3.py`) : `send_message(..., images=...)` émet
  `content: [{type: text}, {type: image_url}, ...]` sur le message `user`
  (rôle `tool_result` inclus). Sans image, le contenu reste une chaîne —
  protocole historique inchangé. En-tête `X-NemApi-Agent: nemesis` ajouté à
  chaque requête (option `nemapi.agent`).
- **Boucles CLI, Web et Telegram** : les images trouvées dans les résultats
  d'outils sont mises en attente puis jointes à la requête suivante ; un
  `notice` et un badge « N image(s) · multimodal » les rendent visibles dans le
  Web. Repli automatique en texte pour les providers sans support.
- **Interface Web** : trombone dans le composer (8 images, 8 Mo chacune),
  aperçus et suppression avant envoi, vignettes dans la bulle utilisateur.
  `POST /ws/chat` accepte `{"type":"user_message","content":...,"images":[...]}`.
  `ws_max_size` relevé à 64 Mo (surchargeable par `NEMESIS_WS_MAX_SIZE`).
- **Telegram** : les photos et documents image envoyés au bot sont téléchargés
  et transmis en multimodal ; un message sans texte mais avec photo est accepté.
- **Prompt système** : nouvelle section « Images and multimodal reading »
  expliquant au modèle qu'il voit l'image jointe, sans redemander de base64.
- **Tests** : 12 nouveaux tests (`tests/test_multimodal.py`) couvrant la
  détection, les limites, le payload provider, le repli legacy et les deux
  scénarios de boucle Web. Contrat vérifié aussi contre le vrai
  `nemapi.utils.tools_format.extract_images` de NemApi.

---

## [Non publié] — Autonomie totale et chemins libres (25 octobre 2026)

### Plus aucune limite d'itérations

- CLI (`agent.py`), Web (`web/backend/agent_runner.py`), Telegram (`telegram_bot.py`),
  agents A2A (`src/core/agent_manager.py`) : la boucle d'outils est désormais illimitée.
  Seules conditions d'arrêt : plus aucun appel d'outil, interruption utilisateur, réponse
  dupliquée, erreur du provider.
- Suppression du plafond « 3 appels d'outil par message » (Web, sous-agents, prompt système).
- Sous-agents : `max_iterations: 0` = illimité (défaut), plafond configurable conservé pour
  ceux qui le souhaitent.

### Plus de workspace, plus de refus de chemin

- `src/core/paths.py` : `session_cwd()` / `set_session_cwd()` / `resolve_path()` remplacent la
  résolution confinée ; `resolve_existing_path()` n'redirige plus vers un workspace.
- `security.workspace` n'est plus qu'un répertoire de départ (vide = répertoire courant) et
  n'est plus créé automatiquement.
- `write_file` respecte strictement `file_path` / `path` / `filename`, crée les dossiers
  parents, renvoie le chemin absolu écrit et refuse explicitement un dossier (fin de
  l'erreur `Errno 21 — Is a directory`).
- `edit`, `delete_file` et `read_file` acceptent les mêmes alias ; `delete_file` refuse
  uniquement la racine du système.
- Explorateur Web et commande `/files` : navigation sur tout le système, chemins absolus
  acceptés, plus de `403 Chemin hors du workspace`.
- Le plan de travail (`todo`) est stocké dans `~/.config/nemesis-cli/.nemesis_todos.json`
  et ne dépend plus du répertoire courant.

### Shell fiable pour les gros travaux

- `bash` utilise `/bin/bash` quand il existe, accepte les commandes multi-lignes et les
  heredocs sans échappement supplémentaire.
- Le répertoire courant est **persistant** : un `cd` reste actif pour les appels suivants
  (capture silencieuse de `$PWD`, sans polluer la sortie). `workdir`/`cwd` restent disponibles.
- Timeout : défaut 600 s, minimum 10 s, maximum 7 jours (plus de plafond à 600 s). Alias
  `timeout_ms` et `background` acceptés.
- Sortie jamais vide : `[no output captured — exit code: N]`, stderr et signal de
  timeout/interruption toujours remontés. `format_tool_feedback()` est partagé par CLI,
  Web et Telegram (le code de sortie n'était jamais affiché auparavant).
- Le code de sortie est aussi visible dans l'interface : badge `exit N` sur les cartes
  d'outil (Web) et suffixe `· exit N` sur les cartes CLI.

### Sous-agents

- Sans clé dédiée, `SubagentFleet` réutilise automatiquement le provider principal
  (`availability()` = `main-provider`) au lieu d'échouer avec « aucune clé API ».
- Les résultats d'outils complets sont transmis aux workers (plus de plafond à 3 appels).
- Le statut de délégation est affiché dès le démarrage de la CLI, dans `/status`
  (ligne `Delegation`) et dans `GET /api/subagents` (`delegation`) : plus de
  découverte tardive d'une délégation indisponible.

### Troisième passe — racine commune des trois outils

Le rapport v3 ne laissait que trois problèmes. Tous partageaient la même cause :
la base de résolution de chemin n'était pas la même entre `write_file`, `bash` et
`apply_patch`.

- **Un résolveur unique** (`src/core/paths.py::resolve_target_path`) traite `file_path`,
  `path`, `filename`, `file` et `target_file` de façon identique :
  - premier alias = dossier existant + second alias = nom de fichier → jointure
    (`path=/projet` + `filename=app.py` → `/projet/app.py`) ;
  - un fichier existant gagne ; sinon un candidat qui ressemble à un fichier (suffixe) ;
  - un dossier seul n'est jamais transformé en fichier : erreur explicite ;
  - un dossier inexistant reste un dossier (fin des fichiers créés à la place du dossier) ;
  - les chemins relatifs se résolvent depuis le répertoire du shell, jamais depuis le
    répertoire du processus.
- **Shell interactif strict** (`tools.py::_looks_interactive`) : le PTY n'est utilisé que si le
  **programme exécuté** en a besoin (`ssh`, `mysql`, `passwd`, `vim`, `htop`, `git log | less`…).
  Auparavant une simple sous-chaîne (`node`, `read `) dans un chemin ou un commentaire basculait la
  commande sur une voie qui repartait du répertoire par défaut, **perdait le `cd`** et renvoyait une
  sortie vide. Cette voie démarre maintenant dans le répertoire du shell, mémorise le répertoire
  final et remonte toujours `exit_code`/`stdout`.
- **`apply_patch` multi-base** : `workdir` explicite → répertoire du shell → ses ancêtres (du plus
  proche au plus haut) → racine du projet (`.git`/`pyproject.toml`) → `$HOME`. Un shell garé dans
  `src/deep/nested` applique désormais `--- a/app.py` sur le fichier de la racine, et le message
  d'erreur liste les répertoires réellement essayés.
- **`--check` couvre 9 scénarios** (ajout : `path` dossier + `filename`, `cd` après une commande
  contenant `node`, patch trouvé hors du répertoire courant) : `nemesis --check` doit afficher
  9 × OK.

### Diagnostic de mise à jour — `nemesis --check`

- Nouvelle commande `nemesis --check` (alias `--selftest`, module `src/core/selfcheck.py`) :
  rejoue les six scénarios corrigés (`write_file` avec `path`, refus propre sans chemin,
  `cd` persistant, `apply_patch` absolu et avec `workdir`, exit code/stderr) et affiche
  le **chemin des modules réellement chargés**.
- Objectif : distinguer un correctif absent d'un correctif présent mais non chargé —
  cas fréquent après un `git pull` quand le CLI tourne depuis un paquet installé
  (`site-packages`, `dist-packages`, installation utilisateur) ou qu'un serveur Web /
  bot Telegram a été démarré avant la mise à jour.
- `/status` affiche désormais `Version` et `Code` (dépôt ou paquet, avec le chemin).
- Codes de retour : `0` si tout passe, `1` sinon (utilisable dans un script).

### Deuxième passe — rapport « Limites des outils et du système » (§7.2)

- **`write_file` / `path`** : les alias `path`, `file_path`, `filename` et `file` sont
  normalisés dans **toutes** les couches, y compris l'exécuteur historique
  (`tools.py::ActionExecutor`) qui levait `TypeError: unexpected keyword argument 'path'`.
  Un chemin vide n'est plus jamais résolu vers le répertoire courant : la source réelle
  de `Errno 21 — Is a directory` (`smart_write`) renvoie désormais un message explicite.
- **`apply_patch`** : réécrit avec un moteur d'application en Python (aucune dépendance au
  répertoire du process ni aux restrictions de GNU `patch`). Nouveaux comportements :
  - `workdir`/`cwd` (défaut : répertoire courant persistant du shell) ;
  - chemins **absolus** acceptés, y compris hors du répertoire de travail — fin de
    `Ignoring potentially dangerous file name` ;
  - préfixes `a/`/`b/` retirés progressivement jusqu'à trouver le fichier ;
  - création (`--- /dev/null`) et suppression (`+++ /dev/null`), multi-hunks et
    multi-fichiers, mode `dry_run`, correspondance tolérante aux espaces ;
  - repli automatique sur GNU `patch` dans le répertoire de la cible ;
  - erreurs explicites (`hunk #2 ... ne correspond à aucune ligne`, `fichier introuvable`).
- **Shell persistant partout** : `tools.py::execute_bash` délègue à `run_bash`, donc l'exécuteur
  historique conserve lui aussi le `cd` entre appels (il repartait auparavant du répertoire
  initial) et remonte exit code, stderr et timeouts comme le chemin moderne.
- **Arguments JSON tronqués** : une réponse dont les `arguments` ne sont pas un JSON valide
  produit désormais `Arguments invalides pour '<outil>': ... (réponse probablement tronquée)`
  au lieu d'un « paramètre manquant » trompeur.
- **Noms d'outils historiques** : `write`, `append`, `replace`, `read`, `validate`,
  `kill_process` sont de nouveau traduits par `ToolBridge` (la table d'alias existait mais
  n'était jamais appelée, d'où `Outil inconnu: append`).

## [Non publié] — Interface Web, Mémoire, GitHub, Performance

### Sous-agents et skills

- Ajout de flottes de sous-agents temporaires exécutées en parallèle via plusieurs profils API OpenAI-compatibles, avec reprise bornée des 429, rotation des identifiants, budget par tâche et rapports de progression.
- Réglages réorganisés en navigation latérale ; ajout de profils fournisseurs personnalisés (nom, URL `/v1`, modèle et clé) et gestion des profils enregistrés.
- Skills intégrées de brainstorming, design frontend, Git, diagnostic et vérification, désactivées par défaut et injectées dans le premier prompt des nouvelles conversations lorsqu'elles sont activées.
- Import de skills via un fichier `SKILL.md` local ou une URL GitHub directe et validée. Le contenu importé n'est pas exécuté.
- Variables d'environnement MCP configurables dans l'interface ; seules leurs clés sont affichées et le fichier MCP est écrit en mode privé.

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
- Les dépendances Python requises par la flotte et l'import de skills (`requests`, `PyYAML`) sont déjà des dépendances de base dans `pyproject.toml` et `requirements.txt` ; aucune nouvelle dépendance Python runtime n'est nécessaire.
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