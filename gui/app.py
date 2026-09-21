# -*- coding: utf-8 -*-
"""应用装配：引擎、信号桥、托盘、剪贴板监听与浏览器桥接服务。"""
from __future__ import annotations

import re
import sys
import threading

from PyQt5.QtCore import (
    QIODevice,
    QLibraryInfo,
    QLocale,
    QObject,
    QTranslator,
    QUrl,
    pyqtSignal,
)
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication, QMessageBox, QStyleFactory

from core.bridge import BridgeServer
from core.config import Settings
from core.engine import DownloadManager

from .main_window import MainWindow
from .tray import TrayController

# 剪贴板监听命中的常见下载文件扩展名
DOWNLOAD_EXT_RE = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:zip|rar|7z|tar|gz|xz|exe|msi|dmg|pkg|deb|rpm|apk|"
    r"iso|img|pdf|doc|docx|xls|xlsx|ppt|pptx|txt|csv|epub|mobi|azw3|"
    r"mp4|mkv|avi|mov|wmv|flv|webm|m4v|ts|mp3|flac|ape|wav|aac|ogg|m4a|"
    r"jpg|jpeg|png|gif|bmp|webp|psd|ai|torrent|crx|xpi|jar|war|"
    r"whl|tar\.gz|zipx|bin|rom|vmdk|ova|ovf|m3u8|7z\.\d{3})(?:\?[^\s\"'<>]*)?$",
    re.IGNORECASE,
)


class EngineBridge(QObject):
    """把工作线程中的引擎回调转成 Qt 信号（自动跨线程排队到主线程）。"""

    added = pyqtSignal(str)
    stateChanged = pyqtSignal(str, str, str)
    event = pyqtSignal(str, str, str)
    removed = pyqtSignal(str)
    # 浏览器扩展入站下载：携带一个请求盒（含 threading.Event），由界面线程弹窗后回填
    inbound = pyqtSignal(object)

    def _on_inbound(self, payload: dict):
        """在桥接 HTTP 线程被调用：阻塞等待界面线程弹出确认窗的决定。

        返回 task_id 表示用户确认并已建任务；返回 None 表示用户取消。
        """
        box = {"payload": payload, "event": threading.Event(),
               "task_id": None}
        self.inbound.emit(box)
        box["event"].wait(300)  # 最多等 5 分钟，避免 HTTP 线程永久挂起
        return box.get("task_id")

    @property
    def callbacks(self) -> dict:
        return {
            "on_added": lambda t: self.added.emit(t.task_id),
            "on_state": lambda t, state, msg: self.stateChanged.emit(t.task_id, state, msg),
            "on_event": lambda t, level, msg: self.event.emit(t.task_id, level, msg),
            "on_removed": lambda tid: self.removed.emit(tid),
            "on_inbound": self._on_inbound,
        }


