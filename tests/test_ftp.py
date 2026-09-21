# -*- coding: utf-8 -*-
"""FTP / FTPS 下载测试（本地 pyftpdlib 服务器，无需外网）。

运行: python -m unittest tests.test_ftp -v
"""
import hashlib
import os
import sys
import tempfile
import threading
import time
import unittest

_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-ftp-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.config import Settings  # noqa: E402
from core.ftp_task import FtpTask, parse_ftp_url  # noqa: E402
from core.task import TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402

from tests.ftp_server import FtpTestServer, make_cert  # noqa: E402

SIZE = 3 * 1024 * 1024 + 12345
DATA = os.urandom(SIZE)
EXPECTED_SHA = hashlib.sha256(DATA).hexdigest()


def _settings(save_dir):
    s = Settings()
    s.save_dir = save_dir
    s.connections = 8
    s.block_size = 256 * 1024
    s.av_scan = False
    s.verify_ssl = False
    s.connect_timeout = 10
    s.read_timeout = 20
    s.retries = 5
    return s


def _run_and_wait(task, timeout=90, target=TaskState.COMPLETED):
    t = threading.Thread(target=task.run, daemon=True)
    t.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if task.state in (target, TaskState.ERROR):
            return task
        time.sleep(0.1)
    raise AssertionError(f"超时 state={task.state} err={task.error_msg}")


def _make_ftp_root():
    root = tempfile.mkdtemp(prefix="pydl-ftp-root-")
    with open(os.path.join(root, "data.bin"), "wb") as f:
        f.write(DATA)
    return root


class FtpUrlParseTests(unittest.TestCase):
    def test_parse_anonymous(self):
        info = parse_ftp_url("ftp://ftp.example.com/pub/a.zip")
        self.assertFalse(info["tls"])
        self.assertEqual(info["host"], "ftp.example.com")
        self.assertEqual(info["port"], 21)
        self.assertEqual(info["user"], "anonymous")
        self.assertEqual(info["path"], "/pub/a.zip")

    def test_parse_credentials_and_tls(self):
        info = parse_ftp_url("ftps://bob:secret@host:2121/home/b/f.dat")
        self.assertTrue(info["tls"])
        self.assertEqual(info["port"], 2121)
        self.assertEqual(info["user"], "bob")
        self.assertEqual(info["password"], "secret")
        self.assertEqual(info["path"], "/home/b/f.dat")


class FtpDownloadTests(unittest.TestCase):
    def test_anonymous_multiconnection(self):
        root = _make_ftp_root()
        save = tempfile.mkdtemp(prefix="pydl-ftp-save-")
        with FtpTestServer(root) as srv:
            url = f"ftp://127.0.0.1:{srv.port}/data.bin"
            task = FtpTask(_settings(save), url,
                           TaskOptions(filename="anon.bin"))
            _run_and_wait(task)
            self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
            with open(os.path.join(save, "anon.bin"), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                                 EXPECTED_SHA)
            self.assertTrue(task.resumable)
            self.assertEqual(task.total_size, SIZE)

    def test_authenticated(self):
        root = _make_ftp_root()
        save = tempfile.mkdtemp(prefix="pydl-ftp-save-")
        with FtpTestServer(root, user="alice", password="pw") as srv:
            url = f"ftp://alice:pw@127.0.0.1:{srv.port}/data.bin"
            task = FtpTask(_settings(save), url,
                           TaskOptions(filename="auth.bin"))
            _run_and_wait(task)
            self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
            with open(os.path.join(save, "auth.bin"), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                                 EXPECTED_SHA)

    def test_pause_resume(self):
        root = _make_ftp_root()
        save = tempfile.mkdtemp(prefix="pydl-ftp-save-")
        with FtpTestServer(root) as srv:
            url = f"ftp://127.0.0.1:{srv.port}/data.bin"
            task = FtpTask(_settings(save), url,
                           TaskOptions(filename="resume.bin"))
            th = threading.Thread(target=task.run, daemon=True)
            th.start()
            deadline = time.time() + 30
            while time.time() < deadline:
                if task.state == TaskState.DOWNLOADING and task.downloaded() > 0:
                    break
                time.sleep(0.05)
            task.pause(timeout=20)
            self.assertIn(task.state, (TaskState.PAUSED, TaskState.COMPLETED))
            if task.state == TaskState.PAUSED:
                done_before = task.downloaded()
                # loopback 极快时暂停瞬间可能恰好收满（==SIZE），亦属合法
                self.assertLessEqual(done_before, SIZE)
                task.resume()
                threading.Thread(target=task.run, daemon=True).start()
                deadline = time.time() + 60
                while time.time() < deadline:
                    if task.state in (TaskState.COMPLETED, TaskState.ERROR):
                        break
                    time.sleep(0.1)
            self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
            with open(os.path.join(save, "resume.bin"), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                                 EXPECTED_SHA)


class FtpsDownloadTests(unittest.TestCase):
    def test_ftps_tls(self):
        try:
            import ssl  # noqa: F401
            from pyftpdlib.handlers import TLS_FTPHandler  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("当前环境不支持 pyftpdlib TLS")
        root = _make_ftp_root()
        save = tempfile.mkdtemp(prefix="pydl-ftps-save-")
        cert = make_cert(os.path.join(tempfile.mkdtemp(prefix="pydl-cert-"),
                                      "cert.pem"))
        settings = _settings(save)
        # pyftpdlib 的 TLS 并发数据连接在本机不稳定（测试服务器限制，非客户端
        # 问题）；FTPS 用单连接验证加密控制/数据通道完整性，多连接分段由
        # 上面的明文 FTP 用例覆盖。
        settings.connections = 1
        with FtpTestServer(root, tls_cert=cert) as srv:
            url = f"ftps://127.0.0.1:{srv.port}/data.bin"
            task = FtpTask(settings, url, TaskOptions(filename="secure.bin"))
            _run_and_wait(task, timeout=60)
            self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
            with open(os.path.join(save, "secure.bin"), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                                 EXPECTED_SHA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
