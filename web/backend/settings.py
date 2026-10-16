"""Settings helpers: MCP servers and installed skills.

Thin wrappers around the CLI managers so the web UI can read and edit the exact
same files the terminal uses (``mcp_config.yaml`` and ``tools_library/``).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit


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
    from src.core.skills_manager import SkillManager
    from src.core.paths import tools_library_path
    from web.backend.config_store import load_config

    mgr = SkillManager(str(tools_library_path()))
    skills = mgr.list_installed()
    skill_config = load_config().get("skills", {})
    enabled = set(skill_config.get("enabled", [])) if isinstance(skill_config, dict) else set()
    out: List[Dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        out.append({
            "name": skill.get("name", "?"),
            "description": skill.get("description", ""),
            "version": skill.get("version", ""),
            "has_skill_md": bool(skill.get("has_skill_md")),
            "source": skill.get("source", ""),
            "enabled": skill.get("name") in enabled,
        })
    out.sort(key=lambda item: item["name"])
    return out


def set_skill_enabled(name: str, enabled: bool) -> List[Dict[str, Any]]:
    from src.core.skills_manager import SkillManager
    from src.core.paths import tools_library_path
    from web.backend.config_store import load_config, save_config

    name = str(name or "").strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name):
        raise ValueError("Nom de skill invalide.")
    manager = SkillManager(str(tools_library_path()))
    if manager.get_skill(name) is None:
        raise KeyError(f"Skill '{name}' introuvable.")
    config = load_config()
    skill_config = config.get("skills")
    if not isinstance(skill_config, dict):
        skill_config = {}
    names = set(skill_config.get("enabled", []))
    if enabled:
        names.add(name)
    else:
        names.discard(name)
    skill_config["enabled"] = sorted(names)
    config["skills"] = skill_config
    save_config(config)
    return list_skills()


def import_skill_content(content: str) -> Dict[str, str]:
    from src.core.skills_manager import SkillManager
    from src.core.paths import tools_library_path

    manager = SkillManager(str(tools_library_path()))
    ok, message = manager.install_skill_content(content)
    if not ok:
        raise ValueError(message)
    return {"message": message}


def import_skill_from_github(url: str) -> Dict[str, str]:
    import requests

    parsed = urlsplit(str(url or "").strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Port d'URL GitHub invalide.") from exc
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Utilisez une URL HTTPS GitHub directe vers un fichier SKILL.md.")
    if parsed.hostname == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if (
            len(parts) < 5
            or parts[2] != "blob"
            or parts[-1].lower() != "skill.md"
            or any(part in {".", ".."} for part in parts)
        ):
            raise ValueError("L'URL GitHub doit viser /owner/repo/blob/branche/.../SKILL.md.")
        raw_path = "/".join([parts[0], parts[1], *parts[3:]])
        parsed = urlsplit(urlunsplit(("https", "raw.githubusercontent.com", f"/{raw_path}", "", "")))
    elif parsed.hostname != "raw.githubusercontent.com":
        raise ValueError("Seules les URL github.com et raw.githubusercontent.com sont acceptées.")
    elif not parsed.path.lower().endswith("/skill.md"):
        raise ValueError("L'URL doit pointer directement vers un fichier SKILL.md.")

    with requests.get(
        urlunsplit(("https", parsed.hostname, parsed.path, "", "")),
        timeout=(5, 15),
        allow_redirects=False,
        stream=True,
    ) as response:
        if 300 <= response.status_code < 400:
            raise ValueError("Les redirections GitHub ne sont pas acceptées.")
        response.raise_for_status()
        content = response.raw.read(200_001).decode("utf-8")
    if len(content.encode("utf-8")) > 200_000:
        raise ValueError("Le fichier SKILL.md dépasse la limite de 200 Ko.")
    result = import_skill_content(content)
    result["source"] = url
    return result