# -*- coding: utf-8 -*-
"""第四轮能力测试：校验和、预分配、杀毒钩子、BT 降级、blob 桥接、协议分发。

运行: python -m unittest tests.test_round4 -v
"""
import hashlib
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-r4-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import checksum  # noqa: E402
from core.antivirus import find_defender, scan_file, scanner_available  # noqa: E402
from core.bridge import BridgeServer  # noqa: E402
from core.config import Settings  # noqa: E402
from core.engine import DownloadManager, task_class_for  # noqa: E402
from core.errors import DownloadError  # noqa: E402
from core.ftp_task import FtpTask  # noqa: E402
from core.hls import HlsTask  # noqa: E402
from core.prealloc import preallocate  # noqa: E402
from core.task import DownloadTask  # noqa: E402
from core.torrent import is_torrent_url, libtorrent_available  # noqa: E402


class ChecksumTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(prefix="pydl-sum-")
        self.data = b"PyDownloader checksum test payload\n" * 1000
        with open(self.tmp, "wb") as f:
            f.write(self.data)

    def tearDown(self):
        if os.path.exists(self.tmp):
            os.remove(self.tmp)

    def test_hash_algorithms(self):
        for algo in ("md5", "sha1", "sha256", "sha512"):
            expect = hashlib.new(algo, self.data).hexdigest()
            self.assertEqual(checksum.hash_file(self.tmp, algo), expect)

    def test_verify_match_and_mismatch(self):
        algo = "sha256"
        good = hashlib.sha256(self.data).hexdigest()
        ok, actual = checksum.verify_file(self.tmp, algo, good)
        self.assertTrue(ok)
        self.assertEqual(actual, good)
        ok2, _ = checksum.verify_file(self.tmp, algo, "deadbeef" * 8)
        self.assertFalse(ok2)

    def test_normalize_aliases(self):
        self.assertEqual(checksum.normalize_algo("SHA-256"), "sha256")
        self.assertEqual(checksum.normalize_algo("sha1"), "sha1")
        self.assertEqual(checksum.normalize_algo("nope"), "")


class PreallocTests(unittest.TestCase):
    def test_preallocate_size(self):
        path = tempfile.mktemp(prefix="pydl-pre-")
        with open(path, "wb"):
            pass
        try:
            size = 1024 * 1024
            preallocate(path, size)
            self.assertEqual(os.path.getsize(path), size)
        finally:
            os.remove(path)


class AntivirusTests(unittest.TestCase):
    def test_scanner_callable_without_crash(self):
        # 无论本机是否有 Defender，接口都应正常返回而不是抛异常
        path = tempfile.mktemp(prefix="pydl-av-", suffix=".txt")
        with open(path, "wb") as f:
            f.write(b"clean text, not a virus\n")
        try:
            result = scan_file(path)
            if result.available:
                self.assertFalse(result.infected, result.detail)
            # 找不到扫描器时必须明确不可用，而不是谎报
            if not scanner_available():
                self.assertFalse(result.available)
        finally:
            os.remove(path)

    def test_find_defender_returns_str(self):
        self.assertIsInstance(find_defender(), str)


class TorrentFallbackTests(unittest.TestCase):
    def test_detect(self):
        self.assertTrue(is_torrent_url("magnet:?xt=urn:btih:abc"))
        self.assertTrue(is_torrent_url("https://x/a.torrent"))
        self.assertFalse(is_torrent_url("https://x/a.zip"))

    def test_magnet_without_libtorrent_raises_clear_error(self):
        settings = Settings()
        settings.save_dir = tempfile.mkdtemp(prefix="pydl-mag-")
        manager = DownloadManager(settings, callbacks={})
        if libtorrent_available():
            self.skipTest("本机已装 libtorrent，跳过缺库降级测试")
        with self.assertRaises(DownloadError):
            manager.add("magnet:?xt=urn:btih:abcd1234&dn=test")


class DispatchTests(unittest.TestCase):
    def test_task_class(self):
        self.assertIs(task_class_for("ftp://h/a.zip"), FtpTask)
        self.assertIs(task_class_for("ftps://h/a"), FtpTask)
        self.assertIs(task_class_for("https://h/a.m3u8"), HlsTask)
        self.assertIs(task_class_for("https://h/a.zip"), DownloadTask)


class BlobBridgeTests(unittest.TestCase):
    def setUp(self):
        self.dl = tempfile.mkdtemp(prefix="pydl-blob-dl-")
        self.settings = Settings(save_dir=self.dl, av_scan=False)
        self.manager = DownloadManager(self.settings, callbacks={})
        self.manager.start()
        self.bridge = BridgeServer(self.manager, port=0)
        self.bridge.start()
        self.port = self.bridge._httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.bridge.stop()
        self.manager.shutdown()

    def _post_blob(self, data, name, token=None):
        headers = {
            "Content-Type": "application/octet-stream",
            "X-PyDL-Token": self.bridge.token if token is None else token,
            "X-Blob-Name": name,
        }
        req = urllib.request.Request(
            f"{self.base}/add-blob", data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:  # noqa: F821
            return e.code, e.read()

    def test_blob_saved(self):
        import json
        payload = bytes(range(256)) * 100
        status, body = self._post_blob(payload, "clip.mp4")
        self.assertEqual(status, 200, body)
        info = json.loads(body.decode())
        self.assertTrue(info["ok"])
        saved = info["path"]
        self.assertTrue(os.path.exists(saved))
        with open(saved, "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_blob_bad_token(self):
        status, _ = self._post_blob(b"x", "a.mp4", token="wrong")
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
