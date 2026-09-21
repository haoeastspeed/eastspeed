# -*- coding: utf-8 -*-
"""浏览器桥接接口端到端测试（模拟扩展请求）。

运行: python -m unittest tests.test_bridge -v
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

_TMP = tempfile.mkdtemp(prefix="pydl-bridge-appdata-")
os.environ["APPDATA"] = _TMP
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.bridge import BridgeServer  # noqa: E402
from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.task import TaskState  # noqa: E402
from tests.range_server import make_server  # noqa: E402

FILE_SIZE = 4 * 1024 * 1024


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class BridgeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.serve = tempfile.mkdtemp(prefix="pydl-served-")
        src = os.path.join(cls.serve, "data.bin")
        with open(src, "wb") as f:
            f.write(os.urandom(FILE_SIZE))
        cls.source_hash = sha256_file(src)
        cls.httpd, cls.base = make_server(cls.serve)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.serve, ignore_errors=True)
        shutil.rmtree(_TMP, ignore_errors=True)

    def setUp(self):
        self.dl = tempfile.mkdtemp(prefix="pydl-bridge-dl-")
        self.settings = Settings(save_dir=self.dl, block_size=256 * 1024,
                                 auto_resume=False)
        self.manager = DownloadManager(self.settings, callbacks={})
        self.manager.start()
        self.bridge = BridgeServer(self.manager, port=0)  # 系统分配端口
        self.bridge.start()
        self.port = self.bridge._httpd.server_address[1]
        self.root = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.bridge.stop()
        self.manager.shutdown()
        shutil.rmtree(self.dl, ignore_errors=True)

    def _request(self, method, path, body=None, token=None, extra_headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.root + path, data=data, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("X-PyDL-Token", token)
        for k, v in (extra_headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def test_01_ping(self):
        with urllib.request.urlopen(self.root + "/ping", timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(json.loads(resp.read().decode())["ok"])

    def test_02_add_requires_token(self):
        code, body = self._request("POST", "/add",
                                   {"url": f"{self.base}/file/data.bin"})
        self.assertEqual(code, 403)
        self.assertIn("token", body["error"])

    def test_03_add_with_token_downloads(self):
        url = f"{self.base}/file/data.bin"
        code, body = self._request("POST", "/add", {
            "url": url,
            "filename": "ext.bin",
            "referer": f"{self.base}/page.html",
        }, token=self.bridge.token)
        self.assertEqual(code, 200, body)
        tid = body["task_id"]

        deadline = time.time() + 60
        while time.time() < deadline:
            state = self.manager.tasks[tid].state
            if state in (TaskState.COMPLETED, TaskState.ERROR):
                break
            time.sleep(0.1)
        self.assertEqual(self.manager.tasks[tid].state, TaskState.COMPLETED,
                         self.manager.tasks[tid].error_msg)
        final = os.path.join(self.dl, "ext.bin")
        self.assertTrue(os.path.exists(final))
        self.assertEqual(sha256_file(final), self.source_hash)

    def test_04_rejects_bad_url(self):
        code, _ = self._request("POST", "/add", {"url": "javascript:alert(1)"},
                                token=self.bridge.token)
        self.assertEqual(code, 400)

    # ---------- 免 Token 自动配对 /pair（简单 GET，按 Origin 放行） ----------
    def test_05_pair_returns_token_without_origin(self):
        # 扩展 Service Worker 的 fetch 可能完全不带 Origin
        code, body = self._request("GET", "/pair")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["token"], self.bridge.token)
        self.assertTrue(body["name"])

    def test_06_pair_allows_extension_origin(self):
        code, body = self._request(
            "GET", "/pair",
            extra_headers={"Origin": "chrome-extension://abcdefghijklmnop"})
        self.assertEqual(code, 200, body)
        self.assertEqual(body["token"], self.bridge.token)

    def test_07_pair_rejects_web_http_origin(self):
        code, body = self._request(
            "GET", "/pair",
            extra_headers={"Origin": "http://evil.example.com"})
        self.assertEqual(code, 403)
        self.assertNotIn("token", body)

    def test_08_pair_rejects_web_https_origin(self):
        code, body = self._request(
            "GET", "/pair",
            extra_headers={"Origin": "https://evil.example.com"})
        self.assertEqual(code, 403)
        self.assertNotIn("token", body)

    # ---------- 接管确认窗回调 on_inbound ----------
    def _install_inbound(self, accept):
        from core.task_options import TaskOptions

        def on_inbound(payload):
            if not accept:
                return None
            opt = TaskOptions(
                filename=payload.get("filename") or None,
                referer=payload.get("referer") or None,
                cookies=payload.get("cookies") or None)
            task = self.manager.add(payload["url"], opt)
            return task.task_id

        self.manager.callbacks["on_inbound"] = on_inbound
        self.settings.takeover_prompt = True

    def test_09_inbound_accepted_creates_task(self):
        self._install_inbound(accept=True)
        url = f"{self.base}/file/data.bin"
        code, body = self._request("POST", "/add", {
            "url": url, "filename": "inbound.bin",
            "referer": f"{self.base}/p.html", "cookies": "a=1"},
            token=self.bridge.token)
        self.assertEqual(code, 200, body)
        self.assertIn("task_id", body)
        self.assertIn(body["task_id"], self.manager.tasks)

    def test_10_inbound_cancelled_reports_cancelled(self):
        self._install_inbound(accept=False)
        url = f"{self.base}/file/cancelled.bin"
        code, body = self._request("POST", "/add", {"url": url},
                                   token=self.bridge.token)
        self.assertEqual(code, 200, body)
        self.assertTrue(body.get("cancelled"))
        self.assertFalse(any(t.url == url for t in self.manager.tasks.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
