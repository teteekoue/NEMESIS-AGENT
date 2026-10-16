#!/usr/bin/env python3
"""Module d'execution d'outils pour l'agent CLI - Version 2.0 (JSON Function Calling)"""
import os, subprocess, sys, json, signal, time, re, builtins, shutil
try:
    import termios
except ImportError:
    termios = None
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Generator
from uploader import FileUploader
from src.core.mcp_manager import MCPManager
from tools_schema import get_tool_handler_method, validate_tool_call, get_all_tool_names

mcp_mgr = MCPManager()

class ActionExecutor:
    """Executeur d'outils pour NEMESIS CLI - Format JSON natif."""
    
    def __init__(self, workspace="./workspace", bridge=None):
        self.workspace_root = Path(workspace).resolve()
        if not self.workspace_root.exists():
            self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.bridge = bridge
        self.uploader = FileUploader()
        self.processes = {}
        self._waiting_for_input = None

    def resolve_path(self, path):
        raw = os.path.expanduser(str(path).strip())
        p = Path(raw)
        return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()

    # =====================================================================
    # NOUVEAU DISPATCH JSON
    # =====================================================================
    
    def execute_tool(self, tool_name: str, parameters: Dict[str, Any]):
        """Point d'entree principal : execute un outil a partir d'un appel JSON.
        Remplace l'ancien execute_action(action_type, content)."""
        try:
            valid, error_msg = validate_tool_call(tool_name, parameters)
            if not valid:
                yield {"success": False, "stdout": error_msg}
                return
            
            handler_name = get_tool_handler_method(tool_name)
            if handler_name is None:
                yield {"success": False, "stdout": f"Aucun handler pour: {tool_name}"}
                return
            
            handler = getattr(self, handler_name, None)
            if handler is None:
                yield {"success": False, "stdout": f"Handler introuvable: {handler_name}"}
                return
            
            result = handler(**parameters)
            if hasattr(result, '__iter__') and not isinstance(result, dict):
                yield from result
            else:
                yield result
                
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur outil '{tool_name}': {str(e)}"}

    # =====================================================================
    # HANDLERS JSON -> appellent les methodes internes
    # =====================================================================

    def execute_bash(self, mode: str = "synchrone", command: str = ""):
        """Handler JSON pour l'outil bash."""
        async_mode = (mode or "synchrone").lower() in ("asynchrone", "async", "background")
        if not command or not str(command).strip():
            yield {"success": False, "stdout": "Commande vide"}
            return
        if "sudo" in command and "-S" not in command:
            command = command.replace("sudo ", "sudo -S ")
        # Fast path: simple non-interactive commands (majority of agent use)
        interactive_hints = ("passwd", "ssh ", "mysql", "psql", "python -i", "node", "read ")
        needs_pty = any(h in command for h in interactive_hints) or command.strip().endswith("| less")
        if async_mode or needs_pty:
            yield from self.execute_bash_live(command, async_mode)
            return
        try:
            r = subprocess.run(
                command,
                shell=True,
                executable="/bin/bash",
                cwd=str(self.workspace_root),
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            yield {"success": r.returncode == 0, "stdout": out, "returncode": r.returncode}
        except subprocess.TimeoutExpired:
            yield {"success": False, "stdout": "[TIMEOUT] Commande > 5 minutes"}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur bash: {e}"}

    def execute_write(self, path: str = None, content: str = None, file_path: str = None):
        """Handler JSON pour l'outil write (ancien format avec path)."""
        # Gérer les deux formats : path et file_path
        actual_path = path or file_path
        if not actual_path or content is None:
            yield {"success": False, "stdout": "Chemin et contenu requis pour write"}
            return
        
        syntax_warnings = self._check_python_syntax_issues(content)
        result = self.smart_write(actual_path, content, "w")
        if syntax_warnings and result.get("success"):
            result["stdout"] += "\n" + syntax_warnings
        yield result

    def execute_write_file(self, file_path: str, content: str):
        """Handler JSON pour l'outil write_file (nouveau format)."""
        if isinstance(content, str):
            # LLM sometimes leaves literal \\n sequences instead of real newlines
            if content.count('\n') <= 1 and ('\\n' in content or (chr(92) + 'n') in content):
                content = content.replace(chr(92) + 'n', '\n').replace(chr(92) + 't', '\t')
        yield from self.execute_write(file_path=file_path, content=content)

    def execute_append(self, path: str, content: str):
        """Handler JSON pour l'outil append."""
        yield self.smart_write(path, content, "a")

    def _normalize_newlines(self, text: str) -> str:
        """Normalise les retours à la ligne : ne convertit que les \n litteraux restants
        (si jamais le JSON etait mal parse et que des \n litteraux subsistent).
        ATTENTION: ne pas double-decoder si json.loads a deja fait le travail.
        """
        result = text.replace('\\r\\n', '\n')
        result = result.replace('\\r', '\n')
        return result

    def _check_python_syntax_issues(self, content: str) -> str:
        """Detecte les problemes de syntaxe Python courants dans le contenu genere."""
        warnings = []
        lines = content.split('\n')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if (stripped.startswith('print(f"') or stripped.startswith("print(f'")) and not (stripped.endswith('")') or stripped.endswith("')")):
                if i+1 < len(lines):
                    next_line = lines[i+1].strip()
                    if '")' in next_line or "')" in next_line:
                        warnings.append(
                            f"\u26a0\ufe0f Ligne {i+1}: f-string coupee sur deux lignes. "
                            f"Cela cause une SyntaxError. Utilisez \\\\n dans la f-string, "
                            f"pas un vrai saut de ligne."
                        )
        return '\n'.join(warnings) if warnings else ""

    def execute_replace(self, path: str, blocks: list):
        """Handler JSON pour l'outil replace (ancien format avec blocks)."""
        try:
            file_path = self.resolve_path(path)
            if not file_path.exists():
                yield {"success": False, "stdout": f"Fichier introuvable: {path}"}
                return
            
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            modified = content
            replacements_done = 0
            
            for block in blocks:
                search_block = block.get("search", "")
                replace_block = block.get("replace", "")
                
                new_content, success, error = self._search_and_replace_block(
                    modified, search_block, replace_block, str(file_path)
                )
                
                if not success:
                    yield {"success": False, "stdout": f"Erreur replace: {error}"}
                    return
                
                modified = new_content
                replacements_done += 1
            
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(modified)
            
            msg = f"Replace: {replacements_done} bloc(s) remplace(s) dans {path}"
            if file_path.suffix in [".py", ".sh"]:
                val = self.validate_code(str(file_path))
                if not val["success"]:
                    msg += " (attention syntaxe)"
            
            yield {"success": True, "stdout": msg}
        except Exception as e:
            yield {"success": False, "stdout": str(e)}

    def execute_edit(self, file_path: str = None, old_string: str = None, new_string: str = None,
                     replace_all: bool = False, path: str = None, blocks: list = None):
        """Handler for the edit tool (canonical name, formerly search_replace).

        Supports two shapes:
        1. Simple: file_path, old_string, new_string [, replace_all]
        2. Blocks: path, blocks (legacy compatibility with replace)
        """
        if file_path and old_string is not None and new_string is not None:
            blocks = [{"search": old_string, "replace": new_string}]
            path = file_path
        elif path and blocks:
            pass
        else:
            yield {"success": False, "stdout": "Invalid parameters for edit"}
            return
        yield from self.execute_replace(path, blocks)

    def execute_search_replace(self, file_path: str = None, old_string: str = None, new_string: str = None,
                               replace_all: bool = False, path: str = None, blocks: list = None):
        """Legacy alias for execute_edit."""
        yield from self.execute_edit(
            file_path=file_path, old_string=old_string, new_string=new_string,
            replace_all=replace_all, path=path, blocks=blocks
        )

    def execute_read(self, files: list = None, path: str = None, paths: list = None,
                     offset: int = None, limit: int = None):
        """Handler JSON pour l'outil read / read_file.

        Accepte un seul fichier (`path`) ou plusieurs (`files` / `paths`, max 10).
        Support optionnel de `offset` (1-based) et `limit` (nombre de lignes).
        """
        file_list = []
        if files:
            if isinstance(files, str):
                file_list = [files]
            else:
                file_list = list(files)
        elif paths:
            if isinstance(paths, str):
                file_list = [paths]
            else:
                file_list = list(paths)
        elif path:
            file_list = [path]

        if not file_list:
            yield {"success": False, "stdout": "Aucun fichier spécifié. Utilisez `path` (str) ou `paths`/`files` (liste, max 10)."}
            return

        if len(file_list) > 10:
            yield {"success": False, "stdout": f"Trop de fichiers ({len(file_list)}). Maximum autorisé : 10 fichiers par appel."}
            return

        yield self.read_file(file_list, offset=offset, limit=limit)

    def execute_read_file(self, path: str = None, paths: list = None, files: list = None,
                          offset: int = None, limit: int = None):
        """Handler JSON pour l'outil read_file (format unifié, multi-fichiers)."""
        yield from self.execute_read(files=files, path=path, paths=paths, offset=offset, limit=limit)

    def execute_update_tracker(self, project: str, task: str, status: str):
        """Handler JSON pour l'outil update_tracker."""
        yield self.update_tracker(project, task, status)

    def execute_mcp_list(self):
        """Handler JSON pour l'outil mcp_list."""
        servers = mcp_mgr.list_servers()
        if not servers:
            yield {"success": True, "stdout": "Aucun serveur MCP installe."}
        else:
            server_names = builtins.list(servers.keys())
            output = "SERVEURS MCP INSTALLES :\n"
            output += "-" * 60 + "\n"
            for name in server_names:
                cfg = servers[name]
                output += f"Nom: {name}\n"
                output += f"Commande: {cfg['command']}\n"
                output += "-" * 60 + "\n"
            yield {"success": True, "stdout": output}

    def execute_mcp_tools_list(self, server: str):
        """Handler JSON pour l'outil mcp_tools_list."""
        client = mcp_mgr.get_client(server)
        if not client:
            yield {"success": False, "stdout": f"Serveur MCP '{server}' introuvable."}
        else:
            try:
                tools = client.list_tools()
                client.close()
                yield {"success": True, "stdout": json.dumps(tools, indent=2)}
            except Exception as e:
                yield {"success": False, "stdout": f"Erreur liste outils MCP: {str(e)}"}

    def execute_mcp_call(self, server: str, tool: str, arguments: dict = None):
        """Handler JSON pour l'outil mcp_call."""
        args_str = json.dumps(arguments) if arguments else "{}"
        yield self.mcp_call(server, tool, args_str)

    def execute_web_search(self, query: str):
        """Handler JSON pour l'outil web_search."""
        yield self.execute_web_search_impl(query)

    def execute_list_dir(self, path: str = ""):
        """Handler JSON pour l'outil list_dir."""
        result = self.list_dir(path or ".")
        yield result

    def execute_grep(self, pattern: str, path: str = ".", include: str = None, case_insensitive: bool = False):
        """Handler JSON pour l'outil grep."""
        try:
            from src.core.tools.grep_tool import grep_files
            result = grep_files(pattern, path, include, case_insensitive)
            yield result
        except Exception as e:
            yield {"success": False, "stdout": str(e)}

    def execute_web_fetch(self, url: str, format: str = "markdown"):
        """Handler JSON pour l'outil web_fetch."""
        try:
            import httpx
            import time
            
            # User-Agent pour eviter le blocage
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) NEMESIS-CLI/2.0"
            }
            
            response = httpx.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            
            content = response.text
            
            if format == "markdown":
                # Essayer de convertir en markdown si c'est du HTML
                if "<html" in content.lower():
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(content, 'html.parser')
                        # Retirer les scripts et styles
                        for script in soup(["script", "style", "noscript", "meta", "link"]):
                            script.decompose()
                        content = soup.get_text()
                    except:
                        pass
            
            yield {"success": True, "stdout": content, "url": url}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur web_fetch: {str(e)}"}

    def execute_delete_file(self, target_file: str):
        """Handler JSON pour l'outil delete_file."""
        try:
            file_path = self.resolve_path(target_file)
            if not file_path.exists():
                yield {"success": False, "stdout": f"Fichier introuvable: {target_file}"}
                return
            
            if file_path.is_dir():
                yield {"success": False, "stdout": f"Impossible de supprimer un dossier avec delete_file: {target_file}"}
                return
            
            file_path.unlink()
            yield {"success": True, "stdout": f"Fichier supprime: {target_file}"}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur delete_file: {str(e)}"}

    def execute_get_task_output(self, task_id: str):
        """Handler JSON pour l'outil get_task_output."""
        try:
            # Vérifier si le processus existe encore
            if task_id in self.processes:
                proc = self.processes[task_id]
                yield {"success": True, "stdout": "Processus encore en cours", "status": "running"}
            else:
                yield {"success": False, "stdout": f"Aucune sortie disponible pour la tâche: {task_id}"}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur get_task_output: {str(e)}"}

    def execute_kill_task(self, task_id: str):
        """Handler JSON pour l'outil kill_task."""
        try:
            # Convertir task_id en int si possible
            try:
                pid = int(task_id)
                yield self.kill_process(pid)
            except ValueError:
                yield {"success": False, "stdout": f"ID de tâche invalide: {task_id}"}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur kill_task: {str(e)}"}

    def execute_list_agents(self):
        """Handler JSON pour l'outil list_agents."""
        yield self.list_agents()

    def execute_delegate_task(self, agent: str, instruction: str, blocking: bool = False, label: str = ""):
        """Handler JSON pour l'outil delegate_task.

        TOUJOURS non-bloquant côté agent principal : la tâche part en thread,
        on renvoie immédiatement task_id + chemin du futur rapport.
        Le paramètre blocking est ignoré (conservé pour compat) — utiliser
        check_reports / agent_status / read_file sur le rapport pour suivre.
        """
        # Force async: main agent must not wait for the sub-agent
        yield self.delegate_task_a2a(
            agent=agent,
            instruction=instruction,
            blocking=False,
            label=label or "",
        )

    def execute_agent_status(self, task_id: str = "", agent: str = ""):
        """Statut d'une tâche A2A ou de tous les jobs / agents."""
        yield self.agent_status(task_id=task_id or "", agent=agent or "")

    def execute_check_reports(self):
        """Handler JSON pour l'outil check_reports."""
        yield self.check_reports()

    def stop_all_processes(self):
        """Handler JSON pour l'outil stop_all."""
        for pid in builtins.list(self.processes.keys()):
            try:
                os.kill(pid, signal.SIGTERM)
            except:
                pass
        self.processes.clear()
        yield {"success": True, "stdout": "Tous les processus sont arretes."}


    def list_dir(self, path):
        """List directory contents (pure Python, no tree dependency)."""
        try:
            target = self.resolve_path(path or ".")
            if not target.exists():
                return {"success": False, "stdout": f"Erreur: chemin introuvable: {path}"}
            if not target.is_dir():
                return {"success": False, "stdout": f"Erreur: n'est pas un dossier: {path}"}
            lines = []
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            for p in entries:
                if p.name.startswith(".") and p.name not in (".", ".."):
                    # include dotfiles but mark them
                    pass
                kind = "dir" if p.is_dir() else "file"
                try:
                    size = p.stat().st_size if p.is_file() else 0
                except OSError:
                    size = 0
                lines.append(f"{'[D]' if kind=='dir' else '[F]'} {p.name}" + (f" ({size} B)" if kind=="file" else "/"))
            # second level summary
            for p in entries:
                if p.is_dir():
                    try:
                        children = list(p.iterdir())[:8]
                        for c in children:
                            lines.append(f"    {'[D]' if c.is_dir() else '[F]'} {p.name}/{c.name}")
                        if len(list(p.iterdir())) > 8:
                            lines.append(f"    ...")
                    except OSError:
                        pass
            return {"success": True, "stdout": "\n".join(lines) if lines else "(vide)"}
        except Exception as e:
            return {"success": False, "stdout": str(e)}


    def execute_bash_live(self, command, async_mode=False):
        if async_mode:
            log_path = self.workspace_root / f"proc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            proc = subprocess.Popen(f"{command} > '{log_path}' 2>&1", shell=True, executable="/bin/bash", preexec_fn=os.setsid, cwd=self.workspace_root)
            self.processes[proc.pid] = {"cmd": command, "log": str(log_path), "start": datetime.now().isoformat()}
            yield {"success": True, "stdout": f"PID {proc.pid} lance en arriere-plan.\nLogs: {log_path.name}"}
        else:
            full_output = []
            # Utiliser stdin=PIPE pour pouvoir envoyer des entrées utilisateur
            # Utiliser pty pour un meilleur support des commandes interactives
            try:
                import pty
                master_fd, slave_fd = pty.openpty()
                
                # Configurer le terminal slave pour désactiver le buffering et l'écho
                try:
                    attrs = termios.tcgetattr(slave_fd)
                    attrs[3] = attrs[3] & ~termios.ICANON & ~termios.ECHO
                    termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
                except:
                    pass
                
                process = subprocess.Popen(command, shell=True, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, 
                                        text=False, executable="/bin/bash", cwd=self.workspace_root)
                os.close(slave_fd)
                
                # Configurer le descripteur de fichier maître pour la lecture non-bloquante
                import fcntl
                fcntl.fcntl(master_fd, fcntl.F_SETFL, fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)
                
                # Stocker le processus et le fd pour l'accès depuis agent.py
                self._waiting_for_input = process
                self._waiting_for_input_fd = master_fd
                
                # Buffer pour les données partielles et les dernières lignes à afficher
                buffer = b""
                display_lines = []
                MAX_DISPLAY_LINES = 50  # Augmente de 10 pour mieux détecter les prompts
                start = time.time()
                no_output_counter = 0
                
                while True:
                    # Vérifier si le processus est terminé
                    if process.poll() is not None:
                        # Envoyer les dernières données du buffer avant de quitter
                        if buffer:
                            try:
                                decoded = buffer.decode('utf-8')
                                if decoded.strip():
                                    full_output.append(decoded + '\n')
                                    display_lines.append(decoded + '\n')
                                    if len(display_lines) > MAX_DISPLAY_LINES:
                                        display_lines = display_lines[-MAX_DISPLAY_LINES:]
                            except:
                                pass
                        break
                    
                    # Vérifier le timeout global
                    if time.time() - start > 300:
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                            time.sleep(0.1)
                            if process.poll() is None:
                                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except:
                            try:
                                process.kill()
                            except:
                                pass
                        os.close(master_fd)
                        yield {"success": False, "stdout": "".join(full_output) + "\n[TIMEOUT] Commande depasse 5 minutes, arretee."}
                        return
                    
                    # Lire les données disponibles depuis le pty
                    try:
                        data = os.read(master_fd, 4096)
                        if data:
                            buffer += data
                            no_output_counter = 0
                            
                            # Traiter toutes les lignes complètes dans le buffer
                            while b'\n' in buffer:
                                line_bytes, buffer = buffer.split(b'\n', 1)
                                try:
                                    line = line_bytes.decode('utf-8')
                                except UnicodeDecodeError:
                                    line = line_bytes.decode('latin-1', errors='replace')
                                
                                if line or line_bytes:
                                    full_output.append(line + '\n')
                                    display_lines.append(line + '\n')
                                    if len(display_lines) > MAX_DISPLAY_LINES:
                                        display_lines = display_lines[-MAX_DISPLAY_LINES:]
                            
                            # Si buffer non vide, essayer d'afficher ce qu'on a (pour les prompts sans newline)
                            if buffer:
                                try:
                                    partial = buffer.decode('utf-8')
                                    if partial.strip():
                                        full_output.append(partial)
                                        display_lines.append(partial)
                                        if len(display_lines) > MAX_DISPLAY_LINES:
                                            display_lines = display_lines[-MAX_DISPLAY_LINES:]
                                except:
                                    pass
                        else:
                            # Pas de données disponibles
                            no_output_counter += 1
                            time.sleep(0.01)
                            
                            # Si pas de sortie depuis 0.1s, vérifier si le processus attend une entrée
                            if no_output_counter >= 10 and process.poll() is None:
                                # Vérifier si le processus est toujours actif
                                try:
                                    os.kill(process.pid, 0)
                                    # Le processus est toujours actif, il attend probablement une entrée
                                    context = "".join(display_lines[-MAX_DISPLAY_LINES:]) if display_lines else "[Commande attend une entrée...]"
                                    
                                    # Vérifier si le buffer contient déjà un prompt partiel
                                    if buffer:
                                        try:
                                            partial_prompt = buffer.decode('utf-8')
                                            if partial_prompt.strip():
                                                context = context + partial_prompt
                                        except:
                                            pass
                                    
                                    yield {"needs_input": True, "input_context": context}
                                    no_output_counter = 0
                                except ProcessLookupError:
                                    break
                                except Exception:
                                    no_output_counter += 1
                    except OSError:
                        break
                    except Exception as e:
                        no_output_counter += 1
                        time.sleep(0.01)
                
                # Fermer le fd et stdin
                try:
                    os.close(master_fd)
                except:
                    pass
                try:
                    process.stdin.close()
                except:
                    pass
                
                # Attendre la fin du processus pour récupérer le return code
                try:
                    process.wait(timeout=5)
                except:
                    process.kill()
                
                yield {"success": process.returncode == 0, "stdout": "".join(full_output)}
                
            except ImportError:
                # Fallback au système original si pty n'est pas disponible
                full_output = []
                process = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE, text=True, executable="/bin/bash", cwd=self.workspace_root)
                start = time.time()
                
                display_lines = []
                MAX_DISPLAY_LINES = 50  # Augmente de 10
                last_output_time = time.time()
                no_output_counter = 0
                
                while True:
                    if process.poll() is not None:
                        break
                    
                    if time.time() - start > 300:
                        process.kill()
                        yield {"success": False, "stdout": "".join(full_output) + "\n[TIMEOUT] Commande depasse 5 minutes, arretee."}
                        return
                    
                    try:
                        import select
                        rlist, _, _ = select.select([process.stdout], [], [], 0.05)
                        if process.stdout in rlist:
                            line = process.stdout.readline()
                            if line:
                                full_output.append(line)
                                display_lines.append(line)
                                if len(display_lines) > MAX_DISPLAY_LINES:
                                    display_lines = display_lines[-MAX_DISPLAY_LINES:]
                                last_output_time = time.time()
                                no_output_counter = 0
                            else:
                                no_output_counter += 1
                        else:
                            no_output_counter += 1
                    except Exception:
                        line = process.stdout.readline()
                        if not line and process.poll() is not None:
                            break
                        if line:
                            full_output.append(line)
                            display_lines.append(line)
                            if len(display_lines) > MAX_DISPLAY_LINES:
                                display_lines = display_lines[-MAX_DISPLAY_LINES:]
                            last_output_time = time.time()
                            no_output_counter = 0
                        else:
                            no_output_counter += 1
                    
                    # Détection plus rapide : 0.2s sans sortie = besoin d'entrée
                    if no_output_counter >= 4 and process.poll() is None:
                        context = "".join(display_lines[-MAX_DISPLAY_LINES:]) if display_lines else "[Commande attend une entrée...]"
                        yield {"needs_input": True, "input_context": context}
                        
                        self._waiting_for_input = process
                        no_output_counter = 0
                        last_output_time = time.time()
                        time.sleep(0.1)
                
                try:
                    process.stdin.close()
                except:
                    pass
                
                yield {"success": process.returncode == 0, "stdout": "".join(full_output)}


    def validate_code(self, file_path):
        target = self.resolve_path(file_path)
        if not target.exists():
            return {"success": False, "stdout": "Fichier inexistant"}
        if target.suffix == ".py":
            result = subprocess.run([sys.executable, "-m", "py_compile", str(target)], capture_output=True, text=True)
        else:
            result = subprocess.run(["sh", "-n", str(target)], capture_output=True, text=True)
        return {"success": result.returncode == 0, "stdout": result.stdout + result.stderr}


    def _extract_action_content(self, content):
        """Extrait le contenu brut d'un bloc markdown code fence si present.
        Utilise par les anciens handlers XML (replace_file). Les handlers JSON
        recoivent deja le contenu propre via json.loads."""
        pattern = re.compile(r'^```.*?\n(.*?)\n\s*```\s*$', re.DOTALL | re.MULTILINE)
        match = pattern.search(content.strip())
        return match.group(1) if match else content.strip()


    def smart_write(self, path, content, mode="w"):
        try:
            file_path = self.resolve_path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            if not content:
                return {"success": False, "stdout": "Contenu vide - fichier non ecrit"}
            with open(file_path, mode, encoding="utf-8") as f:
                f.write(content + "\n")
            msg = f"Fichier ecrit: {file_path} ({len(content)} caracteres)"
            if file_path.suffix in [".py", ".sh"]:
                val = self.validate_code(str(file_path))
                if not val["success"]:
                    msg += " (attention syntaxe)"
            return {"success": True, "stdout": msg}
        except Exception as e:
            return {"success": False, "stdout": str(e)}

    # =====================================================================
    # NOUVEAU SYSTEME REPLACE (Search & Replace en cascade 4 niveaux)
    # =====================================================================


    def _normalize_whitespace(self, text):
        """Niveau 2 : reduit les espaces multiples a un seul, strip debut/fin de ligne."""
        lines = []
        for line in text.splitlines():
            lines.append(re.sub(r'[ \t]+', ' ', line).strip())
        return '\n'.join(lines)


    def _strip_indent(self, text):
        """Niveau 3 : supprime toute indentation en debut de ligne."""
        lines = []
        for line in text.splitlines():
            lines.append(line.lstrip())
        return '\n'.join(lines)


    def _levenshtein_ratio(self, a, b):
        """Niveau 4 : ratio de similarite entre deux chaines (0.0 a 1.0)."""
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        m, n = len(a), len(b)
        if m == 0 or n == 0:
            return 0.0
        # Matrice optimisee (une seule ligne)
        prev = builtins.list(range(n + 1))
        curr = [0] * (n + 1)
        for i in range(1, m + 1):
            curr[0] = i
            for j in range(1, n + 1):
                cost = 0 if a[i-1] == b[j-1] else 1
                curr[j] = min(prev[j] + 1, curr[j-1] + 1, prev[j-1] + cost)
            prev, curr = curr, prev
        distance = prev[n]
        max_len = max(m, n)
        return 1.0 - (distance / max_len)


    def _find_block_position(self, original_content, normalized_search, normalized_full, level):
        """Retrouve la position du bloc SEARCH dans le contenu original."""
        pos = normalized_full.find(normalized_search)
        if pos == -1:
            return -1, -1

        # Compter les caracteres pour retrouver la position dans l'original
        orig_lines = original_content.splitlines(True)
        norm_lines = normalized_full.splitlines(True)
        search_lines = normalized_search.splitlines(True)

        line_count = 0
        char_count = 0
        for i, line in enumerate(norm_lines):
            if char_count >= pos:
                line_count = i
                break
            char_count += len(line)

        # Compter la longueur du bloc original correspondant
        start_pos = 0
        for i in range(line_count):
            start_pos += len(orig_lines[i])

        end_line = min(line_count + len(search_lines), len(orig_lines))
        end_pos = start_pos
        for i in range(line_count, end_line):
            end_pos += len(orig_lines[i])

        return start_pos, end_pos


    def _search_and_replace_block(self, content, search_block, replace_block, file_path_for_errors=""):
        """
        Cherche search_block dans content avec 4 niveaux de tolerance,
        puis le remplace par replace_block.
        Retourne (new_content, success, error_message).
        """
        # Niveau 1 : Exact
        count = content.count(search_block)
        if count == 1:
            new_content = content.replace(search_block, replace_block, 1)
            return new_content, True, None
        elif count > 1:
            return content, False, f"Bloc trouve {count} fois dans le fichier. Ajoute plus de contexte pour le rendre unique."

        # Niveau 2 : Normalisation des espaces
        norm_search = self._normalize_whitespace(search_block)
        norm_content = self._normalize_whitespace(content)
        count = norm_content.count(norm_search)
        if count == 1:
            start, end = self._find_block_position(content, norm_search, norm_content, "whitespace")
            if start >= 0:
                new_content = content[:start] + replace_block + content[end:]
                return new_content, True, None
            return content, False, "Erreur interne : bloc normalise trouve mais position introuvable."
        elif count > 1:
            return content, False, f"Bloc trouve {count} fois apres normalisation. Ajoute plus de contexte."

        # Niveau 3 : Ignore l'indentation
        noindent_search = self._strip_indent(search_block)
        noindent_content = self._strip_indent(content)
        count = noindent_content.count(noindent_search)
        if count == 1:
            start, end = self._find_block_position(content, noindent_search, noindent_content, "noindent")
            if start >= 0:
                new_content = content[:start] + replace_block + content[end:]
                return new_content, True, None
            return content, False, "Erreur interne : bloc sans indentation trouve mais position introuvable."
        elif count > 1:
            return content, False, f"Bloc trouve {count} fois apres suppression de l'indentation. Ajoute plus de contexte."

        # Niveau 4 : Flou (Levenshtein)
        best_ratio = 0.0
        best_start = -1
        best_end = -1
        lines = content.splitlines(True)
        search_lines = search_block.splitlines(True)
        search_len = len(search_block)
        total_len = len(content)

        for i in range(len(lines) - len(search_lines) + 1):
            # Fenetre approximative basee sur les lignes
            window_start = sum(len(l) for l in lines[:i])
            window_end = window_start + sum(len(l) for l in lines[i:i+len(search_lines)])
            window_end = min(window_end, total_len)
            window_text = content[window_start:window_end]
            ratio = self._levenshtein_ratio(search_block, window_text)
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = window_start
                best_end = window_end

        if best_ratio >= 0.85:
            new_content = content[:best_start] + replace_block + content[best_end:]
            return new_content, True, None

        # Echec total : generer un feedback utile
        context_lines = []
        file_lines = content.splitlines()
        for idx, line in enumerate(file_lines[:20], 1):
            if search_block.splitlines()[0].strip()[:20] in line:
                start_ctx = max(0, idx - 3)
                end_ctx = min(len(file_lines), idx + 5)
                for ln in range(start_ctx, end_ctx):
                    context_lines.append(f"   {ln+1}| {file_lines[ln]}")
                break
        if not context_lines:
            total_lines = len(file_lines)
            mid = total_lines // 2
            for ln in range(max(0, mid-3), min(total_lines, mid+5)):
                context_lines.append(f"   {ln+1}| {file_lines[ln]}")

        ctx_str = '\n'.join(context_lines[:15]) if context_lines else "Fichier vide ou illisible."
        return content, False, f"Bloc SEARCH introuvable (meme avec tolerance floue).\nContenu actuel autour de la zone probable :\n{ctx_str}\nLe fichier a peut-etre deja ete modifie ? Verifie avec read ou cat."


    def replace_file(self, path, content):
        """
        Remplace un ou plusieurs blocs dans un fichier.
        Format : le nom du fichier sur la premiere ligne, puis blocs SEARCH/REPLACE.
        Chaque bloc est traite sequentiellement (l'ordre compte).
        """
        try:
            file_path = self.resolve_path(path)
            if not file_path.exists():
                return {"success": False, "stdout": f"Fichier introuvable: {path}"}

            with open(file_path, "r", encoding="utf-8") as f:
                original = f.read()

            # Parser les blocs : le contenu est deja nettoye par _extract_action_content
            # Format attendu : <<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE
            blocks = content.split("<<<<<<< SEARCH")
            if len(blocks) < 2:
                return {"success": False, "stdout": "Aucun bloc SEARCH trouve. Format attendu : <<<<<<< SEARCH\\n...\\n=======\\n...\\n>>>>>>> REPLACE"}

            modified = original
            replacements_done = 0
            errors = []

            for block in blocks[1:]:
                if ">>>>>>> REPLACE" not in block:
                    continue
                if "=======" not in block:
                    continue

                parts = block.split("=======", 1)
                if len(parts) != 2:
                    continue

                search_part = parts[0].strip()
                replace_part = parts[1].split(">>>>>>> REPLACE")[0].strip()

                if not search_part:
                    errors.append("Bloc SEARCH vide - ignore.")
                    continue

                new_content, success, error = self._search_and_replace_block(
                    modified, search_part, replace_part, str(file_path)
                )

                if success:
                    modified = new_content
                    replacements_done += 1
                elif error:
                    errors.append(error)

            if replacements_done == 0:
                return {"success": False, "stdout": f"Aucun remplacement effectue.\nErreurs :\n" + '\n'.join(f'  - {e}' for e in errors)}

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(modified)

            msg = f"Replace: {replacements_done} bloc(s) remplace(s) dans {file_path}"
            if errors:
                msg += f"\nBlocs ignores :\n" + '\n'.join(f'  - {e}' for e in errors)

            if file_path.suffix in [".py", ".sh"]:
                val = self.validate_code(str(file_path))
                if not val["success"]:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(original)
                    return {"success": False, "stdout": f"Erreur de syntaxe apres replace - modifications annulees."}

            return {"success": True, "stdout": msg}
        except Exception as e:
            return {"success": False, "stdout": str(e)}

    # =====================================================================
    # FIN DU NOUVEAU SYSTEME REPLACE
    # =====================================================================


    def _is_text_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                f.read(1024)
            return True
        except (UnicodeDecodeError, IsADirectoryError):
            return False


    def read_file(self, paths, offset: int = None, limit: int = None):
        """Lit un ou plusieurs fichiers (max 10) et retourne le contenu avec préfixe LINE_NUMBER→.

        - paths : liste de chemins (1 à 10)
        - offset : ligne de départ 1-based (optionnel, appliqué à chaque fichier)
        - limit  : nombre max de lignes à retourner par fichier (optionnel)

        Pour les fichiers binaires ou trop volumineux (>2 Mo de texte),
        un upload public est effectué à la place.
        """
        MAX_TEXT_BYTES = 2 * 1024 * 1024   # 2 Mo — au-delà on upload
        MAX_SIZE = 100 * 1024 * 1024       # 100 Mo hard limit

        if not paths:
            return {"success": False, "stdout": "Aucun fichier spécifié"}

        if len(paths) > 10:
            return {"success": False, "stdout": f"Trop de fichiers ({len(paths)}). Maximum : 10."}

        try:
            resolved = []
            for p in paths:
                fp = self.resolve_path(str(p).strip())
                if not fp.exists():
                    return {"success": False, "stdout": f"Fichier introuvable: {p}"}
                if not fp.is_file():
                    return {"success": False, "stdout": f"N'est pas un fichier: {p}"}
                if fp.stat().st_size > MAX_SIZE:
                    return {"success": False, "stdout": f"Fichier trop volumineux (>100 Mo): {fp.name}"}
                resolved.append(fp)

            parts = []
            for src in resolved:
                header = f"=== {src} ==="
                if not self._is_text_file(src):
                    try:
                        url = self.uploader.upload(str(src))
                        parts.append(f"{header}\n[BINAIRE] Uploadé : {url}")
                    except Exception as e:
                        parts.append(f"{header}\n[BINAIRE] Impossible d'uploader : {e}")
                    continue

                size = src.stat().st_size
                if size > MAX_TEXT_BYTES:
                    # Trop gros pour le contexte → upload
                    txt_name = src.stem + ".txt"
                    txt_path = self.workspace_root / txt_name
                    try:
                        with open(src, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                        with open(txt_path, "w", encoding="utf-8") as f:
                            f.write(f"{header}\n{content}")
                        url = self.uploader.upload(str(txt_path))
                        parts.append(f"{header}\n[TROP VOLUMINEUX] Uploadé : {url}")
                    finally:
                        try:
                            if txt_path.exists():
                                txt_path.unlink()
                        except Exception:
                            pass
                    continue

                with open(src, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

                start = 0
                if offset is not None:
                    try:
                        start = max(0, int(offset) - 1)
                    except (TypeError, ValueError):
                        start = 0

                end = len(lines)
                if limit is not None:
                    try:
                        end = min(len(lines), start + int(limit))
                    except (TypeError, ValueError):
                        end = len(lines)

                numbered = []
                for i, line in enumerate(lines[start:end], start=start + 1):
                    # Préfixe LINE_NUMBER→ (le préfixe n'appartient pas au fichier)
                    numbered.append(f"{i}→{line.rstrip(chr(10) + chr(13))}")

                body = "\n".join(numbered)
                if not body:
                    body = "(fichier vide ou plage hors limites)"
                parts.append(f"{header}\n{body}")

            separator = "\n\n" + ("=" * 60) + "\n\n"
            stdout = separator.join(parts)
            return {"success": True, "stdout": stdout, "files_read": len(resolved)}

        except Exception as e:
            return {"success": False, "stdout": f"Erreur read_file: {e}"}


    def check_process(self, pid):
        if pid not in self.processes:
            return {"success": False, "stdout": "PID inconnu"}
        try:
            os.kill(pid, 0)
        except OSError:
            return {"success": True, "stdout": f"PID {pid} termine."}
        with open(self.processes[pid]["log"], "r") as f:
            logs = f.readlines()[-20:]
        return {"success": True, "stdout": f"PID {pid} actif. Logs:\n" + "".join(logs)}


    def kill_process(self, pid):
        if pid not in self.processes:
            return {"success": False, "stdout": "PID inconnu"}
        try:
            os.kill(pid, signal.SIGTERM)
            del self.processes[pid]
        except OSError:
            pass
        return {"success": True, "stdout": f"Processus {pid} arrete."}


    def cleanup_logs(self):
        count = 0
        for pid, info in builtins.list(self.processes.items()):
            try:
                os.kill(pid, 0)
            except OSError:
                if os.path.exists(info["log"]):
                    os.remove(info["log"])
                    count += 1
                del self.processes[pid]
        return {"success": True, "stdout": f"Nettoyage : {count} logs supprimes."}


    def execute_pdf_to_text(self, pdf_file_path, output_dir):
        if not shutil.which("soffice"):
            return {"success": False, "stdout": "LibreOffice non installe. sudo apt install libreoffice"}
        try:
            pdf_path = self.resolve_path(pdf_file_path)
            output_path = self.resolve_path(output_dir)
            cmd = f"soffice --headless --convert-to txt:Text --outdir '{output_path}' '{pdf_path}'"
            subprocess.run(cmd, shell=True, executable="/bin/bash")
            txt_path = output_path / (pdf_path.stem + ".txt")
            if txt_path.exists():
                with open(txt_path, "r") as f:
                    return {"success": True, "stdout": f.read()}
            return {"success": False, "stdout": "Echec de l'extraction."}
        except Exception as e:
            return {"success": False, "stdout": str(e)}


    def execute_html_to_pdf(self, html_file_path, output_dir):
        if not shutil.which("soffice"):
            return {"success": False, "stdout": "LibreOffice non installe. sudo apt install libreoffice"}
        try:
            html_path = self.resolve_path(html_file_path)
            output_path = self.resolve_path(output_dir)
            cmd = f"soffice --headless --convert-to pdf --outdir '{output_path}' '{html_path}'"
            subprocess.run(cmd, shell=True, executable="/bin/bash")
            return {"success": True, "stdout": f"PDF genere dans {output_path}"}
        except Exception as e:
            return {"success": False, "stdout": str(e)}


    def execute_upload(self, file_path):
        try:
            target = self.resolve_path(file_path)
            if not target.exists():
                return {"success": False, "stdout": f"Fichier introuvable: {target}"}
            url = self.uploader.upload(str(target))
            return {"success": True, "stdout": f"Fichier uploade: {url}"}
        except Exception as e:
            return {"success": False, "stdout": str(e)}


    def update_tracker(self, project_path, task, status):
        try:
            # Créer à la racine du projet, pas dans le workspace
            root = Path(".").resolve()
            todo_file = root / "todo.md"
            json_file = root / "projets.json"
            
            data = {}
            if json_file.exists():
                with open(json_file, "r") as f:
                    data = json.load(f)
            
            data[task] = {"status": status, "updated": datetime.now().isoformat()}
            with open(json_file, "w") as f:
                json.dump(data, f, indent=4)
                
            with open(todo_file, "w") as f:
                f.write("# Taches du Projet\n\n")
                for t, info in data.items():
                    mark = "x" if info["status"] == "done" else " "
                    f.write(f"- [{mark}] {t} ({info['status']})\n")
            return {"success": True, "stdout": f"Tracker mis a jour : {task} dans {root}"}
        except Exception as e:
            return {"success": False, "stdout": str(e)}


    def list_skills(self):
        try:
            from src.core.skills_manager import SkillManager
            mgr = SkillManager()
            skills = mgr.list_installed()
            if not skills:
                return {"success": True, "stdout": "Aucun skill additionnel installe dans tools_library/."}
            
            output = "SKILLS INSTALLES :\n"
            output += "-" * 60 + "\n"
            for s in skills:
                output += f"Nom: {s['name']} (v{s['version']})\n"
                output += f"Description: {s['description']}\n"
                output += f"Dossier: tools_library/{s['name']}\n"
                output += "-" * 60 + "\n"
            return {"success": True, "stdout": output}
        except Exception as e:
            return {"success": False, "stdout": f"Erreur lors du listage des skills: {str(e)}"}


    def mcp_call(self, server_cmd, tool_name, args_json):
        try:
            from src.core.mcp_client import SimpleMCPClient
            import json
            # Note: Pour une implémentation réelle, on devrait maintenir une liste 
            # de clients MCP actifs. Ici on en instancie un au vol pour le test.
            client = SimpleMCPClient(server_cmd, []) 
            args = json.loads(args_json)
            result = client.call_tool(tool_name, args)
            client.close()
            return {"success": True, "stdout": str(result)}
        except Exception as e:
            return {"success": False, "stdout": f"Erreur MCP: {str(e)}"}


    def list_agents(self):
        try:
            from src.core.agent_manager import get_scheduler
            from src.core.default_commands import ACTIVE_AGENTS
            scheduler = get_scheduler()
            agents = scheduler.list_agents()
            if not agents and not ACTIVE_AGENTS:
                return {"success": True, "stdout": "Aucun agent enregistre."}
            lines = []
            for a in agents:
                lines.append(
                    f"- {a['name']} | status={a['status']} | provider={a.get('provider')} | "
                    f"model={a.get('model')} | task={a.get('current_task') or '-'}"
                )
            for name in ACTIVE_AGENTS:
                if not any(a["name"] == name for a in agents):
                    lines.append(f"- {name} (enregistre, hors scheduler)")
            return {"success": True, "stdout": "Agents A2A:\n" + "\n".join(lines)}
        except Exception as e:
            return {"success": False, "stdout": str(e)}

    def delegate_task_a2a(self, agent: str, instruction: str, blocking: bool = False, label: str = ""):
        """Délègue une tâche à un sous-agent A2A en arrière-plan (non-bloquant).

        Retourne immédiatement un task_id. Le sous-agent travaille en parallèle.
        À la fin il écrit un rapport indépendant :
            <workspace>/a2a_reports/task_<task_id>.md
        Suivi : agent_status / check_reports / read_file sur ce rapport.
        """
        try:
            from src.core.default_commands import ACTIVE_AGENTS
            from src.core.agent_manager import get_scheduler

            scheduler = get_scheduler()
            name = (agent or "").strip()
            task = (instruction or "").strip()
            if not name or not task:
                return {"success": False, "stdout": "Paramètres requis: agent, instruction"}

            if name not in ACTIVE_AGENTS and name not in scheduler.agents:
                return {
                    "success": False,
                    "stdout": f"Agent '{name}' inconnu. Utilisez /agents pour en créer un (NemAPI v3).",
                }

            if name in ACTIVE_AGENTS:
                ag = ACTIVE_AGENTS[name]
                if not ag._executor:
                    ag.set_executor(self)
                if name not in scheduler.agents:
                    scheduler.register_agent_client(ag)

            if scheduler._executor is None:
                scheduler.set_executor(self)

            # Always non-blocking for the main agent tool call
            task_id = scheduler.delegate(
                agent_name=name,
                label=(label or task)[:80],
                description=task,
                instructions=[task],
                blocking=False,
            )
            if not task_id:
                return {
                    "success": False,
                    "stdout": (
                        f"Délégation refusée (agent '{name}' occupé ou injoignable). "
                        "Vérifiez list_agents / agent_status."
                    ),
                }

            reports_dir = scheduler._ensure_reports_dir()
            report_md = reports_dir / f"task_{task_id}.md"
            return {
                "success": True,
                "stdout": (
                    f"Tâche déléguée à '{name}' en arrière-plan (non-bloquant).\n"
                    f"task_id     : {task_id}\n"
                    f"status      : running\n"
                    f"rapport     : {report_md}\n"
                    f"Suivi       : agent_status(task_id=\"{task_id}\") ou check_reports\n"
                    f"Confirmation: le fichier rapport apparaît quand le sous-agent a terminé."
                ),
                "task_id": task_id,
                "report_path": str(report_md),
                "agent": name,
                "status": "running",
            }
        except Exception as e:
            import traceback
            return {
                "success": False,
                "stdout": f"Erreur délégation A2A: {e}\n{traceback.format_exc()}",
            }

    def agent_status(self, task_id: str = "", agent: str = ""):
        """Statut des jobs A2A et/ou d'un agent."""
        try:
            from src.core.agent_manager import get_scheduler
            from src.core.default_commands import ACTIVE_AGENTS
            scheduler = get_scheduler()
            lines = []

            if task_id:
                st = scheduler.get_task_status(task_id)
                lines.append(f"task_id     : {st.get('task_id', task_id)}")
                lines.append(f"status      : {st.get('status', 'unknown')}")
                lines.append(f"agent       : {st.get('agent')}")
                lines.append(f"label       : {st.get('label')}")
                lines.append(f"report_path : {st.get('report_path')}")
                if st.get("error"):
                    lines.append(f"error       : {st.get('error')}")
                if st.get("summary"):
                    lines.append(f"summary     : {st.get('summary')}")
                # Also check disk report
                reports_dir = scheduler._ensure_reports_dir()
                md = reports_dir / f"task_{task_id}.md"
                if md.exists():
                    lines.append(f"rapport_disk: {md} (présent)")
                else:
                    lines.append(f"rapport_disk: {md} (pas encore généré)")
            else:
                st = scheduler.get_task_status("")
                tasks = st.get("tasks") or []
                if not tasks:
                    lines.append("Aucun job A2A en mémoire.")
                else:
                    lines.append(f"{len(tasks)} job(s) A2A:")
                    for t in tasks:
                        lines.append(
                            f"  - {t.get('task_id')}: status={t.get('status')} "
                            f"agent={t.get('agent')} label={str(t.get('label') or '')[:40]}"
                        )
                # Agents
                lines.append("")
                lines.append("Agents:")
                for a in scheduler.list_agents():
                    lines.append(
                        f"  - {a['name']}: status={a['status']} model={a.get('model')} "
                        f"task={a.get('current_task') or '-'}"
                    )
                for name, ag in ACTIVE_AGENTS.items():
                    if name not in scheduler.agents:
                        lines.append(f"  - {name}: (hors scheduler) status={getattr(ag, 'status', '?')}")

                # Disk reports
                reports_dir = scheduler._ensure_reports_dir()
                mds = sorted(reports_dir.glob("task_*.md"))[-15:]
                lines.append("")
                lines.append(f"Rapports sur disque ({reports_dir}):")
                if not mds:
                    lines.append("  (aucun)")
                else:
                    for f in mds:
                        lines.append(f"  - {f.name}")

            return {"success": True, "stdout": "\n".join(lines)}
        except Exception as e:
            return {"success": False, "stdout": str(e)}


    def delegate_task(self, content):
        """Legacy: content = 'AgentName|instruction' or '[Agent: Name] instruction'."""
        try:
            text = (content or "").strip()
            name, task = "", ""
            if text.startswith("[Agent:") and "]" in text:
                head, _, rest = text.partition("]")
                name = head.replace("[Agent:", "").strip()
                task = rest.strip()
            elif "|" in text:
                name, task = text.split("|", 1)
                name, task = name.strip(), task.strip()
            else:
                return {"success": False, "stdout": "Format: agent|instruction"}
            return self.delegate_task_a2a(agent=name, instruction=task, blocking=False)
        except Exception as e:
            return {"success": False, "stdout": str(e)}

    def check_reports(self):
        """Liste les jobs A2A et les rapports task_*.md sur disque."""
        return self.agent_status(task_id="", agent="")



    def execute_web_search_impl(self, query):
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
                output = f"Résultats pour '{query}':\n\n"
                for i, r in enumerate(results, 1):
                    title = r.get('title', 'Sans titre')
                    href = r.get('href', 'URL non disponible')
                    body = r.get('body', 'Aucun extrait')
                    output += f"{i}. {title}\n   URL: {href}\n   Snippet: {body}\n\n"
            return {"success": True, "stdout": output}
        except Exception as e:
            return {"success": False, "stdout": f"Erreur de recherche: {str(e)}"}


    def execute_glob(self, pattern: str, path: str = ".", max_results: int = 200):
        """Find files by glob pattern under path."""
        try:
            import fnmatch
            root = self.resolve_path(path or ".")
            if not root.exists():
                yield {"success": False, "stdout": f"Chemin introuvable: {path}"}
                return
            matches = []
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    rel = str(p.relative_to(root))
                except ValueError:
                    rel = str(p)
                if (
                    fnmatch.fnmatch(rel, pattern)
                    or fnmatch.fnmatch(p.name, pattern)
                    or fnmatch.fnmatch(str(p), pattern)
                ):
                    matches.append(rel)
                if len(matches) >= int(max_results or 200):
                    break
            if not matches:
                yield {"success": True, "stdout": f"Aucun fichier pour pattern '{pattern}'"}
            else:
                yield {"success": True, "stdout": "\n".join(matches), "count": len(matches)}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur glob: {e}"}


    def execute_git(self, action: str = "status", path: str = "", limit: int = 20):
        """Inspect git repository state."""
        action = (action or "status").lower().strip()
        try:
            import subprocess
            cwd = str(self.workspace_root)
            if action == "status":
                cmd = ["git", "status", "--short", "--branch"]
            elif action == "diff":
                cmd = ["git", "diff"]
                if path:
                    cmd.append(path)
            elif action == "log":
                n = int(limit or 20)
                cmd = ["git", "log", f"-{n}", "--oneline", "--decorate"]
            elif action == "branch":
                cmd = ["git", "branch", "-a"]
            else:
                yield {"success": False, "stdout": f"Action git inconnue: {action}. Utilisez status|diff|log|branch"}
                return
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=30)
            out = (r.stdout or "") + (r.stderr or "")
            yield {"success": r.returncode == 0, "stdout": out or "(vide)"}
        except FileNotFoundError:
            yield {"success": False, "stdout": "git non installé"}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur git: {e}"}

    def execute_todo(self, action: str = "list", content: str = "", items: list = None,
                     id: str = "", status: str = "", force: bool = False):
        """Structured in-memory todo list (persisted under workspace/.nemesis_todos.json)."""
        import json
        from pathlib import Path as P
        store = self.workspace_root / ".nemesis_todos.json"
        todos = []
        if store.exists():
            try:
                todos = json.loads(store.read_text(encoding="utf-8"))
            except Exception:
                todos = []
        action = (action or "list").lower()
        if action == "list":
            if not todos:
                yield {"success": True, "stdout": "Todo list vide."}
                return
            lines = []
            for t in todos:
                lines.append(f"[{t.get('status','pending')}] {t.get('id')}: {t.get('content')}")
            yield {"success": True, "stdout": "\n".join(lines)}
            return
        if action == "clear":
            if force or True:
                store.write_text("[]", encoding="utf-8")
            yield {"success": True, "stdout": "Todo list effacée."}
            return
        if action == "add":
            import uuid
            to_add = items if items else ([content] if content else [])
            if not to_add:
                yield {"success": False, "stdout": "content ou items requis"}
                return
            for c in to_add:
                todos.append({"id": uuid.uuid4().hex[:8], "content": c, "status": "pending"})
            store.write_text(json.dumps(todos, indent=2), encoding="utf-8")
            yield {"success": True, "stdout": f"{len(to_add)} item(s) ajouté(s)."}
            return
        if action == "update":
            if not id:
                yield {"success": False, "stdout": "id requis pour update"}
                return
            found = False
            for t in todos:
                if t.get("id") == id:
                    if content:
                        t["content"] = content
                    if status:
                        t["status"] = status
                    found = True
                    break
            if not found:
                yield {"success": False, "stdout": f"Todo id inconnu: {id}"}
                return
            store.write_text(json.dumps(todos, indent=2), encoding="utf-8")
            yield {"success": True, "stdout": f"Todo {id} mis à jour."}
            return
        yield {"success": False, "stdout": f"Action todo inconnue: {action}"}

    def execute_apply_patch(self, patch: str, dry_run: bool = False):
        """Apply a unified diff. Prefer edit/write_file for simple changes."""
        if not patch or not str(patch).strip():
            yield {"success": False, "stdout": "patch vide"}
            return
        try:
            import subprocess, tempfile, os
            with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
                f.write(patch)
                patch_path = f.name
            cmd = ["git", "apply", "--check", patch_path] if dry_run else ["git", "apply", patch_path]
            r = subprocess.run(cmd, cwd=str(self.workspace_root), capture_output=True, text=True)
            os.unlink(patch_path)
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0:
                # Fallback: try patch command
                with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as f:
                    f.write(patch)
                    patch_path = f.name
                cmd2 = ["patch", "-p1", "--dry-run" if dry_run else "-p1"]
                r2 = subprocess.run(cmd2, cwd=str(self.workspace_root), input=patch, capture_output=True, text=True)
                os.unlink(patch_path)
                if r2.returncode != 0:
                    yield {"success": False, "stdout": f"apply_patch failed:\n{out}\n{r2.stderr}"}
                    return
                yield {"success": True, "stdout": r2.stdout or "patch applied"}
                return
            yield {"success": True, "stdout": out or ("patch valid" if dry_run else "patch applied")}
        except Exception as e:
            yield {"success": False, "stdout": f"Erreur apply_patch: {e}"}


def create_executor_from_config(config, bridge=None):
    """Create the tool executor bound to the configured workspace."""
    workspace = config.get("security", {}).get("workspace", ".")
    if workspace in ("./workspace", "workspace"):
        workspace = "."
    try:
        return _create_new_executor(workspace)
    except Exception:
        return _create_legacy_executor(config, bridge=bridge)


def _create_new_executor(workspace: str):
    """ToolBridge (subclass of ActionExecutor) — preferred entry point."""
    try:
        from src.core.tool_bridge import ToolBridge
        return ToolBridge(workspace=workspace)
    except Exception:
        return ActionExecutor(workspace=workspace)


def _create_legacy_executor(config, bridge=None):
    workspace = config.get("security", {}).get("workspace", "./workspace")
    return ActionExecutor(workspace=workspace, bridge=bridge)
