# -*- coding: utf-8 -*-
"""测试用 HTTPS(HTTP/2) 服务器：hypercorn + 自签证书，支持 Range(206)。

记录实际协商的 HTTP 版本（scope["http_version"]），供测试断言确实走了 h2。
"""
import asyncio
import datetime
import os
import socket
import tempfile
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _gen_cert(cert_path: str, key_path: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc)
                         - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc)
                         + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


class H2TestServer:
    def __init__(self, files: dict):
        # files: {路径: bytes}
        self.files = files
        self.versions: set[str] = set()
        # 先绑定随机端口再释放交给 hypercorn（测试环境竞态概率极低）
        probe = socket.create_server(("127.0.0.1", 0))
        self.port = probe.getsockname()[1]
        probe.close()
        d = tempfile.mkdtemp(prefix="pydl-h2-")
        self.cert = os.path.join(d, "cert.pem")
        self.key = os.path.join(d, "key.pem")
        _gen_cert(self.cert, self.key)
        self._loop = asyncio.new_event_loop()
        self._shutdown = asyncio.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="test-h2")

    def _make_app(self):
        async def app(scope, receive, send):
            if scope["type"] != "http":
                return
            self.versions.add(str(scope.get("http_version")))
            data = self.files.get(scope["path"])
            if data is None:
                await send({"type": "http.response.start", "status": 404,
                            "headers": [b"content-length", b"0"]})
                await send({"type": "http.response.body", "body": b""})
                return
            headers_in = {k.decode().lower(): v.decode()
                          for k, v in scope.get("headers", [])}
            status = 200
            body = data
            rng = headers_in.get("range", "")
            if rng.startswith("bytes="):
                spec = rng[len("bytes="):].split(",")[0].strip()
                start_s, _, end_s = spec.partition("-")
                start = int(start_s) if start_s else 0
                end = int(end_s) if end_s else len(data) - 1
                end = min(end, len(data) - 1)
                body = data[start:end + 1]
                status = 206
                cr = f"bytes {start}-{end}/{len(data)}".encode()
                headers = [
                    (b"content-range", cr),
                    (b"content-length", str(len(body)).encode()),
                    (b"accept-ranges", b"bytes"),
                ]
            else:
                headers = [
                    (b"content-length", str(len(body)).encode()),
                    (b"accept-ranges", b"bytes"),
                ]
            await send({"type": "http.response.start", "status": status,
                        "headers": headers})
            await send({"type": "http.response.body", "body": body})
        return app

    def _run(self):
        asyncio.set_event_loop(self._loop)
        # hypercorn 在 event loop 关闭后仍打日志会触发 logging 格式化异常，测试中静默
        import logging as _logging
        for _n in ("hypercorn", "hypercorn.access", "hypercorn.error"):
            _logging.getLogger(_n).disabled = True
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        cfg = Config()
        cfg.certfile = self.cert
        cfg.keyfile = self.key
        cfg.alpn_protocols = ["h2", "http/1.1"]
        cfg.bind = [f"127.0.0.1:{self.port}"]
        cfg.accesslog = None
        cfg.errorlog = None

        async def wait_shutdown():
            self._ready.set()
            await self._shutdown.wait()

        self._loop.run_until_complete(
            serve(self._make_app(), cfg, shutdown_trigger=wait_shutdown))

    def __enter__(self):
        self._thread.start()
        self._ready.wait(timeout=10)
        # 再给 hypercorn 一点时间开始 accept
        import time as _t
        _t.sleep(1.0)
        return self

    def __exit__(self, *exc):
        self._loop.call_soon_threadsafe(self._shutdown.set)
        self._thread.join(timeout=10)
        return False
