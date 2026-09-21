# -*- coding: utf-8 -*-
"""统一品牌信息：所有面向用户的名称、版本、标识集中在此维护。

- :data:`APP_NAME` 是中文展示名「东方神速」，用于窗口、托盘、关于、安装包、
  浏览器扩展等一切用户可见处。
- :data:`APP_ID` / :data:`EXE_NAME` 是稳定的英文内部标识，用于配置目录、
  可执行文件名、注册表等不建议使用中文、且需要长期稳定的位置。
"""
from __future__ import annotations

import os

APP_NAME = "东方神速"
APP_NAME_EN = "East Speed"
#: 英文内部标识（配置目录 / exe 名 / 卸载注册表键），勿随界面文案改动
APP_ID = "DongFangSpeed"
EXE_NAME = "DongFangSpeed"
APP_VERSION = "1.0.0"

#: 用户可见的默认下载文件夹名（中文友好）
DOWNLOAD_DIR_NAME = APP_NAME
#: 浏览器扩展名称
EXT_NAME = f"{APP_NAME} 下载接管"

WINDOW_TITLE = f"{APP_NAME} 下载管理器"
TRAY_TOOLTIP = f"{APP_NAME} 下载管理器"

#: 本地桥接协议头名称保持稳定（扩展与程序两端约定），不因品牌改名而变动
BRIDGE_TOKEN_HEADER = "X-PyDL-Token"


def asset_path(filename: str) -> str:
    """返回随程序打包的资源文件路径（assets 目录），兼容源码与 PyInstaller。"""
    try:
        from .app_paths import resource_root
        root = resource_root()
    except Exception:  # noqa: BLE001
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "assets", filename)


def app_icon_path() -> str:
    return asset_path("app.ico")
