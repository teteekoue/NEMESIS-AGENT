from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


class _TelegramDownload:
    def __init__(self, content: bytes):
        self.content = content

    async def download_to_drive(self, custom_path):
        Path(custom_path).write_bytes(self.content)
        return custom_path


class _FakeBot:
    def __init__(self, content: bytes = b"attached text"):
        self.content = content
        self.sent_documents = []
        self.sent_messages = []

    async def get_file(self, _file_id):
        return _TelegramDownload(self.content)

    async def send_document(self, **kwargs):
        document = kwargs["document"]
        self.sent_documents.append({**kwargs, "content": document.read()})

    async def send_message(self, **kwargs):
        self.sent_messages.append(kwargs)


@pytest.fixture
def telegram_bot():
    return pytest.importorskip("telegram_bot")


def test_any_document_is_saved_under_workspace_and_prepended_to_user_message(telegram_bot, tmp_path):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    media = SimpleNamespace(
        file_id="tg-file-1",
        file_name="../rapport final.txt",
        mime_type="text/plain",
        file_size=13,
    )
    message = SimpleNamespace(document=media, photo=None)
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(bot=_FakeBot(b"hello document"))

    files, images, errors = asyncio.run(
        bot._collect_telegram_attachments(
            update,
            context,
            984,
            {"security": {"workspace": str(tmp_path)}},
        )
    )

    assert not errors
    assert not images
    assert len(files) == 1
    uploaded = Path(files[0]["path"])
    assert uploaded.is_file()
    assert uploaded.read_bytes() == b"hello document"
    assert uploaded.is_relative_to(tmp_path / ".nemesis" / "telegram_uploads" / "984")
    assert ".." not in uploaded.name
    prompt = bot._prepend_attachment_instruction("Résume ce document.", files)
    assert prompt.startswith("FICHIERS JOINTS REÇUS VIA TELEGRAM")
    assert prompt.index(str(uploaded)) < prompt.index("MESSAGE DE L'UTILISATEUR")
    assert prompt.endswith("Résume ce document.")
    assert "base64" not in prompt


def test_attachments_fall_back_to_tmp_when_workspace_is_missing(telegram_bot, tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot.tempfile, "gettempdir", lambda: str(tmp_path / "tmp-root"))
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    media = SimpleNamespace(file_id="file-2", file_name="report.csv", mime_type="text/csv", file_size=4)
    update = SimpleNamespace(message=SimpleNamespace(document=media))
    context = SimpleNamespace(bot=_FakeBot(b"a,b\n1,2"))

    files, images, errors = asyncio.run(
        bot._collect_telegram_attachments(
            update,
            context,
            44,
            {"security": {"workspace": str(tmp_path / "does-not-exist")}},
        )
    )

    assert not errors and not images
    assert len(files) == 1
    assert Path(files[0]["path"]).is_relative_to(tmp_path / "tmp-root")


def test_oversized_upload_is_rejected_before_download(telegram_bot, tmp_path):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    media = SimpleNamespace(
        file_id="large-file",
        file_name="too-large.zip",
        mime_type="application/zip",
        file_size=telegram_bot.MAX_TELEGRAM_DOWNLOAD_BYTES + 1,
    )
    update = SimpleNamespace(message=SimpleNamespace(document=media))
    fake_bot = _FakeBot()

    files, images, errors = asyncio.run(
        bot._collect_telegram_attachments(
            update,
            SimpleNamespace(bot=fake_bot),
            12,
            {"security": {"workspace": str(tmp_path)}},
        )
    )

    assert not files and not images
    assert errors and "too-large.zip" in errors[0]
    assert not (tmp_path / ".nemesis" / "telegram_uploads" / "12").exists()


def test_telegram_only_send_file_tool_uploads_artifact_to_chat(telegram_bot, tmp_path):
    from src.core.tool_bridge import ToolBridge

    artifact = tmp_path / "résultat final.txt"
    artifact.write_text("livrable vérifié", encoding="utf-8")
    bridge = ToolBridge(workspace=str(tmp_path), enable_subagents=False)
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    bot.workspace = str(tmp_path)
    bot.executor = bridge
    bot._init_nemesis()
    context = SimpleNamespace(bot=_FakeBot())

    result = asyncio.run(
        bot._execute_tool(
            "send_file_to_user",
            {"path": str(artifact), "caption": "Voici le document"},
            55,
            context,
        )
    )

    assert result["success"] is True
    assert "envoyé dans ce chat" in result["content"]
    assert len(context.bot.sent_documents) == 1
    sent = context.bot.sent_documents[0]
    assert sent["chat_id"] == 55
    assert sent["filename"] == artifact.name
    assert sent["caption"] == "Voici le document"
    assert sent["content"] == "livrable vérifié".encode("utf-8")
    assert bridge.risk_for("send_file_to_user") == "high"
    assert "send_file_to_user" in bot.registry._tools
    assert "call `send_file_to_user`" in telegram_bot.TELEGRAM_SYSTEM_PROMPT_EXTENSION
    bridge.close()


def test_send_file_tool_rejects_nonexistent_paths(telegram_bot, tmp_path):
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    result = bot._prepare_telegram_file(str(tmp_path / "missing.pdf"))
    assert result["success"] is False
    assert "inaccessible" in result["error"]


def test_image_uploads_are_saved_as_files_and_passed_as_paths_for_data_url_conversion(
    telegram_bot, tmp_path
):
    # A minimal valid one-pixel PNG; the bytes are never placed in the prompt.
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
    )
    bot = telegram_bot.NemesisTelegramBot.__new__(telegram_bot.NemesisTelegramBot)
    photo = SimpleNamespace(file_id="photo-1", file_size=len(png_bytes))
    update = SimpleNamespace(message=SimpleNamespace(photo=[photo]))
    context = SimpleNamespace(bot=_FakeBot(png_bytes))

    files, images, errors = asyncio.run(
        bot._collect_telegram_attachments(
            update,
            context,
            8,
            {"security": {"workspace": str(tmp_path)}},
        )
    )

    assert not errors
    assert len(files) == len(images) == 1
    assert files[0]["is_image"] is True
    assert images[0]["path"] == files[0]["path"]
    assert images[0]["path"].startswith(str(tmp_path))
    prompt = bot._prepend_attachment_instruction("Décris l'image.", files)
    assert "multimodal" in prompt
    assert "89504e" not in prompt
