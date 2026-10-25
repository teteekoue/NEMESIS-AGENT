"""Unified tool registry — registers all NEMESIS tools with their handlers."""

import os
import queue
import re
from typing import Optional, Any
import yaml

from .paths import resolve_target_path
from .tool_kind import ToolKind, ToolNamespace, ToolDefinition
from .tool_registry import ToolRegistry
from .template_renderer import TemplateRenderer
from .system_prompt import SYSTEM_PROMPT_TEMPLATE

from .tools.read_file import read_file, DESCRIPTION_FULL as READ_DESC
from .tools.search_replace import search_replace, DESCRIPTION_FULL as EDIT_DESC
from .tools.bash import run_bash, get_task_output, kill_task, list_background_tasks, DESCRIPTION_FULL as BASH_DESC
from .tools.grep import grep, DESCRIPTION_FULL as GREP_DESC
from .tools.list_dir import list_dir, DESCRIPTION_FULL as LIST_DIR_DESC
from .tools.web_search import web_search, DESCRIPTION_FULL as WEB_SEARCH_DESC
from .tools.web_fetch import web_fetch, DESCRIPTION_FULL as WEB_FETCH_DESC
from .tools.glob_files import glob_files, DESCRIPTION_FULL as GLOB_DESC
from .tools.git_info import git_info, DESCRIPTION_FULL as GIT_DESC
from .tools.todo import todo, DESCRIPTION_FULL as TODO_DESC
from .tools.apply_patch import apply_patch, DESCRIPTION_FULL as PATCH_DESC
from .memory_store import (
    add_block,
    clear_all as memory_clear_all,
    delete_block,
    build_memory_message,
    load_blocks,
    load_prefs,
    search_blocks,
    upsert_block,
    get_block,
    set_enabled as memory_set_enabled,
)


def _delegate_subagent_tasks(fleet, tasks):
    result = fleet.delegate(tasks)
    if result.get("success"):
        lines = [f"{result.get('queued', 0)} tâche(s) lancée(s) :"]
        lines.extend(
            f"- {task['label']} — ID `{task['task_id']}` — {task['status']}"
            for task in result.get("tasks", [])
        )
        lines.append("Utilisez subagent_status avec chaque ID pour lire les rapports.")
        result["stdout"] = "\n".join(lines)
    else:
        result["stdout"] = str(result.get("error") or "Échec du lancement des sous-agents.")
    return result


def _format_subagent_status(fleet, task_id=""):
    result = fleet.status(task_id)
    tasks = result.get("tasks", [])
    if not tasks:
        result["stdout"] = result.get("message") or "Aucune tâche de sous-agent enregistrée."
        return result

    lines = [f"État des sous-agents ({len(tasks)} tâche(s)) :"]
    for task in tasks:
        lines.append(
            f"- {task['label']} — ID `{task['task_id']}` — {task['status']}"
        )
        metrics = []
        if task.get("model"):
            metrics.append(f"modèle {task['model']}")
        if task.get("duration_seconds") is not None:
            metrics.append(f"{task['duration_seconds']} s")
        if task.get("retry_count"):
            metrics.append(f"{task['retry_count']} reprise(s)")
        if task.get("credential_failovers"):
            metrics.append(f"{task['credential_failovers']} bascule(s) de clé")
        if task.get("api_requests") is not None:
            metrics.append(f"{task['api_requests']} requête(s) API")
        if task.get("prompt_tokens") or task.get("completion_tokens"):
            metrics.append(
                f"{task.get('prompt_tokens', 0)} tokens entrée / "
                f"{task.get('completion_tokens', 0)} sortie"
            )
        if metrics:
            lines.append(f"  Métriques : {', '.join(metrics)}")
        if task.get("last_retry_reason"):
            lines.append(f"  Dernière reprise : {task['last_retry_reason']}")
        if task.get("error"):
            lines.append(f"  Erreur : {task['error']}")
        for step in task.get("partial_progress", []):
            lines.append(
                f"  Action partielle ({step['tool']}, "
                f"{'OK' if step['success'] else 'échec'}) : {step['summary']}"
            )
        report = task.get("result", task.get("result_preview"))
        if report:
            lines.append(f"  Rapport :\n{report}")
    result["stdout"] = "\n".join(lines)
    return result


