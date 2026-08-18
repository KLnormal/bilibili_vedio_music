"""Offline unit tests for the core logic (no network access)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from bilibili_crawler.app import App
from bilibili_crawler.database.database import Database
from bilibili_crawler.database.models import DownloadStatus, Up, Video
from bilibili_crawler.database.repository import Repository
from bilibili_crawler.downloader.limiter import RateLimiter, mbps_to_bps
from bilibili_crawler.filter.duration_filter import DurationFilter


class DurationFilterTest(unittest.TestCase):
    def setUp(self):
        self.f = DurationFilter(300, 1800)

    def test_inclusive_bounds(self):
        self.assertTrue(self.f.is_eligible(300))
        self.assertTrue(self.f.is_eligible(1800))
        self.assertTrue(self.f.is_eligible(600))

    def test_out_of_range(self):
        self.assertFalse(self.f.is_eligible(299))
        self.assertFalse(self.f.is_eligible(1801))

    def test_invalid_duration_rejected(self):
        self.assertFalse(self.f.is_eligible(None))
        self.assertFalse(self.f.is_eligible("abc"))

    def test_invalid_bounds(self):
        with self.assertRaises(ValueError):
            DurationFilter(1800, 300)


class RateLimiterTest(unittest.TestCase):
    def test_default_rate(self):
        limiter = RateLimiter(mbps_to_bps(40))
        self.assertAlmostEqual(limiter.rate / (1024 * 1024), 40.0, places=5)

    def test_set_rate(self):
        limiter = RateLimiter(mbps_to_bps(40))
        limiter.set_rate(mbps_to_bps(20))
        self.assertAlmostEqual(limiter.rate / (1024 * 1024), 20.0, places=5)

    def test_acquire_does_not_block_forever(self):
        limiter = RateLimiter(mbps_to_bps(100))
        limiter.acquire(1024 * 1024)  # well within capacity


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.db = Database(Path(self._tmp) / "test.db")
        self.repo = Repository(self.db)

    def tearDown(self):
        self.db.close()

    def test_up_crud(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        up = self.repo.get_up(1)
        self.assertEqual(up.name, "A")
        self.repo.delete_up(1)
        self.assertIsNone(self.repo.get_up(1))

    def test_bvid_is_unique_identity(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        self.assertTrue(self.repo.insert_video(Video(bvid="BV1x", mid=1, title="t")))
        self.assertFalse(self.repo.insert_video(Video(bvid="BV1x", mid=1, title="dup")))
        self.assertTrue(self.repo.video_exists("BV1x"))
        self.assertEqual(self.repo.count_videos(1), 1)

    def test_download_state_machine(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        self.repo.insert_video(Video(bvid="BV1x", mid=1, title="t"))
        self.repo.update_download_status("BV1x", DownloadStatus.DOWNLOADING)
        self.repo.update_download_status("BV1x", DownloadStatus.DOWNLOADED, path="p.mp4")
        video = self.repo.get_video("BV1x")
        self.assertEqual(video.download_status, DownloadStatus.DOWNLOADED)
        self.assertEqual(video.download_path, "p.mp4")
        self.assertIsNotNone(video.download_time)

    def test_failed_retry(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        self.repo.insert_video(Video(bvid="BV1x", mid=1, title="t"))
        self.repo.update_download_status("BV1x", DownloadStatus.FAILED, error="boom")
        self.assertEqual(self.repo.reset_failed(), 1)
        self.assertEqual(self.repo.get_video("BV1x").download_status, DownloadStatus.PENDING)

    def test_list_downloaded(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        self.repo.insert_video(Video(bvid="BV1a", mid=1, title="a", download_status=DownloadStatus.DOWNLOADED))
        self.repo.insert_video(Video(bvid="BV1b", mid=1, title="b", download_status=DownloadStatus.PENDING))
        self.repo.insert_video(Video(bvid="BV1c", mid=1, title="c", download_status=DownloadStatus.DOWNLOADED))
        downloaded = self.repo.list_downloaded(1)
        self.assertEqual({v.bvid for v in downloaded}, {"BV1a", "BV1c"})


class CheckFilesTest(unittest.TestCase):
    """v0.2 Phase 1: file-consistency check recovers DOWNLOADED -> PENDING."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        cfg = {
            "database": {"path": str(self._tmp / "t.db")},
            "download": {"save_root": str(self._tmp / "dl")},
            "auth": {"cookie_file": str(self._tmp / "cookies.json")},
        }
        self._cfg = self._tmp / "config.yaml"
        self._cfg.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.app = App(config_path=str(self._cfg), configure_logging=False)

    def tearDown(self):
        self.app.close()

    def test_check_files_recovers_missing_only(self):
        self.app.repo.upsert_up(Up(mid=1, name="A"))
        existing = self._tmp / "dl" / "A" / "ok.mp4"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b"x")
        self.app.repo.insert_video(Video(
            bvid="BV1ok", mid=1, title="ok",
            download_status=DownloadStatus.DOWNLOADED, download_path=str(existing),
        ))
        self.app.repo.insert_video(Video(
            bvid="BV1miss", mid=1, title="miss",
            download_status=DownloadStatus.DOWNLOADED,
            download_path=str(self._tmp / "gone.mp4"),
        ))
        result = self.app.check_files(1)
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["missing"], ["BV1miss"])
        self.assertEqual(self.app.repo.get_video("BV1ok").download_status, DownloadStatus.DOWNLOADED)
        self.assertEqual(self.app.repo.get_video("BV1miss").download_status, DownloadStatus.PENDING)


if __name__ == "__main__":
    unittest.main()
