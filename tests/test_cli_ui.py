from io import StringIO

from rich.console import Console

import cli
from agent import NemesisApp
from src.ui.chat_ui import ChatUI


class _FakeClient:
    def __init__(self):
        self.session = None
        self.reset = False

    def configure_session(self, session_id, initialized=False):
        self.session = (session_id, initialized)

    def reset_conversation(self):
        self.reset = True

    def set_conversation(self, messages):
        self.messages = messages


class _FakeComposer:
    def __init__(self):
        self.messages = []

    def display_user_message(self, text):
        self.messages.append(("user", text))

    def display_ai_message(self, text):
        self.messages.append(("assistant", text))

    def display_tool_start(self, tool_name, params):
        self.messages.append(("tool_start", tool_name, params))

    def display_tool_result(self, result, tool_name="", duration=None):
        self.messages.append(("tool", tool_name, result["output"], duration))


def test_cli_entrypoint_reuses_the_agent_app():
    assert cli.NemesisApp is NemesisApp
    assert callable(cli.main)


def test_resume_session_restores_transcript_and_provider_state(monkeypatch):
    from web.backend import sessions

    monkeypatch.setattr(
        sessions,
        "get_session",
        lambda _session_id: {
            "title": "Session existante",
            "nemapi_initialized": True,
            "messages": [
                {"kind": "user", "text": "Bonjour"},
                {"kind": "assistant", "text": "Salut"},
                {"kind": "tool", "name": "bash", "output": "ok", "success": True, "duration": 0.2},
            ],
        },
    )
    app = NemesisApp.__new__(NemesisApp)
    app.console = Console(file=StringIO(), color_system=None)
    app.client = _FakeClient()
    app.composer = _FakeComposer()

    assert app._start_session("session-1") is True
    assert app.session_id == "session-1"
    assert app.client.session == ("session-1", True)
    assert app.client.reset is True
    assert [message["role"] for message in app.client.messages] == [
        "user",
        "assistant",
        "tool_result",
    ]
    assert app._prompt_sent is True
    assert app.composer.messages == [
        ("user", "Bonjour"),
        ("assistant", "Salut"),
        ("tool_start", "bash", {}),
        ("tool", "bash", "ok", 0.2),
    ]


def test_tool_cards_render_parameters_and_output():
    output = StringIO()
    chat = ChatUI(Console(file=output, color_system=None, width=90))

    chat.tool_start("read_file", {"path": "README.md", "limit": 5})
    chat.tool_result(
        {"success": True, "stdout": "secret file contents not displayed"},
        "read_file",
        duration=0.1,
    )
    chat.tool_start("bash", {"command": "echo nemesis"})
    chat.tool_result({"success": True, "stdout": "nemesis"}, "bash", duration=0.2)

    rendered = output.getvalue()
    assert "read_file" in rendered
    assert "README.md" in rendered
    assert "\u2713 0.1s" in rendered
    assert "bash" in rendered
    assert "echo nemesis" in rendered
    assert "\u2713 0.2s" in rendered
    assert "Action terminée." not in rendered
    assert "╭" not in rendered


def test_non_bash_tool_failure_is_brief_and_hides_output():
    output = StringIO()
    chat = ChatUI(Console(file=output, color_system=None, width=90))

    chat.tool_start("grep", {"pattern": "TODO", "path": "src"})
    chat.tool_result(
        {"success": False, "stdout": "internal traceback and details"},
        "grep",
        duration=0.3,
    )

    rendered = output.getvalue()
    assert "grep" in rendered
    assert "TODO" in rendered
    assert "\u2717 0.3s" in rendered
    assert "internal traceback and details" not in rendered
    assert "╭" not in rendered


def test_demo_renders_messages_tool_calls_and_outputs():
    output = StringIO()
    cli.run_demo(Console(file=output, color_system=None, width=100))

    rendered = output.getvalue()
    assert "Lis le guide du projet" in rendered
    assert "README.md" in rendered
    assert "read_file" in rendered or "bash" in rendered
    assert "Texte de démonstration masqué" not in rendered
    assert "3 passed, 0 failed" in rendered
    assert "fictif" in rendered
    assert "aucun provider ni outil exécuté" in rendered
    assert "bash" in rendered
    assert "◆" not in rendered