def create_registry(workspace_dir: str, subagent_fleet=None) -> ToolRegistry:
    """Create and finalize a ToolRegistry with all NEMESIS tools."""
    registry = ToolRegistry()

    _ws = workspace_dir

    registry.register(
        name="read_file",
        kind=ToolKind.READ,
        handler=lambda path=None, paths=None, offset=None, limit=None, file_path=None, filename=None, **kwargs: read_file(
            _ws,
            path=resolve_target_path(path, file_path, filename, kwargs.get("file")),
            paths=paths,
            offset=offset,
            limit=limit,
        ),
        description=READ_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to a single file to read (relative or absolute).",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths to read in batch (relative or absolute).",
                },
                "file_path": {
                    "type": "string",
                    "description": "Alias for 'path'.",
                },
                "filename": {
                    "type": "string",
                    "description": "Alias for 'path'.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed). Applies to each file.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read per file.",
                },
            },
            "anyOf": [
                {"required": ["path"]},
                {"required": ["paths"]},
                {"required": ["file_path"]},
                {"required": ["filename"]},
            ],
        },
        param_names={"path": "path", "offset": "offset", "limit": "limit"},
    )

    registry.register(
        name="edit",
        kind=ToolKind.EDIT,
        handler=lambda file_path=None, old_string=None, new_string=None, replace_all=False, path=None, filename=None, **kwargs: search_replace(
            resolve_target_path(file_path, path, filename, kwargs.get('file')),
            old_string,
            new_string,
            _ws,
            replace_all
        ),
        description=EDIT_DESC + " Accepts 'path' as an alias of 'file_path'; any absolute path is allowed.",
        params_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to modify (relative or absolute).",
                },
                "path": {
                    "type": "string",
                    "description": "The path to the file to modify (alias).",
                },
                "filename": {
                    "type": "string",
                    "description": "The path to the file to modify (alias).",
                },
                "old_string": {
                    "type": "string",
                    "description": "The text to replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "The text to replace it with (must be different from ${{ params.edit.old_string }}).",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences of ${{ params.edit.old_string }} (default false).",
                },
            },
            "required": ["old_string", "new_string"],
            "anyOf": [
                {"required": ["file_path"]},
                {"required": ["path"]},
                {"required": ["filename"]},
            ],
        },
        param_names={
            "old_string": "old_string",
            "new_string": "new_string",
            "replace_all": "replace_all",
        },
    )

    registry.register(
        name="write_file",
        kind=ToolKind.WRITE,
        handler=lambda file_path=None, content=None, path=None, filename=None, **kwargs: _write_file(
            resolve_target_path(file_path, path, filename, kwargs.get('file')),
            content,
            _ws
        ),
        description=(
            "Write content to a file (creates parent directories and overwrites). "
            "The path is honored exactly: relative paths resolve from the current shell "
            "directory and any absolute path anywhere on the machine is accepted. "
            "Accepts file_path, path or filename."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path of the file to write (relative or absolute).",
                },
                "path": {
                    "type": "string",
                    "description": "Path of the file to write (alias).",
                },
                "filename": {
                    "type": "string",
                    "description": "Path of the file to write (alias).",
                },
                "content": {
                    "type": "string",
                    "description": "Full content to write to the file.",
                },
            },
            "required": ["content"],
            "anyOf": [
                {"required": ["file_path"]},
                {"required": ["path"]},
                {"required": ["filename"]},
            ],
        },
    )

    registry.register(
        name="bash",
        kind=ToolKind.EXECUTE,
        handler=lambda command, description="", timeout=None, is_background=False, workdir=None, timeout_ms=None, cwd=None, background=None, **kwargs: run_bash(
            _make_bash_input(
                command,
                description,
                timeout if timeout is not None else timeout_ms,
                is_background if background is None else bool(background),
                workdir or cwd,
                timeout_is_ms=timeout is None and timeout_ms is not None,
            )
        ),
        description=BASH_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to run.",
                },
                "description": {
                    "type": "string",
                    "description": "One sentence explaining why this command is needed.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in SECONDS. 10-30s for quick inspection, longer for builds/tests/installs. Minimum 10, default 600, up to one week. Ignored for is_background=true.",
                },
                "is_background": {
                    "type": "boolean",
                    "description": "Set to true for long-running commands (servers, builds).",
                },
                "workdir": {
                    "type": "string",
                    "description": "Directory to run the command in (relative or absolute). Defaults to the persistent shell directory.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Alias for workdir.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Alias for timeout, expressed in MILLISECONDS.",
                },
                "background": {
                    "type": "boolean",
                    "description": "Alias for is_background.",
                },
            },
            "required": ["command"],
        },
    )

    registry.register(
        name="get_task_output",
        kind=ToolKind.BACKGROUND_TASK_ACTION,
        handler=lambda task_id, timeout_ms=None: get_task_output(task_id, timeout_ms),
        description="Get output and status from a background task.",
        params_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID returned by a background command.",
                },
                "timeout_ms": {
                    "type": "integer",
                    "description": "Optional timeout in milliseconds to wait for completion.",
                },
            },
            "required": ["task_id"],
        },
    )

    registry.register(
        name="kill_task",
        kind=ToolKind.KILL_TASK_ACTION,
        handler=lambda task_id: kill_task(task_id),
        description="Kill a running background task by its task ID.",
        params_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task ID to kill.",
                },
            },
            "required": ["task_id"],
        },
    )

    registry.register(
        name="grep",
        kind=ToolKind.SEARCH,
        handler=lambda pattern, include=None, path=None, case_insensitive=False: grep(
            _make_grep_input(pattern, include, path, case_insensitive)
        ),
        description=GREP_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for.",
                },
                "include": {
                    "type": "string",
                    "description": "File pattern to filter (e.g. '*.py').",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in (relative or absolute). Defaults to the current shell directory.",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default false).",
                },
            },
            "required": ["pattern"],
        },
    )

    registry.register(
        name="list_dir",
        kind=ToolKind.LIST_DIR,
        handler=lambda path=None, depth=2: list_dir(
            _make_listdir_input(path, depth)
        ),
        description=LIST_DIR_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list (relative or absolute). Defaults to the current shell directory.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Recursion depth (default 2).",
                },
            },
        },
    )

    registry.register(
        name="web_search",
        kind=ToolKind.WEB_SEARCH,
        handler=lambda query, max_results=5: web_search(
            _make_web_search_input(query, max_results)
        ),
        description=WEB_SEARCH_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results (default 5, max 10).",
                },
            },
            "required": ["query"],
        },
    )

    registry.register(
        name="web_fetch",
        kind=ToolKind.WEB_FETCH,
        handler=lambda url, format="markdown": web_fetch(_make_web_fetch_input(url, format)),
        description=WEB_FETCH_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch content from.",
                },
                "format": {
                    "type": "string",
                    "description": "Output format: markdown (default), text, or html.",
                },
            },
            "required": ["url"],
        },
    )


    registry.register(
        name="glob",
        kind=ToolKind.SEARCH,
        handler=lambda pattern, path=None, max_results=200: glob_files(pattern, None, path=path, max_results=max_results),
        description=GLOB_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                "path": {"type": "string", "description": "Root directory to search (default: current shell directory)"},
                "max_results": {"type": "integer", "description": "Max files to return (default 200)"},
            },
            "required": ["pattern"],
        },
    )

    registry.register(
        name="git",
        kind=ToolKind.EXECUTE,
        handler=lambda action="status", path=None, limit=20: git_info(action, None, path=path, limit=limit),
        description=GIT_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "status | diff | log | branch"},
                "path": {"type": "string", "description": "Optional path filter for diff"},
                "limit": {"type": "integer", "description": "Max commits for log (default 20)"},
            },
            "required": ["action"],
        },
    )

    registry.register(
        name="todo",
        kind=ToolKind.PLAN,
        handler=lambda action="list", content=None, items=None, id=None, status=None, force=False: todo(
            action, None, content=content, items=items, id=id, status=status, force=force
        ),
        description=TODO_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list | add | update | clear"},
                "content": {"type": "string", "description": "Todo text (for add/update)"},
                "items": {"type": "array", "items": {"type": "string"}, "description": "Multiple todos to add"},
                "id": {"type": "string", "description": "Todo id (for update)"},
                "status": {"type": "string", "description": "pending | in_progress | completed | cancelled"},
                "force": {"type": "boolean", "description": "For clear: remove all items"},
            },
            "required": ["action"],
        },
    )

    registry.register(
        name="apply_patch",
        kind=ToolKind.EDIT,
        handler=lambda patch=None, dry_run=False, workdir=None, cwd=None, diff=None, **kwargs: apply_patch(
            patch if patch is not None else diff,
            None,
            dry_run=dry_run,
            workdir=workdir or cwd,
        ),
        description=PATCH_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Unified diff text"},
                "dry_run": {"type": "boolean", "description": "Validate only, do not write"},
                "workdir": {
                    "type": "string",
                    "description": "Directory the relative paths in the patch resolve from. Defaults to the persistent shell directory.",
                },
                "cwd": {"type": "string", "description": "Alias for workdir."},
                "diff": {"type": "string", "description": "Alias for patch."},
            },
            "anyOf": [{"required": ["patch"]}, {"required": ["diff"]}],
        },
    )

    registry.register(
        name="delete_file",
        kind=ToolKind.DELETE,
        handler=lambda target_file=None, path=None, file_path=None, **kwargs: _delete_file(
            resolve_target_path(target_file, path, file_path, kwargs.get('file')),
            _ws
        ),
        description="Delete a file or directory at the specified path anywhere on the machine. Accepts target_file, path or file_path.",
        params_schema={
            "type": "object",
            "properties": {
                "target_file": {
                    "type": "string",
                    "description": "The path to the file to delete.",
                },
                "path": {
                    "type": "string",
                    "description": "The path to the file to delete (alias).",
                },
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to delete (alias).",
                },
            },
            "anyOf": [
                {"required": ["target_file"]},
                {"required": ["path"]},
                {"required": ["file_path"]}
            ],
        },
    )

    # --- Outils MCP (appels JSON du modele, pas les commandes / de l'utilisateur) ---
    registry.register(
        name="mcp_list",
        kind=ToolKind.OTHER,
        namespace=ToolNamespace.MCP,
        handler=lambda: _mcp_list(),
        description="Liste les serveurs MCP configures dans mcp_config.yaml.",
        params_schema={
            "type": "object",
            "properties": {},
        },
    )

    registry.register(
        name="mcp_tools_list",
        kind=ToolKind.SEARCH_TOOL,
        namespace=ToolNamespace.MCP,
        handler=lambda server: _mcp_tools_list(server),
        description="Decouvre les outils exposes par un serveur MCP.",
        params_schema={
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Nom du serveur MCP (ex: calculator, github).",
                },
            },
            "required": ["server"],
        },
    )

    registry.register(
        name="mcp_call",
        kind=ToolKind.USE_TOOL,
        namespace=ToolNamespace.MCP,
        handler=lambda server, tool, arguments=None: _mcp_call(server, tool, arguments),
        description="Appelle un outil sur un serveur MCP configure.",
        params_schema={
            "type": "object",
            "properties": {
                "server": {
                    "type": "string",
                    "description": "Nom du serveur MCP (ex: calculator, github).",
                },
                "tool": {
                    "type": "string",
                    "description": "Nom de l'outil sur le serveur MCP.",
                },
                "arguments": {
                    "type": "object",
                    "description": "Arguments JSON a passer a l'outil MCP.",
                },
            },
            "required": ["server", "tool"],
        },
    )

    registry.register(
        name="skills_list",
        kind=ToolKind.SKILL,
        handler=lambda: _skills_list(),
        description="List installed skills from tools_library/.",
        params_schema={"type": "object", "properties": {}},
    )

    registry.register(
        name="memory_add",
        kind=ToolKind.OTHER,
        handler=lambda title, content, tags=None: _memory_add(title, content, tags),
        description=(
            "Add a new memory mini-block about the user (preferences, identity, "
            "environment, recurring instructions). Use for stable facts useful in "
            "future sessions. Returns the stored block with its id."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short label for the fact."},
                "content": {"type": "string", "description": "The fact to remember, self-contained."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to group related memories.",
                },
            },
            "required": ["title", "content"],
        },
        risk="medium",
    )

    registry.register(
        name="memory_update",
        kind=ToolKind.OTHER,
        handler=lambda key, title, content, tags=None: _memory_upsert(key, title, content, tags),
        description=(
            "Create or update a memory block identified by a stable 'key'. Use the "
            "same key to update a fact you already stored instead of duplicating it."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Stable identifier of the fact."},
                "title": {"type": "string", "description": "Short label."},
                "content": {"type": "string", "description": "The fact to remember."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags.",
                },
            },
            "required": ["key", "title", "content"],
        },
        risk="medium",
    )

    registry.register(
        name="memory_search",
        kind=ToolKind.MEMORY_SEARCH,
        handler=lambda query="", tags=None, limit=10: _memory_search(query, tags, limit),
        description=(
            "Search the persistent memory for blocks matching a query or tags. "
            "Returns matching blocks with their ids."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text query. Empty lists recent blocks."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to filter.",
                },
                "limit": {"type": "integer", "description": "Maximum blocks to return (default 10)."},
            },
        },
    )

    registry.register(
        name="memory_get",
        kind=ToolKind.MEMORY_GET,
        handler=lambda block_id="": _memory_get(block_id),
        description="Read a single memory block by id, or list all blocks when id is empty.",
        params_schema={
            "type": "object",
            "properties": {
                "block_id": {"type": "string", "description": "Memory block id (optional)."},
            },
        },
    )

    registry.register(
        name="memory_delete",
        kind=ToolKind.DELETE,
        handler=lambda block_id: _memory_delete(block_id),
        description="Delete a single memory block by its id.",
        params_schema={
            "type": "object",
            "properties": {
                "block_id": {"type": "string", "description": "Memory block id to delete."},
            },
            "required": ["block_id"],
        },
        risk="high",
    )

    registry.register(
        name="memory_clear",
        kind=ToolKind.DELETE,
        handler=lambda: _memory_clear(),
        description="Delete ALL memory blocks. Irreversible; ask before using.",
        params_schema={"type": "object", "properties": {}},
        risk="high",
    )

    if subagent_fleet is not None:
        registry.register(
            name="delegate_tasks",
            kind=ToolKind.TASK,
            handler=lambda tasks: _delegate_subagent_tasks(subagent_fleet, tasks),
            description=(
                "Launch temporary NEMESIS sub-agents for independent tasks. Call this tool "
                "when delegation is requested. Each task must contain instruction (aliases: goal, "
                "prompt, description); optional fields are label, context and mode (standard/read_only). "
                "read_only blocks tools that can write or execute commands. Keep instructions "
                "self-contained and non-overlapping. Example: {tasks:[{instruction:'Inspect src/app.py', "
                "context:'Find startup validation', label:'Check startup'}]}. Returns task IDs immediately; "
                "poll subagent_status to read reports, errors, retry counts and token metrics."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "instruction": {
                                    "type": "string",
                                    "description": "A self-contained task with a clear expected outcome.",
                                },
                                "goal": {"type": "string", "description": "Alias for instruction."},
                                "prompt": {"type": "string", "description": "Alias for instruction."},
                                "description": {"type": "string", "description": "Alias for instruction."},
                                "context": {
                                    "type": "string",
                                    "description": "Optional bounded context, relevant files and expected result.",
                                },
                                "mode": {
                                    "type": "string",
                                    "enum": ["standard", "read_only"],
                                    "description": "Use read_only to forbid write and command-execution tools.",
                                },
                                "label": {
                                    "type": "string",
                                    "description": "Short task label shown in status reports.",
                                },
                            },
                        },
                        "description": "Batch of up to 8 independent tasks.",
                    },
                },
                "required": ["tasks"],
            },
        )
        registry.register(
            name="subagent_status",
            kind=ToolKind.MONITOR,
            handler=lambda task_id="": _format_subagent_status(subagent_fleet, task_id),
            description=(
                "List temporary sub-agent tasks or retrieve one task by ID. A task-specific "
                "query returns its complete report or error. Poll queued/running/retrying tasks until "
                "they finish; reports are retained in memory for a limited time."
            ),
            params_schema={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Optional task ID; omit to list retained tasks.",
                    },
                },
            },
        )

    return registry.finalize()


