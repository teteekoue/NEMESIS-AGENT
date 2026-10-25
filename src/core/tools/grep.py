import os
import subprocess
import shutil
from dataclasses import dataclass
from typing import Optional, List

from ..paths import resolve_existing_path, system_base_dir


@dataclass
class GrepInput:
    pattern: str
    include: Optional[str] = None
    path: Optional[str] = None
    mode: str = "content"
    head_limit: Optional[int] = None
    case_insensitive: bool = False
    context_before: int = 0
    context_after: int = 0
    context_around: int = 0


@dataclass
class GrepResult:
    success: bool
    matches: List[str] = None
    match_count: int = 0
    error: Optional[str] = None
    truncated: bool = False

    def __post_init__(self):
        if self.matches is None:
            self.matches = []


DESCRIPTION_FULL = """Search file contents using ripgrep.
- Supports full regex patterns.
- Use include to filter files (e.g. "*.py").
- Modes: content (default), files_with_matches, count.
- Context: context_before/after/around for surrounding lines."""


_MAX_OUTPUT_BYTES = 10 * 1024 * 1024  # 10 Mo - augmente de 64 Ko


def grep(input: GrepInput, workspace_dir: Optional[str] = None) -> GrepResult:
    if shutil.which("rg"):
        return _grep_rg(input, workspace_dir)
    return _grep_fallback(input, workspace_dir)


def _grep_rg(input: GrepInput, workspace_dir: Optional[str] = None) -> GrepResult:
    search_path = (
        resolve_existing_path(input.path)
        if input.path
        else str(workspace_dir or system_base_dir())
    )

    args = ["rg", "--no-heading", "--with-filename", "--line-number", "--color=never"]

    if input.case_insensitive:
        args.append("-i")

    ctx = input.context_around or 0
    if ctx:
        args.extend(["-C", str(ctx)])
    else:
        if input.context_before:
            args.extend(["-B", str(input.context_before)])
        if input.context_after:
            args.extend(["-A", str(input.context_after)])

    if input.include:
        for pat in input.include.split(","):
            args.extend(["--glob", pat.strip()])

    if input.mode == "files_with_matches":
        args.append("-l")
    elif input.mode == "count":
        args.append("-c")

    if input.head_limit:
        args.extend(["-m", str(input.head_limit)])

    args.append("--")
    args.append(input.pattern)
    args.append(search_path)

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 1:
            return GrepResult(success=True, matches=[], match_count=0)
        elif result.returncode > 1:
            return GrepResult(success=False, error=result.stderr.strip())

        output = result.stdout
        truncated = False
        if len(output) > _MAX_OUTPUT_BYTES:
            output = output[:_MAX_OUTPUT_BYTES]
            truncated = True

        lines = output.strip().split("\n") if output.strip() else []
        return GrepResult(
            success=True,
            matches=lines,
            match_count=len(lines),
            truncated=truncated,
        )

    except subprocess.TimeoutExpired:
        return GrepResult(success=False, error="grep timed out")
    except FileNotFoundError:
        return GrepResult(success=False, error="ripgrep (rg) not found. Install with: apt install ripgrep")


def _grep_fallback(input: GrepInput, workspace_dir: Optional[str] = None) -> GrepResult:
    import re as regex

    search_path = (
        resolve_existing_path(input.path)
        if input.path
        else str(workspace_dir or system_base_dir())
    )

    try:
        pattern = regex.compile(
            input.pattern,
            flags=regex.IGNORECASE if input.case_insensitive else 0,
        )
    except regex.error as e:
        return GrepResult(success=False, error=f"Invalid regex: {e}")

    file_pattern = None
    if input.include:
        import fnmatch
        file_pattern = input.include.split(",")

    results = []
    total_bytes = 0

    def should_skip(path, root):
        if file_pattern:
            for fp in file_pattern:
                if fnmatch.fnmatch(path, fp.strip()):
                    return False
            return True
        return False

    if os.path.isfile(search_path):
        files = [search_path]
    else:
        files = []
        for root, dirs, filenames in os.walk(search_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, search_path)
                if not should_skip(rel, root):
                    files.append(fp)

    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    if pattern.search(line):
                        rel_path = os.path.relpath(filepath, search_path)
                        if input.mode == "files_with_matches":
                            if rel_path not in results:
                                results.append(rel_path)
                            break
                        elif input.mode == "count":
                            continue
                        else:
                            entry = f"{rel_path}:{line_num}:{line.rstrip()}"
                            results.append(entry)
                if input.mode == "count":
                    count = sum(1 for l in open(filepath) if pattern.search(l))
                    if count > 0:
                        results.append(f"{rel_path}:{count}")
        except (OSError, UnicodeDecodeError):
            continue

        if input.head_limit and len(results) >= input.head_limit:
            break

    output = "\n".join(results[:input.head_limit] if input.head_limit else results)
    truncated = False
    if len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        truncated = True

    return GrepResult(
        success=True,
        matches=results,
        match_count=len(results),
        truncated=truncated,
    )
