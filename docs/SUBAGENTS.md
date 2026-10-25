# Sous-agents éphémères

NEMESIS peut déléguer plusieurs tâches indépendantes à des instances temporaires. Chaque tâche crée un worker isolé avec son propre historique de conversation et son propre `ToolBridge`. Le worker s'arrête après son rapport ; seule une entrée d'état/résultat bornée reste en mémoire pour permettre la vérification.

## Fonctionnement

1. NEMESIS groupe les tâches indépendantes dans `delegate_tasks(tasks)`. Chaque entrée contient `instruction` (ou ses alias `goal`, `prompt`, `description`), et peut préciser `label`, `context` et `mode: "read_only"` pour interdire les outils d'écriture/exécution. Exemple : `{"instruction":"Analyser src/api.py","context":"Chercher les erreurs de validation","label":"Vérifier l'API","mode":"read_only"}`.
2. La flotte les met en file et les exécute avec une concurrence configurable (1–8, 4 par défaut). Les identifiants retournés servent à suivre les états `queued`, `running`, `retrying`, `completed`, `failed` ou `cancelled`.
3. Chaque appel au modèle envoie l'historique complet OpenAI-compatible de la tâche, les définitions d'outils et les retours d'outils précédents. Les outils sont exécutés localement par NEMESIS, pas par Groq.
4. NEMESIS appelle `subagent_status(task_id)` pour obtenir le rapport, puis vérifie les changements importants avant de les annoncer.

Les sous-agents ont le registre d'outils NEMESIS, le même accès complet au système de fichiers (aucun sandbox) et l'accès MCP/mémoire. Sans clé dédiée configurée, le provider principal est réutilisé automatiquement (`availability()` = `main-provider`) : le statut est affiché au démarrage de la CLI, dans `/status` (ligne `Delegation`) et via `GET /api/subagents` (objet `delegation`) ; `max_iterations: 0` (défaut) signifie aucune limite d'itérations. Ils ne peuvent pas créer d'autres sous-agents. En mode Web « Confirmation », leurs actions à risque passent par la même autorisation utilisateur que les actions de l'agent principal. Plusieurs workers agissent sur le même workspace : leurs tâches doivent éviter les fichiers partagés ou définir explicitement comment fusionner les changements.

## Fournisseur et configuration

Le fournisseur principal NEMAPI reste inchangé ; les sous-agents utilisent une API classique compatible OpenAI (par défaut Groq). Le modèle par défaut est `qwen/qwen3.8-27b`, sur l'URL Groq `https://api.groq.com/openai/v1`.

Dans l'onglet **Réglages → Sous-agents**, configurer :

- plusieurs profils indépendants (`Groq`, `OpenAI` ou endpoint compatible personnalisé), chacun avec son nom, URL, modèle et clé API ;
- nombre maximal de workers, limite d'itérations, timeout, nombre de reprises HTTP 429 et budget maximal de tokens par tâche.

Pour un endpoint personnalisé, renseigner le nom du fournisseur, une URL HTTPS
terminant par `/v1`, l'identifiant exact du modèle et son token d'accès. Les
profils Groq, OpenAI et personnalisés partagent le même équilibrage en rotation.
Les secrets restent dans la configuration utilisateur et ne sont jamais
renvoyés au frontend.

Les profils sont sélectionnés en rotation. Une clé ayant reçu HTTP 429 est mise en refroidissement selon `Retry-After`/le délai retourné par le fournisseur ; la tâche reprend avec un autre profil et un backoff exponentiel borné. Les erreurs 400/401/403 sont signalées distinctement et ne relancent pas l'appel. Les tâches exposent modèle, durée, requêtes, retries et tokens lorsque le fournisseur renvoie l'usage. Le budget par tâche est une estimation conservatrice du prompt (taille JSON / 4) complétée par les tokens réellement rapportés par l'API ; la limite de sortie est ajustée au budget restant.

La clé peut aussi être fournie avec `GROQ_API_KEY` (ou `OPENAI_API_KEY` pour OpenAI). L'API Web ne renvoie jamais la clé au navigateur. Une clé saisie est stockée dans le fichier de configuration utilisateur `~/.config/nemesis-cli/config.yaml`, dont les permissions sont fixées à `0600` ; ne pas copier ce secret dans le dépôt.

Équivalent YAML :

```yaml
subagents:
  profiles:
    - id: equipe-a
      name: Groq équipe A
      provider: groq
      base_url: https://api.groq.com/openai/v1
      model: qwen/qwen3.8-27b
      # api_key: stockée dans le fichier utilisateur, jamais dans le dépôt
  max_concurrent: 4
  max_iterations: 12
  timeout_seconds: 180
  max_retries: 3
  max_tokens_per_task: 24000
```

Les erreurs provider et outils apparaissent dans le statut `failed` ou le résultat d'outil. Les rapports sont limités en mémoire et expirent au fil des nouvelles tâches ; ils ne sont pas écrits sur disque.

## Références provider

- [Groq — OpenAI compatibility](https://console.groq.com/docs/openai)
- [Groq — Tool use](https://console.groq.com/docs/tool-use)
- [Groq — Models and deprecations](https://console.groq.com/docs/models)
