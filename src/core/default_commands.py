from typing import Dict, List, Callable, Optional, Any
import sys
import os
import yaml
import time
import json
from pathlib import Path
from datetime import datetime
from src.core.commands import registry
from src.core.skills_manager import SkillManager
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.markdown import Markdown
try:
    from src.ui.theme import NEMESIS_THEME
except Exception:  # pragma: no cover
    NEMESIS_THEME = None

console = Console(theme=NEMESIS_THEME) if NEMESIS_THEME else Console()


def _make_skill_mgr() -> SkillManager:
    try:
        from src.core.paths import tools_library_path
        return SkillManager(str(tools_library_path()))
    except Exception:
        return SkillManager()


skill_mgr = _make_skill_mgr()

SESSION_START = datetime.now()
MESSAGE_COUNT = 0

_active_app = None

def set_active_app(app):
    global _active_app
    _active_app = app

def increment_message_count():
    global MESSAGE_COUNT
    MESSAGE_COUNT += 1


PROVIDER_LABELS = {
    "nemapi": "NEMAPI",
}


@registry.register("help", "Affiche la liste des commandes disponibles")
def help_command(args=None):
    if not _active_app:
        return  # nothing to introspect

    if args:
        command = registry.get_command(str(args[0]).lower())
        if command:
            usage = command.usage or f"/{command.name}"
            console.print(Panel(
                f"[bold cyan]{usage}[/bold cyan]\n\n{command.description}",
                title="Aide",
                border_style="bright_cyan",
            ))
            return
        console.print(f"[error]Commande inconnue : /{args[0]}[/error]")
        return

    # Shared rendering: use the module console so web capture (which swaps
    # `default_commands.console`) keeps working, with a graceful fallback.
    renderer = getattr(_active_app, "_show_commands", None)
    if callable(renderer):
        try:
            renderer()
        except Exception:
            pass

    table = Table(title="Commandes disponibles", show_header=True, header_style="bold #89b4fa")
    table.add_column("Commande", style="bold #fab387", no_wrap=True)
    table.add_column("Usage", style="#a6adc8", no_wrap=True)
    table.add_column("Description", style="#cdd6f4")
    for cmd in sorted(registry.list_commands(), key=lambda c: c.name):
        table.add_row(f"/{cmd.name}", cmd.usage or "—", cmd.description or "")
    console.print(table)


@registry.register("clear", "Efface l'ecran du terminal")
def clear_command(args=None):
    console.clear()
    from src.ui.header import get_header
    if _active_app:
        client = _active_app.client
        console.print(get_header(
            provider=_active_app.config.get("provider", {}).get("type", "nemapi").upper(),
            target=f"{getattr(client, 'host', '')}:{getattr(client, 'port', '')}",
            model=getattr(client, "model", ""),
            workspace=_active_app.config.get("security", {}).get("workspace", ""),
            connected=_active_app._conn_ok,
        ))
    else:
        console.print(get_header())


@registry.register("show", "Affiche une sortie de commande cachée (ID ou 'last' ou 'all')")
def show_command(args=None):
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return
    
    if not _active_app._hidden_outputs:
        console.print("[dim]Aucune sortie cachee disponible.[/dim]")
        return
    
    # Si pas d'argument, afficher l'aide
    if not args:
        console.print("[yellow]Usage:[/yellow]")
        console.print("  /show last    - Affiche la derniere sortie cachee")
        console.print("  /show all     - Affiche toutes les sorties cachees")
        console.print("  /show <ID>   - Affiche la sortie avec l'ID specifique")
        console.print("  /show list    - Liste toutes les sorties cachees disponibles")
        return
    
    arg = args[0].lower() if args else ""
    
    if arg == "list":
        from rich.table import Table
        from rich.panel import Panel
        from rich import box
        
        if not _active_app._hidden_outputs:
            console.print("[yellow] Aucune sortie cachee disponible.[/yellow]")
            return
        
        table = Table(
            title="[bold bright_cyan]Sorties Cachees Disponibles[/bold bright_cyan]",
            border_style="bright_cyan",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold bright_cyan"
        )
        table.add_column("[bold cyan]ID[/bold cyan]", style="bold cyan", width=8)
        table.add_column("[bold yellow]Statut[/bold yellow]", style="bold yellow", width=10)
        table.add_column("[white]Commande[/white]", style="white", width=50)
        
        for output_id, data in sorted(_active_app._hidden_outputs.items()):
            status = "[green] Succes[/green]" if data["success"] else "[red] Echec[/red]"
            cmd_preview = data["command"][:47] + "..." if len(data["command"]) > 50 else data["command"]
            table.add_row(str(output_id), status, cmd_preview)
        
        console.print(Panel(table, border_style="bright_cyan", padding=(1, 2)))
        console.print("[dim]Tapez /show <ID> pour afficher une sortie specifique[/dim]")
    elif arg == "last":
        if _active_app._hidden_outputs:
            last_id = max(_active_app._hidden_outputs.keys())
            _show_single_output(last_id)
        else:
            console.print("[yellow] Aucune sortie cachee.[/yellow]")
    elif arg == "all":
        if not _active_app._hidden_outputs:
            console.print("[yellow] Aucune sortie cachee.[/yellow]")
            return
        console.print(Panel(
            "[bold bright_cyan]Toutes les Sorties Cachees[/bold bright_cyan]",
            border_style="bright_cyan"
        ))
        for output_id in sorted(_active_app._hidden_outputs.keys()):
            _show_single_output(output_id)
    else:
        # Essayer de parser comme un ID
        try:
            output_id = int(arg)
            if output_id in _active_app._hidden_outputs:
                _show_single_output(output_id)
            else:
                console.print(f"[error]ID {output_id} introuvable.[/error]")
        except ValueError:
            console.print(f"[error]Argument invalide: {arg}[/error]")


