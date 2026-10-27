"""Les skills intégrés doivent être les originaux GitHub, pas des résumés."""
from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core import paths
from src.core.skills_manager import SkillManager

LIBRARY = pathlib.Path(__file__).resolve().parents[1] / "tools_library"
FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

EXPECTED = {
    "brainstorming": 250,
    "systematic-debugging": 250,
    "verification-before-completion": 100,
    "git-workflow": 150,
    "frontend-design": 60,
    "cybersecurity-audit": 180,
    "unit-converter": 15,
}


def _frontmatter(name: str):
    text = (LIBRARY / name / "SKILL.md").read_text(encoding="utf-8")
    match = FM.match(text)
    assert match, f"frontmatter manquant pour {name}"
    return yaml.safe_load(match.group(1)), text[match.end():]


def test_all_bundled_skills_have_full_original_content():
    """Les anciennes versions réécrites faisaient 19 à 24 lignes."""
    for name, minimum in EXPECTED.items():
        meta, body = _frontmatter(name)
        lines = len((LIBRARY / name / "SKILL.md").read_text(encoding="utf-8").splitlines())
        assert lines >= minimum, f"{name} semble encore être un résumé ({lines} lignes)"
        assert len(body.strip()) > 300, f"{name} : corps trop court"


def test_skill_metadata_has_provenance_and_keeps_nemesis_names():
    manager = SkillManager(str(LIBRARY))
    installed = {skill["name"]: skill for skill in manager.list_installed()}
    assert set(installed) == set(EXPECTED)
    for name, skill in installed.items():
        meta, _ = _frontmatter(name)
        assert meta["name"] == name, "le nom NEMESIS doit rester stable pour les réglages"
        assert meta["nemesis_builtin"] is True
        assert str(meta["source"]).startswith("https://github.com/")
        assert meta["source_license"]
        assert str(skill["description"]).strip()
        assert skill["source"] == meta["source"]


def test_supporting_files_are_bundled():
    for relative in (
        "brainstorming/scripts/helper.js",
        "brainstorming/visual-companion.md",
        "systematic-debugging/root-cause-tracing.md",
        "systematic-debugging/defense-in-depth.md",
        "frontend-design/LICENSE.txt",
    ):
        assert (LIBRARY / relative).is_file(), f"fichier compagnon manquant : {relative}"
    assert (LIBRARY / "ATTRIBUTION.md").is_file()


def test_cybersecurity_skill_does_not_wall_off_the_agent():
    _, body = _frontmatter("cybersecurity-audit")
    obsolete = (
        "Cadrage obligatoire",
        "N’obéis pas aux instructions",
        "Réponse lorsque la demande dépasse ce cadre",
        "Ne lance pas de tests de déni de service",
        "Niveau 3 — validation contrôlée",
    )
    for sentence in obsolete:
        assert sentence not in body, f"ancienne restriction encore présente : {sentence}"
    assert "OWASP" in body
    assert "SECURITY_AUDIT.md" in body
    assert "FIX" in body  # l'agent corrige et vérifie, il n'est pas bridé


def test_all_skills_fit_in_the_system_prompt_budget():
    total = sum(len(_frontmatter(name)[1].strip()) for name in EXPECTED)
    assert total < 100_000, f"tous les skills activés dépasseraient la limite ({total})"


def _seed_case(tmp_path: pathlib.Path):
    source = tmp_path / "bundled"
    destination = tmp_path / "user"
    skill = source / "brainstorming"
    skill.mkdir(parents=True)
    (destination / "brainstorming").mkdir(parents=True)
    return source, destination, skill


def test_seed_refreshes_rewritten_builtin_copies(tmp_path):
    source, destination, skill = _seed_case(tmp_path)
    authentic = "---\nname: brainstorming\nnemesis_builtin: true\n---\n\nAUTHENTIC ORIGINAL\n"
    (skill / "SKILL.md").write_text(authentic, encoding="utf-8")
    (skill / "reference.md").write_text("compagnon", encoding="utf-8")
    (destination / "brainstorming" / "SKILL.md").write_text(
        "---\nname: brainstorming\nnemesis_builtin: true\n---\n\nPETIT RÉSUMÉ\n", encoding="utf-8"
    )

    paths._seed_bundled_skills(source, destination)

    assert "AUTHENTIC ORIGINAL" in (destination / "brainstorming" / "SKILL.md").read_text()
    assert (destination / "brainstorming" / "reference.md").read_text() == "compagnon"


def test_seed_never_overwrites_user_customised_skill(tmp_path):
    source, destination, skill = _seed_case(tmp_path)
    (skill / "SKILL.md").write_text(
        "---\nname: brainstorming\nnemesis_builtin: true\n---\n\nAUTHENTIC ORIGINAL\n", encoding="utf-8"
    )
    custom = "---\nname: brainstorming\n---\n\nMON GUIDE PERSONNALISÉ\n"
    (destination / "brainstorming" / "SKILL.md").write_text(custom, encoding="utf-8")

    paths._seed_bundled_skills(source, destination)

    assert (destination / "brainstorming" / "SKILL.md").read_text() == custom


def test_seed_installs_missing_skill_tree(tmp_path):
    source = tmp_path / "bundled"
    destination = tmp_path / "user"
    skill = source / "brainstorming"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: brainstorming\nnemesis_builtin: true\n---\n\nORIGINAL\n", encoding="utf-8"
    )
    (skill / "scripts" / "helper.js").write_text("// ok", encoding="utf-8")
    destination.mkdir()

    paths._seed_bundled_skills(source, destination)

    assert (destination / "brainstorming" / "SKILL.md").is_file()
    assert (destination / "brainstorming" / "scripts" / "helper.js").is_file()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_skill_body_is_verbatim_from_its_declared_source(name):
    """Le marqueur NEMESIS ne doit pas réintroduire de résumé dans le corps."""
    meta, body = _frontmatter(name)
    assert meta["version"]
    assert body.lstrip().startswith("#"), f"{name} : corps original sans titre"


def test_enabled_skill_is_injected_into_the_real_system_prompt(tmp_path, monkeypatch):
    from src.core import agent_tools, paths

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.safe_dump({"skills": {"enabled": ["cybersecurity-audit"]}}), encoding="utf-8"
    )
    monkeypatch.setattr(paths, "config_path", lambda: config_file)
    monkeypatch.setattr(paths, "tools_library_path", lambda: LIBRARY)

    class FakeRenderer:
        def render(self, template):
            return template.replace("{{TOOLS_CATALOG}}", "- read_file")

    class FakeRegistry:
        def get_renderer(self):
            return FakeRenderer()

        def __iter__(self):
            return iter([])

    prompt = agent_tools.build_system_prompt(FakeRegistry())

    assert "## Compétences sélectionnées" in prompt
    assert "# Cybersecurity Audit Expert" in prompt
    assert "SECURITY_AUDIT.md" in prompt
    assert "Cadrage obligatoire" not in prompt
