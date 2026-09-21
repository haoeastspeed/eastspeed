# -*- coding: utf-8 -*-
"""FTP / FTPS 多连接下载任务。

- ``ftp://`` 普通 FTP，``ftps://`` 走显式 AUTH TLS（FTP over TLS，RFC 4217）；
- 二进制模式（TYPE I），``SIZE`` 取大小、``REST`` + ``RETR`` 分段，多控制连接并行；
- 服务器不支持 REST/SIZE 时自动降级为单连接整文件下载；
- 区间分配、meta 持久化、暂停续传、限速、自动分类、校验与 HTTP 任务对齐。

代理不适用于 FTP（requests 风格的 HTTP 代理无法转发 FTP 数据通道），忽略之。
"""
from __future__ import annotations

import ftplib
import json
import os
import socket
import ssl
import threading
import time
import urllib.parse
import uuid

from .category import category_dir_for
from .checksum import normalize_algo, verify_file
from .errors import DownloadError
from .prealloc import preallocate
from .ranges import SegmentAllocator
from .speed import RateLimiter, SpeedMeter
from .task import META_SUFFIX, TMP_SUFFIX, TaskState, human_bytes, state_cn
from .task_options import TaskOptions

WRITE_CHUNK = 64 * 1024


def parse_ftp_url(url: str) -> dict:
    p = urllib.parse.urlparse(url)
    scheme = p.scheme.lower()
    if scheme not in ("ftp", "ftps"):
        raise DownloadError(f"不是 FTP 链接: {url}")
    tls = scheme == "ftps"
    host = p.hostname or ""
    if not host:
        raise DownloadError("FTP 链接缺少主机名")
    port = p.port or (990 if False else 21)  # ftps:// 默认仍按显式 TLS 的 21 端口
    user = urllib.parse.unquote(p.username or "") or "anonymous"
    pwd = urllib.parse.unquote(p.password or "") or "anonymous@"
    path = urllib.parse.unquote(p.path or "/")
    if path.startswith("/") and not path.startswith("//"):
        # ftplib 的目录路径按 CWD 解析，前导 / 表示绝对路径，保留
        pass
    return {"tls": tls, "host": host, "port": port,
            "user": user, "password": pwd, "path": path}


