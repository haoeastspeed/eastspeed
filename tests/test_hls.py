# -*- coding: utf-8 -*-
"""HLS(m3u8) 下载端到端测试。

运行: python -m unittest tests.test_hls -v
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest

_TMP = tempfile.mkdtemp(prefix="pydl-hls-appdata-")
os.environ["APPDATA"] = _TMP
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.hls import HlsTask  # noqa: E402
from core.task import TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402
from tests.hls_server import make_hls_server  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wait_state(manager, tid, targets, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = manager.tasks[tid]
        if t.state in targets:
            return t
        time.sleep(0.1)
    raise AssertionError(f"超时，当前状态 {manager.tasks[tid].state} "
                         f"{manager.tasks[tid].error_msg}")


class HlsTestCase(unittest.TestCase):
    httpd = None
    base = ""
    fx = None

    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.base, cls.fx = make_hls_server()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(_TMP, ignore_errors=True)

    def setUp(self):
        self.appdata = tempfile.mkdtemp(prefix="pydl-hls-ad-")
        os.environ["APPDATA"] = self.appdata
        self.dl = tempfile.mkdtemp(prefix="pydl-hls-dl-")
        self.settings = Settings(save_dir=self.dl, block_size=256 * 1024,
                                 auto_resume=False, retries=3)
        self.manager = DownloadManager(self.settings, callbacks={})
        self.manager.start()

    def tearDown(self):
        self.manager.shutdown()
        shutil.rmtree(self.dl, ignore_errors=True)
        shutil.rmtree(self.appdata, ignore_errors=True)

    def _add(self, url, name=None):
        opt = TaskOptions(connections=8)
        if name:
            opt.filename = name
        return self.manager.add(url, opt).task_id

    def _read_final(self, tid) -> bytes:
        t = self.manager.tasks[tid]
        self.assertTrue(os.path.exists(t.final_path),
                        f"最终文件不存在: {t.final_path} ({t.error_msg})")
        with open(t.final_path, "rb") as f:
            return f.read()

    def test_01_plain_ts_concat(self):
        tid = self._add(f"{self.base}/vod/media.m3u8", "plain.m3u8")
        wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR})
        t = self.manager.tasks[tid]
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        data = self._read_final(tid)
        self.assertEqual(sha(data), sha(b"".join(self.fx["plain"])))
        # 无 ffmpeg 时直拼为 .ts
        if not t._ffmpeg_path():
            self.assertTrue(t.final_path.endswith(".ts"))

    def test_02_master_picks_highest_bitrate(self):
        tid = self._add(f"{self.base}/vod/master.m3u8")
        wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR})
        t = self.manager.tasks[tid]
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        data = self._read_final(tid)
        self.assertEqual(sha(data), sha(b"".join(self.fx["high"])))
        self.assertNotEqual(sha(data), sha(b"".join(self.fx["low"])))

    def test_03_aes128_decrypt(self):
        tid = self._add(f"{self.base}/enc/media.m3u8", "enc.m3u8")
        wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR})
        t = self.manager.tasks[tid]
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        data = self._read_final(tid)
        self.assertEqual(sha(data), sha(b"".join(self.fx["plain"])))

    def test_04_fmp4_with_init_segment(self):
        tid = self._add(f"{self.base}/fmp4/media.m3u8", "fmp4.m3u8")
        wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR})
        t = self.manager.tasks[tid]
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        data = self._read_final(tid)
        self.assertEqual(sha(data), sha(self.fx["init"] + b"".join(self.fx["m4s"])))

    def test_05_pause_resume(self):
        # 独立的分片限速服务器 + 2 连接，保证暂停时任务仍在下载中
        slow_httpd, slow_base, slow_fx = make_hls_server(seg_delay=0.4)
        try:
            opt = TaskOptions(connections=2, filename="pause.m3u8")
            tid = self.manager.add(f"{slow_base}/vod/media.m3u8", opt).task_id
            t = self.manager.tasks[tid]
            deadline = time.time() + 30
            while time.time() < deadline:
                if len(t._completed) >= 2 and t.state == TaskState.DOWNLOADING:
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(len(t._completed), 2)
            self.manager.pause(tid)
            wait_state(self.manager, tid, {TaskState.PAUSED}, timeout=30)
            self.assertTrue(os.path.exists(t.meta_path))
            self.manager.resume(tid)
            wait_state(self.manager, tid, {TaskState.COMPLETED, TaskState.ERROR},
                       timeout=60)
            self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
            data = self._read_final(tid)
            self.assertEqual(sha(data), sha(b"".join(slow_fx["plain"])))
        finally:
            slow_httpd.shutdown()

    def test_06_live_stream_rejected(self):
        tid = self._add(f"{self.base}/live/media.m3u8", "live.m3u8")
        t = wait_state(self.manager, tid, {TaskState.ERROR, TaskState.COMPLETED})
        self.assertEqual(t.state, TaskState.ERROR)
        self.assertIn("直播", t.error_msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
