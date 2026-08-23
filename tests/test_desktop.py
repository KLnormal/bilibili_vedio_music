"""Offline smoke tests for the desktop filtering and scan-status controls."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml
from PySide6.QtWidgets import QApplication

from bilibili_crawler.app import App
from bilibili_crawler.database.models import Up, Video
from bilibili_crawler.desktop.app import BlacklistDialog, MainWindow


class DesktopControlsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.config = self.root / "config.yaml"
        self.config.write_text(yaml.safe_dump({
            "database": {"path": str(self.root / "state.db")},
            "auth": {"cookie_file": str(self.root / "cookies.json")},
            "download": {"save_root": str(self.root / "downloads"), "ffmpeg_path": ""},
        }), encoding="utf-8")
        self.app = App(str(self.config), configure_logging=False)
        self.app.repo.upsert_up(Up(mid=1, name="测试 UP"))
        self.window = MainWindow(self.app_controller())

    def app_controller(self):
        from bilibili_crawler.desktop.controller import DesktopController
        return DesktopController(self.app)

    def tearDown(self):
        self.window.timer.stop()
        self.window.close()

    def test_task_options_override_duration_and_date(self):
        tasks = self.window.tasks
        tasks.duration_override.setChecked(True)
        tasks.min_duration.setValue(120)
        tasks.max_duration.setValue(600)
        tasks.date_override.setChecked(True)
        tasks.min_date.setText("2025.01.01")
        tasks.max_date.setText("2025.12.31")
        tasks.quality.setCurrentText("1080p+")
        tasks.media.setCurrentText("audio")

        options = tasks.options()
        self.assertIsNotNone(options)
        self.assertEqual((options.min_duration, options.max_duration), (120, 600))
        self.assertEqual((options.min_date, options.max_date), ("2025.01.01", "2025.12.31"))
        self.assertTrue(options.date_filter_active)
        self.assertEqual((options.quality, options.media_type), ("1080p+", "audio"))

    def test_task_options_default_to_up_or_global_rules(self):
        options = self.window.tasks.options()
        self.assertIsNone(options.min_duration)
        self.assertIsNone(options.max_duration)
        self.assertIsNone(options.min_date)
        self.assertFalse(options.date_filter_active)

    def test_blacklist_has_dedicated_up_dialog(self):
        dialog = BlacklistDialog(self.window.controller, 1)
        dialog.keyword.setText("切片")
        dialog.add_keyword()
        dialog.save()
        self.assertEqual(self.app.repo.list_blacklist(1), ["切片"])

    def test_scan_status_bar_reflects_busy_and_finished(self):
        self.app.state.set_scan("测试 UP", "获取投稿列表...")
        self.app.state.set_scan_progress(3, 30, 4)
        self.window.tasks.refresh_runtime()
        self.assertEqual(self.window.tasks.scan_progress.minimum(), 0)
        self.assertEqual(self.window.tasks.scan_progress.maximum(), 0)
        self.assertIn("第 3 页", self.window.tasks.scan_status.text())

        self.app.state.finish_scan("扫描完成")
        self.window.tasks.refresh_runtime()
        self.assertEqual(self.window.tasks.scan_progress.value(), 100)
        self.assertIn("扫描完成", self.window.tasks.scan_status.text())


if __name__ == "__main__":
    unittest.main()
