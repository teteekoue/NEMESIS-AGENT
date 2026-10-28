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
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from action_parser import ActionParser
from providers import create_provider
from tools import create_executor_from_config
from web.backend.browser_relay import attach_to_provider, relay_enabled

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

        # Created before the executor so a running bash command can be aborted
        # from the WebSocket thread (Stop button) even with no output.
        self._interrupt = threading.Event()

        self.provider = create_provider(config)
        attach_to_provider(self.provider, config)
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
        if hasattr(self.executor, "set_cancel_event"):
            self.executor.set_cancel_event(self._interrupt)
        if hasattr(self.executor, "set_subagent_authorizer"):
            self.executor.set_subagent_authorizer(self._authorize)

        valid_tools: set = set()
        registry = getattr(self.executor, "registry", None)
        if registry is not None:
            valid_tools = set(registry._tools.keys())
        self.parser = ActionParser(extra_valid_tools=valid_tools or None, strict=True)

        self._authorized: set = set()
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

    def bind_session(self, session_id: Optional[str]) -> None:
        """Attach a session created after the socket was opened.

        The web UI creates its session lazily on the first message, so a fresh
        ``/ws/chat`` connection can have no ``session`` query parameter. Late
        binding lets the runner persist ``nemapi_initialized`` and suppress the
        system prompt when the transcript shows an existing conversation.
        """
        sid = str(session_id or "").strip() or None
        if not sid or self.session_id is not None:
            return
        self.session_id = sid
        initialized = False
        try:
            from web.backend import sessions as session_store
            initialized = session_store.is_initialized(sid)
        except Exception:
            initialized = False
        self._session_initialized = initialized or self._session_initialized
        self._prompt_sent = self._prompt_sent or initialized
        if hasattr(self.provider, "configure_session"):
            self.provider.configure_session(sid, initialized=self._prompt_sent)

    def set_model(self, model: str) -> bool:
        self.provider.model = model
        return True

    def set_approval_mode(self, mode: str) -> None:
        if mode in ("all", "confirm"):
            self.approval_mode = mode
            if mode == "all":
                self._authorized.clear()

    def close(self) -> None:
        self.request_interrupt()
        if hasattr(self.executor, "close"):
            self.executor.close()

    def start_turn(self, message: str, images: Optional[List[Any]] = None) -> bool:
        if self._busy.is_set():
            self.emit("error", message="Un tour est déjà en cours.")
            return False
        self._busy.set()
        self._interrupt.clear()
        self.loop.run_in_executor(None, self._run_turn_safe, message, images or [])
        return True

    # ------------------------------------------------------------------
    # worker thread
    # ------------------------------------------------------------------
    def _run_turn_safe(self, message: str, images: Optional[List[Any]] = None) -> None:
        start = time.time()
        tool_count = 0
        try:
            tool_count = self._run_turn(message, images or [])
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

    def _run_turn(self, message: str, images: Optional[List[Any]] = None) -> int:
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
        # A session can be initialised by another connection while this runner
        # is alive (or bound late, see ``bind_session``). Recheck before every
        # turn so a resumed conversation never receives the prompt again.
        if not self._prompt_sent and self.session_id:
            try:
                from web.backend import sessions as session_store
                if session_store.is_initialized(self.session_id):
                    self._prompt_sent = True
                    self._session_initialized = True
                    if hasattr(self.provider, "configure_session"):
                        self.provider.configure_session(self.session_id, initialized=True)
            except Exception:
                pass

        system_prefix = ""
        if not self._prompt_sent:
            if hasattr(self.executor, "get_system_prompt"):
                system_prefix = self.executor.get_system_prompt() or ""
            self._prompt_sent = True

        current_input = message
        current_role = "user"
        pending_images: List[Any] = list(images or [])
        iteration = 0
        last_hash: Optional[int] = None
        tool_count = 0
        feedback_seq = 0
        protocol_errors = 0

        # No iteration ceiling: the turn ends when the model stops calling
        # tools, when the user interrupts, or when the provider fails.
        while True:
            if self._interrupt.is_set():
                self.emit("notice", message="Tour interrompu par l'utilisateur.")
                break
            iteration += 1
            self.emit("status", state="thinking", at=time.time())

            try:
                from src.core.image_attachments import send_message_with_images

                if pending_images:
                    self.emit(
                        "notice",
                        message=f"{len(pending_images)} image(s) transmise(s) au modèle en multimodal.",
                    )
                result = send_message_with_images(
                    self.provider,
                    current_input,
                    role=current_role,
                    images=pending_images,
                    cancel_event=self._interrupt,
                    system_prefix=system_prefix,
                )
                pending_images = []
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
            # later resume never resends it nor restarts the chat. This must not
            # depend on ``system_prefix`` being non-empty — an empty catalogue
            # still means the conversation has started.
            if not self._session_initialized:
                self._session_initialized = True
                if self.session_id:
                    try:
                        from web.backend import sessions as session_store
                        session_store.mark_initialized(self.session_id)
                    except Exception:
                        pass

            raw = result.get("response", "") or ""
            parsed = self.parser.parse(raw)

            protocol_error = parsed.get("protocol_error")
            # Un provider qui renvoie de vrais tool_calls natifs garde la
            # priorite, meme si son texte contient un marqueur XML residuel.
            if protocol_error and not (
                result.get("tool_calls") or result.get("tool_call")
            ):
                from src.core.agent_tools import protocol_error_feedback

                protocol_errors += 1
                feedback_seq += 1
                self.emit(
                    "notice",
                    message=f"Appel d'outil invalide ou tronqué ({protocol_errors}) : rien n'a été exécuté.",
                )
                current_input = protocol_error_feedback(protocol_error, feedback_seq)
                current_role = "tool_result"
                last_hash = hash(raw.strip())
                if protocol_errors >= 3:
                    self.emit(
                        "error",
                        message="Trop d'appels d'outils invalides consécutifs. Tour interrompu.",
                    )
                    break
                continue

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
                from src.core.image_attachments import collect_result_images

                found_images = collect_result_images(final_res)
                pending_images.extend(found_images)
                self.emit(
                    "tool_result",
                    name=act_type,
                    params=act_content,
                    success=bool(final_res.get("success", False)),
                    output=str(output),
                    exit_code=final_res.get("exit_code"),
                    duration=tool_elapsed,
                    image_count=len(found_images),
                )
                batch_feedback.append(self._format_feedback(action, final_res))

            if self._interrupt.is_set():
                self.emit("notice", message="Tour interrompu par l'utilisateur.")
                break

            from src.core.agent_tools import build_feedback_message

            protocol_errors = 0
            feedback_seq += 1
            current_input = build_feedback_message(batch_feedback, feedback_seq)
            current_role = "tool_result"
            continue

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
        from src.core.agent_tools import format_tool_feedback

        return "FEEDBACK:\n" + format_tool_feedback(action.get("type", ""), res)


def list_tools(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Introspect the live registry exposed to the agent.

    Results are cached for a short TTL: the tool catalog only changes when the
    workspace or MCP config changes, so rebuilding it on every page load is
    wasted work.
    """
    cache_key = str(config.get("security", {}).get("workspace", "")) + "|" + str(
        id(config.get("provider", {}))
    ) + "|" + os.getcwd()
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
    attach_to_provider(provider, config)
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
        lowered = error.lower()
        nemapi = config.get("nemapi") or {}
        blocked = any(word in lowered for word in (
            "ssl", "tls", "max retries", "connection", "timed out", "name or service not known",
        ))
        if not relay_enabled(config) and nemapi.get("url") and blocked:
            error += (
                "\n\nLe serveur ne peut pas joindre cette URL (sortie réseau bloquée). "
                "Active « Relais navigateur » dans Réglages → Connexion : les requêtes "
                "passeront alors par ton navigateur."
            )
    if models:
        connected = True

    current = getattr(provider, "model", "") or config.get("provider", {}).get("model", "")
    return {"connected": connected, "models": models, "current": current, "error": error}
