# -*- coding: utf-8 -*-
"""单个下载任务：状态机 + 多连接动态分段 worker。

典型生命周期::

    QUEUED -> CONNECTING -> DOWNLOADING -> COMPLETED
                                  |           ^
                                  v           |
                                PAUSED ----resume
                                  |
                                  v
                                ERROR（可重试/重新下载）

实现要点：
- 每个 worker 持有独立文件句柄，在自己领取的字节区间内 seek/write，
  互不干扰；HTTP keep-alive 复用同一条 TCP 连接连续领取新区间。
- 每完成一个固定大小的块就记录到 completed 区间集，并节流持久化到
  ``*.pdlmeta``；暂停或崩溃后按 meta 重建未完成区间，实现断点续传。
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid

import requests

from .category import category_dir_for
from .checksum import normalize_algo, verify_file
from .errors import (
    DownloadError,
    FatalHttpError,
    PauseRequested,
    ProbeError,
    RangeUnsupported,
    RetryExhausted,
)
from .prealloc import preallocate
from .probe import (
    FileInfo,
    build_session,
    probe,
    reconcile_filename,
    session_kwargs,
)
from .ranges import SegmentAllocator
from .speed import RateLimiter, SpeedMeter
from .task_options import TaskOptions

TMP_SUFFIX = ".pdltmp"
META_SUFFIX = ".pdlmeta"
WRITE_CHUNK = 64 * 1024
# 预取双缓冲：每条连接在网络侧预读的块数（有界队列，队列满即对发送方背压）。
# 16 连接 × 16 × 64KB ≈ 16MB 上限，且仅在写盘暂时跟不上时才会填满。
PREFETCH_QUEUE = 16
# 磁盘空间预检时在文件大小之外预留的余量
DISK_HEADROOM = 10 * 1024 * 1024

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class TaskState:
    QUEUED = "queued"
    CONNECTING = "connecting"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


_STATE_CN = {
    "queued": "排队中",
    "connecting": "连接中",
    "downloading": "下载中",
    "paused": "已暂停",
    "completed": "已完成",
    "error": "错误",
}


def state_cn(state: str) -> str:
    return _STATE_CN.get(state, state)


def human_bytes(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    for unit in units:
        if f < 1024 or unit == units[-1]:
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


class _TaskConfigView:
    """把全局 Settings 与单任务覆盖合并后的只读视图。"""

    def __init__(self, settings, opt: TaskOptions | None):
        self._s = settings
        self.opt = opt or TaskOptions()

    def __getattr__(self, name):
        opt = self.__dict__.get("opt", None)
        val = getattr(opt, name, None)
        if val is not None:
            return val
        return getattr(self.__dict__["_s"], name)


class DownloadTask:
    def __init__(
        self,
        settings,
        url: str,
        options: TaskOptions | None = None,
        task_id: str | None = None,
        callbacks=None,
    ):
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.url = url
        self.settings = settings
        self.options = options or TaskOptions()
        self.cfg = _TaskConfigView(settings, self.options)
        self.callbacks = callbacks or {}

        self.file_info: FileInfo | None = None
        self.save_dir = self.options.save_dir or settings.save_dir
        self.filename = self.options.filename or ""
        self.final_path = ""
        self.tmp_path = ""
        self.meta_path = ""

        self.state = TaskState.QUEUED
        self.error_msg = ""
        self.created_at = time.time()
        self.completed_at: float | None = None

        self.allocator: SegmentAllocator | None = None
        self.meter = SpeedMeter()
        self.limiter = RateLimiter(settings.speed_limit)
        self._downloaded = 0
        self._resumable_mode = True  # False 时走不可续传的单线程路径

        self.pause_event = threading.Event()
        self.fallback_event = threading.Event()
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._workers: list[threading.Thread] = []
        self._flusher: threading.Thread | None = None
        self._dirty = threading.Event()
        self._stop_flusher = threading.Event()
        self._retry_counts: dict[int, int] = {}
        self._file_size_lock = threading.Lock()

        # 站点例外 / 单任务覆盖合并后的实际连接数（探测后确定）
        self.effective_connections = settings.connections
        # 校验结果
        self.checksum_actual = ""
        self.checksum_ok: bool | None = None
        # 卡死连接看门狗：wid -> {"resp": response, "last": 最近收到数据时间}
        self._active: dict[int, dict] = {}
        self._active_lock = threading.Lock()
        self._watchdog: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        # 自适应块大小调度线程
        self._scaler: threading.Thread | None = None
        self._scaler_stop = threading.Event()
        # 多镜像源：self._sources[0] 为主源，其余为合格镜像；并行记录健康度
        self._sources: list = []
        self._source_health: list[dict] = []
        self._source_lock = threading.Lock()
        self._source_rr = 0
        # PAC 自动配置：按本任务目标 URL 解析出固定代理，写回任务级 proxy
        self._resolve_pac_proxy()

    def _resolve_pac_proxy(self) -> None:
        pac = getattr(self.options, "pac_url", None) or \
            getattr(self.settings, "pac_url", None)
        if not pac:
            return
        try:
            from .proxy_support import resolve_pac_proxy
            resolved = resolve_pac_proxy(pac, self.url)
        except Exception:
            return
        if resolved:
            self.options.proxy = resolved
        elif resolved is not None:
            self.options.proxy = ""  # PAC 判定 DIRECT

    # ---------- 状态与回调 ----------
    def set_state(self, state: str, msg: str = "") -> None:
        with self._state_lock:
            self.state = state
            if msg:
                self.error_msg = msg
            elif state == TaskState.DOWNLOADING:
                self.error_msg = ""
        cb = self.callbacks.get("on_state")
        if cb:
            cb(self, state, msg)

    def emit_event(self, level: str, message: str) -> None:
        cb = self.callbacks.get("on_event")
        if cb:
            cb(self, level, message)

    def speed(self) -> float:
        return self.meter.speed()

    def total_size(self) -> int | None:
        if self.file_info and self.file_info.total_size:
            return self.file_info.total_size
        if self.allocator:
            return self.allocator.total_size
        return None

    def downloaded(self) -> int:
        if self.allocator:
            # 以分配器为权威：completed 与在途已写部分取并集，天然去重，
            # 不会因断流重下/工作窃取重复计数而超过文件总大小。
            return self.allocator.progress_bytes()
        return self._downloaded

    def progress(self) -> float:
        total = self.total_size()
        if not total:
            return 0.0
        return min(1.0, self.downloaded() / total)

    def eta_seconds(self) -> int:
        total = self.total_size()
        spd = self.speed()
        if not total or spd < 1024:
            return -1
        remain = max(0, total - self.downloaded())
        return int(remain / spd)

    def snapshot(self) -> dict:
        with self._state_lock:
            state = self.state
            error = self.error_msg
        return {
            "task_id": self.task_id,
            "url": self.url,
            "filename": self.filename or self.url,
            "size": self.total_size() or 0,
            "downloaded": self.downloaded(),
            "progress": self.progress(),
            "speed": self.speed(),
            "eta": self.eta_seconds(),
            "state": state,
            "state_cn": state_cn(state),
            "error": error,
            "save_dir": self.save_dir,
            "final_path": self.final_path,
            "created_at": self.created_at,
            "checksum_algo": normalize_algo(self.options.checksum_algo),
            "checksum_actual": self.checksum_actual,
            "checksum_ok": self.checksum_ok,
        }

    # ---------- 路径与 meta ----------
    def _prepare_paths(self) -> None:
        os.makedirs(self.save_dir, exist_ok=True)
        if not self.filename:
            self.filename = "download"
        base, ext = os.path.splitext(self.filename)
        final = os.path.join(self.save_dir, self.filename)
        i = 1
        while os.path.exists(final) and not self.meta_path:
            final = os.path.join(self.save_dir, f"{base} ({i}){ext}")
            i += 1
        self.final_path = final
        self.tmp_path = final + TMP_SUFFIX
        self.meta_path = final + META_SUFFIX

    def _meta_dict(self) -> dict:
        return {
            "version": 1,
            "task_id": self.task_id,
            "url": self.url,
            "final_url": self.file_info.final_url if self.file_info else self.url,
            "filename": self.filename,
            "save_dir": self.save_dir,
            "tmp_path": self.tmp_path,
            "final_path": self.final_path,
            "total_size": self.total_size(),
            "block_size": self.cfg.block_size,
            "resumable": bool(self.file_info and self.file_info.resumable),
            "completed": self.allocator.snapshot() if self.allocator else [],
            "etag": self.file_info.etag if self.file_info else "",
            "last_modified": self.file_info.last_modified if self.file_info else "",
            "created_at": self.created_at,
        }

    def flush_meta(self) -> None:
        if not self.meta_path or not self.allocator:
            return
        data = self._meta_dict()
        tmp = self.meta_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.meta_path)
        except OSError:
            pass
        self._dirty.clear()

    @classmethod
    def from_meta(cls, settings, meta: dict, callbacks=None) -> "DownloadTask":
        opt = TaskOptions(save_dir=meta.get("save_dir"), filename=meta.get("filename"))
        task = cls(settings, meta["url"], opt, task_id=meta.get("task_id"), callbacks=callbacks)
        task.final_path = meta.get("final_path", "")
        task.tmp_path = meta.get("tmp_path", "")
        task.meta_path = os.path.join(os.path.dirname(task.final_path),
                                      os.path.basename(task.final_path) + META_SUFFIX)
        task.created_at = meta.get("created_at", time.time())
        fi = FileInfo(
            url=meta["url"],
            final_url=meta.get("final_url", meta["url"]),
            total_size=meta.get("total_size"),
            resumable=meta.get("resumable", False),
            filename=meta.get("filename", ""),
            etag=meta.get("etag", ""),
            last_modified=meta.get("last_modified", ""),
        )
        task.file_info = fi
        task.filename = meta.get("filename") or task.filename
        if fi.resumable and fi.total_size:
            task.allocator = SegmentAllocator(
                fi.total_size, meta.get("block_size", settings.block_size),
                completed=meta.get("completed", []),
            )
        return task

    # ---------- 外部控制 ----------
    def pause(self, timeout: float = 30.0) -> None:
        self.pause_event.set()
        # 状态切换由 _run 收尾时完成；这里仅等待离开运行态
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._state_lock:
                if self.state in (TaskState.PAUSED, TaskState.ERROR, TaskState.COMPLETED):
                    return
            time.sleep(0.1)

    def resume(self) -> None:
        with self._state_lock:
            if self.state not in (TaskState.PAUSED, TaskState.ERROR):
                return
            self.state = TaskState.QUEUED
            self.error_msg = ""
        self.pause_event.clear()
        self.fallback_event.clear()

    def reset_for_restart(self) -> None:
        """清空下载进度与临时文件，供“重新下载”使用（file_info 保留）。"""
        for p in (self.tmp_path, self.meta_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.allocator = None
        self.meter.reset()
        self._downloaded = 0
        self.error_msg = ""
        self.pause_event.clear()
        self.fallback_event.clear()

    # ---------- 主流程（由调度器线程池调用，可在 resume 后再次进入） ----------
    def run(self) -> None:
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            self._run_until_idle()
        except Exception as exc:  # 防御性兜底，任何未预期异常都落为 ERROR
            self.set_state(TaskState.ERROR, f"内部错误: {exc}")
        finally:
            self._run_lock.release()

    def _run_until_idle(self) -> None:
        self.limiter = RateLimiter(self.settings.speed_limit)
        self.set_state(TaskState.CONNECTING)

        # 1) 探测
        if self.file_info is None:
            try:
                self.file_info = probe(self.url, self.cfg)
            except ProbeError as exc:
                self.set_state(TaskState.ERROR, str(exc))
                return
            # 扩展可能从 302 临时地址取到随机资源 ID 作为建议名；当探测到了
            # 权威文件名（Content-Disposition / 原始 URL）时据此纠正
            self.filename = reconcile_filename(
                self.options.filename or "", self.file_info.filename or ""
            )
        self._init_sources()
        self._resolve_runtime()
        self._prepare_paths()

        # 2) 断点续传：重新探测校验远端文件是否变化
        if self.allocator is None and os.path.exists(self.meta_path):
            if not self._load_and_verify_meta():
                return  # 状态已在函数内设置

        info = self.file_info
        if info.total_size and not self._check_disk_space(info.total_size):
            return
        if info.total_size and info.resumable:
            if self.allocator is None:
                self.allocator = SegmentAllocator(info.total_size, self.cfg.block_size)
            self._resumable_mode = True
        else:
            self._resumable_mode = False
            self.allocator = None

        # 3) 预分配 / 打开临时文件
        try:
            if self._resumable_mode:
                fresh = not os.path.exists(self.tmp_path)
                if fresh:
                    with open(self.tmp_path, "wb") as f:
                        f.truncate(info.total_size)
                    if getattr(self.settings, "preallocate", False):
                        if preallocate(self.tmp_path, info.total_size):
                            self.emit_event("info", "已预分配磁盘空间")
            else:
                with open(self.tmp_path, "wb"):
                    pass
        except OSError as exc:
            self.set_state(TaskState.ERROR, f"无法创建文件: {exc}")
            return

        self._start_flusher()
        self._start_watchdog()
        self.set_state(TaskState.DOWNLOADING)

        try:
            if self._resumable_mode:
                self._run_segmented()
            else:
                self._run_single_stream()
        finally:
            self._stop_watchdog()
            self._stop_flusher.set()
            if self._flusher:
                self._flusher.join(timeout=2)

        if self.pause_event.is_set():
            self.flush_meta()
            self.set_state(TaskState.PAUSED)
            return

        if self.state == TaskState.ERROR:
            self.flush_meta()
            return

        # 4) 完成：校验、改名、清理（以分配器区间为准，不看可能因重下虚高的计数）
        if self._resumable_mode and not self.allocator.is_complete():
            self.set_state(TaskState.ERROR, "下载数据不完整，请重新尝试")
            self.flush_meta()
            return
        self.flush_meta()
        if not self._run_checksum(self.tmp_path):
            self.flush_meta()
            return
        try:
            if os.path.exists(self.final_path):
                os.remove(self.final_path)
            os.replace(self.tmp_path, self.final_path)
            if os.path.exists(self.meta_path):
                os.remove(self.meta_path)
        except OSError as exc:
            self.set_state(TaskState.ERROR, f"文件保存失败: {exc}")
            return
        self.completed_at = time.time()
        self.set_state(TaskState.COMPLETED)

    def _load_and_verify_meta(self) -> bool:
        """加载 meta 并校验远端一致性。返回 False 表示应中止本次运行。"""
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, json.JSONDecodeError):
            return True  # meta 损坏，按新任务处理
        try:
            fresh = probe(self.url, self.cfg)
        except ProbeError:
            # 离线：保持暂停，等待网络恢复后手动继续
            self.set_state(TaskState.PAUSED, "网络不可用，保持暂停")
            return False

        same_size = fresh.total_size == meta.get("total_size")
        same_tag = (
            not meta.get("etag")
            or not fresh.etag
            or fresh.etag == meta.get("etag")
            or fresh.last_modified == meta.get("last_modified")
        )
        if not (same_size and same_tag):
            self.emit_event("info", "远端文件已变化，将重新下载")
            for p in (self.tmp_path, self.meta_path):
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            self.file_info = fresh
            return True
        self.file_info = fresh
        if fresh.resumable and fresh.total_size:
            self.allocator = SegmentAllocator(
                fresh.total_size,
                meta.get("block_size", self.cfg.block_size),
                completed=meta.get("completed", []),
            )
        return True

    # ---------- 分段并行下载 ----------
    def _run_segmented(self) -> None:
        total = self.allocator.total_size
        n_blocks = (total + self.cfg.block_size - 1) // self.cfg.block_size
        n_workers = max(1, min(self.effective_connections, n_blocks))
        self._workers = []
        for wid in range(n_workers):
            t = threading.Thread(target=self._worker_loop, args=(wid,), daemon=True,
                                 name=f"dl-{self.task_id[:6]}-{wid}")
            self._workers.append(t)
            t.start()
        # 连接数一次开满（不牺牲高速场景的启动速度），动态再平衡交给
        # 自适应块大小 + 块内工作窃取完成。
        self._start_scaler()
        try:
            for t in self._workers:
                t.join()
        finally:
            self._stop_scaler()

        if self.fallback_event.is_set() and not self.pause_event.is_set():
            # 探测时声称支持 Range，实际返回 200：降级单线程重下
            self.fallback_event.clear()
            self.emit_event("info", "服务器不支持断点续传，已切换单线程下载")
            self.allocator = None
            self._downloaded = 0
            self.meter.reset()
            self._resumable_mode = False
            with open(self.tmp_path, "wb"):
                pass
            self._run_single_stream()

    def _worker_loop(self, wid: int) -> None:
        session = build_session(self.cfg)
        session.headers.update({"Accept-Encoding": "identity"})
        my_sidx = -1  # 粘性绑定的源；该源故障/冷却时置 -1 触发重新选择
        try:
            while (not self.pause_event.is_set() and not self.fallback_event.is_set()
                   and self.state != TaskState.ERROR):
                # 先领未分配缺口；缺口耗尽时从慢连接在途大段的尾部窃取一块
                got = self.allocator.next_for_worker(wid)
                if got is None:
                    # 暂时无活：只要还有在途连接，就等待其完成或失败释放区间
                    # （卡死连接由看门狗兜底断开），避免快连接在慢连接收尾前
                    # 提前退出，导致尾部缺口无人补、或慢连接后续大块无人窃取。
                    if self.allocator.is_complete():
                        return
                    if (self.allocator.active_lease_count() == 0
                            and self.allocator.unclaimed_bytes() == 0):
                        # 既无在途连接也无未分配缺口却未完成：异常兜底，避免死等
                        return
                    time.sleep(0.1)
                    continue
                lid, (start, end) = got
                source, sidx = self._pick_source(my_sidx)
                my_sidx = sidx
                try:
                    self._stream_range(session, wid, lid, start, end, source, sidx)
                    self._source_ok(sidx)
                    self.allocator.complete(lid)
                except PauseRequested:
                    self.allocator.release_lid(lid)
                    return
                except RangeUnsupported:
                    self.allocator.release_lid(lid)
                    self.fallback_event.set()
                    return
                except RetryExhausted as exc:
                    self.allocator.release_lid(lid)
                    self.set_state(TaskState.ERROR, str(exc))
                    return
                except requests.RequestException as exc:
                    self.allocator.release_lid(lid)
                    self._source_failed(sidx)
                    my_sidx = -1  # 下次领取时改选其他健康源
                    if self.pause_event.is_set():
                        return
                    if not self._retry_wait(wid, str(exc)):
                        self.set_state(TaskState.ERROR, f"连接失败，重试耗尽: {exc}")
                        return
                    self._safe_close(session)
                    session = build_session(self.cfg)
                    session.headers.update({"Accept-Encoding": "identity"})
                except Exception as exc:
                    # 跨线程关闭卡死连接等情况下，urllib3 偶发抛出非 RequestException
                    # 的连接级异常；同样释放该分段并重建连接、换源重试，避免丢块。
                    self.allocator.release_lid(lid)
                    self._source_failed(sidx)
                    my_sidx = -1  # 下次领取时改选其他健康源
                    if self.pause_event.is_set():
                        return
                    if not self._retry_wait(wid, f"连接异常 {exc}"):
                        self.set_state(TaskState.ERROR, f"连接异常，重试耗尽: {exc}")
                        return
                    self._safe_close(session)
                    session = build_session(self.cfg)
                    session.headers.update({"Accept-Encoding": "identity"})
        finally:
            self._safe_close(session)

    @staticmethod
    def _safe_close(session) -> None:
        try:
            session.close()
        except Exception:
            pass

    def _stream_range(self, session: requests.Session, wid: int, lid: int,
                      start: int, end: int, source=None, sidx: int = 0) -> None:
        """用指定源（多镜像时由 _pick_source 选择）下载租约 [start, end)。

        含写盘、限速与增量记账；worker 不绑定固定源，每次领取租约都重新挑选
        当前最健康的源，从而聚合所有镜像带宽，并在某源故障时自动切换。

        采用预取双缓冲：一个 reader 线程持续从网络读取并放入有界队列，工作
        线程同时写盘与记账，使 recv 与磁盘写相互重叠，隐藏高延迟链路/磁盘
        抖动造成的连接空窗；队列满即对发送方形成背压（内存有界）。

        若该租约尾部被其他连接窃取（``current_end`` 缩小），则精确写到
        current_end 即结束本次请求，剩余响应字节丢弃且不计入下载进度
        （那部分由窃取者重新下载），从而避免重复写或进度超过文件总大小。
        """
        import queue

        src = source or self.file_info
        url = src.final_url or src.url
        resp = session.get(
            url,
            headers={"Range": f"bytes={start}-{end - 1}", "Accept-Encoding": "identity"},
            stream=True,
            **session_kwargs(self.cfg),
        )
        slot = {"resp": resp, "last": time.time()}
        with self._active_lock:
            self._active[wid] = slot
        stop = threading.Event()
        reader: threading.Thread | None = None
        try:
            status = resp.status_code
            if status in (200, 416):
                if len(self._sources) > 1:
                    # 该镜像不支持 Range（或区间非法）：熔断此源并换其他源重试，
                    # 而不是让整个任务降级为单线程
                    self._source_failed(sidx, fatal=True)
                    raise requests.ConnectionError(
                        f"下载源 {sidx + 1} 不支持断点续传，已切换其他源")
                raise RangeUnsupported()
            if status in RETRYABLE_STATUS:
                raise requests.HTTPError(f"HTTP {status}", response=resp)
            if status >= 400:
                if status in (401, 403, 404):
                    raise FatalHttpError(f"HTTP {status}，无法下载（鉴权或资源不存在）")
                raise requests.HTTPError(f"HTTP {status}", response=resp)
            if status != 206:
                raise requests.HTTPError(f"意外的状态码 {status}", response=resp)

            # reader 只负责网络读取入队；写盘/记账/截断/暂停全部在工作线程，
            # 因此无需为 allocator/文件句柄加额外锁。
            chunks: "queue.Queue" = queue.Queue(maxsize=PREFETCH_QUEUE)

            def _reader() -> None:
                try:
                    for chunk in resp.iter_content(WRITE_CHUNK):
                        if stop.is_set():
                            return
                        if not chunk:
                            continue
                        slot["last"] = time.time()  # 真实网络活性，供看门狗判断
                        while not stop.is_set():
                            try:
                                chunks.put(chunk, timeout=0.2)
                                break
                            except queue.Full:
                                continue
                    chunks.put(None)  # 正常读到 EOF
                except Exception as exc:  # 断流/IncompleteRead 等交给工作线程重试
                    if not stop.is_set():
                        chunks.put(exc)

            reader = threading.Thread(
                target=_reader, daemon=True,
                name=f"rd-{self.task_id[:6]}-{wid}")
            reader.start()

            block = self.cfg.block_size
            with open(self.tmp_path, "r+b") as f:
                f.seek(start)
                pos = start
                boundary = (start // block + 1) * block
                while True:
                    item = chunks.get()
                    if item is None:
                        break  # EOF
                    if isinstance(item, BaseException):
                        raise item
                    if self.pause_event.is_set() or self.fallback_event.is_set():
                        raise PauseRequested()
                    target = self.allocator.current_end(lid) or end
                    if pos >= target:
                        break  # 尾部已被窃取，丢弃队列中剩余响应
                    chunk = item
                    wlen = min(len(chunk), target - pos)
                    f.write(chunk[:wlen])
                    pos += wlen
                    with self._file_size_lock:
                        self._downloaded += wlen
                    self.meter.add_bytes(wlen)
                    self.limiter.consume(wlen)
                    self.allocator.report(lid, pos)
                    while boundary <= min(pos, target):
                        bs = max(start, boundary - block)
                        self.allocator.mark_completed_range(bs, boundary)
                        self._dirty.set()
                        boundary += block
                    if wlen < len(chunk):
                        break  # 恰好写到窃取分割点，正常结束
                target = self.allocator.current_end(lid) or end
                if pos != target:
                    raise requests.ConnectionError(
                        f"数据不完整: 区间 {start}-{target} 实际到 {pos}")
            self._dirty.set()
        finally:
            stop.set()
            with self._active_lock:
                self._active.pop(wid, None)
            resp.close()  # 打断可能仍阻塞在 recv 的 reader
            if reader is not None:
                reader.join(timeout=1.0)

    # ---------- 单线程顺序下载（服务器不支持 Range 或大小未知） ----------
    def _run_single_stream(self) -> None:
        session = build_session(self.cfg)
        session.headers.update({"Accept-Encoding": "identity"})
        try:
            resp = session.get(
                self.file_info.final_url or self.url,
                stream=True,
                **session_kwargs(self.cfg),
            )
            slot = {"resp": resp, "last": time.time()}
            with self._active_lock:
                self._active[0] = slot
            try:
                if resp.status_code in RETRYABLE_STATUS:
                    self.set_state(TaskState.ERROR, f"服务器返回 HTTP {resp.status_code}")
                    return
                if resp.status_code >= 400:
                    self.set_state(TaskState.ERROR, f"服务器返回 HTTP {resp.status_code}")
                    return
                if not self.file_info.total_size:
                    cl = resp.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        self.file_info.total_size = int(cl)
                if self.file_info.total_size and not self._check_disk_space(self.file_info.total_size):
                    return
                with open(self.tmp_path, "r+b") as f:
                    f.seek(0)
                    f.truncate(0)
                    pos = 0
                    for chunk in resp.iter_content(WRITE_CHUNK):
                        if self.pause_event.is_set():
                            return
                        if not chunk:
                            continue
                        f.write(chunk)
                        pos += len(chunk)
                        slot["last"] = time.time()
                        self._downloaded = pos
                        self.meter.add_bytes(len(chunk))
                        self.limiter.consume(len(chunk))
                if self.file_info.total_size is None:
                    self.file_info.total_size = pos
            finally:
                with self._active_lock:
                    self._active.pop(0, None)
                resp.close()
        except requests.RequestException as exc:
            if self.pause_event.is_set():
                return
            self.set_state(TaskState.ERROR, f"连接失败: {exc}")
        finally:
            session.close()

    # ---------- 多镜像源 ----------
    def _init_sources(self) -> None:
        """以主源为源池首项，探测并纳入合格镜像（同大小、支持 Range）。"""
        self._sources = [self.file_info]
        self._source_health = [{"fails": 0, "cooldown": 0.0, "dead": False}]
        self._source_rr = 0
        mirrors = getattr(self.options, "mirrors", None) or []
        total = self.file_info.total_size
        for mirror in mirrors:
            mirror = (mirror or "").strip()
            if not mirror or mirror == self.url:
                continue
            try:
                info = probe(mirror, self.cfg)
            except ProbeError as exc:
                self.emit_event("info", f"镜像源不可用，已忽略（{str(exc)[:60]}）")
                continue
            if not info.resumable:
                self.emit_event("info", "镜像源不支持断点续传，已忽略")
                continue
            if total and info.total_size and info.total_size != total:
                self.emit_event("info", "镜像源文件大小与主源不一致，已忽略")
                continue
            self._sources.append(info)
            self._source_health.append({"fails": 0, "cooldown": 0.0, "dead": False})
        if len(self._sources) > 1:
            self.emit_event("info", f"已启用 {len(self._sources)} 个下载源聚合加速")

    def _pick_source(self, preferred: int = -1):
        """挑选一个当前健康（未死亡、未在冷却）的源，返回 (FileInfo, idx)。

        连接采用“粘性绑定 + 故障转移”：worker 首次轮询绑定一个源并持续使用，
        该源失败/冷却才换源。这样快源上的连接始终用快源，并通过工作窃取接管
        慢源持有的块，聚合带宽随各源真实速度自然分配（接近 IDM/aria2 行为）。
        """
        if len(self._sources) == 1:
            return self._sources[0], 0
        with self._source_lock:
            now = time.time()

            def healthy(i):
                h = self._source_health[i]
                return not h["dead"] and h["cooldown"] <= now

            if 0 <= preferred < len(self._sources) and healthy(preferred):
                return self._sources[preferred], preferred
            avail = [i for i in range(len(self._sources)) if healthy(i)]
            if not avail:
                # 全部被熔断：复位后允许重试，避免无可用源
                for h in self._source_health:
                    h["dead"] = False
                    h["cooldown"] = 0.0
                avail = list(range(len(self._sources)))
            idx = avail[self._source_rr % len(avail)]
            self._source_rr += 1
            return self._sources[idx], idx

    def _source_failed(self, idx: int, fatal: bool = False) -> None:
        if idx >= len(self._source_health):
            return
        with self._source_lock:
            h = self._source_health[idx]
            h["fails"] += 1
            if fatal:
                h["dead"] = True  # 如该源不支持 Range / 资源不存在
            else:
                h["cooldown"] = time.time() + min(2 ** h["fails"], 30)

    def _source_ok(self, idx: int) -> None:
        if 0 <= idx < len(self._source_health):
            with self._source_lock:
                self._source_health[idx]["fails"] = 0
                self._source_health[idx]["cooldown"] = 0.0

    # ---------- 运行时解析 / 磁盘检查 / 卡死看门狗 ----------
    def _resolve_runtime(self) -> None:
        """探测后确定实际连接数与自动分类目录。"""
        if self.options.connections:
            self.effective_connections = self.options.connections
        else:
            self.effective_connections = (
                self.settings.connections_for(self.url) or self.settings.connections
            )
        # 自动分类仅对新任务、且未显式指定保存目录时生效
        if (not self.meta_path and not self.options.save_dir
                and getattr(self.settings, "auto_categorize", False)):
            name = self.filename or (self.file_info.filename if self.file_info else "")
            if name:
                self.save_dir = category_dir_for(name, True, self.settings.save_dir)

    def _check_disk_space(self, need: int) -> bool:
        """下载前检查剩余空间，不足直接报错而不是下到一半失败。"""
        try:
            free = shutil.disk_usage(self.save_dir).free
        except OSError:
            return True  # 无法判断（如某些网络盘），交给写盘阶段
        if free < need + DISK_HEADROOM:
            self.set_state(
                TaskState.ERROR,
                f"磁盘空间不足：需要约 {human_bytes(need)}，当前剩余 {human_bytes(free)}",
            )
            return False
        return True

    def _run_checksum(self, path: str) -> bool:
        """下载完成后按用户指定算法计算并比对摘要。"""
        algo = normalize_algo(self.options.checksum_algo)
        if not algo or not os.path.exists(path):
            return True
        try:
            ok, actual = verify_file(path, algo,
                                     self.options.checksum_expected or "")
        except (OSError, ValueError) as exc:
            self.set_state(TaskState.ERROR, f"校验失败: {exc}")
            return False
        self.checksum_actual = actual
        self.checksum_ok = ok
        if not ok:
            self.set_state(
                TaskState.ERROR,
                f"{algo.upper()} 校验和不匹配，文件可能损坏或不完整（实际 {actual[:16]}…）",
            )
            return False
        self.emit_event("info", f"{algo.upper()} 校验通过")
        return True

    def _start_watchdog(self) -> None:
        self._watchdog_stop.clear()
        self._watchdog = threading.Thread(
            target=self._watchdog_loop, daemon=True, name=f"wd-{self.task_id[:6]}"
        )
        self._watchdog.start()

    def _stop_watchdog(self) -> None:
        self._watchdog_stop.set()
        if self._watchdog:
            self._watchdog.join(timeout=2)
        self._watchdog = None
        with self._active_lock:
            self._active.clear()

    # ---------- 自适应块大小调度 ----------
    def _start_scaler(self) -> None:
        self._scaler_stop.clear()
        self._scaler = threading.Thread(
            target=self._scaler_loop, daemon=True, name=f"sc-{self.task_id[:6]}"
        )
        self._scaler.start()

    def _stop_scaler(self) -> None:
        self._scaler_stop.set()
        if self._scaler:
            self._scaler.join(timeout=2)
        self._scaler = None

    def _scaler_loop(self) -> None:
        """按实时聚合速率自适应调整分段块大小。

        高速链路用大块（降低 Range 请求、锁竞争与磁盘随机写开销），
        低速链路保持小块（加快慢连接尾部被窃取的再平衡速度）。
        目标：单个块约 0.25 秒下完；对齐与限幅由 allocator 完成。
        """
        while not self._scaler_stop.wait(1.5):
            if (self.pause_event.is_set() or self.fallback_event.is_set()
                    or self.state == TaskState.ERROR or self.allocator is None):
                continue
            spd = self.meter.speed()
            if spd >= self.cfg.block_size:
                self.allocator.set_dynamic_block(int(spd * 0.25))

    def _watchdog_loop(self) -> None:
        """监测卡死连接：长时间收不到数据就强制关闭，让该分段回池被重新领取。"""
        timeout = max(3, int(getattr(self.settings, "stall_timeout", 15)))
        while not self._watchdog_stop.wait(1.0):
            if self.pause_event.is_set() or self.fallback_event.is_set():
                continue
            now = time.time()
            with self._active_lock:
                items = list(self._active.items())
            for wid, slot in items:
                if now - slot["last"] >= timeout:
                    # 推后时间戳，避免在 worker 退出前重复关闭同一连接
                    slot["last"] = now + timeout + 60
                    try:
                        slot["resp"].close()
                    except Exception:
                        pass
                    self.emit_event(
                        "info",
                        f"连接 {wid + 1} 已 {timeout} 秒无数据，断开并把该分段交给其他连接",
                    )

    # ---------- 重试/后台持久化 ----------
    def _retry_wait(self, wid: int, reason: str) -> bool:
        n = self._retry_counts.get(wid, 0)
        if n >= self.cfg.retries:
            return False
        self._retry_counts[wid] = n + 1
        delay = min(2 ** n, 30)
        self.emit_event("info", f"连接中断，{delay}s 后第 {n + 1} 次重试（{reason[:60]}）")
        deadline = time.time() + delay
        while time.time() < deadline:
            if self.pause_event.is_set():
                return False
            time.sleep(0.2)
        return True

    def _start_flusher(self) -> None:
        self._stop_flusher.clear()

        def loop():
            while not self._stop_flusher.wait(2.0):
                if self._dirty.is_set():
                    self.flush_meta()

        self._flusher = threading.Thread(target=loop, daemon=True, name=f"meta-{self.task_id[:6]}")
        self._flusher.start()
