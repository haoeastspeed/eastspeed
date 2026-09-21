# -*- coding: utf-8 -*-
"""速度统计与全局限速（令牌按读取量休眠实现）。"""
from __future__ import annotations

import threading
import time
from collections import deque


class SpeedMeter:
    """滑动窗口瞬时速度统计，线程安全。"""

    def __init__(self, window: float = 2.0):
        self.window = window
        self._samples: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()
        self._total = 0

    def add_bytes(self, n: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, n))
            self._total += n

    def speed(self) -> float:
        """返回最近窗口内的平均速度（字节/秒）。"""
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            if not self._samples:
                return 0.0
            span = now - self._samples[0][0]
            total = sum(n for _, n in self._samples)
            if span < 0.2:  # 样本太少时按已用时间估算，避免起步虚高
                return total / max(span, 1e-6)
            return total / self.window

    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._total = 0


class RateLimiter:
    """简单限速器：按字节配额均匀休眠。

    limit 为 0 表示不限速。限速值由所有 worker 共享（全局总速度）。
    """

    def __init__(self, limit_bps: int = 0):
        self.limit = limit_bps
        self._lock = threading.Lock()
        self._next_time = time.monotonic()

    def consume(self, n_bytes: int) -> None:
        if self.limit <= 0:
            return
        with self._lock:
            self._next_time += n_bytes / self.limit
            wait = self._next_time - time.monotonic()
        if wait > 0:
            time.sleep(min(wait, 0.5))
        with self._lock:
            # 落后真实时间太多则重置，避免长时间暂停后疯狂追赶
            if self._next_time < time.monotonic() - 1.0:
                self._next_time = time.monotonic()
