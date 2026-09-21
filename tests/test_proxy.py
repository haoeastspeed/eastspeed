# -*- coding: utf-8 -*-
"""代理认证与 PAC 测试。

- Basic 代理认证（URL 内嵌凭据 / 显式字段 / 错误密码 407）；
- NTLM 代理认证（用 pyspnego 本机完成完整 NTLM 握手，验证 requests_ntlm
  的 407 hook 被正确挂载）；
- PAC 返回指令解析；Windows 上用 WinHTTP 真实解析 http PAC（系统不支持则 skip）。
"""
import base64
import http.client
import http.server
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.parse import urlparse

from core.probe import build_session, session_kwargs
from core.proxy_support import (
    _first_proxy_from_directive,
    resolve_pac_proxy,
)

PAYLOAD = b"DongFangSpeed-proxy-" * 4096  # ~100 KB


def _cfg(**kw):
    base = dict(
        proxy="", proxy_auth=None, proxy_username=None, proxy_password=None,
        proxy_domain=None, pac_url=None, user_agent="t/1.0", referer="",
        headers=None, cookies=None, prefer_http2=False, username="",
        password="", connect_timeout=10, read_timeout=20, verify_ssl=True,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _Target(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAYLOAD
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(body)


class _Proxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _challenge(self, header_value):
        self.send_response(407)
        self.send_header("Proxy-Authenticate", header_value)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

    def _forward(self):
        u = urlparse(self.path)
        conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=10)
        conn.request("GET", u.path or "/")
        r = conn.getresponse()
        body = r.read()
        self.send_response(r.status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        mode = self.server.mode
        auth = self.headers.get("Proxy-Authorization", "")
        if mode == "basic":
            expect = "Basic " + base64.b64encode(b"user:pass").decode()
            if auth != expect:
                return self._challenge('Basic realm="proxy"')
            return self._forward()
        # NTLM
        if not auth:
            return self._challenge("NTLM")
        scheme, _, tok = auth.partition(" ")
        data = base64.b64decode(tok.strip())
        with self.server.lock:
            out = self.server.ntlm.step(data)
            if not self.server.ntlm.complete:
                return self._challenge(
                    "NTLM " + base64.b64encode(out or b"").decode())
        return self._forward()


def _start(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class ProxyAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = _start(_Target)
        _, tp = cls.target.server_address[:2]
        cls.target_url = f"http://127.0.0.1:{tp}/file.bin"

        cls.basic = _start(_Proxy)
        cls.basic.mode = "basic"
        _, bp = cls.basic.server_address[:2]
        cls.basic_url = f"http://127.0.0.1:{bp}"

        cls.ntlm = _start(_Proxy)
        cls.ntlm.mode = "ntlm"
        cls.ntlm.lock = threading.Lock()
        _, np = cls.ntlm.server_address[:2]
        cls.ntlm_url = f"http://127.0.0.1:{np}"

    @classmethod
    def tearDownClass(cls):
        for s in (cls.target, cls.basic, cls.ntlm):
            s.shutdown()

    def _get(self, cfg):
        s = build_session(cfg)
        try:
            r = s.get(self.target_url, **session_kwargs(cfg))
            return r
        finally:
            s.close()

    def test_basic_url_credentials(self):
        r = self._get(_cfg(proxy=f"{self.basic_url}".replace(
            "http://", "http://user:pass@", 1)))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PAYLOAD)

    def test_basic_explicit_fields(self):
        r = self._get(_cfg(proxy=self.basic_url,
                           proxy_auth="basic",
                           proxy_username="user",
                           proxy_password="pass"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PAYLOAD)

    def test_basic_wrong_password_407(self):
        r = self._get(_cfg(proxy=self.basic_url.replace(
            "http://", "http://user:wrong@", 1)))
        self.assertEqual(r.status_code, 407)

    def test_ntlm_proxy_handshake(self):
        try:
            import spnego  # noqa: F401
            import requests_ntlm  # noqa: F401
        except ImportError:
            self.skipTest("缺少 pyspnego/requests_ntlm")
        # 为 NTLM 服务端准备本机凭据文件
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.write(fd, b":alice:secret\n")
        os.close(fd)
        old = os.environ.get("NTLM_USER_FILE")
        os.environ["NTLM_USER_FILE"] = path
        try:
            import spnego
            with self.ntlm.lock:
                self.ntlm.ntlm = spnego.server(
                    hostname="proxy", service="http", protocol="ntlm",
                    options=spnego.NegotiateOptions.use_ntlm)
            r = self._get(_cfg(proxy=self.ntlm_url,
                               proxy_auth="ntlm",
                               proxy_username="alice",
                               proxy_password="secret"))
            self.assertEqual(r.status_code, 200, "NTLM 握手后应成功下载")
            self.assertEqual(r.content, PAYLOAD)
        finally:
            if old is None:
                os.environ.pop("NTLM_USER_FILE", None)
            else:
                os.environ["NTLM_USER_FILE"] = old
            os.remove(path)


class PacParseTests(unittest.TestCase):
    def test_directives(self):
        self.assertEqual(
            _first_proxy_from_directive("PROXY 127.0.0.1:7890; DIRECT"),
            "http://127.0.0.1:7890")
        self.assertEqual(_first_proxy_from_directive("DIRECT"), "")
        self.assertEqual(
            _first_proxy_from_directive("PROXY proxy.corp:8080"),
            "http://proxy.corp:8080")
        self.assertEqual(
            _first_proxy_from_directive("SOCKS5 127.0.0.1:1080"),
            "socks5://127.0.0.1:1080")

    @unittest.skipUnless(os.name == "nt", "WinHTTP PAC 仅 Windows")
    def test_winhttp_resolves_http_pac(self):
        class Pac(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = self.server.pac_body
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = _start(Pac)
        _, port = srv.server_address[:2]
        srv.pac_body = (
            "function FindProxyForURL(url, host){"
            f"return 'PROXY 127.0.0.1:{port}';"
            "}").encode()
        try:
            pac_url = f"http://127.0.0.1:{port}/proxy.pac"
            result = resolve_pac_proxy(pac_url, "http://example.com/a.zip")
            if result is None:
                self.skipTest("当前系统 WinHTTP 未能执行 PAC（环境限制）")
            self.assertEqual(result, f"http://127.0.0.1:{port}")
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
