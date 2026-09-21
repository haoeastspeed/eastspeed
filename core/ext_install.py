# -*- coding: utf-8 -*-
"""浏览器扩展一键安装 / 侧载辅助（Windows）。

现实约束（2025 年起 Chromium 安全收紧）：

* Edge（非 Google 品牌版）仍支持启动参数 ``--load-extension=<目录>`` 加载未打包
  扩展，但只在浏览器**完全退出后**用该参数启动时生效，且不写入用户配置，因此
  需要每次带参数启动 —— 本模块通过在桌面生成一个带参数的快捷方式来“持久化”。
* Chrome 品牌版 137+ 移除了 ``--load-extension``（加
  ``--disable-features=DisableLoadExtensionCommandLineSwitch`` 在当前版本也已
  无效）。个人（未加域）电脑上，未上架扩展无法被第三方静默安装，只能：
  1) 开发者模式“加载已解压的扩展程序”一次（之后随配置持久保留）；
  2) 企业策略 ExtensionInstallForcelist + 自托管 CRX（要求设备加入 AD 域/MDM）；
  3) 上架 Chrome 网上应用店 / Edge 加载项。

本模块把上述流程自动化到“最少点击”：Edge 一键启动加载并生成快捷方式；
Chrome 一键打开扩展管理页、在资源管理器定位扩展目录并把路径复制到剪贴板。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from .app_paths import bundled_extension_dir, stable_extension_dir


def _source_extension_dir() -> str:
    """内置随包扩展目录（内容最新），作为部署到稳定目录的来源。"""
    return bundled_extension_dir()


def deploy_stable_extension() -> str:
    """把扩展复制到稳定目录，返回该目录路径（幂等）。"""
    src = _source_extension_dir()
    dst = stable_extension_dir()
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    def _manifest_version(path: str) -> str:
        try:
            import json
            with open(os.path.join(path, "manifest.json"), "r",
                      encoding="utf-8") as f:
                return json.load(f).get("version", "")
        except Exception:  # noqa: BLE001
            return ""

    need = True
    if os.path.isfile(os.path.join(dst, "manifest.json")):
        # 版本一致且关键文件齐全则不重复复制
        if _manifest_version(src) == _manifest_version(dst) and all(
            os.path.isfile(os.path.join(dst, n))
            for n in ("background.js", "popup.html", "icons/icon128.png")
        ):
            need = False
    if need:
        if os.path.isdir(dst):
            shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    return dst


@dataclass
class BrowserInfo:
    key: str          # "edge" / "chrome"
    name: str         # 展示名
    exe: str          # 可执行文件绝对路径
    version: str = ""

    @property
    def extensions_url(self) -> str:
        return "edge://extensions" if self.key == "edge" else "chrome://extensions"

    @property
    def process_name(self) -> str:
        return "msedge.exe" if self.key == "edge" else "chrome.exe"

    @property
    def supports_cli_load(self) -> bool:
        """是否支持命令行一键加载（当前仅 Edge）。"""
        return self.key == "edge"


def _candidate_paths(key: str) -> list[str]:
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")
    if key == "edge":
        return [
            os.path.join(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
            os.path.join(pf, "Microsoft", "Edge", "Application", "msedge.exe"),
        ]
    return [
        os.path.join(pf, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(pf86, "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(local, "Google", "Chrome", "Application", "chrome.exe"),
    ]


def _read_version(exe: str) -> str:
    """直接读 exe 文件版本（不启动浏览器，不受已有实例影响）。"""
    import re
    try:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "(Get-Item -LiteralPath $env:DFS_EXE).VersionInfo.ProductVersion")
        env = os.environ.copy()
        env["DFS_EXE"] = exe
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=12, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (p.stdout or b"").decode("utf-8", "ignore")
        m = re.search(r"(\d+\.\d+\.\d+\.\d+)", text)
        return m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        return ""


def detect_browsers() -> list[BrowserInfo]:
    """探测本机已安装的 Chrome / Edge。"""
    found: list[BrowserInfo] = []
    for key, name in (("edge", "Microsoft Edge"), ("chrome", "Google Chrome")):
        exe = next((p for p in _candidate_paths(key) if os.path.isfile(p)), "")
        if exe:
            found.append(BrowserInfo(key=key, name=name, exe=exe,
                                     version=_read_version(exe)))
    return found


def is_browser_running(info: BrowserInfo) -> bool:
    try:
        p = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {info.process_name}", "/NH"],
            capture_output=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        raw = p.stdout or b""
        text = raw.decode("gbk", "ignore") or raw.decode("utf-8", "ignore")
        return info.process_name.lower() in text.lower()
    except Exception:  # noqa: BLE001
        return False


def request_close_browser(info: BrowserInfo, force: bool = False) -> None:
    """请求关闭浏览器（默认优雅关闭，force=True 强制结束）。

    命令行加载要求 Edge 完全退出；关闭后 Edge 通常可在重启时恢复标签页。
    """
    args = ["taskkill", "/IM", info.process_name]
    if force:
        args.append("/F")
    subprocess.run(args, capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def launch_edge_with_extension(info: BrowserInfo, ext_dir: str) -> None:
    """以默认用户配置启动 Edge 并加载扩展（调用前应确保 Edge 已完全退出）。"""
    subprocess.Popen([info.exe, f"--load-extension={ext_dir}"],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def open_extensions_page(info: BrowserInfo) -> None:
    """在浏览器中打开扩展管理页（浏览器运行中也可新开标签）。"""
    subprocess.Popen([info.exe, info.extensions_url],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def reveal_in_explorer(path: str) -> None:
    """在资源管理器中打开并选中文件/目录。"""
    target = path
    if os.path.isdir(path):
        target = os.path.join(path, "manifest.json")
    if os.path.isfile(target):
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(target)])
    else:
        subprocess.Popen(["explorer.exe", os.path.normpath(path)])


def create_desktop_shortcut(info: BrowserInfo, ext_dir: str) -> str:
    """在桌面创建“带扩展启动浏览器”的快捷方式，返回快捷方式路径。

    用系统自带 PowerShell + WScript.Shell COM 生成，不依赖 pywin32。
    """
    import base64

    lnk_name = "东方神速 - Edge（已加载扩展）.lnk" if info.key == "edge" \
        else "东方神速 - Chrome（已加载扩展）.lnk"
    args = f'--load-extension="{ext_dir}"'
    # 桌面可能被重定向（如 OneDrive / 用户 profile 迁移），用 SpecialFolders 取真实桌面
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        "$ws=New-Object -ComObject WScript.Shell;"
        "$lnk=Join-Path $ws.SpecialFolders('Desktop') " + ps_literal(lnk_name) + ";"
        "$s=$ws.CreateShortcut($lnk);"
        f"$s.TargetPath={ps_literal(info.exe)};"
        f"$s.Arguments={ps_literal(args)};"
        f"$s.WorkingDirectory={ps_literal(os.path.dirname(info.exe))};"
        f"$s.IconLocation={ps_literal(info.exe + ',0')};"
        "$s.Description='启动浏览器并自动加载东方神速下载接管扩展';"
        "$s.Save(); Write-Output $lnk"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    p = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (p.stdout or b"").decode("utf-8", "ignore").strip()
    return out or os.path.join(os.path.expanduser("~"), "Desktop", lnk_name)


def ps_literal(text: str) -> str:
    """把字符串包成 PowerShell 单引号字面量（内部单引号双写）。"""
    return "'" + text.replace("'", "''") + "'"
