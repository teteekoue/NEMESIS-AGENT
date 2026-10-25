"""The agent loop must not stop on an arbitrary iteration ceiling."""

import asyncio
import os

import pytest

from web.backend import agent_runner


class _FakeProvider:
    """Emits one bash tool call per turn, then a final answer."""

    model = "fake"
    host = "127.0.0.1"
    port = 0

    def __init__(self, calls: int):
        self.calls = calls
        self.sent = 0

    def test_connection(self, max_age: float | None = None) -> bool:
        return True

    def send_message(self, message, role="user", cancel_event=None, system_prefix=""):
        self.sent += 1
        if self.sent <= self.calls:
            return {
                "success": True,
                "response": f"Étape {self.sent}.",
                "tool_calls": [
                    {
                        "type": "bash",
                        "content": {"command": f"echo step-{self.sent}"},
                    }
                ],
            }
        return {"success": True, "response": "Travail terminé.", "tool_calls": []}

    def configure_session(self, *args, **kwargs):
        return None


def test_web_loop_executes_far_more_than_fifty_iterations(tmp_path, monkeypatch):
    provider = _FakeProvider(calls=60)

    monkeypatch.setattr(agent_runner, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        agent_runner, "create_executor_from_config",
        lambda config, bridge=None: __import__(
            "src.core.tool_bridge", fromlist=["ToolBridge"]
        ).ToolBridge(workspace=str(tmp_path), config=config, enable_subagents=False),
    )

    loop = asyncio.new_event_loop()
    runner = agent_runner.AgentRunner(
        {"provider": {"type": "nemapi", "model": "fake"},
         "security": {"approval_mode": "all", "workspace": str(tmp_path)}},
        loop,
        approval_mode="all",
    )
    runner._conn_ok = True
    events = []
    runner.emit = lambda event_type, **data: events.append({"type": event_type, **data})
    try:
        tools_used = runner._run_turn("Fais 60 étapes.")
    finally:
        runner.close()
        loop.close()

    assert tools_used == 60
    assert any(
        event["type"] == "assistant_text" and "terminé" in event["text"].lower()
        for event in events
    )
    assert not any(
        event["type"] == "error" and "limite" in str(event.get("message", "")).lower()
        for event in events
    )


def test_source_has_no_iteration_ceiling():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for relative in (
        "agent.py",
        "telegram_bot.py",
        "web/backend/agent_runner.py",
        "src/core/agent_manager.py",
    ):
        with open(os.path.join(root, relative), "r", encoding="utf-8") as handle:
            content = handle.read()
        assert "MAX_TOOL_ITERATIONS" not in content, relative
        assert "max_tool_iterations" not in content, relative
        assert "max_iterations = 10" not in content, relative
