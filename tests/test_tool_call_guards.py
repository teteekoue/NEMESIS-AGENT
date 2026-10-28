"""Garde-fous d'appel d'outils : taille, JSON strict, append, feedback corrélé.

Ces tests couvrent les problèmes remontés dans le rapport utilisateur :
troncature silencieuse de write_file, feedback décalé, DSML, heredoc et
paramètres read_file. Aucun provider externe n'est contacté.
"""

import json
import os

import pytest

from action_parser import ActionParser
from src.core.agent_tools import (
    build_feedback_message,
    build_system_prompt,
    create_registry,
    format_tool_feedback,
    protocol_error_feedback,
)
from src.core.system_prompt import (
    DSML_PROTOCOL_ERROR,
    TOOL_CALL_CHAR_BUDGET,
    TOOL_CALL_HARD_LIMIT,
    TRUNCATED_PROTOCOL_ERROR,
)
from src.core.tool_bridge import ToolBridge


@pytest.fixture
def bridge(tmp_path):
    return ToolBridge(workspace=str(tmp_path), enable_subagents=False)


def _last(generator):
    result = {}
    for update in generator:
        if "partial" not in update:
            result = update
    return result


# ---------------------------------------------------------------------------
# append : disponible partout
# ---------------------------------------------------------------------------


def test_append_is_a_first_class_tool(tmp_path):
    registry = create_registry(str(tmp_path))
    assert "append" in registry
    names = {f["function"]["name"] for f in registry.get_openai_functions()}
    assert "append" in names
    prompt = build_system_prompt(registry)
    assert "**append**" in prompt
    assert "**write_file**" in prompt


def test_prompt_teaches_all_guards(tmp_path):
    prompt = build_system_prompt(create_registry(str(tmp_path)))
    assert str(TOOL_CALL_CHAR_BUDGET) in prompt
    assert str(TOOL_CALL_HARD_LIMIT) in prompt
    assert "DSML" in prompt
    assert "<tool_call>" in prompt
    assert "heredoc" in prompt.lower()
    assert "call #N" in prompt
    assert "{{TOOLS_CATALOG}}" not in prompt
    assert "{{TOOL_CALL_BUDGET}}" not in prompt


def test_cli_web_telegram_use_the_same_generated_prompt(tmp_path):
    # CLI et Web passent par ToolBridge.get_system_prompt(), Telegram par
    # build_system_prompt() directement : les deux doivent exposer append.
    bridge_prompt = ToolBridge(
        workspace=str(tmp_path), enable_subagents=False
    ).get_system_prompt()
    telegram_prompt = build_system_prompt(create_registry(str(tmp_path)))
    for prompt in (bridge_prompt, telegram_prompt):
        assert "**append**" in prompt
        assert "DSML" in prompt
        assert str(TOOL_CALL_CHAR_BUDGET) in prompt


def test_telegram_source_uses_generated_prompt_and_native_append():
    """Le bot ne doit plus rediriger append vers un outil inexistant."""
    source = open("telegram_bot.py", encoding="utf-8").read()
    assert "build_system_prompt(self.registry)" in source
    assert "'append': 'append_file'" not in source
    assert "'append_file': 'append'" in source
    assert "ActionParser(strict=True)" in source


@pytest.mark.parametrize("surface", ["agent.py", "web/backend/agent_runner.py", "telegram_bot.py"])
def test_all_loops_enable_strict_parser(surface):
    source = open(surface, encoding="utf-8").read()
    assert "strict=True" in source
    assert "protocol_error_feedback" in source
    assert "build_feedback_message" in source


# ---------------------------------------------------------------------------
# append : comportement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path_key", ["path", "file_path", "filename"])
def test_append_accepts_every_path_alias(bridge, tmp_path, path_key):
    target = tmp_path / "chunks.txt"
    first = _last(bridge.execute_tool("write_file", {"path": str(target), "content": "un-"}))
    second = _last(bridge.execute_tool("append", {path_key: str(target), "content": "deux"}))
    assert first["success"] and second["success"]
    assert target.read_text() == "un-deux"
    assert second["bytes_written"] == 4
    assert second["total_bytes"] == 7


