"""FastAPI application exposing the NEMESIS agent to the web UI."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from requests import RequestException

from web.backend import sessions as session_store
from web.backend import config_store
from web.backend import settings as settings_store
from web.backend import telegram_settings as telegram_store
from web.backend import github_settings as github_store
from web.backend.agent_runner import AgentRunner, list_models, list_tools
from web.backend.browser_relay import HUB as RELAY_HUB
from web.backend.terminal import PtySession
from web.backend.config_store import (
    public_config,
    update_config,
    workspace_path,
    load_config,
)

app = FastAPI(title="NEMESIS Web API", version="1.0.0")

# The shared shell directory starts at the configured starting point (or the
# process directory). It is never a sandbox: any path can be used afterwards.
try:
    from src.core.paths import set_session_cwd

    set_session_cwd(workspace_path())
except Exception:
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_BYTES = 512_000


# ----------------------------------------------------------------------
# schemas
# ----------------------------------------------------------------------
class ConfigPatch(BaseModel):
    provider: Optional[Dict[str, Any]] = None
    nemapi: Optional[Dict[str, Any]] = None
    security: Optional[Dict[str, Any]] = None

    model_config = {"extra": "ignore"}


class SubagentsConfigIn(BaseModel):
    provider: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    name: Optional[str] = None
    id: Optional[str] = None
    max_concurrent: int = 4
    max_iterations: int = 0
    timeout_seconds: int = 180
    max_retries: int = 3
    max_tokens_per_task: int = 24000
    api_key: Optional[str] = None
    clear_api_key: bool = False

    model_config = {"extra": "ignore"}


class ModelChoice(BaseModel):
    model: str


class SessionCreate(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class SessionPatch(BaseModel):
    title: str


class MessageIn(BaseModel):
    message: Dict[str, Any]


class McpServerIn(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    description: str = ""


class SkillEnabledIn(BaseModel):
    name: str
    enabled: bool


class SkillImportIn(BaseModel):
    content: Optional[str] = Field(default=None, max_length=200_000)
    filename: Optional[str] = Field(default=None, max_length=255)
    url: Optional[str] = Field(default=None, max_length=2048)


class MemoryBlockIn(BaseModel):
    id: Optional[str] = None
    key: Optional[str] = None
    title: str = ""
    content: str = ""
    tags: Optional[List[str]] = None


class MemoryToggleIn(BaseModel):
    enabled: bool


class TelegramConfigIn(BaseModel):
    token: Optional[str] = None
    bot_name: Optional[str] = None
    workspace: Optional[str] = None
    nemapi: Optional[Dict[str, Any]] = None


class GithubConfigIn(BaseModel):
    token: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    login: Optional[str] = None


# ----------------------------------------------------------------------
# health & config
# ----------------------------------------------------------------------
@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "app": "nemesis-web"}


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    return public_config()


@app.patch("/api/config")
def patch_config(patch: ConfigPatch) -> Dict[str, Any]:
    data = patch.model_dump(exclude_none=True)
    if "nemapi" in data and data["nemapi"] is not None:
        data["nemapi"] = {k: v for k, v in data["nemapi"].items() if v is not None}
    update_config(data)
    return public_config()


@app.get("/api/subagents")
def get_subagents_config() -> Dict[str, Any]:
    return config_store.public_subagents_config()


@app.put("/api/subagents")
def put_subagents_config(payload: SubagentsConfigIn) -> Dict[str, Any]:
    try:
        return config_store.save_subagents_config(payload.model_dump(exclude_none=True))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/subagents/profiles")
def post_subagent_profile(payload: SubagentsConfigIn) -> Dict[str, Any]:
    try:
        return config_store.save_subagent_profile(payload.model_dump(exclude_none=True))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/subagents/profiles/{profile_id}")
def remove_subagent_profile(profile_id: str) -> Dict[str, Any]:
    try:
        return config_store.delete_subagent_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/models")
def get_models() -> Dict[str, Any]:
    return list_models(load_config())


@app.get("/api/relay")
def relay_status() -> Dict[str, Any]:
    config = load_config().get("nemapi", {})
    return {
        **RELAY_HUB.stats(),
        "configured": bool(config.get("browser_relay")),
        "url": str(config.get("url") or ""),
    }


@app.post("/api/models")
def choose_model(choice: ModelChoice) -> Dict[str, Any]:
    saved = update_config({"provider": {"model": choice.model}})
    return {"ok": True, "model": saved["provider"]["model"]}


@app.get("/api/tools")
def get_tools() -> Dict[str, Any]:
    return {"tools": list_tools(load_config())}


# ----------------------------------------------------------------------
# MCP servers & skills
# ----------------------------------------------------------------------
@app.get("/api/mcp")
def get_mcp_servers() -> Dict[str, Any]:
    return {"servers": settings_store.list_mcp_servers()}


@app.post("/api/mcp")
def create_mcp_server(payload: McpServerIn) -> Dict[str, Any]:
    result = settings_store.add_mcp_server(
        payload.name,
        payload.command,
        args=payload.args,
        env=payload.env,
        description=payload.description,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Échec."))
    return {"servers": settings_store.list_mcp_servers(), "message": result.get("message")}


@app.delete("/api/mcp/{name}")
def delete_mcp_server(name: str) -> Dict[str, Any]:
    result = settings_store.remove_mcp_server(name)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "Introuvable."))
    return {"servers": settings_store.list_mcp_servers(), "message": result.get("message")}


@app.get("/api/skills")
def get_skills() -> Dict[str, Any]:
    return {"skills": settings_store.list_skills()}


@app.put("/api/skills")
def put_skill_enabled(payload: SkillEnabledIn) -> Dict[str, Any]:
    try:
        return {"skills": settings_store.set_skill_enabled(payload.name, payload.enabled)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/skills/import")
def post_skill_import(payload: SkillImportIn) -> Dict[str, Any]:
    try:
        if payload.url:
            result = settings_store.import_skill_from_github(payload.url)
        elif payload.content and (payload.filename or "").rsplit("/", 1)[-1].lower() == "skill.md":
            result = settings_store.import_skill_content(payload.content)
        else:
            raise ValueError("Sélectionnez un fichier SKILL.md ou fournissez une URL GitHub.")
        return {"skills": settings_store.list_skills(), **result}
    except (RequestException, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Import GitHub échoué : {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# memory (persistent mini-blocks shared with the Telegram bot)
# ----------------------------------------------------------------------
@app.get("/api/memory")
def get_memory() -> Dict[str, Any]:
    return settings_store.memory_state()


@app.post("/api/memory")
def upsert_memory(payload: MemoryBlockIn) -> Dict[str, Any]:
    result = settings_store.memory_upsert(
        block_id=payload.id,
        key=payload.key,
        title=payload.title,
        content=payload.content,
        tags=payload.tags,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message", "Échec."))
    return settings_store.memory_state()


@app.delete("/api/memory/{block_id}")
def delete_memory(block_id: str) -> Dict[str, Any]:
    result = settings_store.memory_delete(block_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("message", "Introuvable."))
    return settings_store.memory_state()


@app.post("/api/memory/clear")
def clear_memory() -> Dict[str, Any]:
    settings_store.memory_clear()
    return settings_store.memory_state()


# ----------------------------------------------------------------------
# telegram bot settings (shared token + memory + full authorization)
# ----------------------------------------------------------------------
@app.get("/api/telegram")
def get_telegram() -> Dict[str, Any]:
    return telegram_store.public_telegram_config()


@app.post("/api/telegram")
def set_telegram(payload: TelegramConfigIn) -> Dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    return telegram_store.save_telegram_config(data)


@app.post("/api/memory/toggle")
def toggle_memory(payload: MemoryToggleIn) -> Dict[str, Any]:
    settings_store.memory_set_enabled(payload.enabled)
    return settings_store.memory_state()


# ----------------------------------------------------------------------
# github connection (token + identity) backed by the shared config
# ----------------------------------------------------------------------
@app.get("/api/github")
def get_github() -> Dict[str, Any]:
    return github_store.public_github_config()


@app.post("/api/github")
def set_github(payload: GithubConfigIn) -> Dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    return github_store.save_github_config(data)


# ----------------------------------------------------------------------
# workspace files
# ----------------------------------------------------------------------
def _resolve_workspace_path(rel: Optional[str]) -> Path:
    """Resolve any path on the host: the explorer is no longer sandboxed."""
    from src.core.paths import resolve_path

    raw = (rel or "").strip()
    if not raw:
        return workspace_path()
    return Path(resolve_path(raw))


def _display_path(target: Path) -> str:
    """Relative label when the path is under the starting dir, absolute otherwise."""
    try:
        return str(target.relative_to(workspace_path()))
    except ValueError:
        return str(target)


@app.get("/api/files")
def list_files(path: str = Query("")) -> Dict[str, Any]:
    target = _resolve_workspace_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Chemin introuvable.")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Ce chemin n'est pas un dossier.")
    entries: List[Dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        try:
            stat = child.stat()
        except OSError:
            continue
        entries.append({
            "name": child.name,
            "path": _display_path(child),
            "is_dir": child.is_dir(),
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })
    return {
        "path": "" if target == workspace_path() else _display_path(target),
        "root": str(workspace_path()),
        "entries": entries,
    }


@app.get("/api/files/content")
def read_file(path: str = Query(...)) -> Dict[str, Any]:
    target = _resolve_workspace_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable.")
    size = target.stat().st_size
    raw = target.read_bytes()[:MAX_FILE_BYTES]
    try:
        content = raw.decode("utf-8")
        binary = False
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="replace")
        binary = True
    return {
        "path": _display_path(target),
        "content": content,
        "truncated": size > MAX_FILE_BYTES,
        "binary": binary,
        "size": size,
    }


# ----------------------------------------------------------------------
# sessions
# ----------------------------------------------------------------------
@app.get("/api/sessions")
def sessions_list() -> Dict[str, Any]:
    return {"sessions": session_store.list_sessions()}


@app.post("/api/sessions")
def sessions_create(payload: SessionCreate) -> Dict[str, Any]:
    config = load_config()
    model = payload.model or config.get("provider", {}).get("model", "")
    return session_store.create_session(title=payload.title, model=model)


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: str) -> Dict[str, Any]:
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return session


@app.patch("/api/sessions/{session_id}")
def sessions_patch(session_id: str, payload: SessionPatch) -> Dict[str, Any]:
    session = session_store.set_title(session_id, payload.title)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return session


@app.post("/api/sessions/{session_id}/messages")
def sessions_add_message(session_id: str, payload: MessageIn) -> Dict[str, Any]:
    session = session_store.append_message(session_id, payload.message)
    if session is None:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {"ok": True, "count": len(session.get("messages", []))}


@app.delete("/api/sessions/{session_id}")
def sessions_delete(session_id: str) -> Dict[str, Any]:
    ok = session_store.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Session introuvable.")
    return {"ok": True}


# ----------------------------------------------------------------------
# websocket chat
# ----------------------------------------------------------------------
@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    await websocket.accept()
    config = load_config()
    model = websocket.query_params.get("model") or None
    session_id = websocket.query_params.get("session") or None
    approval_mode = websocket.query_params.get("mode") or None
    loop = asyncio.get_running_loop()

    try:
        runner = AgentRunner(
            config, loop, session_id=session_id, model=model, approval_mode=approval_mode
        )
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": f"Initialisation impossible: {exc}"})
        await websocket.close()
        return

    await websocket.send_json({
        "type": "ready",
        "session_id": session_id,
        "provider": runner.provider_info(),
    })

    async def receiver() -> None:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            except Exception:
                return
            kind = data.get("type")
            if kind == "user_message":
                content = (data.get("content") or "").strip()
                raw_images = data.get("images") or []
                images = [img for img in raw_images if isinstance(img, str)] if isinstance(raw_images, list) else []
                images = images[:8]
                if content or images:
                    runner.start_turn(content or "Décris et analyse cette image.", images=images)
            elif kind == "authorize":
                runner.resolve_authorization(data.get("id", ""), data.get("choice", "n"))
            elif kind == "interrupt":
                runner.request_interrupt()
            elif kind == "set_model":
                runner.set_model(data.get("model", ""))
            elif kind == "set_mode":
                runner.set_approval_mode(data.get("mode", ""))
                try:
                    await websocket.send_json({
                        "type": "mode_changed",
                        "mode": runner.approval_mode,
                    })
                except Exception:
                    return
            elif kind == "ping":
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    return

    async def emitter() -> None:
        while True:
            event = await runner.events.get()
            try:
                await websocket.send_json(event)
            except Exception:
                return

    recv_task = asyncio.create_task(receiver())
    emit_task = asyncio.create_task(emitter())
    try:
        await asyncio.wait({recv_task, emit_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        runner.close()
        for task in (recv_task, emit_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(recv_task, emit_task, return_exceptions=True)


# ----------------------------------------------------------------------
# browser relay (NemApi distant / tunnel depuis un backend sans sortie réseau)
# ----------------------------------------------------------------------
@app.websocket("/ws/relay")
async def ws_relay(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue = RELAY_HUB.subscribe(loop)
    try:
        await websocket.send_json({"type": "relay_ready"})
    except Exception:
        RELAY_HUB.unsubscribe(queue)
        return

    async def receiver() -> None:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            except Exception:
                return
            kind = data.get("type")
            if kind == "relay_response":
                RELAY_HUB.deliver(
                    str(data.get("id") or ""),
                    status=data.get("status"),
                    body=str(data.get("body") or ""),
                    error=str(data.get("error") or ""),
                )
            elif kind == "ping":
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    return

    async def sender() -> None:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)

    recv_task = asyncio.create_task(receiver())
    send_task = asyncio.create_task(sender())
    try:
        await asyncio.wait({recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        RELAY_HUB.unsubscribe(queue)
        for task in (recv_task, send_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(recv_task, send_task, return_exceptions=True)


# ----------------------------------------------------------------------
# websocket terminal (real PTY shell on the backend host)
# ----------------------------------------------------------------------
@app.websocket("/ws/terminal")
async def ws_terminal(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_running_loop()

    shell = os.environ.get("SHELL") or "/bin/bash"
    if not Path(shell).exists():
        shell = "/bin/bash"
    try:
        cols = int(websocket.query_params.get("cols") or 120)
        rows = int(websocket.query_params.get("rows") or 30)
    except ValueError:
        cols, rows = 120, 30

    try:
        ws_dir = str(workspace_path())
    except Exception:
        ws_dir = os.path.expanduser("~")

    session = PtySession(shell, cwd=ws_dir, env=dict(os.environ), cols=cols, rows=rows)
    try:
        session.start()
    except Exception as exc:
        await websocket.send_json({"type": "error", "message": f"PTY indisponible: {exc}"})
        await websocket.close()
        return

    await websocket.send_json({"type": "ready", "shell": shell, "cwd": ws_dir})

    output_queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()

    def reader() -> None:
        while True:
            chunk = session.read_available()
            if chunk is None:
                loop.call_soon_threadsafe(output_queue.put_nowait, None)
                return
            if chunk:
                loop.call_soon_threadsafe(output_queue.put_nowait, chunk)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    async def receiver() -> None:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                return
            except Exception:
                return
            kind = data.get("type")
            if kind == "input":
                session.write(str(data.get("data", "")).encode("latin-1", errors="ignore"))
            elif kind == "resize":
                try:
                    session.resize(int(data.get("cols") or cols), int(data.get("rows") or rows))
                except (TypeError, ValueError):
                    pass
            elif kind == "ping":
                try:
                    await websocket.send_json({"type": "pong"})
                except Exception:
                    return

    async def sender() -> None:
        while True:
            chunk = await output_queue.get()
            if chunk is None:
                try:
                    await websocket.send_json({"type": "exit", "code": 0})
                except Exception:
                    pass
                return
            try:
                await websocket.send_json({
                    "type": "output",
                    "data": chunk.decode("latin-1", errors="replace"),
                })
            except Exception:
                return

    recv_task = asyncio.create_task(receiver())
    send_task = asyncio.create_task(sender())
    try:
        await asyncio.wait({recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        session.close()
        for task in (recv_task, send_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(recv_task, send_task, return_exceptions=True)


# ----------------------------------------------------------------------
# static frontend (production build), when present
# ----------------------------------------------------------------------
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        if full_path.startswith(("api", "ws")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            media, _ = mimetypes.guess_type(str(candidate))
            return FileResponse(candidate, media_type=media)
        return FileResponse(_DIST / "index.html")
