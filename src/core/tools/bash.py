import os
import shlex
import subprocess
import shutil
import tempfile
import time
import signal
import uuid
import re
import threading
import sys
import queue
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Generator

from ..paths import resolve_path, session_cwd, set_session_cwd


_ACTIVE_PROCESSES: Dict[str, subprocess.Popen] = {}
_PROCESS_OUTPUT: Dict[str, bytes] = {}
_PROCESS_LOCK = threading.Lock()


@dataclass
class BashInput:
    command: str
    description: str = ""
    timeout_ms: Optional[int] = None
    is_background: bool = False
    workdir: Optional[str] = None


@dataclass
class BashOutput:
    success: bool
    exit_code: Optional[int] = None
    output: str = ""
    error: Optional[str] = None
    task_id: Optional[str] = None
    truncated: bool = False
    signal: Optional[str] = None


DESCRIPTION_FULL = """Execute a bash command in a persistent shell session.
- Any command, any path: the agent is not confined to a workspace.
- Commands run in the current shell directory, which is shared between calls:
  a `cd` performed in one call is still active in the next one. Pass `workdir`
  to run a command somewhere else (relative or absolute path).
- The command is ALWAYS bounded by a generous timeout so it can never run
  forever. `timeout` is in SECONDS (default 600). Use 10-30s for quick
  inspection, 60-3600s for builds/tests/installs; up to one week is accepted.
- Use is_background=true for long-running commands (dev servers, watchers).
  Background commands are NOT bounded by `timeout` and return a task_id at once.
- Multi-line commands, heredocs and pipes are supported as-is: send the exact
  script you want to run, never escape it yourself.
- Every call returns stdout+stderr and the real exit code, even when empty.
- AVOID echo/printf for communication — output text directly instead."""


_DEFAULT_TIMEOUT_S = 600
_MIN_TIMEOUT_S = 10
_MAX_TIMEOUT_S = 7 * 24 * 3600  # one week: no practical ceiling

_CWD_TRAILER_MARK = "__NEMESIS_STATUS__"

_MS_PER_SECOND = 1000

# Low-level defaults (the streamed runner works in milliseconds).
_DEFAULT_TIMEOUT_MS = _DEFAULT_TIMEOUT_S * _MS_PER_SECOND
_MAX_TIMEOUT_MS = _MAX_TIMEOUT_S * _MS_PER_SECOND
_BACKGROUND_MAX_RUNTIME = 86400  # 24h


def timeout_seconds_to_ms(seconds: Optional[int]) -> int:
    """Convert a user-supplied timeout in SECONDS to milliseconds.

    The result is always clamped to [MIN_TIMEOUT_S, MAX_TIMEOUT_S] so every
    foreground command is bounded — no command can run indefinitely. A missing,
    zero or non-numeric value falls back to the default.
    """
    try:
        value = int(seconds) if seconds is not None else 0
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        value = _DEFAULT_TIMEOUT_S
    value = max(_MIN_TIMEOUT_S, min(_MAX_TIMEOUT_S, value))
    return value * _MS_PER_SECOND


def _make_task_id() -> str:
    return uuid.uuid4().hex[:12]


