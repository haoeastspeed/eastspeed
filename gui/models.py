# -*- coding: utf-8 -*-
"""任务表格模型与委托。"""
from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QStyledItemDelegate

from .icons import file_icon
from .utils import format_size, format_eta, match_category

COLUMNS = ["文件名", "大小", "进度", "已下载", "速度", "剩余时间", "状态"]

_STATE_COLOR = {
    "completed": QColor("#15803D"),
    "error": QColor("#DC2626"),
    "paused": QColor("#6B7280"),
    "downloading": QColor("#1456C0"),
    "connecting": QColor("#B45309"),
    "queued": QColor("#6B7280"),
}


class TaskTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._all: list[dict] = []
        self._rows: list[dict] = []
        self._category = "全部"

    def set_category(self, category: str):
        self._category = category
        self._re_filter()

    def update_rows(self, snapshots):
        new_all = sorted(snapshots,
                         key=lambda s: s.get("created_at", 0), reverse=True)
        new_rows = [s for s in new_all
                    if match_category(self._category, s)]
        new_ids = [s["task_id"] for s in new_rows]
        old_ids = [s["task_id"] for s in self._rows]
        self._all = new_all
        if new_ids == old_ids:
            # 行集合与顺序未变（每秒刷新的常见情况）：只更新数据，
            # 不 reset，避免清空用户当前选中行与按钮状态
            self._rows = new_rows
            if new_rows:
                top = self.index(0, 0)
                bottom = self.index(len(new_rows) - 1, len(COLUMNS) - 1)
                self.dataChanged.emit(top, bottom)
            return
        self.beginResetModel()
        self._rows = new_rows
        self.endResetModel()

    def _re_filter(self, emit=True):
        if emit:
            self.beginResetModel()
        self._rows = [s for s in self._all
                      if match_category(self._category, s)]
        if emit:
            self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        s = self._rows[index.row()]
        col = index.column()

        if role == Qt.DecorationRole and col == 0:
            return file_icon(s["filename"])
        if role == Qt.DisplayRole:
            if col == 0:
                return s["filename"]
            if col == 1:
                return format_size(s["size"]) if s["size"] else "-"
            if col == 2:
                return s["progress"]
            if col == 3:
                return format_size(s["downloaded"])
            if col == 4:
                return format_size(s["speed"]) + "/s" if s["speed"] else "-"
            if col == 5:
                return format_eta(s["eta"])
            if col == 6:
                return s["state_cn"]
        elif role == Qt.ForegroundRole and col == 6:
            return _STATE_COLOR.get(s["state"])
        elif role == Qt.TextAlignmentRole:
            if col in (1, 2, 3, 4, 5):
                return Qt.AlignCenter
            if col == 6:
                return Qt.AlignCenter
        elif role == Qt.ToolTipRole:
            if col == 6 and s.get("error"):
                return f"错误：{s['error']}"
            if col == 0:
                tip = s["url"]
                if s.get("error"):
                    tip += f"\n错误: {s['error']}"
                return tip
        return None

    def task_id_at(self, row: int) -> str | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]["task_id"]
        return None

    def snapshot_at(self, row: int) -> dict:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return {}


class ProgressDelegate(QStyledItemDelegate):
    """绿色圆角进度条，直接自绘，风格统一。"""

    def paint(self, painter, option, index):
        value = float(index.data() or 0.0)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        r = option.rect.adjusted(10, 8, -10, -8)
        if r.height() < 4:
            painter.restore()
            return

        painter.setPen(QPen(Qt.NoPen))
        painter.setBrush(QColor("#E8ECF1"))
        painter.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        if value > 0:
            fill_w = max(float(r.height()), r.width() * value)
            painter.setBrush(QColor("#22A55A"))
            painter.drawRoundedRect(
                QRectF(r.x(), r.y(), fill_w, r.height()),
                r.height() / 2, r.height() / 2)

        painter.setPen(QColor("#374151"))
        painter.drawText(option.rect, Qt.AlignCenter, f"{value * 100:.0f}%")
        painter.restore()
