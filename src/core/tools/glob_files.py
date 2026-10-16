"""Glob / find files by pattern within the workspace."""

from __future__ import annotations

import os
import fnmatch
from dataclasses import dataclass
from typing import Optional, List

from ..paths import resolve_existing_path


@dataclass
class GlobResult:
    success: bool
    files: List[str]
    message: str = ""
    truncated: bool = False


DESCRIPTION_FULL = """Find files matching a glob pattern (e.g. **/*.py, src/**/*.ts).
- Recurses from the given path (default: workspace root).
- Accepts absolute paths anywhere on the system; relative paths resolve from the current directory.
- Respects .gitignore when present.
- Returns paths relative to the searched root."""


def _load_gitignore_names(root: str) -> set:
    names = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv"}
    gi = os.path.join(root, ".gitignore")
    if os.path.isfile(gi):
        try:
            with open(gi) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        names.add(line.rstrip("/").lstrip("/"))
        except OSError:
            pass
    return names


def glob_files(
    pattern: str,
    workspace_dir: str,
    path: Optional[str] = None,
    max_results: int = 200,
) -> GlobResult:
    if not pattern or not pattern.strip():
        return GlobResult(success=False, files=[], message="pattern is required")

    root = (
        resolve_existing_path(path, fallback_base=workspace_dir)
        if path
        else os.path.abspath(workspace_dir)
    )
    if not os.path.isdir(root):
        return GlobResult(success=False, files=[], message=f"Path not found: {path or '.'}")

    ignore = _load_gitignore_names(root)
    matches: List[str] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        # prune ignored directories
        dirnames[:] = [d for d in dirnames if d not in ignore and not d.startswith(".")]
        for name in filenames:
            if name in ignore:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            # Normalize ** patterns: '**/*.py' should also match top-level '*.py'
            patterns = [pattern]
            if pattern.startswith("**/"):
                patterns.append(pattern[3:])
            hit = False
            for pat in patterns:
                if (
                    fnmatch.fnmatch(rel, pat)
                    or fnmatch.fnmatch(name, pat)
                    or fnmatch.fnmatch(rel.replace(os.sep, "/"), pat)
                ):
                    hit = True
                    break
            if hit:
                matches.append(rel.replace(os.sep, "/"))
                if len(matches) >= max_results:
                    truncated = True
                    break
        if truncated:
            break

    matches.sort()
    msg = f"Found {len(matches)} file(s)" + (" (truncated)" if truncated else "")
    return GlobResult(success=True, files=matches, message=msg, truncated=truncated)
