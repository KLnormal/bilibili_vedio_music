"""UP-main discovery: add an UP and crawl its submissions incrementally.

Crawling follows the plan:

* First crawl of an UP scans all reachable historical submissions (full scan).
* Later crawls scan newest-first and stop early after ``stop_after_existing``
  consecutive videos that already exist in the database (incremental scan).
* ``bvid`` is the unique video identity (SQLite PRIMARY KEY / UNIQUE).
* Discovered videos are enriched with the ``view`` API (duration/description),
  then filtered by duration: eligible -> PENDING, ineligible -> FILTERED.
* A single video/detail failure never crashes the whole UP task.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..bilibili.client import BilibiliClient, BilibiliError
from ..bilibili.user import get_up_profile, iter_submissions
from ..database.models import DownloadStatus, Up, Video
from ..database.repository import Repository
from ..filter.duration_filter import DurationFilter
from ..state import RuntimeState
from .video_crawler import build_video

logger = logging.getLogger(__name__)


@dataclass
class CrawlStats:
    new: int = 0
    existing: int = 0
    filtered: int = 0
    eligible: int = 0
    failed: int = 0


class UserCrawler:
    def __init__(
        self,
        client: BilibiliClient,
        repo: Repository,
        duration_filter: DurationFilter,
        state: RuntimeState,
        *,
        page_size: int = 30,
        max_pages: Optional[int] = None,
        stop_after_existing: int = 10,
        request_interval: float = 0.3,
    ):
        self.client = client
        self.repo = repo
        self.duration_filter = duration_filter
        self.state = state
        self.page_size = page_size
        self.max_pages = max_pages
        self.stop_after_existing = stop_after_existing
        self.request_interval = request_interval

    # ------------------------------------------------------------------ add --
    def add_up(self, mid: int) -> Up:
        """Resolve an UP's profile and persist the UP record."""
        profile = get_up_profile(self.client, mid)
        up = Up(
            mid=mid,
            name=profile.name,
            face=profile.face,
            description=profile.sign,
        )
        self.repo.upsert_up(up)
        return up

    # ---------------------------------------------------------------- crawl --
    def crawl_up(self, mid: int) -> CrawlStats:
        """Scan an UP's submissions (full or incremental) and update the DB."""
        up = self.repo.get_up(mid)
        if up is None:
            up = self.add_up(mid)

        stats = CrawlStats()
        first_crawl = up.first_crawl_time is None

        # Refresh profile on every crawl (name/face/sign may change).
        profile = get_up_profile(self.client, mid)
        up.name = profile.name or up.name
        up.face = profile.face or up.face
        up.description = profile.sign or up.description
        self.repo.upsert_up(up)

        self.state.reset_scan_stats()
        self.state.set_scan(up.name or str(mid), "获取投稿列表...")

        consecutive_existing = 0
        try:
            for item in iter_submissions(
                self.client, mid, page_size=self.page_size, max_pages=self.max_pages
            ):
                if self.state.stopped:
                    break
                if not item.bvid:
                    continue

                if self.repo.video_exists(item.bvid):
                    stats.existing += 1
                    self.state.add_scan_stats(existing=1)
                    # Refresh cheap metadata for known videos.
                    self._touch_existing(item, mid)
                    if not first_crawl:
                        consecutive_existing += 1
                        if consecutive_existing >= self.stop_after_existing:
                            self.state.set_scan(
                                up.name or str(mid),
                                f"连续 {self.stop_after_existing} 个历史视频，停止扫描",
                            )
                            break
                    continue

                consecutive_existing = 0
                try:
                    video = build_video(
                        self.client, item, mid, request_interval=self.request_interval
                    )
                except BilibiliError as exc:
                    logger.warning("detail fetch failed for %s: %s", item.bvid, exc)
                    stats.failed += 1
                    self.state.log(f"元数据获取失败 {item.bvid}: {exc}")
                    continue

                self._save_new(video)
                stats.new += 1
                if self.duration_filter.is_eligible(video.duration):
                    stats.eligible += 1
                else:
                    stats.filtered += 1
                    self.state.add_scan_stats(filtered=1)
        except BilibiliError as exc:
            logger.warning("submission scan failed for mid %s: %s", mid, exc)
            self.state.log(f"投稿扫描中断 {mid}: {exc}")
        finally:
            self.repo.touch_up_crawl(mid, first=first_crawl)

        self.state.add_scan_stats(new=stats.new)
        return stats

    def _touch_existing(self, item, mid: int) -> None:
        """Update lightweight metadata for an already-known video."""
        video = self.repo.get_video(item.bvid)
        if video is None:
            return
        changed = False
        if not video.title and item.title:
            video.title = item.title
            changed = True
        if not video.pic and item.pic:
            video.pic = item.pic
            changed = True
        if changed:
            from ..crawler.video_crawler import _now_iso

            video.update_time = _now_iso()
            self.repo.update_video_meta(video)

    def _save_new(self, video: Video) -> None:
        if self.duration_filter.is_eligible(video.duration):
            video.download_status = DownloadStatus.PENDING
        else:
            video.download_status = DownloadStatus.FILTERED
            video.filter_reason = "duration_out_of_range"
        self.repo.insert_video(video)