def _shell_executable() -> Optional[str]:
    """Prefer bash when available: richer syntax (heredocs, pipefail, arrays)."""
    for candidate in ("/bin/bash", "/usr/bin/bash", "/usr/local/bin/bash"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("bash")
    return found or None


def resolve_workdir(workdir: Optional[str], fallback: Optional[str] = None) -> str:
    """Resolve a working directory anywhere on the machine.

    Order: explicit ``workdir``, then ``fallback`` (legacy caller), then the
    shared shell directory. Relative paths resolve from the shell directory.
    """
    raw = str(workdir).strip() if workdir not in (None, "") else ""
    if raw:
        return resolve_path(raw)
    legacy = str(fallback).strip() if fallback not in (None, "") else ""
    if legacy:
        return resolve_path(legacy)
    return str(session_cwd())


def _wrap_command(cmd: str) -> tuple[str, Optional[str]]:
    """Append a trailer that records the final directory of the shell.

    The directory is written to a temp file so the command output is never
    polluted. The exit status of the user command is preserved.
    """
    if not cmd or cmd.rstrip().endswith("\\"):
        return cmd, None
    try:
        fd, cwd_file = tempfile.mkstemp(prefix="nemesis-cwd-", suffix=".txt")
        os.close(fd)
    except OSError:
        return cmd, None
    trailer = (
        f"\n{_CWD_TRAILER_MARK}=$?\n"
        f"printf '%s' \"$PWD\" > {shlex.quote(cwd_file)}\n"
        f"exit ${_CWD_TRAILER_MARK}\n"
    )
    return cmd + trailer, cwd_file


def _consume_cwd(cwd_file: Optional[str]) -> None:
    if not cwd_file:
        return
    try:
        with open(cwd_file, "r", encoding="utf-8", errors="replace") as handle:
            final_dir = handle.read().strip()
        if final_dir and os.path.isdir(final_dir):
            set_session_cwd(final_dir)
    except OSError:
        pass
    finally:
        try:
            os.unlink(cwd_file)
        except OSError:
            pass


def _output_with_status(output: str, exit_code: Optional[int], signal_name: Optional[str] = None) -> str:
    """Never return a silently empty result: always expose the exit status."""
    if output.strip():
        return output
    if signal_name:
        return f"[no output captured — signal: {signal_name}]"
    return f"[no output captured — exit code: {exit_code}]"


_SELF_PKILL_RE = re.compile(
    r"(?:^|[;&|\(\n])\s*(pkill|pgrep)((?:\s+-[A-Za-z]*f[A-Za-z]*|\s+--full\b)+)"
    r"\s+(?:'([^']*)'|\"([^\"]*)\"|([^\s;&|()]+))"
)


def _detect_self_matching_pkill(command: str) -> Optional[str]:
    for m in _SELF_PKILL_RE.finditer(command):
        cmd_word = m.group(1)
        pattern = m.group(3) or m.group(4) or m.group(5)
        if not pattern or len(pattern) < 3:
            continue
        if "$(" in pattern or "$`" in pattern or pattern.startswith("`") or "${" in pattern:
            continue
        span_start, span_end = m.span()
        remainder = command[:span_start] + "\n" + command[span_end:]
        if pattern in remainder:
            return f"Rejected: {cmd_word} -f '{pattern}' would match itself (pattern appears in remainder of command). Use a more specific pattern."
    return None


def run_bash(input: BashInput, workspace_dir: Optional[str] = None) -> BashOutput:
    workdir = resolve_workdir(input.workdir, workspace_dir)
    if not os.path.isdir(workdir):
        return BashOutput(success=False, error=f"Workdir not found: {workdir}")

    cmd = input.command.strip()
    if not cmd:
        return BashOutput(success=False, error="Empty command.")

    self_kill = _detect_self_matching_pkill(cmd)
    if self_kill:
        return BashOutput(success=False, error=self_kill)

    if input.is_background:
        return _run_background(cmd, workdir, input.description)
    else:
        return _run_foreground(cmd, workdir, input.timeout_ms or _DEFAULT_TIMEOUT_MS)


def _run_foreground(cmd: str, workdir: str, timeout_ms: int) -> BashOutput:
    timeout_ms = min(timeout_ms, _MAX_TIMEOUT_MS)
    timeout_sec = timeout_ms / 1000.0
    wrapped, cwd_file = _wrap_command(cmd)

    try:
        proc = subprocess.Popen(
            wrapped,
            shell=True,
            executable=_shell_executable(),
            cwd=workdir,
            # Never inherit the server's stdin: a command that reads stdin
            # (e.g. sudo waiting for a password) would otherwise block forever
            # with no output. /dev/null makes it fail fast instead.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid if os.name != "nt" else None,
        )

        stdout_bytes, _ = proc.communicate(timeout=timeout_sec)
        output = stdout_bytes.decode("utf-8", errors="replace")

        if proc.returncode is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL) if os.name != "nt" else proc.kill()
            proc.wait()
            return BashOutput(
                success=False,
                exit_code=-1,
                output=_output_with_status(output, -1, "timeout"),
                error=f"Command timed out after {timeout_ms}ms",
                signal="timeout",
            )

        return BashOutput(
            success=proc.returncode == 0,
            exit_code=proc.returncode,
            output=_output_with_status(output, proc.returncode),
        )

    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
            proc.wait()
        except Exception:
            pass
        remaining = b""
        try:
            remaining = proc.stdout.read()
        except Exception:
            pass
        output = remaining.decode("utf-8", errors="replace")
        return BashOutput(
            success=False,
            exit_code=-1,
            output=_output_with_status(output, -1, "timeout"),
            error=f"Command timed out after {timeout_ms}ms",
            signal="timeout",
        )

    except FileNotFoundError:
        return BashOutput(success=False, error=f"Command not found: {cmd.split()[0]}")
    except OSError as e:
        return BashOutput(success=False, error=str(e))
    finally:
        _consume_cwd(cwd_file)


