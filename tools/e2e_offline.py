# -*- coding: utf-8 -*-
"""扩展异常路径验证：本地程序“不在线”（桥接未启动）时，扩展不得劫持下载，
应放行给浏览器原生下载。判定：chrome.downloads.download 创建的下载在 onCreated
之后仍保留在浏览器下载列表中（未被扩展 cancel/erase），且文件由浏览器真实落盘。

运行: python tools/e2e_offline.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e2e_ext as e2e  # 复用 CDP/Edge 启动辅助

DEAD_PORT = 8798   # 该端口没有任何桥接在监听，模拟“程序未运行”
CDP_PORT = 9335
EDGE = e2e.EDGE
FILE_NAME = "offline-data.bin"


def main():
    serve = tempfile.mkdtemp(prefix="dfs-off-serve-")
    dl = tempfile.mkdtemp(prefix="dfs-off-dl-")
    udd = tempfile.mkdtemp(prefix="dfs-off-edge-")
    ext_copy = os.path.join(tempfile.mkdtemp(prefix="dfs-off-ext-"), "ext")
    with open(os.path.join(serve, FILE_NAME), "wb") as f:
        f.write(os.urandom(1024 * 1024))
    SAFE_NAME = "offline-note.txt"
    with open(os.path.join(serve, SAFE_NAME), "wb") as f:
        f.write(b"safe text " * 120000)  # 约 1.08MB

    shutil.copytree(os.path.join(ROOT, "browser_extension"), ext_copy)
    bg = os.path.join(ext_copy, "background.js")
    with open(bg, "r", encoding="utf-8") as f:
        js = f.read().replace("port: 8765", f"port: {DEAD_PORT}")
    with open(bg, "w", encoding="utf-8") as f:
        f.write(js)

    from tests.range_server import make_server
    # 用 8799（本机安全软件放行的回环端口；离线场景桥接未启动，端口空闲）
    httpd, base = make_server(serve, port=8799)
    url_bin = f"{base}/file/{FILE_NAME}"
    url = f"{base}/file/{SAFE_NAME}"
    safe_size = len(b"safe text " * 120000)

    # 覆盖 e2e 模块里的 CDP 端口常量（其函数引用模块全局）
    e2e.CDP_PORT = CDP_PORT
    proc = subprocess.Popen([
        EDGE, f"--user-data-dir={udd}", f"--load-extension={ext_copy}",
        f"--remote-debugging-port={CDP_PORT}", "--remote-allow-origins=*",
        "--no-first-run", "--no-default-browser-check", "--disable-sync",
        "--disable-features=msEdgeFirstRunFeature,msEdgeWelcomeFLX,"
        "EdgeAccountSetupUX,msIdentityDD,SyncDiagnostics",
        "about:blank"])
    ok = False
    native_fp = ""
    try:
        wsurl, worker = e2e.find_worker()
        if not wsurl:
            print("[FAIL] 扩展后台未加载"); return 1
        print("[ok] worker:", worker)
        ver = e2e.cdp_json("/json/version") or {}
        bws = ver.get("webSocketDebuggerUrl")
        if bws:
            # 不指定 downloadPath：独立 profile 下强制绝对路径会让下载在发起前中断
            e2e.cdp_method(bws, "Browser.setDownloadBehavior",
                           {"behavior": "allow", "eventsEnabled": True})
        # 给 SW 启动期 connect 一定时间（对死端口应快速失败并保持离线）
        time.sleep(3)
        online = e2e.cdp_eval(wsurl, "(async()=>{await connect();"
                                     "return JSON.stringify({online});})()")
        print("[sw] connect() ->", online)
        # 对照：.bin（疑似被安全软件下载防护拦）与 .txt（普通文本）
        probe = e2e.cdp_eval(
            wsurl,
            "(async()=>{async function t(u){try{const r=await fetch(u);"
            "const b=await r.arrayBuffer();return r.status+'/'+b.byteLength;}"
            "catch(e){return 'ERR:'+e.message;}}"
            "return JSON.stringify({"
            f"bin:await t('{url_bin}'),"
            f"txt:await t('{url}'),"
            "dead:await t('http://127.0.0.1:8798/ping')});})()")
        print("[sw] fetch probe ->", probe)
        import urllib.request as _u
        try:
            _rr = _u.urlopen(url, timeout=5)
            print("[py] direct GET ->", _rr.status, len(_rr.read()),
                  "ctype=", _rr.headers.get("Content-Type"))
        except Exception as exc:  # noqa: BLE001
            print("[py] direct GET ERR ->", exc)
        trig = e2e.cdp_eval(
            wsurl,
            "(async()=>{try{const id=await new Promise((res,rej)=>"
            "chrome.downloads.download({"
            f"url:'{url}'"
            "},i=>{if(chrome.runtime.lastError)"
            "rej(new Error(chrome.runtime.lastError.message));else res(i);}));"
            "return 'download id='+id;}catch(e){return 'ERR '+e.message;}})()")
        print("[edge] downloads.download ->", trig)
        time.sleep(5)
        remaining = e2e.cdp_eval(
            wsurl,
            "(async()=>{const ds=await new Promise(r=>"
            "chrome.downloads.search({orderBy:['-startTime']},r));"
            "const d=ds[0]||{};return JSON.stringify({"
            "st:d.state,err:d.error,fp:d.filename,"
            "got:d.bytesReceived,total:d.totalBytes});})()")
        print("[sw] download after onCreated ->", remaining)
        import json as _json
        info = {}
        try:
            info = _json.loads(remaining) if isinstance(remaining, str) else {}
        except ValueError:
            pass
        native_fp = info.get("fp") or ""
        # 期望：浏览器侧下载仍在（未被扩展 erase），且文件由浏览器真实落盘
        kept = bool(native_fp) and SAFE_NAME in native_fp \
            and info.get("st") != "cancelled"
        file_ok = (bool(native_fp) and os.path.exists(native_fp)
                   and os.path.getsize(native_fp) == safe_size)
        print("[browser] native path ->", native_fp)
        if kept and file_ok:
            print("[ok] 程序离线时扩展未劫持，浏览器原生下载正常完成")
            ok = True
        else:
            print("[FAIL] 离线时下载被扩展取消或未落盘（kept=%s file_ok=%s info=%s）"
                  % (kept, file_ok, info))
        return 0 if ok else 1
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
        httpd.shutdown()
        # 清理可能落到浏览器真实下载目录的测试文件
        import glob
        dl_dir = os.path.dirname(native_fp) if native_fp else os.path.join(
            os.path.expanduser("~"), "Downloads")
        for pat in ("offline-note.txt*", "offline-data.bin*"):
            for f in glob.glob(os.path.join(dl_dir, pat)):
                try:
                    os.remove(f)
                except OSError:
                    pass
        for d in (serve, dl, udd):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(os.path.dirname(ext_copy), ignore_errors=True)
        print("[result]", "PASS" if ok else "FAIL")


if __name__ == "__main__":
    sys.exit(main())
