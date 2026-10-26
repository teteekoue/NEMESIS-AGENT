"""Image reading → NemApi multimodal ``image_url`` parts."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from providers.nemapi_v3 import NemapiV3Provider
from src.core.image_attachments import (
    MAX_IMAGES,
    build_image_parts,
    collect_result_images,
    detect_image_mime,
    send_message_with_images,
)
from src.core.tool_bridge import ToolBridge

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_DATA_URL = f"data:image/png;base64,{PNG_B64}"

CONFIG = {
    "provider": {"type": "nemapi", "model": "deepseek-chat"},
    "nemapi": {"host": "127.0.0.1", "port": 8090},
}


class _FakeResponse:
    status_code = 200
    text = '{"choices":[{"message":{"content":"ok"}}]}'


class _FakeSession:
    def __init__(self):
        self.payloads = []
        self.headers = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.payloads.append(json)
        self.headers.append(headers)
        return _FakeResponse()

    def get(self, *args, **kwargs):
        return _FakeResponse()


def _write_png(path):
    path.write_bytes(base64.b64decode(PNG_B64))
    return path


def _provider():
    p = NemapiV3Provider(CONFIG)
    p._session = _FakeSession()
    return p


def _bridge(tmp_path):
    return ToolBridge(workspace=str(tmp_path), config={}, enable_subagents=False)


def test_image_file_is_attached_not_dumped_as_text(tmp_path):
    image = _write_png(tmp_path / "capture.png")
    (tmp_path / "notes.txt").write_text("bonjour\nle monde\n", encoding="utf-8")

    bridge = _bridge(tmp_path)
    image_result = list(bridge.execute_tool("read_file", {"path": str(image)}))[-1]
    text_result = list(bridge.execute_tool("read_file", {"path": str(tmp_path / "notes.txt")}))[-1]

    assert image_result["success"] is True
    assert "[IMAGE ATTACHED]" in image_result["stdout"]
    assert PNG_B64 not in image_result["stdout"]
    assert len(image_result["images"]) == 1
    assert image_result["images"][0]["mime"] == "image/png"
    assert image_result["images"][0]["name"] == "capture.png"
    assert collect_result_images(image_result)

    assert "bonjour" in text_result["stdout"]
    assert not text_result.get("images")


def test_multi_file_read_merges_images(tmp_path):
    image = _write_png(tmp_path / "logo.png")
    text = tmp_path / "readme.md"
    text.write_text("# titre\n", encoding="utf-8")

    bridge = _bridge(tmp_path)
    result = list(bridge.execute_tool("read_file", {"paths": [str(image), str(text)]}))[-1]

    assert len(result["images"]) == 1
    assert "[IMAGE ATTACHED]" in result["stdout"]
    assert "# titre" in result["stdout"]


def test_detect_image_mime_by_magic_bytes(tmp_path):
    image = _write_png(tmp_path / "no-extension")
    assert detect_image_mime(str(image)) == "image/png"
    plain = tmp_path / "notes.md"
    plain.write_text("just text", encoding="utf-8")
    assert detect_image_mime(str(plain)) is None
    misleading = tmp_path / "not-really.png"
    misleading.write_text("ceci est du texte, pas une image", encoding="utf-8")
    assert detect_image_mime(str(misleading)) is None


def test_build_image_parts_uses_data_url_and_filename(tmp_path):
    image = _write_png(tmp_path / "capture.png")

    parts, notes = build_image_parts([{"path": str(image)}])

    assert notes == []
    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"] == PNG_DATA_URL
    assert parts[0]["filename"] == "capture.png"


def test_build_image_parts_refuses_remote_url_and_caps_count():
    parts, notes = build_image_parts([{"url": "https://example.com/a.png"}])
    assert parts == []
    assert "data:" in notes[0]

    specs = [{"url": PNG_DATA_URL}] * (MAX_IMAGES + 2)
    parts, notes = build_image_parts(specs)
    assert len(parts) == MAX_IMAGES
    assert notes and str(MAX_IMAGES) in notes[0]


def test_build_image_parts_skips_oversized_file(tmp_path, monkeypatch):
    image = _write_png(tmp_path / "big.png")
    monkeypatch.setattr("src.core.image_attachments.MAX_IMAGE_BYTES", 4)

    parts, notes = build_image_parts([{"path": str(image)}])

    assert parts == []
    assert "refusée" in notes[0]


def test_provider_sends_openai_multimodal_content_and_agent_header():
    provider = _provider()
    parts, _ = build_image_parts([{"url": PNG_DATA_URL}])

    result = provider.send_message("Que montre cette capture ?", images=parts)

    assert result["success"] is True
    payload = provider._session.payloads[-1]
    message = payload["messages"][0]
    assert message["role"] == "user"
    assert isinstance(message["content"], list)
    assert message["content"][0] == {"type": "text", "text": "Que montre cette capture ?"}
    assert message["content"][1]["image_url"]["url"] == PNG_DATA_URL
    assert payload["fresh_chat"] is False
    assert provider._session.headers[-1]["X-NemApi-Agent"] == "nemesis"


def test_provider_keeps_plain_string_without_images():
    provider = _provider()
    provider.send_message("bonjour", system_prefix="SYS")

    content = provider._session.payloads[-1]["messages"][0]["content"]
    assert isinstance(content, str)
    assert "[SYSTEM INSTRUCTIONS]" in content


def test_images_on_tool_result_are_attached_to_the_user_message():
    provider = _provider()
    parts, _ = build_image_parts([{"url": PNG_DATA_URL}])

    result = send_message_with_images(
        provider,
        "FEEDBACK:\nTool: read_file\nOutput:\n[IMAGE ATTACHED]",
        role="tool_result",
        images=parts,
        system_prefix="",
    )

    assert result["success"] is True
    message = provider._session.payloads[-1]["messages"][0]
    assert message["role"] == "user"
    assert message["content"][1]["image_url"]["url"] == PNG_DATA_URL


def test_legacy_provider_without_multimodal_still_receives_text():
    class _Legacy:
        def __init__(self):
            self.calls = []

        def send_message(self, message, role="user"):
            self.calls.append((message, role))
            return {"success": True, "response": "ok"}

    legacy = _Legacy()
    result = send_message_with_images(
        legacy, "regarde", images=[{"url": PNG_DATA_URL}], system_prefix="SYS"
    )

    assert result["success"] is True
    message, role = legacy.calls[0]
    assert role == "user"
    assert "[SYSTEM INSTRUCTIONS]" in message
    assert "ne gère pas le multimodal" in message
    assert PNG_B64 not in message


def test_web_runner_sends_image_on_the_following_turn(tmp_path, monkeypatch):
    """Full web loop: read_file(image) → next provider request is multimodal."""
    from web.backend import agent_runner

    image = _write_png(tmp_path / "capture.png")

    class _VisionProvider:
        supports_images = True
        model = "deepseek-chat"
        host = "127.0.0.1"
        port = 8090

        def __init__(self):
            self.calls = []

        def configure_session(self, *args, **kwargs):
            return None

        def test_connection(self, max_age=None):
            return True

        def send_message(self, message, role="user", cancel_event=None, system_prefix="", images=None):
            self.calls.append({"message": message, "role": role, "images": images or []})
            if len(self.calls) == 1:
                call = json.dumps({"tool": "read_file", "parameters": {"path": str(image)}})
                return {
                    "success": True,
                    "response": f"Je regarde l'image.\n```json\n{call}\n```",
                    "tool_calls": [],
                }
            return {"success": True, "response": "C'est un pixel transparent.", "tool_calls": []}

    provider = _VisionProvider()
    monkeypatch.setattr(agent_runner, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        agent_runner,
        "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(
            workspace=str(tmp_path), config=config, enable_subagents=False
        ),
    )

    loop = asyncio.new_event_loop()
    runner = agent_runner.AgentRunner(
        {"provider": {"type": "nemapi", "model": "deepseek-chat"},
         "security": {"approval_mode": "all", "workspace": str(tmp_path)}},
        loop,
        approval_mode="all",
    )
    runner._conn_ok = True
    runner._prompt_sent = True
    events = []
    runner.emit = lambda event_type, **data: events.append({"type": event_type, **data})
    try:
        tools_used = runner._run_turn("Décris cette image.")
    finally:
        runner.close()
        loop.close()

    assert tools_used == 1
    assert len(provider.calls) == 2
    assert provider.calls[0]["images"] == []
    attached = provider.calls[1]["images"]
    assert len(attached) == 1
    assert attached[0]["image_url"]["url"] == PNG_DATA_URL
    assert provider.calls[1]["role"] == "tool_result"
    assert any(e["type"] == "tool_result" and e.get("image_count") == 1 for e in events)


def test_web_runner_accepts_images_attached_by_the_user(tmp_path, monkeypatch):
    from web.backend import agent_runner

    class _VisionProvider:
        supports_images = True
        model = "deepseek-chat"
        host = "127.0.0.1"
        port = 8090

        def __init__(self):
            self.calls = []

        def configure_session(self, *args, **kwargs):
            return None

        def test_connection(self, max_age=None):
            return True

        def send_message(self, message, role="user", cancel_event=None, system_prefix="", images=None):
            self.calls.append(images or [])
            return {"success": True, "response": "Image reçue.", "tool_calls": []}

    provider = _VisionProvider()
    monkeypatch.setattr(agent_runner, "create_provider", lambda config: provider)
    monkeypatch.setattr(
        agent_runner,
        "create_executor_from_config",
        lambda config, bridge=None: ToolBridge(
            workspace=str(tmp_path), config=config, enable_subagents=False
        ),
    )

    loop = asyncio.new_event_loop()
    runner = agent_runner.AgentRunner(
        {"provider": {"type": "nemapi", "model": "deepseek-chat"}, "security": {}},
        loop,
        approval_mode="all",
    )
    runner._conn_ok = True
    runner._prompt_sent = True
    runner.emit = lambda event_type, **data: None
    try:
        runner._run_turn("Que vois-tu ?", [PNG_DATA_URL])
    finally:
        runner.close()
        loop.close()

    assert len(provider.calls[0]) == 1
    assert provider.calls[0][0]["image_url"]["url"] == PNG_DATA_URL