def build_system_prompt(registry: ToolRegistry) -> str:
    renderer = registry.get_renderer()
    prompt = renderer.render(SYSTEM_PROMPT_TEMPLATE)

    tools_lines = []
    for name, reg in registry:
        schema = reg.definition.params_schema or {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        params = ", ".join(
            f"{key}{'' if key in required else '?'}" for key in props
        )
        risk = getattr(reg, "risk", "medium")
        suffix = f" — params: {params}" if params else ""
        tools_lines.append(
            f"- **{name}** [{risk}]: {reg.definition.description[:180].split(chr(10))[0]}{suffix}"
        )

    tool_count = len(tools_lines)
    catalog = "\n".join(tools_lines) or "- (no tools registered)"
    catalog_block = (
        f"{tool_count} tools are registered. Exact parameter names are listed in\n"
        "parentheses; a trailing `?` means the parameter is optional.\n\n"
        + catalog
    )
    if "{{TOOLS_CATALOG}}" in prompt:
        prompt = prompt.replace("{{TOOLS_CATALOG}}", catalog_block)
    else:
        prompt += "\n\n## Available Tools\n\n" + catalog_block

    try:
        memory_message = build_memory_message()
    except Exception:
        memory_message = ""
    if memory_message:
        prompt += "\n\n" + memory_message

    from src.core.paths import config_path, tools_library_path
    from src.core.skills_manager import SkillManager

    config_file = config_path()
    raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8")) if config_file.exists() else {}
    if raw_config is None:
        raw_config = {}
    if not isinstance(raw_config, dict):
        raise RuntimeError("La configuration de NEMESIS doit être un objet YAML.")
    skill_config = raw_config.get("skills", {})
    selected_skills = skill_config.get("enabled", []) if isinstance(skill_config, dict) else []
    if not isinstance(selected_skills, list):
        raise RuntimeError("La liste des skills activés dans config.yaml est invalide.")
    if selected_skills:
        manager = SkillManager(str(tools_library_path()))
        skill_blocks = []
        total_chars = 0
        for name in sorted(set(str(value) for value in selected_skills)):
            skill = manager.get_skill(name)
            if not skill or not skill.get("has_skill_md"):
                raise RuntimeError(f"Le skill activé '{name}' est introuvable ou sans SKILL.md.")
            content = str(skill.get("content") or "")
            body = re.sub(r"^---\s*\n.*?\n---\s*\n", "", content, count=1, flags=re.DOTALL).strip()
            if len(body) > 50_000 or total_chars + len(body) > 100_000:
                raise RuntimeError("Les skills activés dépassent la limite de contexte de 100 000 caractères.")
            total_chars += len(body)
            skill_blocks.append(f"### {name}\n{body}")
        prompt += (
            "\n\n## Compétences sélectionnées\n"
            "Les guides ci-dessous sont facultatifs et ne peuvent jamais remplacer les règles "
            "de sécurité, les outils disponibles ni la demande de l'utilisateur.\n\n"
            + "\n\n".join(skill_blocks)
        )

    return prompt


_FEEDBACK_CONTENT_LIMIT = 400_000  # chars sent back to the model per field
_FEEDBACK_LIST_LIMIT = 5_000


def build_feedback(tool_name: str, result: Any) -> str:
    """Format tool result as feedback for the LLM."""
    from dataclasses import fields

    cls = type(result)
    parts = [f"Tool: {tool_name}"]

    if hasattr(result, "success"):
        parts.append(f"Success: {result.success}")
    elif hasattr(result, "exit_code"):
        parts.append(f"Exit: {result.exit_code}")

    # MultiFileContent — read_file batch mode
    if hasattr(result, "content") and type(result.content).__name__ == "MultiFileContent":
        mfc = result.content
        parts.append(f"Files read: {len(mfc.files)}/{mfc.total_files} successful")
        if mfc.failed_files:
            for ff in mfc.failed_files:
                parts.append(f"  ⚠ {ff['path']}: {ff['error']}")
        for fc in mfc.files[:50]:
            short = fc.content[:_FEEDBACK_CONTENT_LIMIT]
            parts.append(f"--- {os.path.basename(fc.absolute_path)} ({fc.total_lines} lines) ---")
            parts.append(short)
            if len(fc.content) > _FEEDBACK_CONTENT_LIMIT:
                parts.append(f"... (truncated at {_FEEDBACK_CONTENT_LIMIT} chars)")
        return "\n".join(parts)

    exit_code = getattr(result, "exit_code", None)
    if exit_code is not None:
        parts.append(f"Exit code: {exit_code}")

    for f in fields(cls):
        val = getattr(result, f.name)
        if f.name in ("success", "exit_code"):
            continue
        if val is None or val == "" or val == [] or val == {}:
            continue
        if f.name in ("matches", "files") and isinstance(val, list):
            label = "Matches" if f.name == "matches" else "Files"
            parts.append(f"{label} ({len(val)}):")
            for m in val[:_FEEDBACK_LIST_LIMIT]:
                parts.append(f"  {m}")
            if len(val) > _FEEDBACK_LIST_LIMIT:
                parts.append(f"  ... and {len(val) - _FEEDBACK_LIST_LIMIT} more")
        elif f.name == "results" and isinstance(val, list):
            for r in val:
                if isinstance(r, dict):
                    parts.append(f"  - {r.get('title', '')}: {r.get('url', '')}")
                    if r.get("snippet"):
                        parts.append(f"    {r['snippet'][:200]}")
        elif f.name in ("content", "output") and isinstance(val, str) and len(val) > _FEEDBACK_CONTENT_LIMIT:
            parts.append(f"{f.name}: {val[:_FEEDBACK_CONTENT_LIMIT]}")
            parts.append(f"... (truncated at {_FEEDBACK_CONTENT_LIMIT} chars)")
        elif f.name in ("content", "output", "message") and isinstance(val, str) and val:
            parts.append(f"{f.name}: {val}")
        elif f.name in ("content", "output") and hasattr(val, "__dataclass_fields__"):
            fc = val
            if hasattr(fc, "content") and fc.content:
                c = (
                    fc.content
                    if len(fc.content) <= _FEEDBACK_CONTENT_LIMIT
                    else fc.content[:_FEEDBACK_CONTENT_LIMIT] + "\n... (truncated)"
                )
                parts.append(f"content: {c}")
            if hasattr(fc, "total_lines"):
                parts.append(f"total_lines: {fc.total_lines}")
        elif f.name == "error" and val:
            parts.append(f"Error: {val}")
        elif f.name == "error_type" and val:
            parts.append(f"Error type: {val}")
        elif f.name == "edits" and val:
            parts.append(f"Edits: {len(val)} change(s)")
        elif f.name == "task_id" and val:
            parts.append(f"Task: {val}")

    return "\n".join(parts)


_APPEND_PREFIX = "__APPEND__:"


def format_tool_feedback(tool_name: str, result: Any, max_chars: int = 10_000_000) -> str:
    """Uniform model feedback for every surface (CLI, Web, Telegram).

    Always exposes the exit code, stderr and a signal/timeout marker so an empty
    stdout is never mistaken for a successful silent command.
    """
    if not isinstance(result, dict):
        return f"Tool: {tool_name}\nSuccess: {getattr(result, 'success', False)}\nOutput:\n{result}"

    output = result.get("stdout")
    if output is None or output == "":
        output = result.get("output")
    if output is None or output == "":
        output = result.get("content")
    output = "" if output is None else str(output)

    error = result.get("error") or result.get("stderr") or ""
    error = "" if error is None else str(error)
    exit_code = result.get("exit_code", result.get("returncode"))
    signal_name = result.get("signal")

    if len(output) > max_chars:
        output = output[:max_chars] + "\n\n[SYSTEM: output truncated]"

    if not output and not error:
        if exit_code is not None:
            output = f"[no output captured — exit code: {exit_code}]"
        elif signal_name:
            output = f"[no output captured — signal: {signal_name}]"
        else:
            output = "[no output captured]"

    lines = [f"Tool: {tool_name}", f"Success: {bool(result.get('success', False))}"]
    if exit_code is not None:
        lines.append(f"Exit code: {exit_code}")
    if signal_name:
        lines.append(f"Signal: {signal_name}")
    if output:
        lines.append(f"Output:\n{output}")
    if error and error not in output:
        lines.append(f"Error:\n{error}")
    return "\n".join(lines)


def _write_file(file_path: str, content: str, workspace_dir: Optional[str] = None) -> object:
    """Write content to the exact path requested, anywhere on the system."""
    from dataclasses import dataclass

    @dataclass
    class WriteResult:
        success: bool
        message: str
        error: Optional[str] = None
        path: Optional[str] = None

    from .paths import resolve_path

    raw = str(file_path if file_path is not None else "").strip()
    if not raw:
        return WriteResult(
            success=False,
            message="",
            error="A file path is required (file_path, path or filename).",
        )
    if content is None:
        return WriteResult(success=False, message="", error="The 'content' parameter is required.")

    text = content if isinstance(content, str) else str(content)
    append = text.startswith(_APPEND_PREFIX)
    if append:
        text = text[len(_APPEND_PREFIX):]

    resolved = resolve_path(raw)
    if os.path.isdir(resolved):
        return WriteResult(
            success=False,
            message="",
            error=f"Path is a directory, not a file: {resolved}",
            path=resolved,
        )

    try:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        with open(resolved, "a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)
    except OSError as e:
        return WriteResult(
            success=False,
            message="",
            error=f"Cannot write {resolved}: {e}",
            path=resolved,
        )

    action = "Content appended to" if append else "File written"
    return WriteResult(success=True, message=f"{action}: {resolved}", path=resolved)


def _delete_file(target_file: str, workspace_dir: Optional[str] = None) -> object:
    from dataclasses import dataclass

    @dataclass
    class DeleteResult:
        success: bool
        message: str
        error: Optional[str] = None
        path: Optional[str] = None

    from .paths import resolve_path

    raw = str(target_file if target_file is not None else "").strip()
    if not raw:
        return DeleteResult(
            success=False,
            message="",
            error="A file path is required (target_file, path or file_path).",
        )

    resolved = resolve_path(raw)
    if os.path.abspath(resolved) == os.path.abspath(os.sep):
        return DeleteResult(
            success=False,
            message="",
            error="Refusing to delete the filesystem root.",
            path=resolved,
        )
    if not os.path.exists(resolved):
        return DeleteResult(success=False, message="", error=f"File not found: {resolved}", path=resolved)

    try:
        if os.path.isdir(resolved):
            import shutil
            shutil.rmtree(resolved)
        else:
            os.remove(resolved)
        return DeleteResult(success=True, message=f"Deleted: {resolved}", path=resolved)
    except OSError as e:
        return DeleteResult(success=False, message="", error=str(e), path=resolved)


def _make_bash_input(
    command,
    description="",
    timeout=None,
    is_background=False,
    workdir=None,
    timeout_is_ms=False,
):
    from .tools.bash import BashInput, timeout_seconds_to_ms, _MAX_TIMEOUT_MS, _DEFAULT_TIMEOUT_MS
    if timeout_is_ms and timeout is not None:
        try:
            milliseconds = int(timeout)
        except (TypeError, ValueError):
            milliseconds = _DEFAULT_TIMEOUT_MS
        milliseconds = max(1000, min(_MAX_TIMEOUT_MS, milliseconds))
    else:
        milliseconds = timeout_seconds_to_ms(timeout)
    return BashInput(
        command=command,
        description=description or "",
        timeout_ms=milliseconds,
        is_background=is_background,
        workdir=workdir,
    )


def _make_grep_input(pattern, include=None, path=None, case_insensitive=False):
    from .tools.grep import GrepInput
    return GrepInput(pattern=pattern, include=include, path=path, case_insensitive=case_insensitive)


def _make_listdir_input(path=None, depth=2):
    from .tools.list_dir import ListDirInput
    return ListDirInput(path=path, depth=depth)


def _make_web_search_input(query, max_results=5):
    from .tools.web_search import WebSearchInput
    return WebSearchInput(query=query, max_results=max_results)


def _make_web_fetch_input(url, format="markdown"):
    from .tools.web_fetch import WebFetchInput
    return WebFetchInput(url=url, format=format)


def _mcp_manager():
    from .mcp_manager import MCPManager
    try:
        from .paths import mcp_config_path
        return MCPManager(str(mcp_config_path()))
    except Exception:
        return MCPManager()


def _mcp_list() -> dict:
    try:
        servers = _mcp_manager().list_servers()
    except Exception as e:
        return {"success": False, "stdout": f"Impossible de lire mcp_config.yaml: {e}"}
    if not servers:
        return {
            "success": True,
            "stdout": (
                "Aucun serveur MCP configure dans mcp_config.yaml.\n"
                "Ajoutez-en avec /mcp ou manuellement (command + env optionnel)."
            ),
        }
    lines = ["SERVEURS MCP CONFIGURES", "-" * 48]
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        cmd = cfg.get("command", "?")
        desc = cfg.get("description") or ""
        lines.append(f"  {name}")
        lines.append(f"    command: {cmd}")
        if desc:
            lines.append(f"    desc:    {desc}")
        env = cfg.get("env") or {}
        if env:
            # Do not print secret values
            lines.append(f"    env:     {', '.join(sorted(env.keys()))} (values hidden)")
    return {"success": True, "stdout": "\n".join(lines)}


def _mcp_tools_list(server: str) -> dict:
    import json
    server = (server or "").strip()
    if not server:
        return {"success": False, "stdout": "Parameter 'server' is required."}
    client = None
    try:
        client = _mcp_manager().get_client(server)
    except Exception as e:
        return {"success": False, "stdout": f"Impossible de demarrer le serveur MCP '{server}': {e}"}
    if not client:
        available = ", ".join(_mcp_manager().list_servers().keys()) or "(none)"
        return {
            "success": False,
            "stdout": f"Serveur MCP '{server}' introuvable. Disponibles: {available}",
        }
    try:
        tools = client.list_tools()
        if isinstance(tools, list):
            lines = [f"Outils MCP sur '{server}' ({len(tools)}):"]
            for t in tools:
                if isinstance(t, dict):
                    n = t.get("name", "?")
                    d = (t.get("description") or "").strip().split("\n")[0][:120]
                    lines.append(f"  - {n}: {d}" if d else f"  - {n}")
                else:
                    lines.append(f"  - {t}")
            # Also attach raw JSON for programmatic use
            lines.append("")
            lines.append(json.dumps(tools, indent=2, ensure_ascii=False)[:6000])
            return {"success": True, "stdout": "\n".join(lines)}
        return {"success": True, "stdout": json.dumps(tools, indent=2, ensure_ascii=False)}
    except TimeoutError as e:
        return {"success": False, "stdout": f"Serveur MCP '{server}' injoignable (timeout). {e}"}
    except Exception as e:
        return {"success": False, "stdout": f"Erreur liste outils MCP: {e}"}
    finally:
        _mcp_manager().release_client(client)


def _mcp_call(server: str, tool: str, arguments=None) -> dict:
    import json
    server = (server or "").strip()
    tool = (tool or "").strip()
    if not server:
        return {"success": False, "stdout": "Parameter 'server' is required."}
    if not tool:
        return {"success": False, "stdout": "Parameter 'tool' is required."}
    client = None
    try:
        client = _mcp_manager().get_client(server)
    except Exception as e:
        return {"success": False, "stdout": f"Impossible de demarrer le serveur MCP '{server}': {e}"}
    if not client:
        available = ", ".join(_mcp_manager().list_servers().keys()) or "(none)"
        return {
            "success": False,
            "stdout": f"Serveur MCP '{server}' introuvable. Disponibles: {available}",
        }
    try:
        if arguments is None:
            args: dict = {}
        elif isinstance(arguments, dict):
            args = arguments
        elif isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
                args = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {"success": False, "stdout": "arguments must be a JSON object"}
        else:
            args = {}
        result = client.call_tool(tool, args)
        # Prefer human-readable text content when present
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            texts = []
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    texts.append(str(item.get("text", "")))
            if texts:
                return {"success": True, "stdout": "\n".join(texts)}
        if isinstance(result, (dict, list)):
            out = json.dumps(result, indent=2, ensure_ascii=False)
        else:
            out = str(result)
        return {"success": True, "stdout": out}
    except TimeoutError as e:
        return {"success": False, "stdout": f"Serveur MCP '{server}' injoignable (timeout). {e}"}
    except Exception as e:
        return {"success": False, "stdout": f"Erreur MCP: {e}"}
    finally:
        _mcp_manager().release_client(client)


def _skills_list() -> dict:
    try:
        from src.core.skills_manager import SkillManager
        try:
            from src.core.paths import tools_library_path
            mgr = SkillManager(str(tools_library_path()))
        except Exception:
            mgr = SkillManager()
        skills = mgr.list_installed()
        if not skills:
            return {
                "success": True,
                "stdout": (
                    "No additional skills installed in tools_library/.\n"
                    "Install with /skills (URL git/zip or dossier local)."
                ),
            }
        lines = [f"INSTALLED SKILLS ({len(skills)})", "-" * 48]
        for s in skills:
            flag = "" if s.get("has_skill_md") else " [no SKILL.md]"
            lines.append(f"  {s.get('name', '?')}  v{s.get('version', '?')}{flag}")
            desc = (s.get("description") or "").strip()
            if desc and desc != "Aucune description disponible.":
                lines.append(f"    {desc[:160]}")
            if s.get("path"):
                lines.append(f"    path: {s['path']}")
        return {"success": True, "stdout": "\n".join(lines)}
    except Exception as e:
        return {"success": False, "stdout": f"skills_list error: {e}"}


def _memory_add(title: str, content: str, tags=None) -> dict:
    try:
        block = add_block(title, content, tags)
        return {
            "success": True,
            "stdout": f"Memoire ajoutee [{block['id']}]: {block.get('title', '')}",
            "block": block,
        }
    except Exception as e:
        return {"success": False, "stdout": f"memory_add error: {e}"}


def _memory_upsert(key: str, title: str, content: str, tags=None) -> dict:
    try:
        block = upsert_block(key, title, content, tags)
        return {
            "success": True,
            "stdout": f"Memoire mise a jour [{block['id']}] ({key}): {block.get('title', '')}",
            "block": block,
        }
    except Exception as e:
        return {"success": False, "stdout": f"memory_update error: {e}"}


def _memory_search(query: str = "", tags=None, limit: int = 10) -> dict:
    try:
        if not query and not tags:
            blocks = sorted(load_blocks(), key=lambda b: float(b.get("updated_at", 0.0) or 0.0), reverse=True)
            blocks = blocks[: max(1, int(limit) if limit else 10)]
        else:
            blocks = search_blocks(query, tags, limit)
        if not blocks:
            return {"success": True, "stdout": "Aucun bloc de memoire ne correspond.", "blocks": []}
        lines = [f"BLOCS MEMOIRE ({len(blocks)})", "-" * 48]
        for b in blocks:
            lines.append(f"[{b.get('id')}] {b.get('title') or b.get('key')}")
            lines.append(f"    {str(b.get('content', ''))[:400]}")
            if b.get("tags"):
                lines.append(f"    tags: {', '.join(b.get('tags') or [])}")
        return {"success": True, "stdout": "\n".join(lines), "blocks": blocks}
    except Exception as e:
        return {"success": False, "stdout": f"memory_search error: {e}"}


def _memory_get(block_id: str = "") -> dict:
    try:
        if not block_id:
            blocks = sorted(load_blocks(), key=lambda b: float(b.get("updated_at", 0.0) or 0.0), reverse=True)
            if not blocks:
                return {"success": True, "stdout": "Memoire vide.", "blocks": []}
            lines = [f"MEMOIRE ({len(blocks)} blocs)", "-" * 48]
            for b in blocks:
                lines.append(f"[{b.get('id')}] {b.get('title') or b.get('key')}: {str(b.get('content', ''))[:400]}")
            return {"success": True, "stdout": "\n".join(lines), "blocks": blocks}
        block = get_block(block_id)
        if block is None:
            return {"success": False, "stdout": f"Bloc introuvable: {block_id}"}
        return {"success": True, "stdout": str(block.get("content", "")), "block": block}
    except Exception as e:
        return {"success": False, "stdout": f"memory_get error: {e}"}


def _memory_delete(block_id: str) -> dict:
    try:
        ok = delete_block(block_id)
        if not ok:
            return {"success": False, "stdout": f"Bloc introuvable: {block_id}"}
        return {"success": True, "stdout": f"Bloc supprime: {block_id}"}
    except Exception as e:
        return {"success": False, "stdout": f"memory_delete error: {e}"}


def _memory_clear() -> dict:
    try:
        memory_clear_all()
        return {"success": True, "stdout": "Memoire videe (tous les blocs supprimes)."}
    except Exception as e:
        return {"success": False, "stdout": f"memory_clear error: {e}"}
