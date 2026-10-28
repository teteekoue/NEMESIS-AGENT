from __future__ import annotations

import threading
import time

from src.core.scheduler import TaskScheduler, current_scheduler_context, wait_for
from src.core.tool_bridge import ToolBridge


def test_scheduled_task_persists_and_runs_when_due(tmp_path):
    scheduler = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    completed = threading.Event()
    calls = []

    def handler(task):
        calls.append(task["instruction"])
        completed.set()
        return {"success": True, "text": "done"}

    scheduler.register_handler("cli", handler)
    created = scheduler.schedule("Vérifier un statut", delay_seconds=0.03, owner_id="cli")
    assert created["success"] is True
    assert completed.wait(2)
    deadline = time.time() + 2
    while time.time() < deadline:
        details = scheduler.list_tasks("cli")["tasks"]
        if details and details[0]["status"] == "completed":
            break
        time.sleep(0.01)

    assert calls == ["Vérifier un statut"]
    assert details[0]["result"] == "done"
    assert details[0]["status"] == "completed"
    scheduler.close(wait=True)

    reopened = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    assert reopened.list_tasks("cli")["tasks"][0]["task_id"] == created["task_id"]
    reopened.close(wait=True)


def test_scheduler_dispatch_selects_the_matching_web_session(tmp_path):
    scheduler = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    fallback = lambda task: None
    session_handler = lambda task: None
    scheduler.register_handler("web", fallback)
    scheduler.register_handler("web", session_handler, "session-a")
    assert scheduler._get_handler("web", "session-a") is session_handler
    assert scheduler._get_handler("web", "session-b") is fallback
    scheduler.close(wait=True)


def test_scheduler_rejects_ambiguous_or_invalid_times(tmp_path):
    scheduler = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    for args in (
        {"run_at": "2026-10-29T10:00:00", "delay_seconds": 10},
        {"run_at": "not-a-date"},
    ):
        try:
            scheduler.schedule("Instruction", owner_id="cli", **args)
        except ValueError:
            pass
        else:
            raise AssertionError("une heure invalide devait être refusée")
    scheduler.close(wait=True)


def test_scheduled_task_can_be_cancelled_and_isolation_is_enforced(tmp_path):
    scheduler = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    scheduled = scheduler.schedule(
        "Ne pas lancer", delay_seconds=600, owner_id="telegram:123", target_id="123"
    )
    assert scheduler.list_tasks("telegram:123", "123")["tasks"][0]["task_id"] == scheduled["task_id"]
    assert scheduler.list_tasks("telegram:456", "456")["tasks"] == []
    assert scheduler.cancel(scheduled["task_id"], "telegram:456", "456")["success"] is False
    assert scheduler.cancel(scheduled["task_id"], "telegram:123", "123")["success"] is True
    assert scheduler.list_tasks("telegram:123", "123")["tasks"][0]["status"] == "cancelled"
    scheduler.close(wait=True)


def test_due_instruction_runs_through_the_strict_agent_tool_loop(tmp_path, monkeypatch):
    import json
    import providers

    target = tmp_path / "scheduled.txt"

    class StubProvider:
        def __init__(self):
            self.responses = [
                "```json\\n" + json.dumps({
                    "tool": "write_file",
                    "parameters": {"path": str(target), "content": "réveillé"},
                }, ensure_ascii=False) + "\\n```",
                "La tâche planifiée est terminée.",
            ]

        def send_message(self, message, role="user", **kwargs):
            return {"success": True, "response": self.responses.pop(0), "tool_calls": []}

    provider = StubProvider()
    monkeypatch.setattr(providers, "create_provider", lambda config: provider)
    from src.core.scheduler import schedule_agent_task

    config = {"security": {"workspace": str(tmp_path), "approval_mode": "all"}}
    scheduler = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    scheduler.register_handler(
        "test-owner",
        lambda task: schedule_agent_task(
            config, task["instruction"], "test-owner", task.get("target_id")
        ),
    )
    scheduled = scheduler.schedule(
        "Écris le fichier demandé.", delay_seconds=0.03, owner_id="test-owner"
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        details = scheduler.list_tasks("test-owner")["tasks"]
        if details and details[0]["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)

    assert scheduled["task_id"] == details[0]["task_id"]
    assert details[0]["status"] == "completed", details[0]["error"]
    assert "La tâche planifiée est terminée." in details[0]["result"]
    assert target.read_text(encoding="utf-8") == "réveillé"
    scheduler.close(wait=True)


def test_schedule_tool_uses_interface_context_and_wait_returns(tmp_path, monkeypatch):
    import src.core.scheduler as scheduler_module

    scheduler = TaskScheduler(tmp_path / "schedule.sqlite3", poll_interval=0.02)
    monkeypatch.setattr(scheduler_module, "_scheduler", scheduler)
    bridge = ToolBridge(workspace=str(tmp_path), enable_subagents=False)
    bridge.set_scheduler_context("web", "session-1")

    result = list(bridge.execute_tool(
        "schedule_task", {"instruction": "Rappeler le rendez-vous", "delay_seconds": 600}
    ))[-1]
    assert result["success"] is True
    stored = scheduler.list_tasks("web", "session-1")["tasks"]
    assert stored[0]["instruction"] == "Rappeler le rendez-vous"

    assert current_scheduler_context() == ("default", None)
    waited = wait_for(0.01, "test")
    assert waited["success"] is True
    assert "Attente terminée" in waited["stdout"]

    interrupted = threading.Event()
    threading.Timer(0.02, interrupted.set).start()
    assert wait_for(5, cancel_event=interrupted)["error"] == "interrupted"
    bridge.close()
    scheduler.close(wait=True)
