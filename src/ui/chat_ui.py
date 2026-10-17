"""NEMESIS Chat UI — slim, professional, terminal-adaptive.

No emojis. Responsive panels sized to the current terminal width.
"""

from __future__ import annotations

import time
from typing import List, Optional

from rich.console import Console, Group
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.table import Table
from rich.box import ROUNDED, SQUARE, SIMPLE
from rich.live import Live
from rich.align import Align
from rich.style import Style
from rich.rule import Rule

from .theme import Catppuccin


MAX_BASH_LINES = 400
MAX_READ_FILE_LINES = 800


def _term_width(console: Console) -> int:
    """Usable width: full terminal minus small margin, min 36."""
    try:
        w = console.size.width
    except Exception:
        w = 80
    return max(36, w - 2)


def _content_width(console: Console) -> int:
    """Slightly narrower content width for readable bubbles."""
    tw = _term_width(console)
    # Use almost full width on narrow terminals; cap only on very wide ones
    return max(36, min(tw, 140))


def _timestamp() -> str:
    return time.strftime("%H:%M:%S")


def _render_markdown(text: str) -> Markdown:
    try:
        return Markdown(
            text,
            code_theme="monokai",
            inline_code_style=Style(color=Catppuccin.PEACH),
        )
    except Exception:
        return Markdown(text)


def _truncate(s: str, n: int = 90) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 3] + "..."


class Bubble:
    """Single chat message. Full-width panel, no overflow."""

    def __init__(self, content: str, sender: str, timestamp: Optional[float] = None):
        self.content = content or ""
        self.sender = sender
        self.ts = timestamp or time.time()
        self.is_user = sender.lower() in ("user", "you", "vous")

    def render(self, console: Console) -> Panel:
        time_str = time.strftime("%H:%M", time.localtime(self.ts))
        w = _content_width(console)
        border = Catppuccin.GREEN if self.is_user else Catppuccin.BLUE
        name_style = f"bold {border}"

        title = Text()
        title.append(self.sender, style=name_style)
        title.append(f"  {time_str}", style=Catppuccin.OVERLAY0)

        body = _render_markdown(self.content) if self.content.strip() else Text(" ", style=Catppuccin.OVERLAY0)

        return Panel(
            body,
            title=title,
            title_align="left",
            border_style=border,
            padding=(0, 1),
            width=w,
            box=ROUNDED,
            expand=False,
        )


class LiveTerminal:
    """Real-time command output frame."""

    def __init__(self, console: Console, title: str = "bash"):
        self.console = console
        self.title = title
        self.lines: List[str] = []
        self._live: Optional[Live] = None

    def __enter__(self):
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=20,
            transient=True,
            vertical_overflow="ellipsis",
        )
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

    def _render(self) -> Panel:
        w = _content_width(self.console)
        content = "\n".join(self.lines) if self.lines else " "
        return Panel(
            Text(content, style=Catppuccin.TEXT),
            title=Text(f" {self.title} ", style=f"bold {Catppuccin.PEACH}"),
            border_style=Catppuccin.SURFACE1,
            padding=(0, 1),
            width=w,
            box=SQUARE,
        )


