# -*- coding: utf-8 -*-
"""“下载网页资源”对话框：扫描一个网页（可跟进一层）里的媒体/文件链接，
勾选后批量创建下载任务。有限深度、默认仅同站、尊重 robots.txt。"""
from __future__ import annotations

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import grabber
from core.errors import DownloadError
from core.task_options import TaskOptions

CATEGORIES = ["视频", "音频", "图片", "文档", "压缩包", "程序"]


class _ScanWorker(QObject):
    done = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, url, settings, same_host, categories, depth, robots):
        super().__init__()
        self.url = url
        self.settings = settings
        self.same_host = same_host
        self.categories = categories
        self.depth = depth
        self.robots = robots

    def run(self):
        try:
            found = grabber.grab(
                self.url, self.settings, same_host=self.same_host,
                categories=self.categories, depth=self.depth,
                respect_robots=self.robots)
            self.done.emit(found)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class GrabSiteDialog(QDialog):
    def __init__(self, settings, manager, initial_url: str = "", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.manager = manager
        self._thread = None
        self._worker = None
        self.setWindowTitle("下载网页资源")
        self.setMinimumSize(680, 560)

        layout = QVBoxLayout(self)

        url_row = QHBoxLayout()
        self.url_edit = QLineEdit(initial_url)
        self.url_edit.setPlaceholderText("粘贴网页地址，例如 https://example.com/video.html")
        self.url_edit.setClearButtonEnabled(True)
        self.scan_btn = QPushButton("扫描")
        self.scan_btn.setProperty("primary", True)
        self.scan_btn.clicked.connect(self._scan)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.scan_btn)
        layout.addLayout(url_row)

        opt_row = QHBoxLayout()
        self.depth_combo = QComboBox()
        self.depth_combo.addItems(["仅当前页", "当前页 + 一层子页"])
        opt_row.addWidget(QLabel("抓取深度:"))
        opt_row.addWidget(self.depth_combo)
        self.same_cb = QCheckBox("仅同站点")
        self.same_cb.setChecked(True)
        self.robots_cb = QCheckBox("遵守 robots.txt")
        self.robots_cb.setChecked(True)
        opt_row.addWidget(self.same_cb)
        opt_row.addWidget(self.robots_cb)
        opt_row.addStretch(1)
        layout.addLayout(opt_row)

        cat_row = QHBoxLayout()
        cat_row.addWidget(QLabel("资源类型:"))
        self.cat_checks = {}
        for cat in CATEGORIES:
            cb = QCheckBox(cat)
            cb.setChecked(cat in ("视频", "音频"))
            self.cat_checks[cat] = cb
            cat_row.addWidget(cb)
        cat_row.addStretch(1)
        layout.addLayout(cat_row)

        self.status = QLabel("输入网页地址后点击「扫描」。")
        self.status.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.status)

        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.NoSelection)
        layout.addWidget(self.list, 1)

        sel_row = QHBoxLayout()
        all_btn = QPushButton("全选")
        none_btn = QPushButton("全不选")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        sel_row.addWidget(all_btn)
        sel_row.addWidget(none_btn)
        sel_row.addStretch(1)
        self.download_btn = QPushButton("下载选中")
        self.download_btn.setProperty("primary", True)
        self.download_btn.clicked.connect(self._download)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        sel_row.addWidget(self.download_btn)
        sel_row.addWidget(close_btn)
        layout.addLayout(sel_row)

    def _set_all(self, checked: bool):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(2 if checked else 0)

    def _selected_categories(self):
        return {c for c, cb in self.cat_checks.items() if cb.isChecked()}

    def _scan(self):
        url = self.url_edit.text().strip()
        if not url.lower().startswith(("http://", "https://")):
            self.status.setText("请输入合法的 http(s) 网页地址。")
            self.url_edit.setFocus()
            return
        cats = self._selected_categories()
        if not cats:
            self.status.setText("请至少选择一种资源类型。")
            return
        self.scan_btn.setEnabled(False)
        self.status.setText("正在扫描，请稍候…")
        self.list.clear()

        self._thread = QThread(self)
        self._worker = _ScanWorker(
            url, self.settings, self.same_cb.isChecked(), cats,
            self.depth_combo.currentIndex(), self.robots_cb.isChecked())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_scanned)
        self._worker.failed.connect(self._on_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_scanned(self, urls):
        self.scan_btn.setEnabled(True)
        for u in urls:
            item = QListWidgetItem(u)
            item.setCheckState(2)
            item.setToolTip(u)
            self.list.addItem(item)
        self.status.setText(f"扫描完成，发现 {len(urls)} 个资源。")

    def _on_failed(self, msg):
        self.scan_btn.setEnabled(True)
        self.status.setText(f"扫描失败：{msg}")

    def _download(self):
        urls = [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == 2]
        if not urls:
            self.status.setText("请先勾选要下载的资源。")
            return
        opt = TaskOptions(
            save_dir=self.settings.save_dir,
            connections=self.settings.connections,
            referer=self.url_edit.text().strip() or None,
        )
        ok, fail = 0, 0
        for u in urls:
            try:
                self.manager.add(u, opt)
                ok += 1
            except DownloadError:
                fail += 1
        msg = f"已创建 {ok} 个下载任务。"
        if fail:
            msg += f" {fail} 个无法处理（如 BT/磁力缺少 libtorrent 组件）。"
        self.status.setText(msg)
        if ok:
            self.accept()