def _show_single_output(output_id):
    from rich.panel import Panel
    
    if not _active_app or output_id not in _active_app._hidden_outputs:
        console.print(f"[error]Sortie {output_id} introuvable.[/error]")
        return
    
    data = _active_app._hidden_outputs[output_id]
    color = "green" if data["success"] else "red"
    icon = "" if data["success"] else ""
    status_text = "[green]Succes[/green]" if data["success"] else "[red]Echec[/red]"
    
    # En-tête élégant
    header = (
        f"[bold {color}]{icon} ID {output_id}[/bold {color}]\n"
        f"[dim]Commande:[/dim] [white]{data['command']}[/white]\n"
        f"[dim]Statut:[/dim] {status_text}"
    )
    
    console.print()
    
    # Afficher la sortie
    output = data["output"]
    if output:
        from rich.panel import Panel
        from rich.text import Text
        from src.ui.theme import Catppuccin
        
        # Limiter la taille de l'affichage
        max_lines = 500  # Augmente de 50
        lines = output.split('\n')
        if len(lines) > max_lines:
            output_display = '\n'.join(lines[:max_lines]) + f"\n\n[dim]... ({len(lines) - max_lines} lignes supplémentaires)[/dim]"
        else:
            output_display = output
        
        console.print(
            Panel(
                Text(output_display, style=Catppuccin.TEXT),
                border_style=color,
                padding=(0, 1),
                title=f"[bold {color}]Sortie de commande[/bold {color}]",
                width=max(40, min(console.width - 4, 120)),
            )
        )
    else:
        console.print("[dim](Aucune sortie)[/dim]\n")


@registry.register("exit", "Quitte l'application")
def exit_command(args=None):
    _print_session_time()
    console.print("[yellow]Fermeture de Nemesis CLI...[/yellow]")
    sys.exit(0)


@registry.register("quit", "Quitte l'application")
def quit_command(args=None):
    _print_session_time()
    console.print("[yellow]Fermeture de Nemesis CLI...[/yellow]")
    sys.exit(0)


def _print_session_time():
    if _active_app and _active_app.session_start > 0:
        elapsed = time.time() - _active_app.session_start
        mins, secs = divmod(int(elapsed), 60)
        console.print(f"[system]Duree de session : {mins:02d}:{secs:02d}[/system]")


@registry.register("stats", "Affiche les statistiques de la session actuelle", "/stats")
def stats_command(args=None):
    uptime = datetime.now() - SESSION_START
    conv_count = _active_app.client.conversation_count() if _active_app and _active_app.client else 0
    table = Table(title="Statistiques de Session", border_style="bright_blue")
    table.add_column("Metrique", style="cyan")
    table.add_column("Valeur", style="white")
    table.add_row("Debut de session", SESSION_START.strftime("%H:%M:%S"))
    table.add_row("Uptime", str(uptime).split('.')[0])
    table.add_row("Messages envoyes", str(MESSAGE_COUNT))
    table.add_row("Messages en contexte", str(conv_count))
    console.print(table)


@registry.register("about", "Informations sur NEMESIS-CLI")
def about_command(args=None):
    text = Text.assemble(
        ("NEMESIS CLI\n", "bold bright_cyan"),
        ("L'agent autonome de codage et d'administration Linux.\n\n", "italic white"),
        ("Provider : ", "dim"), ("NEMAPI\n", "bold white"),
        ("Modèles : ", "dim"), ("récupérés via /v1/models\n", "white"),
        ("Interface : ", "dim"), ("outils batch, Todo, subagents A2A", "white"),
    )
    console.print(Panel(text, border_style="bright_blue", expand=False))


@registry.register("status", "Affiche l'etat de la session, du modele et du workspace", "/status")
def status_command(args=None):
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return
    client = _active_app.client
    table = Table(title="Etat de la session", border_style="bright_blue")
    table.add_column("Parametre", style="cyan")
    table.add_column("Valeur", style="white")
    table.add_row("Connexion", "connectee" if _active_app._conn_ok is True else "non testee" if _active_app._conn_ok is None else "hors ligne")
    table.add_row("Provider", _active_app.config.get("provider", {}).get("type", "nemapi").upper())
    table.add_row("Modele", str(getattr(client, "model", "non selectionne")))
    table.add_row("Endpoint", f"{getattr(client, 'host', '—')}:{getattr(client, 'port', '—')}")
    table.add_row("Workspace", _active_app.config.get("security", {}).get("workspace", "—"))
    table.add_row("Messages", str(MESSAGE_COUNT))
    console.print(table)


@registry.register("todo", "Affiche le plan de travail courant", "/todo")
def todo_command(args=None):
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return
    workspace = _active_app.config.get("security", {}).get("workspace")
    try:
        from src.core.tools.todo import _load
        _active_app.composer.display_todo(_load(workspace) if workspace else [])
    except (OSError, TypeError) as exc:
        console.print(f"[error]Impossible de charger le plan : {exc}[/error]")


@registry.register("tools", "Liste les outils systeme disponibles pour l'agent")
def tools_command(args=None):
    from rich.panel import Panel
    from rich import box
    
    tools_list = [
        ("read_file", "Lecture de fichiers (avec numeros de ligne)"),
        ("write_file", "Creation ou ecrasement de fichiers"),
        ("edit", "Remplacement exact de texte dans un fichier"),
        ("apply_patch", "Application d'un diff unifie multi-fichiers"),
        ("delete_file", "Suppression d'un fichier"),
        ("list_dir", "Exploration de dossiers"),
        ("glob", "Recherche de fichiers par motif (**/*.py)"),
        ("grep", "Recherche regex dans les fichiers"),
        ("bash", "Execution de commandes shell"),
        ("get_task_output", "Sortie d'une tache asynchrone"),
        ("kill_task", "Arret d'une tache asynchrone"),
        ("git", "Etat git (status, diff, log, branch)"),
        ("todo", "Liste de taches structuree (list/add/update/clear)"),
        ("web_search", "Recherche sur Internet"),
        ("web_fetch", "Recuperation du contenu d'une URL"),
        ("mcp_list", "Liste des serveurs MCP configures"),
        ("mcp_tools_list", "Decouverte des outils d'un serveur MCP"),
        ("mcp_call", "Appel d'un outil MCP"),
        ("list_agents", "Liste des agents subordonnes A2A"),
        ("delegate_task", "Delegation A2A a un agent subordonne"),
        ("check_reports", "Rapports des taches A2A terminees"),
    ]
    
    # Créer un tableau plus élégant
    table = Table(
        title="[bold bright_magenta]Outils Systeme Disponibles[/bold bright_magenta]",
        border_style="bright_magenta",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold bright_magenta"
    )
    table.add_column("[bold magenta]Outil[/bold magenta]", style="bold magenta", width=20)
    table.add_column("[white]Description[/white]", style="white", width=55)
    
    # Trier par nom
    tools_list.sort(key=lambda x: x[0])
    
    for name, desc in tools_list:
        tool_name = f"[bold cyan]{name}[/bold cyan]"
        table.add_row(tool_name, desc)
    
    console.print(Panel(table, border_style="bright_magenta", padding=(1, 2)))


