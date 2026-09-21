# -*- coding: utf-8 -*-
"""GUI 冒烟测试：实例化主窗口/对话框并截图（offscreen 或 --real 真实桌面）。

运行: python tests/smoke_gui.py [--real]
截图输出到 tests/output/
"""
import os
import sys
import tempfile

if "--real" not in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

appdata = tempfile.mkdtemp(prefix="pydl-gui-appdata-")
os.environ["APPDATA"] = appdata

from PyQt5.QtWidgets import QApplication  # noqa: E402

from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402
from gui.add_dialog import AddDownloadDialog  # noqa: E402
from gui.batch_dialog import BatchAddDialog  # noqa: E402
from gui.grab_dialog import GrabSiteDialog  # noqa: E402
from gui.app import EngineBridge  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.settings_dialog import SettingsDialog  # noqa: E402
from gui.theme import apply_theme  # noqa: E402
from tests.range_server import make_server  # noqa: E402
from tests.hls_server import make_hls_server  # noqa: E402

app = QApplication([])
apply_theme(app)

serve = tempfile.mkdtemp(prefix="pydl-served-")
# 大文件限速：截图时保持“下载中”
with open(os.path.join(serve, "demo.bin"), "wb") as f:
    f.write(os.urandom(16 * 1024 * 1024))
# 小文件：迅速完成，覆盖不同分类
for name, size in (("movie.mp4", 2 * 1024 * 1024),
                   ("song.mp3", 512 * 1024),
                   ("report.pdf", 300 * 1024),
                   ("archive.zip", 800 * 1024)):
    with open(os.path.join(serve, name), "wb") as f:
        f.write(os.urandom(size))
httpd, base = make_server(serve)
hls_httpd, hls_base, _hls_fx = make_hls_server()

dl = tempfile.mkdtemp(prefix="pydl-gui-dl-")
settings = Settings(save_dir=dl, block_size=256 * 1024, auto_resume=False,
                    av_scan=False)
bridge = EngineBridge()
manager = DownloadManager(settings, callbacks=bridge.callbacks)
manager.start()

win = MainWindow(manager, settings, bridge)
manager.add(f"{base}/file/demo.bin?kbps=400",
            TaskOptions(connections=8, filename="demo-installer.bin"))
manager.add(f"{hls_base}/vod/master.m3u8",
            TaskOptions(connections=8, filename="lesson.m3u8"))
manager.add(f"{base}/file/movie.mp4", TaskOptions(filename="movie.mp4"))
manager.add(f"{base}/file/song.mp3", TaskOptions(filename="song.mp3"))
manager.add(f"{base}/file/report.pdf", TaskOptions(filename="report.pdf"),
            start_paused=True)
manager.add(f"{base}/file/archive.zip", TaskOptions(filename="archive.zip"))
manager.add(f"{base}/file/missing.exe", TaskOptions(filename="broken-download.exe"))

from PyQt5.QtTest import QTest  # noqa: E402

out_dir = os.path.join(_ROOT, "tests", "output")
os.makedirs(out_dir, exist_ok=True)

suffix = "_real" if "--real" in sys.argv else ""
STAGE_LOG = os.path.join(_ROOT, "tests", "output", "stage.log")

def _log(msg):
    import time
    line = f"[{time.strftime('%H:%M:%S')}] {msg}\n"
    with open(STAGE_LOG, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()

open(STAGE_LOG, "w", encoding="utf-8").close()
_log("imports done")
win.resize(1080, 620)
win.show()
QTest.qWait(3500)  # 让小任务完成、HLS 合并、布局与分类计数稳定
_log("manual refresh")
win.refresh()
QTest.qWait(300)
_log("grab main")
win.grab().save(os.path.join(out_dir, f"mainwindow{suffix}.png"))
_log("main saved")

add = AddDownloadDialog(settings, "https://example.com/file.zip", win)
add.show()
QTest.qWait(200)
add.resize(600, 480)
QTest.qWait(200)
_log("grab add")
add.grab().save(os.path.join(out_dir, f"add_dialog{suffix}.png"))
add.close()
_log("add saved")

sd = SettingsDialog(settings, "test-token-AbCdEf123456", win)
sd.show()
QTest.qWait(200)
from PyQt5.QtWidgets import QTabWidget
_sd_tabs = sd.findChild(QTabWidget)
for _ti in range(_sd_tabs.count()):
    _sd_tabs.setCurrentIndex(_ti)
    QTest.qWait(120)
    sd.grab().save(os.path.join(out_dir, f"settings_tab{_ti}{suffix}.png"))
_log("grab settings tabs")
sd.close()
_log("settings saved")

from gui.ext_install_dialog import ExtInstallDialog  # noqa: E402
ei_dlg = ExtInstallDialog("test-token-AbCdEf123456", win)
ei_dlg.show()
QTest.qWait(500)
ei_dlg.resize(620, 600)
QTest.qWait(150)
ei_dlg.grab().save(os.path.join(out_dir, f"ext_install{suffix}.png"))
ei_dlg.close()
_log("ext installer saved")

batch = BatchAddDialog(settings, win)
batch.urls_edit.setPlainText(
    "https://example.com/data/report-2026.xlsx\n"
    "https://cdn.example.com/video/lesson03.mp4\n"
    "https://cdn.example.com/video/lesson04.mp4\n"
    "https://example.com/setup/app-1.2.0.exe\n"
)
batch.show()
QTest.qWait(200)
batch.resize(620, 460)
QTest.qWait(200)
_log("grab batch")
batch.grab().save(os.path.join(out_dir, f"batch_dialog{suffix}.png"))
batch.close()
_log("batch saved")

grab = GrabSiteDialog(settings, manager,
                      "https://example.com/course/lesson01.html", win)
grab.show()
QTest.qWait(200)
grab.resize(700, 580)
QTest.qWait(200)
_log("grab grab-dialog")
grab.grab().save(os.path.join(out_dir, f"grab_dialog{suffix}.png"))
grab.close()
_log("grab saved")

states = {t.filename: t.state for t in manager.tasks.values()}
manager.shutdown()
httpd.shutdown()
hls_httpd.shutdown()
print("GUI smoke OK; states =", states)
print("screenshots in", out_dir)
