import io
import stat

import pytest
import yaml

from src.core.skills_manager import SkillManager
from src.core.mcp_manager import MCPManager
from src.core import paths
from src.core.agent_tools import build_system_prompt, create_registry
from web.backend import config_store, settings


SKILL_CONTENT = """---
name: test-guide
description: A concise guide for testing skill activation.
version: 1.0.0
---

Follow the test guide when it is selected.
"""


def test_skills_are_disabled_until_selected(monkeypatch, tmp_path):
    library = tmp_path / "skills"
    manager = SkillManager(str(library))
    assert manager.install_skill_content(SKILL_CONTENT)[0]

    raw_config = {}
    monkeypatch.setattr(paths, "tools_library_path", lambda: library)
    monkeypatch.setattr(config_store, "load_config", lambda: raw_config)
    monkeypatch.setattr(config_store, "save_config", lambda config: raw_config.update(config) or config)

    assert settings.list_skills()[0]["enabled"] is False
    skills = settings.set_skill_enabled("test-guide", True)
    assert skills[0]["enabled"] is True
    assert raw_config["skills"]["enabled"] == ["test-guide"]


def test_active_skill_is_included_in_initial_system_prompt(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    library = tmp_path / "skills"
    config_file.write_text(yaml.safe_dump({"skills": {"enabled": ["test-guide"]}}), encoding="utf-8")
    SkillManager(str(library)).install_skill_content(SKILL_CONTENT)
    monkeypatch.setattr(paths, "config_path", lambda: config_file)
    monkeypatch.setattr(paths, "tools_library_path", lambda: library)

    prompt = build_system_prompt(create_registry(str(tmp_path)))

    assert "## Compétences sélectionnées" in prompt
    assert "Follow the test guide when it is selected." in prompt
    assert "name: test-guide" not in prompt


def test_import_skill_from_github_only_fetches_allowlisted_raw_file(monkeypatch, tmp_path):
    import requests

    raw_url = "https://raw.githubusercontent.com/example/project/main/skills/test-guide/SKILL.md"
    monkeypatch.setattr(paths, "tools_library_path", lambda: tmp_path)
    monkeypatch.setattr(requests, "get", lambda url, **kwargs: _FakeResponse(SKILL_CONTENT.encode()))

    result = settings.import_skill_from_github(
        "https://github.com/example/project/blob/main/skills/test-guide/SKILL.md"
    )

    assert result["source"].startswith("https://github.com/")
    assert (tmp_path / "test-guide" / "SKILL.md").is_file()
    with pytest.raises(ValueError, match="Seules les URL"):
        settings.import_skill_from_github("https://example.com/SKILL.md")


class _FakeResponse:
    status_code = 200
    is_redirect = False

    def __init__(self, content):
        self.raw = io.BytesIO(content)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def raise_for_status(self):
        return None


def test_mcp_env_settings_are_private_and_list_only_names(monkeypatch, tmp_path):
    config_file = tmp_path / "mcp_config.yaml"
    monkeypatch.setattr(paths, "mcp_config_path", lambda: config_file)

    manager = MCPManager(str(config_file))
    ok, _message = manager.add_server(
        "private-server",
        "node",
        args=["server.js"],
        env={"ACCESS_TOKEN": "secret-value"},
    )

    listed = settings.list_mcp_servers()
    assert ok
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
    assert listed[0]["env"] == ["ACCESS_TOKEN"]
    assert "secret-value" not in str(listed)


def test_bundled_skills_are_seeded_without_overwriting_user_content(tmp_path):
    bundled = tmp_path / "bundled"
    user_library = tmp_path / "user"
    bundled_skill = bundled / "example"
    bundled_skill.mkdir(parents=True)
    (bundled_skill / "SKILL.md").write_text(
        "---\nname: example\nnemesis_builtin: true\n---\nDefault content\n",
        encoding="utf-8",
    )
    user_skill = user_library / "example"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("User version\n", encoding="utf-8")

    paths._seed_bundled_skills(bundled, user_library)

    assert (user_skill / "SKILL.md").read_text(encoding="utf-8") == "User version\n"
