"""Ephemeral, concurrent sub-agent fleet backed by an OpenAI-compatible API."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import requests


DEFAULT_SUBAGENT_CONFIG: Dict[str, Any] = {
    "provider": "groq",
    "base_url": "https://api.groq.com/openai/v1",
    "model": "qwen/qwen3.8-27b",
    "max_concurrent": 4,
    "max_iterations": 0,  # 0 = unlimited
    "timeout_seconds": 180,
    "max_retries": 3,
    "max_tokens_per_task": 24000,
}

MAX_SUBAGENTS = 8
MAX_RESULT_CHARS = 400_000
MAX_RETAINED_TASKS = 100


class SubagentFleet:
    """Creates one short-lived worker per task and retains only task reports."""

    def __init__(self, config: Dict[str, Any], workspace: str):
        self.config = dict(DEFAULT_SUBAGENT_CONFIG)
        configured = config.get("subagents")
        if isinstance(configured, dict):
            self.config.update(configured)
        self.profiles = self._load_profiles(configured)
        self._fallback_profile = None
        if not any(profile.get("api_key") for profile in self.profiles):
            self._fallback_profile = self._build_fallback_profile(config)
            if self._fallback_profile:
                self.profiles.append(self._fallback_profile)
                self.config.update({
                    key: self._fallback_profile[key]
                    for key in ("provider", "base_url", "model", "api_key")
                })
        self._profile_lock = threading.RLock()
        self._profile_cursor = 0
        self._profile_cooldowns: Dict[str, float] = {}
        self._disabled_profiles: Dict[str, str] = {}
        self.workspace = workspace
        self._max_concurrent = max(1, min(int(self.config["max_concurrent"]), MAX_SUBAGENTS))
        self._pool = ThreadPoolExecutor(
            max_workers=self._max_concurrent,
            thread_name_prefix="nemesis-subagent",
        )
        self._tasks: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._authorizer: Optional[Callable[[str, Dict[str, Any], str], bool]] = None
        self._cancel_event: Optional[threading.Event] = None

    def _load_profiles(self, configured: Any) -> List[Dict[str, Any]]:
        profiles = configured.get("profiles") if isinstance(configured, dict) else None
        if not isinstance(profiles, list):
            profiles = (
                [configured]
                if isinstance(configured, dict)
                and any(configured.get(key) for key in ("api_key", "provider", "base_url", "model"))
                else []
            )
        resolved = []
        for index, raw in enumerate(profiles):
            if not isinstance(raw, dict):
                continue
            profile = {**DEFAULT_SUBAGENT_CONFIG, **raw}
            provider = str(profile.get("provider") or "groq").lower()
            if provider == "groq" and profile.get("model") == "openai/gpt-oss-20b":
                profile["model"] = DEFAULT_SUBAGENT_CONFIG["model"]
            env_name = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "")
            profile["api_key"] = str(profile.get("api_key") or (
                os.environ.get(env_name, "") if env_name else ""
            )).strip()
            profile["id"] = str(profile.get("id") or f"profile-{index + 1}")
            resolved.append(profile)
        for provider, env_name, base_url, model in (
            ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1", DEFAULT_SUBAGENT_CONFIG["model"]),
            ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
        ):
            env_key = os.environ.get(env_name, "").strip()
            if env_key and not any(
                profile["provider"] == provider and profile["api_key"] == env_key
                for profile in resolved
            ):
                resolved.append({
                    "id": f"environment-{provider}",
                    "name": f"{provider.title()} ({env_name})",
                    "provider": provider,
                    "base_url": base_url,
                    "model": model,
                    "api_key": env_key,
                })
        self.config["profiles"] = resolved
        if resolved:
            self.config.update({
                key: resolved[0][key]
                for key in ("provider", "base_url", "model", "api_key")
            })
        return resolved

    def _build_fallback_profile(self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Reuse the main provider so delegation works without a dedicated key."""
        provider = config.get("provider") if isinstance(config.get("provider"), dict) else {}
        nemapi = config.get("nemapi") if isinstance(config.get("nemapi"), dict) else {}
        provider_type = str(provider.get("type") or "nemapi").lower()
        model = str(provider.get("model") or nemapi.get("model") or "").strip()
        base_url = ""
        api_key = ""
        if provider_type == "nemapi":
            url = str(nemapi.get("url") or "").strip()
            if url:
                base_url = url.rstrip("/")
            else:
                host = str(nemapi.get("host") or "127.0.0.1").strip()
                port = nemapi.get("port") or 8090
                base_url = f"http://{host}:{port}/v1"
            api_key = str(nemapi.get("token") or provider.get("api_key") or "nemapi").strip()
        else:
            base_url = str(provider.get("base_url") or "").strip().rstrip("/")
            api_key = str(provider.get("api_key") or "").strip()
        if not base_url or not model or not api_key:
            return None
        return {
            "id": "main-provider",
            "name": f"Provider principal ({provider_type} · {model})",
            "provider": provider_type,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "from_main_config": True,
        }

    def _has_api_key(self) -> bool:
        return any(profile.get("api_key") for profile in self.profiles)

    def availability(self) -> Dict[str, Any]:
        """Describe delegation capabilities for prompts and diagnostics."""
        if self._has_api_key():
            return {
                "available": True,
                "mode": "dedicated" if not self._fallback_profile else "main-provider",
                "detail": (
                    "Profils de sous-agents dédiés."
                    if not self._fallback_profile
                    else "Aucune clé dédiée : le provider principal est réutilisé."
                ),
            }
        return {
            "available": False,
            "mode": "unavailable",
            "detail": (
                "Aucune clé API de sous-agent et provider principal inutilisable. "
                "Renseignez une clé dans Réglages → Sous-agents."
            ),
        }

    def _select_profile(self) -> Dict[str, Any]:
        if not self.profiles:
            raise RuntimeError(
                "Aucun profil API de sous-agent n'est configuré. Ajoutez-en un dans "
                "Réglages → Sous-agents ou définissez GROQ_API_KEY."
            )
        with self._profile_lock:
            now = time.monotonic()
            count = len(self.profiles)
            for offset in range(count):
                index = (self._profile_cursor + offset) % count
                profile = self.profiles[index]
                if not profile.get("api_key") or profile["id"] in self._disabled_profiles:
                    continue
                if self._profile_cooldowns.get(profile["id"], 0) <= now:
                    self._profile_cursor = (index + 1) % count
                    return profile
            eligible = [
                p for p in self.profiles
                if p.get("api_key") and p["id"] not in self._disabled_profiles
            ]
            if not eligible:
                disabled = ", ".join(
                    f"{p.get('name') or p['id']}: {self._disabled_profiles[p['id']]}"
                    for p in self.profiles if p["id"] in self._disabled_profiles
                )
                raise RuntimeError(
                    f"Aucun profil API de sous-agent utilisable. Vérifiez les clés dans "
                    f"Réglages → Sous-agents. {disabled}".strip()
                )
            earliest = min(self._profile_cooldowns.get(p["id"], 0) for p in eligible)
            wait = max(0, earliest - now)
        if self._cancel_event is not None and self._cancel_event.wait(wait):
            raise InterruptedError("Interrompu par l'utilisateur.")
        if self._cancel_event is None:
            time.sleep(wait)
        return self._select_profile()

    @staticmethod
    def _retry_after(response: Any, body_text: str) -> float:
        header = response.headers.get("Retry-After") if hasattr(response, "headers") else None
        if header:
            try:
                return max(0.0, float(header))
            except ValueError:
                pass
        for text in (body_text, getattr(response, "text", "")):
            match = re.search(r"try again in\s+([\d.]+)\s*(ms|s|sec|seconds?)", text, re.I)
            if match:
                value = float(match.group(1))
                return value / 1000 if match.group(2).lower() == "ms" else value
        return 0.0

    def _post_completion(
        self, payload: Dict[str, Any], task_id: str
    ) -> tuple[Any, Dict[str, Any]]:
        retries = 0
        max_retries = max(0, min(int(self.config.get("max_retries", 3)), 8))
        while True:
            if self._cancel_event is not None and self._cancel_event.is_set():
                raise InterruptedError("Interrompu par l'utilisateur.")
            profile = self._select_profile()
            profile_base = str(profile.get("base_url") or "").strip().rstrip("/")
            if not profile_base:
                raise RuntimeError(f"URL de base absente pour le profil {profile['id']}.")
            expected_endpoint = profile_base if profile_base.endswith("/v1") else f"{profile_base}/v1"
            request_endpoint = f"{expected_endpoint}/chat/completions"
            profile_payload = {**payload, "model": profile.get("model") or self.config["model"]}
            token_limit = max(1000, int(self.config.get("max_tokens_per_task", 24000)))
            tokens_used = int(self._task_value(task_id, "prompt_tokens", 0)) + int(
                self._task_value(task_id, "completion_tokens", 0)
            )
            estimate_input = (
                len(json.dumps(
                    {"messages": payload.get("messages", []), "tools": payload.get("tools", [])},
                    ensure_ascii=False,
                    default=str,
                )) + 3
            ) // 4
            available_tokens = token_limit - tokens_used - estimate_input
            if available_tokens < 1:
                raise RuntimeError(
                    f"Budget estimé de {token_limit} tokens atteint pour cette tâche "
                    f"({tokens_used} tokens déjà consommés, environ {estimate_input} requis)."
                )
            profile_payload["max_tokens"] = min(
                int(profile_payload.get("max_tokens") or 2048),
                available_tokens,
            )
            self._set_task(
                task_id,
                provider=profile.get("provider"),
                model=profile.get("model"),
                api_requests=int(self._task_value(task_id, "api_requests", 0)) + 1,
            )
            response = requests.post(
                request_endpoint,
                headers={
                    "Authorization": f"Bearer {profile['api_key']}",
                    "Content-Type": "application/json",
                },
                json=profile_payload,
                timeout=int(self.config["timeout_seconds"]),
            )
            if response.status_code != 429:
                if response.status_code in {400, 401, 403}:
                    message = response.text[:1000]
                    reason = {
                        400: "Requête refusée (400) — vérifiez modèle et paramètres",
                        401: "Clé API invalide ou révoquée (401)",
                        403: "Accès refusé au modèle ou endpoint (403)",
                    }[response.status_code]
                    if response.status_code in {401, 403}:
                        with self._profile_lock:
                            self._disabled_profiles[profile["id"]] = reason
                            has_fallback = any(
                                candidate.get("api_key")
                                and candidate["id"] not in self._disabled_profiles
                                for candidate in self.profiles
                            )
                        if has_fallback:
                            self._set_task(
                                task_id,
                                status="retrying",
                                credential_failovers=int(self._task_value(task_id, "credential_failovers", 0)) + 1,
                                last_retry_reason=f"{reason} — bascule vers un autre profil",
                            )
                            continue
                    raise RuntimeError(f"{reason} [{profile.get('name') or profile['id']}]: {message}")
                if response.status_code >= 400:
                    raise RuntimeError(f"API sous-agent HTTP {response.status_code}: {response.text[:1000]}")
                usage = response.json().get("usage") or {}
                self._set_task(
                    task_id,
                    provider=profile.get("provider"),
                    model=profile.get("model"),
                    prompt_tokens=int(self._task_value(task_id, "prompt_tokens", 0)) + int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(self._task_value(task_id, "completion_tokens", 0)) + int(usage.get("completion_tokens") or 0),
                )
                return response, profile

            delay = self._retry_after(response, response.text)
            delay = min(max(delay, min(2 ** retries, 30)), 60)
            with self._profile_lock:
                self._profile_cooldowns[profile["id"]] = time.monotonic() + delay
            self._set_task(
                task_id,
                status="retrying",
                retry_count=int(self._task_value(task_id, "retry_count", 0)) + 1,
                last_retry_reason=f"HTTP 429, attente {delay:.1f}s",
                provider=profile.get("provider"),
                model=profile.get("model"),
            )
            if retries >= max_retries:
                raise RuntimeError(
                    f"Limite de reprises atteinte après HTTP 429 sur le profil "
                    f"{profile.get('name') or profile['id']}."
                )
            retries += 1

    def _task_value(self, task_id: str, key: str, default: Any) -> Any:
        with self._lock:
            return self._tasks.get(task_id, {}).get(key, default)

    def _record_progress(self, task_id: str, tool_name: str, result: Dict[str, Any]) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            progress = task.setdefault("progress", [])
            progress.append({
                "tool": tool_name,
                "success": bool(result.get("success")),
                "summary": str(result.get("stdout") or "")[:500],
                "at": time.time(),
            })
            del progress[:-20]

    def set_authorizer(
        self, callback: Optional[Callable[[str, Dict[str, Any], str], bool]]
    ) -> None:
        self._authorizer = callback

    def set_cancel_event(self, event: Optional[threading.Event]) -> None:
        self._cancel_event = event

    def delegate(self, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Queue a bounded batch of independently identified tasks."""
        if not isinstance(tasks, list) or not tasks:
            return {"success": False, "error": "'tasks' doit être une liste non vide."}
        if len(tasks) > MAX_SUBAGENTS:
            return {"success": False, "error": f"Maximum {MAX_SUBAGENTS} tâches par lot."}
        if not self._has_api_key():
            return {
                "success": False,
                "error": "Aucun profil API de sous-agent n'a de clé. Ajoutez une clé dans "
                "Réglages → Sous-agents, ou définissez GROQ_API_KEY/OPENAI_API_KEY.",
            }

        prepared: List[Dict[str, str]] = []
        for item in tasks:
            if not isinstance(item, dict):
                return {
                    "success": False,
                    "error": "Chaque tâche doit être un objet JSON avec instruction (ou goal/prompt/description). "
                    'Exemple : {"tasks":[{"instruction":"Lire src/main.py et résumer son rôle"}]}.',
                }
            instruction = str(
                item.get("instruction") or item.get("goal") or item.get("prompt")
                or item.get("description") or ""
            ).strip()
            if not instruction:
                return {
                    "success": False,
                    "error": "Champ requis manquant : instruction. Alias acceptés : goal, prompt, description. "
                    'Exemple : {"instruction":"Analyser les erreurs dans src/main.py"}.',
                }
            if item.get("context"):
                instruction = f"Contexte utile:\n{str(item['context'])[:6000]}\n\nTâche:\n{instruction}"
            mode = str(item.get("mode") or "standard").strip().lower()
            if mode not in {"standard", "read_only"}:
                return {
                    "success": False,
                    "error": "Le champ mode doit être 'standard' ou 'read_only'.",
                }
            prepared.append({
                "label": str(item.get("label") or instruction[:80]).strip()[:120],
                "instruction": instruction[:20000],
                "mode": mode,
            })

        queued = []
        with self._lock:
            if not self._prune_completed(len(prepared)):
                return {
                    "success": False,
                    "error": "Historique des tâches saturé ; attendez la fin de tâches actives.",
                }
            for task in prepared:
                task_id = uuid.uuid4().hex[:12]
                record = {
                    "task_id": task_id,
                    "label": task["label"],
                    "instruction": task["instruction"],
                    "mode": task["mode"],
                    "status": "queued",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "result": "",
                    "error": "",
                    "retry_count": 0,
                    "api_requests": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                }
                self._tasks[task_id] = record
                self._pool.submit(self._run_task, task_id)
                queued.append({"task_id": task_id, "label": task["label"], "status": "queued"})
        return {"success": True, "tasks": queued, "queued": len(queued)}

    def status(self, task_id: str = "") -> Dict[str, Any]:
        with self._lock:
            records = (
                [self._tasks[task_id]] if task_id and task_id in self._tasks
                else [] if task_id
                else sorted(self._tasks.values(), key=lambda task: task["created_at"])
            )
            tasks = []
            for record in records:
                task = {
                    key: record[key]
                    for key in ("task_id", "label", "status", "created_at", "updated_at")
                }
                if record["result"]:
                    key = "result" if task_id else "result_preview"
                    limit = MAX_RESULT_CHARS if task_id else 500
                    task[key] = record["result"][:limit]
                if record["error"]:
                    task["error"] = record["error"]
                if record.get("progress") and record["status"] != "completed":
                    task["partial_progress"] = record["progress"]
                for key in (
                    "retry_count", "last_retry_reason", "provider", "model",
                    "api_requests", "prompt_tokens", "completion_tokens", "duration_seconds",
                    "credential_failovers",
                ):
                    if key in record:
                        task[key] = record[key]
                tasks.append(task)
        return {
            "success": True,
            "tasks": tasks,
            "message": "Tâche inconnue ou rapport expiré." if task_id and not tasks else "",
        }

    def _prune_completed(self, required_slots: int = 0) -> bool:
        finished = [
            task_id for task_id, record in self._tasks.items()
            if record["status"] in {"completed", "failed", "cancelled"}
        ]
        while len(self._tasks) + required_slots > MAX_RETAINED_TASKS and finished:
            self._tasks.pop(finished.pop(0), None)
        return len(self._tasks) + required_slots <= MAX_RETAINED_TASKS

    def _set_task(self, task_id: str, **updates: Any) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record is not None:
                record.update(updates, updated_at=time.time())

    def _run_task(self, task_id: str) -> None:
        started = time.monotonic()
        self._set_task(task_id, status="running", started_at=time.time())
        try:
            with self._lock:
                task = dict(self._tasks[task_id])
            result = self._execute(task_id, task)
            self._set_task(
                task_id,
                status="completed",
                result=result[:MAX_RESULT_CHARS],
                duration_seconds=round(time.monotonic() - started, 3),
            )
        except InterruptedError as exc:
            self._set_task(
                task_id,
                status="cancelled",
                error=str(exc),
                duration_seconds=round(time.monotonic() - started, 3),
            )
        except Exception as exc:
            self._set_task(
                task_id,
                status="failed",
                error=str(exc),
                duration_seconds=round(time.monotonic() - started, 3),
            )

    def _execute(self, task_id: str, task: Dict[str, Any]) -> str:
        from src.core.tool_bridge import ToolBridge

        executor = ToolBridge(
            workspace=self.workspace,
            config={"subagents": self.config},
            enable_subagents=False,
        )
        try:
            return self._execute_with_executor(task_id, task, executor)
        finally:
            executor.close()

    def _execute_with_executor(
        self, task_id: str, task: Dict[str, Any], executor: Any
    ) -> str:
        messages: List[Dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "Tu es un sous-agent temporaire, spécialisé dans une seule mission. "
                f"Mission : {task['label']}. Mode : {task.get('mode', 'standard')}. "
                "Exécute uniquement la demande utilisateur ci-dessous, "
                "utilise les outils nécessaires sans déléguer, respecte les autorisations et "
                "en mode read_only n'utilise aucun outil qui écrit ou exécute des commandes, "
                "et ne prétends pas avoir fait une action non confirmée. Termine par un "
                    "rapport concis : résultat, actions/fichiers et vérifications réelles ou blocage."
                ),
            },
            {"role": "user", "content": task["instruction"]},
        ]
        tools = executor.get_openai_tools()
        if task.get("mode") == "read_only":
            tools = [
                definition
                for definition in tools
                if (
                    registration := executor.registry.get_tool(
                        definition.get("function", {}).get("name", "")
                    )
                ) is not None and registration.definition.kind.is_read_only()
            ]
        limit = max(0, int(self.config.get("max_iterations") or 0))
        iteration = 0
        while True:
            iteration += 1
            if limit and iteration > limit:
                raise RuntimeError(
                    f"Limite de {limit} itérations atteinte sans réponse finale."
                )
            response, _profile = self._post_completion(
                {
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                    "temperature": 0.2,
                    "max_tokens": int(self.config.get("max_tokens") or 2048),
                },
                task_id,
            )
            choices = (response.json().get("choices") or [])
            if not choices:
                raise RuntimeError("Réponse API sans choix.")
            message = choices[0].get("message") or {}
            calls = message.get("tool_calls") or []
            if not calls:
                content = message.get("content") or ""
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError("Réponse finale vide de l'API.")
                return content.strip()

            messages.append({
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": calls,
            })
            for call in calls:
                call_id = str(call.get("id") or uuid.uuid4().hex)
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                raw_arguments = function.get("arguments") or "{}"
                try:
                    parameters = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
                except (TypeError, json.JSONDecodeError):
                    parameters = {}
                if not isinstance(parameters, dict):
                    parameters = {}
                if executor.registry.get_tool(name) is None:
                    result = {"success": False, "stdout": f"Outil inconnu: {name}"}
                elif (
                    task.get("mode") == "read_only"
                    and not executor.registry.get_tool(name).definition.kind.is_read_only()
                ):
                    result = {
                        "success": False,
                        "stdout": "Action bloquée : cette tâche est explicitement en mode read_only.",
                    }
                elif self._authorizer is not None:
                    risk = executor.risk_for(name, parameters)
                    if not self._authorizer(f"subagent:{task_id}/{name}", parameters, risk):
                        result = {"success": False, "stdout": "Action refusée par l'utilisateur."}
                    else:
                        result = self._run_tool(executor, name, parameters)
                else:
                    result = self._run_tool(executor, name, parameters)
                self._record_progress(task_id, name, result)
                payload = json.dumps(result, ensure_ascii=False, default=str)
                if len(payload) > MAX_RESULT_CHARS:
                    payload = payload[:MAX_RESULT_CHARS] + "\n...[truncated]"
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": payload,
                })
    @staticmethod
    def _run_tool(executor: Any, name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        result: Dict[str, Any] = {"success": False, "stdout": "Aucun résultat outil."}
        for update in executor.execute_tool(name, parameters):
            if isinstance(update, dict):
                result = update
        return result

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
