"""Central path resolution for NEMESIS CLI (install dir vs user config)."""

from __future__ import annotations

import os
import shutil
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

DEFAULT_WORKSPACE = Path(
    os.environ.get("NEMESIS_WORKSPACE", Path.home() / "nemesis-workspace")
).expanduser()

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
    _DIRS_ENSURED = True
    return USER_CONFIG_DIR


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
    """Base directory used to resolve relative paths across the whole system.

    Relative paths are interpreted from the process working directory so the
    agent can reach any file on the machine instead of being confined to the
    configured workspace.
    """
    try:
        return Path(os.getcwd())
    except OSError:
        return Path.home()


def resolve_path(path_str, base=None) -> str:
    """Resolve a path anywhere on the system.

    - ``~`` and ``$VARIABLES`` are expanded.
    - Absolute paths are returned (realpath applied when possible).
    - Relative paths resolve against ``base`` (default: current working
      directory) and are never confined to a project workspace.
    """
    raw = os.path.expandvars(str(path_str if path_str is not None else "")).strip()
    if raw.startswith("~"):
        raw = os.path.expanduser(raw)
    p = Path(raw)
    if not p.is_absolute():
        base_dir = Path(base) if base is not None else system_base_dir()
        if base_dir.is_file():
            base_dir = base_dir.parent
        p = base_dir / p
    try:
        return os.path.realpath(str(p))
    except OSError:
        return os.path.normpath(str(p))


def resolve_existing_path(path_str, base=None, fallback_base=None) -> str:
    """Resolve a path, preferring an existing location anywhere on the system.

    The system-resolved location is tried first. If it does not exist and a
    ``fallback_base`` is provided (typically the configured workspace), the
    path is also tried relative to that base so legacy workspace-relative
    paths keep working.
    """
    resolved = resolve_path(path_str, base)
    if os.path.exists(resolved):
        return resolved
    if fallback_base is not None:
        alt = resolve_path(path_str, fallback_base)
        if os.path.exists(alt):
            return alt
    return resolved


def resolve_workspace(config: Optional[dict] = None) -> Path:
    """Resolve workspace directory from config or defaults; ensure it exists."""
    ws = None
    if isinstance(config, dict):
        sec = config.get("security") or {}
        if isinstance(sec, dict) and sec.get("workspace"):
            ws = Path(str(sec["workspace"])).expanduser()
    if ws is None:
        ws = DEFAULT_WORKSPACE
    if not ws.is_absolute():
        # Relative paths are relative to HOME, not install dir
        ws = (Path.home() / ws).resolve()
    try:
        ws.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return ws
