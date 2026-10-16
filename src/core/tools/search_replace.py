import os
import re
from dataclasses import dataclass
from typing import Optional, List

from ..paths import resolve_existing_path


@dataclass
class SearchReplaceInput:
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


@dataclass
class EditDetail:
    old_string: str
    old_line: int
    new_string: str
    new_line: int
    context_before: str = ""
    context_after: str = ""


@dataclass
class SearchReplaceResult:
    success: bool
    message: str = ""
    error_type: Optional[str] = None
    edits: Optional[List[EditDetail]] = None


DESCRIPTION_FULL = r"""Replaces an exact string in a file.
- Read the file with ${{ tools.by_kind.read }} before editing.
- ${{ tools.by_kind.read }} prefixes each line with "LINE_NUMBER→". That prefix is NOT part of the file.
- ${{ params.edit.old_string }} must match exactly one place (or set ${{ params.edit.replace_all }}=true).
- ${{ params.edit.old_string }} must be different from ${{ params.edit.new_string }}."""


def _find_nearest_match(content: str, old_string: str) -> Optional[str]:
    keyword = max(old_string.splitlines()[0].split(), key=len, default="")
    if not keyword:
        return None
    for i, line in enumerate(content.splitlines()):
        if keyword in line:
            snippet = line.rstrip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            return f"Nearest match: line {i+1}: {snippet}"
    return None


def search_replace(
    file_path: str,
    old_string: str,
    new_string: str,
    workspace_dir: str,
    replace_all: bool = False,
) -> SearchReplaceResult:
    resolved = resolve_existing_path(file_path, fallback_base=workspace_dir)

    if old_string == new_string:
        return SearchReplaceResult(
            success=False,
            message="Old string and new string are the same.",
            error_type="InvalidInput",
        )

    if old_string == "":
        return _create_file(resolved, file_path, new_string)

    if not os.path.exists(resolved):
        return SearchReplaceResult(
            success=False,
            message=f"Error: {file_path} does not exist.",
            error_type="FileNotFound",
        )

    if os.path.isdir(resolved):
        return SearchReplaceResult(
            success=False,
            message=f"Error: {file_path} is a directory, not a file.",
            error_type="InvalidInput",
        )

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
    except PermissionError:
        return SearchReplaceResult(
            success=False,
            message=f"Permission denied: {file_path}",
            error_type="PermissionDenied",
        )

    has_crlf = "\r\n" in content
    match_content = content.replace("\r\n", "\n") if has_crlf else content

    positions = [m.start() for m in re.finditer(re.escape(old_string), match_content)]

    if not positions:
        hint = _find_nearest_match(match_content, old_string) or ""
        return SearchReplaceResult(
            success=False,
            message=(
                f"The string to replace was not found in the file. "
                f"Use read_file to see the current file content. "
                f"The user may have changed the file since you last read it."
                f"{chr(10) + hint if hint else ''}"
            ),
            error_type="NoMatchesFound",
        )

    if len(positions) > 1 and not replace_all:
        return SearchReplaceResult(
            success=False,
            message=(
                f"The string to replace was found multiple times in the file. "
                f"Use replace_all=true to replace all occurrences, "
                f"or include more context to only edit one occurrence."
            ),
            error_type="MultipleMatchesFound",
        )

    new_text = _replace_at_positions(match_content, positions, old_string, new_string)
    write_text = new_text.replace("\n", "\r\n") if has_crlf else new_text

    try:
        with open(resolved, "w", encoding="utf-8", newline="") as f:
            f.write(write_text)
    except OSError as e:
        return SearchReplaceResult(
            success=False,
            message=f"Error: failed to write {file_path}: {e}",
            error_type="InvalidInput",
        )

    edits = _build_edit_details(new_text, positions, old_string, new_string)
    n = len(positions)
    msg = (
        f"The file {file_path} has been updated successfully."
        if n == 1
        else f"The file {file_path} has been updated. All {n} occurrences were replaced."
    )

    return SearchReplaceResult(success=True, message=msg, edits=edits)


def _create_file(resolved: str, display_path: str, content: str) -> SearchReplaceResult:
    parent = os.path.dirname(resolved)
    try:
        os.makedirs(parent, exist_ok=True)
    except OSError as e:
        return SearchReplaceResult(
            success=False,
            message=f"Error: cannot create {display_path}: {e}",
            error_type="InvalidInput",
        )

    try:
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        return SearchReplaceResult(
            success=False,
            message=f"Error: failed to write {display_path}: {e}",
            error_type="InvalidInput",
        )

    return SearchReplaceResult(
        success=True,
        message=f"The file {display_path} has been created successfully.",
        edits=[
            EditDetail(
                old_string="",
                old_line=1,
                new_string=content,
                new_line=1,
            )
        ],
    )


def _replace_at_positions(text: str, positions: List[int], old: str, new: str) -> str:
    result = []
    last = 0
    for pos in positions:
        result.append(text[last:pos])
        result.append(new)
        last = pos + len(old)
    result.append(text[last:])
    return "".join(result)


_CONTEXT_LINES = 3


def _build_edit_details(
    new_text: str, positions: List[int], old_string: str, new_string: str
) -> List[EditDetail]:
    details = []
    lines = new_text.split("\n")
    for pos in positions:
        line_num = new_text[:pos].count("\n") + 1
        ctx_start = max(0, line_num - 1 - _CONTEXT_LINES)
        ctx_end = min(len(lines), line_num - 1 + _CONTEXT_LINES + 1)
        context_before = "\n".join(lines[ctx_start:line_num - 1])
        context_after = "\n".join(lines[line_num - 1:ctx_end])
        line_start = new_text.rfind("\n", 0, pos) + 1
        line_prefix = new_text[line_start:pos]

        details.append(
            EditDetail(
                old_string=old_string,
                old_line=line_num,
                new_string=new_string,
                new_line=line_num,
                context_before=context_before,
                context_after=context_after,
            )
        )
    return details
