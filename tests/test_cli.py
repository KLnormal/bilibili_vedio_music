"""CLI contract tests for media-aware status and download options."""
from __future__ import annotations

import unittest

from bilibili_crawler.cli.commands import _build_parser


class CliParserTest(unittest.TestCase):
    def test_status_and_check_accept_media_type(self):
        parser = _build_parser()
        status = parser.parse_args(["status", "1", "--type", "audio"])
        check = parser.parse_args(["check", "1", "--type", "audio"])
        self.assertEqual((status.mid, status.media_type), (1, "audio"))
        self.assertEqual((check.mid, check.media_type), (1, "audio"))

    def test_download_and_preview_keep_video_default(self):
        parser = _build_parser()
        download = parser.parse_args(["download"])
        preview = parser.parse_args(["preview"])
        self.assertEqual(download.media_type, "video")
        self.assertEqual(preview.media_type, "video")


if __name__ == "__main__":
    unittest.main()
