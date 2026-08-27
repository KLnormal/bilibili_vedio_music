"""Desktop application entry point and Qt workbench widgets."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Qt, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QInputDialog,
    QPushButton, QProgressBar, QScrollArea, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from ..app import App
from ..database.models import DownloadStatus, UpFilterSettings
from ..options import DownloadOptions, MEDIA_TYPES, QUALITY_TO_QN
from .controller import DesktopController
from .workers import ThumbnailRunnable


def _button(text: str, slot=None, primary: bool = False) -> QPushButton:
    button = QPushButton(text)
    button.setMinimumHeight(32)
    if primary:
        button.setProperty("primary", True)
    if slot:
        button.clicked.connect(slot)
    return button


def _item(value) -> QTableWidgetItem:
    return QTableWidgetItem("" if value is None else str(value))


def _fmt_duration(seconds) -> str:
    if seconds is None:
        return "-"
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class StatCard(QFrame):
    def __init__(self, title: str, color: str = "#89b4fa"):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.value = QLabel("0")
        self.value.setFont(QFont("Segoe UI", 22, QFont.Weight.Bold))
        self.value.setStyleSheet(f"color:{color};")
        layout.addWidget(self.title)
        layout.addWidget(self.value)

    def set_value(self, value) -> None:
        self.value.setText(str(value))


class OverviewPage(QWidget):
    def __init__(self, controller: DesktopController):
        super().__init__()
        self.controller = controller
        root = QVBoxLayout(self)
        title = QLabel("总览")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        grid = QGridLayout()
        self.cards = {}
        specs = [("total", "视频总数", "#89b4fa"), ("PENDING", "待下载", "#f9e2af"),
                 ("DOWNLOADING", "下载中", "#94e2d5"), ("DOWNLOADED", "已下载", "#a6e3a1"),
                 ("FAILED", "失败", "#f38ba8"), ("FILTERED", "已过滤", "#cba6f7"),
                 ("MISSING", "文件缺失", "#fab387")]
        for index, (key, label, color) in enumerate(specs):
            card = StatCard(label, color)
            self.cards[key] = card
            grid.addWidget(card, index // 4, index % 4)
        root.addLayout(grid)

        progress_box = QGroupBox("当前下载")
        progress_layout = QVBoxLayout(progress_box)
        # Reserve space for the QGroupBox caption; without this margin the
        # first label can be drawn over the title on high-DPI displays.
        progress_layout.setContentsMargins(12, 28, 12, 12)
        progress_layout.setSpacing(8)
        self.progress_label = QLabel("暂无任务")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        progress_layout.addWidget(self.progress_label)
        progress_layout.addWidget(self.progress)
        root.addWidget(progress_box)

        scan_box = QGroupBox("当前扫描")
        scan_layout = QVBoxLayout(scan_box)
        scan_layout.setContentsMargins(12, 28, 12, 12)
        scan_layout.setSpacing(8)
        self.scan_label = QLabel("暂无扫描")
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 100)
        scan_layout.addWidget(self.scan_label)
        scan_layout.addWidget(self.scan_progress)
        root.addWidget(scan_box)

        lower = QHBoxLayout()
        self.up_table = QTableWidget(0, 4)
        self.up_table.setHorizontalHeaderLabels(["UP 主", "UID", "视频数", "最近扫描"])
        self.up_table.horizontalHeader().setStretchLastSection(True)
        self.up_table.setAlternatingRowColors(True)
        lower.addWidget(self.up_table, 3)
        self.logs = QTextEdit(readOnly=True)
        self.logs.setPlaceholderText("运行日志")
        self._last_log_text = ""
        self._cached_status = None
        self._last_status_at = 0.0
        lower.addWidget(self.logs, 2)
        root.addLayout(lower, 1)

    def refresh(self) -> None:
        self.refresh_runtime()
        ups = self.controller.list_ups()
        self.up_table.setRowCount(len(ups))
        for row, up in enumerate(ups):
            self.up_table.setItem(row, 0, _item(up.name or "未命名"))
            self.up_table.setItem(row, 1, _item(up.mid))
            self.up_table.setItem(row, 2, _item(self.controller.app.repo.count_videos(up.mid)))
            self.up_table.setItem(row, 3, _item(up.last_crawl_time or "-"))

    def refresh_runtime(self) -> None:
        now = time.monotonic()
        if self._cached_status is None or now - self._last_status_at >= 1.0:
            self._cached_status = self.controller.status()
            self._last_status_at = now
        status = self._cached_status
        self.cards["total"].set_value(status["total"])
        for key, count in status["counts"].items():
            if key in self.cards:
                self.cards[key].set_value(count)
        snap = self.controller.app.state.snapshot()
        if snap.scan_active:
            self.scan_label.setText(
                f"{snap.current_up or 'UP'} · {snap.scan_status} · 新增 {snap.new_count} / 已有 {snap.existing_count} / 过滤 {snap.filtered_count}"
            )
            self.scan_progress.setRange(0, 0)
        else:
            self.scan_label.setText(snap.scan_status or "暂无扫描")
            self.scan_progress.setRange(0, 100)
            self.scan_progress.setValue(100 if snap.scan_status in {"扫描完成", "扫描已停止，可继续续扫"} or snap.scan_status.startswith("已达到扫描上限") else 0)
        p = snap.progress
        self.progress_label.setText(
            f"{p.title or '暂无任务'}  {p.bvid}  {p.speed or ''}".strip()
        )
        self.progress.setValue(int(p.percent or 0))
        text = "\n".join(snap.logs[-80:])
        if text != self._last_log_text:
            self._last_log_text = text
            self.logs.setPlainText(text)


class VideoTable(QTableWidget):
    HEADERS = ["封面", "标题", "BV 号", "发布时间", "时长", "状态", "原因", "本地文件"]

    def __init__(self, controller: DesktopController):
        super().__init__(0, len(self.HEADERS))
        self.controller = controller
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.setAlternatingRowColors(True)
        self.setSortingEnabled(True)
        self.setIconSize(QSize(72, 42))
        self.verticalHeader().setDefaultSectionSize(50)
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnWidth(0, 86)
        self.setColumnWidth(1, 300)
        self.setColumnWidth(2, 140)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._thumb_requested = set()
        self._row_pic_urls = {}
        self.verticalScrollBar().valueChanged.connect(self._load_visible_thumbnails)

    def load(self, videos) -> None:
        self.setSortingEnabled(False)
        self._thumb_requested.clear()
        self._row_pic_urls = {}
        self.setRowCount(len(videos))
        for row, video in enumerate(videos):
            cover = _item("加载中" if video.pic else "-")
            if video.pic:
                cover.setData(Qt.ItemDataRole.UserRole, video.pic)
            self.setItem(row, 0, cover)
            self.setItem(row, 1, _item(video.title))
            self.setItem(row, 2, _item(video.bvid))
            created = "-" if not video.created else datetime.fromtimestamp(video.created).strftime("%Y-%m-%d")
            self.setItem(row, 3, _item(created))
            self.setItem(row, 4, _item(_fmt_duration(video.duration)))
            self.setItem(row, 5, _item(video.download_status.value))
            self.setItem(row, 6, _item(video.filter_reason or video.download_error or ""))
            self.setItem(row, 7, _item(video.download_path or ""))
            if video.pic:
                self._row_pic_urls[row] = video.pic
        self.setSortingEnabled(True)
        QTimer.singleShot(0, self._load_visible_thumbnails)

    def _load_visible_thumbnails(self, *_args) -> None:
        if not self._row_pic_urls:
            return
        first = self.rowAt(0)
        last = self.rowAt(max(0, self.viewport().height() - 1))
        if first < 0:
            first = 0
        if last < first:
            last = min(self.rowCount() - 1, first + 12)
        for row in range(max(0, first - 2), min(self.rowCount(), last + 3)):
            url = self._row_pic_urls.get(row)
            if not url or url in self._thumb_requested:
                continue
            self._thumb_requested.add(url)
            import hashlib
            cache_root = Path(self.controller.app.config["download"]["save_root"]) / ".thumb_cache"
            cache = cache_root / f"{hashlib.sha1(url.encode()).hexdigest()}.jpg"
            runnable = ThumbnailRunnable(url, str(cache))
            runnable.signals.ready.connect(self._thumbnail_ready)
            from PySide6.QtCore import QThreadPool
            QThreadPool.globalInstance().start(runnable)

    def _thumbnail_ready(self, url: str, data: bytes) -> None:
        pixmap = QPixmap()
        if data:
            pixmap.loadFromData(data)
        for row in range(self.rowCount()):
            text = self.item(row, 0)
            # URLs are not stored in the item; use the title row's image only
            # when the asynchronous request completed successfully. The cache
            # is still useful on the next refresh, where this assignment repeats.
            if text and text.text() == "加载中" and text.data(Qt.ItemDataRole.UserRole) == url:
                if pixmap.isNull():
                    text.setText("封面失败")
                else:
                    text.setText("")
                    text.setIcon(QIcon(pixmap.scaled(72, 42, Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation)))
                break


class UpRulesDialog(QDialog):
    """Edit one UP's default duration/date rules."""

    def __init__(self, controller: DesktopController, mid: int, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mid = mid
        self.setWindowTitle(f"UP {mid} 筛选规则")
        self.resize(460, 520)
        root = QVBoxLayout(self)
        form = QFormLayout()
        settings = controller.get_up_filter_settings(mid)
        self.min_duration = QSpinBox(); self.min_duration.setRange(0, 86400)
        self.max_duration = QSpinBox(); self.max_duration.setRange(0, 86400)
        self.min_duration.setSpecialValueText("继承全局")
        self.max_duration.setSpecialValueText("继承全局")
        self.min_duration.setValue(settings.min_duration or 0)
        self.max_duration.setValue(settings.max_duration or 0)
        self.min_date = QLineEdit(settings.min_date or "0")
        self.max_date = QLineEdit(settings.max_date or "0")
        self.min_date.setPlaceholderText("例如 2025.01.01，0=不限")
        self.max_date.setPlaceholderText("例如 2026.01.01，0=不限")
        form.addRow("最小时长（秒）", self.min_duration)
        form.addRow("最大时长（秒）", self.max_duration)
        form.addRow("最早发布时间", self.min_date)
        form.addRow("最晚发布时间", self.max_date)
        root.addLayout(form)
        root.addWidget(QLabel("这些是该 UP 的默认时长和日期规则；本次下载可在‘任务与视频’中临时覆盖。"))
        root.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def save(self):
        min_d = self.min_duration.value() or None
        max_d = self.max_duration.value() or None
        if min_d is not None and max_d is not None and min_d > max_d:
            QMessageBox.warning(self, "规则无效", "最小时长不能大于最大时长")
            return
        min_date = self.min_date.text().strip() or "0"
        max_date = self.max_date.text().strip() or "0"
        try:
            from ..options import parse_date
            parsed_min, parsed_max = parse_date(min_date), parse_date(max_date)
            if parsed_min and parsed_max and parsed_min > parsed_max:
                raise ValueError("最早发布时间不能晚于最晚发布时间")
        except ValueError as exc:
            QMessageBox.warning(self, "日期无效", str(exc))
            return
        self.controller.save_up_filter_settings(UpFilterSettings(self.mid, min_d, max_d, min_date, max_date))
        self.accept()


class _KeywordListDialog(QDialog):
    """Edit one UP's keyword list (blacklist or specified-download list)."""

    title = "关键词"
    description = ""

    def _list(self):
        raise NotImplementedError

    def _add(self, keyword: str) -> bool:
        raise NotImplementedError

    def _remove(self, keyword: str) -> bool:
        raise NotImplementedError

    def __init__(self, controller: DesktopController, mid: int, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.mid = mid
        self.setWindowTitle(f"UP {mid} {self.title}")
        self.resize(430, 420)
        root = QVBoxLayout(self)
        root.addWidget(QLabel(self.description))
        self.blacklist = QListWidget()
        self.blacklist.addItems(self._list())
        self.blacklist.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        root.addWidget(self.blacklist, 1)
        add_row = QHBoxLayout()
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("输入关键词，例如：直播切片")
        self.keyword.returnPressed.connect(self.add_keyword)
        add_row.addWidget(self.keyword, 1)
        add_row.addWidget(_button("添加", self.add_keyword, True))
        add_row.addWidget(_button("删除选中", self.remove_keyword))
        root.addLayout(add_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def add_keyword(self):
        keyword = self.keyword.text().strip()
        current = [self.blacklist.item(i).text() for i in range(self.blacklist.count())]
        if keyword and keyword.casefold() not in {item.casefold() for item in current}:
            self.blacklist.addItem(keyword)
            self.blacklist.setCurrentRow(self.blacklist.count() - 1)
        self.keyword.clear()

    def remove_keyword(self):
        row = self.blacklist.currentRow()
        if row >= 0:
            self.blacklist.takeItem(row)

    def save(self):
        wanted = [self.blacklist.item(i).text() for i in range(self.blacklist.count())]
        current = self._list()
        for keyword in current:
            if keyword not in wanted:
                self._remove(keyword)
        for keyword in wanted:
            if keyword not in current:
                self._add(keyword)
        self.accept()


class BlacklistDialog(_KeywordListDialog):
    """Manage title blacklist keywords for exactly one UP."""

    title = "黑名单"
    description = "命中标题关键词的视频会在本次准备下载时被过滤。"

    def _list(self):
        return self.controller.list_blacklist(self.mid)

    def _add(self, keyword: str) -> bool:
        return self.controller.add_blacklist(self.mid, keyword)

    def _remove(self, keyword: str) -> bool:
        return self.controller.remove_blacklist(self.mid, keyword)


class AllowlistDialog(_KeywordListDialog):
    """Manage specified-download keywords for exactly one UP."""

    title = "指定下载名单"
    description = "仅标题命中指定下载名单关键词的视频会进入下载队列。"

    def _list(self):
        return self.controller.list_allowlist(self.mid)

    def _add(self, keyword: str) -> bool:
        return self.controller.add_allowlist(self.mid, keyword)

    def _remove(self, keyword: str) -> bool:
        return self.controller.remove_allowlist(self.mid, keyword)


class UpPage(QWidget):
    def __init__(self, controller: DesktopController, on_refresh):
        super().__init__()
        self.controller = controller
        self.on_refresh = on_refresh
        root = QVBoxLayout(self)
        header = QHBoxLayout()
        title = QLabel("UP 管理")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(_button("添加 UP", self.add_up, True))
        header.addWidget(_button("刷新", self.refresh))
        root.addLayout(header)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["UP 主", "UID", "视频数", "黑名单", "最近扫描", "启用"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        actions = QHBoxLayout()
        for text, slot in [("扫描", self.scan), ("规则设置", self.rules), ("黑名单", self.blacklist),
                           ("检查文件", self.check), ("预览", self.preview),
                           ("下载", self.download), ("删除", self.remove)]:
            actions.addWidget(_button(text, slot))
        actions.addStretch()
        root.addLayout(actions)

    def selected_mid(self) -> Optional[int]:
        row = self.table.currentRow()
        if row < 0:
            return None
        return int(self.table.item(row, 1).text())

    def refresh(self) -> None:
        ups = self.controller.list_ups()
        self.table.setRowCount(len(ups))
        for row, up in enumerate(ups):
            self.table.setItem(row, 0, _item(up.name or "未命名"))
            self.table.setItem(row, 1, _item(up.mid))
            self.table.setItem(row, 2, _item(self.controller.app.repo.count_videos(up.mid)))
            self.table.setItem(row, 3, _item(len(self.controller.list_blacklist(up.mid))))
            self.table.setItem(row, 4, _item(up.last_crawl_time or "-"))
            enabled = QCheckBox()
            enabled.setChecked(up.enabled)
            enabled.stateChanged.connect(lambda state, mid=up.mid: self.controller.set_up_enabled(mid, bool(state)))
            self.table.setCellWidget(row, 5, enabled)

    def add_up(self) -> None:
        value, ok = QInputDialog.getInt(self, "添加 UP", "请输入 UID：", minValue=1)
        if ok:
            self.controller.start_add_up(value)

    def scan(self):
        self.controller.start_scan(self.selected_mid())

    def check(self):
        self.controller.start_check(self.selected_mid(), "video")

    def rules(self):
        mid = self.selected_mid()
        if mid is None:
            return
        if UpRulesDialog(self.controller, mid, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_refresh()

    def blacklist(self):
        mid = self.selected_mid()
        if mid is None:
            return
        if BlacklistDialog(self.controller, mid, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.on_refresh()

    def preview(self):
        self.controller.start_preview(self.selected_mid(), DownloadOptions())

    def download(self):
        self.controller.start_download(self.selected_mid(), DownloadOptions())

    def remove(self):
        mid = self.selected_mid()
        if mid is None:
            return
        if QMessageBox.question(self, "确认删除", f"确定删除 UP {mid} 及其记录吗？") == QMessageBox.StandardButton.Yes:
            self.controller.remove_up(mid)
            self.refresh()
            self.on_refresh()


class TasksPage(QWidget):
    def __init__(self, controller: DesktopController):
        super().__init__()
        self.controller = controller
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)
        title = QLabel("任务与视频")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.mid = QComboBox()
        self.mid.addItem("全部 UP", None)
        self.mid.setMinimumWidth(140)
        self.quality = QComboBox()
        self.quality.addItems(["默认"] + list(QUALITY_TO_QN))
        self.quality.setMinimumWidth(140)
        self.media = QComboBox()
        self.media.addItems(["video", "audio"])
        self.media.setMinimumWidth(120)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题或 BV 号")
        self.search.setMinimumWidth(120)
        for label, widget in [("UP", self.mid), ("清晰度", self.quality), ("类型", self.media)]:
            controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        self.reset_filters = _button("恢复默认", self._load_filter_defaults)
        controls.addWidget(self.reset_filters)
        controls.addWidget(self.search, 1)
        root.addLayout(controls)

        self.filter_box = QGroupBox("本次下载筛选（仅本次任务，不修改 UP 默认规则）")
        filter_layout = QGridLayout(self.filter_box)
        # QGroupBox draws its caption inside the frame.  Reserve a complete
        # caption row so the first checkbox never overlaps that caption.
        filter_layout.setContentsMargins(16, 30, 16, 14)
        filter_layout.setHorizontalSpacing(10)
        filter_layout.setVerticalSpacing(10)
        self.filter_box.setMinimumHeight(122)
        self.duration_override = QLabel("时长筛选")
        self.duration_override.setToolTip("当前输入的时长范围会直接应用于本次预览/下载")
        self.duration_override.setMinimumWidth(116)
        self.min_duration = QSpinBox(); self.min_duration.setRange(0, 86400); self.min_duration.setValue(0)
        self.min_duration.setSuffix(" 秒"); self.min_duration.setSpecialValueText("不限")
        self.min_duration.setMinimumWidth(170)
        self.max_duration = QSpinBox(); self.max_duration.setRange(0, 86400); self.max_duration.setValue(86400)
        self.max_duration.setSuffix(" 秒")
        self.max_duration.setMinimumWidth(170)
        self.date_override = QLabel("发布时间筛选")
        self.date_override.setToolTip("当前输入的发布时间范围会直接应用于本次预览/下载")
        self.date_override.setMinimumWidth(116)
        self.min_date = QLineEdit("0"); self.min_date.setPlaceholderText("最早 YYYY.MM.DD，0=不限")
        self.max_date = QLineEdit("0"); self.max_date.setPlaceholderText("最晚 YYYY.MM.DD，0=不限")
        self.min_date.setMinimumWidth(170)
        self.max_date.setMinimumWidth(170)
        filter_layout.addWidget(self.duration_override, 0, 0)
        filter_layout.addWidget(QLabel("从"), 0, 1)
        filter_layout.addWidget(self.min_duration, 0, 2)
        filter_layout.addWidget(QLabel("到"), 0, 3)
        filter_layout.addWidget(self.max_duration, 0, 4)
        filter_layout.addWidget(self.date_override, 1, 0)
        filter_layout.addWidget(QLabel("从"), 1, 1)
        filter_layout.addWidget(self.min_date, 1, 2)
        filter_layout.addWidget(QLabel("到"), 1, 3)
        filter_layout.addWidget(self.max_date, 1, 4)

        # Keep blacklist management beside the date filters so the controls
        # remain visible while configuring a one-off download task.
        self.blacklist_box = QGroupBox("黑名单管理")
        self.blacklist_box.setMinimumWidth(320)
        self.blacklist_box.setMaximumWidth(360)
        blacklist_layout = QGridLayout(self.blacklist_box)
        blacklist_layout.setContentsMargins(10, 22, 10, 8)
        blacklist_layout.setHorizontalSpacing(8)
        blacklist_layout.setVerticalSpacing(8)
        self.blacklist_keyword = QLineEdit()
        self.blacklist_keyword.setPlaceholderText("输入关键词")
        self.blacklist_confirm = _button("确认", self._add_blacklist_keyword, True)
        self.blacklist_keyword.returnPressed.connect(self._add_blacklist_keyword)
        blacklist_layout.addWidget(self.blacklist_keyword, 0, 0)
        blacklist_layout.addWidget(self.blacklist_confirm, 0, 1)
        blacklist_layout.addWidget(QLabel("黑名单配置："), 1, 0)
        self.blacklist_enabled_button = _button("启用", self._enable_blacklist)
        self.blacklist_disabled_button = _button("禁用", self._disable_blacklist)
        for button in (self.blacklist_enabled_button, self.blacklist_disabled_button):
            button.setCheckable(True)
            button.setMinimumWidth(58)
        self.blacklist_enabled_group = QButtonGroup(self)
        self.blacklist_enabled_group.setExclusive(True)
        self.blacklist_enabled_group.addButton(self.blacklist_enabled_button)
        self.blacklist_enabled_group.addButton(self.blacklist_disabled_button)
        blacklist_layout.addWidget(self.blacklist_enabled_button, 1, 1)
        blacklist_layout.addWidget(self.blacklist_disabled_button, 1, 2)
        self.blacklist_settings = _button("设置", self._open_blacklist_settings)
        blacklist_layout.addWidget(self.blacklist_settings, 1, 3)
        filter_layout.addWidget(self.blacklist_box, 0, 5, 2, 2)

        self.allowlist_box = QGroupBox("指定下载名单")
        self.allowlist_box.setMinimumWidth(300)
        self.allowlist_box.setMaximumWidth(340)
        allowlist_layout = QGridLayout(self.allowlist_box)
        allowlist_layout.setContentsMargins(10, 22, 10, 8)
        allowlist_layout.setHorizontalSpacing(8)
        allowlist_layout.setVerticalSpacing(8)
        self.allowlist_keyword = QLineEdit()
        self.allowlist_keyword.setPlaceholderText("输入关键词")
        self.allowlist_confirm = _button("确认", self._add_allowlist_keyword, True)
        self.allowlist_keyword.returnPressed.connect(self._add_allowlist_keyword)
        allowlist_layout.addWidget(self.allowlist_keyword, 0, 0)
        allowlist_layout.addWidget(self.allowlist_confirm, 0, 1)
        allowlist_layout.addWidget(QLabel("名单配置："), 1, 0)
        self.allowlist_enabled_button = _button("启用", self._enable_allowlist)
        self.allowlist_disabled_button = _button("禁用", self._disable_allowlist)
        for button in (self.allowlist_enabled_button, self.allowlist_disabled_button):
            button.setCheckable(True)
            button.setMinimumWidth(58)
        self.allowlist_enabled_group = QButtonGroup(self)
        self.allowlist_enabled_group.setExclusive(True)
        self.allowlist_enabled_group.addButton(self.allowlist_enabled_button)
        self.allowlist_enabled_group.addButton(self.allowlist_disabled_button)
        allowlist_layout.addWidget(self.allowlist_enabled_button, 1, 1)
        allowlist_layout.addWidget(self.allowlist_disabled_button, 1, 2)
        self.allowlist_settings = _button("设置", self._open_allowlist_settings)
        allowlist_layout.addWidget(self.allowlist_settings, 1, 3)
        filter_layout.addWidget(self.allowlist_box, 0, 7, 2, 2)
        # Column 5 is intentionally non-stretching: blacklist management sits
        # directly beside the duration/date controls instead of drifting to
        # the far right as the window grows.
        filter_layout.setColumnStretch(5, 0)
        filter_layout.setColumnStretch(6, 0)
        filter_layout.setColumnStretch(7, 0)
        filter_layout.setColumnStretch(8, 1)
        self._load_filter_defaults()
        self._load_blacklist_enabled()
        self._load_allowlist_enabled()
        root.addWidget(self.filter_box)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 2, 0, 2)
        buttons.setSpacing(8)
        for text, slot, primary in [("扫描", self.scan, False), ("预览", self.preview, False),
                                    ("开始下载", self.download, True), ("暂停/恢复", self.pause, False),
                                    ("停止", self.stop, False), ("重试失败", self.retry, False)]:
            buttons.addWidget(_button(text, slot, primary))
        buttons.addStretch()
        root.addLayout(buttons)
        self.task_status = QLabel("任务未启动")
        self.task_status.setMinimumHeight(20)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 100)
        self.task_progress.setValue(0)
        root.addWidget(self.task_status)
        root.addWidget(self.task_progress)
        self.scan_status = QLabel("扫描未启动")
        self.scan_progress = QProgressBar()
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(0)
        root.addWidget(self.scan_status)
        root.addWidget(self.scan_progress)
        self.table = VideoTable(controller)
        root.addWidget(self.table, 1)
        self.log = QTextEdit(readOnly=True)
        self.log.setMaximumHeight(140)
        self._last_log_text = ""
        root.addWidget(self.log)
        self.search.textChanged.connect(self._filter)
        self.mid.currentIndexChanged.connect(self._load_blacklist_enabled)
        self.mid.currentIndexChanged.connect(self._load_allowlist_enabled)
        self.mid.currentIndexChanged.connect(self._load_filter_defaults)
        self.mid.currentIndexChanged.connect(self._refresh_media_view)
        self.media.currentIndexChanged.connect(self._refresh_media_view)
        controller.task_started.connect(self._task_started)
        controller.task_finished.connect(self._task_finished)
        controller.task_failed.connect(self._task_failed)

    def mid_value(self):
        return self.mid.currentData()

    def refresh_ups(self):
        current = self.mid.currentData()
        self.mid.blockSignals(True)
        self.mid.clear()
        self.mid.addItem("全部 UP", None)
        for up in self.controller.list_ups():
            self.mid.addItem(f"{up.name or up.mid} ({up.mid})", up.mid)
        index = max(0, self.mid.findData(current))
        self.mid.setCurrentIndex(index)
        self.mid.blockSignals(False)

    def refresh(self):
        self.refresh_ups()
        self._load_filter_defaults()
        self._refresh_media_view()
        self.refresh_runtime()

    def _refresh_media_view(self, *_args):
        media_type = self.media.currentText()
        self.table.setHorizontalHeaderItem(5, QTableWidgetItem(f"状态 ({media_type})"))
        self.table.load(self.controller.list_videos(self.mid_value(), media_type))
        self.refresh_runtime()

    def _load_filter_defaults(self, *_args):
        mid = self.mid_value()
        if mid is None:
            cfg = self.controller.settings()
            min_d = cfg.get("filter", {}).get("min_duration", 300)
            max_d = cfg.get("filter", {}).get("max_duration", 1800)
            min_date = max_date = "0"
        else:
            settings = self.controller.get_up_filter_settings(mid)
            cfg = self.controller.settings()
            min_d = settings.min_duration if settings.min_duration is not None else cfg["filter"]["min_duration"]
            max_d = settings.max_duration if settings.max_duration is not None else cfg["filter"]["max_duration"]
            min_date = settings.min_date or "0"
            max_date = settings.max_date or "0"
        for widget, value in ((self.min_duration, min_d), (self.max_duration, max_d),
                              (self.min_date, min_date), (self.max_date, max_date)):
            widget.blockSignals(True)
            widget.setValue(int(value)) if isinstance(widget, QSpinBox) else widget.setText(str(value))
            widget.blockSignals(False)

    def refresh_runtime(self):
        snapshot = self.controller.app.state.snapshot()
        text = "\n".join(snapshot.logs[-100:])
        if text != self._last_log_text:
            self._last_log_text = text
            self.log.setPlainText(text)
        if snapshot.scan_active:
            self.scan_status.setText(
                f"扫描：{snapshot.current_up or 'UP'} · {snapshot.scan_status} · 新增 {snapshot.new_count} / 已有 {snapshot.existing_count} / 过滤 {snapshot.filtered_count}"
            )
            self.scan_progress.setRange(0, 0)
        else:
            self.scan_status.setText(f"扫描：{snapshot.scan_status or '未启动'}")
            self.scan_progress.setRange(0, 100)
            self.scan_progress.setValue(100 if snapshot.scan_status in {"扫描完成", "扫描已停止，可继续续扫"} or snapshot.scan_status.startswith("已达到扫描上限") else 0)
        progress = snapshot.progress
        if progress.bvid:
            self.task_status.setText(
                f"下载中：{progress.title or progress.bvid}  {progress.speed or ''}".strip()
            )
            if progress.total and progress.total > 0:
                self.task_progress.setRange(0, 100)
                self.task_progress.setValue(int(progress.percent or 0))
            else:
                self.task_progress.setRange(0, 0)
        elif not self.controller.is_running("download"):
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(0)

    def _task_started(self, name, mid):
        if name == "download":
            self.task_status.setText("下载任务已启动，正在准备队列...")
            self.task_progress.setRange(0, 0)
        elif name == "scan":
            self.scan_status.setText("扫描任务已启动，正在获取投稿列表...")
            self.scan_progress.setRange(0, 0)

    def _task_finished(self, name, result):
        if name == "download":
            self.task_status.setText("下载任务已完成")
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(100)
        elif name == "scan":
            self.scan_status.setText("扫描任务已完成")
            self.scan_progress.setRange(0, 100)
            self.scan_progress.setValue(100)

    def _task_failed(self, name, message):
        if name == "download":
            self.task_status.setText(f"下载任务失败：{message}")
            self.task_progress.setRange(0, 100)
        elif name == "scan":
            self.scan_status.setText(f"扫描任务失败：{message}")
            self.scan_progress.setRange(0, 100)
            self.scan_progress.setValue(0)

    def _filter(self, text: str):
        query = text.casefold().strip()
        for row in range(self.table.rowCount()):
            title = self.table.item(row, 1).text().casefold()
            bvid = self.table.item(row, 2).text().casefold()
            self.table.setRowHidden(row, bool(query and query not in title and query not in bvid))

    def _toggle_duration_filter(self, enabled: bool):
        # Keep the editors interactive so users can prepare values before
        # enabling the override checkbox.  The checkbox still controls
        # whether those values are included in DownloadOptions.
        self.min_duration.setEnabled(True)
        self.max_duration.setEnabled(True)

    def _toggle_date_filter(self, enabled: bool):
        self.min_date.setEnabled(True)
        self.max_date.setEnabled(True)

    def _load_blacklist_enabled(self, *_args):
        enabled = bool(self.controller.settings().get("filter", {}).get("blacklist_enabled", True))
        self.blacklist_enabled_button.setChecked(enabled)
        self.blacklist_disabled_button.setChecked(not enabled)

    def _set_blacklist_enabled(self, enabled: bool) -> None:
        config = self.controller.settings()
        config.setdefault("filter", {})["blacklist_enabled"] = bool(enabled)
        self.controller.save_settings(config)
        self._load_blacklist_enabled()

    def _enable_blacklist(self):
        self._set_blacklist_enabled(True)

    def _disable_blacklist(self):
        self._set_blacklist_enabled(False)

    def _selected_keyword_mid(self) -> Optional[int]:
        mid = self.mid_value()
        if mid is None:
            QMessageBox.information(self, "请选择 UP", "请先在上方选择一个具体 UP，再管理其黑名单。")
        return mid

    def _add_blacklist_keyword(self):
        keyword = self.blacklist_keyword.text().strip()
        if not keyword:
            return
        mid = self._selected_keyword_mid()
        if mid is None:
            return
        if self.controller.add_blacklist(mid, keyword):
            self.blacklist_keyword.clear()
            self.controller.app.state.log(f"UP {mid} 黑名单已保存：{keyword}")
        else:
            QMessageBox.information(self, "黑名单", "该关键词已经存在。")

    def _open_blacklist_settings(self):
        mid = self._selected_keyword_mid()
        if mid is None:
            return
        if BlacklistDialog(self.controller, mid, self).exec() == QDialog.DialogCode.Accepted:
            self.controller.app.state.log(f"UP {mid} 黑名单配置已更新")

    def _load_allowlist_enabled(self, *_args):
        enabled = bool(self.controller.settings().get("filter", {}).get("allowlist_enabled", False))
        self.allowlist_enabled_button.setChecked(enabled)
        self.allowlist_disabled_button.setChecked(not enabled)

    def _set_allowlist_enabled(self, enabled: bool) -> None:
        config = self.controller.settings()
        config.setdefault("filter", {})["allowlist_enabled"] = bool(enabled)
        self.controller.save_settings(config)
        self._load_allowlist_enabled()

    def _enable_allowlist(self):
        self._set_allowlist_enabled(True)

    def _disable_allowlist(self):
        self._set_allowlist_enabled(False)

    def _add_allowlist_keyword(self):
        keyword = self.allowlist_keyword.text().strip()
        if not keyword:
            return
        mid = self._selected_keyword_mid()
        if mid is None:
            return
        if self.controller.add_allowlist(mid, keyword):
            self.allowlist_keyword.clear()
        else:
            QMessageBox.information(self, "指定下载名单", "该关键词已经存在。")

    def _open_allowlist_settings(self):
        mid = self._selected_keyword_mid()
        if mid is None:
            return
        if AllowlistDialog(self.controller, mid, self).exec() == QDialog.DialogCode.Accepted:
            self.controller.app.state.log(f"UP {mid} 指定下载名单配置已更新")

    def options(self):
        quality = self.quality.currentText()
        options = DownloadOptions(
            quality=None if quality == "默认" else quality,
            media_type=self.media.currentText(),
            min_duration=self.min_duration.value(),
            max_duration=self.max_duration.value(),
            min_date=self.min_date.text().strip() or "0",
            max_date=self.max_date.text().strip() or "0",
            date_override=True,
        )
        try:
            options.validate()
        except ValueError as exc:
            QMessageBox.warning(self, "本次筛选无效", str(exc))
            return None
        return options

    def scan(self):
        if not self.controller.start_scan(self.mid_value()):
            QMessageBox.information(self, "任务提示", "扫描任务已经在运行中")

    def preview(self):
        options = self.options()
        if options is None:
            return
        if not self.controller.start_preview(self.mid_value(), options):
            QMessageBox.information(self, "任务提示", "预览任务已经在运行中")

    def download(self):
        options = self.options()
        if options is None:
            return
        if self.controller.start_download(self.mid_value(), options):
            self.task_status.setText("下载任务已启动，正在准备队列...")
        else:
            QMessageBox.warning(self, "无法开始下载", "下载任务已经在运行中，或应用正在退出")
    def retry(self): self.controller.start_retry(self.mid_value(), self.media.currentText())
    def stop(self):
        if self.controller.is_running():
            self.controller.stop()
            self.task_status.setText("正在停止任务…")
            self.scan_status.setText("正在停止任务…")
        else:
            self.task_status.setText("当前没有运行中的任务")
    def pause(self): self.controller.pause(not self.controller.app.state.paused)


class SettingsPage(QWidget):
    def __init__(self, controller: DesktopController):
        super().__init__()
        self.controller = controller
        root = QVBoxLayout(self)
        title = QLabel("设置")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        form = QFormLayout()
        self.fields = {}
        for key, label in [("database.path", "数据库路径"), ("auth.cookie_file", "Cookie 文件"),
                           ("download.save_root", "下载目录"), ("download.ffmpeg_path", "ffmpeg 路径"),
                           ("logging.file", "日志文件")]:
            edit = QLineEdit()
            self.fields[key] = edit
            form.addRow(label, edit)
        self.concurrency = QSpinBox(); self.concurrency.setRange(1, 32)
        self.limit = QDoubleSpinBox(); self.limit.setRange(0.1, 10000); self.limit.setSuffix(" MB/s")
        self.min_duration = QSpinBox(); self.min_duration.setRange(0, 86400)
        self.max_duration = QSpinBox(); self.max_duration.setRange(0, 86400)
        self.quality = QComboBox(); self.quality.addItems(list(QUALITY_TO_QN))
        self.media = QComboBox(); self.media.addItems(list(MEDIA_TYPES))
        self.dash = QCheckBox("优先使用 DASH + ffmpeg")
        for label, widget in [("并发数", self.concurrency), ("限速", self.limit),
                              ("默认最小时长", self.min_duration), ("默认最大时长", self.max_duration),
                              ("清晰度", self.quality), ("媒体类型", self.media)]:
            form.addRow(label, widget)
        form.addRow("媒体流", self.dash)
        root.addLayout(form)
        root.addStretch()
        root.addWidget(_button("保存设置", self.save, True))
        self.load()

    @staticmethod
    def _get(cfg, path, default=""):
        value = cfg
        for part in path.split("."):
            value = value.get(part, {}) if isinstance(value, dict) else default
        return default if isinstance(value, dict) else value

    def load(self):
        cfg = self.controller.settings()
        for key, field in self.fields.items(): field.setText(str(self._get(cfg, key, "")))
        self.concurrency.setValue(int(self._get(cfg, "download.concurrency", 2)))
        self.limit.setValue(float(self._get(cfg, "download.max_speed_mbps", 40)))
        self.min_duration.setValue(int(self._get(cfg, "filter.min_duration", 300)))
        self.max_duration.setValue(int(self._get(cfg, "filter.max_duration", 1800)))
        self.quality.setCurrentText({64:"720p",80:"1080p",112:"1080p+",116:"1080p60",120:"4k"}.get(int(self._get(cfg,"download.qn",80)), "1080p"))
        self.media.setCurrentText(self._get(cfg, "download.type", "video"))
        self.dash.setChecked(bool(self._get(cfg, "download.prefer_dash", True)))

    def save(self):
        cfg = self.controller.settings()
        for path, field in self.fields.items():
            target = cfg
            parts = path.split(".")
            for part in parts[:-1]: target = target.setdefault(part, {})
            target[parts[-1]] = field.text().strip()
        cfg["download"].update({"concurrency": self.concurrency.value(), "max_speed_mbps": self.limit.value(),
                                "qn": QUALITY_TO_QN[self.quality.currentText()], "type": self.media.currentText(),
                                "prefer_dash": self.dash.isChecked()})
        cfg["filter"].update({"min_duration": self.min_duration.value(), "max_duration": self.max_duration.value()})
        try:
            path = self.controller.save_settings(cfg)
            QMessageBox.information(self, "设置已保存", f"已保存到：\n{path}")
        except Exception as exc: QMessageBox.critical(self, "保存失败", str(exc))


class LoginDialog(QDialog):
    def __init__(self, controller: DesktopController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Bilibili 登录")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        self.status = QLabel("点击按钮获取二维码")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url = QLineEdit(readOnly=True)
        self.url.setVisible(False)
        layout.addWidget(self.status); layout.addWidget(self.qr); layout.addWidget(self.url)
        self.login = _button("获取登录二维码", self.start, True)
        layout.addWidget(self.login)
        self.controller.qr_ready.connect(self.show_qr)
        self.controller.login_state_changed.connect(self.login_state)

    def start(self):
        self.status.setText("正在获取二维码...")
        self.login.setEnabled(False)
        self.controller.start_login()

    def show_qr(self, url: str, matrix):
        self.url.setText(url); self.url.setVisible(True)
        size = len(matrix); scale = max(3, min(8, 300 // size))
        image = QImage(size * scale, size * scale, QImage.Format.Format_RGB32)
        image.fill(QColor("white"))
        for y, row in enumerate(matrix):
            for x, cell in enumerate(row):
                if cell:
                    for dy in range(scale):
                        for dx in range(scale): image.setPixelColor(x * scale + dx, y * scale + dy, QColor("#11111b"))
        self.qr.setPixmap(QPixmap.fromImage(image))
        self.status.setText("请使用 Bilibili 手机 App 扫码确认")

    def login_state(self, ok: bool, message: str):
        self.status.setText("登录成功" if ok else message)
        if ok: self.login.setEnabled(True)


class MainWindow(QMainWindow):
    def __init__(self, controller: DesktopController):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("Bilibili 视频采集工作台")
        self.resize(1440, 900)
        self.setStyleSheet(STYLES)
        central = QWidget(); self.setCentralWidget(central)
        layout = QHBoxLayout(central); layout.setContentsMargins(0, 0, 0, 0)
        nav = QFrame(); nav.setObjectName("nav"); nav.setFixedWidth(210)
        nav_layout = QVBoxLayout(nav)
        brand = QLabel("BILIBILI\n采集工作台"); brand.setObjectName("brand")
        nav_layout.addWidget(brand)
        self.pages = QStackedWidget()
        self.overview = OverviewPage(controller)
        self.ups = UpPage(controller, self.refresh_all)
        self.tasks = TasksPage(controller)
        self.settings = SettingsPage(controller)
        for name, page in [("总览", self.overview), ("UP 管理", self.ups), ("任务与视频", self.tasks), ("设置", self.settings)]:
            button = _button(name, lambda checked=False, p=page: self.pages.setCurrentWidget(p))
            button.setProperty("nav", True); nav_layout.addWidget(button)
        nav_layout.addStretch()
        login = _button("登录 Bilibili", self.show_login, True); nav_layout.addWidget(login)
        layout.addWidget(nav); layout.addWidget(self.pages, 1)
        self.pages.addWidget(self.overview); self.pages.addWidget(self.ups); self.pages.addWidget(self.tasks); self.pages.addWidget(self.settings)
        self.statusBar().showMessage("就绪")
        controller.task_started.connect(lambda name, mid: self.statusBar().showMessage(f"任务开始：{name}"))
        controller.task_finished.connect(self.task_finished)
        controller.task_failed.connect(lambda name, msg: self.statusBar().showMessage(f"{name} 失败：{msg}"))
        controller.log_message.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        # Runtime indicators are cheap and update frequently. Tables are only
        # rebuilt on initial load, explicit refresh, or task completion.
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_runtime); self.timer.start(500)
        self.refresh_all()

    def activate_for_interaction(self) -> None:
        """Bring the native window to the foreground after it is shown.

        Windows may leave a newly-created Qt window behind the shell (or behind
        the terminal that launched it).  In that state the UI can be painted
        correctly while the user's first mouse clicks are still delivered to
        the old foreground window.  Calling this after the event loop starts
        makes the desktop entry point deterministic without installing a global
        mouse hook or stealing focus during normal navigation.
        """
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def refresh_all(self):
        self.overview.refresh(); self.ups.refresh(); self.tasks.refresh()

    def refresh_runtime(self):
        self.overview.refresh_runtime(); self.tasks.refresh_runtime()

    def task_finished(self, name, result):
        self.statusBar().showMessage(f"任务完成：{name}", 5000)
        if name == "preview" and isinstance(result, dict):
            stats = result.get("stats", {})
            QMessageBox.information(self, "预览结果", "\n".join(f"{k}: {v}" for k, v in stats.items()) or "没有视频")
        self.refresh_all()

    def show_login(self):
        dialog = LoginDialog(self.controller, self)
        dialog.show()
        self._login_dialog = dialog

    def closeEvent(self, event):
        if self.controller.is_running() and QMessageBox.question(self, "任务进行中", "仍有任务在运行，确定退出吗？") != QMessageBox.StandardButton.Yes:
            event.ignore(); return
        self.timer.stop()
        self.controller.close(); event.accept()


STYLES = """
QWidget { background:#1e1e2e; color:#cdd6f4; font-family:'Segoe UI','Microsoft YaHei'; font-size:13px; }
QFrame#nav { background:#181825; border-right:1px solid #313244; }
QLabel#brand { color:#89b4fa; font-size:22px; font-weight:700; padding:18px 8px; }
QLabel#pageTitle { color:#cdd6f4; font-size:26px; font-weight:700; padding:8px 0 14px; }
QLabel#muted { color:#a6adc8; }
QFrame#card, QGroupBox { background:#242438; border:1px solid #313244; border-radius:8px; }
QFrame#card { padding:8px; }
QPushButton { background:#313244; border:1px solid #45475a; border-radius:6px; padding:7px 14px; }
QPushButton:hover { background:#45475a; }
QPushButton[primary="true"] { background:#89b4fa; color:#11111b; border:0; font-weight:700; }
QPushButton[nav="true"] { text-align:left; background:transparent; border:0; padding:11px 14px; }
QPushButton[nav="true"]:hover { background:#313244; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QTableWidget { background:#181825; border:1px solid #45475a; border-radius:5px; padding:5px; }
QHeaderView::section { background:#313244; padding:7px; border:0; }
QTableWidget { gridline-color:#313244; }
QProgressBar { background:#181825; border:1px solid #45475a; border-radius:5px; text-align:center; }
QProgressBar::chunk { background:#89b4fa; border-radius:4px; }
QStatusBar { background:#181825; color:#a6adc8; }
"""


def _prepare_interactive_qt_platform() -> None:
    """Ensure the Windows desktop entry point uses a real input platform.

    Returns nothing so it can be called before QApplication is constructed.
    ``BILIBILI_DESKTOP_HEADLESS=1`` remains an explicit escape hatch for
    screenshot/CI jobs that intentionally do not need mouse input.
    """
    if sys.platform != "win32":
        return
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    allow_headless = os.environ.get("BILIBILI_DESKTOP_HEADLESS", "").strip().lower() in {
        "1", "true", "yes"
    }
    if platform in {"offscreen", "minimal", "minimalegl"} and not allow_headless:
        os.environ.pop("QT_QPA_PLATFORM", None)
        stream = sys.stderr or getattr(sys, "__stderr__", None)
        if stream is not None:
            print(
                "[desktop] removed inherited QT_QPA_PLATFORM=%s; interactive Windows UI is enabled."
                % platform,
                file=stream,
            )


def run_desktop(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bili-crawler desktop")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv or [])

    # ``QT_QPA_PLATFORM=offscreen`` is useful for the unit tests, but it also
    # creates a window that cannot receive real mouse input.  The desktop
    # command is explicitly an interactive entry point, so on Windows remove
    # an inherited headless platform selection unless the caller opts in for a
    # deliberate headless run (for example, screenshot generation in CI).
    _prepare_interactive_qt_platform()
    qt_app = QApplication.instance() or QApplication(sys.argv)
    app = App(config_path=args.config)
    controller = DesktopController(app)
    window = MainWindow(controller)
    window.show()
    # The queued call runs after the native HWND exists, which is more reliable
    # than activating it immediately after ``show()`` on Windows.
    QTimer.singleShot(0, window.activate_for_interaction)
    return qt_app.exec()
