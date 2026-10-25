"""A2A-compliant Agent Manager for NEMESIS CLI sub-agents.

Manages sub-agent lifecycles using the A2A protocol (Agent-to-Agent).
Each sub-agent uses the same NEMAPI server-side context as the main agent.
"""

from __future__ import annotations

import json
import os
import time
import uuid
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

def _a2a_debug(msg: str) -> None:
    """Internal debug — never printed on the main UI unless NEMESIS_DEBUG=1."""
    if os.environ.get("NEMESIS_DEBUG", "").strip() in ("1", "true", "yes"):
        import sys
        print(msg, file=sys.stderr)

from .a2a_protocol import (
    A2AEnvelope,
    A2AMessageType,
    A2ACoordinator,
    AgentManifest,
    TaskManifest,
    TaskReport,
    TaskStatus,
    TaskPriority,
    TaskArtifact,
    AgentCapability,
)


# Subagents use the same server-side-context NEMAPI provider as the main agent.
NEMAPI_V3_DEFAULT_HOST = "127.0.0.1"
NEMAPI_V3_DEFAULT_PORT = 8090
NEMAPI_V3_DEFAULT_MODEL = "qwen-chat"
# A2A sub-agents: NemAPI v3 only (same as main agent context model)
PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "nemapi": {
        "base_url": f"http://{NEMAPI_V3_DEFAULT_HOST}:{NEMAPI_V3_DEFAULT_PORT}/v1",
        "default_model": NEMAPI_V3_DEFAULT_MODEL,
        "kind": "nemapi_v3",
    },
}


# Tools the teammate must NOT use (meta / would recurse into A2A)
_TEAMMATE_EXCLUDED_TOOLS = frozenset({
    "list_agents", "delegate_task", "check_reports", "agent_status", "skills_list",
})

DEFAULT_SUBAGENT_SYSTEM = """You are a NEMESIS teammate — a peer coding agent with the same tools and standards as the main NEMESIS agent. You work on a delegated task in parallel while NEMESIS handles other work. Be efficient, precise, and autonomous.

## Core rules (non-negotiable)

1. **Batch independent tools.** Emit one JSON tool call or a JSON array of independent calls. Keep dependent writes ordered.
2. **JSON only for actions.** Tool calls and completion reports are pure JSON (optionally inside a ```json fence). No YAML, no pseudo-tools.
3. **Valid JSON.** Escape `"`, `\\`, newlines (`\\n`), tabs. No trailing commas.
4. **Read before edit.** Call `read_file` before `edit`. Copy exact text from FEEDBACK (ignore `LINE_NUMBER→` prefixes).
5. **One step at a time.** Inspect each FEEDBACK before the next action.
6. **Safety.** For destructive or irreversible actions (rm -rf, force-push, drop tables), state intent briefly; prefer safer alternatives when possible.
7. **No invented tools.** Only use tools listed below. Never invent `task_report` as a tool name — completion uses the report format at the end of this prompt.

## Tool-call format (mandatory)

```json
{"tool": "tool_name", "parameters": {"param1": "value1"}}
```

Equivalent keys accepted: `"tool"` / `"name"` / `"action"` and `"parameters"` / `"arguments"` / `"params"`.

Examples:
```json
{"tool": "write_file", "parameters": {"file_path": "hello.py", "content": "print(42)\\n"}}
```
```json
{"tool": "bash", "parameters": {"command": "python3 hello.py"}}
```
```json
{"tool": "read_file", "parameters": {"path": "hello.py"}}
```

## Available tools

__TOOLS_SECTION__

## Workflow

1. Understand the task. If useful, `todo` to track steps.
2. Batch safe discovery calls, then use their combined FEEDBACK to adapt.
3. Prefer `edit` over full `write_file` for small changes; `read_file` before editing.
4. Verify with `bash` (tests, lint) when the task requires it.
5. When the task is fully done, emit a completion report (not a tool call):

```json
{"type": "task_report", "status": "completed", "summary": "short summary", "artifacts": ["file1.py"], "errors": []}
```

On failure: `"status": "failed"` and put the reason in `summary` / `errors`.

## Style

- Concise and professional. No filler.
- Do not narrate plans at length — act.
- Do not invent FEEDBACK or tool results.
- You are a teammate of NEMESIS, not a subordinate with fewer capabilities: same tools, same quality bar.
"""


