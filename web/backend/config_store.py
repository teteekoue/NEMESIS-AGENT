"""Configuration store for the NEMESIS web backend.

Reuses the CLI path resolution (``src.core.paths``) so the web UI and the
terminal share the exact same ``~/.config/nemesis-cli/config.yaml``.
"""

from __future__ import annotations

import threading
import os
import uuid
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit

import yaml

from src.core.paths import (
    config_path,
    ensure_user_dirs,
    resolve_workspace,
)
from src.core.subagents import DEFAULT_SUBAGENT_CONFIG

_LOCK = threading.RLock()

DEFAULT_CONFIG: Dict[str, Any] = {
    "provider": {"type": "nemapi", "model": "qwen-chat"},
    "nemapi": {"host": "127.0.0.1", "port": 8090},
    "security": {"workspace": "", "approval_mode": "all"},
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
        public_url = str(config["nemapi"]["url"]).strip().rstrip("/")
        if public_url and not public_url.lower().startswith(("http://", "https://")):
            public_url = f"https://{public_url}"
        config["nemapi"]["url"] = public_url
    else:
        config["nemapi"].pop("url", None)
    if "browser_relay" in config["nemapi"]:
        config["nemapi"]["browser_relay"] = bool(config["nemapi"]["browser_relay"])

    if not isinstance(config.get("security"), dict):
        config["security"] = {"workspace": "", "approval_mode": "all"}
    # The agent is not sandboxed: an empty workspace means "current directory".
    config["security"]["workspace"] = str(config["security"].get("workspace") or "")
    mode = str(config["security"].get("approval_mode") or "all").lower()
    config["security"]["approval_mode"] = mode if mode in {"all", "confirm"} else "all"

    subagents = config.get("subagents")
    if isinstance(subagents, dict):
        profiles = subagents.get("profiles")
        if isinstance(profiles, list):
            for profile in profiles:
                if (
                    isinstance(profile, dict)
                    and profile.get("provider") == "groq"
                    and profile.get("model") == "openai/gpt-oss-20b"
                ):
                    profile["model"] = DEFAULT_SUBAGENT_CONFIG["model"]
        elif (
            subagents.get("provider") == "groq"
            and subagents.get("model") == "openai/gpt-oss-20b"
        ):
            subagents["model"] = DEFAULT_SUBAGENT_CONFIG["model"]

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
        path.chmod(0o600)
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
            if not str(current["nemapi"].get("url") or "").strip() and "browser_relay" in current["nemapi"]:
                current["nemapi"]["browser_relay"] = False
        if isinstance(patch.get("security"), dict):
            current["security"].update(patch["security"])
        return save_config(current)


def workspace_path() -> Path:
    """Return the resolved workspace directory for the current config."""
    return resolve_workspace(load_config())


def public_config() -> Dict[str, Any]:
    """Shape used by the web UI without returning the optional NEMAPI token."""
    config = load_config()
    nemapi = dict(config.get("nemapi", {}))
    nemapi.pop("token", None)
    security = config.get("security", {})
    return {
        "provider": config.get("provider", {}),
        "nemapi": nemapi,
        "mode": "url" if nemapi.get("url") else "local",
        "workspace": str(resolve_workspace(config)),
        "model": config.get("provider", {}).get("model", ""),
        "security": {
            "workspace": str(resolve_workspace(config)),
            "approval_mode": security.get("approval_mode", "all"),
            "auto_approve_reads": security.get("auto_approve_reads", True),
            "confirm_high_risk": security.get("confirm_high_risk", True),
            "show_tool_outputs": security.get("show_tool_outputs", False),
        },
    }


def public_subagents_config() -> Dict[str, Any]:
    """Expose sub-agent profiles and limits without returning API keys."""
    config = load_config().get("subagents", {})
    if not isinstance(config, dict):
        config = {}
    raw_profiles = config.get("profiles")
    if not isinstance(raw_profiles, list):
        raw_profiles = []
        if any(config.get(key) for key in ("api_key", "provider", "base_url", "model")):
            raw_profiles.append({**config, "id": config.get("id") or "legacy-default"})
    profiles = []
    for index, raw in enumerate(raw_profiles):
        if not isinstance(raw, dict):
            continue
        profile = {**DEFAULT_SUBAGENT_CONFIG, **raw}
        provider = str(profile.get("provider") or "groq").lower()
        env_name = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider, "")
        env_key = os.environ.get(env_name, "") if env_name else ""
        profiles.append({
            "id": str(profile.get("id") or f"profile-{index + 1}"),
            "name": str(profile.get("name") or f"{provider.title()} {index + 1}"),
            "provider": provider,
            "base_url": profile.get("base_url", ""),
            "model": profile.get("model", ""),
            "api_key_set": bool(profile.get("api_key") or env_key),
            "api_key_from_env": bool(not profile.get("api_key") and env_key),
            "api_key_env_name": env_name if not profile.get("api_key") and env_key else "",
        })
    for provider, env_name, base_url, model in (
        ("groq", "GROQ_API_KEY", DEFAULT_SUBAGENT_CONFIG["base_url"], DEFAULT_SUBAGENT_CONFIG["model"]),
        ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    ):
        if os.environ.get(env_name) and not any(
            profile["api_key_from_env"] and profile["provider"] == provider
            for profile in profiles
        ):
            profiles.append({
                "id": f"environment-{provider}",
                "name": f"{provider.title()} (variable d’environnement)",
                "provider": provider,
                "base_url": base_url,
                "model": model,
                "api_key_set": True,
                "api_key_from_env": True,
                "api_key_env_name": env_name,
            })
    main_provider = str((load_config().get("provider") or {}).get("type") or "").lower()
    if any(profile["api_key_set"] for profile in profiles):
        delegation = {
            "available": True,
            "mode": "dedicated",
            "detail": "Profil de sous-agent dedie.",
        }
    elif main_provider:
        delegation = {
            "available": True,
            "mode": "main-provider",
            "detail": "Aucune cle dediee : le provider principal est reutilise.",
        }
    else:
        delegation = {
            "available": False,
            "mode": "unavailable",
            "detail": "Aucun provider utilisable pour la delegation.",
        }
    return {
        "profiles": profiles,
        "profile_count": len(profiles),
        "delegation": delegation,
        "max_concurrent": int(config.get("max_concurrent", 4)),
        "max_iterations": int(config.get("max_iterations", 0)),
        "timeout_seconds": int(config.get("timeout_seconds", 180)),
        "max_retries": int(config.get("max_retries", 3)),
        "max_tokens_per_task": int(config.get("max_tokens_per_task", 24000)),
    }


