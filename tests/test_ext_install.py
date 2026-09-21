# -*- coding: utf-8 -*-
"""浏览器扩展一键安装辅助模块测试（Windows）。

运行: python -m unittest tests.test_ext_install -v
"""
import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import ext_install as ei  # noqa: E402


class ExtInstallTests(unittest.TestCase):
    def test_ps_literal_escaping(self):
        self.assertEqual(ei.ps_literal("a"), "'a'")
        self.assertEqual(ei.ps_literal("a'b"), "'a''b'")

    def test_browser_info_props(self):
        edge = ei.BrowserInfo(key="edge", name="Microsoft Edge", exe="x.exe")
        chrome = ei.BrowserInfo(key="chrome", name="Google Chrome", exe="y.exe")
        self.assertEqual(edge.extensions_url, "edge://extensions")
        self.assertEqual(chrome.extensions_url, "chrome://extensions")
        self.assertEqual(edge.process_name, "msedge.exe")
        self.assertEqual(chrome.process_name, "chrome.exe")
        self.assertTrue(edge.supports_cli_load)
        self.assertFalse(chrome.supports_cli_load)

    def test_stable_dir_under_localappdata(self):
        d = ei.stable_extension_dir()
        self.assertIn("DongFangSpeed", d)
        self.assertTrue(d.replace("\\", "/").endswith("browser_extension"))

    def test_deploy_stable_idempotent(self):
        d = ei.deploy_stable_extension()
        self.assertTrue(os.path.isfile(os.path.join(d, "manifest.json")))
        self.assertTrue(os.path.isfile(os.path.join(d, "background.js")))
        self.assertTrue(os.path.isfile(os.path.join(d, "icons", "icon128.png")))
        #再部署一次应返回同一目录且不报错
        d2 = ei.deploy_stable_extension()
        self.assertEqual(d, d2)

    def test_detect_browsers_structure(self):
        browsers = ei.detect_browsers()
        keys = {b.key for b in browsers}
        self.assertTrue(keys.issubset({"edge", "chrome"}))
        for b in browsers:
            self.assertTrue(os.path.isfile(b.exe))
            # 版本要么为空，要么是 x.y.z.w
            self.assertTrue(not b.version or b.version.count(".") == 3,
                            f"bad version: {b.version!r}")

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows only")
    def test_edge_shortcut_create_and_remove(self):
        browsers = ei.detect_browsers()
        edge = next((b for b in browsers if b.key == "edge"), None)
        if not edge:
            self.skipTest("Edge not present")
        d = ei.deploy_stable_extension()
        lnk = ei.create_desktop_shortcut(edge, d)
        try:
            self.assertTrue(os.path.isfile(lnk), f"shortcut not created: {lnk}")
        finally:
            if os.path.isfile(lnk):
                os.remove(lnk)


if __name__ == "__main__":
    unittest.main()
