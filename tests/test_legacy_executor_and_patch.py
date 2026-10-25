"""Régression du rapport NEMESIS : alias de chemin, apply_patch, shell persistant.

Chaque scénario reproduit une plainte précise du rapport « Limites des outils
et du système » (sections 7.2 A, B et C) pour qu'elle ne puisse plus revenir.
"""

import os

import pytest

from src.core.agent_tools import create_registry
from src.core.paths import reset_session_cwd, session_cwd, set_session_cwd
from src.core.tool_bridge import ToolBridge
from src.core.tools.apply_patch import apply_patch
from tools import ActionExecutor
from tools_schema import validate_tool_call


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


class TestWriteFilePathAlias:
    """§7.2 A — `write_file` doit respecter `path` comme `file_path`."""

    @pytest.mark.parametrize("key", ["path", "file_path", "filename", "file"])
    def test_legacy_executor_accepts_every_alias(self, tmp_path, key):
        set_session_cwd(tmp_path)
        executor = ActionExecutor(workspace=str(tmp_path))
        result = _last(executor.execute_tool("write_file", {key: "alias.txt", "content": "ok"}))
        assert result["success"] is True, result
        assert (tmp_path / "alias.txt").read_text() == "ok\n"

    def test_legacy_executor_never_writes_to_the_directory(self, tmp_path):
        set_session_cwd(tmp_path)
        executor = ActionExecutor(workspace=str(tmp_path))
        result = _last(executor.execute_tool("write_file", {"content": "orphelin"}))
        assert result["success"] is False
        assert "chemin" in result["stdout"].lower()
        assert os.listdir(tmp_path) == []

    def test_legacy_executor_reports_a_directory_target(self, tmp_path):
        set_session_cwd(tmp_path)
        (tmp_path / "dossier").mkdir()
        executor = ActionExecutor(workspace=str(tmp_path))
        result = _last(executor.execute_tool("write_file", {"path": "dossier", "content": "x"}))
        assert result["success"] is False
        assert "repertoire" in result["stdout"].lower()

    def test_registry_rejects_a_call_without_any_path(self):
        ok, error = validate_tool_call("write_file", {"content": "x"})
        assert ok is False
        assert "chemin" in error.lower()

    def test_bridge_write_path_alias(self, tmp_path):
        set_session_cwd(tmp_path)
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        result = None
        for chunk in bridge.execute_tool("write_file", {"path": "bridge.txt", "content": "via path"}):
            if "partial" not in chunk:
                result = chunk
        assert result["success"] is True, result
        assert (tmp_path / "bridge.txt").read_text() == "via path"

    def test_bridge_write_append_alias(self, tmp_path):
        set_session_cwd(tmp_path)
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        list(bridge.execute_tool("write_file", {"path": "a.txt", "content": "un\n"}))
        for chunk in bridge.execute_tool("append", {"file_path": "a.txt", "content": "deux\n"}):
            if "partial" not in chunk:
                assert chunk["success"] is True, chunk
        assert (tmp_path / "a.txt").read_text() == "un\ndeux\n"


