"""Scénarios de bout en bout sur un provider stub local (aucun réseau externe).

Ces tests exécutent les VRAIES boucles Web, CLI et Telegram avec le vrai
ToolBridge et le vrai ActionParser en mode strict. Ils reproduisent le
problème du rapport : écriture d'un long fichier en plusieurs blocs, appel
tronqué, DSML, et corrélation du FEEDBACK.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from action_parser import ActionParser
from src.core.tool_bridge import ToolBridge
from web.backend import agent_runner as runner_module
from web.backend import sessions as session_store


class ScriptedProvider:
    """Provider déterministe : renvoie une liste de réponses, enregistre tout."""

    supports_images = False
    model = "stub-local"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def configure_session(self, session_id, initialized=False):
        pass

    def test_connection(self, max_age=None):
        return True

    def send_message(self, message, role="user", **kwargs):
        self.calls.append(
            {
                "message": message,
                "role": role,
                "system_prefix": kwargs.get("system_prefix", ""),
            }
        )
        index = len(self.calls) - 1
        response = self.responses[index] if index < len(self.responses) else "Terminé."
        return {"success": True, "response": response, "tool_calls": []}


def _call(name, **parameters):
    payload = {"tool": name, "parameters": parameters}
    return "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


def _write_then_append_script(target, chunks):
    responses = [_call("write_file", path=str(target), content=chunks[0])]
    for chunk in chunks[1:]:
        responses.append(_call("append", path=str(target), content=chunk))
    responses.append(_call("read_file", path=str(target)))
    responses.append("Fichier écrit et vérifié.")
    return responses


@pytest.fixture(autouse=True)
def _isolated_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "SESSIONS_DIR", tmp_path / "web_sessions")


@pytest.fixture
def web_env(tmp_path, monkeypatch):
    target = tmp_path / "rapport.txt"
    chunks = ["Premier bloc\n", "Deuxième bloc\n", "Troisième bloc\n"]
    provider = ScriptedProvider(_write_then_append_script(target, chunks))
    bridge = ToolBridge(workspace=str(tmp_path), enable_subagents=False)
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(workspace=str(tmp_path), enable_subagents=False),
    )
    config = {
        "provider": {"type": "nemapi", "model": "stub-local"},
        "security": {"approval_mode": "all"},
    }
    loop = asyncio.new_event_loop()
    yield loop, config, provider, bridge, target, chunks
    loop.close()
    bridge.close()


# ---------------------------------------------------------------------------
# Web : /ws/chat -> AgentRunner
# ---------------------------------------------------------------------------


def test_web_loop_writes_a_long_file_with_append(web_env):
    loop, config, provider, _, target, chunks = web_env
    runner = runner_module.AgentRunner(config, loop)

    tool_count = runner._run_turn("Écris un long rapport en plusieurs blocs.")

    assert target.read_text(encoding="utf-8") == "".join(chunks)
    assert tool_count == 4  # write_file + 2 append + read_file de vérification


def test_web_loop_feedback_is_correlated_and_prompt_is_complete(web_env):
    loop, config, provider, _, target, _ = web_env
    runner = runner_module.AgentRunner(config, loop)
    runner._run_turn("Écris le rapport.")

    feedbacks = [call["message"] for call in provider.calls[1:]]
    for number, feedback in enumerate(feedbacks[:4], start=1):
        assert feedback.startswith("FEEDBACK:\n"), feedback[:80]
        assert f"[call #{number} —" in feedback, feedback[:120]
    # Le prompt système part une seule fois, avec les nouveaux garde-fous.
    assert "**append**" in provider.calls[0]["system_prefix"]
    assert "DSML" in provider.calls[0]["system_prefix"]
    assert all(call["system_prefix"] == "" for call in provider.calls[1:])


def test_web_loop_refuses_a_truncated_call_without_writing(tmp_path, monkeypatch):
    target = tmp_path / "tronque.txt"
    truncated = (
        '```json\n{"tool": "write_file", "parameters": {"path": "'
        + str(target)
        + '", "content": "un contenu qui ne se termine jamais'
    )
    responses = [
        truncated,
        _call("write_file", path=str(target), content="OK\n"),
        "Corrigé.",
    ]
    provider = ScriptedProvider(responses)
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(workspace=str(tmp_path), enable_subagents=False),
    )
    loop = asyncio.new_event_loop()
    try:
        runner = runner_module.AgentRunner(
            {
                "provider": {"type": "nemapi"},
                "security": {"approval_mode": "all"},
            },
            loop,
        )
        tool_count = runner._run_turn("Écris le fichier.")
    finally:
        loop.close()

    assert target.read_text(encoding="utf-8") == "OK\n"
    assert tool_count == 1  # seul l'appel valide a été exécuté
    error_feedback = provider.calls[1]["message"]
    assert "protocol error" in error_feedback
    assert "PROTOCOL ERROR" in error_feedback
    assert "append" in error_feedback
    assert "[call #1 —" in error_feedback
    assert "[call #2 —" in provider.calls[2]["message"]


def test_web_loop_refuses_dsml_and_recovers(tmp_path, monkeypatch):
    target = tmp_path / "dsml.txt"
    dsml = (
        '<|DSML|>{"tool": "write_file", "parameters": {"path": "'
        + str(target)
        + '", "content": "ne pas écrire"}}'
    )
    provider = ScriptedProvider(
        [dsml, _call("append", path=str(target), content="bloc valide"), "Reprise OK."]
    )
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(workspace=str(tmp_path), enable_subagents=False),
    )
    loop = asyncio.new_event_loop()
    try:
        runner = runner_module.AgentRunner(
            {"provider": {"type": "nemapi"}, "security": {"approval_mode": "all"}},
            loop,
        )
        tool_count = runner._run_turn("Écris le fichier.")
    finally:
        loop.close()

    assert target.read_text(encoding="utf-8") == "bloc valide"
    assert tool_count == 1
    assert "DSML" in provider.calls[1]["message"]


def test_web_loop_continues_after_more_than_three_invalid_calls(tmp_path, monkeypatch):
    dsml = '<|DSML|>{"tool": "bash", "parameters": {"command": "ls"}}'
    provider = ScriptedProvider([dsml, dsml, dsml, dsml, _call("bash", command="echo OK")])
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(workspace=str(tmp_path), enable_subagents=False),
    )
    loop = asyncio.new_event_loop()
    try:
        runner = runner_module.AgentRunner(
            {"provider": {"type": "nemapi"}, "security": {"approval_mode": "all"}},
            loop,
        )
        tool_count = runner._run_turn("Test.")
    finally:
        loop.close()

    assert tool_count == 1
    assert len(provider.calls) == 6  # 4 erreurs + exécution valide + réponse finale


# ---------------------------------------------------------------------------
# CLI : NemesisApp._process_cycle
# ---------------------------------------------------------------------------


class _FakeConsole:
    def __init__(self):
        self.lines = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))


class _FakeComposer:
    def __init__(self):
        self.tool_starts = []
        self.tool_results = []
        self.messages = []

    def display_user_message(self, message):
        self.messages.append(message)

    def display_ai_message(self, message):
        self.messages.append(message)

    def display_tool_start(self, name, params):
        self.tool_starts.append((name, params))

    def display_tool_result(self, result, tool_name=None, duration=0):
        self.tool_results.append((tool_name, result))

    def display_todo(self, items):
        pass

    def display_task_summary(self, elapsed, tools, files):
        pass

    def display_thinking(self, label):
        def spinner():
            yield None
            while True:
                yield None

        return spinner()

    def live_terminal(self, name):
        raise RuntimeError("bash non utilisé dans ce scénario")

    def prompt_input(self, label):
        return ""


def _cli_app(tmp_path, provider, monkeypatch):
    import agent as agent_module

    monkeypatch.setattr(
        "src.core.default_commands.increment_message_count", lambda: None
    )
    app = agent_module.NemesisApp.__new__(agent_module.NemesisApp)
    app._conn_ok = True
    app._send_system_prompt = True
    app._prompt_sent = False
    app._session_initialized = False
    app._interrupted = False
    app.session_id = None
    app.debug = False
    app.config = {"security": {"approval_mode": "all"}}
    app.executor = ToolBridge(workspace=str(tmp_path), enable_subagents=False)
    app.client = provider
    app.parser = ActionParser(strict=True)
    app.console = _FakeConsole()
    app.composer = _FakeComposer()
    app._output_counter = 0
    app._hidden_outputs = {}
    app._MAX_HIDDEN_OUTPUTS = 20
    app._parse_response = lambda resp: agent_module.NemesisApp._parse_response(app, resp)
    app._format_feedback = lambda action, res: agent_module.NemesisApp._format_feedback(
        app, action, res
    )
    app._record_session_message = lambda message: None
    app._prune_hidden_outputs = lambda: None
    app._consume_input_interrupt = lambda: False
    app._ask_for_authorization = lambda name, params, risk="medium": True
    return app, agent_module


def test_cli_loop_writes_a_long_file_with_append(tmp_path, monkeypatch):
    target = tmp_path / "cli.txt"
    chunks = ["A\n", "B\n", "C\n"]
    provider = ScriptedProvider(_write_then_append_script(target, chunks))
    app, _ = _cli_app(tmp_path, provider, monkeypatch)

    app._process_cycle("Écris le fichier.")

    assert target.read_text(encoding="utf-8") == "A\nB\nC\n"
    assert [name for name, _ in app.composer.tool_starts] == [
        "write_file",
        "append",
        "append",
        "read_file",
    ]
    for number, call in enumerate(provider.calls[1:5], start=1):
        assert f"[call #{number} —" in call["message"], call["message"][:120]
    assert "**append**" in provider.calls[0]["system_prefix"]


def test_cli_loop_does_not_execute_a_truncated_call(tmp_path, monkeypatch):
    target = tmp_path / "cli_tronque.txt"
    truncated = (
        '```json\n{"tool": "write_file", "parameters": {"path": "'
        + str(target)
        + '", "content": "jamais terminé'
    )
    provider = ScriptedProvider(
        [truncated, _call("append", path=str(target), content="OK"), "Fini."]
    )
    app, _ = _cli_app(tmp_path, provider, monkeypatch)

    app._process_cycle("Écris le fichier.")

    assert target.read_text(encoding="utf-8") == "OK"
    assert [name for name, _ in app.composer.tool_starts] == ["append"]
    assert "PROTOCOL ERROR" in provider.calls[1]["message"]


# ---------------------------------------------------------------------------
# Telegram : NemesisTelegramBot._process_ai_request
# ---------------------------------------------------------------------------


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self.sent.append(text)
        return object()


class _FakeUpdate:
    class _EffectiveChat:
        id = 4242

    effective_chat = _EffectiveChat()


class _FakeContext:
    def __init__(self):
        self.bot = _FakeBot()


def test_telegram_loop_writes_a_long_file_with_append(tmp_path, monkeypatch):
    telegram_bot = pytest.importorskip("telegram_bot")

    target = tmp_path / "telegram.txt"
    chunks = ["un\n", "deux\n", "trois\n"]
    provider = ScriptedProvider(_write_then_append_script(target, chunks))
    executor = ToolBridge(workspace=str(tmp_path), enable_subagents=False)

    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot.workspace = str(tmp_path)
    bot.executor = executor
    bot.registry = executor.registry
    bot.parser = ActionParser(strict=True)
    bot.providers = {4242: provider}
    bot.prompt_system_sent = {}
    bot._feedback_sequences = {}
    bot._get_chat_config = lambda chat_id: {"nemapi": {"model": "stub-local"}}
    bot._get_provider = lambda chat_id: provider
    bot._format_ai_response = lambda outputs, provider_type, model: "\n".join(outputs)
    bot._cleanup_thinking_message = lambda context, chat_id, status: asyncio.sleep(0)

    async def fake_send_long(context, chat_id, text):
        context.bot.sent.append(text)

    bot._send_long_message = fake_send_long

    context = _FakeContext()
    asyncio.run(
        bot._process_ai_request(_FakeUpdate(), context, "Écris le fichier.", 4242)
    )

    assert target.read_text(encoding="utf-8") == "un\ndeux\ntrois\n"
    assert "**append**" in provider.calls[0]["system_prefix"]
    feedbacks = [call["message"] for call in provider.calls[1:5]]
    for number, feedback in enumerate(feedbacks, start=1):
        assert feedback.startswith("FEEDBACK:\n")
        assert f"[call #{number} —" in feedback, feedback[:120]
    executor.close()


def test_telegram_displays_only_the_final_reply_as_readable_plain_text(tmp_path):
    telegram_bot = pytest.importorskip("telegram_bot")

    target = tmp_path / "value.txt"
    target.write_text("vérifié", encoding="utf-8")
    first = "Je commence par vérifier.\n" + _call("read_file", path=str(target))
    final = "## Résultat\n\nLe contenu est **vérifié** : `vérifié`.\n\n- étape une\n- étape deux"
    provider = ScriptedProvider([first, final])
    executor = ToolBridge(workspace=str(tmp_path), enable_subagents=False)
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot.workspace = str(tmp_path)
    bot.executor = executor
    bot.registry = executor.registry
    bot.parser = ActionParser(strict=True)
    bot.providers = {4242: provider}
    bot.prompt_system_sent = {}
    bot._feedback_sequences = {}
    bot._event_loop = None
    bot._get_chat_config = lambda chat_id: {"nemapi": {"model": "stub-local"}}
    bot._get_provider = lambda chat_id: provider
    bot._cleanup_thinking_message = lambda context, chat_id, status: asyncio.sleep(0)
    context = _FakeContext()

    asyncio.run(
        bot._process_ai_request(_FakeUpdate(), context, "Vérifie puis résume.", 4242)
    )

    final_message = context.bot.sent[-1]
    assert "Résultat" in final_message
    assert "vérifié" in final_message
    assert "• étape une" in final_message
    assert "**" not in final_message and "`" not in final_message
    assert "Je commence par vérifier" not in final_message
    assert len([text for text in context.bot.sent if text.startswith("NEMESIS")]) == 1
    executor.close()


def test_telegram_execute_tool_accepts_append(tmp_path):
    telegram_bot = pytest.importorskip("telegram_bot")

    target = tmp_path / "telegram_append.txt"
    executor = ToolBridge(workspace=str(tmp_path), enable_subagents=False)
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot.executor = executor
    bot.registry = executor.registry

    async def run():
        first = await bot._execute_tool(
            "write_file", {"path": str(target), "content": "A"}, 1, _FakeContext()
        )
        second = await bot._execute_tool(
            "append", {"path": str(target), "content": "B"}, 1, _FakeContext()
        )
        return first, second

    first, second = asyncio.run(run())
    assert first["success"] and second["success"]
    assert target.read_text(encoding="utf-8") == "AB"
    executor.close()


def test_web_loop_keeps_native_tool_calls_despite_xml_marker(tmp_path, monkeypatch):
    """Un provider OpenAI-compatible garde ses tool_calls natifs."""
    target = tmp_path / "natif.txt"

    class NativeProvider(ScriptedProvider):
        def send_message(self, message, role="user", **kwargs):
            result = super().send_message(message, role=role, **kwargs)
            if len(self.calls) == 1:
                result["response"] = "<tool_call></tool_call>"
                result["tool_calls"] = [
                    {"type": "append", "content": {"path": str(target), "content": "NATIF"}}
                ]
            return result

    provider = NativeProvider(["OK."])
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(workspace=str(tmp_path), enable_subagents=False),
    )
    loop = asyncio.new_event_loop()
    try:
        runner = runner_module.AgentRunner(
            {"provider": {"type": "nemapi"}, "security": {"approval_mode": "all"}},
            loop,
        )
        tool_count = runner._run_turn("Écris le fichier.")
    finally:
        loop.close()

    assert target.read_text(encoding="utf-8") == "NATIF"
    assert tool_count == 1


def test_protocol_error_counter_resets_after_a_valid_call(tmp_path, monkeypatch):
    """Deux erreurs, un appel valide, deux erreurs : le tour continue."""
    target = tmp_path / "reset.txt"
    dsml = '<|DSML|>{"tool": "bash", "parameters": {"command": "ls"}}'
    provider = ScriptedProvider(
        [
            dsml,
            dsml,
            _call("append", path=str(target), content="OK"),
            dsml,
            dsml,
            "Terminé.",
        ]
    )
    monkeypatch.setattr(runner_module, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        runner_module, "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(workspace=str(tmp_path), enable_subagents=False),
    )
    loop = asyncio.new_event_loop()
    try:
        runner = runner_module.AgentRunner(
            {"provider": {"type": "nemapi"}, "security": {"approval_mode": "all"}},
            loop,
        )
        tool_count = runner._run_turn("Test.")
    finally:
        loop.close()

    assert target.read_text(encoding="utf-8") == "OK"
    assert tool_count == 1
    assert len(provider.calls) == 6
