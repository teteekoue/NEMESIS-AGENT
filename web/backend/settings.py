"""Settings helpers: MCP servers and installed skills.

Thin wrappers around the CLI managers so the web UI can read and edit the exact
same files the terminal uses (``mcp_config.yaml`` and ``tools_library/``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def list_mcp_servers() -> List[Dict[str, Any]]:
    from src.core.mcp_manager import MCPManager
    from src.core.paths import mcp_config_path

    mgr = MCPManager(str(mcp_config_path()))
    servers = mgr.list_servers()
    out: List[Dict[str, Any]] = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        out.append({
            "name": name,
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []) or [],
            "description": cfg.get("description", ""),
            "env": list((cfg.get("env") or {}).keys()),
        })
    out.sort(key=lambda item: item["name"])
    return out


def add_mcp_server(
    name: str,
    command: str,
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    description: str = "",
) -> Dict[str, Any]:
    from src.core.mcp_manager import MCPManager
    from src.core.paths import mcp_config_path

    mgr = MCPManager(str(mcp_config_path()))
    ok, message = mgr.add_server(name, command, env=env, description=description, args=args)
    return {"ok": ok, "message": message}


def remove_mcp_server(name: str) -> Dict[str, Any]:
    from src.core.mcp_manager import MCPManager
    from src.core.paths import mcp_config_path

    mgr = MCPManager(str(mcp_config_path()))
    ok, message = mgr.remove_server(name)
    return {"ok": ok, "message": message}


def memory_state() -> Dict[str, Any]:
    from src.core.memory_store import load_blocks, load_prefs

    prefs = load_prefs()
    blocks = sorted(load_blocks(), key=lambda b: float(b.get("updated_at", 0.0) or 0.0), reverse=True)
    return {"enabled": prefs.enabled, "blocks": blocks}


def memory_upsert(
    block_id: Optional[str] = None,
    key: Optional[str] = None,
    title: str = "",
    content: str = "",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from src.core.memory_store import add_block, get_block, upsert_block, load_blocks

    try:
        if block_id:
            existing = get_block(block_id)
            if existing is None:
                return {"ok": False, "message": "Bloc introuvable."}
            import json as _json

            if not existing.get("key"):
                existing["key"] = block_id
            upsert_block(existing.get("key") or block_id, title, content, tags)
            return {"ok": True}
        if key:
            upsert_block(key, title, content, tags)
            return {"ok": True}
        if not title and not content:
            return {"ok": False, "message": "Titre ou contenu requis."}
        add_block(title, content, tags)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def memory_delete(block_id: str) -> Dict[str, Any]:
    from src.core.memory_store import delete_block

    try:
        ok = delete_block(block_id)
        return {"ok": ok, "message": "" if ok else "Bloc introuvable."}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def memory_clear() -> Dict[str, Any]:
    from src.core.memory_store import clear_all

    try:
        clear_all()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def memory_set_enabled(enabled: bool) -> Dict[str, Any]:
    from src.core.memory_store import set_enabled

    try:
        set_enabled(bool(enabled))
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


def list_skills() -> List[Dict[str, Any]]:
    try:
        from src.core.skills_manager import SkillManager
        from src.core.paths import tools_library_path

        mgr = SkillManager(str(tools_library_path()))
        skills = mgr.list_installed()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        out.append({
            "name": skill.get("name", "?"),
            "description": skill.get("description", ""),
            "version": skill.get("version", ""),
            "has_skill_md": bool(skill.get("has_skill_md")),
            "path": skill.get("path", ""),
        })
    out.sort(key=lambda item: item["name"])
    return out