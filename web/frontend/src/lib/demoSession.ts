import type { ChatMessage } from "@/store/useAppStore";

const base = Date.now() / 1000 - 600;
let offset = 0;
let seq = 0;

function at(): number {
  offset += 3;
  return base + offset;
}

function user(text: string): ChatMessage {
  return { id: `demo-u${seq++}`, kind: "user", text, at: at() };
}

function ai(text: string, turnElapsed?: number, toolCount?: number): ChatMessage {
  return { id: `demo-a${seq++}`, kind: "assistant", text, at: at(), turnElapsed, toolCount };
}

let toolDurationSeq = 0;

function tool(
  name: string,
  params: Record<string, unknown>,
  output: string,
  options: { risk?: string; success?: boolean; status?: ChatMessage["status"]; duration?: number } = {},
): ChatMessage {
  const started = at();
  const duration = options.duration ?? Number((0.2 + (toolDurationSeq++ % 7) * 0.35).toFixed(2));
  return {
    id: `demo-t${seq++}`,
    kind: "tool",
    name,
    params,
    output,
    risk: options.risk ?? "medium",
    success: options.success ?? true,
    status: options.status ?? "done",
    at: started,
    startedAt: started,
    duration,
  };
}

function notice(text: string): ChatMessage {
  return { id: `demo-n${seq++}`, kind: "notice", text, at: at() };
}

/**
 * A fictional, fully-populated session that exercises every tool exposed by the
 * registry so the interface can be reviewed without a live NEMAPI server.
 * Enable it with ``?demo=1`` (auto when the UI opens without a Vite proxy).
 */
