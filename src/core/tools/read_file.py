import os
from dataclasses import dataclass, field
from typing import Optional, List, Union

from ..tool_kind import ToolKind, ToolNamespace
from ..paths import resolve_existing_path
from ..image_attachments import image_spec

MAX_NUM_TOKENS = 100_000  # 100k tokens - augmente de 25k
MAX_LINES_READ = 10_000  # 10k lignes - augmente de 1k


@dataclass
class ReadFileInput:
    path: Optional[str] = None
    paths: Optional[List[str]] = None
    offset: Optional[int] = None
    limit: Optional[int] = None


@dataclass
class FileContent:
    content: str
    absolute_path: str
    offset: Optional[int] = None
    limit: Optional[int] = None
    total_lines: int = 0
    content_concise: Optional[str] = None


@dataclass
class MultiFileContent:
    files: List[FileContent]
    total_files: int = 0
    failed_files: List[dict] = field(default_factory=list)


@dataclass
class ReadFileResult:
    success: bool
    content: Optional[Union[FileContent, MultiFileContent]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    images: List[dict] = field(default_factory=list)


DESCRIPTION_FULL = r"""Read one or more files.
Usage:
- Single file: pass a `path` string (optionally with offset/limit).
- Multiple files: pass a `paths` list of strings.
- By default reads up to {max_lines_read} lines per file.
- Results are returned with line numbers: LINE_NUMBER→LINE_CONTENT.
- The LINE_NUMBER→ prefix is NOT part of the file content.
- Text files are returned as text. Image files (PNG, JPEG, GIF, WebP, BMP, TIFF)
  are NOT dumped as binary: they are attached to the conversation as a
  multimodal image. Analyse the attached image directly; never ask for base64
  and never read the same image twice. At most 8 images per turn."""


def _resolve_path(path_str: str, workspace_dir: str) -> str:
    """Resolve a path anywhere on the system (workspace only as fallback)."""
    return resolve_existing_path(path_str, fallback_base=workspace_dir)


def _read_single_file(
    path_str: str,
    workspace_dir: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> ReadFileResult:
    """Read a single file and return its content (internal helper)."""
    resolved = _resolve_path(path_str, workspace_dir)

    if not os.path.exists(resolved):
        return ReadFileResult(
            success=False,
            error=f"Error: {path_str} does not exist.",
            error_type="FileNotFound",
        )

    if os.path.isdir(resolved):
        return ReadFileResult(
            success=False,
            error=f"Error: {path_str} is a directory, not a file.",
            error_type="IsADirectory",
        )

    image = image_spec(resolved)
    if image:
        return ReadFileResult(
            success=True,
            content=FileContent(
                content=image["placeholder"],
                absolute_path=resolved,
                total_lines=0,
            ),
            images=[image],
        )

    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except PermissionError:
        return ReadFileResult(
            success=False,
            error=f"Permission denied: {path_str}",
            error_type="PermissionDenied",
        )
    except OSError as e:
        return ReadFileResult(
            success=False,
            error=f"Failed to read {path_str}: {e}",
            error_type="FileReadError",
        )

    if not raw:
        return ReadFileResult(
            success=True,
            content=FileContent(content="", absolute_path=resolved, total_lines=0),
        )

    lines = raw.split("\n")
    total_lines = len(lines)

    start = max((offset or 1) - 1, 0)
    effective_limit = min(limit or MAX_LINES_READ, MAX_LINES_READ)
    end = min(start + effective_limit, total_lines)

    formatted_lines = []
    for i in range(start, end):
        line_num = i + 1
        if i == start or line_num % 10 == 0:
            formatted_lines.append(f"{line_num}→{lines[i]}")
        else:
            formatted_lines.append(lines[i])

    content = "\n".join(formatted_lines)
    token_estimate = len(content.split())

    if token_estimate > MAX_NUM_TOKENS:
        grep_hint = "Use grep to search for specific content."
        if offset is not None and limit is not None:
            msg = (
                f"File content ({token_estimate} tokens) exceeds maximum ({MAX_NUM_TOKENS}). "
                f"Try a smaller offset/limit, or {grep_hint}"
            )
        else:
            msg = (
                f"File content ({token_estimate} tokens) exceeds maximum ({MAX_NUM_TOKENS}). "
                f"Use offset/limit to read a shorter range, or {grep_hint}"
            )
        return ReadFileResult(
            success=False,
            error=msg,
            error_type="FileTooLarge",
        )

    return ReadFileResult(
        success=True,
        content=FileContent(
            content=content,
            absolute_path=resolved,
            offset=start + 1 if start > 0 else None,
            limit=effective_limit,
            total_lines=total_lines,
        ),
    )


def read_file(
    workspace_dir: str = ".",
    path: Optional[str] = None,
    paths: Optional[List[str]] = None,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
    file_path: Optional[str] = None,
    file: Optional[str] = None,
    filename: Optional[str] = None,
    **kwargs,
) -> ReadFileResult:
    """Read one or multiple files.

    - Single file: pass `path="..."` (backward compatible).
    - Multiple files: pass `paths=["...", "..."]`.
    - Also accepts: file_path, file, filename (aliases for path).
    - If both are provided, they are merged (path is prepended to paths).
    - Each file is read with the same offset/limit.
    """
    all_paths: List[str] = []

    # Support multiple parameter names for single file
    if path:
        all_paths.append(path)
    elif file_path:
        all_paths.append(file_path)
    elif file:
        all_paths.append(file)
    elif filename:
        all_paths.append(filename)
    
    # Support paths as list or single string
    if paths:
        if isinstance(paths, str):
            all_paths.append(paths)
        else:
            all_paths.extend(paths)

    if not all_paths:
        return ReadFileResult(
            success=False,
            error="No file specified. Provide a 'path', 'file_path', 'file', 'filename' string or a 'paths' list.",
            error_type="MissingParameter",
        )

    # Single file → return single FileContent (backward compatible)
    if len(all_paths) == 1:
        return _read_single_file(all_paths[0], workspace_dir, offset, limit)

    # Multiple files → return MultiFileContent
    file_contents: List[FileContent] = []
    failed_files: List[dict] = []
    images: List[dict] = []

    for p in all_paths:
        result = _read_single_file(p, workspace_dir, offset, limit)
        images.extend(result.images)
        if result.success and isinstance(result.content, FileContent):
            file_contents.append(result.content)
        else:
            failed_files.append({
                "path": p,
                "error": result.error or "Unknown error",
                "error_type": result.error_type or "Unknown",
            })

    if not file_contents:
        return ReadFileResult(
            success=False,
            error=f"All {len(all_paths)} file(s) failed to read.",
            error_type="AllFilesFailed",
        )

    return ReadFileResult(
        success=True,
        content=MultiFileContent(
            files=file_contents,
            total_files=len(all_paths),
            failed_files=failed_files,
        ),
        images=images,
    )
