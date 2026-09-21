# -*- coding: utf-8 -*-
"""全局设置与持久化（%APPDATA%/DongFangSpeed/settings.json）。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field

from .branding import APP_ID, DOWNLOAD_DIR_NAME
from .category import host_of


def app_data_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_ID)
    os.makedirs(path, exist_ok=True)
    return path


def default_save_dir() -> str:
    home = os.path.expanduser("~")
    path = os.path.join(home, "Downloads", DOWNLOAD_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


@dataclass
class Settings:
    save_dir: str = field(default_factory=default_save_dir)
    # 每个任务的最大连接数
    connections: int = 16
    # 同时下载的任务数
    max_concurrent: int = 3
    # 全局限速，字节/秒，0 为不限速
    speed_limit: int = 0
    # HTTP/HTTPS 代理，如 http://127.0.0.1:7890
    proxy: str = ""
    # 代理认证方式：""（不认证）/ basic / ntlm / negotiate
    proxy_auth: str = ""
    proxy_username: str = ""
    proxy_password: str = ""
    # NTLM 域（可选）
    proxy_domain: str = ""
    # PAC 自动配置脚本地址（填写后按目标 URL 自动选择代理）
    pac_url: str = ""
    username: str = ""
    password: str = ""
    user_agent: str = ""
    referer: str = ""
    verify_ssl: bool = True
    connect_timeout: int = 10
    read_timeout: int = 30
    retries: int = 10
    # 单条连接连续多少秒收不到任何数据即判定卡死，强制重连并把该块交回池中
    stall_timeout: int = 15
    # 分段块大小（字节），也是断点续传的最小粒度
    block_size: int = 2 * 1024 * 1024
    # 按站点（域名）覆盖连接数，如 {"example.com": 4}；支持子域后缀匹配
    site_connections: dict = field(default_factory=dict)
    # 按文件类型自动归入 视频/音频/文档/程序/压缩包 子目录
    auto_categorize: bool = False
    # ffmpeg 可执行文件路径（留空则在 PATH 中查找）
    ffmpeg_path: str = ""
    # 下载前真实占盘式预分配（默认关：稀疏预分配更轻巧）
    preallocate: bool = False
    # 下载完成后调用系统杀毒软件扫描（找不到可用扫描器则静默跳过）
    av_scan: bool = True
    # 对支持 HTTP/2 的服务器优先使用 HTTP/2（需要可选依赖 httpx[h2]）
    prefer_http2: bool = False
    # GUI 行为
    clipboard_watch: bool = True
    minimize_to_tray: bool = True
    auto_resume: bool = True
    notify_on_complete: bool = True
    play_sound: bool = False
    # 计划任务：到点全部开始 / 全部暂停（HH:MM，24 小时制）
    scheduler_enabled: bool = False
    scheduler_start: str = ""
    scheduler_stop: str = ""
    # 浏览器扩展本地桥接（仅监听 127.0.0.1 + Token/自动配对），默认开启以便开箱即用
    bridge_enabled: bool = True
    bridge_port: int = 8765
    # 浏览器/扩展接管下载时，是否弹出“新建下载”确认窗（类 IDM 默认行为）
    takeover_prompt: bool = True
    # 自动更新：latest.json 的地址（留空则不检查；可指向 GitHub/Gitee/自建）
    update_url: str = ""
    # 启动时静默检查更新（有新版本才提示）
    check_update_on_start: bool = True
    # 任务列表中被隐藏的列名（右键表头勾选；文件名列不可隐藏）
    hidden_columns: list = field(default_factory=list)
    # 任务列表各列自定义宽度（列名->像素，拖动后记忆；状态列为弹性列不记录）
    column_widths: dict = field(default_factory=dict)
    # 全部下载完成后的动作：none/exit/sleep/hibernate/shutdown（见 core/power.py）
    after_done_action: str = "none"

    _path: str = field(default="", repr=False, compare=False)

    def connections_for(self, url: str) -> int | None:
        """返回某 URL 命中的站点例外连接数，未命中返回 None。"""
        host = host_of(url)
        if not host or not self.site_connections:
            return None
        best = None
        for domain, value in self.site_connections.items():
            dom = (domain or "").strip().lower().lstrip(".")
            if not dom:
                continue
            try:
                n = int(value)
            except (TypeError, ValueError):
                continue
            n = max(1, min(32, n))
            if host == dom or host.endswith("." + dom):
                # 取最长（最具体）的匹配
                if best is None or len(dom) > best[0]:
                    best = (len(dom), n)
        return best[1] if best else None

    @classmethod
    def config_path(cls) -> str:
        return os.path.join(app_data_dir(), "settings.json")

    @classmethod
    def load(cls) -> "Settings":
        path = cls.config_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                raw = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
                cfg = cls(**raw)
                cfg._path = path
                os.makedirs(cfg.save_dir, exist_ok=True)
                return cfg
            except Exception:
                pass
        cfg = cls()
        cfg._path = path
        return cfg

    def save(self) -> None:
        path = self._path or self.config_path()
        data = asdict(self)
        data.pop("_path", None)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
