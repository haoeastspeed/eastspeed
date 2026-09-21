# -*- coding: utf-8 -*-
"""把 Chromium MV3 扩展转换并打包为 Firefox 版（MV2 event page）。

差异处理：
- manifest_version 3 -> 2；background.service_worker -> scripts 事件页（persistent:false）；
- action -> browser_action；host_permissions 合并进 permissions；
- MV2 不支持 content script 的 world:MAIN，剔除 content_hook.js（blob 高级钩子在
  Firefox 降级，下载接管 / 媒体嗅探 / 视频悬浮按钮均保留）；
- 增加 browser_specific_settings.gecko（扩展 ID 与最低版本）；
- 其余 chrome.* API Firefox 均提供同名兼容。

产出：
- dist/firefox_extension/                可在 about:debugging 临时加载的目录
- dist/东方神速-Firefox扩展-<ver>.zip     同内容的 zip（可上传 AMO 签名为 .xpi）
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "browser_extension")
OUT_DIR = os.path.join(ROOT, "dist", "firefox_extension")
GECKO_ID = "east-speed@local"
MIN_FF = "109.0"

# 直接整目录复制的静态资源
COPY_ASSETS = (
    "background.js", "content_bridge.js", "content_video.js",
    "popup.html", "popup.css", "popup.js", "icons", "_locales",
)


def build_manifest(src_manifest: dict) -> dict:
    m = json.loads(json.dumps(src_manifest))  # 深拷贝
    m["manifest_version"] = 2

    # 后台：MV2 事件页
    m.pop("background", None)
    m["background"] = {"scripts": ["background.js"], "persistent": False}

    # content scripts：去掉 MAIN world 的 blob 钩子，去掉 world 字段
    new_cs = []
    for cs in m.get("content_scripts", []):
        files = [f for f in cs.get("js", []) if f != "content_hook.js"]
        if not files:
            continue
        cs = dict(cs)
        cs["js"] = files
        cs.pop("world", None)
        new_cs.append(cs)
    m["content_scripts"] = new_cs

    # action -> browser_action
    if "action" in m:
        m["browser_action"] = m.pop("action")

    # host_permissions 合并进 permissions（MV2）
    perms = list(m.get("permissions", []))
    for h in m.get("host_permissions", []):
        if h not in perms:
            perms.append(h)
    m["permissions"] = perms
    m.pop("host_permissions", None)

    # Firefox 扩展 ID
    m["browser_specific_settings"] = {
        "gecko": {"id": GECKO_ID, "strict_min_version": MIN_FF}
    }
    return m


def main() -> None:
    with open(os.path.join(SRC, "manifest.json"), "r", encoding="utf-8") as f:
        src_manifest = json.load(f)
    version = src_manifest.get("version", "1.0.0")
    manifest = build_manifest(src_manifest)

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    for asset in COPY_ASSETS:
        s = os.path.join(SRC, asset)
        d = os.path.join(OUT_DIR, asset)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        elif os.path.isfile(s):
            shutil.copy2(s, d)

    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    zip_path = os.path.join(
        ROOT, "dist", f"东方神速-Firefox扩展-{version}.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(OUT_DIR):
            for name in files:
                full = os.path.join(base, name)
                arc = os.path.relpath(full, OUT_DIR)
                zf.write(full, arc)

    print(f"Firefox 扩展目录: {OUT_DIR}")
    print(f"Firefox 扩展 ZIP : {zip_path}")
    # 自检：确认关键文件与清单字段
    assert os.path.exists(os.path.join(OUT_DIR, "content_video.js"))
    assert manifest["manifest_version"] == 2
    assert "browser_action" in manifest
    assert "gecko" in manifest["browser_specific_settings"]
    print("RESULT OK")


if __name__ == "__main__":
    main()
