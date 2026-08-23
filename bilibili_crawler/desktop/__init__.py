"""PySide6 desktop workbench for the Bilibili crawler."""

from __future__ import annotations

__all__ = ["run_desktop"]


def run_desktop(argv=None) -> int:
    """Launch the desktop application lazily (so CLI users need no Qt import)."""
    from .app import run_desktop as _run

    return _run(argv)
