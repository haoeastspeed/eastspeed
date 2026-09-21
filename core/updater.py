# -*- coding: utf-8 -*-
"""自动更新框架（检查 → 下载 → SHA256 校验 → 启动安装包）。

更新源是一个可配置的 URL，指向 ``latest.json``：

    {
      "version": "1.0.1",
      "url": "https://example.com/DongFangSpeed-Setup-1.0.1.exe",
      "sha256": "可选，安装包的 SHA256（十六进制）",
      "notes": "本版更新说明……"
    }

更新源地址（GitHub Releases / Gitee / 自建服务器）由用户在设置中填写；
未配置时不发起任何网络请求。安装包为 Inno Setup，支持静默覆盖安装。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile

from . import tls
from .branding import APP_NAME, APP_VERSION


class UpdateError(Exception):
    pass


def parse_version(text: str) -> tuple:
    """把 '1.0.10' 解析为可比较的整数元组 (1,0,10)；非数字段按 0 处理。"""
    nums = re.findall(r"\d+", str(text or ""))
    if not nums:
        return (0,)
    return tuple(int(x) for x in nums[:4])


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def fetch_update_info(update_url: str, timeout: float = 8.0) -> dict:
    """下载并校验 latest.json，返回信息字典；网络/格式错误抛 UpdateError。"""
    if not update_url:
        raise UpdateError("未配置更新源")
    try:
        with tls.requests_session() as sess:
            resp = sess.get(update_url, timeout=timeout)
            resp.raise_for_status()
            info = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"检查更新失败：{exc}") from exc
    if not isinstance(info, dict) or not info.get("version") or not info.get("url"):
        raise UpdateError("更新清单缺少 version 或 url 字段")
    return {
        "version": str(info["version"]).strip(),
        "url": str(info["url"]).strip(),
        "sha256": str(info.get("sha256") or "").strip().lower(),
        "notes": str(info.get("notes") or ""),
    }


def check_for_update(update_url: str, current: str = APP_VERSION,
                     timeout: float = 8.0) -> dict | None:
    """有新版本返回信息字典，否则 None。"""
    info = fetch_update_info(update_url, timeout)
    if is_newer(info["version"], current):
        return info
    return None


def sha256_file(path: str, buf_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf_size), b""):
            h.update(chunk)
    return h.hexdigest()


def download_update(info: dict, dest_dir: str | None = None,
                    progress=None, timeout: float = 60.0) -> str:
    """流式下载安装包并校验 SHA256，返回本地路径。

    progress(downloaded:int, total:int) 可选回调。校验失败删除文件并抛错。
    """
    url = info["url"]
    name = os.path.basename(url.split("?", 1)[0]) or "DongFangSpeed-Setup.exe"
    if not name.lower().endswith(".exe"):
        name += ".exe"
    dest_dir = dest_dir or tempfile.gettempdir()
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, name)

    try:
        sess = tls.requests_session()
        with sess.get(url, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(1 << 18):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        try:
                            progress(done, total)
                        except Exception:  # noqa: BLE001
                            pass
            os.replace(tmp, dest)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"下载更新失败：{exc}") from exc

    expected = info.get("sha256")
    if expected:
        actual = sha256_file(dest)
        if actual.lower() != expected.lower():
            try:
                os.remove(dest)
            except OSError:
                pass
            raise UpdateError(
                f"安装包校验失败（SHA256 不一致，可能下载不完整或被篡改）。\n"
                f"期望 {expected[:16]}…，实际 {actual[:16]}…")
    return dest


def launch_installer(path: str) -> bool:
    """启动 Inno 安装包静默覆盖安装；返回是否成功发起。"""
    if not os.path.isfile(path):
        return False
    try:
        if os.name == "nt":
            # /closeapplications 关闭运行中的旧版，/norestart 不重启，/silent 静默
            subprocess.Popen(
                [path, "/silent", "/closeapplications", "/norestart"],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            subprocess.Popen([path], close_fds=True)
        return True
    except Exception:  # noqa: BLE001
        return False
