"""Unified tool registry — registers all NEMESIS tools with their handlers."""

import os
import queue
from typing import Optional, Any

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


def create_registry(workspace_dir: str) -> ToolRegistry:
    """Create and finalize a ToolRegistry with all NEMESIS tools."""
    registry = ToolRegistry()

    _ws = workspace_dir

    registry.register(
        name="read_file",
        kind=ToolKind.READ,
        handler=lambda path=None, paths=None, offset=None, limit=None: read_file(
            _ws, path=path, paths=paths, offset=offset, limit=limit
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
            ],
        },
        param_names={"path": "path", "offset": "offset", "limit": "limit"},
    )

    registry.register(
        name="edit",
        kind=ToolKind.EDIT,
        handler=lambda file_path=None, old_string=None, new_string=None, replace_all=False, path=None, **kwargs: search_replace(
            file_path or path or kwargs.get('file', ''),
            old_string,
            new_string,
            _ws,
            replace_all
        ),
        description=EDIT_DESC + " Accepte aussi 'path' comme alias de 'file_path'.",
        params_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The path to the file to modify.",
                },
                "path": {
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
            file_path or path or filename or kwargs.get('file', ''),
            content,
            _ws
        ),
        description="Write content to a file (creates or overwrites). Accepts: file_path, path, filename.",
        params_schema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to write.",
                },
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (alias).",
                },
                "filename": {
                    "type": "string",
                    "description": "Path to the file to write (alias).",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file.",
                },
            },
            "required": ["content"],
        },
    )

    registry.register(
        name="bash",
        kind=ToolKind.EXECUTE,
        handler=lambda command, description="", timeout=None, is_background=False, workdir=None: run_bash(
            _make_bash_input(command, description, timeout, is_background, workdir), _ws
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
                    "description": "Optional timeout in milliseconds. Default: 120000 (2 min). 0 = unbounded for background.",
                },
                "is_background": {
                    "type": "boolean",
                    "description": "Set to true for long-running commands (servers, builds).",
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for the command.",
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
            _make_grep_input(pattern, include, path, case_insensitive), _ws
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
                    "description": "Directory or file to search in. Defaults to workspace.",
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
            _make_listdir_input(path, depth), _ws
        ),
        description=LIST_DIR_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory to list. Defaults to workspace root.",
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
        handler=lambda pattern, path=None, max_results=200: glob_files(pattern, _ws, path=path, max_results=max_results),
        description=GLOB_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. **/*.py"},
                "path": {"type": "string", "description": "Root directory to search (default: workspace)"},
                "max_results": {"type": "integer", "description": "Max files to return (default 200)"},
            },
            "required": ["pattern"],
        },
    )

    registry.register(
        name="git",
        kind=ToolKind.EXECUTE,
        handler=lambda action="status", path=None, limit=20: git_info(action, _ws, path=path, limit=limit),
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
            action, _ws, content=content, items=items, id=id, status=status, force=force
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
        handler=lambda patch, dry_run=False: apply_patch(patch, _ws, dry_run=dry_run),
        description=PATCH_DESC,
        params_schema={
            "type": "object",
            "properties": {
                "patch": {"type": "string", "description": "Unified diff text"},
                "dry_run": {"type": "boolean", "description": "Validate only, do not write"},
            },
            "required": ["patch"],
        },
    )

    registry.register(
        name="delete_file",
        kind=ToolKind.DELETE,
        handler=lambda target_file=None, path=None, file_path=None, **kwargs: _delete_file(
            target_file or path or file_path or kwargs.get('file', ''),
            _ws
        ),
        description="Delete a file at the specified path. Accepte 'target_file', 'path' ou 'file_path'.",
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
        name="list_agents",
        kind=ToolKind.LIST,
        handler=lambda: _list_agents(),
        description="List registered A2A sub-agents and their status.",
        params_schema={"type": "object", "properties": {}},
    )

    registry.register(
        name="delegate_task",
        kind=ToolKind.TASK,
        handler=lambda agent, instruction, label="": _delegate_task(agent, instruction, label),
        description=(
            "Delegate a task to a NEMESIS teammate agent. Non-blocking: returns immediately "
            "with task_id if the agent accepted the work. The teammate runs in parallel. "
            "Use agent_status or check_reports later; reports are written under a2a_reports/."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Teammate agent name"},
                "instruction": {"type": "string", "description": "Task instruction"},
                "label": {"type": "string", "description": "Short label (optional)"},
            },
            "required": ["agent", "instruction"],
        },
    )

    registry.register(
        name="check_reports",
        kind=ToolKind.MONITOR,
        handler=lambda task_id="": _check_reports(task_id),
        description=(
            "List completed A2A task reports (in-memory + files under a2a_reports/). "
            "Optional task_id to fetch one report. Prefer read_file on the report path for full detail."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Optional task id to inspect a single report",
                },
            },
        },
    )

    registry.register(
        name="agent_status",
        kind=ToolKind.MONITOR,
        handler=lambda agent="", task_id="": _agent_status(agent, task_id),
        description=(
            "Check teammate agent status and/or async task progress. "
            "Without args: list all agents and running/completed jobs. "
            "With task_id: status of that job (running|completed|failed) and report path."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Filter by agent name (optional)"},
                "task_id": {"type": "string", "description": "Specific task id (optional)"},
            },
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

    return prompt


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
        for fc in mfc.files[:10]:
            short = fc.content[:5000].replace("\n", "\\n")  # 5 Ko - augmente de 300
            parts.append(f"--- {os.path.basename(fc.absolute_path)} ({fc.total_lines} lines) ---")
            parts.append(short)
            if len(fc.content) > 5000:
                parts.append("... (truncated)")
        return "\n".join(parts)

    for f in fields(cls):
        val = getattr(result, f.name)
        if f.name in ("success", "exit_code"):
            continue
        if val is None or val == "" or val == [] or val == {}:
            continue
        if f.name in ("matches", "files") and isinstance(val, list):
            label = "Matches" if f.name == "matches" else "Files"
            parts.append(f"{label} ({len(val)}):")
            for m in val[:100]:
                parts.append(f"  {m}")
            if len(val) > 100:
                parts.append(f"  ... and {len(val) - 100} more")
        elif f.name == "results" and isinstance(val, list):
            for r in val:
                if isinstance(r, dict):
                    parts.append(f"  - {r.get('title', '')}: {r.get('url', '')}")
                    if r.get("snippet"):
                        parts.append(f"    {r['snippet'][:200]}")
        elif f.name in ("content", "output") and isinstance(val, str) and len(val) > 10000:
            parts.append(f"{f.name}: {val[:10000]}")  # 10 Ko - augmente de 2 Ko
            parts.append("... (truncated)")
        elif f.name in ("content", "output", "message") and isinstance(val, str) and val:
            parts.append(f"{f.name}: {val}")
        elif f.name in ("content", "output") and hasattr(val, "__dataclass_fields__"):
            fc = val
            if hasattr(fc, "content") and fc.content:
                c = fc.content if len(fc.content) <= 10000 else fc.content[:10000] + "\n... (truncated)"  # 10 Ko
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


def _write_file(file_path: str, content: str, workspace_dir: str) -> object:
    from dataclasses import dataclass

    @dataclass
    class WriteResult:
        success: bool
        message: str
        error: Optional[str] = None

    from .paths import resolve_existing_path

    resolved = resolve_existing_path(file_path, fallback_base=workspace_dir)
    try:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
    except OSError as e:
        return WriteResult(success=False, message="", error=str(e))

    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        return WriteResult(success=True, message=f"File written: {file_path}")
    except OSError as e:
        return WriteResult(success=False, message="", error=str(e))


def _delete_file(target_file: str, workspace_dir: str) -> object:
    from dataclasses import dataclass

    @dataclass
    class DeleteResult:
        success: bool
        message: str
        error: Optional[str] = None

    from .paths import resolve_existing_path

    resolved = resolve_existing_path(target_file, fallback_base=workspace_dir)
    if not os.path.exists(resolved):
        return DeleteResult(success=False, message="", error=f"File not found: {target_file}")

    try:
        if os.path.isdir(resolved):
            import shutil
            shutil.rmtree(resolved)
        else:
            os.remove(resolved)
        return DeleteResult(success=True, message=f"Deleted: {target_file}")
    except OSError as e:
        return DeleteResult(success=False, message="", error=str(e))


def _make_bash_input(command, description="", timeout=None, is_background=False, workdir=None):
    from .tools.bash import BashInput
    return BashInput(
        command=command,
        description=description or "",
        timeout_ms=timeout,
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


def _list_agents() -> dict:
    try:
        from src.core.agent_manager import get_scheduler
        from src.core.default_commands import ACTIVE_AGENTS
        sched = get_scheduler()
        agents = sched.list_agents()
        if not agents and not ACTIVE_AGENTS:
            return {"success": True, "stdout": "No sub-agents registered. Use /agents to add one."}
        lines = ["SUB-AGENTS", "-" * 48]
        seen = set()
        for a in agents:
            seen.add(a["name"])
            caps = ", ".join(a.get("capabilities") or []) or "-"
            lines.append(
                f"  {a['name']:16}  status={a.get('status','?'):8}  "
                f"provider={a.get('provider','?'):12}  model={a.get('model','?')}"
            )
            if caps != "-":
                lines.append(f"    capabilities: {caps}")
        for name, ag in ACTIVE_AGENTS.items():
            if name in seen:
                continue
            lines.append(
                f"  {name:16}  status={getattr(ag,'status','idle'):8}  "
                f"provider={getattr(ag,'provider','?'):12}  model={getattr(ag,'model','?')}"
            )
        return {"success": True, "stdout": "\n".join(lines)}
    except Exception as e:
        return {"success": False, "stdout": f"list_agents error: {e}"}


def _delegate_task(agent: str = "", instruction: str = "", label: str = "") -> dict:
    """Non-blocking delegation: starts teammate work in background, returns task_id."""
    try:
        from src.core.agent_manager import get_scheduler
        from src.core.default_commands import ACTIVE_AGENTS
        if not agent:
            return {"success": False, "stdout": "Parameter 'agent' is required."}
        if not instruction:
            return {"success": False, "stdout": "Parameter 'instruction' is required."}
        sched = get_scheduler()
        if agent in ACTIVE_AGENTS and agent not in sched.agents:
            sched.register_agent_client(ACTIVE_AGENTS[agent])
        if agent not in sched.agents and agent not in ACTIVE_AGENTS:
            available = list(sched.agents.keys()) or list(ACTIVE_AGENTS.keys())
            return {
                "success": False,
                "stdout": f"Agent '{agent}' unknown. Available: {', '.join(available) or '(none)'}",
            }
        ag = ACTIVE_AGENTS.get(agent) or sched.agents.get(agent)
        if ag and getattr(ag, "status", "idle") == "busy":
            return {
                "success": False,
                "stdout": (
                    f"Agent '{agent}' is already busy on task "
                    f"{getattr(ag, '_current_task_id', '?')}. "
                    "Wait or use agent_status / check_reports."
                ),
            }
        task_id = sched.delegate(
            agent_name=agent,
            label=label or instruction[:60],
            description=instruction,
            instructions=[instruction],
            blocking=False,
        )
        if not task_id:
            return {
                "success": False,
                "stdout": (
                    f"Delegation to '{agent}' failed (agent busy or unreachable). "
                    "Use list_agents / agent_status."
                ),
            }
        reports_hint = "workspace/a2a_reports"
        try:
            if sched._reports_dir:
                reports_hint = str(sched._reports_dir)
        except Exception:
            pass
        report_file = f"{reports_hint.rstrip('/')}/{task_id}.md"
        return {
            "success": True,
            "stdout": (
                f"Delegation accepted.\n"
                f"  task_id: {task_id}\n"
                f"  agent:   {agent}\n"
                f"  status:  running (background)\n"
                f"  instruction: {instruction[:200]}{'…' if len(instruction) > 200 else ''}\n\n"
                f"NEMESIS is NOT blocked. Continue other work.\n"
                f"Later: agent_status(task_id=\"{task_id}\") or check_reports.\n"
                f"Full report will appear under {report_file}"
            ),
            "task_id": task_id,
            "agent": agent,
            "status": "running",
        }
    except Exception as e:
        return {"success": False, "stdout": f"delegate_task error: {e}"}


def _check_reports(task_id: str = "") -> dict:
    try:
        from src.core.agent_manager import get_scheduler
        sched = get_scheduler()
        lines = ["A2A REPORTS", "-" * 48]

        # Live job board
        status_blob = sched.get_task_status(task_id or "")
        if task_id:
            st = status_blob.get("status", "unknown")
            lines.append(f"task_id: {task_id}")
            lines.append(f"status:  {st}")
            if status_blob.get("agent"):
                lines.append(f"agent:   {status_blob['agent']}")
            if status_blob.get("summary"):
                lines.append(f"summary: {status_blob['summary']}")
            if status_blob.get("report_path"):
                lines.append(f"report:  {status_blob['report_path']}")
                lines.append("(Use read_file on the report path for full detail.)")
            if status_blob.get("error"):
                lines.append(f"error:   {status_blob['error']}")
            return {"success": True, "stdout": "\n".join(lines), "status": st}

        tasks = status_blob.get("tasks") or []
        if tasks:
            lines.append("Jobs:")
            for j in tasks:
                lines.append(
                    f"  [{j.get('status', '?'):10}] {j.get('task_id', '?')}  "
                    f"agent={j.get('agent', '?')}  {j.get('label', '')[:40]}"
                )
                if j.get("report_path"):
                    lines.append(f"    report: {j['report_path']}")
        else:
            lines.append("No tracked jobs yet.")

        reports = sched.collect_reports()
        if reports:
            lines.append("")
            lines.append("Completed (coordinator):")
            for r in reports:
                status = r.status.value if hasattr(r.status, "value") else str(r.status)
                lines.append(f"  [{status}] {r.task_id}: {r.summary or '(no summary)'}")

        disk = sched.list_report_files()
        if disk:
            lines.append("")
            lines.append(f"On disk ({len(disk)}):")
            for p in disk[-15:]:
                lines.append(f"  {p}")

        return {"success": True, "stdout": "\n".join(lines)}
    except Exception as e:
        return {"success": False, "stdout": f"check_reports error: {e}"}


def _agent_status(agent: str = "", task_id: str = "") -> dict:
    try:
        from src.core.agent_manager import get_scheduler
        from src.core.default_commands import ACTIVE_AGENTS
        sched = get_scheduler()

        if task_id:
            info = sched.get_task_status(task_id)
            lines = [
                f"task_id: {info.get('task_id', task_id)}",
                f"status:  {info.get('status', 'unknown')}",
            ]
            for k in ("agent", "label", "summary", "report_path", "error", "started_at", "finished_at"):
                if info.get(k) is not None:
                    lines.append(f"{k}: {info[k]}")
            return {"success": True, "stdout": "\n".join(lines), "status": info.get("status")}

        lines = ["AGENTS & JOBS", "-" * 48]
        seen = set()
        for a in sched.list_agents():
            seen.add(a["name"])
            if agent and a["name"] != agent:
                continue
            lines.append(
                f"  {a['name']:16} status={a.get('status', '?'):8}  "
                f"task={a.get('current_task') or '-'}  model={a.get('model', '?')}"
            )
        for name, ag in ACTIVE_AGENTS.items():
            if name in seen:
                continue
            if agent and name != agent:
                continue
            lines.append(
                f"  {name:16} status={getattr(ag, 'status', 'idle'):8}  "
                f"task={getattr(ag, '_current_task_id', None) or '-'}  "
                f"model={getattr(ag, 'model', '?')}"
            )

        blob = sched.get_task_status("")
        jobs = blob.get("tasks") or []
        if jobs:
            lines.append("")
            lines.append("Recent jobs:")
            for j in jobs[-10:]:
                if agent and j.get("agent") != agent:
                    continue
                lines.append(
                    f"  [{j.get('status', '?'):10}] {j.get('task_id', '?')}  "
                    f"{j.get('agent', '?')} — {j.get('label', '')[:50]}"
                )
        return {"success": True, "stdout": "\n".join(lines)}
    except Exception as e:
        return {"success": False, "stdout": f"agent_status error: {e}"}


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
