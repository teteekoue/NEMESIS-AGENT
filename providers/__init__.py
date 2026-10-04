#!/usr/bin/env python3
"""Fabrique de providers pour NEMESIS CLI."""

from typing import Dict, Any
from providers.base import BaseProvider
from providers.nemapi_v3 import NemapiV3Provider
from providers.mock import MockProvider

PROVIDER_MAP = {
    "nemapi": NemapiV3Provider,
    "mock": MockProvider,
}

# Optional OpenAI-compatible providers. They are registered only when the
# `openai` SDK is installed, so the base install keeps working without it.
try:
    from providers.openai_compatible import (
        OpenAICompatibleProvider,
        NvidiaNimProvider,
        XaiProvider,
        OpenRouterProvider,
        OllamaProvider,
        FireworksProvider,
        CohereProvider,
    )

    PROVIDER_MAP.update({
        "openai": OpenAICompatibleProvider,
        "nvidia_nim": NvidiaNimProvider,
        "xai": XaiProvider,
        "openrouter": OpenRouterProvider,
        "ollama": OllamaProvider,
        "fireworks": FireworksProvider,
        "cohere": CohereProvider,
    })
except Exception:  # pragma: no cover - optional dependency
    pass


def create_provider(config: Dict[str, Any]) -> BaseProvider:
    """Cree le provider approprie selon la configuration."""
    provider_config = config.get("provider", {})
    provider_type = provider_config.get("type", "nemapi")

    provider_cls = PROVIDER_MAP.get(provider_type)
    if provider_cls is None:
        available = ", ".join(PROVIDER_MAP.keys())
        raise ValueError(
            f"Provider inconnu: '{provider_type}'. Disponibles: {available}"
        )
    return provider_cls(config)


def list_providers() -> list:
    """Liste les providers disponibles."""
    return list(PROVIDER_MAP.keys())
