"""Reprise de session Web : le prompt système n'est envoyé qu'une seule fois."""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from web.backend import agent_runner as runner_module
from web.backend import sessions as session_store


class FakeProvider:
    supports_images = False
    model = "qwen-chat"
    host = "127.0.0.1"
    port = 8090

    def __init__(self):
        self.calls = []
        self.configured = []
        self._prompt_sent = False

    def configure_session(self, session_id, initialized=False):
        self.configured.append((session_id, initialized))
        self._prompt_sent = bool(initialized)

    def test_connection(self, max_age=None):
        return True

    def send_message(self, message, role="user", cancel_event=None, system_prefix="", images=None):
        self.calls.append({
            "message": message,
            "role": role,
            "system_prefix": system_prefix,
        })
        return {"success": True, "response": "ok", "tool_calls": []}


class FakeRegistry:
    def __init__(self):
        self._tools = {}


class FakeExecutor:
    def __init__(self, prompt="SYSTEM_PROMPT_TOOLS"):
        self.prompt = prompt
        self.registry = FakeRegistry()

    def get_system_prompt(self):
        return self.prompt

    def set_cancel_event(self, event):
        pass

    def set_subagent_authorizer(self, callback):
        pass

    def close(self):
        pass


class FakeParser:
    def parse(self, raw):
        return {"text": raw, "actions": []}


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path, monkeypatch):
    """Aucun test ne doit écrire dans la vraie config utilisateur."""
    monkeypatch.setattr(session_store, "SESSIONS_DIR", tmp_path / "web_sessions")


@pytest.fixture
def env(tmp_path, monkeypatch):
    provider = FakeProvider()
    executor = FakeExecutor()
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: executor,
    )
    monkeypatch.setattr(runner_module, "ActionParser", lambda *a, **k: FakeParser())
    loop = asyncio.new_event_loop()
    config = {
        "provider": {"type": "nemapi", "model": "qwen-chat"},
        "security": {"approval_mode": "all"},
    }
    yield loop, config, provider, executor
    loop.close()


def _runner(env, session_id=None):
    loop, config, _, _ = env
    return runner_module.AgentRunner(config, loop, session_id=session_id)


def test_is_initialized_uses_flag_and_legacy_transcript():
    unknown = session_store.create_session(title="vide")
    assert session_store.is_initialized(unknown["id"]) is False
    assert session_store.is_initialized("inexistant") is False

    only_user = session_store.create_session(title="en cours")
    session_store.append_message(only_user["id"], {"kind": "user", "text": "bonjour", "at": 1})
    assert session_store.is_initialized(only_user["id"]) is False

    legacy = session_store.create_session(title="ancienne")
    session_store.append_message(legacy["id"], {"kind": "user", "text": "bonjour", "at": 1})
    session_store.append_message(legacy["id"], {"kind": "assistant", "text": "salut", "at": 2})
    assert session_store.is_initialized(legacy["id"]) is True

    flagged = session_store.create_session(title="marquée")
    session_store.mark_initialized(flagged["id"])
    assert session_store.is_initialized(flagged["id"]) is True


def test_first_message_of_new_session_sends_prompt_then_marks_initialized(env):
    loop, _, provider, _ = env
    runner = _runner(env)
    created = session_store.create_session(title="nouvelle")
    runner.bind_session(created["id"])

    runner._run_turn("bonjour")

    assert runner.session_id == created["id"]
    assert provider.calls[0]["system_prefix"] == "SYSTEM_PROMPT_TOOLS"
    assert session_store.is_initialized(created["id"]) is True
    assert provider.configured[-1] == (created["id"], False)


def test_followup_in_same_runner_never_resends_prompt(env):
    _, _, provider, _ = env
    runner = _runner(env)
    created = session_store.create_session(title="nouvelle")
    runner.bind_session(created["id"])

    runner._run_turn("bonjour")
    runner._run_turn("et ensuite ?")

    assert provider.calls[0]["system_prefix"] == "SYSTEM_PROMPT_TOOLS"
    assert provider.calls[1]["system_prefix"] == ""


