# -*- coding: utf-8 -*-
"""生成 Edge 加载项商店截图（1280x800，真实桌面渲染以保留中文字体）。

运行（在装有 PyQt5 的 Windows 环境）：
    python tools/make_store_screenshots.py
输出：edge_submission/assets/screenshots/shot_*.png
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
# 强制真实桌面渲染（offscreen 无中文字体）
os.environ.pop("QT_QPA_PLATFORM", None)

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import QApplication, QTabWidget  # noqa: E402

from core.config import Settings  # noqa: E402
from core.engine import DownloadManager  # noqa: E402
from core.task_options import TaskOptions  # noqa: E402
from gui.add_dialog import AddDownloadDialog  # noqa: E402
from gui.app import EngineBridge  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402
from gui.theme import apply_theme  # noqa: E402
from tests.range_server import make_server  # noqa: E402
from tests.hls_server import make_hls_server  # noqa: E402

OUT = os.path.join(_ROOT, "edge_submission", "assets", "screenshots")
os.makedirs(OUT, exist_ok=True)
W, H = 1280, 800
PRIMARY = (45, 127, 249)
BG = (243, 245, 248)


def _font(size, bold=False):
    p = r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc"
    return ImageFont.truetype(p, size) if os.path.isfile(p) else ImageFont.load_default()


def _to_pil(qpix):
    import io
    from PyQt5.QtCore import QBuffer, QByteArray
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    qpix.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(ba))).convert("RGBA")


def full_frame(qpix, name):
    """主窗口：直接铺满 1280x800（白底兜底）。"""
    img = _to_pil(qpix).convert("RGB")
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    if img.width >= W or img.height >= H:
        img.thumbnail((W, H), Image.LANCZOS)
    canvas.paste(img, ((W - img.width) // 2, (H - img.height) // 2))
    canvas.save(os.path.join(OUT, name))


def titled_frame(qpix, title, name):
    """对话框/面板：顶部蓝色标题条 + 居中内容，统一 1280x800。"""
    img = _to_pil(qpix).convert("RGB")
    canvas = Image.new("RGB", (W, H), BG)
    bar = 64
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, W, bar), fill=PRIMARY)
    d.text((40, 16), title, font=_font(28, True), fill=(255, 255, 255))
    avail_h = H - bar - 48
    avail_w = W - 80
    if img.width > avail_w or img.height > avail_h:
        img.thumbnail((avail_w, avail_h), Image.LANCZOS)
    x = (W - img.width) // 2
    y = bar + (avail_h - img.height) // 2 + 20
    # 卡片阴影底
    sh = Image.new("RGB", (img.width + 24, img.height + 24), (210, 216, 226))
    canvas.paste(sh, (x - 12, y - 12))
    canvas.paste(img, (x, y))
    canvas.save(os.path.join(OUT, name))


def shoot_popup():
    """用本机 Edge headless 截真实 popup.html。"""
    try:
        from core.ext_install import detect_browsers
        edge = next((b for b in detect_browsers() if b.key == "edge"), None)
        if not edge:
            print("[shot] 未找到 Edge，跳过 popup 截图")
            return None
        target = os.path.join(_ROOT, "browser_extension", "popup.html")
        out_png = os.path.join(tempfile.gettempdir(), "dfs_popup_shot.png")
        if os.path.exists(out_png):
            os.remove(out_png)
        subprocess.run(
            [edge.exe, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--window-size=420,660", f"--screenshot={out_png}",
             "file:///" + target.replace("\\", "/")],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out_png if os.path.isfile(out_png) else None
    except Exception as exc:  # noqa: BLE001
        print("[shot] popup 截图失败：", exc)
        return None


def main():
    appdata = tempfile.mkdtemp(prefix="store-appdata-")
    os.environ["APPDATA"] = appdata
    app = QApplication([])
    apply_theme(app)

    serve = tempfile.mkdtemp(prefix="store-served-")
    with open(os.path.join(serve, "demo.bin"), "wb") as f:
        f.write(os.urandom(16 * 1024 * 1024))
    for name, size in (("movie.mp4", 2 * 1024 * 1024), ("song.mp3", 512 * 1024),
                       ("report.pdf", 300 * 1024), ("archive.zip", 900 * 1024)):
        with open(os.path.join(serve, name), "wb") as f:
            f.write(os.urandom(size))
    httpd, base = make_server(serve)
    hls_httpd, hls_base, _fx = make_hls_server()

    dl = tempfile.mkdtemp(prefix="store-dl-")
    settings = Settings(save_dir=dl, block_size=256 * 1024,
                        auto_resume=False, av_scan=False)
    bridge = EngineBridge()
    manager = DownloadManager(settings, callbacks=bridge.callbacks)
    manager.start()
    win = MainWindow(manager, settings, bridge)
    manager.add(f"{base}/file/demo.bin?kbps=400",
                TaskOptions(connections=8, filename="DongFangSpeed-Setup-1.0.0.exe"))
    manager.add(f"{hls_base}/vod/master.m3u8",
                TaskOptions(connections=8, filename="产品演示视频.m3u8"))
    manager.add(f"{base}/file/movie.mp4", TaskOptions(filename="会议录像.mp4"))
    manager.add(f"{base}/file/song.mp3", TaskOptions(filename="背景音乐.mp3"))
    manager.add(f"{base}/file/report.pdf", TaskOptions(filename="年度报告.pdf"),
                start_paused=True)
    manager.add(f"{base}/file/archive.zip", TaskOptions(filename="项目资料.zip"))

    win.resize(W, H)
    win.show()
    QTest.qWait(3500)
    win.refresh()
    QTest.qWait(300)
    full_frame(win.grab(), "shot_1_main.png")
    print("[shot] main")

    add = AddDownloadDialog(settings, f"{base}/file/movie.mp4", win)
    add.show()
    QTest.qWait(900)  # 等后台探测回填文件名/大小
    add.resize(620, 500)
    QTest.qWait(200)
    titled_frame(add.grab(), "浏览器点击下载链接 → 自动弹出接管确认窗口", "shot_2_takeover.png")
    add.close()
    print("[shot] takeover")

    from gui.ext_install_dialog import ExtInstallDialog
    ei = ExtInstallDialog("test-token-AbCdEf", win)
    ei.show()
    QTest.qWait(500)
    ei.resize(640, 600)
    QTest.qWait(200)
    titled_frame(ei.grab(), "一键安装浏览器扩展（自动探测 Chrome / Edge）", "shot_3_ext_install.png")
    ei.close()
    print("[shot] ext install")

    manager.shutdown()
    httpd.shutdown()
    hls_httpd.shutdown()

    popup_png = shoot_popup()
    if popup_png:
        img = Image.open(popup_png).convert("RGB")
        titled_frame_image = None  # 直接走合成
        canvas = Image.new("RGB", (W, H), BG)
        bar = 64
        d = ImageDraw.Draw(canvas)
        d.rectangle((0, 0, W, bar), fill=PRIMARY)
        d.text((40, 16), "扩展面板：接管开关 · 媒体嗅探 · Blob · 手动下载",
               font=_font(28, True), fill=(255, 255, 255))
        avail_h = H - bar - 48
        if img.height > avail_h:
            ratio = avail_h / img.height
            img = img.resize((int(img.width * ratio), avail_h), Image.LANCZOS)
        x = (W - img.width) // 2
        y = bar + (avail_h - img.height) // 2 + 20
        sh = Image.new("RGB", (img.width + 24, img.height + 24), (210, 216, 226))
        canvas.paste(sh, (x - 12, y - 12))
        canvas.paste(img, (x, y))
        canvas.save(os.path.join(OUT, "shot_4_popup.png"))
        print("[shot] popup")

    print("screenshots in", OUT)


if __name__ == "__main__":
    main()
