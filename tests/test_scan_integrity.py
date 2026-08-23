import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from bilibili_crawler.app import App
from bilibili_crawler.bilibili.user import (
    SubmissionPageError,
    UpProfile,
    VideoListItem,
    iter_submissions,
)
from bilibili_crawler.database.models import Up, Video


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    @staticmethod
    def anticrawl_params():
        return {}

    def get_json(self, _url, params=None, wbi=False):
        page = int(params["pn"])
        self.calls.append(page)
        return {"list": {"vlist": self.pages[page]}}


class ScanIntegrityTest(unittest.TestCase):
    def test_foreign_only_page_is_retryable_error(self):
        client = FakeClient({1: [{"bvid": "BVforeign", "mid": 999}]})
        with self.assertRaises(SubmissionPageError) as ctx:
            list(iter_submissions(client, 488970166, page_retries=0, page_backoff=0))
        self.assertEqual(ctx.exception.page, 1)

    def test_kaf_shape_scans_417_rows(self):
        pages = {page: [
            {"bvid": f"BV{page:02d}{i:02d}", "mid": 488970166,
             "length": "03:00"}
            for i in range(40)
        ] for page in range(1, 11)}
        pages[11] = [
            {"bvid": f"BV11{i:02d}", "mid": 488970166, "length": "03:00"}
            for i in range(17)
        ]
        client = FakeClient(pages)
        items = list(iter_submissions(client, 488970166, page_size=40,
                                      page_retries=0, page_backoff=0))
        self.assertEqual(len(items), 417)
        self.assertEqual(client.calls, list(range(1, 12)))


class LegacyPartialScanTest(unittest.TestCase):
    def test_old_partial_up_is_scanned_past_existing_prefix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = root / "config.yaml"
            config.write_text(yaml.safe_dump({
                "database": {"path": str(root / "db.sqlite")},
                "auth": {"cookie_file": str(root / "cookies.json")},
                "download": {"save_root": str(root / "downloads"), "ffmpeg_path": ""},
                "crawler": {"page_size": 40, "max_pages": 0,
                             "stop_after_existing": 10, "request_timeout": 1,
                             "retries": 1, "retry_backoff": 0,
                             "request_interval": 0},
            }), encoding="utf-8")
            app = App(str(config), configure_logging=False)
            try:
                app.repo.upsert_up(Up(
                    mid=488970166, name="花谱_kaf",
                    first_crawl_time="2026-01-01T00:00:00Z",
                ))
                for i in range(33):
                    app.repo.insert_video(Video(
                        bvid=f"BV{i:03d}", mid=488970166,
                        title="old", duration=180,
                    ))

                def submissions(*_args, **_kwargs):
                    for i in range(417):
                        yield VideoListItem(
                            bvid=f"BV{i:03d}", title="video",
                            duration_text="03:00", owner_mid=488970166,
                        )

                with mock.patch(
                    "bilibili_crawler.crawler.user_crawler.get_up_profile",
                    return_value=UpProfile(488970166, "花谱_kaf"),
                ), mock.patch(
                    "bilibili_crawler.crawler.user_crawler.iter_submissions",
                    submissions,
                ):
                    stats = app.crawler.crawl_up(488970166)
                self.assertEqual(stats.new, 384)
                self.assertEqual(app.repo.count_videos(488970166), 417)
                self.assertTrue(app.repo.get_up(488970166).scan_complete)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
