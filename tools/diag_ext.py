# -*- coding: utf-8 -*-
"""扩展接管诊断：打开自动点击下载链接的页面，再在扩展 SW 内查询浏览器下载记录，
判断下载是否创建、onCreated 是否运行、配对是否成功。"""
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 8799
CDP_PORT = 9334
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
AUTO_NAME = "diag-auto.zip"


def cdp_json(path):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"_err": str(e)}


def find_worker(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for t in cdp_json("/json/list") or []:
            if t.get("type") == "service_worker" and "background.js" in t.get("url", ""):
                return t["webSocketDebuggerUrl"], t["url"]
        time.sleep(0.8)
    return None, None


def cdp_eval(wsurl, expr, timeout=20):
    from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PyQt5.QtWebSockets import QWebSocket
    app = QCoreApplication.instance() or QCoreApplication([])
    ws = QWebSocket()
    box = {}
    loop = QEventLoop()
    ws.textMessageReceived.connect(lambda msg: (
        box.__setitem__("r", json.loads(msg)),
        loop.quit()) if json.loads(msg).get("id") == 2 else None)
    op = QEventLoop()
    ws.connected.connect(op.quit)
    ws.open(QUrl(wsurl))
    QTimer.singleShot(3000, op.quit)
    op.exec()
    ws.sendTextMessage(json.dumps({"id": 1, "method": "Runtime.enable"}))
    ws.sendTextMessage(json.dumps({"id": 2, "method": "Runtime.evaluate",
                                   "params": {"expression": expr,
                                              "awaitPromise": True,
                                              "returnByValue": True}}))
    QTimer.singleShot(timeout * 1000, loop.quit)
    loop.exec()
    ws.close()
    r = box.get("r", {})
    return r.get("result", {}).get("result", {}).get("value") or r


def cdp_method(wsurl, method, params=None, timeout=12):
    from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PyQt5.QtWebSockets import QWebSocket
    app = QCoreApplication.instance() or QCoreApplication([])
    ws = QWebSocket()
    box = {}
    loop = QEventLoop()

    def on_text(msg):
        d = json.loads(msg)
        if d.get("id") == 2:
            box["r"] = d
            loop.quit()

    ws.textMessageReceived.connect(on_text)
    op = QEventLoop()
    ws.connected.connect(op.quit)
    ws.open(QUrl(wsurl))
    QTimer.singleShot(3000, op.quit)
    op.exec()
    ws.sendTextMessage(json.dumps(
        {"id": 2, "method": method, "params": params or {}}))
    QTimer.singleShot(timeout * 1000, loop.quit)
    loop.exec()
    ws.close()
    return box.get("r")


def main():
    appdata = tempfile.mkdtemp(prefix="dfs-diag-appdata-")
    os.environ["APPDATA"] = appdata
    serve = tempfile.mkdtemp(prefix="dfs-diag-serve-")
    dl = tempfile.mkdtemp(prefix="dfs-diag-dl-")
    udd = tempfile.mkdtemp(prefix="dfs-diag-edge-")
    ext_copy = os.path.join(tempfile.mkdtemp(prefix="dfs-diag-ext-"), "ext")
    with open(os.path.join(serve, AUTO_NAME), "wb") as f:
        f.write(os.urandom(2 * 1024 * 1024))

    shutil.copytree(os.path.join(ROOT, "browser_extension"), ext_copy)
    with open(os.path.join(ext_copy, "background.js"), "r", encoding="utf-8") as f:
        js = f.read().replace("port: 8765", f"port: {PORT}")
    with open(os.path.join(ext_copy, "background.js"), "w", encoding="utf-8") as f:
        f.write(js)

    from tests.range_server import make_server
    from core.bridge import BridgeServer
    from core.config import Settings
    from core.engine import DownloadManager

    httpd, base = make_server(serve)  # 保留 Range 服务器（收尾引用）

    html_dir = tempfile.mkdtemp(prefix="dfs-diag-html-")

    class _H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=html_dir, **k)

        def log_message(self, *a):
            pass

    html_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=html_srv.serve_forever, daemon=True).start()
    hport = html_srv.server_address[1]
    # zip 与页面同源，由同一服务以 application/zip 提供
    with open(os.path.join(html_dir, AUTO_NAME), "wb") as f:
        f.write(os.urandom(2 * 1024 * 1024))
    zip_url = f"http://127.0.0.1:{hport}/{AUTO_NAME}"
    with open(os.path.join(html_dir, "auto.html"), "w", encoding="utf-8") as f:
        f.write(
            "<!doctype html><html><head><meta charset='utf-8'></head><body>"
            "<h3>auto download test</h3>"
            f"<a id='l' href='{zip_url}'>manual</a>"
            "<script>window.addEventListener('load',function(){"
            f"setTimeout(function(){{window.location.href='{zip_url}';}},1200);"
            "});</script></body></html>")
    html_url = f"http://127.0.0.1:{hport}/auto.html"

    settings = Settings(save_dir=dl, takeover_prompt=False, bridge_port=PORT,
                        auto_resume=False, av_scan=False)
    manager = DownloadManager(settings, callbacks={})
    manager.start()
    bridge = BridgeServer(manager, PORT)
    bridge.start()

    proc = subprocess.Popen([
        EDGE, f"--user-data-dir={udd}", f"--load-extension={ext_copy}",
        f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--no-pings",
        "--disable-features=msEdgeFirstRunFeature,msEdgeWelcomeFLX,"
        "EdgeAccountSetupUX,msIdentityDD,SyncDiagnostics,msEdgeEDNFirstRunExperience",
        "--disable-popup-blocking", html_url])
    try:
        wsurl, swurl = find_worker()
        print("worker:", swurl)
        if not wsurl:
            print("NO WORKER"); return 1
        # 等待页面加载并自动点击（1.2s）+ 下载事件传播
        time.sleep(6)
        pages = [(t.get("type"), t.get("url"))
                 for t in (cdp_json("/json/list") or [])
                 if t.get("type") in ("page",)]
        print("PAGES ->", pages)

        # 直接在页面上下文手动触发一次点击，排除页面定时器未执行的因素
        # CDP 自动化默认会取消下载，需在 browser 级显式允许
        ver = cdp_json("/json/version")
        bws = ver.get("webSocketDebuggerUrl") if isinstance(ver, dict) else None
        if bws:
            print("setDownloadBehavior ->",
                  cdp_method(bws, "Browser.setDownloadBehavior",
                             {"behavior": "allow", "downloadPath": dl,
                              "eventsEnabled": True}))
        # 用扩展自身的 chrome.downloads.download 在 SW 内确定性地创建一个真实下载，
        # 这会像用户点击一样触发 chrome.downloads.onCreated（且不受页面自动化下载管控）
        dl_trigger = cdp_eval(
            wsurl,
            "(async()=>{try{const id=await new Promise((res,rej)=>"
            "chrome.downloads.download({"
            f"url:'{zip_url}'"
            "},i=>{if(chrome.runtime.lastError)"
            "rej(new Error(chrome.runtime.lastError.message));else res(i);}));"
            "return 'download id='+id;}catch(e){return 'ERR '+e.message;}})()",
            timeout=10)
        print("SW downloads.download ->", dl_trigger)
        time.sleep(6)
        state = cdp_eval(wsurl, "(async()=>{try{"
                                "const ok=await connect();"
                                "const ds=await new Promise(r=>chrome.downloads.search("
                                "{orderBy:['-startTime']},r));"
                                "return JSON.stringify({online:ok,cfgOn:cfg.enabled,"
                                "takeover:cfg.takeover,hasToken:!!cfg.token,"
                                "downloads:ds.slice(0,5).map(d=>({"
                                "u:(d.url||'').split('/').pop(),st:d.state}))});"
                                "}catch(e){return 'ERR '+e.message;}})()")
        print("SW STATE ->", state)
        print("manager tasks:", [(t.filename, t.state) for t in manager.tasks.values()])
        print("dl dir:", os.listdir(dl))
        # 检查浏览器默认下载目录（udd 内 profile Default）
        prof_dl = os.path.join(udd, "Default", "Downloads")
        if os.path.isdir(prof_dl):
            print("browser downloads:", os.listdir(prof_dl))
        time.sleep(1)
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        bridge.stop(); manager.shutdown(); httpd.shutdown(); html_srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
