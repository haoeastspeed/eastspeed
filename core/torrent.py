# -*- coding: utf-8 -*-
"""BT / 磁力下载（libtorrent 可选适配）。

- 监听端口使用 6881-6991 范围，多任务/端口被占时自动选择可用端口；
- 启用 DHT（含公共引导节点）、UPnP / NAT-PMP / LSD，纯磁力（无 tracker）
  在 NAT/内网后也能尽量找到 peer；
- 支持种子内文件勾选（.torrent 添加前预选；磁力在拿到元数据后应用）；
- 顺序下载（边下边播友好），下完即止（不长期做种）；
- 提供 close() 释放 libtorrent 会话与端口，删除任务时由引擎调用。
"""
from __future__ import annotations

import os
import time
from urllib.parse import urlparse

from .task import (
    DownloadTask, TaskOptions, TaskState, FileInfo,
    DownloadError,
)
from . import checksum

try:  # libtorrent 为可选依赖，缺失时给出清晰引导
    import libtorrent as lt
    _HAS_LT = True
    MISSING_REASON = ""
except Exception as _e:  # pragma: no cover - 取决于运行环境
    lt = None
    _HAS_LT = False
    MISSING_REASON = (
        "BT/磁力下载需要可选依赖 libtorrent。便携版/安装包已内置；"
        "源码运行请执行：pip install libtorrent"
    )


def libtorrent_available() -> bool:
    return _HAS_LT


_MAGNET_PREFIXES = ("magnet:?",)
_TORRENT_SUFFIXES = (".torrent",)


def is_torrent_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if u.startswith(_MAGNET_PREFIXES):
        return True
    path = urlparse(u).path.lower()
    return path.endswith(_TORRENT_SUFFIXES)


# 公共 DHT 引导节点（无 tracker 的纯磁力靠它进入 DHT 网络）
_DHT_ROUTERS = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.libtorrent.org", 25401),
]

# 监听端口范围：多任务、端口占用时在范围内自动选择
_LISTEN_RANGE = "6881-6991"


def _build_session():
    """创建带 DHT/UPnP/NAT-PMP/LSD 与必要扩展的 libtorrent 会话。"""
    mask = 0
    for name in ("status_notification", "error_notification",
                 "storage_notification", "tracker_notification",
                 "peer_notification"):
        cat = getattr(getattr(lt.alert, "category_t", object), name, None)
        if cat is not None:
            mask |= int(cat)
    cfg = {
        "listen_interfaces": f"0.0.0.0:{_LISTEN_RANGE},[::]:{_LISTEN_RANGE}",
        "enable_dht": True,
        "enable_lsd": True,          # 本地节点发现
        "enable_upnp": True,         # 路由器端口映射
        "enable_natpmp": True,
        "alert_mask": mask,
    }
    session = lt.session(cfg)
    # 增强项逐项容错应用，避免个别键名在不同版本不可用而整体失败
    for key, val in {
        "connections_limit": 200,
        "active_downloads": -1,
        "active_seeds": 1,
        "announce_to_all_trackers": True,
        "announce_to_all_tiers": True,
        # 下完即止，不长期占用上行带宽做种
        "seed_time_limit": 30,
        "seed_time_ratio_limit": 0,
    }.items():
        try:
            session.apply_settings({key: val})
        except Exception:
            pass
    for ext in ("create_ut_metadata_plugin", "create_ut_pex_plugin",
                "create_smart_ban_plugin"):
        factory = getattr(lt, ext, None)
        if factory is not None:
            try:
                session.add_extension(factory)
            except Exception:
                pass
    for host, port in _DHT_ROUTERS:
        try:
            session.add_dht_router(host, port)
        except Exception:
            pass
    try:
        session.start_dht()
    except Exception:
        pass
    return session


def _file_priorities(ti, selected: list[str] | None) -> list[int]:
    """根据勾选的文件名/相对路径生成每文件优先级（0=跳过，1=下载）。

    一个都没匹配上时回退为全下载，避免用户误填导致空任务。
    """
    n = ti.files().num_files()
    if not selected:
        return [1] * n
    wanted = [s.replace("\\", "/").lower().strip("/") for s in selected if s]
    pri = []
    for i in range(n):
        rel = ti.files().file_path(i).replace("\\", "/")
        base = os.path.basename(rel).lower()
        hit = any(w == rel or w == base or w in rel for w in wanted)
        pri.append(1 if hit else 0)
    return pri if any(pri) else [1] * n


