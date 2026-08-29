"""Offline tests for active download-root reconciliation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from bilibili_crawler.app import App
from bilibili_crawler.database.models import DownloadStatus, Up, Video
from bilibili_crawler.download_directory import build_media_file_index, normalize_download_root
from bilibili_crawler.options import DownloadOptions


class DownloadDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.a = self.tmp / "A"
        self.b = self.tmp / "B"
        self.cfg = self.tmp / "config.yaml"
        self.cfg.write_text(yaml.safe_dump({
            "database": {"path": str(self.tmp / "state.db")},
            "auth": {"cookie_file": str(self.tmp / "cookies.json")},
            "download": {"save_root": str(self.a), "ffmpeg_path": ""},
        }), encoding="utf-8")
        self.app = App(str(self.cfg), configure_logging=False)
        self.app.repo.upsert_up(Up(mid=1, name="测试UP"))

    def tearDown(self):
        self.app.close()

    def _video(self, bvid="BV1ROOT", status=DownloadStatus.PENDING):
        self.app.repo.insert_video(Video(bvid=bvid, mid=1, title="标题", duration=600,
                                         download_status=status))

    def test_switch_root_rebuilds_state_and_switching_back_rechecks_files(self):
        self._video()
        (self.a / "nested").mkdir(parents=True)
        a_file = self.a / "nested" / "标题 [BV1ROOT].mp4"
        a_file.write_bytes(b"mp4")
        self.app.check_files(1, "video")
        self.assertEqual(self.app.repo.get_media("BV1ROOT", "video").status, DownloadStatus.DOWNLOADED)

        result = self.app.switch_download_root(self.b)
        self.assertEqual(result["root"], str(normalize_download_root(self.b)))
        self.assertEqual(self.app.repo.get_media("BV1ROOT", "video").status, DownloadStatus.PENDING)
        self.assertEqual(self.app.repo.get_media("BV1ROOT", "video").download_path, "")

        b_file = self.b / "标题 [BV1ROOT].mp4"
        b_file.write_bytes(b"new")
        self.app.check_files(1, "video")
        self.assertEqual(self.app.repo.get_media("BV1ROOT", "video").download_path, str(b_file.resolve()))

        self.app.switch_download_root(self.a)
        self.assertEqual(self.app.repo.get_media("BV1ROOT", "video").status, DownloadStatus.DOWNLOADED)
        self.assertEqual(self.app.repo.get_media("BV1ROOT", "video").download_path, str(a_file.resolve()))

    def test_root_switch_resets_failed_and_downloading_but_keeps_media_separation(self):
        self._video("BVFAIL", DownloadStatus.PENDING)
        self._video("BVAUDIO", DownloadStatus.PENDING)
        self.app.repo.update_download_status("BVFAIL", DownloadStatus.FAILED, error="old", media_type="video")
        self.app.repo.update_download_status("BVAUDIO", DownloadStatus.DOWNLOADING, media_type="audio")
        self.app.switch_download_root(self.b)
        self.assertEqual(self.app.repo.get_media("BVFAIL", "video").status, DownloadStatus.PENDING)
        self.assertEqual(self.app.repo.get_media("BVAUDIO", "audio").status, DownloadStatus.PENDING)
        self.assertEqual(self.app.repo.get_media("BVFAIL", "video").download_error, "")

    def test_index_ignores_invalid_files_and_chooses_newest_duplicate(self):
        self._video("BV1DUPX")
        nested = self.b / "x"; nested.mkdir(parents=True)
        old = nested / "old [BV1DUPX].mp4"; old.write_bytes(b"old")
        new = nested / "new [BV1DUPX].mp4"; new.write_bytes(b"new")
        old.touch(); new.touch()
        new_mtime = old.stat().st_mtime_ns + 10_000_000
        import os
        os.utime(new, ns=(new_mtime, new_mtime))
        (nested / "empty [BV1DUPX].m4a").touch()
        (nested / "partial [BV1DUPX].mp4.part").write_bytes(b"part")
        (nested / "wrong BV1DUPX.mp4").write_bytes(b"wrong")
        index = build_media_file_index(self.b)
        self.assertEqual(index[("BV1DUPX", "video")].path, new.resolve())
        self.assertNotIn(("BV1DUPX", "audio"), index)

    def test_invalid_root_does_not_change_active_root(self):
        old = normalize_download_root(self.app.download_root)
        bad = self.tmp / "not-a-directory"
        bad.write_bytes(b"file")
        with self.assertRaises((NotADirectoryError, OSError)):
            self.app.switch_download_root(bad)
        self.assertEqual(normalize_download_root(self.app.download_root), old)
        self.assertEqual(self.app.repo.get_meta("active_download_root"), str(old))

    def test_preview_uses_new_root_state_instead_of_old_path(self):
        self._video("BV1PREV")
        a_file = self.a / "标题 [BV1PREV].mp4"
        a_file.write_bytes(b"a")
        self.app.check_files(1, "video")
        self.assertEqual(self.app.preview(1, DownloadOptions())[
            "stats"].get("DOWNLOADED"), 1)
        self.app.switch_download_root(self.b)
        preview = self.app.preview(1, DownloadOptions())
        self.assertEqual(preview["stats"].get("READY"), 1)
        self.assertEqual(preview["stats"].get("DOWNLOADED", 0), 0)


if __name__ == "__main__":
    unittest.main()
