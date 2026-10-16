import threading
import time

from src.core.subagents import SubagentFleet


class _Response:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call-read",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"sample.txt"}',
                        },
                    }],
                },
            }],
        }


class _FinalResponse:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "Read completed."}}]}


class _WriteResponse:
    status_code = 200
    text = ""

    @staticmethod
    def json():
        return {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call-write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"file_path":"blocked.txt","content":"not written"}',
                        },
                    }],
                },
            }],
        }


class _RateLimitedResponse:
    status_code = 429
    text = "rate limit"
    headers = {"Retry-After": "0.01"}

    @staticmethod
    def json():
        return {"error": {"message": "rate limited"}}


def _wait_for_tasks(fleet, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        tasks = fleet.status()["tasks"]
        if tasks and all(task["status"] in {"completed", "failed", "cancelled"} for task in tasks):
            return tasks
        time.sleep(0.01)
    raise AssertionError(f"Tasks did not finish: {fleet.status()}")


def test_fleet_runs_temporary_tasks_concurrently_and_uses_tools(tmp_path, monkeypatch):
    (tmp_path / "sample.txt").write_text("shared tool access", encoding="utf-8")
    active = 0
    max_active = 0
    authorization_headers = set()
    lock = threading.Lock()

    def fake_post(_url, *, json, headers, **_kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            authorization_headers.add(headers["Authorization"])
        try:
            time.sleep(0.04)
            if any(message.get("role") == "tool" for message in json["messages"]):
                return _FinalResponse()
            return _Response()
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr("src.core.subagents.requests.post", fake_post)
    fleet = SubagentFleet(
        {
            "subagents": {
                "api_key": "test-key",
                "max_concurrent": 2,
                "max_iterations": 4,
            },
        },
        str(tmp_path),
    )
    try:
        submitted = fleet.delegate([
            {"label": "reader one", "instruction": "Read the test file."},
            {"label": "reader two", "instruction": "Read the same test file."},
        ])
        assert submitted["success"] is True
        assert len({task["task_id"] for task in submitted["tasks"]}) == 2

        completed = _wait_for_tasks(fleet)
        assert all(task["status"] == "completed" for task in completed)
        assert all(
            fleet.status(task["task_id"])["tasks"][0]["result"] == "Read completed."
            for task in completed
        )
        assert authorization_headers == {"Bearer test-key"}
        assert max_active == 2
    finally:
        fleet.close()


def test_fleet_reports_missing_key_and_rejects_empty_instructions(tmp_path):
    fleet = SubagentFleet({}, str(tmp_path))
    try:
        assert fleet.delegate([{"instruction": "Run without a key"}])["success"] is False
    finally:
        fleet.close()

    configured = SubagentFleet(
        {"subagents": {"api_key": "test-key"}},
        str(tmp_path),
    )
    try:
        result = configured.delegate([{"instruction": " "}])
        assert result["success"] is False
        assert "instruction" in result["error"]
    finally:
        configured.close()


def test_fleet_rotates_keys_after_429_and_reports_retry_metrics(tmp_path, monkeypatch):
    calls = []

    def fake_post(_url, *, headers, **_kwargs):
        calls.append(headers["Authorization"])
        if headers["Authorization"] == "Bearer first-key":
            return _RateLimitedResponse()
        return _FinalResponse()

    monkeypatch.setattr("src.core.subagents.requests.post", fake_post)
    fleet = SubagentFleet(
        {"subagents": {
            "max_concurrent": 1,
            "max_retries": 2,
            "profiles": [
                {
                    "id": "first",
                    "name": "First",
                    "provider": "groq",
                    "base_url": "https://api.groq.com/openai/v1",
                    "model": "qwen/qwen3.8-27b",
                    "api_key": "first-key",
                },
                {
                    "id": "second",
                    "name": "Second",
                    "provider": "groq",
                    "base_url": "https://api.groq.com/openai/v1",
                    "model": "qwen/qwen3.8-27b",
                    "api_key": "second-key",
                },
            ],
        }},
        str(tmp_path),
    )
    try:
        submitted = fleet.delegate([{"goal": "Summarize the project"}])
        completed = _wait_for_tasks(fleet)
        status = fleet.status(submitted["tasks"][0]["task_id"])["tasks"][0]

        assert completed[0]["status"] == "completed"
        assert status["retry_count"] == 1
        assert status["api_requests"] == 2
        assert status["model"] == "qwen/qwen3.8-27b"
        assert calls == ["Bearer first-key", "Bearer second-key"]
    finally:
        fleet.close()


def test_invalid_profile_falls_back_without_reusing_it(tmp_path, monkeypatch):
    calls = []

    class _Unauthorized:
        status_code = 401
        text = "invalid api key"
        headers = {}

    def fake_post(_url, *, headers, **_kwargs):
        calls.append(headers["Authorization"])
        return _Unauthorized() if headers["Authorization"] == "Bearer bad-key" else _FinalResponse()

    monkeypatch.setattr("src.core.subagents.requests.post", fake_post)
    fleet = SubagentFleet(
        {"subagents": {"profiles": [
            {
                "id": "bad",
                "provider": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "model": "qwen/qwen3.8-27b",
                "api_key": "bad-key",
            },
            {
                "id": "good",
                "provider": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "model": "qwen/qwen3.8-27b",
                "api_key": "good-key",
            },
        ]}},
        str(tmp_path),
    )
    try:
        launched = fleet.delegate([{"instruction": "Summarize the project"}])
        completed = _wait_for_tasks(fleet)
        status = fleet.status(launched["tasks"][0]["task_id"])["tasks"][0]

        assert completed[0]["status"] == "completed"
        assert calls == ["Bearer bad-key", "Bearer good-key"]
        assert status["credential_failovers"] == 1
    finally:
        fleet.close()


def test_task_contract_accepts_aliases_and_explains_missing_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.core.subagents.requests.post",
        lambda *_args, **_kwargs: _FinalResponse(),
    )
    fleet = SubagentFleet({}, str(tmp_path))
    try:
        fleet.profiles = [{
            "id": "configured",
            "api_key": "key",
            "provider": "custom",
            "base_url": "https://api.example.test/v1",
            "model": "unit-test-model",
        }]
        missing = fleet.delegate([{"label": "missing"}])
        assert "instruction" in missing["error"]
        assert "Exemple" in missing["error"]
        accepted = fleet.delegate([{"goal": "Check one thing"}])
        assert accepted["success"] is True
        assert _wait_for_tasks(fleet)[0]["status"] == "completed"
    finally:
        fleet.close()


def test_read_only_task_blocks_write_tools(tmp_path, monkeypatch):
    (tmp_path / "blocked.txt").write_text("original", encoding="utf-8")

    def fake_post(_url, *, json, **_kwargs):
        return _FinalResponse() if any(m.get("role") == "tool" for m in json["messages"]) else _WriteResponse()

    monkeypatch.setattr("src.core.subagents.requests.post", fake_post)
    fleet = SubagentFleet({
        "subagents": {
            "profiles": [{
                "id": "test-profile",
                "provider": "custom",
                "base_url": "https://api.example.test/v1",
                "model": "test-model",
                "api_key": "test-key",
            }],
        },
    }, str(tmp_path))
    try:
        submitted = fleet.delegate([{
            "instruction": "Try to write a file",
            "mode": "read_only",
        }])
        completed = _wait_for_tasks(fleet)
        task = fleet.status(submitted["tasks"][0]["task_id"])["tasks"][0]

        assert completed[0]["status"] == "completed"
        assert (tmp_path / "blocked.txt").read_text(encoding="utf-8") == "original"
        assert task["result"] == "Read completed."
    finally:
        fleet.close()


def test_bridge_returns_launch_ids_and_full_reports_to_coordinator(tmp_path, monkeypatch):
    seen_system_prompts = []

    def fake_post(_url, *, json, **_kwargs):
        seen_system_prompts.append(json["messages"][0]["content"])
        return _FinalResponse()

    monkeypatch.setattr("src.core.subagents.requests.post", fake_post)
    from src.core.tool_bridge import ToolBridge

    bridge = ToolBridge(
        workspace=str(tmp_path),
        config={"subagents": {"api_key": "test-key", "max_iterations": 2}},
    )
    try:
        launch = list(bridge.execute_tool(
            "delegate_tasks",
            {"tasks": [{"label": "Résumé ciblé", "instruction": "Inspecter le projet."}]},
        ))[-1]
        task = launch["tasks"][0]
        assert launch["success"] is True
        assert task["task_id"] in launch["stdout"]
        assert "subagent_status" in launch["stdout"]

        completed = _wait_for_tasks(bridge.subagent_fleet)
        assert completed[0]["status"] == "completed"

        status = list(bridge.execute_tool(
            "subagent_status", {"task_id": task["task_id"]}
        ))[-1]
        assert status["success"] is True
        assert "Read completed." in status["stdout"]
        assert "iron rules" not in seen_system_prompts[0]
        assert "Résumé ciblé" in seen_system_prompts[0]
    finally:
        bridge.close()
