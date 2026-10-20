"""NEMESIS Chat UI — messages en gouttiere, cartes d'outils sobres.

Pas de cadres autour des messages : le contenu domine, la couleur signale.
L'API publique (add_message, tool_start, tool_result, ...) est inchangee.
"""

from __future__ import annotations

import time
from typing import List, Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from .render import (
    content_width,
    guttered,
    preview_lines,
    tool_card,
    truncate_middle,
)
from .theme import Catppuccin

MAX_BASH_LINES = 400


def _markdown(text: str) -> Markdown:
    try:
        return Markdown(text, code_theme="monokai")
    except Exception:
        return Markdown(text)


class Bubble:
    """Un message : label colore + corps derriere une gouttiere."""

    def __init__(self, content: str, sender: str, timestamp: Optional[float] = None):
        self.content = content or ""
        self.sender = sender
        self.ts = timestamp or time.time()
        self.is_user = sender.lower() in ("user", "you", "vous")

    def render(self, console: Console) -> list:
        width = content_width(console)
        color = Catppuccin.GREEN if self.is_user else Catppuccin.BLUE
        name = "Vous" if self.is_user else self.sender

        rows: list = [Text(name, style="bold " + color), Text("")]
        if self.content.strip():
            body = Text(self.content, style=Catppuccin.TEXT) if self.is_user else _markdown(self.content)
            rows.append(guttered(body, color, width))
        rows.append(Text("\u2502", style=color))
        rows.append(Text(""))
        return rows


