# NEMESIS — Intégration GitHub

Connecter GitHub à NEMESIS permet à l'agent de manipuler vos dépôts, issues et pull requests, et de rechercher du code, via des **outils GitHub dédiés** exposés par un serveur **MCP** (« GitHub MCP Server »).

---

## Sommaire

1. [Principe](#principe)
2. [Prérequis](#prérequis)
3. [Créer un token (PAT)](#créer-un-token-pat)
4. [Connexion depuis l'interface Web](#connexion-depuis-linterface-web)
5. [Ce qui est enregistré](#ce-qui-est-enregistré)
6. [Utilisation par l'agent](#utilisation-par-lagent)
7. [Déconnexion](#déconnexion)
8. [API REST](#api-rest)
9. [Sécurité](#sécurité)
10. [Dépannage](#dépannage)

---

## Principe

Plutôt que d'ajouter une dizaine d'outils GitHub au registre principal (ce qui l'alourdirait), NEMESIS :

1. enregistre un serveur **MCP GitHub** (`npx -y @modelcontextprotocol/server-github`) ;
2. lui injecte votre token via la variable d'environnement `GITHUB_PERSONAL_ACCESS_TOKEN` ;
3. l'agent appelle ensuite les outils GitHub par le biais des outils MCP génériques (`mcp_tools_list`, `mcp_call`).

Aucun outil système supplémentaire n'est donc ajouté au registre principal.

---

## Prérequis

- **Node.js / npx** installés sur la machine qui exécute NEMESIS (le serveur MCP GitHub est distribué via npm).
- Un **token personnel GitHub (PAT)**.

---

## Créer un token (PAT)

1. GitHub → **Settings → Developer settings → Personal access tokens**.
2. Choisissez *Fine-grained* ou *Classic*.
3. Scopes/perm Cognitions recommandés :
   - `repo` (dépôts privés/publics, code, commits, PRs)
   - `read:org` (organisations)
   - `workflow` (si vous manipulez des GitHub Actions)
4. Copiez le token (il ne sera plus affiché ensuite).

---

## Connexion depuis l'interface Web

Réglages → onglet **GitHub** :

- **Token personnel GitHub (PAT)** : collez le token.
- **Nom Git (`user.name`)** : utilisé pour l'identité des commits.
- **Email Git (`user.email`)** : idem.
- **Identifiant GitHub** (optionnel) : votre login.
- Cliquez **Enregistrer**.

Le badge passe à **Connecté** et le serveur MCP `github` apparaît dans l'onglet **MCP**.

---

## Ce qui est enregistré

Configuration partagée (toutes surfaces) :

```text
~/.config/nemesis-cli/
├── github.yaml       # { name, email, login, token }
└── mcp_config.yaml   # entrée "github" (command + args + env)
```

Entrée MCP générée :

```yaml
github:
  command: npx
  args: ["-y", "@modelcontextprotocol/server-github"]
  env:
    GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_xxx"
    GIT_AUTHOR_NAME: "Octocat"
    GIT_COMMITTER_NAME: "Octocat"
    GIT_AUTHOR_EMAIL: "octo@example.com"
    GIT_COMMITTER_EMAIL: "octo@example.com"
  description: "GitHub MCP server (issues, PRs, repos, code search)."
```

Le token n'est **jamais** renvoyé au frontend : l'API ne renvoie qu'un aperçu masqué (`ghp_...7890`).

---

## Utilisation par l'agent

Une fois connecté, l'agent peut :

1. **Découvrir** les outils GitHub disponibles :

   ```json
   { "tool": "mcp_tools_list", "parameters": { "server": "github" } }
   ```

2. **Appeler** un outil GitHub (exemple : créer une issue) :

   ```json
   {
     "tool": "mcp_call",
     "parameters": {
       "server": "github",
       "tool": "create_issue",
       "arguments": {
         "owner": "teteekoue",
         "repo": "NEMESIS-CLI",
         "title": "Bug: ...",
         "body": "..."
       }
     }
   }
   ```

Les serveurs MCP sont **mis en cache (pool)** : le serveur GitHub est réutilisé entre deux appels d'un même tour, ce qui évite le coût de démarrage `npx`.

---

## Déconnexion

Réglages → GitHub → **Déconnecter** : supprime le token, l'identité et l'entrée MCP `github`.

---

## API REST

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET  | `/api/github` | État (connecté, aperçu token, identité, commande MCP) |
| POST | `/api/github` | Enregistre `token`, `name`, `email`, `login` ; synchronise le MCP |

Exemples :

```bash
# État
curl http://localhost:8000/api/github

# Connexion
curl -X POST http://localhost:8000/api/github \
  -H "Content-Type: application/json" \
  -d '{"token":"ghp_xxx","name":"Octocat","email":"octo@example.com","login":"octocat"}'

# Déconnexion (token vide)
curl -X POST http://localhost:8000/api/github \
  -H "Content-Type: application/json" \
  -d '{"token":"","name":"","email":"","login":""}'
```

---

## Sécurité

- Le token est stocké **localement** dans `~/.config/nemesis-cli/github.yaml` (permissions du répertoire utilisateur).
- Il n'est jamais transmis au navigateur (seul un aperçu masqué est renvoyé).
- Ajoutez `github.yaml`/`mcp_config.yaml` à vos exclusions si vous versionnez votre répertoire de config.
- Préférez un **fine-grained token** limité aux dépôts nécessaires.

---

## Dépannage

| Problème | Cause probable / Solution |
| -------- | ------------------------- |
| Le serveur MCP `github` n'apparaît pas | Node.js/npx absent, ou token non enregistré |
| `MCP command not found: npx` | Installer Node.js (npm/npx) |
| Erreur d'authentification GitHub | Token invalide/expiré ou scopes insuffisants |
| Le premier appel est lent | Démarrage `npx` initial ; les appels suivants sont mis en cache |
| Outils GitHub non listés | Vérifier que `mcp_tools_list("github")` répond, puis relancer |