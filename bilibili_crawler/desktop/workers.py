"""Qt worker primitives used by the desktop controller."""
from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class TaskWorker(QObject):
    """Run a callable away from the Qt GUI thread."""

    result = Signal(object)
    error = Signal(str)
    log = Signal(str)
    progress = Signal(object)
    qr_ready = Signal(str, str, object)
    finished = Signal()

    def __init__(self, fn: Callable[[threading.Event, "TaskWorker"], Any]):
        super().__init__()
        self.fn = fn
        self.cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            self.result.emit(self.fn(self.cancel_event, self))
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
        finally:
            self.finished.emit()


class ThumbnailSignals(QObject):
    ready = Signal(str, object)


class ThumbnailRunnable(QRunnable):
    """Fetch one thumbnail and return a QPixmap through a signal."""

    def __init__(self, url: str, cache_path: str):
        super().__init__()
        self.url = url
        self.cache_path = cache_path
        self.signals = ThumbnailSignals()

    @Slot()
    def run(self) -> None:
        from pathlib import Path

        path = Path(self.cache_path)
        try:
            if not path.is_file():
                import requests

                response = requests.get(
                    self.url,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"},
                    timeout=10,
                )
                response.raise_for_status()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(response.content)
            # QPixmap is GUI-thread bound on Windows.  Only read bytes here;
            # the receiving slot constructs the pixmap on the Qt GUI thread.
            self.signals.ready.emit(self.url, path.read_bytes())
        except Exception:
            self.signals.ready.emit(self.url, b"")
