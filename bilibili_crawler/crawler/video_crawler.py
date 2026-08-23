"""Per-video metadata enrichment.

A submission-list entry already carries ``bvid`` / ``title`` / ``pic`` /
``mm:ss`` duration and the publish timestamp, so scanning can build a
:class:`Video` record **without** a per-video ``view`` API call. The ``view``
call (which supplies the precise ``duration``, ``description`` and the
``cid`` required for downloading) is deferred to the download stage, where it
is needed anyway. This keeps the scan light (a few dozen requests for a large
UP instead of hundreds), which drastically lowers risk-control pressure and
lets the paginated scan reach deeper into the UP's history.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..bilibili.user import VideoListItem, parse_duration_text
from ..bilibili.video import VIDEO_URL_TEMPLATE
from ..database.models import Video


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_video(item: VideoListItem, mid: int) -> Video:
    """Build a Video model from a submission-list entry (no view API call).

    ``description``/``cid`` are filled later by the download stage, which calls
    ``get_video_detail`` anyway.
    """
    return Video(
        bvid=item.bvid,
        mid=mid,
        duration=parse_duration_text(item.duration_text),
        created=item.created or None,
        title=item.title,
        description="",
        pic=item.pic,
        url=VIDEO_URL_TEMPLATE.format(bvid=item.bvid),
        update_time=_now_iso(),
    )