class FtpTask:
    """与 DownloadTask / HlsTask 接口对齐的 FTP 下载任务。"""

    kind = "ftp"

    def __init__(self, settings, url: str, options: TaskOptions | None = None,
                 task_id: str | None = None, callbacks=None):
        self.settings = settings
        self.url = url
        self.info = parse_ftp_url(url)
        self.options = options or TaskOptions()
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.callbacks = callbacks or {}

        if self.options.username:
            self.info["user"] = self.options.username
        if self.options.password:
            self.info["password"] = self.options.password

        self.save_dir = self.options.save_dir or settings.save_dir
        self.filename = self.options.filename or os.path.basename(
            self.info["path"].rstrip("/")) or "ftp-download.bin"
        self.final_path = ""
        self.tmp_path = ""
        self.meta_path = ""

        self.state = TaskState.QUEUED
        self.error_msg = ""
        self.created_at = time.time()
        self.completed_at = None

        self.total_size = 0
        self.resumable = False
        self.allocator: SegmentAllocator | None = None
        self.meter = SpeedMeter()
        self.limiter = RateLimiter(settings.speed_limit)
        self.effective_connections = settings.connections
        self.checksum_actual = ""
        self.checksum_ok = None

        self.pause_event = threading.Event()
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._meta_dirty = threading.Event()
        self._stop_flusher = threading.Event()
        self._flusher = None
        self._retries: dict[int, int] = {}

    # ----- 状态 -----
    def set_state(self, state, msg=""):
        with self._state_lock:
            self.state = state
            self.error_msg = msg
        cb = self.callbacks.get("on_state")
        if cb:
            cb(self, state, msg)

    def emit_event(self, level, message):
        cb = self.callbacks.get("on_event")
        if cb:
            cb(self, level, message)

    def downloaded(self):
        if self.allocator:
            return self.allocator.done_bytes()
        return self.meter_total

    meter_total = 0

    def total_size_value(self):
        return self.total_size or 0

    def speed(self):
        return self.meter.speed()

    def progress(self):
        if not self.total_size:
            return 0.0
        return min(1.0, self.downloaded() / self.total_size)

    def eta_seconds(self):
        spd, total, done = self.speed(), self.total_size, self.downloaded()
        if not total or spd < 1024:
            return -1
        return int(max(0, total - done) / spd)

    def snapshot(self):
        with self._state_lock:
            state, error = self.state, self.error_msg
        return {
            "task_id": self.task_id, "url": self.url,
            "filename": self.filename, "size": self.total_size or 0,
            "downloaded": self.downloaded(), "progress": self.progress(),
            "speed": self.speed(), "eta": self.eta_seconds(),
            "state": state, "state_cn": state_cn(state), "error": error,
            "save_dir": self.save_dir, "final_path": self.final_path,
            "created_at": self.created_at,
            "checksum_algo": normalize_algo(self.options.checksum_algo),
            "checksum_actual": self.checksum_actual,
            "checksum_ok": self.checksum_ok,
        }

    # ----- 控制 -----
    def pause(self, timeout=30.0):
        self.pause_event.set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._state_lock:
                if self.state in (TaskState.PAUSED, TaskState.ERROR,
                                  TaskState.COMPLETED):
                    return
            time.sleep(0.1)

    def resume(self):
        with self._state_lock:
            if self.state not in (TaskState.PAUSED, TaskState.ERROR):
                return
            self.state = TaskState.QUEUED
            self.error_msg = ""
        self.pause_event.clear()

    def reset_for_restart(self):
        for p in (self.tmp_path, self.meta_path, self.final_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self.allocator = None
        self.meter_total = 0
        self.meter = SpeedMeter()
        self.error_msg = ""
        self.total_size = 0
        self.pause_event.clear()

    # ----- 连接 -----
    def _connect(self):
        i = self.info
        timeout = max(5, self.settings.read_timeout)
        if i["tls"]:
            ctx = ssl.create_default_context()
            if not self.settings.verify_ssl:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            ftp = ftplib.FTP_TLS(context=ctx, timeout=timeout)
        else:
            ftp = ftplib.FTP(timeout=timeout)
        ftp.connect(i["host"], i["port"], timeout=self.settings.connect_timeout)
        ftp.login(i["user"], i["password"])
        if i["tls"]:
            ftp.prot_p()  # 数据通道加密
        ftp.voidcmd("TYPE I")
        return ftp

    def _prepare(self):
        os.makedirs(self.save_dir, exist_ok=True)
        final = os.path.join(self.save_dir, self.filename)
        base, ext = os.path.splitext(final)
        n = 1
        while os.path.exists(final) and not self.final_path:
            final = f"{base} ({n}){ext}"
            n += 1
        self.final_path = final
        self.tmp_path = final + TMP_SUFFIX
        self.meta_path = final + META_SUFFIX

    def _resolve_runtime(self):
        if self.options.connections:
            self.effective_connections = self.options.connections
        else:
            self.effective_connections = (
                self.settings.connections_for(self.url) or self.settings.connections
            )
        if not self.options.save_dir and getattr(self.settings,
                                                 "auto_categorize", False):
            from .category import category_dir_for as cdf
            self.save_dir = cdf(self.filename, True, self.settings.save_dir)

    # ----- meta -----
    def _meta_dict(self):
        return {
            "kind": "ftp", "version": 1, "task_id": self.task_id,
            "url": self.url, "save_dir": self.save_dir,
            "filename": self.filename, "tmp_path": self.tmp_path,
            "final_path": self.final_path, "total_size": self.total_size,
            "block_size": self.settings.block_size,
            "resumable": self.resumable,
            "completed": self.allocator.snapshot() if self.allocator else [],
            "created_at": self.created_at,
        }

    def flush_meta(self):
        if not self.meta_path:
            return
        tmp = self.meta_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._meta_dict(), f, ensure_ascii=False)
            os.replace(tmp, self.meta_path)
        except OSError:
            pass
        self._meta_dirty.clear()

    @classmethod
    def from_meta(cls, settings, meta, callbacks=None):
        opt = TaskOptions(save_dir=meta.get("save_dir"),
                          filename=meta.get("filename"))
        task = cls(settings, meta["url"], opt,
                   task_id=meta.get("task_id"), callbacks=callbacks)
        task.final_path = meta.get("final_path", "")
        task.tmp_path = meta.get("tmp_path", "")
        task.meta_path = task.final_path + META_SUFFIX
        task.total_size = meta.get("total_size", 0)
        task.resumable = meta.get("resumable", False)
        if task.resumable and task.total_size:
            task.allocator = SegmentAllocator(
                task.total_size, meta.get("block_size", settings.block_size),
                completed=meta.get("completed", []))
            task.meter_total = task.allocator.done_bytes()
        task.created_at = meta.get("created_at", time.time())
        return task

    def _start_flusher(self):
        self._stop_flusher.clear()

        def loop():
            while not self._stop_flusher.wait(2.0):
                if self._meta_dirty.is_set():
                    self.flush_meta()

        self._flusher = threading.Thread(target=loop, daemon=True,
                                         name=f"ftp-meta-{self.task_id[:6]}")
        self._flusher.start()

    # ----- 主流程 -----
    def run(self):
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            self._run_until_idle()
        except Exception as exc:  # noqa: BLE001
            self.set_state(TaskState.ERROR, f"内部错误: {exc}")
        finally:
            self._run_lock.release()

    def _probe(self, ftp):
        """返回 (size, resumable)。"""
        size = None
        try:
            size = ftp.size(self.info["path"])
        except (ftplib.Error, OSError):
            size = None
        resumable = False
        if size:
            # 探测 REST：成功时代理服务器回 350（ftplib.voidcmd 只认 2xx 会误抛，
            # 故用 sendcmd 取原始应答）。
            try:
                resp = ftp.sendcmd("REST 0")
                resumable = resp.startswith("350")
            except ftplib.Error:
                resumable = False
        return (size or 0), resumable

    def _run_until_idle(self):
        self.limiter = RateLimiter(self.settings.speed_limit)
        self.set_state(TaskState.CONNECTING)
        self._resolve_runtime()
        try:
            probe_ftp = self._connect()
        except (ftplib.Error, OSError, ssl.SSLError) as exc:
            self.set_state(TaskState.ERROR, f"FTP 连接失败: {exc}")
            return
        try:
            if not self.total_size:
                self.total_size, self.resumable = self._probe(probe_ftp)
        finally:
            try:
                probe_ftp.quit()
            except Exception:  # noqa: BLE001
                probe_ftp.close()

        self._prepare()
        # 恢复后若目录因自动分类变化，_prepare 已重建 final；meta 路径以 final 为准
        self.meta_path = self.final_path + META_SUFFIX
        self.tmp_path = self.final_path + TMP_SUFFIX

        if self.resumable and self.total_size:
            try:
                free = __import__("shutil").disk_usage(self.save_dir).free
                if free < self.total_size + 10 * 1024 * 1024:
                    self.set_state(
                        TaskState.ERROR,
                        f"磁盘空间不足：需要约 {human_bytes(self.total_size)}，"
                        f"当前剩余 {human_bytes(free)}")
                    return
            except OSError:
                pass
            if self.allocator is None:
                self.allocator = SegmentAllocator(
                    self.total_size, self.settings.block_size)
            fresh = not os.path.exists(self.tmp_path)
            if fresh:
                with open(self.tmp_path, "wb") as f:
                    f.truncate(self.total_size)
                if getattr(self.settings, "preallocate", False):
                    if preallocate(self.tmp_path, self.total_size):
                        self.emit_event("info", "已预分配磁盘空间")
        else:
            self.resumable = False
            with open(self.tmp_path, "wb"):
                pass

        self._start_flusher()
        self.set_state(TaskState.DOWNLOADING)
        try:
            if self.resumable:
                error = self._run_segmented()
            else:
                error = self._run_single()
        finally:
            self._stop_flusher.set()
            if self._flusher:
                self._flusher.join(timeout=2)

        if self.pause_event.is_set():
            self.flush_meta()
            self.set_state(TaskState.PAUSED)
            return
        if error:
            self.flush_meta()
            self.set_state(TaskState.ERROR, error)
            return

        self.flush_meta()
        if not self._run_checksum(self.tmp_path):
            return
        try:
            if os.path.exists(self.final_path):
                os.remove(self.final_path)
            os.replace(self.tmp_path, self.final_path)
            if os.path.exists(self.meta_path):
                os.remove(self.meta_path)
        except OSError as exc:
            self.set_state(TaskState.ERROR, f"保存文件失败: {exc}")
            return
        self.completed_at = time.time()
        self.set_state(TaskState.COMPLETED)

    def _retr_range(self, ftp, offset, length, fh):
        """下载 [offset, offset+length)，写入文件句柄当前位置由调用方 seek。"""
        ftp.voidcmd("TYPE I")
        # 部分 FTPS 服务器（实测 pyftpdlib）在“REST 0 + TLS 数据通道”会卡死，
        # 从头开始的首块用不带 REST 的 RETR 更兼容。
        if self.info["tls"] and offset == 0:
            sock = ftp.transfercmd(f"RETR {self.info['path']}")
        else:
            sock = ftp.transfercmd(f"RETR {self.info['path']}", rest=offset)
        got = 0
        try:
            while got < length:
                if self.pause_event.is_set():
                    break
                chunk = sock.recv(min(WRITE_CHUNK, length - got))
                if not chunk:
                    break
                fh.write(chunk)
                got += len(chunk)
                self.meter.add_bytes(len(chunk))
                self.limiter.consume(len(chunk))
        finally:
            try:
                sock.close()
            except OSError:
                pass
        # FTPS 分段模式下每个分块使用独立连接、读完即弃（见 _run_segmented），
        # 非末块不去读 226 应答（数据未传完，应答时序不固定），直接关闭连接。
        last_chunk = (not self.total_size) or (offset + length >= self.total_size)
        if not (self.info["tls"] and not last_chunk):
            try:
                ftp.voidresp()  # 读取 226/225 完成应答
            except ftplib.Error:
                pass
        return got

    def _run_segmented(self):
        n = max(1, min(self.effective_connections, 32))
        errors: list[str] = []
        lock = threading.Lock()

        def worker(wid):
            tls = self.info["tls"]
            ftp = None
            fh = open(self.tmp_path, "r+b")
            try:
                if not tls:
                    ftp = self._connect()  # 明文复用同一控制连接
                while not self.pause_event.is_set():
                    block = self.allocator.claim()
                    if block is None:
                        return
                    start, end = block
                    length = end - start
                    got = 0
                    attempts = 0
                    while got < length and not self.pause_event.is_set():
                        try:
                            if tls:
                                # FTPS：每个分块一条全新加密连接，读完即弃，
                                # 规避在同一 TLS 控制连接上多次数据传输的兼容问题
                                self._safe_close(ftp)
                                ftp = self._connect()
                            elif ftp is None:
                                ftp = self._connect()
                            fh.seek(start + got)
                            got += self._retr_range(
                                ftp, start + got, length - got, fh)
                            if tls:
                                self._safe_close(ftp)
                                ftp = None
                        except (ftplib.Error, OSError, socket.timeout,
                                ssl.SSLError) as exc:
                            attempts += 1
                            self._safe_close(ftp)
                            ftp = None
                            if attempts > self.settings.retries:
                                with lock:
                                    errors.append(f"分段 {start} 重试耗尽: {exc}")
                                self.allocator.release(start, end)
                                return
                            time.sleep(min(2 ** attempts, 20))
                            if not tls:
                                try:
                                    ftp = self._connect()
                                except (ftplib.Error, OSError) as exc2:
                                    with lock:
                                        errors.append(str(exc2))
                                    self.allocator.release(start, end)
                                    return
                    if got < length:
                        self.allocator.release(start, end)
                        return
                    self.allocator.mark_done(start, end)
                    with lock:
                        self.meter_total = self.allocator.done_bytes()
                    self._meta_dirty.set()
            except (ftplib.Error, OSError) as exc:
                with lock:
                    errors.append(str(exc))
            finally:
                try:
                    fh.close()
                except OSError:
                    pass
                self._safe_close(ftp)

        threads = [threading.Thread(target=worker, args=(w,), daemon=True,
                                    name=f"ftp-{self.task_id[:6]}-{w}")
                   for w in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            return errors[0]
        if not self.allocator.is_complete():
            return "部分分段缺失，下载不完整"
        return None

    def _run_single(self):
        try:
            ftp = self._connect()
        except (ftplib.Error, OSError, ssl.SSLError) as exc:
            return f"FTP 连接失败: {exc}"
        try:
            ftp.voidcmd("TYPE I")
            sock = ftp.transfercmd(f"RETR {self.info['path']}")
            total = 0
            with open(self.tmp_path, "wb") as fh:
                while not self.pause_event.is_set():
                    chunk = sock.recv(WRITE_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    self.meter.add_bytes(len(chunk))
                    self.limiter.consume(len(chunk))
            sock.close()
            ftp.voidresp()
            self.total_size = total
            self.meter_total = total
            return None
        except (ftplib.Error, OSError, socket.timeout, ssl.SSLError) as exc:
            return f"FTP 下载失败: {exc}"
        finally:
            self._safe_close(ftp)

    def _run_checksum(self, path):
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
                f"{algo.upper()} 校验和不匹配（实际 {actual[:16]}…）")
            return False
        self.emit_event("info", f"{algo.upper()} 校验通过")
        return True

    @staticmethod
    def _safe_close(ftp):
        if ftp is None:
            return
        try:
            ftp.close()
        except Exception:  # noqa: BLE001
            pass
