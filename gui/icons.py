# -*- coding: utf-8 -*-
"""用 QPainter 运行时绘制的彩色矢量图标（任意 DPI 清晰，无需图片资源）。"""
from __future__ import annotations

import math

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PyQt5.QtWidgets import QApplication

from core.category import category_of

_SIZE = 64

# 各文件类型图标配色（与分类圆点一致）
_FILE_COLORS = {
    "视频": "#9B59F0", "音频": "#F2994A", "文档": "#3E7CB1",
    "程序": "#16A394", "压缩包": "#D9A106", "其他": "#8A94A6",
}
_file_icon_cache: dict[str, QIcon] = {}


def _new_pixmap():
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def _disc(p: QPainter, color: str):
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    m = _SIZE * 0.05
    p.drawEllipse(QRectF(m, m, _SIZE - 2 * m, _SIZE - 2 * m))


def icon_new() -> QIcon:
    pm, p = _new_pixmap()
    _disc(p, "#2D7FF9")
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    t = _SIZE * 0.11
    L = _SIZE * 0.42
    cx = cy = _SIZE / 2
    p.drawRoundedRect(QRectF(cx - L / 2, cy - t / 2, L, t), t / 2, t / 2)
    p.drawRoundedRect(QRectF(cx - t / 2, cy - L / 2, t, L), t / 2, t / 2)
    p.end()
    return QIcon(pm)


_app_icon_cache: QIcon | None = None


def app_icon() -> QIcon:
    """品牌应用图标（assets/app.ico）；资源缺失时回退到矢量绘制图标。"""
    global _app_icon_cache
    if _app_icon_cache is not None:
        return _app_icon_cache
    try:
        import os
        from core.branding import app_icon_path
        path = app_icon_path()
        if os.path.isfile(path):
            ic = QIcon(path)
            if not ic.isNull():
                _app_icon_cache = ic
                return ic
    except Exception:  # noqa: BLE001
        pass
    _app_icon_cache = icon_new()
    return _app_icon_cache


def icon_play() -> QIcon:
    pm, p = _new_pixmap()
    _disc(p, "#22A55A")
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    cx, cy = _SIZE / 2, _SIZE / 2
    tri = QPolygonF([
        QPointF(cx - _SIZE * 0.14, cy - _SIZE * 0.22),
        QPointF(cx - _SIZE * 0.14, cy + _SIZE * 0.22),
        QPointF(cx + _SIZE * 0.24, cy),
    ])
    p.drawPolygon(tri)
    p.end()
    return QIcon(pm)


def icon_pause() -> QIcon:
    pm, p = _new_pixmap()
    _disc(p, "#E5484D")
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    w, h, gap = _SIZE * 0.13, _SIZE * 0.4, _SIZE * 0.1
    cy = _SIZE / 2
    p.drawRoundedRect(QRectF(_SIZE / 2 - gap - w, cy - h / 2, w, h), w * 0.3, w * 0.3)
    p.drawRoundedRect(QRectF(_SIZE / 2 + gap, cy - h / 2, w, h), w * 0.3, w * 0.3)
    p.end()
    return QIcon(pm)


def icon_restart() -> QIcon:
    pm, p = _new_pixmap()
    _disc(p, "#16A394")
    cx = cy = _SIZE / 2
    r = _SIZE * 0.28
    pen = QPen(QColor("white"))
    pen.setWidthF(_SIZE * 0.1)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    # 约 300 度的弧，开口在右上方
    start, span = 70 * 16, 290 * 16
    p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), start, span)
    # 末端箭头（末端角度 70+290=360 即 3 点钟方向，顺时针切线向下）
    end_deg = math.radians((70 + 290) % 360)
    pos = (cx + r * math.cos(end_deg), cy - r * math.sin(end_deg) * -1)
    # Qt 坐标 y 向下：角度 a 的点为 (cx + r cos a, cy + r sin a)
    pos = (cx + r * math.cos(end_deg), cy + r * math.sin(end_deg))
    dx, dy = -math.sin(end_deg), math.cos(end_deg)  # 顺时针切向
    ah, aw = _SIZE * 0.2, _SIZE * 0.11
    tip = (pos[0] + dx * ah, pos[1] + dy * ah)
    bx, by = pos[0] - dx * ah * 0.5, pos[1] - dy * ah * 0.5
    px, py = -dy, dx
    arrow = QPolygonF([
        QPointF(*tip),
        QPointF(bx + px * aw, by + py * aw),
        QPointF(bx - px * aw, by - py * aw),
    ])
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    p.drawPolygon(arrow)
    p.end()
    return QIcon(pm)


