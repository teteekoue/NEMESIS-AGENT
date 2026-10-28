"""Auto-vérification des correctifs d'outils, exécutable sans provider.

`nemesis --check` rejoue les scénarios du rapport « Limites des outils et du
système » sur le code réellement chargé par l'interpréteur, puis affiche le
chemin des modules utilisés. Cela distingue immédiatement un correctif absent
d'un correctif présent mais non chargé (paquet installé obsolète, serveur
démarré avant la mise à jour, ancien environnement virtuel).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional

from .paths import reset_session_cwd, session_cwd, set_session_cwd


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _module_sources() -> List[tuple]:
    """Return (label, file) for the modules that hold the tool logic."""
    import tools
    import tools_schema

    from . import agent_tools, tool_bridge
    from .tools import apply_patch as apply_patch_module

    entries = [
        ("tools.py", tools.__file__),
        ("tools_schema.py", tools_schema.__file__),
        ("src/core/agent_tools.py", agent_tools.__file__),
        ("src/core/tool_bridge.py", tool_bridge.__file__),
        ("src/core/tools/apply_patch.py", apply_patch_module.__file__),
    ]
    return [(label, os.path.realpath(path or "")) for label, path in entries]


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("nemesis-cli")
    except Exception:  # noqa: BLE001 - metadata is optional
        return "inconnue"


def code_origin() -> tuple:
    """Return (kind, directory) for the loaded tool modules.

    ``kind`` is ``"depot"`` when the code comes from a working copy and
    ``"paquet"`` when it comes from an installed distribution, which is the
    usual reason a ``git pull`` seems to have no effect.
    """
    try:
        import tools

        directory = os.path.dirname(os.path.realpath(tools.__file__))
    except Exception:  # noqa: BLE001 - reported by the caller
        return "inconnu", ""

    # An installed copy is a copy outside any source tree (no pyproject.toml
    # and no .git above it). This also catches dist-packages and user installs.
    current = directory
    for _ in range(8):
        if os.path.isfile(os.path.join(current, "pyproject.toml")) or os.path.isdir(
            os.path.join(current, ".git")
        ):
            return "depot", directory
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return "paquet", directory


def module_report() -> str:
    """Multi-line description of the code currently loaded."""
    lines = [f"Version du paquet declaree : {_package_version()}"]
    for label, path in _module_sources():
        lines.append(f"{label:28} {path}")
    try:
        import tools

        location = os.path.dirname(os.path.realpath(tools.__file__))
        kind, directory = code_origin()
        if kind == "paquet":
            lines.append(
                "ATTENTION : les modules charges viennent d'un paquet installe "
                f"({directory}), pas du depot. `git pull` ne met PAS a jour cette copie : "
                "reinstallez le paquet (pip install .) ou lancez le CLI depuis le depot."
            )
        else:
            lines.append(f"Source : depot ({directory}).")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _last(gen) -> dict:
    result = None
    for update in gen:
        if "partial" not in update:
            result = update
    return result or {}


def _check_write_file_path(root: str) -> CheckResult:
    target = os.path.join(root, "via_path.txt")
    result = _last(_bridge().execute_tool("write_file", {"path": target, "content": "ok\n"}))
    ok = bool(result.get("success")) and os.path.isfile(target)
    return CheckResult("write_file accepte `path`", ok, str(result.get("stdout") or result.get("error"))[:200])


def _check_write_file_without_path(root: str) -> CheckResult:
    before = set(os.listdir(root))
    result = _last(_bridge().execute_tool("write_file", {"content": "orphelin"}))
    created = set(os.listdir(root)) - before
    ok = not result.get("success") and not created
    return CheckResult(
        "write_file sans chemin refuse proprement",
        ok,
        str(result.get("stdout") or result.get("error"))[:200],
    )


def _check_persistent_cd(root: str) -> CheckResult:
    sub = os.path.join(root, "sub")
    os.makedirs(sub, exist_ok=True)
    bridge = _bridge()
    _last(bridge.execute_tool("bash", {"command": f"cd {sub} && pwd"}))
    after = _last(bridge.execute_tool("bash", {"command": "pwd"}))
    ok = os.path.realpath(str(session_cwd())) == os.path.realpath(sub) and str(sub) in str(after.get("stdout"))
    return CheckResult("cd persistant entre deux appels bash", ok, str(after.get("stdout") or after.get("error")).strip()[:200])


def _check_apply_patch_absolute(root: str) -> CheckResult:
    target = os.path.join(root, "patch_abs.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("avant\n")
    patch = f"--- {target}\n+++ {target}\n@@ -1 +1 @@\n-avant\n+apres\n"
    result = _last(_bridge().execute_tool("apply_patch", {"patch": patch}))
    content = open(target, encoding="utf-8").read() if os.path.isfile(target) else ""
    ok = bool(result.get("success")) and content == "apres\n"
    return CheckResult("apply_patch avec chemin absolu", ok, str(result.get("stdout") or result.get("error"))[:200])


def _check_apply_patch_workdir(root: str) -> CheckResult:
    project = os.path.join(root, "projet")
    os.makedirs(project, exist_ok=True)
    target = os.path.join(project, "rel.txt")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("un\n")
    patch = "--- a/rel.txt\n+++ b/rel.txt\n@@ -1 +1 @@\n-un\n+deux\n"
    result = _last(_bridge().execute_tool("apply_patch", {"patch": patch, "workdir": project}))
    content = open(target, encoding="utf-8").read() if os.path.isfile(target) else ""
    ok = bool(result.get("success")) and content == "deux\n"
    return CheckResult("apply_patch avec `workdir`", ok, str(result.get("stdout") or result.get("error"))[:200])


def _check_write_directory_plus_filename(root: str) -> CheckResult:
    directory = os.path.join(root, "projet")
    os.makedirs(directory, exist_ok=True)
    result = _last(
        _bridge().execute_tool(
            "write_file",
            {"path": directory, "filename": "alias.txt", "content": "ok\n"},
        )
    )
    target = os.path.join(directory, "alias.txt")
    ok = bool(result.get("success")) and os.path.isfile(target)
    return CheckResult(
        "write_file joint `path` (dossier) et `filename`",
        ok,
        str(result.get("stdout") or result.get("error"))[:200],
    )


def _check_cd_survives_keyword_commands(root: str) -> CheckResult:
    bridge = _bridge()
    target = os.path.join(root, "cible")
    os.makedirs(target, exist_ok=True)
    # « node_modules » dans un commentaire suffisait à basculer sur l'ancien
    # chemin PTY, qui repartait du répertoire par défaut et perdait le cd.
    _last(bridge.execute_tool("bash", {"command": f"cd {target} && pwd # node_modules"}))
    after = _last(bridge.execute_tool("bash", {"command": "pwd"}))
    ok = os.path.realpath(str(session_cwd())) == os.path.realpath(target) and os.path.realpath(
        str(after.get("stdout", "")).strip()
    ) == os.path.realpath(target)
    return CheckResult(
        "cd conservé après une commande contenant `node`",
        ok,
        str(after.get("stdout") or after.get("error")).strip()[:200],
    )


def _check_patch_from_another_directory(root: str) -> CheckResult:
    project = os.path.join(root, "depot")
    os.makedirs(os.path.join(project, ".git"), exist_ok=True)
    source = os.path.join(project, "src", "app.py")
    os.makedirs(os.path.dirname(source), exist_ok=True)
    with open(source, "w", encoding="utf-8") as handle:
        handle.write("v1\n")
    nested = os.path.join(project, "src", "deep", "nested")
    os.makedirs(nested, exist_ok=True)
    set_session_cwd(nested)
    patch = "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-v1\n+v2\n"
    result = _last(_bridge().execute_tool("apply_patch", {"patch": patch}))
    content = open(source, encoding="utf-8").read() if os.path.isfile(source) else ""
    ok = bool(result.get("success")) and content == "v2\n"
    return CheckResult(
        "apply_patch trouve le fichier hors du répertoire courant",
        ok,
        str(result.get("stdout") or result.get("error"))[:200],
    )


def _check_exit_code(root: str) -> CheckResult:
    result = _last(_bridge().execute_tool("bash", {"command": "echo souci >&2; exit 3"}))
    ok = result.get("exit_code") == 3 and "souci" in str(result.get("stdout") or result.get("output"))
    return CheckResult("exit code et stderr remontes", ok, f"exit={result.get('exit_code')} sortie={result.get('stdout')!r}"[:200])


def _check_append_chunks(root: str) -> CheckResult:
    target = os.path.join(root, "long.txt")
    bridge = _bridge()
    first = _last(bridge.execute_tool("write_file", {"path": target, "content": "AAA"}))
    second = _last(bridge.execute_tool("append", {"path": target, "content": "BBB"}))
    third = _last(bridge.execute_tool("append", {"file_path": target, "content": "CCC"}))
    content = open(target, encoding="utf-8").read() if os.path.isfile(target) else ""
    ok = (
        all(r.get("success") for r in (first, second, third))
        and content == "AAABBBCCC"
        and third.get("total_bytes") == 9
    )
    return CheckResult(
        "write_file + append écrivent un fichier en plusieurs blocs",
        ok,
        f"contenu={content!r} total_bytes={third.get('total_bytes')}",
    )


def _check_oversized_call_rejected(root: str) -> CheckResult:
    target = os.path.join(root, "trop_long.txt")
    from .system_prompt import TOOL_CALL_HARD_LIMIT

    result = _last(
        _bridge().execute_tool(
            "write_file",
            {"path": target, "content": "x" * (TOOL_CALL_HARD_LIMIT + 500)},
        )
    )
    ok = not result.get("success") and not os.path.exists(target)
    return CheckResult(
        "un appel d'outil surdimensionné est refusé sans rien écrire",
        ok,
        str(result.get("stdout") or result.get("error"))[:200],
    )


def _check_strict_protocol(root: str) -> CheckResult:
    from action_parser import ActionParser

    parser = ActionParser(strict=True)
    truncated = '```json\n{"tool": "write_file", "parameters": {"path": "a.txt", "content": "coupe'
    bare_truncated = '{"tool": "write_file", "parameters": {"path": "a.txt", "content": "coupe'
    dsml = '<|DSML|>{"tool": "read_file", "parameters": {"path": "a.txt"}}'
    valid = '```json\n{"tool": "read_file", "parameters": {"path": "a.txt"}}\n```'
    p_trunc = parser.parse(truncated)
    p_bare = parser.parse(bare_truncated)
    p_dsml = parser.parse(dsml)
    p_valid = parser.parse(valid)
    ok = (
        bool(p_trunc.get("protocol_error"))
        and not p_trunc.get("actions")
        and bool(p_bare.get("protocol_error"))
        and not p_bare.get("actions")
        and bool(p_dsml.get("protocol_error"))
        and not p_dsml.get("actions")
        and not p_valid.get("protocol_error")
        and len(p_valid.get("actions") or []) == 1
    )
    return CheckResult(
        "DSML et JSON tronqué rejetés, JSON valide accepté",
        ok,
        f"tronque={bool(p_trunc.get('protocol_error'))} sans_fence={bool(p_bare.get('protocol_error'))} "
        f"dsml={bool(p_dsml.get('protocol_error'))} valide={len(p_valid.get('actions') or [])}",
    )


def _bridge():
    from .tool_bridge import ToolBridge

    return ToolBridge(workspace=None, config={"security": {"workspace": ""}})


def run_selfcheck() -> List[CheckResult]:
    """Run every fix scenario in a temporary directory."""
    original = session_cwd()
    root = tempfile.mkdtemp(prefix="nemesis-check-")
    set_session_cwd(root)
    checks: List[tuple] = [
        ("write_file accepte `path`", _check_write_file_path),
        ("write_file sans chemin refuse proprement", _check_write_file_without_path),
        ("cd persistant entre deux appels bash", _check_persistent_cd),
        ("write_file joint `path` (dossier) et `filename`", _check_write_directory_plus_filename),
        ("cd conservé après une commande contenant `node`", _check_cd_survives_keyword_commands),
        ("apply_patch avec chemin absolu", _check_apply_patch_absolute),
        ("apply_patch avec `workdir`", _check_apply_patch_workdir),
        ("apply_patch trouve le fichier hors du répertoire courant", _check_patch_from_another_directory),
        ("exit code et stderr remontes", _check_exit_code),
        ("write_file + append en plusieurs blocs", _check_append_chunks),
        ("appel surdimensionné refusé", _check_oversized_call_rejected),
        ("protocole JSON strict", _check_strict_protocol),
    ]
    results: List[CheckResult] = []
    try:
        for name, fn in checks:
            try:
                results.append(_guard(name, fn, root))
            except Exception as exc:  # noqa: BLE001 - reported as a failure
                results.append(CheckResult(name, False, f"exception: {exc}"))
    finally:
        set_session_cwd(original)
        shutil.rmtree(root, ignore_errors=True)
    return results


def _guard(name: str, fn: Callable[[str], CheckResult], root: str) -> CheckResult:
    return fn(root)
