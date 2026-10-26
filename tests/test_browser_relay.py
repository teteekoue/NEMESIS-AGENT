"""Relais navigateur : contourner une sortie réseau serveur bloquée."""
from __future__ import annotations

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers.nemapi_v3 import NemapiV3Provider
from web.backend import browser_relay as relay
from web.backend import config_store


def _browser(hub, captured, *, status=200, body='{"choices":[{"message":{"content":"ok"}}]}'):
    """Simule un onglet connecté : capture la requête puis répond."""
    hub._dispatch = lambda request_id: captured.append(
        dict(hub._pending[request_id].payload)
    )

    def respond():
        for _ in range(200):
            if captured:
                break
            time.sleep(0.01)
        if captured:
            hub.deliver(captured[0]["id"], status=status, body=body)

    thread = threading.Thread(target=respond, daemon=True)
    thread.start()
    return thread


def test_relay_session_get_roundtrip():
    hub = relay.RelayHub()
    captured: list[dict] = []
    thread = _browser(hub, captured, body='{"object":"list","data":[{"id":"deepseek-chat"}]}')

    session = relay.RelaySession(hub)
    response = session.get(
        "https://tunnel.example.test/v1/models",
        timeout=10,
        headers={"X-NemApi-Agent": "nemesis"},
    )
    thread.join(timeout=2)

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "deepseek-chat"
    assert captured[0]["method"] == "GET"
    assert captured[0]["url"].endswith("/v1/models")
    assert captured[0]["headers"]["X-NemApi-Agent"] == "nemesis"
    assert hub.stats()["responses"] == 1


def test_relay_post_serializes_json_and_keeps_headers():
    hub = relay.RelayHub()
    captured: list[dict] = []
    thread = _browser(hub, captured)

    session = relay.RelaySession(hub)
    response = session.post(
        "https://tunnel.example.test/v1/chat/completions",
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": " hello "}]},
        headers={"X-NemApi-Agent": "nemesis", "Content-Type": "application/json"},
        timeout=(10, 180),
    )
    thread.join(timeout=2)

    assert response.status_code == 200
    payload = captured[0]
    assert payload["method"] == "POST"
    assert payload["headers"]["X-NemApi-Agent"] == "nemesis"
    assert json.loads(payload["body"])["messages"][0]["content"] == " hello "


def test_relay_reports_browser_fetch_error():
    hub = relay.RelayHub()

    def fail(request_id):
        hub.deliver(request_id, error="Failed to fetch")

    hub._dispatch = fail
    with pytest.raises(relay.RelayError, match="Failed to fetch"):
        relay.RelaySession(hub).get("https://tunnel.example.test/v1/models", timeout=10)


def test_relay_fails_fast_without_any_browser(monkeypatch):
    monkeypatch.setattr(relay, "RELAY_CONNECT_GRACE", 0.05)
    hub = relay.RelayHub()
    with pytest.raises(relay.RelayError, match="Relais navigateur inactif"):
        relay.RelaySession(hub).get("https://tunnel.example.test/v1/models", timeout=5)


def test_relay_enabled_requires_url_and_flag():
    assert relay.relay_enabled({"nemapi": {"url": "https://x/v1", "browser_relay": True}})
    assert not relay.relay_enabled({"nemapi": {"url": "https://x/v1"}})
    assert not relay.relay_enabled({"nemapi": {"browser_relay": True}})
    assert not relay.relay_enabled({})


def test_attach_to_provider_uses_relay_session():
    provider = NemapiV3Provider({
        "provider": {"type": "nemapi", "model": "deepseek-chat"},
        "nemapi": {"url": "https://tunnel.example.test/v1", "browser_relay": True},
    })
    assert relay.attach_to_provider(provider, {
        "nemapi": {"url": "https://tunnel.example.test/v1", "browser_relay": True}
    })
    assert isinstance(provider._session, relay.RelaySession)

    plain = NemapiV3Provider({"provider": {"type": "nemapi", "model": "qwen-chat"}})
    assert not relay.attach_to_provider(plain, {"nemapi": {}})
    assert not isinstance(plain._session, relay.RelaySession)


def test_provider_chat_goes_through_the_browser_relay():
    hub = relay.RelayHub()
    captured: list[dict] = []
    thread = _browser(hub, captured)
    provider = NemapiV3Provider({
        "provider": {"type": "nemapi", "model": "deepseek-chat"},
        "nemapi": {"url": "https://tunnel.example.test/v1"},
    })
    provider._session = relay.RelaySession(hub)

    result = provider.send_message("bonjour", system_prefix="SYS")
    thread.join(timeout=2)

    assert result["success"] is True
    assert captured[0]["url"] == "https://tunnel.example.test/v1/chat/completions"
    sent = json.loads(captured[0]["body"])
    assert sent["messages"][0]["role"] == "user"
    assert "[SYSTEM INSTRUCTIONS]" in sent["messages"][0]["content"]
    assert captured[0]["headers"]["X-NemApi-Agent"] == "nemesis"


def test_list_models_suggests_relay_on_tls_error(monkeypatch):
    from web.backend import agent_runner

    class FakeProvider:
        model = "deepseek-chat"

        def test_connection(self):
            return False

        def list_models(self):
            raise RuntimeError(
                "HTTPSConnectionPool(host='x.trycloudflare.com', port=443): "
                "Max retries exceeded with url: /v1/models "
                "(Caused by SSLError(SSLZeroReturnError(6, 'TLS/SSL connection has been closed (EOF)'))))"
            )

    monkeypatch.setattr(agent_runner, "create_provider", lambda config: FakeProvider())
    result = agent_runner.list_models({
        "provider": {"model": "deepseek-chat"},
        "nemapi": {"url": "https://x.trycloudflare.com/v1"},
    })
    assert result["connected"] is False
    assert "Relais navigateur" in result["error"]


def test_config_store_normalizes_relay_flag_and_url(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    monkeypatch.setattr(config_store, "config_path", lambda: config_file)
    monkeypatch.setattr(config_store, "ensure_user_dirs", lambda: None)

    saved = config_store.update_config({
        "nemapi": {"url": "tunnel.example.test/v1/", "host": "", "browser_relay": True},
        "provider": {"model": "deepseek-chat"},
    })
    assert saved["nemapi"]["url"] == "https://tunnel.example.test/v1"
    assert saved["nemapi"]["browser_relay"] is True
    assert config_store.public_config()["mode"] == "url"
    assert config_store.public_config()["nemapi"]["browser_relay"] is True

    local = config_store.update_config({
        "nemapi": {"url": "", "host": "127.0.0.1", "port": 8090},
    })
    assert local["nemapi"]["browser_relay"] is False
    assert config_store.public_config()["mode"] == "local"
