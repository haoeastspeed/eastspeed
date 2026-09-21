# -*- coding: utf-8 -*-
"""底部实时总速度曲线（QPainter 自绘，轻量、无第三方依赖）。"""
from __future__ import annotations

from collections import deque

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QWidget

from .utils import format_speed


def _axis_label(value: float) -> str:
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.1f} M"
    if value >= 1024:
        return f"{value / 1024:.0f} K"
    return "0" if value < 1 else f"{value:.0f} B"


class SpeedChart(QWidget):
    def __init__(self, parent=None, seconds: int = 120):
        super().__init__(parent)
        self._data: deque[float] = deque([0.0] * seconds, maxlen=seconds)
        self._current = 0.0
        self.setMinimumHeight(84)
        self.setMaximumHeight(96)

    def push(self, bytes_per_sec: float):
        self._current = max(0.0, bytes_per_sec)
        self._data.append(self._current)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(10, 8, -10, -10)

        # 背景卡片
        p.setPen(QPen(QColor("#E4E8EF"), 1))
        p.setBrush(QColor("#FBFCFE"))
        p.drawRoundedRect(rect, 8, 8)

        plot = rect.adjusted(66, 10, -14, -12)
        values = list(self._data)
        peak = max(values) if values else 0.0
        scale_peak = max(peak * 1.15, 1.0)

        # 横向网格 + 刻度
        p.setPen(QPen(QColor("#EEF1F5"), 1))
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        for i in range(4):
            y = plot.top() + plot.height() * i / 3.0
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            value = scale_peak * (1 - i / 3.0)
            p.setPen(QColor("#9AA4B2"))
            p.drawText(QRectF(rect.left() + 2, y - 9,
                              plot.left() - rect.left() - 6, 18),
                       Qt.AlignRight | Qt.AlignVCenter, _axis_label(value))
            p.setPen(QPen(QColor("#EEF1F5"), 1))

        if peak <= 0:
            p.setPen(QColor("#9AA4B2"))
            p.drawText(plot, Qt.AlignCenter, "暂无下载速度")
            p.end()
            return

        n = len(values)

        def point(i: int) -> QPointF:
            x = plot.left() + plot.width() * i / max(1, n - 1)
            y = plot.bottom() - plot.height() * (values[i] / scale_peak)
            return QPointF(x, y)

        # 面积填充
        fill = QPainterPath()
        fill.moveTo(plot.left(), plot.bottom())
        for i in range(n):
            fill.lineTo(point(i))
        fill.lineTo(plot.right(), plot.bottom())
        fill.closeSubpath()
        p.fillPath(fill, QColor(45, 127, 249, 38))

        # 折线
        line = QPainterPath()
        line.moveTo(point(0))
        for i in range(1, n):
            line.lineTo(point(i))
        pen = QPen(QColor("#2D7FF9"), 1.8)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawPath(line)

        # 当前值（右上角）
        head = point(n - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2D7FF9"))
        p.drawEllipse(head, 3.2, 3.2)
        p.setPen(QColor("#1456C0"))
        p.drawText(QRectF(plot.right() - 120, plot.top() - 6, 120, 18),
                   Qt.AlignRight | Qt.AlignVCenter, format_speed(self._current))
        p.end()
