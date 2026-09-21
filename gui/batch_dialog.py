# -*- coding: utf-8 -*-
"""批量下载对话框：一次粘贴多条链接（每行一个）。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.task_options import TaskOptions


class BatchAddDialog(QDialog):
    LATER = 2

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("批量下载")
        self.setMinimumSize(600, 420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.urls_edit = QPlainTextEdit()
        self.urls_edit.setPlaceholderText(
            "每行一个下载链接，例如：\nhttps://example.com/a.zip\nhttps://example.com/b.mp4"
        )
        form.addRow("下载链接（每行一条）:", self.urls_edit)

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(settings.save_dir)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(browse)
        form.addRow("保存到:", dir_row)

        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setValue(settings.connections)
        self.conn_spin.setSuffix(" 个连接")
        form.addRow("每任务连接数:", self.conn_spin)

        layout.addLayout(form)

        self.count_label = QLabel("已识别 0 条链接")
        self.count_label.setStyleSheet("color: #2D7FF9;")
        self.urls_edit.textChanged.connect(self._update_count)
        layout.addWidget(self.count_label)

        hint = QLabel("将为每条链接创建一个独立任务；.m3u8 链接自动按 HLS 视频下载。")
        hint.setStyleSheet("color: #6b7280;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_later = QPushButton("全部稍后下载")
        self.btn_start = QPushButton("全部开始下载")
        self.btn_start.setProperty("primary", True)
        self.btn_start.setDefault(True)
        self.btn_cancel = QPushButton("取消")
        self.btn_later.clicked.connect(lambda: self._finish(self.LATER))
        self.btn_start.clicked.connect(lambda: self._finish(self.Accepted))
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_later)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def urls(self) -> list[str]:
        seen, out = set(), []
        for raw in self.urls_edit.toPlainText().splitlines():
            url = raw.strip()
            if url.lower().startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    def _update_count(self):
        self.count_label.setText(f"已识别 {len(self.urls())} 条链接")

    def task_options(self) -> TaskOptions:
        return TaskOptions(
            save_dir=self.dir_edit.text().strip() or None,
            connections=self.conn_spin.value(),
        )

    def _finish(self, code: int):
        if not self.urls():
            self.urls_edit.setFocus()
            return
        self.done(code)
