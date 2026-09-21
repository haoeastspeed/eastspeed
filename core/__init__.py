# -*- coding: utf-8 -*-
"""PyDownloader 自研下载引擎（纯 Python，不依赖 GUI 框架）。"""

from .config import Settings
from .engine import DownloadManager
from .probe import FileInfo, ProbeError, probe
from .ranges import RangeSet, SegmentAllocator
from .task import DownloadTask, TaskState

__all__ = [
    "Settings",
    "DownloadManager",
    "DownloadTask",
    "TaskState",
    "FileInfo",
    "ProbeError",
    "probe",
    "RangeSet",
    "SegmentAllocator",
]
