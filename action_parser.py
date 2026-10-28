#!/usr/bin/env python3
"""
JSON tool call parser for NEMESIS CLI / Web / Telegram.

JSON is the required format in strict mode (enabled by the main CLI, Web and
Telegram loops). The permissive legacy path still accepts common XML-like tool-call
dialects emitted by local models, and repairs harmless JSON mistakes
(trailing commas, single quotes, unescaped newlines).

Strict mode NEVER executes a truncated call: NemApi cuts long answers in the
middle of the JSON, which used to produce a partial write announced as a
success. Instead it returns ``protocol_error`` so the loop can tell the model
to split the call into write_file + append chunks.

Supported call shapes (all equivalent after normalisation):
  {"tool": "name", "parameters": {...}}
  {"name": "name", "arguments": {...}}
  {"action": "name", "params": {...}}

Public API:
  ActionParser.parse(raw_response) -> {"text": str, "action": None | {"type": str, "content": dict}}
"""

from __future__ import annotations

import json
import re
import ast
from typing import Any, Dict, List, Optional, Set, Tuple

from src.core.system_prompt import DSML_PROTOCOL_ERROR, TRUNCATED_PROTOCOL_ERROR

_DSML_MARKER = re.compile(
    r"<\|\s*DSML\s*\|>|</?DSML\b|\bDSML\s*\|", re.I
)
_XML_TOOL_MARKER = re.compile(
    r"</?(?:tool_call|function|parameter)\b|<function\s*=", re.I
)
_TOOL_KEY_MARKER = re.compile(r'"(?:tool|name|action|type)"\s*:', re.I)


