"""Composer — input/output orchestration for NEMESIS CLI."""

from __future__ import annotations

import sys
from typing import Optional

from rich.console import Console
from rich.text import Text

from prompt_toolkit import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.layout import Layout, HSplit, FloatContainer, Float
from prompt_toolkit.widgets import Frame, TextArea
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PtStyle
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.clipboard import InMemoryClipboard

from src.core.commands import registry
from .theme import Catppuccin, NEMESIS_THEME
from .chat_ui import ChatUI


class SlashCompleter(Completer):
    """Completer for / commands — display adapts to available commands."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        query = text[1:].lower()
        commands = sorted(registry.list_commands(), key=lambda c: c.name)
        for cmd in commands:
            if query and not cmd.name.lower().startswith(query):
                continue
            desc = (cmd.description or "").strip()
            # Slim display: /name  description
            display = f"/{cmd.name}"
            if desc:
                display = f"/{cmd.name}  —  {desc}"
            yield Completion(
                cmd.name,
                start_position=-len(query) if query else 0,
                display=display,
            )


def _command_count() -> int:
    try:
        return max(1, len(registry.list_commands()))
    except Exception:
        return 8


class Composer:
    """Manages input (prompt_toolkit) and output (ChatUI)."""

    DEFAULT_TITLE = "input"
    AUTH_TITLE = "authorization  [y / n / a]"

    def __init__(self, version: str | None = None):
        self.console = Console(theme=NEMESIS_THEME)
        self.chat = ChatUI(self.console)
        self._is_tty = sys.stdin.isatty()
        self._version = version  # compatibility only; never displayed
        self.interrupt_requested = False
        if self._is_tty:
            self._init_pt()

    def _init_pt(self):
        self.pt_style = PtStyle.from_dict({
            "frame.border": Catppuccin.SURFACE1,
            "frame.label": f"bold {Catppuccin.BLUE}",
            "completion-menu": f"bg:{Catppuccin.MANTLE}",
            "completion-menu.completion": f"bg:{Catppuccin.MANTLE} {Catppuccin.TEXT}",
            "completion-menu.completion.current": f"bg:{Catppuccin.SURFACE1} {Catppuccin.TEXT} bold",
            "completion-menu.meta.completion": f"bg:{Catppuccin.MANTLE} {Catppuccin.OVERLAY0}",
            "completion-menu.meta.completion.current": f"bg:{Catppuccin.SURFACE1} {Catppuccin.SUBTEXT0}",
            "completion-menu.border": Catppuccin.SURFACE1,
            "scrollbar.background": Catppuccin.MANTLE,
            "scrollbar.button": Catppuccin.SURFACE1,
        })

        self.input_area = TextArea(
            prompt=HTML(f" <b><ansicyan>&gt;</ansicyan></b> "),
            multiline=False,
            wrap_lines=False,
            completer=SlashCompleter(),
            complete_while_typing=True,
            style=f"bg:{Catppuccin.BASE} fg:{Catppuccin.TEXT}",
        )

        self._frame = Frame(
            body=self.input_area,
            title=self.DEFAULT_TITLE,
            height=3,
            style=f"bg:{Catppuccin.BASE}",
        )

        # Dynamic menu height: scale with number of commands (min 4, max 16)
        menu_h = min(16, max(4, _command_count() + 1))

        root = HSplit([
            self._frame,
            CompletionsMenu(max_height=menu_h, scroll_offset=1),
        ])

        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event):
            text = self.input_area.text.rstrip()
            self.input_area.text = ""
            event.app.exit(result=text)

        @kb.add("c-c")
        @kb.add("c-d")
        def _interrupt(event):
            # Signal the agent loop that the user cancelled the current prompt
            # (e.g. an authorization request or an interactive command input).
            self.interrupt_requested = True
            event.app.exit(result=None)

        @kb.add("escape")
        def _clear(event):
            self.input_area.text = ""

        @kb.add("c-v")
        def _paste(event):
            try:
                self.input_area.buffer.paste()
            except Exception:
                pass

        @kb.add("up")
        def _up(event):
            b = self.input_area.buffer
            if b.complete_state:
                b.complete_previous()

        @kb.add("down")
        @kb.add("tab")
        def _down(event):
            b = self.input_area.buffer
            if b.complete_state:
                b.complete_next()
            else:
                b.start_completion(select_first=False)

        @kb.add("s-tab")
        def _stab(event):
            b = self.input_area.buffer
            if b.complete_state:
                b.complete_previous()

        self.input_app = Application(
            layout=Layout(root),
            key_bindings=kb,
            full_screen=False,
            erase_when_done=True,
            style=self.pt_style,
            clipboard=InMemoryClipboard(),
            paste_mode=True,
        )

    # ── Output ──────────────────────────────────

    def display_user_message(self, text: str):
        self.chat.add_message(text, "User")

    def display_ai_message(self, text: str):
        self.chat.add_message(text, "NEMESIS")

    def display_tool_start(self, tool_name: str, params: dict):
        self.chat.tool_start(tool_name, params)

    def display_tool_result(self, result: dict, tool_name: str = ""):
        self.chat.tool_result(result, tool_name)

    def display_tool_error(self, error: str):
        self.chat.tool_error(error)

    def display_thinking(self, message: str = "thinking..."):
        return self.chat.thinking(message)

    def display_task_summary(self, elapsed: float, tool_count: int = 0):
        self.chat.task_summary(elapsed, tool_count)

    def display_todo(self, items):
        self.chat.todo_panel(items)

    def display_welcome(
        self,
        provider: str = "",
        target: str = "",
        model: str = "",
        workspace: str = "",
        connected: bool | None = None,
    ):
        self.chat.welcome(
            provider=provider,
            target=target,
            model=model,
            workspace=workspace,
            connected=connected,
        )

    def display_auth(self, tool_name: str, params: dict):
        self.console.print(self.chat.auth_prompt(tool_name, params if isinstance(params, dict) else {}))

    def live_terminal(self, title: str = "bash"):
        return self.chat.live_terminal(title)

    # ── Input ───────────────────────────────────

    def prompt_input(self, title: Optional[str] = None, placeholder: str = "") -> Optional[str]:
        if not self._is_tty:
            try:
                return input().strip()
            except (EOFError, KeyboardInterrupt):
                return None

        prev_title = self._frame.title
        try:
            if title is not None:
                self._frame.title = title
            self.input_area.text = placeholder
            result = self.input_app.run()
            return result.strip() if result else None
        except (KeyboardInterrupt, EOFError):
            return None
        finally:
            self._frame.title = prev_title
            self.input_area.text = ""
