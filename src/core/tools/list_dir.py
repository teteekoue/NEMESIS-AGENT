import os
from dataclasses import dataclass
from typing import Optional, List

from ..paths import resolve_existing_path, system_base_dir


@dataclass
class ListDirInput:
    path: Optional[str] = None
    depth: int = 2
    max_entries: int = 500


@dataclass
class ListDirResult:
    success: bool
    output: str = ""
    error: Optional[str] = None
    truncated: bool = False


DESCRIPTION_FULL = """List files and directories in a tree-like format.
- Respects .gitignore patterns.
- Summarizes directories with many files.
- Use depth to control recursion depth."""


_MAX_OUTPUT_CHARS = 1_000_000  # 1 Mo - augmente de 10 Ko


def list_dir(input: ListDirInput, workspace_dir: Optional[str] = None) -> ListDirResult:
    root = (
        resolve_existing_path(input.path)
        if input.path
        else str(workspace_dir or system_base_dir())
    )

    if not os.path.exists(root):
        return ListDirResult(success=False, error=f"Path not found: {input.path or '.'}")

    if os.path.isfile(root):
        return ListDirResult(success=False, error=f"Path is a file: {input.path or '.'}")

    lines = []
    _walk(root, root, input.depth, input.max_entries, lines, 0)

    output = "\n".join(lines)
    truncated = len(output) > _MAX_OUTPUT_CHARS
    if truncated:
        output = output[:_MAX_OUTPUT_CHARS] + "\n... (truncated)"

    return ListDirResult(success=True, output=output, truncated=truncated)


def _load_gitignore(root: str) -> set:
    patterns = {".git", ".hg", ".svn"}
    gitignore_path = os.path.join(root, ".gitignore")
    if os.path.isfile(gitignore_path):
        try:
            with open(gitignore_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.add(line.rstrip("/"))
        except OSError:
            pass
    return patterns


def _walk(
    root: str,
    current: str,
    max_depth: int,
    max_entries: int,
    lines: List[str],
    depth: int,
) -> int:
    if depth > max_depth or len(lines) >= max_entries:
        return 0

    gitignore = _load_gitignore(root)
    indent = "  " * depth

    try:
        entries = sorted(os.listdir(current))
    except PermissionError:
        lines.append(f"{indent}[permission denied]")
        return 0

    dirs = []
    files = []
    for entry in entries:
        if entry.startswith(".") and entry != ".env":
            continue
        full = os.path.join(current, entry)
        if os.path.isdir(full):
            dirs.append((entry, full))
        else:
            files.append(entry)

    count = 0
    for name in files:
        if len(lines) >= max_entries:
            break
        icon = "📄"
        lines.append(f"{indent}{icon} {name}")
        count += 1

    for name, full_path in dirs:
        if len(lines) >= max_entries:
            break
        sub_count = 0
        try:
            sub_entries = len(os.listdir(full_path))
        except PermissionError:
            sub_entries = 0
        icon = "📁"
        if sub_entries > 30:
            lines.append(f"{indent}{icon} {name}/ [{sub_entries} files]")
            count += 1
            continue
        lines.append(f"{indent}{icon} {name}/")
        count += 1
        sub_count = _walk(root, full_path, max_depth, max_entries, lines, depth + 1)
        count += sub_count

    return count
