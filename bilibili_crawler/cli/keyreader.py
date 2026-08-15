"""Minimal cross-platform keyboard reader for the TUI.

Reads single keys without requiring the Enter key (the way btop/htop do):
Windows uses ``msvcrt``, POSIX uses ``termios`` + ``select``. This keeps the
TUI free of a blocking ``input()`` call that would fight the terminal redraw.
"""
from __future__ import annotations

import sys
from typing import Optional


class KeyReader:
    def __init__(self) -> None:
        self._posix = sys.platform != "win32"
        self._fd = None
        self._old = None
        if self._posix:
            import termios
            import tty

            self._termios = termios
            self._tty = tty
            self._fd = sys.stdin.fileno()
        else:
            import msvcrt  # noqa: F401

            self._msvcrt = sys.modules["msvcrt"]

    def read(self, timeout: Optional[float] = None) -> Optional[str]:
        """Return a single character, or None on timeout/EOF."""
        if self._posix:
            return self._read_posix(timeout)
        return self._read_windows(timeout)

    # -------------------------------------------------------------- Windows --
    def _read_windows(self, timeout: Optional[float]) -> Optional[str]:
        msvcrt = self._msvcrt
        if timeout is not None:
            import time

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    return self._decode(msvcrt.getwch())
                time.sleep(0.03)
            return None
        return self._decode(msvcrt.getwch())

    @staticmethod
    def _decode(ch: str) -> str:
        # getwch returns a single-character str on py3.
        if ch in ("\x00", "\xe0"):
            # Discard the second byte of extended keys (arrows etc.).
            import msvcrt

            msvcrt.getwch()
            return ""
        return ch

    # ---------------------------------------------------------------- POSIX --
    def _read_posix(self, timeout: Optional[float]) -> Optional[str]:
        import select

        if not self._old:
            self._enter_raw()
        try:
            if timeout is not None:
                ready, _, _ = select.select([self._fd], [], [], timeout)
                if not ready:
                    return None
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                # Escape sequence (arrow keys): consume and ignore.
                ready, _, _ = select.select([self._fd], [], [], 0.05)
                while ready:
                    sys.stdin.read(1)
                    ready, _, _ = select.select([self._fd], [], [], 0.05)
                return "\x1b"
            return ch
        except (OSError, ValueError):
            return None

    def _enter_raw(self) -> None:
        self._old = self._termios.tcgetattr(self._fd)
        self._tty.setraw(self._fd)

    def restore(self) -> None:
        if self._posix and self._old is not None:
            try:
                self._termios.tcsetattr(self._fd, self._termios.TCSADRAIN, self._old)
            except (OSError, ValueError):
                pass
            self._old = None