def test_append_creates_file_and_parent_directories(bridge, tmp_path):
    target = tmp_path / "nouveau" / "profond" / "f.txt"
    result = _last(bridge.execute_tool("append", {"path": str(target), "content": "bloc"}))
    assert result["success"]
    assert target.read_text() == "bloc"
    assert result["total_bytes"] == 4


def test_append_alias_is_supported(bridge, tmp_path):
    target = tmp_path / "alias.txt"
    _last(bridge.execute_tool("write_file", {"path": str(target), "content": "A"}))
    result = _last(bridge.execute_tool("append_file", {"path": str(target), "content": "B"}))
    assert result["success"]
    assert target.read_text() == "AB"


def test_append_requires_content_and_path(bridge, tmp_path):
    target = tmp_path / "vide.txt"
    no_content = _last(bridge.execute_tool("append", {"path": str(target)}))
    no_path = _last(bridge.execute_tool("append", {"content": "x"}))
    assert not no_content["success"]
    assert not no_path["success"]
    assert not target.exists()


def test_long_file_written_in_three_chunks(bridge, tmp_path):
    target = tmp_path / "long.md"
    chunks = ["ligne 1\n", "ligne 2\n", "ligne 3\n"]
    _last(bridge.execute_tool("write_file", {"path": str(target), "content": chunks[0]}))
    for chunk in chunks[1:]:
        result = _last(bridge.execute_tool("append", {"path": str(target), "content": chunk}))
        assert result["success"]
    assert target.read_text() == "".join(chunks)
    assert os.path.getsize(target) == len("".join(chunks).encode("utf-8"))


def test_write_verification_detects_a_size_mismatch(bridge, tmp_path, monkeypatch):
    target = tmp_path / "verif.txt"
    real_getsize = os.path.getsize

    def fake_getsize(path):
        if str(path) == str(target):
            return 1
        return real_getsize(path)

    monkeypatch.setattr("src.core.agent_tools.os.path.getsize", fake_getsize)
    result = _last(bridge.execute_tool("write_file", {"path": str(target), "content": "un long contenu"}))
    assert not result["success"]
    assert "verification failed" in str(result["stdout"]).lower()


def test_legacy_append_mapping_still_works(bridge, tmp_path):
    """L'ancien chemin `__APPEND__:` reste compatible."""
    from src.core.agent_tools import _write_file

    target = tmp_path / "legacy.txt"
    _write_file(str(target), "A")
    _write_file(str(target), "__APPEND__:B")
    assert target.read_text() == "AB"


# ---------------------------------------------------------------------------
# Garde-fou de taille
# ---------------------------------------------------------------------------


def test_hard_limit_refuses_oversized_call_without_writing(bridge, tmp_path):
    target = tmp_path / "trop_long.txt"
    result = _last(
        bridge.execute_tool(
            "write_file",
            {"path": str(target), "content": "x" * (TOOL_CALL_HARD_LIMIT + 100)},
        )
    )
    assert not result["success"]
    assert "PROTOCOL ERROR" in result["stdout"]
    assert "append" in result["stdout"]
    assert not target.exists()


def test_hard_limit_applies_to_all_tools(bridge, tmp_path):
    huge = "x" * (TOOL_CALL_HARD_LIMIT + 100)
    for name, params in (
        ("bash", {"command": huge}),
        ("edit", {"file_path": "a.txt", "old_string": "a", "new_string": huge}),
        ("append", {"path": "a.txt", "content": huge}),
        ("apply_patch", {"patch": huge}),
    ):
        result = _last(bridge.execute_tool(name, params))
        assert not result["success"], name
        assert "PROTOCOL ERROR" in result["stdout"], name


def test_call_just_under_the_limit_is_accepted(bridge, tmp_path):
    target = tmp_path / "ok.txt"
    content = "y" * (TOOL_CALL_CHAR_BUDGET - 150)
    result = _last(bridge.execute_tool("write_file", {"path": str(target), "content": content}))
    assert result["success"]
    assert target.read_text() == content


def test_size_is_measured_in_characters_not_bytes(bridge, tmp_path):
    target = tmp_path / "utf8.txt"
    content = "é" * (TOOL_CALL_CHAR_BUDGET - 150)
    result = _last(bridge.execute_tool("write_file", {"path": str(target), "content": content}))
    assert result["success"]
    assert target.read_text() == content


