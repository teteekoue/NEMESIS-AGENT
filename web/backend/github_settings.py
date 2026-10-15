"""Thin wrapper exposing the shared GitHub settings to the web backend."""

from __future__ import annotations

from typing import Any, Dict

from src.core import github_settings as _core


def public_github_config() -> Dict[str, Any]:
    return _core.public_github_config()


def save_github_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    return _core.save_github_config(patch)