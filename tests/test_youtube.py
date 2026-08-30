import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bilibili_crawler.youtube import YouTubeService, identify_channel


class YouTubeInputTests(unittest.TestCase):
    def test_identifier_detection(self):
        self.assertEqual(identify_channel("12345"), ("bilibili", "12345"))
        self.assertEqual(identify_channel("@demo"), ("youtube", "@demo"))
        self.assertEqual(identify_channel("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"), ("youtube", "UCabcdefghijklmnopqrstuv"))
        with self.assertRaises(ValueError):
            identify_channel("https://youtu.be/abcdefghijk")


class YouTubeDatabaseTests(unittest.TestCase):
    def test_auth_options_support_cookie_file_and_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cookie = root / "youtube.cookies.txt"
            cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            service = YouTubeService(root / "youtube.db", root / "downloads", cookie_file=str(cookie))
            options = service._auth_options()
            self.assertEqual(options["cookiefile"], str(cookie.resolve()))
            service.cookies_from_browser = "Edge"
            options = service._auth_options()
            self.assertEqual(options["cookiesfrombrowser"], ("edge",))
            self.assertNotIn("cookiefile", options)
            service.close()

    def test_missing_cookie_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = YouTubeService(Path(tmp) / "youtube.db", Path(tmp) / "downloads", cookie_file=str(Path(tmp) / "missing.txt"))
            with self.assertRaisesRegex(RuntimeError, "Cookie 文件不存在"):
                service._auth_options()
            service.close()

    def test_browser_cookie_lock_error_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = YouTubeService(Path(tmp) / "youtube.db", Path(tmp) / "downloads", cookies_from_browser="edge")

            class FailingYoutubeDL:
                def __init__(self, _options):
                    pass

                def extract_info(self, _url, download=False):
                    raise RuntimeError("ERROR: Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")

            with mock.patch.object(service, "_ydl", return_value=mock.Mock(YoutubeDL=FailingYoutubeDL)):
                with self.assertRaisesRegex(RuntimeError, "无法读取 Edge Cookie 数据库.*完全退出 Edge"):
                    service.check_authentication()
            service.close()

    def test_browser_dpapi_error_recommends_netscape_cookie(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = YouTubeService(Path(tmp) / "youtube.db", Path(tmp) / "downloads", cookies_from_browser="edge")

            class FailingYoutubeDL:
                def __init__(self, _options):
                    pass

                def extract_info(self, _url, download=False):
                    raise RuntimeError("ERROR: Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")

            with mock.patch.object(service, "_ydl", return_value=mock.Mock(YoutubeDL=FailingYoutubeDL)):
                with self.assertRaisesRegex(RuntimeError, "无法解密 Edge Cookie.*v20.*Netscape Cookie"):
                    service.check_authentication()
            service.close()

    def test_database_and_media_state_are_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = YouTubeService(root / "youtube.db", root / "downloads")
            service.db.execute("INSERT INTO channel(channel_id,name,url) VALUES('UCdemo','Demo','https://example.invalid')")
            service.db.execute("INSERT INTO video(video_id,channel_id,title,duration,url) VALUES('abcdefghijk','UCdemo','hello',120,'https://youtu.be/abcdefghijk')")
            service.db.execute("INSERT OR IGNORE INTO media(video_id,media_type) VALUES('abcdefghijk','video'),('abcdefghijk','audio')")
            service.db.commit()
            service.db.execute("UPDATE media SET status='DOWNLOADED',download_path=? WHERE video_id='abcdefghijk' AND media_type='video'", (str(root / "downloads" / "Bilibili" / "bad.mp4"),))
            service.db.commit()
            self.assertEqual(service.status("UCdemo", "video")["counts"]["DOWNLOADED"], 1)
            self.assertEqual(service.status("UCdemo", "audio")["counts"]["PENDING"], 1)
            service.close()

    def test_scan_without_channel_scans_enabled_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = YouTubeService(root / "youtube.db", root / "downloads")
            service.db.executemany(
                "INSERT INTO channel(channel_id,name,url,enabled) VALUES(?,?,?,?)",
                [
                    ("UCenabled", "Enabled", "https://example.invalid/enabled", 1),
                    ("UCdisabled", "Disabled", "https://example.invalid/disabled", 0),
                ],
            )
            service.db.commit()

            class FakeYoutubeDL:
                urls = []

                def __init__(self, options):
                    self.options = options

                def extract_info(self, url, download=False):
                    self.urls.append(url)
                    return {"entries": [{"id": "abcdefghijk", "title": "hello", "duration": 120}]}

            fake_yt_dlp = mock.Mock(YoutubeDL=FakeYoutubeDL)
            with mock.patch.object(service, "_ydl", return_value=fake_yt_dlp):
                result = service.scan()
            self.assertEqual(result, {"new": 1, "existing": 0})
            self.assertEqual(service.db.execute("SELECT channel_id FROM video").fetchone()[0], "UCenabled")
            self.assertEqual(FakeYoutubeDL.urls, ["https://www.youtube.com/channel/UCenabled/videos"])
            service.close()
