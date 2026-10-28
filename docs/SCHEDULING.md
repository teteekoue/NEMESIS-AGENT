# Planification des tâches de NEMESIS

NEMESIS dispose d'outils communs au CLI, à l'interface Web et à Telegram :

- `schedule_task` : programme une instruction autonome pour une heure future ou un délai ;
- `list_scheduled_tasks` : affiche l'état et le résultat des tâches de l'interface/session courante ;
- `cancel_scheduled_task` : annule une tâche encore en attente ;
- `wait` : met en pause le tour courant pour une durée courte.

## Planifier

Une planification est ponctuelle. Fournir exactement une heure ISO 8601 (avec fuseau conseillé)
ou un délai en secondes :

```json
{"tool":"schedule_task","parameters":{"instruction":"Vérifie demain matin si le rapport est prêt et résume son état.","run_at":"2026-10-29T09:00:00+00:00"}}
```

```json
{"tool":"schedule_task","parameters":{"instruction":"Dans le dossier de travail, vérifie que les tests passent et signale le résultat.","delay_seconds":3600}}
```

Les tâches et leurs résultats sont conservés dans `scheduled_tasks.sqlite3` sous le répertoire de configuration NEMESIS. Elles ne sont pas perdues si le processus redémarre : si leur échéance est passée, elles démarrent lorsque l'interface correspondante revient en ligne. Une tâche en cours lors d'un arrêt brutal peut être relancée après expiration de son bail ; les tâches planifiées offrent donc une exécution au moins une fois, pas une garantie « exactement une fois ».

La planification elle-même est une action à risque élevé et passe par le mécanisme d'approbation de l'interface. Une fois approuvée, l'instruction est exécutée automatiquement à l'échéance, avec le provider et les outils NEMESIS configurés. Elle n'attend pas une confirmation interactive au moment du réveil. NEMESIS ne crée une planification que si l'utilisateur l'a demandée. Les récurrences/cron ne sont pas encore prises en charge.

## Attendre

Utiliser `wait` pour une courte pause du tour courant, par exemple le temps qu'un processus finisse. L'attente peut être interrompue dans le Web et dans le CLI. Pour une pause longue ou une action future, préférer `schedule_task` : le processus n'est pas bloqué dans l'intervalle.

## Telegram : réponse finale lisible

Pour la réponse finale uniquement, l'interface Telegram convertit les titres, listes, citations, tableaux, liens et blocs de code Markdown en texte brut structuré. Les réponses intermédiaires et feedbacks d'outils restent internes au déroulement et ne sont pas envoyés comme messages finaux.
