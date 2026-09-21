# -*- coding: utf-8 -*-
"""运行时路径解析：同时兼容「源码运行」与 PyInstaller 打包（单文件/便携/安装版）。

关键区别：
- :func:`resource_root` 指向**只读的程序资源根**。单文件(onefile)模式下是
  PyInstaller 启动时解压出的临时目录 ``sys._MEIPASS``；便携(onedir)模式下是
  exe 旁的 ``_internal``；源码运行时是项目根目录。
- :func:`app_dir` 指向**可执行文件所在目录**（源码运行时为项目根），适合放
  需要在磁盘上长期存在、且要被外部程序（如 Chrome 加载扩展）访问的文件。

浏览器扩展必须是磁盘上的真实文件夹才能被 Chrome/Edge「加载已解压的扩展程序」。
为保证“加载一次、永久生效”，打包后扩展统一释放到**不随 exe 位置变化的固定目录**
``%LOCALAPPDATA%\\DongFangSpeed\\browser_extension``（见 :func:`stable_extension_dir`）：
单文件版即使每次从不同路径启动也复用同一目录，程序升级仅在版本号变化时覆盖内容，
浏览器始终指向同一路径、无需再次手动加载。
"""
from __future__ import annotations

import os
import shutil
import sys

_EXT_DIRNAME = "browser_extension"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """exe 所在目录（打包后）或项目根目录（源码运行）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    # 本文件位于 <项目根>/core/app_paths.py，上两级即项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_root() -> str:
    """打包内只读资源根目录。"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        return meipass if meipass else app_dir()
    return app_dir()


def bundled_extension_dir() -> str:
    """随程序打包在内的浏览器扩展目录（可能位于只读临时解压目录）。"""
    return os.path.join(resource_root(), _EXT_DIRNAME)


def stable_extension_dir() -> str:
    """不随 exe 位置变化的稳定扩展目录，供浏览器长期引用（一次加载永久生效）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DongFangSpeed", _EXT_DIRNAME)


def deployed_extension_dir() -> str:
    """供 Chrome/Edge 加载的扩展目录（磁盘真实文件夹）。

    打包后返回固定目录 :func:`stable_extension_dir`；源码运行时返回项目内目录。
    """
    if is_frozen():
        return stable_extension_dir()
    return os.path.join(app_dir(), _EXT_DIRNAME)


def _manifest_version(path: str) -> str:
    try:
        import json
        with open(os.path.join(path, "manifest.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version", ""))
    except OSError:
        return ""


def ensure_browser_extension() -> str:
    """确保磁盘上存在可被浏览器加载的扩展目录，返回该目录路径。

    源码运行时扩展本就在项目目录，直接返回。打包后把内置扩展释放到固定目录，
    仅当目录缺失或 manifest 版本号变化（程序升级）时才复制，平时启动不重复释放。
    任何失败都不应阻断主程序启动。
    """
    src = bundled_extension_dir()
    dst = deployed_extension_dir()
    if not is_frozen():
        return dst if os.path.isdir(dst) else src
    try:
        if os.path.isdir(src) and (
            not os.path.isfile(os.path.join(dst, "manifest.json"))
            or _manifest_version(src) != _manifest_version(dst)
        ):
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(src, dst)
    except Exception:  # noqa: BLE001 释放失败不影响下载主功能
        return dst if os.path.isdir(dst) else src
    return dst if os.path.isdir(dst) else src
