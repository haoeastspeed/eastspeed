# -*- coding: utf-8 -*-
"""单任务覆盖参数（独立成模块，避免与 task.py 循环导入）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskOptions:
    connections: int | None = None
    save_dir: str | None = None
    filename: str | None = None
    username: str | None = None
    password: str | None = None
    proxy: str | None = None
    referer: str | None = None
    cookies: dict | str | None = None
    headers: dict | None = None
    # 下载完成后的校验：算法（md5/sha1/sha256/sha512）与期望摘要（十六进制）
    checksum_algo: str | None = None
    checksum_expected: str | None = None
    # BT：仅下载种子内这些文件（匹配相对路径或文件名，None/空=全部下载）
    bt_files: list[str] | None = None
    # BT：额外追加的 tracker（announce）地址
    bt_trackers: list[str] | None = None
    # 代理认证方式：basic / digest / ntlm（默认 basic）
    proxy_auth: str | None = None
    # 代理认证用户名/密码（也可直接写在 proxy URL 的 user:pass@ 中）
    proxy_username: str | None = None
    proxy_password: str | None = None
    # NTLM 域名（可选）
    proxy_domain: str | None = None
    # PAC 自动配置脚本地址（http(s):// 或 file:///；设置后按目标 URL 解析代理）
    pac_url: str | None = None
    # 多镜像源：同一文件的额外下载地址（需与主 URL 大小一致且支持 Range），
    # 分段调度会在所有源之间聚合带宽、失败自动换源（本次运行有效）
    mirrors: list[str] | None = None
