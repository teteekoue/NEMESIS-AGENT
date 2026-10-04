"""Long-term memory store for NEMESIS.

A lightweight, file-backed key/value memory with keyword search. It persists
facts, decisions and project knowledge across sessions so the agent does not
need the whole conversation history in context.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional


MEMORY_FILENAME = ".nemesis_memory.json"


DESCRIPTION_FULL = """Persistent long-term memory.
Actions:
- set: store a memory (content required; optional tags list, key)
- get: retrieve a memory by id or exact key
- search: keyword search across stored memories (query required)
- list: list recent memories
- delete: remove a memory by id
Use this to remember durable facts, project conventions and decisions."""


def _path(workspace_dir: str) -> str:
    return os.path.join(workspace_dir, MEMORY_FILENAME)


def _load(workspace_dir: str) -> List[Dict[str, Any]]:
    p = _path(workspace_dir)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(workspace_dir: str, items: List[Dict[str, Any]]) -> None:
    p = _path(workspace_dir)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _tokens(text: str) -> List[str]:
    return [t for t in re.split(r"\W+", (text or "").lower()) if len(t) > 1]


def _format(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "Memory is empty."
    lines = ["MEMORY", "-" * 40]
    for it in items:
        tags = it.get("tags") or []
        tag_str = f" [tags: {', '.join(tags)}]" if tags else ""
        lines.append(f"{it.get('id')}: {it.get('content')}{tag_str}")
    return "\n".join(lines)


def memory(
    action: str,
    workspace_dir: str,
    content: Optional[str] = None,
    query: Optional[str] = None,
    key: Optional[str] = None,
    tags: Optional[List[str]] = None,
    id: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    action = (action or "list").lower().strip()
    data = _load(workspace_dir)

    if action == "list":
        rows = list(reversed(data))[: max(1, limit)]
        return {"success": True, "stdout": _format(rows), "memories": rows}

    if action == "set":
        if not content:
            return {"success": False, "stdout": "content is required for set."}
        # Upsert by key when provided, otherwise create.
        existing = None
        if key:
            existing = next((it for it in data if it.get("key") == key), None)
        if existing:
            existing["content"] = content
            existing["tags"] = [str(t) for t in (tags or existing.get("tags") or [])]
            existing["updated_at"] = _now()
            saved = existing
        else:
            saved = {
                "id": str(uuid.uuid4())[:8],
                "key": key,
                "content": content,
                "tags": [str(t) for t in (tags or [])],
                "created_at": _now(),
                "updated_at": _now(),
            }
            data.append(saved)
        _save(workspace_dir, data)
        return {"success": True, "stdout": f"Memory saved ({saved['id']}).", "memory": saved}

    if action == "get":
        target = id or key
        if not target:
            return {"success": False, "stdout": "id or key is required for get."}
        found = next((it for it in data if it.get("id") == target or it.get("key") == target), None)
        if not found:
            return {"success": False, "stdout": f"Memory '{target}' not found."}
        return {"success": True, "stdout": _format([found]), "memory": found}

    if action == "search":
        if not query:
            return {"success": False, "stdout": "query is required for search."}
        q_tokens = set(_tokens(query))
        if not q_tokens:
            return {"success": True, "stdout": "Memory search: empty query.", "memories": []}
        scored = []
        for it in data:
            text = it.get("content", "")
            hay = set(_tokens(text + " " + " ".join(it.get("tags") or [])))
            overlap = len(q_tokens & hay)
            if overlap:
                scored.append((overlap, it))
        scored.sort(key=lambda x: (-x[0], x[1].get("updated_at", "")))
        rows = [it for _, it in scored[: max(1, limit)]]
        return {"success": True, "stdout": _format(rows) if rows else "No matching memories.", "memories": rows}

    if action == "delete":
        if not id:
            return {"success": False, "stdout": "id is required for delete."}
        remaining = [it for it in data if it.get("id") != id]
        if len(remaining) == len(data):
            return {"success": False, "stdout": f"Memory id '{id}' not found."}
        _save(workspace_dir, remaining)
        return {"success": True, "stdout": f"Memory {id} deleted."}

    if action == "clear":
        count = len(data)
        _save(workspace_dir, [])
        return {"success": True, "stdout": f"Mémoire vidée ({count} entrée(s))."}

    return {"success": False, "stdout": f"Unknown action '{action}'. Use: set, get, search, list, delete, clear"}