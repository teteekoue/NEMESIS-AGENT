"""Persistent one-shot scheduler for agent prompts and intentional waits."""

from __future__ import annotations

import contextvars
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional


_owner_context = contextvars.ContextVar("nemesis_scheduler_owner", default="default")
_target_context = contextvars.ContextVar("nemesis_scheduler_target", default=None)


@contextmanager
def scheduler_context(owner_id: str, target_id: Optional[str] = None):
    owner_token = _owner_context.set(owner_id or "default")
    target_token = _target_context.set(str(target_id) if target_id is not None else None)
    try:
        yield
    finally:
        _owner_context.reset(owner_token)
        _target_context.reset(target_token)


def current_scheduler_context() -> tuple[str, Optional[str]]:
    return _owner_context.get(), _target_context.get()


def _to_timestamp(run_at: Optional[str], delay_seconds: Optional[float]) -> float:
    if (run_at is None) == (delay_seconds is None):
        raise ValueError("Fournissez exactement un paramètre : run_at ou delay_seconds.")
    if delay_seconds is not None:
        delay = float(delay_seconds)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("delay_seconds doit être un nombre fini, positif ou nul.")
        return time.time() + delay
    raw = str(run_at).strip()
    if "T" not in raw and " " not in raw:
        raise ValueError("run_at doit inclure une heure ISO 8601, pas seulement une date.")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("run_at doit être une date ISO 8601, par exemple 2026-12-24T09:00:00+01:00.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


class TaskScheduler:
    """SQLite-backed queue; handlers execute due prompts in the owning surface."""

    def __init__(self, database_path: str | Path, poll_interval: float = 0.5):
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.poll_interval = max(0.05, float(poll_interval))
        self._handlers: Dict[tuple[str, Optional[str]], Callable[[dict], Any]] = {}
        self._handlers_lock = threading.RLock()
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._init_db()
        try:
            os.chmod(self.database_path, 0o600)
        except OSError:
            pass
        self._worker = threading.Thread(target=self._run, name="nemesis-scheduler", daemon=True)
        self._worker.start()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    task_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    target_id TEXT,
                    instruction TEXT NOT NULL,
                    run_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    started_at REAL,
                    finished_at REAL
                )"""
            )
            # Recover a task only after its worker heartbeat is stale; separate
            # interface processes may share this database concurrently.
            conn.execute(
                "UPDATE scheduled_tasks SET status='pending', started_at=NULL "
                "WHERE status='running' AND started_at < ?",
                (time.time() - 120,),
            )

    def register_handler(
        self, owner_id: str, handler: Callable[[dict], Any], target_id: Optional[str] = None
    ) -> None:
        key = (owner_id, str(target_id) if target_id is not None else None)
        with self._handlers_lock:
            self._handlers[key] = handler
        self._wake.set()

    def unregister_handler(self, owner_id: str, handler=None, target_id: Optional[str] = None) -> None:
        key = (owner_id, str(target_id) if target_id is not None else None)
        with self._handlers_lock:
            current = self._handlers.get(key)
            if current is not None and (handler is None or current is handler):
                self._handlers.pop(key, None)

    def schedule(
        self,
        instruction: str,
        *,
        run_at: Optional[str] = None,
        delay_seconds: Optional[float] = None,
        owner_id: str = "default",
        target_id: Optional[str] = None,
    ) -> dict:
        instruction = str(instruction or "").strip()
        if not instruction:
            raise ValueError("instruction ne peut pas être vide.")
        timestamp = _to_timestamp(run_at, delay_seconds)
        now = time.time()
        task_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO scheduled_tasks
                   (task_id, owner_id, target_id, instruction, run_at, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (task_id, owner_id or "default", target_id, instruction, timestamp, now),
            )
        self._wake.set()
        return {
            "success": True,
            "task_id": task_id,
            "run_at": datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds"),
            "stdout": f"Tâche planifiée — ID {task_id} — {datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec='seconds')}",
        }

    def list_tasks(self, owner_id: str = "default", target_id: Optional[str] = None) -> dict:
        query = "SELECT * FROM scheduled_tasks WHERE owner_id=?"
        args: list[Any] = [owner_id or "default"]
        if target_id is not None:
            query += " AND target_id=?"
            args.append(str(target_id))
        query += " ORDER BY run_at, created_at"
        with self._connect() as conn:
            rows = conn.execute(query, args).fetchall()
        tasks = [self._public_row(row) for row in rows]
        lines = []
        for task in tasks:
            lines.append(
                f"- {task['task_id']} | {task['status']} | {task['run_at']} | {task['instruction']}"
            )
            if task.get("error"):
                lines.append(f"  Erreur : {task['error'][:1000]}")
            elif task.get("result"):
                lines.append(f"  Résultat : {task['result'][:1500]}")
        return {
            "success": True,
            "tasks": tasks,
            "stdout": "Tâches planifiées :\n" + ("\n".join(lines) if lines else "Aucune."),
        }

    def cancel(self, task_id: str, owner_id: str = "default", target_id: Optional[str] = None) -> dict:
        query = "UPDATE scheduled_tasks SET status='cancelled', finished_at=? WHERE task_id=? AND owner_id=? AND status='pending'"
        args: list[Any] = [time.time(), str(task_id), owner_id or "default"]
        if target_id is not None:
            query += " AND target_id=?"
            args.append(str(target_id))
        with self._connect() as conn:
            cursor = conn.execute(query, args)
        if cursor.rowcount:
            return {"success": True, "stdout": f"Tâche {task_id} annulée."}
        return {"success": False, "error": "Tâche inexistante, déjà lancée ou hors de votre contexte.", "stdout": "Tâche non annulée."}

    @staticmethod
    def _public_row(row) -> dict:
        return {
            "task_id": row["task_id"],
            "owner_id": row["owner_id"],
            "target_id": row["target_id"],
            "instruction": row["instruction"],
            "run_at": datetime.fromtimestamp(row["run_at"]).astimezone().isoformat(timespec="seconds"),
            "status": row["status"],
            "result": row["result"],
            "error": row["error"],
            "created_at": datetime.fromtimestamp(row["created_at"]).astimezone().isoformat(timespec="seconds"),
        }

    def _get_handler(self, owner_id: str, target_id: Optional[str] = None):
        with self._handlers_lock:
            handler = self._handlers.get((owner_id, str(target_id) if target_id is not None else None))
            if handler is not None:
                return handler
            handler = self._handlers.get((owner_id, None))
            if handler is not None:
                return handler
            # Telegram registers one wildcard handler for per-chat owners.
            prefix = owner_id.split(":", 1)[0] + ":*"
            return self._handlers.get((prefix, None))

    def _run(self) -> None:
        while not self._closed.is_set():
            self._dispatch_due()
            self._wake.wait(self.poll_interval)
            self._wake.clear()

    def _dispatch_due(self) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET status='pending', started_at=NULL "
                "WHERE status='running' AND started_at < ?",
                (now - 120,),
            )
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE status='pending' AND run_at<=? ORDER BY run_at LIMIT 50",
                (now,),
            ).fetchall()
        for row in rows:
            handler = self._get_handler(row["owner_id"], row["target_id"])
            if handler is None:
                continue
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE scheduled_tasks SET status='running', started_at=? WHERE task_id=? AND status='pending'",
                    (time.time(), row["task_id"]),
                )
            if cursor.rowcount:
                task = self._public_row(row)
                threading.Thread(
                    target=self._execute,
                    args=(task, handler),
                    name=f"nemesis-scheduled-{task['task_id']}",
                    daemon=True,
                ).start()

    def _execute(self, task: dict, handler: Callable[[dict], Any]) -> None:
        status = "completed"
        result = ""
        error = ""
        heartbeat_stop = threading.Event()

        def heartbeat():
            while not heartbeat_stop.wait(30):
                try:
                    with self._connect() as conn:
                        conn.execute(
                            "UPDATE scheduled_tasks SET started_at=? WHERE task_id=? AND status='running'",
                            (time.time(), task["task_id"]),
                        )
                except sqlite3.Error:
                    pass

        heartbeat_thread = threading.Thread(target=heartbeat, name="nemesis-scheduler-heartbeat", daemon=True)
        heartbeat_thread.start()
        try:
            value = handler(task)
            if isinstance(value, dict):
                result = str(value.get("text") or value.get("stdout") or "")
                if value.get("success") is False:
                    status = "failed"
                    error = str(value.get("error") or result)
            else:
                result = str(value or "")
        except Exception as exc:  # noqa: BLE001 - persist task failure for the user
            status = "failed"
            error = str(exc)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)
        with self._connect() as conn:
            conn.execute(
                "UPDATE scheduled_tasks SET status=?, result=?, error=?, finished_at=? WHERE task_id=?",
                (status, result[-20000:], error[-4000:], time.time(), task["task_id"]),
            )

    def close(self, wait: bool = False) -> None:
        self._closed.set()
        self._wake.set()
        if wait and self._worker.is_alive():
            self._worker.join(timeout=2)


_scheduler: Optional[TaskScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> TaskScheduler:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            from .paths import USER_CONFIG_DIR

            _scheduler = TaskScheduler(USER_CONFIG_DIR / "scheduled_tasks.sqlite3")
        return _scheduler


def wait_for(seconds: float, reason: str = "", cancel_event=None) -> dict:
    duration = float(seconds)
    if not math.isfinite(duration) or duration < 0:
        return {"success": False, "error": "seconds doit être un nombre fini, positif ou nul."}
    started = time.monotonic()
    if cancel_event is not None:
        interrupted = cancel_event.wait(duration)
    else:
        time.sleep(duration)
        interrupted = False
    elapsed = round(time.monotonic() - started, 2)
    if interrupted:
        return {"success": False, "stdout": f"Attente interrompue après {elapsed} s.", "error": "interrupted"}
    label = f" ({reason.strip()})" if reason else ""
    return {"success": True, "stdout": f"Attente terminée après {elapsed} s{label}."}


def schedule_agent_task(config: dict, instruction: str, owner_id: str, target_id=None) -> dict:
    """Run a due instruction through the shared strict agent/tool loop."""
    from action_parser import ActionParser
    from providers import create_provider
    from .tool_bridge import ToolBridge
    from .agent_tools import build_feedback_message, format_tool_feedback, protocol_error_feedback
    from .image_attachments import send_message_with_images

    provider = create_provider(config)
    security = config.get("security", {}) if isinstance(config.get("security"), dict) else {}
    workspace = security.get("workspace") or None
    bridge = ToolBridge(
        workspace=workspace,
        config=config,
        scheduler_owner_id=owner_id,
        scheduler_target_id=target_id,
    )
    parser = ActionParser(extra_valid_tools=set(bridge.registry._tools), strict=True)
    current_input = instruction
    current_role = "user"
    system_prefix = bridge.get_system_prompt()
    tool_count = 0
    last_text = ""
    feedback_seq = 0
    try:
        while True:
            response = send_message_with_images(
                provider, current_input, role=current_role, system_prefix=system_prefix
            )
            system_prefix = ""
            if not response or not response.get("success"):
                return {"success": False, "error": str((response or {}).get("error") or "Provider indisponible.")}
            raw = response.get("response", "") or ""
            parsed = parser.parse(raw)
            if parsed.get("protocol_error") and not (response.get("tool_calls") or response.get("tool_call")):
                feedback_seq += 1
                current_input = protocol_error_feedback(parsed["protocol_error"], feedback_seq)
                current_role = "tool_result"
                continue
            native_calls = list(response.get("tool_calls") or [])
            if response.get("tool_call") and not native_calls:
                native_calls.append(response["tool_call"])
            for native in native_calls:
                if isinstance(native, dict):
                    parsed.setdefault("actions", []).append({
                        "type": native.get("type", ""), "content": native.get("content", {})
                    })
            text = str(parsed.get("text") or "").strip()
            if text and not text.startswith("FEEDBACK:"):
                last_text = text
            actions = parsed.get("actions") or ([parsed["action"]] if parsed.get("action") else [])
            if not actions:
                break
            feedback = []
            for action in actions:
                name = action.get("type", "")
                params = action.get("content") or {}
                if not isinstance(params, dict):
                    params = {"value": params}
                result = {}
                for update in bridge.execute_tool(name, params):
                    result = update
                    if name != "bash" or "success" in update:
                        continue
                feedback.append(format_tool_feedback(name, result))
                tool_count += 1
            feedback_seq += 1
            current_input = build_feedback_message(feedback, feedback_seq)
            current_role = "tool_result"
        return {"success": True, "text": last_text, "tool_count": tool_count}
    finally:
        bridge.close()
        close = getattr(provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
