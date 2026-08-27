"""Offline coverage for progressive, DASH and audio download paths."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bilibili_crawler.bilibili.video import PlaybackInfo, Stream, VideoDetail
from bilibili_crawler.downloader.downloader import VideoDownloader
from bilibili_crawler.downloader.limiter import RateLimiter, mbps_to_bps


class VideoDownloaderModesTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.limiter = RateLimiter(mbps_to_bps(1000))
        self.client = object()
        self.detail = VideoDetail(
            bvid="BVmode",
            title="模式测试",
            cid=99,
            mid=1,
        )

    def test_progressive_video_writes_mp4(self):
        playback = PlaybackInfo(progressive_streams=[Stream("progressive", quality_id=80)])
        downloader = VideoDownloader(
            self.client, save_root=str(self.root), prefer_dash=False, ffmpeg_path=""
        )

        def fake_stream(_url, dest, *_args, **_kwargs):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"mp4")

        with mock.patch("bilibili_crawler.downloader.downloader.get_playback_info", return_value=playback):
            with mock.patch.object(downloader, "_download_stream", side_effect=fake_stream):
                result = downloader.download(
                    self.detail, "测试 UP", self.limiter, qn=80, media_type="video"
                )

        self.assertEqual(result.suffix, ".mp4")
        self.assertTrue(result.is_file())
        self.assertEqual(result.read_bytes(), b"mp4")

    def test_dash_video_never_selects_quality_above_request(self):
        playback = PlaybackInfo(
            video_streams=[
                Stream("4k", quality_id=120, bandwidth=9_000_000),
                Stream("1080p+", quality_id=112, bandwidth=2_000_000),
            ],
            audio_streams=[Stream("audio", quality_id=30280, bandwidth=200_000)],
        )
        downloader = VideoDownloader(
            self.client, save_root=str(self.root), prefer_dash=True, ffmpeg_path="ffmpeg"
        )
        selected = []

        def fake_stream(url, dest, *_args, **_kwargs):
            selected.append(url)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"part")

        def fake_merge(video, audio, output):
            output.write_bytes(video.read_bytes() + (audio.read_bytes() if audio else b""))

        with mock.patch("bilibili_crawler.downloader.downloader.get_playback_info", return_value=playback):
            with mock.patch.object(downloader, "_download_stream", side_effect=fake_stream):
                with mock.patch.object(downloader, "_merge", side_effect=fake_merge):
                    result = downloader.download(
                        self.detail, "测试 UP", self.limiter, qn=112, media_type="video"
                    )

        self.assertEqual(result.suffix, ".mp4")
        self.assertTrue(result.is_file())
        self.assertEqual(selected[:2], ["1080p+", "audio"])

    def test_audio_dash_uses_requested_quality_and_m4a(self):
        playback = PlaybackInfo(audio_streams=[Stream("audio", quality_id=30280)])
        downloader = VideoDownloader(
            self.client, save_root=str(self.root), prefer_dash=True, ffmpeg_path="ffmpeg"
        )
        with mock.patch(
            "bilibili_crawler.downloader.downloader.get_playback_info", return_value=playback
        ) as lookup:
            with mock.patch.object(downloader, "_download_stream") as fetch:
                result = downloader.download(
                    self.detail, "测试 UP", self.limiter, qn=112, media_type="audio"
                )

        self.assertEqual(result.suffix, ".m4a")
        lookup.assert_called_once_with(self.client, "BVmode", 99, qn=112, prefer_dash=True)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "audio")

    def test_audio_without_dash_falls_back_with_same_quality(self):
        first = PlaybackInfo()
        fallback = PlaybackInfo(progressive_streams=[Stream("progressive", quality_id=80)])
        downloader = VideoDownloader(
            self.client, save_root=str(self.root), prefer_dash=True, ffmpeg_path="ffmpeg"
        )

        def fake_stream(_url, dest, *_args, **_kwargs):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"source")

        def fake_extract(_src, output):
            output.write_bytes(b"m4a")

        with mock.patch(
            "bilibili_crawler.downloader.downloader.get_playback_info",
            side_effect=[first, fallback],
        ) as lookup:
            with mock.patch.object(downloader, "_download_stream", side_effect=fake_stream):
                with mock.patch.object(downloader, "_extract_audio", side_effect=fake_extract):
                    result = downloader.download(
                        self.detail, "测试 UP", self.limiter, qn=112, media_type="audio"
                    )

        self.assertEqual(result.suffix, ".m4a")
        self.assertEqual(result.read_bytes(), b"m4a")
        self.assertEqual(
            lookup.call_args_list,
            [
                mock.call(self.client, "BVmode", 99, qn=112, prefer_dash=True),
                mock.call(self.client, "BVmode", 99, qn=112, prefer_dash=False),
            ],
        )

    def test_ffmpeg_runs_without_creating_console_window(self):
        downloader = VideoDownloader(
            self.client, save_root=str(self.root), prefer_dash=True, ffmpeg_path="ffmpeg"
        )
        completed = mock.Mock(returncode=0, stderr="")
        with mock.patch("bilibili_crawler.downloader.downloader.subprocess.run", return_value=completed) as run:
            downloader._run_ffmpeg(["ffmpeg", "-version"])
        self.assertEqual(run.call_args.kwargs["creationflags"], getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0))


if __name__ == "__main__":
    unittest.main()
