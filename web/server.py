"""Serveur Flask pour l'UI web NEMESIS (terminal dans le navigateur).

Orchestration identique au CLI terminal : NemApi (contexte cote serveur),
prompt systeme envoye une seule fois par nouvelle session, autorisation des
outils par outil+signature, sorties bash masquables. L'etat est pousse au
navigateur via Server-Sent Events (SSE).
"""

from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from flask import Flask, Response, jsonify, request, send_from_directory
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Flask requis pour l'UI web : pip install --break-system-packages flask") from exc

from web.session import WebSession, _auth_signature

# Ensure slash command handlers are registered against the global registry.
import src.core.default_commands  # noqa: F401


class CommandContext:
    """Bridge passed to /resume-like commands that need to switch or start turns."""

    def __init__(self, server: "WebServer", session: WebSession):
        self.server = server
        self.session = session
        self.pending_turn = False
        self._plan_mode = False

    @property
    def config_dir(self):
        from src.core.paths import USER_CONFIG_DIR

        return str(USER_CONFIG_DIR)

    def select_session(self, sid: str) -> str:
        self.broadcast_select(sid)
        return sid

    def broadcast_select(self, sid: str) -> None:
        self.server.broadcast({"session": self.session.id, "type": "select_session", "data": {"id": sid}})

    def run_turn(self, text: str) -> None:
        threading.Thread(target=self.server._run_cycle, args=(self.session, text), daemon=True).start()
        self.pending_turn = True

    def enter_plan_mode(self) -> None:
        self.session._plan_mode = True

    def leave_plan_mode(self) -> bool:
        val = getattr(self.session, "_plan_mode", False)
        self.session._plan_mode = False
        return val


HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

MAX_TOOL_ITERATIONS = 50


