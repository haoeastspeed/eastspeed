# -*- coding: utf-8 -*-
"""HLS(m3u8) 下载：playlist 解析、AES-128 解密、多线程分片、合并。

支持：
- Master playlist（自动选最高码率 variant）；
- Media playlist：MPEG-TS 与 fMP4（EXT-X-MAP + m4s，含 BYTERANGE）；
- AES-128-CBC 加密（未给 IV 时按媒体序号生成）；
- 分片多线程下载、断点续传（meta 记录已完成分片，密钥内嵌保存）；
- 合并：优先 ffmpeg remux 为 mp4，缺失时 TS 直拼为 .ts、fMP4 直拼为 .mp4。

不支持（明确报错）：SAMPLE-AES、无 ENDLIST 的直播流。
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

import requests

from .category import category_dir_for
from .checksum import normalize_algo, verify_file
from .errors import DownloadError, FatalHttpError
from .probe import build_session, session_kwargs
from .speed import RateLimiter, SpeedMeter
from .task import META_SUFFIX, TaskState, state_cn
from .task_options import TaskOptions

PARTS_DIR_SUFFIX = ".pdlhls"
WRITE_CHUNK = 64 * 1024
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def looks_like_hls(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith(".m3u8")


# ---------- 数据结构 ----------

@dataclass
class Segment:
    url: str
    duration: float = 0.0
    key_b64: str | None = None      # AES-128 密钥（base64），None 表示不加密
    iv_hex: str | None = None       # 16 字节 IV（hex），None 时用媒体序号
    is_init: bool = False           # EXT-X-MAP 初始化段
    byterange: tuple[int, int] | None = None  # (offset, length)

    def to_dict(self):
        return {"url": self.url, "duration": self.duration,
                "key_b64": self.key_b64, "iv_hex": self.iv_hex,
                "is_init": self.is_init, "byterange": self.byterange}

    @classmethod
    def from_dict(cls, d):
        return cls(url=d["url"], duration=d.get("duration", 0.0),
                   key_b64=d.get("key_b64"), iv_hex=d.get("iv_hex"),
                   is_init=d.get("is_init", False),
                   byterange=tuple(d["byterange"]) if d.get("byterange") else None)


@dataclass
class Playlist:
    is_master: bool = False
    variants: list[tuple[int, str]] = field(default_factory=list)  # (bandwidth, uri)
    segments: list[Segment] = field(default_factory=list)
    media_sequence: int = 0
    live: bool = False
    container: str = "ts"          # "ts" 或 "mp4"(fMP4)


# ---------- 解析 ----------

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("([^"]*)"|([^,]+))')


def _parse_attrs(line: str) -> dict:
    out = {}
    for m in _ATTR_RE.finditer(line):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(4).strip()
    return out


def parse_m3u8(text: str, base_url: str, key_cache: dict[str, bytes],
               session_getter) -> Playlist:
    """解析 m3u8 文本。key_cache 缓存已下载密钥；session_getter() 返回 session。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise DownloadError("不是有效的 m3u8 文件（缺少 #EXTM3U）")

    pl = Playlist()
    is_master = any(ln.startswith("#EXT-X-STREAM-INF") for ln in lines)
    pl.is_master = is_master
    if is_master:
        pending_bw = 0
        for ln in lines:
            if ln.startswith("#EXT-X-STREAM-INF"):
                attrs = _parse_attrs(ln)
                try:
                    pending_bw = int(attrs.get("BANDWIDTH", "0"))
                except ValueError:
                    pending_bw = 0
            elif not ln.startswith("#"):
                pl.variants.append((pending_bw, urllib.parse.urljoin(base_url, ln)))
                pending_bw = 0
        return pl

    # Media playlist
    pl.live = not any(ln.startswith("#EXT-X-ENDLIST") for ln in lines)
    seq_m = [ln for ln in lines if ln.startswith("#EXT-X-MEDIA-SEQUENCE")]
    if seq_m:
        try:
            pl.media_sequence = int(seq_m[0].split(":", 1)[1].strip())
        except (ValueError, IndexError):
            pl.media_sequence = 0

    method = "NONE"
    key_uri = None
    iv_hex = None
    pending_map: Segment | None = None
    pending_duration = 0.0
    seq = pl.media_sequence

    def resolve_key(uri):
        if uri in key_cache:
            return key_cache[uri]
        sess = session_getter()
        resp = sess.get(urllib.parse.urljoin(base_url, uri),
                        **session_kwargs(sess.cfg))
        if resp.status_code >= 400:
            raise DownloadError(f"密钥下载失败 HTTP {resp.status_code}")
        key = resp.content
        if len(key) != 16:
            raise DownloadError("AES-128 密钥长度不是 16 字节")
        key_cache[uri] = key
        return key

    for ln in lines:
        if ln.startswith("#EXT-X-KEY"):
            attrs = _parse_attrs(ln)
            method = attrs.get("METHOD", "NONE")
            if method == "SAMPLE-AES":
                raise DownloadError("SAMPLE-AES 加密暂不支持")
            if method == "AES-128":
                key_uri = attrs.get("URI")
                iv = attrs.get("IV")
                iv_hex = iv[2:] if iv and iv.lower().startswith("0x") else iv
            else:
                key_uri, iv_hex = None, None
        elif ln.startswith("#EXT-X-MAP"):
            attrs = _parse_attrs(ln)
            seg = Segment(url=urllib.parse.urljoin(base_url, attrs["URI"]),
                          is_init=True)
            if "BYTERANGE" in attrs:
                seg.byterange = _parse_byterange(attrs["BYTERANGE"])
            if method == "AES-128" and key_uri:
                key = resolve_key(key_uri)
                seg.key_b64 = base64.b64encode(key).decode()
                seg.iv_hex = iv_hex
            pending_map = seg
            pl.container = "mp4"
        elif ln.startswith("#EXTINF"):
            try:
                pending_duration = float(ln.split(":", 1)[1].split(",")[0])
            except (ValueError, IndexError):
                pending_duration = 0.0
        elif ln.startswith("#"):
            continue
        else:
            # 媒体分片 URI（可能带 BYTERANGE，fMP4 常见）
            seg = Segment(url=urllib.parse.urljoin(base_url, ln),
                          duration=pending_duration)
            range_attr = None
            # BYTERANGE 通常写在 URI 前一行的非标准标签里，标准为 EXT-X-BYTERANGE
            pending_duration = 0.0
            if method == "AES-128" and key_uri:
                key = resolve_key(key_uri)
                seg.key_b64 = base64.b64encode(key).decode()
                seg.iv_hex = iv_hex or f"{seq:032x}"
            if pending_map is not None:
                pl.segments.append(pending_map)
                pending_map = None
            pl.segments.append(seg)
            seq += 1

    # fMP4：去掉重复 init（EXT-X-MAP 只在切换时出现，通常一个）
    seen_init = set()
    uniq = []
    for s in pl.segments:
        if s.is_init and s.url in seen_init:
            continue
        if s.is_init:
            seen_init.add(s.url)
        uniq.append(s)
    pl.segments = uniq
    return pl