def _run_background(cmd: str, workdir: str, description: str) -> BashOutput:
    task_id = _make_task_id()

    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            executable=_shell_executable(),
            cwd=workdir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid if os.name != "nt" else None,
        )

        with _PROCESS_LOCK:
            _ACTIVE_PROCESSES[task_id] = proc
            _PROCESS_OUTPUT[task_id] = b""

        def _reader():
            try:
                for chunk in iter(lambda: proc.stdout.read(4096), b""):
                    with _PROCESS_LOCK:
                        _PROCESS_OUTPUT[task_id] += chunk
            except Exception:
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        return BashOutput(
            success=True,
            task_id=task_id,
            output=f"Command started in background. task_id: {task_id}\nDescription: {description}",
        )

    except FileNotFoundError:
        return BashOutput(success=False, error=f"Command not found: {cmd.split()[0]}")
    except OSError as e:
        return BashOutput(success=False, error=str(e))


def get_task_output(task_id: str, timeout_ms: Optional[int] = None) -> BashOutput:
    with _PROCESS_LOCK:
        proc = _ACTIVE_PROCESSES.get(task_id)

    if not proc:
        output = ""
        with _PROCESS_LOCK:
            if task_id in _PROCESS_OUTPUT:
                output = _PROCESS_OUTPUT.pop(task_id, b"").decode("utf-8", errors="replace")
        return BashOutput(
            success=output != "",
            output=output or "",
            error=f"Task {task_id} not found" if not output else None,
        )

    if timeout_ms and timeout_ms > 0:
        try:
            proc.wait(timeout=timeout_ms / 1000.0)
        except subprocess.TimeoutExpired:
            with _PROCESS_LOCK:
                output = _PROCESS_OUTPUT.get(task_id, b"").decode("utf-8", errors="replace")
            return BashOutput(
                success=True,
                exit_code=None,
                output=output,
                task_id=task_id,
                error="Task still running",
            )

    if proc.poll() is not None:
        exit_code = proc.returncode
        with _PROCESS_LOCK:
            output = _PROCESS_OUTPUT.pop(task_id, b"").decode("utf-8", errors="replace")
            _ACTIVE_PROCESSES.pop(task_id, None)
        return BashOutput(
            success=exit_code == 0,
            exit_code=exit_code,
            output=output,
        )
    else:
        with _PROCESS_LOCK:
            output = _PROCESS_OUTPUT.get(task_id, b"").decode("utf-8", errors="replace")
        return BashOutput(
            success=True,
            exit_code=None,
            output=output,
            task_id=task_id,
            error="Task still running",
        )


def kill_task(task_id: str) -> BashOutput:
    with _PROCESS_LOCK:
        proc = _ACTIVE_PROCESSES.pop(task_id, None)
        _PROCESS_OUTPUT.pop(task_id, None)

    if not proc:
        return BashOutput(success=False, error=f"Task {task_id} not found")

    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
        proc.wait()
        return BashOutput(success=True, output=f"Task {task_id} killed")
    except Exception as e:
        return BashOutput(success=False, error=str(e))


def list_background_tasks() -> List[Dict[str, Any]]:
    with _PROCESS_LOCK:
        tasks = []
        for task_id, proc in _ACTIVE_PROCESSES.items():
            running = proc.poll() is None
            tasks.append({
                "task_id": task_id,
                "running": running,
                "exit_code": proc.returncode if not running else None,
            })
        for task_id in list(_PROCESS_OUTPUT.keys()):
            if task_id not in _ACTIVE_PROCESSES:
                tasks.append({
                    "task_id": task_id,
                    "running": False,
                    "output": _PROCESS_OUTPUT[task_id].decode("utf-8", errors="replace"),
                })
        return tasks


