# -*- coding: utf-8 -*-
"""主窗口：分类栏、任务列表、工具栏、菜单与状态栏（IDM 风格浅色界面）。"""
from __future__ import annotations

import datetime
import os

from PyQt5.QtCore import QPoint, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QHeaderView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from core.branding import (
    APP_NAME,
    APP_NAME_EN,
    APP_VERSION,
    WINDOW_TITLE,
)
from core.errors import DownloadError
from core.task import TaskState

from . import icons
from .add_dialog import AddDownloadDialog
from .batch_dialog import BatchAddDialog
from .grab_dialog import GrabSiteDialog
from .icons import icon_dot
from .models import COLUMNS, ProgressDelegate, TaskTableModel
from .settings_dialog import SettingsDialog
from .speedchart import SpeedChart
from .update_dialog import check_for_updates
from .utils import CATEGORIES, CATEGORY_COLORS, format_speed, match_category


class MainWindow(QMainWindow):
    settingsChanged = pyqtSignal()

    def __init__(self, manager, settings, bridge, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.settings = settings
        self.bridge = bridge

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1060, 600)
        self.setWindowIcon(icons.app_icon())
        self._selected_tid = ""
        # “全部下载完成后动作”状态：曾有活动任务 / 本轮已触发（防重复弹窗）
        self._had_active = False
        self._after_done_fired = False

        self._build_central()
        self._build_toolbar()
        self._build_menu()
        self.statusBar().showMessage("就绪")

        # 定时刷新 + 引擎事件即时刷新
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        bridge.added.connect(lambda *_: self.refresh())
        bridge.stateChanged.connect(lambda *_: self.refresh())
        bridge.stateChanged.connect(self._on_state_changed)
        bridge.removed.connect(lambda *_: self.refresh())
        bridge.event.connect(self._on_event)
        if hasattr(bridge, "inbound"):
            bridge.inbound.connect(self._on_inbound)
        self.refresh()

        # 完成通知去重 + 计划任务
        self._notified: set[tuple] = set()
        self._sched_fired: set[tuple] = set()
        self.sched_timer = QTimer(self)
        self.sched_timer.timeout.connect(self._check_schedule)
        self.sched_timer.start(30 * 1000)

        # 启动后静默检查更新（仅在配置了更新源且开启开关时；有新版本才提示）
        if (getattr(self.settings, "check_update_on_start", True)
                and getattr(self.settings, "update_url", "")):
            QTimer.singleShot(
                4000,
                lambda: check_for_updates(self.settings, self, silent=True))

    # ---------- 界面构建 ----------
    def _build_central(self):
        self.model = TaskTableModel(self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setIconSize(QSize(22, 22))
        self.table.setItemDelegateForColumn(2, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setVerticalScrollMode(QTableView.ScrollPerPixel)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setHighlightSections(False)
        header = self.table.horizontalHeader()
        # 列宽可自由拖动、列标题可拖动重排（IDM 式）
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)
        header.setMinimumSectionSize(60)
        # 文件名 / 大小 / 进度 / 已下载 / 速度 / 剩余时间：可交互调宽
        widths = {0: 280, 1: 84, 2: 132, 3: 92, 4: 84, 5: 80}
        saved_widths = getattr(self.settings, "column_widths", {}) or {}
        for col, w in widths.items():
            header.setSectionResizeMode(col, QHeaderView.Interactive)
            sw = None
            try:
                sw = int(saved_widths.get(COLUMNS[col]))
            except (TypeError, ValueError):
                sw = None
            self.table.setColumnWidth(col, sw if isinstance(sw, int) and sw >= 40 else w)
        # 最后“状态”列弹性占满右侧空白
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        self._default_widths = dict(widths)
        # 拖动列宽后记忆（仅更新内存，退出时随 settings.save 统一落盘）
        header.sectionResized.connect(self._on_section_resized)
        # 右键表头可勾选显示/隐藏列
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        self._apply_hidden_columns()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(self._double_clicked)
        self.table.selectionModel().selectionChanged.connect(
            self._on_selection_changed)

        self.category_list = QListWidget()
        self.category_list.setObjectName("CategoryList")
        self.category_list.setFrameShape(QListWidget.NoFrame)
        self.category_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for cat in CATEGORIES:
            item = QListWidgetItem(icon_dot(CATEGORY_COLORS[cat], 16), cat)
            self.category_list.addItem(item)
        self.category_list.setCurrentRow(0)
        self.category_list.currentRowChanged.connect(self._on_category_changed)

        table_wrap = QWidget()
        lay = QVBoxLayout(table_wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.category_list)
        splitter.addWidget(table_wrap)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([176, 884])
        splitter.setHandleWidth(4)
        splitter.setChildrenCollapsible(False)

        self.speed_chart = SpeedChart(self)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(10, 10, 10, 6)
        cl.setSpacing(8)
        cl.addWidget(splitter)
        cl.addWidget(self.speed_chart)
        self.setCentralWidget(central)

    def _build_toolbar(self):
        tb = QToolBar("主工具栏", self)
        tb.setIconSize(QSize(28, 28))
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)

        self.act_new = QAction(icons.icon_new(), "新建下载", self)
        self.act_resume = QAction(icons.icon_play(), "继续", self)
        self.act_pause = QAction(icons.icon_pause(), "暂停", self)
        self.act_restart = QAction(icons.icon_restart(), "重新下载", self)
        self.act_delete = QAction(icons.icon_delete(), "删除", self)
        self.act_settings = QAction(icons.icon_settings(), "设置", self)

        self.act_new.triggered.connect(self.add_url)
        self.act_resume.triggered.connect(self.resume_selected)
        self.act_pause.triggered.connect(self.pause_selected)
        self.act_restart.triggered.connect(self.restart_selected)
        self.act_delete.triggered.connect(self.remove_selected)
        self.act_settings.triggered.connect(self.open_settings)

        for act in (self.act_new,):
            tb.addAction(act)
        tb.addSeparator()
        for act in (self.act_resume, self.act_pause, self.act_restart, self.act_delete):
            tb.addAction(act)

        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().Expanding,
                             spacer.sizePolicy().Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.act_settings)

    def _build_menu(self):
        mb = self.menuBar()
        m_task = mb.addMenu("任务(&T)")
        m_task.addAction(self.act_new)
        m_task.addAction("批量下载…", self.add_batch)
        m_task.addAction("下载网页资源…", self.add_grab)
        m_task.addSeparator()
        m_task.addAction(self.act_resume)
        m_task.addAction(self.act_pause)
        m_task.addAction(self.act_restart)
        m_task.addSeparator()
        m_task.addAction("删除任务（保留文件）", lambda: self.remove_selected(False), "Del")
        m_task.addAction("删除任务及文件", lambda: self.remove_selected(True))
        m_task.addSeparator()
        m_task.addAction("打开保存目录", self.open_folder)
        m_task.addSeparator()
        m_task.addAction("退出", QApplication.quit)

        m_dl = mb.addMenu("下载(&D)")
        m_dl.addAction("全部继续", self.manager.resume_all)
        m_dl.addAction("全部暂停", self.manager.pause_all)
        m_dl.addSeparator()
        m_dl.addAction("清除全部已完成任务", self.clear_completed)

        m_tools = mb.addMenu("工具(&O)")
        m_tools.addAction(self.act_settings)

        m_help = mb.addMenu("帮助(&H)")
        m_help.addAction("检查更新…", self.check_updates)
        m_help.addSeparator()
        m_help.addAction(f"关于 {APP_NAME}", self.about)

    def check_updates(self):
        check_for_updates(self.settings, self, silent=False)

    # ---------- 刷新 ----------
    def refresh(self):
        snaps = self.manager.snapshots()
        self.model.update_rows(snaps)
        self._update_category_counts(snaps)
        active = [s for s in snaps if s["state"] == TaskState.DOWNLOADING]
        total_speed = sum(s["speed"] for s in active)
        done = sum(1 for s in snaps if s["state"] == TaskState.COMPLETED)
        self.statusBar().showMessage(
            f"任务 {len(snaps)} 个 | 下载中 {len(active)} 个 | "
            f"已完成 {done} 个 | 总速度 {format_speed(total_speed)}"
        )
        self.speed_chart.push(total_speed)
        self._restore_selection()
        self._update_action_states()
        self._check_after_done(snaps)

    def _update_category_counts(self, snaps):
        for row, cat in enumerate(CATEGORIES):
            count = sum(1 for s in snaps if match_category(cat, s))
            item = self.category_list.item(row)
            item.setText(f"{cat}    {count}")

    def _update_action_states(self):
        snap = self._selected_snapshot()
        state = snap.get("state", "")
        has = bool(snap)
        running = state in (TaskState.QUEUED, TaskState.CONNECTING, TaskState.DOWNLOADING)
        self.act_resume.setEnabled(has and state in (TaskState.PAUSED, TaskState.ERROR))
        self.act_pause.setEnabled(has and running)
        self.act_restart.setEnabled(has)
        self.act_delete.setEnabled(has)

    def _on_category_changed(self, row):
        if 0 <= row < len(CATEGORIES):
            self.model.set_category(CATEGORIES[row])

    def _selected_id(self) -> str:
        idx = self.table.currentIndex()
        return self.model.task_id_at(idx.row()) if idx.isValid() else ""

    def _selected_snapshot(self) -> dict:
        idx = self.table.currentIndex()
        return self.model.snapshot_at(idx.row()) if idx.isValid() else {}

    def _on_selection_changed(self, *args):
        tid = self._selected_id()
        if tid:
            self._selected_tid = tid

    def _restore_selection(self):
        """每秒刷新/模型 reset 后，按 task_id 恢复用户选中行。"""
        if not self._selected_tid:
            return
        if self._selected_id() == self._selected_tid:
            return
        for row in range(self.model.rowCount()):
            if self.model.task_id_at(row) == self._selected_tid:
                idx = self.model.index(row, 0)
                self.table.setCurrentIndex(idx)
                self.table.selectRow(row)
                return

    # ---------- 任务操作 ----------
    def add_url(self, initial_url: str = ""):
        try:
            dlg = AddDownloadDialog(self.settings, initial_url, self)
            result = dlg.exec_()
        except Exception as exc:  # noqa: BLE001  对话框/探测异常绝不能拖垮主程序
            QMessageBox.warning(self, "无法打开新建下载", str(exc))
            return
        if result not in (AddDownloadDialog.Accepted, AddDownloadDialog.LATER):
            return
        paused = result == AddDownloadDialog.LATER
        try:
            self.manager.add(dlg.url(), dlg.task_options(), start_paused=paused)
        except DownloadError as exc:
            QMessageBox.warning(self, "无法添加下载", str(exc))
            return
        self.refresh()

    def _on_inbound(self, box: dict):
        """浏览器扩展接管：在界面线程弹出“新建下载”确认窗（HTTP 线程在等待）。"""
        payload = box.get("payload", {}) or {}
        url = (payload.get("url") or "").strip()
        try:
            if not url:
                return
            # 把主窗口与确认窗提到前台（程序可能最小化到托盘）
            self.showNormal()
            self.raise_()
            self.activateWindow()
            inbound = {
                "filename": payload.get("filename", ""),
                "referer": payload.get("referer", ""),
                "cookies": payload.get("cookies", ""),
            }
            dlg = AddDownloadDialog(self.settings, url, self, inbound=inbound)
            result = dlg.exec_()
            if result in (AddDownloadDialog.Accepted, AddDownloadDialog.LATER):
                try:
                    task = self.manager.add(
                        dlg.url(), dlg.task_options(),
                        start_paused=(result == AddDownloadDialog.LATER))
                    box["task_id"] = task.task_id
                except DownloadError as exc:
                    QMessageBox.warning(self, "无法添加下载", str(exc))
                    box["task_id"] = None
                except Exception as exc:  # noqa: BLE001  兜底，避免桥接槽异常
                    QMessageBox.warning(self, "无法打开下载确认窗", str(exc))
                    box["task_id"] = None
        finally:
            # 无论确认/取消/异常都必须放行等待中的桥接 HTTP 线程
            box["event"].set()
        self.refresh()

    def add_grab(self, initial_url: str = ""):
        dlg = GrabSiteDialog(self.settings, self.manager, initial_url, self)
        dlg.exec_()
        self.refresh()

    def add_batch(self):
        dlg = BatchAddDialog(self.settings, self)
        code = dlg.exec_()
        if code not in (BatchAddDialog.Accepted, BatchAddDialog.LATER):
            return
        paused = code == BatchAddDialog.LATER
        opt = dlg.task_options()
        existing = {t.url for t in self.manager.tasks.values()}
        n = 0
        failed = 0
        last_err = ""
        for u in dlg.urls():
            if u in existing:
                continue
            try:
                self.manager.add(u, opt, start_paused=paused)
                existing.add(u)
                n += 1
            except DownloadError as exc:
                failed += 1
                last_err = str(exc)
        self.refresh()
        if failed:
            QMessageBox.warning(
                self, "部分链接无法添加",
                f"成功 {n} 个，失败 {failed} 个。\n{last_err}")
        else:
            self.statusBar().showMessage(f"已添加 {n} 个批量下载任务", 5000)

    def _on_state_changed(self, tid, state, msg):
        if not getattr(self.settings, "notify_on_complete", True):
            return
        if state not in (TaskState.COMPLETED, TaskState.ERROR):
            return
        key = (tid, state)
        if key in self._notified:
            return
        self._notified.add(key)
        snap = next((s for s in self.manager.snapshots() if s["task_id"] == tid), {})
        name = os.path.basename(snap.get("final_path") or snap.get("filename") or "文件")
        if state == TaskState.COMPLETED:
            title, body, icon = "下载完成", name, QSystemTrayIcon.Information
        else:
            title = "下载失败"
            body = f"{name}\n{snap.get('error') or msg or ''}"
            icon = QSystemTrayIcon.Warning
        tray = getattr(self, "tray", None)
        if tray is not None and tray.tray.supportsMessages():
            tray.tray.showMessage(title, body, icon, 6000)
        else:
            self.statusBar().showMessage(f"{title}: {name}", 8000)
        if getattr(self.settings, "play_sound", False):
            QApplication.beep()

    def _check_schedule(self):
        s = self.settings
        if not getattr(s, "scheduler_enabled", False):
            self._sched_fired.clear()
            return
        day = datetime.date.today().toordinal()
        now = datetime.datetime.now().strftime("%H:%M")
        start = getattr(s, "scheduler_start", "")
        stop = getattr(s, "scheduler_stop", "")
        if start and now == start and (day, "start") not in self._sched_fired:
            self._sched_fired.add((day, "start"))
            self.manager.resume_all()
            self.statusBar().showMessage(f"计划任务：{start} 已全部开始", 8000)
        if stop and now == stop and (day, "stop") not in self._sched_fired:
            self._sched_fired.add((day, "stop"))
            self.manager.pause_all()
            self.statusBar().showMessage(f"计划任务：{stop} 已全部暂停", 8000)

    # ---------- 全部完成后动作 ----------
    _ACTIVE_STATES = (TaskState.QUEUED, TaskState.CONNECTING, TaskState.DOWNLOADING)

    def clear_completed(self):
        """一键清除所有已完成任务（保留文件，对应 IDM Delete Completed）。"""
        try:
            n = self.manager.remove_completed(False)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "清除失败", str(exc))
            return
        self.statusBar().showMessage(
            f"已清除 {n} 个已完成任务" if n else "没有已完成的任务可清除", 4000)
        self.refresh()

    def _check_after_done(self, snaps: list[dict]):
        """所有任务都下载完成时，按设置执行关机/休眠/睡眠/退出（仅触发一次）。"""
        action = getattr(self.settings, "after_done_action", "none")
        if not snaps:
            self._had_active = False
            self._after_done_fired = False
            return
        if any(s["state"] in self._ACTIVE_STATES for s in snaps):
            # 出现活动任务：标记“曾活动”，并为新一轮完成复位触发标志
            self._had_active = True
            self._after_done_fired = False
            return
        if self._after_done_fired or action == "none" or not self._had_active:
            return
        # 存在暂停/失败等未完成任务时不触发，等待用户处理或继续
        if not all(s["state"] == TaskState.COMPLETED for s in snaps):
            return
        self._after_done_fired = True
        self._run_after_done(action)

    def _run_after_done(self, action: str):
        from core import power
        label = power.ACTION_LABELS.get(action, action)
        wait = 60
        box = QMessageBox(self)
        box.setWindowTitle("全部下载已完成")
        box.setIcon(QMessageBox.Information)
        box.setText(f"全部下载任务已完成，将在 {wait} 秒后{label}。\n"
                    "点击“取消”可阻止该操作。")
        btn_now = box.addButton("立即执行", QMessageBox.AcceptRole)
        btn_cancel = box.addButton("取消", QMessageBox.RejectRole)
        remain = [wait]
        timer = QTimer(box)

        def tick():
            remain[0] -= 1
            if remain[0] <= 0:
                timer.stop()
                btn_now.click()  # 倒计时结束，按“立即执行”处理
            else:
                btn_now.setText(f"立即执行（{remain[0]} 秒）")

        timer.timeout.connect(tick)
        timer.start(1000)
        box.exec_()
        timer.stop()
        if box.clickedButton() is btn_cancel:
            self.statusBar().showMessage("已取消完成后动作", 5000)
            return
        try:
            if action == "exit":
                QApplication.quit()
            elif action == "shutdown":
                power.schedule_shutdown(30)  # 系统级 30 秒缓冲，仍可用 shutdown /a 取消
            elif action in ("sleep", "hibernate"):
                power.perform(action)
        except Exception as exc:  # noqa: BLE001  电源动作失败不应崩溃
            QMessageBox.warning(self, "操作失败", f"无法执行“{label}”：\n{exc}")

    def pause_selected(self):
        tid = self._selected_id()
        if tid:
            self.manager.pause(tid)

    def resume_selected(self):
        tid = self._selected_id()
        if tid:
            self.manager.resume(tid)

    def restart_selected(self):
        tid = self._selected_id()
        if tid:
            self.manager.restart(tid)

    def remove_selected(self, delete_files: bool | None = None):
        tid = self._selected_id()
        if not tid:
            return
        if delete_files is None:
            box = QMessageBox(self)
            box.setWindowTitle("删除任务")
            box.setText("要如何删除该任务？")
            btn_task = box.addButton("仅删除任务", QMessageBox.AcceptRole)
            btn_both = box.addButton("删除任务和文件", QMessageBox.DestructiveRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            clicked = box.clickedButton()
            if clicked == btn_task:
                delete_files = False
            elif clicked == btn_both:
                delete_files = True
            else:
                return
        self.manager.remove(tid, delete_files)

    def open_folder(self):
        snap = self._selected_snapshot()
        path = snap.get("save_dir", "")
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_settings(self):
        bridge_server = getattr(self, "bridge_server", None)
        if bridge_server is not None:
            token = bridge_server.token
        else:
            try:
                from core.bridge import get_bridge_token
                token = get_bridge_token()
            except OSError:
                token = ""
        dlg = SettingsDialog(self.settings, token, self)
        if dlg.exec_() == SettingsDialog.Accepted:
            bridge_changed = dlg.apply()
            self.manager.apply_speed_limit()
            self.settingsChanged.emit()
            if bridge_changed:
                self.statusBar().showMessage("桥接设置将在重启程序后完全生效", 5000)

    def about(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"关于 {APP_NAME}")
        box.setIconPixmap(icons.app_icon().pixmap(72, 72))
        box.setStandardButtons(QMessageBox.Ok)
        box.setText(
            f"<h3 style='margin-bottom:2px'>{APP_NAME}"
            f"<span style='font-weight:400;color:#5B6B7F;font-size:10pt;'>"
            f"　{APP_NAME_EN}　v{APP_VERSION}</span></h3>"
            "<p>自研多线程下载管理器（Python + PyQt5，GPL-3.0 开源）。</p>"
            "<p>动态分段多连接、断点续传、HTTP/HTTPS/FTP/FTPS、HTTP/2、"
            "m3u8/HLS 视频、网页资源抓取、BT/磁力、MD5/SHA 校验、"
            "真实占盘预分配、完成后杀毒、队列调度、限速、代理、"
            "剪贴板监听与浏览器扩展接管。</p>"
            "<p style='color:#8A94A6;'>BT/磁力基于 libtorrent（发行版已内置）；"
            "本程序不提供 DRM 加密媒体的解密。</p>"
        )
        box.exec_()

    # ---------- 表格交互 ----------
    def _double_clicked(self, index):
        snap = self.model.snapshot_at(index.row())
        tid = snap.get("task_id", "")
        state = snap.get("state", "")
        if state in (TaskState.DOWNLOADING, TaskState.CONNECTING, TaskState.QUEUED):
            self.manager.pause(tid)
        elif state in (TaskState.PAUSED, TaskState.ERROR):
            self.manager.resume(tid)
        elif state == TaskState.COMPLETED and snap.get("final_path"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(snap["final_path"]))

    # ---------- 表头列显示/隐藏 ----------
    def _on_section_resized(self, col: int, _old: int, new: int):
        # 状态列（最后一列）为弹性列，不记录；其余列拖动后记忆宽度
        if col == len(COLUMNS) - 1:
            return
        try:
            self.settings.column_widths[COLUMNS[col]] = int(new)
        except Exception:  # noqa: BLE001
            pass

    def _apply_hidden_columns(self):
        hidden = set(getattr(self.settings, "hidden_columns", []) or [])
        for col, name in enumerate(COLUMNS):
            # 文件名列（第 0 列）始终显示，保证可辨识
            self.table.setColumnHidden(col, col != 0 and name in hidden)

    def _persist_hidden_columns(self):
        hidden = [COLUMNS[c] for c in range(len(COLUMNS))
                  if self.table.isColumnHidden(c)]
        self.settings.hidden_columns = hidden
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001
            pass

    def _header_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setWindowTitle("显示/隐藏列")
        title = menu.addAction("显示/隐藏列")
        title.setEnabled(False)
        menu.addSeparator()
        for col, name in enumerate(COLUMNS):
            act = QAction(name, menu, checkable=True)
            act.setChecked(not self.table.isColumnHidden(col))
            if col == 0:
                act.setEnabled(False)  # 文件名列不可隐藏
            act.toggled.connect(
                lambda checked, c=col: self._toggle_column(c, checked))
            menu.addAction(act)
        menu.addSeparator()
        menu.addAction("重置列", self._reset_columns)
        header = self.table.horizontalHeader()
        menu.exec_(header.viewport().mapToGlobal(pos))

    def _toggle_column(self, col: int, shown: bool):
        if col == 0:
            return  # 文件名列始终显示
        self.table.setColumnHidden(col, not shown)
        self._persist_hidden_columns()

    def _reset_columns(self):
        for col in range(len(COLUMNS)):
            self.table.setColumnHidden(col, False)
        for col, w in self._default_widths.items():
            self.table.setColumnWidth(col, w)
        self.settings.hidden_columns = []
        self.settings.column_widths = {}
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001
            pass

    def _context_menu(self, pos: QPoint):
        snap = self._selected_snapshot()
        if not self._selected_id():
            return
        menu = QMenu(self)
        menu.addAction("继续", self.resume_selected)
        menu.addAction("暂停", self.pause_selected)
        menu.addAction("重新下载", self.restart_selected)
        menu.addSeparator()
        menu.addAction("删除任务（保留文件）", lambda: self.remove_selected(False))
        menu.addAction("删除任务及文件", lambda: self.remove_selected(True))
        menu.addSeparator()
        menu.addAction("打开保存目录", self.open_folder)
        if snap.get("error"):
            menu.addSeparator()
            menu.addAction("查看错误详情", self._show_error_detail)
        digest = snap.get("checksum_actual")
        if digest:
            algo = (snap.get("checksum_algo") or "sha256").upper()
            menu.addAction(f"复制 {algo} 校验值", self._copy_checksum)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def _show_error_detail(self):
        snap = self._selected_snapshot()
        err = snap.get("error") or "（无详细错误信息）"
        name = snap.get("filename") or ""
        box = QMessageBox(self)
        box.setWindowTitle("下载失败原因")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"{name}\n\n{err}")
        box.setStandardButtons(QMessageBox.Ok)
        box.exec_()

    def _copy_checksum(self):
        snap = self._selected_snapshot()
        digest = snap.get("checksum_actual")
        if digest:
            QApplication.clipboard().setText(digest)
            self.statusBar().showMessage("校验值已复制到剪贴板", 4000)

    def _on_event(self, task_id: str, level: str, message: str):
        self.statusBar().showMessage(message, 6000)

    # ---------- 关闭行为 ----------
    def closeEvent(self, event):
        if self.settings.minimize_to_tray:
            event.ignore()
            self.hide()
        else:
            event.accept()
            QApplication.quit()
