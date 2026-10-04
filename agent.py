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
        self._send_system_prompt = False
        # Système d'autorisation pour les outils
        # Les outils à haut risque sont autorisés par signature exacte (outil +
        # paramètres) pour éviter qu'un "a" sur une commande bénigne autorise
        # ensuite toute commande du même type (ex: rm -rf).
        self._authorized_tools = set()  # Outils non destructifs autorisés (mode 'a')
        self._authorized_signatures = set()  # Signatures d'appels à haut risque
        self._last_auth_choice = None  # Dernier choix d'autorisation
        # Stockage des sorties de commandes pour affichage différé
        self._hidden_outputs = {}  # Dict: {output_id: {"command": str, "output": str, "success": bool}}
        self._output_counter = 0  # Compteur pour générer les IDs de sortie
        # Flag pour gérer l'interruption propre
        self._interrupted = False  # True si l'utilisateur a interrompu la tâche en cours

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
        # Keep the full config, but strip any MCP-server-shaped block that may
        # have been merged historically. Detection is structural (a command key
        # and no provider "type"), not a hard-coded list of server names.
        to_save = dict(self.config or {})
        for key in list(to_save.keys()):
            entry = to_save.get(key)
            if (
                isinstance(entry, dict)
                and "command" in entry
                and "type" not in entry
                and key not in ("provider",)
            ):
                to_save.pop(key, None)
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(to_save, f, default_flow_style=False, allow_unicode=True)

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

    @staticmethod
    def _auth_signature(tool_name: str, action_content: Dict) -> str:
        import hashlib
        import json as _json
        try:
            blob = _json.dumps(action_content, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            blob = repr(action_content)
        digest = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
        return f"{tool_name}:{digest}"

    def _ask_for_authorization(self, tool_name: str, action_content: Dict, risk: str = "medium") -> bool:
        """Ask the user to authorize a tool call. Returns True if allowed."""
        if risk == "read":
            return True
        params = action_content if isinstance(action_content, dict) else {"value": action_content}
        signature = self._auth_signature(tool_name, params)
        if tool_name in self._authorized_tools:
            return True
        if signature in self._authorized_signatures:
            return True

        self.console.print()
        if hasattr(self.composer, "display_auth"):
            self.composer.display_auth(tool_name, params)
        else:
            self.console.print(f"[yellow]Authorize tool:[/yellow] {tool_name}")
        self.console.print(f"[dim]  risque: {risk}[/dim]")

        while True:
            try:
                choice = self.composer.prompt_input(title=self.composer.AUTH_TITLE if hasattr(self.composer, "AUTH_TITLE") else "authorization [y/n/a]")
                if self._consume_input_interrupt():
                    return False
                if choice is None:
                    # EOF / cancelled prompt: deny instead of looping forever.
                    self.console.print("[yellow]  authorization cancelled[/yellow]")
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
                        if risk == "high":
                            # High-risk calls are approved by exact signature only.
                            self._authorized_signatures.add(signature)
                            self.console.print("[green]  this exact high-risk call authorized for this session[/green]")
                        else:
                            self._authorized_tools.add(tool_name)
                            self.console.print(f"[green]  '{tool_name}' authorized for this session[/green]")
                        self._last_auth_choice = "a"
                        return True
                self.console.print("[dim]  reply y, n, or a[/dim]")
            except (EOFError, KeyboardInterrupt):
                self.console.print("[yellow]  cancelled[/yellow]")
                return False

    def _authorize_subagent_tool(self, tool_name: str, params: Dict, risk: str) -> bool:
        """Authorization callback used by A2A sub-agents running in background.

        It reuses the same interactive prompt as the main loop, so a delegated
        task cannot run destructive tools without explicit user approval.
        """
        return self._ask_for_authorization(tool_name, params, risk)

    def _execute_single_tool(self, action: Dict) -> Dict:
        """Run one read-only tool and return the final result dict.

        Read-only tools have no interactive input or interruption handling, so
        they can safely run in a worker thread for parallel batches.
        """
        act_type = action['type']
        act_content = action.get('content', {}) or {}
        final_res: Dict[str, Any] = {}
        for update in self.executor.execute_tool(act_type, act_content):
            if "success" in update or (act_type != "bash" and update):
                final_res = update
            if act_type != "bash" and final_res:
                break
        return final_res

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
                return
            if normalized in {"o", "oui", "y", "yes"}:
                self._send_system_prompt = True
                return
            self.console.print("[dim]Répondez par o (oui) ou n (non).[/dim]")

    def _process_cycle(self, message: str):
        from src.core.default_commands import increment_message_count
        
        # Réinitialiser le flag d'interruption au début d'un nouveau cycle
        self._interrupted = False

        # Tester la connexion au premier message si pas encore fait
        if self._conn_ok is None:
            self._conn_ok = self.client.test_connection()

        if self._conn_ok is False:
            self.console.print("[error]NEMAPI n'est pas connecte. Utilisez /config pour le configurer.[/error]")
            return

        # Envoi du prompt systeme au premier message (une seule fois par session)
        if self._send_system_prompt and not self._prompt_sent:
            prompt = self.executor.get_system_prompt() if hasattr(self.executor, "get_system_prompt") else ""
            if prompt:
                try:
                    init_result = self.client.send_message(prompt, role="system")
                except TypeError:
                    init_result = self.client.send_message(prompt)
                self._prompt_sent = bool(init_result.get("success", True)) if isinstance(init_result, dict) else True

        increment_message_count()

        # Afficher le message de l'utilisateur en bulle
        self.composer.display_user_message(message)

        current_input = message
        current_role = "user"
        iteration = 0
        max_tool_iterations = 50
        last_tool_name = None
        last_response_hash = None
        last_feedback_signature = None
        consecutive_failures = 0
        start_time = time.time()

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
            try:
                try:
                    result = self.client.send_message(current_input, role=current_role)
                except TypeError:
                    result = self.client.send_message(current_input)
            finally:
                try:
                    anim.close()
                except Exception:
                    pass

            # Le spinner s'arrête automatiquement ici
            if not result['success']:
                self.console.print(f"[error]Erreur: {result.get('error', 'inconnu')}[/error]")
                break

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

            # --- Duplicate response detection (stable hash) ---
            import hashlib
            response_hash = hashlib.sha256(raw_response.strip().encode("utf-8", "replace")).hexdigest()
            if response_hash == last_response_hash and iteration > 1:
                self.console.print(
                    "[yellow]Réponse identique détectée : la boucle est interrompue pour éviter une répétition.[/yellow]"
                )
                if parsed['text'] and not parsed['text'].startswith("FEEDBACK:"):
                    self.composer.display_ai_message(parsed['text'])
                break
            last_response_hash = response_hash

            # --- Display AI text ---
            display_text = parsed['text']
            if display_text and not display_text.startswith("FEEDBACK:"):
                self.composer.display_ai_message(display_text)

            # --- No action = end of cycle ---
            actions = parsed.get("actions") or ([parsed["action"]] if parsed.get("action") else [])
            if not actions:
                break
            batch_feedback = []

            # --- Parallelize independent read-only calls in the same turn ---
            def _is_read_only(a):
                return self.executor.risk_for(a['type'], a.get('content', {})) == "read" if hasattr(self.executor, "risk_for") else False

            read_only_calls = [a for a in actions if _is_read_only(a)]
            other_calls = [a for a in actions if not _is_read_only(a)]

            if len(read_only_calls) > 1:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                results_by_idx = {}
                with ThreadPoolExecutor(max_workers=min(4, len(read_only_calls))) as pool:
                    future_to_idx = {}
                    for idx, action in enumerate(read_only_calls):
                        self.composer.display_tool_start(action['type'], action.get('content', {}) or {})
                        fut = pool.submit(self._execute_single_tool, action)
                        future_to_idx[fut] = idx
                    for fut in as_completed(future_to_idx):
                        idx = future_to_idx[fut]
                        try:
                            res = fut.result()
                        except Exception as e:
                            res = {"success": False, "stdout": f"Erreur: {e}"}
                        results_by_idx[idx] = res
                for idx, action in enumerate(read_only_calls):
                    res = results_by_idx[idx]
                    self.composer.display_tool_result(res, tool_name=action['type'])
                    batch_feedback.append(self._format_feedback(action, res))
                if len(other_calls) == 0:
                    current_input = "FEEDBACK:\n" + "\n\n".join(batch_feedback)
                    current_role = "tool_result"
                    continue
                actions_iter = other_calls
            else:
                actions_iter = actions

            for action in actions_iter:
                act_type = action['type']
                act_content = action.get('content', {})
                last_tool_name = act_type
                risk = self.executor.risk_for(act_type, act_content) if hasattr(self.executor, "risk_for") else "medium"
                if not self._ask_for_authorization(act_type, act_content, risk):
                    self.console.print(f"[red] Exécution de l'outil '{act_type}' refusée.[/red]")
                    batch_feedback.append(f"Tool: {act_type}\nSuccess: false\nOutput:\n[EXECUTION REFUSEE PAR L'UTILISATEUR]")
                    continue

                self.composer.display_tool_start(act_type, act_content if isinstance(act_content, dict) else {})
                final_res = {}
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
                    if "success" in update or (act_type != "bash" and update):
                        final_res = update
                    if act_type != "bash" and final_res:
                        break

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
                    self.composer.display_tool_result(final_res, tool_name=act_type)
                    if act_type == "todo" and final_res.get("items") is not None:
                        self.composer.display_todo(final_res["items"])
                batch_feedback.append(self._format_feedback(action, final_res))

            if any(("[EXECUTION REFUSEE" in fb) or ("Succes: False" in fb) for fb in batch_feedback):
                consecutive_failures += 1
            else:
                consecutive_failures = 0
            if consecutive_failures >= 3:
                self.console.print(
                    "[error]3 échecs consécutifs d'outils : arrêt du cycle pour éviter une boucle improductive.[/error]"
                )
                break

            feedback_input = "FEEDBACK:\n" + "\n\n".join(batch_feedback)
            feedback_signature = hashlib.sha256(feedback_input.encode("utf-8", "replace")).hexdigest()
            if feedback_signature == last_feedback_signature:
                self.console.print(
                    "[yellow]Aucun progrès détecté (même retour d'outil) : arrêt du cycle.[/yellow]"
                )
                break
            last_feedback_signature = feedback_signature
            current_input = feedback_input
            current_role = "tool_result"
            continue

        if iteration >= max_tool_iterations:
            self.console.print(f"[error]Limite de {max_tool_iterations} iterations d'outils atteinte. Arret du cycle.[/error]")

        elapsed = time.time() - start_time
        self.composer.display_task_summary(elapsed, tool_count=iteration - 1)

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
            self.parser = ActionParser.from_registry(self.executor.registry)
        
        # Passer l'exécuteur au scheduler A2A pour les subagents
        from src.core.agent_manager import get_scheduler
        scheduler = get_scheduler()
        scheduler.set_executor(self.executor)
        scheduler.set_auth_callback(self._authorize_subagent_tool)
        
        # Recharger les agents avec l'exécuteur maintenant disponible
        # Les agents chargés depuis agents.json avant que l'exécuteur ne soit créé n'avaient pas d'exécuteur
        from src.core.default_commands import reload_agents_with_executor
        reload_agents_with_executor(self.executor)
        
        src.core.default_commands.set_active_app(self)

        self.console.clear()

        # Provider info for welcome
        provider_type = self.config['provider']['type']
        host = getattr(self.client, "host", None)
        port = getattr(self.client, "port", None)
        host_info = (
            f"{host}:{port}"
            if host and port
            else getattr(self.client, "base_url", "—")
        )
        workspace = self.config.get("security", {}).get("workspace", "")

        # Modern welcome screen
        self.composer.display_welcome(
            provider=provider_type.upper(),
            target=host_info,
            model=getattr(self.client, "model", ""),
            workspace=workspace,
            connected=None,
        )

        self._ask_system_prompt()

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
                    continue

                if not user_input:
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