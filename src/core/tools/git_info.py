"""Git status / diff / log helpers for the agent."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional, List


@dataclass
class GitResult:
    success: bool
    output: str
    error: Optional[str] = None


DESCRIPTION_FULL = """Inspect git repository state.
Actions:
- status: working tree status (short by default)
- diff: unstaged + staged diff (optionally for a path)
- log: recent commit log
- branch: current branch and list of local branches"""


def _run_git(args: List[str], cwd: str, timeout: int = 30) -> GitResult:
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and not proc.stdout:
            return GitResult(success=False, output=out.strip(), error=out.strip() or f"git exited {proc.returncode}")
        return GitResult(success=True, output=out.strip())
    except FileNotFoundError:
        return GitResult(success=False, output="", error="git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        return GitResult(success=False, output="", error="git command timed out")
    except Exception as e:
        return GitResult(success=False, output="", error=str(e))


def git_info(
    action: str,
    workspace_dir: str,
    path: Optional[str] = None,
    limit: int = 20,
) -> GitResult:
    from ..paths import resolve_existing_path

    action = (action or "status").lower().strip()
    cwd = os.getcwd()
    if path:
        candidate = resolve_existing_path(path)
        if os.path.isdir(candidate):
            cwd = candidate

    # Ensure we are inside a git repo (walk up)
    probe = _run_git(["rev-parse", "--show-toplevel"], cwd)
    if not probe.success:
        return GitResult(success=False, output="", error="Not a git repository (or git unavailable)")

    if action == "status":
        return _run_git(["status", "--short", "--branch"], cwd)
    if action == "diff":
        args = ["diff", "--stat"] if not path else ["diff", "--", path]
        # Include staged as well for a fuller picture when no path
        if not path:
            staged = _run_git(["diff", "--cached", "--stat"], cwd)
            unstaged = _run_git(["diff", "--stat"], cwd)
            parts = []
            if staged.success and staged.output:
                parts.append("=== STAGED ===\n" + staged.output)
            if unstaged.success and unstaged.output:
                parts.append("=== UNSTAGED ===\n" + unstaged.output)
            if not parts:
                # full patch if stats empty
                return _run_git(["diff", "HEAD"], cwd)
            return GitResult(success=True, output="\n\n".join(parts))
        return _run_git(args, cwd)
    if action == "log":
        n = max(1, min(int(limit or 20), 100))
        return _run_git(["log", f"-{n}", "--oneline", "--decorate"], cwd)
    if action == "branch":
        return _run_git(["branch", "-vv"], cwd)

    return GitResult(
        success=False,
        output="",
        error=f"Unknown action '{action}'. Use: status, diff, log, branch",
    )