class ActionParser:
    """Strict JSON tool-call parser with aggressive repair for LLM output."""

    # Canonical tool names (underscores). Keep in sync with the executor.
    VALID_TOOLS: Set[str] = {
        "read_file",
        "write_file",
        "edit",
        "bash",
        "get_task_output",
        "kill_task",
        "list_dir",
        "grep",
        "glob",
        "git",
        "todo",
        "apply_patch",
        "web_search",
        "web_fetch",
        "delete_file",
        "mcp_list",
        "mcp_tools_list",
        "mcp_call",
        "list_agents",
        "delegate_task",
        "check_reports",
        # Legacy
        "write",
        "read",
        "replace",
        "append",
        "append_file",
        "schedule_task",
        "list_scheduled_tasks",
        "cancel_scheduled_task",
        "wait",
        "validate",
        "status",
        "kill_process",
        "stop_all",
        "cleanup_logs",
        "update_tracker",
        "skills_list",
        "search_replace",
    }

    # Map any historical / alternate name onto the canonical name.
    LEGACY_MAP: Dict[str, str] = {
        "search_replace": "edit",
        "search-replace": "edit",
        "replace": "edit",
        "write": "write_file",
        "read": "read_file",
        "read-file": "read_file",
        "write-file": "write_file",
        "get-task-output": "get_task_output",
        "kill-task": "kill_task",
        "list-dir": "list_dir",
        "web-search": "web_search",
        "web-fetch": "web_fetch",
        "delete-file": "delete_file",
        "append_file": "append",
        "append-file": "append",
    }

    def __init__(
        self,
        extra_valid_tools: Optional[Set[str]] = None,
        strict: bool = False,
    ):
        if extra_valid_tools:
            self.VALID_TOOLS = self.VALID_TOOLS | set(extra_valid_tools)
        # Strict mode is enabled by the main CLI/Web/Telegram loops: JSON only,
        # no DSML, no truncated call ever executed.
        self.strict = bool(strict)

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def parse(self, raw_response: str) -> Dict[str, Any]:
        """Parse a response, then apply the strict protocol guards if enabled."""
        result = self._parse_unchecked(raw_response)
        if not self.strict:
            return result
        return self._enforce_strict(raw_response, result)

    def _enforce_strict(self, raw_response: str, result: Dict[str, Any]) -> Dict[str, Any]:
        if not raw_response or not isinstance(raw_response, str):
            return result

        if _DSML_MARKER.search(raw_response) or _XML_TOOL_MARKER.search(raw_response):
            return self._protocol_error(raw_response, result, DSML_PROTOCOL_ERROR)
        if self._response_looks_truncated(raw_response):
            return self._protocol_error(raw_response, result, TRUNCATED_PROTOCOL_ERROR)
        for action in result.get("actions") or []:
            content = action.get("content") or {}
            if content and set(content.keys()) <= {"value", "_raw"}:
                return self._protocol_error(raw_response, result, TRUNCATED_PROTOCOL_ERROR)
        return result

    @staticmethod
    def _protocol_error(
        raw_response: str, result: Dict[str, Any], message: str
    ) -> Dict[str, Any]:
        text = re.sub(r"```.*", "", raw_response, flags=re.S)
        text = _DSML_MARKER.sub("", text)
        text = _XML_TOOL_MARKER.sub("", text)
        result["text"] = text.strip()
        result["actions"] = []
        result["action"] = None
        result["protocol_error"] = message
        return result

    def _response_looks_truncated(self, raw_response: str) -> bool:
        """Detect a provider that stopped mid-JSON instead of finishing the call."""
        if raw_response.count("```") % 2 == 1:
            # Only a fence that actually contains a tool-call object matters;
            # an unfinished prose/code example must stay a normal answer.
            tail = raw_response.rsplit("```", 1)[-1]
            if _TOOL_KEY_MARKER.search(tail):
                return True
        for match in re.finditer(
            r"```([a-zA-Z]*)\s*\n(.*?)(?:```|$)", raw_response, re.S
        ):
            label, body = match.group(1).lower(), match.group(2)
            if label and label != "json":
                continue
            if not _TOOL_KEY_MARKER.search(body):
                continue
            if not self._json_structure_complete(body):
                return True

        # NemApi coupe aussi les appels emis SANS bloc ```json : le parseur
        # permissif reconstruit alors un objet valide a partir d'un prefixe,
        # ce qui ecrirait un fichier partiel. Verifier l'equilibre general.
        if "```" not in raw_response and _TOOL_KEY_MARKER.search(raw_response):
            starts = [i for i, char in enumerate(raw_response) if char in "{["]
            if starts and not self._balanced_tool_object(raw_response, starts[0]):
                return True
        return False

    @staticmethod
    def _balanced_tool_object(text: str, start: int) -> bool:
        """True si une structure JSON complete demarre a ``start``.

        On s'arrete des que la structure exterieure se ferme, afin que la prose
        qui suit (guillemets, apostrophes) ne fausse pas l'analyse. Seules les
        chaines JSON a double quotes sont prises en compte.
        """
        pairs = {"{": "}", "[": "]"}
        stack: List[str] = []
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if escape:
                escape = False
                continue
            if char == "\\":
                if in_string:
                    escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char in pairs:
                stack.append(pairs[char])
            elif char in "}]":
                if not stack or stack.pop() != char:
                    return False
                if not stack:
                    return True
        return False

    @staticmethod
    def _json_structure_complete(text: str) -> bool:
        stack: List[str] = []
        in_string = False
        escape = False
        for char in text:
            if escape:
                escape = False
                continue
            if char == "\\":
                if in_string:
                    escape = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char in "{[":
                stack.append("}" if char == "{" else "]")
            elif char in "}]":
                if not stack or stack.pop() != char:
                    return False
        return not in_string and not stack

    def _parse_unchecked(self, raw_response: str) -> Dict[str, Any]:
        """Permissive extraction, kept for legacy/non-NemApi providers.

        Returns:
            {"text": str, "actions": list, "action": first action or None}
        """
        result: Dict[str, Any] = {"text": raw_response or "", "action": None, "actions": []}

        if not raw_response or not isinstance(raw_response, str):
            return result

        if raw_response.startswith("FEEDBACK:"):
            return result

        # Canonical batch form may be returned without a markdown fence.
        stripped = raw_response.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                actions = [self._validate_tool_obj(item) for item in parsed]
                actions = [item for item in actions if item]
                if actions:
                    result["text"] = ""
                    result["actions"] = actions
                    result["action"] = actions[0]
                    return result

        # 1. Accept XML-like tool calls emitted by several chat templates.
        xml_actions, xml_blocks = self._extract_xml_tool_calls(raw_response)
        if xml_actions:
            text = raw_response
            for block in xml_blocks:
                text = self._remove_matched_fence(text, block)
            result["text"] = self._strip_tool_markup(text)
            result["actions"] = xml_actions
            result["action"] = xml_actions[0]
            return result

        # Some providers return native tool calls alongside empty XML markers.
        # Strip those markers even when no valid XML action was extracted; never
        # show the model's wire protocol as assistant text.
        if self._contains_tool_markup(raw_response):
            result["text"] = self._strip_tool_markup(raw_response)

        # 2. Prefer fenced ```json ... ``` blocks
        actions, consumed = self._extract_actions_from_fences(raw_response)
        if actions:
            text = raw_response
            for fence in consumed:
                text = self._remove_matched_fence(text, fence)
            result["text"] = self._clean_text(text)
            result["actions"] = self._deduplicate(actions)
            result["action"] = actions[0]
            return result

        # 3. Any balanced JSON objects that look like tool calls. Multiple
        # sibling calls are preserved in source order.
        extracted = self._extract_tool_json_objects(raw_response)
        if extracted:
            text = raw_response
            for _, span in reversed(extracted):
                text = text[: span[0]] + text[span[1] :]
            actions = self._deduplicate([action for action, _ in extracted])
            result["text"] = self._clean_text(text)
            result["actions"] = actions
            result["action"] = actions[0]
            return result

        # Inline JSON is often truncated by providers when the generated file
        # body is large. Recover the largest valid tool object even when its
        # closing braces are missing.
        action, span = self._extract_largest_tool_json(raw_response)
        if action:
            text = raw_response[: span[0]] + raw_response[span[1] :]
            result["text"] = text.strip()
            result["actions"] = [action]
            result["action"] = action
            return result

        # Last format: tool-call syntax emitted as ``name(key=value, ...)``.
        actions, spans = self._extract_function_calls(raw_response)
        if actions:
            text = raw_response
            for start, end in reversed(spans):
                text = text[:start] + text[end:]
            actions = self._deduplicate(actions)
            result["text"] = self._clean_text(text)
            result["actions"] = actions
            result["action"] = actions[0]
            return result

        return result

    def _clean_text(self, text: str) -> str:
        """Remove all known wire-format residue while preserving user prose."""
        text = self._strip_tool_markup(text)
        text = re.sub(r"```(?:json|tool|xml)?\s*```", "", text, flags=re.I)
        text = re.sub(r"[ \t]+\n", "\n", text)
        return text.strip()

    @staticmethod
    def _deduplicate(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for action in actions:
            key = json.dumps(action, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                unique.append(action)
        return unique

    def _strip_tool_markup(self, text: str) -> str:
        """Remove orphan XML tool tags left by truncated model responses."""
        # Remove empty/incomplete tool-call containers first. This also handles
        # repeated empty blocks emitted before native ``tool_calls``.
        text = re.sub(r"<tool_call\b[^>]*>.*?</tool_call\s*>", "", text, flags=re.I | re.S)
        text = re.sub(r"<tool_call\b[^>]*>.*$", "", text, flags=re.I | re.S)
        text = re.sub(
            r"</?(?:tool_call|function|parameter)(?:\s*=\s*[^>]+)?\s*>",
            "",
            text,
            flags=re.I,
        )
        return text.strip()

    @staticmethod
    def _contains_tool_markup(text: str) -> bool:
        return bool(re.search(r"</?(?:tool_call|function|parameter)\b", text, flags=re.I))

    def _extract_xml_tool_calls(self, text: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Parse permissive ``<tool_call>`` blocks used by Qwen-style templates.

        Accepted examples include ``<function=bash>`` and
        ``<parameter=command>value</parameter>``. Parameters may contain
        newlines, shell operators, JSON, or arbitrary code.
        """
        actions: List[Dict[str, Any]] = []
        blocks: List[str] = []
        block_pattern = re.compile(
            r"<tool_call\b[^>]*>(.*?)(?:</tool_call\s*>|$)", re.I | re.S
        )
        function_pattern = re.compile(
            r"<function\s*=\s*['\"]?([^>\s'\"]+)['\"]?\s*>|"
            r"<function\s+name\s*=\s*['\"]([^'\"]+)['\"][^>]*>|"
            r"<function\s*>(.*?)</function\s*>",
            re.I | re.S,
        )
        parameter_pattern = re.compile(
            r"<parameter\s*=\s*['\"]?([^>\s'\"]+)['\"]?\s*>(.*?)"
            r"</parameter\s*>(?=\s*<parameter\s*=|</tool_call\s*>|$)",
            re.I | re.S,
        )

        for block_match in block_pattern.finditer(text):
            block = block_match.group(0)
            body = block_match.group(1)
            function_match = function_pattern.search(body)
            if not function_match:
                # Some templates put JSON directly inside <tool_call>.
                action, _ = self._extract_largest_tool_json(body)
                if action:
                    actions.append(action)
                    blocks.append(block)
                continue
            name = (
                function_match.group(1)
                or function_match.group(2)
                or function_match.group(3)
                or ""
            ).strip()
            params: Dict[str, Any] = {}
            for parameter_match in parameter_pattern.finditer(body):
                key = parameter_match.group(1).strip()
                value = parameter_match.group(2).strip()
                params[key] = value
            if not params:
                # Accept <parameter name="x"> and JSON-like attributes.
                for match in re.finditer(
                    r"<parameter\b[^>]*\bname\s*=\s*['\"]([^'\"]+)['\"][^>]*>(.*?)</parameter\s*>",
                    body, flags=re.I | re.S,
                ):
                    params[match.group(1)] = match.group(2).strip()
            action = self._validate_tool_obj({"tool": name, "parameters": params})
            if action:
                actions.append(action)
                blocks.append(block)
        return actions, blocks

    # ------------------------------------------------------------------
    # Fence extraction
    # ------------------------------------------------------------------

    def _extract_from_fences(self, text: str) -> Tuple[Optional[Dict], Optional[str]]:
        """Return (action, matched_fence_text) or (None, None)."""
        # ```json ... ```  or  ```JSON ... ```  or plain ``` ... ```
        patterns = [
            re.compile(r"```(?:json|JSON)\s*\n?(.*?)```", re.S),
            re.compile(r"```\s*\n?(\{.*?\})\s*```", re.S),
        ]
        for pat in patterns:
            for m in pat.finditer(text):
                candidate = m.group(1).strip()
                if not candidate.startswith("{"):
                    continue
                action = self._parse_json_candidate(candidate)
                if action:
                    return action, m.group(0)
        return None, None

    def _extract_actions_from_fences(self, text: str) -> Tuple[List[Dict], List[str]]:
        """Extract every valid tool call from fenced JSON blocks."""
        actions: List[Dict] = []
        fences: List[str] = []
        pattern = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)
        for match in pattern.finditer(text):
            candidate = match.group(1).strip()
            if candidate.startswith("["):
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    parsed_actions = [self._validate_tool_obj(item) for item in parsed]
                    parsed_actions = [item for item in parsed_actions if item]
                    if parsed_actions:
                        actions.extend(parsed_actions)
                        fences.append(match.group(0))
                        continue
                repaired = self._parse_json_array(candidate)
                if repaired:
                    actions.extend(repaired)
                    fences.append(match.group(0))
                    continue
            action = self._parse_json_candidate(candidate) if candidate.startswith("{") else None
            if action:
                actions.append(action)
                fences.append(match.group(0))
        return actions, fences

    def _parse_json_array(self, raw: str) -> List[Dict[str, Any]]:
        candidate = self._repair_truncated(self._repair_newlines_in_strings(raw.strip()))
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(value, list):
            return []
        return [action for item in value if (action := self._validate_tool_obj(item))]

    def _remove_matched_fence(self, full: str, fence: str) -> str:
        if not fence:
            return full.strip()
        return full.replace(fence, "", 1).strip()

    # ------------------------------------------------------------------
    # Largest balanced JSON extraction
    # ------------------------------------------------------------------

    def _extract_largest_tool_json(
        self, text: str
    ) -> Tuple[Optional[Dict], Tuple[int, int]]:
        """Scan for the largest balanced {...} that validates as a tool call."""
        starts = [i for i, c in enumerate(text) if c == "{"]
        best: Optional[Dict] = None
        best_span = (0, 0)

        for start in starts:
            end = self._find_matching_brace(text, start)
            if end < 0:
                # Possibly truncated – try repairing from start to end of text
                candidate = text[start:]
                action = self._parse_json_candidate(candidate)
                if action and (best is None or len(candidate) > best_span[1] - best_span[0]):
                    best = action
                    best_span = (start, len(text))
                continue

            candidate = text[start:end]
            action = self._parse_json_candidate(candidate)
            if action and (best is None or (end - start) > (best_span[1] - best_span[0])):
                best = action
                best_span = (start, end)

        return best, best_span

    def _extract_tool_json_objects(
        self, text: str
    ) -> List[Tuple[Dict[str, Any], Tuple[int, int]]]:
        """Extract non-overlapping sibling tool objects in source order."""
        candidates: List[Tuple[Dict[str, Any], Tuple[int, int]]] = []
        for start, ch in enumerate(text):
            if ch not in "{[":
                continue
            end = self._find_matching_delimiter(text, start)
            if end < 0:
                continue
            candidate = text[start:end]
            action = self._parse_json_candidate(candidate)
            if action:
                candidates.append((action, (start, end)))
            elif ch == "[":
                for item in self._parse_json_array(candidate):
                    candidates.append((item, (start, end)))

        # Keep outer candidates and discard nested parameter objects.
        selected: List[Tuple[Dict[str, Any], Tuple[int, int]]] = []
        for candidate in sorted(candidates, key=lambda item: (item[1][0], -(item[1][1] - item[1][0]))):
            start, end = candidate[1]
            if any(
                other[1] != candidate[1] and other[1][0] <= start and end <= other[1][1]
                for other in selected
            ):
                continue
            selected.append(candidate)
        return sorted(selected, key=lambda item: item[1][0])

    def _extract_function_calls(self, text: str) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]]]:
        """Parse safe ``tool_name(key=value)`` calls without executing code."""
        actions: List[Dict[str, Any]] = []
        spans: List[Tuple[int, int]] = []
        pattern = re.compile(
            r"\b(" + "|".join(map(re.escape, sorted(self.VALID_TOOLS, key=len, reverse=True))) + r")\s*\(",
            re.I,
        )
        for match in pattern.finditer(text):
            end = self._find_matching_delimiter(text, match.end() - 1, "(", ")")
            if end < 0:
                continue
            args_text = text[match.end():end - 1].strip()
            params: Dict[str, Any] = {}
            try:
                tree = ast.parse(f"f({args_text})", mode="eval").body
                for keyword in tree.keywords:
                    if keyword.arg is None:
                        continue
                    params[keyword.arg] = ast.literal_eval(keyword.value)
            except (SyntaxError, ValueError, TypeError, MemoryError):
                continue
            action = self._validate_tool_obj({"tool": match.group(1), "parameters": params})
            if action:
                actions.append(action)
                spans.append((match.start(), end))
        return actions, spans

    def _find_matching_brace(self, s: str, start: int) -> int:
        """Return index past the matching '}' or -1 if not found / unbalanced."""
        depth = 0
        in_string = False
        escape = False
        i = start
        while i < len(s):
            ch = s[i]
            if escape:
                escape = False
                i += 1
                continue
            if ch == "\\":
                escape = True
                i += 1
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                i += 1
                continue
            if in_string:
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return -1

    def _find_matching_delimiter(
        self, s: str, start: int, opening: Optional[str] = None, closing: Optional[str] = None
    ) -> int:
        """Find a balanced JSON/Python delimiter while respecting quoted strings."""
        if opening is None:
            opening = s[start]
            closing = "}" if opening == "{" else "]"
        assert closing is not None
        pairs = {"{": "}", "[": "]", "(": ")"}
        stack: List[str] = []
        quote: Optional[str] = None
        escape = False
        for index in range(start, len(s)):
            char = s[index]
            if quote:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char in pairs:
                stack.append(pairs[char])
            elif stack and char == stack[-1]:
                stack.pop()
                if not stack:
                    return index + 1
            elif stack and char in "}]":
                return -1
        return -1

    # ------------------------------------------------------------------
    # Core JSON parsing + progressive repair
    # ------------------------------------------------------------------

    def _parse_json_candidate(self, raw: str) -> Optional[Dict[str, Any]]:
        """Try strict parse, then a series of repairs."""
        s = raw.strip()
        if not s.startswith("{"):
            return None

        # Fast path
        try:
            obj = json.loads(s)
            return self._validate_tool_obj(obj)
        except (json.JSONDecodeError, ValueError):
            pass

        # Progressive repairs
        repairs = [
            self._repair_newlines_in_strings,
            self._repair_trailing_commas,
            self._repair_comments,
            self._repair_unquoted_keys,
            self._repair_single_quotes,
            self._repair_control_chars,
            self._repair_truncated,
            self._repair_python_escapes,
        ]

        # Apply repairs cumulatively and also try each in isolation
        candidate = s
        for repair in repairs:
            candidate = repair(candidate)
            try:
                obj = json.loads(candidate)
                result = self._validate_tool_obj(obj)
                if result:
                    return result
            except (json.JSONDecodeError, ValueError):
                continue

        # Last resort: extract the largest parsable substring after heavy repair
        heavily = self._repair_newlines_in_strings(
            self._repair_control_chars(self._repair_trailing_commas(s))
        )
        try:
            obj = json.loads(heavily)
            return self._validate_tool_obj(obj)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try to close and parse a prefix
        for length in range(len(heavily), max(len(heavily) // 2, 20), -1):
            prefix = heavily[:length]
            closed = self._repair_truncated(prefix)
            try:
                obj = json.loads(closed)
                result = self._validate_tool_obj(obj)
                if result:
                    return result
            except (json.JSONDecodeError, ValueError):
                continue

        return None

    # ------------------------------------------------------------------
    # Individual repair functions
    # ------------------------------------------------------------------

    def _repair_newlines_in_strings(self, s: str) -> str:
        """Escape real newlines / tabs / CRs that appear inside JSON strings."""
        result: List[str] = []
        in_string = False
        escape = False
        i = 0
        while i < len(s):
            ch = s[i]
            if escape:
                result.append(ch)
                escape = False
                i += 1
                continue
            if ch == "\\":
                result.append(ch)
                escape = True
                i += 1
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                i += 1
                continue
            if in_string:
                if ch == "\n":
                    result.append("\\n")
                elif ch == "\r":
                    result.append("\\r")
                elif ch == "\t":
                    result.append("\\t")
                else:
                    result.append(ch)
            else:
                result.append(ch)
            i += 1
        return "".join(result)

    def _repair_trailing_commas(self, s: str) -> str:
        s = re.sub(r",\s*}", "}", s)
        s = re.sub(r",\s*]", "]", s)
        return s

    def _repair_comments(self, s: str) -> str:
        s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
        s = re.sub(r"//[^\n]*", "", s)
        return s

    def _repair_unquoted_keys(self, s: str) -> str:
        return re.sub(
            r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)',
            r'\1"\2"\3',
            s,
        )

    def _repair_single_quotes(self, s: str) -> str:
        """Convert single-quoted strings that look like JSON delimiters."""
        # Only touch delimiters, not apostrophes inside already-double-quoted text.
        # Heuristic: 'key':  and  : 'value'  patterns.
        s = re.sub(r"'\s*:", '":', s)
        s = re.sub(r":\s*'", ': "', s)
        s = re.sub(r"'\s*,", '",', s)
        s = re.sub(r",\s*'", ', "', s)
        s = re.sub(r"\{\s*'", '{"', s)
        s = re.sub(r"'\s*\}", '"}', s)
        s = re.sub(r"\[\s*'", '["', s)
        s = re.sub(r"'\s*\]", '"]', s)
        return s

    def _repair_control_chars(self, s: str) -> str:
        """Remove or escape other control characters that break json.loads."""
        # Keep \t \n \r already handled; strip the rest of C0 controls.
        return "".join(
            ch if (ord(ch) >= 32 or ch in "\t\n\r") else " "
            for ch in s
        )

    def _repair_truncated(self, s: str) -> str:
        """Append missing closing braces / brackets."""
        depth_brace = 0
        depth_bracket = 0
        in_string = False
        escape = False
        for ch in s:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth_brace += 1
            elif ch == "}":
                depth_brace -= 1
            elif ch == "[":
                depth_bracket += 1
            elif ch == "]":
                depth_bracket -= 1
        # If we are still inside a string, close it
        if in_string:
            s += '"'
        s += "]" * max(depth_bracket, 0)
        s += "}" * max(depth_brace, 0)
        return s

    def _repair_python_escapes(self, s: str) -> str:
        """Handle common Python-style escapes that LLMs inject into JSON strings."""
        # Convert triple-double quotes that sometimes appear inside content
        s = s.replace('"""', '\\"\\"\\"')
        # Fix bare backslashes that are not valid JSON escapes
        # (keep known escapes: \" \\ \/ \b \f \n \r \t \uXXXX)
        def _fix_backslash(m: re.Match) -> str:
            nxt = m.group(1)
            if nxt in '"\\/bfnrtu':
                return m.group(0)
            # Invalid escape → double the backslash
            return "\\\\" + nxt

        s = re.sub(r'\\(.)', _fix_backslash, s)
        return s

    # ------------------------------------------------------------------
    # Validation / normalisation
    # ------------------------------------------------------------------

    def _normalize_tool_name(self, name: str) -> str:
        name = name.strip().replace("-", "_")
        return self.LEGACY_MAP.get(name, name)

    def _validate_tool_obj(self, obj: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(obj, dict):
            return None

        # Native-style batches can be serialized as {"tool_calls": [...]}.
        # The public parser exposes them through the normal actions list.
        if isinstance(obj.get("tool_calls"), list):
            return None

        tool_name = (
            obj.get("tool")
            or obj.get("name")
            or obj.get("action")
            or obj.get("tool_name")
            or obj.get("function")
            or ""
        )
        if not tool_name or not isinstance(tool_name, str):
            return None

        tool_name = self._normalize_tool_name(tool_name)
        if tool_name not in self.VALID_TOOLS:
            return None

        params = (
            obj.get("parameters")
            or obj.get("arguments")
            or obj.get("params")
            or obj.get("input")
            or obj.get("kwargs")
            or {}
        )
        if isinstance(params, str):
            try:
                decoded = json.loads(params)
                params = decoded if isinstance(decoded, dict) else {"value": decoded}
            except (json.JSONDecodeError, TypeError):
                params = {"value": params}
        if not isinstance(params, dict):
            params = {"value": params}

        # Alias parameter names for the edit tool (old_string / new_string stay canonical)
        if tool_name == "edit":
            if "old_string" not in params and "search" in params:
                params["old_string"] = params.pop("search")
            if "new_string" not in params and "replace" in params:
                params["new_string"] = params.pop("replace")

        return {"type": tool_name, "content": params}

    def get_valid_tools(self) -> Set[str]:
        return self.VALID_TOOLS.copy()
