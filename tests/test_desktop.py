"""Offline smoke tests for the desktop filtering and scan-status controls."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from bilibili_crawler.app import App
from bilibili_crawler.bilibili.video import VideoDetail
from bilibili_crawler.database.models import DownloadStatus, Up, Video
from bilibili_crawler.desktop.app import (
    BlacklistDialog,
    MainWindow,
    _prepare_interactive_qt_platform,
)
from bilibili_crawler.options import DownloadOptions


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
        self.window.show()
        self.qt.processEvents()

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

    def test_navigation_and_filter_controls_accept_mouse_clicks(self):
        nav = next(button for button in self.window.findChildren(QPushButton)
                   if button.text() == "任务与视频")
        QTest.mouseClick(nav, Qt.MouseButton.LeftButton)
        self.qt.processEvents()
        self.assertIs(self.window.pages.currentWidget(), self.window.tasks)
        QTest.mouseClick(self.window.tasks.duration_override, Qt.MouseButton.LeftButton)
        self.qt.processEvents()
        self.assertTrue(self.window.tasks.min_duration.isEnabled())

    def test_task_filter_caption_controls_buttons_and_status_do_not_overlap(self):
        tasks = self.window.tasks
        self.window.pages.setCurrentWidget(tasks)
        self.qt.processEvents()

        filter_top = tasks.filter_box.geometry().top()
        duration_top = tasks.duration_override.mapTo(tasks, QPoint(0, 0)).y()
        date_top = tasks.date_override.mapTo(tasks, QPoint(0, 0)).y()
        download_button = next(
            button for button in tasks.findChildren(QPushButton)
            if button.text() == "开始下载"
        )
        button_top = download_button.mapTo(tasks, QPoint(0, 0)).y()
        status_top = tasks.task_status.mapTo(tasks, QPoint(0, 0)).y()

        # The group title occupies the upper caption area; each filter row,
        # action row and status label must begin below the preceding region.
        self.assertGreaterEqual(duration_top, filter_top + 28)
        self.assertGreater(date_top, duration_top + tasks.duration_override.height())
        self.assertGreater(button_top, tasks.filter_box.geometry().bottom())
        self.assertGreater(status_top, button_top + download_button.height())

    def test_task_filter_control_widths_and_blacklist_adjacency(self):
        tasks = self.window.tasks
        self.window.pages.setCurrentWidget(tasks)
        self.qt.processEvents()
        self.assertGreaterEqual(tasks.mid.minimumWidth(), 140)
        self.assertGreaterEqual(tasks.quality.minimumWidth(), 140)
        self.assertGreaterEqual(tasks.media.minimumWidth(), 120)
        date_right = tasks.max_date.mapTo(tasks.filter_box, QPoint(tasks.max_date.width(), 0)).x()
        blacklist_left = tasks.blacklist_box.geometry().left()
        self.assertGreaterEqual(blacklist_left, date_right)
        self.assertLessEqual(blacklist_left - date_right, 24)

    def test_overview_group_caption_has_clearance(self):
        overview = self.window.overview
        label_top = overview.progress_label.mapTo(overview, QPoint(0, 0)).y()
        box_top = overview.progress_label.parentWidget().mapTo(overview, QPoint(0, 0)).y()
        self.assertGreaterEqual(label_top, box_top + 24)

    def test_desktop_window_can_reclaim_foreground_focus(self):
        """The interactive entry point must not leave a visible window inert."""
        self.window.activate_for_interaction()
        self.assertTrue(self.window.isEnabled())
        self.assertTrue(self.window.isVisible())

    def test_video_search_filters_rows_without_rebuilding_the_table(self):
        self.app.repo.insert_video(Video(bvid="BVmatch", mid=1, title="目标视频"))
        self.app.repo.insert_video(Video(bvid="BVother", mid=1, title="另一个视频"))
        tasks = self.window.tasks
        tasks.refresh()
        self.assertEqual(tasks.table.rowCount(), 2)
        tasks.search.setText("BVmatch")
        self.qt.processEvents()
        visible = [
            not tasks.table.isRowHidden(row) for row in range(tasks.table.rowCount())
        ]
        self.assertEqual(visible, [True, False])

    def test_desktop_entry_removes_inherited_headless_qt_platform(self):
        # Tests themselves use the offscreen Qt plugin, so patch the platform
        # marker only while exercising the Windows startup guard.
        with mock.patch("bilibili_crawler.desktop.app.sys.platform", "win32"):
            with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}, clear=False):
                os.environ.pop("BILIBILI_DESKTOP_HEADLESS", None)
                with mock.patch("bilibili_crawler.desktop.app.sys.stderr", None):
                    _prepare_interactive_qt_platform()
                self.assertNotIn("QT_QPA_PLATFORM", os.environ)

            with mock.patch.dict(os.environ, {
                "QT_QPA_PLATFORM": "offscreen",
                "BILIBILI_DESKTOP_HEADLESS": "1",
            }, clear=False):
                _prepare_interactive_qt_platform()
                self.assertEqual(os.environ.get("QT_QPA_PLATFORM"), "offscreen")
                os.environ.pop("BILIBILI_DESKTOP_HEADLESS", None)

    def test_task_options_default_to_up_or_global_rules(self):
        options = self.window.tasks.options()
        self.assertIsNone(options.min_duration)
        self.assertIsNone(options.max_duration)
        self.assertIsNone(options.min_date)
        self.assertFalse(options.date_filter_active)

    def test_task_options_are_used_by_preview_rules(self):
        created = int(datetime(2025, 6, 1).timestamp())
        self.app.repo.insert_video(Video(
            bvid="BVok", mid=1, title="符合条件", duration=180, created=created,
        ))
        self.app.repo.insert_video(Video(
            bvid="BVlong", mid=1, title="时长过长", duration=900, created=created,
        ))
        tasks = self.window.tasks
        tasks.duration_override.setChecked(True)
        tasks.min_duration.setValue(120)
        tasks.max_duration.setValue(600)
        tasks.date_override.setChecked(True)
        tasks.min_date.setText("2025.01.01")
        tasks.max_date.setText("2025.12.31")
        result = self.app.preview(1, tasks.options())
        self.assertEqual(result["stats"].get("READY"), 1)
        self.assertEqual(result["stats"].get("FILTERED"), 1)

    def test_blacklist_has_dedicated_up_dialog(self):
        dialog = BlacklistDialog(self.window.controller, 1)
        dialog.keyword.setText("切片")
        dialog.add_keyword()
        dialog.save()
        self.assertEqual(self.app.repo.list_blacklist(1), ["切片"])

    def test_task_page_blacklist_controls_persist_and_manage_selected_up(self):
        tasks = self.window.tasks
        tasks.refresh_ups()
        tasks.mid.setCurrentIndex(tasks.mid.findData(1))
        tasks.blacklist_keyword.setText("广告")
        tasks.blacklist_confirm.click()
        self.assertEqual(self.app.repo.list_blacklist(1), ["广告"])

        tasks.blacklist_disabled_button.click()
        self.assertFalse(self.app.config["filter"]["blacklist_enabled"])
        saved = yaml.safe_load(self.config.read_text(encoding="utf-8"))
        self.assertFalse(saved["filter"]["blacklist_enabled"])

        tasks.blacklist_enabled_button.click()
        self.assertTrue(self.app.config["filter"]["blacklist_enabled"])

    def test_blacklist_enabled_setting_controls_preview(self):
        self.app.repo.insert_video(Video(bvid="BVblocked", mid=1, title="广告通知", duration=600))
        self.app.add_blacklist(1, "广告")
        tasks = self.window.tasks
        self.assertEqual(self.app.preview(1, tasks.options())["stats"].get("FILTERED"), 1)
        config = self.window.controller.settings()
        config["filter"]["blacklist_enabled"] = False
        self.window.controller.save_settings(config)
        self.assertEqual(self.app.preview(1, tasks.options())["stats"].get("READY"), 1)

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

    def test_desktop_download_runs_end_to_end_without_network(self):
        video = Video(bvid="BVdownload", mid=1, title="测试下载", duration=600)
        self.app.repo.insert_video(video)
        calls = []
        output = self.root / "downloads" / "测试 UP" / "测试下载 [BVdownload].m4a"

        class FakeDownloader:
            client = None

            def download(self, detail, up_dir, limiter, progress, media_type, qn):
                calls.append((detail.bvid, up_dir, media_type, qn))
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"fake-audio")
                progress(4, 4, "1.0 MB/s")
                return output

        self.app.download_manager.downloader = FakeDownloader()
        detail = VideoDetail(bvid="BVdownload", title="测试下载", duration=600, cid=1, mid=1)
        with mock.patch("bilibili_crawler.bilibili.video.get_video_detail", return_value=detail):
            options = DownloadOptions(quality="1080p+", media_type="audio")
            self.assertTrue(self.window.controller.start_download(1, options))
            deadline = time.time() + 3
            while self.window.controller.is_running("download") and time.time() < deadline:
                self.qt.processEvents()
                time.sleep(0.02)
            self.qt.processEvents()

        self.assertFalse(self.window.controller.is_running("download"))
        downloaded = self.app.repo.get_video("BVdownload")
        self.assertEqual(downloaded.download_status, DownloadStatus.DOWNLOADED, downloaded.download_error)
        self.assertTrue(output.is_file())
        self.assertEqual(calls, [("BVdownload", "测试 UP", "audio", 112)])


if __name__ == "__main__":
    unittest.main()
