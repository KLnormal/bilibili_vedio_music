"""Offline unit tests for the core logic (no network access)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from bilibili_crawler.app import App
from bilibili_crawler.bilibili.video import Stream, VideoDetail, pick_best_leq
from bilibili_crawler.bilibili.user import SubmissionPageError, UpProfile, VideoListItem, iter_submissions
from bilibili_crawler.database.database import Database
from bilibili_crawler.database.models import DownloadStatus, Up, Video
from bilibili_crawler.database.repository import Repository
from bilibili_crawler.downloader.limiter import RateLimiter, mbps_to_bps
from bilibili_crawler.filter.blacklist_filter import blacklist_hit, blacklist_match
from bilibili_crawler.filter.decision import DecisionEngine
from bilibili_crawler.filter.duration_filter import DurationFilter
from bilibili_crawler.filter.explain import explain
from bilibili_crawler.options import DownloadOptions, parse_date, quality_name


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


class SubmissionPaginationTest(unittest.TestCase):
    class FakeClient:
        def __init__(self, pages, failures=None):
            self.pages = pages
            self.failures = dict(failures or {})
            self.calls = []

        @staticmethod
        def anticrawl_params():
            return {}

        def get_json(self, _url, params=None, wbi=False):
            page = int(params["pn"])
            self.calls.append(page)
            remaining = self.failures.get(page, 0)
            if remaining:
                self.failures[page] = remaining - 1
                from bilibili_crawler.bilibili.client import BilibiliError
                raise BilibiliError("HTTP 412 (risk control)")
            return {"list": {"vlist": self.pages.get(page, [])}}

    def test_keeps_mixed_related_rows_and_stops_on_short_page(self):
        client = self.FakeClient({
            1: [{"bvid": "BV1", "mid": 7, "length": "01:00"},
                {"bvid": "FOREIGN", "mid": 8, "length": "01:00"}],
            2: [{"bvid": "BV2", "mid": 7, "length": "02:00"}],
        })
        items = list(iter_submissions(client, 7, page_size=2, page_retries=0, page_backoff=0))
        # The space page may contain related V.W.P/Kamitsubaki uploads.  Keep
        # those rows so pagination matches the web UI's historical count.
        self.assertEqual([x.bvid for x in items], ["BV1", "FOREIGN", "BV2"])
        self.assertEqual(client.calls, [1, 2])

    def test_retries_same_page_and_reports_page_on_exhaustion(self):
        client = self.FakeClient({1: [{"bvid": "BV1", "mid": 7}]}, failures={1: 1})
        with mock.patch("bilibili_crawler.bilibili.user.time.sleep"):
            items = list(iter_submissions(client, 7, page_size=30, page_retries=1, page_backoff=0))
        self.assertEqual([x.bvid for x in items], ["BV1"])
        self.assertEqual(client.calls, [1, 1])

        failing = self.FakeClient({}, failures={3: 2})
        with mock.patch("bilibili_crawler.bilibili.user.time.sleep"):
            with self.assertRaises(SubmissionPageError) as ctx:
                list(iter_submissions(failing, 7, page_size=30, start_page=3,
                                      page_retries=1, page_backoff=0))
        self.assertEqual(ctx.exception.page, 3)

    def test_repeated_page_is_not_treated_as_infinite_history(self):
        page = [{"bvid": "BV1", "mid": 7}]
        client = self.FakeClient({1: page, 2: page})
        with self.assertRaises(SubmissionPageError):
            list(iter_submissions(client, 7, page_size=1, page_retries=0, page_backoff=0))


class ScanResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = self.tmp / "config.yaml"
        cfg.write_text(yaml.safe_dump({
            "database": {"path": str(self.tmp / "db.sqlite")},
            "auth": {"cookie_file": str(self.tmp / "cookies.json")},
            "download": {"save_root": str(self.tmp / "downloads"), "ffmpeg_path": ""},
        }), encoding="utf-8")
        self.app = App(str(cfg), configure_logging=False)
        self.app.repo.upsert_up(Up(mid=7, name="A"))

    def tearDown(self):
        self.app.close()

    def test_failed_page_is_resumed_without_incremental_short_circuit(self):
        from bilibili_crawler.bilibili.client import BilibiliError

        with mock.patch("bilibili_crawler.crawler.user_crawler.get_up_profile",
                        return_value=UpProfile(7, "A")), \
             mock.patch("bilibili_crawler.crawler.user_crawler.iter_submissions",
                        side_effect=SubmissionPageError(5, BilibiliError("412"))):
            self.app.crawler.crawl_up(7)
        up = self.app.repo.get_up(7)
        self.assertEqual((up.scan_next_page, up.scan_incomplete), (5, True))

        starts = []
        def resumed(_client, _mid, **kwargs):
            starts.append(kwargs["start_page"])
            kwargs["page_callback"](5, 1, 6)
            yield VideoListItem("BVresume", "resume", duration_text="01:00", owner_mid=7)

        with mock.patch("bilibili_crawler.crawler.user_crawler.get_up_profile",
                        return_value=UpProfile(7, "A")), \
             mock.patch("bilibili_crawler.crawler.user_crawler.iter_submissions", resumed):
            self.app.crawler.crawl_up(7)
        self.assertEqual(starts, [5])
        up = self.app.repo.get_up(7)
        self.assertEqual((up.scan_next_page, up.scan_incomplete), (1, False))
        self.assertTrue(self.app.repo.video_exists("BVresume"))


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

    def test_scan_cursor_persists(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        self.repo.set_scan_progress(1, 9, True)
        up = self.repo.get_up(1)
        self.assertEqual(up.scan_next_page, 9)
        self.assertTrue(up.scan_incomplete)
        self.repo.set_scan_progress(1, 1, False)
        up = self.repo.get_up(1)
        self.assertEqual((up.scan_next_page, up.scan_incomplete), (1, False))

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

    def test_recover_orphan_downloading(self):
        self.repo.upsert_up(Up(mid=1, name="A"))
        self.repo.insert_video(Video(bvid="BV1a", mid=1, title="a", download_status=DownloadStatus.DOWNLOADING))
        self.repo.insert_video(Video(bvid="BV1b", mid=1, title="b", download_status=DownloadStatus.DOWNLOADED))
        self.repo.insert_video(Video(bvid="BV1c", mid=1, title="c", download_status=DownloadStatus.DOWNLOADING))
        n = self.repo.recover_orphan_downloading(1)
        self.assertEqual(n, 2)
        self.assertEqual(self.repo.get_video("BV1a").download_status, DownloadStatus.PENDING)
        self.assertEqual(self.repo.get_video("BV1c").download_status, DownloadStatus.PENDING)
        self.assertEqual(self.repo.get_video("BV1b").download_status, DownloadStatus.DOWNLOADED)  # untouched


class DecisionEngineTest(unittest.TestCase):
    """v0.2 Phase 4: unified rule decision + explanation."""

    def setUp(self):
        self.engine = DecisionEngine(300, 1800, ["TEST"])

    def test_ready(self):
        v = Video(bvid="BV1a", mid=1, duration=600, title="ok", download_status=DownloadStatus.PENDING)
        self.assertEqual(self.engine.decide(v).decision, "READY")

    def test_filtered_duration(self):
        v = Video(bvid="BV1a", mid=1, duration=45, title="short", download_status=DownloadStatus.PENDING)
        d = self.engine.decide(v)
        self.assertEqual(d.decision, "FILTERED")
        self.assertEqual(d.reason, "duration_out_of_range")

    def test_filtered_blacklist(self):
        v = Video(bvid="BV1a", mid=1, duration=600, title="TESTDATAABC", download_status=DownloadStatus.PENDING)
        d = self.engine.decide(v)
        self.assertEqual(d.decision, "FILTERED")
        self.assertEqual(d.reason, "blacklist: TEST")

    def test_downloaded_vs_missing(self):
        v = Video(bvid="BV1a", mid=1, duration=600, title="t", download_status=DownloadStatus.DOWNLOADED, download_path="/no/such.mp4")
        self.assertEqual(self.engine.decide(v).decision, "MISSING")

    def test_failed(self):
        v = Video(bvid="BV1a", mid=1, duration=600, title="t", download_status=DownloadStatus.FAILED, download_error="boom")
        self.assertEqual(self.engine.decide(v).decision, "FAILED")

    def test_explain_output(self):
        v = Video(bvid="BV1a", mid=1, duration=600, title="TESTDATAABC", download_status=DownloadStatus.PENDING)
        d = self.engine.decide(v)
        text = explain(d, v, 300, 1800)
        self.assertIn("Decision: FILTERED", text)
        self.assertIn("Reason: blacklist: TEST", text)

    def test_date_filter(self):
        from datetime import datetime

        engine = DecisionEngine(300, 1800, [], datetime(2025, 10, 1), datetime(2026, 1, 25))

        def make(created):
            return Video(bvid="BV1a", mid=1, duration=600, title="ok",
                         created=created, download_status=DownloadStatus.PENDING)

        # 边界包含当天
        self.assertEqual(engine.decide(make(int(datetime(2025, 10, 1).timestamp()))).decision, "READY")
        self.assertEqual(engine.decide(make(int(datetime(2026, 1, 25).timestamp()))).decision, "READY")
        # 范围内
        self.assertEqual(engine.decide(make(int(datetime(2025, 12, 1).timestamp()))).decision, "READY")
        # 早于开始
        d = engine.decide(make(int(datetime(2025, 9, 1).timestamp())))
        self.assertEqual((d.decision, d.reason), ("FILTERED", "date_out_of_range"))
        # 晚于结束
        d = engine.decide(make(int(datetime(2026, 2, 1).timestamp())))
        self.assertEqual((d.decision, d.reason), ("FILTERED", "date_out_of_range"))
        # 缺发布时间
        d = engine.decide(make(None))
        self.assertEqual((d.decision, d.reason), ("FILTERED", "date_missing"))
        # 未设置日期规则时不参与过滤
        plain = DecisionEngine(300, 1800)
        self.assertEqual(plain.decide(make(None)).decision, "READY")


class BlacklistFilterTest(unittest.TestCase):
    """v0.2 Phase 3: case-insensitive contiguous-substring matching."""

    def test_match(self):
        self.assertTrue(blacklist_match("TESTDATAABC", "TEST"))
        self.assertTrue(blacklist_match("AAA TEST BBB", "TEST"))
        self.assertTrue(blacklist_match("testdataabc", "TEST"))  # case-insensitive

    def test_no_match(self):
        self.assertFalse(blacklist_match("TESAAAB", "TEST"))
        self.assertFalse(blacklist_match("abc", "TEST"))

    def test_blacklist_hit_returns_keyword(self):
        self.assertEqual(blacklist_hit("TESTDATAABC", ["广告", "TEST"]), "TEST")
        self.assertIsNone(blacklist_hit("hello", ["广告", "TEST"]))


class BlacklistRepositoryTest(unittest.TestCase):
    """v0.2 Phase 3: up_blacklist CRUD + schema migration."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.db = Database(self._tmp / "t.db")
        self.repo = Repository(self.db)
        self.repo.upsert_up(Up(mid=1, name="A"))

    def tearDown(self):
        self.db.close()

    def test_crud(self):
        self.assertTrue(self.repo.add_blacklist(1, "TEST"))
        self.assertTrue(self.repo.add_blacklist(1, "广告"))
        self.assertFalse(self.repo.add_blacklist(1, "TEST"))  # duplicate ignored
        self.assertEqual(self.repo.list_blacklist(1), ["TEST", "广告"])
        self.assertTrue(self.repo.remove_blacklist(1, "TEST"))
        self.assertEqual(self.repo.list_blacklist(1), ["广告"])

    def test_migration_skipped_to_filtered_and_filter_reason(self):
        # Build an old-style schema (no filter_reason, a SKIPPED row), then let
        # Database's idempotent migration upgrade it.
        import sqlite3

        path = self._tmp / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE up (mid INTEGER PRIMARY KEY, name TEXT NOT NULL DEFAULT '', "
            "face TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', "
            "first_crawl_time TEXT, last_crawl_time TEXT, enabled INTEGER NOT NULL DEFAULT 1, "
            "save_path TEXT NOT NULL DEFAULT '');"
            "CREATE TABLE video (bvid TEXT PRIMARY KEY, mid INTEGER NOT NULL, duration INTEGER, "
            "title TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '', "
            "pic TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', update_time TEXT, "
            "download_status TEXT NOT NULL DEFAULT 'PENDING', download_path TEXT NOT NULL DEFAULT '', "
            "download_time TEXT, download_error TEXT NOT NULL DEFAULT '');"
            "INSERT INTO video (bvid, mid, title, download_status) VALUES ('BV1a', 1, 'a', 'SKIPPED');"
        )
        conn.commit()
        conn.close()

        db = Database(path)
        repo = Repository(db)
        cols = [r[1] for r in db.connection.execute("PRAGMA table_info(video)")]
        self.assertIn("filter_reason", cols)
        self.assertEqual(repo.get_video("BV1a").download_status, DownloadStatus.FILTERED)
        db.close()


