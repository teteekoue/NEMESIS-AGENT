#!/usr/bin/env python3
"""
Schema centralise de tous les outils disponibles pour NEMESIS.
Ce module definit la structure JSON attendue pour chaque outil,
remplacant l'ancien systeme de parsing XML <ACTION>.
"""

from typing import Dict, List, Any

# ============================================================
# DEFINITION DES OUTILS
# ============================================================
# Chaque outil a :
#   - name: identifiant unique
#   - description: courte description
#   - parameters: dict des parametres attendus avec leur description
#   - handler_method: nom de la methode dans ActionExecutor
# ============================================================

TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "name": 'bash',
        "description": 'Exécute une commande shell (synchrone ou asynchrone)',
        "parameters": {
            'command': 'str - la commande shell a executer',
            'mode': "str - 'synchrone' (defaut) ou 'asynchrone'",
            'timeout': 'int - timeout en SECONDES (min 10, max 604800, defaut 600)',
            'timeout_ms': 'int - alias de timeout, en millisecondes',
            'workdir': 'str - repertoire d execution (relatif ou absolu). Defaut : repertoire courant persistant',
            'cwd': 'str - alias de workdir',
            'background': 'bool - alias de mode=asynchrone',
        },
        "handler_method": 'execute_bash',
    },
    {
        "name": 'write',
        "description": 'Crée ou écrase un fichier complet',
        "parameters": {
            'path': 'str - chemin du fichier (relatif ou absolu)',
            'file_path': 'str - alias de path',
            'filename': 'str - alias de path',
            'content': 'str - contenu complet du fichier',
        },
        "handler_method": 'execute_write',
    },
    {
        "name": 'write_file',
        "description": 'Crée ou écrase un fichier complet (nouveau format)',
        "parameters": {
            'file_path': 'str - chemin du fichier (relatif ou absolu)',
            'path': 'str - alias de file_path',
            'filename': 'str - alias de file_path',
            'content': 'str - contenu complet du fichier',
        },
        "handler_method": 'execute_write_file',
    },
    {
        "name": 'append',
        "description": (
            "Ajoute UN bloc de contenu à la fin d'un fichier (crée le fichier s'il "
            "n'existe pas). Pour un long fichier : write_file pour le premier bloc, "
            "puis un appel append par bloc suivant, chacun sous 4000 caractères de JSON."
        ),
        "parameters": {
            'path': 'str - chemin du fichier',
            'file_path': 'str - alias de path',
            'filename': 'str - alias de path',
            'content': 'str - UNIQUEMENT le bloc suivant a ajouter',
        },
        "handler_method": 'execute_append',
    },
    {
        "name": 'append_file',
        "description": "Alias de append : ajoute un bloc à la fin d'un fichier.",
        "parameters": {
            'path': 'str - chemin du fichier',
            'file_path': 'str - alias de path',
            'content': 'str - bloc a ajouter',
        },
        "handler_method": 'execute_append',
    },
    {
        "name": 'replace',
        "description": 'Modifie un ou plusieurs blocs dans un fichier (search & replace)',
        "parameters": {'path': 'str - chemin du fichier', 'blocks': "list[dict] - liste de {'search': str, 'replace': str}"},
        "handler_method": 'execute_replace',
    },
    {
        "name": 'edit',
        "description": 'Replace an exact string in a file (formerly search_replace)',
        "parameters": {'file_path': 'str - path of the file to edit', 'old_string': 'str - exact text to find and replace', 'new_string': 'str - replacement text', 'replace_all': 'bool - replace every occurrence (optional, default false)'},
        "handler_method": 'execute_edit',
    },
    {
        "name": 'search_replace',
        "description": 'Legacy alias for edit',
        "parameters": {'file_path': 'str - path of the file to edit', 'old_string': 'str - exact text to find and replace', 'new_string': 'str - replacement text', 'replace_all': 'bool - replace every occurrence (optional, default false)'},
        "handler_method": 'execute_edit',
    },
    {
        "name": 'read',
        "description": "Lit un ou plusieurs fichiers (max 10). Retourne le contenu texte avec préfixe LINE_NUMBER→. Les images (PNG/JPEG/GIF/WebP/BMP/TIFF) sont transmises au modèle en pièce jointe multimodale NemApi, jamais en texte ni en base64.",
        "parameters": {'path': "str - chemin d'un seul fichier (optionnel si paths/files fourni)", 'paths': 'list[str] - liste de chemins (max 10, optionnel)', 'files': 'list[str] - alias de paths (max 10, optionnel)', 'offset': 'int - ligne de départ 1-based (optionnel, appliqué à chaque fichier)', 'limit': 'int - nombre max de lignes par fichier (optionnel)'},
        "handler_method": 'execute_read',
    },
    {
        "name": 'read_file',
        "description": 'Lit un ou plusieurs fichiers (max 10). Format unifié. Retourne le contenu avec préfixe LINE_NUMBER→. Support offset/limit.',
        "parameters": {'path': "str - chemin d'un seul fichier (optionnel si paths/files fourni)", 'paths': 'list[str] - liste de chemins (max 10, optionnel)', 'files': 'list[str] - alias de paths (max 10, optionnel)', 'offset': 'int - ligne de départ 1-based (optionnel)', 'limit': 'int - nombre max de lignes par fichier (optionnel)'},
        "handler_method": 'execute_read_file',
    },
    {
        "name": 'list_dir',
        "description": "Explore la structure d'un dossier",
        "parameters": {'path': 'str - chemin du dossier'},
        "handler_method": 'execute_list_dir',
    },
    {
        "name": 'list-dir',
        "description": "Explore la structure d'un dossier (format tirets)",
        "parameters": {'path': 'str - chemin du dossier'},
        "handler_method": 'execute_list_dir',
    },
    {
        "name": 'validate',
        "description": "Vérifie la syntaxe d'un fichier Python ou Shell",
        "parameters": {'path': 'str - chemin du fichier'},
        "handler_method": 'validate_code',
    },
    {
        "name": 'update_tracker',
        "description": 'Met à jour le suivi de tâches',
        "parameters": {'project': 'str - dossier du projet', 'task': 'str - nom de la tâche', 'status': "str - 'todo', 'in_progress', ou 'done'"},
        "handler_method": 'execute_update_tracker',
    },
    {
        "name": 'status',
        "description": "Vérifie l'état d'un processus asynchrone",
        "parameters": {'pid': 'int - PID du processus'},
        "handler_method": 'check_process',
    },
    {
        "name": 'kill_process',
        "description": 'Arrête un processus asynchrone',
        "parameters": {'pid': 'int - PID du processus'},
        "handler_method": 'kill_process',
    },
    {
        "name": 'stop_all',
        "description": 'Arrête tous les processus asynchrones',
        "parameters": {},
        "handler_method": 'stop_all_processes',
    },
    {
        "name": 'cleanup_logs',
        "description": 'Nettoie les fichiers de log des processus terminés',
        "parameters": {},
        "handler_method": 'cleanup_logs',
    },
    {
        "name": 'agent_status',
        "description": "Statut d'une tâche A2A (task_id) ou de tous les jobs/agents. Indique si le rapport task_<id>.md existe.",
        "parameters": {'task_id': 'str - id de la tâche (optionnel)', 'agent': "str - nom d'agent (optionnel)"},
        "handler_method": 'execute_agent_status',
    },
    {
        "name": 'mcp_list',
        "description": 'Liste les serveurs MCP configurés',
        "parameters": {},
        "handler_method": 'execute_mcp_list',
    },
    {
        "name": 'mcp_tools_list',
        "description": "Découvre les outils d'un serveur MCP",
        "parameters": {'server': 'str - nom du serveur MCP'},
        "handler_method": 'execute_mcp_tools_list',
    },
    {
        "name": 'mcp_call',
        "description": 'Appelle un outil sur un serveur MCP',
        "parameters": {'server': 'str - nom du serveur MCP', 'tool': "str - nom de l'outil", 'arguments': "dict - arguments de l'outil"},
        "handler_method": 'execute_mcp_call',
    },
    {
        "name": 'web_search',
        "description": 'Effectue une recherche sur Internet',
        "parameters": {'query': 'str - la requête de recherche'},
        "handler_method": 'execute_web_search',
    },
    {
        "name": 'skills_list',
        "description": 'Liste les skills installés dans tools_library/',
        "parameters": {},
        "handler_method": 'list_skills',
    },
    {
        "name": 'grep',
        "description": 'Recherche des motifs dans les fichiers',
        "parameters": {'pattern': 'str - motif regex a rechercher', 'path': 'str - chemin de recherche (defaut: .)', 'include': 'str - pattern pour filter les fichiers (optionnel)', 'case_insensitive': 'bool - recherche insensible a la casse (optionnel)'},
        "handler_method": 'execute_grep',
    },
    {
        "name": 'web_fetch',
        "description": "Récupère le contenu d'une URL",
        "parameters": {'url': 'str - URL a récupérer', 'format': 'str - format de sortie (markdown, html, text) - defaut: markdown'},
        "handler_method": 'execute_web_fetch',
    },
    {
        "name": 'web-fetch',
        "description": "Récupère le contenu d'une URL (format tirets)",
        "parameters": {'url': 'str - URL a récupérer', 'format': 'str - format de sortie (markdown, html, text) - defaut: markdown'},
        "handler_method": 'execute_web_fetch',
    },
    {
        "name": 'delete_file',
        "description": 'Supprime un fichier',
        "parameters": {'target_file': 'str - chemin du fichier a supprimer'},
        "handler_method": 'execute_delete_file',
    },
    {
        "name": 'delete-file',
        "description": 'Supprime un fichier (format tirets)',
        "parameters": {'target_file': 'str - chemin du fichier a supprimer'},
        "handler_method": 'execute_delete_file',
    },
    {
        "name": 'get_task_output',
        "description": "Récupère la sortie d'une tâche asynchrone",
        "parameters": {'task_id': 'str - ID de la tâche'},
        "handler_method": 'execute_get_task_output',
    },
    {
        "name": 'get-task-output',
        "description": "Récupère la sortie d'une tâche asynchrone (format tirets)",
        "parameters": {'task_id': 'str - ID de la tâche'},
        "handler_method": 'execute_get_task_output',
    },
    {
        "name": 'kill_task',
        "description": 'Arrête une tâche asynchrone',
        "parameters": {'task_id': 'str - ID de la tâche a arrêter'},
        "handler_method": 'execute_kill_task',
    },
    {
        "name": 'kill-task',
        "description": 'Arrête une tâche asynchrone (format tirets)',
        "parameters": {'task_id': 'str - ID de la tâche a arrêter'},
        "handler_method": 'execute_kill_task',
    },
    {
        "name": 'list_agents',
        "description": 'Liste les agents subordonnés disponibles (nouveau format)',
        "parameters": {},
        "handler_method": 'execute_list_agents',
    },
    {
        "name": 'list-agents',
        "description": 'Liste les agents subordonnés disponibles (format tirets)',
        "parameters": {},
        "handler_method": 'execute_list_agents',
    },
    {
        "name": 'delegate_task',
        "description": 'Délègue une tâche à un sous-agent A2A en arrière-plan (non-bloquant). Retourne immédiatement task_id. Rapport final: a2a_reports/task_<id>.md',
        "parameters": {'agent': "str - nom de l'agent", 'instruction': 'str - description détaillée de la tâche'},
        "handler_method": 'execute_delegate_task',
    },
    {
        "name": 'delegate-task',
        "description": 'Alias de delegate_task (non-bloquant)',
        "parameters": {'agent': "str - nom de l'agent", 'instruction': 'str - description détaillée de la tâche'},
        "handler_method": 'execute_delegate_task',
    },
    {
        "name": 'check_reports',
        "description": 'Liste les jobs A2A et les rapports task_*.md dans a2a_reports/',
        "parameters": {},
        "handler_method": 'execute_check_reports',
    },
    {
        "name": 'check-reports',
        "description": 'Vérifie les rapports de tâches terminées (format tirets)',
        "parameters": {},
        "handler_method": 'execute_check_reports',
    },
    {
        "name": 'glob',
        "description": 'Find files matching a glob pattern',
        "parameters": {'pattern': 'str - glob pattern e.g. **/*.py', 'path': 'str - optional root path', 'max_results': 'int - max results (default 200)'},
        "handler_method": 'execute_glob',
    },
    {
        "name": 'git',
        "description": 'Git status, diff, log, or branch',
        "parameters": {'action': 'str - status | diff | log | branch', 'path': 'str - optional path for diff', 'limit': 'int - max commits for log'},
        "handler_method": 'execute_git',
    },
    {
        "name": 'todo',
        "description": 'Manage structured todo list for multi-step work',
        "parameters": {'action': 'str - list | add | update | clear', 'content': 'str - todo text', 'items': 'list[str] - multiple todos', 'id': 'str - todo id for update', 'status': 'str - pending|in_progress|completed|cancelled', 'force': 'bool - clear all when true'},
        "handler_method": 'execute_todo',
    },
    {
        "name": 'apply_patch',
        "description": 'Apply a unified diff patch (chemins relatifs resolus depuis workdir, chemins absolus acceptes)',
        "parameters": {
            'patch': 'str - unified diff text',
            'diff': 'str - alias de patch',
            'dry_run': 'bool - validate only',
            'workdir': 'str - repertoire de resolution des chemins relatifs (defaut : repertoire courant persistant)',
            'cwd': 'str - alias de workdir',
        },
        "handler_method": 'execute_apply_patch',
    },
]

