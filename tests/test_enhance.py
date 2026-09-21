# -*- coding: utf-8 -*-
"""第三轮增强能力测试：卡死连接抢占、磁盘空间预检、站点连接数例外、自动分类。

运行: python -m unittest tests.test_enhance -v
"""
import collections
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-enh-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.category import category_dir_for, category_of, host_of  # noqa: E402
from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.task import TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402

from tests.range_server import make_server  # noqa: E402
from tests.hls_server import make_hls_server  # noqa: E402

FILE_SIZE = 4 * 1024 * 1024


def wait_state(manager, tid, targets, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = manager.tasks[tid]
        if t.state in targets:
            return t
        time.sleep(0.1)
    raise AssertionError(f"超时，当前状态 {manager.tasks[tid].state} "
                         f"{manager.tasks[tid].error_msg}")


class CategoryHelperTests(unittest.TestCase):
    def test_category_of(self):
        self.assertEqual(category_of("a.mp4"), "视频")
        self.assertEqual(category_of("a.m3u8"), "视频")
        self.assertEqual(category_of("a.flac"), "音频")
        self.assertEqual(category_of("a.pdf"), "文档")
        self.assertEqual(category_of("a.exe"), "程序")
        self.assertEqual(category_of("a.7z"), "压缩包")
        self.assertEqual(category_of("a.unknown"), "其他")

    def test_category_dir(self):
        base = os.path.join("root", "dl")
        self.assertEqual(category_dir_for("a.mp4", True, base),
                         os.path.join(base, "视频"))
        # “其他”留在根目录
        self.assertEqual(category_dir_for("a.xyz", True, base), base)
        # 未开启自动分类
        self.assertEqual(category_dir_for("a.mp4", False, base), base)
        # 显式目录优先
        self.assertEqual(category_dir_for("a.mp4", True, base, "D:\\x"), "D:\\x")

    def test_host_of(self):
        self.assertEqual(host_of("http://www.Example.com:8080/a"), "example.com")
        self.assertEqual(host_of("https://cdn.x.org/p"), "cdn.x.org")


class SiteConnectionTests(unittest.TestCase):
    def test_connections_for_matching(self):
        s = Settings(site_connections={"example.com": 4})
        self.assertEqual(s.connections_for("http://example.com/f"), 4)
        self.assertEqual(s.connections_for("http://www.example.com/f"), 4)
        self.assertEqual(s.connections_for("http://a.b.example.com/f"), 4)
        self.assertIsNone(s.connections_for("http://notexample.com/f"))
        self.assertIsNone(s.connections_for("http://other.com/f"))

    def test_connections_for_clamped_and_longest_wins(self):
        s = Settings(site_connections={"example.com": 99, "cdn.example.com": 2})
        # 越界裁剪到 32；更具体的域名优先
        self.assertEqual(s.connections_for("http://example.com/"), 32)
        self.assertEqual(s.connections_for("http://cdn.example.com/"), 2)


class _StallHandler(BaseHTTPRequestHandler):
    """前 N 个首次出现的 Range 在发出响应头后挂起，模拟服务端接受连接但不发数据。"""
    data = b""
    lock = threading.Lock()
    seen: set = set()
    stall_n = 8

    def log_message(self, *args):
        pass

    def do_GET(self):
        total = len(self.data)
        rng = self.headers.get("Range", "")
        import re
        m = re.search(r"bytes=(\d+)-(\d*)", rng)
        start = int(m.group(1))
        end = int(m.group(2)) if m and m.group(2) else total - 1
        end = min(end, total - 1)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        with self.lock:
            first = start not in self.seen
            self.seen.add(start)
            order = len(self.seen)
        if first and order <= self.stall_n:
            time.sleep(12)  # 远超看门狗阈值，应被客户端强制断开
            try:
                self.wfile.write(self.data[start:start + 1024])
                self.wfile.flush()
            except OSError:
                pass
            return
        try:
            self.wfile.write(self.data[start:end + 1])
            self.wfile.flush()
        except OSError:
            pass


def make_stall_server(data: bytes):
    class Handler(_StallHandler):
        pass
    Handler.data = data
    Handler.seen = set()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}/file/stuck.bin"


class EngineEnhanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serve_dir = tempfile.mkdtemp(prefix="pydl-enh-served-")
        src = os.path.join(cls.serve_dir, "data.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(FILE_SIZE))
        cls.httpd, cls.base = make_server(cls.serve_dir)
        cls.hls_httpd, cls.hls_base, _ = make_hls_server()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.hls_httpd.shutdown()
        shutil.rmtree(cls.serve_dir, ignore_errors=True)
        shutil.rmtree(_TMP_APPDATA, ignore_errors=True)

    def setUp(self):
        self.appdata = tempfile.mkdtemp(prefix="pydl-enh-ad-")
        os.environ["APPDATA"] = self.appdata
        self.dl = tempfile.mkdtemp(prefix="pydl-enh-dl-")
        self.settings = Settings(save_dir=self.dl, block_size=256 * 1024,
                                 connect_timeout=3, read_timeout=30,
                                 auto_resume=False, retries=10)
        self.manager = DownloadManager(self.settings, callbacks={})
        self.manager.start()

    def tearDown(self):
        self.manager.shutdown(pause_running=True)
        shutil.rmtree(self.dl, ignore_errors=True)
        shutil.rmtree(self.appdata, ignore_errors=True)

    def test_site_connections_applied_at_runtime(self):
        self.settings.connections = 16
        self.settings.site_connections = {"127.0.0.1": 2}
        tid = self.manager.add(
            f"{self.base}/file/data.bin",
            TaskOptions(filename="site.bin"),
        ).task_id
        t = wait_state(self.manager, tid,
                       {TaskState.COMPLETED, TaskState.ERROR})
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        self.assertEqual(t.effective_connections, 2)

    def test_disk_space_precheck(self):
        usage = collections.namedtuple("usage", ["total", "used", "free"])
        tiny = usage(100 * 1024 ** 3, 99 * 1024 ** 3, 1 * 1024 ** 2)
        with patch("core.task.shutil.disk_usage", return_value=tiny):
            tid = self.manager.add(
                f"{self.base}/file/data.bin",
                TaskOptions(connections=4, filename="big.bin"),
            ).task_id
            t = wait_state(self.manager, tid, {TaskState.ERROR}, timeout=30)
        self.assertEqual(t.state, TaskState.ERROR)
        self.assertIn("磁盘空间", t.error_msg)
        self.assertFalse(os.path.exists(os.path.join(self.dl, "big.bin")))

    def test_auto_categorize_http_video(self):
        self.settings.auto_categorize = True
        tid = self.manager.add(
            f"{self.base}/file/data.bin",
            TaskOptions(connections=8, filename="movie.mp4"),
        ).task_id
        t = wait_state(self.manager, tid,
                       {TaskState.COMPLETED, TaskState.ERROR})
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        expected = os.path.join(self.dl, "视频", "movie.mp4")
        self.assertTrue(os.path.exists(expected), expected)

    def test_auto_categorize_hls_video(self):
        self.settings.auto_categorize = True
        tid = self.manager.add(
            f"{self.hls_base}/vod/media.m3u8",
            TaskOptions(connections=8, filename="lesson.m3u8"),
        ).task_id
        t = wait_state(self.manager, tid,
                       {TaskState.COMPLETED, TaskState.ERROR}, timeout=60)
        self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
        self.assertIn("视频", t.final_path.split(os.sep))

    def test_stalled_connection_is_killed_and_redistributed(self):
        """服务端对首批分段只发响应头不发数据，看门狗应断开并让任务最终完整。"""
        data = os.urandom(FILE_SIZE)
        stall_httpd, url = make_stall_server(data)
        try:
            self.settings.stall_timeout = 3
            events: list[str] = []
            manager = DownloadManager(
                self.settings,
                callbacks={"on_event": lambda task, level, msg: events.append(msg)},
            )
            manager.start()
            try:
                tid = manager.add(
                    url, TaskOptions(connections=8, filename="stuck.bin")
                ).task_id
                t = wait_state(manager, tid,
                               {TaskState.COMPLETED, TaskState.ERROR}, timeout=60)
                self.assertEqual(t.state, TaskState.COMPLETED, t.error_msg)
                final = os.path.join(self.dl, "stuck.bin")
                with open(final, "rb") as f:
                    self.assertEqual(f.read(), data)
                self.assertTrue(
                    any("无数据" in m for m in events),
                    f"看门狗未触发介入，事件: {events}",
                )
            finally:
                manager.shutdown(pause_running=True)
        finally:
            stall_httpd.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
