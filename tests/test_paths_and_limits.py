"""No workspace confinement, no iteration ceiling, explicit feedback."""

import os

import pytest

from src.core.agent_tools import create_registry, format_tool_feedback
from src.core.paths import (
    resolve_existing_path,
    resolve_path,
    reset_session_cwd,
    session_cwd,
    set_session_cwd,
)
from src.core.tool_bridge import ToolBridge
from src.core.tools.bash import BashInput, run_bash, run_bash_streamed


@pytest.fixture(autouse=True)
def _restore_cwd():
    original = session_cwd()
    yield
    set_session_cwd(original)


def _last(gen):
    result = None
    for update in gen:
        result = update
    return result


class TestAbsoluteAndRelativePaths:
    def test_relative_paths_follow_the_shell_directory(self, tmp_path):
        set_session_cwd(tmp_path)
        assert resolve_path("sub/file.txt") == os.path.realpath(
            str(tmp_path / "sub" / "file.txt")
        )

    def test_absolute_paths_are_never_redirected(self, tmp_path):
        outside = tmp_path / "elsewhere" / "note.txt"
        set_session_cwd("/")
        assert resolve_path(str(outside)) == os.path.realpath(str(outside))

    def test_existing_path_does_not_fall_back_to_a_workspace(self, tmp_path):
        set_session_cwd(tmp_path / "shell")
        (tmp_path / "shell").mkdir()
        assert resolve_existing_path("ghost.txt", fallback_base=str(tmp_path)) == os.path.realpath(
            str(tmp_path / "shell" / "ghost.txt")
        )


class TestWriteFile:
    def test_path_alias_is_honoured(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "file.txt"
        result = _last(
            ToolBridge(workspace=str(tmp_path)).execute_tool(
                "write_file", {"path": str(target), "content": "hello"}
            )
        )
        assert result["success"] is True
        assert target.read_text() == "hello"
        assert result["path"] == os.path.realpath(str(target))

    def test_directory_target_returns_a_clear_error(self, tmp_path):
        result = _last(
            ToolBridge(workspace=str(tmp_path)).execute_tool(
                "write_file", {"path": str(tmp_path), "content": "x"}
            )
        )
        assert result["success"] is False
        assert "directory" in (result["error"] or "").lower()

    def test_missing_path_is_reported(self, tmp_path):
        result = _last(
            ToolBridge(workspace=str(tmp_path)).execute_tool("write_file", {"content": "x"})
        )
        assert result["success"] is False
        assert "path" in (result["error"] or "").lower()

    def test_write_outside_any_workspace(self, tmp_path):
        bridge = ToolBridge(workspace=str(tmp_path / "start"))
        target = tmp_path / "outside.txt"
        result = _last(
            bridge.execute_tool("write_file", {"file_path": str(target), "content": "ok"})
        )
        assert result["success"] is True
        assert target.read_text() == "ok"

    def test_edit_accepts_path_alias_anywhere(self, tmp_path):
        target = tmp_path / "edit.txt"
        target.write_text("before", encoding="utf-8")
        bridge = ToolBridge(workspace=str(tmp_path / "start"))
        result = _last(
            bridge.execute_tool(
                "edit",
                {"path": str(target), "old_string": "before", "new_string": "after"},
            )
        )
        assert result["success"] is True
        assert target.read_text() == "after"


class TestRegistryPathAliases:
    def test_registry_write_file_uses_path_alias(self, tmp_path):
        registry = create_registry(str(tmp_path))
        handler = registry.get_tool("write_file").handler
        result = handler(path=str(tmp_path / "reg.txt"), content="via registry")
        assert result.success is True
        assert (tmp_path / "reg.txt").read_text() == "via registry"


class TestPersistentShell:
    def test_cd_persists_between_calls(self, tmp_path):
        set_session_cwd(tmp_path)
        first = run_bash(BashInput(command="cd /tmp && pwd"), str(tmp_path))
        assert first.exit_code == 0
        assert first.output.strip() == "/tmp"
        assert str(session_cwd()) == resolve_path("/tmp")
        second = run_bash(BashInput(command="pwd"))
        assert second.output.strip() == "/tmp"

    def test_workdir_overrides_the_shell_directory(self, tmp_path):
        set_session_cwd("/")
        result = run_bash(BashInput(command="pwd", workdir=str(tmp_path)))
        assert result.output.strip() == os.path.realpath(str(tmp_path))

    def test_heredoc_and_quotes_survive(self, tmp_path):
        set_session_cwd(tmp_path)
        script = (
            "cat <<'EOF'\n"
            "ligne 'simple'\n"
            'ligne "double" et $HOME\n'
            "EOF\n"
        )
        result = run_bash(BashInput(command=script))
        assert result.success is True
        assert "ligne 'simple'" in result.output
        assert "ligne \"double\" et $HOME" in result.output

    def test_exit_code_is_always_reported(self, tmp_path):
        zero = run_bash(BashInput(command="true"), str(tmp_path))
        assert zero.exit_code == 0
        assert "exit code: 0" in zero.output

        failing = run_bash(BashInput(command="exit 7"), str(tmp_path))
        assert failing.exit_code == 7
        assert "exit code: 7" in failing.output

    def test_streamed_runs_keep_the_bash_output_clean(self, tmp_path):
        updates = list(run_bash_streamed("echo hello", str(tmp_path), timeout_ms=5000))
        partials = [u["partial"] for u in updates if "partial" in u]
        assert partials == ["hello"]
        assert updates[-1]["exit_code"] == 0


class TestFeedback:
    def test_empty_output_still_shows_the_exit_code(self):
        text = format_tool_feedback("bash", {"success": True, "exit_code": 0, "output": ""})
        assert "Exit code: 0" in text
        assert "no output captured" in text

    def test_stderr_and_signal_are_exposed(self):
        text = format_tool_feedback(
            "bash",
            {"success": False, "exit_code": -1, "output": "", "error": "boom", "signal": "timeout"},
        )
        assert "Signal: timeout" in text
        assert "boom" in text


class TestSubagentFallback:
    def test_main_provider_is_reused_without_a_dedicated_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from src.core.subagents import SubagentFleet

        fleet = SubagentFleet(
            {
                "provider": {"type": "nemapi", "model": "qwen-chat"},
                "nemapi": {"host": "127.0.0.1", "port": 8090},
            },
            str(tmp_path),
        )
        try:
            availability = fleet.availability()
            assert availability["available"] is True
            assert availability["mode"] == "main-provider"
            assert fleet._select_profile()["base_url"].endswith("/v1")
        finally:
            fleet.close()
