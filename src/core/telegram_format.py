"""Convert assistant Markdown into readable plain text for Telegram chats."""

from __future__ import annotations

import html
import re


_FENCE = re.compile(r"^\s*(```|~~~)(?:[^\n]*)$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*)([-+*]|\d+[.)])\s+(.+)$")


def _inline_plain_text(line: str) -> str:
    line = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"Image : \1 (\2)", line)
    line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
    line = re.sub(r"<((?:https?://|mailto:)[^>]+)>", r"\1", line)
    line = re.sub(r"`{1,3}", "", line)
    line = line.replace("**", "").replace("__", "").replace("~~", "")
    line = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", line)
    line = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", line)
    line = re.sub(r"\\([\\`*_{}\[\]()#+.!|>~-])", r"\1", line)
    line = re.sub(r"<[^>]+>", "", line)
    return html.unescape(line)


def markdown_to_plain_text(markdown: str) -> str:
    """Remove Markdown syntax while retaining headings, lists, tables and code."""
    source = str(markdown or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = source.split("\n")
    output: list[str] = []
    in_code = False
    for raw in lines:
        if _FENCE.match(raw):
            in_code = not in_code
            continue
        if in_code:
            output.append("  " + raw.rstrip())
            continue
        if _TABLE_RULE.match(raw):
            continue
        heading = _HEADING.match(raw)
        if heading:
            title = _inline_plain_text(heading.group(1)).strip()
            if title:
                output.extend([title, "─" * min(36, max(3, len(title)))])
            continue
        stripped = raw.strip()
        if not stripped:
            if output and output[-1] != "":
                output.append("")
            continue
        if re.fullmatch(r"(?:-{3,}|\*{3,}|_{3,})", stripped):
            output.append("──────────")
            continue
        if stripped.startswith(">"):
            quoted = re.sub(r"^>\s?", "", stripped)
            output.append("│ " + _inline_plain_text(quoted))
            continue
        item = _LIST.match(raw)
        if item:
            marker = item.group(2)
            prefix = f"{marker} " if marker[0].isdigit() else "• "
            output.append(item.group(1) + prefix + _inline_plain_text(item.group(3)))
            continue
        if "|" in stripped and stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            output.append("  " + "   |   ".join(_inline_plain_text(cell) for cell in cells))
            continue
        output.append(_inline_plain_text(raw).rstrip())

    result = "\n".join(output).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result
