"""Plain data classes for UP and Video records.

The SQLite database is the single source of truth. These models are thin
containers used between the repository and the rest of the system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DownloadStatus(str, Enum):
    """Download state machine.

    ``FILTERED`` is not a download state: it marks a video that was discovered
    but rejected by the download rules (duration / blacklist) with a
    ``filter_reason`` explaining why. Removing the rule lets it re-enter the
    pipeline (v0.2 section 3.6).
    """

    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    FAILED = "FAILED"
    FILTERED = "FILTERED"


@dataclass
class Up:
    mid: int
    name: str = ""
    face: str = ""
    description: str = ""
    first_crawl_time: Optional[str] = None
    last_crawl_time: Optional[str] = None
    enabled: bool = True
    save_path: str = ""
    scan_next_page: int = 1
    scan_incomplete: bool = False
    scan_complete: bool = False


@dataclass
class UpFilterSettings:
    """Per-UP filtering defaults; None means inherit global config."""

    mid: int
    min_duration: Optional[int] = None
    max_duration: Optional[int] = None
    min_date: Optional[str] = None
    max_date: Optional[str] = None


@dataclass
class Video:
    bvid: str
    mid: int
    duration: Optional[int] = None
    created: Optional[int] = None    # 发布时间戳（Unix 秒），日期筛选用
    title: str = ""
    description: str = ""
    pic: str = ""
    url: str = ""
    update_time: Optional[str] = None
    download_status: DownloadStatus = DownloadStatus.PENDING
    download_path: str = ""
    download_time: Optional[str] = None
    download_error: str = ""
    filter_reason: str = ""

    def to_row(self) -> dict:
        return {
            "bvid": self.bvid,
            "mid": self.mid,
            "duration": self.duration,
            "created": self.created,
            "title": self.title,
            "description": self.description,
            "pic": self.pic,
            "url": self.url,
            "update_time": self.update_time,
            "download_status": self.download_status.value,
            "download_path": self.download_path,
            "download_time": self.download_time,
            "download_error": self.download_error,
            "filter_reason": self.filter_reason,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Video":
        return cls(
            bvid=row["bvid"],
            mid=row["mid"],
            duration=row["duration"],
            created=row.get("created"),
            title=row["title"] or "",
            description=row["description"] or "",
            pic=row["pic"] or "",
            url=row["url"] or "",
            update_time=row["update_time"],
            download_status=DownloadStatus(row["download_status"] or "PENDING"),
            download_path=row["download_path"] or "",
            download_time=row["download_time"],
            download_error=row["download_error"] or "",
            filter_reason=row["filter_reason"] if "filter_reason" in row else "",
        )


@dataclass
class MediaDownload:
    """Download state for one video representation (video or audio)."""

    bvid: str
    media_type: str
    status: DownloadStatus = DownloadStatus.PENDING
    download_path: str = ""
    download_time: Optional[str] = None
    download_error: str = ""
    filter_reason: str = ""
