# -*- coding: utf-8 -*-
"""自动更新框架测试（本地 HTTP 托管 latest.json 与安装包，端到端）。"""
import functools
import hashlib
import http.server
import os
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from core.updater import (
    UpdateError,
    check_for_update,
    download_update,
    is_newer,
    parse_version,
    sha256_file,
)


def _serve(directory):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class VersionTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_version("1.0.10"), (1, 0, 10))
        self.assertEqual(parse_version("v2.3"), (2, 3))

    def test_compare(self):
        self.assertTrue(is_newer("1.0.1", "1.0.0"))
        self.assertTrue(is_newer("1.1.0", "1.0.9"))
        self.assertFalse(is_newer("1.0.0", "1.0.0"))
        self.assertFalse(is_newer("0.9.9", "1.0.0"))


class UpdateFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="dfs-upd-")
        cls.srv = _serve(cls.dir)
        _, port = cls.srv.server_address[:2]
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _write_json(self, obj, name="latest.json"):
        import json
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return f"{self.base}/{name}"

    def test_new_version_detected(self):
        url = self._write_json({
            "version": "9.9.9", "url": f"{self.base}/setup.exe",
            "sha256": "", "notes": "测试新版"})
        info = check_for_update(url, current="1.0.0")
        self.assertIsNotNone(info)
        self.assertEqual(info["version"], "9.9.9")

    def test_no_update_when_same_or_old(self):
        url = self._write_json({"version": "1.0.0", "url": f"{self.base}/x.exe"})
        self.assertIsNone(check_for_update(url, current="1.0.0"))
        url2 = self._write_json({"version": "0.9.0", "url": f"{self.base}/x.exe"},
                                name="old.json")
        self.assertIsNone(check_for_update(url2, current="1.0.0"))

    def test_bad_manifest(self):
        p = os.path.join(self.dir, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(UpdateError):
            check_for_update(f"{self.base}/bad.json")

    def test_download_and_hash_ok(self):
        payload = b"installer-bytes-" * 5000
        setup = os.path.join(self.dir, "setup.exe")
        with open(setup, "wb") as f:
            f.write(payload)
        digest = hashlib.sha256(payload).hexdigest()
        dl_dir = tempfile.mkdtemp(prefix="dfs-upd-dl-")
        info = {"version": "9.9.9", "url": f"{self.base}/setup.exe",
                "sha256": digest, "notes": ""}
        seen = []
        path = download_update(info, dest_dir=dl_dir,
                               progress=lambda d, t: seen.append((d, t)))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(sha256_file(path), digest)
        self.assertTrue(seen and seen[-1][0] == len(payload))

    def test_download_hash_mismatch_rejected(self):
        payload = b"xyz" * 100
        with open(os.path.join(self.dir, "setup2.exe"), "wb") as f:
            f.write(payload)
        dl_dir = tempfile.mkdtemp(prefix="dfs-upd-bad-")
        info = {"version": "9.9.9", "url": f"{self.base}/setup2.exe",
                "sha256": "0" * 64, "notes": ""}
        with self.assertRaises(UpdateError):
            download_update(info, dest_dir=dl_dir)
        # 校验失败的文件应被删除
        self.assertFalse(
            os.path.exists(os.path.join(dl_dir, "setup2.exe")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
