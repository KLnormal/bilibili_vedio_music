"""Opt-in online smoke test for 花譜's complete submission history.

This test is deliberately skipped unless ``RUN_BILIBILI_LIVE=1`` is set.  It
always uses a temporary SQLite database/configuration and never touches the
developer's normal ``kaf_full_scan.db``.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from bilibili_crawler.app import App


@unittest.skipUnless(os.environ.get("RUN_BILIBILI_LIVE") == "1", "set RUN_BILIBILI_LIVE=1 to run online smoke tests")
class KafLiveScanTest(unittest.TestCase):
    def test_full_scan_reaches_history_end(self):
        with tempfile.TemporaryDirectory(prefix="bili-live-") as td:
            root = Path(td)
            cfg = root / "config.yaml"
            cfg.write_text(yaml.safe_dump({
                "database": {"path": str(root / "kaf-live.db")},
                "auth": {"cookie_file": str(root / "cookies.json")},
                "crawler": {
                    "page_size": 30,
                    "max_pages": 0,
                    # Do not enable the incremental consecutive-existing
                    # short-circuit during this full-history smoke test.
                    "stop_after_existing": 10,
                    "request_interval": 0.4,
                },
                "download": {"save_root": str(root / "downloads"), "ffmpeg_path": ""},
            }), encoding="utf-8")
            app = App(str(cfg), configure_logging=False)
            try:
                app.add_up(488970166)
                stats = app.scan(488970166)
                up = app.repo.get_up(488970166)
                self.assertIsNotNone(up)
                self.assertTrue(up.scan_complete, f"scan did not complete: {up}")
                self.assertEqual(up.scan_next_page, 1)
                # The public history is currently roughly 420 rows.  Keep a
                # lower bound to detect regressions to the old first-page-only
                # behaviour while allowing Bilibili to remove/merge entries.
                total = app.repo.count_videos(488970166)
                self.assertGreaterEqual(total, 300)
                self.assertGreater(stats.new + stats.existing, 0)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