class ChatUI:
    """Interface de chat — orchestration du rendu."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.messages: List[Bubble] = []
        self._pending_tool: Optional[tuple[str, dict]] = None

    @staticmethod
    def _strip_tool_json(text: str) -> str:
        """Retire les blocs JSON d'appel d'outil qui fuitent dans le texte."""
        import re

        if not text or "{" not in text:
            return text
        # Blocs ```json ... ``` ou ``` ... ``` contenant un appel d'outil
        text = re.sub(r"```(?:json)?\s*\{[\s\S]*?\"(?:tool|action|name)\"[\s\S]*?```", "", text)
        # Objet JSON nu ressemblant a un appel d'outil
        text = re.sub(r'\{\s*"(?:tool|action)"\s*:[\s\S]*?\}(?=\s*$)', "", text, flags=re.MULTILINE)
        return text.strip()

    def add_message(self, content: str, sender: str, timestamp: Optional[float] = None):
        if sender.lower() not in ("user", "you", "vous"):
            content = self._strip_tool_json(content)
            if not content.strip():
                return
        bubble = Bubble(content, sender, timestamp)
        self.messages.append(bubble)
        for row in bubble.render(self.console):
            self.console.print(row)

    def tool_start(self, tool_name: str, params: dict):
        self._pending_tool = (tool_name, params if isinstance(params, dict) else {})

    def _label(self, tool_name: str, params: dict) -> str:
        params = params or {}
        # Une description explicite prime : c'est l'intention, pas le detail.
        desc = params.get("description")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()
        if tool_name in ("grep", "glob"):
            # Pour une recherche, le motif est l'information, pas le dossier.
            pattern = params.get("pattern") or params.get("query") or ""
            where = params.get("path") or params.get("include") or ""
            if pattern and where:
                return f"{pattern}  in  {where}"
            return str(pattern or where or "")
        value = (
            params.get("command")
            or params.get("path")
            or params.get("file_path")
            or params.get("query")
            or params.get("pattern")
            or params.get("url")
            or ""
        )
        if isinstance(value, list):
            value = ", ".join(str(v) for v in value[:3])
        return str(value)

    def _accent(self, tool_name: str) -> str:
        if tool_name in ("read_file", "read", "glob", "grep", "list_dir"):
            return Catppuccin.LAVENDER
        if tool_name in ("write_file", "write", "edit", "apply_patch", "search_replace"):
            return Catppuccin.MAUVE
        return Catppuccin.PEACH

    def tool_result(self, result: dict, tool_name: str = "", duration: float | None = None):
        success = bool(result.success) if hasattr(result, "success") else bool(
            result.get("success", False) if isinstance(result, dict) else False
        )
        _, pending_params = self._pending_tool or (tool_name, {})
        self._pending_tool = None

        # Seuls les outils terminal montrent leur corps : une lecture de fichier
        # ne doit pas deverser le contenu dans le fil de conversation.
        if tool_name in ("bash", "shell", "terminal"):
            if isinstance(result, dict):
                body = result.get("output") or result.get("stdout") or result.get("error") or ""
            else:
                body = getattr(result, "output", "") or getattr(result, "error", "") or ""
        elif not success:
            body = (result.get("error") if isinstance(result, dict) else getattr(result, "error", "")) or ""
        else:
            body = ""

        card = tool_card(
            name=tool_name or "tool",
            params=self._label(tool_name, pending_params),
            body=str(body),
            duration=duration,
            success=success,
            accent=self._accent(tool_name),
            width=content_width(self.console),
        )
        for row in card:
            self.console.print(row)

    def tool_error(self, error: str):
        text = Text()
        text.append("\u2717 ", style="tool.fail")
        text.append(str(error), style=Catppuccin.RED)
        self.console.print(text)

    def auth_prompt(self, tool_name: str, params: dict) -> Text:
        summary = truncate_middle(self._label(tool_name, params), content_width(self.console) - 40)
        return Text.assemble(
            ("  ! ", "bold " + Catppuccin.YELLOW),
            ("autoriser ", Catppuccin.SUBTEXT0),
            (f"{tool_name} ", Catppuccin.TEXT),
            (summary, Catppuccin.SUBTEXT0),
            ("   [y] une fois  [a] toujours  [n] refuser", Catppuccin.OVERLAY0),
        )

    def task_summary(self, elapsed: float, tool_count: int = 0, files_changed: int = 0):
        if elapsed >= 60:
            mins, secs = divmod(int(elapsed), 60)
            label = f"{mins:02d}:{secs:02d}"
        else:
            label = f"{elapsed:.1f}s"
        parts = [label]
        if tool_count:
            parts.append(f"{tool_count} outil" + ("s" if tool_count > 1 else ""))
        if files_changed:
            parts.append(f"{files_changed} fichier" + ("s" if files_changed > 1 else "") + " modifie" + ("s" if files_changed > 1 else ""))
        self.console.print(Text("  " + " \u00b7 ".join(parts), style=Catppuccin.OVERLAY0))

    def clear(self):
        self.console.clear()

    def thinking(self, message: str = "reflexion..."):
        """Generateur de spinner : next(anim) demarre, anim.close() arrete."""
        from rich.live import Live
        from rich.spinner import Spinner

        spinner = Spinner("dots", text=message, style=Catppuccin.BLUE)
        live = Live(spinner, console=self.console, refresh_per_second=12, transient=True)
        try:
            with live:
                yield
        finally:
            pass

    def todo_panel(self, items):
        from rich.table import Table

        rows = items if isinstance(items, list) else []
        table = Table.grid(padding=(0, 1))
        table.add_column(width=1, no_wrap=True)
        table.add_column()
        markers = {
            "pending": ("\u25cb", Catppuccin.OVERLAY0),
            "in_progress": ("\u25cf", Catppuccin.BLUE),
            "completed": ("\u2713", Catppuccin.GREEN),
            "cancelled": ("\u00d7", Catppuccin.RED),
        }
        for item in rows:
            marker, color = markers.get(item.get("status", "pending"), ("?", Catppuccin.YELLOW))
            table.add_row(Text(marker, style=f"bold {color}"), Text(item.get("content", ""), style=Catppuccin.TEXT))
        if not rows:
            table.add_row(Text("\u00b7", style=Catppuccin.OVERLAY0), Text("Aucune tache planifiee", style=Catppuccin.SUBTEXT0))
        self.console.print(Text("Plan", style="bold " + Catppuccin.MAUVE))
        self.console.print(guttered(table, Catppuccin.SURFACE1, content_width(self.console)))
        self.console.print()

    def welcome(self, version=None, provider="", target="", model="", workspace="", connected=None):
        from .header import banner
        from rich.rule import Rule

        self.console.print()
        self.console.print(
            banner(provider=provider, target=target, model=model, workspace=workspace, connected=connected)
        )
        self.console.print(Rule(style=Catppuccin.SURFACE0))
        self.console.print()

    def live_terminal(self, title: str = "bash"):
        return LiveTerminal(self.console, title)


class LiveTerminal:
    """Sortie de commande en temps reel, rendue comme une carte d'outil."""

    def __init__(self, console: Console, title: str = "bash"):
        self.console = console
        self.title = title
        self.lines: List[str] = []
        self._live = None

    def __enter__(self):
        from rich.live import Live

        self._live = Live(self._render(), console=self.console, refresh_per_second=15,
                          transient=True, vertical_overflow="ellipsis")
        self._live.__enter__()
        return self

    def __exit__(self, *args):
        if self._live:
            self._live.__exit__(*args)

    def add_line(self, line: str):
        self.lines.append(line.rstrip("\n"))
        if len(self.lines) > MAX_BASH_LINES:
            self.lines = self.lines[-MAX_BASH_LINES:]
        if self._live:
            self._live.update(self._render())

    def _render(self):
        from rich.console import Group

        body = "\n".join(self.lines) if self.lines else " …"
        rows = [
            Text(self.title, style="bold " + Catppuccin.PEACH),
            guttered(Text(body, style=Catppuccin.TEXT), Catppuccin.SURFACE1, content_width(self.console)),
        ]
        return Group(*rows)