def test_config_accepts_full_v1_url_and_optional_empty_token(monkeypatch):
    import providers
    from src.core import default_commands

    class FakeProvider:
        model = "qwen-chat"

        def test_connection(self):
            return True

        def list_models(self):
            return []

    class FakeApp:
        config = {
            "provider": {"type": "nemapi", "model": "qwen-chat"},
            "nemapi": {"host": "127.0.0.1", "port": 8090},
            "security": {"workspace": "/tmp/workspace"},
        }

        def reconfigure_provider(self, new_config):
            self.saved_config = new_config
            return True, "ok"

    app = FakeApp()
    answers = iter(["https://api.example.test/custom/v1", "qwen-chat"])
    monkeypatch.setattr(default_commands, "_active_app", app)
    monkeypatch.setattr(default_commands.console, "input", lambda _prompt: next(answers))
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "")
    monkeypatch.setattr(providers, "create_provider", lambda _config: FakeProvider())

    default_commands.config_command()

    assert app.saved_config["nemapi"] == {"url": "https://api.example.test/custom/v1"}
    assert app.saved_config["provider"]["model"] == "qwen-chat"


def test_nemapi_provider_sends_auth_header_only_when_token_configured():
    from providers.nemapi_v3 import NemapiV3Provider

    class FakeResponse:
        status_code = 200
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def json(self):
            return {"data": [{"id": "qwen-chat"}]}

        def raise_for_status(self):
            return None

    class FakeSession:
        def __init__(self):
            self.get_headers = []
            self.post_headers = []

        def get(self, _url, **kwargs):
            self.get_headers.append(kwargs.get("headers", {}))
            return FakeResponse()

        def post(self, _url, **kwargs):
            self.post_headers.append(kwargs.get("headers", {}))
            return FakeResponse()

    provider = NemapiV3Provider({
        "provider": {"type": "nemapi", "model": "qwen-chat"},
        "nemapi": {"url": "https://api.example.test/v1", "token": "secret-token"},
    })
    provider._session = FakeSession()
    assert provider._auth_headers() == {
        "Authorization": "Bearer secret-token",
        "X-NemApi-Agent": "nemesis",
    }
    provider.test_connection()
    provider.list_models()
    provider.send_message("bonjour")
    assert provider._session.get_headers[0]["Authorization"] == "Bearer secret-token"
    assert provider._session.post_headers[0]["Authorization"] == "Bearer secret-token"
    assert provider._session.post_headers[0]["X-NemApi-Agent"] == "nemesis"

    provider.api_token = ""
    assert provider._auth_headers() == {"X-NemApi-Agent": "nemesis"}


def test_web_public_config_never_returns_nemapi_token(monkeypatch):
    from web.backend import config_store

    monkeypatch.setattr(config_store, "load_config", lambda: {
        "provider": {"type": "nemapi", "model": "qwen-chat"},
        "nemapi": {"url": "https://api.example.test/v1", "token": "secret-token"},
        "security": {"workspace": "/tmp/workspace", "approval_mode": "confirm"},
    })
    public = config_store.public_config()

    assert public["nemapi"] == {"url": "https://api.example.test/v1"}


def test_cli_skills_menu_toggles_skill_for_new_conversations(monkeypatch):
    from src.core import default_commands
    from web.backend import settings

    class FakeSkillManager:
        def list_installed(self):
            return [{"name": "cybersecurity-audit", "version": "1.0.0", "description": "Audit défensif"}]

    enabled = False
    calls = []

    def list_skills():
        return [{"name": "cybersecurity-audit", "enabled": enabled}]

    def set_skill_enabled(name, value):
        nonlocal enabled
        enabled = value
        calls.append((name, value))

    answers = iter(["6", "cybersecurity-audit", "q"])
    monkeypatch.setattr(default_commands, "skill_mgr", FakeSkillManager())
    monkeypatch.setattr(default_commands.console, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(settings, "list_skills", list_skills)
    monkeypatch.setattr(settings, "set_skill_enabled", set_skill_enabled)

    default_commands.skills_command()

    assert calls == [("cybersecurity-audit", True)]
    assert enabled
