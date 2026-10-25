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
from rich import box

console = Console()


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
    if _active_app:
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
        _active_app._show_commands()


@registry.register("clear", "Efface l'ecran du terminal")
def clear_command(args=None):
    console.clear()
    from src.ui.header import get_header
    if _active_app:
        client = _active_app.client
        console.print(get_header(
            provider=_active_app.config.get("provider", {}).get("type", "nemapi").upper(),
            target=getattr(client, "base_url", None)
            or f"{getattr(client, 'host', '')}:{getattr(client, 'port', '')}",
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
        ("Interface : ", "dim"), ("outils batch, Todo, sous-agents éphémères", "white"),
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
    table.add_row("Session", getattr(_active_app, "session_id", None) or "—")
    table.add_row("Autorisation", _active_app.config.get("security", {}).get("approval_mode", "confirm"))
    table.add_row("Messages", str(MESSAGE_COUNT))
    try:
        from src.core.selfcheck import _package_version, code_origin

        origin_kind, origin_dir = code_origin()
        table.add_row("Version", _package_version())
        table.add_row("Code", f"{origin_kind} — {origin_dir}")
    except Exception:  # noqa: BLE001 - informational only
        pass
    fleet = getattr(getattr(_active_app, "executor", None), "subagent_fleet", None)
    if fleet is not None and hasattr(fleet, "availability"):
        state = fleet.availability()
        delegation = f"{'oui' if state.get('available') else 'non'} ({state.get('mode', '—')})"
        table.add_row("Delegation", delegation)
    console.print(table)


@registry.register("todo", "Affiche le plan de travail courant", "/todo")
def todo_command(args=None):
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return
    workspace = _active_app.config.get("security", {}).get("workspace")
    try:
        from src.core.tools.todo import _load
        _active_app.composer.display_todo(_load(workspace))
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
        ("delegate_tasks", "Lancement de tâches indépendantes en parallèle"),
        ("subagent_status", "Suivi et résultats des tâches temporaires"),
    ]
    active_registry = getattr(getattr(_active_app, "executor", None), "registry", None)
    if active_registry is not None:
        tools_list = [
            (name, getattr(registered.definition, "description", ""))
            for name, registered in active_registry
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


@registry.register("sessions", "Liste, crée, ouvre ou supprime une conversation", "/sessions [list|new|open <id>|delete <id>]")
@registry.register("session", "Alias de /sessions")
def sessions_command(args=None):
    if _active_app:
        _active_app._manage_sessions(args or [])


@registry.register("memory", "Gère la mémoire partagée avec le Web et Telegram", "/memory [list|add|delete <id>|on|off|clear]")
def memory_command(args=None):
    from web.backend import settings as store

    args = args or []
    operation = args[0].lower() if args else "list"
    if operation == "list":
        state = store.memory_state()
        console.print(f"Mémoire : {'activée' if state['enabled'] else 'désactivée'}")
        table = Table(title="Mémoire partagée", box=box.ROUNDED)
        table.add_column("ID", style="dim")
        table.add_column("Titre", style="bold")
        table.add_column("Contenu")
        for block in state["blocks"]:
            table.add_row(str(block.get("id", "")), block.get("title", ""), block.get("content", ""))
        console.print(table)
    elif operation == "add":
        title = console.input("Titre : ").strip()
        content = console.input("Contenu : ").strip()
        result = store.memory_upsert(title=title, content=content)
        console.print(f"[success]{result.get('message') or 'Bloc enregistré.'}[/success]" if result["ok"] else f"[error]{result['message']}[/error]")
    elif operation == "delete" and len(args) > 1:
        result = store.memory_delete(args[1])
        console.print("[success]Bloc supprimé.[/success]" if result["ok"] else f"[error]{result['message']}[/error]")
    elif operation in {"on", "off"}:
        result = store.memory_set_enabled(operation == "on")
        console.print("[success]Préférence mémoire mise à jour.[/success]" if result["ok"] else f"[error]{result['message']}[/error]")
    elif operation == "clear":
        if console.input("Effacer tous les blocs mémoire ? [y/N] ").strip().lower() == "y":
            result = store.memory_clear()
            console.print("[success]Mémoire effacée.[/success]" if result["ok"] else f"[error]{result['message']}[/error]")
    else:
        console.print("[dim]Usage : /memory [list|add|delete <id>|on|off|clear][/dim]")


@registry.register("approval", "Configure les confirmations des outils (confirm/all)", "/approval [confirm|all]")
def approval_command(args=None):
    from web.backend.config_store import load_config, update_config

    config = load_config()
    current = config.get("security", {}).get("approval_mode", "confirm")
    if not args:
        console.print(f"Mode d'autorisation : [bold]{current}[/bold] (confirm ou all)")
        return
    mode = args[0].lower()
    if mode not in {"confirm", "all"}:
        console.print("[error]Choisissez confirm ou all.[/error]")
        return
    update_config({"security": {"approval_mode": mode}})
    if _active_app:
        _active_app.config.setdefault("security", {})["approval_mode"] = mode
    console.print(f"[success]Mode d'autorisation enregistré : {mode}[/success]")


@registry.register("subagents", "Gère les profils et limites des sous-agents", "/subagents [list|limits|profile add|profile delete <id>]")
def subagents_command(args=None):
    import getpass
    import sys
    from web.backend import config_store

    args = args or []
    operation = args[0].lower() if args else "list"
    if operation == "limits":
        current = config_store.public_subagents_config()
        fields = (
            ("max_concurrent", "Sous-agents simultanés"),
            ("max_iterations", "Itérations par tâche (0 = illimité)"),
            ("timeout_seconds", "Délai maximal (secondes)"),
            ("max_retries", "Tentatives"),
            ("max_tokens_per_task", "Budget tokens par tâche"),
        )
        values = {}
        for key, label in fields:
            raw = console.input(f"{label} [{current[key]}] : ").strip()
            try:
                values[key] = int(raw) if raw else int(current[key])
            except ValueError:
                console.print(f"[error]Valeur invalide pour {label}.[/error]")
                return
        try:
            config_store.save_subagents_config(values)
        except ValueError as exc:
            console.print(f"[error]{exc}[/error]")
            return
        console.print("[success]Limites des sous-agents enregistrées.[/success]")
        return

    if operation == "profile" and len(args) > 1 and args[1].lower() == "delete":
        if len(args) < 3:
            console.print("[error]Usage : /subagents profile delete <id>[/error]")
            return
        try:
            config_store.delete_subagent_profile(args[2])
        except (KeyError, ValueError) as exc:
            console.print(f"[error]{exc}[/error]")
            return
        console.print("[success]Profil supprimé.[/success]")
        return

    if operation == "profile" and len(args) > 1 and args[1].lower() == "add":
        if not sys.stdin.isatty():
            console.print("[error]La création d'un profil exige un terminal interactif pour protéger sa clé.[/error]")
            return
        provider = console.input("Provider [groq/openai/custom] : ").strip().lower() or "groq"
        defaults = {
            "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
            "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
            "custom": ("", ""),
        }
        base_url_default, model_default = defaults.get(provider, ("", ""))
        base_url = console.input(f"URL API [{base_url_default}] : ").strip() or base_url_default
        model = console.input(f"Modèle [{model_default}] : ").strip() or model_default
        name = console.input(f"Nom [{provider.title()} · {model}] : ").strip() or f"{provider.title()} · {model}"
        api_key = getpass.getpass("Clé API (saisie masquée) : ").strip()
        try:
            config_store.save_subagent_profile({
                "provider": provider,
                "base_url": base_url,
                "model": model,
                "name": name,
                "api_key": api_key,
            })
        except ValueError as exc:
            console.print(f"[error]{exc}[/error]")
            return
        console.print("[success]Profil de sous-agent enregistré.[/success]")
        return

    if operation not in {"list", "profile"}:
        console.print("[dim]Usage : /subagents [list|limits|profile add|profile delete <id>][/dim]")
        return
    settings = config_store.public_subagents_config()
    table = Table(title="Profils des sous-agents", box=box.ROUNDED)
    table.add_column("ID", style="dim")
    table.add_column("Nom", style="bold")
    table.add_column("Provider")
    table.add_column("Modèle")
    table.add_column("Clé")
    for profile in settings["profiles"]:
        table.add_row(
            profile["id"],
            profile["name"],
            profile["provider"],
            profile["model"],
            "environnement" if profile["api_key_from_env"] else "configurée" if profile["api_key_set"] else "absente",
        )
    console.print(table)
    iteration_label = (
        "illimitées" if not settings["max_iterations"] else f"{settings['max_iterations']} itérations"
    )
    console.print(
        f"Limites : {settings['max_concurrent']} en parallèle · "
        f"{iteration_label} · {settings['timeout_seconds']} s"
    )
    if not settings["profiles"]:
        console.print(
            "[dim]Aucune clé dédiée : les sous-agents réutilisent le provider principal "
            "quand celui-ci est configuré.[/dim]"
        )
    console.print("[dim]/subagents limits · /subagents profile add · /subagents profile delete <id>[/dim]")


@registry.register("files", "Explore les fichiers du système", "/files [chemin]")
def files_command(args=None):
    from pathlib import Path

    from src.core.paths import resolve_path

    if not _active_app:
        return
    raw = " ".join(args or []).strip()
    target = Path(resolve_path(raw)) if raw else Path(resolve_path("."))
    if not target.is_dir():
        console.print(f"[error]Dossier introuvable : {target}[/error]")
        return
    table = Table(title=f"Fichiers · {target}", box=box.ROUNDED)
    table.add_column("Type", width=8)
    table.add_column("Nom")
    try:
        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as exc:
        console.print(f"[error]Impossible de lire le dossier : {exc}[/error]")
        return
    for entry in entries[:300]:
        table.add_row("dossier" if entry.is_dir() else "fichier", entry.name + ("/" if entry.is_dir() else ""))
    console.print(table)


@registry.register("integrations", "Consulte et configure GitHub ou Telegram sans afficher les secrets", "/integrations [github|telegram]")
def integrations_command(args=None):
    import getpass
    import sys
    from web.backend import github_settings, telegram_settings

    service = (args[0].lower() if args else "").strip()
    if service in {"github", "telegram"} and not sys.stdin.isatty():
        console.print("[error]La configuration des intégrations exige un terminal interactif pour protéger les secrets.[/error]")
        return
    if service == "github":
        settings = github_settings.public_github_config()
        console.print(f"GitHub : {'connecté' if settings['connected'] else 'déconnecté'} · {settings.get('token_preview') or 'aucun jeton'}")
        console.print(f"Identité : {settings.get('name') or '—'} <{settings.get('email') or '—'}>")
        if console.input("Mettre à jour les identifiants GitHub ? [y/N] ").strip().lower() != "y":
            return
        token = getpass.getpass("Jeton GitHub (vide pour conserver, '-' pour déconnecter) : ")
        name = console.input(f"Nom Git [{settings.get('name') or '—'}] : ").strip()
        email = console.input(f"E-mail Git [{settings.get('email') or '—'}] : ").strip()
        login = console.input(f"Identifiant GitHub [{settings.get('login') or '—'}] : ").strip()
        patch = {}
        if name:
            patch["name"] = "" if name == "-" else name
        if email:
            patch["email"] = "" if email == "-" else email
        if login:
            patch["login"] = "" if login == "-" else login
        if token == "-":
            patch["token"] = ""
        elif token:
            patch["token"] = token
        github_settings.save_github_config(patch)
        console.print("[success]Réglages GitHub enregistrés.[/success]")
    elif service == "telegram":
        settings = telegram_settings.public_telegram_config()
        console.print(f"Telegram : {'configuré' if settings['token_set'] else 'non configuré'} · {settings.get('token_preview') or 'aucun jeton'}")
        if console.input("Mettre à jour le bot Telegram ? [y/N] ").strip().lower() != "y":
            return
        token = getpass.getpass("Jeton Telegram (vide pour conserver, '-' pour déconnecter) : ")
        bot_name = console.input(f"Nom du bot [{settings.get('bot_name') or '—'}] : ").strip()
        workspace = console.input(f"Espace de travail [{settings.get('workspace') or '—'}] : ").strip()
        nemapi = settings.get("nemapi") or {}
        host = console.input(f"Serveur NEMAPI [{nemapi.get('host') or '—'}] : ").strip()
        port = console.input(f"Port NEMAPI [{nemapi.get('port') or '—'}] : ").strip()
        model = console.input(f"Modèle NEMAPI [{nemapi.get('model') or '—'}] : ").strip()
        patch = {}
        if bot_name:
            patch["bot_name"] = "" if bot_name == "-" else bot_name
        if workspace:
            patch["workspace"] = "" if workspace == "-" else workspace
        nemapi_patch = {}
        if host:
            nemapi_patch["host"] = "" if host == "-" else host
        if port:
            try:
                nemapi_patch["port"] = int(port)
            except ValueError:
                console.print("[error]Le port NEMAPI doit être un entier.[/error]")
                return
        if model:
            nemapi_patch["model"] = "" if model == "-" else model
        if nemapi_patch:
            patch["nemapi"] = nemapi_patch
        if token == "-":
            patch["token"] = ""
        elif token:
            patch["token"] = token
        telegram_settings.save_telegram_config(patch)
        console.print("[success]Réglages Telegram enregistrés.[/success]")
    else:
        console.print("[dim]/integrations github · /integrations telegram[/dim]")


from src.core.mcp_manager import MCPManager
def _make_mcp_mgr() -> MCPManager:
    try:
        from src.core.paths import mcp_config_path
        return MCPManager(str(mcp_config_path()))
    except Exception:
        return MCPManager()


mcp_mgr = _make_mcp_mgr()


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


@registry.register("delegate", "Crée un sous-agent temporaire pour une instruction")
def delegate_command(args=None):
    """Crée une tâche éphémère avec la flotte configurée."""
    instruction = " ".join(args or []).strip()
    fleet = getattr(getattr(_active_app, "executor", None), "subagent_fleet", None)
    if not instruction or fleet is None:
        console.print("[yellow]Usage: /delegate <instruction>[/yellow]")
        console.print("[dim]Configurez le provider des sous-agents dans config.yaml ou Réglages Web.[/dim]")
        return
    result = fleet.delegate([{"instruction": instruction}])
    if result.get("success"):
        console.print(f"[success]Tâche lancée : {result['tasks'][0]['task_id']}[/success]")
    else:
        console.print(f"[error]{result.get('error', 'Échec de la délégation.')}[/error]")


@registry.register("agent_status", "Statut des tâches de sous-agents temporaires")
def agent_status_command(args=None):
    """Affiche l'état des tâches de sous-agents retenues en mémoire."""
    task_id = (args[0] if args else "") or ""
    fleet = getattr(getattr(_active_app, "executor", None), "subagent_fleet", None)
    if fleet is None:
        console.print("[yellow]Flotte de sous-agents indisponible.[/yellow]")
        return
    console.print(json.dumps(fleet.status(task_id), ensure_ascii=False, indent=2))


@registry.register("agents", "Explique le fonctionnement des sous-agents temporaires")
def agents_command(args=None):
    console.print(
        "[system]Les sous-agents sont créés à la demande et disparaissent à la fin de leur tâche. "
        "Configurez leur provider dans Réglages Web ou config.yaml ; utilisez delegate_tasks "
        "et subagent_status pour les coordonner.[/system]"
    )


@registry.register("nemesis.md", "Genere un fichier NEMESIS.md pour le suivi du projet")
def nemesis_md_command(args=None):
    console.print("[yellow]Instruction de generation du NEMESIS.md envoyee a l'IA...[/yellow]")
    return "PROMPT_INTERNAL: Genere un fichier NEMESIS.md a la racine pour documenter ce projet (objectifs, structure, technologies, etat d'avancement). Utilise l'action 'write'."


@registry.register("skills", "Gere les competences (skills) de l'agent")
def skills_command(args=None):
    while True:
        installed = skill_mgr.list_installed()
        console.print("\n[bold magenta]Gestion des Skills (Claude-Compatible)[/bold magenta]")
        console.print(f"1. Liste des skills installes ({len(installed)})")
        console.print("2. Installer via URL (GitHub/ZIP)")
        console.print("3. Importer un dossier local")
        console.print("4. Voir le contenu d'un skill (SKILL.md)")
        console.print("5. Desinstaller un skill")
        console.print("6. Activer/desactiver pour les nouvelles conversations")
        console.print("q. Retour")

        choice = console.input("\nChoix > ").strip().lower()
        if choice == "1":
            if not installed:
                console.print("[yellow]Aucun skill additionnel installe.[/yellow]")
            else:
                table = Table(title="Skills Installes", border_style="magenta")
                table.add_column("Nom", style="bold magenta")
                table.add_column("Version", style="dim")
                table.add_column("État", style="cyan")
                table.add_column("Description", style="white")
                from web.backend.settings import list_skills
                enabled_skills = {
                    skill["name"] for skill in list_skills() if skill.get("enabled")
                }
                for s in installed:
                    table.add_row(
                        str(s.get("name", "?")),
                        str(s.get("version", "?")),
                        "Activé" if s.get("name") in enabled_skills else "Désactivé",
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
        elif choice == "6":
            from web.backend.settings import list_skills, set_skill_enabled

            name = console.input("Nom du skill : ").strip()
            current = next(
                (skill for skill in list_skills() if skill["name"] == name),
                None,
            )
            if current is None:
                console.print(f"[error]Skill '{name}' introuvable.[/error]")
                continue
            try:
                set_skill_enabled(name, not current["enabled"])
            except (KeyError, ValueError) as exc:
                console.print(f"[error]{exc}[/error]")
                continue
            state = "activé" if not current["enabled"] else "désactivé"
            console.print(
                f"[success]Skill '{name}' {state} pour les nouvelles conversations.[/success]"
            )
        elif choice == "q":
            break
        else:
            console.print("[error]Option invalide.[/error]")


# =====================================================================
# NOUVELLES COMMANDES : config, models, history
# =====================================================================

@registry.register("config", "Configure l'URL /v1 et le jeton facultatif NEMAPI", "/config")
def config_command(args=None):
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return

    console.print("\n[bold magenta]Configuration du serveur NEMAPI[/bold magenta]\n")

    import copy
    import getpass
    from urllib.parse import urlparse

    config = _active_app.config
    current = config.get("nemapi", config.get("nemapi_v3", {}))
    current_url = str(current.get("url") or "")
    if not current_url and current.get("host"):
        scheme = "https" if current.get("https") else "http"
        port = int(current.get("port") or 8090)
        default_port = 443 if scheme == "https" else 80
        suffix = f":{port}" if port != default_port else ""
        current_url = f"{scheme}://{current['host']}{suffix}/v1"

    url = console.input(f"URL NEMAPI complète (ex. https://serveur/v1) [{current_url or 'http://127.0.0.1:8090/v1'}] : ").strip()
    url = url or current_url or "http://127.0.0.1:8090/v1"
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not parsed.path.rstrip("/").endswith("/v1")
        ):
            raise ValueError
        parsed.port
    except ValueError:
        console.print("[error]URL invalide : utilisez une URL HTTP(S) se terminant par /v1, sans identifiants, paramètres ni fragment.[/error]")
        return

    token = getpass.getpass("Jeton NEMAPI (facultatif, vide si non requis) : ").strip()
    new_config = copy.deepcopy(config)
    new_config["provider"] = {
        "type": "nemapi",
        "model": config.get("provider", {}).get("model", ""),
    }
    new_config["nemapi"] = {"url": url.rstrip("/")}
    if token:
        new_config["nemapi"]["token"] = token
    else:
        new_config["nemapi"].pop("token", None)
    new_config["nemapi"].pop("host", None)
    new_config["nemapi"].pop("port", None)
    provider_type = "nemapi"

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


@registry.register("models", "Liste les modeles NEMAPI et permet d'en selectionner un", "/models")
@registry.register("model", "Alias de /models")
def models_command(args=None):
    if not _active_app or not _active_app.client:
        console.print("[error]Aucun provider actif.[/error]")
        return

    provider_type = _active_app.config.get("provider", {}).get("type", "bridge")

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



@registry.register("a2a", "Alias historique du statut des sous-agents temporaires")
def a2a_command(args=None):
    """Affiche le statut des taches ephemeres."""
    agent_status_command(args)


@registry.register(
    "continue",
    "Poursuit la discussion existante : le prompt systeme ne sera pas renvoye",
    "/continue",
)
def continue_command(args=None):
    """Bascule la prochaine requete en mode poursuite de discussion."""
    if not _active_app:
        console.print("[error]Application non initialisee.[/error]")
        return

    app = _active_app
    app._send_system_prompt = False
    app._prompt_sent = True
    app._session_initialized = True

    client = getattr(app, "client", None)
    session_id = getattr(app, "session_id", None)
    if session_id and client and hasattr(client, "configure_session"):
        try:
            client.configure_session(session_id, initialized=True)
        except Exception:
            pass

    console.print(
        "[success]Poursuite de la discussion[/success] "
        "[dim]- le prompt systeme ne sera pas renvoye au prochain message.[/dim]"
    )
