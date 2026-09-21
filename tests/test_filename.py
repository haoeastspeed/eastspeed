# -*- coding: utf-8 -*-
"""文件名识别 / 重定向兼容测试。

覆盖：
- Content-Disposition 的 filename* 与裸 filename=（UTF-8 / GBK、latin-1 纠码）；
- 随机资源 ID（UUID / 长十六进制）识别、通用下载端点词降权；
- GitHub Release、对象存储签名直链等 302 跳转后的真实文件名还原；
- 无 Content-Disposition 时按 Content-Type 补扩展名；
- DownloadTask 端到端：扩展透传随机串也能落盘为真实文件名。

运行: python -m unittest tests.test_filename -v
"""
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from urllib.parse import quote

# 在导入 core 之前重定向 APPDATA，避免污染真实配置目录
_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Settings  # noqa: E402
from core.probe import (  # noqa: E402
    _filename_from_disposition,
    choose_url_filename,
    meaningful_filename,
    probe,
    reconcile_filename,
)
from core.task import DownloadTask, TaskState  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402
from tests.range_server import make_server  # noqa: E402

CN = "东方神速-Setup-1.0.0.exe"
GUID = "5dd04da9-2a59-4c10-8a5b-6c7d8e9f0011"


class DispositionParseTests(unittest.TestCase):
    def test_star_utf8(self):
        disp = f"attachment; filename*=UTF-8''{quote(CN)}"
        self.assertEqual(_filename_from_disposition(disp), CN)

    def test_star_gbk(self):
        pct = "".join("%%%02X" % b for b in CN.encode("gbk"))
        disp = f"attachment; filename*=GBK''{pct}"
        self.assertEqual(_filename_from_disposition(disp), CN)

    def test_legacy_utf8_latin1_repair(self):
        # 服务器把 UTF-8 字节直接塞进 filename=，requests 按 latin-1 解码头
        broken = CN.encode("utf-8").decode("latin-1")
        self.assertEqual(
            _filename_from_disposition(f'attachment; filename="{broken}"'), CN
        )

    def test_legacy_gbk_latin1_repair(self):
        broken = CN.encode("gbk").decode("latin-1")
        self.assertEqual(
            _filename_from_disposition(f"attachment; filename={broken}"), CN
        )

    def test_star_preferred_over_legacy(self):
        disp = "attachment; filename=fallback.exe; filename*=UTF-8''real%20name.zip"
        self.assertEqual(_filename_from_disposition(disp), "real name.zip")

    def test_ascii_quoted_inline_and_github(self):
        self.assertEqual(
            _filename_from_disposition('attachment; filename="DongFangSpeed.exe"'),
            "DongFangSpeed.exe",
        )
        self.assertEqual(
            _filename_from_disposition("inline; filename=report.pdf"),
            "report.pdf",
        )
        gh = ("attachment; filename=DongFangSpeed.exe; "
              "filename*=UTF-8''DongFangSpeed.exe")
        self.assertEqual(_filename_from_disposition(gh), "DongFangSpeed.exe")

    def test_path_stripped(self):
        self.assertEqual(
            _filename_from_disposition('attachment; filename="a/b/c.zip"'),
            "c.zip",
        )

    def test_empty(self):
        self.assertEqual(_filename_from_disposition(""), "")
        self.assertEqual(_filename_from_disposition(None), "")


class MeaningfulNameTests(unittest.TestCase):
    def test_good_names(self):
        for n in ["DongFangSpeed.exe", "report final v2.zip", "data.bin",
                  "README", "LICENSE", "Makefile", "download"]:
            self.assertTrue(meaningful_filename(n), n)

    def test_random_ids(self):
        for n in [GUID, "5dd04da92a594c108a5b6c7d8e9f0011",
                  "dGhpcyBpcyBhIGxvbmcgYmFzZTY0IHRva2VuIGluZGVlZA==", ""]:
            self.assertFalse(meaningful_filename(n), repr(n))


