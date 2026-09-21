# -*- coding: utf-8 -*-
"""下载完成后的杀毒钩子（Windows）。

优先调用系统自带的 Microsoft Defender 命令行扫描器 ``MpCmdRun.exe`` 对单个文件
做自定义扫描（``-DisableRemediation`` 仅检测、不擅自隔离）。检出威胁时由引擎
删除文件并把任务置为错误。

- 非 Windows、找不到 Defender、或扫描器异常：``available=False``，静默跳过，
  绝不因为“没有杀毒软件”而让下载失败；
- 第三方杀毒软件通常仍会让系统保留 Defender 命令行接口；若完全没有，本模块
  如实返回不可用，不谎报已扫描。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
from dataclasses import dataclass

# MpCmdRun 自定义扫描返回码：0 未发现威胁；2 发现威胁
_RET_CLEAN = 0
_RET_INFECTED = 2


@dataclass
class ScanResult:
    available: bool = False      # 本机是否存在可用扫描器
    infected: bool = False       # 是否检出威胁
    threat: str = ""             # 威胁名称（若扫描器提供）
    detail: str = ""             # 人类可读说明
    returncode: int | None = None


def find_defender() -> str:
    """返回 MpCmdRun.exe 路径，找不到返回空串。"""
    if sys.platform != "win32":
        return ""
    candidates = []
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.append(os.path.join(program_files, "Windows Defender",
                                   "MpCmdRun.exe"))
    # 新版按版本号放在 ProgramData\...\Platform\<版本>\
    platform_dir = r"C:\ProgramData\Microsoft\Windows Defender\Platform"
    versioned = sorted(glob.glob(os.path.join(platform_dir, "*", "MpCmdRun.exe")))
    candidates.extend(reversed(versioned))  # 最新版本优先
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ""


def scanner_available() -> bool:
    return bool(find_defender())


def scan_file(path: str, timeout: int = 180) -> ScanResult:
    exe = find_defender()
    if not exe:
        return ScanResult(available=False, detail="未找到 Windows Defender 扫描器")
    if not os.path.exists(path):
        return ScanResult(available=True, detail="文件不存在")
    cmd = [exe, "-Scan", "-ScanType", "3", "-DisableRemediation", "-File", path]
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return ScanResult(available=True, detail="杀毒扫描超时，已跳过")
    except OSError as exc:
        return ScanResult(available=False, detail=f"扫描器调用失败: {exc}")

    code = proc.returncode
    if code == _RET_INFECTED:
        return ScanResult(available=True, infected=True,
                          threat="", detail="Windows Defender 检出威胁",
                          returncode=code)
    if code == _RET_CLEAN:
        return ScanResult(available=True, infected=False,
                          detail="未发现威胁", returncode=code)
    # 其它返回码多为扫描器自身状态（如服务未就绪），不武断判定为带毒
    return ScanResult(available=True, infected=False,
                      detail=f"扫描器返回码 {code}，按未检出处理",
                      returncode=code)