def test_invalid_arguments_are_not_silently_executed(bridge, tmp_path):
    target = tmp_path / "raw.txt"
    result = _last(
        bridge.execute_tool("write_file", {"value": '{"path": "%s", "content": "coupe' % target})
    )
    assert not result["success"]
    assert "tronqu" in result["stdout"].lower()
    assert "append" in result["stdout"]
    assert not target.exists()


# ---------------------------------------------------------------------------
# Parseur strict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '<|DSML|>{"tool": "read_file", "parameters": {"path": "a.txt"}}',
        '<DSML>{"tool": "bash", "parameters": {"command": "ls"}}</DSML>',
        "<tool_call><function=bash><parameter=command>ls</parameter></tool_call>",
        '<function=write_file>{"path": "a.txt"}</function>',
    ],
)
def test_strict_parser_rejects_dsml_and_xml_dialects(raw):
    parsed = ActionParser(strict=True).parse(raw)
    assert parsed["protocol_error"] == DSML_PROTOCOL_ERROR
    assert parsed["actions"] == []
    assert parsed["action"] is None


@pytest.mark.parametrize(
    "raw",
    [
        '```json\n{"tool": "write_file", "parameters": {"path": "a.txt", "content": "coupe',
        '```json\n[{"tool": "read_file", "parameters": {"path": "a.txt"}}, {"tool": "bash"',
        '```\n{"tool": "write_file", "parameters": {"content": "texte sans fin',
        # Sans fence : c'est exactement le cas qui ecrivait un fichier partiel.
        'J\'écris le fichier.\n{"tool": "write_file", "parameters": {"path": "a.txt", "content": "jamais fini',
        '[{"tool": "read_file", "parameters": {"path": "a.txt"}}, {"tool": "bash"',
    ],
)
def test_strict_parser_rejects_truncated_json(raw):
    parsed = ActionParser(strict=True).parse(raw)
    assert parsed["protocol_error"] == TRUNCATED_PROTOCOL_ERROR
    assert parsed["actions"] == []


def test_strict_parser_accepts_valid_json_call():
    raw = 'Je lis le fichier.\n```json\n{"tool": "read_file", "parameters": {"path": "a.txt"}}\n```'
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert len(parsed["actions"]) == 1
    assert parsed["actions"][0]["type"] == "read_file"
    assert parsed["actions"][0]["content"]["path"] == "a.txt"


def test_strict_parser_accepts_bare_json_with_french_prose():
    payload = json.dumps(
        {"tool": "write_file", "parameters": {"path": "a.txt", "content": "l'ami d'Élodie\n"}},
        ensure_ascii=False,
    )
    raw = "J'écris le fichier.\n" + payload + "\nIl a dit \"oui\"."
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert len(parsed["actions"]) == 1
    assert parsed["actions"][0]["content"]["content"] == "l'ami d'Élodie\n"


def test_strict_parser_accepts_valid_bare_batch():
    raw = json.dumps(
        [
            {"tool": "read_file", "parameters": {"path": "a.txt"}},
            {"tool": "bash", "parameters": {"command": "echo ok"}},
        ]
    )
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert len(parsed["actions"]) == 2


def test_strict_parser_accepts_valid_json_batch():
    raw = (
        "```json\n"
        '[{"tool": "read_file", "parameters": {"path": "a.txt"}}, '
        '{"tool": "read_file", "parameters": {"path": "b.txt"}}]\n'
        "```"
    )
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert len(parsed["actions"]) == 2


def test_strict_parser_accepts_append_call():
    raw = '```json\n{"tool": "append", "parameters": {"path": "a.txt", "content": "bloc"}}\n```'
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert parsed["actions"][0]["type"] == "append"


def test_strict_parser_normalises_append_file_alias():
    raw = '```json\n{"tool": "append_file", "parameters": {"path": "a.txt", "content": "x"}}\n```'
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert parsed["actions"][0]["type"] == "append"


def test_strict_parser_rejects_raw_argument_fallback():
    raw = '```json\n{"tool": "write_file", "parameters": {"value": "du texte brut}}\n```'
    parsed = ActionParser(strict=True).parse(raw)
    assert parsed.get("protocol_error")
    assert parsed["actions"] == []


