"""Central path resolution for NEMESIS CLI (install dir vs user config)."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Optional

import sys

# Package / install root (directory containing agent.py or sys._MEIPASS when frozen)
if getattr(sys, "frozen", False):
    INSTALL_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
else:
    INSTALL_DIR = Path(__file__).resolve().parent.parent.parent

# User-writable config & state
USER_CONFIG_DIR = Path(
    os.environ.get("NEMESIS_CONFIG_DIR", Path.home() / ".config" / "nemesis-cli")
).expanduser()

# Kept for backward compatibility only. NEMESIS is no longer confined to a
# workspace: the default base directory is the shell's current directory.
DEFAULT_WORKSPACE = Path(
    os.environ.get("NEMESIS_WORKSPACE") or Path.cwd()
).expanduser()

_SESSION_CWD: Optional[Path] = None
_SESSION_CWD_LOCK = threading.Lock()

# Filenames
CONFIG_NAME = "config.yaml"
MCP_CONFIG_NAME = "mcp_config.yaml"
AGENTS_NAME = "agents.json"
PROMPT_NAME = "prompt_system.txt"
TOOLS_LIBRARY_NAME = "tools_library"


def _find_resource(name: str) -> Optional[Path]:
    for base in (
        INSTALL_DIR,
        Path(sys.prefix),
        Path(sys.prefix) / "data",
        Path.cwd(),
    ):
        cand = base / name
        if cand.exists():
            return cand
    return None


_DIRS_ENSURED = False


def ensure_user_dirs() -> Path:
    """Create user config directory and seed defaults from install if missing.

    Runs at most once per process: the directory tree and seed files do not
    change during a session, so repeated stat/scan calls are pure overhead on
    hot paths such as config serialization.
    """
    global _DIRS_ENSURED
    if _DIRS_ENSURED and USER_CONFIG_DIR.exists():
        return USER_CONFIG_DIR
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _seed_if_missing(CONFIG_NAME)
    _seed_if_missing(MCP_CONFIG_NAME)
    # tools_library: seed empty structure or copy bundled skills
    user_lib = USER_CONFIG_DIR / TOOLS_LIBRARY_NAME
    install_lib = _find_resource(TOOLS_LIBRARY_NAME)
    if not user_lib.exists() and install_lib and install_lib.is_dir():
        try:
            shutil.copytree(install_lib, user_lib)
        except OSError:
            user_lib.mkdir(parents=True, exist_ok=True)
    elif not user_lib.exists():
        user_lib.mkdir(parents=True, exist_ok=True)
    if install_lib and install_lib.is_dir():
        _seed_bundled_skills(install_lib, user_lib)
    _DIRS_ENSURED = True
    return USER_CONFIG_DIR


def _seed_bundled_skills(source: Path, destination: Path) -> None:
    """Keep package-provided builtin skills in sync with the user library.

    A user-customised skill (one whose frontmatter no longer carries
    ``nemesis_builtin: true``) is never overwritten. Bundled skills that still
    carry the marker are refreshed when their SKILL.md differs, so restoring an
    authentic upstream version also replaces older rewritten copies.
    """
    try:
        for skill_dir in source.iterdir():
            skill_file = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_file.is_file():
                continue
            try:
                content = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter = content.split("---", 2)
            if len(frontmatter) != 3 or "nemesis_builtin: true" not in frontmatter[1]:
                continue
            target = destination / skill_dir.name
            if not target.exists():
                shutil.copytree(skill_dir, target)
                continue
            installed = target / "SKILL.md"
            try:
                installed_content = installed.read_text(encoding="utf-8")
            except OSError:
                continue
            installed_frontmatter = installed_content.split("---", 2)
            if (
                len(installed_frontmatter) != 3
                or "nemesis_builtin: true" not in installed_frontmatter[1]
                or installed_content == content
            ):
                continue
            shutil.copytree(skill_dir, target, dirs_exist_ok=True)
    except OSError:
        pass


def _seed_if_missing(name: str) -> None:
    dest = USER_CONFIG_DIR / name
    if dest.exists():
        return
    src = _find_resource(name)
    if src and src.is_file():
        try:
            shutil.copy2(src, dest)
        except OSError:
            pass


def config_path() -> Path:
    ensure_user_dirs()
    return USER_CONFIG_DIR / CONFIG_NAME


def mcp_config_path() -> Path:
    ensure_user_dirs()
    user = USER_CONFIG_DIR / MCP_CONFIG_NAME
    if user.exists():
        return user
    install = _find_resource(MCP_CONFIG_NAME)
    return install if install and install.exists() else user


def github_path() -> Path:
    """Shared GitHub connection settings (web + CLI + Telegram)."""
    ensure_user_dirs()
    return USER_CONFIG_DIR / "github.yaml"


def agents_path() -> Path:
    ensure_user_dirs()
    return USER_CONFIG_DIR / AGENTS_NAME


def prompt_system_path() -> Path:
    """Prefer install prompt (read-only package data), fallback to cwd/user."""
    for candidate in (
        _find_resource(PROMPT_NAME),
        USER_CONFIG_DIR / PROMPT_NAME,
        Path.cwd() / PROMPT_NAME,
    ):
        if candidate and candidate.is_file():
            return candidate
    return INSTALL_DIR / PROMPT_NAME


def tools_library_path() -> Path:
    ensure_user_dirs()
    return USER_CONFIG_DIR / TOOLS_LIBRARY_NAME


def system_base_dir() -> Path:
    """Current base directory used to resolve every relative path.

    NEMESIS has no sandbox and no fixed workspace: the agent works anywhere on
    the machine. Relative paths resolve against the live shell directory, which
    follows ``cd`` from one bash call to the next.
    """
    return session_cwd()


def session_cwd() -> Path:
    """Return the shared shell directory for this process (never sandboxed)."""
    global _SESSION_CWD
    with _SESSION_CWD_LOCK:
        if _SESSION_CWD is None:
            try:
                _SESSION_CWD = Path(os.getcwd())
            except OSError:
                _SESSION_CWD = Path.home()
        return _SESSION_CWD


def set_session_cwd(path) -> Path:
    """Update the shared shell directory after a command changed directory."""
    global _SESSION_CWD
    raw = str(path if path is not None else "").strip()
    candidate = Path(os.path.expandvars(os.path.expanduser(raw))) if raw else Path.home()
    if not candidate.is_absolute():
        candidate = session_cwd() / candidate
    try:
        candidate = Path(os.path.realpath(str(candidate)))
    except OSError:
        candidate = Path(os.path.normpath(str(candidate)))
    with _SESSION_CWD_LOCK:
        _SESSION_CWD = candidate
    return candidate


def reset_session_cwd() -> Path:
    """Reset the shared shell directory to the process working directory."""
    global _SESSION_CWD
    try:
        cwd = Path(os.getcwd())
    except OSError:
        cwd = Path.home()
    with _SESSION_CWD_LOCK:
        _SESSION_CWD = cwd
    return cwd


def resolve_path(path_str, base=None) -> str:
    """Resolve a path anywhere on the system.

    - ``~`` and ``$VARIABLES`` are expanded.
    - Absolute paths are returned (realpath applied when possible).
    - Relative paths resolve against ``base`` (default: the live shell
      directory) and are never confined to a project workspace.
    """
    raw = os.path.expandvars(str(path_str if path_str is not None else "")).strip()
    if raw.startswith("~"):
        raw = os.path.expanduser(raw)
    p = Path(raw) if raw else Path(".")
    if not p.is_absolute():
        base_dir = Path(base) if base is not None else system_base_dir()
        if base_dir.is_file():
            base_dir = base_dir.parent
        p = base_dir / p
    try:
        return os.path.realpath(str(p))
    except OSError:
        return os.path.normpath(str(p))


# Noms d'arguments que le modèle utilise pour désigner le fichier cible.
PATH_ALIASES = ("file_path", "path", "filename", "file", "target_file")


def resolve_target_path(*candidates, base=None) -> str:
    """Resolve the single file targeted by several path aliases.

    The model sometimes sends ``path`` pointing to a *directory* together with
    ``filename`` holding the file name, or an absolute path in one alias and a
    relative one in another. Rules, applied in this order:

    1. first candidate is an **existing directory** and another candidate is a
       bare file name → join them (``path=/projet`` + ``filename=app.py``);
    2. an existing **file** among the candidates;
    3. a candidate that looks like a file (it has a suffix) and is not a
       directory;
    4. an existing directory alone → returned unchanged, so the caller reports
       "path is a directory" instead of writing into a default folder;
    5. first candidate without a suffix plus a bare file name → join them, so a
       directory that does not exist yet is still created as a directory.

    Relative candidates resolve from ``base`` (default: the live shell
    directory), never from the interpreter working directory.

    Returns ``""`` when no candidate holds a usable path.
    """
    cleaned = []
    for candidate in candidates:
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            cleaned.append(text)
    if not cleaned:
        return ""

    expanded = [os.path.expandvars(os.path.expanduser(text)) for text in cleaned]
    resolved = [resolve_path(text, base) for text in expanded]

    def _simple_name(candidate: str) -> bool:
        return (
            os.sep not in candidate
            and "/" not in candidate
            and candidate not in (".", "..")
        )

    # 1) explicit directory + bare file name
    first = expanded[0]
    if os.path.isdir(resolve_path(first, base)):
        for index, other in enumerate(expanded[1:], start=1):
            if os.path.isabs(other) or not _simple_name(other):
                continue
            return resolve_path(os.path.join(resolved[0], other), base)

    # 2) an existing file wins
    for text, path in zip(expanded, resolved):
        if os.path.isfile(path):
            return path

    # 3) a candidate that looks like a file (suffix, not a directory). A bare
    #    name ("app.py") is skipped when another alias may be its directory.
    for text, path in zip(expanded, resolved):
        if len(expanded) >= 2 and _simple_name(text):
            continue
        name = os.path.basename(path)
        if "." in name[1:] and not os.path.isdir(path):
            return path

    # 4) an existing directory alone
    for path in resolved:
        if os.path.isdir(path):
            return path

    # 5) a directory that does not exist yet + a bare file name
    if len(expanded) >= 2 and "." not in os.path.basename(resolved[0])[1:]:
        for other in expanded[1:]:
            if os.path.isabs(other) or not _simple_name(other):
                continue
            return resolve_path(os.path.join(resolved[0], other), base)

    return resolved[0]


def resolve_existing_path(path_str, base=None, fallback_base=None) -> str:
    """Resolve a path anywhere on the system.

    ``fallback_base`` is accepted for backward compatibility only: NEMESIS no
    longer redirects unknown paths into a workspace directory.
    """
    return resolve_path(path_str, base)


def resolve_workspace(config: Optional[dict] = None) -> Path:
    """Return the starting directory of the agent.

    An explicit ``security.workspace`` is still honoured as a *starting point*
    for relative paths, but nothing is confined to it. Without configuration
    the live shell directory is used and no directory is created.
    """
    ws = None
    if isinstance(config, dict):
        sec = config.get("security") or {}
        if isinstance(sec, dict) and sec.get("workspace"):
            ws = str(sec["workspace"]).strip()
    if not ws:
        return session_cwd()
    expanded = Path(os.path.expandvars(os.path.expanduser(ws)))
    if not expanded.is_absolute():
        expanded = session_cwd() / expanded
    candidate = Path(os.path.normpath(str(expanded)))
    if not candidate.is_dir():
        # A stale "workspace" from an older install must never block the agent:
        # fall back to the live shell directory.
        return session_cwd()
    return candidate
