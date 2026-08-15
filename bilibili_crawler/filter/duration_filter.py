"""Duration filter (plan section 5).

The eligibility rule is inclusive on both ends:

    min_duration <= duration <= max_duration

A video without a valid ``duration`` (missing or non-integer) is treated as
**not eligible** so the download task never enters an indeterminate state.
"""
from __future__ import annotations

from typing import Optional


class DurationFilter:
    def __init__(self, min_duration: int, max_duration: int):
        if min_duration < 0 or max_duration < 0:
            raise ValueError("duration bounds must be non-negative")
        if min_duration > max_duration:
            raise ValueError("min_duration must not exceed max_duration")
        self.min_duration = min_duration
        self.max_duration = max_duration

    def is_eligible(self, duration: Optional[int]) -> bool:
        if duration is None:
            return False
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            return False
        return self.min_duration <= duration <= self.max_duration

    def __repr__(self) -> str:
        return (
            f"DurationFilter(min={self.min_duration}s, max={self.max_duration}s)"
        )