class A2AAgentClient:
    """NEMESIS teammate agent — same tools as the main agent, A2A task protocol.

    Peer worker for parallel tasks. Backed by any OpenAI-compatible chat API
    (Groq, OpenAI, xAI, Ollama, ...).
    """

    def __init__(
        self,
        name: str,
        api_key: str = "",
        provider: str = "nemapi",
        model: str = "",
        base_url: str = "",
        executor: Optional[Any] = None,
        host: str = "",
        port: int = 0,
    ):
        self.name = name
        self.provider = (provider or "nemapi").lower().strip().replace("-v3", "")
        if self.provider not in PROVIDER_PRESETS:
            self.provider = "nemapi"
        preset = PROVIDER_PRESETS["nemapi"]
        self._kind = preset.get("kind", "openai")

        if self.provider == "nemapi":
            self._kind = "nemapi_v3"
            self.host = (host or os.environ.get("NEMAPI_HOST") or NEMAPI_V3_DEFAULT_HOST).strip()
            self.port = int(port or os.environ.get("NEMAPI_PORT") or NEMAPI_V3_DEFAULT_PORT)
            self.base_url = (base_url or f"http://{self.host}:{self.port}/v1").rstrip("/")
            self.model = (model or NEMAPI_V3_DEFAULT_MODEL).strip()
            self.api_key = api_key or "nemapi"
        else:
            self.host = host or ""
            self.port = int(port or 0)
            self.base_url = (base_url or preset.get("base_url")).rstrip("/")
            self.model = model or preset.get("default_model") or NEMAPI_V3_DEFAULT_MODEL
            self.api_key = api_key or "nemapi"

        self._client = None
        self._req = None
        self._conversation: List[dict] = []
        self._system_prompt_loaded = False
        self._manifest: Optional[AgentManifest] = None
        self._last_heartbeat: float = 0.0
        self._status: str = "idle"
        self._current_task_id: Optional[str] = None
        self._error: Optional[str] = None
        self._executor = executor
        self._tools_cache = None
        self._interrupted = False

        self._init_client()

    def _init_client(self) -> None:
        """Init HTTP client for NemAPI v3 or OpenAI-compatible APIs (Groq, ...)."""
        try:
            import requests as req
            self._req = req
            if self._kind == "nemapi_v3":
                try:
                    req.get(f"http://{self.host}:{self.port}/status", timeout=3)
                except Exception:
                    pass
                self._client = "nemapi_v3"
                self._error = None
            else:
                if not self.api_key:
                    self._error = f"API key manquante pour provider {self.provider}"
                    self._client = None
                    return
                self._client = "openai_compat"
                self._error = None
        except Exception as e:
            self._error = f"Failed to init client: {e}"
            self._client = None
            self._req = None


    def to_config(self) -> Dict[str, Any]:
        return {
            "api_key": self.api_key,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "host": getattr(self, "host", ""),
            "port": getattr(self, "port", 0),
        }

    @classmethod
    def from_config(cls, name: str, cfg: Dict[str, Any]) -> "A2AAgentClient":
        return cls(
            name=name,
            api_key=cfg.get("api_key", ""),
            provider="nemapi",
            model=cfg.get("model", ""),
            base_url=cfg.get("base_url", ""),
            host=cfg.get("host", ""),
            port=int(cfg.get("port") or 0),
        )

    def set_executor(self, executor: Any) -> None:
        """Définit l'exécuteur d'outils pour le subagent."""
        self._executor = executor
        # Do NOT auto-enable native OpenAI tool_calls for A2A sub-agents.
        # Many providers/models (Groq free-tier, Ollama, etc.) reject tools= payload.
        # A2A relies on text JSON tool calls which work universally.
        # Call set_tools(...) explicitly if native tool_calls are desired.
        self._tools_cache = None

    def set_tools(self, tools: List[dict]) -> None:
        """Définit les outils disponibles pour le subagent."""
        self._tools_cache = tools

    def _run_executor_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a tool via the attached executor and normalize the result.

        ToolBridge.execute_tool is a generator that yields result dicts.
        This helper consumes it and returns the final (or only) result dict.
        """
        if not self._executor:
            return {"success": False, "stdout": f"No executor available for tool {tool_name}"}

        try:
            gen_or_result = self._executor.execute_tool(tool_name, params)

            # Generator path (ToolBridge / modern executor)
            if hasattr(gen_or_result, "__iter__") and not isinstance(gen_or_result, (str, dict, bytes)):
                last = None
                for update in gen_or_result:
                    last = update
                if isinstance(last, dict):
                    return last
                if last is not None:
                    return {"success": True, "stdout": str(last)}
                return {"success": False, "stdout": "Tool returned no result"}

            # Direct dict / object path (legacy)
            if isinstance(gen_or_result, dict):
                return gen_or_result
            return {"success": True, "stdout": str(gen_or_result)}
        except Exception as e:
            return {"success": False, "stdout": f"Erreur outil '{tool_name}': {e}"}

    @staticmethod
    def _extract_json_objects(text: str) -> List[str]:
        """Extract candidate JSON object strings from free text (handles nesting)."""
        import re
        candidates = []
        # Prefer fenced code blocks first
        for m in re.finditer(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL | re.IGNORECASE):
            candidates.append(m.group(1).strip())
        # Then scan for balanced {...}
        i = 0
        while i < len(text):
            if text[i] == "{":
                depth = 0
                in_str = False
                escape = False
                for j in range(i, len(text)):
                    c = text[j]
                    if in_str:
                        if escape:
                            escape = False
                        elif c == "\\":
                            escape = True
                        elif c == '"':
                            in_str = False
                        continue
                    if c == '"':
                        in_str = True
                    elif c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            candidates.append(text[i : j + 1])
                            i = j
                            break
                else:
                    break
            i += 1
        return candidates

    def _parse_tool_call(self, parsed: dict):
        """Return (tool_name, params) from various JSON tool-call shapes, or (None, None)."""
        if not isinstance(parsed, dict):
            return None, None

        # Skip pure A2A / report envelopes
        if parsed.get("a2a_version") or parsed.get("type") in (
            "task_report", "task_ack", "task_progress", "capability_report",
            "heartbeat_ack", "error", "cancel_ack", "capability_query",
            "task_assign", "heartbeat", "cancel",
        ):
            return None, None
        if "name" in parsed and "arguments" in parsed:
            args = parsed["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            return parsed["name"], args if isinstance(args, dict) else {}
        if "tool" in parsed and ("parameters" in parsed or "arguments" in parsed or "params" in parsed):
            params = parsed.get("parameters") or parsed.get("arguments") or parsed.get("params") or {}
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except Exception:
                    params = {}
            return parsed["tool"], params if isinstance(params, dict) else {}
        if "action" in parsed and ("params" in parsed or "parameters" in parsed or "arguments" in parsed):
            params = parsed.get("params") or parsed.get("parameters") or parsed.get("arguments") or {}
            return parsed["action"], params if isinstance(params, dict) else {}
        if "tool_name" in parsed and ("tool_arguments" in parsed or "parameters" in parsed):
            args = parsed.get("tool_arguments") or parsed.get("parameters") or {}
            return parsed["tool_name"], args if isinstance(args, dict) else {}
        # Single-key form: {"write_file": {...}}
        if len(parsed) == 1:
            key = next(iter(parsed))
            val = parsed[key]
            if isinstance(val, dict) and key not in (
                "a2a_version", "type", "payload", "task_id", "status", "summary",
            ):
                return key, val
        return None, None

    def _detect_tool_call(self, response_text: str):
        """Scan response text for the first valid tool-call JSON. Returns (name, params) or (None, None)."""
        if not response_text or not response_text.strip():
            return None, None

        # Prefer ActionParser when available (same as main agent)
        try:
            from action_parser import ActionParser
            parser = ActionParser()
            result = parser.parse(response_text)
            if result and isinstance(result, dict):
                # Format 1: canonical {"tool":..., "parameters":...}
                tname = result.get("tool") or result.get("name")
                params = result.get("parameters") or result.get("arguments") or result.get("params")
                if tname and isinstance(params, dict):
                    return tname, params
                # Format 2: ActionParser internal {"action": {"type":..., "content":...}}
                action = result.get("action")
                if isinstance(action, dict):
                    tname = action.get("type") or action.get("tool") or action.get("name")
                    params = action.get("content") or action.get("parameters") or action.get("arguments") or {}
                    if tname and tname not in ("task_report", "done", "finish"):
                        return tname, params if isinstance(params, dict) else {}
                if isinstance(action, str) and action not in ("task_report", "done", "finish"):
                    params = result.get("content") or result.get("parameters") or {}
                    return action, params if isinstance(params, dict) else {}
        except Exception as e:
            _a2a_debug(f"[DEBUG] ActionParser fallback: {e}")
        for json_str in self._extract_json_objects(response_text):
            try:
                parsed = json.loads(json_str)
            except (json.JSONDecodeError, TypeError):
                continue
            tool_name, params = self._parse_tool_call(parsed)
            if tool_name:
                return tool_name, params or {}
        return None, None

    def _detect_tool_calls(self, response_text: str):
        """Return all canonical tool calls in a model turn."""
        try:
            from action_parser import ActionParser
            parsed = ActionParser().parse(response_text)
            actions = parsed.get("actions") or []
            return [
                (item.get("type"), item.get("content") or {})
                for item in actions
                if isinstance(item, dict) and item.get("type")
            ]
        except Exception as e:
            _a2a_debug(f"[DEBUG] batch tool parser failed: {e}")
            one = self._detect_tool_call(response_text)
            return [one] if one[0] else []

    def _call_llm(self, messages: List[dict]) -> str:
        """Call NemAPI v3 LLM; return assistant text.

        Context model matches the main agent / NemapiV3Provider:
        the remote session (browser tab) holds history — we only send
        the *current* message, never the full conversation array.
        """
        if not getattr(self, "_req", None):
            raise RuntimeError(self._error or "HTTP client not initialized")

        # NemAPI v3: one message per request (server-side session context)
        if self._kind == "nemapi_v3" and messages:
            send_messages = [messages[-1]]
        else:
            send_messages = messages

        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": send_messages,
            "stream": False,
            "temperature": 0.3,
            "max_tokens": 4096,
        }
        headers = {"Content-Type": "application/json"}
        if self._kind != "nemapi_v3" and self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_err = ""
        for attempt in range(1, 6):
            resp = self._req.post(url, json=payload, headers=headers, timeout=180)
            if resp.status_code == 200:
                break
            last_err = f"LLM HTTP {resp.status_code}: {resp.text[:500]}"
            # Rate limit / transient
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                wait = min(2 ** attempt, 20)
                _a2a_debug(f"[DEBUG] {self.name} LLM {resp.status_code}, retry in {wait}s (attempt {attempt}/5)")
                time.sleep(wait)
                continue
            raise RuntimeError(last_err)
        else:
            raise RuntimeError(last_err or "LLM call failed")

        text_body = resp.text or ""
        try:
            data = json.loads(text_body)
            if isinstance(data, dict) and data.get("choices"):
                msg = data["choices"][0].get("message") or {}
                content = (msg.get("content") or "") if isinstance(msg, dict) else str(msg)
                if "<think>" in content:
                    import re as _re
                    content = _re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
                return content
        except json.JSONDecodeError:
            pass

        if "data:" in text_body:
            chunks = []
            for line in text_body.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    continue
                try:
                    data = json.loads(chunk)
                    delta = (data.get("choices") or [{}])[0].get("delta") or {}
                    if delta.get("content"):
                        chunks.append(delta["content"])
                except json.JSONDecodeError:
                    continue
            if chunks:
                return "".join(chunks)
        return text_body


    # ── Public API ──

    def send_message(self, message: str, role: str = "user", _tool_depth: int = 0) -> str:
        """Send a message and get a response string, with tool execution support.

        _tool_depth: internal counter to prevent infinite tool-call recursion.
        Uses NemAPI v3 only. Parses text JSON tool calls and executes them
        via the shared ActionExecutor.
        """
        self._ensure_system_prompt()
        self._conversation.append({
            "role": role if role != "tool_result" else "user",
            "content": message,
        })

        if not self._client:
            return json.dumps({
                "a2a_version": "1.0",
                "type": "error",
                "payload": {"error": self._error or "NemAPI v3 client not initialized."},
            })

        max_tool_depth = 15
        try:
            # Keep conversation bounded
            if len(self._conversation) > 40:
                system = [m for m in self._conversation if m.get("role") == "system"]
                rest = [m for m in self._conversation if m.get("role") != "system"]
                self._conversation = system + rest[-30:]

            response_text = self._call_llm(self._conversation)
            _a2a_debug(f"[DEBUG] Agent {self.name} réponse LLM: {(response_text or '')[:300]}")
            self._conversation.append({"role": "assistant", "content": response_text or ""})

            # --- Detect and execute text JSON tool calls ---
            if self._executor and (response_text or "").strip() and _tool_depth < max_tool_depth:
                tool_calls = self._detect_tool_calls(response_text)
                if tool_calls:
                    feedback_parts = []
                    for tool_name, params in tool_calls:
                        if tool_name in (
                            "task_report", "task_ack", "task_progress", "done", "finish",
                            "capability_report", "heartbeat_ack", "error", "cancel_ack",
                        ):
                            continue
                        _a2a_debug(f"[DEBUG] Tool call détecté: {tool_name}")
                        result = self._run_executor_tool(tool_name, params or {})
                        success = result.get("success", True)
                        output = result.get("stdout", result.get("content", str(result)))
                        out_str = str(output)
                        if len(out_str) > 12000:
                            out_str = out_str[:400000] + "\n...[truncated]..."
                        feedback_parts.append(
                            f"Tool: {tool_name}\nSucces: {success}\nOutput:\n{out_str}"
                        )
                    if feedback_parts:
                        feedback = "FEEDBACK:\n" + "\n\n".join(feedback_parts)
                        return self.send_message(feedback, role="user", _tool_depth=_tool_depth + 1)

            return response_text or ""

        except Exception as e:
            error_msg = f"Agent {self.name} error: {e}"
            self._error = error_msg
            return json.dumps({
                "a2a_version": "1.0",
                "type": "error",
                "payload": {"error": error_msg},
            })

    def query_capabilities(self) -> Optional[AgentManifest]:
        envelope = A2AEnvelope.create(
            sender="nemesis",
            recipient=self.name,
            msg_type=A2AMessageType.CAPABILITY_QUERY,
            payload={},
        )
        raw = self.send_message(envelope.to_json())
        parsed = self._parse_a2a_response(raw)
        if parsed and parsed.type == A2AMessageType.CAPABILITY_REPORT:
            caps_raw = parsed.payload.get("capabilities", [])
            valid = set(c.value for c in AgentCapability)
            manifest = AgentManifest(
                name=self.name,
                version=parsed.payload.get("version", "1.0"),
                description=parsed.payload.get("description", ""),
                capabilities=[AgentCapability(c) for c in caps_raw if c in valid],
                domains=parsed.payload.get("domains", []),
                max_concurrent_tasks=parsed.payload.get("max_concurrent_tasks", 1),
                max_tokens=parsed.payload.get("max_tokens", 4096),
                model=parsed.payload.get("model", self.model),
            )
            self._manifest = manifest
            return manifest
        # Soft fallback: declare generic coding capabilities without a report
        self._manifest = AgentManifest(
            name=self.name,
            description=f"Sub-agent via {self.provider}/{self.model}",
            capabilities=[
                AgentCapability.READ_FILES,
                AgentCapability.WRITE_FILES,
                AgentCapability.EDIT_FILES,
                AgentCapability.EXECUTE_BASH,
                AgentCapability.SEARCH_CODE,
                AgentCapability.PYTHON_DEV,
            ],
            model=self.model,
        )
        return self._manifest

    def assign_task(self, manifest: TaskManifest) -> Optional[dict]:
        """Assign a task to the subagent and execute it with tool support."""
        envelope = A2AEnvelope.create(
            sender="nemesis",
            recipient=self.name,
            msg_type=A2AMessageType.TASK_ASSIGN,
            payload=manifest.to_payload(),
        )
        self._current_task_id = manifest.task_id
        self._status = "busy"

        # First message: models often skip ACK and start working immediately.
        # send_message already executes tool calls recursively until a final text reply.
        raw = self.send_message(
            "Task assigned (A2A). Work autonomously, batching independent tool calls "
            "when useful, and use FEEDBACK to continue. When fully done emit task_report JSON.\n\n"
            f"task_id: {manifest.task_id}\n"
            f"label: {manifest.label}\n"
            f"description: {manifest.description}\n"
            f"instructions: {manifest.instructions}"
        )
        parsed = self._parse_a2a_response(raw)
        if parsed and parsed.type == A2AMessageType.TASK_REPORT:
            self._status = "idle"
            return {
                "status": parsed.payload.get("status", "completed"),
                "task_id": manifest.task_id,
                "report": parsed.payload,
            }
        if parsed and parsed.type == A2AMessageType.ERROR:
            err = str(parsed.payload.get("error", ""))
            if not any(x in err for x in ("429", "Rate limit", "502", "503")):
                self._status = "idle"
                return {"status": "failed", "task_id": manifest.task_id, "error": err}
        # Continue multi-turn loop if work not finished yet
        return self._execute_task_loop(manifest)

    def _execute_task_loop(self, manifest: TaskManifest) -> Optional[dict]:
        """Boucle d'exécution de la tâche avec support des outils.

        Pas de plafond d'itérations : la boucle s'arrête sur rapport final,
        erreur définitive ou interruption.
        """
        iteration = 0

        while not self._interrupted:
            iteration += 1

            if iteration == 1:
                prompt = (
                    "Task for you (NEMESIS teammate). "
                    "Batch independent tool calls when useful, use FEEDBACK to continue, and "
                    "When fully done, emit a task_report JSON (type=task_report, status=completed).\n\n"
                    f"{manifest.description}"
                )
            else:
                prompt = (
                    "Continue the task. Use one JSON tool call or a JSON array of "
                    "independent calls — or a final task_report if done."
                )

            response = self.send_message(prompt, role="user")

            # Feedback d'outil déjà traité en interne par send_message → continuer
            if isinstance(response, str) and ("FEEDBACK:" in response or response.startswith("FEEDBACK:")):
                continue

            parsed = self._parse_a2a_response(response)
            if parsed:
                if parsed.type == A2AMessageType.TASK_REPORT:
                    self._status = "idle"
                    return {
                        "status": parsed.payload.get("status", "completed"),
                        "task_id": manifest.task_id,
                        "report": parsed.payload,
                    }
                if parsed.type == A2AMessageType.TASK_PROGRESS:
                    continue
                if parsed.type == A2AMessageType.ERROR:
                    err = str(parsed.payload.get("error", "Unknown error"))
                    # Transient rate-limit / server errors: retry next iteration
                    if any(x in err for x in ("429", "Rate limit", "rate_limit", "502", "503", "504")):
                        _a2a_debug(f"[DEBUG] {self.name} transient error, will retry: {err[:120]}")
                        time.sleep(3)
                        continue
                    self._status = "idle"
                    return {
                        "status": "failed",
                        "task_id": manifest.task_id,
                        "error": err,
                    }
            # Réponse textuelle non-A2A : on laisse l'agent continuer
            continue

        self._status = "idle"
        return {
            "status": "cancelled",
            "task_id": manifest.task_id,
            "error": "Task interrupted",
        }

    def get_progress(self) -> Optional[dict]:
        if not self._current_task_id:
            return None
        for msg in reversed(self._conversation):
            if msg["role"] == "assistant":
                parsed = self._parse_a2a_response(msg["content"])
                if parsed and parsed.type == A2AMessageType.TASK_PROGRESS:
                    return parsed.payload
                break
        return None

    def collect_report(self) -> Optional[TaskReport]:
        """Scan conversation for a task_report envelope."""
        for msg in reversed(self._conversation):
            if msg["role"] != "assistant":
                continue
            parsed = self._parse_a2a_response(msg["content"])
            if parsed and parsed.type == A2AMessageType.TASK_REPORT:
                p = parsed.payload
                status_raw = p.get("status", "completed")
                try:
                    status = TaskStatus(status_raw)
                except ValueError:
                    status = TaskStatus.COMPLETED
                report = TaskReport(
                    task_id=p.get("task_id", self._current_task_id or ""),
                    status=status,
                    summary=p.get("summary", ""),
                    artifacts=[TaskArtifact(**a) for a in p.get("artifacts", []) if isinstance(a, dict)],
                    errors=p.get("errors", []),
                    stats=p.get("stats", {}),
                )
                self._status = "idle"
                self._current_task_id = None
                return report
        return None

    def send_feedback(self, feedback: str) -> str:
        return self.send_message(f"FEEDBACK:\n{feedback}", role="user")

    def reset_conversation(self) -> None:
        system = [m for m in self._conversation if m.get("role") == "system"]
        self._conversation = system

    @property
    def status(self) -> str:
        return self._status

    @property
    def manifest(self) -> Optional[AgentManifest]:
        return self._manifest

    @property
    def error(self) -> Optional[str]:
        return self._error

    def _build_tools_section(self) -> str:
        """Build the tools documentation block from the live registry (same tools as NEMESIS)."""
        fallback = (
            "- **read_file**: Read file(s). Params: path / paths, offset, limit\n"
            "- **write_file**: Create/overwrite a file. Params: file_path (or path), content\n"
            "- **edit**: Surgical replace. Params: file_path, old_string, new_string, replace_all?\n"
            "- **delete_file**: Delete a file. Params: target_file (or path / file_path)\n"
            "- **list_dir**: List directory. Params: path?\n"
            "- **grep**: Regex search. Params: pattern, path?, include?, case_insensitive?\n"
            "- **glob**: Find files by pattern. Params: pattern, path?, max_results?\n"
            "- **bash**: Run shell command. Params: command, mode? (synchrone|asynchrone)\n"
            "- **get_task_output**: Output of background bash. Params: task_id\n"
            "- **kill_task**: Kill background task. Params: task_id\n"
            "- **git**: Repo status/diff/log/branch. Params: action, path?, limit?\n"
            "- **todo**: Task list. Params: action (list|add|update|clear), content?, items?, id?, status?\n"
            "- **apply_patch**: Apply unified diff. Params: patch, dry_run?\n"
            "- **web_search**: Web search. Params: query, max_results?\n"
            "- **web_fetch**: Fetch URL. Params: url, format? (markdown|html|text)\n"
            "- **mcp_list** / **mcp_tools_list** / **mcp_call**: MCP servers and tools"
        )
        registry = None
        if self._executor is not None and hasattr(self._executor, "registry"):
            registry = self._executor.registry
        if registry is None:
            return fallback

        lines = []
        try:
            # ToolRegistry iterates or exposes _tools
            tools_map = getattr(registry, "_tools", None)
            if tools_map is None and hasattr(registry, "get_definitions"):
                for defn in registry.get_definitions():
                    name = getattr(defn, "name", None)
                    if not name or name in _TEAMMATE_EXCLUDED_TOOLS:
                        continue
                    desc = (getattr(defn, "description", "") or "").strip().split("\n")[0][:140]
                    lines.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")
            else:
                for name, reg in (tools_map or {}).items():
                    if name in _TEAMMATE_EXCLUDED_TOOLS:
                        continue
                    defn = getattr(reg, "definition", None)
                    desc = ""
                    if defn is not None:
                        desc = (getattr(defn, "description", "") or "").strip().split("\n")[0][:140]
                    # Prefer short param list from schema
                    schema = getattr(defn, "params_schema", None) or {}
                    props = schema.get("properties") or {}
                    required = set(schema.get("required") or [])
                    if props:
                        parts = []
                        for p, meta in props.items():
                            mark = "" if p in required else "?"
                            parts.append(f"{p}{mark}")
                        param_hint = ", ".join(parts[:8])
                        if desc:
                            lines.append(f"- **{name}**: {desc} — params: {param_hint}")
                        else:
                            lines.append(f"- **{name}** — params: {param_hint}")
                    else:
                        lines.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")
        except Exception:
            return fallback

        return "\n".join(lines) if lines else fallback

    def _ensure_system_prompt(self) -> None:
        if self._system_prompt_loaded:
            return
        self._system_prompt_loaded = True
        content = DEFAULT_SUBAGENT_SYSTEM
        p_path = Path(__file__).resolve().parent.parent / "prompts" / "agent_subordinate_prompt.txt"
        if not p_path.exists():
            p_path = Path("agent_subordinate_prompt.txt")
        if p_path.exists():
            try:
                content = p_path.read_text(encoding="utf-8")
            except OSError:
                pass
        tools_section = self._build_tools_section()
        if "__TOOLS_SECTION__" in content:
            content = content.replace("__TOOLS_SECTION__", tools_section)
        elif "## Available tools" not in content and "Available Tools" not in content:
            content = content.rstrip() + "\n\n## Available tools\n\n" + tools_section + "\n"
        self._conversation.insert(0, {"role": "system", "content": content})
        # NemAPI v3: session context is server-side — push system prompt once
        # as a standalone message (same as main agent), then continue with
        # single-message turns only.
        if self._kind == "nemapi_v3" and self._client:
            try:
                self._call_llm([{"role": "system", "content": content}])
            except Exception as e:
                _a2a_debug(f"[DEBUG] {self.name} system prompt send failed: {e}")

    def _parse_a2a_response(self, raw: str) -> Optional[A2AEnvelope]:
        if not raw:
            return None

        def _try_build(data: dict) -> Optional[A2AEnvelope]:
            if not isinstance(data, dict):
                return None
            if "type" in data:
                try:
                    if "a2a_version" not in data:
                        data = dict(data)
                        data["a2a_version"] = "1.0"
                    return A2AEnvelope.from_json(json.dumps(data, default=str))
                except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                    pass
            # Loose task_report shapes models often emit
            status = data.get("status")
            if status in ("completed", "failed", "done") and (
                "summary" in data or "task_id" in data or data.get("tool") == "task_report"
            ):
                payload = {
                    "task_id": data.get("task_id", self._current_task_id or ""),
                    "status": "completed" if status == "done" else status,
                    "summary": data.get("summary", ""),
                    "artifacts": data.get("artifacts", []),
                    "errors": data.get("errors", []),
                    "stats": data.get("stats", {}),
                }
                return A2AEnvelope.create(
                    sender=self.name,
                    recipient="nemesis",
                    msg_type=A2AMessageType.TASK_REPORT,
                    payload=payload,
                )
            return None

        try:
            built = _try_build(json.loads(raw))
            if built:
                return built
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        import re
        for pattern in [
            r"```(?:json)?\s*\n?(.*?)```",
            r'(\{[\s\S]*"a2a_version"[\s\S]*\})',
            r'(\{[\s\S]*"type"\s*:\s*"(?:capability_report|task_ack|task_report|heartbeat_ack|error)"[\s\S]*\})',
        ]:
            match = re.search(pattern, raw, re.DOTALL)
            if match:
                try:
                    built = _try_build(json.loads(match.group(1).strip()))
                    if built:
                        return built
                except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                    continue
        for json_str in self._extract_json_objects(raw):
            try:
                built = _try_build(json.loads(json_str))
                if built:
                    return built
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return None


class AgentClient(A2AAgentClient):
    """Legacy alias for A2AAgentClient."""
    pass


class A2ATaskScheduler:
    """Orchestrates non-blocking task delegation to NEMESIS teammate agents."""

    def __init__(self):
        self.coordinator = A2ACoordinator()
        self.agents: Dict[str, A2AAgentClient] = {}
        self._lock = threading.Lock()
        self._executor = None
        # task_id -> runtime record (async jobs)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._reports_dir: Optional[Path] = None

    def set_executor(self, executor: Any) -> None:
        """Définit l'exécuteur partagé pour tous les agents."""
        self._executor = executor
        for agent in self.agents.values():
            agent.set_executor(executor)
        # Resolve reports directory under workspace
        try:
            root = getattr(executor, "workspace_root", None)
            if root is None and hasattr(executor, "workspace"):
                root = executor.workspace
            if root is not None:
                self._reports_dir = Path(root) / "a2a_reports"
                self._reports_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _ensure_reports_dir(self) -> Path:
        if self._reports_dir is None:
            # Prefer executor workspace, then ~/nemesis-workspace, then ./workspace
            candidates = []
            if self._executor is not None:
                root = getattr(self._executor, "workspace_root", None)
                if root is None:
                    root = getattr(self._executor, "workspace", None)
                if root is not None:
                    candidates.append(Path(root))
            try:
                from src.core.paths import USER_CONFIG_DIR

                candidates.append(Path(USER_CONFIG_DIR))
            except Exception:
                candidates.append(Path.home() / ".config" / "nemesis-cli")
            for base in candidates:
                try:
                    d = Path(base).expanduser().resolve() / "a2a_reports"
                    d.mkdir(parents=True, exist_ok=True)
                    self._reports_dir = d
                    break
                except Exception:
                    continue
            if self._reports_dir is None:
                self._reports_dir = Path("./a2a_reports").resolve()
                self._reports_dir.mkdir(parents=True, exist_ok=True)
        return self._reports_dir

    def register_agent_client(self, agent: A2AAgentClient) -> None:
        with self._lock:
            self.agents[agent.name] = agent
            if self._executor:
                agent.set_executor(self._executor)

    def remove_agent(self, name: str) -> None:
        with self._lock:
            self.agents.pop(name, None)

    def list_agents(self) -> List[Dict[str, Any]]:
        result = []
        for name, agent in self.agents.items():
            m = agent.manifest
            result.append({
                "name": name,
                "status": agent.status,
                "provider": agent.provider,
                "model": agent.model,
                "capabilities": [c.value for c in m.capabilities] if m else [],
                "domains": m.domains if m else [],
                "current_task": agent._current_task_id,
            })
        return result

    def find_best_agent(self, required_capabilities: List[AgentCapability]) -> Optional[A2AAgentClient]:
        best_agent = None
        best_score = -1
        for name, agent in self.agents.items():
            m = agent.manifest
            if agent.status == "busy":
                continue
            if not m:
                if best_agent is None:
                    best_agent = agent
                continue
            score = sum(1 for c in required_capabilities if c in m.capabilities)
            if score > best_score:
                best_score = score
                best_agent = agent
        return best_agent

    def delegate(
        self,
        agent_name: str,
        label: str,
        description: str,
        instructions: List[str],
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout_ms: int = 300000,
        context_files: Optional[List[str]] = None,
        expected_output: str = "",
        blocking: bool = False,
    ) -> Optional[str]:
        """Delegate a task. By default non-blocking (returns task_id immediately).

        Set blocking=True only for legacy/sync behaviour.
        """
        agent = self.agents.get(agent_name)
        if not agent:
            return None

        if self._executor:
            agent.set_executor(self._executor)

        if agent.status == "busy":
            # Allow queueing only one job per agent for now
            return None

        manifest = self.coordinator.create_task_manifest(
            label=label,
            description=description,
            instructions=instructions,
            priority=priority,
            timeout_ms=timeout_ms,
            context_files=context_files,
            expected_output=expected_output,
        )

        if blocking:
            result = agent.assign_task(manifest)
            self._finalize_job(manifest, agent_name, result)
            if result:
                return manifest.task_id
            self.coordinator.active_tasks.pop(manifest.task_id, None)
            return None

        # Non-blocking: start background worker
        job = {
            "task_id": manifest.task_id,
            "agent": agent_name,
            "label": label,
            "description": description,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
            "report_path": None,
        }
        with self._lock:
            self._jobs[manifest.task_id] = job

        agent._status = "busy"
        agent._current_task_id = manifest.task_id

        def _worker():
            try:
                result = agent.assign_task(manifest)
                if result is None:
                    err = getattr(agent, "_error", None) or "assign_task returned no result"
                    result = {"status": "failed", "error": err, "summary": err}
                self._finalize_job(manifest, agent_name, result)
            except Exception as e:
                with self._lock:
                    job = self._jobs.get(manifest.task_id)
                    if job:
                        job["status"] = "failed"
                        job["error"] = str(e)
                        job["finished_at"] = time.time()
                agent._status = "idle"
                agent._current_task_id = None
                self._write_report_file(
                    manifest,
                    agent_name,
                    {"status": "failed", "error": str(e), "summary": str(e)},
                )

        t = threading.Thread(
            target=_worker,
            name=f"a2a-{agent_name}-{manifest.task_id[:8]}",
            daemon=True,
        )
        with self._lock:
            self._jobs[manifest.task_id]["thread"] = t
        t.start()
        return manifest.task_id

    def _finalize_job(
        self,
        manifest: TaskManifest,
        agent_name: str,
        result: Optional[dict],
    ) -> None:
        """Store result, write workspace report, mark agent idle."""
        status = "completed"
        if not result:
            status = "failed"
        else:
            st = str(result.get("status", "")).lower()
            if st in ("failed", "error", "timeout"):
                status = st if st != "error" else "failed"

        report_path = self._write_report_file(manifest, agent_name, result or {})

        with self._lock:
            job = self._jobs.get(manifest.task_id)
            if job:
                job["status"] = status
                job["result"] = result
                job["finished_at"] = time.time()
                job["report_path"] = str(report_path) if report_path else None

        # Persist in coordinator for collect_reports
        try:
            from src.core.a2a_protocol import TaskReport, TaskStatus, TaskArtifact
            payload = (result or {}).get("report") or (result or {})
            summary = payload.get("summary") or (result or {}).get("error") or status
            artifacts_raw = payload.get("artifacts") or []
            artifacts = []
            for a in artifacts_raw:
                if isinstance(a, dict):
                    path = a.get("path") or a.get("file_path") or ""
                    if path:
                        artifacts.append(TaskArtifact(
                            path=path,
                            description=str(a.get("description", ""))[:500],
                        ))
                elif isinstance(a, str):
                    artifacts.append(TaskArtifact(path=a, description=""))
            try:
                ts = TaskStatus(status)
            except ValueError:
                ts = TaskStatus.COMPLETED if status == "completed" else TaskStatus.FAILED
            err_list = []
            for e in (payload.get("errors") or []):
                if isinstance(e, dict):
                    err_list.append(e)
                else:
                    err_list.append({"message": str(e)})
            if result and result.get("error"):
                err_list.append({"message": str(result["error"])})
            report = TaskReport(
                task_id=manifest.task_id,
                status=ts,
                summary=str(summary)[:2000],
                artifacts=artifacts,
                errors=err_list,
                stats=payload.get("stats") or {},
            )
            self.coordinator.complete_task(manifest.task_id, report)
        except Exception:
            pass

        agent = self.agents.get(agent_name)
        if agent:
            agent._status = "idle"
            agent._current_task_id = None

    def _write_report_file(
        self,
        manifest: TaskManifest,
        agent_name: str,
        result: dict,
    ) -> Optional[Path]:
        """Write a human-readable report under <start dir>/a2a_reports/."""
        try:
            reports_dir = self._ensure_reports_dir()
            status = str((result or {}).get("status", "unknown"))
            payload = (result or {}).get("report") or (result or {})
            summary = payload.get("summary") or result.get("error") or ""
            artifacts = payload.get("artifacts") or []
            errors = payload.get("errors") or []
            finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            # Markdown report for NEMESIS to read_file
            md_path = reports_dir / f"task_{manifest.task_id}.md"
            lines = [
                f"# A2A Task Report — {manifest.task_id}",
                "",
                f"- **agent**: {agent_name}",
                f"- **label**: {manifest.label}",
                f"- **status**: {status}",
                f"- **finished_at**: {finished}",
                "",
                "## Instruction",
                "",
                manifest.description or "(none)",
                "",
                "## Summary",
                "",
                str(summary) or "(none)",
                "",
            ]
            if artifacts:
                lines.append("## Artifacts")
                lines.append("")
                for a in artifacts:
                    if isinstance(a, dict):
                        lines.append(f"- {a.get('path', a)}")
                    else:
                        lines.append(f"- {a}")
                lines.append("")
            if errors:
                lines.append("## Errors")
                lines.append("")
                for e in errors:
                    lines.append(f"- {e}")
                lines.append("")
            lines.append("## Raw result")
            lines.append("")
            lines.append("```json")
            try:
                lines.append(json.dumps(result, indent=2, default=str)[:8000])
            except Exception:
                lines.append(str(result)[:8000])
            lines.append("```")
            lines.append("")
            md_path.write_text("\n".join(lines), encoding="utf-8")

            # JSON sidecar for programmatic use
            json_path = reports_dir / f"task_{manifest.task_id}.json"
            json_path.write_text(
                json.dumps(
                    {
                        "task_id": manifest.task_id,
                        "agent": agent_name,
                        "label": manifest.label,
                        "status": status,
                        "finished_at": finished,
                        "description": manifest.description,
                        "result": result,
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
            return md_path
        except Exception as e:
            print(f"[A2A] Failed to write report: {e}")
            return None

    def get_task_status(self, task_id: str = "") -> Dict[str, Any]:
        """Return status for one task or all jobs."""
        with self._lock:
            if task_id:
                job = self._jobs.get(task_id)
                if not job:
                    # Fall back to completed coordinator reports
                    rep = self.coordinator.task_reports.get(task_id)
                    if rep:
                        return {
                            "task_id": task_id,
                            "status": rep.status.value if hasattr(rep.status, "value") else str(rep.status),
                            "summary": rep.summary,
                            "agent": None,
                        }
                    return {"task_id": task_id, "status": "unknown"}
                return {
                    "task_id": job["task_id"],
                    "agent": job["agent"],
                    "label": job.get("label"),
                    "status": job["status"],
                    "started_at": job.get("started_at"),
                    "finished_at": job.get("finished_at"),
                    "report_path": job.get("report_path"),
                    "error": job.get("error"),
                    "summary": (
                        (job.get("result") or {}).get("report", {}) or job.get("result") or {}
                    ).get("summary"),
                }
            # All jobs
            return {
                "tasks": [
                    {
                        "task_id": j["task_id"],
                        "agent": j["agent"],
                        "label": j.get("label"),
                        "status": j["status"],
                        "report_path": j.get("report_path"),
                    }
                    for j in self._jobs.values()
                ]
            }

    def collect_reports(self) -> List[TaskReport]:
        # Poll agents for reports still in conversation
        for agent in self.agents.values():
            report = agent.collect_report()
            if report and report.task_id:
                self.coordinator.complete_task(report.task_id, report)
        return list(self.coordinator.task_reports.values())

    def get_report(self, task_id: str) -> Optional[TaskReport]:
        return self.coordinator.task_reports.get(task_id)

    def list_report_files(self) -> List[str]:
        """List markdown reports on disk."""
        d = self._ensure_reports_dir()
        return sorted(str(p) for p in d.glob("*.md"))


_scheduler: Optional[A2ATaskScheduler] = None


def get_scheduler() -> A2ATaskScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = A2ATaskScheduler()
    return _scheduler
