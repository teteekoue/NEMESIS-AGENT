"""Session persistence for the NEMESIS web UI.

Sessions are lightweight transcripts stored as JSON under the user config
directory (``~/.config/nemesis-cli/web_sessions``). NEMAPI keeps the real
conversation context server-side, so a session here is a durable UI record
that can be reopened and displayed; continuing a reopened session reuses the
same NEMAPI chat when available.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.paths import USER_CONFIG_DIR

_LOCK = threading.RLock()
SESSIONS_DIR = USER_CONFIG_DIR / "web_sessions"


def _ensure_dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")
    return SESSIONS_DIR / f"{safe}.json"


def _now() -> float:
    return time.time()


def create_session(title: Optional[str] = None, model: str = "") -> Dict[str, Any]:
    with _LOCK:
        _ensure_dir()
        session = {
            "id": uuid.uuid4().hex[:12],
            "title": title or "Nouvelle conversation",
            "model": model,
            "created_at": _now(),
            "updated_at": _now(),
            "messages": [],
            # Whether the server-side NEMAPI conversation has already received
            # the system prompt. Used to resume a session without ever resending
            # the prompt or starting a new NEMAPI chat.
            "nemapi_initialized": False,
        }
        _write(session)
        return session


def _write(session: Dict[str, Any]) -> None:
    session["updated_at"] = _now()
    with open(_path(session["id"]), "w", encoding="utf-8") as handle:
        json.dump(session, handle, ensure_ascii=False, indent=2)


def save_session(session: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        _ensure_dir()
        session.setdefault("id", uuid.uuid4().hex[:12])
        session.setdefault("created_at", _now())
        session.setdefault("title", "Nouvelle conversation")
        _write(session)
        return session


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        _ensure_dir()
        path = _path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None


def list_sessions() -> List[Dict[str, Any]]:
    with _LOCK:
        _ensure_dir()
        sessions: List[Dict[str, Any]] = []
        for path in SESSIONS_DIR.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            sessions.append({
                "id": data.get("id", path.stem),
                "title": data.get("title", "Conversation"),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", 0),
                "updated_at": data.get("updated_at", 0),
                "message_count": len(data.get("messages", [])),
            })
        sessions.sort(key=lambda item: item.get("updated_at", 0), reverse=True)
        return sessions


def append_message(session_id: str, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _LOCK:
        session = get_session(session_id)
        if session is None:
            return None
        session.setdefault("messages", []).append(message)
        _write(session)
        return session


def set_title(session_id: str, title: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        session = get_session(session_id)
        if session is None:
            return None
        session["title"] = title
        _write(session)
        return session


def mark_initialized(session_id: str) -> Optional[Dict[str, Any]]:
    """Flag that the NEMAPI conversation already has its system prompt."""
    with _LOCK:
        session = get_session(session_id)
        if session is None:
            return None
        session["nemapi_initialized"] = True
        _write(session)
        return session


def is_initialized(session_id: str) -> bool:
    if not session_id:
        return False
    session = get_session(session_id)
    return bool(session and session.get("nemapi_initialized"))


def delete_session(session_id: str) -> bool:
    """Archive a session by moving it to ``web_sessions/_deleted``."""
    with _LOCK:
        _ensure_dir()
        path = _path(session_id)
        if not path.exists():
            return False
        archive = SESSIONS_DIR / "_deleted"
        archive.mkdir(exist_ok=True)
        path.replace(archive / path.name)
        return True
