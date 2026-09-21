# -*- coding: utf-8 -*-
"""浏览器扩展一键安装向导。

Edge：完全退出后用 ``--load-extension`` 一键启动加载，并可生成桌面快捷方式，
以后双击快捷方式即自动加载。Chrome 品牌版已封堵命令行侧载，改为一键打开扩展
管理页、在资源管理器定位扩展目录并把路径复制到剪贴板，把手动步骤压到最少。
"""
from __future__ import annotations

import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QDialog,
    QWidget,
)

from core import ext_install


class ExtInstallDialog(QDialog):
    def __init__(self, bridge_token: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("安装浏览器扩展")
        self.setMinimumWidth(600)
        self._token = bridge_token
        self._browsers = []
        self._ext_dir = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(16, 16, 16, 14)
        root.setSpacing(10)

        title = QLabel("安装「东方神速」浏览器扩展")
        title.setStyleSheet("font-size:15px;font-weight:600;")
        root.addWidget(title)

        intro = QLabel(
            "扩展用于接管浏览器下载与网页媒体（mp4 / m3u8 / blob 等）。"
            "受 Chrome、Edge 2025 年起的安全限制，未上架扩展无法被第三方完全"
            "静默安装：Edge 可一键加载（推荐配合桌面快捷方式长期使用）；"
            "Chrome 需在开发者模式下手动加载一次，之后会随浏览器保留。"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#6b7280;")
        root.addWidget(intro)

        # 扩展稳定目录
        dir_box = QGroupBox("扩展文件夹")
        dl = QHBoxLayout(dir_box)
        self.dir_edit = QLineEdit()
        self.dir_edit.setReadOnly(True)
        copy_dir = QPushButton("复制路径")
        copy_dir.clicked.connect(self._copy_dir)
        open_dir = QPushButton("打开文件夹")
        open_dir.clicked.connect(self._open_dir)
        dl.addWidget(self.dir_edit)
        dl.addWidget(copy_dir)
        dl.addWidget(open_dir)
        root.addWidget(dir_box)

        # 浏览器动态区
        self._browser_area = QVBoxLayout()
        self._browser_area.setSpacing(8)
        root.addLayout(self._browser_area)

        if self._token:
            tok = QLabel(
                "扩展装好后，点击浏览器工具栏的扩展图标，把桥接 Token 粘贴进去"
                "（可在「设置 → 界面与集成」复制 Token）。"
            )
        else:
            tok = QLabel(
                "提示：请先在「设置 → 界面与集成」启用浏览器扩展本地桥接，"
                "再把 Token 填入扩展弹窗。"
            )
        tok.setWordWrap(True)
        tok.setStyleSheet("color:#6b7280;")
        root.addWidget(tok)

        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

        close_btn = QPushButton("关闭")
        close_btn.setProperty("primary", True)
        close_btn.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.setContentsMargins(16, 0, 16, 12)
        row.addStretch(1)
        row.addWidget(close_btn)
        outer.addLayout(row)

        self._load()

    # ---------- 数据 ----------
    def _load(self):
        try:
            self._ext_dir = ext_install.deploy_stable_extension()
            self.dir_edit.setText(self._ext_dir)
            self._browsers = ext_install.detect_browsers()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "初始化失败", f"准备扩展目录时出错：\n{exc}")
            return
        # 清掉旧卡片
        while self._browser_area.count():
            item = self._browser_area.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        if not self._browsers:
            miss = QLabel("未检测到 Google Chrome 或 Microsoft Edge。")
            miss.setStyleSheet("color:#b91c1c;")
            self._browser_area.addWidget(miss)
            return
        for info in self._browsers:
            self._browser_area.addWidget(self._browser_card(info))
        self._browser_area.addStretch(1)

    def _browser_card(self, info) -> QGroupBox:
        box = QGroupBox(f"{info.name}" + (f"  {info.version}" if info.version else ""))
        v = QVBoxLayout(box)
        v.setSpacing(6)
        status = QLabel()
        v.addWidget(status)

        row = QHBoxLayout()
        if info.supports_cli_load:
            shortcut_cb = QCheckBox("同时创建桌面快捷方式（推荐，以后双击即自动加载）")
            shortcut_cb.setChecked(True)
            v.addWidget(shortcut_cb)

            btn = QPushButton("一键安装并启动 Edge")
            btn.setProperty("primary", True)
            btn.clicked.connect(lambda: self._install_edge(info, status, shortcut_cb))
            row.addWidget(btn)
            tip = QLabel("需短暂关闭 Edge 后以加载扩展的方式重启（标签页一般可恢复）。")
        else:
            btn = QPushButton("打开 Chrome 扩展页并定位文件夹")
            btn.setProperty("primary", True)
            btn.clicked.connect(lambda: self._install_chrome(info, status))
            row.addWidget(btn)
            tip = QLabel(
                "随后三步：① 打开右上角「开发者模式」；② 点「加载已解压的扩展程序」；"
                "③ 在弹出框直接粘贴已复制的路径并确定。"
            )
        tip.setStyleSheet("color:#6b7280;")
        tip.setWordWrap(True)
        row.addStretch(1)
        v.addLayout(row)
        v.addWidget(tip)
        self._refresh_status(info, status)
        return box

    def _refresh_status(self, info, status: QLabel):
        if ext_install.is_browser_running(info):
            status.setText("状态：正在运行")
            status.setStyleSheet("color:#b45309;")
        else:
            status.setText("状态：未运行")
            status.setStyleSheet("color:#15803d;")

    # ---------- 动作 ----------
    def _copy_dir(self):
        if self._ext_dir:
            QApplication.clipboard().setText(self._ext_dir)
            QMessageBox.information(self, "已复制", "扩展文件夹路径已复制到剪贴板。")

    def _open_dir(self):
        if self._ext_dir:
            ext_install.reveal_in_explorer(self._ext_dir)

    def _install_edge(self, info, status: QLabel, shortcut_cb: QCheckBox):
        try:
            if ext_install.is_browser_running(info):
                ret = QMessageBox.question(
                    self, "关闭 Edge",
                    "需要完全关闭 Edge 才能加载扩展（重启后标签页通常可恢复）。\n"
                    "是否现在关闭 Edge 并继续？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if ret != QMessageBox.Yes:
                    return
                ext_install.request_close_browser(info)
                for _ in range(10):
                    time.sleep(0.6)
                    QApplication.processEvents()
                    if not ext_install.is_browser_running(info):
                        break
                else:
                    ext_install.request_close_browser(info, force=True)
                    time.sleep(1.2)
            ext_install.deploy_stable_extension()
            if shortcut_cb.isChecked():
                lnk = ext_install.create_desktop_shortcut(info, self._ext_dir)
                status.setText(f"已加载扩展，并创建桌面快捷方式。\n{lnk}")
            else:
                status.setText("已加载扩展并启动 Edge。")
            status.setStyleSheet("color:#15803d;")
            ext_install.launch_edge_with_extension(info, self._ext_dir)
            QMessageBox.information(
                self, "完成",
                "Edge 已带扩展启动。若出现“关闭开发人员模式下的扩展”提示，"
                "请点“以后再说/保留”。以后请用桌面快捷方式启动 Edge，扩展会自动加载。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "安装失败", f"Edge 一键安装出错：\n{exc}")

    def _install_chrome(self, info, status: QLabel):
        try:
            ext_install.deploy_stable_extension()
            QApplication.clipboard().setText(self._ext_dir)
            ext_install.open_extensions_page(info)
            ext_install.reveal_in_explorer(self._ext_dir)
            status.setText("已打开扩展页、复制路径并定位文件夹，请按上面三步完成加载。")
            status.setStyleSheet("color:#15803d;")
            QMessageBox.information(
                self, "继续手动加载",
                "已为你：\n1) 打开 chrome://extensions\n2) 复制扩展文件夹路径\n"
                "3) 在资源管理器中定位该文件夹\n\n"
                "请在扩展页打开「开发者模式」→ 点「加载已解压的扩展程序」→ "
                "在弹出框粘贴路径并确定。\n"
                "若 Chrome 顶部提示停用开发者模式扩展，请选择“保留”。")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "安装失败", f"打开 Chrome 扩展页出错：\n{exc}")
