"""Unified rule decision engine (v0.2 Phase 4).

Produces a single, explainable decision per video:

    READY / FILTERED / DOWNLOADED / MISSING / FAILED / DOWNLOADING

with a ``reason`` and a per-rule ``checks`` map. The downloader should only
consume videos decided READY; everything else is explained by the decision
(AGENT_PROMPT_v0.2 section 6). Rules are evaluated in the order defined in
section 7: download/file state -> duration -> blacklist.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    ):
        self._duration = DurationFilter(min_duration, max_duration)
        self._blacklist = list(blacklist_keywords)

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

        # PENDING / FILTERED -> (re-)evaluate duration then blacklist.
        duration_ok = self._duration.is_eligible(video.duration)
        checks["duration"] = duration_ok
        if not duration_ok:
            return Decision("FILTERED", reason="duration_out_of_range", checks=checks)

        hit = blacklist_hit(video.title, self._blacklist)
        checks["blacklist"] = hit
        if hit:
            return Decision("FILTERED", reason=f"blacklist: {hit}", checks=checks)

        return Decision("READY", checks=checks)

    @staticmethod
    def _file_exists(video: Video) -> bool:
        if not video.download_path:
            return False
        return Path(video.download_path).is_file()
