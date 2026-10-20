"""Banniere de demarrage + en-tete de session."""

from __future__ import annotations

from pathlib import Path

from rich.console import Group
from rich.text import Text

from .theme import Catppuccin


# ASCII compact : lisible a 60 colonnes, discret a l'ecran.
BANNER = r"""
 _  _ ___ __  __ ___ ___ ___ ___
| \| | __|  \/  | __/ __|_ _/ __|
| .` | _|| |\/| | _|\__ \| |\__ \
|_|\_|___|_|  |_|___|___/___|___/"""


def _shorten_path(path: str, limit: int = 44) -> str:
    if not path:
        return ""
    p = str(path)
    if len(p) <= limit:
        return p
    parts = Path(p).parts
    tail = parts[-1] if parts else p
    return ".../" + tail


def _kv(label: str, value: str, width: int = 12) -> Text:
    row = Text()
    row.append(label.ljust(width), style=Catppuccin.OVERLAY0)
    row.append(value, style=Catppuccin.SUBTEXT1)
    return row


def banner(
    provider: str = "",
    target: str = "",
    model: str = "",
    workspace: str = "",
    connected: bool | None = None,
):
    """Bloc de demarrage : logo, identite, cible et etat."""
    rows = []

    rows.append(Text(BANNER, style="bold " + Catppuccin.MAUVE))

    tagline = Text()
    tagline.append("agent ia autonome", style=Catppuccin.OVERLAY0)
    tagline.append("   \u00b7   ", style=Catppuccin.SURFACE1)
    tagline.append("cli \u00b7 web \u00b7 telegram", style=Catppuccin.OVERLAY0)
    rows.append(tagline)
    rows.append(Text(""))

    if model:
        rows.append(_kv("modele", model))
    if provider:
        rows.append(_kv("provider", provider))
    if target:
        rows.append(_kv("endpoint", target))
    if workspace:
        rows.append(_kv("espace", _shorten_path(workspace, 56)))

    if connected is True:
        state = Text("connecte", style=Catppuccin.GREEN)
    elif connected is False:
        state = Text("hors ligne", style=Catppuccin.RED)
    else:
        state = Text("non teste", style=Catppuccin.YELLOW)
    row = Text()
    row.append("connexion".ljust(12), style=Catppuccin.OVERLAY0)
    row.append_text(state)
    rows.append(row)

    rows.append(Text(""))
    hints = Text()
    hints.append("/help", style="bold " + Catppuccin.PEACH)
    hints.append("  commandes", style=Catppuccin.OVERLAY0)
    hints.append("   \u00b7   ", style=Catppuccin.SURFACE1)
    hints.append("/status", style="bold " + Catppuccin.PEACH)
    hints.append("  etat", style=Catppuccin.OVERLAY0)
    hints.append("   \u00b7   ", style=Catppuccin.SURFACE1)
    hints.append("Ctrl+C", style="bold " + Catppuccin.PEACH)
    hints.append("  interrompre", style=Catppuccin.OVERLAY0)
    rows.append(hints)

    return Group(*rows)


def get_header(
    version: str | None = None,
    provider: str = "",
    target: str = "",
    model: str = "",
    workspace: str = "",
    connected: bool | None = None,
):
    """Une seule ligne : marque, modele, dossier. Pas de cadre."""
    line = Text()
    line.append("nemesis", style="bold " + Catppuccin.LAVENDER)

    if model:
        line.append("  " + model, style=Catppuccin.SUBTEXT0)
    if provider:
        line.append("  \u00b7  " + provider, style=Catppuccin.OVERLAY0)

    if connected is True:
        line.append("  \u00b7  ", style=Catppuccin.SURFACE1)
        line.append("connecte", style=Catppuccin.GREEN)
    elif connected is False:
        line.append("  \u00b7  ", style=Catppuccin.SURFACE1)
        line.append("hors ligne", style=Catppuccin.RED)

    if workspace:
        line.append("  \u00b7  ", style=Catppuccin.SURFACE1)
        line.append(_shorten_path(workspace), style=Catppuccin.OVERLAY0)

    return line