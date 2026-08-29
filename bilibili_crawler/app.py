"""Application wiring: builds every subsystem and exposes high-level actions.

Both the CLI commands and the TUI use this class, so the core logic runs
identically with or without the interactive terminal (plan principle #4).
"""
from __future__ import annotations

import logging
import os
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
from .database.models import DownloadStatus, Up, UpFilterSettings
from .database.repository import Repository
from .download_directory import (
    build_media_file_index,
    ensure_writable_root,
    normalize_download_root,
)
from .downloader.downloader import VideoDownloader
from .downloader.limiter import RateLimiter, mbps_to_bps
from .downloader.task_manager import DownloadTaskManager
from .filter.decision import DecisionEngine
from .filter.duration_filter import DurationFilter
from .options import DownloadOptions, parse_date
from .state import RuntimeState

logger = logging.getLogger(__name__)


class App:
    """Root object holding configuration and all subsystems."""

    def __init__(self, config_path: Optional[str] = None, configure_logging: bool = True):
        self.config_path = Path(config_path).resolve() if config_path else Path.cwd() / "config.yaml"
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
            duration_filter_for_mid=self.get_duration_filter,
        )

        # --- downloader -----------------------------------------------------
        self.downloader = VideoDownloader(
            self.client,
            save_root=str(ensure_writable_root(dl["save_root"])),
            qn=dl["qn"],
            prefer_dash=dl["prefer_dash"],
            ffmpeg_path=dl["ffmpeg_path"],
            user_agent=dl["user_agent"],
            referer=dl["referer"],
        )
        self.download_root = normalize_download_root(dl["save_root"])
        self._download_root_lock = threading.RLock()
        # Reconcile the configured root before workers are created.  This also
        # upgrades old databases whose media paths point at a previous root.
        self.sync_download_directory()
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
                self.sync_download_directory(mid=mid)
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
            self.sync_download_directory()
            return stats

    def reset_failed(self, mid: Optional[int] = None, media_type: str = "video") -> int:
        n = self.repo.reset_failed(mid, media_type)
        self.state.log(f"已重置 {n} 个失败任务为 PENDING")
        return n

    # ------------------------------------------------------- v0.2 commands --
    def status(self, mid: Optional[int] = None, media_type: str = "video") -> dict:
        """Return status statistics for one UP or the whole database."""
        counts = self.repo.count_by_status(mid, media_type)
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

    def check_files(self, mid: Optional[int] = None, media_type: str = "video") -> dict:
        """Synchronize the selected media type with the active root."""
        return self.sync_download_directory(mid=mid, media_type=media_type)

    def sync_download_directory(
        self,
        mid: Optional[int] = None,
        media_type: Optional[str] = None,
        *,
        root_changed: Optional[bool] = None,
    ) -> dict:
        """Reconcile database media state with files below the active root."""
        if media_type is not None and media_type not in ("video", "audio"):
            raise ValueError("media_type must be 'video' or 'audio'")
        with self._download_root_lock:
            root = ensure_writable_root(self.download_root)
            previous = self.repo.get_meta("active_download_root")
            changed = (
                bool(root_changed)
                if root_changed is not None
                else previous is None or os.path.normcase(str(root)) != os.path.normcase(str(previous))
            )
            index = build_media_file_index(root)
            return self.repo.reconcile_media_files(
                index,
                active_root=str(root),
                root_changed=changed,
                mid=mid,
                media_type=media_type,
            )

    def switch_download_root(self, new_root: str | Path) -> dict:
        """Validate, scan and activate a new download root atomically."""
        target = ensure_writable_root(new_root)
        with self._download_root_lock:
            current = normalize_download_root(self.download_root)
            changed = os.path.normcase(str(target)) != os.path.normcase(str(current))
            if not changed:
                return self.sync_download_directory(root_changed=False)
            index = build_media_file_index(target)
            result = self.repo.reconcile_media_files(
                index, active_root=str(target), root_changed=True
            )
            self.download_root = target
            self.downloader.save_root = target
            # Keep runtime configuration in sync for callers that switch the
            # root directly (the desktop controller persists it separately).
            self.config.setdefault("download", {})["save_root"] = str(target)
            return result

    def verify_current_download_files(
        self, mid: Optional[int] = None, media_type: Optional[str] = None
    ) -> dict:
        """Lightweight preflight check for recorded files in the active root."""
        if media_type is not None and media_type not in ("video", "audio"):
            raise ValueError("media_type must be 'video' or 'audio'")
        root = normalize_download_root(self.download_root)
        types = (media_type,) if media_type else ("video", "audio")
        missing: List[str] = []
        checked = 0
        for kind in types:
            for video in self.repo.list_downloaded(mid, kind):
                checked += 1
                try:
                    path = Path(video.download_path).expanduser().resolve(strict=False)
                    valid = path.is_file() and path.stat().st_size > 0 and path.is_relative_to(root)
                except OSError:
                    valid = False
                if not valid:
                    missing.append(video.bvid)
                    self.repo.update_download_status(
                        video.bvid, DownloadStatus.PENDING, media_type=kind
                    )
        return {"checked": checked, "missing": missing, "root": str(root)}

    def download_bv(self, bvids: List[str], options: Optional[DownloadOptions] = None) -> List[tuple]:
        """Directly download videos by bvid, bypassing all UP rules.

        Explicitly specifying a BV means the user explicitly wants it downloaded
        (v0.2 section 9): no add, no scan, no UP blacklist, no duration filter.
        """
        options = options or DownloadOptions()
        options.validate()
        self._ensure_current_download_root()
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
                if self.repo.video_exists(bvid):
                    self.repo.update_download_status(
                        bvid, DownloadStatus.DOWNLOADED, path=str(path),
                        media_type=options.media_type,
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

    def add_allowlist(self, mid: int, keyword: str) -> bool:
        ok = self.repo.add_allowlist(mid, keyword)
        if ok:
            self.state.log(f"UP {mid} 指定下载名单添加: {keyword}")
        return ok

    def remove_allowlist(self, mid: int, keyword: str) -> bool:
        ok = self.repo.remove_allowlist(mid, keyword)
        if ok:
            self.state.log(f"UP {mid} 指定下载名单移除: {keyword}")
        return ok

    def list_allowlist(self, mid: int) -> List[str]:
        return self.repo.list_allowlist(mid)

    def get_up_filter_settings(self, mid: int) -> UpFilterSettings:
        return self.repo.get_up_filter_settings(mid)

    def get_duration_filter(self, mid: int) -> DurationFilter:
        settings = self.repo.get_up_filter_settings(mid)
        return DurationFilter(
            settings.min_duration if settings.min_duration is not None else self.config["filter"]["min_duration"],
            settings.max_duration if settings.max_duration is not None else self.config["filter"]["max_duration"],
        )

    def save_up_filter_settings(self, settings: UpFilterSettings) -> None:
        if settings.min_duration is not None and settings.max_duration is not None:
            DurationFilter(settings.min_duration, settings.max_duration)
        self.repo.upsert_up_filter_settings(settings)
        self.state.log(f"UP {settings.mid} 筛选规则已保存")

    def prepare_download(self, mid: Optional[int], options: DownloadOptions) -> Dict[str, int]:
        """Re-evaluate rules for PENDING videos, applying CLI overrides.

        READY videos stay PENDING; FILTERED videos are marked FILTERED with an
        explainable ``filter_reason`` (blacklist / duration). The downloader
        then only consumes READY (PENDING) videos. ``mid=None`` applies to all
        enabled UPs, each with its own blacklist.
        """
        options.validate()
        self._ensure_current_download_root()
        self.verify_current_download_files(mid, options.media_type)
        mids = [mid] if mid is not None else [u.mid for u in self.repo.list_ups(enabled_only=True)]

        counts: Dict[str, int] = {"ready": 0, "filtered": 0}
        for m in mids:
            settings = self.repo.get_up_filter_settings(m)
            min_d = options.min_duration if options.min_duration is not None else (settings.min_duration if settings.min_duration is not None else self.config["filter"]["min_duration"])
            max_d = options.max_duration if options.max_duration is not None else (settings.max_duration if settings.max_duration is not None else self.config["filter"]["max_duration"])
            min_date = options.min_datetime if options.date_filter_active else parse_date(settings.min_date)
            max_date = options.max_datetime if options.date_filter_active else parse_date(settings.max_date)
            filter_config = self.config.get("filter", {})
            blacklist = self.repo.list_blacklist(m) if filter_config.get("blacklist_enabled", True) else []
            allowlist = self.repo.list_allowlist(m) if filter_config.get("allowlist_enabled", False) else []
            engine = DecisionEngine(
                min_d, max_d, blacklist,
                min_date=min_date, max_date=max_date,
                allowlist_keywords=allowlist,
            )
            for video in self.repo.list_videos(m, options.media_type):
                if video.download_status not in (DownloadStatus.PENDING, DownloadStatus.FILTERED):
                    continue
                decision = engine.decide(video)
                if decision.decision == "READY":
                    if video.download_status is DownloadStatus.FILTERED:
                        self.repo.set_pending(video.bvid, options.media_type)
                    counts["ready"] += 1
                elif decision.decision == "FILTERED":
                    self.repo.set_filtered(video.bvid, decision.reason, options.media_type)
                    counts["filtered"] += 1
        return counts

    def preview(self, mid: Optional[int], options: DownloadOptions) -> dict:
        """Evaluate rules without downloading (dry-run).

        Returns decision statistics plus the full (video, decision) list so the
        CLI/TUI can show both aggregate counts and per-video explanations.
        """
        options.validate()
        self._ensure_current_download_root()
        self.verify_current_download_files(mid, options.media_type)
        mids = [mid] if mid is not None else [u.mid for u in self.repo.list_ups(enabled_only=True)]

        decisions: List[tuple] = []
        for m in mids:
            settings = self.repo.get_up_filter_settings(m)
            min_d = options.min_duration if options.min_duration is not None else (settings.min_duration if settings.min_duration is not None else self.config["filter"]["min_duration"])
            max_d = options.max_duration if options.max_duration is not None else (settings.max_duration if settings.max_duration is not None else self.config["filter"]["max_duration"])
            min_date = options.min_datetime if options.date_filter_active else parse_date(settings.min_date)
            max_date = options.max_datetime if options.date_filter_active else parse_date(settings.max_date)
            filter_config = self.config.get("filter", {})
            blacklist = self.repo.list_blacklist(m) if filter_config.get("blacklist_enabled", True) else []
            allowlist = self.repo.list_allowlist(m) if filter_config.get("allowlist_enabled", False) else []
            engine = DecisionEngine(
                min_d, max_d, blacklist,
                min_date=min_date, max_date=max_date,
                allowlist_keywords=allowlist,
            )
            for video in self.repo.list_videos(m, options.media_type):
                decisions.append((video, engine.decide(video)))

        stats = Counter(d.decision for _, d in decisions)
        return {
            "stats": dict(stats),
            "decisions": decisions,
            "min_duration": options.min_duration if options.min_duration is not None else self.config["filter"]["min_duration"],
            "max_duration": options.max_duration if options.max_duration is not None else self.config["filter"]["max_duration"],
        }

    def set_limit(self, mbps: float) -> float:
        self.limiter.set_rate(mbps_to_bps(mbps))
        self.state.log(f"下载限速已调整为 {mbps} MB/s")
        return mbps

    def apply_runtime_config(self, config: dict) -> None:
        """Apply settings that can safely change while the desktop is open."""
        new_root = ensure_writable_root(config["download"]["save_root"])
        f = config["filter"]
        new_duration_filter = DurationFilter(f["min_duration"], f["max_duration"])
        dl = config["download"]
        new_limit = float(dl["max_speed_mbps"])
        new_concurrency = max(1, int(dl["concurrency"]))
        if os.path.normcase(str(new_root)) != os.path.normcase(str(self.download_root)):
            self.switch_download_root(new_root)
        self.config = config
        self.duration_filter = new_duration_filter
        self.crawler.duration_filter = self.duration_filter
        self.crawler.request_interval = float(config["crawler"].get("request_interval", 0.3))
        self.set_limit(new_limit)
        self.downloader.save_root = self.download_root
        self.downloader.qn = int(dl["qn"])
        self.downloader.prefer_dash = bool(dl["prefer_dash"])
        self.downloader.ffmpeg_path = str(dl.get("ffmpeg_path", "")) or self.downloader._find_ffmpeg()
        self.download_manager.concurrency = new_concurrency

    def _ensure_current_download_root(self) -> None:
        root = normalize_download_root(self.config["download"]["save_root"])
        if os.path.normcase(str(root)) != os.path.normcase(str(self.download_root)):
            self.switch_download_root(root)

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
