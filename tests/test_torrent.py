# -*- coding: utf-8 -*-
"""BT/磁力本机确定性测试。

用 libtorrent 在本机起一个 seeder 自播种，东方神速的 TorrentTask 作为
downloader，通过 connect_peer 直连本机 peer 真实下载（不依赖公网 tracker/DHT），
覆盖：
  1. .torrent 文件下载并校验 SHA256；
  2. magnet 磁力下载（经 ut_metadata 交换元数据）并校验 SHA256；
  3. 种子内文件勾选（只下选中文件，跳过其余）；
  4. close() 释放会话（句柄失效）。

未安装 libtorrent 时整个模块 skip。
"""
import hashlib
import os
import shutil
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from core.task import TaskState
from core.task_options import TaskOptions

try:
    import libtorrent as lt
    _HAS_LT = True
except Exception:  # pragma: no cover
    _HAS_LT = False

from core.torrent import TorrentTask  # noqa: E402

SEED_PORT = 7131  # 避开下载端监听范围 6881-6991
ROOT_NAME = "btseed"
BIG_NAME, BIG_SIZE = "movie.dat", 400 * 1024 + 77
TXT_NAME, TXT_SIZE = "info.txt", 512


def _make_settings(save_dir):
    return SimpleNamespace(
        save_dir=save_dir, speed_limit=0, connections=8,
        connect_timeout=10, read_timeout=30, verify_ssl=True,
        user_agent="t", proxy="", username="", password="",
        referer="", headers=None, cookies=None, prefer_http2=False,
    )


def _wait_terminal(task, handle_ready=None, timeout=70):
    """在独立线程跑 task.run，主线程注入 peer 并等待终态。"""
    t = threading.Thread(target=task.run, daemon=True)
    t.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = getattr(task, "_handle", None)
        if h is not None:
            try:
                if h.is_valid():
                    h.connect_peer(("127.0.0.1", SEED_PORT))
                    if handle_ready:
                        handle_ready(task)
            except Exception:
                pass
        if task.state in (TaskState.COMPLETED, TaskState.ERROR):
            return task.state
        time.sleep(0.25)
    return task.state


@unittest.skipUnless(_HAS_LT, "未安装 libtorrent，跳过 BT 测试")
class TorrentDownloadTests(unittest.TestCase):
    seeder = None
    seed_session = None
    tmp_root = None
    tor_path = None
    magnet = None
    big_sha = None
    txt_sha = None

    @classmethod
    def setUpClass(cls):
        cls.tmp_root = tempfile.mkdtemp(prefix="dfs-bt-")
        seeddir = os.path.join(cls.tmp_root, "seed")
        os.makedirs(os.path.join(seeddir, ROOT_NAME))
        big = os.urandom(BIG_SIZE)
        txt = os.urandom(TXT_SIZE)
        with open(os.path.join(seeddir, ROOT_NAME, BIG_NAME), "wb") as f:
            f.write(big)
        with open(os.path.join(seeddir, ROOT_NAME, TXT_NAME), "wb") as f:
            f.write(txt)
        cls.big_sha = hashlib.sha256(big).hexdigest()
        cls.txt_sha = hashlib.sha256(txt).hexdigest()

        fs = lt.file_storage()
        fs.add_file(f"{ROOT_NAME}/{BIG_NAME}", BIG_SIZE)
        fs.add_file(f"{ROOT_NAME}/{TXT_NAME}", TXT_SIZE)
        flags = int(getattr(lt.create_torrent, "v1_only", 0) or 0)
        ct = lt.create_torrent(fs, 0, flags)
        lt.set_piece_hashes(ct, seeddir)
        tor_bytes = lt.bencode(ct.generate())
        cls.tor_path = os.path.join(cls.tmp_root, "a.torrent")
        with open(cls.tor_path, "wb") as f:
            f.write(tor_bytes)
        info = lt.torrent_info(cls.tor_path)
        cls.magnet = lt.make_magnet_uri(info)

        cls.seed_session = lt.session({
            "listen_interfaces": f"127.0.0.1:{SEED_PORT}",
            "enable_dht": False, "enable_upnp": False,
            "enable_natpmp": False, "enable_lsd": False, "alert_mask": 0,
        })
        h = cls.seed_session.add_torrent(
            {"ti": info, "save_path": seeddir})
        deadline = time.time() + 20
        while time.time() < deadline:
            st = h.status()
            if st.is_seeding:
                break
            time.sleep(0.2)
        assert h.status().is_seeding, "本机 seeder 未能进入做种状态"

    @classmethod
    def tearDownClass(cls):
        if cls.seed_session is not None:
            try:
                del cls.seed_session
            except Exception:
                pass
        if cls.tmp_root:
            shutil.rmtree(cls.tmp_root, ignore_errors=True)

    def _new_task(self, dl_dir, url, options=None):
        os.makedirs(dl_dir, exist_ok=True)
        opt = options or TaskOptions(save_dir=dl_dir)
        if options is None:
            opt.save_dir = dl_dir
        return TorrentTask(_make_settings(dl_dir), url, opt)

    def test_torrent_file_download(self):
        dl = os.path.join(self.tmp_root, "dl_torrent")
        task = self._new_task(dl, self.tor_path)
        state = _wait_terminal(task)
        self.assertEqual(state, TaskState.COMPLETED,
                         f"未完成，状态={state} 错误={task.error_msg}")
        p = os.path.join(dl, ROOT_NAME, BIG_NAME)
        self.assertTrue(os.path.exists(p), "缺少大文件")
        self.assertEqual(hashlib.sha256(open(p, "rb").read()).hexdigest(),
                         self.big_sha)
        task.close()

    def test_magnet_download(self):
        dl = os.path.join(self.tmp_root, "dl_magnet")
        task = self._new_task(dl, self.magnet)
        state = _wait_terminal(task)
        self.assertEqual(state, TaskState.COMPLETED,
                         f"磁力未完成，状态={state} 错误={task.error_msg}")
        p = os.path.join(dl, ROOT_NAME, BIG_NAME)
        self.assertTrue(os.path.exists(p), "磁力下载缺少文件")
        self.assertEqual(hashlib.sha256(open(p, "rb").read()).hexdigest(),
                         self.big_sha)
        task.close()

    def test_file_selection_only_downloads_wanted(self):
        dl = os.path.join(self.tmp_root, "dl_select")
        opt = TaskOptions(save_dir=dl, bt_files=[TXT_NAME])
        task = self._new_task(dl, self.tor_path, opt)
        state = _wait_terminal(task)
        self.assertEqual(state, TaskState.COMPLETED,
                         f"勾选下载未完成，状态={state} 错误={task.error_msg}")
        wanted = os.path.join(dl, ROOT_NAME, TXT_NAME)
        skipped = os.path.join(dl, ROOT_NAME, BIG_NAME)
        self.assertTrue(os.path.exists(wanted), "选中的文件未下载")
        self.assertEqual(hashlib.sha256(open(wanted, "rb").read()).hexdigest(),
                         self.txt_sha)
        # 被排除的文件应不存在或为空占位
        self.assertFalse(os.path.exists(skipped)
                         and os.path.getsize(skipped) > 0,
                         "不应下载被排除的文件")
        task.close()

    def test_close_releases_session(self):
        dl = os.path.join(self.tmp_root, "dl_close")
        task = self._new_task(dl, self.tor_path)
        _wait_terminal(task)
        handle = task._handle
        self.assertIsNotNone(handle)
        task.close()
        self.assertTrue(task._closed)
        self.assertIsNone(task._session)
        self.assertIsNone(task._handle)


if __name__ == "__main__":
    unittest.main(verbosity=2)