class TestApplyPatch:
    """§7.2 C — apply_patch doit trouver les fichiers, même en chemin absolu."""

    def _write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_relative_checkout_from_a_subdirectory(self, tmp_path):
        target = tmp_path / "cible.txt"
        self._write(target, "un\ndeux\n")
        set_session_cwd(tmp_path / "sous" if (tmp_path / "sous").mkdir() is None else tmp_path)
        patch = "--- a/../cible.txt\n+++ b/../cible.txt\n@@ -1,2 +1,2 @@\n-un\n+UN\n deux\n"
        result = apply_patch(patch, workdir=str(tmp_path / "sous"))
        assert result.success is True, result.message
        assert target.read_text() == "UN\ndeux\n"

    def test_absolute_path_is_honored(self, tmp_path):
        target = tmp_path / "abs.txt"
        self._write(target, "avant\n")
        patch = f"--- {target}\n+++ {target}\n@@ -1 +1 @@\n-avant\n+apres\n"
        result = apply_patch(patch, workdir=str(tmp_path.parent))
        assert result.success is True, result.message
        assert target.read_text() == "apres\n"

    def test_multi_hunk_and_multi_file(self, tmp_path):
        first = tmp_path / "un.txt"
        self._write(first, "a\nb\nc\nd\ne\nf\n")
        patch = (
            f"--- {first}\n+++ {first}\n"
            "@@ -1,2 +1,2 @@\n-a\n+A\n b\n"
            "@@ -5,2 +5,2 @@\n e\n-f\n+F\n"
            f"--- /dev/null\n+++ {tmp_path / 'neuf.txt'}\n"
            "@@ -0,0 +1,1 @@\n+nouveau\n"
        )
        result = apply_patch(patch, workdir=str(tmp_path))
        assert result.success is True, result.message
        assert first.read_text() == "A\nb\nc\nd\ne\nF\n"
        assert (tmp_path / "neuf.txt").read_text() == "nouveau\n"

    def test_workdir_resolves_relative_headers(self, tmp_path):
        target = tmp_path / "projet" / "f.txt"
        self._write(target, "x\n")
        patch = "--- a/f.txt\n+++ b/f.txt\n@@ -1 +1 @@\n-x\n+y\n"
        result = apply_patch(patch, workdir=str(tmp_path / "projet"))
        assert result.success is True, result.message
        assert target.read_text() == "y\n"

    def test_missing_file_reports_a_clear_error(self, tmp_path):
        set_session_cwd(tmp_path)
        result = apply_patch("--- a/absent.txt\n+++ b/absent.txt\n@@ -1 +1 @@\n-a\n+b\n")
        assert result.success is False
        assert "introuvable" in result.message.lower()

    def test_dry_run_does_not_write(self, tmp_path):
        target = tmp_path / "sec.txt"
        self._write(target, "garde\n")
        patch = "--- a/sec.txt\n+++ b/sec.txt\n@@ -1 +1 @@\n-garde\n+change\n"
        assert apply_patch(patch, workdir=str(tmp_path), dry_run=True).success is True
        assert target.read_text() == "garde\n"

    def test_bridge_apply_patch_uses_the_shell_directory(self, tmp_path):
        target = tmp_path / "bridge.txt"
        self._write(target, "v1\n")
        set_session_cwd(tmp_path)
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        patch = "--- a/bridge.txt\n+++ b/bridge.txt\n@@ -1 +1 @@\n-v1\n+v2\n"
        result = None
        for chunk in bridge.execute_tool("apply_patch", {"patch": patch}):
            if "partial" not in chunk:
                result = chunk
        assert result["success"] is True, result
        assert target.read_text() == "v2\n"

    def test_legacy_executor_apply_patch(self, tmp_path):
        target = tmp_path / "legacy.txt"
        self._write(target, "un\n")
        set_session_cwd(tmp_path)
        executor = ActionExecutor(workspace=str(tmp_path))
        result = _last(executor.execute_tool(
            "apply_patch",
            {"patch": "--- a/legacy.txt\n+++ b/legacy.txt\n@@ -1 +1 @@\n-un\n+deux\n"},
        ))
        assert result["success"] is True, result
        assert target.read_text() == "deux\n"


class TestPersistentShell:
    """§7.2 B — le `cd` doit survivre à l'appel suivant."""

    def test_legacy_executor_keeps_the_directory(self, tmp_path):
        set_session_cwd(tmp_path)
        (tmp_path / "sub").mkdir()
        executor = ActionExecutor(workspace=str(tmp_path))
        _last(executor.execute_tool("bash", {"command": "cd sub && pwd"}))
        assert session_cwd() == (tmp_path / "sub").resolve()
        assert _last(executor.execute_tool("bash", {"command": "pwd"}))["stdout"].strip() == str(
            (tmp_path / "sub").resolve()
        )

    def test_explicit_workdir_wins_and_is_remembered(self, tmp_path):
        set_session_cwd(tmp_path)
        executor = ActionExecutor(workspace=str(tmp_path))
        assert _last(executor.execute_tool(
            "bash", {"command": "pwd", "workdir": str(tmp_path)}
        ))["stdout"].strip() == str(tmp_path.resolve())

    def test_exit_code_and_stderr_are_reported(self, tmp_path):
        set_session_cwd(tmp_path)
        executor = ActionExecutor(workspace=str(tmp_path))
        failed = _last(executor.execute_tool("bash", {"command": "false"}))
        assert failed["exit_code"] == 1
        assert "exit code: 1" in failed["stdout"]
        stderr_only = _last(executor.execute_tool(
            "bash", {"command": "echo probleme >&2 && exit 3"}
        ))
        assert stderr_only["exit_code"] == 3
        assert "probleme" in stderr_only["stdout"]


class TestRegistryPatchSchema:
    def test_workdir_is_accepted(self):
        registry = create_registry(None)
        ok, cleaned, error = registry.validate_call(
            "apply_patch", {"patch": "--- a/x\n+++ b/x\n", "workdir": "/tmp"}
        )
        assert ok is True, error
        assert cleaned["workdir"] == "/tmp"

    def test_diff_alias_is_accepted(self):
        registry = create_registry(None)
        ok, cleaned, error = registry.validate_call("apply_patch", {"diff": "--- a/x\n+++ b/x\n"})
        assert ok is True, error
        assert cleaned["diff"].startswith("--- a/x")
