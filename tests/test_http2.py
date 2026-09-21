# -*- coding: utf-8 -*-
"""HTTP/2 下载测试（本地 hypercorn h2 服务器，自签 TLS，无需外网）。

运行: python -m unittest tests.test_http2 -v
"""
import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest

_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-h2-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.config import Settings  # noqa: E402
from core.http2 import h2_available  # noqa: E402
from core.task import DownloadTask, TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402

from tests.h2_server import H2TestServer  # noqa: E402

SIZE = 2 * 1024 * 1024 + 777
DATA = os.urandom(SIZE)
EXPECTED = hashlib.sha256(DATA).hexdigest()


@unittest.skipUnless(h2_available(), "未安装 httpx[h2]，跳过 HTTP/2 测试")
class Http2DownloadTests(unittest.TestCase):
    def test_download_over_h2_with_ranges(self):
        save = tempfile.mkdtemp(prefix="pydl-h2-save-")
        s = Settings()
        s.save_dir = save
        s.connections = 8
        s.block_size = 256 * 1024
        s.av_scan = False
        s.verify_ssl = False
        s.prefer_http2 = True
        s.retries = 5

        with H2TestServer({"/file.bin": DATA}) as srv:
            url = f"https://127.0.0.1:{srv.port}/file.bin"
            task = DownloadTask(s, url, TaskOptions(filename="h2.bin"))
            th = threading.Thread(target=task.run, daemon=True)
            th.start()
            deadline = time.time() + 60
            while time.time() < deadline:
                if task.state in (TaskState.COMPLETED, TaskState.ERROR):
                    break
                time.sleep(0.1)
            self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
            out = os.path.join(save, "h2.bin")
            self.assertTrue(os.path.exists(out))
            with open(out, "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), EXPECTED)
            # 确实通过 HTTP/2 协商完成了请求
            self.assertTrue(
                any(v.startswith("2") for v in srv.versions),
                f"未观察到 HTTP/2，实际版本 {srv.versions}")


@unittest.skipUnless(h2_available(), "未安装 httpx[h2]，跳过 HTTP/2 测试")
class Http2ExceptionMappingTests(unittest.TestCase):
    """锁定 httpx→requests 异常映射，防止引用不存在的异常类（如 NetRoundingError）。"""

    def test_network_errors_map_to_connection_error(self):
        import httpx
        import requests
        from core.http2 import _convert
        for exc in (httpx.ConnectError("x"), httpx.ReadError("x"),
                    httpx.WriteError("x"), httpx.CloseError("x"),
                    httpx.NetworkError("x"), httpx.RemoteProtocolError("x")):
            with self.subTest(exc=type(exc).__name__):
                self.assertIsInstance(_convert(exc), requests.ConnectionError)

    def test_timeout_maps_to_timeout(self):
        import httpx
        import requests
        from core.http2 import _convert
        self.assertIsInstance(_convert(httpx.ReadTimeout("x")), requests.Timeout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
