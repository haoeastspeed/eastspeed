# -*- coding: utf-8 -*-
"""任务列表选中保持回归测试。

历史问题：列表每秒 beginResetModel/endResetModel 全量重置，会清空当前选中行，
导致“下载列表里的文件无法选中、工具栏按钮全灰”。本测试在 offscreen 下验证：
1) 同序刷新（仅 dataChanged）后选中不丢；
2) 新增任务导致模型 reset 后，仍按 task_id 恢复选中；
3) 选中错误任务时“继续/重新下载/删除”按钮可用。

运行: python -m unittest tests.test_gui_selection -v
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_TMP = tempfile.mkdtemp(prefix="pydl-sel-appdata-")
os.environ["APPDATA"] = _TMP
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import unittest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.config import Settings  # noqa: E402
from gui.app import EngineBridge  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.models import COLUMNS  # noqa: E402

app = QApplication.instance() or QApplication([])


def make_snap(tid, name, state, state_cn, created_at, error=""):
    return {
        "task_id": tid, "filename": name, "url": f"https://example.com/{name}",
        "state": state, "state_cn": state_cn, "progress": 0.0, "size": 1024,
        "downloaded": 0, "speed": 0, "eta": 0, "error": error,
        "final_path": "", "save_dir": "", "created_at": created_at,
        "checksum_actual": "",
    }


class FakeManager:
    def __init__(self, snaps):
        self._snaps = snaps
        self.tasks = {}

    def snapshots(self):
        return self._snaps

    def resume_all(self):
        pass

    def pause_all(self):
        pass


class SelectionKeepTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(save_dir=tempfile.mkdtemp(prefix="pydl-sel-dl-"))
        self.bridge = EngineBridge()
        self.snaps = [
            make_snap("t-completed", "a.zip", "completed", "已完成", 3),
            make_snap("t-error", "b.exe", "error", "错误", 2, error="403 Forbidden"),
            make_snap("t-paused", "c.mp4", "paused", "已暂停", 1),
        ]
        self.manager = FakeManager(self.snaps)
        self.win = MainWindow(self.manager, self.settings, self.bridge)

    def _row_of(self, tid):
        for r in range(self.win.model.rowCount()):
            if self.win.model.task_id_at(r) == tid:
                return r
        return -1

    def test_selection_survives_repeated_refresh(self):
        self.win.refresh()
        row = self._row_of("t-error")
        self.assertGreaterEqual(row, 0)
        self.win.table.selectRow(row)
        self.assertEqual(self.win._selected_id(), "t-error")
        # 模拟每秒刷新（行集合不变，走 dataChanged 路径）
        for _ in range(5):
            self.win.refresh()
        self.assertEqual(self.win._selected_id(), "t-error")
        # 错误任务：继续 / 重新下载 / 删除 应可用
        self.assertTrue(self.win.act_resume.isEnabled())
        self.assertTrue(self.win.act_restart.isEnabled())
        self.assertTrue(self.win.act_delete.isEnabled())

    def test_selection_restored_after_model_reset(self):
        self.win.refresh()
        row = self._row_of("t-paused")
        self.win.table.selectRow(row)
        self.assertEqual(self.win._selected_id(), "t-paused")
        # 新增一个任务（id 序列变化 -> reset），旧选中任务仍在列表中
        self.snaps.insert(0, make_snap("t-new", "new.bin", "queued", "等待中", 9))
        self.win.refresh()
        self.assertEqual(self.win._selected_id(), "t-paused")


class ColumnCustomizeTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(save_dir=tempfile.mkdtemp(prefix="pydl-col-dl-"))
        self.bridge = EngineBridge()
        self.manager = FakeManager([])
        self.win = MainWindow(self.manager, self.settings, self.bridge)

    def test_hide_column_persists(self):
        # “速度”列 = 第 4 列
        self.win._toggle_column(4, False)
        self.assertTrue(self.win.table.isColumnHidden(4))
        self.assertIn("速度", self.settings.hidden_columns)

    def test_filename_column_cannot_hide(self):
        self.win._toggle_column(0, False)
        self.assertFalse(self.win.table.isColumnHidden(0))

    def test_column_width_remembered(self):
        # 交互式改宽“大小”列（第 1 列），应记入设置
        self.win.table.setColumnWidth(1, 132)
        self.assertEqual(self.settings.column_widths.get("大小"), 132)
        # 弹性状态列（最后一列）不记录
        self.win._on_section_resized(len(COLUMNS) - 1, 100, 200)
        self.assertNotIn("状态", self.settings.column_widths)

    def test_reset_columns(self):
        self.win._toggle_column(4, False)  # 隐藏速度列
        self.win.table.setColumnWidth(1, 200)
        self.win._reset_columns()
        for c in range(len(COLUMNS)):
            self.assertFalse(self.win.table.isColumnHidden(c))
        self.assertEqual(self.settings.hidden_columns, [])
        self.assertEqual(self.settings.column_widths, {})
        self.assertEqual(self.win.table.columnWidth(1),
                         self.win._default_widths[1])

    def test_persisted_layout_restored_on_recreate(self):
        self.settings.hidden_columns = ["速度"]
        self.settings.column_widths = {"文件名": 401}
        self.settings.save()
        loaded = Settings.load()
        win2 = MainWindow(FakeManager([]), loaded, EngineBridge())
        self.assertTrue(win2.table.isColumnHidden(4))
        self.assertEqual(win2.table.columnWidth(0), 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
