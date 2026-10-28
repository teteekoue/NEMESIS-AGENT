# NEMESIS CLI — Tool Call Format

## Overview

NEMESIS CLI, Web and Telegram accept **only** the JSON tool-call format. The main loops
use `ActionParser(strict=True)`: JSON only, no DSML/XML dialect, and **no silent repair
of a truncated call**. A permissive legacy parser (`strict=False`) remains available
for other providers, but harmless JSON mistakes (trailing commas, single quotes) are
still tolerated in both modes.

No YAML, XML, or natural-language fallbacks are used by the main agent. See
[docs/TOOL_LIMITS.md](docs/TOOL_LIMITS.md) for the runtime limits.

## Hard size limit and long files

NemApi truncates answers between ~5.5 KB and ~6.8 KB. Every single tool call must stay
under **4000 characters of JSON** (hard ceiling 5000 enforced by `ToolBridge`, for all
tools). A long file is written as:

```json
{"tool": "write_file", "parameters": {"path": "long.txt", "content": "first chunk\n"}}
```

then, in separate messages (wait for FEEDBACK between each):

```json
{"tool": "append", "parameters": {"path": "long.txt", "content": "next chunk\n"}}
```

`append` accepts `file_path`, `path` or `filename`, creates the file and its parent
directories, and reports `bytes_written` / `total_bytes` so the final size can be
verified. Never use a heredoc inside a `bash` call.

## Forbidden dialects and protocol errors

`<|DSML|>`, `<DSML>`, `<tool_call>`, `<function=...>` and `<parameter=...>` are
forbidden. A strict parser response with `protocol_error` means **nothing was executed**:
the loop sends the error back as a correlated `FEEDBACK` block (`[call #N — …]`) and asks
the model to re-emit valid JSON or split the call. After three consecutive protocol
errors the turn stops.

## Canonical Format

```json
{
  "tool": "tool_name",
  "parameters": {
    "param1": "value1",
    "param2": "value2"
  }
}
```

Independent calls may be batched in one response:

```json
[
  {"tool": "read_file", "parameters": {"path": "src/app.py"}},
  {"tool": "grep", "parameters": {"pattern": "TODO", "path": "src"}}
]
```

Calls are executed in array order. Batch only independent operations; keep
dependent writes in separate turns so each result can be inspected first.

The block should appear inside a markdown code fence (` ```json ` … ` ``` `). Bare `{…}` objects are also detected when they form a valid tool call.

### Accepted key aliases

| Role        | Accepted keys                          |
|-------------|----------------------------------------|
| Tool name   | `tool`, `name`, `action`               |
| Arguments   | `parameters`, `arguments`, `params`    |

### Tool name normalisation

- Hyphens are converted to underscores (`web-search` → `web_search`).
- Legacy names are mapped: `search_replace` / `replace` → `edit`, `write` → `write_file`, `read` → `read_file`, etc.

## Escaping Rules

Inside every JSON string value you must escape:

| Character     | Escape sequence |
|---------------|-----------------|
| `"`           | `\"`            |
| `\`           | `\\`            |
| newline       | `\n`            |
| tab           | `\t`            |
| carriage return | `\r`          |

Example with Python code containing a docstring:

```json
{
  "tool": "write_file",
  "parameters": {
    "file_path": "hello.py",
    "content": "def greet(name):\n    \"\"\"Return a greeting.\"\"\"\n    return f\"Hello, {name}!\\n\""
  }
}
```

## Parser Repair Capabilities

When the LLM produces imperfect JSON the parser will attempt, in order:

1. Escape real newlines / tabs / CRs that appear inside string literals.
2. Remove trailing commas before `}` or `]`.
3. Strip `//` and `/* */` comments.
4. Quote unquoted object keys.
5. Convert single-quoted strings that look like delimiters.
6. Close truncated braces / brackets.
7. Fix common invalid backslash escapes.

These repairs are applied progressively so that large payloads containing code, paths with backslashes, or multi-line content still parse successfully.

## Examples

### edit (formerly search_replace)

```json
{
  "tool": "edit",
  "parameters": {
    "file_path": "src/config.py",
    "old_string": "DEBUG = False",
    "new_string": "DEBUG = True"
  }
}
```

### bash

```json
{
  "tool": "bash",
  "parameters": {
    "command": "ls -la /tmp | head -5"
  }
}
```

### read_file

```json
{
  "tool": "read_file",
  "parameters": {
    "path": "README.md",
    "offset": 1,
    "limit": 50
  }
}
```

## What Is No Longer Supported

- YAML-style named fences (` ```edit\n…\n``` `)
- XML `<ACTION type="…">…</ACTION>`
- Natural-language fallbacks ("I will use bash to …")

All tool calls must be valid (or repairable) JSON.
