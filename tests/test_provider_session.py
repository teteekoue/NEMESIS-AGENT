"""Tests for NEMAPI logical-session handling (system prompt / fresh_chat).

NemApi's ``fresh_chat`` makes the browser extension reload the provider page.
NEMESIS must NEVER trigger that, so it is always sent as False. The only state
that matters is whether the system prompt was already delivered for a session.
"""

import json

from providers.nemapi_v3 import NemapiV3Provider


class _FakeResponse:
    status_code = 200
    text = '{"choices":[{"message":{"content":"ok"}}]}'

    def json(self):
        return json.loads(self.text)


class _FakeSession:
    def __init__(self):
        self.payloads = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        return _FakeResponse()

    def get(self, *args, **kwargs):
        class _R:
            status_code = 200

        return _R()


CONFIG = {
    "provider": {"type": "nemapi", "model": "qwen-chat"},
    "nemapi": {"host": "127.0.0.1", "port": 8090},
}


def _provider(initialized=False, session_id=None):
    p = NemapiV3Provider(CONFIG)
    p._session = _FakeSession()
    p.configure_session(session_id, initialized=initialized)
    return p


def test_new_session_sends_prompt_and_never_fresh_chat():
    p = _provider()
    p.send_message("bonjour", role="user", system_prefix="SYSTEM_PROMPT_TOOLS")

    payload = p._session.payloads[-1]
    assert payload["fresh_chat"] is False
    content = payload["messages"][0]["content"]
    assert "[SYSTEM INSTRUCTIONS]" in content
    assert "SYSTEM_PROMPT_TOOLS" in content


def test_followup_does_not_resend_prompt_nor_fresh_chat():
    p = _provider()
    p.send_message("bonjour", role="user", system_prefix="SYSTEM_PROMPT_TOOLS")
    p.send_message("suite", role="user", system_prefix="")

    payload = p._session.payloads[-1]
    assert payload["fresh_chat"] is False
    assert "[SYSTEM INSTRUCTIONS]" not in payload["messages"][0]["content"]


def test_resumed_session_never_refreshes_url_nor_resends_prompt():
    p = _provider(initialized=True, session_id="abc123")
    p.send_message("je reprends", role="user", system_prefix="SYSTEM_PROMPT_TOOLS")

    payload = p._session.payloads[-1]
    assert payload["fresh_chat"] is False
    assert "[SYSTEM INSTRUCTIONS]" not in payload["messages"][0]["content"]


def test_every_request_sends_fresh_chat_false():
    p = _provider()
    p.send_message("a", role="user", system_prefix="SYS")
    p.send_message("b", role="user", system_prefix="")
    p.send_message("resultat outil", role="tool_result", system_prefix="")

    assert all(payload["fresh_chat"] is False for payload in p._session.payloads)