# -*- coding: utf-8 -*-
"""生成「东方神速」品牌图标：蓝渐变圆角底 + 白色下载箭头 + 金色闪电。

运行（需 Pillow）:
    python tools/make_brand_icons.py

产物:
    assets/app.ico                 多尺寸 Windows 图标（16~256）
    assets/app.png                 512 高清图（关于对话框等）
    browser_extension/icons/*.png  16/32/48/128 扩展图标
采用 4 倍超采样绘制后缩小，保证 16px 下依然清晰。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SS = 4                      # 超采样倍数
N = 256                     # 逻辑画布尺寸
PX = N * SS                 # 实际绘制尺寸 1024

TOP_COLOR = (63, 150, 255)      # 渐变顶 亮蓝 #3F96FF
BOTTOM_COLOR = (9, 70, 211)     # 渐变底 深蓝 #0946D3
WHITE = (255, 255, 255, 255)
BOLT_FILL = (255, 210, 63, 255)     # 金色闪电 #FFD23F
BOLT_EDGE = (202, 134, 10, 255)     # 深金描边 #CA860A
SHADOW = (5, 28, 78, 110)


def s(v):
    return int(round(v * SS))


def pt(points):
    return [(s(x), s(y)) for x, y in points]


def vertical_gradient(size, top, bottom):
    img = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b, 255))
    return img


def rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def arrow_shape(draw):
    """白色下载箭头：竖杆 + 倒三角，竖杆下端插入三角，整体居中略偏左。"""
    cx = 104
    # 竖杆（圆角矩形），下端插入倒三角，避免颈部凸耳
    draw.rounded_rectangle([s(cx - 15), s(72), s(cx + 15), s(146)],
                           radius=s(15), fill=WHITE)
    # 倒三角
    draw.polygon(pt([(cx, 198), (cx - 52, 134), (cx + 52, 134)]), fill=WHITE)


# 金色闪电（位于右上，完整落在圆角安全区内），经典 zig-zag
BOLT = [(186, 60), (154, 120), (178, 120), (168, 170),
        (204, 106), (182, 106), (196, 60)]


def _scale(points, factor):
    """以多边形质心为中心缩放。"""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    return [(cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in points]


def bolt_layers(draw):
    # 深金外描边（外扩多边形）+ 金色本体
    draw.polygon(pt(_scale(BOLT, 1.16)), fill=BOLT_EDGE)
    draw.polygon(pt(BOLT), fill=BOLT_FILL)


def build_logo():
    base = vertical_gradient(PX, TOP_COLOR, BOTTOM_COLOR)
    mask = rounded_mask(PX, s(54))
    canvas = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    canvas.paste(base, (0, 0), mask)

    # 顶部柔和高光
    gloss = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gloss)
    gd.ellipse([s(-60), s(-150), s(316), s(120)], fill=(255, 255, 255, 26))
    gloss = gloss.filter(ImageFilter.GaussianBlur(s(6)))
    canvas = Image.alpha_composite(canvas, gloss)

    # 阴影层（箭头 + 闪电），轻微下移并模糊
    shadow = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    arrow_shape(sd)
    sd.polygon(pt(BOLT), fill=SHADOW)
    shadow = shadow.filter(ImageFilter.GaussianBlur(s(5)))
    shifted = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    shifted.paste(shadow, (0, s(7)), shadow)
    canvas = Image.alpha_composite(canvas, shifted)

    # 实体层
    fg = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    fd = ImageDraw.Draw(fg)
    arrow_shape(fd)
    # 闪电：深金外描边 + 金色本体，提升小尺寸对比
    bolt_layers(fd)
    canvas = Image.alpha_composite(canvas, fg)
    return canvas


def main():
    logo = build_logo()

    assets = os.path.join(ROOT, "assets")
    os.makedirs(assets, exist_ok=True)

    # 512 高清 PNG
    logo.resize((512, 512), Image.LANCZOS).save(os.path.join(assets, "app.png"))

    # 多尺寸 ICO
    ico_path = os.path.join(assets, "app.ico")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    logo.save(ico_path, sizes=[(z, z) for z in sizes])

    # 浏览器扩展图标
    ext_icons = os.path.join(ROOT, "browser_extension", "icons")
    os.makedirs(ext_icons, exist_ok=True)
    for z in (16, 32, 48, 128):
        logo.resize((z, z), Image.LANCZOS).save(
            os.path.join(ext_icons, f"icon{z}.png"))

    print("brand icons generated:")
    print(" ", ico_path)
    print(" ", os.path.join(assets, "app.png"))
    print(" ", ext_icons)


if __name__ == "__main__":
    sys.exit(main())
