from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.paths import USER_CONFIG_DIR

_LOCK = threading.RLock()

MEM_DIR = Path(USER_CONFIG_DIR) / "memory"


def _ensure() -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)


def _prefs_path() -> Path:
    return MEM_DIR / "prefs.json"


def _blocks_path() -> Path:
    return MEM_DIR / "blocks.json"


def _now() -> float:
    return time.time()


def _load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


@dataclass
class MemoryPrefs:
    enabled: bool = True
    updated_at: float = 0.0


def load_prefs() -> MemoryPrefs:
    with _LOCK:
        _ensure()
        raw = _load_json(_prefs_path(), {})
        return MemoryPrefs(enabled=bool(raw.get("enabled", True)), updated_at=float(raw.get("updated_at", 0.0) or 0.0))


def set_enabled(enabled: bool) -> MemoryPrefs:
    with _LOCK:
        _ensure()
        prefs = load_prefs()
        prefs.enabled = bool(enabled)
        prefs.updated_at = _now()
        _save_json(_prefs_path(), {"enabled": prefs.enabled, "updated_at": prefs.updated_at})
        return prefs


def load_blocks() -> List[Dict[str, Any]]:
    with _LOCK:
        _ensure()
        blocks = _load_json(_blocks_path(), [])
        if not isinstance(blocks, list):
            return []
        out: List[Dict[str, Any]] = []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            out.append(b)
        return out


def _normalize_block(block: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    block = dict(block)
    block.setdefault("id", uuid.uuid4().hex[:12])
    block.setdefault("created_at", now)
    block.setdefault("updated_at", now)
    block.setdefault("key", "")
    block.setdefault("title", "")
    block.setdefault("content", "")
    block.setdefault("tags", [])
    if not isinstance(block["tags"], list):
        block["tags"] = []
    return block


def upsert_block(key: str, title: str, content: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    with _LOCK:
        _ensure()
        key = (key or "").strip()
        title = (title or "").strip()
        content = (content or "").strip()
        tags = tags or []
        if not isinstance(tags, list):
            tags = []
        blocks = load_blocks()
        existing = None
        for b in blocks:
            if str(b.get("key", "")) == key and key:
                existing = b
                break
        now = _now()
        if existing is None:
            b = {
                "id": uuid.uuid4().hex[:12],
                "key": key,
                "title": title,
                "content": content,
                "tags": tags,
                "created_at": now,
                "updated_at": now,
            }
            blocks.append(b)
            result = b
        else:
            existing["title"] = title or existing.get("title", "")
            existing["content"] = content or existing.get("content", "")
            existing["tags"] = tags
            existing["updated_at"] = now
            result = existing
        _save_json(_blocks_path(), blocks)
        return result


def add_block(title: str, content: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    with _LOCK:
        _ensure()
        title = (title or "").strip()
        content = (content or "").strip()
        tags = tags or []
        if not isinstance(tags, list):
            tags = []
        now = _now()
        b = {
            "id": uuid.uuid4().hex[:12],
            "key": "",
            "title": title,
            "content": content,
            "tags": tags,
            "created_at": now,
            "updated_at": now,
        }
        blocks = load_blocks()
        blocks.append(b)
        _save_json(_blocks_path(), blocks)
        return b


def get_block(block_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        for b in load_blocks():
            if str(b.get("id")) == str(block_id):
                return b
        return None


def delete_block(block_id: str) -> bool:
    with _LOCK:
        blocks = load_blocks()
        before = len(blocks)
        blocks = [b for b in blocks if str(b.get("id")) != str(block_id)]
        if len(blocks) == before:
            return False
        _save_json(_blocks_path(), blocks)
        return True


def clear_all() -> None:
    with _LOCK:
        _ensure()
        _save_json(_blocks_path(), [])


def search_blocks(query: str, tags: Optional[List[str]] = None, limit: int = 10) -> List[Dict[str, Any]]:
    with _LOCK:
        q = (query or "").strip().lower()
        tags = tags or []
        if not isinstance(tags, list):
            tags = []
        blocks = load_blocks()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for b in blocks:
            text = f"{b.get('key','')}\n{b.get('title','')}\n{b.get('content','')}\n{' '.join(b.get('tags') or [])}".lower()
            score = 0.0
            if q:
                if q in text:
                    score += 10.0
                else:
                    for tok in q.split():
                        if tok and tok in text:
                            score += 1.0
            if tags:
                bt = set((b.get("tags") or []))
                matches = sum(1 for t in tags if t in bt)
                score += float(matches) * 2.0
            scored.append((score, b))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = [b for s, b in scored if s > 0.0]
        return out[: max(1, int(limit) if limit else 10)]


def build_memory_message(max_blocks: int = 12) -> str:
    with _LOCK:
        prefs = load_prefs()
        if not prefs.enabled:
            return ""
        blocks = load_blocks()
        blocks = sorted(blocks, key=lambda b: float(b.get("updated_at", 0.0) or 0.0), reverse=True)
        blocks = blocks[: max(1, int(max_blocks))]
        if not blocks:
            return ""
        lines = ["MEMOIRE UTILISATEUR (partagée, persistante)"]
        for b in blocks:
            title = str(b.get("title") or b.get("key") or "")
            content = str(b.get("content") or "")
            blk_id = str(b.get("id") or "")
            lines.append(f"- [{blk_id}] {title}: {content}")
        return "\n".join(lines)