class WebServer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sessions: Dict[str, WebSession] = {}
        self._lock = threading.Lock()
        # SSE buffer (instance-level so multiple servers never share events).
        self._subscribers: List[Dict[str, Any]] = []
        self.app = Flask(__name__, static_folder=None)
        self._build_routes()

    # ------------------------------------------------------------- resources
    def make_provider(self):
        from providers import create_provider

        return create_provider(self.config)

    def make_executor(self, workspace: str):
        from src.core.tool_bridge import ToolBridge

        return ToolBridge(workspace=workspace)

    def make_parser(self, executor):
        from action_parser import ActionParser

        if hasattr(executor, "registry") and executor.registry:
            return ActionParser.from_registry(executor.registry)
        return ActionParser()

    # ---------------------------------------------------------------- events
    def broadcast(self, event: Dict[str, Any]) -> None:
        # Global event stream; the client filters by `session`.
        with self._lock:
            self._subscribers.append(dict(event))
            # keep a bounded history
            if len(self._subscribers) > 2000:
                del self._subscribers[: len(self._subscribers) - 2000]

    def _wait_for_events(self) -> List[Dict[str, Any]]:
        """Return currently buffered events without blocking (simple poll)."""
        with self._lock:
            if not self._subscribers:
                return []
            out = list(self._subscribers)
            self._subscribers.clear()
            return out

    # ------------------------------------------------------------ sessions
    def create_session(self, workspace: str, title: str = "") -> WebSession:
        sess = WebSession(self, workspace)
        try:
            sess.provider = self.make_provider()
        except Exception as e:  # provider unavailable at startup is non-fatal
            sess.error_startup = str(e)
        try:
            sess.executor = self.make_executor(workspace)
            sess.parser = self.make_parser(sess.executor)
        except Exception as e:
            sess.error_startup = f"{sess.error_startup}; {e}" if sess.error_startup else str(e)
        if title:
            sess.title = title
        with self._lock:
            self.sessions[sess.id] = sess
        return sess

    def get_session(self, sid: str) -> Optional[WebSession]:
        with self._lock:
            return self.sessions.get(sid)

    def list_sessions(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = [s.to_summary() for s in self.sessions.values()]
        return sorted(items, key=lambda x: x["updated_at"], reverse=True)

    def persist_sessions(self) -> None:  # no-op: aucune session conservee entre runs
        return

    def load_persisted_sessions(self) -> None:  # no-op: demarrage a zero session
        return

    def delete_session(self, sid: str) -> bool:
        with self._lock:
            return self.sessions.pop(sid, None) is not None

    # --------------------------------------------------------------- routes
    def _build_routes(self) -> None:
        app = self.app

        @app.route("/")
        def index():
            body = (STATIC_DIR / "index.html").read_bytes()
            resp = Response(body, mimetype="text/html")
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return resp

        @app.route("/static/<path:name>")
        @app.route("/assets/<path:name>")
        def static_files(name):
            directory = str(STATIC_DIR / Path(name).parent) if "/" in name else str(STATIC_DIR)
            resp = send_from_directory(directory, Path(name).name)
            # Le proxy d'aperçu peut casser le caching/conditional requests :
            # on sert les assets en no-store pour garantir un chargement frais.
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
            resp.headers.pop("ETag", None)
            resp.headers.pop("Last-Modified", None)
            return resp

        @app.route("/api/sessions", methods=["GET"])
        def api_sessions():
            return jsonify({"sessions": self.list_sessions()})

        @app.route("/api/sessions", methods=["POST"])
        def api_create_session():
            payload = request.get_json(silent=True) or {}
            workspace = payload.get("workspace") or self.config.get("security", {}).get("workspace", ".")
            title = payload.get("title") or ""
            sess = self.create_session(workspace, title=title)
            self.persist_sessions()
            return jsonify({"session": sess.to_summary()})

        @app.route("/api/sessions/<sid>", methods=["DELETE"])
        def api_delete_session(sid):
            with self._lock:
                existed = self.sessions.pop(sid, None) is not None
            if existed:
                self.persist_sessions()
            return jsonify({"ok": bool(existed)})

        @app.route("/api/sessions/<sid>", methods=["GET"])
        def api_session_detail(sid):
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            return jsonify({
                "session": sess.to_summary(),
                "messages": sess.messages,
                "hidden_outputs": {str(k): v for k, v in sess._hidden_outputs.items()},
            })

        @app.route("/api/sessions/<sid>/resume", methods=["POST"])
        def api_resume(sid):
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            # Resume keeps server-side context intact; we simply mark that the
            # session continues (no fresh_chat reset). Nothing else to do.
            sess.touch()
            return jsonify({"ok": True, "resumed": True})

        @app.route("/api/sessions/<sid>/interrupt", methods=["POST"])
        def api_interrupt(sid):
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            sess.interrupt()
            return jsonify({"ok": True})

        @app.route("/api/sessions/<sid>/message", methods=["POST"])
        def api_message(sid):
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            payload = request.get_json(silent=True) or {}
            text = (payload.get("text") or "").strip()
            if not text:
                return jsonify({"error": "empty message"}), 400
            if sess._busy:
                return jsonify({"error": "session busy"}), 409
            threading.Thread(target=self._run_cycle, args=(sess, text), daemon=True).start()
            return jsonify({"ok": True})

        @app.route("/api/sessions/<sid>/command", methods=["POST"])
        def api_command(sid):
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            payload = request.get_json(silent=True) or {}
            raw = (payload.get("command") or "").strip()
            result = self.run_command(sess, raw)
            return jsonify(result)

        @app.route("/api/commands", methods=["GET"])
        def api_commands():
            from src.core.commands import registry as term_registry
            from web.commands import registry as web_registry

            merged: Dict[str, Dict[str, Any]] = {}
            # Terminal commands that open their own interactive prompts are not
            # safe to auto-run from the palette; the UI will prefill instead.
            INTERACTIVE = {"config", "skills", "mcp", "agents", "a2a"}
            for c in term_registry.list_commands():
                merged[c.name] = {
                    "name": "/" + c.name,
                    "description": c.description,
                    "usage": c.usage or ("/" + c.name),
                    "takes_args": bool(c.usage and "<" in c.usage),
                    "interactive": c.name in INTERACTIVE,
                }
            for w in web_registry.list():
                merged[w.name] = {
                    "name": "/" + w.name,
                    "description": w.description,
                    "usage": w.usage or ("/" + w.name),
                    "takes_args": bool(w.usage and "<" in w.usage),
                    "interactive": False,
                }
            return jsonify({"commands": sorted(merged.values(), key=lambda x: x["name"])})

        @app.route("/api/models", methods=["GET"])
        def api_models():
            sess_id = request.args.get("session")
            provider = None
            if sess_id and (s := self.get_session(sess_id)):
                provider = s.provider
            if provider is None:
                try:
                    provider = self.make_provider()
                except Exception as e:
                    return jsonify({"error": str(e)}), 500
            try:
                models = provider.list_models()
            except Exception as e:
                models = []
            current = getattr(provider, "model", "")
            return jsonify({"models": models, "current": current})

        @app.route("/api/tools", methods=["GET"])
        def api_tools():
            sess_id = request.args.get("session")
            executor = None
            if sess_id and (s := self.get_session(sess_id)):
                executor = s.executor
            if executor is None:
                ws = self.config.get("security", {}).get("workspace", ".")
                try:
                    executor = self.make_executor(ws)
                except Exception as e:
                    return jsonify({"error": str(e)}), 500
            tools = []
            for name, reg in executor.registry:
                tools.append({
                    "name": name,
                    "risk": getattr(reg, "risk", "medium"),
                    "kind": getattr(getattr(reg, "definition", None), "kind", None).__str__() if getattr(reg, "definition", None) else "",
                    "description": (getattr(getattr(reg, "definition", None), "description", "") or "")[:300],
                })
            return jsonify({"tools": tools, "workspace": str(getattr(executor, "workspace_root", ""))})

        @app.route("/api/status", methods=["GET"])
        def api_status():
            sess_id = request.args.get("session")
            sess = self.get_session(sess_id) if sess_id else None
            connected = None
            model = ""
            host = ""
            if sess and sess.provider:
                model = getattr(sess.provider, "model", "")
                h = getattr(sess.provider, "host", None)
                p = getattr(sess.provider, "port", None)
                host = f"{h}:{p}" if h and p else getattr(sess.provider, "base_url", "")
                try:
                    connected = bool(sess.provider.test_connection())
                except Exception:
                    connected = False
            return jsonify({
                "provider": "nemapi",
                "model": model,
                "host": host,
                "connected": connected,
                "workspace": sess.workspace if sess else self.config.get("security", {}).get("workspace", ""),
                "busy": bool(sess._busy) if sess else False,
                "plan_mode": bool(getattr(sess, "_plan_mode", False)) if sess else False,
                "authorized_tools": sorted(sess._authorized_tools) if sess else [],
                "authorized_signatures": len(sess._authorized_signatures) if sess else 0,
            })

        @app.route("/api/sessions/<sid>/output/<int:output_id>", methods=["GET"])
        def api_output(sid, output_id):
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            rec = sess._hidden_outputs.get(output_id)
            if not rec:
                return jsonify({"error": "unknown output id"}), 404
            # Only expose the full bash output once the call was executed (i.e. authorized).
            return jsonify({
                "output_id": output_id,
                "command": rec.get("command", ""),
                "success": rec.get("success", False),
                "output": rec.get("output", ""),
            })

        @app.route("/api/sessions/<sid>/show", methods=["POST"])
        def api_show(sid):
            # /show <id|last|all>: returns the requested hidden outputs (terminal parity).
            sess = self.get_session(sid)
            if not sess:
                return jsonify({"error": "not found"}), 404
            payload = request.get_json(silent=True) or {}
            arg = str(payload.get("target", "last")).lower()
            ids = sorted(sess._hidden_outputs.keys())
            if arg == "all":
                picked = ids
            elif arg == "last":
                picked = ids[-1:]
            else:
                try:
                    picked = [int(arg)]
                except ValueError:
                    picked = []
            out = [{"output_id": i, **sess._hidden_outputs[i]} for i in picked if i in sess._hidden_outputs]
            return jsonify({"outputs": out})

        @app.route("/api/config", methods=["GET", "PUT"])
        def api_config():
            if request.method == "GET":
                sec = self.config.get("security", {}) or {}
                prov = self.config.get("provider", {}) or {}
                nem = self.config.get("nemapi", {}) or {}
                return jsonify({
                    "workspace": sec.get("workspace", ""),
                    "model": prov.get("model", ""),
                    "nemapi_host": nem.get("host", ""),
                    "nemapi_port": nem.get("port", 8090),
                })
            payload = request.get_json(silent=True) or {}
            changed = False
            if "workspace" in payload and payload["workspace"]:
                self.config.setdefault("security", {})["workspace"] = str(Path(str(payload["workspace"])).expanduser().resolve())
                changed = True
            if "model" in payload and payload["model"]:
                self.config.setdefault("provider", {})["model"] = str(payload["model"])
                for sid in list(self.sessions.keys()):
                    s = self.get_session(sid)
                    if s and s.provider:
                        s.provider.model = str(payload["model"])
                changed = True
            if "nemapi_host" in payload or "nemapi_port" in payload:
                nem = self.config.setdefault("nemapi", {})
                if "nemapi_host" in payload:
                    nem["host"] = str(payload["nemapi_host"]).strip()
                if "nemapi_port" in payload:
                    try:
                        nem["port"] = int(payload["nemapi_port"])
                    except (TypeError, ValueError):
                        pass
                new_prov = {"type": "nemapi", "model": self.config.get("provider", {}).get("model", "")}
                sess = None
                for sid in list(self.sessions.keys()):
                    s = self.get_session(sid)
                    if s and s.provider is not None:
                        sess = s; break
                if sess is not None:
                    try:
                        ok, msg = sess.shim.reconfigure_provider({**self.config, "provider": new_prov, "nemapi": dict(nem)})
                        if not ok:
                            return jsonify({"ok": False, "error": msg}), 502
                        self.config["provider"] = new_prov
                    except Exception as e:
                        return jsonify({"ok": False, "error": str(e)}), 500
                changed = True
            if changed:
                self.persist_sessions()
            return jsonify({"ok": True})

        @app.route("/api/auth/respond", methods=["POST"])
        def api_auth_respond():
            payload = request.get_json(silent=True) or {}
            sid = payload.get("session")
            rid = payload.get("request_id")
            choice = payload.get("choice")
            sess = self.get_session(sid) if sid else None
            if not sess or not rid:
                return jsonify({"error": "not found"}), 404
            q = sess._pending_auth.get(rid)
            if q is None:
                return jsonify({"error": "stale request"}), 409
            q.put(choice)
            return jsonify({"ok": True})

        @app.route("/api/events")
        def api_events():
            def gen():
                # Send an initial hello, then poll the shared queue every 0.15s.
                yield "event: hello\ndata: {}\n\n"
                while True:
                    events = self._wait_for_events()
                    if events:
                        for ev in events:
                            yield "data: %s\n\n" % json.dumps(ev)
                    else:
                        yield ": ping\n\n"
                    time.sleep(0.15)
            return Response(gen(), mimetype="text/event-stream", headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            })

    # ------------------------------------------------------------- commands
    def _capture_console(self, fn):
        """Run a slash-command handler capturing its rich output.

        Returns (result, captured_text). Interactive prompts inside the command
        are answered with a safe default ('q') so web calls never block, and any
        leftover EOF is reported instead of crashing.
        """
        import io
        from rich.console import Console
        import src.core.default_commands as dcmd
        import builtins

        buf = io.StringIO()
        rc = Console(file=buf, force_terminal=False, width=100)
        orig_console = dcmd.console
        orig_input = builtins.input

        def fake_input(prompt=""):
            buf.write(str(prompt) + " q\n")
            return "q"

        dcmd.console = rc
        builtins.input = fake_input
        try:
            result = fn()
        finally:
            dcmd.console = orig_console
            builtins.input = orig_input
        return result, buf.getvalue()

    def run_command(self, sess: WebSession, raw: str) -> Dict[str, Any]:
        import src.core.default_commands as dcmd
        from src.core.commands import registry as term_registry
        from web.commands import registry as web_registry, WebCommand

        cmd = raw.strip()
        if cmd.startswith("/"):
            cmd = cmd[1:]
        parts = cmd.split()
        if not parts:
            names = sorted({"/" + c.name for c in term_registry.list_commands()} | {"/" + w.name for w in web_registry.list()})
            return {"type": "commands", "commands": names}
        name = parts[0].lower()
        args = parts[1:]

        # 1) Dedicated web terminal commands take priority (Claude Code parity).
        wc: Optional[WebCommand] = web_registry.get(name)
        if wc is not None:
            ctx = CommandContext(self, sess)
            try:
                result = wc.handler(args, ctx) or ""
            except Exception as e:
                return {"type": "error", "message": f"Erreur commande : {e}"}
            # /plan starts an async agent turn; keep the UI responsive.
            self.persist_sessions()
            if isinstance(result, dict):
                # Structured results (choice menus, etc.) pass through verbatim.
                out = {"type": result.get("type", "ok"), **result, "command": "/" + name}
            else:
                out = {"type": "ok", "text": str(result), "command": "/" + name}
            if ctx.pending_turn:
                out["agent_started"] = True
            return out

        # 2) Terminal registered commands (help/status/tools/config/history/...).
        command = term_registry.get_command(name)
        if not command:
            return {"type": "error", "message": f"Commande inconnue : /{name}"}

        # Ensure slash-command side effects target this session's active app.
        dcmd.set_active_app(sess.shim)

        try:
            res, captured = self._capture_console(lambda: command.handler(args))
        except Exception as e:
            return {"type": "error", "message": f"Erreur commande : {e}"}

        payload: Dict[str, Any] = {}
        if isinstance(res, str) and res.startswith("PROMPT_INTERNAL:"):
            internal = res.replace("PROMPT_INTERNAL:", "").strip()
            threading.Thread(target=self._run_cycle, args=(sess, internal), daemon=True).start()
            payload = {"type": "internal_prompt", "ok": True}
        elif res is None:
            payload = {"type": "ok"}
        elif isinstance(res, dict):
            payload = res
        else:
            payload = {"type": "text", "text": str(res)}
        if captured:
            payload.setdefault("output_type", "rich")
            payload["output"] = captured
        return payload

    # ------------------------------------------------------------ agent loop
    def _capture_rich(self, fn) -> str:
        import io
        from rich.console import Console

        buf = io.StringIO()
        rc = Console(file=buf, force_terminal=False, width=100)
        import src.core.default_commands as dcmd

        original = dcmd.console
        dcmd.console = rc
        try:
            fn()
        finally:
            dcmd.console = original
        return buf.getvalue()

    def _ensure_provider(self, sess: WebSession) -> None:
        if sess.provider is not None:
            return
        try:
            sess.provider = self.make_provider()
            sess.error_startup = None
        except Exception as e:
            sess.error_startup = str(e)

    def _ensure_executor(self, sess: WebSession) -> None:
        if sess.executor is not None and sess.parser is not None:
            return
        try:
            sess.executor = self.make_executor(sess.workspace)
            sess.parser = self.make_parser(sess.executor)
            sess.error_startup = None
        except Exception as e:
            sess.error_startup = str(e)

    def _run_cycle(self, sess: WebSession, user_text: str) -> int:
        sess._busy = True
        sess._interrupted = False
        start_time = time.time()
        tool_count = 0
        try:
            self._ensure_provider(sess)
            self._ensure_executor(sess)
            if sess.provider is None or sess.executor is None:
                raise RuntimeError(sess.error_startup or "provider/executor indisponible")
            # connection test (mirrors CLI first-message probe)
            if sess._conn_ok is None:
                try:
                    sess._conn_ok = bool(sess.provider.test_connection())
                except Exception:
                    sess._conn_ok = False
            if sess._conn_ok is False:
                self.emit_error(sess, "NEMAPI n'est pas connecté. Utilisez /config pour le configurer.")
                return 0
        except Exception as e:
            self.emit_error(sess, f"Initialisation impossible : {e}")
            sess._busy = False
            return 0
        try:
            tool_count = self._process_cycle(sess, user_text) or tool_count
        finally:
            elapsed = time.time() - start_time
            self.emit_task_summary(sess, elapsed, max(tool_count - 1, 0))
            sess._busy = False
            self.persist_sessions()
        return tool_count

    def emit_user(self, sess: WebSession, text: str) -> None:
        msg = sess.add_message({"role": "user", "content": text})
        self.broadcast({"session": sess.id, "type": "user_message", "data": {"id": msg["id"], "text": text}})

    def emit_ai(self, sess: WebSession, text: str) -> None:
        msg = sess.add_message({"role": "assistant", "content": text})
        self.broadcast({"session": sess.id, "type": "ai_message", "data": {"id": msg["id"], "text": text}})

    def emit_system(self, sess: WebSession, text: str, level: str = "system") -> None:
        msg = sess.add_message({"role": "system", "content": text, "level": level})
        self.broadcast({"session": sess.id, "type": "system_message", "data": {"id": msg["id"], "text": text, "level": level}})

    def emit_error(self, sess: WebSession, text: str) -> None:
        self.emit_system(sess, text, level="error")

    def emit_thinking(self, sess: WebSession, on: bool, label: str = "NEMESIS réfléchit...") -> None:
        self.broadcast({"session": sess.id, "type": "thinking", "data": {"on": on, "label": label}})

    def emit_tool_start(self, sess: WebSession, tool: str, params: Dict[str, Any]) -> Dict[str, Any]:
        call_id = uuid.uuid4().hex[:8]
        item = sess.add_message({
            "role": "tool_call",
            "call_id": call_id,
            "tool": tool,
            "params": params,
        })
        self.broadcast({"session": sess.id, "type": "tool_start", "data": {
            "id": item["id"], "call_id": call_id, "tool": tool, "params": params,
        }})
        return item

    def emit_tool_result(self, sess: WebSession, item: Dict[str, Any], res: Dict[str, Any], output_id: Optional[int]) -> None:
        success = bool(res.get("success", False))
        stdout = res.get("stdout", res.get("content", "")) or ""
        error = res.get("error", "")
        items = res.get("items")
        summary = sess.add_message({
            "role": "tool_result",
            "call_id": item["call_id"],
            "tool": item["tool"],
            "success": success,
            "output_id": output_id,
            "truncated_preview": stdout[:4000],
            "error": error,
            "items": items,
        })
        self.broadcast({"session": sess.id, "type": "tool_result", "data": {
            "id": summary["id"], "call_id": item["call_id"], "tool": item["tool"],
            "success": success, "output_id": output_id, "preview": stdout[:4000],
            "error": error, "items": items,
        }})

    def emit_task_summary(self, sess: WebSession, elapsed: float, tool_count: int) -> None:
        mins, secs = divmod(int(elapsed), 60)
        self.broadcast({"session": sess.id, "type": "task_summary", "data": {
            "elapsed": round(elapsed, 2), "mmss": f"{mins:02d}:{secs:02d}", "tools": tool_count,
        }})

    def request_authorization(self, sess: WebSession, tool: str, params: Dict[str, Any], risk: str) -> bool:
        if sess.has_authorization(tool, params, risk):
            return True
        rid = uuid.uuid4().hex
        q: "queue.Queue" = queue.Queue()
        sess._pending_auth[rid] = q
        self.broadcast({"session": sess.id, "type": "auth_request", "data": {
            "request_id": rid, "tool": tool, "params": params, "risk": risk,
            "signature": _auth_signature(tool, params),
        }})
        try:
            choice = q.get(timeout=300)  # 5 min max wait
        except Exception:
            choice = "n"
        finally:
            sess._pending_auth.pop(rid, None)
        allowed = sess.grant(tool, params, risk, choice)
        self.broadcast({"session": sess.id, "type": "auth_resolved", "data": {
            "request_id": rid, "choice": choice, "allowed": allowed,
        }})
        return allowed

    def _process_cycle(self, sess: WebSession, message: str) -> int:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import hashlib
        import src.core.default_commands as dcmd

        dcmd.increment_message_count()
        if not sess.title or sess.title == "Nouvelle session":
            trimmed = (message or "").strip().splitlines()[0][:60] if message else ""
            if trimmed:
                sess.title = trimmed

        # A new session starts by sending the system prompt once; NemApi keeps
        # the context server-side (fresh_chat reset happens in the provider).
        if sess._send_system_prompt and not sess._prompt_sent:
            prompt = sess.executor.get_system_prompt()
            if prompt:
                try:
                    init = sess.provider.send_message(prompt, role="system")
                except TypeError:
                    init = sess.provider.send_message(prompt)
                sess._prompt_sent = bool(init.get("success", True)) if isinstance(init, dict) else True

        self.emit_user(sess, message)

        current_input = message
        current_role = "user"
        iteration = 0
        last_response_hash = None
        last_feedback_signature = None
        consecutive_failures = 0

        while iteration < MAX_TOOL_ITERATIONS:
            if sess._interrupted:
                sess._interrupted = False
                self.emit_system(sess, "Tâche interrompue par l'utilisateur.", level="warning")
                break
            iteration += 1
            self.emit_thinking(sess, True, "NEMESIS réfléchit..." if iteration == 1 else "analyse du résultat...")
            try:
                try:
                    result = sess.provider.send_message(current_input, role=current_role)
                except TypeError:
                    result = sess.provider.send_message(current_input)
            finally:
                self.emit_thinking(sess, False)

            if not result.get("success"):
                self.emit_error(sess, f"Erreur : {result.get('error', 'inconnu')}")
                break

            raw_response = result.get("response", "")
            parsed = sess.parser.parse(raw_response)
            native_calls = result.get("tool_calls") or []
            for nc in native_calls:
                if isinstance(nc, dict):
                    parsed.setdefault("actions", []).append({"type": nc.get("type", ""), "content": nc.get("content", {})})

            display_text = parsed.get("text") or ""
            if display_text and not display_text.startswith("FEEDBACK:"):
                self.emit_ai(sess, display_text)

            actions = parsed.get("actions") or ([parsed["action"]] if parsed.get("action") else [])
            if not actions:
                break

            response_hash = hashlib.sha256((raw_response or "").strip().encode("utf-8", "replace")).hexdigest()
            if response_hash == last_response_hash and iteration > 1:
                self.emit_system(sess, "Réponse identique détectée : boucle interrompue.", level="warning")
                break
            last_response_hash = response_hash

            def is_read_only(a):
                try:
                    return sess.executor.risk_for(a["type"], a.get("content", {}) or {}) == "read"
                except Exception:
                    return False

            read_only = [a for a in actions if is_read_only(a)]
            other = [a for a in actions if not is_read_only(a)]

            batch_feedback = []
            if len(read_only) > 1:
                results_by_idx = {}
                started = []
                for idx, action in enumerate(read_only):
                    started.append(self.emit_tool_start(sess, action["type"], action.get("content", {}) or {}))
                with ThreadPoolExecutor(max_workers=min(4, len(read_only))) as pool:
                    fut_to_idx = {}
                    for idx, action in enumerate(read_only):
                        fut_to_idx[pool.submit(self._execute_single_tool, sess, action)] = idx
                    for fut in as_completed(fut_to_idx):
                        idx = fut_to_idx[fut]
                        try:
                            res = fut.result()
                        except Exception as e:
                            res = {"success": False, "stdout": f"Erreur: {e}"}
                        results_by_idx[idx] = res
                for idx, action in enumerate(read_only):
                    res = results_by_idx[idx]
                    self.emit_tool_result(sess, started[idx], res, None)
                    batch_feedback.append(sess.format_feedback(action, res))
                if not other:
                    current_input = "FEEDBACK:\n" + "\n\n".join(batch_feedback)
                    current_role = "tool_result"
                    continue
                actions_iter = other
            else:
                actions_iter = actions

            for action in actions_iter:
                act_type = action["type"]
                act_content = action.get("content", {}) or {}
                try:
                    risk = sess.executor.risk_for(act_type, act_content)
                except Exception:
                    risk = "medium"
                if not self.request_authorization(sess, act_type, act_content, risk):
                    self.emit_system(sess, f"Exécution de l'outil '{act_type}' refusée.", level="warning")
                    batch_feedback.append(f"Tool: {act_type}\nSuccess: false\nOutput:\n[EXECUTION REFUSEE PAR L'UTILISATEUR]")
                    continue

                item = self.emit_tool_start(sess, act_type, act_content)
                final_res: Dict[str, Any] = {}
                output_id = None
                for update in sess.stream_tool(act_type, act_content):
                    if sess._interrupted:
                        sess._interrupted = False
                        final_res = {"success": False, "stdout": "", "error": "Interrompu par l'utilisateur"}
                        break
                    if update.get("needs_input"):
                        self.emit_system(sess, "La commande attend une entrée utilisateur (non supporté en web).", level="warning")
                        continue
                    if "partial" in update and act_type == "bash":
                        self.broadcast({"session": sess.id, "type": "tool_output", "data": {
                            "call_id": item["call_id"], "chunk": update["partial"],
                        }})
                        continue
                    if "success" in update or (act_type != "bash" and update):
                        final_res = update
                    if act_type != "bash" and final_res:
                        break

                if final_res:
                    if act_type == "bash":
                        output = (final_res.get("output") or final_res.get("stdout") or final_res.get("error") or "")
                        sess._output_counter += 1
                        output_id = sess._output_counter
                        sess._hidden_outputs[output_id] = {
                            "command": str(act_content.get("command", "")),
                            "output": str(output),
                            "success": bool(final_res.get("success", False)),
                        }
                        final_res = dict(final_res)
                        final_res["output_id"] = output_id
                    self.emit_tool_result(sess, item, final_res, output_id)
                batch_feedback.append(sess.format_feedback(action, final_res))

            failed = any("[EXECUTION REFUSEE" in fb or "Succes: False" in fb for fb in batch_feedback)
            consecutive_failures = consecutive_failures + 1 if failed else 0
            if consecutive_failures >= 3:
                self.emit_system(sess, "3 échecs consécutifs d'outils : arrêt du cycle.", level="error")
                break

            feedback_input = "FEEDBACK:\n" + "\n\n".join(batch_feedback)
            sig = hashlib.sha256(feedback_input.encode("utf-8", "replace")).hexdigest()
            if sig == last_feedback_signature:
                self.emit_system(sess, "Aucun progrès détecté : arrêt du cycle.", level="warning")
                break
            last_feedback_signature = sig
            current_input = feedback_input
            current_role = "tool_result"

        return iteration

    def _execute_single_tool(self, sess: WebSession, action: Dict[str, Any]) -> Dict[str, Any]:
        act_type = action["type"]
        act_content = action.get("content", {}) or {}
        final_res: Dict[str, Any] = {}
        for update in sess.stream_tool(act_type, act_content):
            if "success" in update or (act_type != "bash" and update):
                final_res = update
            if act_type != "bash" and final_res:
                break
        return final_res


