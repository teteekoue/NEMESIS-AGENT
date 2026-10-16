"""Tests for streamed bash execution: cancel, timeout, silent commands."""

import threading
import time

from src.core.tools.bash import run_bash_streamed


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