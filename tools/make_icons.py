# -*- coding: utf-8 -*-
"""生成浏览器扩展所需 PNG 图标（蓝底白色下载箭头）。

运行: python tools/make_icons.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import QApplication

app = QApplication([])


def draw_icon(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    margin = max(1, size // 16)
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    radius = size * 0.22

    grad = QLinearGradient(0, 0, 0, size)
    grad.setColorAt(0, QColor("#3D8BFF"))
    grad.setColorAt(1, QColor("#1E5FC4"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(rect, radius, radius)

    white = QColor("white")
    p.setBrush(white)
    pen = QPen(white)
    pen.setWidthF(size * 0.085)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)

    cx = size / 2
    # 竖线
    top = size * 0.24
    mid = size * 0.56
    p.drawLine(QPointF(cx, top), QPointF(cx, mid))
    # 箭头两翼
    wing = size * 0.17
    p.drawLine(QPointF(cx, mid + size * 0.06), QPointF(cx - wing, mid - wing * 0.55))
    p.drawLine(QPointF(cx, mid + size * 0.06), QPointF(cx + wing, mid - wing * 0.55))
    # 底部托盘
    tray_y = size * 0.78
    half = size * 0.24
    p.drawLine(QPointF(cx - half, tray_y), QPointF(cx + half, tray_y))
    p.end()
    return pm


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "browser_extension", "icons")
    os.makedirs(out, exist_ok=True)
    for s in (16, 32, 48, 128):
        draw_icon(s).save(os.path.join(out, f"icon{s}.png"), "PNG")
        print("saved", f"icon{s}.png")


if __name__ == "__main__":
    main()
