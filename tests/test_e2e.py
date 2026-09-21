# -*- coding: utf-8 -*-
"""端到端下载测试（启动本地 Range 服务器，真实走 HTTP）。

运行: python -m unittest tests.test_e2e -v
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest

# 在导入 core 之前重定向 APPDATA，避免污染真实配置目录
_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.task import TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402

from tests.range_server import make_server  # noqa: E402

FILE_SIZE = 8 * 1024 * 1024


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wait_state(manager: DownloadManager, tid: str, targets, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = manager.tasks[tid].snapshot()
        if snap["state"] in targets:
            return snap
        time.sleep(0.1)
    raise AssertionError(f"任务未进入 {targets}，当前状态 {manager.tasks[tid].state}")


def wait_progress(manager: DownloadManager, tid: str, frac: float, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = manager.tasks[tid].snapshot()
        if snap["size"] and snap["downloaded"] / snap["size"] >= frac:
            return snap
        if snap["state"] in (TaskState.ERROR, TaskState.COMPLETED):
            return snap
        time.sleep(0.1)
    raise AssertionError("等待进度超时")


class E2ETestCase(unittest.TestCase):
    httpd = None
    base = ""
    serve_dir = ""
    source_hash = ""

    @classmethod
    def setUpClass(cls):
        cls.serve_dir = tempfile.mkdtemp(prefix="pydl-served-")
        src = os.path.join(cls.serve_dir, "data.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(FILE_SIZE))
        cls.source_hash = sha256_file(src)
        cls.httpd, cls.base = make_server(cls.serve_dir)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.serve_dir, ignore_errors=True)
        shutil.rmtree(_TMP_APPDATA, ignore_errors=True)

    def setUp(self):
        # 每个用例独立的 APPDATA（tasks.json）与下载目录，彻底隔离
        self.appdata = tempfile.mkdtemp(prefix="pydl-appdata-")
        os.environ["APPDATA"] = self.appdata
        self.dl_dir = tempfile.mkdtemp(prefix="pydl-dl-")
        self.settings = Settings(
            save_dir=self.dl_dir,
            block_size=256 * 1024,
            connect_timeout=3,
            read_timeout=10,
            auto_resume=False,
            retries=3,
        )
        self.manager = DownloadManager(self.settings, callbacks={})
        self.manager.start()

    def tearDown(self):
        self.manager.shutdown(pause_running=True)
        shutil.rmtree(self.dl_dir, ignore_errors=True)
        shutil.rmtree(self.appdata, ignore_errors=True)

    def _final_path(self, name="data.bin"):
        return os.path.join(self.dl_dir, name)

    def test_01_full_download_integrity(self):
        tid = self.manager.add(
            f"{self.base}/file/data.bin",
            TaskOptions(connections=16, filename="full.bin"),
        ).task_id
        snap = wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR}, 60)
        self.assertEqual(snap["state"], TaskState.COMPLETED, snap.get("error"))
        self.assertEqual(snap["size"], FILE_SIZE)
        self.assertEqual(sha256_file(self._final_path("full.bin")), self.source_hash)

    def test_02_pause_and_resume(self):
        # 服务器每连接限速 256KB/s，8 连接聚合约 2MB/s，确保暂停时机确定
        tid = self.manager.add(
            f"{self.base}/file/data.bin?kbps=256",
            TaskOptions(connections=8, filename="paused.bin"),
        ).task_id
        wait_progress(self.manager, tid, 0.2, 60)
        self.manager.pause(tid)
        snap = wait_state(self.manager, tid, {TaskState.PAUSED}, 30)
        self.assertLess(snap["downloaded"], FILE_SIZE)
        self.assertTrue(os.path.exists(self._final_path("paused.bin") + ".pdlmeta"))
        # 继续后应完整
        self.manager.resume(tid)
        snap = wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR}, 60)
        self.assertEqual(snap["state"], TaskState.COMPLETED, snap.get("error"))
        self.assertEqual(sha256_file(self._final_path("paused.bin")), self.source_hash)

    def test_03_restart_recovery_from_meta(self):
        """模拟程序崩溃：新管理器从 tasks.json + pdlmeta 恢复续传。"""
        tid = self.manager.add(
            f"{self.base}/file/data.bin?kbps=256",
            TaskOptions(connections=8, filename="crash.bin"),
        ).task_id
        wait_progress(self.manager, tid, 0.4, 60)
        self.manager.shutdown(pause_running=True)
        # 确认中断现场：临时文件与 meta 均存在，最终文件不存在
        self.assertTrue(os.path.exists(self._final_path("crash.bin") + ".pdlmeta"))
        self.assertFalse(os.path.exists(self._final_path("crash.bin")))

        manager2 = DownloadManager(self.settings, callbacks={})
        manager2.load_index()
        self.assertIn(tid, manager2.tasks)
        task = manager2.tasks[tid]
        self.assertEqual(task.state, TaskState.PAUSED)
        self.assertGreater(task.downloaded(), 0)
        # 直接同步运行到完成
        task.resume()
        task.run()
        self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
        self.assertEqual(sha256_file(self._final_path("crash.bin")), self.source_hash)

    def test_04_no_range_fallback_single_thread(self):
        tid = self.manager.add(
            f"{self.base}/norange/data.bin",
            TaskOptions(connections=16, filename="norange.bin"),
        ).task_id
        snap = wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR}, 60)
        self.assertEqual(snap["state"], TaskState.COMPLETED, snap.get("error"))
        self.assertEqual(sha256_file(self._final_path("norange.bin")), self.source_hash)

    def test_05_multi_connection_speedup(self):
        """服务器对每条连接限速 1MB/s，多连接聚合速度应明显更快。"""
        url = f"{self.base}/file/data.bin?kbps=1024"

        t0 = time.time()
        t1 = self.manager.add(url, TaskOptions(connections=1, filename="slow.bin")).task_id
        snap = wait_state(self.manager, t1, {TaskState.COMPLETED}, 60)
        dur_single = time.time() - t0
        self.assertEqual(snap["state"], TaskState.COMPLETED)

        t0 = time.time()
        t8 = self.manager.add(url, TaskOptions(connections=8, filename="fast.bin")).task_id
        snap = wait_state(self.manager, t8, {TaskState.COMPLETED}, 60)
        dur_multi = time.time() - t0
        self.assertEqual(snap["state"], TaskState.COMPLETED)

        print(f"\n单连接 {dur_single:.2f}s vs 8连接 {dur_multi:.2f}s "
              f"(提速 {dur_single / max(dur_multi, 0.01):.1f}x)")
        self.assertLess(dur_multi, dur_single * 0.6,
                        "多连接未体现聚合提速，动态分段可能失效")


if __name__ == "__main__":
    unittest.main(verbosity=2)