# ──────────────────────────────────────────────
# Streaming bash execution (real-time output)
# ──────────────────────────────────────────────

def _terminate_process_group(proc: subprocess.Popen) -> None:
    """Kill the whole process group (shell + children) and reap it."""
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def run_bash_streamed(
    cmd: str,
    workdir: Optional[str] = None,
    timeout_ms: Optional[int] = None,
    cancel_event=None,
) -> Generator[Dict[str, Any], None, None]:
    """Execute bash and yield lines in real-time as {'partial': line} dicts.

    Final result is yielded as {'success': bool, 'output': str, ...}.
    Used by ToolBridge for real-time display of command output.

    ``workdir`` defaults to the shared shell directory, so a ``cd`` issued in a
    previous call keeps applying until another one changes it.

    ``cancel_event`` (threading.Event) aborts the running command as soon as it
    is set. Output is read on a reader thread so the timeout and the cancel
    signal are honored even when the command produces NO output at all (e.g.
    ``sudo`` waiting for a password) — otherwise a silent command would block
    this generator forever.
    """
    timeout_ms = timeout_ms or _DEFAULT_TIMEOUT_MS
    timeout_ms = min(timeout_ms, _MAX_TIMEOUT_MS)
    timeout_sec = timeout_ms / 1000.0

    resolved_workdir = resolve_workdir(workdir)
    if not os.path.isdir(resolved_workdir):
        yield {"success": False, "error": f"Workdir not found: {resolved_workdir}"}
        return

    wrapped, cwd_file = _wrap_command(cmd)

    try:
        proc = subprocess.Popen(
            wrapped,
            shell=True,
            executable=_shell_executable(),
            cwd=resolved_workdir,
            # A silent, stdin-reading command (sudo password prompt, interactive
            # tool) must not hang: /dev/null makes it fail fast with output.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # unbuffered: readline() streams line by line
            preexec_fn=os.setsid if os.name != "nt" else None,
        )
    except FileNotFoundError:
        _consume_cwd(cwd_file)
        yield {"success": False, "error": f"Command not found: {cmd.split()[0]}"}
        return
    except OSError as e:
        _consume_cwd(cwd_file)
        yield {"success": False, "error": str(e)}
        return

    output_lines: List[str] = []
    line_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
    start_time = time.time()

    def _reader() -> None:
        try:
            for chunk in iter(lambda: proc.stdout.read(4096), b""):
                line_queue.put(chunk)
        except Exception:
            pass
        finally:
            line_queue.put(None)

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    timed_out = False
    cancelled = False
    stream_done = False
    carry = b""

    while not stream_done:
        try:
            chunk = line_queue.get(timeout=0.1)
        except queue.Empty:
            chunk = b""

        if chunk is None:
            stream_done = True
        elif chunk:
            carry += chunk
            # Split on newlines, keep the trailing partial line buffered.
            while b"\n" in carry:
                raw_line, carry = carry.split(b"\n", 1)
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r")
                output_lines.append(line)
                yield {"partial": line}

        # Check cancel/timeout on every poll cycle, even without output.
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        if timeout_sec > 0 and (time.time() - start_time) > timeout_sec:
            timed_out = True
            break

    if carry:
        line = carry.decode("utf-8", errors="replace").rstrip("\r")
        output_lines.append(line)
        yield {"partial": line}

    if cancelled or timed_out:
        _terminate_process_group(proc)
        _consume_cwd(cwd_file)
        output = "\n".join(output_lines)
        if cancelled:
            yield {
                "success": False,
                "output": _output_with_status(output, -1, "interrupted"),
                "error": "Commande interrompue par l'utilisateur.",
                "signal": "interrupted",
                "exit_code": -1,
            }
        else:
            yield {
                "success": False,
                "output": _output_with_status(output, -1, "timeout"),
                "error": f"Command timed out after {timeout_ms}ms",
                "signal": "timeout",
                "exit_code": -1,
            }
        return

    try:
        proc.stdout.close()
    except Exception:
        pass
    proc.wait()
    _consume_cwd(cwd_file)
    output = "\n".join(output_lines)
    yield {
        "success": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": _output_with_status(output, proc.returncode),
    }