def icon_delete() -> QIcon:
    pm, p = _new_pixmap()
    _disc(p, "#8A94A6")
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    cx = _SIZE / 2
    # 桶盖与提钮
    p.drawRoundedRect(QRectF(cx - _SIZE * 0.26, _SIZE * 0.26, _SIZE * 0.52,
                             _SIZE * 0.08), _SIZE * 0.03, _SIZE * 0.03)
    p.drawRoundedRect(QRectF(cx - _SIZE * 0.08, _SIZE * 0.19, _SIZE * 0.16,
                             _SIZE * 0.07), _SIZE * 0.02, _SIZE * 0.02)
    # 桶身
    p.drawRoundedRect(QRectF(cx - _SIZE * 0.21, _SIZE * 0.37, _SIZE * 0.42,
                             _SIZE * 0.38), _SIZE * 0.05, _SIZE * 0.05)
    # 桶身竖纹（用底色）
    p.setBrush(QColor("#8A94A6"))
    for i in (-1, 0, 1):
        x = cx + i * _SIZE * 0.1
        p.drawRoundedRect(QRectF(x - _SIZE * 0.022, _SIZE * 0.43,
                                 _SIZE * 0.044, _SIZE * 0.26),
                          _SIZE * 0.02, _SIZE * 0.02)
    p.end()
    return QIcon(pm)


def icon_settings() -> QIcon:
    pm, p = _new_pixmap()
    _disc(p, "#5B6B7F")
    cx = cy = _SIZE / 2
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    # 8 个齿
    tooth_w, tooth_l = _SIZE * 0.09, _SIZE * 0.13
    r_base = _SIZE * 0.24
    for i in range(8):
        p.save()
        p.translate(cx, cy)
        p.rotate(i * 45)
        p.drawRoundedRect(QRectF(-tooth_w / 2, -r_base - tooth_l * 0.55,
                                 tooth_w, tooth_l), tooth_w / 3, tooth_w / 3)
        p.restore()
    p.drawEllipse(QPointF(cx, cy), r_base, r_base)
    p.setBrush(QColor("#5B6B7F"))
    p.drawEllipse(QPointF(cx, cy), _SIZE * 0.1, _SIZE * 0.1)
    p.end()
    return QIcon(pm)


def file_icon(filename: str) -> QIcon:
    """带扩展名标签的彩色文件页图标，按类型着色（结果缓存）。"""
    name = filename or ""
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else "FILE"
    ext = ext[:4]
    cache_key = ext
    if cache_key in _file_icon_cache:
        return _file_icon_cache[cache_key]

    cat = category_of(name)
    color = _FILE_COLORS.get(cat, _FILE_COLORS["其他"])
    pm, p = _new_pixmap()

    x0, y0 = _SIZE * 0.22, _SIZE * 0.1
    w, h = _SIZE * 0.56, _SIZE * 0.8
    rad = _SIZE * 0.07
    rect = QRectF(x0, y0, w, h)
    band_h = _SIZE * 0.27

    # 整页按圆角裁剪：先铺类型色，再用白色覆盖上部，留下部色带
    clip = QPainterPath()
    clip.addRoundedRect(rect, rad, rad)
    p.setClipPath(clip)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRect(rect)
    p.setBrush(QColor("white"))
    p.drawRect(QRectF(x0, y0, w, h - band_h))
    p.setClipping(False)

    # 右上角折页
    fold = _SIZE * 0.17
    p.setBrush(QColor("#E7ECF2"))
    fold_tri = QPolygonF([
        QPointF(x0 + w - fold, y0 + _SIZE * 0.012),
        QPointF(x0 + w - _SIZE * 0.012, y0 + fold),
        QPointF(x0 + w - _SIZE * 0.012, y0 + _SIZE * 0.012),
    ])
    p.drawPolygon(fold_tri)
    p.setPen(QPen(QColor("#D5DBE3"), _SIZE * 0.012))
    p.drawLine(QPointF(x0 + w - fold, y0 + _SIZE * 0.02),
               QPointF(x0 + w - fold, y0 + fold - _SIZE * 0.01))
    p.drawLine(QPointF(x0 + w - fold, y0 + fold - _SIZE * 0.01),
               QPointF(x0 + w - _SIZE * 0.01, y0 + fold - _SIZE * 0.01))

    # 上部两道横线，模拟文档
    p.setPen(QPen(QColor("#C7CFDA"), _SIZE * 0.02, Qt.SolidLine, Qt.RoundCap))
    for i in range(2):
        yy = y0 + h * 0.36 + i * _SIZE * 0.13
        p.drawLine(QPointF(x0 + _SIZE * 0.09, yy),
                   QPointF(x0 + w - _SIZE * 0.09, yy))

    # 边框
    p.setPen(QPen(QColor(color), _SIZE * 0.012))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(rect, rad, rad)

    # 色带内扩展名
    p.setPen(QColor("white"))
    font = QFont("Arial")
    font.setBold(True)
    font.setPixelSize(int(band_h * 0.46))
    p.setFont(font)
    p.drawText(QRectF(x0, y0 + h - band_h, w, band_h), Qt.AlignCenter, ext)
    p.end()

    icon = QIcon(pm)
    _file_icon_cache[cache_key] = icon
    return icon


def icon_dot(color: str, size: int = 18) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    m = size * 0.18
    p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    return QIcon(pm)


def standard_icon(which) -> QIcon:
    return QApplication.style().standardIcon(which)
