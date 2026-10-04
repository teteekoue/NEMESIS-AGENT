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


DESCRIPTION_FULL = """Manage a structured todo list for the current workspace.
Actions:
- list: show all todos
- add: add one or more items (content string, or items list)
- set: replace the whole plan (items list of strings or {content,status})
- update: change status/content of an item by id
- clear: remove completed/cancelled items (or all if force=true)
Keep exactly one item in_progress."""


def _path(workspace_dir: str) -> str:
    return os.path.join(workspace_dir, TODO_FILENAME)


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
    workspace_dir: str,
    content: Optional[str] = None,
    items: Optional[list] = None,
    id: Optional[str] = None,
    status: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    action = (action or "list").lower().strip()
    data = _load(workspace_dir)

    if action == "list":
        return {"success": True, "stdout": _format(data), "items": data}

    if action == "set":
        if not items:
            return {"success": False, "stdout": "Provide items to set the plan."}
        valid_status = ("pending", "in_progress", "completed", "cancelled")
        new_data: List[Dict[str, Any]] = []
        for entry in items:
            if isinstance(entry, dict):
                text = str(entry.get("content") or entry.get("text") or "").strip()
                st = entry.get("status") or "pending"
            else:
                text = str(entry).strip()
                st = "pending"
            if not text:
                continue
            if st not in valid_status:
                st = "pending"
            now = _now()
            new_data.append({
                "id": str(uuid.uuid4())[:8],
                "content": text,
                "status": st,
                "created_at": now,
                "updated_at": now,
            })
        # Enforce a single active step.
        seen_active = False
        for it in new_data:
            if it["status"] == "in_progress":
                if seen_active:
                    it["status"] = "pending"
                else:
                    seen_active = True
        _save(workspace_dir, new_data)
        return {"success": True, "stdout": _format(new_data), "items": new_data}

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
        if status is not None and status not in ("pending", "in_progress", "completed", "cancelled"):
            return {"success": False, "stdout": f"Invalid status: {status}"}
        found = False
        for it in data:
            if it.get("id") == id:
                found = True
                if content is not None:
                    it["content"] = content
                if status is not None:
                    it["status"] = status
                it["updated_at"] = _now()
                break
        if not found:
            return {"success": False, "stdout": f"Todo id '{id}' not found."}
        # Enforce a single active step so the plan stays unambiguous.
        if status == "in_progress":
            for it in data:
                if it.get("id") != id and it.get("status") == "in_progress":
                    it["status"] = "pending"
                    it["updated_at"] = _now()
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
