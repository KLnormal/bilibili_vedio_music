"""UP-main discovery: add an UP and crawl its submissions incrementally.

Crawling follows the plan:

* First crawl of an UP scans all reachable historical submissions (full scan).
* Later crawls scan newest-first and stop early after ``stop_after_existing``
  consecutive videos that already exist in the database (incremental scan).
* ``bvid`` is the unique video identity (SQLite PRIMARY KEY / UNIQUE).
* Discovered videos are built from submission-list data (no per-video view API
  call), then filtered by duration: eligible -> PENDING, ineligible -> FILTERED.
* The download stage enriches metadata (description/cid) on demand.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..bilibili.client import BilibiliClient, BilibiliError
from ..bilibili.user import SubmissionPageError, get_up_profile, iter_submissions
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
        duration_filter_for_mid: Optional[Callable[[int], DurationFilter]] = None,
    ):
        self.client = client
        self.repo = repo
        self.duration_filter = duration_filter
        self.state = state
        self.page_size = page_size
        self.max_pages = max_pages
        self.stop_after_existing = stop_after_existing
        self.request_interval = request_interval
        self.duration_filter_for_mid = duration_filter_for_mid

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
        duration_filter = (
            self.duration_filter_for_mid(mid)
            if self.duration_filter_for_mid is not None
            else self.duration_filter
        )
        first_crawl = up.first_crawl_time is None
        resume_scan = bool(up.scan_incomplete)
        start_page = up.scan_next_page if resume_scan else 1
        last_page = start_page - 1
        last_page_count = None

        # Refresh profile on every crawl (name/face/sign may change).
        profile = get_up_profile(self.client, mid)
        up.name = profile.name or up.name
        up.face = profile.face or up.face
        up.description = profile.sign or up.description
        self.repo.upsert_up(up)

        self.state.reset_scan_stats()
        self.state.set_scan(up.name or str(mid), "获取投稿列表...")

        consecutive_existing = 0
        stopped_early = False
        try:
            def page_done(page: int, _count: int, next_page: int) -> None:
                nonlocal last_page, last_page_count
                last_page = page
                last_page_count = _count
                self.repo.set_scan_progress(mid, next_page, True)
                self.state.set_scan_progress(page, _count, next_page)

            for item in iter_submissions(
                self.client, mid, page_size=self.page_size, max_pages=self.max_pages,
                start_page=start_page, page_callback=page_done,
            ):
                if self.state.stopped:
                    stopped_early = True
                    break
                if not item.bvid:
                    continue

                if self.repo.video_exists(item.bvid):
                    stats.existing += 1
                    self.state.add_scan_stats(existing=1)
                    # Refresh cheap metadata for known videos.
                    self._touch_existing(item, mid)
                    if up.scan_complete and not resume_scan:
                        consecutive_existing += 1
                        if consecutive_existing >= self.stop_after_existing:
                            self.state.set_scan(
                                up.name or str(mid),
                                f"连续 {self.stop_after_existing} 个历史视频，停止扫描",
                            )
                            break
                    continue

                consecutive_existing = 0
                # 轻量入库：只用投稿列表数据（时长/发布时间），不调 view API，
                # 大幅减少请求、降低风控压力，让翻页能深入获取全部历史投稿。
                video = build_video(item, mid)

                self._save_new(video, duration_filter)
                stats.new += 1
                if duration_filter.is_eligible(video.duration):
                    stats.eligible += 1
                else:
                    stats.filtered += 1
                    self.state.add_scan_stats(filtered=1)
            bounded = self.max_pages is not None and last_page >= self.max_pages
            if stopped_early:
                self.repo.set_scan_progress(mid, max(1, last_page), True, complete=False)
                self.state.finish_scan("扫描已停止，可继续续扫")
            elif bounded and (last_page_count or 0) >= self.page_size:
                self.repo.set_scan_progress(mid, last_page + 1, True, complete=False)
                self.state.finish_scan(f"已达到扫描上限：第 {last_page} 页")
            else:
                self.repo.set_scan_progress(mid, 1, False, complete=True)
                self.state.finish_scan("扫描完成")
        except SubmissionPageError as exc:
            # Keep the exact failed page so a later scan can continue instead
            # of restarting at page 1 and hitting the incremental short-circuit.
            self.repo.set_scan_progress(mid, exc.page, True, complete=False)
            logger.warning("submission scan stopped at page %s for mid %s: %s", exc.page, mid, exc)
            self.state.log(f"投稿扫描在第 {exc.page} 页暂停，可重试继续: {exc.cause}")
            self.state.finish_scan(f"扫描暂停：第 {exc.page} 页")
        except BilibiliError as exc:
            logger.warning("submission scan failed for mid %s: %s", mid, exc)
            self.state.log(f"投稿扫描中断 {mid}: {exc}")
            self.state.finish_scan("扫描失败")
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

    def _save_new(self, video: Video, duration_filter: Optional[DurationFilter] = None) -> None:
        duration_filter = duration_filter or self.duration_filter
        if duration_filter.is_eligible(video.duration):
            video.download_status = DownloadStatus.PENDING
        else:
            video.download_status = DownloadStatus.FILTERED
            video.filter_reason = "duration_out_of_range"
        self.repo.insert_video(video)
