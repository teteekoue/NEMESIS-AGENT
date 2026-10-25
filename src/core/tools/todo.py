"""Persistent todo list for multi-step agent tasks."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any


TODO_FILENAME = ".nemesis_todos.json"


@dataclass
class TodoItem:
    id: str
    content: str
    status: str = "pending"  # pending | in_progress | completed | cancelled
    created_at: str = ""
    updated_at: str = ""


DESCRIPTION_FULL = """Manage a structured todo list shared by every session.
Actions:
- list: show all todos
- add: add one or more items (content string, or items list)
- update: change status/content of an item by id
- clear: remove completed/cancelled items (or all if force=true)"""


def _path(workspace_dir: Optional[str] = None) -> str:
    """Todo list lives in the user config dir: it no longer depends on a cwd."""
    from ..paths import USER_CONFIG_DIR, ensure_user_dirs

    try:
        ensure_user_dirs()
        base = USER_CONFIG_DIR
    except OSError:
        base = None
    if base is not None:
        return os.path.join(str(base), TODO_FILENAME)
    return os.path.join(workspace_dir or os.getcwd(), TODO_FILENAME)


def _legacy_path(workspace_dir: Optional[str]) -> Optional[str]:
    if not workspace_dir:
        return None
    legacy = os.path.join(str(workspace_dir), TODO_FILENAME)
    return legacy if os.path.isfile(legacy) else None


def _load(workspace_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    p = _path(workspace_dir)
    if not os.path.isfile(p):
        legacy = _legacy_path(workspace_dir)
        if not legacy:
            return []
        p = legacy
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save(workspace_dir: Optional[str], items: List[Dict[str, Any]]) -> None:
    p = _path(workspace_dir)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
    except OSError:
        legacy = _legacy_path(workspace_dir) or os.path.join(
            workspace_dir or os.getcwd(), TODO_FILENAME
        )
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _format(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "Todo list is empty."
    lines = ["TODO LIST", "-" * 40]
    for it in items:
        st = it.get("status", "pending")
        mark = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]", "cancelled": "[-]"}.get(st, "[?]")
        lines.append(f"{mark} {it.get('id', '?')}: {it.get('content', '')}  ({st})")
    return "\n".join(lines)


def todo(
    action: str,
    workspace_dir: Optional[str] = None,
    content: Optional[str] = None,
    items: Optional[List[str]] = None,
    id: Optional[str] = None,
    status: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    action = (action or "list").lower().strip()
    data = _load(workspace_dir)

    if action == "list":
        return {"success": True, "stdout": _format(data), "items": data}

    if action == "add":
        to_add: List[str] = []
        if content:
            to_add.append(content)
        if items:
            to_add.extend([str(x) for x in items if x])
        if not to_add:
            return {"success": False, "stdout": "Provide content or items to add."}
        for text in to_add:
            tid = str(uuid.uuid4())[:8]
            now = _now()
            data.append({
                "id": tid,
                "content": text,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            })
        _save(workspace_dir, data)
        return {"success": True, "stdout": _format(data), "items": data}

    if action == "update":
        if not id:
            return {"success": False, "stdout": "id is required for update."}
        found = False
        for it in data:
            if it.get("id") == id:
                found = True
                if content is not None:
                    it["content"] = content
                if status is not None:
                    if status not in ("pending", "in_progress", "completed", "cancelled"):
                        return {"success": False, "stdout": f"Invalid status: {status}"}
                    it["status"] = status
                it["updated_at"] = _now()
                break
        if not found:
            return {"success": False, "stdout": f"Todo id '{id}' not found."}
        _save(workspace_dir, data)
        return {"success": True, "stdout": _format(data), "items": data}

    if action == "clear":
        if force:
            data = []
        else:
            data = [it for it in data if it.get("status") not in ("completed", "cancelled")]
        _save(workspace_dir, data)
        return {"success": True, "stdout": _format(data), "items": data}

    return {"success": False, "stdout": f"Unknown action '{action}'. Use: list, add, update, clear"}
