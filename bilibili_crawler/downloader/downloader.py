"""Video downloader.

Downloads a single video to ``<save_root>/<UP name or mid>/<title> [BVxxxx].mp4``
using either a progressive stream or DASH (video + audio merged with ffmpeg).
The download is rate-limited through the shared :class:`RateLimiter` and writes
to a ``.part`` temp file that is atomically renamed on success.

The downloader never inspects existing files to decide whether to download —
that decision belongs to the database's download status (plan principle #1).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import requests

from ..bilibili.client import BilibiliClient, BilibiliError
from ..bilibili.video import VideoDetail, get_playback_info, pick_best, pick_best_leq
from .limiter import RateLimiter

ProgressCallback = Callable[[int, int, str], None]
# (downloaded_bytes, total_bytes_or_negative, human_readable_speed)

_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Make a filesystem-safe file name from a video title."""
    name = _ILLEGAL.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "video"


class DownloadError(Exception):
    """Raised when a video download ultimately fails."""


class VideoDownloader:
    def __init__(
        self,
        client: BilibiliClient,
        *,
        save_root: str,
        qn: int = 80,
        prefer_dash: bool = True,
        ffmpeg_path: str = "",
        user_agent: str = "",
        referer: str = "https://www.bilibili.com",
        chunk_size: int = 1024 * 512,
    ):
        self.client = client
        self.save_root = Path(save_root)
        self.qn = qn
        self.prefer_dash = prefer_dash
        self.ffmpeg_path = ffmpeg_path or self._find_ffmpeg()
        self.user_agent = user_agent
        self.referer = referer
        self.chunk_size = chunk_size
        # Set by DownloadTaskManager so an in-flight HTTP stream can be
        # interrupted promptly when the user presses Stop.
        self.stop_event = threading.Event()

    # ------------------------------------------------------------- helpers --
    @staticmethod
    def _find_ffmpeg() -> str:
        return shutil.which("ffmpeg") or ""

    @property
    def has_ffmpeg(self) -> bool:
        return bool(self.ffmpeg_path)

    def cancel(self) -> None:
        self.stop_event.set()

    def clear_cancel(self) -> None:
        self.stop_event.clear()

    def _run_ffmpeg(self, cmd):
        kwargs = {"capture_output": True, "text": True}
        # Prevent ffmpeg's console subsystem from opening a visible window on
        # Windows desktop launches.  The flag is ignored on other platforms.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(cmd, **kwargs)

    def _media_headers(self, referer: str) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Referer": referer,
            "Origin": "https://www.bilibili.com",
        }

    # -------------------------------------------------------------- public --
    def download(
        self,
        detail: VideoDetail,
        up_dir: str,
        limiter: RateLimiter,
        progress: Optional[ProgressCallback] = None,
        media_type: str = "video",
        qn: Optional[int] = None,
    ) -> Path:
        """Download ``detail`` and return the final file path.

        ``media_type`` selects ``video`` (MP4) or ``audio`` (M4A, no video and
        no MP3 transcode). ``qn`` overrides the configured quality for this
        single download. Raises :class:`DownloadError` on failure; the caller
        owns the database status update.
        """
        if detail.cid is None:
            raise DownloadError(f"video {detail.bvid} has no cid (cannot download)")

        effective_qn = qn if qn is not None else self.qn

        # DASH (separate audio/video) needs ffmpeg to merge. Without it we ask
        # Bilibili for a single progressive stream instead.
        use_dash = self.prefer_dash and self.has_ffmpeg
        playback = get_playback_info(
            self.client, detail.bvid, detail.cid, qn=effective_qn, prefer_dash=use_dash
        )

        save_dir = self.save_root / sanitize_filename(up_dir, max_len=60)
        save_dir.mkdir(parents=True, exist_ok=True)

        # ``bvid`` already carries the "BV" prefix.
        base = f"{sanitize_filename(detail.title)} [{detail.bvid}]"

        if media_type == "audio":
            return self._download_audio(
                detail, playback, save_dir, base, limiter, progress, effective_qn
            )

        final_path = save_dir / f"{base}.mp4"

        if use_dash and playback.video_streams:
            self._download_dash(detail, playback, save_dir, base, limiter, progress, effective_qn)
            return final_path

        if playback.progressive_streams:
            self._download_progressive(detail, playback, final_path, limiter, progress)
            return final_path

        # Last resort: re-request a progressive stream (covers "ffmpeg missing
        # but DASH came back anyway" and empty-DASH edge cases).
        fallback = get_playback_info(
            self.client, detail.bvid, detail.cid, qn=effective_qn, prefer_dash=False
        )
        if fallback.progressive_streams:
            self._download_progressive(detail, fallback, final_path, limiter, progress)
            return final_path

        raise DownloadError(f"no downloadable stream for {detail.bvid}")

    # --------------------------------------------------------------- audio --
    def _download_audio(
        self,
        detail: VideoDetail,
        playback,
        save_dir: Path,
        base: str,
        limiter: RateLimiter,
        progress: Optional[ProgressCallback],
        qn: int,
    ) -> Path:
        """Download only the audio track, saved as ``.m4a`` (no MP3 transcode)."""
        final_path = save_dir / f"{base}.m4a"

        audio_stream = pick_best(playback.audio_streams)
        if audio_stream is not None:
            self._download_stream(
                audio_stream.url, final_path, detail.bvid, limiter, progress,
                suffix=" (audio)",
            )
            return final_path

        # No DASH audio available: extract the track from a progressive stream.
        if not self.has_ffmpeg:
            raise DownloadError(
                f"audio download needs ffmpeg (no DASH audio stream for {detail.bvid})"
            )
        fallback = get_playback_info(
            self.client, detail.bvid, detail.cid, qn=qn, prefer_dash=False
        )
        stream = pick_best(fallback.progressive_streams)
        if stream is None:
            raise DownloadError(f"no audio stream for {detail.bvid}")

        tmp = save_dir / f"{base}.audio.src.part"
        try:
            self._download_stream(
                stream.url, tmp, detail.bvid, limiter, progress, suffix=" (progressive)"
            )
            self._extract_audio(tmp, final_path)
        finally:
            tmp.unlink(missing_ok=True)
        return final_path

    def _extract_audio(self, src: Path, output: Path) -> None:
        cmd = [
            self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-vn", "-c", "copy", str(output),
        ]
        try:
            proc = self._run_ffmpeg(cmd)
        except OSError as exc:
            raise DownloadError(f"failed to run ffmpeg: {exc}") from exc
        if proc.returncode != 0:
            raise DownloadError(f"ffmpeg audio extract failed: {proc.stderr.strip()[:300]}")

    # ------------------------------------------------------------ progressive --
    def _download_progressive(
        self,
        detail: VideoDetail,
        playback,
        final_path: Path,
        limiter: RateLimiter,
        progress: Optional[ProgressCallback],
    ) -> None:
        stream = pick_best(playback.progressive_streams)
        if stream is None:
            raise DownloadError(f"no progressive stream for {detail.bvid}")
        self._download_stream(stream.url, final_path, detail.bvid, limiter, progress)

    # ----------------------------------------------------------------- DASH --
    def _download_dash(
        self,
        detail: VideoDetail,
        playback,
        save_dir: Path,
        base: str,
        limiter: RateLimiter,
        progress: Optional[ProgressCallback],
        qn: int,
    ) -> None:
        # Pick the best video stream *within* the requested tier (Bilibili
        # returns higher tiers too, but we must not jump above the request).
        video_stream = pick_best_leq(playback.video_streams, qn)
        audio_stream = pick_best(playback.audio_streams)
        if video_stream is None:
            raise DownloadError(f"no DASH video stream <= qn {qn} for {detail.bvid}")

        video_part = save_dir / f"{base}.video.part"
        audio_part = save_dir / f"{base}.audio.part"
        final_path = save_dir / f"{base}.mp4"

        try:
            self._download_stream(
                video_stream.url, video_part, detail.bvid, limiter, progress,
                suffix=" (video)",
            )
            if audio_stream is not None:
                self._download_stream(
                    audio_stream.url, audio_part, detail.bvid, limiter, progress,
                    suffix=" (audio)",
                )
            self._merge(video_part, audio_part if audio_stream else None, final_path)
        finally:
            video_part.unlink(missing_ok=True)
            audio_part.unlink(missing_ok=True)

    def _merge(self, video: Path, audio: Optional[Path], output: Path) -> None:
        cmd = [self.ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
        if audio is not None:
            cmd += ["-i", str(audio)]
        cmd += ["-c", "copy", str(output)]
        try:
            proc = self._run_ffmpeg(cmd)
        except OSError as exc:
            raise DownloadError(f"failed to run ffmpeg: {exc}") from exc
        if proc.returncode != 0:
            raise DownloadError(f"ffmpeg merge failed: {proc.stderr.strip()[:300]}")

    # --------------------------------------------------------------- stream --
    def _download_stream(
        self,
        url: str,
        dest: Path,
        bvid: str,
        limiter: RateLimiter,
        progress: Optional[ProgressCallback],
        suffix: str = "",
    ) -> None:
        tmp = dest.with_suffix(dest.suffix + ".part")
        headers = self._media_headers(f"https://www.bilibili.com/video/{bvid}")

        try:
            with self.client.session.get(
                url, headers=headers, stream=True, timeout=(10, 30)
            ) as resp:
                if resp.status_code != 200:
                    raise DownloadError(f"HTTP {resp.status_code} for {bvid}{suffix}")
                total = int(resp.headers.get("Content-Length") or -1)
                downloaded = 0
                start = time.monotonic()
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        if self.stop_event.is_set():
                            raise DownloadError("下载已停止")
                        if not chunk:
                            continue
                        limiter.acquire(len(chunk))
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if progress:
                            elapsed = max(1e-6, time.monotonic() - start)
                            speed = downloaded / elapsed
                            progress(
                                downloaded,
                                total,
                                f"{speed / (1024 * 1024):.1f} MB/s",
                            )
            tmp.replace(dest)
        except requests.RequestException as exc:
            tmp.unlink(missing_ok=True)
            raise DownloadError(f"network error for {bvid}{suffix}: {exc}") from exc
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise DownloadError(f"I/O error for {bvid}{suffix}: {exc}") from exc
