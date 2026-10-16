import pytest

from web.backend import config_store
from src.core.subagents import SubagentFleet


def test_subagent_settings_never_expose_api_key(monkeypatch):
    raw_config = {}
    monkeypatch.setattr(config_store, "load_config", lambda: raw_config)

    def save_config(data):
        raw_config.update(data.copy())
        return data

    monkeypatch.setattr(config_store, "save_config", save_config)
    public = config_store.save_subagent_profile({
        "name": "Test Groq",
        "provider": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
        "api_key": "test-secret",
    })

    assert raw_config["subagents"]["profiles"][0]["api_key"] == "test-secret"
    assert public["profiles"][0]["api_key_set"] is True
    assert "api_key" not in public["profiles"][0]
    assert "test-secret" not in str(public)


def test_saved_api_key_survives_config_reload_and_reaches_fleet(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr(config_store, "config_path", lambda: config_file)
    monkeypatch.setattr(config_store, "ensure_user_dirs", lambda: tmp_path)

    public = config_store.save_subagent_profile({
        "name": "Test Groq",
        "provider": "groq",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "qwen/qwen3.8-27b",
        "api_key": "test-secret",
    })

    reloaded = config_store.load_config()
    fleet = SubagentFleet(reloaded, str(tmp_path))

    assert public["profiles"][0]["api_key_set"] is True
    assert reloaded["subagents"]["profiles"][0]["api_key"] == "test-secret"
    assert fleet.profiles[0]["api_key"] == "test-secret"
    fleet.close()


def test_subagent_settings_validate_limits(monkeypatch):
    monkeypatch.setattr(config_store, "load_config", lambda: {})
    with pytest.raises(ValueError, match="simultanés"):
        config_store.save_subagents_config({
            "provider": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "qwen/qwen3.8-27b",
            "max_concurrent": 9,
        })


def test_changing_provider_does_not_reuse_previous_api_key(monkeypatch):
    raw_config = {
        "subagents": {
            "profiles": [{
                "id": "saved-profile",
                "name": "Groq",
                "provider": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "model": "qwen/qwen3.8-27b",
                "api_key": "groq-secret",
            }],
        },
    }
    monkeypatch.setattr(config_store, "load_config", lambda: raw_config)
    monkeypatch.setattr(config_store, "save_config", lambda data: data)
    public = config_store.save_subagent_profile({
        "id": "saved-profile",
        "name": "Custom provider",
        "provider": "custom",
        "base_url": "https://api.example.test/v1",
        "model": "custom-model",
    })

    assert "api_key" not in raw_config["subagents"]["profiles"][0]
    assert public["profiles"][0]["api_key_set"] is False


def test_custom_provider_requires_named_v1_endpoint(monkeypatch):
    monkeypatch.setattr(config_store, "load_config", lambda: {})
    monkeypatch.setattr(config_store, "save_config", lambda data: data)

    with pytest.raises(ValueError, match="nom du fournisseur"):
        config_store.save_subagent_profile({
            "provider": "custom",
            "base_url": "https://api.example.test/v1",
            "model": "custom-model",
            "api_key": "test-secret",
        })
    with pytest.raises(ValueError, match="se terminer par /v1"):
        config_store.save_subagent_profile({
            "name": "Example",
            "provider": "custom",
            "base_url": "https://api.example.test/openai",
            "model": "custom-model",
            "api_key": "test-secret",
        })


def test_delete_profile_removes_only_selected_secret(monkeypatch):
    raw_config = {
        "subagents": {
            "profiles": [
                {"id": "first", "provider": "groq", "api_key": "first-secret"},
                {"id": "second", "provider": "groq", "api_key": "second-secret"},
            ],
        },
    }
    monkeypatch.setattr(config_store, "load_config", lambda: raw_config)
    monkeypatch.setattr(config_store, "save_config", lambda data: data)

    public = config_store.delete_subagent_profile("first")

    assert [profile["id"] for profile in raw_config["subagents"]["profiles"]] == ["second"]
    assert public["profile_count"] == 1
    assert "first-secret" not in str(public)


def test_environment_key_is_discoverable_and_loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "environment-secret")
    monkeypatch.setattr(config_store, "load_config", lambda: {})

    public = config_store.public_subagents_config()
    fleet = SubagentFleet({"subagents": {}}, str(tmp_path))
    try:
        assert public["profile_count"] == 1
        assert public["profiles"][0]["api_key_from_env"] is True
        assert fleet.profiles[0]["api_key"] == "environment-secret"
    finally:
        fleet.close()


def test_global_limits_save_preserves_profiles(monkeypatch):
    raw_config = {
        "subagents": {
            "profiles": [{"id": "keep", "api_key": "keep-secret"}],
        },
    }
    monkeypatch.setattr(config_store, "load_config", lambda: raw_config)
    monkeypatch.setattr(config_store, "save_config", lambda data: data)

    public = config_store.save_subagents_config({
        "max_concurrent": 2,
        "max_iterations": 8,
        "timeout_seconds": 60,
        "max_retries": 2,
        "max_tokens_per_task": 12000,
    })

    assert raw_config["subagents"]["profiles"][0]["api_key"] == "keep-secret"
    assert public["profile_count"] == 1
    assert public["max_tokens_per_task"] == 12000


def test_legacy_groq_default_migrates_to_qwen_38():
    normalized = config_store._normalize({
        "subagents": {
            "provider": "groq",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "openai/gpt-oss-20b",
            "api_key": "legacy-secret",
        },
    })

    assert normalized["subagents"]["model"] == "qwen/qwen3.8-27b"
    fleet = SubagentFleet(normalized, ".")
    try:
        assert fleet.profiles[0]["model"] == "qwen/qwen3.8-27b"
    finally:
        fleet.close()
