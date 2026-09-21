# -*- coding: utf-8 -*-
"""界面格式化工具。"""
from __future__ import annotations

from core.category import category_of  # noqa: F401  （引擎/界面共用同一份分类）


def format_size(num: float) -> str:
    if not num:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while num >= 1024 and i < len(units) - 1:
        num /= 1024.0
        i += 1
    if i == 0:
        return f"{int(num)} B"
    return f"{num:.2f} {units[i]}"


def format_speed(bps: float) -> str:
    if bps < 1:
        return "-"
    return format_size(bps) + "/s"


def format_eta(seconds: int) -> str:
    if seconds < 0:
        return "-"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


CATEGORIES = ["全部", "进行中", "已完成", "视频", "音频", "文档", "程序", "压缩包", "其他"]


def match_category(category: str, snap: dict) -> bool:
    if category == "全部":
        return True
    state = snap.get("state")
    if category == "进行中":
        return state in ("queued", "connecting", "downloading", "paused", "error")
    if category == "已完成":
        return state == "completed"
    return category_of(snap.get("filename", "")) == category


# 分类圆点配色
CATEGORY_COLORS = {
    "全部": "#2D7FF9", "进行中": "#2D7FF9", "已完成": "#22A55A",
    "视频": "#9B59F0", "音频": "#F2994A", "文档": "#3E7CB1",
    "程序": "#16A394", "压缩包": "#D9A106", "其他": "#8A94A6",
}
