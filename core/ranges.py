# -*- coding: utf-8 -*-
"""区间集合与 IDM 式动态分段分配器。

- 所有区间均为半开区间 ``[start, end)``，单位字节。
- :class:`RangeSet` 维护有序、互不相交的区间，支持插入合并与删除。
- :class:`SegmentAllocator` 实现 IDM 的动态分段策略：
  1. 连接空闲即在当前最大未完成区间尾部领取一个块，连接全程复用；
  2. **自适应块大小**：高速链路用大块降低请求/锁/磁盘开销，低速用小块；
  3. **块内工作窃取（work stealing）**：当未分配缺口耗尽、仍有连接在缓慢
     下载大段时，空闲连接从该慢租约的**尾部**劈走一块接手，被窃连接读到
     分割点即提前结束本次请求，把尾部让给快连接——无需等待连接完全卡死。

持久化时只保存 completed 区间；租约（allocated / leases）不持久化——
程序崩溃或暂停后，这些字节重新变为可领取状态，由新连接重下，天然断点续传。
"""
from __future__ import annotations

import bisect
import threading


class RangeSet:
    """有序、不相交的区间集合。"""

    __slots__ = ("_r",)

    def __init__(self, ranges=None):
        # 每个元素为 [start, end]，按 start 升序，互不相交
        self._r: list[list[int]] = []
        if ranges:
            for start, end in ranges:
                self.add(start, end)

    def add(self, start: int, end: int) -> None:
        """插入区间 [start, end)，自动与重叠/相邻区间合并。"""
        if end <= start:
            return
        starts = [r[0] for r in self._r]
        i = bisect.bisect_right(starts, end)
        j = i - 1
        while j >= 0 and self._r[j][1] >= start:
            start = min(start, self._r[j][0])
            end = max(end, self._r[j][1])
            j -= 1
        self._r[j + 1 : i] = [[start, end]]

    def remove_range(self, start: int, end: int) -> None:
        """从集合中挖去 [start, end)。"""
        if end <= start:
            return
        result: list[list[int]] = []
        for a, b in self._r:
            if b <= start or a >= end:
                result.append([a, b])
                continue
            if a < start:
                result.append([a, start])
            if b > end:
                result.append([end, b])
        self._r = result

    def covered_length(self) -> int:
        return sum(b - a for a, b in self._r)

    def gaps(self, total_start: int, total_end: int) -> list[list[int]]:
        """返回 [total_start, total_end) 与本集合的差集（即未覆盖区间）。"""
        result = []
        cur = total_start
        for a, b in self._r:
            if b <= cur:
                continue
            if a > cur:
                result.append([cur, min(a, total_end)])
            cur = max(cur, b)
            if cur >= total_end:
                break
        if cur < total_end:
            result.append([cur, total_end])
        return result

    def to_list(self) -> list[list[int]]:
        return [[a, b] for a, b in self._r]

    @classmethod
    def merge(cls, *sets: "RangeSet") -> "RangeSet":
        merged = RangeSet()
        for rs in sets:
            for a, b in rs.to_list():
                merged.add(a, b)
        return merged

    def __len__(self) -> int:
        return len(self._r)

    def __iter__(self):
        return iter(self._r)


