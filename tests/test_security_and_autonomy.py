"""Integration tests for the security hardening and autonomy additions."""

import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.tool_bridge import ToolBridge
from action_parser import ActionParser


class TestPathConfinement:
    def test_paths_list_escape_rejected(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        err = tb._check_paths("read_file", {"paths": ["/etc/passwd"]})
        assert err is not None

    def test_relative_escape_rejected(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        assert tb._check_paths("read_file", {"path": "../../etc/passwd"}) is not None

    def test_inside_path_allowed(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        assert tb._check_paths("read_file", {"path": "src/app.py"}) is None

    def test_patch_absolute_target_rejected(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        patch = "--- a/x\n+++ /etc/evil\n@@ -1 +1 @@\n-a\n+b\n"
        assert tb._check_paths("apply_patch", {"patch": patch}) is not None

    def test_patch_relative_escape_rejected(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        patch = "--- a/x\n+++ ../../outside\n@@ -1 +1 @@\n-a\n+b\n"
        assert tb._check_paths("apply_patch", {"patch": patch}) is not None

    def test_patch_inside_allowed(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        patch = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
        assert tb._check_paths("apply_patch", {"patch": patch}) is None


class TestParserRegistrySync:
    def test_agent_status_is_valid(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))
        parser = ActionParser.from_registry(tb.registry)
        r = parser.parse('{"tool":"agent_status","parameters":{}}')
        assert r["action"]["type"] == "agent_status"

    def test_kill_process_alias_maps_to_kill_task(self):
        parser = ActionParser()
        r = parser.parse('{"tool":"kill_process","parameters":{"pid":"1"}}')
        assert r["action"]["type"] == "kill_task"


class TestParserNonDestructive:
    def test_url_not_corrupted_by_comment_repair(self):
        parser = ActionParser()
        raw = '{tool: "write_file", parameters: {file_path: "u.txt", content: "http://example.com/a"}}'
        r = parser.parse(raw)
        assert r["action"]["content"]["content"] == "http://example.com/a"

    def test_single_quoted_code_preserved(self):
        parser = ActionParser()
        raw = "{'tool': 'bash', 'parameters': {'command': 'echo \"a,b\"'}}"
        r = parser.parse(raw)
        assert r["action"]["content"]["command"] == 'echo "a,b"'


class TestMemoryTool:
    def test_set_search_get_delete(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))

        def run(params):
            return list(tb.execute_tool("memory", params))[-1]

        saved = run({"action": "set", "content": "convention: ruff", "tags": ["style"]})
        assert saved["success"] is True
        mid = saved["memory"]["id"]

        found = run({"action": "search", "query": "ruff"})
        assert found["success"] is True
        assert any("ruff" in m["content"] for m in found["memories"])

        got = run({"action": "get", "id": mid})
        assert got["success"] is True

        deleted = run({"action": "delete", "id": mid})
        assert deleted["success"] is True
        assert run({"action": "get", "id": mid})["success"] is False


class TestTodoSingleActive:
    def test_only_one_in_progress(self, tmp_path):
        tb = ToolBridge(workspace=str(tmp_path))

        def run(params):
            return list(tb.execute_tool("todo", params))[-1]

        a = run({"action": "add", "content": "first"})
        b = run({"action": "add", "content": "second"})
        id_a = a["items"][0]["id"]
        id_b = b["items"][1]["id"]
        run({"action": "update", "id": id_a, "status": "in_progress"})
        run({"action": "update", "id": id_b, "status": "in_progress"})
        final = run({"action": "list"})
        active = [t for t in final["items"] if t["status"] == "in_progress"]
        assert len(active) == 1
        assert active[0]["id"] == id_b


class TestBashStreamingTimeout:
    def test_silent_hanging_command_times_out(self, tmp_path):
        from src.core.tools.bash import run_bash_streamed

        start = time.time()
        last = None
        for update in run_bash_streamed("sleep 30", str(tmp_path), timeout_ms=800):
            last = update
        assert last is not None
        assert last.get("signal") == "timeout"
        assert time.time() - start < 5

    def test_normal_streamed_command(self, tmp_path):
        from src.core.tools.bash import run_bash_streamed

        last = None
        for update in run_bash_streamed("echo hi", str(tmp_path), timeout_ms=5000):
            last = update
        assert last["success"] is True
        assert "hi" in last["output"]


class TestAuthSignatureScoping:
    def test_signature_differs_for_different_params(self):
        from agent import NemesisApp

        s1 = NemesisApp._auth_signature("bash", {"command": "echo hi"})
        s2 = NemesisApp._auth_signature("bash", {"command": "rm -rf /"})
        assert s1 != s2

    def test_signature_stable_for_same_params(self):
        from agent import NemesisApp

        s1 = NemesisApp._auth_signature("bash", {"command": "echo hi", "x": 1})
        s2 = NemesisApp._auth_signature("bash", {"x": 1, "command": "echo hi"})
        assert s1 == s2