# ============================================================
# FONCTIONS UTILITAIRES
# ============================================================

def get_tool_by_name(name: str) -> Dict[str, Any] | None:
    """Retourne la definition d'un outil par son nom."""
    for tool in TOOLS_SCHEMA:
        if tool["name"] == name:
            return tool
    return None

def get_all_tool_names() -> List[str]:
    """Retourne la liste de tous les noms d'outils disponibles."""
    return [t["name"] for t in TOOLS_SCHEMA]

def get_tool_handler_method(name: str) -> str | None:
    """Retourne le nom de la methode handler pour un outil donne."""
    tool = get_tool_by_name(name)
    return tool["handler_method"] if tool else None

def validate_tool_call(tool_name: str, parameters: Dict[str, Any]) -> tuple[bool, str]:
    """Validate tool call. Only enforce clearly required params (not optional)."""
    tool = get_tool_by_name(tool_name)
    if tool is None:
        return False, f"Outil inconnu: {tool_name}. Outils disponibles: {', '.join(get_all_tool_names())}"
    parameters = parameters or {}
    # Strict required only for a few critical tools
    required_map = {
        "bash": ["command"],
        "write_file": ["content"],
        "write": ["content"],
        "append": ["content"],
        "append_file": ["content"],
        "edit": ["old_string", "new_string"],
        "search_replace": ["old_string", "new_string"],
        "grep": ["pattern"],
        "glob": ["pattern"],
        "web_search": ["query"],
        "web_fetch": ["url"],
        "delegate_task": ["agent", "instruction"],
        "delegate-task": ["agent", "instruction"],
    }
    path_aliases = ("file_path", "path", "filename", "file", "target_file")
    needs_path = {
        "write", "write_file", "append", "append_file", "replace", "edit", "search_replace",
        "read", "read_file", "delete_file",
    }
    if tool_name in needs_path and not any(parameters.get(alias) for alias in path_aliases):
        if not isinstance(parameters.get("files"), list) and not isinstance(parameters.get("paths"), list):
            return (
                False,
                f"Parametre manquant: un chemin est requis pour '{tool_name}' "
                f"(file_path, path ou filename)",
            )
    for param in required_map.get(tool_name, []):
        if param not in parameters:
            # alternate names
            alts = {
                "content": [],
                "agent": [],
                "instruction": [],
            }
            if param not in parameters:
                return False, f"Parametre manquant: '{param}' pour l'outil '{tool_name}'"
    return True, ""

print(f"Tools schema charge: {len(TOOLS_SCHEMA)} outils definis.")
