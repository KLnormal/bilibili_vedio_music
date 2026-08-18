"""Per-video metadata enrichment.

A submission-list entry only carries ``bvid`` / ``title`` / ``pic`` / a
``mm:ss`` duration. The authoritative metadata (precise ``duration`` seconds,
``description``, ``cid``) comes from the ``view`` endpoint. This module turns a
list item into a fully-populated :class:`Video` model.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from ..bilibili.client import BilibiliClient, BilibiliError
from ..bilibili.user import VideoListItem
from ..bilibili.video import get_video_detail
from ..database.models import Video


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_video(
    client: BilibiliClient,
    item: VideoListItem,
    mid: int,
    request_interval: float = 0.0,
) -> Video:
    """Build a Video model for a newly-discovered list item.

    Raises :class:`BilibiliError` when the detail request fails, so the caller
    can decide whether to keep going (a single failure must not crash the UP).
    """
    detail = get_video_detail(client, item.bvid)
    if request_interval > 0:
        time.sleep(request_interval)
    return Video(
        bvid=item.bvid,
        mid=mid,
        duration=detail.duration,
        created=detail.pubdate or item.created or None,
        title=detail.title or item.title,
        description=detail.description,
        pic=detail.pic or item.pic,
        url=detail.url,
        update_time=_now_iso(),
    )
