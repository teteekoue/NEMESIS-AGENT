"""Tests for streamed bash execution: cancel, timeout, silent commands."""

import threading
import time

from src.core.tools.bash import (
    run_bash_streamed,
    timeout_seconds_to_ms,
    _DEFAULT_TIMEOUT_S,
    _MIN_TIMEOUT_S,
    _MAX_TIMEOUT_S,
)


def _collect(gen):
    partials = []
    final = None
    for update in gen:
        if "partial" in update:
            partials.append(update["partial"])
        else:
            final = update
    return partials, final


def test_normal_command_streams_output(tmp_path):
    partials, final = _collect(
        run_bash_streamed("echo hello; echo world", str(tmp_path), timeout_ms=5000)
    )
    assert partials == ["hello", "world"]
    assert final["success"] is True
    assert final["exit_code"] == 0


def test_silent_stdin_command_does_not_hang(tmp_path):
    start = time.time()
    _, final = _collect(
        run_bash_streamed("read -p 'Password: ' x; echo done", str(tmp_path), timeout_ms=10000)
    )
    assert time.time() - start < 5
    assert final["success"] is True


def test_timeout_on_silent_command(tmp_path):
    start = time.time()
    _, final = _collect(run_bash_streamed("sleep 30", str(tmp_path), timeout_ms=800))
    assert time.time() - start < 5
    assert final["success"] is False
    assert final["signal"] == "timeout"


def test_cancel_stops_silent_command(tmp_path):
    cancel = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.4), cancel.set()), daemon=True).start()
    start = time.time()
    _, final = _collect(
        run_bash_streamed("sleep 30", str(tmp_path), timeout_ms=60000, cancel_event=cancel)
    )
    assert time.time() - start < 5
    assert final["success"] is False
    assert final["signal"] == "interrupted"


def test_cancel_after_partial_output(tmp_path):
    cancel = threading.Event()
    threading.Thread(target=lambda: (time.sleep(0.5), cancel.set()), daemon=True).start()
    partials, final = _collect(
        run_bash_streamed(
            "echo started; sleep 30", str(tmp_path), timeout_ms=60000, cancel_event=cancel
        )
    )
    assert partials == ["started"]
    assert final["signal"] == "interrupted"


class TestTimeoutSeconds:
    def test_default_when_missing_or_zero(self):
        assert timeout_seconds_to_ms(None) == _DEFAULT_TIMEOUT_S * 1000
        assert timeout_seconds_to_ms(0) == _DEFAULT_TIMEOUT_S * 1000

    def test_minimum_enforced(self):
        assert timeout_seconds_to_ms(1) == _MIN_TIMEOUT_S * 1000
        assert timeout_seconds_to_ms(9) == _MIN_TIMEOUT_S * 1000

    def test_maximum_enforced(self):
        assert timeout_seconds_to_ms(99999) == _MAX_TIMEOUT_S * 1000

    def test_valid_value_passed_through(self):
        assert timeout_seconds_to_ms(45) == 45_000

    def test_invalid_value_falls_back_to_default(self):
        assert timeout_seconds_to_ms("abc") == _DEFAULT_TIMEOUT_S * 1000

    def test_schema_documents_seconds(self, tmp_path):
        from src.core.agent_tools import create_registry

        registry = create_registry(str(tmp_path))
        for fn in registry.get_openai_functions():
            if fn["function"]["name"] == "bash":
                desc = fn["function"]["parameters"]["properties"]["timeout"]["description"]
                assert "SECONDS" in desc
                return
        raise AssertionError("bash tool not found")