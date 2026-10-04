import os
import subprocess
import shutil
import time
import signal
import uuid
import re
import threading
import queue
import sys
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Generator


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
- Commands run in the workspace directory by default.
- Use is_background=true for long-running commands (dev servers, builds).
- Background commands return a task_id immediately.
- Default timeout: 120s foreground, unbounded background.
- AVOID echo/printf for communication — output text directly instead."""


_DEFAULT_TIMEOUT_MS = 120_000
_MAX_TIMEOUT_MS = 300_000
_BACKGROUND_MAX_RUNTIME = 86400  # 24h


def _make_task_id() -> str:
    return uuid.uuid4().hex[:12]


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


def _is_noop_command(command: str) -> bool:
    t = command.strip()
    return not t or t == "true" or t == ":"


def _safe_stdin():
    """Return a usable stdin for the child, or DEVNULL when unavailable.

    Captured/pseudo stdin (pytest, notebooks, pipes) has no valid fileno and
    would make Popen raise "redirected stdin is pseudofile, has no fileno()".
    """
    try:
        fd = sys.stdin.fileno()
        os.fstat(fd)
        return sys.stdin
    except Exception:
        return subprocess.DEVNULL


def _is_bare_echo(command: str) -> bool:
    t = command.strip()
    if not t.startswith("echo"):
        return False
    if not t[4:].startswith((" ", "\t")) and len(t) > 4:
        if not re.match(r"^echo[ \t]", t):
            return False
    rest = t[4:].strip()
    while rest.startswith("-") and set(rest.lstrip("-")[:5]).issubset({"n", "e", "E"}):
        rest = rest.lstrip("-").lstrip("neE").strip()
    return bool(rest) and len(rest) < 512 and not re.search(r"[;&|><$\`\(\)\n]", rest)


def _add_noop_reminder(output: str, command: str) -> str:
    if _is_noop_command(command) or _is_bare_echo(command):
        output += (
            "\n\n<system-reminder>\n"
            "You appear to be running empty/echo commands to stay active while waiting. "
            "End your turn — you will be woken automatically when there is something to do.\n"
            "</system-reminder>"
        )
    return output


def run_bash(input: BashInput, workspace_dir: str) -> BashOutput:
    workdir = input.workdir or workspace_dir
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

    # On ajoute -S pour permettre à sudo de lire le mot de passe depuis stdin
    actual_cmd = cmd.replace("sudo ", "sudo -S ", 1) if cmd.startswith("sudo ") else cmd

    try:
        proc = subprocess.Popen(
            actual_cmd,
            shell=True,
            cwd=workdir,
            stdin=_safe_stdin(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid if os.name != "nt" else None,
        )

        stdout_bytes, _ = proc.communicate(timeout=timeout_sec)
        output = stdout_bytes.decode("utf-8", errors="replace")

        if proc.returncode is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL) if os.name != "nt" else proc.kill()
            proc.wait()
            output = _add_noop_reminder(output, cmd)
            return BashOutput(
                success=False,
                exit_code=-1,
                output=output,
                error=f"Command timed out after {timeout_ms}ms",
                signal="timeout",
            )

        output = _add_noop_reminder(output, cmd)
        return BashOutput(
            success=proc.returncode == 0,
            exit_code=proc.returncode,
            output=output,
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
        output = _add_noop_reminder(output, cmd)
        return BashOutput(
            success=False,
            exit_code=-1,
            output=output,
            error=f"Command timed out after {timeout_ms}ms",
            signal="timeout",
        )

    except FileNotFoundError:
        return BashOutput(success=False, error=f"Command not found: {cmd.split()[0]}")
    except OSError as e:
        return BashOutput(success=False, error=str(e))


def _run_background(cmd: str, workdir: str, description: str) -> BashOutput:
    task_id = _make_task_id()

    # On ajoute -S pour permettre à sudo de lire le mot de passe depuis stdin
    actual_cmd = cmd.replace("sudo ", "sudo -S ", 1) if cmd.startswith("sudo ") else cmd

    try:
        proc = subprocess.Popen(
            actual_cmd,
            shell=True,
            cwd=workdir,
            stdin=_safe_stdin(),
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

def run_bash_streamed(
    cmd: str,
    workdir: str,
    timeout_ms: Optional[int] = None,
) -> Generator[Dict[str, Any], None, None]:
    """Execute bash and yield lines in real-time as {'partial': line} dicts.
    
    Final result is yielded as {'success': bool, 'output': str, ...}.
    Used by ToolBridge for real-time display of command output.
    """
    timeout_ms = timeout_ms or _DEFAULT_TIMEOUT_MS
    timeout_ms = min(timeout_ms, _MAX_TIMEOUT_MS)
    timeout_sec = timeout_ms / 1000.0

    actual_cmd = cmd.replace("sudo ", "sudo -S ", 1) if cmd.startswith("sudo ") else cmd

    try:
        proc = subprocess.Popen(
            actual_cmd,
            shell=True,
            cwd=workdir,
            stdin=_safe_stdin(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # unbuffered: readline() streams line by line
            preexec_fn=os.setsid if os.name != "nt" else None,
        )

        output_lines = []
        start_time = time.time()
        timed_out = False
        # Read on a daemon thread so a silent, blocking command cannot defeat
        # the timeout by never producing a line to readline().
        line_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()

        def _reader():
            try:
                for raw in iter(proc.stdout.readline, b""):
                    line_queue.put(raw)
            except Exception:
                pass
            finally:
                line_queue.put(None)

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        while True:
            remaining = timeout_sec - (time.time() - start_time)
            if remaining <= 0:
                timed_out = True
                break
            try:
                line_bytes = line_queue.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                if proc.poll() is not None and line_queue.empty():
                    break
                continue
            if line_bytes is None:
                break
            line = line_bytes.decode("utf-8", errors="replace").rstrip()
            output_lines.append(line)
            yield {"partial": line}

        if timed_out:
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
            else:
                proc.kill()
            output = "\n".join(output_lines)
            yield {
                "success": False,
                "output": output,
                "error": f"Command timed out after {timeout_ms}ms",
                "signal": "timeout",
                "exit_code": -1,
            }
            return

        proc.stdout.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        output = "\n".join(output_lines)
        output = _add_noop_reminder(output, cmd)

        yield {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "output": output,
        }

    except FileNotFoundError:
        yield {"success": False, "error": f"Command not found: {cmd.split()[0]}"}
    except OSError as e:
        yield {"success": False, "error": str(e)}
