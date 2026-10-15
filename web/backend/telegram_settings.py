"""Telegram bot settings shared with the CLI bot.

The bot reads its token from ``TELEGRAM_BOT_TOKEN`` or the project
``telegram_config.yaml``. To let the web UI configure the same bot, we persist a
small overlay under the shared user config directory and merge it at load time.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

import yaml

from src.core.paths import USER_CONFIG_DIR

_LOCK = threading.RLock()
_CONFIG_NAME = "telegram_bot.yaml"
_INSTALL_CONFIG = Path(__file__).resolve().parent.parent.parent / "telegram_config.yaml"


def _user_path() -> Path:
    return Path(USER_CONFIG_DIR) / _CONFIG_NAME


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
            return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def load_telegram_config() -> Dict[str, Any]:
    with _LOCK:
        merged: Dict[str, Any] = {}
        for path in (_INSTALL_CONFIG, _user_path()):
            merged.update(_read_yaml(path))
        return merged


def public_telegram_config() -> Dict[str, Any]:
    cfg = load_telegram_config()
    token = str(cfg.get("token") or "")
    nemapi = cfg.get("nemapi") if isinstance(cfg.get("nemapi"), dict) else {}
    return {
        "token_set": bool(token),
        "token_preview": (token[:8] + "…") if token else "",
        "bot_name": cfg.get("bot_name", ""),
        "workspace": cfg.get("workspace", ""),
        "nemapi": {
            "host": nemapi.get("host", ""),
            "port": nemapi.get("port", 0),
            "model": nemapi.get("model", ""),
        },
        "memory_shared": True,
        "approval_mode": "all",
    }


def save_telegram_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        cfg = _read_yaml(_user_path())
        if "token" in patch:
            cfg["token"] = str(patch.get("token") or "")
        if "bot_name" in patch:
            cfg["bot_name"] = str(patch.get("bot_name") or "")
        if "workspace" in patch:
            cfg["workspace"] = str(patch.get("workspace") or "")
        if isinstance(patch.get("nemapi"), dict):
            nemapi = cfg.get("nemapi") if isinstance(cfg.get("nemapi"), dict) else {}
            nemapi.update({k: v for k, v in patch["nemapi"].items() if v is not None})
            cfg["nemapi"] = nemapi
        if not cfg.get("provider"):
            cfg["provider"] = {"type": "nemapi"}
        path = _user_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(cfg, handle, allow_unicode=True, sort_keys=False)
        return public_telegram_config()