def _validate_subagent_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    provider = str(data.get("provider") or "groq").strip().lower()
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    model = str(data.get("model") or "").strip()
    name = str(data.get("name") or f"{provider.title()} — {model}").strip()
    if provider not in {"groq", "openai", "custom"}:
        raise ValueError("Provider sous-agent invalide.")
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
    ):
        raise ValueError("L'URL de base doit commencer par http:// ou https://.")
    if provider == "custom" and not parsed_url.path.rstrip("/").endswith("/v1"):
        raise ValueError("L'URL d'un fournisseur personnalisé doit se terminer par /v1.")
    if not model:
        raise ValueError("Le nom du modèle est obligatoire.")
    if provider == "custom" and not str(data.get("name") or "").strip():
        raise ValueError("Le nom du fournisseur personnalisé est obligatoire.")
    if not name:
        raise ValueError("Le nom du profil est obligatoire.")
    api_key = str(data.get("api_key") or "").strip()
    if not api_key and not data.get("id"):
        raise ValueError("La clé API est obligatoire pour créer un profil.")
    return {
        "id": str(data.get("id") or uuid.uuid4().hex[:12]),
        "name": name[:80],
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key": api_key,
    }


def save_subagent_profile(data: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update one credential profile while preserving other keys."""
    profile = _validate_subagent_profile(data)
    current = load_config()
    settings = current.get("subagents")
    if not isinstance(settings, dict):
        settings = {}
    profiles = settings.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
        if settings.get("api_key"):
            profiles.append({
                "id": "legacy-default",
                "name": "Groq (profil existant)",
                **{key: settings.get(key, DEFAULT_SUBAGENT_CONFIG[key])
                   for key in ("provider", "base_url", "model", "api_key")},
            })
    for index, saved in enumerate(profiles):
        if isinstance(saved, dict):
            saved.setdefault("id", f"profile-{index + 1}")
    previous_provider = None
    previous_url = None
    matched = False
    for index, saved in enumerate(profiles):
        if not isinstance(saved, dict) or saved.get("id") != profile["id"]:
            continue
        matched = True
        previous_provider = saved.get("provider")
        previous_url = saved.get("base_url")
        if not profile["api_key"]:
            profile["api_key"] = str(saved.get("api_key") or "")
        profiles[index] = profile
        break
    else:
        profiles.append(profile)
    if not profile["api_key"] and not matched:
        raise ValueError("La clé API est obligatoire pour créer un profil.")
    if previous_provider and (
        profile["provider"] != previous_provider or profile["base_url"] != previous_url
    ) and not data.get("api_key"):
        profile.pop("api_key", None)
    settings["profiles"] = profiles
    settings.pop("api_key", None)
    settings.pop("provider", None)
    settings.pop("base_url", None)
    settings.pop("model", None)
    current["subagents"] = settings
    save_config(current)
    return public_subagents_config()


def delete_subagent_profile(profile_id: str) -> Dict[str, Any]:
    """Delete a saved API profile without exposing or returning its secret."""
    current = load_config()
    settings = current.get("subagents")
    if not isinstance(settings, dict):
        raise ValueError("Aucun profil de sous-agent n'est configuré.")
    profiles = settings.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
        if settings.get("api_key"):
            profiles.append({"id": "legacy-default", "api_key": settings["api_key"]})
    filtered = [profile for profile in profiles if not isinstance(profile, dict) or profile.get("id") != profile_id]
    if len(filtered) == len(profiles):
        raise KeyError("Profil de sous-agent introuvable.")
    settings["profiles"] = filtered
    settings.pop("api_key", None)
    settings.pop("provider", None)
    settings.pop("base_url", None)
    settings.pop("model", None)
    current["subagents"] = settings
    save_config(current)
    return public_subagents_config()


def save_subagents_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """Persist global worker limits and legacy single-profile updates."""
    if any(key in data for key in ("provider", "base_url", "model")):
        legacy = {
            key: data[key]
            for key in ("provider", "base_url", "model", "api_key")
            if key in data
        }
        legacy["id"] = "legacy-default"
        existing = load_config().get("subagents", {})
        old_profiles = existing.get("profiles", []) if isinstance(existing, dict) else []
        existing_profile = next(
            (p for p in old_profiles if isinstance(p, dict) and p.get("id") == "legacy-default"),
            None,
        ) if isinstance(old_profiles, list) else None
        if not legacy.get("api_key") and existing_profile:
            legacy["api_key"] = existing_profile.get("api_key", "")
        elif not legacy.get("api_key") and isinstance(existing, dict):
            legacy["api_key"] = existing.get("api_key", "")
        old_provider = (existing_profile or {}).get("provider") if existing_profile else (
            existing.get("provider") if isinstance(existing, dict) else None
        )
        old_url = (existing_profile or {}).get("base_url") if existing_profile else (
            existing.get("base_url") if isinstance(existing, dict) else None
        )
        if (
            not data.get("api_key")
            and old_provider
            and (
                legacy.get("provider") != old_provider
                or str(legacy.get("base_url") or "").rstrip("/") != str(old_url or "").rstrip("/")
            )
        ):
            legacy.pop("api_key", None)
        if data.get("clear_api_key"):
            legacy.pop("api_key", None)
        profile = _validate_subagent_profile(legacy)
        current = load_config()
        settings = current.get("subagents")
        if not isinstance(settings, dict):
            settings = {}
        profiles = settings.get("profiles")
        if not isinstance(profiles, list):
            profiles = []
        profiles = [p for p in profiles if not isinstance(p, dict) or p.get("id") != profile["id"]]
        if not data.get("clear_api_key") or profile.get("api_key"):
            profiles.append(profile)
        settings["profiles"] = profiles
        settings.pop("api_key", None)
        settings.pop("provider", None)
        settings.pop("base_url", None)
        settings.pop("model", None)
        current["subagents"] = settings
    else:
        current = load_config()
        settings = current.get("subagents")
        if not isinstance(settings, dict):
            settings = {}
        current["subagents"] = settings

    max_concurrent = int(data.get("max_concurrent", 4))
    max_iterations = int(data.get("max_iterations", 0))
    timeout_seconds = int(data.get("timeout_seconds", 180))
    max_retries = int(data.get("max_retries", 3))
    max_tokens_per_task = int(data.get("max_tokens_per_task", 24000))
    if not 1 <= max_concurrent <= 8:
        raise ValueError("Le nombre de sous-agents simultanés doit être entre 1 et 8.")
    if not 0 <= max_iterations <= 100000:
        raise ValueError(
            "Le nombre maximal d'itérations doit être 0 (illimité) ou un entier positif."
        )
    if not 10 <= timeout_seconds <= 600:
        raise ValueError("Le timeout doit être entre 10 et 600 secondes.")
    if not 0 <= max_retries <= 8:
        raise ValueError("Le nombre de reprises doit être entre 0 et 8.")
    if not 1000 <= max_tokens_per_task <= 200000:
        raise ValueError("Le budget par tâche doit être entre 1 000 et 200 000 tokens.")
    settings.update({
        "max_concurrent": max_concurrent,
        "max_iterations": max_iterations,
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "max_tokens_per_task": max_tokens_per_task,
    })
    current["subagents"] = settings
    save_config(current)
    return public_subagents_config()
