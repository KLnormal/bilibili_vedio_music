"""Offline unit tests for the core logic (no network access)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