export function buildDemoSession(): ChatMessage[] {
  return [
    user(
      "Salut NEMESIS ! J'aimerais que tu prépares un petit projet Python « metrics » avec un README, un module et des tests. Tu peux aussi récupérer les taux de change actuels et me dire ce que donne la suite de tests.",
    ),
    ai(
      "Avec plaisir. Je commence par planifier le travail, puis j'explore le workspace et je pose la structure du projet.\n\n**Plan :**\n1. Créer la structure du projet\n2. Écrire `metrics/__main__.py` et `README.md`\n3. Ajouter un test et lancer `pytest`\n4. Récupérer les taux de change et résumer",
      4.8,
      3,
    ),
    tool("list_dir", { path: "." }, ".\n├── .git/\n├── README.md\n└── src/", { risk: "read" }),
    tool("glob", { pattern: "src/**/*.py", max_results: 20 }, "src/__init__.py\nsrc/utils.py", { risk: "read" }),
    tool("grep", { pattern: "def ", path: "src", include: "*.py", case_insensitive: false }, "src/utils.py:4:def slugify(text):", { risk: "read" }),
    tool(
      "todo",
      {
        items: [
          { content: "Créer la structure du projet", status: "in_progress" },
          { content: "Écrire le module et le README", status: "pending" },
          { content: "Lancer les tests", status: "pending" },
          { content: "Récupérer les taux de change", status: "pending" },
        ],
      },
      "Plan mis à jour — 4 étapes.",
    ),
    user("Parfait, n'oublie pas un README propre et un test qui passe."),
    tool("bash", { command: "mkdir -p metrics/tests", description: "Créer l'arborescence" }, "(aucune sortie)", { risk: "medium" }),
    tool(
      "write_file",
      {
        file_path: "metrics/__main__.py",
        content:
          'def bmi(weight_kg: float, height_m: float) -> float:\n    """Indice de masse corporelle."""\n    if height_m <= 0:\n        raise ValueError("height must be > 0")\n    return round(weight_kg / (height_m ** 2), 2)\n',
      },
      "Écrit 5 lignes dans metrics/__main__.py",
    ),
    tool(
      "write_file",
      {
        file_path: "metrics/README.md",
        content: "# Metrics\n\nPetite bibliothèque de calculs de santé.\n\n```bash\npython -m metrics\n```\n",
      },
      "Écrit 8 lignes dans metrics/README.md",
    ),
    ai(
      "La structure est en place. J'ajoute maintenant un test puis j'aligne le calcul sur la formule officielle.",
      6.2,
      5,
    ),
    tool(
      "read_file",
      { path: "metrics/__main__.py" },
      'def bmi(weight_kg: float, height_m: float) -> float:\n    """Indice de masse corporelle."""\n    if height_m <= 0:\n        raise ValueError("height must be > 0")\n    return round(weight_kg / (height_m ** 2), 2)\n',
      { risk: "read" },
    ),
    tool(
      "edit",
      {
        file_path: "metrics/__main__.py",
        old_string: '        raise ValueError("height must be > 0")',
        new_string: '        raise ValueError("height_m doit être strictement positif")',
        replace_all: false,
      },
      "1 remplacement appliqué dans metrics/__main__.py",
    ),
    tool(
      "write_file",
      {
        file_path: "metrics/tests/test_bmi.py",
        content:
          "from metrics.__main__ import bmi\n\n\ndef test_bmi():\n    assert bmi(70, 1.75) == 22.86\n",
      },
      "Écrit 5 lignes dans metrics/tests/test_bmi.py",
    ),
    tool(
      "apply_patch",
      {
        patch:
          "--- a/metrics/__main__.py\n+++ b/metrics/__main__.py\n@@ -1,3 +1,4 @@\n+from math import isfinite\n def bmi(weight_kg: float, height_m: float) -> float:\n",
        dry_run: false,
      },
      "Patch appliqué (1 hunk).",
    ),
    tool("bash", { command: "python -m pytest -q", description: "Lancer les tests" }, "1 passed in 0.04s", { risk: "medium" }),
    notice("NEMESIS a récupéré le retour des tests."),
    tool("web_search", { query: "API taux de change EUR USD temps réel", max_results: 5 }, "1. exchangerate.host — API gratuite\n2. open.er-api.com — pas de clé", { risk: "read" }),
    tool("web_fetch", { url: "https://open.er-api.com/v6/latest/EUR", format: "markdown" }, '{\n  "base": "EUR",\n  "rates": { "USD": 1.08, "GBP": 0.85 }\n}', { risk: "read" }),
    tool("git", { action: "status", path: ".", limit: 20 }, "On branch main\n\nUntracked files:\n  metrics/", { risk: "medium" }),
    tool("mcp_list", {}, "calculator — MCP interne (opérations mathématiques)", { risk: "medium" }),
    tool(
      "mcp_call",
      { server: "calculator", tool: "multiply", arguments: { a: 70, b: 1 } },
      "70",
      { risk: "high" },
    ),
    tool("skills_list", {}, "unit-converter — conversions d'unités", { risk: "medium" }),
    tool(
      "delegate_task",
      { agent: "Agent1", instruction: "Vérifie la couverture de tests du module metrics." },
      "Tâche déléguée à Agent1 (id=job_7f3a).",
      { risk: "medium" },
    ),
    tool("list_agents", {}, "Agent1 — nemapi · qwen-chat · en ligne", { risk: "read" }),
    tool("agent_status", { task_id: "job_7f3a" }, "job_7f3a — completed (rapport: .nemesis/reports/job_7f3a.md)", { risk: "medium" }),
    tool("get_task_output", { task_id: "job_7f3a", timeout_ms: 5000 }, "Couverture estimée : 92 % (1 test, 1 fonction).", { risk: "medium" }),
    tool("bash", { command: "sleep 120 & echo $!", mode: "asynchrone", description: "Tâche de fond" }, "48213", { risk: "medium" }),
    tool("kill_task", { task_id: "48213" }, "Tâche 48213 arrêtée.", { risk: "high" }),
    // One denied tool, to show the authorization result state
    tool(
      "delete_file",
      { target_file: "metrics/tmp.log" },
      "",
      { risk: "high", success: false, status: "denied" },
    ),
    ai(
      "Tout est prêt. Voici le récapitulatif :\n\n| Étape | Statut |\n| --- | --- |\n| Structure du projet | ✅ |\n| Module `metrics` | ✅ |\n| README | ✅ |\n| Tests `pytest` | ✅ (1 passed) |\n| Taux de change | ✅ 1 EUR = **1.08 USD** |\n\nLe test passe et la couverture estimée est de **92 %**. La suppression de `metrics/tmp.log` a été refusée, je l'ai donc laissé en place.\n\nProchaine étape possible : publier sur GitHub via un `git push`.",
      18.4,
      14,
    ),
  ];
}