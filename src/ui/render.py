"""Primitives de rendu — gutter, cartes d'outils, troncature.

Isole la mise en forme de l'orchestration (chat_ui.py).
"""

from __future__ import annotations

from typing import Iterable, Optional

from rich import box
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .theme import (
    Catppuccin,
    MAX_CONTENT_WIDTH,
    MIN_CONTENT_WIDTH,
    TOOL_OUTPUT_PREVIEW_LINES,
)

GUTTER = "\u2502"


def content_width(console: Console) -> int:
    """Largeur utile, capee pour rester lisible."""
    try:
        w = console.size.width
    except Exception:
        w = 80
    return max(MIN_CONTENT_WIDTH, min(w - 4, MAX_CONTENT_WIDTH))


def guttered(renderable, color: str, width: int) -> Table:
    """Place un renderable derriere une gouttiere verticale."""
    grid = Table.grid(padding=0)
    grid.add_column(width=2, no_wrap=True)
    grid.add_column(width=max(1, width - 2))
    grid.add_row(Text(GUTTER, style=color), renderable)
    return grid


def truncate_middle(text: str, limit: int = 90) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def preview_lines(body: str, limit: int = TOOL_OUTPUT_PREVIEW_LINES) -> tuple[str, int]:
    """Retourne (apercu, nombre_de_lignes_masquees)."""
    lines = body.rstrip("\n").splitlines()
    if len(lines) <= limit:
        return "\n".join(lines), 0
    return "\n".join(lines[:limit]), len(lines) - limit


def tool_card(
    name: str,
    params: str,
    body: str,
    duration: Optional[float],
    success: bool = True,
    accent: str = Catppuccin.PEACH,
    width: int = 100,
    truncate: bool = True,
    status_detail: str = "",
    framed: bool = False,
) -> list:
    """Carte d'outil; Bash peut encadrer sa sortie pour la distinguer du fil."""
    icon = "\u2713" if success else "\u2717"
    status_style = "tool.ok" if success else "tool.fail"

    head = Text()
    head.append(name, style=f"bold {accent}")
    if params:
        head.append("  " + truncate_middle(params, max(20, width - len(name) - 18)), style="tool.params")
    head.append("   " + icon + " ", style=status_style)
    if duration is not None:
        head.append(f"{duration:.1f}s", style="tool.dim")
    if status_detail:
        head.append(f" · {status_detail}", style="tool.dim")

    shown, hidden = preview_lines(body, TOOL_OUTPUT_PREVIEW_LINES) if truncate else (body, 0)
    rows: list = [head]
    if shown.strip():
        if framed:
            rows = [
                Panel(
                    Group(head, Text(shown, style="tool.body")),
                    border_style=accent,
                    box=box.ROUNDED,
                    padding=(0, 1),
                    width=width,
                )
            ]
        else:
            rows.append(guttered(Text(shown, style="tool.body"), "tool.gutter", width))
            if hidden == 0:
                rows.append(Text("\u2570" + "\u2500" * min(width - 2, 60), style="tool.gutter"))
    if hidden:
        more = Text()
        more.append(f"   \u2026 {hidden} ligne(s) masquee(s) \u00b7 /show last", style="tool.dim")
        rows.append(more)
    rows.append(Text(""))
    return rows
