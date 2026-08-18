"""Application wiring: builds every subsystem and exposes high-level actions.

Both the CLI commands and the TUI use this class, so the core logic runs
identically with or without the interactive terminal (plan principle #4).
"""
from __future__ import annotations

import logging
import shutil
import threading
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from .bilibili.auth import LoginManager
from .bilibili.client import BilibiliClient
from .bilibili.video import get_video_detail
from .config.configuration import load_config, resolve_cookie_path, resolve_data_path
from .crawler.scheduler import Scheduler
from .crawler.user_crawler import CrawlStats, UserCrawler
from .database.database import Database
from .database.models import DownloadStatus, Up
from .database.repository import Repository
from .downloader.downloader import VideoDownloader
from .downloader.limiter import RateLimiter, mbps_to_bps
from .downloader.task_manager import DownloadTaskManager
from .filter.decision import DecisionEngine
from .filter.duration_filter import DurationFilter
from .options import DownloadOptions
from .state import RuntimeState

logger = logging.getLogger(__name__)


class App:
    """Root object holding configuration and all subsystems."""

    def __init__(self, config_path: Optional[str] = None, configure_logging: bool = True):
        self.config = load_config(config_path)
        if configure_logging:
            self._setup_logging()

        # --- persistence ---------------------------------------------------
        self.db = Database(resolve_data_path(self.config))
        self.repo = Repository(self.db)
        # Serializes scans so a manual "refresh" never overlaps the scheduler.
        self.scan_lock = threading.Lock()

        # --- bilibili HTTP client -----------------------------------------
        dl = self.config["download"]
        self.client = BilibiliClient(
            user_agent=dl["user_agent"],
            referer=dl["referer"],
            timeout=self.config["crawler"]["request_timeout"],
            retries=self.config["crawler"]["retries"],
            retry_backoff=self.config["crawler"]["retry_backoff"],
        )
        self.login = LoginManager(
            self.client, resolve_cookie_path(self.config)
        )
        self.login.load_cookies()

        # --- shared runtime state -----------------------------------------
        self.limiter = RateLimiter(mbps_to_bps(dl["max_speed_mbps"]))
        self.state = RuntimeState(self.limiter)

        # --- filter --------------------------------------------------------
        f = self.config["filter"]
        self.duration_filter = DurationFilter(f["min_duration"], f["max_duration"])

        # --- crawler --------------------------------------------------------
        c = self.config["crawler"]
        self.crawler = UserCrawler(
            self.client,
            self.repo,
            self.duration_filter,
            self.state,
            page_size=c["page_size"],
            max_pages=c.get("max_pages", 0) or None,
            stop_after_existing=c["stop_after_existing"],
            request_interval=c["request_interval"],
        )

        # --- downloader -----------------------------------------------------
        self.downloader = VideoDownloader(
            self.client,
            save_root=dl["save_root"],
            qn=dl["qn"],
            prefer_dash=dl["prefer_dash"],
            ffmpeg_path=dl["ffmpeg_path"],
            user_agent=dl["user_agent"],
            referer=dl["referer"],
        )
        self.download_manager = DownloadTaskManager(
            self.repo, self.downloader, self.state, concurrency=dl["concurrency"]
        )

        # --- scheduler -------------------------------------------------------
        self.scheduler = Scheduler(
            self.crawler,
            self.repo,
            self.state,
            self.download_manager,
            scan_lock=self.scan_lock,
            rescan_interval=3600.0,
        )

        # Recover orphaned DOWNLOADING tasks left behind by a previous crashed
        # run (no download workers are running at this point).
        self.repo.recover_orphan_downloading()

    def _setup_logging(self) -> None:
        cfg = self.config["logging"]
        level = getattr(logging, cfg.get("level", "INFO").upper(), logging.INFO)
        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if cfg.get("file"):
            try:
                handlers.append(logging.FileHandler(cfg["file"], encoding="utf-8"))
            except OSError:
                pass
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=handlers,
        )

    # ------------------------------------------------------------- actions --
    def add_up(self, mid: int) -> Up:
        up = self.crawler.add_up(mid)
        self.state.log(f"已添加 UP: {up.name or mid} (mid={mid})")
        return up

    def remove_up(self, mid: int) -> bool:
        removed = self.repo.delete_up(mid)
        if removed:
            self.state.log(f"已删除 UP: mid={mid}")
        return removed

    def list_ups(self) -> List[Up]:
        return self.repo.list_ups()

    def scan(self, mid: Optional[int] = None) -> CrawlStats:
        """Scan all enabled UPs (or a single mid). Serialized by ``scan_lock``.

        After crawling, a file-consistency check runs so DOWNLOADED videos whose
        file is gone are detected (MISSING -> PENDING).
        """
        with self.scan_lock:
            if mid is not None:
                stats = self.crawler.crawl_up(mid)
                self.check_files(mid)
                return stats
            stats = CrawlStats()
            for up in self.repo.list_ups(enabled_only=True):
                if self.state.stopped:
                    break
                s = self.crawler.crawl_up(up.mid)
                stats.new += s.new
                stats.existing += s.existing
                stats.filtered += s.filtered
                stats.eligible += s.eligible
                stats.failed += s.failed
            self.check_files(None)
            return stats

    def reset_failed(self, mid: Optional[int] = None) -> int:
        n = self.repo.reset_failed(mid)
        self.state.log(f"已重置 {n} 个失败任务为 PENDING")
        return n

    # ------------------------------------------------------- v0.2 commands --
    def status(self, mid: Optional[int] = None) -> dict:
        """Return status statistics for one UP or the whole database."""
        counts = self.repo.count_by_status(mid)
        result: dict = {
            "mid": mid,
            "total": sum(counts.values()),
            "counts": counts,
        }
        if mid is not None:
            up = self.repo.get_up(mid)
            if up:
                result["up"] = {
                    "name": up.name,
                    "last_crawl_time": up.last_crawl_time,
                }
        return result

    def check_files(self, mid: Optional[int] = None) -> dict:
        """Check DOWNLOADED videos against the filesystem.

        A video recorded as DOWNLOADED whose file no longer exists is marked
        MISSING and reset to PENDING so it can be re-downloaded (plan v0.2
        section 5). The database stays the source of truth; the filesystem is
        only the existence verifier.
        """
        videos = self.repo.list_downloaded(mid)
        missing: List[str] = []
        for v in videos:
            path = Path(v.download_path) if v.download_path else None
            if path is None or not path.is_file():
                missing.append(v.bvid)
                self.repo.update_download_status(v.bvid, DownloadStatus.PENDING)
        return {"checked": len(videos), "missing": missing}

    def download_bv(self, bvids: List[str], options: Optional[DownloadOptions] = None) -> List[tuple]:
        """Directly download videos by bvid, bypassing all UP rules.

        Explicitly specifying a BV means the user explicitly wants it downloaded
        (v0.2 section 9): no add, no scan, no UP blacklist, no duration filter.
        """
        options = options or DownloadOptions()
        results: List[tuple] = []
        for bvid in bvids:
            try:
                detail = get_video_detail(self.client, bvid)
                up = self.repo.get_up(detail.mid) if detail.mid else None
                if detail.mid:
                    up_dir = (up.name if up and up.name else str(detail.mid))
                else:
                    up_dir = "direct"
                path = self.downloader.download(
                    detail, up_dir, self.limiter,
                    media_type=options.media_type, qn=options.qn,
                )
                results.append((bvid, True, str(path)))
            except Exception as exc:  # noqa: BLE001
                results.append((bvid, False, str(exc)))
        return results

    def add_blacklist(self, mid: int, keyword: str) -> bool:
        ok = self.repo.add_blacklist(mid, keyword)
        if ok:
            self.state.log(f"UP {mid} 黑名单添加: {keyword}")
        return ok

    def remove_blacklist(self, mid: int, keyword: str) -> bool:
        ok = self.repo.remove_blacklist(mid, keyword)
        if ok:
            self.state.log(f"UP {mid} 黑名单移除: {keyword}")
        return ok

    def list_blacklist(self, mid: int) -> List[str]:
        return self.repo.list_blacklist(mid)

    def prepare_download(self, mid: Optional[int], options: DownloadOptions) -> Dict[str, int]:
        """Re-evaluate rules for PENDING videos, applying CLI overrides.

        READY videos stay PENDING; FILTERED videos are marked FILTERED with an
        explainable ``filter_reason`` (blacklist / duration). The downloader
        then only consumes READY (PENDING) videos. ``mid=None`` applies to all
        enabled UPs, each with its own blacklist.
        """
        min_d = (
            options.min_duration
            if options.min_duration is not None
            else self.config["filter"]["min_duration"]
        )
        max_d = (
            options.max_duration
            if options.max_duration is not None
            else self.config["filter"]["max_duration"]
        )
        mids = [mid] if mid is not None else [u.mid for u in self.repo.list_ups(enabled_only=True)]

        counts: Dict[str, int] = {"ready": 0, "filtered": 0}
        for m in mids:
            engine = DecisionEngine(min_d, max_d, self.repo.list_blacklist(m))
            for video in self.repo.list_pending(m):
                decision = engine.decide(video)
                if decision.decision == "READY":
                    counts["ready"] += 1
                elif decision.decision == "FILTERED":
                    self.repo.set_filtered(video.bvid, decision.reason)
                    counts["filtered"] += 1
        return counts

    def preview(self, mid: Optional[int], options: DownloadOptions) -> dict:
        """Evaluate rules without downloading (dry-run).

        Returns decision statistics plus the full (video, decision) list so the
        CLI/TUI can show both aggregate counts and per-video explanations.
        """
        min_d = (
            options.min_duration
            if options.min_duration is not None
            else self.config["filter"]["min_duration"]
        )
        max_d = (
            options.max_duration
            if options.max_duration is not None
            else self.config["filter"]["max_duration"]
        )
        mids = [mid] if mid is not None else [u.mid for u in self.repo.list_ups(enabled_only=True)]

        decisions: List[tuple] = []
        for m in mids:
            engine = DecisionEngine(min_d, max_d, self.repo.list_blacklist(m))
            for video in self.repo.list_videos(m):
                decisions.append((video, engine.decide(video)))

        stats = Counter(d.decision for _, d in decisions)
        return {
            "stats": dict(stats),
            "decisions": decisions,
            "min_duration": min_d,
            "max_duration": max_d,
        }

    def set_limit(self, mbps: float) -> float:
        self.limiter.set_rate(mbps_to_bps(mbps))
        self.state.log(f"下载限速已调整为 {mbps} MB/s")
        return mbps

    @property
    def has_ffmpeg(self) -> bool:
        return self.downloader.has_ffmpeg

    def close(self) -> None:
        self.scheduler.stop()
        self.download_manager.stop()
        self.db.close()


def check_ffmpeg() -> str:
    """Return the ffmpeg path if available, else empty string."""
    return shutil.which("ffmpeg") or ""
