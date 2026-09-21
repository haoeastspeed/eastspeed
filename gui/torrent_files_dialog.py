# -*- coding: utf-8 -*-
"""BT 种子文件勾选对话框（添加 .torrent 前选择要下载的文件）。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


def human_size(n: int) -> str:
    f = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.2f} {unit}"
        f /= 1024
    return f"{f:.2f} TB"


# 常见视频扩展名，便于“只选视频”一键勾选
VIDEO_EXTS = (
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mpg", ".mpeg", ".ts", ".rmvb", ".rm", ".3gp", ".iso",
)


class TorrentFilesDialog(QDialog):
    """列出种子内全部文件供勾选，:meth:`selected_files` 返回相对路径列表。"""

    def __init__(self, torrent_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择种子中要下载的文件")
        self.setMinimumSize(560, 440)

        import libtorrent as lt
        self._ti = lt.torrent_info(torrent_path)
        files = self._ti.files()

        root = QVBoxLayout(self)
        title = QLabel(f"种子：{self._ti.name()}")
        title.setStyleSheet("font-weight: bold;")
        root.addWidget(title)

        bar = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_none = QPushButton("全不选")
        btn_video = QPushButton("只选视频")
        btn_all.clicked.connect(lambda: self._set_all(True))
        btn_none.clicked.connect(lambda: self._set_all(False))
        btn_video.clicked.connect(self._set_only_video)
        bar.addWidget(btn_all)
        bar.addWidget(btn_none)
        bar.addWidget(btn_video)
        bar.addStretch(1)
        self.size_label = QLabel()
        bar.addWidget(self.size_label)
        root.addLayout(bar)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.setSelectionMode(QAbstractItemView.NoSelection)
        n = files.num_files()
        total = 0
        for i in range(n):
            rel = files.file_path(i).replace("\\", "/")
            size = files.file_size(i)
            total += size
            item = QListWidgetItem(f"{rel}    （{human_size(size)}）")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, rel)
            item.setData(Qt.UserRole + 1, size)
            self.list.addItem(item)
        self._total = total
        self.list.itemChanged.connect(self._update_size)
        root.addWidget(self.list, 1)

        hint = QLabel("未勾选的文件不会下载；磁力链接需先取得元数据，默认下载全部文件。")
        hint.setStyleSheet("color: #6b7280;")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_if_any)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._update_size()

    def _set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(state)

    def _set_only_video(self) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            rel = str(item.data(Qt.UserRole)).lower()
            item.setCheckState(
                Qt.Checked if rel.endswith(VIDEO_EXTS) else Qt.Unchecked)

    def _update_size(self, *_args) -> None:
        chosen = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                chosen += int(item.data(Qt.UserRole + 1) or 0)
        self.size_label.setText(
            f"已选 {human_size(chosen)} / 共 {human_size(self._total)}")

    def _accept_if_any(self) -> None:
        if not self.selected_files():
            return  # 一个都没选时不允许确定
        self.accept()

    def selected_files(self) -> list[str]:
        result = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                result.append(str(item.data(Qt.UserRole)))
        return result
