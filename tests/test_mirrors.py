# -*- coding: utf-8 -*-
"""多镜像源合并下载测试。

覆盖：
- 快镜像为慢主源聚合加速（连接粘性绑定 + 工作窃取，字节最终哈希一致）；
- 主源持续断流（drop=100）时故障转移到健康镜像并完成；
- 与主源大小不一致、或不支持 Range 的镜像在探测阶段被剔除，不影响下载。

测试用同一台本地服务器的不同 query/前缀模拟不同镜像（URL 不同即视为独立源）。
"""
import hashlib
import os
import shutil
import tempfile
import time
import unittest

from core.config import Settings
from core.engine import DownloadManager
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


class MirrorE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_appdata = os.environ.get("APPDATA")
        cls.appdata = tempfile.mkdtemp(prefix="dfs-appdata-")
        os.environ["APPDATA"] = cls.appdata
        cls.tmp = tempfile.mkdtemp(prefix="dfs_mirror_")
        cls.data = os.urandom(FILE_SIZE)
        with open(os.path.join(cls.tmp, "data.bin"), "wb") as f:
            f.write(cls.data)
        # 大小不一致的“伪镜像”
        with open(os.path.join(cls.tmp, "small.bin"), "wb") as f:
            f.write(os.urandom(1024 * 1024))
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

    def _run(self, primary, mirrors, connections=8, timeout=90, retries=20):
        save = tempfile.mkdtemp(dir=self.tmp)
        settings = Settings(
            save, block_size=BLOCK, connect_timeout=3, read_timeout=10,
            auto_resume=False, retries=retries,
        )
        mgr = DownloadManager(settings, callbacks={})
        mgr.start()
        task = mgr.add(primary, TaskOptions(
            connections=connections, mirrors=mirrors))
        tid = task.task_id
        t0 = time.time()
        while time.time() - t0 < timeout:
            if mgr.tasks[tid].state in TERMINAL:
                break
            time.sleep(0.1)
        dur = time.time() - t0
        task = mgr.tasks[tid]
        mgr.shutdown(pause_running=True)
        return task, os.path.join(save, "data.bin"), dur

    def _assert_intact(self, task, path):
        self.assertEqual(task.state, TaskState.COMPLETED,
                         f"任务未完成: {task.state} {task.error_msg}")
        self.assertTrue(os.path.exists(path), "最终文件不存在")
        self.assertEqual(os.path.getsize(path), FILE_SIZE)
        self.assertEqual(_sha(path), self.sha)

    def test_fast_mirror_accelerates_slow_primary(self):
        # 主源每连接仅 48KB/s（8 连接聚合约 384KB/s，单主源需约 22s），
        # 镜像不限速：粘性绑定快源 + 工作窃取，应在远短于单主源的时间完成
        primary = f"{self.prefix}/file/data.bin?kbps=48"
        mirror = f"{self.prefix}/file/data.bin"
        task, path, dur = self._run(primary, [mirror], timeout=60)
        self._assert_intact(task, path)
        self.assertGreaterEqual(len(task._sources), 2)
        self.assertLess(dur, 15, f"快镜像未体现聚合加速，耗时 {dur:.1f}s")

    def test_primary_dropping_fails_over_to_mirror(self):
        # 主源每条连接必在中途断流（drop=100），健康镜像应接管并完成
        primary = f"{self.prefix}/file/data.bin?drop=100"
        mirror = f"{self.prefix}/file/data.bin"
        task, path, dur = self._run(primary, [mirror], timeout=90)
        self._assert_intact(task, path)

    def test_size_mismatch_mirror_ignored(self):
        # 镜像文件大小与主源不一致：探测阶段剔除，仅用主源完成
        primary = f"{self.prefix}/file/data.bin"
        mirror = f"{self.prefix}/file/small.bin"
        task, path, _ = self._run(primary, [mirror], timeout=60)
        self._assert_intact(task, path)
        self.assertEqual(len(task._sources), 1, "大小不一致的镜像不应入池")

    def test_norange_mirror_ignored(self):
        # 镜像不支持 Range（始终 200）：探测阶段剔除，仅用主源分段完成
        primary = f"{self.prefix}/file/data.bin"
        mirror = f"{self.prefix}/norange/data.bin"
        task, path, _ = self._run(primary, [mirror], timeout=60)
        self._assert_intact(task, path)
        self.assertEqual(len(task._sources), 1, "不支持 Range 的镜像不应入池")


if __name__ == "__main__":
    unittest.main(verbosity=2)
