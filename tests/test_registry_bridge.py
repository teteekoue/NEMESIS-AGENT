"""Tests for the ToolRegistry and ToolBridge integration."""
import os
import tempfile
import pytest
from src.core.tool_bridge import ToolBridge
from src.core.agent_tools import create_registry, build_system_prompt, build_feedback

# Core tools that must always be present. New tools may be added freely.
EXPECTED_TOOLS = {
    "read_file", "edit", "bash", "grep", "list_dir", "web_search",
    "web_fetch", "write_file", "delete_file", "get_task_output", "kill_task",
    "mcp_list", "mcp_tools_list", "mcp_call",
}


class TestToolRegistry:
    def test_create_registry(self, tmp_path):
        reg = create_registry(str(tmp_path))
        assert len(reg) >= len(EXPECTED_TOOLS)
        assert "read_file" in reg
        assert "edit" in reg
        assert "bash" in reg
        assert "grep" in reg
        assert "list_dir" in reg
        assert "web_search" in reg
        assert "web_fetch" in reg
        assert "write_file" in reg
        assert "delete_file" in reg
        assert "get_task_output" in reg
        assert "kill_task" in reg
        assert "mcp_list" in reg
        assert "mcp_tools_list" in reg
        assert "mcp_call" in reg

    def test_get_openai_functions(self, tmp_path):
        reg = create_registry(str(tmp_path))
        funcs = reg.get_openai_functions()
        assert len(funcs) == len(reg)
        for f in funcs:
            assert f["type"] == "function"
            assert "name" in f["function"]
            assert "description" in f["function"]
            assert "parameters" in f["function"]

    def test_tool_for_kind(self, tmp_path):
        reg = create_registry(str(tmp_path))
        from src.core.tool_kind import ToolKind
        assert reg.tool_for_kind(ToolKind.READ) == "read_file"
        assert reg.tool_for_kind(ToolKind.EDIT) == "edit"
        assert reg.tool_for_kind(ToolKind.EXECUTE) == "bash"
        assert reg.tool_for_kind(ToolKind.SEARCH) == "grep"
        assert reg.tool_for_kind(ToolKind.LIST_DIR) == "list_dir"

    def test_build_system_prompt(self, tmp_path):
        reg = create_registry(str(tmp_path))
        prompt = build_system_prompt(reg)
        assert "NEMESIS" in prompt
        assert "read_file" in prompt
        assert "edit" in prompt
        assert "bash" in prompt
        assert "grep" in prompt
        assert "list_dir" in prompt
        assert "Available Tools" in prompt


class TestToolBridge:
    def test_create_bridge(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        assert tb.get_system_prompt() is not None
        assert len(tb.get_openai_tools()) == len(tb.registry)

    def test_execute_read_file(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("content")
        tb = ToolBridge(workspace=str(tmp_path))
        results = list(tb.execute_tool("read_file", {"path": "data.txt", "limit": 1}))
        non_partial = [r for r in results if "partial" not in r]
        assert len(non_partial) == 1
        assert non_partial[0]["success"] is True

    def test_execute_bash(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        results = list(tb.execute_tool("bash", {"command": "echo test_output", "description": "test"}))
        non_partial = [r for r in results if "partial" not in r]
        assert len(non_partial) == 1
        assert non_partial[0]["success"] is True
        assert "test_output" in non_partial[0]["stdout"]

    def test_execute_unknown_tool(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        results = list(tb.execute_tool("nonexistent_tool", {}))
        non_partial = [r for r in results if "partial" not in r]
        assert len(non_partial) == 1
        assert non_partial[0]["success"] is False

    def test_get_system_prompt(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        prompt = tb.get_system_prompt()
        assert len(prompt) > 1000
        assert "NEMESIS" in prompt

    def test_get_openai_tools(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        tools = tb.get_openai_tools()
        tool_names = [t["function"]["name"] for t in tools]
        assert "read_file" in tool_names
        assert "bash" in tool_names
        assert "grep" in tool_names


class TestBuildFeedback:
    def test_read_result(self, tmp_path):
        from src.core.tools.read_file import read_file
        f = tmp_path / "x.txt"
        f.write_text("hello")
        result = read_file(str(tmp_path), path=str(f))
        feedback = build_feedback("read_file", result)
        assert "read_file" in feedback
        assert "hello" in feedback

    def test_bash_result(self, tmp_path):
        from src.core.tools.bash import run_bash, BashInput
        result = run_bash(BashInput(command="echo ok"), str(tmp_path))
        feedback = build_feedback("bash", result)
        assert "bash" in feedback
        assert "ok" in feedback
