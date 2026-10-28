# Limites du runtime et protocole d'appel d'outils

Ce document décrit les pièges observés avec NemApi et les garde-fous livrés
dans NEMESIS. Les règles essentielles sont **déjà injectées dans le prompt
système** (`src/core/system_prompt.py`) : ce fichier sert de référence
détaillée pour les humains. Aucun accès réseau externe n'est nécessaire.

---

## 1. Troncature silencieuse de `write_file`

**Symptôme historique.** NemApi coupe ses réponses au milieu du JSON. Mesures
de la session d'origine :

| Taille demandée | Résultat observé |
|---|---|
| 100 o – 5 537 o | ✅ fichier complet |
| ~6 800 o et au-delà | ❌ fichier absent ou partiel, mais `Success: True` |

Le seuil se situe entre ~5,5 Ko et ~6,8 Ko. Le parseur « réparait » le JSON
incomplet en refermant la chaîne : l'outil écrivait alors un contenu tronqué
et annonçait un succès. C'était le mode de défaillance le plus grave.

**Correctifs livrés.**

- Le prompt impose **4 000 caractères de JSON par appel** (marge sous le seuil)
  et un **plafond dur de 5 000 caractères** appliqué par `ToolBridge` à
  *tous* les outils : `write_file`, `append`, `edit`, `apply_patch`, `bash`,
  MCP, délégation, mémoire. Au-delà, l'appel est **refusé sans rien exécuter**
  avec un message actionnable.
