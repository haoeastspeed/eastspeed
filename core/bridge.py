# -*- coding: utf-8 -*-
"""本地 HTTP 桥接服务（仅绑定 127.0.0.1）。

为后续浏览器扩展预留：扩展拦截到下载请求后，POST 到
``http://127.0.0.1:<port>/add`` 即可把任务交给本程序::

    {"url": "https://example.com/file.zip", "filename": "可选"}

请求需携带 ``X-PyDL-Token`` 头（token 保存在 %APPDATA%/DongFangSpeed/bridge.token），
防止其他网页随意发起下载。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .branding import APP_NAME
from .category import category_dir_for
from .config import app_data_dir
from .task_options import TaskOptions


def _safe_name(name: str) -> str:
    name = os.path.basename((name or "").replace("\\", "/").strip())
    name = re.sub(r'[\\/:*?"<>|\x00]', "_", name)
    return name[:180] or "blob-download.bin"


def _unique_path(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _load_or_create_token() -> str:
    path = os.path.join(app_data_dir(), "bridge.token")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                token = f.read().strip()
            if token:
                return token
        except OSError:
            pass
    token = secrets.token_urlsafe(24)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def get_bridge_token() -> str:
    """读取（必要时创建）桥接 Token，供界面展示与复制。"""
    return _load_or_create_token()


class BridgeServer:
    def __init__(self, manager, port: int = 8765):
        self.manager = manager
        self.port = port
        self.token = _load_or_create_token()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread = None

    def start(self) -> None:
        manager = self.manager
        token = self.token

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"  # keep-alive，避免 HTTP/1.0 关连接的 RST 竞态

            def log_message(self, *args):  # 默认静默
                if os.environ.get("DFS_BRIDGE_DEBUG"):
                    sys.stderr.write(
                        f"[bridge] {self.command} {self.path} "
                        f"origin={self.headers.get('Origin', '')!r} "
                        f"acrh={self.headers.get('Access-Control-Request-Headers', '')!r}\n")
                    sys.stderr.flush()

            def _origin(self) -> str:
                return self.headers.get("Origin", "") or ""

            def _is_extension_origin(self) -> bool:
                """扩展后台页来源为 chrome-extension:// / moz-extension://；
                Service Worker 的 fetch 可能完全不带 Origin。普通网页是 http(s)://。"""
                origin = self._origin()
                if not origin:
                    return True
                return origin.startswith(("chrome-extension://",
                                          "moz-extension://", "edge-extension://"))

            def _cors(self):
                # 普通接口（不含敏感信息，/add 仍有 Token 校验）允许任意来源
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, X-PyDL-Token, X-PyDL-Client, X-Blob-Name, X-Blob-Size")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                # 允许公网 HTTPS 页面访问本机回环（Private Network Access）
                self.send_header("Access-Control-Allow-Private-Network", "true")

            def _json(self, code: int, body: dict):
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._cors()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                if self.path == "/ping":
                    self._json(200, {"ok": True, "name": APP_NAME})
                elif self.path == "/pair":
                    self._handle_pair()
                else:
                    self._json(404, {"error": "not found"})

            def _handle_pair(self):
                """免填 Token 的本机自动配对（简单 GET，不触发 CORS 预检/PNA 预检）。

                安全模型：服务只绑定回环，Token 本就明文存于本机，配对只决定“浏览器内
                哪个上下文能拿到 Token”。浏览器跨域 fetch 必带 Origin：
                - 扩展后台页 Origin 为 chrome-extension://（Service Worker 也可能不带
                  Origin），放行并返回 Token；
                - 普通网页 Origin 为 http(s)://，一律 403 且不返回 Token，从而阻止
                  网页 CSRF 静默获取 Token（配合 /add 的 Token 校验）。
                """
                origin = self._origin().lower()
                if origin.startswith(("http://", "https://")):
                    self._json(403, {"error": "forbidden"})
                    return
                data = json.dumps(
                    {"ok": True, "token": token, "name": APP_NAME},
                    ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._cors()
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path == "/add-blob":
                    self._handle_add_blob()
                    return
                if self.path != "/add":
                    self._json(404, {"error": "not found"})
                    return
                if self.headers.get("X-PyDL-Token", "") != token:
                    self._json(403, {"error": "bad token"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    url = (payload or {}).get("url", "").strip()
                except (ValueError, UnicodeDecodeError):
                    self._json(400, {"error": "bad request"})
                    return
                if not url.lower().startswith(("http://", "https://")):
                    self._json(400, {"error": "invalid url"})
                    return
                opt = TaskOptions(
                    filename=payload.get("filename") or None,
                    save_dir=payload.get("save_dir") or None,
                    referer=payload.get("referer") or None,
                    cookies=payload.get("cookies"),
                    headers=payload.get("headers"),
                )
                inbound = manager.callbacks.get("on_inbound")
                if inbound and getattr(manager.settings, "takeover_prompt", True):
                    # 交给 GUI 弹“新建下载”确认窗（在界面线程阻塞等待用户决定）
                    try:
                        task_id = inbound(payload)
                    except Exception:  # noqa: BLE001  弹窗异常则退回直接添加
                        task_id = None
                    if not task_id:
                        self._json(200, {"ok": True, "cancelled": True})
                        return
                    self._json(200, {"ok": True, "task_id": task_id})
                    return
                task = manager.add(url, opt)
                self._json(200, {"ok": True, "task_id": task.task_id})

            def _handle_add_blob(self):
                """接收浏览器 content script 读出的 blob 二进制并直接落盘。"""
                if self.headers.get("X-PyDL-Token", "") != token:
                    self._json(403, {"error": "bad token"})
                    return
                name = _safe_name(self.headers.get("X-Blob-Name", "blob.bin"))
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    length = 0
                if length <= 0:
                    self._json(400, {"error": "empty blob"})
                    return
                settings = manager.settings
                save_dir = category_dir_for(
                    name, getattr(settings, "auto_categorize", False),
                    settings.save_dir)
                os.makedirs(save_dir, exist_ok=True)
                final = _unique_path(os.path.join(save_dir, name))
                try:
                    remaining = length
                    with open(final, "wb") as f:
                        while remaining > 0:
                            chunk = self.rfile.read(min(1 << 20, remaining))
                            if not chunk:
                                break
                            f.write(chunk)
                            remaining -= len(chunk)
                    written = os.path.getsize(final)
                except OSError as exc:
                    self._json(500, {"error": f"write failed: {exc}"})
                    return
                if written != length:
                    self._json(400, {"error": "incomplete blob"})
                    return
                self._json(200, {"ok": True, "filename": name,
                                 "path": final, "size": written})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True,
                                        name="bridge-http")
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
