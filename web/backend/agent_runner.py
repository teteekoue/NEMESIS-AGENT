"""Headless NEMESIS agent runner for the web UI.

The terminal app (``agent.py``) drives the agent loop against a Rich console.
This runner reuses the exact same building blocks -- provider, tool executor
and ``ActionParser`` -- but turns the loop into a stream of events and makes
tool authorization asynchronous instead of an interactive prompt.

Blocking work (HTTP to NEMAPI, subprocess execution) runs in a worker thread;
events are marshalled back onto the asyncio loop through a thread-safe queue.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from action_parser import ActionParser
from providers import create_provider
from tools import create_executor_from_config

MAX_TOOL_ITERATIONS = 50
MAX_TOOL_CALLS_PER_MESSAGE = 3  # hard ceiling enforced regardless of model output
AUTHORIZE_TIMEOUT = 600  # seconds to wait for a human decision

# Cache the expensive introspection endpoints (tools catalog) and the
# provider connectivity probe across runners so opening a new chat / page
# load does not pay a fresh network round-trip every time.
_TOOLS_CACHE: Dict[str, Any] = {"key": None, "value": None, "at": 0.0}
_TOOLS_CACHE_TTL = 30.0


class AgentRunner:
    """One runner per chat connection: owns a provider + tool executor."""

    def __init__(self, config: Dict[str, Any], loop: asyncio.AbstractEventLoop,
                 session_id: Optional[str] = None, model: Optional[str] = None,
                 approval_mode: Optional[str] = None):
        self.config = config
        self.loop = loop
        self.session_id = session_id
        self.events: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()

        security = config.get("security", {}) if isinstance(config.get("security"), dict) else {}
        self.approval_mode = approval_mode or security.get("approval_mode") or "confirm"

        self.provider = create_provider(config)
        if model:
            self.provider.model = model
        self._session_initialized = False
        if session_id is not None:
            try:
                from web.backend import sessions as session_store
                self._session_initialized = session_store.is_initialized(session_id)
            except Exception:
                self._session_initialized = False
        # Bind the provider to the persisted logical session: when the session
        # was already initialized NEMAPI keeps the server-side context, so we
        # must not request a fresh chat nor resend the system prompt.
        if hasattr(self.provider, "configure_session"):
            self.provider.configure_session(session_id, initialized=self._session_initialized)
        self.executor = create_executor_from_config(config, bridge=self.provider)

        valid_tools: set = set()
        registry = getattr(self.executor, "registry", None)
        if registry is not None:
            valid_tools = set(registry._tools.keys())
        self.parser = ActionParser(extra_valid_tools=valid_tools or None)

        self._authorized: set = set()
        self._interrupt = threading.Event()
        self._auth_waiters: Dict[str, threading.Event] = {}
        self._auth_results: Dict[str, str] = {}
        self._busy = threading.Event()
        self._conn_ok: Optional[bool] = None
        self._prompt_sent = self._session_initialized

    # ------------------------------------------------------------------
    # event plumbing
    # ------------------------------------------------------------------
    def emit(self, event_type: str, **data: Any) -> None:
        payload = {"type": event_type, **data}
        try:
            self.loop.call_soon_threadsafe(self.events.put_nowait, payload)
        except RuntimeError:
            # loop already closed during shutdown
            pass

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def provider_info(self) -> Dict[str, Any]:
        return {
            "type": self.config.get("provider", {}).get("type", "nemapi"),
            "model": getattr(self.provider, "model", ""),
            "host": getattr(self.provider, "host", ""),
            "port": getattr(self.provider, "port", ""),
            "approval_mode": self.approval_mode,
        }

    # ------------------------------------------------------------------
    # control channel (called from the async side)
    # ------------------------------------------------------------------
    def request_interrupt(self) -> None:
        self._interrupt.set()
        for waiter in list(self._auth_waiters.values()):
            waiter.set()

    def resolve_authorization(self, request_id: str, choice: str) -> None:
        self._auth_results[request_id] = (choice or "n").strip().lower()
        waiter = self._auth_waiters.get(request_id)
        if waiter is not None:
            waiter.set()

    def set_model(self, model: str) -> bool:
        self.provider.model = model
        return True

    def set_approval_mode(self, mode: str) -> None:
        if mode in ("all", "confirm"):
            self.approval_mode = mode
            if mode == "all":
                self._authorized.clear()

    def start_turn(self, message: str) -> bool:
        if self._busy.is_set():
            self.emit("error", message="Un tour est déjà en cours.")
            return False
        self._busy.set()
        self._interrupt.clear()
        self.loop.run_in_executor(None, self._run_turn_safe, message)
        return True

    # ------------------------------------------------------------------
    # worker thread
    # ------------------------------------------------------------------
    def _run_turn_safe(self, message: str) -> None:
        start = time.time()
        tool_count = 0
        try:
            tool_count = self._run_turn(message)
        except Exception as exc:  # pragma: no cover - defensive
            self.emit("error", message=f"Erreur inattendue de l'agent: {exc}")
        finally:
            self._busy.clear()
            self.emit(
                "turn_end",
                elapsed=round(time.time() - start, 2),
                tool_count=tool_count,
                at=time.time(),
            )

    def _run_turn(self, message: str) -> int:
        if self._conn_ok is None:
            # Probe once, but never let a slow probe delay the first token:
            # if the provider itself is reachable enough to answer, skip.
            try:
                self._conn_ok = self.provider.test_connection(max_age=20.0)
            except TypeError:
                self._conn_ok = self.provider.test_connection()
        if self._conn_ok is False:
            self.emit(
                "error",
                message="NEMAPI est injoignable. Vérifiez l'hôte et le port dans les réglages.",
            )
            return 0

        # The system prompt (tools catalog + memory) is merged into the first
        # user message so a new session costs a single round-trip instead of two.
        # It is only ever sent once per logical session: resuming an existing
        # conversation never resends the prompt or the tool catalogue.
        system_prefix = ""
        prompt_will_send = False
        if not self._prompt_sent:
            if hasattr(self.executor, "get_system_prompt"):
                system_prefix = self.executor.get_system_prompt() or ""
            prompt_will_send = bool(system_prefix)
            self._prompt_sent = True

        current_input = message
        current_role = "user"
        iteration = 0
        last_hash: Optional[int] = None
        tool_count = 0

        while iteration < MAX_TOOL_ITERATIONS:
            if self._interrupt.is_set():
                self.emit("notice", message="Tour interrompu par l'utilisateur.")
                break
            iteration += 1
            self.emit("status", state="thinking", at=time.time())

            try:
                try:
                    result = self.provider.send_message(
                        current_input,
                        role=current_role,
                        cancel_event=self._interrupt,
                        system_prefix=system_prefix,
                    )
                except TypeError:
                    if system_prefix and current_role == "user":
                        result = self.provider.send_message(
                            f"[SYSTEM INSTRUCTIONS]\n{system_prefix}\n\n[USER MESSAGE]\n{current_input}",
                            role=current_role,
                        )
                    else:
                        result = self.provider.send_message(current_input, role=current_role)
            except Exception as exc:
                self.emit("error", message=str(exc))
                break
            system_prefix = ""

            if self._interrupt.is_set():
                self.emit("notice", message="Tour interrompu par l'utilisateur.")
                break

            if not result.get("success"):
                self.emit("error", message=result.get("error", "Erreur inconnue du provider."))
                break

            # Once the first request of a session succeeds, the NEMAPI
            # conversation now holds the system prompt: persist that fact so a
            # later resume never resends it nor restarts the chat.
            if prompt_will_send and not self._session_initialized:
                prompt_will_send = False
                self._session_initialized = True
                if self.session_id:
                    try:
                        from web.backend import sessions as session_store
                        session_store.mark_initialized(self.session_id)
                    except Exception:
                        pass

            raw = result.get("response", "") or ""
            parsed = self.parser.parse(raw)

            for native_call in result.get("tool_calls") or []:
                if isinstance(native_call, dict):
                    parsed.setdefault("actions", []).append({
                        "type": native_call.get("type", ""),
                        "content": native_call.get("content", {}),
                    })

            response_hash = hash(raw.strip())
            if response_hash == last_hash and iteration > 1:
                if parsed.get("text") and not str(parsed["text"]).startswith("FEEDBACK:"):
                    self.emit("assistant_text", text=parsed["text"], at=time.time())
                break
            last_hash = response_hash

            text = parsed.get("text")
            if text and not str(text).startswith("FEEDBACK:"):
                self.emit("assistant_text", text=text, at=time.time())

            actions = parsed.get("actions") or ([parsed["action"]] if parsed.get("action") else [])
            if not actions:
                break

            # Hard ceiling: never execute more than MAX_TOOL_CALLS_PER_MESSAGE
            # calls within a single model message, even if the model emitted more.
            if len(actions) > MAX_TOOL_CALLS_PER_MESSAGE:
                extra = len(actions) - MAX_TOOL_CALLS_PER_MESSAGE
                actions = actions[:MAX_TOOL_CALLS_PER_MESSAGE]
                self.emit(
                    "notice",
                    message=(
                        f"Le modèle a demandé {len(actions) + extra} appels en un seul message ; "
                        f"seuls les {MAX_TOOL_CALLS_PER_MESSAGE} premiers sont exécutés (limite)."
                    ),
                )

            batch_feedback: List[str] = []
            for action in actions:
                if self._interrupt.is_set():
                    break
                act_type = action.get("type", "")
                act_content = action.get("content") or {}
                if not isinstance(act_content, dict):
                    act_content = {"value": act_content}

                risk = "medium"
                if hasattr(self.executor, "risk_for"):
                    try:
                        risk = self.executor.risk_for(act_type, act_content)
                    except TypeError:
                        risk = self.executor.risk_for(act_type)

                if not self._authorize(act_type, act_content, risk):
                    self.emit("tool_denied", name=act_type, params=act_content, risk=risk)
                    batch_feedback.append(
                        f"Tool: {act_type}\nSuccess: false\nOutput:\n"
                        "[EXECUTION REFUSEE PAR L'UTILISATEUR]"
                    )
                    continue

                tool_count += 1
                self.emit("tool_start", name=act_type, params=act_content, risk=risk, at=time.time())

                final_res: Dict[str, Any] = {}
                tool_started = time.time()
                for update in self.executor.execute_tool(act_type, act_content):
                    if self._interrupt.is_set():
                        final_res = {"success": False, "stdout": "Interrompu par l'utilisateur"}
                        break
                    if "success" in update or (act_type != "bash" and update):
                        final_res = update
                    if act_type != "bash" and final_res:
                        break

                tool_elapsed = round(time.time() - tool_started, 2)
                output = (
                    final_res.get("output")
                    or final_res.get("stdout")
                    or final_res.get("error")
                    or ""
                )
                self.emit(
                    "tool_result",
                    name=act_type,
                    params=act_content,
                    success=bool(final_res.get("success", False)),
                    output=str(output),
                    duration=tool_elapsed,
                )
                batch_feedback.append(self._format_feedback(action, final_res))

            if self._interrupt.is_set():
                self.emit("notice", message="Tour interrompu par l'utilisateur.")
                break

            current_input = "FEEDBACK:\n" + "\n\n".join(batch_feedback)
            current_role = "tool_result"
            continue

        if iteration >= MAX_TOOL_ITERATIONS:
            self.emit("error", message=f"Limite de {MAX_TOOL_ITERATIONS} itérations atteinte.")

        return tool_count

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _authorize(self, name: str, params: Dict[str, Any], risk: str) -> bool:
        if self.approval_mode == "all":
            return True
        if risk == "read":
            return True
        if name in self._authorized:
            return True

        request_id = uuid.uuid4().hex[:8]
        waiter = threading.Event()
        self._auth_waiters[request_id] = waiter
        self.emit("authorize_request", id=request_id, name=name, params=params, risk=risk)

        deadline = time.time() + AUTHORIZE_TIMEOUT
        while not waiter.wait(1.0):
            if self._interrupt.is_set() or time.time() > deadline:
                self._auth_waiters.pop(request_id, None)
                self._auth_results.pop(request_id, None)
                return False

        self._auth_waiters.pop(request_id, None)
        choice = self._auth_results.pop(request_id, "n")
        if choice in ("a", "always"):
            self._authorized.add(name)
            self.emit("notice", message=f"'{name}' autorisé pour cette session.")
            return True
        return choice in ("y", "yes")

    @staticmethod
    def _format_feedback(action: Dict[str, Any], res: Dict[str, Any]) -> str:
        output = res.get("stdout", res.get("content", "")) or ""
        if not output:
            output = "[Aucune sortie capturee]"
        return (
            f"FEEDBACK:\nTool: {action.get('type', '')}\n"
            f"Succes: {res.get('success', False)}\nOutput:\n{output}"
        )


def list_tools(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Introspect the live registry exposed to the agent.

    Results are cached for a short TTL: the tool catalog only changes when the
    workspace or MCP config changes, so rebuilding it on every page load is
    wasted work.
    """
    cache_key = str(config.get("security", {}).get("workspace", "")) + "|" + str(
        id(config.get("provider", {}))
    )
    now = time.time()
    if (
        _TOOLS_CACHE["value"] is not None
        and _TOOLS_CACHE["key"] == cache_key
        and (now - _TOOLS_CACHE["at"]) < _TOOLS_CACHE_TTL
    ):
        return _TOOLS_CACHE["value"]

    executor = create_executor_from_config(config)
    registry = getattr(executor, "registry", None)
    if registry is None:
        return []
    tools: List[Dict[str, Any]] = []
    for name, reg in registry:
        definition = reg.definition
        try:
            risk = registry.risk_for(name)
        except Exception:
            risk = "medium"
        tools.append({
            "name": name,
            "description": getattr(definition, "description", ""),
            "risk": risk,
            "kind": getattr(getattr(definition, "kind", None), "value", None)
            or str(getattr(definition, "kind", "")),
        })
    tools.sort(key=lambda item: item["name"])
    _TOOLS_CACHE["key"] = cache_key
    _TOOLS_CACHE["value"] = tools
    _TOOLS_CACHE["at"] = now
    return tools


def list_models(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return available models from the provider plus the configured default.

    ``list_models`` is attempted even when the connectivity probe is
    inconclusive: some servers answer ``/v1/models`` while rejecting the
    ``/status`` probe used by ``test_connection``.
    """
    provider = create_provider(config)
    connected = False
    try:
        connected = provider.test_connection()
    except Exception:
        connected = False

    models: List[Dict[str, Any]] = []
    error: str = ""
    try:
        models = provider.list_models() or []
    except Exception as exc:
        error = str(exc)
    if models:
        connected = True

    current = getattr(provider, "model", "") or config.get("provider", {}).get("model", "")
    return {"connected": connected, "models": models, "current": current, "error": error}