class DownloadOptionsTest(unittest.TestCase):
    """v0.2 Phase 2: quality mapping + media type."""

    def test_quality_to_qn_mapping(self):
        self.assertEqual(DownloadOptions(quality="720p").qn, 64)
        self.assertEqual(DownloadOptions(quality="1080p").qn, 80)
        self.assertEqual(DownloadOptions(quality="1080p+").qn, 112)
        self.assertEqual(DownloadOptions(quality="1080p60").qn, 116)
        self.assertEqual(DownloadOptions(quality="4k").qn, 120)
        self.assertIsNone(DownloadOptions().qn)

    def test_pick_best_leq_respects_tier(self):
        streams = [
            Stream(url="a", quality_id=120, bandwidth=9_000_000),
            Stream(url="b", quality_id=112, bandwidth=2_750_000),
            Stream(url="c", quality_id=80, bandwidth=1_800_000),
        ]
        self.assertEqual(pick_best_leq(streams, 112).quality_id, 112)  # never 120
        self.assertEqual(pick_best_leq(streams, 120).quality_id, 120)
        self.assertEqual(pick_best_leq(streams, 80).quality_id, 80)
        self.assertIsNone(pick_best_leq(streams, 16))

    def test_quality_name_reverse(self):
        self.assertEqual(quality_name(64), "720p")
        self.assertEqual(quality_name(120), "4k")
        self.assertEqual(quality_name(999), "999")

    def test_validate_media_type(self):
        with self.assertRaises(ValueError):
            DownloadOptions(media_type="mp3").validate()
        DownloadOptions(media_type="audio").validate()  # ok

    def test_validate_quality(self):
        with self.assertRaises(ValueError):
            DownloadOptions(quality="8k").validate()

    def test_validate_duration_order(self):
        with self.assertRaises(ValueError):
            DownloadOptions(min_duration=500, max_duration=300).validate()

    def test_parse_date_unlimited(self):
        self.assertIsNone(parse_date(None))
        self.assertIsNone(parse_date("0"))
        self.assertIsNone(parse_date(""))

    def test_parse_date_valid(self):
        from datetime import datetime

        self.assertEqual(parse_date("2025.10.01"), datetime(2025, 10, 1))

    def test_parse_date_invalid(self):
        with self.assertRaises(ValueError):
            parse_date("2025.13.01")
        with self.assertRaises(ValueError):
            parse_date("20251001")

    def test_date_filter_active(self):
        self.assertFalse(DownloadOptions().date_filter_active)
        self.assertFalse(DownloadOptions(min_date="0", max_date="0").date_filter_active)
        self.assertTrue(DownloadOptions(min_date="2025.10.01", max_date="0").date_filter_active)
        self.assertTrue(DownloadOptions(min_date="0", max_date="2026.01.25").date_filter_active)

    def test_date_order_validation(self):
        with self.assertRaises(ValueError):
            DownloadOptions(min_date="2026.01.25", max_date="2025.10.01").validate()


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

    def test_preview_does_not_modify_state(self):
        self.app.repo.upsert_up(Up(mid=1, name="A"))
        self.app.repo.insert_video(Video(bvid="BV1a", mid=1, duration=600, title="t",
                                         download_status=DownloadStatus.PENDING))
        before = self.app.repo.get_video("BV1a").download_status
        result = self.app.preview(1, DownloadOptions())
        after = self.app.repo.get_video("BV1a").download_status
        self.assertEqual(before, after)  # dry-run must not change state
        self.assertEqual(result["stats"].get("READY"), 1)

    def test_download_bv_bypasses_blacklist(self):
        # A BV whose title hits the UP blacklist must still download when
        # explicitly requested via download-bv.
        self.app.repo.upsert_up(Up(mid=1, name="A"))
        self.app.repo.add_blacklist(1, "TEST")
        detail = VideoDetail(bvid="BV1a", title="TESTDATAABC", duration=600, cid=1, mid=1)
        with mock.patch("bilibili_crawler.app.get_video_detail", return_value=detail), \
             mock.patch.object(self.app.downloader, "download", return_value=Path("/tmp/x.mp4")):
            results = self.app.download_bv(["BV1a"], DownloadOptions())
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0][1])  # success (not filtered by blacklist)


if __name__ == "__main__":
    unittest.main()
