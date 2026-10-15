# NEMESIS — Mémoire persistante

NEMESIS peut mémoriser des informations durables (préférences, identité, contexte récurrent) et les **réinjecter automatiquement** dans le prompt système à chaque nouvelle session. La mémoire est **unique et partagée** entre le CLI, l'interface Web et le bot Telegram.

---

## Sommaire

1. [Principe](#principe)
2. [Emplacement et format](#emplacement-et-format)
3. [Comment elle est utilisée](#comment-elle-est-utilisée)
4. [Gestion depuis l'interface Web](#gestion-depuis-linterface-web)
5. [Outils mémoire de l'agent](#outils-mémoire-de-lagent)
6. [Activer / désactiver](#activer--désactiver)
7. [API REST](#api-rest)
8. [Bonnes pratiques](#bonnes-pratiques)

---

## Principe

La mémoire se compose de deux éléments :

- une **préférence d'activation** (`enabled`) ;
- une liste de **mini-blocs** autonomes, chacun avec un titre, un contenu, des tags et une clé stable optionnelle.

Elle n'est PAS un historique de conversation : le contexte conversationnel reste géré côté NEMAPI. La mémoire sert à conserver des **faits stables** utiles entre les sessions (par ex. « l'utilisateur préfère le français », « le projet utilise FastAPI »).

---

## Emplacement et format

Tout est stocké dans le répertoire de configuration utilisateur (partagé par toutes les surfaces) :

```text
~/.config/nemesis-cli/
└── memory/
    ├── prefs.json     # { "enabled": true }
    └── blocks.json    # [ { id, key, title, content, tags, created_at, updated_at }, ... ]
```

Le répertoire est surchargeable via `NEMESIS_CONFIG_DIR`.

Un bloc a la forme :

```json
{
  "id": "92c602d68ae8",
  "key": "",
  "title": "Préférences de langue",
  "content": "L'utilisateur préfère être répondu en français.",
  "tags": ["pref"],
  "created_at": 1792085714.4,
  "updated_at": 1792085714.4
}
```

- `id` : identifiant unique généré.
- `key` : identifiant **stable** optionnel (utilisé pour la mise à jour sans doublon).

---

## Comment elle est utilisée

À chaque nouvelle session, `build_system_prompt()` (dans `src/core/agent_tools.py`) ajoute un bloc « MEMOIRE UTILISATEUR » au prompt système **si la mémoire est activée**. Ce prompt est partagé par :

- le **CLI** (`agent.py`, `cli.py`) ;
- le **bot Telegram** (`telegram_bot.py`) ;
- l'**interface Web** (`web/backend/agent_runner.py`).

Résultat : toutes les surfaces « connaissent » les mêmes informations mémorisées, sans configuration supplémentaire.

---

## Gestion depuis l'interface Web

Réglages → onglet **Mémoire** :

- bascule **Utiliser la mémoire** (activer/désactiver) ;
- formulaire **Ajouter / Modifier** un bloc (titre, contenu, tags séparés par des virgules) ;
- liste des blocs avec actions **modifier** et **supprimer** ;
- bouton **Tout supprimer**.

---

## Outils mémoire de l'agent

L'agent peut gérer sa propre mémoire via 6 outils :

| Outil | Rôle | Paramètres |
| ----- | ---- | ---------- |
| `memory_add` | Ajoute un bloc | `title`, `content`, `tags?` |
| `memory_update` | Crée/actualise via une clé stable | `key`, `title`, `content`, `tags?` |
| `memory_search` | Recherche par texte/tags | `query?`, `tags?`, `limit?` |
| `memory_get` | Lit un bloc (ou liste tout) | `block_id?` |
| `memory_delete` | Supprime un bloc (risque élevé) | `block_id` |
| `memory_clear` | Vide toute la mémoire (risque élevé) | — |

Niveaux de risque : `memory_add`/`memory_update` sont *medium*, `memory_delete`/`memory_clear` sont *high* (confirmation requise en mode `confirm`).

---

## Activer / désactiver

- Interface Web : onglet Mémoire.
- Programme : `prefs.json` (`enabled`) ou `settings_store.memory_set_enabled(...)`.

Quand la mémoire est désactivée, aucun contenu n'est injecté — mais les blocs restent stockés.

---

## API REST

| Méthode | Route | Description |
| ------- | ----- | ----------- |
| GET    | `/api/memory` | `{ enabled, blocks }` |
| POST   | `/api/memory` | Ajoute/actualise (`id`, `key`, `title`, `content`, `tags`) |
| DELETE | `/api/memory/{id}` | Supprime un bloc |
| POST   | `/api/memory/clear` | Vide la mémoire |
| POST   | `/api/memory/toggle` | `{ enabled: bool }` |

Exemple :

```bash
curl -X POST http://localhost:8000/api/memory \
  -H "Content-Type: application/json" \
  -d '{"title":"Langue","content":"Répondre en français.","tags":["pref"]}'
```

---

## Bonnes pratiques

- Un bloc = **un fait autonome** (compréhensible hors contexte).
- Utiliser des `tags` pour regrouper (ex. `pref`, `projet`, `environnement`).
- Réutiliser la même `key` (`memory_update`) pour corriger un fait existant plutôt que d'en créer un nouveau.
- Éviter de mémoriser des secrets (tokens, mots de passe).