# -*- coding: utf-8 -*-
"""系统托盘图标与菜单。"""
from __future__ import annotations

from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

from core.branding import TRAY_TOOLTIP, APP_NAME
from .icons import app_icon


class TrayController:
    def __init__(self, window, manager, notify_background: bool = False):
        self.window = window
        self.manager = manager
        self.tray = QSystemTrayIcon(app_icon(), window)
        self.tray.setToolTip(TRAY_TOOLTIP)

        menu = QMenu()
        act_show = QAction("显示主窗口", window)
        act_pause = QAction("全部暂停", window)
        act_resume = QAction("全部继续", window)
        act_quit = QAction("退出", window)
        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_pause)
        menu.addAction(act_resume)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)

        act_show.triggered.connect(self._show)
        act_pause.triggered.connect(manager.pause_all)
        act_resume.triggered.connect(manager.resume_all)
        act_quit.triggered.connect(self._quit)
        self.tray.activated.connect(self._activated)
        self.tray.show()
        if notify_background:
            self.tray.showMessage(
                f"{APP_NAME}已在后台运行",
                "程序将驻留系统托盘，可从托盘菜单退出。",
            )

    def _show(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def _activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show()

    def _quit(self):
        self.tray.hide()
        QApplication.quit()
