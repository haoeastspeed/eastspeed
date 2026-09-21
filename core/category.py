# -*- coding: utf-8 -*-
"""按扩展名做文件分类（引擎与界面共用）。"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# 类别 -> 保存子目录名
CATEGORY_DIRS = {
    "视频": "视频",
    "音频": "音频",
    "文档": "文档",
    "程序": "程序",
    "压缩包": "压缩包",
}

_CATEGORY_EXT = {
    "视频": {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "ts",
            "m3u8", "rmvb", "rm", "3gp", "mpg", "mpeg", "f4v", "vob"},
    "音频": {"mp3", "flac", "ape", "wav", "aac", "ogg", "m4a", "wma", "opus",
            "aiff", "amr"},
    "文档": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
            "epub", "mobi", "azw3", "md", "rtf", "wps", "et", "dps", "odt",
            "ods", "odp", "pages", "numbers", "key"},
    "程序": {"exe", "msi", "apk", "dmg", "pkg", "deb", "rpm", "appimage",
            "whl", "jar", "bat", "ps1", "cmd", "com", "gadget", "ipa"},
    "压缩包": {"zip", "rar", "7z", "tar", "gz", "xz", "bz2", "iso", "tgz",
             "tbz", "zst", "lz", "cab", "lzma"},
}


def category_of(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    for cat, exts in _CATEGORY_EXT.items():
        if ext in exts:
            return cat
    return "其他"


def category_dir_for(filename: str, auto_categorize: bool, base_dir: str,
                     explicit_dir: str | None = None) -> str:
    """决定保存目录：显式指定优先；开启自动分类则按类型进子目录，否则用根目录。"""
    if explicit_dir:
        return explicit_dir
    if not auto_categorize:
        return base_dir
    cat = category_of(filename)
    sub = CATEGORY_DIRS.get(cat)
    if not sub:
        return base_dir  # “其他”留在根目录
    return os.path.join(base_dir, sub)


def host_of(url: str) -> str:
    """取小写主机名（去端口、去 www. 前缀）。"""
    host = urlparse(url).hostname or ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host
