"""Thread-safe application facade for the PySide6 workbench."""
from __future__ import annotations

import copy
import os
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QThread, Signal

from ..app import App
from ..config.configuration import save_config
from ..download_directory import normalize_download_root
from ..database.models import DownloadStatus
from ..database.models import UpFilterSettings
from ..options import DownloadOptions
from .workers import TaskWorker
from ..youtube import identify_channel, YouTubeChannel


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
        self.source = "bilibili"
        self._handles: Dict[str, _Handle] = {}
        self._closing = False
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- reads --
    def snapshot(self):
        snap = self.app.state.snapshot()
        self.state_changed.emit(snap)
        return snap

    def list_ups(self):
        if self.source == "youtube":
            return self.app.youtube().list_channels()
        return self.app.list_ups()

    def set_source(self, source: str) -> None:
        if source not in ("bilibili", "youtube"):
            raise ValueError("invalid source")
        self.source = source

    def count_videos(self, channel_id=None, media_type="video") -> int:
        if self.source == "youtube":
            return len(self.app.youtube().list_videos(channel_id, media_type))
        return self.app.repo.count_videos(channel_id)

    def downloaded_videos(self, source: str, media_type: str = "video"):
        previous = self.source
        try:
            self.source = source
            if source == "youtube":
                return [v for v in self.app.youtube().list_videos(None, media_type) if v.status == "DOWNLOADED"]
            return self.app.repo.list_downloaded(None, media_type)
        finally:
            self.source = previous

    def list_videos(self, mid=None, media_type="video"):
        if self.source == "youtube":
            return self.app.youtube().list_videos(mid, media_type)
        return self.app.repo.list_videos(mid, media_type)

    def status(self, mid=None, media_type="video") -> dict:
        if self.source == "youtube":
            return self.app.youtube().status(mid, media_type)
        result = self.app.status(mid, media_type)
        missing = 0
        root = normalize_download_root(self.app.download_root)
        for video in self.app.repo.list_downloaded(mid, media_type):
            try:
                path = Path(video.download_path).expanduser().resolve(strict=False)
                valid = path.is_file() and path.stat().st_size > 0 and path.is_relative_to(root)
            except OSError:
                valid = False
            if not valid:
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
        if self.source == "youtube":
            return self.app.youtube().preview(mid, options.media_type, options)
        return self.app.preview(mid, options)

    # --------------------------------------------------------------- CRUD --
    def remove_up(self, mid: int) -> bool:
        if self.source == "youtube":
            return self.app.youtube().remove_channel(str(mid))
        return self.app.remove_up(mid)

    def set_up_enabled(self, mid: int, enabled: bool) -> None:
        if self.source == "youtube":
            self.app.youtube().db.execute("UPDATE channel SET enabled=? WHERE channel_id=?", (int(enabled), str(mid))); self.app.youtube().db.commit(); return
        self.app.repo.set_up_enabled(mid, enabled)

    def add_blacklist(self, mid: int, keyword: str) -> bool:
        if self.source == "youtube":
            db = self.app.youtube().db; cur = db.execute("INSERT OR IGNORE INTO blacklist(channel_id,keyword) VALUES(?,?)", (str(mid), keyword.strip())); db.commit(); return cur.rowcount > 0
        return self.app.add_blacklist(mid, keyword)

    def remove_blacklist(self, mid: int, keyword: str) -> bool:
        if self.source == "youtube":
            db = self.app.youtube().db; cur = db.execute("DELETE FROM blacklist WHERE channel_id=? AND keyword=?", (str(mid), keyword.strip())); db.commit(); return cur.rowcount > 0
        return self.app.remove_blacklist(mid, keyword)

    def list_blacklist(self, mid: int):
        if self.source == "youtube":
            return [r[0] for r in self.app.youtube().db.execute("SELECT keyword FROM blacklist WHERE channel_id=? ORDER BY keyword", (str(mid),))]
        return self.app.list_blacklist(mid)

    def add_allowlist(self, mid: int, keyword: str) -> bool:
        if self.source == "youtube":
            db = self.app.youtube().db; cur = db.execute("INSERT OR IGNORE INTO allowlist(channel_id,keyword) VALUES(?,?)", (str(mid), keyword.strip())); db.commit(); return cur.rowcount > 0
        return self.app.add_allowlist(mid, keyword)

    def remove_allowlist(self, mid: int, keyword: str) -> bool:
        if self.source == "youtube":
            db = self.app.youtube().db; cur = db.execute("DELETE FROM allowlist WHERE channel_id=? AND keyword=?", (str(mid), keyword.strip())); db.commit(); return cur.rowcount > 0
        return self.app.remove_allowlist(mid, keyword)

    def list_allowlist(self, mid: int):
        if self.source == "youtube":
            return [r[0] for r in self.app.youtube().db.execute("SELECT keyword FROM allowlist WHERE channel_id=? ORDER BY keyword", (str(mid),))]
        return self.app.list_allowlist(mid)

    def get_up_filter_settings(self, mid: int) -> UpFilterSettings:
        if self.source == "youtube":
            row = self.app.youtube().db.execute("SELECT * FROM filter_settings WHERE channel_id=?", (str(mid),)).fetchone()
            if row:
                return UpFilterSettings(str(mid), row["min_duration"], row["max_duration"], row["min_date"], row["max_date"])
            return UpFilterSettings(str(mid))
        return self.app.get_up_filter_settings(mid)

    def save_up_filter_settings(self, settings: UpFilterSettings) -> None:
        if self.source == "youtube":
            db = self.app.youtube().db
            db.execute("INSERT INTO filter_settings(channel_id,min_duration,max_duration,min_date,max_date) VALUES(?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET min_duration=excluded.min_duration,max_duration=excluded.max_duration,min_date=excluded.min_date,max_date=excluded.max_date", (str(settings.mid), settings.min_duration, settings.max_duration, settings.min_date, settings.max_date)); db.commit(); return
        self.app.save_up_filter_settings(settings)

    def save_settings(self, config: dict) -> Path:
        new_root = normalize_download_root(config["download"]["save_root"])
        current_root = normalize_download_root(self.app.download_root)
        with self._lock:
            other_tasks_running = any(name != "settings" for name in self._handles)
        if os.path.normcase(str(new_root)) != os.path.normcase(str(current_root)) and other_tasks_running:
            raise RuntimeError("任务运行中不能切换下载目录，请先停止任务")
        old_config = copy.deepcopy(self.app.config)
        target = save_config(config, self.app.config_path)
        try:
            self.app.apply_runtime_config(config)
        except Exception:
            # Keep the on-disk configuration consistent with the active
            # application state if validation or directory reconciliation
            # fails after the atomic config write.
            try:
                save_config(old_config, self.app.config_path)
            except Exception:
                pass
            raise
        return target

    def start_save_settings(self, config: dict) -> bool:
        """Persist settings away from the GUI thread (directory scans included)."""
        with self._lock:
            if any(name != "settings" for name in self._handles):
                self.log_message.emit("任务运行中不能切换设置或下载目录")
                return False
        return self._start("settings", lambda cancel, worker: str(self.save_settings(config)))

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
        if self.source == "youtube":
            return self._start("add_up", lambda cancel, worker: self.app.youtube().add_channel(str(mid)), mid)
        return self._start("add_up", lambda cancel, worker: self.app.add_up(mid), mid)

    def start_scan(self, mid: Optional[int] = None) -> bool:
        if self.source == "youtube":
            # ``None`` is the Tasks page's "全部 UP" selection.  Preserve it
            # so the YouTube service can scan every enabled channel instead
            # of trying to resolve the literal string "None".
            channel = str(mid) if mid else None
            return self._start("scan", lambda cancel, worker: self.app.youtube().scan(channel), mid)
        return self._start("scan", lambda cancel, worker: self.app.scan(mid), mid)

    def start_check(self, mid: Optional[int] = None, media_type: str = "video") -> bool:
        if self.source == "youtube":
            return self._start("check", lambda cancel, worker: self.app.youtube().check_files(str(mid) if mid else None, media_type), mid)
        return self._start("check", lambda cancel, worker: self.app.check_files(mid, media_type), mid)

    def start_retry(self, mid: Optional[int] = None, media_type: str = "video") -> bool:
        if self.source == "youtube":
            return self._start("retry", lambda cancel, worker: self.app.youtube().reset_failed(str(mid) if mid else None, media_type), mid)
        return self._start("retry", lambda cancel, worker: self.app.reset_failed(mid, media_type), mid)

    def start_preview(self, mid: Optional[int], options: DownloadOptions) -> bool:
        if self.source == "youtube":
            return self._start("preview", lambda cancel, worker: self.app.youtube().preview(str(mid) if mid else None, options.media_type, options), mid)
        return self._start("preview", lambda cancel, worker: self.app.preview(mid, options), mid)

    def start_download(self, mid: Optional[int], options: DownloadOptions) -> bool:
        if self.source == "youtube":
            return self._start("download", lambda cancel, worker: self.app.youtube().download(str(mid) if mid else None, options.media_type, quality=options.quality, options=options, stop_event=cancel), mid)
        def work(cancel: threading.Event, worker: TaskWorker):
            prepared = self.app.prepare_download(mid, options)
            self.app.download_manager.set_options(options)
            self.app.download_manager.set_mid(mid)
            self.app.download_manager.start()
            try:
                while not cancel.is_set() and not self.app.state.stopped:
                    counts = self.app.repo.count_by_status(mid, options.media_type)
                    if counts.get("PENDING", 0) + counts.get("DOWNLOADING", 0) == 0:
                        break
                    time.sleep(0.25)
            finally:
                self.app.download_manager.stop()
                self.app.download_manager.join(timeout=10)
            return {"prepared": prepared, "status": self.status(mid, options.media_type)}

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
