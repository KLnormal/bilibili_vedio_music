import tempfile
import unittest
from pathlib import Path

from bilibili_crawler.youtube import YouTubeService, identify_channel


class YouTubeInputTests(unittest.TestCase):
    def test_identifier_detection(self):
        self.assertEqual(identify_channel("12345"), ("bilibili", "12345"))
        self.assertEqual(identify_channel("@demo"), ("youtube", "@demo"))
        self.assertEqual(identify_channel("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv"), ("youtube", "UCabcdefghijklmnopqrstuv"))
        with self.assertRaises(ValueError):
            identify_channel("https://youtu.be/abcdefghijk")


class YouTubeDatabaseTests(unittest.TestCase):
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

