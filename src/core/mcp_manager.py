"""MCP server registry backed by mcp_config.yaml."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml


class MCPManager:
    """Load / save MCP server definitions and spawn clients."""

    def __init__(self, config_file: str = "mcp_config.yaml"):
        self.config_file = Path(config_file)
        self.servers: Dict[str, Any] = self._load_config()
        self._pool: Dict[str, Any] = {}

    def reload(self) -> None:
        self.servers = self._load_config()

    def get_pooled_client(self, name: str, timeout: float = 30.0):
        """Return a long-lived, initialized client for the named server.

        Reuses a running session when available so MCP tools keep their state
        and avoid a process spawn per call. Falls back to a fresh client if the
        pooled one died. Caller must NOT close pooled clients.
        """
        self.reload()
        if name not in self.servers:
            return None
        client = self._pool.get(name)
        if client is not None:
            proc = getattr(client, "process", None)
            if proc is None or proc.poll() is None:
                return client
            self._pool.pop(name, None)
        from src.core.mcp_client import SimpleMCPClient

        server_cfg = self.servers[name]
        if not isinstance(server_cfg, dict):
            return None
        command, args = self._parse_command(server_cfg)
        env = server_cfg.get("env") or {}
        if env and not isinstance(env, dict):
            env = {}
        cwd = server_cfg.get("cwd")
        client = SimpleMCPClient(
            command,
            args,
            env={str(k): str(v) for k, v in env.items()},
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        client.initialize()
        self._pool[name] = client
        return client

    def close_pool(self) -> None:
        """Shut down every pooled client."""
        for client in list(self._pool.values()):
            try:
                client.close()
            except Exception:
                pass
        self._pool.clear()

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_file.exists():
            return {}
        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                return {}
            # Drop non-dict entries / reserved keys
            out: Dict[str, Any] = {}
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, dict) and v.get("command"):
                    out[k] = v
            return out
        except Exception:
            return {}

    def _save_config(self) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            yaml.dump(self.servers, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def add_server(
        self,
        name: str,
        command: str,
        env: Optional[Dict[str, str]] = None,
        description: str = "",
        args: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        name = (name or "").strip()
        command = (command or "").strip()
        if not name or not command:
            return False, "name and command are required"
        if any(c in name for c in " \t\n/\\"):
            return False, "invalid server name"
        entry: Dict[str, Any] = {"command": command}
        if args:
            entry["args"] = list(args)
        if env:
            entry["env"] = {str(k): str(v) for k, v in env.items()}
        if description:
            entry["description"] = description
        self.servers[name] = entry
        try:
            self._save_config()
        except Exception as e:
            return False, f"Failed to save config: {e}"
        return True, f"Serveur MCP '{name}' ajoute."

    def remove_server(self, name: str) -> Tuple[bool, str]:
        if name in self.servers:
            del self.servers[name]
            try:
                self._save_config()
            except Exception as e:
                return False, f"Failed to save config: {e}"
            return True, f"Serveur MCP '{name}' supprime."
        return False, f"Serveur '{name}' non trouve."

    def list_servers(self) -> Dict[str, Any]:
        # Always re-read so external edits are visible
        self.reload()
        return dict(self.servers)

    def get_server_config(self, name: str) -> Optional[Dict[str, Any]]:
        self.reload()
        cfg = self.servers.get(name)
        return dict(cfg) if isinstance(cfg, dict) else None

    @staticmethod
    def _parse_command(server_cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
        """Return (executable, args) from config entry."""
        cmd_str = server_cfg.get("command") or ""
        explicit_args = server_cfg.get("args")
        if explicit_args is not None:
            if not isinstance(explicit_args, list):
                raise RuntimeError("'args' must be a list of strings")
            parts = shlex.split(str(cmd_str)) if cmd_str else []
            if not parts:
                raise RuntimeError("Empty command")
            return parts[0], list(parts[1:]) + [str(a) for a in explicit_args]

        parts = shlex.split(str(cmd_str))
        if not parts:
            raise RuntimeError("Empty command")
        return parts[0], parts[1:]

    def get_client(self, name: str, timeout: float = 30.0):
        """Spawn a new SimpleMCPClient for the named server (caller must close())."""
        self.reload()
        if name not in self.servers:
            return None
        from src.core.mcp_client import SimpleMCPClient

        server_cfg = self.servers[name]
        if not isinstance(server_cfg, dict):
            return None
        command, args = self._parse_command(server_cfg)
        env = server_cfg.get("env") or {}
        if env and not isinstance(env, dict):
            env = {}
        cwd = server_cfg.get("cwd")
        return SimpleMCPClient(
            command,
            args,
            env={str(k): str(v) for k, v in env.items()},
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )

    def test_server(self, name: str, timeout: float = 15.0) -> Tuple[bool, str]:
        """Start server, initialize, list tools, close. Returns (ok, message)."""
        try:
            client = self.get_client(name, timeout=timeout)
        except Exception as e:
            return False, f"Start failed: {e}"
        if client is None:
            return False, f"Server '{name}' not found"
        try:
            client.initialize()
            tools = client.list_tools()
            n = len(tools) if isinstance(tools, list) else "?"
            return True, f"OK — {n} tool(s) available"
        except Exception as e:
            return False, str(e)
        finally:
            try:
                client.close()
            except Exception:
                pass
