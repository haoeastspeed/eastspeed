# -*- coding: utf-8 -*-
"""「东方神速」可执行文件代码签名（Windows signtool，RFC3161 时间戳）。

证书来源（任一）：
  1. 环境变量 DFS_PFX（.pfx/.p12 路径）+ DFS_PFX_PASSWORD（密码，可选）；
  2. 命令行参数 --pfx 路径 [--password 密码]。

没有证书时脚本不报错、直接跳过（返回 0），便于在 CI / 本地无证书环境构建。

重要说明：
  - 自签名证书可以走完签名流程，但客户机默认不信任，SmartScreen 仍会拦截；
  - 要消除 SmartScreen 警告，需购买 OV（组织验证）或 EV（扩展验证）代码签名证书，
    EV 证书通常还需 USB Token / 硬件签名服务；OV 证书还需积累信誉；
  - 时间戳服务器保证证书过期后签名仍有效。

用法：
  python tools/sign_exe.py                 # 签名 dist 下已知产物
  python tools/sign_exe.py a.exe b.msi     # 签名指定文件
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")

# RFC3161 时间戳服务器（依次尝试）
TIMESTAMP_SERVERS = [
    "http://timestamp.acs.microsoft.com",
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
]


def find_signtool() -> str | None:
    """在 Windows Kits 与 PATH 中定位最新的 x64 signtool.exe。"""
    if os.name != "nt":
        return None
    # PATH
    found = shutil_which("signtool.exe")
    if found:
        return found
    patterns = [
        r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe",
        r"C:\Program Files\Windows Kits\10\bin\*\x64\signtool.exe",
    ]
    candidates = []
    for pat in patterns:
        candidates += glob.glob(pat)
    if candidates:
        # 版本目录名形如 10.0.22621.0，按字符串排序取最新
        candidates.sort()
        return candidates[-1]
    return None


def shutil_which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def resolve_cert(cli_pfx: str | None, cli_pwd: str | None):
    pfx = cli_pfx or os.environ.get("DFS_PFX", "")
    pwd = cli_pwd if cli_pwd is not None else os.environ.get("DFS_PFX_PASSWORD", "")
    if pfx and os.path.isfile(pfx):
        return pfx, pwd
    return None, None


def sign_file(path: str, pfx: str, password: str, signtool: str,
              dry: bool = False) -> bool:
    """对单个文件签名，时间戳服务器依次容错。返回是否成功。"""
    if not os.path.isfile(path):
        print(f"[sign] 跳过（不存在）：{path}")
        return False
    base_cmd = [
        signtool, "sign", "/f", pfx,
        "/fd", "sha256",
        "/d", "DongFangSpeed",
    ]
    if password:
        base_cmd += ["/p", password]
    if dry:
        print(f"[sign][dry] 将签名：{path}")
        return True
    last_err = ""
    for ts in TIMESTAMP_SERVERS:
        cmd = base_cmd + ["/tr", ts, "/td", "sha256", path]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"[sign] 已签名（{os.path.basename(path)}，TS={ts}）")
            return True
        last_err = (proc.stderr or proc.stdout or "").strip()
        # 时间戳服务器不可用时换下一个；签名本身错误也会在最后报告
    print(f"[sign] 签名失败：{path}\n        {last_err[:300]}")
    return False


def default_targets() -> list[str]:
    targets = []
    single = os.path.join(DIST, "DongFangSpeed.exe")
    portable = os.path.join(DIST, "DongFangSpeed", "DongFangSpeed.exe")
    setup = os.path.join(DIST, "DongFangSpeed-Setup-1.0.0.exe")
    for p in (single, portable, setup):
        if os.path.isfile(p):
            targets.append(p)
    # 通配兜底：dist 根下的安装包
    for p in glob.glob(os.path.join(DIST, "DongFangSpeed-Setup-*.exe")):
        if p not in targets:
            targets.append(p)
    return targets


def main(argv: list[str]) -> int:
    # 解析选项，同时收集位置参数（待签名文件）；选项本身及其值不计入目标
    targets: list[str] = []
    pfx_arg = None
    pwd_arg = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--pfx" and i + 1 < len(argv):
            pfx_arg = argv[i + 1]
            i += 2
            continue
        if a == "--password" and i + 1 < len(argv):
            pwd_arg = argv[i + 1]
            i += 2
            continue
        if a.startswith("--"):
            i += 1
            continue
        targets.append(a)
        i += 1

    pfx, password = resolve_cert(pfx_arg, pwd_arg)
    if not pfx:
        print("[sign] 未提供代码签名证书（设置环境变量 DFS_PFX 或传 --pfx），跳过签名。")
        print("       提示：自签名证书不被客户机信任、SmartScreen 仍会拦截；"
              "正式发布请使用 OV/EV 代码签名证书。")
        return 0

    signtool = find_signtool()
    if not signtool:
        print("[sign] 未找到 signtool.exe（请安装 Windows SDK），跳过签名。")
        return 0

    targets = targets or default_targets()
    if not targets:
        print("[sign] dist 下没有可签名的产物，请先运行 build_exe.py。")
        return 0

    ok = True
    for t in targets:
        ok = sign_file(os.path.abspath(t), pfx, password, signtool) and ok
    print("[sign]", "全部签名完成" if ok else "存在签名失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