class SegmentAllocator:
    """动态分段分配器（线程安全），支持自适应块与块内工作窃取。

    租约（lease）记为 ``[start, end, owner, pos]``：
      - ``start/end`` 为本次 HTTP Range 请求覆盖的半开区间，``end`` 可能因
        被其他连接窃取尾部而缩小；
      - ``owner`` 为持有该租约的 worker 编号；
      - ``pos`` 为该连接已有效写入的最新位置。
    """

    def __init__(
        self,
        total_size: int,
        block_size: int = 2 * 1024 * 1024,
        completed=None,
        max_dynamic_blocks: int = 4,
    ):
        if total_size <= 0:
            raise ValueError("total_size must be positive")
        self.total_size = total_size
        self.block_size = block_size
        # 动态块上限 = 基础块的 max_dynamic_blocks 倍
        self.max_dynamic_block = block_size * max(1, max_dynamic_blocks)
        self.dynamic_block = block_size
        # 运行时工作窃取的最小粒度（独立于持久化基础块，可更细）：
        # 慢连接即便只持有一个基础块，也能从其尾部劈走 steal_unit 交给快连接。
        # 持久化仍按基础块网格记账，并由 complete() 按租约精确端点兜底，
        # 因此任意（非基础块对齐的）分割点都不会造成字节遗漏或重复。
        self.steal_unit = max(block_size // 8, 64 * 1024)
        self.completed = RangeSet(completed)
        self.allocated = RangeSet()
        self.leases: dict[int, list] = {}
        self._lid = 0
        self.lock = threading.Lock()
        # 观测计数（测试/调优用）
        self.steal_count = 0

    # ---------- 自适应块大小 ----------
    def set_dynamic_block(self, size: int) -> None:
        """按实时聚合速率调整块大小，对齐基础块并限制在 [block, max]。"""
        with self.lock:
            b = self.block_size * max(1, size // self.block_size)
            self.dynamic_block = max(self.block_size, min(b, self.max_dynamic_block))

    # ---------- 内部工具（调用方须持锁） ----------
    def _gap_lease_locked(self, owner, size: int):
        busy = RangeSet.merge(self.completed, self.allocated)
        available = busy.gaps(0, self.total_size)
        if not available:
            return None
        start, end = max(available, key=lambda x: x[1] - x[0])
        if end - start > size:
            cs = end - size
        else:
            cs = start
        return self._add_lease_locked(cs, end, owner)

    def _add_lease_locked(self, start: int, end: int, owner):
        self._lid += 1
        lid = self._lid
        self.leases[lid] = [start, end, owner, start]
        self.allocated.add(start, end)
        return lid, (start, end)

    # ---------- worker 主路径：领取 / 窃取 ----------
    def acquire(self, owner=None):
        """从最大未分配缺口尾部领取一个动态块，返回 (lid, (start, end)) 或 None。"""
        with self.lock:
            return self._gap_lease_locked(owner, self.dynamic_block)

    def steal(self, owner):
        """空闲连接从最慢在途租约的尾部劈走一块（工作窃取）。

        分割点对齐基础块，并给原连接至少保留一个完整基础块，避免抢占它
        即将写完的数据。返回 (lid, (split, end)) 或 None。
        """
        with self.lock:
            unit = self.steal_unit
            best = None  # (tail_len, victim_lid, split)
            for vid, lease in list(self.leases.items()):
                s, e, ow, pos = lease
                if ow == owner:
                    continue
                # 优先按动态块大小劈；空间不足时退而劈一个最小窃取粒度
                split = e - self.dynamic_block
                if split <= pos + unit:
                    split = e - unit
                split = (split // unit) * unit  # 分割点对齐窃取粒度
                if split <= pos + unit or split <= s:
                    continue
                tail = e - split
                if best is None or tail > best[0]:
                    best = (tail, vid, split)
            if best is None:
                return None
            _, vid, split = best
            victim = self.leases[vid]
            orig_end = victim[1]
            # 缩小受害者租约；尾部 [split, orig_end) 仍在 allocated 中，
            # 只是改由新租约接管。
            victim[1] = split
            new = self._add_lease_locked(split, orig_end, owner)
            self.steal_count += 1
            return new

    def next_for_worker(self, owner):
        """先领未分配缺口；没有则尝试窃取慢连接尾部。"""
        with self.lock:
            got = self._gap_lease_locked(owner, self.dynamic_block)
            if got is not None:
                return got
        return self.steal(owner)

    # ---------- 进度 / 租约生命周期 ----------
    def report(self, lid: int, pos: int) -> None:
        with self.lock:
            lease = self.leases.get(lid)
            if lease and pos > lease[3]:
                lease[3] = pos

    def current_end(self, lid: int):
        """租约当前右端点（可能因被窃取而缩小）。"""
        with self.lock:
            lease = self.leases.get(lid)
            return lease[1] if lease else None

    def complete(self, lid: int) -> None:
        """正常结束租约：把其最终区间标记完成（含被窃取后剩下的前缀）。"""
        with self.lock:
            lease = self.leases.pop(lid, None)
            if not lease:
                return
            s, e, _ow, _pos = lease
            self.completed.add(s, e)
            self.allocated.remove_range(s, e)

    def release_lid(self, lid: int) -> None:
        """出错/暂停：归还租约当前未完成区间；已被窃走的尾部不动。"""
        with self.lock:
            lease = self.leases.pop(lid, None)
            if not lease:
                return
            s, e, _ow, _pos = lease
            self.allocated.remove_range(s, e)

    def mark_completed_range(self, start: int, end: int) -> None:
        """流式下载中增量记账一个已完成基础块（不触碰租约表）。"""
        if end <= start:
            return
        with self.lock:
            self.completed.add(start, end)
            self.allocated.remove_range(start, end)

    # ---------- 统计 ----------
    def done_bytes(self) -> int:
        with self.lock:
            return self.completed.covered_length()

    def progress_bytes(self) -> int:
        """已有效落盘的字节数（去重，且含在途已写、尚未 mark 的部分）。

        等于 completed 与所有在途租约 ``[start, pos)`` 的并集长度：不同租约
        当前区间互不重叠，被窃取/重下的字节由 RangeSet 合并去重，因此在断流
        重试、工作窃取等情况下也不会重复计数（这是进度条/ETA/完成判定的
        权威来源，独立的“累计读取字节数”计数器可能因重下而超过总大小）。
        """
        with self.lock:
            union = RangeSet()
            for a, b in self.completed.to_list():
                union.add(a, b)
            for s, _e, _ow, pos in self.leases.values():
                if pos > s:
                    union.add(s, min(pos, _e))
            return union.covered_length()

    def remaining_bytes(self) -> int:
        return self.total_size - self.done_bytes()

    def unclaimed_bytes(self) -> int:
        with self.lock:
            busy = RangeSet.merge(self.completed, self.allocated)
            return busy.gaps(0, self.total_size) and sum(
                b - a for a, b in busy.gaps(0, self.total_size)
            ) or 0

    def active_lease_count(self) -> int:
        with self.lock:
            return len(self.leases)

    def is_complete(self) -> bool:
        return self.done_bytes() >= self.total_size

    def snapshot(self) -> list[list[int]]:
        with self.lock:
            return self.completed.to_list()

    # ---------- 旧版区间 API（保留兼容：无租约的简单调用与既有单元测试） ----------
    def claim(self):
        """领取一个块并返回 (start, end) 或 None（不含 owner 的简化形式）。"""
        got = self.acquire(owner=None)
        return got[1] if got else None

    def mark_done(self, start: int, end: int) -> None:
        """标记 [start, end) 完成，并清理被其完整覆盖的租约。"""
        with self.lock:
            self.completed.add(start, end)
            self.allocated.remove_range(start, end)
            for lid in [i for i, l in self.leases.items()
                        if l[0] >= start and l[1] <= end]:
                del self.leases[lid]

    def release(self, start: int, end: int) -> None:
        """放弃领取（出错/暂停），未完成部分归还可用池；已完成块保留。"""
        with self.lock:
            self.allocated.remove_range(start, end)
            for lid in [i for i, l in self.leases.items()
                        if l[0] >= start and l[1] <= end]:
                del self.leases[lid]
