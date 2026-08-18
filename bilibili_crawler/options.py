"""Shared download options and quality mapping (v0.2 Phase 2).

``DownloadOptions`` is the single parameter object shared by the CLI, preview
and the downloader, so ``download`` and ``preview`` evaluate the exact same
rules (AGENT_PROMPT_v0.2 section 5). Quality is exposed to users as readable
names and mapped to Bilibili ``qn`` codes internally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# User-facing quality name -> Bilibili qn code (AGENT_PROMPT_v0.2 section 10,
# extended with 1080P high-bitrate "1080p+" per user request).
QUALITY_TO_QN = {
    "720p": 64,
    "1080p": 80,
    "1080p+": 112,     # 1080P 高码率
    "1080p60": 116,
    "4k": 120,
}

# Reverse mapping for reporting the *actually granted* quality.
QN_TO_QUALITY = {qn: name for name, qn in QUALITY_TO_QN.items()}

MEDIA_TYPES = ("video", "audio")


@dataclass
class DownloadOptions:
    """Download parameters for a single task (CLI overrides config)."""

    quality: Optional[str] = None      # "720p" | "1080p" | "1080p60" | "4k"
    media_type: str = "video"          # "video" | "audio"
    min_duration: Optional[int] = None  # seconds, inclusive
    max_duration: Optional[int] = None  # seconds, inclusive

    @property
    def qn(self) -> Optional[int]:
        """The qn code for ``quality``, or None when not specified."""
        if not self.quality:
            return None
        return QUALITY_TO_QN.get(self.quality.lower())

    def validate(self) -> None:
        if self.media_type not in MEDIA_TYPES:
            raise ValueError(f"media type must be one of {MEDIA_TYPES}, got {self.media_type!r}")
        if self.quality is not None and self.quality.lower() not in QUALITY_TO_QN:
            raise ValueError(
                f"quality must be one of {list(QUALITY_TO_QN)}, got {self.quality!r}"
            )
        if (
            self.min_duration is not None
            and self.max_duration is not None
            and self.min_duration > self.max_duration
        ):
            raise ValueError("min_duration must not exceed max_duration")


def quality_name(qn: int) -> str:
    """Return the readable name for a granted qn code (fallback to the code)."""
    return QN_TO_QUALITY.get(qn, str(qn))