def test_resumed_flagged_session_never_resends_prompt(env):
    _, _, provider, _ = env
    created = session_store.create_session(title="existante")
    session_store.mark_initialized(created["id"])

    runner = _runner(env, session_id=created["id"])
    runner._run_turn("je reprends")

    assert runner._prompt_sent is True
    assert provider.configured[0] == (created["id"], True)
    assert provider.calls[0]["system_prefix"] == ""


def test_resumed_legacy_transcript_never_resends_prompt(env):
    _, _, provider, _ = env
    created = session_store.create_session(title="ancienne")
    session_store.append_message(created["id"], {"kind": "user", "text": "bonjour", "at": 1})
    session_store.append_message(created["id"], {"kind": "assistant", "text": "salut", "at": 2})

    runner = _runner(env, session_id=created["id"])
    runner._run_turn("on continue")

    assert provider.configured[0] == (created["id"], True)
    assert provider.calls[0]["system_prefix"] == ""


def test_late_binding_to_legacy_session_suppresses_prompt(env):
    _, _, provider, _ = env
    created = session_store.create_session(title="ancienne")
    session_store.append_message(created["id"], {"kind": "user", "text": "bonjour", "at": 1})
    session_store.append_message(created["id"], {"kind": "assistant", "text": "salut", "at": 2})

    runner = _runner(env)  # socket ouvert sans ?session=
    runner.bind_session(created["id"])
    runner._run_turn("on reprend")

    assert provider.configured[-1] == (created["id"], True)
    assert provider.calls[0]["system_prefix"] == ""


def test_empty_catalogue_still_marks_session_initialized(env):
    _, _, _, executor = env
    executor.prompt = ""
    runner = _runner(env)
    created = session_store.create_session(title="nouvelle")
    runner.bind_session(created["id"])

    runner._run_turn("bonjour")

    assert session_store.is_initialized(created["id"]) is True


def test_bind_session_is_idempotent_and_ignores_unknown_ids(env):
    _, _, provider, _ = env
    runner = _runner(env)
    runner.bind_session("")
    runner.bind_session(None)
    assert runner.session_id is None

    created = session_store.create_session(title="nouvelle")
    runner.bind_session(created["id"])
    count = len(provider.configured)
    runner.bind_session(created["id"])
    assert len(provider.configured) == count


def test_ws_chat_binds_session_id_sent_with_user_message(monkeypatch):
    """Le premier message crée la session après l'ouverture du socket."""
    from starlette.websockets import WebSocketDisconnect
    from web.backend import app as app_module

    recorded = {}

    class FakeRunner:
        def __init__(self, config, loop, session_id=None, model=None, approval_mode=None):
            self.events = asyncio.Queue()
            self.bound = []
            self.started = None
            recorded["runner"] = self

        def provider_info(self):
            return {"type": "nemapi"}

        def bind_session(self, sid):
            self.bound.append(sid)

        def start_turn(self, message, images=None):
            self.started = (message, images)

        def close(self):
            pass

    class FakeWebSocket:
        query_params = {}

        def __init__(self, incoming):
            self.incoming = iter(incoming)
            self.sent = []

        async def accept(self):
            pass

        async def send_json(self, payload):
            self.sent.append(payload)

        async def receive_json(self):
            try:
                return next(self.incoming)
            except StopIteration as exc:
                raise WebSocketDisconnect() from exc

        async def close(self):
            pass

    monkeypatch.setattr(app_module, "AgentRunner", FakeRunner)
    monkeypatch.setattr(app_module, "load_config", lambda: {"provider": {"type": "nemapi"}})

    ws = FakeWebSocket([
        {
            "type": "user_message",
            "content": "bonjour",
            "session_id": "abc123",
            "images": ["data:image/png;base64,AAAA"],
        },
        {"type": "ping"},
    ])
    asyncio.run(app_module.ws_chat(ws))

    assert ws.sent[0]["type"] == "ready"
    assert {"type": "pong"} in ws.sent
    assert recorded["runner"].bound == ["abc123"]
    assert recorded["runner"].started[0] == "bonjour"
    assert recorded["runner"].started[1] == ["data:image/png;base64,AAAA"]
