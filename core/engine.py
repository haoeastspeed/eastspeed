# -*- coding: utf-8 -*-
"""下载管理器：任务队列、并发调度、索引持久化与重启恢复。"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections import OrderedDict

from .config import app_data_dir
from .errors import DownloadError
from .ftp_task import FtpTask
from .hls import HlsTask, looks_like_hls
from .probe import FileInfo
from .task import META_SUFFIX, TMP_SUFFIX, DownloadTask, TaskState
from .task_options import TaskOptions  # noqa: F401  (re-export convenience)
from .torrent import (MISSING_REASON, TorrentTask, is_torrent_url,
                      libtorrent_available)


def task_class_for(url: str):
    scheme = url.lower().split("://", 1)[0] if "://" in url else ""
    if scheme in ("ftp", "ftps"):
        return FtpTask
    if looks_like_hls(url):
        return HlsTask
    return DownloadTask


class DownloadManager:
    def __init__(self, settings, callbacks=None):
        self.settings = settings
        # callbacks: on_added(task), on_state(task,state,msg), on_event(task,level,msg),
        #            on_removed(task_id)
        self.callbacks = callbacks or {}
        self.tasks: "OrderedDict[str, DownloadTask]" = OrderedDict()
        self._lock = threading.Lock()
        self._running = True
        self._scheduler: threading.Thread | None = None
        self._index_path = os.path.join(app_data_dir(), "tasks.json")

    # ---------- 回调转发 ----------
    def _task_callbacks(self) -> dict:
        def on_state(task, state, msg):
            if state == TaskState.COMPLETED:
                self._persist_index()
                if getattr(self.settings, "av_scan", False):
                    threading.Thread(
                        target=self._av_scan, args=(task,),
                        daemon=True, name="av-scan").start()
            cb = self.callbacks.get("on_state")
            if cb:
                cb(task, state, msg)

        def on_event(task, level, msg):
            cb = self.callbacks.get("on_event")
            if cb:
                cb(task, level, msg)

        return {"on_state": on_state, "on_event": on_event}

    # ---------- 生命周期 ----------
    def start(self) -> None:
        self.load_index()
        self._scheduler = threading.Thread(target=self._schedule_loop, daemon=True,
                                           name="dl-scheduler")
        self._scheduler.start()

    def shutdown(self, pause_running: bool = True) -> None:
        self._running = False
        if pause_running:
            with self._lock:
                tasks = list(self.tasks.values())
            for t in tasks:
                if t.state in (TaskState.CONNECTING, TaskState.DOWNLOADING, TaskState.QUEUED):
                    t.pause(timeout=20)
            # 关闭保险：对已暂停/出错任务再同步落盘一次续传现场，消除极端
            # 时序下 meta 尚未写完的竞态；已完成任务不写，以免残留 meta 文件
            for t in tasks:
                if t.state in (TaskState.PAUSED, TaskState.ERROR):
                    flush = getattr(t, "flush_meta", None)
                    if callable(flush):
                        try:
                            flush()
                        except Exception:
                            pass
            # 释放所有 BT 会话/监听端口，避免退出后线程残留
            for t in tasks:
                close = getattr(t, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
        self._persist_index()

    def _av_scan(self, task) -> None:
        """下载完成后调用系统杀毒软件扫描；检出威胁则删除文件并置为错误。"""
        path = getattr(task, "final_path", "")
        # 仅扫描单文件产物；BT 多文件目录不在此钩子范围内
        if not path or not os.path.isfile(path):
            return
        try:
            from .antivirus import scan_file
            result = scan_file(path)
        except Exception:  # noqa: BLE001  扫描器异常绝不能影响已完成任务
            return
        if not result.available:
            return  # 本机没有可用扫描器，静默跳过
        if result.infected:
            try:
                os.remove(path)
            except OSError:
                pass
            task.set_state(
                TaskState.ERROR,
                f"杀毒软件检出威胁并已删除文件：{result.threat or result.detail}")
            self._persist_index()
        else:
            task.callbacks.get("on_event") and task.callbacks["on_event"](
                task, "info", "杀毒扫描通过")

    # ---------- 任务增删控制 ----------
    def add(self, url: str, options: TaskOptions | None = None,
            start_paused: bool = False) -> DownloadTask:
        """添加任务；start_paused=True 时以暂停状态加入（“稍后下载”）。"""
        options = options or TaskOptions()
        with self._lock:
            for t in self.tasks.values():
                if t.url == url and t.state in (
                    TaskState.QUEUED, TaskState.CONNECTING,
                    TaskState.DOWNLOADING, TaskState.PAUSED,
                ):
                    return t
            if is_torrent_url(url):
                if not libtorrent_available():
                    raise DownloadError(MISSING_REASON)
                cls = TorrentTask
            else:
                cls = task_class_for(url)
            task = cls(self.settings, url, options, callbacks=self._task_callbacks())
            self.tasks[task.task_id] = task
        # set_state 会触发回调链（回调可能再次访问引擎锁），必须放在锁外
        if start_paused:
            task.set_state(TaskState.PAUSED)
        self._persist_index()
        cb = self.callbacks.get("on_added")
        if cb:
            cb(task)
        return task

    def pause(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if t:
            t.pause()
            self._persist_index()

    def resume(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if t:
            t.resume()
            self._persist_index()

    def restart(self, task_id: str) -> None:
        t = self.tasks.get(task_id)
        if not t:
            return
        if t.state in (TaskState.CONNECTING, TaskState.DOWNLOADING, TaskState.QUEUED):
            t.pause()
        if hasattr(t, "reset_for_restart"):
            t.reset_for_restart()
        else:
            for p in (t.tmp_path, t.meta_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            t.allocator = None
            t.meter.reset()
            t._downloaded = 0
            t.error_msg = ""
            t.pause_event.clear()
            t.fallback_event.clear()
        t.resume()
        self._persist_index()

    def remove(self, task_id: str, delete_files: bool = False) -> None:
        with self._lock:
            t = self.tasks.pop(task_id, None)
        if not t:
            return
        if t.state in (TaskState.CONNECTING, TaskState.DOWNLOADING, TaskState.QUEUED):
            t.pause()
        # BT 任务：先释放 libtorrent 会话/端口与文件句柄，否则文件可能被占用
        close = getattr(t, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        if delete_files:
            for p in (t.tmp_path, t.meta_path, t.final_path):
                if not p:
                    continue
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    elif os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass
        self._persist_index()
        cb = self.callbacks.get("on_removed")
        if cb:
            cb(task_id)

    def remove_completed(self, delete_files: bool = False) -> int:
        """清除所有已完成任务（默认保留文件），返回清除数量（对应 IDM Delete Completed）。"""
        ids = [s["task_id"] for s in self.snapshots()
               if s.get("state") == TaskState.COMPLETED]
        for tid in ids:
            self.remove(tid, delete_files)
        return len(ids)

    def pause_all(self) -> None:
        for t in list(self.tasks.values()):
            if t.state in (TaskState.QUEUED, TaskState.CONNECTING, TaskState.DOWNLOADING):
                t.pause()

    def resume_all(self) -> None:
        for t in list(self.tasks.values()):
            if t.state in (TaskState.PAUSED, TaskState.ERROR):
                t.resume()

    def apply_speed_limit(self) -> None:
        for t in self.tasks.values():
            t.limiter.limit = self.settings.speed_limit

    def snapshots(self) -> list[dict]:
        with self._lock:
            return [t.snapshot() for t in self.tasks.values()]

    # ---------- 调度 ----------
    def _schedule_loop(self) -> None:
        while self._running:
            try:
                with self._lock:
                    tasks = list(self.tasks.values())
                active = sum(
                    1 for t in tasks
                    if t.state in (TaskState.CONNECTING, TaskState.DOWNLOADING)
                )
                slots = max(1, self.settings.max_concurrent) - active
                for t in tasks:
                    if slots <= 0:
                        break
                    if t.state == TaskState.QUEUED:
                        threading.Thread(
                            target=t.run, daemon=True,
                            name=f"dispatch-{t.task_id[:6]}",
                        ).start()
                        slots -= 1
            except Exception:
                pass
            time.sleep(0.5)

    # ---------- 索引持久化 / 重启恢复 ----------
    def _persist_index(self) -> None:
        with self._lock:
            index = [
                {
                    "task_id": t.task_id,
                    "url": t.url,
                    "save_dir": t.save_dir,
                    "filename": t.filename,
                    "created_at": t.created_at,
                }
                for t in self.tasks.values()
            ]
        tmp = self._index_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._index_path)
        except OSError:
            pass

    def load_index(self) -> None:
        if not os.path.exists(self._index_path):
            return
        try:
            with open(self._index_path, "r", encoding="utf-8") as f:
                index = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        for item in index:
            try:
                self._restore_one(item)
            except Exception:
                continue

    def _restore_one(self, item: dict) -> None:
        url = item["url"]
        save_dir = item.get("save_dir") or self.settings.save_dir
        filename = item.get("filename") or ""
        final = os.path.join(save_dir, filename) if filename else ""
        meta = (final + META_SUFFIX) if final else ""
        tmp = (final + TMP_SUFFIX) if final else ""

        if final and os.path.exists(meta):
            with open(meta, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            kind = meta_data.get("kind")
            if kind == "hls":
                task = HlsTask.from_meta(
                    self.settings, meta_data, callbacks=self._task_callbacks()
                )
            elif kind == "ftp":
                task = FtpTask.from_meta(
                    self.settings, meta_data, callbacks=self._task_callbacks()
                )
            else:
                task = DownloadTask.from_meta(
                    self.settings, meta_data, callbacks=self._task_callbacks()
                )
            task.task_id = item.get("task_id", task.task_id)
            if os.path.exists(final) and not os.path.exists(tmp):
                task.set_state(TaskState.COMPLETED)
            else:
                task.set_state(TaskState.PAUSED)
                if self.settings.auto_resume:
                    task.resume()
        elif final and os.path.exists(final):
            task = DownloadTask(
                self.settings, url,
                TaskOptions(save_dir=save_dir, filename=filename),
                task_id=item.get("task_id"),
                callbacks=self._task_callbacks(),
            )
            task.final_path = final
            task.filename = filename
            task.file_info = FileInfo(
                url=url, final_url=url,
                total_size=os.path.getsize(final),
                resumable=True, filename=filename,
            )
            task.completed_at = item.get("created_at")
            task.set_state(TaskState.COMPLETED)
        else:
            # meta 丢失：清理残缺临时文件，重新排队下载
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            task = DownloadTask(
                self.settings, url,
                TaskOptions(save_dir=save_dir, filename=filename or None),
                task_id=item.get("task_id"),
                callbacks=self._task_callbacks(),
            )
        with self._lock:
            self.tasks[task.task_id] = task
        cb = self.callbacks.get("on_added")
        if cb:
            cb(task)
