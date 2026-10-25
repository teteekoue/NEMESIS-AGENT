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


def t_path(path):
    """Retourne le chemin sous forme de chaîne (lisibilité des tests)."""
    return str(path)


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


class TestPathAliasResolution:
    """§1 — `path` doit se comporter exactement comme `file_path`."""

    def test_directory_path_plus_filename_is_joined(self, tmp_path):
        (tmp_path / "projet").mkdir()
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        set_session_cwd(tmp_path)
        result = None
        for chunk in bridge.execute_tool(
            "write_file",
            {"path": str(tmp_path / "projet"), "filename": "app.py", "content": "x = 1"},
        ):
            if "partial" not in chunk:
                result = chunk
        assert result["success"] is True, result
        assert (tmp_path / "projet" / "app.py").read_text() == "x = 1"

    def test_relative_directory_plus_filename(self, tmp_path):
        (tmp_path / "src").mkdir()
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        set_session_cwd(tmp_path)
        for chunk in bridge.execute_tool(
            "write_file", {"path": "src", "filename": "main.py", "content": "y"}
        ):
            if "partial" not in chunk:
                assert chunk["success"] is True, chunk
        assert (tmp_path / "src" / "main.py").read_text() == "y"

    def test_existing_file_wins_over_directory(self, tmp_path):
        target = tmp_path / "reel.txt"
        target.write_text("avant")
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        set_session_cwd(tmp_path)
        for chunk in bridge.execute_tool(
            "write_file",
            {"path": str(tmp_path), "file_path": str(target), "content": "apres"},
        ):
            if "partial" not in chunk:
                assert chunk["success"] is True, chunk
        assert target.read_text() == "apres"

    def test_lone_directory_still_reports_an_error(self, tmp_path):
        (tmp_path / "dossier").mkdir()
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        set_session_cwd(tmp_path)
        for chunk in bridge.execute_tool("write_file", {"path": str(tmp_path / "dossier"), "content": "x"}):
            if "partial" not in chunk:
                assert chunk["success"] is False
                assert "directory" in str(chunk.get("error", "")).lower()

    def test_resolver_prefers_files_then_absolute_paths(self, tmp_path):
        from src.core.paths import resolve_target_path

        (tmp_path / "d").mkdir()
        (tmp_path / "f.txt").write_text("x")
        assert resolve_target_path(str(tmp_path / "d"), str(tmp_path / "f.txt")).endswith("f.txt")
        assert resolve_target_path(str(tmp_path / "d"), str(tmp_path / "neuf.txt")).endswith("neuf.txt")
        assert resolve_target_path(str(tmp_path / "d"), "app.py").endswith("d/app.py")
        assert resolve_target_path(str(tmp_path / "d")).endswith("/d")


class TestInteractiveShellPath:
    """§2 — une commande non interactive ne doit jamais perdre le `cd`."""

    @pytest.mark.parametrize(
        "command",
        ["cd {d} && pwd", "cd {d} && pwd # node_modules", "cd {d} && ls | wc -l"],
    )
    def test_non_interactive_commands_keep_the_directory(self, tmp_path, command):
        set_session_cwd(tmp_path)
        target_dir = tmp_path / "cible"
        target_dir.mkdir()
        executor = ActionExecutor(workspace=str(t_path(tmp_path)))
        _last(executor.execute_tool("bash", {"command": command.format(d=target_dir)}))
        assert session_cwd() == target_dir.resolve()
        follow_up = _last(executor.execute_tool("bash", {"command": "pwd"}))
        assert follow_up["stdout"].strip() == str(target_dir.resolve())

    def test_word_detection_only_matches_the_program(self):
        from tools import _looks_interactive

        assert _looks_interactive("cd /tmp && pwd # node") is False
        assert _looks_interactive("cat node_modules/app.js") is False
        assert _looks_interactive("ls | head") is False
        assert _looks_interactive("cd /tmp && echo hi > out.txt") is False
        assert _looks_interactive("ssh serveur") is True
        assert _looks_interactive("sudo mysql -u root") is True
        assert _looks_interactive("git log | less") is True

    def test_pty_path_reports_the_exit_code(self, tmp_path):
        set_session_cwd(tmp_path)
        executor = ActionExecutor(workspace=str(t_path(tmp_path)))
        result = _last(
            executor.execute_tool(
                "bash", {"command": "ssh -o BatchMode=yes -o ConnectTimeout=1 localhost true"}
            )
        )
        assert result.get("exit_code") is not None
        assert result["stdout"].strip(), "la sortie ne doit jamais être vide"


class TestPatchResolutionBases:
    """§3 — apply_patch doit trouver le fichier hors du répertoire du shell."""

    def test_project_root_is_found_from_a_nested_shell_directory(self, tmp_path):
        (tmp_path / ".git").mkdir()
        target = tmp_path / "src" / "app.py"
        target.parent.mkdir()
        target.write_text("v1\n")
        nested = tmp_path / "src" / "deep" / "nested"
        nested.mkdir(parents=True)
        set_session_cwd(nested)
        patch = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-v1\n+v2\n"
        result = apply_patch(patch)
        assert result.success is True, result.message
        assert target.read_text() == "v2\n"

    def test_shell_inside_the_file_directory(self, tmp_path):
        target = tmp_path / "app.py"
        target.write_text("a\n")
        set_session_cwd(tmp_path)
        result = apply_patch("--- app.py\n+++ app.py\n@@ -1 +1 @@\n-a\n+b\n")
        assert result.success is True, result.message
        assert target.read_text() == "b\n"

    def test_missing_file_lists_the_tried_directories(self, tmp_path):
        set_session_cwd(tmp_path)
        result = apply_patch("--- a/absent.txt\n+++ b/absent.txt\n@@ -1 +1 @@\n-a\n+b\n")
        assert result.success is False
        assert "répertoires essayés" in result.message.lower() or "repertoires essayes" in result.message.lower()


class TestPatchAncestorResolution:
    """§3 (suite) — un shell garé loin du fichier ne doit plus bloquer le patch."""

    def test_ancestors_of_the_shell_directory_are_tried(self, tmp_path):
        target = tmp_path / "app.py"
        target.write_text("v1\n")
        nested = tmp_path / "src" / "deep" / "nested"
        nested.mkdir(parents=True)
        set_session_cwd(nested)
        result = apply_patch("--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-v1\n+v2\n")
        assert result.success is True, result.message
        assert target.read_text() == "v2\n"

    def test_closest_directory_wins(self, tmp_path):
        root_file = tmp_path / "notes.txt"
        root_file.write_text("racine\n")
        inner = tmp_path / "sous"
        inner.mkdir()
        inner_file = inner / "notes.txt"
        inner_file.write_text("interne\n")
        set_session_cwd(inner)
        result = apply_patch("--- a/notes.txt\n+++ b/notes.txt\n@@ -1 +1 @@\n-interne\n+modifie\n")
        assert result.success is True, result.message
        assert inner_file.read_text() == "modifie\n"
        assert root_file.read_text() == "racine\n"

    def test_write_file_creates_a_missing_directory_alias(self, tmp_path):
        set_session_cwd(tmp_path)
        bridge = ToolBridge(workspace=str(tmp_path), config={"security": {"workspace": ""}})
        target = tmp_path / "nouveau" / "sous"
        for chunk in bridge.execute_tool(
            "write_file", {"path": str(target), "filename": "f.txt", "content": "z"}
        ):
            if "partial" not in chunk:
                assert chunk["success"] is True, chunk
        assert (target / "f.txt").read_text() == "z"
        assert not os.path.exists(str(target) + "/f.txt/f.txt")
