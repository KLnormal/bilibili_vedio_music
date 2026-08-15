"""Fetch UP-main profile info and submission lists from Bilibili.

A ``mid`` is the UP's unique identity (plan section 2.1). The submission list
is paginated and ordered by publication date (newest first).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Optional

from .client import BilibiliClient, BilibiliError


@dataclass
class UpProfile:
    mid: int
    name: str = ""
    face: str = ""
    sign: str = ""


@dataclass
class VideoListItem:
    """One entry of the submission list (search API shape)."""

    bvid: str
    title: str = ""
    pic: str = ""
    duration_text: str = ""  # "mm:ss" as returned by the list API
    created: int = 0


def get_up_profile(client: BilibiliClient, mid: int) -> UpProfile:
    """Fetch an UP's basic profile. Returns a profile with defaults on failure."""
    try:
        params = {"mid": mid, "token": "", "platform": "web"}
        params.update(client.anticrawl_params())
        data = client.get_json(
            f"{BilibiliClient.BASE}/x/space/wbi/acc/info",
            params=params,
            wbi=True,
        )
    except BilibiliError:
        # Profile fetch is best-effort; the crawler continues with defaults.
        return UpProfile(mid=mid)

    if not isinstance(data, dict):
        return UpProfile(mid=mid)

    return UpProfile(
        mid=mid,
        name=data.get("name", ""),
        face=data.get("face", ""),
        sign=data.get("sign", ""),
    )


def iter_submissions(
    client: BilibiliClient,
    mid: int,
    *,
    page_size: int = 30,
    max_pages: Optional[int] = None,
) -> Iterator[VideoListItem]:
    """Yield submission-list items newest-first, page by page.

    ``max_pages`` optionally caps the scan (used by tests / manual runs).
    """
    page = 1
    while True:
        if max_pages is not None and page > max_pages:
            return
        try:
            params = {
                "mid": mid,
                "ps": page_size,
                "pn": page,
                "order": "pubdate",
                "tid": 0,
                "keyword": "",
                "platform": "web",
                "order_avoided": "true",
            }
            params.update(client.anticrawl_params())
            data = client.get_json(
                f"{BilibiliClient.BASE}/x/space/wbi/arc/search",
                params=params,
                wbi=True,
            )
        except BilibiliError:
            # A failed page is reported as an error to the caller, who decides
            # whether to stop the incremental scan.
            raise

        if not isinstance(data, dict):
            return

        vlist = (data.get("list") or {}).get("vlist") or []
        for item in vlist:
            yield VideoListItem(
                bvid=item.get("bvid", ""),
                title=item.get("title", ""),
                pic=item.get("pic", ""),
                duration_text=item.get("length", ""),
                created=item.get("created", 0),
            )

        if len(vlist) < page_size:
            return
        page += 1


def parse_duration_text(text: str) -> Optional[int]:
    """Parse Bilibili's ``mm:ss`` / ``hh:mm:ss`` duration string into seconds."""
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None
