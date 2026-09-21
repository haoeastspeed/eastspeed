# -*- coding: utf-8 -*-
"""设置对话框（多选项卡）。"""
from __future__ import annotations

from PyQt5.QtCore import QTime, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)


def _parse_time(text: str) -> QTime:
    try:
        h, m = text.split(":")
        t = QTime(int(h), int(m))
        return t if t.isValid() else QTime(0, 0)
    except Exception:
        return QTime(0, 0)


class SettingsDialog(QDialog):
    def __init__(self, settings, bridge_token: str = "", parent=None):
        super().__init__(parent)
        self.settings = settings
        self.bridge_token = bridge_token
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)

        root = QVBoxLayout(self)
        tabs = QTabWidget()

        def new_tab(title: str) -> QFormLayout:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(14, 14, 14, 10)
            tabs.addTab(page, title)
            return form

        # ---------- 选项卡 1：下载 ----------
        f1 = new_tab("下载")

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(settings.save_dir)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(browse)
        f1.addRow("默认下载目录:", dir_row)

        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setValue(settings.connections)
        f1.addRow("每任务连接数:", self.conn_spin)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 10)
        self.concurrent_spin.setValue(settings.max_concurrent)
        f1.addRow("同时下载任务数:", self.concurrent_spin)

        self.speed_spin = QSpinBox()
        self.speed_spin.setRange(0, 1024 * 1024)
        self.speed_spin.setSingleStep(64)
        self.speed_spin.setValue(settings.speed_limit // 1024)
        self.speed_spin.setSuffix(" KB/s（0 不限）")
        f1.addRow("全局限速:", self.speed_spin)

        self.stall_spin = QSpinBox()
        self.stall_spin.setRange(3, 120)
        self.stall_spin.setValue(settings.stall_timeout)
        self.stall_spin.setSuffix(" 秒无数据则重连")
        f1.addRow("卡死连接判定:", self.stall_spin)

        self.site_edit = QPlainTextEdit()
        self.site_edit.setPlaceholderText("每行一条，域名=连接数，例如：\nexample.com=4\ncdn.test.org=16")
        self.site_edit.setPlainText(
            "\n".join(f"{d}={n}" for d, n in (settings.site_connections or {}).items())
        )
        self.site_edit.setMaximumHeight(78)
        f1.addRow("站点连接数例外:", self.site_edit)

        # ---------- 选项卡 2：高级（工具与协议） ----------
        f2 = new_tab("高级")

        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_edit = QLineEdit(getattr(settings, "ffmpeg_path", ""))
        self.ffmpeg_edit.setPlaceholderText("留空则在系统 PATH 中查找 ffmpeg")
        ff_btn = QPushButton("浏览…")
        ff_btn.clicked.connect(self._pick_ffmpeg)
        ffmpeg_row.addWidget(self.ffmpeg_edit)
        ffmpeg_row.addWidget(ff_btn)
        f2.addRow("ffmpeg 路径:", ffmpeg_row)

        self.prealloc_cb = QCheckBox("真实占盘式预分配（占满磁盘，防止中途盘满）")
        self.prealloc_cb.setToolTip(
            "开启后在 Windows 上由内核零填充并真实占满簇；关闭则使用稀疏文件，更省实际空间。")
        self.prealloc_cb.setChecked(getattr(settings, "preallocate", False))
        f2.addRow(self.prealloc_cb)

        self.av_cb = QCheckBox("下载完成后调用 Windows Defender 扫描文件")
        self.av_cb.setChecked(getattr(settings, "av_scan", True))
        f2.addRow(self.av_cb)

        self.h2_cb = QCheckBox("优先使用 HTTP/2（不支持时自动回退 HTTP/1.1）")
        self.h2_cb.setChecked(getattr(settings, "prefer_http2", False))
        try:
            from core.http2 import h2_available
            if not h2_available():
                self.h2_cb.setEnabled(False)
                self.h2_cb.setText("优先使用 HTTP/2（未装 httpx[h2]，不可用）")
        except Exception:
            pass
        f2.addRow(self.h2_cb)

        self.insecure_tls_cb = QCheckBox(
            "忽略 HTTPS 证书错误（不安全；仅用于可信内网/自签证书站点）")
        self.insecure_tls_cb.setChecked(not getattr(settings, "verify_ssl", True))
        f2.addRow(self.insecure_tls_cb)

        self.update_url_edit = QLineEdit(getattr(settings, "update_url", ""))
        self.update_url_edit.setPlaceholderText(
            "https://你的站点/dongfangspeed/latest.json（留空则不检查更新）")
        f2.addRow("更新清单地址:", self.update_url_edit)

        self.autoupdate_cb = QCheckBox("启动时自动检查更新（有新版本才提示）")
        self.autoupdate_cb.setChecked(getattr(settings, "check_update_on_start", True))
        f2.addRow(self.autoupdate_cb)

        # ---------- 选项卡 3：提醒与整理 ----------
        f3 = new_tab("提醒与整理")

        self.categorize_cb = QCheckBox("按文件类型自动归类到 视频/音频/文档/程序/压缩包 子文件夹")
        self.categorize_cb.setChecked(getattr(settings, "auto_categorize", False))
        f3.addRow(self.categorize_cb)

        self.notify_cb = QCheckBox("下载完成 / 失败时弹出系统通知")
        self.notify_cb.setChecked(getattr(settings, "notify_on_complete", True))
        f3.addRow(self.notify_cb)

        self.sound_cb = QCheckBox("通知时播放提示音")
        self.sound_cb.setChecked(getattr(settings, "play_sound", False))
        f3.addRow(self.sound_cb)

        # ---------- 选项卡 4：计划任务 ----------
        f4 = new_tab("计划任务")

        self.sched_cb = QCheckBox("启用计划任务（每天定时）")
        self.sched_cb.setChecked(getattr(settings, "scheduler_enabled", False))
        f4.addRow(self.sched_cb)

        self.start_time = QTimeEdit(_parse_time(getattr(settings, "scheduler_start", "")))
        self.start_time.setDisplayFormat("HH:mm")
        f4.addRow("到点全部开始:", self.start_time)

        self.stop_time = QTimeEdit(_parse_time(getattr(settings, "scheduler_stop", "")))
        self.stop_time.setDisplayFormat("HH:mm")
        f4.addRow("到点全部暂停:", self.stop_time)
        sched_hint = QLabel("留 00:00 表示不设置该动作。")
        sched_hint.setStyleSheet("color: #6b7280;")
        f4.addRow("", sched_hint)

        from core import power
        self.after_done_combo = QComboBox()
        for act in power.ACTIONS:
            self.after_done_combo.addItem(power.ACTION_LABELS[act], act)
        cur_action = getattr(settings, "after_done_action", "none") or "none"
        self.after_done_combo.setCurrentIndex(
            max(0, self.after_done_combo.findData(cur_action)))
        f4.addRow("全部下载完成后:", self.after_done_combo)
        after_hint = QLabel(
            "所有任务都下载完成后自动执行；关机/休眠/睡眠前会弹出 60 秒倒计时，可随时取消。")
        after_hint.setWordWrap(True)
        after_hint.setStyleSheet("color: #6b7280;")
        f4.addRow("", after_hint)

        # ---------- 选项卡 5：界面与浏览器集成 ----------
        f5 = new_tab("界面与集成")

        self.autostart_cb = QCheckBox("开机自动启动（后台驻留系统托盘）")
        self._autostart_supported = False
        try:
            from core import autostart
            # 仅打包后的 exe 允许注册开机启动；源码/解释器运行时禁用该开关
            self._autostart_supported = autostart.is_allowed()
            self.autostart_cb.setChecked(
                autostart.is_enabled() if self._autostart_supported else False)
            if not autostart.is_allowed():
                self.autostart_cb.setEnabled(False)
                self.autostart_cb.setText(
                    "开机自动启动（仅单文件版/安装版支持）"
                    if autostart.is_supported()
                    else "开机自动启动（当前系统不支持）")
        except Exception:  # noqa: BLE001  注册表不可用时不影响其余设置
            self.autostart_cb.setEnabled(False)
        f5.addRow(self.autostart_cb)

        self.clipboard_cb = QCheckBox("监听剪贴板中的下载链接")
        self.clipboard_cb.setChecked(settings.clipboard_watch)
        f5.addRow(self.clipboard_cb)

        self.tray_cb = QCheckBox("关闭窗口时最小化到系统托盘")
        self.tray_cb.setChecked(settings.minimize_to_tray)
        f5.addRow(self.tray_cb)

        self.autoresume_cb = QCheckBox("启动后自动继续未完成的下载")
        self.autoresume_cb.setChecked(settings.auto_resume)
        f5.addRow(self.autoresume_cb)

        self.bridge_cb = QCheckBox("启用浏览器扩展本地桥接（HTTP）")
        self.bridge_cb.setChecked(settings.bridge_enabled)
        f5.addRow(self.bridge_cb)

        self.takeover_cb = QCheckBox("浏览器接管下载时弹出确认窗口（推荐，类 IDM）")
        self.takeover_cb.setChecked(getattr(settings, "takeover_prompt", True))
        f5.addRow(self.takeover_cb)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(settings.bridge_port)
        f5.addRow("桥接端口:", self.port_spin)

        token_row = QHBoxLayout()
        self.token_edit = QLineEdit(self.bridge_token)
        self.token_edit.setReadOnly(True)
        self.token_edit.setPlaceholderText("启用桥接后生成，扩展会自动配对，通常无需复制")
        copy_btn = QPushButton("复制 Token")
        copy_btn.clicked.connect(self._copy_token)
        token_row.addWidget(self.token_edit)
        token_row.addWidget(copy_btn)
        f5.addRow("桥接 Token:", token_row)

        guide = QLabel(
            "扩展用于接管浏览器下载与网页媒体，装好后会自动配对、无需手动填 Token；"
            "程序未运行时不会拦截，浏览器会照常下载。"
            "Edge 可一键加载并生成桌面快捷方式；"
            "Chrome 需在开发者模式下加载一次（向导会自动打开页面并定位文件夹）。"
        )
        guide.setWordWrap(True)
        guide.setStyleSheet("color: #6b7280;")
        f5.addRow("", guide)

        ext_row = QHBoxLayout()
        install_ext_btn = QPushButton("一键安装浏览器扩展…")
        install_ext_btn.setProperty("primary", True)
        install_ext_btn.clicked.connect(self._open_ext_installer)
        ext_row.addWidget(install_ext_btn)
        ext_btn = QPushButton("打开扩展文件夹")
        ext_btn.clicked.connect(self._open_extension_dir)
        ext_row.addWidget(ext_btn)
        ext_row.addStretch(1)
        f5.addRow("", ext_row)

        # ---------- 选项卡 6：代理 ----------
        f6 = new_tab("代理")

        self.proxy_edit = QLineEdit(getattr(settings, "proxy", ""))
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        f6.addRow("固定代理服务器:", self.proxy_edit)

        self.proxy_auth_combo = QComboBox()
        self.proxy_auth_combo.addItem("不认证", "")
        self.proxy_auth_combo.addItem("Basic", "basic")
        self.proxy_auth_combo.addItem("NTLM（Windows 域）", "ntlm")
        self.proxy_auth_combo.addItem("Negotiate", "negotiate")
        cur_auth = getattr(settings, "proxy_auth", "") or ""
        idx = max(0, self.proxy_auth_combo.findData(cur_auth))
        self.proxy_auth_combo.setCurrentIndex(idx)
        f6.addRow("代理认证方式:", self.proxy_auth_combo)

        self.proxy_user_edit = QLineEdit(getattr(settings, "proxy_username", ""))
        f6.addRow("代理用户名:", self.proxy_user_edit)

        self.proxy_pwd_edit = QLineEdit(getattr(settings, "proxy_password", ""))
        self.proxy_pwd_edit.setEchoMode(QLineEdit.Password)
        f6.addRow("代理密码:", self.proxy_pwd_edit)

        self.proxy_domain_edit = QLineEdit(getattr(settings, "proxy_domain", ""))
        self.proxy_domain_edit.setPlaceholderText("仅 NTLM 需要，如 CORP")
        f6.addRow("NTLM 域:", self.proxy_domain_edit)

        self.pac_edit = QLineEdit(getattr(settings, "pac_url", ""))
        self.pac_edit.setPlaceholderText("http://example.com/proxy.pac（留空则用固定代理）")
        f6.addRow("PAC 脚本地址:", self.pac_edit)

        proxy_hint = QLabel(
            "填写 PAC 后将按每个下载的目标网址自动选择代理（Windows 使用系统 WinHTTP "
            "引擎，与 IE/Edge 一致），判定直连时不走代理。也可直接在代理地址中内嵌账号，"
            "如 http://用户:密码@127.0.0.1:7890。注：Digest 代理认证、FTP 经 HTTP "
            "CONNECT 隧道暂不支持。")
        proxy_hint.setWordWrap(True)
        proxy_hint.setStyleSheet("color: #6b7280;")
        f6.addRow("", proxy_hint)

        root.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择默认下载目录", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _pick_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 ffmpeg 可执行文件", "",
                                              "可执行文件 (ffmpeg.exe ffmpeg);;所有文件 (*)")
        if path:
            self.ffmpeg_edit.setText(path)

    def _copy_token(self):
        if self.token_edit.text():
            QApplication.clipboard().setText(self.token_edit.text())
            self.token_edit.setFocus()
            self.token_edit.selectAll()

    def _open_extension_dir(self):
        try:
            from core.app_paths import ensure_browser_extension
            path = ensure_browser_extension()
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        except Exception:  # noqa: BLE001
            pass

    def _open_ext_installer(self):
        from gui.ext_install_dialog import ExtInstallDialog
        dlg = ExtInstallDialog(bridge_token=self.bridge_token, parent=self)
        dlg.exec_()

    def _parse_sites(self) -> dict:
        result: dict[str, int] = {}
        for line in self.site_edit.toPlainText().splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            domain, _, value = line.partition("=")
            domain = domain.strip().lower().lstrip(".")
            try:
                n = max(1, min(32, int(value.strip())))
            except ValueError:
                continue
            if domain:
                result[domain] = n
        return result

    @staticmethod
    def _time_text(t: QTime) -> str:
        text = t.toString("HH:mm")
        return "" if text == "00:00" else text

    def apply(self) -> bool:
        """写回设置并保存，返回桥接是否需要重启。"""
        s = self.settings
        old_bridge = (s.bridge_enabled, s.bridge_port)
        s.save_dir = self.dir_edit.text().strip() or s.save_dir
        s.connections = self.conn_spin.value()
        s.max_concurrent = self.concurrent_spin.value()
        s.speed_limit = self.speed_spin.value() * 1024
        s.stall_timeout = self.stall_spin.value()
        s.proxy = self.proxy_edit.text().strip()
        s.proxy_auth = self.proxy_auth_combo.currentData() or ""
        s.proxy_username = self.proxy_user_edit.text().strip()
        s.proxy_password = self.proxy_pwd_edit.text()
        s.proxy_domain = self.proxy_domain_edit.text().strip()
        s.pac_url = self.pac_edit.text().strip()
        s.site_connections = self._parse_sites()
        s.ffmpeg_path = self.ffmpeg_edit.text().strip()
        s.preallocate = self.prealloc_cb.isChecked()
        s.av_scan = self.av_cb.isChecked()
        s.prefer_http2 = self.h2_cb.isChecked()
        s.verify_ssl = not self.insecure_tls_cb.isChecked()
        s.update_url = self.update_url_edit.text().strip()
        s.check_update_on_start = self.autoupdate_cb.isChecked()
        s.auto_categorize = self.categorize_cb.isChecked()
        s.notify_on_complete = self.notify_cb.isChecked()
        s.play_sound = self.sound_cb.isChecked()
        s.scheduler_enabled = self.sched_cb.isChecked()
        s.scheduler_start = self._time_text(self.start_time.time())
        s.scheduler_stop = self._time_text(self.stop_time.time())
        s.clipboard_watch = self.clipboard_cb.isChecked()
        s.minimize_to_tray = self.tray_cb.isChecked()
        s.auto_resume = self.autoresume_cb.isChecked()
        s.bridge_enabled = self.bridge_cb.isChecked()
        s.bridge_port = self.port_spin.value()
        s.takeover_prompt = self.takeover_cb.isChecked()
        s.after_done_action = self.after_done_combo.currentData() or "none"
        # 开机自启：仅打包版写注册表 Run 键；源码形态顺手清理历史残留
        try:
            from core import autostart
            if getattr(self, "_autostart_supported", False):
                autostart.set_enabled(self.autostart_cb.isChecked())
            else:
                autostart.cleanup_dev_residue()
        except Exception:  # noqa: BLE001
            pass
        s.save()
        return old_bridge != (s.bridge_enabled, s.bridge_port)
