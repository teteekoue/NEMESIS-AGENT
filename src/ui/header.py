"""Banniere de demarrage + en-tete de session."""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .render import truncate_middle
from .theme import Catppuccin


BANNER = r"""
 _  _ ___ __  __ ___ ___ ___ ___
| \| | __|  \/  | __/ __|_ _/ __|
| .` | _|| |\/| | _|\__ \| |\__ \
|_|\_|___|_|  |_|___|___/___|___/"""


def _shorten_path(path: str, limit: int = 44) -> str:
    if not path:
        return ""
    value = str(path)
    if len(value) <= limit:
        return value
    parts = Path(value).parts
    tail = parts[-1] if parts else value
    return ".../" + tail


def _kv(label: str, value: str, width: int = 10) -> Text:
    row = Text()
    row.append(label.upper().ljust(width), style=Catppuccin.OVERLAY0)
    row.append(value, style=Catppuccin.SUBTEXT1)
    return row


def banner(
    provider: str = "",
    target: str = "",
    model: str = "",
    workspace: str = "",
    connected: bool | None = None,
    version: str | None = None,
):
    """Banniere de demarrage avec identite, configuration et raccourcis."""
    content = []
    content.append(Text(BANNER, style="bold " + Catppuccin.MAUVE))

    tagline = Text()
    tagline.append("AGENT IA AUTONOME", style="bold " + Catppuccin.TEXT)
    tagline.append("   ·   ", style=Catppuccin.SURFACE1)
    tagline.append("CLI  /  WEB  /  TELEGRAM", style=Catppuccin.OVERLAY0)
    if version:
        tagline.append("   ·   ", style=Catppuccin.SURFACE1)
        tagline.append(f"v{version}", style=Catppuccin.BLUE)
    content.extend((tagline, Text("")))

    details = Table.grid(padding=(0, 3))
    details.add_column(no_wrap=True)
    details.add_column(no_wrap=True)

    if model or provider:
        details.add_row(
            _kv("Modèle", truncate_middle(model, 28)) if model else Text(""),
            _kv("Fournisseur", truncate_middle(provider, 20), 12) if provider else Text(""),
        )
    if target:
        details.add_row(_kv("Endpoint", truncate_middle(target, 48)), Text(""))
    if workspace:
        details.add_row(_kv("Espace", _shorten_path(workspace, 48)), Text(""))

    status_label, status_style = {
        True: ("connecté", Catppuccin.GREEN),
        False: ("hors ligne", Catppuccin.RED),
        None: ("non testé", Catppuccin.YELLOW),
    }[connected]
    status = Text()
    status.append(" ● ", style=status_style)
    status.append(status_label, style=status_style)
    connection = _kv("Connexion", "")
    connection.append_text(status)
    details.add_row(connection, Text(""))
    content.append(details)

    shortcuts = Text()
    shortcuts.append("/help", style="bold " + Catppuccin.PEACH)
    shortcuts.append("  aide", style=Catppuccin.OVERLAY0)
    shortcuts.append("   ·   ", style=Catppuccin.SURFACE1)
    shortcuts.append("/status", style="bold " + Catppuccin.PEACH)
    shortcuts.append("  état", style=Catppuccin.OVERLAY0)
    shortcuts.append("   ·   ", style=Catppuccin.SURFACE1)
    shortcuts.append("Ctrl+C", style="bold " + Catppuccin.PEACH)
    shortcuts.append("  interrompre", style=Catppuccin.OVERLAY0)
    content.extend((Text(""), shortcuts))

    return Panel(
        Group(*content),
        title=Text(" NEMESIS · SESSION ", style="bold " + Catppuccin.LAVENDER),
        title_align="left",
        border_style=Catppuccin.SURFACE1,
        box=box.ROUNDED,
        padding=(1, 2),
        expand=False,
    )


def get_header(
    version: str | None = None,
    provider: str = "",
    target: str = "",
    model: str = "",
    workspace: str = "",
    connected: bool | None = None,
):
    """Une seule ligne de session, sans cadre."""
    line = Text()
    line.append("NEMESIS", style="bold " + Catppuccin.LAVENDER)
    if version:
        line.append(f" v{version}", style=Catppuccin.OVERLAY0)

    if model:
        line.append("  " + model, style=Catppuccin.SUBTEXT0)
    if provider:
        line.append("  ·  " + provider, style=Catppuccin.OVERLAY0)

    if connected is True:
        line.append("  ·  ", style=Catppuccin.SURFACE1)
        line.append("connecté", style=Catppuccin.GREEN)
    elif connected is False:
        line.append("  ·  ", style=Catppuccin.SURFACE1)
        line.append("hors ligne", style=Catppuccin.RED)

    if workspace:
        line.append("  ·  ", style=Catppuccin.SURFACE1)
        line.append(_shorten_path(workspace), style=Catppuccin.OVERLAY0)

    return line