from src.core.mcp_manager import MCPManager
from src.core.agent_manager import AgentClient, A2AAgentClient, get_scheduler, A2ATaskScheduler
from src.core.a2a_protocol import (
    A2AEnvelope, A2AMessageType, TaskPriority,
    AgentCapability, TaskManifest,
)

def _agents_file() -> Path:
    try:
        from src.core.paths import agents_path
        return agents_path()
    except Exception:
        return Path("agents.json")


AGENTS_FILE = _agents_file()
ACTIVE_AGENTS: Dict[str, AgentClient] = {}


def _make_mcp_mgr() -> MCPManager:
    try:
        from src.core.paths import mcp_config_path
        return MCPManager(str(mcp_config_path()))
    except Exception:
        return MCPManager()


mcp_mgr = _make_mcp_mgr()
a2a_scheduler = get_scheduler()


@registry.register("mcp", "Gere les serveurs MCP")
def mcp_command(args=None):
    console.print("\n[bold magenta]Gestion des serveurs MCP[/bold magenta]")
    console.print("1. Liste des serveurs")
    console.print("2. Ajouter un serveur")
    console.print("3. Supprimer un serveur")
    console.print("4. Tester un serveur (initialize + tools/list)")
    console.print("q. Retour")

    choice = console.input("\nChoix > ").strip().lower()
    if choice == "1":
        servers = mcp_mgr.list_servers()
        if not servers:
            console.print("[yellow]Aucun serveur MCP configure.[/yellow]")
        else:
            for name, cfg in servers.items():
                desc = cfg.get("description") or ""
                console.print(f"- [bold]{name}[/bold] : {cfg.get('command', '?')}")
                if desc:
                    console.print(f"  [dim]{desc}[/dim]")
    elif choice == "2":
        name = console.input("Nom : ").strip()
        cmd = console.input("Commande : ").strip()
        desc = console.input("Description (optionnel) : ").strip()
        if name and cmd:
            success, msg = mcp_mgr.add_server(name, cmd, description=desc)
            console.print(f"[success]{msg}[/success]" if success else f"[error]{msg}[/error]")
    elif choice == "3":
        name = console.input("Nom a supprimer : ").strip()
        success, msg = mcp_mgr.remove_server(name)
        console.print(f"[success]{msg}[/success]" if success else f"[error]{msg}[/error]")
    elif choice == "4":
        name = console.input("Nom du serveur a tester : ").strip()
        if name:
            console.print(f"[dim]Test de '{name}'...[/dim]")
            ok, msg = mcp_mgr.test_server(name)
            console.print(f"[success]{msg}[/success]" if ok else f"[error]{msg}[/error]")


