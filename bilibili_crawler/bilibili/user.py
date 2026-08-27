"""Fetch UP-main profile info and submission lists from Bilibili.

A ``mid`` is the UP's unique identity (plan section 2.1). The submission list
is paginated and ordered by publication date (newest first).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional

import time

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
    owner_mid: Optional[int] = None


class SubmissionPageError(BilibiliError):
    """A page could not be fetched after retries; ``page`` is resumable."""

    def __init__(self, page: int, cause: Exception):
        self.page = page
        self.cause = cause
        super().__init__(f"submission page {page} failed: {cause}")


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
    page_retries: int = 3,
    page_backoff: float = 20.0,
    start_page: int = 1,
    page_callback: Optional[Callable[[int, int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Iterator[VideoListItem]:
    """Yield submission-list items newest-first, page by page.

    ``max_pages`` optionally caps the scan (used by tests / manual runs).

    Deep pagination on the space endpoint is risk-controlled: when a page is
    rejected (412 / -352 / -799) we wait ``page_backoff`` seconds and retry the
    same page up to ``page_retries`` extra times, so a scan can push further
    into the UP's history instead of stopping after the first few pages.
    """
    page = max(1, int(start_page))
    seen_signatures = set()
    while True:
        if should_stop is not None and should_stop():
            return
        if max_pages is not None and page > max_pages:
            return
        valid_items = None
        raw_count = 0
        signature = ()
        for attempt in range(page_retries + 1):
            if should_stop is not None and should_stop():
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
                if not isinstance(data, dict):
                    raise BilibiliError("malformed submission response")
                payload = data.get("list")
                vlist = payload.get("vlist") if isinstance(payload, dict) else None
                if not isinstance(vlist, list):
                    raise BilibiliError("malformed submission payload")
                candidate_signature = tuple(
                    item.get("bvid", "") for item in vlist if isinstance(item, dict)
                )
                if candidate_signature and candidate_signature in seen_signatures:
                    raise BilibiliError("repeated page payload")
                candidates = []
                all_items = []
                for item in vlist:
                    if not isinstance(item, dict):
                        continue
                    owner_mid = item.get("mid")
                    try:
                        owner_mid = int(owner_mid) if owner_mid is not None else None
                    except (TypeError, ValueError):
                        owner_mid = None
                    parsed = VideoListItem(
                        bvid=item.get("bvid", ""),
                        title=item.get("title", ""),
                        pic=item.get("pic", ""),
                        duration_text=item.get("length", ""),
                        created=item.get("created", 0),
                        owner_mid=owner_mid,
                    )
                    all_items.append(parsed)
                    if owner_mid is None or owner_mid == mid:
                        candidates.append(parsed)
                if vlist and not candidates:
                    raise BilibiliError("foreign submission payload")
                # Bilibili's official space list includes related V.W.P /
                # Kamitsubaki uploads in mixed pages.  The web UI counts these
                # rows too, so retain the raw page once it contains at least
                # one row belonging to the requested UP.  A foreign-only page
                # is still rejected above as a risk-control response.
                valid_items = all_items
                raw_count = len(vlist)
                signature = candidate_signature
                break
            except BilibiliError as exc:
                if attempt < page_retries:
                    # Event.wait-style callbacks let the desktop Stop button
                    # interrupt a long anti-crawl backoff immediately.  Keep
                    # this compatible with plain callbacks used by tests.
                    deadline = time.monotonic() + max(0.0, page_backoff)
                    while True:
                        if should_stop is not None and should_stop():
                            return
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        time.sleep(min(0.2, remaining))
                    continue
                # Retries exhausted: surface the error so the caller can decide
                # to stop (the videos already scanned are kept).
                raise SubmissionPageError(page, exc) from exc

        if signature:
            seen_signatures.add(signature)
        for item in valid_items:
            yield item

        if page_callback is not None:
            page_callback(page, raw_count, page + 1)

        if raw_count < page_size:
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
