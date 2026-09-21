# -*- coding: utf-8 -*-
"""“全部下载完成后”的系统电源动作（Windows）。

动作：
* none      不做任何操作
* exit      退出本程序
* sleep     进入睡眠（S3，内存保持）
* hibernate 休眠（写入磁盘后断电）
* shutdown  关机

关机/休眠通过系统 shutdown.exe 执行；睡眠通过 PowrProf.SetSuspendState。
关机给系统级倒计时，可用 :func:`cancel_shutdown` 取消（shutdown /a）。
"""
from __future__ import annotations

import ctypes
import os
import subprocess

ACTIONS = ("none", "exit", "sleep", "hibernate", "shutdown")
ACTION_LABELS = {
    "none": "不执行任何操作",
    "exit": "退出东方神速",
    "sleep": "让电脑进入睡眠",
    "hibernate": "让电脑休眠",
    "shutdown": "关闭电脑",
}


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_supported() -> bool:
    return os.name == "nt"


def schedule_shutdown(seconds: int = 60, comment: str = "东方神速：全部下载已完成") -> None:
    """安排系统在 seconds 秒后关机（可用 cancel_shutdown 取消）。"""
    subprocess.run(
        ["shutdown", "/s", "/t", str(int(seconds)), "/c", comment],
        capture_output=True, creationflags=_no_window())


def cancel_shutdown() -> None:
    """取消已安排的关机/休眠倒计时。"""
    subprocess.run(["shutdown", "/a"], capture_output=True,
                   creationflags=_no_window())


def hibernate() -> None:
    subprocess.run(["shutdown", "/h"], capture_output=True,
                   creationflags=_no_window())


def sleep() -> None:
    """进入睡眠（S3）。若系统启用了休眠且混合睡眠开启，可能表现为休眠。"""
    try:
        # SetSuspendState(bHibernate=False, bForceCritical=False, bDisableWakeEvent=False)
        ctypes.windll.PowrProf.SetSuspendState(0, 0, 0)
    except Exception:  # noqa: BLE001  无 PowrProf 时回退到 rundll32
        subprocess.run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            capture_output=True, creationflags=_no_window())


def perform(action: str) -> None:
    """立即执行指定动作（exit 由界面层处理，这里只管系统电源动作）。"""
    if action == "shutdown":
        schedule_shutdown(60)
    elif action == "hibernate":
        hibernate()
    elif action == "sleep":
        sleep()