def _parse_byterange(value: str) -> tuple[int, int]:
    # "1024@0" => (offset, length)；"1024"（续接）简化要求显式 offset
    parts = value.split("@")
    length = int(parts[0])
    offset = int(parts[1]) if len(parts) > 1 else 0
    return offset, length


# ---------- 解密 ----------

def decrypt_segment(data: bytes, seg: Segment) -> bytes:
    if not seg.key_b64:
        return data
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(seg.key_b64)
    iv = bytes.fromhex(seg.iv_hex) if seg.iv_hex else b"\x00" * 16
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    raw = decryptor.update(data) + decryptor.finalize()
    try:
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(raw) + unpadder.finalize()
    except ValueError:
        # 个别流不对分片做 padding，直接返回原始解密结果
        return raw


# ---------- HLS 任务 ----------

class HlsTask:
    """与 DownloadTask 接口对齐的 HLS 下载任务。"""

    def __init__(self, settings, url: str, options: TaskOptions | None = None,
                 task_id: str | None = None, callbacks=None):
        import uuid
        self.task_id = task_id or uuid.uuid4().hex[:12]
        self.url = url
        self.settings = settings
        self.options = options or TaskOptions()
        cfg = _HlsConfigView(settings, self.options)
        self.cfg = cfg
        self.callbacks = callbacks or {}

        self.save_dir = self.options.save_dir or settings.save_dir
        self.filename = self.options.filename or ""
        self.final_path = ""
        self.parts_dir = ""
        self.meta_path = ""
        self.tmp_path = ""  # 兼容引擎删除逻辑（指向分片目录）

        self.state = TaskState.QUEUED
        self.error_msg = ""
        self.created_at = time.time()
        self.completed_at = None

        self.playlist: Playlist | None = None
        self.segments: list[Segment] = []
        self._completed: set[int] = set()
        self._downloaded_bytes = 0
        self.meter = SpeedMeter()
        self.limiter = RateLimiter(settings.speed_limit)
        self.container = "ts"
        self.effective_connections = settings.connections
        self.checksum_actual = ""
        self.checksum_ok: bool | None = None

        self.pause_event = threading.Event()
        self._state_lock = threading.Lock()
        self._run_lock = threading.Lock()
        self._lock = threading.Lock()
        self._retry = {}
        self._session = None
        self._key_cache: dict[str, bytes] = {}
        self._dirty = threading.Event()
        self._stop_flusher = threading.Event()
        self._flusher = None

    # ----- 状态/回调 -----
    def set_state(self, state, msg=""):
        with self._state_lock:
            self.state = state
            self.error_msg = msg  # 未显式传消息时清空，避免残留“合并中”等
        cb = self.callbacks.get("on_state")
        if cb:
            cb(self, state, msg)

    def emit_event(self, level, message):
        cb = self.callbacks.get("on_event")
        if cb:
            cb(self, level, message)

    def speed(self):
        return self.meter.speed()

    def total_size(self):
        if not self.segments:
            return 0
        done = len(self._completed)
        if done == 0:
            return 0
        # 按已下载分片平均大小估算总量
        return int(self._downloaded_bytes * len(self.segments) / done)

    def downloaded(self):
        return self._downloaded_bytes

    def progress(self):
        if not self.segments:
            return 0.0
        return len(self._completed) / len(self.segments)

    def eta_seconds(self):
        spd, total, done = self.speed(), self.total_size(), self._downloaded_bytes
        if not total or spd < 1024:
            return -1
        return int(max(0, total - done) / spd)

    def snapshot(self):
        with self._state_lock:
            state, error = self.state, self.error_msg
        return {
            "task_id": self.task_id, "url": self.url,
            "filename": self.filename or self.url,
            "size": self.total_size(), "downloaded": self._downloaded_bytes,
            "progress": self.progress(), "speed": self.speed(),
            "eta": self.eta_seconds(), "state": state,
            "state_cn": state_cn(state), "error": error,
            "save_dir": self.save_dir, "final_path": self.final_path,
            "created_at": self.created_at,
            "checksum_algo": normalize_algo(self.options.checksum_algo),
            "checksum_actual": self.checksum_actual,
            "checksum_ok": self.checksum_ok,
        }

    def _run_checksum(self, path: str) -> bool:
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
                f"{algo.upper()} 校验和不匹配，合并文件可能损坏（实际 {actual[:16]}…）",
            )
            return False
        self.emit_event("info", f"{algo.upper()} 校验通过")
        return True

    # ----- 外部控制 -----
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
        if self.parts_dir and os.path.isdir(self.parts_dir):
            shutil.rmtree(self.parts_dir, ignore_errors=True)
        for p in (self.meta_path, self.final_path):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        self._completed.clear()
        self._downloaded_bytes = 0
        self.meter.reset()
        self.error_msg = ""
        self.playlist = None
        self.segments = []
        self.pause_event.clear()

    # ----- 路径/meta -----
    def _prepare_paths(self):
        os.makedirs(self.save_dir, exist_ok=True)
        if not self.filename:
            stem = os.path.basename(urllib.parse.urlparse(self.url).path) or "video"
            if stem.lower().endswith(".m3u8"):
                stem = stem[:-5]
            self.filename = stem + ".m3u8"
        base = os.path.splitext(self.filename)[0]
        final = os.path.join(self.save_dir, base + "." + self.container)
        i = 1
        while os.path.exists(final):
            final = os.path.join(self.save_dir, f"{base} ({i}).{self.container}")
            i += 1
        self.final_path = final
        self.parts_dir = final + PARTS_DIR_SUFFIX
        self.meta_path = final + META_SUFFIX
        self.tmp_path = self.parts_dir
        os.makedirs(self.parts_dir, exist_ok=True)

    def _meta_dict(self):
        return {
            "kind": "hls", "version": 1, "task_id": self.task_id,
            "url": self.url, "save_dir": self.save_dir,
            "filename": self.filename, "final_path": self.final_path,
            "parts_dir": self.parts_dir, "container": self.container,
            "segments": [s.to_dict() for s in self.segments],
            "completed": sorted(self._completed),
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
        self._dirty.clear()

    @classmethod
    def from_meta(cls, settings, meta, callbacks=None):
        opt = TaskOptions(save_dir=meta.get("save_dir"),
                          filename=meta.get("filename"))
        task = cls(settings, meta["url"], opt,
                   task_id=meta.get("task_id"), callbacks=callbacks)
        task.final_path = meta.get("final_path", "")
        task.parts_dir = meta.get("parts_dir", "")
        task.tmp_path = task.parts_dir
        task.meta_path = os.path.join(
            os.path.dirname(task.final_path),
            os.path.basename(task.final_path) + META_SUFFIX)
        task.container = meta.get("container", "ts")
        task.segments = [Segment.from_dict(d) for d in meta.get("segments", [])]
        task._completed = set(meta.get("completed", []))
        task.created_at = meta.get("created_at", time.time())
        return task

    # ----- 主流程 -----
    def run(self):
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            self._run_until_idle()
        except Exception as exc:
            self.set_state(TaskState.ERROR, f"内部错误: {exc}")
        finally:
            self._run_lock.release()

    def _resolve_runtime(self):
        """探测后确定实际连接数与自动分类目录（HLS 一律归视频）。"""
        if self.options.connections:
            self.effective_connections = self.options.connections
        else:
            self.effective_connections = (
                self.settings.connections_for(self.url) or self.settings.connections
            )
        if (not self.meta_path and not self.options.save_dir
                and getattr(self.settings, "auto_categorize", False)):
            base = os.path.splitext(self.filename or "video")[0] or "video"
            self.save_dir = category_dir_for(base + ".mp4", True, self.settings.save_dir)

    def _session_getter(self):
        return self._session

    def _run_until_idle(self):
        self.limiter = RateLimiter(self.settings.speed_limit)
        self.set_state(TaskState.CONNECTING)

        if not self.segments:
            try:
                self._load_playlist()
            except DownloadError as exc:
                self.set_state(TaskState.ERROR, str(exc))
                return
        self._resolve_runtime()
        self._prepare_paths()

        self._start_flusher()
        self.set_state(TaskState.DOWNLOADING)
        try:
            self._download_all()
        finally:
            self._stop_flusher.set()
            if self._flusher:
                self._flusher.join(timeout=2)
            if self._session:
                self._session.close()

        if self.pause_event.is_set():
            self.flush_meta()
            self.set_state(TaskState.PAUSED)
            return
        if self.state == TaskState.ERROR:
            self.flush_meta()
            return

        self.set_state(TaskState.CONNECTING, "合并中")
        try:
            self._merge()
        except DownloadError as exc:
            self.set_state(TaskState.ERROR, str(exc))
            return

        if not self._run_checksum(self.final_path):
            return

        shutil.rmtree(self.parts_dir, ignore_errors=True)
        if os.path.exists(self.meta_path):
            try:
                os.remove(self.meta_path)
            except OSError:
                pass
        self.completed_at = time.time()
        self.set_state(TaskState.COMPLETED)

    def _http_get_text(self, url):
        sess = build_session(self.cfg)
        sess.cfg = self.cfg
        self._session = sess
        resp = sess.get(url, headers={"Accept-Encoding": "identity"},
                        **session_kwargs(self.cfg))
        if resp.status_code >= 400:
            raise DownloadError(f"播放列表下载失败 HTTP {resp.status_code}")
        resp.encoding = resp.encoding or "utf-8"
        return resp.text, resp.url

    def _load_playlist(self):
        self._session = build_session(self.cfg)
        self._session.cfg = self.cfg
        text, final_url = self._http_get_text(self.url)
        pl = parse_m3u8(text, final_url, self._key_cache, self._session_getter)
        if pl.is_master:
            if not pl.variants:
                raise DownloadError("Master 播放列表中没有可用码率")
            pl.variants.sort(key=lambda x: x[0])
            best = pl.variants[-1][1]
            self.emit_event("info", f"检测到多码率，选择最高码率 ({pl.variants[-1][0]})")
            text, final_url = self._http_get_text(best)
            pl = parse_m3u8(text, final_url, self._key_cache,
                            self._session_getter)
        if pl.live:
            raise DownloadError("直播流（无 EXT-X-ENDLIST）暂不支持下载")
        if not pl.segments:
            raise DownloadError("播放列表中没有分片")
        self.playlist = pl
        self.segments = pl.segments
        self.container = pl.container
        # 用户显式指定文件名时尊重其主名
        if self.options.filename:
            self.filename = self.options.filename

    # ----- 分片下载 -----
    def _download_all(self):
        n_workers = max(1, min(self.effective_connections, len(self.segments)))
        q: queue.Queue[int] = queue.Queue()
        for i in range(len(self.segments)):
            if i not in self._completed:
                q.put(i)
        errors: list[str] = []
        threads = []

        def worker(wid):
            sess = build_session(self.cfg)
            sess.cfg = self.cfg
            sess.headers.update({"Accept-Encoding": "identity"})
            while not self.pause_event.is_set():
                try:
                    idx = q.get_nowait()
                except queue.Empty:
                    return
                try:
                    self._download_segment(sess, idx)
                except FatalHttpError as exc:
                    errors.append(str(exc))
                    return
                except (requests.RequestException, DownloadError) as exc:
                    q.put(idx)
                    if not self._retry_wait(wid, str(exc)):
                        errors.append(f"分片 {idx} 重试耗尽: {exc}")
                        return
                    sess.close()
                    sess = build_session(self.cfg)
                    sess.cfg = self.cfg
                    sess.headers.update({"Accept-Encoding": "identity"})
            sess.close()

        for wid in range(n_workers):
            t = threading.Thread(target=worker, args=(wid,), daemon=True,
                                 name=f"hls-{self.task_id[:6]}-{wid}")
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        if errors:
            self.set_state(TaskState.ERROR, errors[0])
            return
        if not self.pause_event.is_set() and len(self._completed) != len(self.segments):
            self.set_state(TaskState.ERROR, "部分分片缺失，下载不完整")

    def _download_segment(self, sess, idx: int):
        seg = self.segments[idx]
        part = os.path.join(self.parts_dir, f"part_{idx:06d}")
        if idx in self._completed and os.path.exists(part):
            return
        headers = {"Accept-Encoding": "identity"}
        if seg.byterange:
            offset, length = seg.byterange
            headers["Range"] = f"bytes={offset}-{offset + length - 1}"
        resp = sess.get(seg.url, headers=headers, stream=True,
                        **session_kwargs(self.cfg))
        if resp.status_code in RETRYABLE_STATUS:
            raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)
        if resp.status_code in (401, 403, 404):
            raise FatalHttpError(f"分片 {idx} 无法下载 HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise requests.HTTPError(f"HTTP {resp.status_code}", response=resp)

        buf = bytearray()
        for chunk in resp.iter_content(WRITE_CHUNK):
            if self.pause_event.is_set():
                resp.close()
                return
            if not chunk:
                continue
            buf.extend(chunk)
            self.meter.add_bytes(len(chunk))
            self.limiter.consume(len(chunk))
        resp.close()

        data = decrypt_segment(bytes(buf), seg)
        tmp = part + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, part)
        with self._lock:
            self._completed.add(idx)
            self._downloaded_bytes += len(data)
        self._dirty.set()

    def _retry_wait(self, wid, reason):
        n = self._retry.get(wid, 0)
        if n >= self.cfg.retries:
            return False
        self._retry[wid] = n + 1
        delay = min(2 ** n, 20)
        deadline = time.time() + delay
        while time.time() < deadline:
            if self.pause_event.is_set():
                return False
            time.sleep(0.2)
        return True

    # ----- 合并 -----
    def _ordered_parts(self):
        result = []
        for i, seg in enumerate(self.segments):
            if seg.is_init:
                continue
            part = os.path.join(self.parts_dir, f"part_{i:06d}")
            if os.path.exists(part):
                result.append((i, part, seg))
        result.sort(key=lambda x: x[0])
        return result

    def _ffmpeg_path(self):
        configured = getattr(self.settings, "ffmpeg_path", "") or ""
        if configured and os.path.exists(configured):
            return configured
        return shutil.which("ffmpeg")

    def _merge(self):
        parts = self._ordered_parts()
        if not parts:
            raise DownloadError("没有可合并的分片")
        ffmpeg = self._ffmpeg_path()

        # 最终扩展名：有 ffmpeg 统一 remux 为 mp4；否则按容器直拼
        if ffmpeg:
            target = os.path.splitext(self.final_path)[0] + ".mp4"
            self.final_path = target
            self.meta_path = target + META_SUFFIX
            if self._merge_ffmpeg(ffmpeg, parts):
                return
            self.emit_event("info", "ffmpeg 合并失败，回退直拼")

        if self.container == "mp4":
            # fMP4：init 段 + 媒体段直拼
            self._concat(parts, include_init=True)
        else:
            target = os.path.splitext(self.final_path)[0] + ".ts"
            self.final_path = target
            self.meta_path = target + META_SUFFIX
            self._concat(parts, include_init=False)

    def _merge_ffmpeg(self, ffmpeg, parts):
        """ffmpeg concat demuxer remux 为 mp4。"""
        list_path = os.path.join(self.parts_dir, "filelist.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for _, part, _seg in parts:
                f.write(f"file '{os.path.basename(part)}'\n")
        target = self.final_path
        cmd = [ffmpeg, "-y", "-f", "concat", "-safe", "0",
               "-i", "filelist.txt", "-c", "copy"]
        if self.container == "ts":
            cmd += ["-bsf:a", "aac_adtstoasc"]
        cmd.append(target)
        try:
            proc = subprocess.run(
                cmd, cwd=self.parts_dir,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=600,
            )
            return proc.returncode == 0 and os.path.exists(target)
        except (subprocess.SubprocessError, OSError):
            return False

    def _concat(self, parts, include_init):
        target_tmp = self.final_path + ".joining"
        with open(target_tmp, "wb") as out:
            if include_init:
                for i, seg in enumerate(self.segments):
                    if seg.is_init:
                        p = os.path.join(self.parts_dir, f"part_{i:06d}")
                        if os.path.exists(p):
                            with open(p, "rb") as f:
                                shutil.copyfileobj(f, out)
            for _, part, _seg in parts:
                with open(part, "rb") as f:
                    shutil.copyfileobj(f, out)
        os.replace(target_tmp, self.final_path)

    # ----- meta 后台落盘 -----
    def _start_flusher(self):
        self._stop_flusher.clear()

        def loop():
            while not self._stop_flusher.wait(2.0):
                if self._dirty.is_set():
                    self.flush_meta()

        self._flusher = threading.Thread(target=loop, daemon=True,
                                         name=f"hls-meta-{self.task_id[:6]}")
        self._flusher.start()


class _HlsConfigView:
    def __init__(self, settings, options):
        self._s = settings
        self.opt = options

    def __getattr__(self, name):
        val = getattr(self.opt, name, None)
        if val is not None:
            return val
        return getattr(self._s, name)
