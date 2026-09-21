# -*- coding: utf-8 -*-
"""真实外网下载验证（手动运行: python tests/test_live.py）。

对比同一文件 1 连接与 16 连接的耗时/速度，并校验两者哈希一致。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time

_TMP = tempfile.mkdtemp(prefix="pydl-live-appdata-")
os.environ["APPDATA"] = _TMP
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.probe import probe  # noqa: E402
from core.task import TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402

# 华为云镜像上的 Python 安装包（约 27MB，支持 Range）
URL = "https://mirrors.huaweicloud.com/python/3.12.3/python-3.12.3-amd64.exe"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wait(manager, tid, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = manager.tasks[tid]
        if t.state in (TaskState.COMPLETED, TaskState.ERROR):
            return t.snapshot()
        time.sleep(0.2)
    raise TimeoutError("下载超时")


def run_case(connections, dl_dir, appdata):
    os.environ["APPDATA"] = appdata
    settings = Settings(save_dir=dl_dir, block_size=2 * 1024 * 1024,
                        connect_timeout=10, read_timeout=30)
    manager = DownloadManager(settings, callbacks={})
    manager.start()
    name = f"c{connections}.gz"
    t0 = time.time()
    tid = manager.add(URL, TaskOptions(connections=connections, filename=name)).task_id
    snap = wait(manager, tid)
    dur = time.time() - t0
    manager.shutdown()
    return snap, dur, os.path.join(dl_dir, name)


def main():
    work = tempfile.mkdtemp(prefix="pydl-live-")
    try:
        probe_appdata = tempfile.mkdtemp(dir=work)
        os.environ["APPDATA"] = probe_appdata
        info = probe(URL, Settings(save_dir=work))
        print(f"探测: HTTP {info.status_code}, resumable={info.resumable}, "
              f"size={info.total_size} bytes ({info.total_size / 1e6:.1f} MB), "
              f"filename={info.filename}")
        assert info.resumable, "镜像站应支持 Range"
        assert info.total_size and info.total_size > 1_000_000

        s1, d1, p1 = run_case(1, tempfile.mkdtemp(dir=work),
                              tempfile.mkdtemp(dir=work))
        print(f"1  连接: {s1['state']} {d1:.2f}s, "
              f"平均 {info.total_size / d1 / 1e6:.2f} MB/s")
        assert s1["state"] == TaskState.COMPLETED, s1.get("error")

        s16, d16, p16 = run_case(16, tempfile.mkdtemp(dir=work),
                                 tempfile.mkdtemp(dir=work))
        print(f"16 连接: {s16['state']} {d16:.2f}s, "
              f"平均 {info.total_size / d16 / 1e6:.2f} MB/s, "
              f"提速 {d1 / max(d16, 0.01):.2f}x")
        assert s16["state"] == TaskState.COMPLETED, s16.get("error")

        h1, h16 = sha256_file(p1), sha256_file(p16)
        print("哈希一致:", h1 == h16)
        assert h1 == h16
        print("LIVE TEST PASSED")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    main()