def create_app(config: Optional[Dict[str, Any]] = None) -> Flask:
    if config is None:
        cfg_mod = _load_config()
        config = cfg_mod
    server = WebServer(config)
    return server.app


def _load_config() -> Dict[str, Any]:
    from src.core.paths import config_path, resolve_workspace, DEFAULT_WORKSPACE
    import yaml

    path = config_path()
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        config = loaded if isinstance(loaded, dict) else {}
    else:
        config = {}
    config.setdefault("provider", {"type": "nemapi", "model": "qwen-chat"})
    sec = config.setdefault("security", {})
    ws = resolve_workspace(config) if callable(resolve_workspace) else DEFAULT_WORKSPACE
    sec.setdefault("workspace", str(ws))
    return config


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="UI web NEMESIS (terminal dans le navigateur)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--mock", action="store_true", help="Utiliser le provider mock hors-ligne")
    parser.add_argument("--workspace", default=None, help="Chemin du workspace des outils")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    config = _load_config()
    if args.workspace:
        config.setdefault("security", {})["workspace"] = str(Path(args.workspace).expanduser().resolve())
    if args.mock:
        config.setdefault("provider", {})["type"] = "mock"

    server = WebServer(config)
    server.load_persisted_sessions()
    print(f"NEMESIS UI web — http://{args.host}:{args.port}")
    server.app.run(host=args.host, port=args.port, threaded=True, debug=args.debug, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