def load_agents():
    agents_file = _agents_file()
    if agents_file.exists():
        try:
            with open(agents_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for name, info in data.items():
                    if isinstance(info, str):
                        info = {"api_key": info}
                    agent = AgentClient.from_config(name, info)
                    ACTIVE_AGENTS[name] = agent
                    a2a_scheduler.register_agent_client(agent)
        except Exception as e:
            console.print(f"[error]Erreur chargement agents : {e}[/error]")
    # Propager l'exécuteur à tous les agents après le chargement
    if a2a_scheduler._executor:
        for agent in ACTIVE_AGENTS.values():
            agent.set_executor(a2a_scheduler._executor)

def save_agents():
    data = {name: ag.to_config() for name, ag in ACTIVE_AGENTS.items()}
    agents_file = _agents_file()
    agents_file.parent.mkdir(parents=True, exist_ok=True)
    with open(agents_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def reload_agents_with_executor(executor):
    """Propager l'exécuteur à tous les agents déjà chargés."""
    for agent in ACTIVE_AGENTS.values():
        agent.set_executor(executor)
    # Mettre à jour le scheduler également
    a2a_scheduler.set_executor(executor)

load_agents()


@registry.register("delegate", "Delegue une tache a un coequipier A2A en arriere-plan (usage: /delegate <nom_agent> <instruction>)")
def delegate_command(args=None):
    """Délègue une tâche à un coéquipier A2A — non bloquant."""
    if not args or len(args) < 2:
        console.print("[yellow]Usage: /delegate <nom_agent> <instruction>[/yellow]")
        console.print("[dim]Exemple: /delegate mon_agent 'Écris un fichier test.py avec print(\"Hello\")'[/dim]")
        console.print("[dim]La délégation est asynchrone: NEMESIS n'attend pas la fin. Utilisez /agent_status ou check_reports.[/dim]")
        return

    agent_name = args[0]
    instruction = " ".join(args[1:])

    if agent_name not in ACTIVE_AGENTS:
        console.print(f"[error]Agent '{agent_name}' introuvable. Utilisez /agents pour en créer un.[/error]")
        return

    agent = ACTIVE_AGENTS[agent_name]

    if not agent._executor:
        console.print(f"[yellow]L'agent '{agent_name}' n'a pas d'exécuteur. Tentative de réparation...[/yellow]")
        if a2a_scheduler._executor:
            agent.set_executor(a2a_scheduler._executor)
            console.print("[success]Exécuteur propagé.[/success]")
        else:
            console.print("[error]Aucun exécuteur disponible. Redémarrez NEMESIS.[/error]")
            return

    if agent_name not in a2a_scheduler.agents:
        a2a_scheduler.register_agent_client(agent)

    if getattr(agent, "status", "idle") == "busy":
        console.print(
            f"[yellow]Agent '{agent_name}' déjà occupé "
            f"(task={getattr(agent, '_current_task_id', '?')}).[/yellow]"
        )
        return

    console.print(f"[system]Délégation asynchrone → '{agent_name}'...[/system]")
    console.print(f"[dim]Instruction: {instruction}[/dim]")

    try:
        task_id = a2a_scheduler.delegate(
            agent_name=agent_name,
            label=instruction[:60],
            description=instruction,
            instructions=[instruction],
            blocking=False,
        )
        if not task_id:
            console.print("[error]Délégation refusée (agent occupé ou injoignable).[/error]")
            return
        reports_dir = getattr(a2a_scheduler, "_reports_dir", None) or "workspace/a2a_reports"
        console.print("[success]Délégation acceptée — travail en arrière-plan.[/success]")
        console.print(f"  task_id : [bold]{task_id}[/bold]")
        console.print(f"  rapport : {reports_dir}/{task_id}.md (écrit à la fin)")
        console.print("[dim]NEMESIS n'est pas bloqué. /agent_status ou outil agent_status / check_reports.[/dim]")
    except Exception as e:
        console.print(f"[error]Erreur lors de la délégation: {e}[/error]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")


@registry.register("agent_status", "Statut des coequipiers A2A et des taches async (usage: /agent_status [task_id])")
def agent_status_command(args=None):
    """Affiche le statut des agents et des jobs A2A."""
    task_id = (args[0] if args else "") or ""
    try:
        from src.core.agent_tools import _agent_status
        out = _agent_status(task_id=task_id) if task_id else _agent_status()
        console.print(out.get("stdout", ""))
    except Exception as e:
        console.print(f"[error]{e}[/error]")


@registry.register("agents", "Gere les agents subordonnes (A2A) pour deleguer des taches")
def agents_command(args=None):
    while True:
        console.print("\n[bold magenta]Gestion des Agents Subordonnes (A2A · NemAPI v3)[/bold magenta]")
        console.print(f"Agents : {', '.join(ACTIVE_AGENTS.keys()) if ACTIVE_AGENTS else 'Aucun'}")
        console.print("\n1. Ajouter un agent")
        console.print("2. Chat interactif avec un agent (mode A2A)")
        console.print("3. Supprimer un agent")
        console.print("4. Sonder les capacites A2A d'un agent")
        console.print("q. Retour")

        choice = console.input("\nChoix > ").strip().lower()
        if choice == "1":
            from src.core.agent_manager import NEMAPI_V3_DEFAULT_MODEL, NEMAPI_V3_DEFAULT_HOST, NEMAPI_V3_DEFAULT_PORT
            name = console.input("Nom de l'agent : ").strip()
            if not name:
                console.print("[error]Nom requis.[/error]")
                continue
            # A2A: NemAPI v3 only
            provider = "nemapi"
            console.print("[dim]Provider: NEMAPI uniquement — les modèles sont récupérés depuis /v1/models[/dim]")
            model = console.input(f"Modele (optionnel) [{NEMAPI_V3_DEFAULT_MODEL}] : ").strip() or NEMAPI_V3_DEFAULT_MODEL
            host = console.input(f"Host [{NEMAPI_V3_DEFAULT_HOST}] : ").strip() or NEMAPI_V3_DEFAULT_HOST
            port_s = console.input(f"Port [{NEMAPI_V3_DEFAULT_PORT}] : ").strip() or str(NEMAPI_V3_DEFAULT_PORT)
            try:
                port = int(port_s)
            except ValueError:
                port = NEMAPI_V3_DEFAULT_PORT
            agent = AgentClient(
                name=name,
                api_key="nemapi",
                provider=provider,
                model=model,
                host=host,
                port=port,
            )
            ACTIVE_AGENTS[name] = agent
            a2a_scheduler.register_agent_client(agent)
            # Optional capability probe
            try:
                manifest = agent.query_capabilities()
                if manifest:
                    console.print(f"[dim]Capabilities: {[c.value for c in manifest.capabilities]}[/dim]")
            except Exception as e:
                console.print(f"[dim]Capability probe skipped: {e}[/dim]")
            save_agents()
            console.print(
                f"[success]Agent '{name}' ajoute "
                f"(provider={provider}, model={agent.model}) et enregistre A2A.[/success]"
            )
        elif choice == "2":
            name = console.input("Nom de l'agent : ").strip()
            if name not in ACTIVE_AGENTS:
                console.print("[error]Agent inconnu.[/error]")
                continue
            agent = ACTIVE_AGENTS[name]
            console.print(f"[system]Mode A2A avec {name} (tapez 'q' pour quitter).[/system]")
            console.print("[dim]Les messages sont envoyes sous forme d'enveloppes A2A.[/dim]")
            while True:
                user_msg = console.input(f"\n[user] {name} > [/user]")
                if user_msg == "q":
                    break

                # Detecter les commandes speciales A2A
                if user_msg.startswith("/capability"):
                    env = A2AEnvelope.create("nemesis", name, A2AMessageType.CAPABILITY_QUERY, {})
                    console.print(f"[dim]→ Envoi A2A: {env.type.value}[/dim]")
                    response = agent.send_message(env.to_json())
                    # Try to parse A2A response
                    parsed = agent._parse_a2a_response(response)
                    if parsed:
                        console.print(f"\n[ai] {name} (A2A {parsed.type.value}) :[/ai]")
                        from rich.json import JSON
                        console.print(JSON(json.dumps(parsed.payload, ensure_ascii=False, default=str)))
                    else:
                        console.print(f"\n[ai] {name} :[/ai]\n{response}")
                elif user_msg.startswith("/heartbeat"):
                    env = A2AEnvelope.create("nemesis", name, A2AMessageType.HEARTBEAT, {})
                    console.print(f"[dim]→ Envoi A2A: heartbeat[/dim]")
                    agent.send_message(env.to_json())
                    # Peut aussi envoyer en texte direct
                    response = agent.send_message("HEARTBEAT")
                    console.print(f"\n[ai] {name} :[/ai]\n{response}")
                else:
                    # Chat enveloppe A2A standard
                    env = A2AEnvelope.create("nemesis", name, A2AMessageType.TASK_ASSIGN, {
                        "task_id": f"chat-{int(time.time())}",
                        "label": "Chat message",
                        "description": user_msg,
                        "instructions": [user_msg],
                    })
                    console.print(f"[dim]→ Envoi A2A: task_assign[/dim]")
                    response = agent.send_message(env.to_json())

                    # Parse and display A2A response
                    parsed = agent._parse_a2a_response(response)
                    if parsed:
                        console.print(f"\n[ai] {name} (A2A {parsed.type.value}) :[/ai]")
                        from rich.json import JSON
                        console.print(JSON(json.dumps(parsed.payload, ensure_ascii=False, default=str)))
                        # If it's a task_report with artifacts, show them
                        if parsed.type == A2AMessageType.TASK_REPORT:
                            artifacts = parsed.payload.get("artifacts", [])
                            if artifacts:
                                console.print("\n[bold cyan]Artifacts produits:[/bold cyan]")
                                for a in artifacts:
                                    console.print(f"   {a['path']} — {a.get('description', '')}")
                    else:
                        console.print(f"\n[ai] {name} :[/ai]\n{response}")
        elif choice == "3":
            name = console.input("Nom a supprimer : ").strip()
            if name in ACTIVE_AGENTS:
                del ACTIVE_AGENTS[name]
                a2a_scheduler.remove_agent(name)
                save_agents()
                console.print("[success]Supprime.[/success]")
        elif choice == "4":
            name = console.input("Nom de l'agent : ").strip()
            if name not in ACTIVE_AGENTS:
                console.print("[error]Agent inconnu.[/error]")
                continue
            agent = ACTIVE_AGENTS[name]
            console.print(f"[system]Sondage des capacites A2A de {name}...[/system]")
            manifest = agent.query_capabilities()
            if manifest:
                console.print(f"\n[bold green]Manifest A2A — {manifest.name}[/bold green]")
                console.print(f"  Version: {manifest.version}")
                console.print(f"  Description: {manifest.description}")
                console.print(f"  Modele: {manifest.model}")
                console.print(f"  Capacites: {', '.join(c.value for c in manifest.capabilities)}")
                console.print(f"  Domaines: {', '.join(manifest.domains)}")
                console.print(f"  Taches concurrentes max: {manifest.max_concurrent_tasks}")
            else:
                console.print("[yellow]L'agent n'a pas repondu au protocole A2A. Verifiez son prompt.[/yellow]")
        elif choice == "q":
            break


@registry.register("nemesis.md", "Genere un fichier NEMESIS.md pour le suivi du projet")
def nemesis_md_command(args=None):
    console.print("[yellow]Instruction de generation du NEMESIS.md envoyee a l'IA...[/yellow]")
    return "PROMPT_INTERNAL: Genere un fichier NEMESIS.md a la racine pour documenter ce projet (objectifs, structure, technologies, etat d'avancement). Utilise l'action 'write'."


@registry.register("skills", "Gere les competences (skills) de l'agent", "/skills [list|show <nom>|install <url|dossier>|remove <nom>]")
def skills_command(args=None):
    args = list(args or [])

    def _render_list():
        installed = skill_mgr.list_installed()
        if not installed:
            console.print("[yellow]Aucun skill additionnel installe.[/yellow]")
            return
        table = Table(title=f"Skills installes ({len(installed)})", border_style="magenta")
        table.add_column("Nom", style="bold magenta")
        table.add_column("Version", style="dim")
        table.add_column("Description", style="white")
        for s in installed:
            table.add_row(str(s.get("name", "?")), str(s.get("version", "?")), str(s.get("description", ""))[:80])
        console.print(table)

    # Non-interactive path used by the web UI.
    if args:
        sub = str(args[0]).lower()
        if sub == "list":
            _render_list(); return
        if sub == "show" and len(args) > 1:
            meta = skill_mgr.get_skill(args[1])
            if not meta:
                console.print(f"[error]Skill '{args[1]}' introuvable.[/error]"); return
            console.print(f"[bold]{meta.get('name')}[/bold] v{meta.get('version')}")
            console.print(meta.get("description", ""))
            content = meta.get("content")
            if content:
                console.print("\n[dim]--- SKILL.md ---[/dim]\n" + content[:4000])
            else:
                console.print("[yellow]Pas de SKILL.md[/yellow]")
            return
        if sub == "install" and len(args) > 1:
            target = args[1]
            source = Path(target).expanduser()
            if source.exists() and source.is_dir():
                ok, msg = skill_mgr.install_from_local(str(source))
            elif target.startswith(("http://", "https://")) or target.endswith((".git", ".zip")):
                ok, msg = skill_mgr.install_from_url(target)
            else:
                console.print("[error]Chemin ou URL invalide pour /skills install.[/error]"); return
            console.print(f"[success]{msg}[/success]" if ok else f"[error]{msg}[/error]")
            return
        if sub == "remove" and len(args) > 1:
            ok, msg = skill_mgr.uninstall(args[1])
            console.print(f"[success]{msg}[/success]" if ok else f"[error]{msg}[/error]")
            return
        console.print("[error]Usage: /skills list | show <nom> | install <url|dossier> | remove <nom>[/error]")
        return

    while True:
        installed = skill_mgr.list_installed()
        console.print("\n[bold magenta]Gestion des Skills (Claude-Compatible)[/bold magenta]")
        console.print(f"1. Liste des skills installes ({len(installed)})")
        console.print("2. Installer via URL (GitHub/ZIP)")
        console.print("3. Importer un dossier local")
        console.print("4. Voir le contenu d'un skill (SKILL.md)")
        console.print("5. Desinstaller un skill")
        console.print("q. Retour")

        choice = console.input("\nChoix > ").strip().lower()
        if choice == "1":
            if not installed:
                console.print("[yellow]Aucun skill additionnel installe.[/yellow]")
            else:
                table = Table(title="Skills Installes", border_style="magenta")
                table.add_column("Nom", style="bold magenta")
                table.add_column("Version", style="dim")
                table.add_column("Description", style="white")
                for s in installed:
                    table.add_row(
                        str(s.get("name", "?")),
                        str(s.get("version", "?")),
                        str(s.get("description", ""))[:80],
                    )
                console.print(table)
        elif choice == "2":
            url = console.input("URL du skill (.git ou .zip) : ").strip()
            if url:
                success, msg = skill_mgr.install_from_url(url)
                console.print(f"[success]{msg}[/success]" if success else f"[error]{msg}[/error]")
        elif choice == "3":
            path = console.input("Chemin du dossier local : ").strip()
            if path:
                success, msg = skill_mgr.install_from_local(path)
                console.print(f"[success]{msg}[/success]" if success else f"[error]{msg}[/error]")
        elif choice == "4":
            name = console.input("Nom du skill : ").strip()
            if name:
                meta = skill_mgr.get_skill(name)
                if not meta:
                    console.print(f"[error]Skill '{name}' introuvable.[/error]")
                else:
                    console.print(f"[bold]{meta.get('name')}[/bold] v{meta.get('version')}")
                    console.print(meta.get("description", ""))
                    content = meta.get("content")
                    if content:
                        console.print("\n[dim]--- SKILL.md ---[/dim]")
                        console.print(content[:4000])
                    else:
                        console.print("[yellow]Pas de SKILL.md[/yellow]")
        elif choice == "5":
            name = console.input("Nom du skill a supprimer : ").strip()
            if name:
                success, msg = skill_mgr.uninstall(name)
                console.print(f"[success]{msg}[/success]" if success else f"[error]{msg}[/error]")
        elif choice == "q":
            break
        else:
            console.print("[error]Option invalide.[/error]")


# =====================================================================
# NOUVELLES COMMANDES : config, models, history
# =====================================================================

@registry.register("config", "Configure l'URL et le port du serveur NEMAPI", "/config [url] [port] [modele]")
def config_command(args=None):
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return

    args = list(args or [])
    # Non-interactive path (web UI / scripting): pass url, port and optionally the model.
    if args and str(args[0]).lower() in ("--set", "-s"):
        args = args[1:]
    if args:
        from src.core.paths import DEFAULT_WORKSPACE
        current_cfg = _active_app.config
        new_config = {"security": {"workspace": current_cfg.get("security", {}).get("workspace", str(DEFAULT_WORKSPACE))}}
        raw_url = str(args[0]).strip()
        port_str = str(args[1]).strip() if len(args) > 1 else ""
        model = str(args[2]).strip() if len(args) > 2 else current_cfg.get("provider", {}).get("model", "")
        if "://" in raw_url:
            from urllib.parse import urlparse
            parsed = urlparse(raw_url)
            host = parsed.hostname or ""
            if not port_str and parsed.port:
                port_str = str(parsed.port)
        else:
            host = raw_url.rstrip("/")
        if not host:
            console.print("[error]URL NEMAPI invalide.[/error]")
            return
        try:
            port = int(port_str) if port_str else _active_app.config.get("nemapi", {}).get("port", 8090)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            console.print("[error]Port invalide (1-65535).[/error]")
            return
        new_config["provider"] = {"type": "nemapi", "model": model}
        new_config["nemapi"] = {"host": host, "port": port}
        console.print(f"[system]Configuration NemApi : {host}:{port} · modele='{model}'[/system]")
        success, msg = _active_app.reconfigure_provider(new_config)
        if success:
            console.print(f"[success]Provider configure : {msg}[/success]")
        else:
            console.print(f"[error]Echec: {msg}[/error]")
        return

    console.print("\n[bold magenta]Configuration du serveur NEMAPI[/bold magenta]\n")

    provider_type = "nemapi"

    try:
        from src.core.paths import DEFAULT_WORKSPACE
        _ws_default = str(DEFAULT_WORKSPACE)
    except Exception:
        _ws_default = str(Path.home() / "nemesis-workspace")
    new_config = {"security": {"workspace": _active_app.config.get("security", {}).get("workspace", _ws_default)}}

    current = _active_app.config.get("nemapi", _active_app.config.get("nemapi_v3", {}))
    current_url = current.get("url") or current.get("host", "127.0.0.1")
    url = console.input(f"URL NEMAPI [{current_url}] : ").strip()
    port_str = console.input(f"Port [{current.get('port', 8090)}] : ").strip()
    url = url or current_url
    if "://" in url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not port_str and parsed.port:
            port_str = str(parsed.port)
    else:
        host = url.rstrip("/")
    if not host:
        console.print("[error]URL NEMAPI invalide.[/error]")
        return
    try:
        port = int(port_str) if port_str else current.get("port", 8090)
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        console.print("[error]Port invalide (1-65535).[/error]")
        return

    new_config["provider"] = {
        "type": "nemapi",
        "model": _active_app.config.get("provider", {}).get("model", ""),
    }
    new_config["nemapi"] = {"host": host, "port": port}

    console.print("\n[system]Test de connexion a NEMAPI...[/system]")
    from providers import create_provider
    try:
        test_client = create_provider(new_config)
        if not test_client.test_connection():
            console.print("[error]Connexion echouee. Verifiez vos parametres.[/error]")
            return
        console.print("[success]Connexion OK.[/success]")
    except Exception as e:
        console.print(f"[error]Erreur: {e}[/error]")
        return

    if provider_type == "nemapi":
        console.print("\n[system]Recuperation de la liste des modeles...[/system]")
        models = test_client.list_models()

        if not models:
            console.print("[yellow]Liste officielle indisponible. Saisie de l'identifiant du modele.[/yellow]")
            model = console.input("ID du modele NEMAPI : ").strip()
            if not model:
                console.print("[error]Modele requis.[/error]")
                return
        else:
            models = [m for m in models if m.get("id")]
            page_size = 15
            total_pages = max(1, (len(models) + page_size - 1) // page_size)
            current_page = 0
            selected_idx = None

            while selected_idx is None:
                start = current_page * page_size
                end = min(start + page_size, len(models))
                page_models = models[start:end]

                title = f"Modeles disponibles ({provider_type}) — page {current_page + 1}/{total_pages}"
                table = Table(title=title, border_style="bright_blue")
                table.add_column("#", style="dim", width=4)
                table.add_column("Modele", style="bold white")
                table.add_column("Proprietaire", style="grey70")

                for i, m in enumerate(page_models):
                    table.add_row(str(i + 1), m["id"], m.get("owned_by", ""))
                console.print(table)

                prompt_text = "Numero du modele"
                if total_pages > 1:
                    prompt_text += ", n/N pour naviguer"
                prompt_text += ", f <mot> pour filtrer, q pour annuler"
                choice = console.input(f"\n{prompt_text} : ").strip()

                if choice.lower() == "q":
                    return
                elif choice.lower() == "n" and current_page < total_pages - 1:
                    current_page += 1
                elif choice.lower() == "p" and current_page > 0:
                    current_page -= 1
                elif choice.lower().startswith("f "):
                    query = choice[2:].strip().lower()
                    filtered = [m for m in models if query in m["id"].lower()]
                    if filtered:
                        models = filtered
                        current_page = 0
                        total_pages = max(1, (len(models) + page_size - 1) // page_size)
                        console.print(f"[success]{len(models)} modeles trouves.[/success]")
                    else:
                        console.print("[yellow]Aucun modele correspondant.[/yellow]")
                else:
                    try:
                        idx = int(choice) - 1
                        if 0 <= idx < len(page_models):
                            selected_idx = idx
                        else:
                            console.print("[error]Numero invalide.[/error]")
                    except ValueError:
                        console.print("[error]Entree invalide.[/error]")

            model = page_models[selected_idx]["id"]

        new_config["provider"]["model"] = model
        console.print(f"[success]Modele selectionne: {model}[/success]")

    console.print("[system]Application de la configuration...[/system]")
    success, msg = _active_app.reconfigure_provider(new_config)
    if success:
        console.print(f"[success]Provider change : {PROVIDER_LABELS.get(provider_type, provider_type)}[/success]")
    else:
        console.print(f"[error]Echec: {msg}[/error]")


@registry.register("models", "Liste les modeles NEMAPI et permet d'en selectionner un", "/models [filtre] | /model <id>")
@registry.register("model", "Alias de /models")
def models_command(args=None):
    if not _active_app or not _active_app.client:
        console.print("[error]Aucun provider actif.[/error]")
        return

    provider_type = _active_app.config.get("provider", {}).get("type", "bridge")
    args = list(args or [])

    # Non-interactive path (web UI): `/models` lists, `/models <id>` selects.
    if args and str(args[0]).lower() not in ("--list", "-l"):
        selected = str(args[0]).strip()
        try:
            available = {m["id"] for m in (_active_app.client.list_models() or []) if isinstance(m, dict) and m.get("id")}
        except Exception:
            available = set()
        if available and selected not in available:
            console.print(f"[yellow]Modele '{selected}' absent de la liste serveur ; application quand meme.[/yellow]")
        _active_app.config.setdefault("provider", {})["model"] = selected
        _active_app._save_config()
        _active_app.client.model = selected
        console.print(f"[success]Modele change : {selected}[/success]")
        console.print("[yellow]Note: la conversation actuelle est conservee.[/yellow]")
        return

    console.print(f"\n[system]Recuperation des modeles depuis {PROVIDER_LABELS.get(provider_type, provider_type)}...[/system]")
    try:
        models = _active_app.client.list_models()
    except Exception as exc:
        console.print(f"[yellow]Impossible de recuperer la liste officielle : {exc}[/yellow]")
        models = []

    if not models:
        console.print("[yellow]NEMAPI n'a pas retourne de modeles.[/yellow]")
        manual = console.input(f"ID du modele [{_active_app.client.model or 'gemini-chat'}] : ").strip()
        selected = manual or _active_app.client.model
        if not selected:
            console.print("[error]Identifiant de modele requis.[/error]")
            return
        _active_app.config.setdefault("provider", {})["model"] = selected
        _active_app._save_config()
        _active_app.client.model = selected
        console.print(f"[success]Modele configure : {selected}[/success]")
        return

    models = [m for m in models if m.get("id")]
    current_model = _active_app.client.model
    console.print(f"[dim]Modele actuel: [success]{current_model}[/success][/dim]\n")

    page_size = 15
    total_pages = max(1, (len(models) + page_size - 1) // page_size)
    current_page = 0
    selected = None

    while selected is None:
        start = current_page * page_size
        end = min(start + page_size, len(models))
        page_models = models[start:end]

        table = Table(title=f"Modeles ({provider_type}) — page {current_page + 1}/{total_pages}", border_style="bright_blue")
        table.add_column("#", style="dim", width=4)
        table.add_column("Modele", style="bold white")
        table.add_column("Actuel", style="green", width=6)
        table.add_column("Proprietaire", style="grey70")

        for i, m in enumerate(page_models):
            is_current = ">>" if m["id"] == current_model else ""
            table.add_row(str(i + 1), m["id"], is_current, m.get("owned_by", ""))
        console.print(table)

        prompt_text = "Numero du modele"
        if total_pages > 1:
            prompt_text += ", n/N pour naviguer"
        prompt_text += ", f <mot> pour filtrer, q pour annuler"
        choice = console.input(f"\n{prompt_text} : ").strip()

        if choice.lower() == "q":
            return
        elif choice.lower() == "n" and current_page < total_pages - 1:
            current_page += 1
        elif choice.lower() == "p" and current_page > 0:
            current_page -= 1
        elif choice.lower().startswith("f "):
            query = choice[2:].strip().lower()
            filtered = [m for m in models if query in m["id"].lower()]
            if filtered:
                models = filtered
                current_page = 0
                total_pages = max(1, (len(models) + page_size - 1) // page_size)
            else:
                console.print("[yellow]Aucun modele correspondant.[/yellow]")
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(page_models):
                    selected = page_models[idx]["id"]
                else:
                    console.print("[error]Numero invalide.[/error]")
            except ValueError:
                console.print("[error]Entree invalide.[/error]")

    _active_app.config["provider"]["model"] = selected
    _active_app._save_config()
    _active_app.client.model = selected
    console.print(f"[success]Modele change : {selected}[/success]")
    console.print("[yellow]Note: la conversation actuelle est conservee.[/yellow]")


@registry.register("history", "Gere l'historique de conversation (clear/save/load)")
def history_command(args=None):
    if not _active_app or not _active_app.client:
        console.print("[error]Aucun provider actif.[/error]")
        return

    client = _active_app.client
    count = client.conversation_count()

    sub = (args[0].lower() if args and len(args) > 0 else "") if args else ""

    if sub == "clear":
        client.reset_conversation()
        console.print(f"[success]Conversation effacee ({count} messages supprimes).[/success]")
        return

    if sub == "save":
        filename = args[1] if args and len(args) > 1 else f"conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        ok = client.save_conversation(filename)
        if ok:
            console.print(f"[success]Conversation sauvegardee ({count} messages) → {filename}[/success]")
        else:
            console.print(f"[error]Erreur d'ecriture: {filename}[/error]")
        return

    if sub == "load":
        if not args or len(args) < 2:
            console.print("[error]Usage: /history load <fichier.json>[/error]")
            return
        filename = args[1]
        if not Path(filename).exists():
            console.print(f"[error]Fichier introuvable: {filename}[/error]")
            return
        ok = client.load_conversation(filename)
        if ok:
            new_count = client.conversation_count()
            console.print(f"[success]Conversation chargee ({new_count} messages) depuis {filename}[/success]")
        else:
            console.print(f"[error]Erreur de lecture: {filename}[/error]")
        return

    conv = client.get_conversation()
    console.print(f"\n[bold]Historique de conversation[/bold] — [dim]{count} messages[/dim]")
    console.print(f"[dim]Commandes : /history clear | save [fichier] | load <fichier>[/dim]\n")

    if not conv:
        console.print("[yellow]Conversation vide.[/yellow]")
        return

    MAX_SHOW = 20
    start_idx = max(0, len(conv) - MAX_SHOW)
    if start_idx > 0:
        console.print(f"[dim]... {start_idx} messages masques. Affichage des {MAX_SHOW} derniers.[/dim]\n")

    for i, msg in enumerate(conv[start_idx:], start_idx + 1):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:200]
        content = content.replace("\n", " ")
        if role == "system":
            style = "bold yellow"
            prefix = "SYS"
        elif role == "user":
            style = "bright_cyan"
            prefix = "YOU"
        elif role == "assistant":
            style = "bright_blue"
            prefix = "AI "
        else:
            style = "dim"
            prefix = "???"

        console.print(f"  [{style}]#{i:03d} {prefix} | {content}[/{style}]")


@registry.register("a2a", "A2A Scheduler — gere les taches deleguees aux sub-agents")
def a2a_command(args=None):
    """Gestion du planificateur A2A : etat des agents et des taches."""
    while True:
        agent_list = a2a_scheduler.list_agents()
        reports = a2a_scheduler.collect_reports()

        console.print("\n[bold magenta]Planificateur A2A[/bold magenta]")

        table = Table(title=f"Agents enregistres ({len(agent_list)})", border_style="bright_cyan")
        table.add_column("Nom", style="bold cyan")
        table.add_column("Statut", style="yellow")
        table.add_column("Modele", style="dim")
        table.add_column("Tâche courante", style="white")
        table.add_column("Capacites", style="green", width=30)

        if not agent_list:
            table.add_row("—", "—", "—", "—", "Aucun agent")
        else:
            for a in agent_list:
                caps = ", ".join(a["capabilities"][:4])
                if len(a["capabilities"]) > 4:
                    caps += "..."
                table.add_row(
                    a["name"],
                    "" if a["status"] == "idle" else "",
                    a["model"],
                    a["current_task"] or "—",
                    caps or "—",
                )
        console.print(table)

        if reports:
            console.print(f"\n[bold]Rapports de taches ({len(reports)})[/bold]")
            for r in reports:
                status_icon = "" if r.status.value == "completed" else ""
                console.print(f"  {status_icon} {r.task_id}: {r.summary[:80]}")

        console.print("\n1. Lister les agents disponibles")
        console.print("2. Afficher les rapports de taches")
        console.print("3. Reinitialiser le planificateur")
        console.print("4. Sonder les capacites de tous les agents")
        console.print("q. Retour")

        choice = console.input("\nChoix > ").strip().lower()
        if choice == "1":
            console.print(f"\n[bold]Agents ({len(agent_list)}) :[/bold]")
            for a in agent_list:
                console.print(f"  - {a['name']} ({a['model']}) — {a['status']}")
                if a["domains"]:
                    console.print(f"    Domaines: {', '.join(a['domains'])}")
                if a["capabilities"]:
                    console.print(f"    Capacites: {', '.join(a['capabilities'])}")
        elif choice == "2":
            reports = a2a_scheduler.collect_reports()
            if not reports:
                console.print("[yellow]Aucun rapport disponible.[/yellow]")
            else:
                for r in reports:
                    console.print(f"\n[bold cyan]{r.task_id}[/bold cyan] — {r.status.value}")
                    console.print(f"  Resume: {r.summary}")
                    if r.artifacts:
                        for a in r.artifacts:
                            console.print(f"   {a.path} ({a.description})")
                    if r.errors:
                        for e in r.errors:
                            console.print(f"   {e.get('code', '?')}: {e.get('message', '')}")
        elif choice == "3":
            from src.core.a2a_protocol import A2ACoordinator
            a2a_scheduler.coordinator = A2ACoordinator()
            console.print("[success]Planificateur reinitialise.[/success]")
        elif choice == "4":
            console.print("[system]Sondage de tous les agents...[/system]")
            for name in list(ACTIVE_AGENTS.keys()):
                agent = ACTIVE_AGENTS.get(name)
                if agent:
                    console.print(f"  → {name}...")
                    manifest = agent.query_capabilities()
                    if manifest:
                        console.print(f"     Capacites: {', '.join(c.value for c in manifest.capabilities)}")
                    else:
                        console.print(f"     Pas de reponse A2A")
        elif choice == "q":
            break
