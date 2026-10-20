import os
import sys
import time
import tempfile
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich import box

from src.ui.theme import NEMESIS_THEME
from src.ui.header import get_header
from src.ui.composer import Composer
from src.core.commands import registry
import src.core.default_commands  # noqa: F401 — enregistre les commandes /
from providers import create_provider
from tools import create_executor_from_config
from action_parser import ActionParser


class NemesisApp:
    def __init__(self, debug: bool = False):
        self.console = Console(theme=NEMESIS_THEME)
        self.composer = Composer()
        self._console = self.console  # alias for direct access
        # User config lives in ~/.config/nemesis-cli (not install dir)
        from src.core.paths import (
            config_path,
            mcp_config_path,
            ensure_user_dirs,
            resolve_workspace,
            INSTALL_DIR,
            USER_CONFIG_DIR,
        )
        ensure_user_dirs()
        self.install_dir = INSTALL_DIR
        self.user_config_dir = USER_CONFIG_DIR
        self.config_path = config_path()
        self.mcp_config_path = mcp_config_path()
        self.config = None
        self.client = None
        self.executor = None
        self.parser = ActionParser()
        self.debug = debug
        self.auto_allow = False
        self.last_interrupt = 0
        self.session_start = 0.0
        self._conn_ok = None
        self._prompt_sent = False
        # Par defaut : nouvelle discussion, le prompt systeme part au premier message.
        # /continue bascule ce flag a False pour reprendre une session existante.
        self._send_system_prompt = True
        # Système d'autorisation pour les outils
        self._authorized_tools = set()  # Outils autorisés pour cette session (mode 'a')
        self._last_auth_choice = None  # Dernier choix d'autorisation
        # Stockage des sorties de commandes pour affichage différé
        self._hidden_outputs = {}  # Dict: {output_id: {"command": str, "output": str, "success": bool}}
        self._output_counter = 0  # Compteur pour générer les IDs de sortie
        self.session_id = None
        self._session_initialized = False
        # Flag pour gérer l'interruption propre
        self._interrupted = False  # True si l'utilisateur a interrompu la tâche en cours

    def _record_session_message(self, message: Dict[str, Any]):
        if not self.session_id:
            return
        from web.backend import sessions as session_store

        session_store.append_message(self.session_id, message)

    def _start_session(self, session_id: Optional[str] = None):
        from web.backend import sessions as session_store

        if session_id:
            session = session_store.get_session(session_id)
            if session is None:
                self.console.print(f"[error]Session introuvable : {session_id}[/error]")
                return False
            self.session_id = session_id
            self._session_initialized = bool(session.get("nemapi_initialized"))
            if hasattr(self.client, "configure_session"):
                self.client.configure_session(session_id, initialized=self._session_initialized)
            if hasattr(self.client, "reset_conversation"):
                self.client.reset_conversation()
            if hasattr(self.client, "set_conversation"):
                provider_history = []
                for message in session.get("messages", []):
                    kind = message.get("kind")
                    if kind in {"user", "assistant"}:
                        provider_history.append({
                            "role": kind,
                            "content": message.get("text", ""),
                        })
                    elif kind == "tool":
                        provider_history.append({
                            "role": "tool_result",
                            "content": (
                                f"Tool: {message.get('name', 'outil')}\n"
                                f"Success: {message.get('success', False)}\n"
                                f"Output:\n{message.get('output', '')}"
                            ),
                        })
                self.client.set_conversation(provider_history)
            for message in session.get("messages", []):
                kind = message.get("kind")
                if kind == "user":
                    self.composer.display_user_message(message.get("text", ""))
                elif kind == "assistant":
                    self.composer.display_ai_message(message.get("text", ""))
                elif kind == "tool":
                    self.composer.display_tool_start(
                        message.get("name", "outil"),
                        message.get("params", {}),
                    )
                    self.composer.display_tool_result(
                        {"success": message.get("success", False), "output": message.get("output", "")},
                        tool_name=message.get("name", "outil"),
                        duration=message.get("duration"),
                    )
            self._prompt_sent = self._session_initialized
            self._send_system_prompt = not self._session_initialized
            self.console.print(f"[success]Session reprise : {session.get('title', session_id)}[/success]")
            return True

        session = session_store.create_session(model=getattr(self.client, "model", ""))
        self.session_id = session["id"]
        self._session_initialized = False
        self._prompt_sent = False
        if hasattr(self.client, "reset_conversation"):
            self.client.reset_conversation()
        if hasattr(self.client, "configure_session"):
            self.client.configure_session(self.session_id, initialized=False)
        return True

    def _manage_sessions(self, args: List[str]):
        from rich.table import Table
        from web.backend import sessions as session_store

        operation = args[0].lower() if args else "list"
        if operation == "new":
            if self._start_session():
                self.console.print(f"[success]Nouvelle session créée : {self.session_id}[/success]")
            return
        if operation == "open":
            if len(args) < 2:
                self.console.print("[error]Usage : /sessions open <id>[/error]")
                return
            self._start_session(args[1])
            return
        if operation == "delete":
            if len(args) < 2:
                self.console.print("[error]Usage : /sessions delete <id>[/error]")
                return
            if not session_store.delete_session(args[1]):
                self.console.print("[error]Session introuvable.[/error]")
                return
            self.console.print("[success]Session supprimée de la liste.[/success]")
            if args[1] == self.session_id:
                self._start_session()
            return
        if operation != "list":
            self.console.print("[dim]Usage : /sessions [list|new|open <id>|delete <id>][/dim]")
            return

        table = Table(title="Conversations", box=box.ROUNDED, expand=True)
        table.add_column("Active", width=7)
        table.add_column("ID", style="dim", width=14)
        table.add_column("Titre")
        table.add_column("Messages", justify="right")
        for session in session_store.list_sessions():
            table.add_row(
                "●" if session["id"] == self.session_id else "",
                session["id"],
                session["title"],
                str(session["message_count"]),
            )
        self.console.print(table)
        self.console.print("[dim]/sessions new · /sessions open <id> · /sessions delete <id>[/dim]")

    def _load_config(self):
        from src.core.paths import resolve_workspace, DEFAULT_WORKSPACE

        config_exists = self.config_path.exists()
        needs_save = False

        if config_exists:
            with open(self.config_path, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
                self.config = loaded if isinstance(loaded, dict) else {}
        else:
            self.config = {}
            needs_save = True

        # MCP stays in its own file — never merge into provider config
        # (tools load mcp_config.yaml via MCPManager)

        # Valeurs par defaut pour les sections manquantes ou invalides
        if not isinstance(self.config.get("provider"), dict) or not self.config["provider"]:
            self.config["provider"] = {
                "type": "nemapi",
                "model": "qwen-chat",
            }
            needs_save = True
        if self.config["provider"].get("type") in {"bridge", "nemapi_bridge", "nemapi-v3"}:
            self.config["provider"]["type"] = "nemapi"
            needs_save = True
        if not isinstance(self.config.get("nemapi"), dict):
            legacy = self.config.get("nemapi_v3", {})
            self.config["nemapi"] = legacy if isinstance(legacy, dict) else {"host": "127.0.0.1", "port": 8090}
            needs_save = True
        if not isinstance(self.config.get("security"), dict):
            self.config["security"] = {"workspace": str(DEFAULT_WORKSPACE)}
            needs_save = True
        elif not self.config["security"].get("workspace"):
            self.config["security"]["workspace"] = str(DEFAULT_WORKSPACE)
            needs_save = True

        # S'assurer que provider.type existe
        if "type" not in self.config["provider"]:
            self.config["provider"]["type"] = "nemapi"
            self.config["provider"].setdefault("model", "qwen-chat")
            needs_save = True

        # Normalize workspace path and ensure directory exists
        ws = resolve_workspace(self.config)
        self.config["security"]["workspace"] = str(ws)

        # Sauvegarder si des valeurs par defaut ont ete ajoutees
        if needs_save:
            self._save_config()

    def _save_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        # Never persist MCP server blocks inside main config
        clean = {
            k: v
            for k, v in (self.config or {}).items()
            if k in ("provider", "bridge", "security", "nemapi_v3", "nemapi")
            or (isinstance(v, dict) and "type" in (self.config.get("provider") or {}) and k == "provider")
        }
        # Keep full config but strip known MCP-only keys that may have been merged historically
        to_save = dict(self.config or {})
        for mcp_key in list(to_save.keys()):
            if mcp_key in ("calculator", "github") and isinstance(to_save.get(mcp_key), dict):
                if "command" in to_save[mcp_key] and "type" not in to_save[mcp_key]:
                    to_save.pop(mcp_key, None)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(to_save, f, default_flow_style=False, allow_unicode=True)
        self.config_path.chmod(0o600)

    def reconfigure_provider(self, new_config: Dict[str, Any]):
        """Reconfigure NEMAPI a chaud (appelee par /config)."""
        old_conversation = self.client.get_conversation() if self.client else []
        try:
            new_client = create_provider(new_config)
            conn_ok = new_client.test_connection()
            if not conn_ok:
                return False, "Connexion impossible avec la nouvelle config"

            self.client = new_client
            self.client.set_conversation(old_conversation)
            self.config = new_config
            self._conn_ok = True
            self._prompt_sent = False
            self._save_config()
            return True, "Provider reconfigure"
        except Exception as e:
            return False, str(e)

    def _show_commands(self):
        """Affiche la liste des commandes disponibles."""
        from rich.table import Table
        from rich.panel import Panel
        from rich.text import Text
        
        # Créer un tableau plus élégant
        table = Table(
            title="[bold #cba6f7]Commandes disponibles[/bold #cba6f7]",
            border_style="#45475a",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold #89b4fa",
            expand=True,
        )
        table.add_column("Commande", style="bold #fab387", no_wrap=True, width=22)
        table.add_column("Usage", style="#a6adc8", no_wrap=True, width=32)
        table.add_column("Description", style="#cdd6f4")
        
        # Trier les commandes par nom
        sorted_commands = sorted(registry.list_commands(), key=lambda c: c.name)
        
        for cmd in sorted_commands:
            table.add_row(
                f"/{cmd.name}",
                cmd.usage or "—",
                cmd.description or "Aucune description",
            )
        
        # Ajouter un pied de tableau
        self.console.print(Panel(table, border_style="#45475a", padding=(1, 2)))
        self.console.print("[dim]Astuce : Tab complète une commande · /help affiche cette liste · /exit quitte[/dim]")

    def _handle_interrupt(self):
        now = time.time()
        if now - self.last_interrupt < 2:
            # Double Ctrl+C : fermeture forcée
            self.console.print("\n[error]Fermeture forcee...[/error]")
            sys.exit(0)
        else:
            # Premier Ctrl+C : interrompre la tâche en cours
            self._interrupted = True
            self.last_interrupt = now
            self.console.print("\n[yellow] Tâche interrompue. Tapez Ctrl+C encore pour forcer la fermeture.[/yellow]")

    def _consume_input_interrupt(self) -> bool:
        """Consume an interrupt captured by a custom composer key binding."""
        if not getattr(self.composer, "interrupt_requested", False):
            return False
        self.composer.interrupt_requested = False
        self._handle_interrupt()
        return True

    def _parse_response(self, resp: str) -> Dict:
        """Parse une réponse LLM via le parseur multi-niveaux (ActionParser).

        Supporte 6 niveaux : YAML, JSON strict, JSON relaxé,
        blocs nus, XML, regex fallback.
        """
        return self.parser.parse(resp)

    def _ask_for_authorization(self, tool_name: str, action_content: Dict, risk: str = "medium") -> bool:
        """Ask the user to authorize a tool call. Returns True if allowed."""
        if risk == "read":
            return True
        if self.config.get("security", {}).get("approval_mode", "confirm") == "all":
            return True
        if tool_name in self._authorized_tools:
            return True

        params = action_content if isinstance(action_content, dict) else {"value": action_content}
        self.console.print()
        if hasattr(self.composer, "display_auth"):
            self.composer.display_auth(tool_name, params)
        else:
            self.console.print(f"[yellow]Authorize tool:[/yellow] {tool_name}")

        while True:
            try:
                choice = self.composer.prompt_input(title=self.composer.AUTH_TITLE if hasattr(self.composer, "AUTH_TITLE") else "authorization [y/n/a]")
                if self._consume_input_interrupt():
                    return False
                if choice:
                    choice = choice.strip().lower()
                    if choice in ("y", "yes"):
                        self._last_auth_choice = "y"
                        return True
                    if choice in ("n", "no"):
                        self._last_auth_choice = "n"
                        return False
                    if choice in ("a", "always"):
                        self._authorized_tools.add(tool_name)
                        self._last_auth_choice = "a"
                        self.console.print(f"[green]  '{tool_name}' authorized for this session[/green]")
                        return True
                self.console.print("[dim]  reply y, n, or a[/dim]")
            except (EOFError, KeyboardInterrupt):
                self.console.print("[yellow]  cancelled[/yellow]")
                return False

    def _format_feedback(self, action: Dict, res: Dict) -> str:
        output = res.get('stdout', res.get('content', ''))
        MAX_CHARS = 10_000_000  # 10 Mo - augmente de 500 Ko
        if len(output) > MAX_CHARS:
            output = output[:MAX_CHARS] + "\n\n[AVERTISSEMENT SYSTEME : Logs tronques.]"
        tool_name = action['type']
        success = res.get('success', False)
        
        # S'assurer que output n'est jamais vide - au minimum un message par défaut
        if not output:
            output = "[Aucune sortie capturee - la commande a peut-etre echoue ou n'a produit aucune sortie]"
        
        return f"FEEDBACK:\nTool: {tool_name}\nSucces: {success}\nOutput:\n{output}"

    def _ask_system_prompt(self) -> None:
        """Ask whether the system prompt should initialize the NEMAPI session."""
        self.console.print(
            "[yellow]Envoyer le prompt système au prochain message ? "
            "[o/N] (N conserve la session NEMAPI existante)[/yellow]"
        )
        while True:
            try:
                choice = self.composer.prompt_input(
                    title="prompt système [o / n]"
                )
                if self._consume_input_interrupt():
                    return
            except (EOFError, KeyboardInterrupt):
                choice = None

            normalized = (choice or "").strip().lower()
            if not normalized or normalized in {"n", "non", "no"}:
                self._send_system_prompt = False
                self._prompt_sent = True
                client = getattr(self, "client", None)
                if getattr(self, "session_id", None) and hasattr(client, "configure_session"):
                    self.client.configure_session(self.session_id, initialized=True)
                return
            if normalized in {"o", "oui", "y", "yes"}:
                self._send_system_prompt = True
                client = getattr(self, "client", None)
                if getattr(self, "session_id", None) and hasattr(client, "configure_session"):
                    self.client.configure_session(self.session_id, initialized=False)
                return
            self.console.print("[dim]Répondez par o (oui) ou n (non).[/dim]")

    def _process_cycle(self, message: str):
        from src.core.default_commands import increment_message_count
        
        # Réinitialiser le flag d'interruption au début d'un nouveau cycle
        self._interrupted = False
        cycle_started = time.time()
        cycle_tools = 0
        cycle_files: set[str] = set()

        # Tester la connexion au premier message si pas encore fait
        if self._conn_ok is None:
            try:
                self._conn_ok = self.client.test_connection(max_age=20.0)
            except TypeError:
                self._conn_ok = self.client.test_connection()

        if self._conn_ok is False:
            self.console.print("[error]NEMAPI n'est pas connecte. Utilisez /config pour le configurer.[/error]")
            return

        # Le prompt systeme (outils + memoire) est fusionne avec le premier
        # message utilisateur pour ne faire qu'un seul aller-retour provider.
        system_prefix = ""
        if self._send_system_prompt and not self._prompt_sent:
            system_prefix = self.executor.get_system_prompt() if hasattr(self.executor, "get_system_prompt") else ""
            self._prompt_sent = True

        increment_message_count()

        # Afficher le message de l'utilisateur en bulle
        self.composer.display_user_message(message)
        message_at = time.time()
        self._record_session_message({"kind": "user", "text": message, "at": message_at})
        if self.session_id:
            from web.backend import sessions as session_store

            session = session_store.get_session(self.session_id)
            if session and session.get("title") == "Nouvelle conversation":
                session_store.set_title(self.session_id, message.strip()[:64] or "Nouvelle conversation")

        current_input = message
        current_role = "user"
        iteration = 0
        max_tool_iterations = 50
        last_response_hash = None

        while iteration < max_tool_iterations:
            # Vérifier si interruption demandée
            if self._interrupted:
                self._interrupted = False
                self.console.print("[yellow] Tâche interrompue par l'utilisateur.[/yellow]")
                break
                
            iteration += 1
            if self.debug:
                self.console.print(f"[debug]Iteration {iteration}/{max_tool_iterations}: Envoi (role={current_role})...[/debug]")

            # Thinking animation during LLM call
            anim = self.composer.display_thinking(
                "NEMESIS is thinking..." if iteration == 1 else "analyzing result..."
            )
            next(anim)  # start spinner
            prompt_sent_this_turn = bool(system_prefix)
            try:
                try:
                    result = self.client.send_message(
                        current_input, role=current_role, system_prefix=system_prefix
                    )
                except TypeError:
                    if system_prefix and current_role == "user":
                        result = self.client.send_message(
                            f"[SYSTEM INSTRUCTIONS]\n{system_prefix}\n\n[USER MESSAGE]\n{current_input}",
                            role=current_role,
                        )
                    else:
                        result = self.client.send_message(current_input)
                system_prefix = ""
            finally:
                try:
                    anim.close()
                except Exception:
                    pass

            # Le spinner s'arrête automatiquement ici
            if not result['success']:
                self.console.print(f"[error]Erreur: {result.get('error', 'inconnu')}[/error]")
                break
            if prompt_sent_this_turn and self.session_id:
                from web.backend import sessions as session_store

                session_store.mark_initialized(self.session_id)
                self._session_initialized = True

            raw_response = result.get('response', '')
            parsed = self._parse_response(raw_response)
            native_calls = result.get("tool_calls") or []
            for native_call in native_calls:
                if isinstance(native_call, dict):
                    parsed.setdefault("actions", []).append({
                        "type": native_call.get("type", ""),
                        "content": native_call.get("content", {}),
                    })
            native_call = result.get("tool_call")
            if native_call and isinstance(native_call, dict) and not native_calls:
                parsed.setdefault("actions", []).append({
                    "type": native_call.get("type", ""),
                    "content": native_call.get("content", {}),
                })
                parsed["action"] = parsed["actions"][0]
            if self.debug:
                self.console.print(f"[debug]Reponse recue ({len(raw_response)} chars), action={parsed['action'] is not None}[/debug]")

            # --- Duplicate response detection ---
            response_hash = hash(raw_response.strip())
            if response_hash == last_response_hash and iteration > 1:
                if parsed['text'] and not parsed['text'].startswith("FEEDBACK:"):
                    self.composer.display_ai_message(parsed['text'])
                    self._record_session_message({
                        "kind": "assistant",
                        "text": parsed["text"],
                        "at": time.time(),
                    })
                break
            last_response_hash = response_hash

            # --- Display AI text ---
            display_text = parsed['text']
            if display_text and not display_text.startswith("FEEDBACK:"):
                self.composer.display_ai_message(display_text)
                self._record_session_message({
                    "kind": "assistant",
                    "text": display_text,
                    "at": time.time(),
                })

            # --- No action = end of cycle ---
            actions = parsed.get("actions") or ([parsed["action"]] if parsed.get("action") else [])
            if not actions:
                break
            batch_feedback = []
            for action in actions:
                act_type = action['type']
                act_content = action.get('content', {})

                risk = self.executor.risk_for(act_type, act_content) if hasattr(self.executor, "risk_for") else "medium"
                if not self._ask_for_authorization(act_type, act_content, risk):
                    self.console.print(f"[red] Exécution de l'outil '{act_type}' refusée.[/red]")
                    batch_feedback.append(f"Tool: {act_type}\nSuccess: false\nOutput:\n[EXECUTION REFUSEE PAR L'UTILISATEUR]")
                    continue

                self.composer.display_tool_start(act_type, act_content if isinstance(act_content, dict) else {})
                cycle_tools += 1
                if act_type in ("edit", "write_file", "write", "apply_patch", "search_replace"):
                    target = (act_content or {}).get("path") or (act_content or {}).get("file_path")
                    if target:
                        cycle_files.add(str(target))
                final_res = {}
                tool_started = time.time()
                live = None
                if act_type == "bash":
                    try:
                        live = self.composer.live_terminal("bash")
                        live.__enter__()
                    except Exception:
                        live = None
                for update in self.executor.execute_tool(act_type, act_content):
                    if self._interrupted:
                        self._interrupted = False
                        final_res = {"success": False, "stdout": "", "error": "Interrompu par l'utilisateur"}
                        break
                    if update.get("needs_input"):
                        self.console.print("[yellow]La commande attend une entrée utilisateur.[/yellow]")
                        try:
                            user_input = self.composer.prompt_input("[yellow]>>>[/yellow] ")
                            if self._consume_input_interrupt():
                                break
                            waiting = getattr(self.executor, "_waiting_for_input", None)
                            if user_input and waiting and getattr(waiting, "stdin", None):
                                waiting.stdin.write(user_input + "\n")
                                waiting.stdin.flush()
                        except (EOFError, KeyboardInterrupt):
                            pass
                        continue
                    if live is not None and act_type == "bash":
                        # Le tool bash streame des lignes sous la cle 'partial'.
                        partial = update.get("partial")
                        if partial:
                            live.add_line(str(partial))
                    if "success" in update or (act_type != "bash" and update):
                        final_res = update
                    if act_type != "bash" and final_res:
                        break

                if live is not None:
                    try:
                        live.__exit__(None, None, None)
                    except Exception:
                        pass

                if final_res:
                    if act_type == "bash":
                        output = (
                            final_res.get("output")
                            or final_res.get("stdout")
                            or final_res.get("error")
                            or ""
                        )
                        self._output_counter += 1
                        output_id = self._output_counter
                        self._hidden_outputs[output_id] = {
                            "command": str(act_content.get("command", "")),
                            "output": str(output),
                            "success": bool(final_res.get("success", False)),
                        }
                        fd, output_path = tempfile.mkstemp(
                            prefix=f"nemesis-bash-{output_id}-",
                            suffix=".log",
                            text=True,
                        )
                        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as output_file:
                            output_file.write(str(output))
                        self._hidden_outputs[output_id]["output_path"] = output_path
                        final_res = dict(final_res)
                        final_res["output_id"] = output_id
                        final_res["output_path"] = output_path
                    tool_duration = time.time() - tool_started
                    self.composer.display_tool_result(
                        final_res, tool_name=act_type, duration=tool_duration
                    )
                    self._record_session_message({
                        "kind": "tool",
                        "name": act_type,
                        "params": act_content,
                        "success": bool(final_res.get("success", False)),
                        "output": str(
                            final_res.get("output")
                            or final_res.get("stdout")
                            or final_res.get("error")
                            or ""
                        ),
                        "duration": round(tool_duration, 2),
                        "at": time.time(),
                    })
                    if act_type == "todo" and final_res.get("items") is not None:
                        self.composer.display_todo(final_res["items"])
                batch_feedback.append(self._format_feedback(action, final_res))

            current_input = "FEEDBACK:\n" + "\n\n".join(batch_feedback)
            current_role = "tool_result"
            continue

        if iteration >= max_tool_iterations:
            self.console.print(f"[error]Limite de {max_tool_iterations} iterations d'outils atteinte. Arret du cycle.[/error]")

        # Resume discret du tour : duree, outils, fichiers touches.
        if cycle_tools:
            self.composer.display_task_summary(
                time.time() - cycle_started, cycle_tools, len(cycle_files)
            )

    def run(self):
        self.session_start = time.time()
        self.console.clear()

        self._load_config()

        try:
            self.client = create_provider(self.config)
        except Exception as e:
            self.console.print(f"[error]Erreur provider: {e}[/error]")
            self.console.print(
                "[yellow]Provider indisponible. Vérifiez que NEMAPI est démarré et accessible.[/yellow]"
            )
            return

        self.executor = create_executor_from_config(self.config, bridge=self.client)
        # Synchroniser le parseur avec les outils de l'executeur
        if hasattr(self.executor, "registry") and self.executor.registry:
            extra_tools = set(self.executor.registry._tools.keys())
            self.parser = ActionParser(extra_valid_tools=extra_tools)

        src.core.default_commands.set_active_app(self)
        self._start_session()

        self.console.clear()

        # Provider info for welcome
        provider_type = self.config['provider']['type']
        host_info = getattr(self.client, "base_url", None)
        if not host_info:
            host = getattr(self.client, "host", None)
            port = getattr(self.client, "port", None)
            host_info = f"{host}:{port}" if host and port else "—"
        workspace = self.config.get("security", {}).get("workspace", "")

        # Modern welcome screen
        self.composer.display_welcome(
            provider=provider_type.upper(),
            target=host_info,
            model=getattr(self.client, "model", ""),
            workspace=workspace,
            connected=None,
        )

        # Pas de question interactive : le prompt systeme part automatiquement
        # au premier message. /continue permet de reprendre sans le renvoyer.

        # Ne pas tester la connexion au demarrage — le faire au premier message
        self._conn_ok = None

        try:
            while True:
                try:
                    user_input = self.composer.prompt_input()
                except KeyboardInterrupt:
                    self._handle_interrupt()
                    continue

                if self._consume_input_interrupt():
                    # _consume_input_interrupt() appelle deja _handle_interrupt().
                    continue

                if user_input is None:
                    # prompt_input a retourne None : Ctrl+C ou Ctrl+D.
                    self._handle_interrupt()
                    continue

                if not user_input.strip():
                    continue

                if user_input.strip().lower() in ("/exit", "/quit"):
                    parts = user_input.strip()[1:].split()
                    cmd = registry.get_command(parts[0].lower())
                    if cmd:
                        cmd.handler(parts[1:])
                    continue

                if user_input.startswith("/"):
                    parts = user_input[1:].split()
                    if not parts:
                        self._show_commands()
                        continue

                    cmd_name = parts[0].lower()
                    cmd_args = parts[1:]
                    cmd = registry.get_command(cmd_name)
                    if cmd:
                        res = cmd.handler(cmd_args)
                        if isinstance(res, str) and res.startswith("PROMPT_INTERNAL:"):
                            self._process_cycle(res.replace("PROMPT_INTERNAL:", "").strip())
                    else:
                        self.console.print(
                            f"[error]Commande inconnue : /{cmd_name}[/error] "
                            "[dim]Utilisez /help pour voir les commandes disponibles.[/dim]"
                        )
                else:
                    try:
                        self._process_cycle(user_input)
                    except KeyboardInterrupt:
                        self._handle_interrupt()
                        continue

        except KeyboardInterrupt:
            elapsed = time.time() - self.session_start
            mins, secs = divmod(int(elapsed), 60)
            self.console.print(f"\n[system]Session terminee — Duree totale : {mins:02d}:{secs:02d}[/system]")
            self.console.print("[system]Au revoir ![/system]")


def main(argv: Optional[List[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    debug_mode = "--debug" in args
    app = NemesisApp(debug=debug_mode)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())