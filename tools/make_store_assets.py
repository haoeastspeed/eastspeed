# -*- coding: utf-8 -*-
"""生成 Microsoft Edge 加载项上架所需的图片素材与商店版扩展 zip。

产物（输出到 edge_submission/assets/ 与 edge_submission/）：
  - store_logo_300.png      商店扩展 logo（1:1，300x300，必需，最小 128）
  - tile_small_440x280.png  小型宣传图（可选，440x280）
  - tile_large_1400x560.png 大型宣传图（可选，1400x560）
  - 东方神速-Edge商店版-<ver>.zip  商店上传包（manifest.json 位于 zip 根）

运行：python tools/make_store_assets.py
"""
from __future__ import annotations

import os
import zipfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "edge_submission")
ASSETS = os.path.join(OUT, "assets")
SRC_LOGO = os.path.join(ROOT, "assets", "app.png")
EXT_DIR = os.path.join(ROOT, "browser_extension")

PRIMARY = (45, 127, 249)      # #2D7FF9
PRIMARY_DARK = (20, 86, 192)  # #1456C0
SUCCESS = (34, 165, 90)       # #22A55A
WHITE = (255, 255, 255)


def _font(size: int, bold: bool = False):
    candidates = (
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    )
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _gradient(size, c1, c2):
    """从左上 c1 到右下 c2 的对角线性渐变。"""
    w, h = size
    base = Image.new("RGB", size, c1)
    top = Image.new("RGB", size, c2)
    mask = Image.new("L", size)
    md = mask.load()
    denom = max(1, w + h - 2)
    for y in range(h):
        for x in range(w):
            md[x, y] = int(255 * (x + y) / denom)
    base.paste(top, (0, 0), mask)
    return base


def _rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_logo():
    img = Image.open(SRC_LOGO).convert("RGBA")
    img = img.resize((300, 300), Image.LANCZOS)
    img.save(os.path.join(ASSETS, "store_logo_300.png"))


def _paste_logo(canvas, logo, box, circle_bg=True):
    x, y, w, h = box
    if circle_bg:
        d = ImageDraw.Draw(canvas)
        _rounded_rect(d, (x, y, x + w, y + h), radius=28,
                      fill=(255, 255, 255, 38))
    lw = int(w * 0.82)
    lg = logo.resize((lw, lw), Image.LANCZOS)
    canvas.paste(lg, (x + (w - lw) // 2, y + (h - lw) // 2), lg)


def make_tile_large(logo):
    W, H = 1400, 560
    cv = _gradient((W, H), PRIMARY, PRIMARY_DARK).convert("RGBA")
    d = ImageDraw.Draw(cv)
    # 左侧 logo 卡片
    _paste_logo(cv, logo, (90, 110, 340, 340))
    # 文案
    d.text((480, 150), "东方神速", font=_font(118, True), fill=WHITE)
    d.text((484, 296), "East Speed  ·  多线程下载管理器",
           font=_font(40), fill=(224, 235, 255))
    tags = "多连接加速 · 断点续传 · 浏览器一键接管 · m3u8 / BT / 磁力"
    d.text((484, 372), tags, font=_font(34), fill=(206, 224, 250))
    # 绿色点缀条
    d.rounded_rectangle((484, 452, 484 + 360, 462), radius=5, fill=SUCCESS)
    cv.convert("RGB").save(os.path.join(ASSETS, "tile_large_1400x560.png"))


def make_tile_small(logo):
    W, H = 440, 280
    cv = _gradient((W, H), PRIMARY, PRIMARY_DARK).convert("RGBA")
    _paste_logo(cv, logo, (34, 76, 128, 128))
    d = ImageDraw.Draw(cv)
    d.text((180, 78), "东方神速", font=_font(46, True), fill=WHITE)
    d.text((182, 140), "多线程下载", font=_font(24), fill=(214, 228, 250))
    d.text((182, 174), "浏览器一键接管", font=_font(24), fill=(214, 228, 250))
    cv.convert("RGB").save(os.path.join(ASSETS, "tile_small_440x280.png"))


def make_store_zip():
    import json
    with open(os.path.join(EXT_DIR, "manifest.json"), encoding="utf-8") as f:
        version = json.load(f).get("version", "1.0.0")
    out_zip = os.path.join(OUT, f"东方神速-Edge商店版-{version}.zip")
    if os.path.exists(out_zip):
        os.remove(out_zip)
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(EXT_DIR):
            for name in files:
                full = os.path.join(base, name)
                rel = os.path.relpath(full, EXT_DIR)
                # 商店包不需要操作系统元数据文件
                if name in ("Thumbs.db", ".DS_Store"):
                    continue
                zf.write(full, rel.replace(os.sep, "/"))
    return out_zip


def main():
    os.makedirs(ASSETS, exist_ok=True)
    make_logo()
    logo = Image.open(SRC_LOGO).convert("RGBA")
    make_tile_large(logo)
    make_tile_small(logo)
    zf = make_store_zip()
    print("[store] 素材已生成到", ASSETS)
    for name in sorted(os.listdir(ASSETS)):
        print("  -", name)
    print("[store] 商店包：", zf)


if __name__ == "__main__":
    main()
