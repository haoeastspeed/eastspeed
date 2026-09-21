# -*- coding: utf-8 -*-
"""IDM 式动态分段增强测试。

覆盖：
- 自适应块大小：对齐基础块、上下限钳制；
- 块内工作窃取：快连接从慢连接在途大段尾部劈块，区间不重不漏、
  每字节恰好完成一次，且给慢连接保留完整基础块；
- 病态服务器韧性：随机断流（drop）靠重试补齐、确定性慢连接
  （slowevery）下窃取真实发生、并发连接上限（maxconn）不死锁、
  服务器关闭 keep-alive（close）可完成；全部要求最终 SHA-256 一致。
"""
import hashlib
import os
import shutil
import tempfile
import time
import unittest

from core.config import Settings
from core.engine import DownloadManager
from core.ranges import SegmentAllocator
from core.task import TaskState
from core.task_options import TaskOptions
from tests.range_server import make_server

FILE_SIZE = 8 * 1024 * 1024
BLOCK = 256 * 1024
TERMINAL = {TaskState.COMPLETED, TaskState.ERROR}


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class AdaptiveBlockTests(unittest.TestCase):
    def test_align_and_clamp(self):
        B = 1024 * 1024
        a = SegmentAllocator(64 * B, block_size=B)
        self.assertEqual(a.dynamic_block, B)
        # 极高速率 -> 钳到上限 4B
        a.set_dynamic_block(int(120 * 1024 * 1024 * 0.25))
        self.assertEqual(a.dynamic_block, 4 * B)
        # 过小 -> 钳到基础块
        a.set_dynamic_block(B // 2)
        self.assertEqual(a.dynamic_block, B)
        # 非对齐 -> 向下对齐到基础块整数倍
        a.set_dynamic_block(3 * B + 12345)
        self.assertEqual(a.dynamic_block, 3 * B)


class WorkStealingUnitTests(unittest.TestCase):
    def test_steal_splits_tail_and_covers_every_byte_once(self):
        B = 1024 * 1024
        total = 16 * B
        a = SegmentAllocator(total, block_size=B)

        # 慢连接先领走尾部 4B（临时把动态块放到上限）
        a.set_dynamic_block(total)
        slow_lid, (ss, se) = a.acquire(7)
        self.assertEqual((ss, se), (12 * B, 16 * B))

        # 快连接用基础块把剩余缺口 [0,12B) 全部领走并完成（只领缺口，不偷）
        a.set_dynamic_block(B)
        fast = []
        while True:
            got = a.acquire(len(fast) % 6)
            if got is None:
                break
            fast.append(got)
        self.assertEqual(len(fast), 12)
        for lid, _ in fast:
            a.complete(lid)

        # 慢连接仅推进 1/8 块，此时文件远未完成
        a.report(slow_lid, ss + B // 8)
        self.assertFalse(a.is_complete())

        # 第一个窃取块必须落在慢连接区间内、对齐，并给慢连接留出完整基础块
        first = a.steal(0)
        self.assertIsNotNone(first)
        _f_lid, (qs, qe) = first
        self.assertEqual(qe, se)
        self.assertEqual(a.current_end(slow_lid), qs)
        self.assertGreater(qs, ss + B // 8 + B)

        # 其余快连接继续窃取，直到慢连接只剩必须由它自己收尾的部分
        stolen = [first]
        while True:
            got = a.steal(1)
            if got is None:
                break
            stolen.append(got)
        self.assertGreaterEqual(a.steal_count, 1)

        a.complete(slow_lid)
        for lid, _ in stolen:
            a.complete(lid)

        self.assertEqual(a.active_lease_count(), 0)
        self.assertTrue(a.is_complete())
        self.assertEqual(a.done_bytes(), total)  # 不重不漏

    def test_no_steal_when_tail_too_small(self):
        B = 1024 * 1024
        a = SegmentAllocator(4 * B, block_size=B)
        # 先由快连接领走并完成前 3 块
        for _ in range(3):
            lid, _ = a.acquire(1)
            a.complete(lid)
        # 最后一块由慢连接持有，且只剩不到一个窃取粒度的尾部
        lid, (_s, _e) = a.acquire(0)
        a.report(lid, _e - a.steal_unit // 2)
        # 剩余尾部不足以同时给原连接保留一个粒度并再劈出一个粒度，不可窃取
        self.assertIsNone(a.steal(2))
        a.complete(lid)
        self.assertTrue(a.is_complete())

    def test_chained_steals_keep_victim_consistent(self):
        # 同一慢租约可被连续窃取多次，受害者右端点单调收缩且始终在进度之后
        B = 1024 * 1024
        a = SegmentAllocator(8 * B, block_size=B)
        a.set_dynamic_block(8 * B)  # 钳到 4B
        lid, (s, e) = a.acquire(9)
        self.assertEqual((s, e), (4 * B, 8 * B))
        a.set_dynamic_block(B)
        # 领走并完成 [0,4B)
        while True:
            got = a.acquire(0)
            if got is None:
                break
            a.complete(got[0])
        a.report(lid, s + 1)  # 慢连接几乎没动
        ends = []
        thieves = []
        while True:
            got = a.steal(2)
            if got is None:
                break
            thieves.append(got)
            ends.append(a.current_end(lid))
        # 受害者端点单调不增，且始终大于已写位置 + 一个最小窃取粒度
        self.assertEqual(ends, sorted(ends, reverse=True))
        for cut in ends:
            self.assertGreater(cut, s + 1 + a.steal_unit)
        a.complete(lid)
        for t_lid, _ in thieves:
            a.complete(t_lid)
        self.assertTrue(a.is_complete())
        self.assertEqual(a.done_bytes(), 8 * B)


class DynamicSegmentE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_appdata = os.environ.get("APPDATA")
        cls.appdata = tempfile.mkdtemp(prefix="dfs-appdata-")
        os.environ["APPDATA"] = cls.appdata
        cls.tmp = tempfile.mkdtemp(prefix="dfs_dyn_")
        cls.src = os.path.join(cls.tmp, "data.bin")
        cls.data = os.urandom(FILE_SIZE)
        with open(cls.src, "wb") as f:
            f.write(cls.data)
        cls.sha = hashlib.sha256(cls.data).hexdigest()
        cls.httpd, cls.prefix = make_server(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.appdata, ignore_errors=True)
        if cls._old_appdata is not None:
            os.environ["APPDATA"] = cls._old_appdata

    def _run(self, query, connections=8, timeout=120, retries=12):
        save = tempfile.mkdtemp(dir=self.tmp)
        settings = Settings(
            save,
            block_size=BLOCK,
            connect_timeout=3,
            read_timeout=10,
            auto_resume=False,
            retries=retries,
        )
        mgr = DownloadManager(settings, callbacks={})
        mgr.start()
        task = mgr.add(self.prefix + query, TaskOptions(connections=connections))
        tid = task.task_id
        deadline = time.time() + timeout
        while time.time() < deadline:
            if mgr.tasks[tid].state in TERMINAL:
                break
            time.sleep(0.1)
        task = mgr.tasks[tid]
        mgr.shutdown(pause_running=True)
        return task, os.path.join(save, "data.bin")

    def _assert_complete_and_intact(self, task, path):
        self.assertEqual(task.state, TaskState.COMPLETED,
                         f"任务未完成: {task.state} {task.error_msg}")
        self.assertTrue(os.path.exists(path), "最终文件不存在")
        self.assertEqual(os.path.getsize(path), FILE_SIZE)
        self.assertEqual(_sha(path), self.sha)

    def test_random_dropped_connections_recover(self):
        # 每条连接 40% 概率在发送中途断流：靠重试 + 分段重领补齐，哈希必须一致
        task, path = self._run("/file/data.bin?drop=40", connections=8, timeout=120)
        self._assert_complete_and_intact(task, path)

    def test_slow_connections_get_stolen(self):
        # 每 4 条连接有 1 条慢 8 倍（确定性）：快连接应窃取慢连接尾部
        task, path = self._run(
            "/file/data.bin?kbps=512&slowevery=4&slowdiv=8",
            connections=8, timeout=90)
        self._assert_complete_and_intact(task, path)
        self.assertIsNotNone(task.allocator)
        self.assertGreater(
            task.allocator.steal_count, 0,
            "未发生工作窃取：慢连接尾部未被快连接接管")

    def test_server_connection_cap_no_deadlock(self):
        # 服务器同时只允许 2 条连接传数据，客户端开 8 条：应排队完成，不死锁
        task, path = self._run("/file/data.bin?maxconn=2", connections=8, timeout=90)
        self._assert_complete_and_intact(task, path)

    def test_server_closes_keepalive(self):
        # 每个响应后关闭 TCP 连接：客户端应能不断新建连接完成下载
        task, path = self._run("/file/data.bin?close=1", connections=8, timeout=90)
        self._assert_complete_and_intact(task, path)

    def test_network_jitter_prefetch_completes(self):
        # 每 32KB 随机停顿 0~60ms（高延迟抖动）：预取双缓冲应平滑读取、
        # reader 线程正常读到 EOF 并收尾，最终哈希一致且不死锁
        task, path = self._run("/file/data.bin?jitter=60", connections=8, timeout=120)
        self._assert_complete_and_intact(task, path)
        # 所有 worker 与其 reader 线程都应已退出，不残留
        for t in getattr(task, "_workers", []):
            self.assertFalse(t.is_alive(), "worker 线程残留")


if __name__ == "__main__":
    unittest.main(verbosity=2)
