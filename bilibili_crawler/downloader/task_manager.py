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
from ..options import DownloadOptions
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
        self._mid: Optional[int] = None
        self._options = DownloadOptions()

    # -------------------------------------------------------------- control --
    def set_mid(self, mid: Optional[int]) -> None:
        """Restrict downloads to a single UP (None = all UPs)."""
        self._mid = mid

    def set_options(self, options: DownloadOptions) -> None:
        """Set download options (quality / media type) for this run."""
        self._options = options

    def start(self) -> None:
        self._stop_event.clear()
        clear_cancel = getattr(self.downloader, "clear_cancel", None)
        if clear_cancel:
            clear_cancel()
        self._workers = [w for w in self._workers if w.is_alive()]
        for _ in range(self.concurrency):
            worker = threading.Thread(
                target=self._worker_loop, name="download-worker", daemon=True
            )
            worker.start()
            self._workers.append(worker)

    def stop(self) -> None:
        self._stop_event.set()
        cancel = getattr(self.downloader, "cancel", None)
        if cancel:
            cancel()

    def join(self, timeout: Optional[float] = None) -> None:
        for worker in self._workers:
            worker.join(timeout)
        self._workers = [w for w in self._workers if w.is_alive()]

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
            # Atomically claim one PENDING video (flips it to DOWNLOADING).
            video = self.repo.claim_next_pending(self._mid, self._options.media_type)
            if video is None:
                time.sleep(1.0)
                continue
            self._process(video)

    def _process(self, video) -> None:
        bvid = video.bvid
        up = self.repo.get_up(video.mid)
        up_dir = up.name if (up and up.name) else str(video.mid)

        # ``claim_next_pending`` already marked this video DOWNLOADING.
        self.state.set_progress(
            bvid=bvid, title=video.title, downloaded=0, total=-1, speed="", status="starting"
        )

        try:
            # The downloader needs a full VideoDetail (cid comes from the view API).
            from ..bilibili.video import get_video_detail

            detail = get_video_detail(self.downloader.client, bvid)
            if not detail.title and video.title:
                detail.title = video.title
            # 轻量扫描阶段 description 为空；下载时已拿到 view 详情，回写一次。
            if detail.description and not video.description:
                video.description = detail.description
                self.repo.update_video_meta(video)

            def on_progress(downloaded: int, total: int, speed: str) -> None:
                self.state.set_progress(
                    bvid=bvid, downloaded=downloaded, total=total, speed=speed, status="downloading"
                )

            path = self.downloader.download(
                detail,
                up_dir,
                self.state.limiter,
                progress=on_progress,
                media_type=self._options.media_type,
                qn=self._options.qn,
            )
            self.repo.update_download_status(
                bvid, DownloadStatus.DOWNLOADED, path=str(path),
                media_type=self._options.media_type,
            )
            self.state.add_download_result(True)
            self.state.log(f"下载成功: {video.title} ({bvid})")
        except (DownloadError, Exception) as exc:  # noqa: BLE001
            message = str(exc)[:500]
            if self._stop_event.is_set() or self.state.stopped:
                # A user cancellation is not a failed download; leave it in
                # the queue so a later Start can resume it.
                self.repo.set_pending(bvid, self._options.media_type)
                self.state.log(f"下载已停止: {bvid}")
            else:
                self.repo.update_download_status(
                    bvid, DownloadStatus.FAILED, error=message,
                    media_type=self._options.media_type,
                )
                self.state.add_download_result(False)
                self.state.log(f"下载失败: {bvid} -> {message}")
        finally:
            self.state.clear_progress()