class ClipboardWatcher:
    def __init__(self, app: QApplication, window: MainWindow, manager: DownloadManager,
                 settings: Settings):
        self.app = app
        self.window = window
        self.manager = manager
        self.settings = settings
        self._last_url = ""
        app.clipboard().dataChanged.connect(self._on_change)

    def _on_change(self):
        if not self.settings.clipboard_watch:
            return
        text = self.app.clipboard().text().strip()
        m = DOWNLOAD_EXT_RE.search(text)
        if not m:
            return
        url = m.group(0)
        if url == self._last_url:
            return
        self._last_url = url
        if any(t.url == url for t in self.manager.tasks.values()):
            return
        box = QMessageBox(self.window)
        box.setWindowTitle("检测到下载链接")
        box.setText(f"是否下载以下文件？\n\n{url}")
        btn_yes = box.addButton("下载", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec_()
        if box.clickedButton() is btn_yes:
            self.window.add_url(url)


def acquire_single_instance(app: QApplication, name: str):
    """单实例锁。

    返回 QLocalServer 表示本进程是首个实例（应继续启动）；
    返回 None 表示已有实例在运行（调用方应通知其激活窗口后退出）。
    Windows 下 QLocalServer 即命名管道，进程正常退出后系统自动回收；
    异常退出残留时 removeServer 兜底。
    """
    probe = QLocalSocket()
    probe.connectToServer(name, QIODevice.WriteOnly)
    if probe.waitForConnected(300):
        # 已有实例：发“激活窗口”消息后让本进程退出
        probe.write(b"show")
        probe.flush()
        probe.waitForBytesWritten(500)
        probe.disconnectFromServer()
        return None
    probe.abort()

    server = QLocalServer()
    if not server.listen(name):
        QLocalServer.removeServer(name)
        server.listen(name)  # 仍失败则放弃单实例保护，但不阻断启动
    # 保持引用，防止被垃圾回收
    app._single_instance_server = server
    return server


def run() -> int:
    # 清理旧版本从源码/解释器运行时误写的开机启动残留（仅源码形态动作）
    try:
        from core import autostart
        autostart.cleanup_dev_residue()
    except Exception:  # noqa: BLE001 清理失败不阻断启动
        pass

    # 启动时确保浏览器扩展已部署到固定目录（%LOCALAPPDATA%，一次加载永久生效）
    from core.app_paths import ensure_browser_extension
    try:
        ensure_browser_extension()
    except Exception:  # noqa: BLE001 释放失败不阻断启动
        pass

    app = QApplication(sys.argv)
    from core.branding import APP_ID, APP_NAME
    app.setApplicationName(APP_ID)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle(QStyleFactory.create("Fusion"))

    # 单实例：已有实例运行时，激活其窗口并退出本进程
    single_server = acquire_single_instance(app, f"{APP_ID}-single-instance")
    if single_server is None:
        return 0

    from .theme import apply_theme
    apply_theme(app)

    # 加载 Qt 内置对话框中文翻译（确定/取消等按钮）
    translator = QTranslator()
    qt_i18n = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    if not translator.load(QLocale(QLocale.Chinese, QLocale.China),
                           "qtbase_", "", qt_i18n):
        translator.load("qt_zh_CN", qt_i18n)  # PyQt5 5.15 旧命名
    app.installTranslator(translator)

    settings = Settings.load()
    bridge = EngineBridge()
    manager = DownloadManager(settings, callbacks=bridge.callbacks)
    manager.start()

    # --minimized：开机自启等场景后台驻留托盘，不弹主窗口
    start_hidden = "--minimized" in sys.argv

    window = MainWindow(manager, settings, bridge)
    tray = TrayController(window, manager, notify_background=start_hidden)
    window.tray = tray
    watcher = ClipboardWatcher(app, window, manager, settings)

    http_bridge: BridgeServer | None = None

    def start_bridge():
        nonlocal http_bridge
        if http_bridge is None and settings.bridge_enabled:
            try:
                http_bridge = BridgeServer(manager, settings.bridge_port)
                http_bridge.start()
                window.bridge_server = http_bridge
            except OSError as exc:
                window.statusBar().showMessage(f"桥接服务启动失败: {exc}", 8000)
                http_bridge = None

    def stop_bridge():
        nonlocal http_bridge
        if http_bridge is not None:
            http_bridge.stop()
            http_bridge = None

    def on_settings_changed():
        if settings.bridge_enabled:
            start_bridge()
        else:
            stop_bridge()

    window.settingsChanged.connect(on_settings_changed)
    start_bridge()
    if start_hidden:
        window.hide()  # 开机自启：仅驻留托盘，不显示主窗口
    else:
        window.show()

    def _raise_existing():
        # 排空第二个实例发来的连接（消息体仅为唤醒信号）
        while single_server is not None and single_server.hasPendingConnections():
            conn = single_server.nextPendingConnection()
            if conn is not None:
                conn.readAll()
                conn.deleteLater()
        # 从最小化/托盘隐藏状态恢复并前置
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    if single_server is not None:
        single_server.newConnection.connect(_raise_existing)

    code = app.exec_()

    stop_bridge()
    manager.shutdown(pause_running=True)
    settings.save()
    return code
