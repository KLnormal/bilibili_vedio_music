"""Shared, thread-safe runtime state consumed by the TUI.

The crawler / downloader worker threads update this object; the TUI thread
reads a consistent snapshot each refresh cycle. This keeps the core logic
fully decoupled from presentation (plan principle #4).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

from .downloader.limiter import RateLimiter


@dataclass
class DownloadProgress:
    bvid: str = ""
    title: str = ""
    downloaded: int = 0
    total: int = -1
    speed: str = ""
    status: str = ""

    @property
    def percent(self) -> Optional[float]:
        if self.total and self.total > 0:
            return min(100.0, self.downloaded / self.total * 100.0)
        return None


@dataclass
class Snapshot:
    paused: bool = False
    stopped: bool = False
    # scan
    current_up: str = ""
    scan_status: str = ""
    scan_active: bool = False
    scan_page: int = 0
    scan_items: int = 0
    scan_next_page: int = 1
    new_count: int = 0
    existing_count: int = 0
    filtered_count: int = 0
    # download
    progress: DownloadProgress = field(default_factory=DownloadProgress)
    downloaded_count: int = 0
    failed_count: int = 0
    pending_count: int = 0
    # limiter
    rate_mbps: float = 0.0
    # logs
    logs: List[str] = field(default_factory=list)
    # UP overview rows: (name, mid, video_count, enabled)
    ups: List[tuple] = field(default_factory=list)


class RuntimeState:
    def __init__(self, limiter: RateLimiter):
        self.limiter = limiter
        self._lock = threading.RLock()
        self._paused = False
        self._stopped = False
        self._current_up = ""
        self._scan_status = ""
        self._scan_active = False
        self._scan_page = 0
        self._scan_items = 0
        self._scan_next_page = 1
        self._new_count = 0
        self._existing_count = 0
        self._filtered_count = 0
        self._progress = DownloadProgress()
        self._downloaded_count = 0
        self._failed_count = 0
        self._pending_count = 0
        self._ups: List[tuple] = []
        self._logs: Deque[str] = deque(maxlen=200)

    # ------------------------------------------------------------ control --
    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, value: bool) -> None:
        with self._lock:
            self._paused = value

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def request_stop(self) -> None:
        with self._lock:
            self._stopped = True

    def reset_stop(self) -> None:
        """Clear a user-requested stop so another desktop task can run."""
        with self._lock:
            self._stopped = False

    # -------------------------------------------------------------- logging --
    def log(self, message: str) -> None:
        with self._lock:
            self._logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    # ---------------------------------------------------------------- scan --
    def set_scan(self, up_name: str, status: str) -> None:
        with self._lock:
            self._current_up = up_name
            self._scan_status = status
            self._scan_active = True

    def set_scan_progress(self, page: int, items: int, next_page: int) -> None:
        with self._lock:
            self._scan_page = max(0, int(page))
            self._scan_items += max(0, int(items))
            self._scan_next_page = max(1, int(next_page))
            self._scan_active = True
            self._scan_status = f"第 {self._scan_page} 页，已处理 {self._scan_items} 条"

    def finish_scan(self, status: str = "扫描完成") -> None:
        with self._lock:
            self._scan_status = status
            self._scan_active = False

    def add_scan_stats(self, new: int = 0, existing: int = 0, filtered: int = 0) -> None:
        with self._lock:
            self._new_count += new
            self._existing_count += existing
            self._filtered_count += filtered

    def reset_scan_stats(self) -> None:
        with self._lock:
            self._new_count = 0
            self._existing_count = 0
            self._filtered_count = 0
            self._scan_page = 0
            self._scan_items = 0
            self._scan_next_page = 1

    # ------------------------------------------------------------ download --
    def set_progress(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._progress, key, value)

    def clear_progress(self) -> None:
        with self._lock:
            self._progress = DownloadProgress()

    def add_download_result(self, success: bool) -> None:
        with self._lock:
            if success:
                self._downloaded_count += 1
            else:
                self._failed_count += 1

    def set_pending_count(self, count: int) -> None:
        with self._lock:
            self._pending_count = count

    def set_ups(self, ups: List[tuple]) -> None:
        with self._lock:
            self._ups = list(ups)

    # ------------------------------------------------------------ snapshot --
    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                paused=self._paused,
                stopped=self._stopped,
                current_up=self._current_up,
                scan_status=self._scan_status,
                scan_active=self._scan_active,
                scan_page=self._scan_page,
                scan_items=self._scan_items,
                scan_next_page=self._scan_next_page,
                new_count=self._new_count,
                existing_count=self._existing_count,
                filtered_count=self._filtered_count,
                progress=DownloadProgress(
                    bvid=self._progress.bvid,
                    title=self._progress.title,
                    downloaded=self._progress.downloaded,
                    total=self._progress.total,
                    speed=self._progress.speed,
                    status=self._progress.status,
                ),
                downloaded_count=self._downloaded_count,
                failed_count=self._failed_count,
                pending_count=self._pending_count,
                rate_mbps=self.limiter.rate / (1024 * 1024),
                logs=list(self._logs),
                ups=list(self._ups),
            )
