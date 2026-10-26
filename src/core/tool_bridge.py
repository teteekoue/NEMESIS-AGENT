"""ToolBridge — connects the new ToolRegistry with the old ActionExecutor interface.

This allows a gradual migration: the agent loop can use the new tool system
while the existing provider layer stays unchanged.
"""

import os
from typing import Dict, Any, Generator
from pathlib import Path
from src.core.agent_tools import create_registry, build_system_prompt, build_feedback
from src.core.tools.bash import run_bash_streamed


class ToolBridge:
    """Adapter: presents the new ToolRegistry through the old execute_tool() interface."""

    def __init__(
        self,
        workspace: str | None = None,
        cancel_event=None,
        config: Dict[str, Any] | None = None,
        enable_subagents: bool = True,
    ):
        raw_workspace = str(workspace).strip() if workspace else ""
        self.workspace_root = (
            Path(raw_workspace).expanduser().resolve() if raw_workspace else Path.cwd()
        )
        if not self.workspace_root.exists():
            self.workspace_root = Path.cwd()
        self.subagent_fleet = None
        if enable_subagents:
            from src.core.subagents import SubagentFleet

            self.subagent_fleet = SubagentFleet(config or {}, str(self.workspace_root))
        self.registry = create_registry(str(self.workspace_root), self.subagent_fleet)
        # Optional threading.Event used to abort a running bash command as soon
        # as the user interrupts the turn (Stop button).
        self.cancel_event = cancel_event
        if self.subagent_fleet is not None:
            self.subagent_fleet.set_cancel_event(cancel_event)

    def set_cancel_event(self, cancel_event) -> None:
        self.cancel_event = cancel_event
        if self.subagent_fleet is not None:
            self.subagent_fleet.set_cancel_event(cancel_event)

    def set_subagent_authorizer(self, callback) -> None:
        if self.subagent_fleet is not None:
            self.subagent_fleet.set_authorizer(callback)

    def close(self) -> None:
        if self.subagent_fleet is not None:
            self.subagent_fleet.close()

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

    def _resolve_tool_path(self, raw) -> str:
        from src.core.paths import resolve_path

        return resolve_path(raw)

    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]):
        yield from self._execute(tool_name, parameters)

    def _execute(self, tool_name: str, parameters: Dict[str, Any]):
        try:
            parameters = dict(parameters or {})
            # Le parseur renvoie {"value": <texte brut>} quand les arguments ne
            # sont pas un JSON valide (reponse tronquee par le provider, quoting
            # casse). Le dire clairement au lieu d'un "parametre manquant".
            if parameters and set(parameters.keys()) <= {"value", "_raw"}:
                fragment = str(parameters.get("value") or parameters.get("_raw") or "")
                preview = fragment[:400] + ("..." if len(fragment) > 400 else "")
                yield {
                    "success": False,
                    "stdout": (
                        f"Arguments invalides pour '{tool_name}': le JSON recu n'a pas pu "
                        f"etre decode (reponse probablement tronquee). Fragment recu:\n{preview}"
                    ),
                    "error": "arguments JSON invalides",
                }
                return
            reg = self.registry.get_tool(tool_name)
            if reg is None:
                # Noms historiques (`write`, `append`, `replace`, `read`,
                # `validate`, ...) : les traduire vers l'outil moderne
                # equivalent au lieu de renvoyer "outil inconnu".
                legacy_name, converter = self.adapt_for_old_executor(tool_name, parameters)
                if legacy_name != tool_name and self.registry.get_tool(legacy_name) is not None:
                    try:
                        converted = converter(parameters) or {}
                    except Exception:  # noqa: BLE001 - keep the original error
                        converted = None
                    if isinstance(converted, dict) and converted:
                        yield from self._execute(legacy_name, converted)
                        return
                yield {"success": False, "stdout": f"Outil inconnu: {tool_name}"}
                return

            # Compatibilite legacy : mode asynchrone -> is_background
            if tool_name == "bash" and "mode" in parameters and "is_background" not in parameters:
                parameters["is_background"] = str(parameters.pop("mode", "")).lower() in (
                    "asynchrone", "async", "background", "true"
                )

            ok, cleaned, error = self.registry.validate_call(tool_name, parameters)
            if not ok:
                yield {"success": False, "stdout": error, "error": error}
                return
            parameters = cleaned

            if tool_name == "bash":
                from src.core.agent_tools import _make_bash_input

                seconds = parameters.get("timeout")
                milliseconds = parameters.get("timeout_ms")
                bash_input = _make_bash_input(
                    parameters.get("command", ""),
                    parameters.get("description", ""),
                    seconds if seconds is not None else milliseconds,
                    bool(parameters.get("is_background", parameters.get("background", False))),
                    parameters.get("workdir") or parameters.get("cwd"),
                    timeout_is_ms=seconds is None and milliseconds is not None,
                )

                if bash_input.is_background:
                    result = reg.handler(
                        command=bash_input.command,
                        description=bash_input.description,
                        timeout=parameters.get("timeout"),
                        timeout_ms=parameters.get("timeout_ms"),
                        is_background=True,
                        workdir=bash_input.workdir,
                    )
                    if not isinstance(result, dict):
                        result = {"success": getattr(result, "success", False),
                                  "stdout": getattr(result, "output", ""),
                                  "error": getattr(result, "error", None)}
                    result.setdefault("stdout", result.get("output", ""))
                    yield result
                    return

                cmd = bash_input.command
                workdir = bash_input.workdir

                final: Dict[str, Any] | None = None
                accumulated: list[str] = []
                for update in run_bash_streamed(
                    cmd, workdir, bash_input.timeout_ms, cancel_event=self.cancel_event
                ):
                    if "partial" in update:
                        accumulated.append(update["partial"])
                    else:
                        final = update
                if final is None:
                    final = {
                        "success": False,
                        "output": "",
                        "error": "La commande n'a produit aucun resultat exploitable.",
                        "exit_code": -1,
                    }
                final = dict(final)
                # Keep both keys: chat surfaces read `output`, the model sees
                # `stdout`; neither may silently drop stderr or the exit code.
                final.setdefault("stdout", final.get("output", ""))
                final.setdefault("output", final.get("stdout", ""))
                yield final
                return

            result = reg.handler(**parameters)

            if isinstance(result, dict):
                yield result
                return

            if hasattr(result, "__iter__") and not isinstance(result, str):
                for item in result:
                    yield item
                return

            formatted = build_feedback(tool_name, result)
            success = getattr(result, "success", getattr(result, "exit_code", 1) == 0)
            err = getattr(result, "error", getattr(result, "stderr", None))

            output = {"success": success, "stdout": formatted, "error": err}
            if getattr(result, "images", None):
                output["images"] = list(result.images)
            if tool_name == "edit" and hasattr(result, "edits"):
                output["edits"] = result.edits
            if tool_name == "edit" and hasattr(result, "message"):
                output["message"] = result.message
            if hasattr(result, "path"):
                output["path"] = result.path

            yield output

        except Exception as e:
            yield {"success": False, "stdout": f"Erreur outil '{tool_name}': {str(e)}", "error": str(e)}

    def get_system_prompt(self) -> str:
        # The live registry is the single source of truth for capabilities.
        # A static prompt can drift from the installed handlers and schemas.
        prompt = build_system_prompt(self.registry)
        if self.subagent_fleet is not None:
            availability = self.subagent_fleet.availability()
            if availability["available"]:
                if availability["mode"] == "main-provider":
                    prompt += (
                        "\n\nDélégation disponible : aucune clé dédiée n'est configurée, "
                        "le provider principal est réutilisé par les sous-agents."
                    )
            else:
                prompt += (
                    "\n\nDélégation indisponible : aucune clé API de sous-agent et provider "
                    "principal inutilisable. Signaler ce mode dégradé si une délégation est "
                    "demandée, sans bloquer le reste du travail."
                )
        return prompt

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
                "file_path": p.get("file_path") or p.get("path") or p.get("filename") or "",
                "content": p.get("content", ""),
            }),
            "append": ("write_file", lambda p: {
                "file_path": p.get("file_path") or p.get("path") or p.get("filename") or "",
                "content": f"__APPEND__:{p.get('content', '')}",
            }),
            "replace": ("edit", lambda p: {
                "file_path": p.get("file_path") or p.get("path") or p.get("filename") or "",
                "old_string": p.get("blocks", [{}])[0].get("search", "") if p.get("blocks") else "",
                "new_string": p.get("blocks", [{}])[0].get("replace", "") if p.get("blocks") else "",
                "replace_all": False,
            }),
            "read": ("read_file", lambda p: {
                "path": p.get("file_path") or p.get("filename") or (
                    (p.get("files") or [""])[0] if isinstance(p.get("files"), list) else p.get("path", "")
                ),
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


def create_tool_bridge(workspace: str | None = None, config=None) -> ToolBridge:
    return ToolBridge(workspace, config=config)
