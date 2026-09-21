# -*- coding: utf-8 -*-
"""磁盘预分配。

默认（``preallocate=False``）下载引擎只做稀疏 ``truncate``：文件逻辑大小立刻
等于最终大小，但簇按需分配，最省 IO、最“轻巧”。

当用户在设置中开启“下载前预分配磁盘空间”后，Windows 上通过
``FSCTL_SET_ZERO_DATA`` 让文件系统在内核态把整段清零并**真实占用簇**（避免下载
到后期才发现磁盘配额/碎片问题）；该方式比 Python 逐块写零快得多。其它平台或
调用失败时回退到普通 ``truncate``（尽力而为，不阻断下载）。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import byref, c_ulong, c_longlong, sizeof

FSCTL_SET_ZERO_DATA = 0x000980C8
GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x00000001 | 0x00000002 | 0x00000004
OPEN_EXISTING = 3
FILE_BEGIN = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _LARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", c_longlong)]


class _FILE_ZERO_DATA_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("FileOffset", _LARGE_INTEGER),
        ("BeyondFinalZero", _LARGE_INTEGER),
    ]


def _preallocate_windows(path: str, size: int) -> None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    k32.SetFilePointerEx.argtypes = [
        ctypes.c_void_p, _LARGE_INTEGER,
        ctypes.POINTER(_LARGE_INTEGER), ctypes.c_uint32,
    ]
    k32.SetFilePointerEx.restype = ctypes.c_int
    k32.SetEndOfFile.argtypes = [ctypes.c_void_p]
    k32.SetEndOfFile.restype = ctypes.c_int
    k32.DeviceIoControl.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(c_ulong), ctypes.c_void_p,
    ]
    k32.DeviceIoControl.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]

    handle = k32.CreateFileW(
        os.path.abspath(path), GENERIC_WRITE, FILE_SHARE_RW, None,
        OPEN_EXISTING, 0, None,
    )
    if handle is None or handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        new_pos = _LARGE_INTEGER(0)
        if not k32.SetFilePointerEx(handle, _LARGE_INTEGER(size),
                                   byref(new_pos), FILE_BEGIN):
            raise ctypes.WinError(ctypes.get_last_error())
        if not k32.SetEndOfFile(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        buf = _FILE_ZERO_DATA_INFORMATION(_LARGE_INTEGER(0),
                                          _LARGE_INTEGER(size))
        returned = c_ulong(0)
        ok = k32.DeviceIoControl(
            handle, FSCTL_SET_ZERO_DATA, byref(buf), sizeof(buf),
            None, 0, byref(returned), None,
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        k32.CloseHandle(handle)


def preallocate(path: str, size: int) -> bool:
    """把已存在文件扩展并尽量真实占盘到 size 字节。

    返回 True 表示已真实占用簇，False 表示仅做了逻辑扩展（稀疏/回退）。
    """
    if size <= 0:
        return False
    if not os.path.exists(path):
        with open(path, "wb"):
            pass
    if sys.platform == "win32":
        try:
            _preallocate_windows(path, size)
            return True
        except (OSError, ctypes.WinError):
            pass
    # 跨平台回退：逻辑扩展（多数文件系统上为稀疏，不保证占簇）
    try:
        with open(path, "r+b") as f:
            f.truncate(size)
    except OSError:
        pass
    return False
