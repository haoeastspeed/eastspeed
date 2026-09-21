# -*- coding: utf-8 -*-
"""网页视频悬浮下载按钮端到端验证（Windows + Edge）。

启动本地服务器（含一个 <video src="sample.mp4"> 页面）与桥接，加载扩展后通过
CDP 在页面里触发 video 的 mouseenter 让悬浮按钮出现并点击，断言任务被桥接接管、
真实下载完成且 SHA256 一致。

运行: python tools/e2e_video_btn.py
"""
import functools
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class _QuietHTTPServer(ThreadingHTTPServer):
    """清理阶段浏览器在途连接被重置属正常，静默这类异常以免污染回归结果。"""

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1],
                      (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)

import e2e_ext  # noqa: E402
from e2e_ext import cdp_eval, cdp_json, cdp_method, sha256_file  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 8803
CDP_PORT = 9341
e2e_ext.CDP_PORT = CDP_PORT  # 让复用的 CDP 辅助函数连本次的调试端口
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
MP4_NAME = "sample.mp4"
MP4_SIZE = 2 * 1024 * 1024

HTML = (
    "<!doctype html><html><head><meta charset='utf-8'><title>vt</title></head>"
    "<body style='margin:0'><h3>video</h3>"
    "<video id='v' src='{mp4}' controls width='640' height='360'></video>"
    "</body></html>"
)


def find_page(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for t in cdp_json("/json/list") or []:
            if t.get("type") == "page" and "video.html" in t.get("url", ""):
                return t["webSocketDebuggerUrl"]
        time.sleep(0.8)
    return None


def main():
    appdata = tempfile.mkdtemp(prefix="dfs-vbtn-appdata-")
    os.environ["APPDATA"] = appdata
    serve = tempfile.mkdtemp(prefix="dfs-vbtn-serve-")
    dl = tempfile.mkdtemp(prefix="dfs-vbtn-dl-")
    udd = tempfile.mkdtemp(prefix="dfs-vbtn-edge-")
    ext_copy = os.path.join(tempfile.mkdtemp(prefix="dfs-vbtn-ext-"), "ext")

    data = os.urandom(MP4_SIZE)
    with open(os.path.join(serve, MP4_NAME), "wb") as f:
        f.write(data)
    want_hash = hashlib.sha256(data).hexdigest()

    shutil.copytree(os.path.join(ROOT, "browser_extension"), ext_copy)
    bg = os.path.join(ext_copy, "background.js")
    with open(bg, "r", encoding="utf-8") as f:
        js = f.read().replace("port: 8765", f"port: {PORT}")
    with open(bg, "w", encoding="utf-8") as f:
        f.write(js)

    from tests.range_server import make_server
    from core.bridge import BridgeServer
    from core.config import Settings
    from core.engine import DownloadManager
    from core.task import TaskState

    httpd, base = make_server(serve)          # A：支持 Range 的下载源
    mp4_url = f"{base}/file/{MP4_NAME}"
    # B：普通网页服务器渲染 video.html（range_server 会强制 attachment，不能托管网页）
    html_handler = functools.partial(SimpleHTTPRequestHandler, directory=serve)
    html_srv = _QuietHTTPServer(("127.0.0.1", 0), html_handler)
    threading.Thread(target=html_srv.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{html_srv.server_address[1]}/video.html"
    with open(os.path.join(serve, "video.html"), "w", encoding="utf-8") as f:
        f.write(HTML.format(mp4=mp4_url))
    settings = Settings(save_dir=dl, takeover_prompt=False, bridge_port=PORT,
                        auto_resume=False, av_scan=False)
    manager = DownloadManager(settings, callbacks={})
    manager.start()
    bridge = BridgeServer(manager, PORT)
    bridge.start()
    print(f"[bridge] port={PORT} page={page_url}")

    args = [
        EDGE,
        f"--user-data-dir={udd}",
        f"--load-extension={ext_copy}",
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check", "--disable-sync",
        "--autoplay-policy=no-user-gesture-required",
        "about:blank",
    ]
    proc = subprocess.Popen(args)
    ok = False
    try:
        # 等浏览器就绪（与 e2e_ext 一致用 /json/list 判定），再用 CDP 显式新建测试页
        bws = None
        end = time.time() + 45
        while time.time() < end:
            if cdp_json("/json/list") is not None:
                ver = cdp_json("/json/version") or {}
                if ver.get("webSocketDebuggerUrl"):
                    bws = ver["webSocketDebuggerUrl"]
                    break
            time.sleep(0.8)
        if not bws:
            print("[FAIL] Edge CDP 未就绪")
            return 1
        time.sleep(3)  # 让扩展 SW 完成启动期配对
        cdp_method(bws, "Target.createTarget", {"url": page_url})
        ws = find_page()
        if not ws:
            print("[FAIL] 测试页面未打开")
            return 1
        print("[ok] 测试页面已打开")

        # 等悬浮按钮注入（document_idle content script）
        injected = None
        deadline = time.time() + 25
        while time.time() < deadline:
            injected = cdp_eval(
                ws, "!!document.getElementById('dfs-video-float-btn')")
            if injected is True:
                break
            time.sleep(0.8)
        if injected is not True:
            print("[FAIL] 视频悬浮按钮未注入:", injected)
            return 1
        print("[ok] 悬浮按钮已注入")

        # 触发 mouseenter 让按钮显示并记录 current，再点击
        click = cdp_eval(
            ws,
            "(async()=>{const v=document.querySelector('video');"
            "v.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true}));"
            "await new Promise(r=>setTimeout(r,300));"
            "const b=document.getElementById('dfs-video-float-btn');"
            "const shown=b?b.style.display:'none';b.click();"
            "await new Promise(r=>setTimeout(r,300));"
            "const txt=b?b.querySelector('.dfs-vbtn-text').textContent:'';"
            "return JSON.stringify({shown,txt});})()")
        print("[edge] click ->", click)

        task = None
        deadline = time.time() + 30
        while time.time() < deadline:
            for t in manager.tasks.values():
                if t.url.split("?")[0].endswith(MP4_NAME):
                    task = t
                    break
            if task:
                break
            time.sleep(0.5)
        if task is None:
            print("[FAIL] 悬浮按钮未把视频转交给桥接")
            return 1
        print("[ok] 已接管视频:", task.task_id, task.filename)

        deadline = time.time() + 60
        while time.time() < deadline and task.state not in (
                TaskState.COMPLETED, TaskState.ERROR):
            time.sleep(0.5)
        if task.state != TaskState.COMPLETED:
            print("[FAIL] 任务未完成:", task.state, task.error_msg)
            return 1
        final = os.path.join(dl, task.filename)
        if sha256_file(final) != want_hash:
            print("[FAIL] 哈希不一致")
            return 1
        print("[ok] 视频下载完成且 SHA256 一致:", task.filename)
        ok = True
        return 0
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        # kill 浏览器可能使在途连接重置，逐个容错关闭，不影响 PASS 结论
        for fn in (getattr(bridge, "stop", None),
                   getattr(manager, "shutdown", None),
                   getattr(httpd, "shutdown", None),
                   getattr(html_srv, "shutdown", None)):
            try:
                if fn:
                    fn()
            except Exception:
                pass
        for d in (appdata, serve, dl, udd):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(os.path.dirname(ext_copy), ignore_errors=True)
        print("[result]", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    sys.exit(main())
