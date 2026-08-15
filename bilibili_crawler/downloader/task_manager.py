"""Download task manager (plan section 7, state machine).

Implements:

    PENDING -> DOWNLOADING -> DOWNLOADED
    PENDING -> DOWNLOADING -> FAILED -> (re-entered later) -> DOWNLOADING -> ...

A fixed pool of worker threads consumes PENDING rows from the database. Each
video's state transition is written back to SQLite so that "discovered" and
"downloaded" stay independent, and a FAILED video is retried on the next run.
"""
from __future__ import annotations

import threading
import time
from typing import List, Optional

from ..database.models import DownloadStatus
from ..database.repository import Repository
from ..state import RuntimeState
from .downloader import DownloadError, VideoDownloader


class DownloadTaskManager:
    def __init__(
        self,
        repo: Repository,
        downloader: VideoDownloader,
        state: RuntimeState,
        concurrency: int = 2,
    ):
        self.repo = repo
        self.downloader = downloader
        self.state = state
        self.concurrency = max(1, concurrency)
        self._workers: List[threading.Thread] = []
        self._stop_event = threading.Event()

    # -------------------------------------------------------------- control --
    def start(self) -> None:
        self._stop_event.clear()
        for _ in range(self.concurrency):
            worker = threading.Thread(
                target=self._worker_loop, name="download-worker", daemon=True
            )
            worker.start()
            self._workers.append(worker)

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        for worker in self._workers:
            worker.join(timeout)

    def _should_continue(self) -> bool:
        if self._stop_event.is_set():
            return False
        if self.state.stopped:
            return False
        while self.state.paused:
            if self._stop_event.is_set() or self.state.stopped:
                return False
            time.sleep(0.2)
        return True

    # ---------------------------------------------------------------- worker --
    def _worker_loop(self) -> None:
        while self._should_continue():
            # Claim one PENDING video at a time (small batch per worker).
            videos = self.repo.list_pending(limit=1)
            if not videos:
                time.sleep(1.0)
                continue
            self._process(videos[0])

    def _process(self, video) -> None:
        bvid = video.bvid
        up = self.repo.get_up(video.mid)
        up_dir = up.name if (up and up.name) else str(video.mid)

        self.repo.update_download_status(bvid, DownloadStatus.DOWNLOADING)
        self.state.set_progress(
            bvid=bvid, title=video.title, downloaded=0, total=-1, speed="", status="starting"
        )

        try:
            # The downloader needs a full VideoDetail (cid comes from the view API).
            from ..bilibili.video import get_video_detail

            detail = get_video_detail(self.downloader.client, bvid)
            if not detail.title and video.title:
                detail.title = video.title

            def on_progress(downloaded: int, total: int, speed: str) -> None:
                self.state.set_progress(
                    bvid=bvid, downloaded=downloaded, total=total, speed=speed, status="downloading"
                )

            path = self.downloader.download(
                detail, up_dir, self.state.limiter, progress=on_progress
            )
            self.repo.update_download_status(
                bvid, DownloadStatus.DOWNLOADED, path=str(path)
            )
            self.state.add_download_result(True)
            self.state.log(f"下载成功: {video.title} ({bvid})")
        except (DownloadError, Exception) as exc:  # noqa: BLE001
            message = str(exc)[:500]
            self.repo.update_download_status(bvid, DownloadStatus.FAILED, error=message)
            self.state.add_download_result(False)
            self.state.log(f"下载失败: {bvid} -> {message}")
        finally:
            self.state.clear_progress()
