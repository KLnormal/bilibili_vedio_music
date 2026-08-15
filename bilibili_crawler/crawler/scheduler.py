"""High-level scheduler: scan UP mains, then feed the download queue.

The scheduler runs in a background thread and performs:

1. For every enabled UP: incremental discovery (see ``UserCrawler``).
2. Refresh the pending/downloaded/failed counters exposed to the TUI.
3. Download workers (``DownloadTaskManager``) consume PENDING videos.
4. After a configurable interval, re-scan for new submissions (incremental).

It owns the "monitor -> discover -> filter -> download -> re-check" loop from
the plan's final goal (section 19).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..database.repository import Repository
from ..downloader.task_manager import DownloadTaskManager
from ..state import RuntimeState
from .user_crawler import UserCrawler

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        crawler: UserCrawler,
        repo: Repository,
        state: RuntimeState,
        download_manager: DownloadTaskManager,
        *,
        scan_lock: Optional[threading.Lock] = None,
        rescan_interval: float = 3600.0,
    ):
        self.crawler = crawler
        self.repo = repo
        self.state = state
        self.download_manager = download_manager
        self.scan_lock = scan_lock or threading.Lock()
        self.rescan_interval = rescan_interval

    def scan_all(self) -> None:
        """Scan every enabled UP once (full or incremental)."""
        with self.scan_lock:
            ups = self.repo.list_ups(enabled_only=True)
            self.state.log(f"开始扫描 {len(ups)} 个 UP 主")
            for up in ups:
                if self.state.stopped:
                    break
                self.state.log(f"扫描 UP: {up.name or up.mid} (mid={up.mid})")
                try:
                    self.crawler.crawl_up(up.mid)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("crawl failed for mid %s", up.mid)
                    self.state.log(f"UP 扫描异常 {up.mid}: {exc}")
            self._refresh_pending()
            self.state.set_scan("", "扫描完成")

    def _refresh_pending(self) -> None:
        pending = len(self.repo.list_pending(limit=1000))
        self.state.set_pending_count(pending)

    def run(self, once: bool = False) -> None:
        """Run the scan->download loop until stopped (or once)."""
        # Download workers consume the queue concurrently with scanning.
        self.download_manager.start()
        try:
            while not self.state.stopped:
                if not self.state.paused:
                    self.scan_all()
                if once:
                    break
                self._refresh_pending()
                # Sleep in small slices so pause/stop respond promptly.
                waited = 0.0
                while waited < self.rescan_interval:
                    if self.state.stopped:
                        return
                    time.sleep(0.5)
                    waited += 0.5
        finally:
            self.download_manager.stop()

    def stop(self) -> None:
        self.state.request_stop()
        self.download_manager.stop()
