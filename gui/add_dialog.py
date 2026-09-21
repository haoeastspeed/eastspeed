# -*- coding: utf-8 -*-
"""“新建下载”对话框（IDM 风格：开始下载 / 稍后下载 / 取消）。"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from core.probe import meaningful_filename
from core.task_options import TaskOptions

# 保活后台探测线程，避免对话框关闭时 QThread 仍在运行被回收
_PROBE_WORKERS: set["_ProbeWorker"] = set()


def human_size(n) -> str:
    if not n:
        return "未知"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


class _ProbeWorker(QThread):
    """后台探测文件信息（文件名 / 大小 / 是否支持分段），绝不阻塞界面。"""

    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(self, seq: int, url: str, cfg, parent=None):
        super().__init__(parent)
        self.seq = seq
        self.url = url
        self.cfg = cfg
        _PROBE_WORKERS.add(self)
        self.finished.connect(lambda: _PROBE_WORKERS.discard(self))
        self.finished.connect(self.deleteLater)

    def run(self) -> None:
        try:
            from core.probe import probe
            info = probe(self.url, self.cfg)
            self.succeeded.emit(self.seq, info)
        except Exception as exc:  # noqa: BLE001  探测失败不影响下载
            self.failed.emit(self.seq, str(exc))


class AddDownloadDialog(QDialog):
    LATER = 2  # 自定义返回码：稍后下载（以暂停状态加入）

    def __init__(self, settings, initial_url: str = "", parent=None,
                 inbound: dict | None = None):
        super().__init__(parent)
        self.settings = settings
        # 浏览器扩展接管时传入：filename / referer / cookies
        self._inbound = inbound or {}
        # .torrent 多文件勾选结果（相对路径列表）
        self._bt_files: list[str] | None = None
        # 后台探测（文件名/大小/断点支持）
        self._probe_seq = 0
        self._worker = None
        # 浏览器接管已带“像样”文件名时，视为用户已定名，不再用探测结果覆盖；
        # 若带来的是 302 临时地址随机串（无扩展名 UUID），不锁定，待探测纠正
        _inbound_name = str(self._inbound.get("filename") or "").strip()
        self._name_touched = bool(_inbound_name) and meaningful_filename(_inbound_name)
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._probe)
        self.setWindowTitle("新建下载" if not self._inbound else "浏览器下载接管")
        self.setMinimumWidth(580)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setVerticalSpacing(10)

        self.url_edit = QLineEdit(initial_url)
        self.url_edit.setPlaceholderText("https://example.com/file.zip 或 .m3u8")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.textChanged.connect(lambda *_: self._schedule_probe())
        form.addRow("下载链接:", self.url_edit)

        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(settings.save_dir)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(self.dir_edit)
        dir_row.addWidget(browse)
        form.addRow("保存到:", dir_row)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("留空则自动使用服务器文件名")
        # textEdited 仅在用户手动键入时触发，程序化 setText 不触发
        self.name_edit.textEdited.connect(lambda *_: setattr(self, "_name_touched", True))
        form.addRow("文件名:", self.name_edit)

        # 后台探测到的文件信息（大小 / 是否支持多线程断点）
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color:#6b7280;")
        self.info_label.setWordWrap(True)
        form.addRow("", self.info_label)

        self.conn_spin = QSpinBox()
        self.conn_spin.setRange(1, 32)
        self.conn_spin.setValue(settings.connections)
        self.conn_spin.setSuffix(" 个连接")
        form.addRow("连接数:", self.conn_spin)

        layout.addLayout(form)

        adv = QGroupBox("高级选项（通常无需填写）")
        adv.setCheckable(True)
        adv.setChecked(False)
        adv_form = QFormLayout()
        self.user_edit = QLineEdit()
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.referer_edit = QLineEdit()
        self.referer_edit.setPlaceholderText("https://example.com/")
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setPlaceholderText("name=value; name2=value2")
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890（留空用全局设置）")
        adv_form.addRow("用户名:", self.user_edit)
        adv_form.addRow("密码:", self.pwd_edit)
        adv_form.addRow("Referer:", self.referer_edit)
        adv_form.addRow("Cookie:", self.cookie_edit)
        adv_form.addRow("代理:", self.proxy_edit)

        self.sum_combo = QComboBox()
        self.sum_combo.addItems(["不校验", "MD5", "SHA-1", "SHA-256", "SHA-512"])
        adv_form.addRow("校验算法:", self.sum_combo)
        self.sum_edit = QLineEdit()
        self.sum_edit.setPlaceholderText("可选：粘贴官方提供的哈希值，完成后自动比对")
        adv_form.addRow("期望哈希:", self.sum_edit)

        self.mirror_edit = QPlainTextEdit()
        self.mirror_edit.setPlaceholderText(
            "可选：每行填一个镜像 URL（须为同一文件且支持断点续传），\n"
            "程序会在多个下载源之间聚合加速，某个源故障时自动切换")
        self.mirror_edit.setFixedHeight(64)
        adv_form.addRow("镜像源:", self.mirror_edit)

        adv.setLayout(adv_form)
        layout.addWidget(adv)

        # 浏览器接管：预填文件名与来源信息（Referer/Cookie 有助于通过防盗链）
        if self._inbound:
            _inbound_name = str(self._inbound.get("filename") or "").strip()
            # 随机资源 ID 不预填，等后台探测到 Content-Disposition 真名后自动补入
            if _inbound_name and meaningful_filename(_inbound_name):
                self.name_edit.setText(_inbound_name)
            if self._inbound.get("referer"):
                self.referer_edit.setText(str(self._inbound["referer"]).strip())
            if self._inbound.get("cookies"):
                self.cookie_edit.setText(str(self._inbound["cookies"]).strip())
            if self._inbound.get("referer") or self._inbound.get("cookies"):
                adv.setChecked(True)

        hint = QLabel("提示: 服务器若对单连接限速，多连接可明显提速；若按 IP 限速则无效。"
                      ".m3u8 自动按 HLS 视频下载；本地多文件 .torrent 添加时可勾选文件，"
                      "magnet 磁力链接需先取得元数据，默认下载全部文件。")
        hint.setStyleSheet("color: #6b7280;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_later = QPushButton("稍后下载")
        self.btn_start = QPushButton("开始下载")
        self.btn_start.setProperty("primary", True)
        self.btn_start.setDefault(True)
        self.btn_cancel = QPushButton("取消")
        self.btn_later.clicked.connect(self._validate_later)
        self.btn_start.clicked.connect(self._validate_accept)
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_later)
        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_cancel)
        layout.addLayout(btn_row)

        # 已有链接（浏览器接管 / 剪贴板 / 带入 URL）时，打开即后台探测
        if self._probe_url():
            self._schedule_probe()

    # ---------- 后台探测文件信息 ----------
    def _probe_url(self) -> str:
        url = self.url_edit.text().strip()
        low = url.lower()
        if low.startswith(("http://", "https://")):
            return url
        return ""

    def _schedule_probe(self) -> None:
        # URL 去抖：连续粘贴/输入只在停顿后探测一次
        self._probe_timer.start(600)

    def _make_cfg(self):
        opt = TaskOptions()
        if self.referer_edit.text().strip():
            opt.referer = self.referer_edit.text().strip()
        if self.cookie_edit.text().strip():
            opt.cookies = self.cookie_edit.text().strip()
        if self.proxy_edit.text().strip():
            opt.proxy = self.proxy_edit.text().strip()
        if self.user_edit.text().strip():
            opt.username = self.user_edit.text().strip()
            opt.password = self.pwd_edit.text()
        from core.task import _TaskConfigView
        return _TaskConfigView(self.settings, opt)

    def _probe(self) -> None:
        url = self._probe_url()
        if not url:
            self.info_label.setText("")
            return
        self.info_label.setStyleSheet("color:#6b7280;")
        self.info_label.setText("正在获取文件信息…")
        self._probe_seq += 1
        seq = self._probe_seq
        try:
            cfg = self._make_cfg()
        except Exception:  # noqa: BLE001
            cfg = self.settings
        worker = _ProbeWorker(seq, url, cfg)
        worker.succeeded.connect(self._on_probe_ok)
        worker.failed.connect(self._on_probe_failed)
        self._worker = worker
        worker.start()

    def _on_probe_ok(self, seq: int, info) -> None:
        if seq != self._probe_seq:
            return  # 已被更新的请求取代或对话框已关闭
        size = human_size(info.total_size)
        if info.resumable:
            tail = "支持多线程与断点续传"
        else:
            tail = "服务器不支持分段，将以单线程下载"
        self.info_label.setStyleSheet("color:#2563eb;")
        self.info_label.setText(f"大小：{size}　·　{tail}")
        # 用户未手动定名且接管未带名时，自动同步服务器文件名
        if not self._name_touched and info.filename:
            self.name_edit.setText(info.filename)

    def _on_probe_failed(self, seq: int, message: str) -> None:
        if seq != self._probe_seq:
            return
        self.info_label.setStyleSheet("color:#b45309;")
        self.info_label.setText("未能自动获取文件信息（不影响下载，开始后会再次识别）")

    def done(self, code: int) -> None:
        # 关闭即作废在途探测，残留线程结束时回调因 seq 不匹配被忽略
        self._probe_seq += 1
        self._probe_timer.stop()
        super().done(code)

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _valid_url(self) -> bool:
        url = self.url_edit.text().strip()
        low = url.lower()
        if low.startswith(("http://", "https://", "ftp://", "ftps://",
                           "magnet:")):
            return True
        # 本地 .torrent 文件路径
        if low.endswith(".torrent") and os.path.exists(url):
            return True
        self.url_edit.setFocus()
        return False

    def _maybe_pick_torrent_files(self) -> bool:
        """本地多文件 .torrent：弹出文件勾选；返回 False 表示用户取消。"""
        self._bt_files = None
        url = self.url_edit.text().strip()
        if not (url.lower().endswith(".torrent") and os.path.exists(url)):
            return True
        try:
            import libtorrent as lt
        except ImportError:
            return True
        try:
            ti = lt.torrent_info(url)
        except Exception:
            return True  # 解析失败交给引擎报错，不阻塞添加
        if ti.files().num_files() <= 1:
            return True
        from gui.torrent_files_dialog import TorrentFilesDialog
        dlg = TorrentFilesDialog(url, self)
        if dlg.exec_() != QDialog.Accepted:
            return False
        self._bt_files = dlg.selected_files()
        return True

    def _validate_accept(self):
        if self._valid_url() and self._maybe_pick_torrent_files():
            self.accept()

    def _validate_later(self):
        if self._valid_url() and self._maybe_pick_torrent_files():
            self.done(self.LATER)

    def url(self) -> str:
        return self.url_edit.text().strip()

    def task_options(self) -> TaskOptions:
        opt = TaskOptions(
            save_dir=self.dir_edit.text().strip() or None,
            filename=self.name_edit.text().strip() or None,
            connections=self.conn_spin.value(),
        )
        if self.user_edit.text().strip():
            opt.username = self.user_edit.text().strip()
            opt.password = self.pwd_edit.text()
        if self.referer_edit.text().strip():
            opt.referer = self.referer_edit.text().strip()
        if self.cookie_edit.text().strip():
            opt.cookies = self.cookie_edit.text().strip()
        if self.proxy_edit.text().strip():
            opt.proxy = self.proxy_edit.text().strip()
        algo_map = {"MD5": "md5", "SHA-1": "sha1",
                    "SHA-256": "sha256", "SHA-512": "sha512"}
        algo = algo_map.get(self.sum_combo.currentText())
        if algo:
            opt.checksum_algo = algo
            opt.checksum_expected = self.sum_edit.text().strip() or None
        mirrors = [
            ln.strip() for ln in self.mirror_edit.toPlainText().splitlines()
            if ln.strip().lower().startswith(("http://", "https://"))
            and ln.strip() != self.url()
        ]
        if mirrors:
            opt.mirrors = mirrors
        if getattr(self, "_bt_files", None):
            opt.bt_files = self._bt_files
        return opt
