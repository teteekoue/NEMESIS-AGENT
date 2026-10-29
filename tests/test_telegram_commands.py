from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _FakeBot:
    def __init__(self):
        self.sent = []
        self.commands = None

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

    async def set_my_commands(self, commands):
        self.commands = commands


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


def test_sensitive_direct_tool_executes_without_confirmation(telegram_bot):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    calls = []

    async def execute(tool_name, params, chat_id, _context):
        calls.append((tool_name, params, chat_id))
        return {"success": True, "content": "Fichier supprimé."}

    bot._execute_tool = execute
    bot._format_tool_result_for_display = lambda result: result["content"]
    bot.executor = SimpleNamespace(risk_for=lambda *_args: "high")

    result = asyncio.run(
        bot._execute_direct_tool(
            SimpleNamespace(), "delete_file", {"path": "danger.txt"}, 71
        )
    )

    assert result == "Fichier supprimé."
    assert calls == [("delete_file", {"path": "danger.txt"}, 71)]
    assert not hasattr(bot, "_ask_for_confirmation")


def test_approval_command_is_informational_and_never_changes_policy(telegram_bot):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    config = {"nemapi": {}, "security": {"workspace": "/tmp"}}
    saved = []
    bot._get_chat_config = lambda _chat_id: config
    bot._save_chat_config = lambda _chat_id, value: saved.append(value.copy()) or True
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=71),
        effective_message=SimpleNamespace(text="/approval all"),
    )
    context = SimpleNamespace(args=["all"], bot=_FakeBot())

    asyncio.run(bot.cli_command(update, context))

    assert "approval_mode" not in config["security"]
    assert not saved
    assert "désactivées" in context.bot.sent[0]["text"]


def test_chat_config_ignores_legacy_authorization_settings(telegram_bot, tmp_path, monkeypatch):
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

    assert loaded["security"] == {"workspace": str(tmp_path)}
    assert loaded["session_id"] == "session-123"
