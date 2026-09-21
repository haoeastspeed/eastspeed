# -*- coding: utf-8 -*-
"""第十二轮功能测试：开机自启、全部完成后电源动作、清除已完成、扩展固定目录统一。

运行: python -m unittest tests.test_round12 -v
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core import autostart, power  # noqa: E402
from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.task import TaskState  # noqa: E402


class AutostartTests(unittest.TestCase):
    """开机自启写当前用户注册表 Run 键（Windows）。"""

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows only")
    def test_registry_write_cycle(self):
        # 底层注册表读写：注册命令必须带 --minimized（开机后台驻留）
        before = autostart._read()
        try:
            autostart._write(autostart.startup_command())
            self.assertTrue(autostart.is_enabled())
            self.assertIn("--minimized", autostart._read())
            autostart._delete()
            self.assertFalse(autostart.is_enabled())
        finally:
            if before:
                autostart._write(before)
            else:
                autostart._delete()

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows only")
    def test_enable_blocked_outside_frozen(self):
        # 非打包（源码/解释器）形态不允许注册开机启动，应抛错而非写注册表
        if autostart.is_frozen():
            self.skipTest("仅在源码运行形态校验")
        before = autostart._read()
        try:
            with self.assertRaises(RuntimeError):
                autostart.enable()
        finally:
            if before:
                autostart._write(before)
            else:
                autostart._delete()

    @unittest.skipUnless(sys.platform.startswith("win"), "Windows only")
    def test_cleanup_dev_residue(self):
        if autostart.is_frozen():
            self.skipTest("仅在源码运行形态校验")
        dev_cmd = r'"C:\Temp\pythonw.exe" "C:\proj\main.py" --minimized'
        exe_cmd = r'"C:\Program Files\DongFangSpeed\DongFangSpeed.exe" --minimized'
        before = autostart._read()
        try:
            autostart._write(dev_cmd)
            self.assertTrue(autostart.cleanup_dev_residue())
            self.assertFalse(autostart.is_enabled())
            # 不含 main.py 的正式 exe 命令不应被误删
            autostart._write(exe_cmd)
            self.assertFalse(autostart.cleanup_dev_residue())
            self.assertTrue(autostart.is_enabled())
        finally:
            autostart._delete()
            if before:
                autostart._write(before)


class PowerActionTests(unittest.TestCase):
    def test_labels_cover_actions(self):
        self.assertEqual(set(power.ACTIONS), set(power.ACTION_LABELS.keys()))
        self.assertIn("shutdown", power.ACTIONS)

    def test_schedule_shutdown_command(self):
        with mock.patch("core.power.subprocess.run") as run:
            power.schedule_shutdown(60)
            args = run.call_args[0][0]
            self.assertIn("/s", args)
            self.assertIn("60", args)

    def test_cancel_shutdown_command(self):
        with mock.patch("core.power.subprocess.run") as run:
            power.cancel_shutdown()
            args = run.call_args[0][0]
            self.assertIn("/a", args)

    def test_hibernate_command(self):
        with mock.patch("core.power.subprocess.run") as run:
            power.hibernate()
            args = run.call_args[0][0]
            self.assertIn("/h", args)

    def test_sleep_uses_suspend_state(self):
        with mock.patch("core.power.ctypes") as mc:
            power.sleep()
            mc.windll.PowrProf.SetSuspendState.assert_called_once_with(0, 0, 0)

    def test_perform_dispatch(self):
        with mock.patch("core.power.subprocess.run") as run:
            power.perform("shutdown")
            self.assertIn("/s", run.call_args[0][0])
        # none / exit 不应触发任何系统命令
        with mock.patch("core.power.subprocess.run") as run:
            power.perform("none")
            run.assert_not_called()


class RemoveCompletedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["APPDATA"] = self.tmp
        self.settings = Settings()
        self.manager = DownloadManager(self.settings)  # 不 start，无调度线程

    def test_only_completed_removed(self):
        t1 = self.manager.add("http://example.com/a.zip")
        t1.set_state(TaskState.COMPLETED)
        t2 = self.manager.add("http://example.com/b.zip")
        t2.set_state(TaskState.PAUSED)
        t3 = self.manager.add("http://example.com/c.zip")
        t3.set_state(TaskState.COMPLETED)
        t4 = self.manager.add("http://example.com/d.zip")
        t4.set_state(TaskState.ERROR)

        removed = self.manager.remove_completed(False)
        self.assertEqual(removed, 2)
        left = {s["task_id"] for s in self.manager.snapshots()}
        self.assertEqual(left, {t2.task_id, t4.task_id})

    def test_remove_completed_empty(self):
        self.assertEqual(self.manager.remove_completed(False), 0)


class StableExtensionDirTests(unittest.TestCase):
    def test_stable_dir_modules_agree(self):
        """ext_install 与 app_paths 必须指向同一个固定目录（一次加载永久生效）。"""
        from core import app_paths
        from core.ext_install import stable_extension_dir as ei_stable
        self.assertEqual(ei_stable(), app_paths.stable_extension_dir())
        d = app_paths.stable_extension_dir()
        self.assertIn("DongFangSpeed", d)
        self.assertTrue(d.replace("\\", "/").endswith("browser_extension"))

    def test_deploy_to_stable_idempotent(self):
        from core.ext_install import deploy_stable_extension
        out = deploy_stable_extension()
        self.assertTrue(os.path.isfile(os.path.join(out, "manifest.json")))
        out2 = deploy_stable_extension()
        self.assertEqual(out, out2)


if __name__ == "__main__":
    unittest.main()