class TorrentTask(DownloadTask):
    """libtorrent 任务（.torrent 文件 URL/本地路径或 magnet 链接）。"""

    def __init__(self, settings, url, options=None, task_id=None, callbacks=None):
        super().__init__(settings, url, options, task_id, callbacks)
        self._session = None
        self._handle = None
        self._closed = False
        self._meta_applied = False
        self._ti = None
        self._nb_done = -1
        # BT 实时统计（total_wanted / total_wanted_done / download_rate）
        self._bt_total = 0
        self._bt_done = 0
        self._bt_speed = 0
        # BT 直接落盘到目标目录（libtorrent 自行管理分片与断点续传）
        self.allocator = None

    # ---------- 统计（覆盖基类，供 snapshot/进度条使用）----------
    def total_size(self) -> int:
        if self._bt_total:
            return self._bt_total
        if self.file_info and self.file_info.total_size:
            return self.file_info.total_size
        return 0

    def downloaded(self) -> int:
        return self._bt_done

    def speed(self) -> float:
        return float(self._bt_speed)

    def _fail(self, err: Exception) -> None:
        self.set_state(TaskState.ERROR, str(err))

    # ---------- 生命周期 ----------
    def run(self) -> None:
        if not _HAS_LT:
            self._fail(DownloadError(MISSING_REASON))
            return
        try:
            self.set_state(TaskState.CONNECTING)
            if self._session is None:
                self._add()
            else:
                # 暂停后恢复：复用已有会话/句柄
                self._resume_handle()
            self._run_loop()
        except DownloadError as e:
            self._fail(e)
        except Exception as e:  # noqa: BLE001 - 统一进入错误态
            self._fail(DownloadError(f"BT 任务失败：{e}"))

    def pause(self, timeout: float = 30.0) -> None:
        try:
            if self._handle is not None and self._session is not None:
                self._handle.pause()
        except Exception:
            pass
        super().pause(timeout)

    def close(self) -> None:
        """释放 libtorrent 句柄/会话与监听端口（幂等）。"""
        if self._closed:
            return
        self._closed = True
        session, handle = self._session, self._handle
        self._handle = None
        self._session = None
        if session is not None:
            try:
                if handle is not None and handle.is_valid():
                    session.remove_torrent(handle)
            except Exception:
                pass
            try:
                del session
            except Exception:
                pass

    def reset_for_restart(self) -> None:
        """重下：关闭旧会话，清空句柄，让 run() 重新添加。"""
        self.close()
        self._closed = False
        self._meta_applied = False
        self._ti = None
        self.meter.reset()
        self._downloaded = 0
        self.error_msg = ""
        self.pause_event.clear()
        self.fallback_event.clear()

    # ---------- 添加种子/磁力 ----------
    def _add(self) -> None:
        self._session = _build_session()
        url = self.url.strip()
        if url.lower().startswith("magnet:"):
            sparse = lt.storage_mode_t.storage_mode_sparse
            p = lt.parse_magnet_uri(url)
            if isinstance(p, dict):  # 旧版本返回 dict
                p["save_path"] = self.save_dir
                p["storage_mode"] = sparse
                self.filename = p.get("name") or ""
            else:                   # 2.x 返回 add_torrent_params
                p.save_path = self.save_dir
                p.storage_mode = sparse
                self.filename = getattr(p, "name", None) or ""
            self._handle = self._session.add_torrent(p)
        else:
            data = self._fetch_torrent(url)
            ti = lt.torrent_info(lt.bdecode(data))
            self._ti = ti
            params = {
                "ti": ti,
                "save_path": self.save_dir,
                "storage_mode": lt.storage_mode_t.storage_mode_sparse,
                "file_priorities": _file_priorities(
                    ti, self.options.bt_files),
            }
            self._handle = self._session.add_torrent(params)
            self._set_names_from_ti(ti)
        try:
            self._handle.set_sequential_download(True)
        except Exception:
            pass
        self._apply_extra_trackers()

    def _resume_handle(self) -> None:
        # 恢复会话（close 后 _session 为 None，应由 reset 路径处理）
        if self._session is None:
            self._add()
            return
        try:
            if self._handle is not None and self._handle.is_valid():
                self._handle.resume()
                self._handle.set_sequential_download(True)
        except Exception:
            pass

    def _fetch_torrent(self, url: str) -> bytes:
        if os.path.exists(url):  # 本地 .torrent
            with open(url, "rb") as f:
                return f.read()
        from .probe import build_session, session_kwargs
        sess = build_session(self.cfg)
        try:
            resp = sess.get(url, **session_kwargs(self.cfg))
            resp.raise_for_status()
            return resp.content
        finally:
            close = getattr(sess, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _apply_extra_trackers(self) -> None:
        trackers = self.options.bt_trackers or []
        if not trackers or self._handle is None:
            return
        try:
            existing = {tr.get("url") if isinstance(tr, dict) else tr
                        for tr in (self._handle.trackers() or [])}
            for t in trackers:
                if not t or t in existing:
                    continue
                try:
                    self._handle.add_tracker({"url": t})
                except Exception:
                    try:
                        self._handle.add_tracker(t)
                    except Exception:
                        pass
        except Exception:
            pass

    def _set_names_from_ti(self, ti) -> None:
        name = ti.name()
        self.filename = name
        if ti.files().num_files() == 1:
            self.final_path = os.path.join(self.save_dir,
                                           ti.files().file_path(0))
        else:
            self.final_path = os.path.join(self.save_dir, name)
        self.file_info = FileInfo(
            url=self.url, final_url=self.url,
            total_size=ti.total_size(), resumable=True,
            filename=self.filename,
        )

    # ---------- 主循环 ----------
    def _drain_alerts(self) -> str | None:
        """处理 libtorrent 告警，返回错误信息（无错误返回 None）。"""
        if self._session is None:
            return None
        try:
            alerts = self._session.pop_alerts()
        except Exception:
            return None
        err = None
        for a in alerts:
            kind = type(a).__name__
            if kind == "metadata_received_alert" and self._handle is not None:
                self._on_metadata()
            elif kind in ("file_error_alert", "torrent_error_alert",
                          "metadata_failed_alert", "add_torrent_error_alert",
                          "torrent_delete_failed_alert"):
                msg = ""
                for attr in ("error", "msg", "message"):
                    v = getattr(a, attr, None)
                    if v:
                        msg = str(v)
                        break
                if not msg:
                    msg = kind
                err = msg
        return err

    def _on_metadata(self) -> None:
        if self._meta_applied or self._handle is None:
            return
        try:
            st = self._handle.status()
            ti = getattr(st, "torrent_file", None)
            if ti is None:
                return
            self._ti = ti
            self._meta_applied = True
            self._set_names_from_ti(ti)
            if self.options.bt_files:
                pri = _file_priorities(ti, self.options.bt_files)
                self._handle.prioritize_files(pri)
            self._handle.set_sequential_download(True)
            self.emit_event("info", "已获取种子元数据")
        except Exception:
            pass

    def _run_loop(self) -> None:
        last = time.time()
        no_progress = 0.0
        while not self._closed:
            if self.state == TaskState.PAUSED:
                return  # 暂停：保留会话，等待 resume 重新进入 run
            now = time.time()
            dt = now - last
            last = now

            err = self._drain_alerts()
            if err:
                raise DownloadError(f"BT 错误：{err}")
            if self._handle is None or not self._handle.is_valid():
                raise DownloadError("种子添加失败（无效的磁力链接或种子文件）")
            st = self._handle.status()

            # 磁力拿到元数据前
            if st.torrent_file is None:
                self.set_state(TaskState.CONNECTING)
            else:
                if not self._meta_applied:
                    self._on_metadata()
                self.set_state(TaskState.DOWNLOADING)

            total = max(0, int(getattr(st, "total_wanted", 0) or 0))
            done = max(0, int(getattr(st, "total_wanted_done", 0) or 0))
            speed = max(0, int(getattr(st, "download_rate", 0) or 0))
            self._bt_total, self._bt_done, self._bt_speed = total, done, speed
            if total:
                self._downloaded = done
                # 仅在有元数据后计无进度：有 peer 却 5 分钟无进展才放弃；
                # 无 peer 不判死（BT 找种本就可能很慢）
                if st.torrent_file is not None:
                    if done == self._nb_done:
                        no_progress += dt
                    else:
                        no_progress = 0.0
                    self._nb_done = done
                    if st.num_peers > 0 and no_progress > 300:
                        raise DownloadError("长时间无下载进展，可稍后重试或更换种子")

            # 完成：进度满且已拿到元数据
            if st.torrent_file is not None and total and done >= total and \
                    st.progress >= 1.0:
                self._finish_bt()
                return

            # 暂停事件等待（带超时以继续刷新状态）
            if self.pause_event.wait(0.5):
                if not self._closed:
                    self.set_state(TaskState.PAUSED)
                return

    def _finish_bt(self) -> None:
        self._bt_done = self._bt_total
        self._downloaded = self._bt_total
        # 单文件哈希校验（多文件目录不校验整体）
        if (self.options.checksum_algo and self.final_path
                and os.path.isfile(self.final_path)):
            algo = self.options.checksum_algo
            actual = checksum.hash_file(self.final_path, algo)
            self.checksum_actual = actual
            expected = checksum.normalize_hex(self.options.checksum_expected)
            if expected and actual.lower() != expected:
                self.checksum_ok = False
                raise DownloadError(
                    f"{checksum.normalize_algo(algo).upper()} 校验失败："
                    f"期望 {expected}，实际 {actual}")
            self.checksum_ok = True
            self.emit_event("info",
                            f"{checksum.normalize_algo(algo).upper()} 校验通过：{actual}")
        self.set_state(TaskState.COMPLETED)
        self.completed_at = time.time()
