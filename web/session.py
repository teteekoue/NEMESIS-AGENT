"""Session web : reproduit l'orchestration du terminal NEMESIS.

Contraintes heritees du CLI conservees ici :
- Provider unique NemApi, contexte gere cote serveur (on n'envoie que le
  message courant ; le prompt systeme est envoye une seule fois au demarrage
  d'une nouvelle session via fresh_chat).
- Autorisation par outil + signature exacte pour les appels a haut risque.
- Sorties bash stockees avec un output_id pour pouvoir etre masquees/affichees.

Les outils sont adresses via la registry partagee (ToolRegistry) plutot que
les branches speciales de ToolBridge.execute_tool, afin de rester genereux
et coherents avec ce que publie /api/tools.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _auth_signature(tool_name: str, params: Dict[str, Any]) -> str:
    try:
        blob = json.dumps(params or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = repr(params)
    digest = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
    return f"{tool_name}:{digest}"


class AppShim:
    """Exposes the attributes slash commands expect from NemesisApp."""

    def __init__(self, session: "WebSession"):
        self._session = session

    @property
    def config(self) -> Dict[str, Any]:
        return self._session.server.config

    @property
    def client(self):
        return self._session.provider

    @property
    def executor(self):
        return self._session.executor

    @property
    def parser(self):
        return self._session.parser

    @parser.setter
    def parser(self, value):
        self._session.parser = value

    @property
    def workspace(self) -> str:
        return self._session.workspace

    @property
    def console(self):
        # Commands print through the module-level rich Console in default_commands;
        # the web server swaps that temporarily to capture output. This property is
        # only a fallback for handlers that call app.console directly.
        from rich.console import Console

        return Console()

    @property
    def composer(self):
        return self._session.composer

    @property
    def auto_allow(self) -> bool:
        return False

    @auto_allow.setter
    def auto_allow(self, value: bool) -> None:
        pass

    @property
    def debug(self) -> bool:
        return self._session.debug

    @debug.setter
    def debug(self, value: bool) -> None:
        self._session.debug = value

    @property
    def last_interrupt(self) -> float:
        return getattr(self._session, "_last_interrupt", 0.0)

    @last_interrupt.setter
    def last_interrupt(self, value: float) -> None:
        self._session._last_interrupt = value

    @property
    def session_start(self) -> float:
        return self._session.created_at

    @session_start.setter
    def session_start(self, value: float) -> None:
        self._session.created_at = value

    @property
    def _conn_ok(self):
        return self._session._conn_ok

    @_conn_ok.setter
    def _conn_ok(self, value) -> None:
        self._session._conn_ok = value

    @property
    def _prompt_sent(self) -> bool:
        return self._session._prompt_sent

    @_prompt_sent.setter
    def _prompt_sent(self, value: bool) -> None:
        self._session._prompt_sent = value

    @property
    def _send_system_prompt(self) -> bool:
        return self._session._send_system_prompt

    @_send_system_prompt.setter
    def _send_system_prompt(self, value: bool) -> None:
        self._session._send_system_prompt = value

    @property
    def _authorized_tools(self) -> set:
        return self._session._authorized_tools

    @_authorized_tools.setter
    def _authorized_tools(self, value: set) -> None:
        self._session._authorized_tools = value

    @property
    def _authorized_signatures(self) -> set:
        return self._session._authorized_signatures

    @_authorized_signatures.setter
    def _authorized_signatures(self, value: set) -> None:
        self._session._authorized_signatures = value

    @property
    def _last_auth_choice(self):
        return getattr(self._session, "_last_auth_choice", None)

    @_last_auth_choice.setter
    def _last_auth_choice(self, value) -> None:
        self._session._last_auth_choice = value

    @property
    def _hidden_outputs(self) -> Dict[int, Dict[str, Any]]:
        return self._session._hidden_outputs

    @_hidden_outputs.setter
    def _hidden_outputs(self, value) -> None:
        self._session._hidden_outputs = value

    @property
    def _output_counter(self) -> int:
        return self._session._output_counter

    @_output_counter.setter
    def _output_counter(self, value: int) -> None:
        self._session._output_counter = value

    @property
    def _interrupted(self) -> bool:
        return self._session._interrupted

    @_interrupted.setter
    def _interrupted(self, value: bool) -> None:
        self._session._interrupted = value

    @property
    def install_dir(self) -> Path:
        from src.core.paths import INSTALL_DIR

        return INSTALL_DIR

    @property
    def user_config_dir(self) -> Path:
        from src.core.paths import USER_CONFIG_DIR

        return USER_CONFIG_DIR

    @property
    def config_path(self) -> Path:
        from src.core.paths import config_path

        return config_path()

    @property
    def mcp_config_path(self) -> Path:
        from src.core.paths import mcp_config_path

        return mcp_config_path()

    def as_app_shim(self) -> "AppShim":
        return self

    def _consume_input_interrupt(self) -> bool:
        return False

    def _handle_interrupt(self) -> None:
        self._session.interrupt()

    def _show_commands(self) -> None:
        from rich.console import Console
        from rich.table import Table
        from rich import box
        from src.core.commands import registry

        console = Console()
        table = Table(title="Commandes disponibles", border_style="#45475a", box=box.ROUNDED, show_header=True)
        table.add_column("Commande", style="bold #fab387", no_wrap=True)
        table.add_column("Usage", style="#a6adc8", no_wrap=True)
        table.add_column("Description", style="#cdd6f4")
        for cmd in sorted(registry.list_commands(), key=lambda c: c.name):
            table.add_row(f"/{cmd.name}", cmd.usage or "—", cmd.description or "")
        console.print(table)

    def _save_config(self) -> None:
        import yaml
        from src.core.paths import config_path

        sess = self._session
        try:
            path = config_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(sess.server.config, f, allow_unicode=True, sort_keys=False)
        except Exception:
            pass

    def reconfigure_provider(self, new_config: Dict[str, Any]):
        """Rebuild provider on the fly (invoked by /config)."""
        from providers import create_provider

        sess = self._session
        old_conversation = sess.provider.get_conversation() if sess.provider else []
        try:
            new_client = create_provider(new_config)
            conn_ok = new_client.test_connection()
            sess.provider = new_client
            sess.provider.set_conversation(old_conversation)
            sess.server.config = new_config
            sess._conn_ok = True
            # A fresh provider means a fresh context server-side: re-send system prompt.
            sess._prompt_sent = False
            self._save_config()
            return True, "Provider reconfigure" if conn_ok else "Provider reconfigure (serveur joignable plus tard)"
        except Exception as e:
            return False, str(e)


class WebSession:
    def __init__(self, server: "WebServer", workspace: str):
        self.id = uuid.uuid4().hex[:12]
        self.server = server
        self.workspace = workspace
        self.provider = None
        self.executor = None
        self.parser = None
        self.title = "Nouvelle session"
        self.created_at = time.time()
        self.updated_at = time.time()
        self.messages: List[Dict[str, Any]] = []
        self._prompt_sent = False
        self._send_system_prompt = True
        self._authorized_tools: set = set()
        self._authorized_signatures: set = set()
        self._hidden_outputs: Dict[int, Dict[str, Any]] = {}
        self._output_counter = 0
        self._interrupted = False
        self._busy = False
        self._last_interrupt = 0.0
        self._last_auth_choice = None
        self._conn_ok = None
        self.error_startup = None
        self._pending_auth: Dict[str, "queue.Queue"] = {}
        self._lock = threading.Lock()

        from src.ui.composer import Composer

        self.composer = Composer()
        self.shim = AppShim(self)

    # ------------------------------------------------------------- lifecycle
    def touch(self) -> None:
        self.updated_at = time.time()

    def add_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        if "id" not in msg:
            msg["id"] = len(self.messages)
        self.messages.append(msg)
        self.touch()
        return msg

    def to_summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "workspace": self.workspace,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "turn_count": len([m for m in self.messages if m.get("role") == "user"]),
            "message_count": len(self.messages),
            "model": getattr(self.provider, "model", "") if self.provider else "",
            "provider": "nemapi",
        }

    # ---------------------------------------------------------------- events
    def emit(self, event: str, data: Dict[str, Any]) -> None:
        self.server.broadcast({"session": self.id, "type": event, "data": data})

    # ------------------------------------------------------------- interrupt
    def interrupt(self) -> None:
        self._interrupted = True

    # ---------------------------------------------------------- authorization
    def has_authorization(self, tool_name: str, params: Dict[str, Any], risk: str) -> bool:
        if risk == "read":
            return True
        if tool_name in self._authorized_tools:
            return True
        if _auth_signature(tool_name, params) in self._authorized_signatures:
            return True
        return False

    def grant(self, tool_name: str, params: Dict[str, Any], risk: str, choice: str) -> bool:
        sig = _auth_signature(tool_name, params)
        if choice in ("y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        if choice in ("a", "always"):
            if risk == "high":
                self._authorized_signatures.add(sig)
            else:
                self._authorized_tools.add(tool_name)
            return True
        return False

    # ------------------------------------------------------------ tool call
    def format_feedback(self, action: Dict[str, Any], res: Dict[str, Any]) -> str:
        output = res.get("stdout", res.get("content", "")) or ""
        max_chars = 10_000_000
        if len(output) > max_chars:
            output = output[:max_chars] + "\n\n[AVERTISSEMENT SYSTEME : Logs tronques.]"
        if not output:
            output = "[Aucune sortie capturee - la commande a peut-etre echoue ou n'a produit aucune sortie]"
        tool_name = action["type"]
        success = res.get("success", False)
        return f"FEEDBACK:\nTool: {tool_name}\nSucces: {success}\nOutput:\n{output}"

    def stream_tool(self, act_type: str, params: Dict[str, Any]):
        """Execute one tool by name against the shared registry.

        Yields result dicts (bash may yield several partial updates). Unknown
        tools and validation errors are surfaced as failed results instead of
        raising, matching the bridge's tolerant behaviour.
        """
        reg = self.executor.registry.get_tool(act_type)
        if reg is None:
            yield {"success": False, "stdout": f"Outil inconnu: {act_type}"}
            return

        ok, cleaned, error = self.executor.registry.validate_call(act_type, params)
        if not ok:
            yield {"success": False, "stdout": error}
            return
        params = cleaned

        kwargs = {k: v for k, v in params.items() if v is not None}

        # bash foreground streams via run_bash_streamed so partial output can be
        # forwarded to the UI; everything else uses the plain registry handler.
        if act_type == "bash" and not kwargs.get("is_background", False):
            from src.core.tools.bash import run_bash_streamed

            workdir = kwargs.get("workdir") or str(getattr(self.executor, "workspace_root", "."))
            timeout = kwargs.get("timeout")
            final: Optional[Dict[str, Any]] = None
            try:
                for update in run_bash_streamed(kwargs.get("command", ""), str(workdir), timeout):
                    if "partial" in update:
                        yield update
                    else:
                        final = update
            except Exception as e:
                yield {"success": False, "stdout": f"Erreur outil 'bash': {e}"}
                return
            if final is not None:
                yield dict(final) if isinstance(final, dict) else {"success": True, "stdout": str(final)}
            return

        try:
            result = reg.handler(**kwargs)
        except TypeError as e:
            yield {"success": False, "stdout": f"Parametres invalides pour '{act_type}': {e}"}
            return
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur outil '{act_type}': {e}"}
            return

        # dict result (todo, many handlers) — forward verbatim
        if isinstance(result, dict):
            yield result
            return

        # iterable result (should be rare now that bash is handled above)
        if hasattr(result, "__iter__") and not isinstance(result, str) and not hasattr(result, "success"):
            for item in result:
                if isinstance(item, dict):
                    yield item
                else:
                    yield {"success": True, "stdout": str(item)}
            return

        # dataclass-style result: normalize using build_feedback + known fields
        out = self._normalize_result(act_type, result)
        yield out

    def _normalize_result(self, tool_name: str, result: Any) -> Dict[str, Any]:
        from src.core.agent_tools import build_feedback

        success = bool(getattr(result, "success", getattr(result, "exit_code", 1) == 0))
        err = getattr(result, "error", getattr(result, "stderr", None))

        stdout = ""
        content = getattr(result, "content", None)
        if content is not None:
            ctype = type(content).__name__
            if ctype == "MultiFileContent":
                parts = []
                for fc in content.files[:10]:
                    head = fc.content_concise or fc.content
                    parts.append(f"--- {fc.absolute_path} ({fc.total_lines} lignes) ---\n{head}")
                stdout = "\n".join(parts)
            elif hasattr(content, "content"):
                stdout = content.content_concise or content.content
            elif isinstance(content, str):
                stdout = content

        # bash / generic output attribute
        if not stdout:
            o = getattr(result, "output", "")
            if isinstance(o, str):
                stdout = o

        # edit details
        edits = getattr(result, "edits", None)
        msg = getattr(result, "message", None)
        if edits:
            lines = [msg] if msg else []
            for ed in edits:
                ln = getattr(ed, "line_number", "?")
                old = (getattr(ed, "old_text", "") or "").splitlines()[:3]
                new = (getattr(ed, "new_text", "") or "").splitlines()[:3]
                lines.append(f"Ligne {ln}: {' | '.join(old)} → {' | '.join(new)}")
            stdout = "\n".join(lines) if lines else stdout

        if not stdout:
            stdout = build_feedback(tool_name, result)

        out = {"success": success, "stdout": stdout}
        if err:
            out["error"] = str(err)
        if msg and tool_name in ("edit", "write_file", "delete_file"):
            out["message"] = str(msg)
        # todo items pass through for plan rendering
        if tool_name == "todo" and hasattr(result, "items"):
            out["items"] = getattr(result, "items")
        return out