class ChatUI:
    """Slim chat interface."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.messages: List[Bubble] = []
        self._last_sender: Optional[str] = None
        self._pending_tool: Optional[tuple[str, dict]] = None

    def add_message(self, content: str, sender: str, timestamp: Optional[float] = None):
        bubble = Bubble(content, sender, timestamp)
        self.messages.append(bubble)
        if self._last_sender and self._last_sender != sender:
            self.console.print()
        self._last_sender = sender
        self.console.print(bubble.render(self.console))

    def thinking(self, message: str = "thinking..."):
        from rich.progress import Progress, SpinnerColumn, TextColumn

        with Progress(
            SpinnerColumn(spinner_name="line", style=Catppuccin.BLUE),
            TextColumn(f"[{Catppuccin.SUBTEXT0}]{message}[/]"),
            console=self.console,
            transient=True,
            refresh_per_second=12,
        ) as progress:
            progress.add_task("think", total=None)
            yield

    def tool_start(self, tool_name: str, params: dict):
        """Hold the invocation until its result can be shown on the same line."""
        self._pending_tool = (tool_name, params if isinstance(params, dict) else {})

    def _tool_invocation(self, tool_name: str, params: dict) -> str:
        """Create a short, human-readable action label."""
        params = params if isinstance(params, dict) else {}
        path = params.get("path") or params.get("file_path") or params.get("filename")
        command = params.get("command")
        query = params.get("query") or params.get("pattern") or params.get("url")
        labels = {
            "read_file": "Lire",
            "read": "Lire",
            "write_file": "Écrire",
            "write": "Écrire",
            "edit": "Modifier",
            "search_replace": "Modifier",
            "apply_patch": "Appliquer un patch",
            "delete_file": "Supprimer",
            "list_dir": "Lister",
            "glob": "Rechercher des fichiers",
            "grep": "Rechercher dans les fichiers",
            "bash": "Terminal",
            "git": "Git",
            "web_search": "Recherche Web",
            "web_fetch": "Ouvrir une page Web",
            "todo": "Plan",
            "delegate_tasks": "Déléguer",
            "subagent_status": "État des sous-agents",
            "mcp_call": "Outil MCP",
            "mcp_list": "Serveurs MCP",
            "mcp_tools_list": "Outils MCP",
            "skills_list": "Compétences",
            "memory_search": "Recherche mémoire",
            "memory_add": "Ajouter à la mémoire",
            "memory_get": "Lire la mémoire",
            "memory_update": "Modifier la mémoire",
            "memory_delete": "Supprimer de la mémoire",
        }
        label = labels.get(tool_name, tool_name.replace("_", " ").capitalize())
        if tool_name == "bash":
            value = command
        elif tool_name in {"grep", "glob", "web_search", "web_fetch"}:
            value = query or path
        else:
            value = path or query
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value[:3])
        if isinstance(value, str) and value.strip():
            limit = 110 if tool_name == "bash" else 90
            return f"{label} · {_truncate(value.strip(), limit)}"
        return label

    @staticmethod
    def _tool_name(tool_name: str) -> str:
        names = {
            "read_file": "Lecture fichier",
            "read": "Lecture fichier",
            "write_file": "Écriture fichier",
            "write": "Écriture fichier",
            "edit": "Modification fichier",
            "search_replace": "Modification fichier",
            "apply_patch": "Application du patch",
            "delete_file": "Suppression fichier",
            "list_dir": "Liste du dossier",
            "glob": "Recherche de fichiers",
            "grep": "Recherche dans les fichiers",
            "bash": "Terminal",
            "git": "Git",
            "web_search": "Recherche Web",
            "web_fetch": "Page Web",
            "todo": "Plan",
            "delegate_tasks": "Délégation",
            "subagent_status": "État des sous-agents",
            "mcp_call": "Outil MCP",
            "mcp_list": "Serveurs MCP",
            "mcp_tools_list": "Outils MCP",
            "skills_list": "Compétences",
        }
        return names.get(tool_name, tool_name.replace("_", " ").capitalize())

    def tool_result(self, result: dict, tool_name: str = "", duration: float | None = None):
        if hasattr(result, "success"):
            success = bool(result.success)
        elif isinstance(result, dict):
            success = bool(result.get("success", False))
        else:
            success = False

        pending_name, pending_params = self._pending_tool or (tool_name, {})
        if pending_name != tool_name:
            pending_params = {}
        action = self._tool_invocation(tool_name, pending_params)
        color = Catppuccin.GREEN if success else Catppuccin.RED
        status = "OK" if success else "Échec"
        row = Text()
        row.append("  ")
        row.append("✓ " if success else "✗ ", style=f"bold {color}")
        row.append(action, style=Catppuccin.TEXT)
        row.append("   ")
        row.append(status, style=f"bold {color}")
        if duration is not None:
            row.append(f"   {duration:.1f}s", style=Catppuccin.OVERLAY0)
        self.console.print(row)
        self._pending_tool = None

        if tool_name == "bash":
            if isinstance(result, dict):
                output = result.get("output") or result.get("stdout") or result.get("error") or ""
            else:
                output = getattr(result, "output", "") or getattr(result, "error", "")
            lines = str(output).splitlines()
            preview = "\n".join(lines[:10])
            if len(lines) > 10:
                preview += f"\n… {len(lines) - 10} lignes masquées · /show last"
            self.console.print(
                Panel(
                    Text(preview or "(aucune sortie)", style=Catppuccin.TEXT),
                    title=Text(" Sortie ", style=f"bold {Catppuccin.SUBTEXT0}"),
                    title_align="left",
                    border_style=Catppuccin.SURFACE1,
                    padding=(0, 1),
                    width=_content_width(self.console),
                    box=ROUNDED,
                )
            )

    def _display_edit_diff(self, result: dict, w: int, color: str, status: str):
        """Affiche un diff structuré pour l'édition."""
        from rich.text import Text
        from rich.panel import Panel

        edits = result.get("edits", [])
        message = result.get("message", "Modification effectuee")
        
        if not edits:
            self.console.print(
                Panel(
                    Text(message, style=color),
                    border_style=color,
                    padding=(0, 1),
                    width=w,
                    box=SQUARE,
                    title=Text(f" edit · {status} ", style=f"bold {color}"),
                    title_align="left",
                )
            )
            return

        content = Text("")
        for edit in edits:
            if hasattr(edit, "old_string"):
                old_str = edit.old_string
                new_str = edit.new_string
                old_line = getattr(edit, "old_line", 0)
                new_line = getattr(edit, "new_line", 0)
            else:
                old_str = edit.get("old_string", "")
                new_str = edit.get("new_string", "")
                old_line = edit.get("old_line", 0)
                new_line = edit.get("new_line", 0)

            old_lines = old_str.split("\n")
            for i, line in enumerate(old_lines):
                line_num = old_line + i if old_line else i + 1
                content.append(f"-{line_num} {line}\n", style=Catppuccin.RED)

            new_lines = new_str.split("\n")
            for i, line in enumerate(new_lines):
                line_num = new_line + i if new_line else i + 1
                content.append(f"+{line_num} {line}\n", style=Catppuccin.GREEN)

            content.append("\n", style=Catppuccin.OVERLAY0)

        if not content:
            content = Text("(aucun changement visible)", style=Catppuccin.OVERLAY0)

        self.console.print(
            Panel(
                content,
                border_style=color,
                padding=(0, 1),
                width=w,
                box=SQUARE,
                title=Text(f" edit · {status} ", style=f"bold {color}"),
                title_align="left",
            )
        )

    def tool_error(self, error: str):
        w = _content_width(self.console)
        self.console.print(
            Panel(
                Text(str(error), style=Catppuccin.RED),
                border_style=Catppuccin.RED,
                padding=(0, 1),
                width=w,
                box=SQUARE,
                title=Text(" error ", style=f"bold {Catppuccin.RED}"),
                title_align="left",
            )
        )

    def auth_prompt(self, tool_name: str, params: dict) -> Text:
        """Render authorization as one compact line instead of a panel."""
        summary = _truncate(self._tool_invocation(tool_name, params), _term_width(self.console) - 34)
        return Text.assemble(
            ("  ", Catppuccin.OVERLAY0),
            ("! ", f"bold {Catppuccin.YELLOW}"),
            ("authorize ", Catppuccin.SUBTEXT0),
            (summary, Catppuccin.TEXT),
            ("  [y] once  [a] always  [n] deny", Catppuccin.SUBTEXT0),
        )

    def live_terminal(self, title: str = "bash") -> LiveTerminal:
        return LiveTerminal(self.console, title)

    def task_summary(self, elapsed: float, tool_count: int = 0):
        if elapsed >= 60:
            mins, secs = divmod(int(elapsed), 60)
            time_str = f"{mins:02d}:{secs:02d}"
        else:
            time_str = f"{elapsed:.1f}s"
        parts = [f"done in {time_str}"]
        if tool_count:
            parts.append(f"{tool_count} tool(s)")
        self.console.print(Text("  " + " · ".join(parts), style=Catppuccin.OVERLAY0))

    def todo_panel(self, items):
        """Render the current task plan as a compact, readable terminal panel."""
        from rich.table import Table
        rows = items if isinstance(items, list) else []
        table = Table(show_header=False, box=None, pad_edge=False, expand=True)
        table.add_column("state", width=3)
        table.add_column("task")
        markers = {
            "pending": ("○", Catppuccin.OVERLAY0),
            "in_progress": ("●", Catppuccin.BLUE),
            "completed": ("✓", Catppuccin.GREEN),
            "cancelled": ("×", Catppuccin.RED),
        }
        for item in rows:
            marker, color = markers.get(item.get("status", "pending"), ("?", Catppuccin.YELLOW))
            table.add_row(Text(marker, style=f"bold {color}"), Text(item.get("content", ""), style=Catppuccin.TEXT))
        if not rows:
            table.add_row(Text("·", style=Catppuccin.OVERLAY0), Text("Aucune tâche planifiée", style=Catppuccin.SUBTEXT0))
        self.console.print(Panel(table, title=Text(" plan ", style=f"bold {Catppuccin.MAUVE}"),
                                 border_style=Catppuccin.SURFACE1, box=ROUNDED, padding=(0, 1)))

    def welcome(
        self,
        version: str | None = None,
        provider: str = "",
        target: str = "",
        model: str = "",
        workspace: str = "",
        connected: bool | None = None,
    ):
        from .header import get_header

        del version
        self.console.print(
            get_header(
                provider=provider,
                target=target,
                model=model,
                workspace=workspace,
                connected=connected,
            )
        )
        self.console.print(
            Text("  Entrez votre demande ou utilisez /help pour explorer l'interface.",
                 style=Catppuccin.SUBTEXT0)
        )
        self.console.print()

    def _legacy_welcome(self, version: str = "", provider: str = "", target: str = ""):
        """Compatibility shim for integrations that used the old implementation."""
        w = _content_width(self.console)
        brand = Text()
        brand.append("NEMESIS", style=f"bold {Catppuccin.MAUVE}")

        lines = [
            brand,
            Text("autonomous coding agent", style=f"italic {Catppuccin.SUBTEXT0}"),
        ]
        if provider:
            lines.append(Text(f"{provider}  ->  {target}", style=Catppuccin.GREEN))
        lines.append(Text("type /help for commands", style=Catppuccin.OVERLAY0))

        self.console.print(
            Panel(
                Group(*lines),
                border_style=Catppuccin.SURFACE1,
                padding=(1, 2),
                width=w,
                box=ROUNDED,
            )
        )
    def clear(self):
        self.console.clear()
