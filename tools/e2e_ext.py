# -*- coding: utf-8 -*-
"""浏览器扩展端到端验证（Windows + Edge）。

流程：
1) 启动本地 Range 文件服务器与东方神速桥接（独立端口、独立 APPDATA，不影响已装实例）；
2) 复制 browser_extension 到临时目录并把默认端口改成桥接端口；
3) 用独立 user-data-dir 的 Edge 以 --load-extension 加载扩展；
4) 通过 CDP 在扩展 Service Worker 内调用 chrome.downloads.download 创建一个真实下载
   （与用户点击下载链接等价，同样触发 chrome.downloads.onCreated；用扩展 API 而非页面
   点击，是为了绕开 CDP 自动化对“页面发起下载”的默认拦截，保证测试可重复）；
5) 断言扩展完成自动配对、取消浏览器侧下载并 POST 到 /add，任务真实下载完成且哈希一致。

运行: python tools/e2e_ext.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 8799       # 独立端口，避免与用户正在运行的实例冲突
CDP_PORT = 9333
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FILE_NAME = "e2e-data.zip"
FILE_SIZE = 5 * 1024 * 1024


def cdp_json(path):
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{CDP_PORT}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _ws_call(wsurl, messages, timeout=20):
    """通过 QWebSocket 发送若干 CDP 消息，返回 id 最大的响应（按 id 收集）。"""
    from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PyQt5.QtWebSockets import QWebSocket
    app = QCoreApplication.instance() or QCoreApplication([])
    ws = QWebSocket()
    want = max(m["id"] for m in messages)
    box = {}
    loop = QEventLoop()

    def on_text(msg):
        d = json.loads(msg)
        if d.get("id") in (m["id"] for m in messages):
            box[d["id"]] = d
        if d.get("id") == want:
            loop.quit()

    ws.textMessageReceived.connect(on_text)
    op = QEventLoop()
    ws.connected.connect(op.quit)
    ws.open(QUrl(wsurl))
    QTimer.singleShot(3000, op.quit)
    op.exec()
    for m in messages:
        ws.sendTextMessage(json.dumps(m))
    QTimer.singleShot(timeout * 1000, loop.quit)
    loop.exec()
    ws.close()
    return box


def cdp_eval(wsurl, expr, timeout=20):
    box = _ws_call(wsurl, [
        {"id": 1, "method": "Runtime.enable"},
        {"id": 2, "method": "Runtime.evaluate",
         "params": {"expression": expr, "awaitPromise": True,
                    "returnByValue": True}},
    ], timeout=timeout)
    r = box.get(2, {})
    return r.get("result", {}).get("result", {}).get("value") or r


def cdp_method(wsurl, method, params=None, timeout=12):
    box = _ws_call(wsurl, [{"id": 2, "method": method, "params": params or {}}],
                   timeout=timeout)
    return box.get(2)


def find_worker(timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for t in cdp_json("/json/list") or []:
            if t.get("type") == "service_worker" and "background.js" in t.get("url", ""):
                return t["webSocketDebuggerUrl"], t["url"]
        time.sleep(0.8)
    return None, None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    appdata = tempfile.mkdtemp(prefix="dfs-e2e-appdata-")
    os.environ["APPDATA"] = appdata
    serve = tempfile.mkdtemp(prefix="dfs-e2e-serve-")
    dl = tempfile.mkdtemp(prefix="dfs-e2e-dl-")
    udd = tempfile.mkdtemp(prefix="dfs-e2e-edge-")
    ext_copy = os.path.join(tempfile.mkdtemp(prefix="dfs-e2e-ext-"), "ext")

    data = os.urandom(FILE_SIZE)
    with open(os.path.join(serve, FILE_NAME), "wb") as f:
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

    httpd, base = make_server(serve)
    url = f"{base}/file/{FILE_NAME}"
    settings = Settings(save_dir=dl, takeover_prompt=False, bridge_port=PORT,
                        auto_resume=False, av_scan=False)
    manager = DownloadManager(settings, callbacks={})
    manager.start()
    bridge = BridgeServer(manager, PORT)
    bridge.start()
    print(f"[bridge] token={bridge.token} port={PORT}")

    args = [
        EDGE,
        f"--user-data-dir={udd}",
        f"--load-extension={ext_copy}",
        f"--remote-debugging-port={CDP_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check",
        "--disable-sync", "--no-pings",
        "--disable-features=msEdgeFirstRunFeature,msEdgeWelcomeFLX,"
        "EdgeAccountSetupUX,msIdentityDD,SyncDiagnostics,msEdgeEDNFirstRunExperience",
        "about:blank",
    ]
    proc = subprocess.Popen(args)
    print("[edge] launched, waiting for extension service worker ...")
    wsurl, worker = find_worker()
    if not wsurl:
        print("[FAIL] 扩展后台未加载（--load-extension 可能被策略禁用）")
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        return 1
    print("[ok] extension worker:", worker)

    # CDP 自动化默认拦截下载，需在 browser 级显式允许（扩展 API 下载同样受此约束）
    ver = cdp_json("/json/version") or {}
    bws = ver.get("webSocketDebuggerUrl")
    if bws:
        cdp_method(bws, "Browser.setDownloadBehavior",
                   {"behavior": "allow", "downloadPath": dl,
                    "eventsEnabled": True})

    # 给 SW 一点时间完成启动期 /ping + /pair
    time.sleep(2)

    # 在扩展 SW 内创建真实下载，触发 chrome.downloads.onCreated 自动接管
    trig = cdp_eval(
        wsurl,
        "(async()=>{try{const id=await new Promise((res,rej)=>"
        "chrome.downloads.download({"
        f"url:'{url}'"
        "},i=>{if(chrome.runtime.lastError)"
        "rej(new Error(chrome.runtime.lastError.message));else res(i);}));"
        "return 'download id='+id;}catch(e){return 'ERR '+e.message;}})()")
    print("[edge] downloads.download ->", trig)
    if not isinstance(trig, str) or not trig.startswith("download id="):
        print("[FAIL] 浏览器侧下载未创建:", trig)
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        return 1

    ok = False
    task = None
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            for t in manager.tasks.values():
                if t.url.split("?")[0].endswith(FILE_NAME):
                    task = t
                    break
            if task:
                break
            time.sleep(0.5)

        if task is None:
            print("[FAIL] 扩展未把任务转交给桥接（自动配对或接管失败）")
            return 1
        print("[ok] 已通过扩展自动配对并接管任务:", task.task_id, task.filename)

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
        print("[ok] 下载完成且 SHA256 一致:", task.filename)
        ok = True
        return 0
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        bridge.stop()
        manager.shutdown()
        httpd.shutdown()
        for d in (appdata, serve, dl, udd):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(os.path.dirname(ext_copy), ignore_errors=True)
        print("[result]", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    sys.exit(main())
