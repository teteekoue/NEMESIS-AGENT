"""Interactive PTY-backed shell for the web terminal panel.

A dedicated ``/ws/terminal`` websocket attaches a real pseudoterminal on the
host running the backend, so the right-hand terminal tab is an actual shell
(not just a log viewer). Messages are JSON:

  client -> server : {"type": "input", "data": "<keystrokes>"}
                     {"type": "resize", "cols": 120, "rows": 30}
                     {"type": "ping"}
  server -> client : {"type": "output", "data": "<bytes as latin-1 str>"}
                     {"type": "ready", "shell": "/bin/bash", "cwd": "..."}
                     {"type": "exit", "code": 0}

The PTY is spawned in the configured workspace. This runs a real shell on the
machine hosting the backend; it is intended for local/dev use.
"""

from __future__ import annotations

import asyncio
import os
import pty
import select
import signal
import struct
import fcntl
import termios
from typing import Optional


class PtySession:
    """Owns a forkpty shell and pumps its output into an asyncio queue."""

    def __init__(self, shell: str, cwd: str, env: dict, cols: int = 120, rows: int = 30):
        self.shell = shell
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self._env = env
        self.pid: int = -1
        self.fd: int = -1
        self.queue: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
        self._closed = False

    def start(self) -> None:
        pid, fd = pty.fork()
        if pid == 0:  # child
            try:
                os.chdir(self.cwd)
            except OSError:
                os.chdir("/")
            env = dict(self._env)
            env["TERM"] = "xterm-256color"
            env["PS1"] = r"\w $ "
            os.execvpe(self.shell, [self.shell, "-i"], env)
            os._exit(1)
        self.pid = pid
        self.fd = fd
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.resize(self.cols, self.rows)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        if self.fd < 0:
            return
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    def write(self, data: bytes) -> None:
        if self.fd < 0 or self._closed:
            return
        try:
            os.write(self.fd, data)
        except OSError:
            self.close()

    def read_available(self) -> Optional[bytes]:
        if self.fd < 0 or self._closed:
            return None
        try:
            ready, _, _ = select.select([self.fd], [], [], 0.1)
        except (OSError, ValueError):
            return None
        if not ready:
            return b""
        try:
            return os.read(self.fd, 65536)
        except OSError:
            return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
        if self.pid > 0:
            try:
                os.kill(self.pid, signal.SIGHUP)
            except OSError:
                pass
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except OSError:
                pass