"""GitHub connection settings shared by the web UI, CLI and Telegram bot.

The credentials are stored in the user config directory (``github.yaml``).
Saving them:
  * registers a ``github`` MCP server (``@modelcontextprotocol/server-github``)
    with the token injected through ``GITHUB_PERSONAL_ACCESS_TOKEN`` so the
    agent gets dedicated GitHub tools without polluting the core tool registry;
  * remembers the git identity (name/email) so commits made by the agent are
    attributed correctly.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .paths import github_path, mcp_config_path

_LOCK = threading.RLock()

GITHUB_MCP_NAME = "github"
GITHUB_MCP_COMMAND = "npx"
GITHUB_MCP_ARGS = ["-y", "@modelcontextprotocol/server-github"]


def load_github_config() -> Dict[str, Any]:
    with _LOCK:
        path = github_path()
        if not path.exists():
            return {"name": "", "email": "", "token": "", "login": ""}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        return {
            "name": str(data.get("name", "") or ""),
            "email": str(data.get("email", "") or ""),
            "token": str(data.get("token", "") or ""),
            "login": str(data.get("login", "") or ""),
        }


def _token_preview(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return token[:2] + "..."
    return f"{token[:4]}...{token[-4:]}"


def public_github_config() -> Dict[str, Any]:
    cfg = load_github_config()
    return {
        "connected": bool(cfg.get("token")),
        "token_set": bool(cfg.get("token")),
        "token_preview": _token_preview(cfg.get("token", "")),
        "name": cfg.get("name", ""),
        "email": cfg.get("email", ""),
        "login": cfg.get("login", ""),
        "mcp_server": GITHUB_MCP_NAME,
        "mcp_command": f"{GITHUB_MCP_COMMAND} {' '.join(GITHUB_MCP_ARGS)}",
    }


def _write_github_file(cfg: Dict[str, Any]) -> None:
    path = github_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _sync_mcp_server(token: str, name: str = "", email: str = "") -> None:
    """Create/update or remove the ``github`` MCP server entry."""
    from .mcp_manager import MCPManager

    mgr = MCPManager(str(mcp_config_path()))
    if not token:
        if GITHUB_MCP_NAME in mgr.list_servers():
            mgr.remove_server(GITHUB_MCP_NAME)
        return
    env = {"GITHUB_PERSONAL_ACCESS_TOKEN": token}
    if name:
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_COMMITTER_NAME"] = name
    if email:
        env["GIT_AUTHOR_EMAIL"] = email
        env["GIT_COMMITTER_EMAIL"] = email
    mgr.add_server(
        GITHUB_MCP_NAME,
        GITHUB_MCP_COMMAND,
        args=list(GITHUB_MCP_ARGS),
        env=env,
        description="GitHub MCP server (issues, PRs, repos, code search).",
    )


def save_github_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge and persist GitHub settings, then sync the MCP server entry."""
    with _LOCK:
        current = load_github_config()
        for key in ("name", "email", "login"):
            if patch.get(key) is not None:
                current[key] = str(patch.get(key) or "").strip()
        # Empty token explicitly disconnects; missing token keeps the current.
        if patch.get("token") is not None:
            current["token"] = str(patch.get("token") or "").strip()
        _write_github_file(current)
        try:
            _sync_mcp_server(
                current.get("token", ""),
                current.get("name", ""),
                current.get("email", ""),
            )
        except Exception:
            # Credentials are still persisted; MCP sync can be retried.
            pass
        return public_github_config()


def git_identity() -> Dict[str, str]:
    cfg = load_github_config()
    return {"name": cfg.get("name", ""), "email": cfg.get("email", "")}