class ChooseReconcileTests(unittest.TestCase):
    def test_github_release(self):
        orig = ("https://github.com/wwwhdf/dongfangspeed/releases/download/"
                "v1.0.0/DongFangSpeed.exe")
        final = ("https://objects.githubusercontent.com/github-production/"
                 f"release-asset-2e65be/123/{GUID}?X-Amz-Algorithm=AWS4-HMAC-SHA256")
        self.assertEqual(choose_url_filename(orig, final), "DongFangSpeed.exe")

    def test_final_has_real_name(self):
        # 原始是通用下载端点 /dl，最终地址才有真名
        self.assertEqual(
            choose_url_filename("https://x.example.com/dl?id=1",
                                "https://cdn.example.com/files/a/report.zip"),
            "report.zip",
        )

    def test_reconcile(self):
        r = reconcile_filename
        self.assertEqual(r("", "DongFangSpeed.exe"), "DongFangSpeed.exe")
        self.assertEqual(r(GUID, "DongFangSpeed.exe"), "DongFangSpeed.exe")
        # 显式指定的正常名（用户改名 / 扩展取到真名）应保留
        self.assertEqual(r("custom.exe", "DongFangSpeed.exe"), "custom.exe")
        # 无扩展名的合法文件不应被误伤
        self.assertEqual(r("README", "README"), "README")
        self.assertEqual(r("", ""), "")


class RedirectServerTests(unittest.TestCase):
    httpd = None
    base = ""
    serve_dir = dl_dir = None

    @classmethod
    def setUpClass(cls):
        cls.serve_dir = tempfile.mkdtemp(prefix="pydl-served-")
        for name, size in [("DongFangSpeed.exe", 1024 * 1024),
                           ("report.zip", 777 * 1024)]:
            with open(os.path.join(cls.serve_dir, name), "wb") as f:
                f.write(os.urandom(size))
        cls.httpd, cls.base = make_server(cls.serve_dir)
        cls.dl_dir = tempfile.mkdtemp(prefix="pydl-dl-")
        cls.settings = Settings(
            save_dir=cls.dl_dir, block_size=128 * 1024,
            connect_timeout=3, read_timeout=10, auto_resume=False, retries=2,
        )

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.serve_dir, ignore_errors=True)
        shutil.rmtree(cls.dl_dir, ignore_errors=True)
        shutil.rmtree(_TMP_APPDATA, ignore_errors=True)

    def test_probe_redirect_with_disposition(self):
        # 完整复现 GitHub：/redir/<真名> 302 到 /cdn/<随机token>，CD 给真名
        info = probe(f"{self.base}/redir/DongFangSpeed.exe", self.settings)
        self.assertEqual(info.filename, "DongFangSpeed.exe")
        self.assertGreater(info.total_size, 0)
        # 最终地址路径末段确实是随机 token
        self.assertNotIn("DongFangSpeed", info.final_url.split("?")[0])

    def test_probe_redirect_without_disposition_uses_original(self):
        info = probe(f"{self.base}/redir_nocd/DongFangSpeed.exe", self.settings)
        self.assertEqual(info.filename, "DongFangSpeed.exe")

    def test_content_type_extension_fallback(self):
        # 原始与最终 URL 都是无扩展名随机 token、且无 CD：按 Content-Type 补 .zip
        url = f"{self.base}/cdn_nocd/{uuid.uuid4().hex}?f=report.zip"
        info = probe(url, self.settings)
        self.assertTrue(info.filename.endswith(".zip"), info.filename)

    def test_e2e_task_garbage_suggested_name_corrected(self):
        # 模拟修复前的扩展把随机 UUID 当文件名透传，任务仍应落盘为真名
        opt = TaskOptions(connections=8, filename=GUID)
        task = DownloadTask(
            self.settings, f"{self.base}/redir/DongFangSpeed.exe", opt
        )
        task.run()
        self.assertEqual(task.state, TaskState.COMPLETED, task.error_msg)
        self.assertEqual(
            os.path.basename(task.final_path), "DongFangSpeed.exe"
        )
        self.assertTrue(os.path.exists(task.final_path))


if __name__ == "__main__":
    unittest.main(verbosity=2)
