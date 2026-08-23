"""Thread-safe application facade for the PySide6 workbench."""
from __future__ import annotations

import copy
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..app import App
from ..config.configuration import save_config
from ..database.models import DownloadStatus
from ..database.models import UpFilterSettings
from ..options import DownloadOptions
from .workers import TaskWorker


@dataclass
class _Handle:
    thread: QThread
    worker: TaskWorker


class DesktopController(QObject):
    """Only public bridge used by the desktop views."""

    state_changed = Signal(object)
    task_started = Signal(str, object)
    task_progress = Signal(object)
    task_finished = Signal(str, object)
    task_failed = Signal(str, str)
    log_message = Signal(str)
    login_state_changed = Signal(bool, str)
    qr_ready = Signal(str, object)

    def __init__(self, app: App, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.app = app
        self._handles: Dict[str, _Handle] = {}
        self._closing = False
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- reads --
    def snapshot(self):
        snap = self.app.state.snapshot()
        self.state_changed.emit(snap)
        return snap

    def list_ups(self):
        return self.app.list_ups()

    def list_videos(self, mid=None):
        return self.app.repo.list_videos(mid)

    def status(self, mid=None) -> dict:
        result = self.app.status(mid)
        missing = 0
        for video in self.app.repo.list_downloaded(mid):
            if not video.download_path or not Path(video.download_path).is_file():
                missing += 1
        result["counts"] = dict(result["counts"])
        # A missing file is a more useful presentation state than DOWNLOADED;
        # keep the status buckets mutually exclusive in the dashboard.
        result["counts"]["DOWNLOADED"] = max(0, result["counts"].get("DOWNLOADED", 0) - missing)
        result["counts"]["MISSING"] = missing
        result["total"] = sum(result["counts"].values())
        return result

    def settings(self) -> dict:
        return copy.deepcopy(self.app.config)

    def preview_sync(self, mid, options):
        return self.app.preview(mid, options)

    # --------------------------------------------------------------- CRUD --
    def remove_up(self, mid: int) -> bool:
        return self.app.remove_up(mid)

    def set_up_enabled(self, mid: int, enabled: bool) -> None:
        self.app.repo.set_up_enabled(mid, enabled)

    def add_blacklist(self, mid: int, keyword: str) -> bool:
        return self.app.add_blacklist(mid, keyword)

    def remove_blacklist(self, mid: int, keyword: str) -> bool:
        return self.app.remove_blacklist(mid, keyword)

    def list_blacklist(self, mid: int):
        return self.app.list_blacklist(mid)

    def get_up_filter_settings(self, mid: int) -> UpFilterSettings:
        return self.app.get_up_filter_settings(mid)

    def save_up_filter_settings(self, settings: UpFilterSettings) -> None:
        self.app.save_up_filter_settings(settings)

    def save_settings(self, config: dict) -> Path:
        target = save_config(config, self.app.config_path)
        self.app.apply_runtime_config(config)
        return target

    # ------------------------------------------------------------- workers --
    def _start(self, name: str, fn: Callable[[threading.Event, TaskWorker], Any], mid=None) -> bool:
        with self._lock:
            if name in self._handles:
                self.log_message.emit(f"任务已在运行：{name}")
                return False
            if self._closing:
                return False
            self.app.state.reset_stop()
            thread = QThread(self)
            worker = TaskWorker(fn)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.log.connect(self.log_message)
            worker.progress.connect(self.task_progress)
            worker.qr_ready.connect(self._on_qr_ready)
            worker.result.connect(lambda result, n=name: self.task_finished.emit(n, result))
            worker.error.connect(lambda message, n=name: self.task_failed.emit(n, message))
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(lambda n=name: self._finished(n))
            self._handles[name] = _Handle(thread, worker)
            self.task_started.emit(name, mid)
            thread.start()
            return True

    def _finished(self, name: str) -> None:
        with self._lock:
            self._handles.pop(name, None)
        if not self._closing:
            self.app.state.reset_stop()
        self.snapshot()

    def _on_qr_ready(self, url: str, key: str, matrix: object) -> None:
        self.qr_ready.emit(url, matrix)

    def is_running(self, name: Optional[str] = None) -> bool:
        with self._lock:
            return bool(self._handles) if name is None else name in self._handles

    def start_add_up(self, mid: int) -> bool:
        return self._start("add_up", lambda cancel, worker: self.app.add_up(mid), mid)

    def start_scan(self, mid: Optional[int] = None) -> bool:
        return self._start("scan", lambda cancel, worker: self.app.scan(mid), mid)

    def start_check(self, mid: Optional[int] = None) -> bool:
        return self._start("check", lambda cancel, worker: self.app.check_files(mid), mid)

    def start_retry(self, mid: Optional[int] = None) -> bool:
        return self._start("retry", lambda cancel, worker: self.app.reset_failed(mid), mid)

    def start_preview(self, mid: Optional[int], options: DownloadOptions) -> bool:
        return self._start("preview", lambda cancel, worker: self.app.preview(mid, options), mid)

    def start_download(self, mid: Optional[int], options: DownloadOptions) -> bool:
        def work(cancel: threading.Event, worker: TaskWorker):
            prepared = self.app.prepare_download(mid, options)
            self.app.download_manager.set_options(options)
            self.app.download_manager.set_mid(mid)
            self.app.download_manager.start()
            try:
                while not cancel.is_set() and not self.app.state.stopped:
                    counts = self.app.repo.count_by_status(mid)
                    if counts.get("PENDING", 0) + counts.get("DOWNLOADING", 0) == 0:
                        break
                    time.sleep(0.25)
            finally:
                self.app.download_manager.stop()
                self.app.download_manager.join(timeout=10)
            return {"prepared": prepared, "status": self.status(mid)}

        return self._start("download", work, mid)

    def start_login(self) -> bool:
        def work(cancel: threading.Event, worker: TaskWorker):
            url, key, matrix = self.app.login.request_qrcode()
            worker.qr_ready.emit(url, key, matrix)
            while not cancel.is_set():
                ok, status = self.app.login.poll_qrcode_once(key)
                worker.log.emit(status)
                self.login_state_changed.emit(ok, status)
                if ok:
                    return True
                if status in {"expired", "failed"}:
                    return False
                time.sleep(1.5)
            return False

        return self._start("login", work)

    def pause(self, value: bool) -> None:
        self.app.state.set_paused(value)
        self.log_message.emit("已暂停" if value else "已恢复")

    def stop(self, name: Optional[str] = None) -> None:
        with self._lock:
            handles = list(self._handles.items())
        for task_name, handle in handles:
            if name is not None and task_name != name:
                continue
            handle.worker.cancel_event.set()
        self.app.state.request_stop()
        self.app.download_manager.stop()

    def close(self) -> None:
        self._closing = True
        self.stop()
        with self._lock:
            handles = list(self._handles.values())
        for handle in handles:
            handle.thread.quit()
            handle.thread.wait(3000)
        self.app.close()