- `ActionParser(strict=True)` (activé dans les boucles CLI, Web et Telegram)
  détecte une chaîne, un objet ou une clôture de bloc ``` non terminés. Il
  renvoie `protocol_error` au lieu d'exécuter un appel réparé : la boucle
  renvoie ce message au modèle, qui doit redécouper son appel.
- `write_file` et `append` vérifient réellement la taille du fichier après
  écriture (`bytes_written`, `total_bytes`). Un écart produit un échec
  explicite, jamais un succès silencieux.
- Le modèle doit vérifier le résultat final avec `read_file` ou `wc -c`.

**Méthode pour un long fichier.**

1. `write_file` avec le **premier bloc** et le chemin définitif.
2. Un appel `append` par bloc suivant, **un par message**, en attendant le
   `FEEDBACK` entre chaque (ces appels sont dépendants, jamais groupés).
3. Chaque appel ne contient que son propre bloc : ne jamais renvoyer le fichier
   entier, ni ajouter de séparateur, d'ellipse ou de numéro de ligne.
4. Vérification finale avec `read_file` / `wc -c` avant d'annoncer le succès.

L'outil `append` accepte `file_path`, `path` ou `filename`, crée le fichier et
ses dossiers parents s'ils n'existent pas, et fonctionne sur **Web, CLI et
Telegram** (même `ToolBridge` / même `ToolRegistry`).

---

## 2. Feedback décalé d'un appel

**Symptôme historique.** Le `FEEDBACK` reçu correspondait parfois à l'appel
précédent (résultat de `bash` après un `write_file`, doublon sur deux `bash`
consécutifs). Conséquence : une vérification supplémentaire à chaque fois.

**Correctifs livrés.**

- Chaque bloc de feedback porte désormais un marqueur de corrélation
  `[call #N — M tool(s)]`, généré par `build_feedback_message()` et incrémenté
  à chaque lot d'outils dans les trois boucles. Le prompt explique au modèle
  que `N` ne doit jamais se répéter.
- Si le marqueur se répète ou si les noms d'outils ne correspondent pas au
  dernier appel, le modèle doit considérer la réponse comme périmée, émettre
  une commande inoffensive (`echo PING_<N>`) pour forcer un tour frais, puis
  re-vérifier. Il ne doit jamais enchaîner une action destructive sur un
  feedback non corrélé.
- Les erreurs de protocole utilisent `protocol_error_feedback()` avec le même
  marqueur, ce qui évite tout décalage supplémentaire.

---

## 3. `read_file` et paramètres

**Symptôme historique.** `Parametres requis pour 'read_file': path ou paths ou
file_path ou filename` alors que `path` était fourni — le JSON était en fait
mal formé (balise interne, objet imbriqué).

**Correctifs livrés.**

- Validation plus tolérante : `file`, `target_file`, `target`, `name` sont
  normalisés vers `file_path` / `path`, et `files` vers `paths`.
- Le prompt rappelle la forme canonique :

  ```json
  {"tool": "read_file", "parameters": {"path": "fichier.txt"}}
  ```

- En mode strict, un appel dont les arguments n'ont pas pu être décodés est
  rejeté explicitement au lieu d'être exécuté avec un fragment.

---

## 4. DSML et dialectes XML — interdits

**Symptôme historique.** Émission de blocs `<|DSML|>` ou `<tool_call>` au lieu
du JSON attendu, avec un aller-retour perdu à chaque correction.

**Correctifs livrés.**

- Le prompt interdit explicitement `<|DSML|>`, `<DSML>`, `</DSML>`,
  `<tool_call>`, `<function=...>`, `<parameter=...>` et tout hybride XML/JSON.
- Le parseur strict rejette ces réponses et renvoie un `PROTOCOL ERROR`
  demandant de réémettre **un seul objet JSON valide** dans un bloc ```json.
- Après trois erreurs de protocole consécutives, le tour s'arrête pour éviter
  une boucle infinie.
- La tolérance historique aux dialectes XML reste disponible pour les autres
  providers via `ActionParser(strict=False)` ; elle n'est plus utilisée par les
  boucles principales.

---

## 5. Heredoc `bash`

**Symptôme historique.** `bash: unexpected EOF while looking for matching '`
dès que le contenu contient une apostrophe, un backtick ou un `$`.

**Correctif livré.** Le prompt interdit les heredocs (`<<EOF`, `<<'EOF'`)
dans un appel `bash`. Pour créer un script : `write_file` puis `append`,
ensuite `bash: python3 script.py`. Rappels de quoting : `"$VAR"` s'expand,
`'$VAR'` non ; ne jamais mettre une variable entre apostrophes.

---

## 6. Pièges mineurs recensés

| Problème | Contexte | Règle retenue |
|---|---|---|
| `/tmp/...` absent malgré un succès | écriture dans un répertoire éphémère | préférer un chemin absolu stable et vérifier avec `read_file` |
| `sed -i` avec `$F` non expansé | variable entre apostrophes | `"$VAR"` avec guillemets doubles, chemin complet |
| `python3 -c '...'` avec `$F` | idem | garder le corps court, sinon écrire un vrai fichier |
| `os.path.abspath(__file__)` | résout depuis le CWD, pas le symlink | `os.path.realpath(__file__)` pour le chemin physique |
| `input keycombination` absent | Android 9 | éviter, aucun correctif runtime possible |
| `KEYCODE_CTRL_A` / `KEYCODE_DEL` inopérants | WebView Android | vérifier le résultat au lieu de supposer |
| `screencap` silencieux (code 1, 0 o) | ARCVM en transition | relire la taille du fichier, réessayer puis signaler la limite |

Ces règles sont résumées dans la section « Shell and environment pitfalls » du
prompt système. Les limites matérielles Android ne peuvent pas être corrigées
côté runtime : le modèle doit les vérifier et les signaler honnêtement.

---

## 7. Vérification locale

```bash
./venv/bin/python -m pytest tests/ -q
./venv/bin/nemesis --check          # 12 vérifications, dont append et protocole strict
```

`tests/test_tool_call_guards.py` couvre les garde-fous unitairement ;
`tests/test_tool_loop_e2e.py` rejoue les scénarios complets (écriture par
blocs, appel tronqué, DSML, corrélation du feedback) sur les vraies boucles
CLI, Web et Telegram avec un provider stub local. Aucune requête n'est envoyée
à une URL externe.
