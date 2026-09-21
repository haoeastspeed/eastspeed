# -*- coding: utf-8 -*-
"""自动更新 GUI：后台线程检查 / 下载（带进度）/ SHA256 校验 / 启动安装包。"""
from __future__ import annotations

import os
import tempfile

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QMessageBox,
    QProgressDialog,
)

from core import updater
from core.branding import APP_NAME, APP_VERSION


class _CheckThread(QThread):
    done = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def run(self):
        try:
            self.done.emit(updater.check_for_update(self._url))
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(str(exc))


class _DownloadThread(QThread):
    progress_sig = pyqtSignal(int, int)
    done = pyqtSignal(str)
    fail = pyqtSignal(str)

    def __init__(self, info: dict, dest_dir: str):
        super().__init__()
        self._info = info
        self._dest = dest_dir

    def run(self):
        try:
            path = updater.download_update(
                self._info, self._dest,
                progress=lambda d, t: self.progress_sig.emit(int(d), int(t)))
            self.done.emit(path)
        except Exception as exc:  # noqa: BLE001
            self.fail.emit(str(exc))


def _hold(parent, thread: QThread):
    """把线程挂到父窗口上，防止局部变量被回收。"""
    if not hasattr(parent, "_dfs_update_threads"):
        parent._dfs_update_threads = []
    parent._dfs_update_threads.append(thread)
    thread.finished.connect(lambda: parent._dfs_update_threads.remove(thread)
                            if thread in getattr(parent, "_dfs_update_threads", [])
                            else None)


def check_for_updates(settings, parent, silent: bool = False):
    """检查更新。silent=True 时仅在发现新版本时打扰用户（用于启动后静默检查）。"""
    url = (getattr(settings, "update_url", "") or "").strip()
    if not url:
        if not silent:
            QMessageBox.information(
                parent, "检查更新",
                "尚未配置更新源。\n请在“设置 → 高级”中填写更新清单 "
                "（latest.json）地址，例如 GitHub/Gitee Releases 或自建地址。")
        return

    prog = QProgressDialog("正在检查更新…", None, 0, 0, parent)
    prog.setWindowTitle("检查更新")
    prog.setWindowModality(2)  # WindowModal
    prog.setCancelButton(None)
    prog.show()

    check = _CheckThread(url)

    def on_done(info):
        prog.close()
        if not info:
            if not silent:
                QMessageBox.information(
                    parent, "检查更新",
                    f"当前已是最新版本（{APP_VERSION}）。")
            return
        _offer_update(info, parent)

    def on_fail(msg):
        prog.close()
        if not silent:
            QMessageBox.warning(parent, "检查更新", msg)

    check.done.connect(on_done)
    check.fail.connect(on_fail)
    _hold(parent, check)
    check.start()


def _offer_update(info: dict, parent):
    box = QMessageBox(parent)
    box.setWindowTitle("发现新版本")
    box.setIcon(QMessageBox.Information)
    notes = info.get("notes") or ""
    box.setText(f"发现新版本 {info['version']}（当前 {APP_VERSION}）。\n\n{notes}")
    btn_yes = box.addButton("立即下载更新", QMessageBox.AcceptRole)
    box.addButton("稍后", QMessageBox.RejectRole)
    box.exec_()
    if box.clickedButton() is not btn_yes:
        return

    dest_dir = tempfile.gettempdir()
    prog = QProgressDialog("正在下载更新…", "取消", 0, 100, parent)
    prog.setWindowTitle("下载更新")
    prog.setWindowModality(2)
    prog.setAutoClose(False)
    prog.setMinimumDuration(0)

    dl = _DownloadThread(info, dest_dir)

    def on_progress(done, total):
        if total > 0:
            prog.setMaximum(100)
            prog.setValue(min(100, int(done * 100 / total)))

    def on_done(path):
        prog.close()
        ret = QMessageBox.question(
            parent, "更新已就绪",
            "更新已下载并通过 SHA256 校验。\n立即关闭程序并安装新版本吗？\n"
            "（安装程序会自动关闭当前版本并覆盖安装）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret == QMessageBox.Yes:
            if updater.launch_installer(path):
                QApplication.quit()
            else:
                QMessageBox.warning(
                    parent, "更新",
                    f"无法自动启动安装程序，请手动运行：\n{path}")

    def on_fail(msg):
        prog.close()
        QMessageBox.warning(parent, "更新", msg)

    dl.progress_sig.connect(on_progress)
    dl.done.connect(on_done)
    dl.fail.connect(on_fail)
    prog.canceled.connect(dl.terminate)
    _hold(parent, dl)
    dl.start()
