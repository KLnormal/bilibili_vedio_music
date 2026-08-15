"""Fetch video metadata and playback URLs from Bilibili.

The "view" endpoint supplies authoritative metadata including the precise
``duration`` (seconds), ``desc`` and the first page ``cid`` — the cid is later
required to obtain a downloadable stream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .client import BilibiliClient, BilibiliError

VIDEO_URL_TEMPLATE = "https://www.bilibili.com/video/{bvid}"


@dataclass
class VideoDetail:
    bvid: str
    title: str = ""
    description: str = ""
    pic: str = ""
    duration: Optional[int] = None  # seconds
    cid: Optional[int] = None
    mid: int = 0

    @property
    def url(self) -> str:
        return VIDEO_URL_TEMPLATE.format(bvid=self.bvid)


@dataclass
class Stream:
    """A single media stream (DASH video/audio or progressive durl)."""

    url: str
    quality_id: int = 0
    codecs: str = ""
    bandwidth: int = 0
    mime_type: str = ""


@dataclass
class PlaybackInfo:
    """Parsed playback URLs for a given bvid/cid."""

    video_streams: List[Stream] = field(default_factory=list)
    audio_streams: List[Stream] = field(default_factory=list)
    progressive_streams: List[Stream] = field(default_factory=list)
    quality: int = 0


def get_video_detail(client: BilibiliClient, bvid: str) -> VideoDetail:
    data = client.get_json(
        f"{BilibiliClient.BASE}/x/web-interface/view", params={"bvid": bvid}
    )
    if not isinstance(data, dict):
        raise BilibiliError(f"unexpected view response for {bvid}")

    pages = data.get("pages") or []
    cid = None
    if pages and isinstance(pages[0], dict):
        cid = pages[0].get("cid")

    owner = data.get("owner") or {}
    mid = owner.get("mid", 0)

    duration = data.get("duration")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    return VideoDetail(
        bvid=bvid,
        title=data.get("title", ""),
        description=data.get("desc", ""),
        pic=data.get("pic", ""),
        duration=duration,
        cid=int(cid) if cid is not None else None,
        mid=int(mid) if mid else 0,
    )


def get_playback_info(
    client: BilibiliClient,
    bvid: str,
    cid: int,
    *,
    qn: int = 80,
    prefer_dash: bool = True,
) -> PlaybackInfo:
    """Fetch playback URLs.

    ``fnval=16`` requests DASH (separate audio/video), ``fnval=1`` requests a
    progressive single-file stream. We ask for DASH first and fall back to
    progressive streams when DASH is unavailable.
    """
    info = PlaybackInfo()

    params = {
        "bvid": bvid,
        "cid": cid,
        "qn": qn,
        "fnver": 0,
        "fourk": 1,
    }
    if prefer_dash:
        params["fnval"] = 16
    else:
        params["fnval"] = 1

    data = client.get_json(
        f"{BilibiliClient.BASE}/x/player/playurl",
        params=params,
        wbi=True,
        headers={"Referer": VIDEO_URL_TEMPLATE.format(bvid=bvid)},
    )
    if not isinstance(data, dict):
        return info

    info.quality = data.get("quality", 0)

    dash = data.get("dash") or {}
    for s in dash.get("video") or []:
        info.video_streams.append(
            Stream(
                url=s.get("baseUrl") or s.get("base_url", ""),
                quality_id=s.get("id", 0),
                codecs=s.get("codecs", ""),
                bandwidth=s.get("bandwidth", 0),
                mime_type=s.get("mimeType", ""),
            )
        )
    for s in dash.get("audio") or []:
        info.audio_streams.append(
            Stream(
                url=s.get("baseUrl") or s.get("base_url", ""),
                quality_id=s.get("id", 0),
                codecs=s.get("codecs", ""),
                bandwidth=s.get("bandwidth", 0),
                mime_type=s.get("mimeType", ""),
            )
        )

    for s in data.get("durl") or []:
        info.progressive_streams.append(
            Stream(url=s.get("url", ""), quality_id=s.get("id", 0))
        )

    return info


def pick_best(streams: List[Stream]) -> Optional[Stream]:
    """Pick the stream with the highest bandwidth (fallback to first)."""
    if not streams:
        return None
    return max(streams, key=lambda s: s.bandwidth or 0)
