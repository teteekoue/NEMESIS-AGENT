"""Configuration store for the NEMESIS web backend.

Reuses the CLI path resolution (``src.core.paths``) so the web UI and the
terminal share the exact same ``~/.config/nemesis-cli/config.yaml``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

import yaml

from src.core.paths import (
    config_path,
    ensure_user_dirs,
    resolve_workspace,
    DEFAULT_WORKSPACE,
)

_LOCK = threading.RLock()

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": {"type": "nemapi", "model": "qwen-chat"},
    "nemapi": {"host": "127.0.0.1", "port": 8090},
    "security": {"workspace": str(DEFAULT_WORKSPACE), "approval_mode": "all"},
}


def _normalize(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    """Fill missing/invalid sections with sane defaults (mirrors agent.py)."""
    config: Dict[str, Any] = raw if isinstance(raw, dict) else {}

    if not isinstance(config.get("provider"), dict) or not config["provider"]:
        config["provider"] = {"type": "nemapi", "model": "qwen-chat"}
    if config["provider"].get("type") in {"bridge", "nemapi_bridge", "nemapi-v3"}:
        config["provider"]["type"] = "nemapi"
    if "type" not in config["provider"]:
        config["provider"]["type"] = "nemapi"
    config["provider"].setdefault("model", "qwen-chat")

    if not isinstance(config.get("nemapi"), dict):
        legacy = config.get("nemapi_v3", {})
        config["nemapi"] = legacy if isinstance(legacy, dict) else {"host": "127.0.0.1", "port": 8090}
    config["nemapi"].setdefault("host", "127.0.0.1")
    config["nemapi"].setdefault("port", 8090)
    if config["nemapi"].get("url"):
        config["nemapi"]["url"] = str(config["nemapi"]["url"]).strip().rstrip("/")
    else:
        config["nemapi"].pop("url", None)

    if not isinstance(config.get("security"), dict):
        config["security"] = {"workspace": str(DEFAULT_WORKSPACE), "approval_mode": "all"}
    ws = resolve_workspace(config)
    config["security"]["workspace"] = str(ws)
    mode = str(config["security"].get("approval_mode") or "all").lower()
    config["security"]["approval_mode"] = mode if mode in {"all", "confirm"} else "all"

    return config


def load_config() -> Dict[str, Any]:
    """Load and normalize the shared configuration file."""
    with _LOCK:
        ensure_user_dirs()
        path = config_path()
        raw: Dict[str, Any] | None = None
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = yaml.safe_load(handle)
                    raw = loaded if isinstance(loaded, dict) else {}
            except (OSError, yaml.YAMLError):
                raw = {}
        config = _normalize(raw)
        return config


def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a normalized configuration and return the stored value."""
    with _LOCK:
        ensure_user_dirs()
        normalized = _normalize(dict(config))
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(normalized, handle, allow_unicode=True, sort_keys=False)
        return normalized


class _ActiveUrlProxyMixin:
    """Handle clearing a public URL when switching back to local host/port."""

    pass


def update_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-ish merge a partial configuration update (provider/nemapi/security)."""
    with _LOCK:
        current = load_config()
        if isinstance(patch.get("provider"), dict):
            current["provider"].update(patch["provider"])
        if isinstance(patch.get("nemapi"), dict):
            incoming = dict(patch["nemapi"])
            # Sentinels from the web UI:
            #   url="" -> use local host/port (drop the public URL)
            #   host="" -> use the public URL (ignore local host/port)
            url = (incoming.get("url") or "").strip()
            host = (incoming.get("host") or "").strip()
            if incoming.get("url") is not None and not url:
                incoming.pop("url", None)
                current["nemapi"].pop("url", None)
            if incoming.get("host") is not None and not host:
                incoming.pop("host", None)
            current["nemapi"].update(incoming)
        if isinstance(patch.get("security"), dict):
            current["security"].update(patch["security"])
        return save_config(current)


def workspace_path() -> Path:
    """Return the resolved workspace directory for the current config."""
    return resolve_workspace(load_config())


def public_config() -> Dict[str, Any]:
    """Shape used by the web UI (no secrets; NEMAPI needs none)."""
    config = load_config()
    nemapi = config.get("nemapi", {})
    security = config.get("security", {})
    return {
        "provider": config.get("provider", {}),
        "nemapi": nemapi,
        "mode": "url" if nemapi.get("url") else "local",
        "workspace": security.get("workspace", ""),
        "model": config.get("provider", {}).get("model", ""),
        "security": {
            "workspace": security.get("workspace", ""),
            "approval_mode": security.get("approval_mode", "all"),
            "auto_approve_reads": security.get("auto_approve_reads", True),
            "confirm_high_risk": security.get("confirm_high_risk", True),
            "show_tool_outputs": security.get("show_tool_outputs", False),
        },
    }
