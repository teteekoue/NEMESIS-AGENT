# Interface CLI — architecture et conventions

Documentation de la couche `src/ui/`. Dernière refonte : octobre 2026 (branche `ui/v2`).

## Principes de design

L'interface suit le langage visuel des agents CLI modernes (Claude Code, Codex, Gemini CLI) :

- **Pas de cadre autour des messages.** Le contenu domine, une gouttière verticale `│` marque le bloc.
- **Cartes d'outils sans bordure.** En-tête sobre (nom, paramètre, statut, durée), corps derrière la gouttière.
- **Largeur capée à 110 colonnes** (`MAX_CONTENT_WIDTH` dans `theme.py`) pour rester lisible sur grand écran.
- **Palette restreinte.** Catppuccin Mocha, mais la couleur signale (rôle, statut) au lieu de décorer.
- **Troncature systématique.** Toute sortie d'outil est coupée à 8 lignes (`TOOL_OUTPUT_PREVIEW_LINES`) avant affichage. Un conteneur ne doit jamais déborder.

## Modules

| Fichier | Rôle |
|---|---|
| `src/ui/theme.py` | Palette Catppuccin, tokens sémantiques Rich, constantes de largeur/troncature |
| `src/ui/render.py` | Primitives : `guttered()`, `tool_card()`, `diff_body()`, `content_width()`, `truncate_middle()`, `preview_lines()` |
| `src/ui/chat_ui.py` | `ChatUI` (orchestration), `Bubble` (messages), `LiveTerminal` (streaming) |
| `src/ui/composer.py` | Saisie prompt_toolkit, complétion slash, key bindings |
| `src/ui/header.py` | `banner()` (démarrage) et `get_header()` (ligne unique) |

## API publique de `ChatUI`

Consommée par `composer.py` et `agent.py`. Toute modification doit préserver ces signatures :

```python
add_message(content, sender, timestamp=None)
tool_start(tool_name, params)
tool_result(result, tool_name="", duration=None)
tool_error(error)
thinking(message="...")          # GÉNÉRATEUR : next(anim) démarre, anim.close() arrête
auth_prompt(tool_name, params) -> Text
task_summary(elapsed, tool_count=0, files_changed=0)
todo_panel(items)
welcome(version=None, provider="", target="", model="", workspace="", connected=None)
live_terminal(title="bash") -> LiveTerminal
clear()
```

**Attention :** `thinking()` doit rester un générateur. `agent.py` fait `next(anim)` puis `anim.close()`. Retourner un objet `Progress` provoque `TypeError: 'Progress' object is not an iterator`.

## Rendu des outils

`tool_result()` applique trois règles :

1. **Corps affiché uniquement pour les outils terminal** (`bash`, `shell`, `terminal`). Une lecture de fichier ne déverse pas son contenu dans le fil.
2. **Erreurs toujours visibles**, même pour les outils non-terminal.
3. **Diffs** : pour `edit`, `write_file`, `apply_patch`, `search_replace`, si le corps contient des lignes `+`/`-`, il est rendu par `diff_body()` en rouge/vert.

Le paramètre affiché en tête de carte est choisi par `_label()` : `description` en priorité, puis le motif pour `grep`/`glob`, puis la commande ou le chemin.

## Streaming bash

Le tool `bash` (`src/core/tools/bash.py`) yield des dictionnaires `{"partial": ligne}` pendant l'exécution, puis un résultat final `{"success": ...}`.

`agent.py` branche un `LiveTerminal` autour de la boucle `execute_tool()` et pousse chaque `partial` via `live.add_line()`. La clé est **`partial`**, pas `output`/`stdout`.

## Bannière de démarrage

`header.banner()` affiche :

- Logo ASCII 4 lignes (lisible à 60 colonnes)
- Tagline
- Modèle, provider, endpoint, espace, état de connexion (paires clé/valeur alignées à 12 colonnes)
- Raccourcis `/help`, `/status`, `Ctrl+C`

Suivie d'une `Rule` de séparation.

## Résumé de tour

Affiché en fin de `_process_cycle()`, uniquement si au moins un outil a tourné :

```
  2.3s · 4 outils · 1 fichier modifie
```

Accord singulier/pluriel géré. Durée en `mm:ss` au-delà de 60 s. Les compteurs (`cycle_started`, `cycle_tools`, `cycle_files`) sont initialisés au début de `_process_cycle` dans `agent.py`.

## Prompt système : nouvelle discussion vs poursuite

Aucune question interactive au démarrage.

- **Par défaut** (`_send_system_prompt = True`) : le prompt système + mémoire partent avec le premier message.
- **`/continue`** : bascule `_send_system_prompt = False`, `_prompt_sent = True`, `_session_initialized = True`. Le prochain message ne renvoie pas le contexte.

La commande est déclarée dans `src/core/default_commands.py`.

## Interruptions (Ctrl+C)

- **1er Ctrl+C** : interrompt la tâche en cours (`_interrupted = True`)
- **2e Ctrl+C sous 2 s** : ferme le programme (`SystemExit`)

Le key binding `c-c` de prompt_toolkit pose `composer.interrupt_requested = True` puis fait `app.exit()`. La boucle `run()` consomme ce flag via `_consume_input_interrupt()`, qui appelle `_handle_interrupt()` — **ne pas l'appeler une seconde fois** après, sinon le programme se ferme au premier Ctrl+C.

Ctrl+D (`c-d`) a son propre binding et retourne aussi `None`.

## Points fragiles

- `agent.py` et `src/core/default_commands.py` sont volumineux (≈800 et ≈1140 lignes). Les éditions ciblées y ont déjà corrompu du code : **vérifier la compilation après chaque édition** avec `python -c 'import ast; ast.parse(open(f).read())'`.
- `src/ui/composer.py` : `TextArea` de prompt_toolkit n'expose que `text`, `document`, `accept_handler`. Ne pas accéder à `.title` ou `.prompt` — ils n'existent pas. Pour changer l'invite, afficher une ligne de contexte au-dessus.
- `Rich Markdown` n'accepte pas `inline_code_style` (utiliser `inline_code_theme`, ou rien).

## Vérification

```bash
cd ~/Projets/NEMESIS
.venv/bin/python cli.py --demo        # aperçu hors ligne
.venv/bin/python -m pytest -q          # 116 tests
```
