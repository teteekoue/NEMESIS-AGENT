from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _FakeQuery:
    def __init__(self, data, chat_id=71):
        self.data = data
        self.message = SimpleNamespace(chat_id=chat_id)
        self.edited = []
        self.answered = False

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text):
        self.edited.append(text)


class _FakeBot:
    def __init__(self):
        self.sent = []
        self.commands = None

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def set_my_commands(self, commands):
        self.commands = commands


class _FakeProvider:
    supports_images = False

    def __init__(self, response="Terminé après l'action."):
        self.response = response
        self.calls = []

    def send_message(self, message, role="user", **kwargs):
        self.calls.append((message, role, kwargs))
        return {"success": True, "response": self.response}


@pytest.fixture
def telegram_bot():
    return pytest.importorskip("telegram_bot")


def test_telegram_command_catalog_tracks_cli_registry(telegram_bot):
    from src.core import default_commands  # noqa: F401
    from src.core.commands import registry

    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    catalog = bot._cli_command_catalog()
    exposed_cli_names = {original for _telegram, original, _description in catalog}
    assert exposed_cli_names == {command.name for command in registry.list_commands()}

    specs = {name: description for name, _original, description in bot._telegram_command_specs()}
    for command in registry.list_commands():
        telegram_name = command.name.replace(".", "_")
        assert telegram_name in specs
        assert specs[telegram_name] == command.description
    assert "nemesis_md" in specs
    assert "approval" in specs
    assert "sessions" in specs


def test_telegram_registers_published_commands_after_startup(telegram_bot):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot._event_loop = None
    fake_bot = _FakeBot()
    application = SimpleNamespace(bot=fake_bot)

    asyncio.run(bot._post_init(application))

    assert fake_bot.commands
    names = {command.command for command in fake_bot.commands}
    assert {"start", "provider", "new", "approval", "sessions", "nemesis_md"} <= names
    assert all("." not in name for name in names)


def test_always_approval_is_persisted_and_resumes_agent_without_markdown_parse_errors(
    telegram_bot,
):
    from action_parser import ActionParser

    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot.pending_actions = {
        71: {
            "tool_name": "delete_file",
            "params": {"path": "file_with_underscores.txt"},
        }
    }
    bot._session_authorized_tools = {}
    bot._feedback_sequences = {}
    bot.parser = ActionParser(strict=True)
    bot.executor = SimpleNamespace(risk_for=lambda *_args: "high")
    config = {
        "nemapi": {"model": "stub-local"},
        "security": {"approval_mode": "confirm", "authorized_tools": []},
    }
    saved = []
    bot._get_chat_config = lambda _chat_id: config
    bot._save_chat_config = lambda _chat_id, value: saved.append(value.copy()) or True
    bot._execute_tool = _execute_failure
    provider = _FakeProvider()
    bot._get_provider = lambda _chat_id: provider
    bot._format_ai_response = lambda outputs, _provider, _model: "\n".join(outputs)

    query = _FakeQuery("tool_always")
    context = SimpleNamespace(bot=_FakeBot())
    update = SimpleNamespace(callback_query=query)

    asyncio.run(bot.button_handler(update, context))

    assert query.answered
    assert "delete_file" in bot._session_authorized_tools[71]
    assert config["security"]["authorized_tools"] == ["delete_file"]
    assert saved
    assert "FEEDBACK:" in provider.calls[0][0]
    assert provider.calls[0][1] == "tool_result"
    assert "Terminé après l'action." in "\n".join(item["text"] for item in context.bot.sent)
    assert "autorisé pour cette session" in query.edited[-1]
    assert all("parse_mode" not in item for item in context.bot.sent)


async def _execute_failure(_tool_name, _params, _chat_id, _context):
    return {"success": False, "error": "outil indisponible"}


def test_direct_high_risk_tool_requests_confirmation_before_execution(telegram_bot):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot.executor = SimpleNamespace(risk_for=lambda *_args: "high")
    bot.pending_actions = {}
    bot._session_authorized_tools = {}
    config = {"security": {"approval_mode": "confirm", "authorized_tools": []}}
    bot._get_chat_config = lambda _chat_id: config
    bot._execute_tool = lambda *_args: pytest.fail("A high-risk tool ran before approval")
    requested = []

    async def ask(_update, _context, name, params):
        requested.append((name, params))
        bot.pending_actions[71] = {"tool_name": name, "params": params}

    bot._ask_for_confirmation = ask
    result = asyncio.run(
        bot._execute_direct_tool_or_confirm(
            SimpleNamespace(), SimpleNamespace(), "delete_file", {"path": "danger.txt"}, 71
        )
    )

    assert result == telegram_bot.TOOL_CONFIRMATION_PENDING
    assert requested == [("delete_file", {"path": "danger.txt"})]
    assert bot.pending_actions[71]["source"] == "direct"


def test_session_authorizations_survive_bot_reconstruction(telegram_bot):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot._session_authorized_tools = {}
    config = {"security": {"authorized_tools": ["delete_file"]}}
    bot._get_chat_config = lambda _chat_id: config

    assert bot._authorized_tools_for_chat(71) == {"delete_file"}


def test_approval_slash_command_updates_per_chat_policy(telegram_bot):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot._session_authorized_tools = {71: {"bash"}}
    config = {"nemapi": {}, "security": {"approval_mode": "confirm", "authorized_tools": []}}
    saved = []
    bot._get_chat_config = lambda _chat_id: config
    bot._save_chat_config = lambda _chat_id, value: saved.append(value.copy()) or True
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=71),
        effective_message=SimpleNamespace(text="/approval all"),
    )
    context = SimpleNamespace(args=["all"], bot=_FakeBot())

    asyncio.run(bot.cli_command(update, context))

    assert config["security"]["approval_mode"] == "all"
    assert config["security"]["authorized_tools"] == []
    assert bot._session_authorized_tools[71] == {"bash"}
    assert saved


def test_chat_config_persists_session_authorizations_and_approval_mode(telegram_bot, tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "BOT_CONFIG_DIR", tmp_path / "telegram-config")
    config = {
        "provider": {"type": "nemapi"},
        "nemapi": {"host": "localhost", "port": 8080, "model": "stub"},
        "security": {
            "workspace": str(tmp_path),
            "approval_mode": "confirm",
            "authorized_tools": ["delete_file", "bash"],
        },
        "session_id": "session-123",
    }

    assert telegram_bot.save_chat_config(71, config)
    loaded = telegram_bot.load_chat_config(71)

    assert loaded["security"]["approval_mode"] == "confirm"
    assert loaded["security"]["authorized_tools"] == ["delete_file", "bash"]
    assert loaded["session_id"] == "session-123"
