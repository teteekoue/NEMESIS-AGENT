"""ToolBridge — connects the new ToolRegistry with the old ActionExecutor interface.

This allows a gradual migration: the agent loop can use the new tool system
while the existing provider layer stays unchanged.
"""

import os
import re
from typing import Dict, Any, Generator
from pathlib import Path
from src.core.agent_tools import create_registry, build_system_prompt, build_feedback
from src.core.tools.bash import run_bash_streamed


class ToolBridge:
    """Adapter: presents the new ToolRegistry through the old execute_tool() interface."""

    def __init__(self, workspace: str = "./workspace"):
        self.workspace_root = Path(workspace).resolve()
        if not self.workspace_root.exists():
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.registry = create_registry(str(self.workspace_root))
        self._read_paths = set()

    def risk_for(self, tool_name: str, parameters: Dict[str, Any] | None = None) -> str:
        """Return the runtime risk class used by approval UIs."""
        risk = self.registry.risk_for(tool_name)
        if tool_name == "bash":
            command = str((parameters or {}).get("command", "")).lower()
            if any(token in command for token in (
                "rm ", "rm\t", "rmdir", "git push", "git reset", "git clean",
                "kill ", "pkill ", "shutdown", "reboot", "drop table", "truncate ",
                "curl ", "wget ", "nc ", "chmod ", "chown ",
            )):
                return "high"
        return risk

    _PATH_KEYS = ("path", "paths", "file_path", "file", "filename", "target_file", "workdir", "cwd")

    def _is_within_workspace(self, raw: Any) -> bool:
        """Return True if raw resolves inside the workspace (symlinks followed)."""
        candidate = Path(str(raw)).expanduser()
        resolved = (candidate if candidate.is_absolute() else self.workspace_root / candidate).resolve()
        return resolved == self.workspace_root or self.workspace_root in resolved.parents

    def _check_patch_paths(self, patch: str) -> str | None:
        """Reject patches touching files outside the workspace.

        A patch is executed by the `patch` binary with cwd set to the workspace,
        so a `+++ ../outside` or an absolute path would escape containment.
        """
        for match in re.finditer(r"^(?:\+\+\+|---)\s+([^\t\n]+)", patch, re.M):
            target = match.group(1).strip()
            if target in ("/dev/null", "/dev/stdin"):
                continue
            # Strip the b/ or a/ prefix that `patch -p1` removes.
            cleaned = re.sub(r"^[ab]/", "", target)
            if not self._is_within_workspace(cleaned):
                return f"Patch targets a path outside workspace: {target}"
        return None

    def _check_paths(self, tool_name: str, parameters: Dict[str, Any]) -> str | None:
        for key in self._PATH_KEYS:
            value = parameters.get(key)
            if value is None or value == "":
                continue
            values = value if isinstance(value, list) else [value]
            for raw in values:
                if raw is None or raw == "":
                    continue
                if not self._is_within_workspace(raw):
                    return f"Path outside workspace is not allowed: {raw}"
        # apply_patch carries its targets inside the diff body, not in a path key.
        if tool_name == "apply_patch":
            return self._check_patch_paths(str(parameters.get("patch", "")))
        return None

    def _check_read_before_write(self, tool_name: str, parameters: Dict[str, Any]) -> str | None:
        if tool_name not in {"edit", "delete_file"}:
            return None
        raw = parameters.get("file_path") or parameters.get("target_file") or parameters.get("path")
        if not raw:
            return None
        path = (self.workspace_root / str(raw)).resolve()
        if path.exists() and str(path) not in self._read_paths:
            return f"Read the existing file with read_file before using {tool_name}: {raw}"
        return None

    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]):
        yield from self._execute(tool_name, parameters)

    def _execute(self, tool_name: str, parameters: Dict[str, Any]):
        try:
            parameters = parameters or {}
            path_error = self._check_paths(tool_name, parameters)
            if path_error:
                yield {"success": False, "stdout": path_error, "error": path_error}
                return
            read_error = self._check_read_before_write(tool_name, parameters)
            if read_error:
                yield {"success": False, "stdout": read_error, "error": read_error}
                return
            reg = self.registry.get_tool(tool_name)
            if reg is None:
                yield {"success": False, "stdout": f"Outil inconnu: {tool_name}"}
                return

            # Compatibilite legacy : mode asynchrone -> is_background
            if tool_name == "bash" and "mode" in parameters and "is_background" not in parameters:
                parameters = dict(parameters)
                parameters["is_background"] = str(parameters.pop("mode", "")).lower() in ("asynchrone", "async", "background", "true")

            ok, cleaned, error = self.registry.validate_call(tool_name, parameters)
            if not ok:
                yield {"success": False, "stdout": error}
                return
            parameters = cleaned

            if tool_name == "bash":
                cmd = parameters.get("command", "")
                is_bg = parameters.get("is_background", False)
                timeout = parameters.get("timeout")
                workdir = parameters.get("workdir", self.workspace_root)

                if is_bg:
                    # Background: use normal handler (returns immediately)
                    result = reg.handler(
                        command=cmd,
                        description=parameters.get("description", ""),
                        timeout=timeout,
                        is_background=True,
                        workdir=workdir,
                    )
                else:
                    # Foreground: execute and collect all output (no streaming)
                    wd = str(workdir) if not isinstance(workdir, str) else workdir
                    full_output = []
                    for update in run_bash_streamed(cmd, wd, timeout):
                        if "partial" in update:
                            # Accumulate output but don't yield it
                            full_output.append(update["partial"])
                        else:
                            result = update
                    # Combine accumulated output with final result
                    if isinstance(result, dict):
                        # The result already contains the full output from bash.py, but we've also accumulated partial outputs
                        # Use only the accumulated output to avoid duplication
                        existing_stdout = result.get("stdout", "")
                        if full_output:
                            result["stdout"] = "\n".join(full_output)
                        else:
                            result["stdout"] = existing_stdout
                        result.pop("output", None)  # Clean up duplicate key
                        yield result
                    return
            elif tool_name == "get_task_output":
                result = reg.handler(
                    task_id=parameters.get("task_id", ""),
                    timeout_ms=parameters.get("timeout_ms"),
                )
            elif tool_name == "kill_task":
                result = reg.handler(task_id=parameters.get("task_id", ""))
            elif tool_name == "list_dir":
                result = reg.handler(
                    path=parameters.get("path"),
                    depth=parameters.get("depth", 2),
                )
            elif tool_name == "web_search":
                result = reg.handler(
                    query=parameters.get("query", ""),
                    max_results=parameters.get("max_results", 5),
                )
            elif tool_name == "web_fetch":
                result = reg.handler(
                    url=parameters.get("url", ""),
                    format=parameters.get("format", "markdown"),
                )
            elif tool_name == "read_file":
                result = reg.handler(
                    path=parameters.get("path"),
                    paths=parameters.get("paths"),
                    offset=parameters.get("offset"),
                    limit=parameters.get("limit"),
                )
            elif tool_name == "edit":
                result = reg.handler(
                    file_path=parameters.get("file_path", ""),
                    old_string=parameters.get("old_string", ""),
                    new_string=parameters.get("new_string", ""),
                    replace_all=parameters.get("replace_all", False),
                )
            elif tool_name == "write_file":
                result = reg.handler(
                    file_path=parameters.get("file_path", ""),
                    content=parameters.get("content", ""),
                )
            elif tool_name == "delete_file":
                result = reg.handler(target_file=parameters.get("target_file", ""))
            elif tool_name == "grep":
                result = reg.handler(
                    pattern=parameters.get("pattern", ""),
                    include=parameters.get("include"),
                    path=parameters.get("path"),
                    case_insensitive=parameters.get("case_insensitive", False),
                )
            elif tool_name == "mcp_list":
                result = reg.handler()
            elif tool_name == "mcp_tools_list":
                result = reg.handler(server=parameters.get("server", ""))
            elif tool_name == "mcp_call":
                result = reg.handler(
                    server=parameters.get("server", ""),
                    tool=parameters.get("tool", ""),
                    arguments=parameters.get("arguments"),
                )
            elif tool_name == "glob":
                result = reg.handler(
                    pattern=parameters.get("pattern", ""),
                    path=parameters.get("path"),
                    max_results=parameters.get("max_results", 200),
                )
            elif tool_name == "git":
                result = reg.handler(
                    action=parameters.get("action", "status"),
                    path=parameters.get("path"),
                    limit=parameters.get("limit", 20),
                )
            elif tool_name == "todo":
                result = reg.handler(
                    action=parameters.get("action", "list"),
                    content=parameters.get("content"),
                    items=parameters.get("items"),
                    id=parameters.get("id"),
                    status=parameters.get("status"),
                    force=parameters.get("force", False),
                )
            elif tool_name == "apply_patch":
                result = reg.handler(
                    patch=parameters.get("patch", ""),
                    dry_run=parameters.get("dry_run", False),
                )
            else:
                # Generic dispatch for any remaining registered tool
                try:
                    result = reg.handler(**parameters)
                except TypeError:
                    yield {"success": False, "stdout": f"Outil non implemente ou parametres invalides: {tool_name}"}
                    return

            if tool_name == "read_file" and getattr(result, "success", False):
                read_values = parameters.get("paths") or [parameters.get("path")]
                for raw in read_values:
                    if raw:
                        self._read_paths.add(str((self.workspace_root / str(raw)).resolve()))

            if isinstance(result, dict):
                yield result
                return

            if hasattr(result, "__iter__") and not isinstance(result, str):
                for item in result:
                    if tool_name == "read_file" and getattr(item, "get", lambda *_: False)("success"):
                        for raw in parameters.get("paths") or [parameters.get("path")]:
                            if raw:
                                self._read_paths.add(str((self.workspace_root / str(raw)).resolve()))
                    yield item
                return

            formatted = build_feedback(tool_name, result)
            success = getattr(result, "success", getattr(result, "exit_code", 1) == 0)
            err = getattr(result, "error", getattr(result, "stderr", None))
            
            # Pour edit, inclure les edits dans le résultat
            output = {"success": success, "stdout": formatted, "error": err}
            if tool_name == "edit" and hasattr(result, "edits"):
                output["edits"] = result.edits
            if tool_name == "edit" and hasattr(result, "message"):
                output["message"] = result.message
            
            yield output

        except Exception as e:
            yield {"success": False, "stdout": f"Erreur outil '{tool_name}': {str(e)}"}

    def get_system_prompt(self) -> str:
        # The live registry is the single source of truth for capabilities.
        # A static prompt can drift from the installed handlers and schemas.
        return build_system_prompt(self.registry, str(self.workspace_root))

    def get_openai_tools(self) -> list:
        return self.registry.get_openai_functions()

    def adapt_for_old_executor(self, tool_name, params):
        """Map old tool names (from the legacy XML/JSON grammar) to new tool names."""
        mapping = {
            "bash": ("bash", lambda p: {
                "command": p.get("command", ""),
                "mode": p.get("mode", "synchrone"),
                "description": "",
            }),
            "write": ("write_file", lambda p: {
                "file_path": p.get("path", ""),
                "content": p.get("content", ""),
            }),
            "append": ("write_file", lambda p: {
                "file_path": p.get("path", ""),
                "content": f"__APPEND__:{p.get('content', '')}",
            }),
            "replace": ("edit", lambda p: {
                "file_path": p.get("path", ""),
                "old_string": p.get("blocks", [{}])[0].get("search", "") if p.get("blocks") else "",
                "new_string": p.get("blocks", [{}])[0].get("replace", "") if p.get("blocks") else "",
                "replace_all": False,
            }),
            "read": ("read_file", lambda p: {
                "path": (p.get("files", [""])[0]) if isinstance(p.get("files"), list) else p.get("path", ""),
            }),
            "list_dir": ("list_dir", lambda p: {
                "path": p.get("path", "."),
                "depth": 2,
            }),
            "validate": ("bash", lambda p: {
                "command": f"python -m py_compile {p.get('path', '')}" if p.get("path", "").endswith(".py")
                else f"sh -n {p.get('path', '')}",
                "description": "Validate syntax",
            }),
            "web_search": ("web_search", lambda p: {
                "query": p.get("query", ""),
                "max_results": 5,
            }),
            "status": ("get_task_output", lambda p: {
                "task_id": str(p.get("pid", "")),
            }),
            "kill_process": ("kill_task", lambda p: {
                "task_id": str(p.get("pid", "")),
            }),
            "cleanup_logs": ("kill_task", lambda p: {
                "task_id": "__cleanup__",
            }),
        }
        return mapping.get(tool_name, (tool_name, lambda p: p))


def create_tool_bridge(workspace: str = "./workspace") -> ToolBridge:
    return ToolBridge(workspace)
