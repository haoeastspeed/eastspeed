# -*- coding: utf-8 -*-
"""开机自动启动（Windows：当前用户注册表 Run 键，无需管理员权限）。

* **仅打包后的 exe（单文件版/安装版）允许注册**开机启动，命令为
  ``"DongFangSpeed.exe" --minimized``，开机后在系统托盘后台驻留；
* 从源码 / Python 解释器运行时（包括临时虚拟环境）**不允许注册**，避免把
  开发用的 ``pythonw.exe`` 路径写进用户开机启动；程序启动时还会自动清理
  这类历史残留（见 :func:`cleanup_dev_residue`）；
* 只写 HKEY_CURRENT_USER，不需要管理员，也不影响其他用户。
"""
from __future__ import annotations

import os
import sys

from .branding import APP_ID
from .app_paths import app_dir

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = APP_ID  # DongFangSpeed


def is_frozen() -> bool:
    """是否为 PyInstaller 打包后的 exe 运行形态。"""
    return bool(getattr(sys, "frozen", False))


def startup_command() -> str:
    """返回开机启动时执行的命令行。"""
    if is_frozen():
        return f'"{sys.executable}" --minimized'
    # 源码运行：优先用同目录 pythonw.exe（无控制台窗口）
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    main_py = os.path.join(app_dir(), "main.py")
    return f'"{pyw}" "{main_py}" --minimized'


def is_supported() -> bool:
    """注册表开机启动能力在当前系统是否可用（Windows）。"""
    return os.name == "nt"


def is_allowed() -> bool:
    """当前运行形态是否允许注册开机启动（仅打包后的 exe）。"""
    return is_supported() and is_frozen()


def _read() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        return str(value) if value else None
    except OSError:
        return None


def _write(cmd: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, cmd)


def _delete() -> None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_enabled() -> bool:
    """是否已设置开机启动（Run 键中存在本程序的值）。"""
    if not is_supported():
        return False
    return bool(_read())


def enable() -> None:
    """开启开机启动（仅打包 exe 形态允许）。"""
    if not is_supported():
        raise RuntimeError("当前系统不支持注册表开机启动")
    if not is_allowed():
        raise RuntimeError(
            "开机自动启动仅在打包后的单文件版/安装版中可用；"
            "从源码或 Python 解释器运行时不会注册开机启动")
    _write(startup_command())


def disable() -> None:
    """关闭开机启动（值不存在时静默成功，源码形态也可用于清理残留）。"""
    if is_supported():
        _delete()


def set_enabled(enabled: bool) -> None:
    if enabled:
        enable()
    else:
        disable()


def cleanup_dev_residue() -> bool:
    """清理源码/解释器运行时误写入的开机启动残留。

    仅在非打包（开发/源码）形态下动作：若 Run 键里本程序的值指向
    ``main.py``，说明是旧版本从源码运行时注册的开发残留，删除之。
    返回是否执行了清理。打包形态或无残留时返回 False。
    """
    if not is_supported() or is_frozen():
        return False
    value = _read()
    if value and "main.py" in value:
        _delete()
        return True
    return False
