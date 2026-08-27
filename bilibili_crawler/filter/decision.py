"""Unified rule decision engine (v0.2 Phase 4).

Produces a single, explainable decision per video:

    READY / FILTERED / DOWNLOADED / MISSING / FAILED / DOWNLOADING

with a ``reason`` and a per-rule ``checks`` map. The downloader should only
consume videos decided READY; everything else is explained by the decision
(AGENT_PROMPT_v0.2 section 6). Rules are evaluated in the order:
download/file state -> duration -> date -> blacklist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from ..database.models import DownloadStatus, Video
from .blacklist_filter import blacklist_hit
from .duration_filter import DurationFilter


@dataclass
class Decision:
    decision: str                    # READY/FILTERED/DOWNLOADED/MISSING/FAILED/DOWNLOADING
    reason: str = ""                 # e.g. "duration_out_of_range" / "blacklist: TEST"
    checks: Dict[str, Any] = field(default_factory=dict)


class DecisionEngine:
    def __init__(
        self,
        min_duration: int,
        max_duration: int,
        blacklist_keywords: Iterable[str] = (),
        min_date: Optional[datetime] = None,
        max_date: Optional[datetime] = None,
        allowlist_keywords: Iterable[str] = (),
    ):
        self._duration = DurationFilter(min_duration, max_duration)
        self._blacklist = list(blacklist_keywords)
        self._min_date = min_date
        self._max_date = max_date
        self._allowlist = list(allowlist_keywords)

    def decide(self, video: Video, file_exists: Optional[bool] = None) -> Decision:
        """Evaluate all rules for one video and return a Decision."""
        status = video.download_status
        checks: Dict[str, Any] = {}

        if status is DownloadStatus.DOWNLOADED:
            exists = self._file_exists(video) if file_exists is None else file_exists
            checks["file"] = exists
            if exists:
                return Decision("DOWNLOADED", checks=checks)
            return Decision("MISSING", reason="file_missing", checks=checks)

        if status is DownloadStatus.FAILED:
            return Decision("FAILED", reason=video.download_error or "download_failed", checks=checks)

        if status is DownloadStatus.DOWNLOADING:
            return Decision("DOWNLOADING", checks=checks)

        # PENDING / FILTERED -> (re-)evaluate duration -> date -> blacklist.
        duration_ok = self._duration.is_eligible(video.duration)
        checks["duration"] = duration_ok
        if not duration_ok:
            return Decision("FILTERED", reason="duration_out_of_range", checks=checks)

        if self._min_date is not None or self._max_date is not None:
            created = video.created
            checks["date"] = created
            if created is None:
                return Decision("FILTERED", reason="date_missing", checks=checks)
            created_date = datetime.fromtimestamp(created).date()
            if self._min_date is not None and created_date < self._min_date.date():
                return Decision("FILTERED", reason="date_out_of_range", checks=checks)
            if self._max_date is not None and created_date > self._max_date.date():
                return Decision("FILTERED", reason="date_out_of_range", checks=checks)

        allowed = True
        if self._allowlist:
            allowed = blacklist_hit(video.title, self._allowlist)
            checks["allowlist"] = allowed

        # Evaluate the blacklist even when an allowlist miss was found.  When
        # both rules are enabled, blacklist is authoritative and must be the
        # reported reason (and the effective exclusion) for a matching title.
        hit = blacklist_hit(video.title, self._blacklist)
        checks["blacklist"] = hit
        if hit:
            return Decision("FILTERED", reason=f"blacklist: {hit}", checks=checks)

        if self._allowlist and not allowed:
            return Decision("FILTERED", reason="allowlist_miss", checks=checks)

        return Decision("READY", checks=checks)

    @staticmethod
    def _file_exists(video: Video) -> bool:
        if not video.download_path:
            return False
        return Path(video.download_path).is_file()