def test_strict_parser_keeps_prose_with_unfinished_code_fence():
    raw = "Voici un exemple :\n```python\nprint('bonjour')\n"
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert parsed["actions"] == []
    assert "Voici un exemple" in parsed["text"]


def test_permissive_parser_keeps_legacy_xml_support():
    """Les autres providers gardent leur tolérance historique."""
    raw = "<tool_call><function=bash><parameter=command>echo ok</parameter></tool_call>"
    parsed = ActionParser().parse(raw)
    assert not parsed.get("protocol_error")
    assert any(action["type"] == "bash" for action in parsed["actions"])


def test_feedback_passthrough_is_not_flagged():
    raw = "FEEDBACK:\n[call #2 — 1 tool(s)]\n\nTool: bash\nSuccess: true"
    parsed = ActionParser(strict=True).parse(raw)
    assert not parsed.get("protocol_error")
    assert parsed["actions"] == []


# ---------------------------------------------------------------------------
# Feedback corrélé
# ---------------------------------------------------------------------------


def test_feedback_message_has_monotonic_marker():
    first = build_feedback_message(["Tool: bash\nSuccess: true"], 1)
    second = build_feedback_message(["Tool: bash\nSuccess: true"], 2)
    assert first.startswith("FEEDBACK:\n[call #1 — 1 tool(s)]")
    assert second.startswith("FEEDBACK:\n[call #2 — 1 tool(s)]")
    assert first != second


def test_feedback_message_lists_every_tool():
    message = build_feedback_message(
        ["Tool: read_file\nSuccess: true", "Tool: grep\nSuccess: true"], 7
    )
    assert "[call #7 — 2 tool(s)]" in message
    assert "Tool: read_file" in message
    assert "Tool: grep" in message


def test_protocol_error_feedback_is_actionable():
    message = protocol_error_feedback(TRUNCATED_PROTOCOL_ERROR, 3)
    assert message.startswith("FEEDBACK:\n[call #3 — protocol error]")
    assert "Success: false" in message
    assert "append" in message


def test_format_tool_feedback_exposes_bytes():
    result = {"success": True, "stdout": "ok", "total_bytes": 12}
    assert "total_bytes" not in result["stdout"]
    # Le message de write_file contient déjà les compteurs dans stdout.
    feedback = format_tool_feedback("append", {"success": True, "stdout": "bytes_written: 3, total_bytes: 12"})
    assert "total_bytes: 12" in feedback


def test_feedback_blocks_are_distinguishable_even_with_same_output():
    """Deux appels identiques ne produisent pas deux feedbacks ambigus."""
    payload = "Tool: bash\nSuccess: true\nOutput:\nok"
    a = build_feedback_message([payload], 4)
    b = build_feedback_message([payload], 5)
    assert a != b
    assert "[call #4" in a and "[call #5" in b


# ---------------------------------------------------------------------------
# Alias de paramètres (read_file)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"file": "a.txt"},
        {"target_file": "a.txt"},
        {"filename": "a.txt"},
        {"file_path": "a.txt"},
        {"path": "a.txt"},
    ],
)
def test_read_file_accepts_common_path_aliases(bridge, tmp_path, params):
    target = tmp_path / "a.txt"
    target.write_text("contenu")
    arguments = dict(params)
    arguments[list(params)[0]] = str(target)
    result = _last(bridge.execute_tool("read_file", arguments))
    assert result["success"], result.get("stdout")
    assert "contenu" in str(result.get("stdout") or result.get("output") or "")


def test_read_file_accepts_files_list_alias(bridge, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("contenu")
    result = _last(bridge.execute_tool("read_file", {"files": [str(target)]}))
    assert result["success"], result.get("stdout")


def test_legacy_schema_validates_append(tmp_path):
    from tools_schema import validate_tool_call

    ok, error = validate_tool_call("append", {"path": "a.txt", "content": "x"})
    assert ok, error
    ok, error = validate_tool_call("append", {"path": "a.txt"})
    assert not ok
    assert "content" in error
    ok, error = validate_tool_call("append_file", {"path": "a.txt", "content": "x"})
    assert ok, error


def test_tools_schema_exposes_append_to_every_surface():
    from tools_schema import get_all_tool_names

    names = get_all_tool_names()
    assert "append" in names
    assert "append_file" in names
