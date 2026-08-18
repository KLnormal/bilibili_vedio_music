"""Rule explanation formatting (v0.2 Phase 4).

Turns a :class:`Decision` into a human-readable block explaining why a video
was downloaded or filtered, so users can answer "why didn't this download?"
(AGENT_PROMPT_v0.2 section 13). The explanation is built from the structured
``Decision`` object, not concatenated ad-hoc strings.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ..database.models import Video
from .decision import Decision


def explain(
    decision: Decision,
    video: Optional[Video] = None,
    min_duration: Optional[int] = None,
    max_duration: Optional[int] = None,
) -> str:
    """Format a Decision as a readable multi-line explanation block."""
    lines: list[str] = []
    if video is not None:
        lines.append(f"Video: {video.title}")
        lines.append(f"BV: {video.bvid}")
        lines.append("")

    checks = decision.checks

    if "file" in checks:
        ok = checks["file"]
        lines.append(f"File: {'exists' if ok else 'missing'}    {'PASS' if ok else 'FAIL'}")

    if "duration" in checks:
        ok = checks["duration"]
        dur = video.duration if video is not None and video.duration is not None else "?"
        lines.append(f"Duration: {dur}s    {'PASS' if ok else 'FAIL'}")
        if min_duration is not None and max_duration is not None:
            lines.append(f"Range: {min_duration}~{max_duration}    {'PASS' if ok else 'FAIL'}")

    if "date" in checks:
        created = checks["date"]
        date_fail = decision.reason in ("date_out_of_range", "date_missing")
        if created is None:
            lines.append("Created: missing    FAIL")
        else:
            created_str = datetime.fromtimestamp(created).strftime("%Y-%m-%d")
            lines.append(f"Created: {created_str}    {'FAIL' if date_fail else 'PASS'}")

    if "blacklist" in checks:
        hit = checks["blacklist"]
        lines.append(f"Blacklist: {hit or 'none'}    {'FAIL' if hit else 'PASS'}")

    lines.append(f"Decision: {decision.decision}")
    if decision.reason:
        lines.append(f"Reason: {decision.reason}")
    return "\n".join(lines)
